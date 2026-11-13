"""AC-7: the scripts import only the standard library and stay compatible with Python 3.10."""
from __future__ import annotations

import ast
import sys

from conftest import SCRIPTS, cli, write

LOCAL = {p.stem for p in SCRIPTS.glob("*.py")}


def scripts():
    return sorted(SCRIPTS.glob("*.py"))


def test_all_imports_are_standard_library_or_bundled():
    stdlib = set(sys.stdlib_module_names)
    for path in scripts():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name in stdlib or name in LOCAL, f"{path.name}: {name}"


def test_tomllib_is_imported_lazily_only():
    for path in scripts():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for top in tree.body:
            if isinstance(top, ast.Import):
                assert all(a.name != "tomllib" for a in top.names), path.name
            if isinstance(top, ast.ImportFrom):
                assert top.module != "tomllib", path.name
    toml_src = (SCRIPTS / "_ceg_toml.py").read_text(encoding="utf-8")
    assert "import tomllib" in toml_src and "E_PYTHON" in toml_src


def test_sources_parse_as_python_3_10():
    for path in scripts():
        ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_no_dynamic_code_or_shell_use():
    banned = {"eval", "exec", "compile", "__import__", "getattr", "setattr", "vars", "globals", "locals"}
    for path in scripts():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned, f"{path.name}: {node.func.id}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    assert m.split(".")[0] not in {"subprocess", "socket", "urllib", "http", "ctypes", "pickle"}, path.name


def test_other_formats_work_when_tomllib_is_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tomllib", None)
    f = write(tmp_path, "c.yaml", "a: 1\n")
    assert cli("set", str(f), "a", "2")[0] == 0
