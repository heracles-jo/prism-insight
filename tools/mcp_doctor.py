#!/usr/bin/env python3
"""Report whether every MCP server the report path needs can actually start.

Why this exists: report sections silently degrade to "Analysis failed: ..."
when an MCP server cannot launch, and the cause is usually environmental — a
missing binary, a path that only exists on one machine, an unset key. The
failure looks identical in the report no matter which of those it was, and it
only shows up after a full (slow, paid) generation.

This resolves the config exactly the way the report path does, then checks each
server without launching it, so the same command can be run on every host and
the outputs compared.

**Never prints a secret.** Env vars are reported by name and set/unset only —
a lesson from leaking two credentials into a chat log by printing a resolved
registry object.

Usage:
    python tools/mcp_doctor.py            # human-readable
    python tools/mcp_doctor.py --json     # machine-diffable across hosts

Exit code is non-zero if any server is unusable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cores.llm import config_loader  # noqa: E402
from cores.llm.config_loader import (  # noqa: E402
    load_mcp_registry,
    load_report_mcp_registry,
)

def _load_repo_env(project_root: Path) -> dict:
    """Read the repo `.env`, the way every runtime entry point does.

    Without this the diagnostic reads a different environment than the thing it
    is diagnosing: `cores/analysis.py` and the MCP servers call `load_dotenv()`
    at start-up, so a key that lives only in `.env` is present for them and
    absent here. The tool then reports a working server as UNSET_ENV — the
    false positive its own docstring calls indistinguishable from a real
    breakage, and the reason two hosts' outputs could not be compared.

    `load_dotenv` does not override an already-exported variable, so a shell
    that set one explicitly still wins, exactly as it does at runtime.

    Returns where it looked and whether it found anything — never what it read.
    Two hosts disagreeing is only informative if you know which file each used.
    """
    env_path = project_root / ".env"
    if not env_path.exists():
        return {"path": str(env_path), "loaded": False}

    from dotenv import load_dotenv

    load_dotenv(env_path)
    return {"path": str(env_path), "loaded": True}


# `${VAR}` and `${VAR:-default}`, the two forms the loader interpolates.
# Matching only the first made a defaulted variable look like a literal, so
# an intentionally-optional entry was reported as an unset credential.
_ENV_REF = re.compile(r"^\$\{([A-Z0-9_]+)(?::-(.*))?\}$")
# A filesystem path, as opposed to a URL, an npm package spec, or a flag.
# Being strict here matters: this output is meant to be diffed across hosts,
# and a false positive is indistinguishable from a real breakage.
_FILE_SUFFIXES = (".js", ".py", ".sqlite", ".db", ".json", ".yaml", ".yml")
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

OK = "OK"
MISSING_COMMAND = "MISSING_COMMAND"
MISSING_PATH = "MISSING_PATH"
UNSET_ENV = "UNSET_ENV"
ABSOLUTE_PATH = "ABSOLUTE_PATH"
CANNOT_IMPORT = "CANNOT_IMPORT"


@dataclass
class ServerReport:
    name: str
    command: str
    command_found: bool
    problems: list[str] = field(default_factory=list)
    paths: list[dict] = field(default_factory=list)
    env: list[dict] = field(default_factory=list)
    launch: dict | None = None

    @property
    def healthy(self) -> bool:
        return not self.problems


_PYTHON_COMMAND = re.compile(r"(^|/)(python|python3|python3\.\d+)$")


def _launch_module(command: str, args) -> str | None:
    """The module a `python -m MODULE` server would run, if that is what it is.

    Servers launched by npx or uv are somebody else's dependency problem; only
    the ones this repo starts with an interpreter are checked.
    """
    if not _PYTHON_COMMAND.search(command or ""):
        return None
    args = list(args or [])
    if "-m" not in args:
        return None
    index = args.index("-m")
    return args[index + 1] if index + 1 < len(args) else None


def _check_launch(command: str, args, spec_env: dict, project_root: Path) -> dict | None:
    """Can this interpreter actually import the module it is told to run?

    `shutil.which` answers whether a `python3` exists, which is not the
    question. The reported failure was a host whose system interpreter is 3.9
    and has neither `mcp` nor the repo's dependencies: the command was found,
    the server died at launch, and the only symptom downstream was an empty
    report section.

    Importing the module is exactly what `python -m` does first, so this checks
    the real thing rather than a proxy for it. It runs with the server's own
    declared environment — PYTHONPATH included — because that is what the
    server will be given.

    Returns None when there is nothing to check, so a server this does not
    apply to is not reported as passing something it never took.
    """
    module = _launch_module(command, args)
    if module is None:
        return None

    env = {**os.environ, **{k: str(v) for k, v in (spec_env or {}).items()}}
    try:
        result = subprocess.run(
            [command, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=30,
            cwd=str(project_root), env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"module": module, "importable": False, "detail": type(exc).__name__}

    if result.returncode == 0:
        return {"module": module, "importable": True, "detail": ""}

    # The last line is the exception; the traceback above it is noise here, and
    # a full traceback could carry a path that differs between hosts.
    lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
    return {
        "module": module,
        "importable": False,
        "detail": lines[-1][:120] if lines else f"exit {result.returncode}",
    }


def _check_env(spec_env: dict, raw_env: dict | None = None) -> list[dict]:
    """Report env var names and whether a value resolves — never the value.

    ``spec_env`` has already been interpolated by the loader, so ``${VAR}``
    references are indistinguishable from literals by the time the registry
    exists. ``raw_env`` is the same block read straight from the YAML, which is
    what tells the two apart — and telling them apart is the whole point of
    migrating credentials out of the config file.
    """

    checked: list[dict] = []
    raw_env = raw_env or {}
    for key, resolved in sorted((spec_env or {}).items()):
        declared = str(raw_env.get(key, resolved) or "")
        reference = _ENV_REF.match(declared)
        if reference:
            var, default = reference.group(1), reference.group(2)
            # A default is the author saying what to do when the variable is
            # absent, so an unset one is a choice rather than a gap — including
            # an empty default, which means "pass nothing and let the server
            # decide". Flagging those buries the genuinely missing credentials
            # this tool exists to surface.
            checked.append(
                {
                    "key": key,
                    "source": f"${{{var}:-…}}" if default is not None else f"${{{var}}}",
                    "set": bool(os.environ.get(var)) or default is not None,
                }
            )
        else:
            # A credential written into the config file: machine-local, drifts
            # between hosts, and one `git add -A` away from being committed.
            checked.append(
                {
                    "key": key,
                    "source": "inline",
                    "set": bool(str(resolved or "").strip()),
                }
            )
    return checked


def _resolve_config_path(label: str) -> Path | None:
    """Mirror the loader's search order so the raw YAML can be read too.

    Must track :func:`load_report_mcp_registry` / :func:`load_mcp_registry`.
    When the report path stopped preferring the legacy config, this lagged
    behind and mislabelled the source while the registry itself was already
    correct — so if the loader's precedence changes, change it here too.
    """

    if label == "agent":
        # Always this file: it is what mcp-agent reads, regardless of what the
        # loader's search order would prefer.
        return config_loader._LEGACY_CONFIG
    if label == "report":
        override = os.environ.get("REPORT_MCP_CONFIG")
        if override:
            return Path(override)
    override = os.environ.get("PRISM_MCP_CONFIG")
    if override:
        return Path(override)
    if config_loader._NATIVE_CONFIG.exists():
        return config_loader._NATIVE_CONFIG
    if config_loader._LEGACY_CONFIG.exists():
        return config_loader._LEGACY_CONFIG
    return None


def _load_agent_registry():
    """The agent path config, read directly rather than through the loader.

    `load_mcp_registry` prefers the native registry and only falls back to this
    file when native is absent, so on a normal install nothing ever inspects it
    — and the servers it declares are most of a batch's MCP traffic. A host
    whose `python3` cannot import `mcp` produced 130 errors and no output at
    all, and the diagnostic reported everything healthy because it had never
    looked here.

    No interpolation: mcp-agent does not expand ${VAR} when it reads this file
    (measured), so the diagnostic must see the same literal values it will.
    """
    from cores.llm.mcp_registry import McpServerRegistry

    path = config_loader._LEGACY_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"{path} not present")
    return McpServerRegistry.from_yaml_dict(yaml.safe_load(path.read_text()) or {})


def _raw_servers(path: Path | None) -> dict:
    """Return the uninterpolated ``servers`` block, in either YAML shape."""

    if path is None or not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if "servers" in raw:
        return raw.get("servers") or {}
    return ((raw.get("mcp") or {}).get("servers")) or {}


def _looks_like_path(text: str) -> bool:
    if text.startswith("-") or _URL.match(text):
        return False
    if text.startswith("@"):
        return False  # npm scoped package, e.g. @scope/name@latest
    if text.startswith("/") or text.startswith("./") or text.startswith("../"):
        return True
    return text.endswith(_FILE_SUFFIXES)


def _base_dir(args, project_root: Path) -> Path:
    """Where relative args resolve from.

    Some servers are launched with `--directory X`, which moves the base; the
    sqlite entry's `../stock_tracking_db.sqlite` only makes sense relative to
    that, not to the repo root.
    """

    items = [str(a) for a in args or ()]
    for flag in ("--directory", "--cwd"):
        if flag in items:
            index = items.index(flag)
            if index + 1 < len(items):
                return project_root / items[index + 1]
    return project_root


def _check_args(args, project_root: Path) -> list[dict]:
    base = _base_dir(args, project_root)
    resolved: list[dict] = []
    for arg in args or ():
        text = str(arg)
        if not _looks_like_path(text):
            continue
        candidate = Path(text)
        absolute = candidate.is_absolute()
        full = candidate if absolute else (base / candidate)
        resolved.append(
            {
                "arg": text,
                "absolute": absolute,
                "base": None if absolute else str(base),
                "exists": full.exists(),
            }
        )
    return resolved


def inspect(registry, project_root: Path, raw_servers: dict | None = None) -> list[ServerReport]:
    raw_servers = raw_servers or {}
    reports: list[ServerReport] = []
    for name in sorted(registry.names()):
        spec = registry.get(name)
        command_found = shutil.which(spec.command) is not None
        raw_env = ((raw_servers.get(name) or {}).get("env")) or {}
        report = ServerReport(
            name=name,
            command=spec.command,
            command_found=command_found,
            paths=_check_args(spec.args, project_root),
            env=_check_env(dict(spec.env or {}), raw_env),
        )
        if not command_found:
            report.problems.append(MISSING_COMMAND)
        else:
            # Only worth asking once the command exists; otherwise the failure
            # is already named and running it would just say so again.
            report.launch = _check_launch(
                spec.command, spec.args, dict(spec.env or {}), project_root
            )
            if report.launch and not report.launch["importable"]:
                report.problems.append(CANNOT_IMPORT)
        for path in report.paths:
            if not path["exists"]:
                report.problems.append(MISSING_PATH)
            elif path["absolute"]:
                # Exists here, but an absolute path will not survive a move to
                # another host — the exact failure this tool was written for.
                report.problems.append(ABSOLUTE_PATH)
        for entry in report.env:
            if not entry["set"]:
                report.problems.append(UNSET_ENV)
        reports.append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-diffable output")
    parser.add_argument(
        "--all",
        action="store_true",
        help="also inspect the native registry, not just the report path",
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    # Before the registries load: the loader interpolates ${VAR} from the
    # environment, so reading .env afterwards would leave the registry holding
    # blanks while _check_env reported the same variables as set.
    env_source = _load_repo_env(project_root)

    sources = [("report", load_report_mcp_registry)]
    if config_loader._LEGACY_CONFIG.exists():
        # Checked by default, not behind --all: the analysis agents are the bulk
        # of a batch, and their servers were the ones dying unseen.
        sources.append(("agent", _load_agent_registry))
    if args.all:
        sources.append(("native", load_mcp_registry))

    payload = {
        "host": os.uname().nodename,
        "project_root": str(project_root),
        "env": env_source,
        "config": {
            # The four original keys are kept verbatim: this output gets diffed
            # against other hosts, possibly running an older build, and renaming
            # one would break the comparison this tool exists for.
            "native": str(config_loader._NATIVE_CONFIG),
            "native_exists": config_loader._NATIVE_CONFIG.exists(),
            "native_role": "report generation",
            "legacy": str(config_loader._LEGACY_CONFIG),
            "legacy_exists": config_loader._LEGACY_CONFIG.exists(),
            # Not a leftover. mcp-agent reads this for the analysis agents —
            # company info, macro, news, the buy and sell specialists — which
            # are most of the MCP traffic in a batch.
            "legacy_role": "analysis agents (mcp-agent)",
        },
        "registries": {},
    }

    unhealthy = 0
    for label, loader in sources:
        try:
            registry = loader()
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            payload["registries"][label] = {"error": str(exc)}
            unhealthy += 1
            continue
        config_path = _resolve_config_path(label)
        reports = inspect(registry, project_root, _raw_servers(config_path))
        unhealthy += sum(1 for r in reports if not r.healthy)
        payload["registries"][label] = {
            "source": str(config_path) if config_path else None,
            "servers": [
                {
                    "name": r.name,
                    "command": r.command,
                    "command_found": r.command_found,
                    "paths": r.paths,
                    "env": r.env,
                    "launch": r.launch,
                    "problems": sorted(set(r.problems)),
                }
                for r in reports
            ]
        }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 1 if unhealthy else 0

    print(f"host: {payload['host']}")
    print(f"root: {payload['project_root']}")
    cfg = payload["config"]
    print("config:")
    for key, label in (("native", "report path"), ("legacy", "agent path ")):
        state = "present" if cfg[f"{key}_exists"] else "MISSING"
        name = Path(cfg[key]).name
        print(f"  {label} ({name})  {state}  — {cfg[f'{key}_role']}")
    env_info = payload["env"]
    print(
        f"env: {env_info['path']} "
        f"({'loaded' if env_info['loaded'] else 'not found'})"
    )
    if cfg["legacy_exists"]:
        print(
            "  note: ${VAR} is not expanded in the agent path config — "
            "mcp-agent does not interpolate, so values go in the file itself"
        )
    else:
        # Its absence is the problem, not its presence. This used to advise
        # deleting it as a leftover, which would have taken out every analysis
        # agent: the report path moved to the native registry, the agents did
        # not, and a batch log showed 25 references to this file.
        print(
            "  the agent path config is missing; the analysis agents have no "
            "MCP servers without it"
        )
    for label, data in payload["registries"].items():
        print(f"\n[{label}] source={data.get('source')}")
        if "error" in data:
            print(f"  ERROR: {data['error']}")
            continue
        for server in data["servers"]:
            mark = "ok " if not server["problems"] else "FAIL"
            print(f"  {mark} {server['name']:<14} command={server['command']}", end="")
            if not server["command_found"]:
                print(" (not on PATH)", end="")
            print()
            for path in server["paths"]:
                flag = "exists" if path["exists"] else "MISSING"
                kind = "abs" if path["absolute"] else "rel"
                print(f"        arg[{kind}] {flag}: {path['arg']}")
            for entry in server["env"]:
                state = "set" if entry["set"] else "UNSET"
                print(f"        env {entry['key']} <- {entry['source']} [{state}]")
            launch = server.get("launch")
            if launch:
                if launch["importable"]:
                    print(f"        import {launch['module']}: ok")
                else:
                    print(
                        f"        import {launch['module']}: FAILED — {launch['detail']}"
                    )
            if server["problems"]:
                print(f"        problems: {', '.join(sorted(set(server['problems'])))}")

    print(f"\nunhealthy servers: {unhealthy}")
    return 1 if unhealthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
