"""AC-5: every input problem is a defined [E_CODE] error with a fix hint, and the target stays untouched."""
from __future__ import annotations

import json
import os

import pytest
from conftest import cli, run_cli, write
from samples import BASE_YAML, SETTINGS_JSONC

DUP_YAML = "server:\n  host: a\n  port: 8080\n  tls: true\n  extra: 1\n  port: 9090\n"


def snapshot(path):
    st = os.stat(path)
    return path.read_bytes(), st.st_mtime_ns


def assert_defined_error(code, err, name):
    assert code == 2, err
    assert f"[{name}]" in err and "fix:" in err and err.startswith("config-edit-guard: error")
    assert len(err.strip().splitlines()) == 1


def test_missing_file(tmp_path):
    code, _, err = cli("set", str(tmp_path / "nope.yaml"), "a", "1")
    assert_defined_error(code, err, "E_FILE_NOT_FOUND")
    assert "nope.yaml" in err


def test_directory_instead_of_file(tmp_path):
    code, _, err = cli("locate", str(tmp_path), "a")
    assert_defined_error(code, err, "E_FILE_NOT_FOUND")


@pytest.mark.parametrize("data", [b"a: \xff\xfe broken\n", b"\xff\xfea\x00:\x001\x00", b"\xfe\xff\x00a", b"a: 1\x00\n"])
def test_encoding_errors_are_defined(tmp_path, data):
    f = write(tmp_path, "c.yaml", data)
    before = snapshot(f)
    code, _, err = cli("set", str(f), "a", "2")
    assert_defined_error(code, err, "E_ENCODING")
    assert snapshot(f) == before


def test_verify_reports_encoding_errors_for_either_side(tmp_path):
    good = write(tmp_path, "a.yaml", "a: 1\n")
    bad = write(tmp_path, "b.yaml", b"a: \xff\n")
    for first, second in ((good, bad), (bad, good)):
        code, _, err = cli("verify", str(first), str(second))
        assert_defined_error(code, err, "E_ENCODING")


def test_utf8_bom_is_accepted_not_an_error(tmp_path):
    f = write(tmp_path, "c.yaml", b"\xef\xbb\xbfa: 1\n")
    assert cli("locate", str(f), "a")[0] == 0


@pytest.mark.parametrize("name,text,needle", [
    ("p.yaml", "a: b: c\n", "p.yaml:1:"),
    ("p.yaml", "a: 1\n  b: 2\n", "p.yaml:2:"),
    ("p.yaml", "a:\n\tb: 1\n", "p.yaml:2:"),
    ("p.yaml", "a: *nothing\n", "p.yaml:1:"),
    ("p.json", '{"a": }', "p.json:1:"),
    ("p.toml", "a = \n", "p.toml:1:"),
    ("p.xml", "<a><b></a>", "p.xml:1:"),
])
def test_parse_errors_carry_line_and_column(tmp_path, name, text, needle):
    f = write(tmp_path, name, text)
    before = snapshot(f)
    code, _, err = cli("set", str(f), "a", "x")
    if name == "p.toml" and "[E_PYTHON]" in err:
        pytest.skip("TOML needs Python 3.11+")
    assert_defined_error(code, err, "E_PARSE" if "multi" not in err else "E_UNSUPPORTED")
    assert needle in err
    assert snapshot(f) == before


@pytest.mark.parametrize("text", ["a: [1,\n  2]\n", "? k\n: v\n", "a: 'x\n  y'\n", "a: one\n  two\n"])
def test_unsupported_yaml_features_name_line_and_the_way_out(tmp_path, text):
    f = write(tmp_path, "u.yaml", text)
    before = snapshot(f)
    code, _, err = cli("set", str(f), "a", "x")
    assert_defined_error(code, err, "E_UNSUPPORTED")
    assert "u.yaml:" in err and "verify --text-only" in err
    assert snapshot(f) == before


def test_unknown_path_suggests_the_nearest_keys(tmp_path):
    f = write(tmp_path, "c.yaml", "server:\n  port: 1\n  host: a\n  hostname: b\n  timeout: 3\n")
    before = snapshot(f)
    code, _, err = cli("set", str(f), "server.hots", "x")
    assert_defined_error(code, err, "E_PATH_NOT_FOUND")
    assert "did you mean: host" in err and "hostname" in err and "timeout" not in err
    assert snapshot(f) == before


def test_unknown_path_without_close_match_lists_keys(tmp_path):
    f = write(tmp_path, "c.yaml", "a: 1\nb: 2\n")
    code, _, err = cli("locate", str(f), "zzzzzz")
    assert_defined_error(code, err, "E_PATH_NOT_FOUND")
    assert "available keys: a, b" in err


