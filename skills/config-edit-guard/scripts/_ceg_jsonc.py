"""JSON and JSONC scanner with exact value spans.

Comments (``//`` and ``/* */``) and trailing commas are accepted everywhere, so a
settings file with comments is parsed in place and never rewritten.  Duplicate keys are
kept in the tree (``json.loads`` would silently keep the last one).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from _ceg_model import (
    Comment, ConfigError, Doc, Flat, Node, collect_dups, lookup_generic, resolve_generic, typed, walk_tree,
)

_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
_MAX_DEPTH = 150


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0
        self.comments: list = []
        self.doc: Optional[Doc] = None
        self.starts = [0] + [m.end() for m in re.finditer(r"\r\n|\n|\r", text)]

    def lc(self, off: int) -> tuple:
        import bisect
        k = bisect.bisect_right(self.starts, off) - 1
        return k + 1, off - self.starts[k] + 1

    def fail(self, key: str, off: int, **params) -> ConfigError:
        line, col = self.lc(off)
        return ConfigError("E_PARSE", key, line=line, col=col, **params)

    def ws(self) -> None:
        t = self.text
        while self.i < self.n:
            c = t[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif t.startswith("//", self.i):
                j = t.find("\n", self.i)
                j = self.n if j < 0 else j
                end = j - 1 if j > self.i and t[j - 1] == "\r" else j
                line, col = self.lc(self.i)
                self.comments.append(Comment(line, col, t[self.i:end].rstrip()))
                self.i = end
            elif t.startswith("/*", self.i):
                j = t.find("*/", self.i + 2)
                if j < 0:
                    raise self.fail("e.parse.json.comment_open", self.i)
                line, col = self.lc(self.i)
                self.comments.append(Comment(line, col, t[self.i:j + 2]))
                self.i = j + 2
            else:
                break

    def value(self, depth: int) -> Node:
        if depth > _MAX_DEPTH:
            raise self.fail("e.parse.too_deep", self.i)
        self.ws()
        if self.i >= self.n:
            raise self.fail("e.parse.json.eof", self.n)
        c = self.text[self.i]
        if c == "{":
            return self.obj(depth)
        if c == "[":
            return self.arr(depth)
        if c == '"':
            return self.string()
        for word, val in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.i):
                node = Node("scalar", self.i, self.i + len(word), style="bare", raw=word, value=val)
                self.i += len(word)
                return node
        m = _NUMBER.match(self.text, self.i)
        if m:
            raw = m.group(0)
            val = json.loads(raw)
            node = Node("scalar", self.i, m.end(), style="bare", raw=raw, value=val)
            self.i = m.end()
            return node
        raise self.fail("e.parse.json.unexpected", self.i, ch=c)

    def string(self) -> Node:
        t = self.text
        j = self.i + 1
        while j < self.n:
            c = t[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                break
            if c in "\r\n":
                raise self.fail("e.parse.json.string_open", self.i)
            j += 1
        if j >= self.n:
            raise self.fail("e.parse.json.string_open", self.i)
        raw = t[self.i:j + 1]
        try:
            val = json.loads(raw)
        except ValueError:
            raise self.fail("e.parse.json.string_bad", self.i) from None
        node = Node("scalar", self.i, j + 1, style="double", raw=raw, value=val)
        self.i = j + 1
        return node

    def obj(self, depth: int) -> Node:
        node = Node("map", self.i, self.i, style="block")
        self.i += 1
        while True:
            self.ws()
            if self.i >= self.n:
                raise self.fail("e.parse.json.eof", self.n)
            if self.text[self.i] == "}":
                self.i += 1
                break
            if self.text[self.i] != '"':
                raise self.fail("e.parse.json.key_expected", self.i, ch=self.text[self.i])
            kn = self.string()
            self.ws()
            if self.i >= self.n or self.text[self.i] != ":":
                raise self.fail("e.parse.json.colon", self.i)
            self.i += 1
            val = self.value(depth + 1)
            val.key = kn.value
            val.key_node = kn
            node.children.append(val)
            self.ws()
            if self.i >= self.n:
                raise self.fail("e.parse.json.eof", self.n)
            c = self.text[self.i]
            if c == ",":
                self.i += 1
            elif c == "}":
                self.i += 1
                break
            else:
                raise self.fail("e.parse.json.comma", self.i, ch=c)
        node.end = self.i
        return node

    def arr(self, depth: int) -> Node:
        node = Node("seq", self.i, self.i, style="block")
        self.i += 1
        while True:
            self.ws()
            if self.i >= self.n:
                raise self.fail("e.parse.json.eof", self.n)
            if self.text[self.i] == "]":
                self.i += 1
                break
            node.children.append(self.value(depth + 1))
            self.ws()
            if self.i >= self.n:
                raise self.fail("e.parse.json.eof", self.n)
            c = self.text[self.i]
            if c == ",":
                self.i += 1
            elif c == "]":
                self.i += 1
                break
            else:
                raise self.fail("e.parse.json.comma", self.i, ch=c)
        node.end = self.i
        return node


def parse(text: str) -> Doc:
    p = _Parser(text)
    root = p.value(0)
    p.ws()
    if p.i < p.n:
        raise p.fail("e.parse.json.extra", p.i)
    doc = Doc("json", text, [root], comments=p.comments)
    if p.comments or _has_trailing_comma(text, root):
        doc.fmt = "jsonc"
    return doc


def _has_trailing_comma(text: str, root: Node) -> bool:
    for _path, node in walk_tree(root):
        if node.kind in ("map", "seq") and node.children:
            tail = text[node.children[-1].end:node.end - 1]
            if re.match(r"^(?:\s|/\*.*?\*/|//[^\n]*\n)*,", tail, re.S):
                return True
    return False


def resolve(doc: Doc, segs: tuple, path_text: str, doc_index: int = 0):
    return resolve_generic(doc, doc.roots[0], segs, path_text), segs


def lookup(doc: Doc, segs: tuple) -> Optional[Node]:
    return lookup_generic(doc.roots[0], segs)


def flatten(doc: Doc) -> Flat:
    flat = Flat()
    for path, node in walk_tree(doc.roots[0]):
        if node.kind == "scalar":
            flat.values[path] = typed(node.value)
        elif not node.children:
            flat.values[path] = (node.kind, 0)
    return flat


def walk(doc: Doc):
    yield from walk_tree(doc.roots[0])


def dups(doc: Doc) -> list:
    return collect_dups(doc.roots, doc)
