"""AC-1: differential test against PyYAML (development-only dependency, never used by the scripts)."""
from __future__ import annotations

import pytest

yaml = pytest.importorskip("yaml")

import _ceg_yaml  # noqa: E402
from _ceg_model import Doc  # noqa: E402
from samples import BASE_YAML, DEPLOY_YAML, FOUR_SPACE_YAML, PROBE_YAML  # noqa: E402

FIXTURES = {
    "probe": PROBE_YAML,
    "base": BASE_YAML,
    "deploy": DEPLOY_YAML,
    "four_space": FOUR_SPACE_YAML,
    "compact": "items:\n  - name: a\n    n: 1\n  - name: b\n    n: 2\n  - - x\n    - y\nempty:\n",
    "flow": "m: {a: 1, b: [x, 'y z', \"w\"], c: {d: e}}\nl: [1, 2.5, true, null, ~]\n",
    "quoted_keys": "'a b': 1\n\"c d\": 'it''s'\nplain key: \"tab\\there\"\n",
    "unicode": "제목: 안녕하세요\nlist:\n  - 값1\n  - \"값2\"\n",
    "comments": "# top\na: 1   # one\n\n# between\nb: 'two'  # two\n",
    "seq_same_indent": "key:\n- a\n- b\nother: 1\n",
    "crlf": "a: 1\r\nb:\r\n  c: 'x'\r\n",
    "anchors": "base: &b\n  x: 1\nuse: *b\nlist: &l [1, 2]\ncopy: *l\n",
    "scalars": "a: yes\nb: no\nc: 0755\nd: 1.10\ne: 1e3\nf: .inf\ng: 190:20:30\nh: 2001-12-14\ni: ~\nj: 0x1F\nk: 1_000\nl: -.5\nm: +12\nn: 'yes'\n",
}

TAGS = {
    "tag:yaml.org,2002:str": "str", "tag:yaml.org,2002:int": "int", "tag:yaml.org,2002:float": "float",
    "tag:yaml.org,2002:bool": "bool", "tag:yaml.org,2002:null": "null", "tag:yaml.org,2002:timestamp": "timestamp",
    "tag:yaml.org,2002:merge": "str",
}


def compare(py, mine, doc: Doc, seen: set, spans: bool = True) -> None:
    if mine.kind == "alias":
        assert id(py) in seen
        return
    seen.add(id(py))
    if isinstance(py, yaml.ScalarNode):
        assert mine.kind == "scalar"
        if mine.style == "plain":
            assert py.value == mine.raw
            resolved = yaml.resolver.Resolver().resolve(yaml.ScalarNode, py.value, (True, False))
            assert TAGS.get(resolved, resolved) == _ceg_yaml.resolve11(mine.raw)[0]
        else:
            assert py.value == mine.value
        if spans and not mine.empty:
            assert py.start_mark.index == mine.start and py.end_mark.index == mine.end
            assert (py.start_mark.line + 1, py.start_mark.column + 1) == doc.line_col(mine.start)
    elif isinstance(py, yaml.MappingNode):
        assert mine.kind == "map" and len(py.value) == len(mine.children)
        for (kp, vp), child in zip(py.value, mine.children):
            compare(kp, child.key_node, doc, seen, spans)
            compare(vp, child, doc, seen, spans)
    else:
        assert mine.kind == "seq" and len(py.value) == len(mine.children)
        for vp, child in zip(py.value, mine.children):
            compare(vp, child, doc, seen, spans)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_values_and_spans_match_pyyaml_compose(name):
    text = FIXTURES[name]
    doc = _ceg_yaml.parse_yaml(text)
    compare(yaml.compose(text), doc.roots[0], doc, set())


@pytest.mark.parametrize("raw", ["yes", "No", "on", "OFF", "true", "0755", "0o17", "1.10", "1e3", "1.5", ".inf", "-.inf",
                                 "190:20:30", "0x1F", "0b101", "1_000", "-.5", "+12", "~", "null", "NULL", "abc", "2001-12-14", "y"])
def test_yaml11_resolution_matches_pyyaml(raw):
    loaded = yaml.safe_load("v: " + raw)["v"]
    mine = _ceg_yaml.resolve11(raw)
    if hasattr(loaded, "isoformat"):
        assert mine[0] == "timestamp"
    elif isinstance(loaded, float) and loaded != loaded:
        assert mine == ("float", "nan")
    else:
        assert mine == _typed(loaded)


def _typed(v):
    from _ceg_model import typed

    return typed(v)


def test_merge_and_duplicate_semantics_follow_pyyaml():
    doc = _ceg_yaml.parse_yaml(PROBE_YAML)
    flat = _ceg_yaml.flatten(doc)
    loaded = yaml.safe_load(PROBE_YAML)
    assert flat.values[("service", "timeout")] == ("int", loaded["service"]["timeout"])
    assert flat.values[("service", "port")] == ("int", loaded["service"]["port"])  # duplicate: last one wins
    assert flat.values[("service", "version")][0] == "float"