def test_list_index_out_of_range_and_wrong_node_kind(tmp_path):
    f = write(tmp_path, "c.yaml", "l: [1, 2]\nm: x\n")
    code, _, err = cli("locate", str(f), "l[5]")
    assert_defined_error(code, err, "E_PATH_NOT_FOUND")
    assert "out of range" in err
    code, _, err = cli("locate", str(f), "m.x")
    assert_defined_error(code, err, "E_PATH_NOT_FOUND")
    code, _, err = cli("locate", str(f), "m[0]")
    assert_defined_error(code, err, "E_PATH_NOT_FOUND")


def test_duplicate_key_is_ambiguous_and_names_both_lines(tmp_path):
    f = write(tmp_path, "d.yaml", DUP_YAML)
    before = snapshot(f)
    code, _, err = cli("set", str(f), "server.port", "8443")
    assert_defined_error(code, err, "E_AMBIGUOUS_PATH")
    assert "appears more than once (line 3, line 6)" in err and "remove the duplicate" in err
    assert snapshot(f) == before


def test_set_on_a_collection_is_refused(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    before = snapshot(f)
    code, _, err = cli("set", str(f), "service", "x")
    assert_defined_error(code, err, "E_NOT_SCALAR")
    assert "verify" in err and snapshot(f) == before
    g = write(tmp_path, "a.json", '{"a": [1]}')
    code, _, err = cli("set", str(g), "a", "1")
    assert_defined_error(code, err, "E_NOT_SCALAR")


def test_unknown_extension_needs_format(tmp_path):
    f = write(tmp_path, "settings.ini", "a: 1\n")
    code, _, err = cli("locate", str(f), "a")
    assert_defined_error(code, err, "E_FORMAT")
    assert "--format" in err
    assert cli("locate", str(f), "a", "--format", "yaml")[0] == 0


@pytest.mark.parametrize("bad", ["", "a..b", "a[", "a[x]", '"unterminated', "a.", ".a", "a[0]b"])
def test_bad_path_syntax(tmp_path, bad):
    f = write(tmp_path, "c.yaml", "a: 1\n")
    code, _, err = cli("locate", str(f), bad)
    assert_defined_error(code, err, "E_USAGE")


def test_bad_allow_value_and_argparse_errors(tmp_path):
    f = write(tmp_path, "c.yaml", "a: 1\n")
    code, _, err = cli("verify", str(f), str(f), "--allow", "nonsense")
    assert_defined_error(code, err, "E_USAGE")
    for args in ([], ["set"], ["set", "x.yaml", "a"], ["verify", "a.yaml"], ["locate", "--nope"], ["set", "f", "p", "v", "--type", "zzz"]):
        code, _, err = cli(*args)
        assert_defined_error(code, err, "E_USAGE")


def test_unwritable_report_path_is_an_error(tmp_path):
    f = write(tmp_path, "c.yaml", "a: 1\n")
    code, _, err = cli("locate", str(f), "a", "--report", str(tmp_path / "no" / "such" / "r.json"))
    assert_defined_error(code, err, "E_USAGE")


def test_json_flag_prints_the_error_as_json_too(tmp_path):
    code, out, err = cli("locate", str(tmp_path / "x.yaml"), "a", "--json")
    payload = json.loads(out)
    assert code == 2 and payload["status"] == "error" and payload["error"]["code"] == "E_FILE_NOT_FOUND"
    assert "fix:" in err


@pytest.mark.parametrize("args", [[], ["locate"], ["set"], ["verify"]])
def test_every_help_exits_zero_in_both_languages(args):
    for lang in ("en", "ko"):
        proc = run_cli("--lang", lang, *args, "--help")
        assert proc.returncode == 0 and proc.stdout.strip(), proc.stderr
        assert proc.stderr == ""


def test_failed_self_check_does_not_write(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    before = snapshot(f)
    # ``--allow`` cannot hide a real problem: forcing a value the scanner would misread is refused at render time
    code, _, err = cli("set", str(f), "service.name", "x", "--type", "int")
    assert_defined_error(code, err, "E_TYPE")
    assert snapshot(f) == before


def test_atomic_write_leaves_no_temp_files_and_keeps_permissions(tmp_path):
    f = write(tmp_path, "c.yaml", "a: 1\n")
    os.chmod(f, 0o640)
    assert cli("set", str(f), "a", "2")[0] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.yaml"]
    assert os.stat(f).st_mode & 0o777 == 0o640


def test_symlinked_target_keeps_the_link(tmp_path):
    real = write(tmp_path, "real.json", SETTINGS_JSONC)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert cli("set", str(link), '"editor.fontSize"', "16")[0] == 0
    assert link.is_symlink() and '"editor.fontSize": 16' in real.read_text(encoding="utf-8")
