"""AC-3: two-layer verification and its exit codes."""
from __future__ import annotations

import json

import pytest
from conftest import cli, write
from samples import BASE_YAML, PROBE_NODUP

from _ceg_model import Source
from _ceg_verify import verify_sources

EDITED = BASE_YAML.replace("name: 'api'", "name: 'gateway'")


def src(text: str, bom: bool = False) -> Source:
    raw = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
    return Source(raw=raw, text=text, bom=bom)


def check(after: str, expect=("service.name",), allow=(), text_only=False, bom=False, before: str = BASE_YAML) -> dict:
    return verify_sources(src(before), src(after, bom), "yaml", list(expect), set(allow), text_only)


def test_probe_sketch_pair_splits_collateral_alias_and_lost_comment():
    after = (PROBE_NODUP.replace("name: 'api'", "name: 'gateway'").replace("  retries: 3\n", "  retries: 5\n")
             .replace("# seconds", ""))
    rep = verify_sources(src(PROBE_NODUP), src(after), "yaml", ["service.name"], set())
    assert rep["collateral_paths"] == ["defaults.retries"]
    assert rep["via_alias"] == ["service.retries"]
    assert rep["lost_comments"] == [{"line": 3, "text": "# seconds"}]
    assert not rep["ok"] and {"collateral_paths", "lost_comments"} <= set(rep["violations"])


def test_clean_set_style_edit_passes():
    rep = check(EDITED)
    assert rep["ok"] and rep["violations"] == [] and rep["changed_paths"] == ["service.name"]


def test_no_op_pair_passes_without_expectations():
    assert check(BASE_YAML, expect=())["ok"]


INJECTIONS = {
    "lost_comments": EDITED.replace("   # seconds", ""),
    "key_order_changed": EDITED.replace("  host: example.com\n  port: 8080\n", "  port: 8080\n  host: example.com\n"),
    "quote_style_changed": EDITED.replace('enabled: "no"', "enabled: 'no'"),
    "blank_line_delta": EDITED.replace("  retries: 3\n\n", "  retries: 3\n"),
    "eol_changed": EDITED.replace("\n", "\r\n"),
    "final_newline_changed": EDITED.rstrip("\n"),
    "anchors_lost": EDITED.replace("&defaults", "&base").replace("*defaults", "*base"),
    "duplicate_keys": EDITED.replace("  port: 8080\n", "  port: 8080\n  host: other.example.com\n"),
    "yaml11_risky": EDITED.replace('enabled: "no"', "enabled: no"),
    "unexpected_line_changes": EDITED.replace("    # seconds", "    # seconds").replace("tags: [a, b]", "tags:\n    - a\n    - b"),
}


@pytest.mark.parametrize("violation", sorted(INJECTIONS))
def test_each_injected_problem_exits_one(violation, tmp_path):
    after = INJECTIONS[violation]
    rep = check(after)
    assert not rep["ok"] and violation in rep["violations"], rep["violations"]
    before_f = write(tmp_path, "before.yaml", BASE_YAML)
    after_f = write(tmp_path, "after.yaml", after.encode("utf-8"))
    code, out, _ = cli("verify", str(before_f), str(after_f), "--expect", "service.name")
    assert code == 1 and "FAIL" in out


def test_bom_added_is_flagged():
    rep = check(EDITED, bom=True)
    assert "bom_changed" in rep["violations"] and rep["bom_changed"]


def test_expected_path_that_did_not_change_is_flagged():
    rep = check(BASE_YAML, expect=("service.name",))
    assert rep["unchanged_expected"] == ["service.name"] and "unchanged_expected" in rep["violations"]


@pytest.mark.parametrize("violation,allow", [
    ("lost_comments", "comments"), ("key_order_changed", "order"), ("quote_style_changed", "quotes"),
    ("blank_line_delta", "blank_lines"), ("eol_changed", "eol"), ("final_newline_changed", "eol"),
])
def test_allow_switches_off_only_its_own_check(violation, allow):
    rep = check(INJECTIONS[violation], allow=(allow,))
    assert rep["ok"], rep["violations"]
    other = [v for v in INJECTIONS if v not in (violation, "eol_changed", "final_newline_changed")]
    for name in other[:3]:
        assert not check(INJECTIONS[name], allow=(allow,))["ok"]


