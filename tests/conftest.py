"""Shared helpers: a pinned locale, an in-process CLI runner and a subprocess runner."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "config-edit-guard" / "scripts"
SCRIPT = SCRIPTS / "config_edit_guard.py"

for _p in (str(SCRIPTS),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config_edit_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _pinned_locale(monkeypatch):
    """Tests must not depend on the developer's locale: English unless a test says otherwise."""
    for name in ("CONFIG_EDIT_GUARD_LANG", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "C")


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TMPDIR", "HOME")}
    env["LANG"] = "C"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def run_cli(*args: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the script as a user would, with a clean environment and LANG pinned to C."""
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                          env=clean_env() if env is None else env, cwd=str(cwd) if cwd else None, timeout=60)


def cli(*args: str) -> tuple:
    """In-process run: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = config_edit_guard.main(list(args), prog="config_edit_guard.py")
    return code, out.getvalue(), err.getvalue()


def write(folder: Path, name: str, data) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_bytes(data.encode("utf-8"))
    else:
        path.write_bytes(data)
    return path


def diff_entries(a: str, b: str) -> list:
    """Changed lines as difflib reports them (one '-' and one '+' entry per replaced line)."""
    import difflib

    return [x for x in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if not x.startswith(("---", "+++", "@@"))]
