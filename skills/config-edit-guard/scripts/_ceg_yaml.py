"""YAML subset scanner with exact value spans, scalar resolution and front matter.

Supported: block mappings and sequences (including the compact ``- key: v`` form),
single-line plain / single-quoted / double-quoted scalars, flow collections that fit on
one line, ``|`` and ``>`` block scalars (read only), comments, anchors, aliases, ``<<``
merge keys, tags (kept as opaque labels) and several documents.  Everything else raises
``E_UNSUPPORTED`` with the line and column instead of guessing.
"""
from __future__ import annotations

import re
from typing import Optional

from _ceg_model import (
    Comment, ConfigError, Doc, DocTag, Flat, Node, collect_dups, lookup_generic, resolve_generic,
    typed, walk_tree,
)

# ---------------------------------------------------------------- scalar resolution

_NULL12 = {"", "~", "null", "Null", "NULL"}
_BOOL12 = {"true": True, "True": True, "TRUE": True, "false": False, "False": False, "FALSE": False}
_INT12 = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_FLOAT12 = re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")

_BOOL11 = {"yes": True, "Yes": True, "YES": True, "no": False, "No": False, "NO": False,
           "true": True, "True": True, "TRUE": True, "false": False, "False": False, "FALSE": False,
           "on": True, "On": True, "ON": True, "off": False, "Off": False, "OFF": False}
