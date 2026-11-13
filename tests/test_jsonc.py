"""AC-2: JSON and JSONC set is a byte-preserving one-line edit."""
from __future__ import annotations

import json

import pytest
from conftest import cli, diff_entries, write
from samples import SETTINGS_JSONC


def test_number_edit_keeps_comments_commas_and_urls(tmp_path):
    f = write(tmp_path, "settings.jsonc", SETTINGS_JSONC)
    code, out, err = cli("set", str(f), '"editor.fontSize"', "15", "--json")
    assert code == 0, err
    after = f.read_text(encoding="utf-8")
    assert after == SETTINGS_JSONC.replace('"editor.fontSize": 14', '"editor.fontSize": 15')
    assert after.count("//") + after.count("/*") == SETTINGS_JSONC.count("//") + SETTINGS_JSONC.count("/*")
    assert "https://proxy.example.com:8080" in after and after.count(",\n}") == SETTINGS_JSONC.count(",\n}")
    assert len(diff_entries(SETTINGS_JSONC, after)) == 2
    rep = json.loads(out)
    assert rep["type"] == "int" and rep["format"] == "jsonc"


def test_string_edit_escapes_and_keeps_type(tmp_path):
    f = write(tmp_path, "settings.jsonc", SETTINGS_JSONC)
    code, _, err = cli("set", str(f), '"workbench.colorTheme"', 'Solarized "Light"')
    assert code == 0, err
    assert '"workbench.colorTheme": "Solarized \\"Light\\""' in f.read_text(encoding="utf-8")


def test_plain_json_is_reported_as_json(tmp_path):
    f = write(tmp_path, "a.json", '{\n  "a": {"b": [1, 2, 3]}\n}\n')
    code, out, _ = cli("locate", str(f), "a.b[2]", "--json")
    rep = json.loads(out)
    assert code == 0 and rep["format"] == "json" and rep["value"] == 3
    assert cli("set", str(f), "a.b[2]", "9")[0] == 0
    assert f.read_text(encoding="utf-8") == '{\n  "a": {"b": [1, 2, 9]}\n}\n'


def test_dotted_key_needs_quotes_and_the_error_says_so(tmp_path):
    f = write(tmp_path, "settings.jsonc", SETTINGS_JSONC)
    code, _, err = cli("set", str(f), "editor.fontSize", "15")
    assert code == 2 and "[E_PATH_NOT_FOUND]" in err and '"editor.fontSize"' in err


def test_duplicate_json_keys_are_ambiguous(tmp_path):
    f = write(tmp_path, "d.json", '{\n  "a": 1,\n  "a": 2\n}\n')
    code, _, err = cli("set", str(f), "a", "3")
    assert code == 2 and "[E_AMBIGUOUS_PATH]" in err and "line 2, line 3" in err


@pytest.mark.parametrize("text", ['{"a": }', '{"a": 1,, }', '{"a" 1}', '{"a": "x\n"}', "{'a': 1}", '{"a": 1} x', "", '{"a": /* open'])
def test_parse_errors_have_line_and_column(tmp_path, text):
    f = write(tmp_path, "bad.json", text)
    code, _, err = cli("locate", str(f), "a")
    assert code == 2 and "[E_PARSE]" in err and "bad.json:" in err


def test_type_rules(tmp_path):
    f = write(tmp_path, "t.json", '{"s": "x", "n": 1, "b": true, "z": null}\n')
    assert cli("set", str(f), "n", "abc")[0] == 2
    assert cli("set", str(f), "n", "abc", "--type", "str")[0] == 0
    assert cli("set", str(f), "b", "yes")[0] == 2
    assert cli("set", str(f), "b", "false")[0] == 0
    assert cli("set", str(f), "z", "5")[0] == 0
    assert json.loads(f.read_text(encoding="utf-8")) == {"s": "x", "n": "abc", "b": False, "z": 5}


def test_setting_the_same_value_writes_nothing(tmp_path):
    f = write(tmp_path, "s.json", '{"a": 1}\n')
    before = f.stat().st_mtime_ns
    code, out, _ = cli("set", str(f), "a", "1", "--json")
    assert code == 0 and json.loads(out)["status"] == "unchanged" and f.stat().st_mtime_ns == before
