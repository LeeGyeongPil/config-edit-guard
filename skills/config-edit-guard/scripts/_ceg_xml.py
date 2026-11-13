"""XML positions for attribute values and element text (.csproj, pom.xml, .config ...).

``xml.parsers.expat`` validates the document; a small tokenizer then records where each
start tag, attribute value, element text and comment sits, so a value can be replaced
without re-serialising the file (ElementTree would rewrite the declaration quotes and
the spacing inside untouched tags).
"""
from __future__ import annotations

import re
import xml.parsers.expat
from typing import Optional

from _ceg_model import Comment, ConfigError, Doc, Flat, Node, format_segment

_NAME = r"[^\s/>=<\"'!?]+"
_ATTR_RE = re.compile(r"\s+(" + _NAME + r")\s*=\s*(\"[^\"]*\"|'[^']*')")
_ENTITY_RE = re.compile(r"&(lt|gt|amp|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);")
_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def unescape(raw: str) -> str:
    def sub(m):
        name = m.group(1)
        if name in _NAMED:
            return _NAMED[name]
        return chr(int(name[2:], 16)) if name[1] == "x" else chr(int(name[1:]))

    return _ENTITY_RE.sub(sub, raw)


def _validate(text: str) -> None:
    parser = xml.parsers.expat.ParserCreate(encoding="utf-8")
    try:
        parser.Parse(text.encode("utf-8"), True)
    except xml.parsers.expat.ExpatError as exc:
        detail = xml.parsers.expat.ErrorString(exc.code)
        raise ConfigError("E_PARSE", "e.parse.xml.invalid", line=exc.lineno, col=exc.offset + 1, detail=detail) from None


def parse(text: str) -> Doc:
    _validate(text)
    doc = Doc("xml", text, [])
    n = len(text)
    i = 0
    stack: list = []
    root: Optional[Node] = None
    text_start = 0
    comments: list = []

    def close_text(upto: int) -> None:
        if not stack:
            return
        top = stack[-1]
        if top.children and any(c.kind == "elem" for c in top.children):
            top.text_node = None
            return
        raw = text[text_start:upto]
        tn = Node("scalar", text_start, upto, style="text", raw=raw, value=unescape(raw))
        tn.entity_text = ("&" in raw) or ("<" in raw)
        top.text_node = tn

    while i < n:
        j = text.find("<", i)
        if j < 0:
            break
        if text.startswith("<!--", j):
            k = text.find("-->", j + 4)
            line, col = doc.line_col(j)
            comments.append(Comment(line, col, text[j:k + 3]))
            i = k + 3
            continue
        if text.startswith("<![CDATA[", j):
            k = text.find("]]>", j)
            i = k + 3
            continue
        if text.startswith("<?", j):
            k = text.find("?>", j)
            i = k + 2
            continue
        if text.startswith("<!", j):
            depth = 0
            k = j
            while k < n:
                if text[k] == "[":
                    depth += 1
                elif text[k] == "]":
                    depth -= 1
                elif text[k] == ">" and depth <= 0:
                    break
                k += 1
            i = k + 1
            continue
        if text.startswith("</", j):
            close_text(j)
            k = text.find(">", j)
            top = stack.pop()
            top.end = k + 1
            i = k + 1
            text_start = i
            continue
        # start tag
        m = re.compile(_NAME).match(text, j + 1)
        name = m.group(0)
        k = m.end()
        node = Node("elem", j, j, style="element", key=name)
        while True:
            am = _ATTR_RE.match(text, k)
            if not am:
                break
            q = am.group(2)
            vs = am.start(2)
            an = Node("scalar", vs, vs + len(q), style="attr_single" if q[0] == "'" else "attr_double",
                      raw=q, value=unescape(q[1:-1]), key="@" + am.group(1))
            an.key_node = Node("scalar", am.start(1), am.end(1), style="key", value=am.group(1))
            an.entity_text = "&" in q
            node.children.append(an)
            k = am.end()
        while k < n and text[k] in " \t\r\n":
            k += 1
        self_closing = text.startswith("/>", k)
        end_tag = k + 2 if self_closing else k + 1
        if stack:
            close_text(j)
            stack[-1].children.append(node)
        else:
            root = node
        node.self_closing = self_closing
        if self_closing:
            node.end = end_tag
            node.text_node = None
        else:
            stack.append(node)
        i = end_tag
        text_start = i
    if root is None:
        raise ConfigError("E_PARSE", "e.parse.xml.invalid", line=1, col=1, detail="no root element")
    doc.roots = [root]
    doc.comments = comments
    return doc


# ---------------------------------------------------------------- paths

def _elem_children(node: Node) -> list:
    return [c for c in node.children if c.kind == "elem"]


def _normalize(segs: tuple) -> list:
    """Pair every element name with an index (default 0): [('PropertyGroup', 0), ...]."""
    out: list = []
    i = 0
    while i < len(segs):
        seg = segs[i]
        if isinstance(seg, int):
            raise ConfigError("E_USAGE", "e.usage.xml_index", path="")
        if seg.startswith("@"):
            out.append((seg, 0))
            i += 1
            continue
        idx = 0
        if i + 1 < len(segs) and isinstance(segs[i + 1], int):
            idx = segs[i + 1]
            i += 1
        out.append((seg, idx))
        i += 1
    return out


