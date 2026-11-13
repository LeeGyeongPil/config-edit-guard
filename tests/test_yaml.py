"""AC-1: YAML locate and set replace only the value span."""
from __future__ import annotations

import json

import pytest
from conftest import cli, diff_entries, write
from samples import BASE_YAML, DEPLOY_YAML, FOUR_SPACE_YAML, PROBE_YAML


def locate(path, yaml_path, *extra):
    code, out, err = cli("locate", str(path), yaml_path, "--json", *extra)
    assert code == 0, err
    return json.loads(out)


def test_probe_service_name_span(tmp_path):
    f = write(tmp_path, "probe.yaml", PROBE_YAML)
    rep = locate(f, "service.name")
    assert rep["span"]["start_byte"] == 109
    assert rep["span"]["end_byte"] == 114
    assert rep["style"] == "single"
    assert rep["value"] == "api" and rep["type"] == "str"
    assert (rep["span"]["line"], rep["span"]["column"]) == (7, 9)


def test_set_keeps_single_quotes_and_changes_one_line(tmp_path):
    f = write(tmp_path, "probe.yaml", PROBE_YAML)
    code, out, err = cli("set", str(f), "service.name", "gateway")
    assert code == 0, err
    after = f.read_text(encoding="utf-8")
    assert after == PROBE_YAML.replace("name: 'api'", "name: 'gateway'")
    assert len(diff_entries(PROBE_YAML, after)) == 2  # one removed + one added line
    assert "# seconds" in after and "&defaults" in after and "<<: *defaults" in after


def test_four_space_indent_and_flow_spacing_are_untouched(tmp_path):
    f = write(tmp_path, "c.yaml", FOUR_SPACE_YAML)
    code, _, err = cli("set", str(f), "root.flow.y", "5")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == FOUR_SPACE_YAML.replace("y: 2}", "y: 5}")


def test_crlf_file_stays_crlf(tmp_path):
    crlf = DEPLOY_YAML.replace("\n", "\r\n")
    f = write(tmp_path, "d.yaml", crlf)
    code, _, err = cli("set", str(f), "spec.replicas", "3")
    assert code == 0, err
    after = f.read_bytes().decode("utf-8")
    assert after == crlf.replace("replicas: 2", "replicas: 3")
    assert after.count("\r\n") == crlf.count("\r\n") and "\n" not in after.replace("\r\n", "")


def test_deploy_image_path_with_list_index(tmp_path):
    f = write(tmp_path, "d.yaml", DEPLOY_YAML)
    code, out, err = cli("set", str(f), "spec.template.spec.containers[0].image", "registry.example.com/api:1.4.2")
    assert code == 0, err
    after = f.read_text(encoding="utf-8")
    assert "image: registry.example.com/api:1.4.2   # pinned" in after
    assert len(diff_entries(DEPLOY_YAML, after)) == 2


def test_compact_sequence_and_anchor_alias_untouched(tmp_path):
    f = write(tmp_path, "c.yaml", "items:\n  - name: a\n    n: 1\n  - name: b\n    n: 2\n")
    code, _, err = cli("set", str(f), "items[1].n", "9")
    assert code == 0, err
    assert f.read_text(encoding="utf-8").endswith("  - name: b\n    n: 9\n")


def test_set_value_behind_a_merge_key_edits_the_anchor_and_reports_the_alias(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    code, out, err = cli("set", str(f), "defaults.retries", "5", "--json")
    assert code == 0, err
    rep = json.loads(out)
    assert rep["verify"]["via_alias"] == ["service.retries"]
    assert rep["verify"]["collateral_paths"] == []


def test_key_from_merge_is_refused_with_a_hint(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    code, _, err = cli("set", str(f), "service.retries", "5")
    assert code == 2 and "[E_PATH_NOT_FOUND]" in err and "merge key" in err


def test_multi_document_selection_by_flag_and_prefix(tmp_path):
    text = "a: 1\n---\na: 2\n"
    f = write(tmp_path, "m.yaml", text)
    assert locate(f, "a", "--doc", "1")["value"] == 2
    assert locate(f, "1:a")["value"] == 2
    code, _, err = cli("set", str(f), "1:a", "7")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == "a: 1\n---\na: 7\n"
    code, _, err = cli("locate", str(f), "a", "--doc", "5")
    assert code == 2 and "[E_PATH_NOT_FOUND]" in err


def test_cjk_and_bom_byte_offsets(tmp_path):
    text = "제목: 안녕\nname: 값\n"
    f = write(tmp_path, "k.yaml", b"\xef\xbb\xbf" + text.encode("utf-8"))
    rep = locate(f, "name")
    raw = f.read_bytes()
    s, e = rep["span"]["start_byte"], rep["span"]["end_byte"]
    assert raw[s:e].decode("utf-8") == "값"
    assert rep["span"]["column"] == 7  # characters, not bytes
    code, _, err = cli("set", str(f), "name", "새값")
    assert code == 0, err
    after = f.read_bytes()
    assert after.startswith(b"\xef\xbb\xbf") and after.decode("utf-8-sig") == text.replace("값", "새값")


def test_empty_value_gets_a_space_and_the_value(tmp_path):
    f = write(tmp_path, "e.yaml", "a:\nb: 1\n")
    code, _, err = cli("set", str(f), "a", "hello")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == "a: hello\nb: 1\n"


def test_block_scalar_is_located_but_not_set(tmp_path):
    f = write(tmp_path, "b.yaml", "run: |\n  echo one\n  echo two\nnext: 1\n")
    rep = locate(f, "run")
    assert rep["style"] == "literal" and rep["value"] == "echo one\necho two\n"
    code, _, err = cli("set", str(f), "run", "x")
    assert code == 2 and "[E_UNSUPPORTED]" in err


def test_alias_value_is_not_set(tmp_path):
    f = write(tmp_path, "a.yaml", "a: &x 1\nb: *x\n")
    code, _, err = cli("set", str(f), "b", "2")
    assert code == 2 and "[E_UNSUPPORTED]" in err


@pytest.mark.parametrize("text", [
    "a: first\n  second\n",          # multi-line plain scalar
    "a: [1,\n  2]\n",                # multi-line flow
    "? complex\n: value\n",          # complex key
    "a: 'one\n  two'\n",             # multi-line quoted
    "%YAML 1.2\n---\na: 1\n",        # directive
])
def test_unsupported_features_fail_loudly(tmp_path, text):
    f = write(tmp_path, "u.yaml", text)
    code, _, err = cli("locate", str(f), "a")
    assert code == 2 and "[E_UNSUPPORTED]" in err and "fix:" in err


def test_comments_and_blank_lines_survive_many_edits(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    for path, value in (("other.port", "9090"), ("service.name", "web"), ("defaults.timeout", "60")):
        assert cli("set", str(f), path, value)[0] == 0
    after = f.read_text(encoding="utf-8")
    assert after.count("#") == BASE_YAML.count("#") and after.count("\n\n") == BASE_YAML.count("\n\n")
