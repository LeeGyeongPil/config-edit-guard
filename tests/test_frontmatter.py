"""AC-1: Markdown front matter uses the YAML scanner and leaves the body alone."""
from __future__ import annotations

import json

from conftest import cli, write
from samples import SKILL_MD


def test_set_in_front_matter_changes_one_line_and_not_the_body(tmp_path):
    f = write(tmp_path, "notes.md", SKILL_MD)
    code, out, err = cli("set", str(f), "title", "Release notes 2", "--json")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == SKILL_MD.replace("title: Release notes", "title: Release notes 2")
    assert json.loads(out)["format"] == "frontmatter"


def test_flow_item_and_comment_in_front_matter(tmp_path):
    f = write(tmp_path, "notes.md", SKILL_MD)
    code, _, err = cli("set", str(f), "tags[1]", "c")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == SKILL_MD.replace("[a, b]   # keep", "[a, c]   # keep")


def test_boolean_stays_boolean(tmp_path):
    f = write(tmp_path, "notes.md", SKILL_MD)
    assert cli("set", str(f), "draft", "true")[0] == 0
    assert "draft: true" in f.read_text(encoding="utf-8")
    code, _, err = cli("set", str(f), "draft", "maybe")
    assert code == 2 and "[E_TYPE]" in err


def test_locate_span_is_inside_the_front_matter(tmp_path):
    f = write(tmp_path, "notes.md", SKILL_MD)
    code, out, _ = cli("locate", str(f), "title", "--json")
    rep = json.loads(out)
    raw = f.read_bytes()
    assert raw[rep["span"]["start_byte"]:rep["span"]["end_byte"]] == b"Release notes"
    assert rep["span"]["line"] == 2


def test_markdown_without_front_matter_is_a_defined_error(tmp_path):
    f = write(tmp_path, "plain.md", "# Just a heading\n")
    code, _, err = cli("locate", str(f), "title")
    assert code == 2 and "[E_PARSE]" in err and "fix:" in err


def test_verify_checks_front_matter_and_flags_body_changes(tmp_path):
    before = write(tmp_path, "a.md", SKILL_MD)
    after_ok = write(tmp_path, "b.md", SKILL_MD.replace("title: Release notes", "title: New"))
    assert cli("verify", str(before), str(after_ok), "--expect", "title")[0] == 0
    after_body = write(tmp_path, "c.md", SKILL_MD.replace("Body text", "Changed body text"))
    code, out, _ = cli("verify", str(before), str(after_body), "--json")
    assert code == 1 and "unexpected_line_changes" in json.loads(out)["violations"]


def test_verify_detects_comment_loss_in_front_matter(tmp_path):
    before = write(tmp_path, "a.md", SKILL_MD)
    after = write(tmp_path, "b.md", SKILL_MD.replace("   # keep", ""))
    code, out, _ = cli("verify", str(before), str(after), "--json")
    rep = json.loads(out)
    assert code == 1 and rep["lost_comments"] == [{"line": 3, "text": "# keep"}]