def resolve(doc: Doc, segs: tuple, path_text: str, doc_index: int = 0):
    import difflib

    pairs = _normalize(segs)
    root = doc.roots[0]
    if not pairs or pairs[0][0] != root.key:
        raise ConfigError("E_PATH_NOT_FOUND", "e.path.xml_root", path=path_text, root=root.key)
    node = root
    walked = [root.key]
    for name, idx in pairs[1:]:
        here = ".".join(walked)
        if name.startswith("@"):
            hits = [c for c in node.children if c.kind == "scalar" and c.key == name]
            if not hits:
                names = [c.key for c in node.children if c.kind == "scalar"]
                close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.missing_suggest" if close else "e.path.missing",
                                  path=path_text, key=name, at=here, suggest=", ".join(close or names[:5]))
            node = hits[0]
        else:
            sibs = [c for c in _elem_children(node) if c.key == name]
            if not sibs:
                names = []
                for c in _elem_children(node):
                    if c.key not in names:
                        names.append(c.key)
                close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.missing_suggest" if close else "e.path.missing",
                                  path=path_text, key=name, at=here, suggest=", ".join(close or names[:5]))
            if idx >= len(sibs):
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.index_range", path=path_text, at=here, index=idx, count=len(sibs))
            if len(sibs) > 1 and not _explicit_index(segs, name):
                doc.warnings.append(f"xml_multiple:{name}:{len(sibs)}")
            node = sibs[idx]
        walked.append(name)
    norm: list = []
    for name, idx in pairs:
        norm.append(name)
        if not name.startswith("@"):
            norm.append(idx)
    return node, tuple(norm)


def _explicit_index(segs: tuple, name: str) -> bool:
    for i, seg in enumerate(segs):
        if seg == name and i + 1 < len(segs) and isinstance(segs[i + 1], int):
            return True
    return False


def lookup(doc: Doc, segs: tuple) -> Optional[Node]:
    try:
        pairs = _normalize(segs)
    except ConfigError:
        return None
    root = doc.roots[0]
    if not pairs or pairs[0][0] != root.key:
        return None
    node = root
    for name, idx in pairs[1:]:
        if name.startswith("@"):
            hits = [c for c in node.children if c.kind == "scalar" and c.key == name]
            if not hits:
                return None
            node = hits[0]
        else:
            sibs = [c for c in _elem_children(node) if c.key == name]
            if idx >= len(sibs):
                return None
            node = sibs[idx]
    return node


def value_node(node: Node) -> Optional[Node]:
    """The scalar span that ``set`` edits for an element or attribute node (None for containers)."""
    if node.kind == "scalar":
        return node
    if node.text_node is not None:
        return node.text_node
    return None


def is_container(node: Node) -> bool:
    return node.kind == "elem" and bool(_elem_children(node))


def _display(path: tuple, counts: dict) -> str:
    out = ""
    k = 0
    while k < len(path):
        seg = path[k]
        if isinstance(seg, str) and seg.startswith("@"):
            out += ("." if out else "") + seg
            k += 1
            continue
        idx = path[k + 1]
        part = format_segment(seg)
        if counts.get(path[:k + 2], 1) > 1:
            part += f"[{idx}]"
        out += ("." if out else "") + part
        k += 2
    return out


def _count_siblings(root: Node) -> dict:
    counts: dict = {}

    def visit(node: Node, path: tuple) -> None:
        names: dict = {}
        for c in _elem_children(node):
            names[c.key] = names.get(c.key, 0) + 1
        seen: dict = {}
        for c in _elem_children(node):
            i = seen.get(c.key, 0)
            seen[c.key] = i + 1
            p = path + (c.key, i)
            counts[p] = names[c.key]
            visit(c, p)

    visit(root, (root.key, 0))
    counts[(root.key, 0)] = 1
    return counts


def flatten(doc: Doc) -> Flat:
    flat = Flat()
    root = doc.roots[0]
    counts = _count_siblings(root)

    def emit(node: Node, path: tuple) -> None:
        flat.display[path] = _display(path, counts)
        for c in node.children:
            if c.kind == "scalar":
                p = path + (c.key,)
                flat.values[p] = ("str", c.value)
                flat.display[p] = _display(p, counts)
        kids = _elem_children(node)
        if not kids:
            flat.values[path] = ("str", node.text_node.value if node.text_node is not None else "")
            return
        seen: dict = {}
        for c in kids:
            i = seen.get(c.key, 0)
            seen[c.key] = i + 1
            emit(c, path + (c.key, i))
        stray = _stray_text(doc.text, node)
        if stray:
            p = path + ("#text", 0)
            flat.values[p] = ("str", stray)
            flat.display[p] = _display(path, counts) + ".#text"

    emit(root, (root.key, 0))
    return flat


def _stray_text(text: str, node: Node) -> str:
    """Non-blank text sitting between child elements (mixed content)."""
    pieces = []
    pos = node.start
    m = re.compile(r">").search(text, pos)
    pos = (m.end() if m else pos)
    for c in _elem_children(node):
        pieces.append(text[pos:c.start])
        pos = c.end
    pieces.append(text[pos:node.end - len(node.key) - 3] if node.end else "")
    joined = "".join(re.sub(r"<!--.*?-->", "", p, flags=re.S) for p in pieces).strip()
    return unescape(joined)


def walk(doc: Doc):
    root = doc.roots[0]

    def visit(node: Node, path: tuple):
        yield path, node
        for c in node.children:
            if c.kind == "scalar":
                yield path + (c.key,), c
        seen: dict = {}
        for c in _elem_children(node):
            i = seen.get(c.key, 0)
            seen[c.key] = i + 1
            yield from visit(c, path + (c.key, i))

    yield from visit(root, (root.key, 0))


def dups(doc: Doc) -> list:
    return []
