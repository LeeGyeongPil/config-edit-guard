"""Render a new scalar value and replace exactly its span; write files atomically."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Optional

import _ceg_formats
import _ceg_toml
import _ceg_xml
import _ceg_yaml
from _ceg_model import ConfigError, Doc, Node, Source, format_path, split_lines, to_bytes, typed

TYPES = ("auto", "str", "int", "float", "bool", "null")


@dataclass
class Rendered:
    text: str
    style: str
    type: str
    quote_added: bool = False
    quote_reason: Optional[str] = None


@dataclass
class EditPlan:
    doc: Doc
    node: Node  # the node whose span is replaced
    norm: tuple
    old_type: str
    old_value: Any
    rendered: Rendered
    new_text: str
    insert: bool


# ---------------------------------------------------------------- YAML rendering

_CONTROL = re.compile("[\x00-\x08\x0a-\x1f\x7f\x85\u2028\u2029]")


def _json_dq(value: str) -> str:
    out = json.dumps(value, ensure_ascii=False)
    return out.replace("\x7f", "\\x7f").replace("\x85", "\\N").replace("\u2028", "\\L").replace("\u2029", "\\P")


def yaml_quote_reason(v: str) -> Optional[str]:
    """Why a plain scalar ``v`` cannot be written unquoted without changing its meaning."""
    if v == "" or v != v.strip():
        return "indicator"
    if _CONTROL.search(v):
        return "indicator"
    c = v[0]
    if c in "-?:" and (len(v) == 1 or v[1] in " \t"):
        return "indicator"
    if c in ",[]{}#&*!|>'\"%@`":
        return "indicator"
    if ": " in v or " #" in v or v.endswith(":") or "\t#" in v:
        return "indicator"
    risk = _ceg_yaml.risk_reason(v)
    if risk in ("yaml11_bool", "yaml11_octal"):
        return risk
    r12 = _ceg_yaml.resolve12(v)
    if r12[0] == "float":
        try:
            lossless = repr(float(v)) == v
        except ValueError:
            lossless = False
        return "yaml12_non_string" if lossless else "float_lossy"
    if r12[0] != "str":
        return "yaml12_non_string"
    if risk:
        return risk
    return None


def render_yaml_str(v: str, style: str) -> Rendered:
    if style == "single":
        if _CONTROL.search(v):
            return Rendered(_json_dq(v), "double", "str", False, "indicator")
        return Rendered("'" + v.replace("'", "''") + "'", "single", "str")
    if style == "double":
        return Rendered(_json_dq(v), "double", "str")
    reason = yaml_quote_reason(v)
    if reason:
        return Rendered(_json_dq(v), "double", "str", True, reason)
    return Rendered(v, "plain", "str")


def _type_error(path_text: str, want: str, value: str, fmt_hint: str = "") -> ConfigError:
    return ConfigError("E_TYPE", "e.type.mismatch", path=path_text, want=want, value=value)


def render_yaml(node: Node, old_type: str, value: str, type_opt: str, path_text: str) -> Rendered:
    want = type_opt
    if want == "auto":
        if old_type in ("str", "int", "float", "bool", "null"):
            want = old_type
        else:
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.type_old", path=path_text, old=old_type)
        if old_type == "null":
            inferred = _ceg_yaml.resolve12(value)[0] if node.style == "plain" else "str"
            want = inferred if inferred in ("int", "float", "bool", "null") else "str"
    if want == "str":
        return render_yaml_str(value, node.style if node.style in ("single", "double") else "plain")
    r = _ceg_yaml.resolve12(value)[0]
    ok = {"int": r == "int", "float": r in ("int", "float"), "bool": r == "bool", "null": r == "null"}[want]
    if want == "null" and value == "":
        return Rendered("null", "plain", "null")
    if not ok or value != value.strip() or value == "":
        raise _type_error(path_text, want, value)
    return Rendered(value, "plain", want)


# ---------------------------------------------------------------- JSON / TOML / XML rendering

_JSON_INT = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_JSON_NUM = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$")


def render_json(old_type: str, value: str, type_opt: str, path_text: str) -> Rendered:
    want = type_opt
    if want == "auto":
        want = old_type if old_type in ("str", "int", "float", "bool", "null") else "str"
        if old_type == "null":
            if value == "null":
                want = "null"
            elif value in ("true", "false"):
                want = "bool"
            elif _JSON_NUM.match(value):
                want = "float" if _JSON_INT.match(value) is None else "int"
            else:
                want = "str"
    if want == "str":
        return Rendered(json.dumps(value, ensure_ascii=False), "double", "str")
    ok = {"int": bool(_JSON_INT.match(value)), "float": bool(_JSON_NUM.match(value)),
          "bool": value in ("true", "false"), "null": value == "null"}[want]
    if not ok:
        raise _type_error(path_text, want, value)
    return Rendered(value, "bare", want)


def _toml_dq(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def render_toml(node: Node, old_type: str, value: str, type_opt: str, path_text: str) -> Rendered:
    want = old_type if type_opt == "auto" else type_opt
    if want == "str":
        if node.style == "literal_str" and not re.search("[\x00-\x1f\x7f']", value):
            return Rendered("'" + value + "'", "literal_str", "str")
        return Rendered(_toml_dq(value), "basic", "str")
    if want == "null":
        raise ConfigError("E_TYPE", "e.type.toml_null", path=path_text)
    tomllib = _ceg_toml.import_tomllib()
    ok = False
    if value == value.strip() and value and "\n" not in value and "#" not in value:
        try:
            got = tomllib.loads("v = " + value)["v"]
            ok = typed(got)[0] == want
        except (tomllib.TOMLDecodeError, KeyError):
            ok = False
    if not ok:
        raise _type_error(path_text, want, value)
    return Rendered(value, "bare", want)


def xml_escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def xml_escape_attr(value: str, quote: str) -> str:
    out = value.replace("&", "&amp;").replace("<", "&lt;")
    out = out.replace(quote, "&quot;" if quote == '"' else "&apos;")
    return out.replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;")


def render_xml(node: Node, value: str, type_opt: str, path_text: str) -> Rendered:
    if type_opt in ("int", "float", "bool"):
        pattern = {"int": _JSON_INT, "float": _JSON_NUM, "bool": re.compile("^(?:true|false)$")}[type_opt]
        if not pattern.match(value):
            raise _type_error(path_text, type_opt, value)
    if node.style.startswith("attr_"):
        quote = "'" if node.style == "attr_single" else '"'
        return Rendered(quote + xml_escape_attr(value, quote) + quote, node.style, "str")
    return Rendered(xml_escape_text(value), "text", "str")


# ---------------------------------------------------------------- planning a set

def span_info(doc: Doc, bom: bool, start: int, end: int) -> dict:
    prefix = 3 if bom else 0
    line, col = doc.line_col(start)
    eline, ecol = doc.line_col(end)
    sb = prefix + len(doc.text[:start].encode("utf-8"))
    eb = sb + len(doc.text[start:end].encode("utf-8"))
    return {"start_byte": sb, "end_byte": eb, "line": line, "column": col, "end_line": eline, "end_column": ecol}


def _scalar_guard(doc: Doc, node: Node, path_text: str, fmt: str) -> Node:
    """The node whose span ``set`` replaces, or the right ConfigError."""
    fam = _ceg_formats.family(fmt)
    if fam == "xml":
        target = _ceg_xml.value_node(node)
        if _ceg_xml.is_container(node):
            names = []
            for c in node.children:
                if c.kind == "elem" and c.key not in names:
                    names.append(c.key)
            raise ConfigError("E_NOT_SCALAR", "e.not_scalar", path=path_text, kind="element", keys=", ".join(names[:8]))
        if node.kind == "elem" and node.self_closing:
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.xml_selfclosing", path=path_text)
        if target is None:
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.xml_text", path=path_text)
        if target.entity_text:
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.xml_entities", path=path_text)
        return target
    if node.kind in ("map", "seq"):
        if node.kind == "map":
            keys = ", ".join(str(c.key) for c in node.children[:8])
        else:
            keys = ", ".join(f"[{i}]" for i in range(min(len(node.children), 8)))
        raise ConfigError("E_NOT_SCALAR", "e.not_scalar", path=path_text, kind=node.kind, keys=keys)
    if node.kind == "alias":
        raise ConfigError("E_UNSUPPORTED", "e.unsupported.alias_value", path=path_text, name=node.alias)
    if node.style in ("literal", "folded"):
        raise ConfigError("E_UNSUPPORTED", "e.unsupported.block_scalar", path=path_text)
    if node.style in ("ml_basic", "ml_literal"):
        raise ConfigError("E_UNSUPPORTED", "e.unsupported.multiline_string", path=path_text)
    if node.tag:
        raise ConfigError("E_UNSUPPORTED", "e.unsupported.tagged", path=path_text, tag=node.tag)
    return node


def plan_set(source: Source, fmt: str, path_text: str, segs: tuple, doc_index: int, value: str, type_opt: str) -> EditPlan:
    doc = _ceg_formats.parse_text(fmt, source.text)
    mod = _ceg_formats.module_for(doc)
    node, norm = mod.resolve(doc, segs, path_text, doc_index)
    target = _scalar_guard(doc, node, path_text, fmt)
    flat = mod.flatten(doc)
    old = flat.values.get(norm) or flat.values.get(norm + (0,)) or ("str", target.raw)
    fam = _ceg_formats.family(fmt)
    if fam in ("yaml", "frontmatter"):
        rendered = render_yaml(target, old[0], value, type_opt, path_text)
    elif fam == "json":
        rendered = render_json(old[0], value, type_opt, path_text)
    elif fam == "toml":
        rendered = render_toml(target, old[0], value, type_opt, path_text)
    else:
        rendered = render_xml(target, value, type_opt, path_text)
    insert = target.start == target.end
    text = source.text
    if insert and fam in ("yaml", "frontmatter"):
        new_text = text[:target.start] + " " + rendered.text + text[target.end:]
    else:
        new_text = text[:target.start] + rendered.text + text[target.end:]
    return EditPlan(doc=doc, node=target, norm=norm, old_type=old[0], old_value=old[1], rendered=rendered,
                    new_text=new_text, insert=insert)


def changed_line_count(before: str, after: str) -> int:
    import difflib

    a, b = split_lines(before), split_lines(after)
    total = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            total += max(i2 - i1, j2 - j1)
    return total


def write_atomic(path: str, data: bytes) -> None:
    """Write ``data`` next to ``path`` and rename it into place, keeping the file's permissions."""
    real = os.path.realpath(path)
    folder = os.path.dirname(real) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".ceg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            shutil.copymode(real, tmp)
        except OSError:
            pass
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def final_bytes(source: Source, new_text: str) -> bytes:
    return to_bytes(new_text, source.bom)
