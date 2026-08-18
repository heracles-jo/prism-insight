"""Path heuristics for the MCP diagnostic.

The point of the tool is that its output can be diffed across hosts, so a
false positive is as harmful as a missed failure — the first version flagged a
URL, an npm package spec, and a correctly-resolved sqlite path as missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tools.mcp_doctor import (
    _base_dir,
    _check_env,
    _check_launch,
    _launch_module,
    _load_repo_env,
    _looks_like_path,
)


@pytest.mark.parametrize(
    "arg",
    [
        "perplexity-ask/dist/index.js",
        "/abs/path/to/index.js",
        "../stock_tracking_db.sqlite",
        "./local.py",
        "config.yaml",
    ],
)
def test_filesystem_paths_are_recognized(arg):
    assert _looks_like_path(arg) is True


@pytest.mark.parametrize(
    "arg",
    [
        "http://localhost:8000/sse",
        "https://example.test/thing",
        "@mzxrai/mcp-webresearch@latest",
        "firecrawl-mcp",
        "mcp-server-sqlite",
        "-y",
        "--directory",
        "run",
    ],
)
def test_urls_packages_and_flags_are_not_paths(arg):
    assert _looks_like_path(arg) is False


def test_directory_flag_moves_the_resolution_base():
    """`uv --directory sqlite run ...` makes ../db resolve from sqlite/."""

    root = Path("/repo")
    args = ("--directory", "sqlite", "run", "mcp-server-sqlite", "--db-path", "../x.sqlite")

    assert _base_dir(args, root) == root / "sqlite"


def test_without_a_directory_flag_the_base_is_the_repo_root():
    root = Path("/repo")

    assert _base_dir(("-y", "firecrawl-mcp"), root) == root


class TestEnvReporting:
    """Values must never appear — two were leaked into a chat log this way."""

    def test_env_reference_reports_name_and_whether_it_is_set(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "super-secret-value")

        [entry] = _check_env({"SOME_KEY": "${SOME_KEY}"})

        assert entry == {"key": "SOME_KEY", "source": "${SOME_KEY}", "set": True}

    def test_unset_reference_is_reported_as_unset(self, monkeypatch):
        monkeypatch.delenv("ABSENT_KEY", raising=False)

        [entry] = _check_env({"ABSENT_KEY": "${ABSENT_KEY}"})

        assert entry["set"] is False

    def test_inline_literals_are_flagged_without_exposing_them(self):
        [entry] = _check_env({"PERPLEXITY_API_KEY": "pplx-realsecretvalue"})

        assert entry["source"] == "inline"
        assert entry["set"] is True
        assert "realsecret" not in repr(entry)

    def test_blank_inline_value_counts_as_unset(self):
        [entry] = _check_env({"EMPTY": "   "})

        assert entry["set"] is False

    def test_raw_yaml_reveals_a_reference_the_loader_already_substituted(
        self, monkeypatch
    ):
        """The loader interpolates before the registry exists.

        Without the raw block, a `${VAR}` reference and a hardcoded credential
        are indistinguishable — and telling them apart is the entire point of
        migrating secrets out of the config file.
        """

        monkeypatch.setenv("PERPLEXITY_API_KEY", "resolved-secret")
        interpolated = {"PERPLEXITY_API_KEY": "resolved-secret"}
        raw = {"PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"}

        [entry] = _check_env(interpolated, raw)

        assert entry["source"] == "${PERPLEXITY_API_KEY}"
        assert entry["set"] is True
        assert "resolved-secret" not in repr(entry)

    def test_a_hardcoded_credential_is_still_reported_as_inline(self):
        interpolated = {"PERPLEXITY_API_KEY": "pplx-hardcoded"}
        raw = {"PERPLEXITY_API_KEY": "pplx-hardcoded"}

        [entry] = _check_env(interpolated, raw)

        assert entry["source"] == "inline"


class TestDotenvLoading:
    """The diagnostic has to read the same environment as the runtime.

    Every entry point calls `load_dotenv()`; this tool did not, so a key that
    lives only in `.env` looked unset here and set everywhere else. The result
    was a working server reported as UNSET_ENV — the exact false positive the
    module docstring says makes the output worthless.
    """

    def test_a_variable_from_the_env_file_is_visible(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRISM_DOCTOR_PROBE", raising=False)
        (tmp_path / ".env").write_text(
            "PRISM_DOCTOR_PROBE=from-file\n", encoding="utf-8"
        )

        result = _load_repo_env(tmp_path)

        assert result["loaded"] is True
        assert os.environ.get("PRISM_DOCTOR_PROBE") == "from-file"

    def test_an_exported_variable_beats_the_file(self, monkeypatch, tmp_path):
        """A shell that set it explicitly meant it; the file must not overrule."""
        monkeypatch.setenv("PRISM_DOCTOR_PROBE", "from-shell")
        (tmp_path / ".env").write_text(
            "PRISM_DOCTOR_PROBE=from-file\n", encoding="utf-8"
        )

        _load_repo_env(tmp_path)

        assert os.environ["PRISM_DOCTOR_PROBE"] == "from-shell"

    def test_a_checkout_without_an_env_file_still_runs(self, tmp_path):
        result = _load_repo_env(tmp_path)

        assert result["loaded"] is False
        assert result["path"].endswith(".env")

    def test_the_report_names_the_file_but_not_its_contents(self, monkeypatch, tmp_path):
        """Saying how many keys were read would leak how many secrets exist."""
        monkeypatch.delenv("SOME_KEY", raising=False)
        (tmp_path / ".env").write_text(
            "SOME_KEY=super-secret-value\n", encoding="utf-8"
        )

        result = _load_repo_env(tmp_path)

        assert set(result) == {"path", "loaded"}
        assert "super-secret-value" not in repr(result)


class TestLaunchCheck:
    """`shutil.which` answers the wrong question.

    A host whose system interpreter is 3.9 has a `python3` on PATH and no
    `mcp` in it. The command was found, the server died at launch, and the only
    symptom downstream was a report section that came out empty — which is how
    this went unnoticed until somebody deployed to a new machine.
    """

    @pytest.mark.parametrize(
        ("command", "args", "expected"),
        [
            ("python3", ["-m", "cores.market_data.mcp_server"], "cores.market_data.mcp_server"),
            ("/usr/bin/python3.11", ["-m", "pkg.mod"], "pkg.mod"),
            ("/some/venv/bin/python", ["-m", "pkg.mod"], "pkg.mod"),
            # Not ours to check: whoever packaged these owns their dependencies.
            ("npx", ["-y", "firecrawl-mcp@3.23.6"], None),
            ("uv", ["--directory", "sqlite", "run", "mcp-server-sqlite"], None),
            # A python invoked without -m runs a script, not a module.
            ("python3", ["script.py"], None),
            # Malformed: `-m` with nothing after it.
            ("python3", ["-m"], None),
        ],
    )
    def test_only_python_module_servers_are_checked(self, command, args, expected):
        assert _launch_module(command, args) == expected

    def test_an_interpreter_that_cannot_import_the_module_is_reported(self, tmp_path):
        """The reported failure, reproduced: the command exists, the import does not."""
        result = _check_launch(
            sys.executable, ["-m", "a_module_that_does_not_exist"], {}, tmp_path
        )

        assert result is not None
        assert result["importable"] is False
        assert "ModuleNotFoundError" in result["detail"]

    def test_an_interpreter_that_can_import_it_passes(self, tmp_path):
        result = _check_launch(sys.executable, ["-m", "json"], {}, tmp_path)

        assert result == {"module": "json", "importable": True, "detail": ""}

    def test_a_server_this_does_not_apply_to_returns_none(self, tmp_path):
        """None, not a pass — a server never checked must not look checked."""
        assert _check_launch("npx", ["-y", "some-mcp@1.0.0"], {}, tmp_path) is None

    def test_the_declared_environment_reaches_the_subprocess(self, tmp_path):
        """PYTHONPATH is declared per server, and without it the import fails.

        Checking with the ambient environment instead would pass here and fail
        at launch — the same gap in a new place.
        """
        package = tmp_path / "prism_probe_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        # A real directory that is not the one holding the package, so the only
        # way in is the declared PYTHONPATH.
        (tmp_path / "elsewhere").mkdir()

        without = _check_launch(
            sys.executable, ["-m", "prism_probe_pkg"], {}, tmp_path / "elsewhere"
        )
        with_path = _check_launch(
            sys.executable,
            ["-m", "prism_probe_pkg"],
            {"PYTHONPATH": str(tmp_path)},
            tmp_path / "elsewhere",
        )

        assert without["importable"] is False
        assert with_path["importable"] is True

    def test_a_failure_detail_carries_no_traceback(self, tmp_path):
        """A traceback quotes paths, which differ per host and defeat diffing."""
        result = _check_launch(
            sys.executable, ["-m", "a_module_that_does_not_exist"], {}, tmp_path
        )

        assert "Traceback" not in result["detail"]
        assert len(result["detail"].splitlines()) == 1
