"""AC-4: type and quoting rules."""
from __future__ import annotations

import json

import pytest
from conftest import cli, write


def set_json(path, yaml_path, value, *extra):
    code, out, err = cli("set", str(path), yaml_path, value, "--json", *extra)
    return code, (json.loads(out) if out.strip().startswith("{") else None), err


def test_norway_problem_gets_quotes(tmp_path):
    f = write(tmp_path, "locale.yaml", "country: DE   # ISO code\nlanguage: de\n")
    code, rep, err = set_json(f, "country", "NO")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == 'country: "NO"   # ISO code\nlanguage: de\n'
    assert rep["quote_added"] is True and rep["quote_reason"] == "yaml11_bool" and rep["style"] == "double"


@pytest.mark.parametrize("value,reason", [
    ("0755", "yaml11_octal"),
    ("1.10", "float_lossy"),
    ("on", "yaml11_bool"),
    ("Yes", "yaml11_bool"),
    ("y", "yaml11_bool"),
    ("~", "yaml12_non_string"),
    ("null", "yaml12_non_string"),
    ("true", "yaml12_non_string"),
    ("42", "yaml12_non_string"),
    ("1.5", "yaml12_non_string"),
    ("2026-11-13", "yaml11_other"),
    ("1:30", "yaml11_other"),
    ("a: b", "indicator"),
    ("a #b", "indicator"),
    ("- x", "indicator"),
    ("*star", "indicator"),
    ("", "indicator"),
    (" padded", "indicator"),
])
def test_plain_string_that_would_change_meaning_is_quoted(tmp_path, value, reason):
    f = write(tmp_path, "c.yaml", "k: old\n")
    code, rep, err = set_json(f, "k", value)
    assert code == 0, err
    assert rep["quote_added"] is True and rep["quote_reason"] == reason
    import _ceg_yaml

    after = f.read_text(encoding="utf-8")
    assert after.startswith('k: "') and json.loads(after[3:].strip()) == value
    assert _ceg_yaml.parse_yaml(after).roots[0].children[0].value == value


@pytest.mark.parametrize("value", ["gateway", "api-2", "1.2.3", "v1.10", "hello world", "a:b", "http://x.example.com/a"])
def test_ordinary_strings_stay_plain(tmp_path, value):
    f = write(tmp_path, "c.yaml", "k: old\n")
    code, rep, _ = set_json(f, "k", value)
    assert code == 0 and rep["quote_added"] is False and rep["quote_reason"] is None
    assert f.read_text(encoding="utf-8") == f"k: {value}\n"


def test_original_quote_style_is_kept(tmp_path):
    f = write(tmp_path, "c.yaml", "a: 'x'\nb: \"y\"\n")
    assert cli("set", str(f), "a", "NO")[0] == 0
    assert cli("set", str(f), "b", 'say "hi"')[0] == 0
    assert f.read_text(encoding="utf-8") == "a: 'NO'\nb: \"say \\\"hi\\\"\"\n"
    assert cli("set", str(f), "a", "it's")[0] == 0
    assert "a: 'it''s'" in f.read_text(encoding="utf-8")


def test_non_integer_on_an_int_is_a_type_error_unless_forced_to_string(tmp_path):
    f = write(tmp_path, "c.yaml", "port: 8080\n")
    before = f.read_bytes()
    code, _, err = cli("set", str(f), "port", "abc")
    assert code == 2 and "[E_TYPE]" in err and "--type str" in err and f.read_bytes() == before
    code, rep, err = set_json(f, "port", "abc", "--type", "str")
    assert code == 0 and rep["type"] == "str" and f.read_text(encoding="utf-8") == "port: abc\n"


def test_numbers_and_booleans_keep_their_type(tmp_path):
    f = write(tmp_path, "c.yaml", "n: 1\nx: 1.5\nb: true\nz: ~\n")
    assert cli("set", str(f), "n", "10")[0] == 0
    assert cli("set", str(f), "x", "2")[0] == 0           # an int is a valid float value
    assert cli("set", str(f), "b", "False")[0] == 0
    assert cli("set", str(f), "n", "1.5")[0] == 2          # float on an int
    assert cli("set", str(f), "b", "no")[0] == 2           # YAML 1.1 bool word on a bool: not a 1.2 bool
    assert f.read_text(encoding="utf-8") == "n: 10\nx: 2\nb: False\nz: ~\n"


def test_null_value_takes_the_type_of_the_new_text(tmp_path):
    f = write(tmp_path, "c.yaml", "a: ~\nb: ~\nc: ~\n")
    assert cli("set", str(f), "a", "5")[0] == 0
    assert cli("set", str(f), "b", "text")[0] == 0
    assert cli("set", str(f), "c", "null")[0] == 0
    assert f.read_text(encoding="utf-8") == "a: 5\nb: text\nc: null\n"


def test_explicit_types(tmp_path):
    f = write(tmp_path, "c.yaml", "a: x\nb: x\nc: x\n")
    assert cli("set", str(f), "a", "7", "--type", "int")[0] == 0
    assert cli("set", str(f), "b", "true", "--type", "bool")[0] == 0
    assert cli("set", str(f), "c", "", "--type", "null")[0] == 0
    assert f.read_text(encoding="utf-8") == "a: 7\nb: true\nc: null\n"
    assert cli("set", str(f), "a", "x", "--type", "float")[0] == 2


def test_json_toml_xml_keep_their_own_rules(tmp_path):
    j = write(tmp_path, "a.json", '{"s": "x"}\n')
    assert cli("set", str(j), "s", "NO")[0] == 0 and '"NO"' in j.read_text(encoding="utf-8")  # JSON strings are always quoted
    x = write(tmp_path, "a.xml", "<r><n>1</n></r>\n")
    assert cli("set", str(x), "r.n", "abc", "--type", "int")[0] == 2
    assert cli("set", str(x), "r.n", "abc")[0] == 0