def test_allow_does_not_hide_collateral_changes():
    after = EDITED.replace("port: 8080", "port: 9090")
    assert check(after, allow=("comments", "order", "quotes", "blank_lines", "eol"))["collateral_paths"] == ["other.port"]


def test_text_only_skips_the_semantic_layer_but_keeps_text_checks():
    unsupported = "a: first\n  second   # note\nb: 1\n"
    after = "a: first\n  second\nb: 2\n"
    rep = verify_sources(src(unsupported), src(after), "yaml", [], set(), text_only=True)
    assert rep["mode"] == "text_only" and rep["collateral_paths"] == [] and rep["changed_paths"] == []
    assert rep["lost_comments"] == [{"line": 2, "text": "# note"}] and "lost_comments" in rep["violations"]


def test_text_only_cli_and_the_full_mode_error(tmp_path):
    before = write(tmp_path, "a.yaml", "a: first\n  second\n")
    after = write(tmp_path, "b.yaml", "a: first\n  second\n")
    code, _, err = cli("verify", str(before), str(after))
    assert code == 2 and "[E_UNSUPPORTED]" in err and "--text-only" in err
    code, out, _ = cli("verify", str(before), str(after), "--text-only")
    assert code == 0 and "OK" in out


def test_via_alias_alone_is_not_a_violation():
    after = BASE_YAML.replace("retries: 3", "retries: 5")
    rep = check(after, expect=("defaults.retries",))
    assert rep["ok"] and rep["via_alias"] == ["service.retries"] and rep["collateral_paths"] == []


def test_expected_collection_may_change_freely():
    after = BASE_YAML.replace("tags: [a, b]", "tags: [a, b, c]")
    assert check(after, expect=("service.tags",))["ok"]


def test_added_comment_is_informational():
    rep = check(EDITED.replace("other:\n", "other:   # new\n"))
    assert rep["added_comments"] == [{"line": 12, "text": "# new"}] and rep["ok"] is False  # the line change is unexpected
    assert "added_comments" not in rep["violations"]


def test_json_report_and_exit_code_through_the_cli(tmp_path):
    before = write(tmp_path, "a.yaml", BASE_YAML)
    after = write(tmp_path, "b.yaml", INJECTIONS["lost_comments"])
    report = tmp_path / "report.json"
    code, out, _ = cli("verify", str(before), str(after), "--expect", "service.name", "--report", str(report), "--json")
    assert code == 1
    stdout_json = json.loads(out)
    file_json = json.loads(report.read_text(encoding="utf-8"))
    assert stdout_json == file_json and file_json["tool"] == "config-edit-guard" and file_json["status"] == "unintended_changes"
    assert report.read_text(encoding="utf-8").endswith("}\n")


def test_set_result_verifies_clean_against_the_original(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    orig = write(tmp_path, "c.yaml.orig", BASE_YAML)
    assert cli("set", str(f), "service.name", "gateway")[0] == 0
    code, out, _ = cli("verify", str(orig), str(f), "--expect", "service.name")
    assert code == 0 and "OK" in out  # the .orig suffix does not stop format detection


def test_json_and_toml_and_xml_pairs():
    before = '{\n  // c\n  "a": 1,\n  "b": 2\n}\n'
    after = '{\n  "a": 1,\n  "b": 3\n}\n'
    rep = verify_sources(src(before), src(after), "jsonc", ["b"], set())
    assert rep["lost_comments"] == [{"line": 2, "text": "// c"}] and not rep["ok"]
    xml_b = "<r>\n  <!-- c -->\n  <a x='1'>1</a>\n</r>\n"
    xml_a = "<r>\n  <a x='1'>2</a>\n</r>\n"
    rep = verify_sources(src(xml_b), src(xml_a), "xml", ["r.a"], set())
    assert rep["lost_comments"] and rep["collateral_paths"] == []


def test_multi_document_paths_carry_a_document_prefix():
    before = "a: 1\n---\na: 2\n"
    after = "a: 1\n---\na: 3\n"
    rep = verify_sources(src(before), src(after), "yaml", ["1:a"], set())
    assert rep["ok"] and rep["changed_paths"] == ["1:a"]
    rep = verify_sources(src(before), src(after), "yaml", ["a"], set())
    assert rep["collateral_paths"] == ["1:a"]
