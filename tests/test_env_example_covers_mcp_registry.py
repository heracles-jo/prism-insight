"""The registry substitutes env vars; `.env.example` has to name them.

`cores/llm/mcp_servers.yaml` stopped hardcoding values and started reading
`${VAR}` from the environment. Nothing told the operator which variables that
meant, so a fresh host came up with servers that could not start —
`PRISM_MCP_PYTHON` unset resolves to a bare `python3`, which on a machine whose
system interpreter is 3.9 has no `mcp` at all. The server dies without an error
anybody sees; the report just has an empty section.

These tests fail when the registry grows a variable the example does not
mention, so the two cannot drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "cores" / "llm" / "mcp_servers.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# `${VAR}` and `${VAR:-default}`, the two forms the loader interpolates. Reading
# only the first is the bug mcp_doctor had: a defaulted variable looked like a
# literal, so it was never recognised as a variable at all.
_ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}")

# Commented-out entries count as documented — that is how this file marks an
# optional variable.
_ENV_ENTRY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.M)

# A value that tells you what to put there. `your_krx_id` is not a credential,
# and a length test alone calls it one — `your_kakao_bot_token` is 20 characters.
_PLACEHOLDER = re.compile(r"your|example|placeholder|here|change[_-]?me|xxx", re.I)


def _referenced_variables() -> set[str]:
    if not REGISTRY.exists():
        pytest.skip("registry not present in this checkout")
    # Comments are prose. One of them explains that the interpolator cannot
    # handle a nested `${A:-${B}}`, and scanning it turns that sentence into a
    # demand that `.env.example` document a variable named A.
    body = "\n".join(
        line for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    return set(_ENV_REF.findall(body))


def _documented_variables() -> set[str]:
    if not ENV_EXAMPLE.exists():
        pytest.skip(".env.example not present in this checkout")
    return set(_ENV_ENTRY.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def test_every_variable_the_registry_reads_is_documented():
    referenced = _referenced_variables()

    assert referenced, "the regex matched nothing — it no longer fits the file"
    undocumented = sorted(referenced - _documented_variables())

    assert not undocumented, (
        "cores/llm/mcp_servers.yaml reads these, but .env.example never mentions "
        f"them: {undocumented}. An operator has no way to learn they exist."
    )


def test_the_defaulted_form_is_recognised_as_a_variable():
    """Guards the regex itself, which is where this class of bug lives.

    `PRISM_MCP_PYTHON` is only ever written as `${PRISM_MCP_PYTHON:-python3}`.
    A pattern that matches `${VAR}` alone finds nothing to check and the test
    above passes while documenting nothing.
    """
    assert _ENV_REF.findall("${PRISM_MCP_PYTHON:-python3}") == ["PRISM_MCP_PYTHON"]
    assert _ENV_REF.findall("${FIRECRAWL_API_KEY}") == ["FIRECRAWL_API_KEY"]
    assert "PRISM_MCP_PYTHON" in _referenced_variables()


def test_the_interpreter_variable_says_what_happens_without_it():
    """Naming it is not enough; its absence is silent and needs saying.

    Every other variable here degrades visibly — a missing API key takes out one
    server and says so. An unset PRISM_MCP_PYTHON falls back to a `python3` that
    may not be able to run the server at all, and the only symptom is an empty
    report section.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    position = text.index("PRISM_MCP_PYTHON")
    preamble = text[max(0, position - 900) : position]

    assert "조용히" in preamble, (
        "PRISM_MCP_PYTHON is listed but its silent failure mode is not explained"
    )


def test_the_example_carries_no_real_credentials():
    """A placeholder file is the easiest place to leak a key by accident."""
    secretish = ("KEY", "TOKEN", "SECRET", "PW")
    offenders = []

    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=(.*)$", line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).split("#")[0].strip()
        if not value or not any(word in name for word in secretish):
            continue
        if _PLACEHOLDER.search(value):
            continue
        # What is left is opaque. A real credential is a long run of characters
        # that says nothing about itself.
        if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", value):
            offenders.append(name)

    assert not offenders, (
        f"these look like real credentials rather than placeholders: {offenders}"
    )
