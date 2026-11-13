"""TOML scanner: exact positions from a line scanner, meaning from ``tomllib``.

``tomllib`` (Python 3.11+) validates the text and supplies the values; the scanner below
only finds where each key, table header, string, array and inline table sits.  The module
is imported lazily so that the other formats still work on Python 3.10.
"""
from __future__ import annotations

import re
from typing import Optional

from _ceg_model import (
    Comment, ConfigError, Doc, Flat, Node, flatten_python, lookup_generic, resolve_generic, walk_tree,
)

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_BASIC_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\", "e": "\x1b"}


def import_tomllib():
    try:
        import tomllib  # noqa: PLC0415 - lazy on purpose: Python 3.10 has no tomllib
    except ImportError:
        import sys
        raise ConfigError("E_PYTHON", "e.python.toml", version="%d.%d" % sys.version_info[:2]) from None
    return tomllib


def _decode_basic(raw: str) -> str:
    body = raw[1:-1]
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        e = body[i] if i < len(body) else ""
        if e in _BASIC_ESC:
            out.append(_BASIC_ESC[e])
            i += 1
        elif e in "uU":
            width = 4 if e == "u" else 8
            out.append(chr(int(body[i + 1:i + 1 + width], 16)))
            i += 1 + width
        else:
            out.append(e)
            i += 1
    return "".join(out)