_INT11 = re.compile(r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$")
_FLOAT11 = re.compile(r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_TIMESTAMP11 = re.compile(r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)$")
_YN_EXTRA = {"y", "Y", "n", "N"}
_OCTAL_RE = re.compile(r"^[-+]?0[0-7_]+$")


def _num_float(text: str) -> float:
    t = text.replace("_", "")
    low = t.lstrip("+-").lower()
    if low == ".inf":
        return float("-inf") if t.startswith("-") else float("inf")
    if low == ".nan":
        return float("nan")
    if ":" in t:
        sign = -1.0 if t.startswith("-") else 1.0
        total = 0.0
        for part in t.lstrip("+-").split(":"):
            total = total * 60 + float(part)
        return sign * total
    return float(t)


def _int11(text: str) -> int:
    t = text.replace("_", "")
    sign = -1 if t.startswith("-") else 1
    body = t.lstrip("+-")
    if ":" in body:
        total = 0
        for part in body.split(":"):
            total = total * 60 + int(part)
        return sign * total
    if body.startswith("0b"):
        return sign * int(body[2:], 2)
    if body.startswith("0x"):
        return sign * int(body[2:], 16)
    if len(body) > 1 and body.startswith("0"):
        return sign * int(body, 8)
    return sign * int(body)


def resolve12(raw: str) -> tuple:
    """Type-tagged value of a plain scalar under the YAML 1.2 core schema."""
    if raw in _NULL12:
        return ("null", None)
    if raw in _BOOL12:
        return ("bool", _BOOL12[raw])
    if _INT12.match(raw):
        body = raw.lstrip("+-")
        if body.startswith("0o"):
            return ("int", int(body[2:], 8) * (-1 if raw.startswith("-") else 1))
        if body.startswith("0x"):
            return ("int", int(body[2:], 16) * (-1 if raw.startswith("-") else 1))
        return ("int", int(raw))
    if _FLOAT12.match(raw):
        return typed(_num_float(raw))
    return ("str", raw)


def resolve11(raw: str) -> tuple:
    """Type-tagged value of a plain scalar the way PyYAML's YAML 1.1 resolver reads it."""
    if raw in _NULL12:
        return ("null", None)
    if raw in _BOOL11:
        return ("bool", _BOOL11[raw])
    if _FLOAT11.match(raw):
        return typed(_num_float(raw))
    if _INT11.match(raw):
        return ("int", _int11(raw))
    if _TIMESTAMP11.match(raw):
        return ("timestamp", raw)
    return ("str", raw)


def risk_reason(raw: str) -> Optional[str]:
    """Why a plain scalar is read differently by YAML 1.1 loaders, or None when it is safe."""
    if raw in _YN_EXTRA or (raw in _BOOL11 and raw not in _BOOL12):
        return "yaml11_bool"
    if _OCTAL_RE.match(raw) and resolve11(raw)[0] == "int":
        return "yaml11_octal"
    if resolve12(raw) != resolve11(raw):
        return "yaml11_other"
    return None


# ---------------------------------------------------------------- low level text helpers

_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
            "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
            "L": " ", "P": " "}


def quoted_end(s: str, pos: int) -> Optional[int]:
    """Index just after the closing quote of the quoted scalar starting at ``pos`` (None if open)."""
    q = s[pos]
    i = pos + 1
    n = len(s)
    while i < n:
        c = s[i]
        if q == "'":
            if c == "'":
                if i + 1 < n and s[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
        else:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return i + 1
        i += 1
    return None


def decode_quoted(inner_with_quotes: str, line: int, col: int) -> str:
    q = inner_with_quotes[0]
    body = inner_with_quotes[1:-1]
    if q == "'":
        return body.replace("''", "'")
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise ConfigError("E_PARSE", "e.parse.yaml.escape", line=line, col=col, esc="\\")
        e = body[i]
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 1
        elif e in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[e]
            digits = body[i + 1:i + 1 + width]
            if len(digits) != width or not re.match(r"^[0-9a-fA-F]+$", digits):
                raise ConfigError("E_PARSE", "e.parse.yaml.escape", line=line, col=col, esc="\\" + e + digits)
            out.append(chr(int(digits, 16)))
            i += 1 + width
        else:
            raise ConfigError("E_PARSE", "e.parse.yaml.escape", line=line, col=col, esc="\\" + e)
    return "".join(out)


class _Line:
    __slots__ = ("start", "text", "indent", "blank", "comment_only")

    def __init__(self, start: int, text: str) -> None:
        self.start = start
        self.text = text
        stripped = text.lstrip(" ")
        self.indent = len(text) - len(stripped)
        self.blank = text.strip() == ""
        self.comment_only = (not self.blank) and text.lstrip(" \t").startswith("#")


def split_text_lines(text: str) -> list:
    lines = []
    pos = 0
    for m in re.finditer(r"\r\n|\n|\r", text):
        lines.append(_Line(pos, text[pos:m.start()]))
        pos = m.end()
    if pos < len(text):
        lines.append(_Line(pos, text[pos:]))
    return lines


def _is_dash(s: str, pos: int) -> bool:
    return pos < len(s) and s[pos] == "-" and (pos + 1 == len(s) or s[pos + 1] in " \t")


_FLOW_STOP = ",[]{}"


class _Scanner:
    def __init__(self, doc_text: str, lines: list, lo: int, hi: int) -> None:
        self.text = doc_text
        self.lines = lines
        self.pos = lo
        self.hi = hi
        self.comments: list = []
        self.anchor_nodes: dict = {}
        self.anchor_lines: dict = {}
        self.depth = 0

    # -- positions
    def lc(self, li: int, col: int) -> tuple:
        return li + 1, col + 1

    def err(self, key: str, li: int, col: int, **params) -> ConfigError:
        code = "E_UNSUPPORTED" if key.startswith("e.unsupported") else "E_PARSE"
        return ConfigError(code, key, line=li + 1, col=col + 1, **params)

    def off(self, li: int, col: int) -> int:
        return self.lines[li].start + col

    # -- line navigation
    def sig(self) -> Optional[int]:
        """Skip blank and comment-only lines (recording comments); index of the next real line."""
        while self.pos < self.hi:
            ln = self.lines[self.pos]
            if ln.blank:
                self.pos += 1
                continue
            if ln.comment_only:
                idx = ln.text.index("#")
                self.comments.append(Comment(self.pos + 1, idx + 1, ln.text[idx:].rstrip()))
                self.pos += 1
                continue
            lead = ln.text[:len(ln.text) - len(ln.text.lstrip(" \t"))]
            if "\t" in lead:
                raise self.err("e.parse.yaml.tab", self.pos, lead.index("\t"))
            return self.pos
        return None

    def peek_next_content(self, after: int) -> Optional[int]:
        """Next non-blank line index after ``after`` without recording comments."""
        j = after
        while j < self.hi:
            if not self.lines[j].blank:
                return j
            j += 1
        return None

    def rest_comment(self, li: int, col: int) -> None:
        """Validate and record what follows a finished value on its line."""
        s = self.lines[li].text
        rest = s[col:]
        if rest.strip() == "":
            return
        j = col
        while j < len(s) and s[j] in " \t":
            j += 1
        if s[j] == "#" and (j > col or j == 0 or s[j - 1] in " \t"):
            self.comments.append(Comment(li + 1, j + 1, s[j:].rstrip()))
            return
        raise self.err("e.parse.yaml.trailing", li, j, text=s[j:j + 20])

    # -- keys
    def scan_key(self, li: int, pos: int):
        s = self.lines[li].text
        if pos >= len(s):
            return None
        c = s[pos]
        if c == "?" and (pos + 1 == len(s) or s[pos + 1] in " \t"):
            raise self.err("e.unsupported.complex_key", li, pos)
        if c in "'\"":
            end = quoted_end(s, pos)
            if end is None:
                return None
            j = end
            while j < len(s) and s[j] in " \t":
                j += 1
            if j < len(s) and s[j] == ":" and (j + 1 == len(s) or s[j + 1] in " \t"):
                text = decode_quoted(s[pos:end], li + 1, pos + 1)
                return text, pos, end, j, "single" if c == "'" else "double"
            return None
        if c in "[{":
            return None
        if c in "&*!|>%@`":
            if self._plain_key_end(s, pos) is not None:
                raise self.err("e.unsupported.key_prefix", li, pos, ch=c)
            return None
        i = self._plain_key_end(s, pos)
        if i is None:
            return None
        key = s[pos:i].rstrip()
        if key == "":
            return None
        return key, pos, pos + len(key), i, "plain"

    @staticmethod
    def _plain_key_end(s: str, pos: int) -> Optional[int]:
        i = pos
        n = len(s)
        while i < n:
            ch = s[i]
            if ch == ":" and (i + 1 == n or s[i + 1] in " \t"):
                return i
            if ch == "#" and i > pos and s[i - 1] in " \t":
                return None
            i += 1
        return None

    # -- structure
    def parse_node(self, li: int, pos: int, parent_indent: int, inline_ok: bool, same_indent_seq: bool = False) -> Node:
        self.depth += 1
        if self.depth > 150:
            raise self.err("e.parse.too_deep", li, pos)
        try:
            return self._parse_node(li, pos, parent_indent, inline_ok, same_indent_seq)
        finally:
            self.depth -= 1

    def _parse_node(self, li, pos, parent_indent, inline_ok, same_indent_seq) -> Node:
        s = self.lines[li].text
        while pos < len(s) and s[pos] in " \t":
            pos += 1
        anchor = tag = None
        while pos < len(s) and s[pos] in "&!":
            m = re.match(r"[^\s,\[\]{}]+", s[pos:])
            token = m.group(0) if m else s[pos]
            if s[pos] == "&":
                anchor = token[1:]
                if anchor == "":
                    raise self.err("e.parse.yaml.anchor_name", li, pos)
            else:
                tag = token
            pos += len(token)
            while pos < len(s) and s[pos] in " \t":
                pos += 1
        rest = s[pos:]
        node: Node
        if rest == "" or (rest.startswith("#") and (pos == 0 or s[pos - 1] in " \t")):
            cpos = pos
            if rest.startswith("#"):
                self.comments.append(Comment(li + 1, pos + 1, rest.rstrip()))
            self.pos = li + 1
            nxt = self.sig()
            end_of_content = len(s[:cpos].rstrip())
            if nxt is not None and (self.lines[nxt].indent > parent_indent or
                                    (same_indent_seq and self.lines[nxt].indent == parent_indent and
                                     _is_dash(self.lines[nxt].text, self.lines[nxt].indent))):
                node = self.parse_block(nxt, self.lines[nxt].indent, parent_indent)
            else:
                o = self.off(li, max(end_of_content, 0))
                node = Node("scalar", o, o, style="plain", value=None, raw="", empty=True)
        else:
            if (anchor or tag) and inline_ok and (_is_dash(s, pos) or self.scan_key(li, pos)):
                raise self.err("e.unsupported.props_before_key", li, pos)
            node = self._inline_value(li, pos, parent_indent, inline_ok)
        node.anchor = anchor
        node.tag = tag
        if anchor:
            self.anchor_nodes[anchor] = node
            self.anchor_lines.setdefault(anchor, li + 1)
        return node

    def parse_block(self, li: int, indent: int, parent_indent: int) -> Node:
        s = self.lines[li].text
        if _is_dash(s, indent):
            return self.parse_seq(li, indent)
        if self.scan_key(li, indent):
            return self.parse_map(li, indent)
        return self.parse_node(li, indent, parent_indent, inline_ok=False)

    def parse_seq(self, li: int, col: int) -> Node:
        node = Node("seq", self.off(li, col), self.off(li, col), style="block")
        while True:
            s = self.lines[li].text
            vpos = col + 1
            while vpos < len(s) and s[vpos] in " \t":
                vpos += 1
            item = self.parse_node(li, vpos, col, inline_ok=True)
            node.children.append(item)
            node.end = item.end
            nxt = self.sig()
            if nxt is None or self.lines[nxt].indent < col:
                break
            ln = self.lines[nxt]
            if ln.indent > col:
                raise self.err("e.parse.yaml.indent", nxt, ln.indent)
            if not _is_dash(ln.text, col):
                break
            li = nxt
        return node

    def parse_map(self, li: int, col: int) -> Node:
        node = Node("map", self.off(li, col), self.off(li, col), style="block")
        while True:
            k = self.scan_key(li, col)
            if k is None:
                raise self.err("e.parse.yaml.expected_key", li, col)
            text, ks, ke, colon, kstyle = k
            kn = Node("scalar", self.off(li, ks), self.off(li, ke), style=kstyle, value=text,
                      raw=self.lines[li].text[ks:ke])
            val = self.parse_node(li, colon + 1, col, inline_ok=False, same_indent_seq=True)
            val.key = text
            val.key_node = kn
            node.children.append(val)
            node.end = val.end
            if text == "<<" and kstyle == "plain":
                self._collect_merges(node, val, li, ks)
            nxt = self.sig()
            if nxt is None or self.lines[nxt].indent < col:
                break
            ln = self.lines[nxt]
            if ln.indent > col:
                raise self.err("e.parse.yaml.indent", nxt, ln.indent)
            li = nxt
        return node

    def _collect_merges(self, mapping: Node, val: Node, li: int, col: int) -> None:
        items = val.children if val.kind == "seq" else [val]
        for it in items:
            if it.kind != "alias" or it.target is None or it.target.kind != "map":
                raise self.err("e.parse.yaml.merge", li, col)
            mapping.merges.append(it.target)

    # -- inline values
    def _inline_value(self, li: int, pos: int, parent_indent: int, inline_ok: bool) -> Node:
        s = self.lines[li].text
        rest = s[pos:]
        c = rest[0]
        if _is_dash(s, pos):
            if not inline_ok:
                raise self.err("e.parse.yaml.nested_block", li, pos)
            return self.parse_seq(li, pos)
        if inline_ok and self.scan_key(li, pos):
            return self.parse_map(li, pos)
        if c in "@`%":
            raise self.err("e.parse.yaml.reserved", li, pos, ch=c)
        if c == "*":
            m = re.match(r"\*([^\s,\[\]{}]+)", rest)
            if not m:
                raise self.err("e.parse.yaml.alias_name", li, pos)
            name = m.group(1)
            if name not in self.anchor_nodes:
                raise self.err("e.parse.yaml.alias_unknown", li, pos, name=name)
            node = Node("alias", self.off(li, pos), self.off(li, pos + m.end()), style="alias", raw=m.group(0),
                        alias=name, target=self.anchor_nodes[name])
            self.rest_comment(li, pos + m.end())
            self.pos = li + 1
            return node
        if c in "|>":
            return self._block_scalar(li, pos, parent_indent)
        if c in "[{":
            node, end = self._flow(li, pos, 0)
            self.rest_comment(li, end)
            self.pos = li + 1
            self._no_continuation(li, parent_indent)
            return node
        if c in "'\"":
            end = quoted_end(s, pos)
            if end is None:
                raise self.err("e.unsupported.multiline_quoted", li, pos)
            raw = s[pos:end]
            node = Node("scalar", self.off(li, pos), self.off(li, end), style="single" if c == "'" else "double",
                        raw=raw, value=("str", decode_quoted(raw, li + 1, pos + 1)))
            node.value = node.value[1]
            self.rest_comment(li, end)
            self.pos = li + 1
            self._no_continuation(li, parent_indent)
            return node
        end = self._plain_end(s, pos, li)
        raw = s[pos:end]
        node = Node("scalar", self.off(li, pos), self.off(li, end), style="plain", raw=raw)
        node.value = raw
        self.rest_comment(li, end)
        self.pos = li + 1
        self._no_continuation(li, parent_indent)
        return node

    def _plain_end(self, s: str, pos: int, li: int) -> int:
        i = pos
        n = len(s)
        end = n
        while i < n:
            ch = s[i]
            if ch == "#" and i > pos and s[i - 1] in " \t":
                end = i
                break
            if ch == ":" and (i + 1 == n or s[i + 1] in " \t"):
                raise self.err("e.parse.yaml.mapping_here", li, i)
            i += 1
        while end > pos and s[end - 1] in " \t":
            end -= 1
        return end

    def _no_continuation(self, li: int, parent_indent: int) -> None:
        nxt = self.peek_next_content(li + 1)
        if nxt is None:
            return
        ln = self.lines[nxt]
        if ln.comment_only:
            return
        if ln.indent > parent_indent:
            if self.scan_key(nxt, ln.indent):
                raise self.err("e.parse.yaml.indent", nxt, ln.indent)
            raise self.err("e.unsupported.multiline_scalar", nxt, ln.indent)

    def _block_scalar(self, li: int, pos: int, parent_indent: int) -> Node:
        s = self.lines[li].text
        m = re.match(r"([|>])([-+]?)([1-9]?)([-+]?)[ \t]*(#.*)?$", s[pos:])
        if not m:
            raise self.err("e.parse.yaml.block_header", li, pos)
        style = "literal" if m.group(1) == "|" else "folded"
        chomp = m.group(2) or m.group(4)
        explicit = int(m.group(3)) if m.group(3) else 0
        if m.group(5):
            self.comments.append(Comment(li + 1, pos + m.start(5) + 1, m.group(5).rstrip()))
        base = max(parent_indent, 0) if parent_indent >= 0 else 0
        j = li + 1
        block_indent = (base + explicit) if explicit else None
        content: list = []
        while j < self.hi:
            ln = self.lines[j]
            if ln.blank:
                content.append(j)
                j += 1
                continue
            if block_indent is None:
                if ln.indent <= parent_indent:
                    break
                block_indent = ln.indent
            if ln.indent < block_indent:
                break
            content.append(j)
            j += 1
        if block_indent is None:
            block_indent = 0
        while content and self.lines[content[-1]].blank:
            content.pop()
            j -= 1
        trailing_blank = 0
        # lines after the last content line that were blank belong to the chomp indicator
        t = content[-1] + 1 if content else li + 1
        while t < self.hi and self.lines[t].blank:
            trailing_blank += 1
            t += 1
        body_lines = [self.lines[x].text[block_indent:] if not self.lines[x].blank else "" for x in content]
        if style == "literal":
            value = "\n".join(body_lines)
        else:
            value = _fold(body_lines)
        if body_lines:
            if chomp == "-":
                pass
            elif chomp == "+":
                value += "\n" * (1 + trailing_blank)
            else:
                value += "\n"
        if content:
            first = content[0]
            while first < content[-1] and self.lines[first].blank:
                first += 1
            start = self.off(first, min(block_indent, len(self.lines[first].text)))
            end = self.off(content[-1], len(self.lines[content[-1]].text))
            raw = self.text[start:end]
        else:
            start = end = self.off(li, len(s))
            raw = ""
        self.pos = (content[-1] + 1) if content else li + 1
        node = Node("scalar", start, end, style=style, value=value, raw=raw)
        return node

    # -- flow collections (single line)
    def _flow(self, li: int, pos: int, depth: int):
        if depth > 40:
            raise self.err("e.parse.too_deep", li, pos)
        s = self.lines[li].text
        opener = s[pos]
        closer = "]" if opener == "[" else "}"
        node = Node("seq" if opener == "[" else "map", self.off(li, pos), self.off(li, pos), style="flow")
        i = pos + 1
        n = len(s)
        while True:
            while i < n and s[i] in " \t":
                i += 1
            if i >= n:
                raise self.err("e.unsupported.multiline_flow", li, pos)
            if s[i] == closer:
                i += 1
                break
            if s[i] == ",":
                raise self.err("e.parse.yaml.flow_comma", li, i)
            if opener == "[":
                item, i = self._flow_item(li, i, depth, key_ok=False)
                node.children.append(item)
            else:
                key_text, kn, i = self._flow_key(li, i, depth)
                while i < n and s[i] in " \t":
                    i += 1
                if i < n and s[i] == ":":
                    i += 1
                    while i < n and s[i] in " \t":
                        i += 1
                    if i < n and s[i] not in ",}":
                        val, i = self._flow_item(li, i, depth, key_ok=False)
                    else:
                        o = self.off(li, i)
                        val = Node("scalar", o, o, style="plain", value=None, raw="", empty=True)
                else:
                    o = self.off(li, i)
                    val = Node("scalar", o, o, style="plain", value=None, raw="", empty=True)
                val.key = key_text
                val.key_node = kn
                node.children.append(val)
            while i < n and s[i] in " \t":
                i += 1
            if i >= n:
                raise self.err("e.unsupported.multiline_flow", li, pos)
            if s[i] == ",":
                i += 1
                continue
            if s[i] == closer:
                i += 1
                break
            raise self.err("e.parse.yaml.flow_expected", li, i, ch=s[i])
        node.end = self.off(li, i)
        return node, i

    def _flow_key(self, li, i, depth):
        s = self.lines[li].text
        if s[i] in "'\"":
            end = quoted_end(s, i)
            if end is None:
                raise self.err("e.unsupported.multiline_quoted", li, i)
            kn = Node("scalar", self.off(li, i), self.off(li, end), style="single" if s[i] == "'" else "double",
                      raw=s[i:end], value=decode_quoted(s[i:end], li + 1, i + 1))
            return kn.value, kn, end
        j = i
        n = len(s)
        while j < n and s[j] not in ",}" and not (s[j] == ":" and (j + 1 == n or s[j + 1] in " \t,}")):
            if s[j] in "[{":
                raise self.err("e.unsupported.complex_key", li, j)
            j += 1
        raw = s[i:j].rstrip()
        kn = Node("scalar", self.off(li, i), self.off(li, i + len(raw)), style="plain", raw=raw, value=raw)
        return raw, kn, i + len(raw)

    def _flow_item(self, li, i, depth, key_ok):
        s = self.lines[li].text
        c = s[i]
        n = len(s)
        if c in "[{":
            return self._flow(li, i, depth + 1)
        if c == "*":
            m = re.match(r"\*([^\s,\[\]{}]+)", s[i:])
            if not m:
                raise self.err("e.parse.yaml.alias_name", li, i)
            name = m.group(1)
            if name not in self.anchor_nodes:
                raise self.err("e.parse.yaml.alias_unknown", li, i, name=name)
            return Node("alias", self.off(li, i), self.off(li, i + m.end()), style="alias", raw=m.group(0), alias=name,
                        target=self.anchor_nodes[name]), i + m.end()
        if c in "&!":
            raise self.err("e.unsupported.flow_props", li, i)
        if c in "'\"":
            end = quoted_end(s, i)
            if end is None:
                raise self.err("e.unsupported.multiline_quoted", li, i)
            node = Node("scalar", self.off(li, i), self.off(li, end), style="single" if c == "'" else "double",
                        raw=s[i:end], value=decode_quoted(s[i:end], li + 1, i + 1))
            return node, end
        j = i
        while j < n and s[j] not in ",]}":
            if s[j] == ":" and (j + 1 == n or s[j + 1] in " \t,]}"):
                raise self.err("e.unsupported.flow_pair", li, j)
            if s[j] == "#" and j > i and s[j - 1] in " \t":
                break
            j += 1
        raw = s[i:j].rstrip()
        if raw == "":
            raise self.err("e.parse.yaml.flow_expected", li, i, ch=s[i] if i < n else "")
        node = Node("scalar", self.off(li, i), self.off(li, i + len(raw)), style="plain", raw=raw, value=raw)
        return node, i + len(raw)


def _fold(lines: list) -> str:
    out = ""
    pending_nl = 0
    first = True
    for ln in lines:
        if ln == "":
            pending_nl += 1
            continue
        if first:
            out = ln
            first = False
        elif pending_nl:
            out += "\n" * pending_nl + ln
        elif ln[0] in " \t" or out.split("\n")[-1][:1] in (" ", "\t"):
            out += "\n" + ln
        else:
            out += " " + ln
        pending_nl = 0
    return out


# ---------------------------------------------------------------- documents

def _has_content(lines: list, a: int, b: int) -> bool:
    return any(not (lines[j].blank or lines[j].comment_only) for j in range(a, b))


def _document_ranges(lines: list, lo: int, hi: int) -> list:
    """(first line, end line, keep) per document; ranges without content are scanned only for comments."""
    out = []
    cur = lo
    explicit = False
    started = False
    for j in range(lo, hi):
        t = lines[j].text
        if t.startswith("%") and not started and not explicit:
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.directive", line=j + 1, col=1)
        is_start = t.startswith("---") and (len(t) == 3 or t[3] in " \t")
        is_end = t.startswith("...") and (len(t) == 3 or t[3] in " \t")
        if not (is_start or is_end):
            continue
        tail = t[3:].strip()
        if tail and not tail.startswith("#"):
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.marker_content", line=j + 1, col=4)
        out.append((cur, j, _has_content(lines, cur, j)))
        cur = j + 1
        explicit = is_start
        started = True
    out.append((cur, hi, _has_content(lines, cur, hi)))
    return out


def _scan_range(text: str, lines: list, lo: int, hi: int):
    sc = _Scanner(text, lines, lo, hi)
    first = sc.sig()
    if first is None:
        o = lines[lo].start if lo < len(lines) else len(text)
        root = Node("scalar", o, o, style="plain", value=None, raw="", empty=True)
        return root, sc
    root = sc.parse_node(first, sc.lines[first].indent, -1, inline_ok=True)
    nxt = sc.sig()
    if nxt is not None:
        raise sc.err("e.parse.yaml.extra", nxt, sc.lines[nxt].indent)
    return root, sc


def parse_yaml(text: str, line_range: Optional[tuple] = None, fmt: str = "yaml") -> Doc:
    lines = split_text_lines(text)
    if line_range is None:
        ranges = _document_ranges(lines, 0, len(lines))
    else:
        ranges = [(line_range[0], line_range[1], True)]
    roots: list = []
    comments: list = []
    anchors: dict = {}
    for a, b, keep in ranges:
        root, sc = _scan_range(text, lines, a, b)
        if keep:
            roots.append(root)
        comments.extend(sc.comments)
        for k, v in sc.anchor_lines.items():
            anchors.setdefault(k, v)
    if not roots:
        o = len(text)
        roots.append(Node("scalar", o, o, style="plain", value=None, raw="", empty=True))
    if line_range is None:
        for j, ln in enumerate(lines):
            t = ln.text
            if (t.startswith("---") or t.startswith("...")) and "#" in t[3:]:
                idx = t.index("#", 3)
                comments.append(Comment(j + 1, idx + 1, t[idx:].rstrip()))
    comments.sort(key=lambda c: (c.line, c.col))
    return Doc(fmt, text, roots, comments=comments, anchors=anchors)


def find_frontmatter(text: str) -> Optional[tuple]:
    """Line index range (first line after the opening ---, closing line) of a leading front matter block."""
    lines = split_text_lines(text)
    if not lines or lines[0].text.rstrip() != "---":
        return None
    for j in range(1, len(lines)):
        t = lines[j].text.rstrip()
        if t in ("---", "..."):
            return 1, j
    return None


def parse_frontmatter(text: str) -> Doc:
    rng = find_frontmatter(text)
    if rng is None:
        lines = split_text_lines(text)
        o = 0
        root = Node("scalar", o, o, style="plain", value=None, raw="", empty=True)
        doc = Doc("frontmatter", text, [root])
        doc.warnings.append("no_frontmatter")
        return doc
    return parse_yaml(text, rng, fmt="frontmatter")


# ---------------------------------------------------------------- path resolution

def _doc_root(doc: Doc, doc_index: int) -> Node:
    if doc_index >= len(doc.roots):
        raise ConfigError("E_PATH_NOT_FOUND", "e.path.doc_range", doc=doc_index, count=len(doc.roots))
    return doc.roots[doc_index]


def resolve(doc: Doc, segs: tuple, path_text: str, doc_index: int = 0):
    if doc.fmt == "frontmatter" and "no_frontmatter" in doc.warnings:
        raise ConfigError("E_PARSE", "e.parse.no_frontmatter", line=1, col=1)
    segs = tuple(s for s in segs if not isinstance(s, DocTag))
    node = resolve_generic(doc, _doc_root(doc, doc_index), segs, path_text)
    full = ((DocTag(f"{doc_index}:"),) if doc_index > 0 else ()) + segs
    return node, full


def lookup(doc: Doc, segs: tuple) -> Optional[Node]:
    idx = 0
    if segs and isinstance(segs[0], DocTag):
        idx = int(str(segs[0])[:-1])
        segs = segs[1:]
    if idx >= len(doc.roots):
        return None
    return lookup_generic(doc.roots[idx], segs)


def _scalar_value(node: Node) -> tuple:
    if node.tag:
        return ("tagged", f"{node.tag} {node.raw}")
    if node.style == "plain":
        return resolve12(node.raw)
    if node.style in ("single", "double", "literal", "folded"):
        return ("str", node.value)
    return ("str", node.raw)


def flatten(doc: Doc) -> Flat:
    flat = Flat()
    multi = len(doc.roots) > 1

    def entries(node: Node) -> list:
        own = [c for c in node.children if not (c.key == "<<" and node.merges)]
        seen = {c.key for c in own}
        out = [(c.key, c, True) for c in own]
        for target in node.merges:
            for key, child, _own in entries(target):
                if key not in seen:
                    seen.add(key)
                    out.append((key, child, False))
        return out

    def emit(node: Node, path: tuple, spath: tuple) -> None:
        if spath == path:
            node.spath = path
        if node.kind == "alias":
            target = node.target
            emit(target, path, target.spath if target.spath is not None else path)
            return
        if node.kind == "scalar":
            flat.values[path] = _scalar_value(node)
            if spath != path:
                flat.via[path] = spath
            return
        if node.kind == "seq":
            if not node.children:
                flat.values[path] = ("seq", 0)
                if spath != path:
                    flat.via[path] = spath
            for i, child in enumerate(node.children):
                emit(child, path + (i,), spath + (i,))
            return
        items = entries(node)
        if not items:
            flat.values[path] = ("map", 0)
            if spath != path:
                flat.via[path] = spath
        for key, child, is_own in items:
            if is_own:
                emit(child, path + (key,), spath + (key,))
            else:
                src = child.spath if child.spath is not None else spath + (key,)
                emit(child, path + (key,), src)

    for idx, root in enumerate(doc.roots):
        prefix = (DocTag(f"{idx}:"),) if idx > 0 and multi else ()
        if root.kind == "scalar" and root.empty and not prefix:
            flat.values[()] = ("null", None)
            continue
        emit(root, prefix, prefix)
    return flat


def walk(doc: Doc):
    """(path, node) for every real node, with doc tags when there are several documents."""
    for idx, root in enumerate(doc.roots):
        prefix = (DocTag(f"{idx}:"),) if idx > 0 else ()
        yield from walk_tree(root, prefix)


def dups(doc: Doc) -> list:
    tags = [DocTag(f"{i}:") if i > 0 else None for i in range(len(doc.roots))]
    return collect_dups(doc.roots, doc, tags)
