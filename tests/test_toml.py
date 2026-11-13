"""AC-2: TOML set changes only the expected path (checked with tomllib) and keeps trailing comments."""
from __future__ import annotations

import json
import sys

import pytest
from conftest import cli, diff_entries, write
from samples import PYPROJECT_TOML

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="TOML support needs Python 3.11+ (tomllib)")


def _diff_paths(a: dict, b: dict, prefix=()):
    from _ceg_model import flatten_python

    fa, fb = flatten_python(a), flatten_python(b)
    return sorted(p for p in set(fa) | set(fb) if fa.get(p) != fb.get(p))


def test_version_edit_is_semantically_exact_and_keeps_comment(tmp_path):
    import tomllib

    f = write(tmp_path, "pyproject.toml", PYPROJECT_TOML)
    code, _, err = cli("set", str(f), "project.version", "0.4.0")
    assert code == 0, err
    after = f.read_text(encoding="utf-8")
    assert after == PYPROJECT_TOML.replace('version = "0.3.0"', 'version = "0.4.0"')
    assert _diff_paths(tomllib.loads(PYPROJECT_TOML), tomllib.loads(after)) == [("project", "version")]
    assert '# display name' in after and len(diff_entries(PYPROJECT_TOML, after)) == 2


def test_integer_dotted_and_array_table_paths(tmp_path):
    f = write(tmp_path, "pyproject.toml", PYPROJECT_TOML)
    assert cli("set", str(f), "tool.ruff.line-length", "100")[0] == 0
    assert cli("set", str(f), "bin[1].name", "other")[0] == 0
    after = f.read_text(encoding="utf-8")
    assert "line-length = 100" in after and "name = 'other'" in after  # literal-string style kept
    assert "name = 'first'" in after


def test_array_element_in_a_multiline_array(tmp_path):
    f = write(tmp_path, "pyproject.toml", PYPROJECT_TOML)
    assert cli("set", str(f), "project.dependencies[1]", "c")[0] == 0
    assert '  "c" ]' in f.read_text(encoding="utf-8")


def test_inline_table_and_quoted_keys(tmp_path):
    text = 'a = { x = 1, "y.z" = "s" }\n[t."q k"]\nv = 1.5\n'
    f = write(tmp_path, "c.toml", text)
    assert cli("set", str(f), "a.x", "2")[0] == 0
    assert cli("set", str(f), 'a."y.z"', "t")[0] == 0
    assert cli("set", str(f), 't."q k".v', "2.5")[0] == 0
    assert f.read_text(encoding="utf-8") == 'a = { x = 2, "y.z" = "t" }\n[t."q k"]\nv = 2.5\n'


def test_type_checks_use_tomllib(tmp_path):
    f = write(tmp_path, "c.toml", "n = 1\nd = 2026-11-13\ns = 'x'\nb = true\n")
    assert cli("set", str(f), "n", "1.5")[0] == 2
    assert cli("set", str(f), "d", "2027-01-01")[0] == 0
    assert cli("set", str(f), "d", "tomorrow")[0] == 2
    assert cli("set", str(f), "b", "false")[0] == 0
    assert cli("set", str(f), "s", "it's")[0] == 0
    assert 's = "it\'s"' in f.read_text(encoding="utf-8")  # literal style cannot hold a quote, falls back to basic


def test_multiline_string_is_unsupported(tmp_path):
    f = write(tmp_path, "c.toml", 's = """\nline\n"""\n')
    code, _, err = cli("set", str(f), "s", "x")
    assert code == 2 and "[E_UNSUPPORTED]" in err


def test_invalid_toml_reports_line_and_column(tmp_path):
    f = write(tmp_path, "c.toml", "a = 1\n[a]\nb = 2\n")
    code, _, err = cli("locate", str(f), "a")
    assert code == 2 and "[E_PARSE]" in err and "c.toml:" in err


def test_locate_reports_span_and_style(tmp_path):
    f = write(tmp_path, "pyproject.toml", PYPROJECT_TOML)
    code, out, _ = cli("locate", str(f), "project.name", "--json")
    rep = json.loads(out)
    raw = f.read_bytes()
    assert raw[rep["span"]["start_byte"]:rep["span"]["end_byte"]] == b'"api"' and rep["style"] == "basic"


def test_toml_on_old_python_is_a_defined_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tomllib", None)  # makes `import tomllib` raise ImportError
    f = write(tmp_path, "c.toml", "a = 1\n")
    code, _, err = cli("locate", str(f), "a")
    assert code == 2 and "[E_PYTHON]" in err and "3.11" in err
    g = write(tmp_path, "c.json", '{"a": 1}')
    assert cli("locate", str(g), "a")[0] == 0