class _Scan:
    def __init__(self, text: str, doc: Doc) -> None:
        self.t = text
        self.n = len(text)
        self.i = 0
        self.doc = doc
        self.comments: list = []
        self.depth = 0

    def fail(self, key: str, off: int, **params) -> ConfigError:
        line, col = self.doc.line_col(min(off, max(self.n - 1, 0)))
        return ConfigError("E_PARSE", key, line=line, col=col, **params)

    def comment(self) -> None:
        j = self.t.find("\n", self.i)
        j = self.n if j < 0 else j
        end = j - 1 if j > self.i and self.t[j - 1] == "\r" else j
        line, col = self.doc.line_col(self.i)
        self.comments.append(Comment(line, col, self.t[self.i:end].rstrip()))
        self.i = end

    def skip_ws(self) -> None:
        while self.i < self.n and self.t[self.i] in " \t":
            self.i += 1

    def skip_all(self) -> None:
        """Whitespace, newlines and comments."""
        while self.i < self.n:
            c = self.t[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "#":
                self.comment()
            else:
                break

    def key_part(self) -> tuple:
        t = self.t
        c = t[self.i] if self.i < self.n else ""
        start = self.i
        if c == '"':
            j = self.i + 1
            while j < self.n and t[j] != '"':
                j += 2 if t[j] == "\\" else 1
            raw = t[start:j + 1]
            self.i = j + 1
            return _decode_basic(raw), start, self.i
        if c == "'":
            j = t.find("'", self.i + 1)
            if j < 0:
                raise self.fail("e.parse.toml.scan", self.i)
            self.i = j + 1
            return t[start + 1:j], start, self.i
        m = _BARE_KEY.match(t, self.i)
        if not m:
            raise self.fail("e.parse.toml.scan", self.i)
        self.i = m.end()
        return m.group(0), start, self.i

    def dotted_key(self) -> list:
        parts = []
        while True:
            self.skip_ws()
            parts.append(self.key_part())
            self.skip_ws()
            if self.i < self.n and self.t[self.i] == ".":
                self.i += 1
                continue
            return parts

    def value(self) -> Node:
        self.depth += 1
        if self.depth > 150:
            raise self.fail("e.parse.too_deep", self.i)
        try:
            return self._value()
        finally:
            self.depth -= 1

    def _value(self) -> Node:
        t = self.t
        c = t[self.i]
        start = self.i
        if t.startswith('"""', self.i) or t.startswith("'''", self.i):
            q = t[self.i:self.i + 3]
            j = self.i + 3
            while True:
                k = t.find(q, j)
                if k < 0:
                    raise self.fail("e.parse.toml.scan", self.i)
                if q == '"""':
                    back = 0
                    while t[k - 1 - back] == "\\":
                        back += 1
                    if back % 2 == 1:
                        j = k + 1
                        continue
                end = k + 3
                while end < self.n and t[end] == q[0] and end - k < 5:
                    end += 1
                break
            self.i = end
            return Node("scalar", start, end, style="ml_basic" if q == '"""' else "ml_literal", raw=t[start:end])
        if c == '"':
            j = self.i + 1
            while j < self.n and t[j] != '"':
                j += 2 if t[j] == "\\" else 1
            self.i = j + 1
            return Node("scalar", start, self.i, style="basic", raw=t[start:self.i])
        if c == "'":
            j = t.find("'", self.i + 1)
            self.i = j + 1
            return Node("scalar", start, self.i, style="literal_str", raw=t[start:self.i])
        if c == "[":
            node = Node("seq", start, start, style="flow")
            self.i += 1
            while True:
                self.skip_all()
                if t[self.i] == "]":
                    self.i += 1
                    break
                node.children.append(self.value())
                self.skip_all()
                if t[self.i] == ",":
                    self.i += 1
            node.end = self.i
            return node
        if c == "{":
            node = Node("map", start, start, style="flow")
            self.i += 1
            while True:
                self.skip_ws()
                if t[self.i] == "}":
                    self.i += 1
                    break
                keys = self.dotted_key()
                self.skip_ws()
                self.i += 1  # '='
                self.skip_ws()
                val = self.value()
                target = _ensure(node, keys[:-1], start)
                val.key = keys[-1][0]
                val.key_node = Node("scalar", keys[-1][1], keys[-1][2], style="key", value=keys[-1][0])
                target.children.append(val)
                self.skip_ws()
                if t[self.i] == ",":
                    self.i += 1
            node.end = self.i
            return node
        j = self.i
        while j < self.n and t[j] not in ",]}#\r\n":
            j += 1
        raw = t[self.i:j].rstrip()
        self.i = start + len(raw)
        return Node("scalar", start, self.i, style="bare", raw=raw)


def _ensure(container: Node, keys: list, pos: int) -> Node:
    """Find or create nested table nodes for dotted keys below ``container``."""
    node = container
    for text, ks, ke in keys:
        hit = None
        for c in node.children:
            if c.key == text:
                hit = c
        if hit is None:
            hit = Node("map", ks, ke, style="block", key=text,
                       key_node=Node("scalar", ks, ke, style="key", value=text))
            node.children.append(hit)
        if hit.kind == "seq":
            node = hit.children[-1]
        else:
            node = hit
    return node


def parse(text: str) -> Doc:
    tomllib = import_tomllib()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = str(exc)
        m = re.search(r"\(at line (\d+), column (\d+)\)", msg)
        line, col = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        detail = re.sub(r"\s*\(at line.*$", "", msg) or "invalid TOML"
        raise ConfigError("E_PARSE", "e.parse.toml.invalid", line=line, col=col, detail=detail) from None
    doc = Doc("toml", text, [], data=data)
    root = Node("map", 0, len(text), style="block")
    sc = _Scan(text, doc)
    cur = root
    while True:
        sc.skip_all()
        if sc.i >= sc.n:
            break
        if text[sc.i] == "[":
            array = text.startswith("[[", sc.i)
            sc.i += 2 if array else 1
            keys = sc.dotted_key()
            sc.skip_ws()
            sc.i += 2 if array else 1
            hs = keys[0][1]
            if array:
                parent = _ensure(root, keys[:-1], hs)
                text_k, ks, ke = keys[-1]
                seq = None
                for c in parent.children:
                    if c.key == text_k and c.kind == "seq":
                        seq = c
                if seq is None:
                    seq = Node("seq", hs, sc.i, style="block", key=text_k,
                               key_node=Node("scalar", ks, ke, style="key", value=text_k))
                    parent.children.append(seq)
                cur = Node("map", hs, sc.i, style="block")
                seq.children.append(cur)
            else:
                cur = _ensure(root, keys, hs)
                cur.start = min(cur.start, hs)
            sc.skip_ws()
            if sc.i < sc.n and text[sc.i] == "#":
                sc.comment()
            continue
        keys = sc.dotted_key()
        sc.skip_ws()
        if sc.i >= sc.n or text[sc.i] != "=":
            raise sc.fail("e.parse.toml.scan", sc.i)
        sc.i += 1
        sc.skip_ws()
        val = sc.value()
        target = _ensure(cur, keys[:-1], val.start)
        val.key = keys[-1][0]
        val.key_node = Node("scalar", keys[-1][1], keys[-1][2], style="key", value=keys[-1][0])
        target.children.append(val)
        sc.skip_ws()
        if sc.i < sc.n and text[sc.i] == "#":
            sc.comment()
    doc.roots = [root]
    doc.comments = sc.comments
    return doc


def resolve(doc: Doc, segs: tuple, path_text: str, doc_index: int = 0):
    return resolve_generic(doc, doc.roots[0], segs, path_text), segs


def lookup(doc: Doc, segs: tuple) -> Optional[Node]:
    return lookup_generic(doc.roots[0], segs)


def flatten(doc: Doc) -> Flat:
    flat = Flat()
    flat.values = flatten_python(doc.data)
    return flat


def walk(doc: Doc):
    yield from walk_tree(doc.roots[0])


def dups(doc: Doc) -> list:
    return []
