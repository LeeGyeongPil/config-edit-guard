"""AC-2: XML set (.csproj, pom.xml, ...) edits the value in place; a library rewrite would not."""
from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

from conftest import cli, diff_entries, write
from samples import APP_CSPROJ


def test_target_framework_is_a_one_line_edit(tmp_path):
    f = write(tmp_path, "App.csproj", APP_CSPROJ)
    code, out, err = cli("set", str(f), "Project.PropertyGroup[0].TargetFramework", "net9.0", "--json")
    assert code == 0, err
    after = f.read_text(encoding="utf-8")
    assert after == APP_CSPROJ.replace("net8.0", "net9.0")
    assert after.startswith('<?xml version="1.0" encoding="utf-8"?>\n')
    assert "<Nullable   enable='true'>enable</Nullable>" in after
    assert len(diff_entries(APP_CSPROJ, after)) == 2
    assert json.loads(out)["normalized_path"] == "Project.PropertyGroup.TargetFramework"


def test_elementtree_rewrite_of_the_same_edit_is_a_six_line_diff():
    tree = ET.ElementTree(ET.fromstring(APP_CSPROJ, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))))
    tree.getroot().find("PropertyGroup/TargetFramework").text = "net9.0"
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    assert len(diff_entries(APP_CSPROJ, buf.getvalue().decode("utf-8"))) == 6


def test_attribute_edit_keeps_its_quote_style_and_spacing(tmp_path):
    f = write(tmp_path, "App.csproj", APP_CSPROJ)
    code, _, err = cli("set", str(f), "Project.PropertyGroup.Nullable.@enable", "false")
    assert code == 0, err
    assert f.read_text(encoding="utf-8") == APP_CSPROJ.replace("enable='true'", "enable='false'")


def test_escaping_in_text_and_attributes(tmp_path):
    f = write(tmp_path, "a.xml", '<r a="x"><t>old</t></r>\n')
    assert cli("set", str(f), "r.t", "a<b&c")[0] == 0
    assert cli("set", str(f), "r.@a", 'say "hi"')[0] == 0
    assert f.read_text(encoding="utf-8") == '<r a="say &quot;hi&quot;"><t>a&lt;b&amp;c</t></r>\n'
    code, out, _ = cli("locate", str(f), "r.t", "--json")
    assert json.loads(out)["value"] == "a<b&c"


def test_sibling_index_and_multiple_match_warning(tmp_path):
    text = "<p>\n  <i v='1'/>\n  <g><a>1</a></g>\n  <g><a>2</a></g>\n</p>\n"
    f = write(tmp_path, "p.xml", text)
    assert cli("set", str(f), "p.g[1].a", "9")[0] == 0
    assert f.read_text(encoding="utf-8").count("<a>9</a>") == 1
    code, out, _ = cli("locate", str(f), "p.g.a", "--json")
    assert code == 0 and json.loads(out)["warnings"] == ["xml_multiple:g:2"]


def test_pom_style_dotted_element_names(tmp_path):
    text = "<project><properties><java.version>11</java.version></properties></project>\n"
    f = write(tmp_path, "pom.xml", text)
    assert cli("set", str(f), 'project.properties."java.version"', "17")[0] == 0
    assert "<java.version>17</java.version>" in f.read_text(encoding="utf-8")


def test_container_self_closing_and_entity_text_are_refused(tmp_path):
    f = write(tmp_path, "a.xml", "<r><a><b>1</b></a><c/><d>x &amp; y</d><e><![CDATA[z]]></e></r>\n")
    for path, code_name in (("r.a", "E_NOT_SCALAR"), ("r.c", "E_UNSUPPORTED"), ("r.d", "E_UNSUPPORTED"), ("r.e", "E_UNSUPPORTED")):
        code, _, err = cli("set", str(f), path, "v")
        assert code == 2 and f"[{code_name}]" in err, (path, err)


def test_empty_element_text_can_be_set(tmp_path):
    f = write(tmp_path, "a.xml", "<r><a></a></r>\n")
    assert cli("set", str(f), "r.a", "v")[0] == 0
    assert f.read_text(encoding="utf-8") == "<r><a>v</a></r>\n"


def test_invalid_xml_and_wrong_root(tmp_path):
    f = write(tmp_path, "bad.xml", "<a><b></a>\n")
    code, _, err = cli("locate", str(f), "a")
    assert code == 2 and "[E_PARSE]" in err and "bad.xml:1:" in err
    g = write(tmp_path, "ok.xml", "<a><b>1</b></a>\n")
    code, _, err = cli("locate", str(g), "b")
    assert code == 2 and "[E_PATH_NOT_FOUND]" in err and "root element name a" in err


def test_verify_sees_lost_xml_comment_and_quote_change(tmp_path):
    before = write(tmp_path, "a.csproj", APP_CSPROJ)
    rewritten = APP_CSPROJ.replace("  <!-- removed Foo.dll: breaks arm64 build -->\n", "").replace("enable='true'", 'enable="true"')
    after = write(tmp_path, "b.csproj", rewritten)
    code, out, _ = cli("verify", str(before), str(after), "--json")
    rep = json.loads(out)
    assert code == 1
    assert rep["lost_comments"] == [{"line": 3, "text": "<!-- removed Foo.dll: breaks arm64 build -->"}]
    assert rep["quote_style_changed"] == ["Project.PropertyGroup.Nullable.@enable"]
