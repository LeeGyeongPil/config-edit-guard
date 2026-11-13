"""Shared data model for config-edit-guard: nodes, documents, paths, errors.

Every format module (YAML, JSONC, TOML, XML) turns a text into a ``Doc`` whose
``Node`` tree carries exact character spans.  The edit and verify layers work on
that common model only.  Standard library only.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

VERSION = "0.1.1"
TOOL = "config-edit-guard"

LINE_BREAK_RE = re.compile(r"\r\n|\n|\r")
BARE_SEGMENT_RE = re.compile(r'^[^.\[\]"\s]+$')


class ConfigError(Exception):
    """An input problem.  ``code`` is the stable E_* code, ``key`` a message-table key."""

    def __init__(self, code: str, msg_key: str, **params: Any) -> None:
        super().__init__(f"{code} {msg_key} {params}")
        self.code = code
        self.key = msg_key
        self.params = params


class DocTag(str):
    """First element of a path tuple that selects a YAML document (``1:``)."""


@dataclass(eq=False)
class Node:
    """One value in a document tree (scalar, map, seq, alias or XML element)."""

    kind: str  # scalar | map | seq | alias | elem
    start: int
    end: int
    style: str = ""
    value: Any = None
    raw: str = ""
    key: Optional[str] = None
    key_node: Optional["Node"] = None
    children: list = field(default_factory=list)
    anchor: Optional[str] = None
    alias: Optional[str] = None
    target: Optional["Node"] = None
    tag: Optional[str] = None
    merges: list = field(default_factory=list)
    empty: bool = False
    text_node: Optional["Node"] = None  # XML: the element text span
    self_closing: bool = False  # XML: <a/>
    entity_text: bool = False  # XML: text holds entities or CDATA
    spath: Optional[tuple] = None  # YAML: real path, set while flattening


@dataclass
class Comment:
    line: int
    col: int
    text: str


@dataclass
class DupKey:
    path: tuple
    key: str
    lines: list


@dataclass
class Source:
    """A file read as bytes: the decoded text (BOM removed) and whether a BOM was present."""

    raw: bytes
    text: str
    bom: bool


@dataclass
class Doc:
    fmt: str
    text: str
    roots: list
    comments: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)
    data: Any = None  # TOML: tomllib result
    warnings: list = field(default_factory=list)
    line_starts: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.line_starts:
            self.line_starts = compute_line_starts(self.text)

    def line_col(self, offset: int) -> tuple:
        """1-based (line, column) of a character offset; column counts characters."""
        i = bisect.bisect_right(self.line_starts, offset) - 1
        return i + 1, offset - self.line_starts[i] + 1

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)


@dataclass
class Flat:
    """Semantic view of a document: path tuple -> (type name, value)."""

    values: dict = field(default_factory=dict)
    via: dict = field(default_factory=dict)  # alias-derived path -> source path
    display: dict = field(default_factory=dict)  # optional path tuple -> text

    def name(self, path: tuple) -> str:
        return self.display.get(path) or format_path(path)


def compute_line_starts(text: str) -> list:
    starts = [0]
    for m in LINE_BREAK_RE.finditer(text):
        starts.append(m.end())
    return starts


def split_lines(text: str) -> list:
    """Lines without their line breaks; a final break does not add an empty line."""
    if text == "":
        return []
    parts = LINE_BREAK_RE.split(text)
    if LINE_BREAK_RE.search(text[-2:]) and parts and parts[-1] == "":
        parts.pop()
    return parts


# ---------------------------------------------------------------- reading files

def decode_bytes(raw: bytes, label: str) -> Source:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ConfigError("E_ENCODING", "e.encoding.utf16", file=label)
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("E_ENCODING", "e.encoding.invalid", file=label, offset=exc.start + (3 if bom else 0)) from None
    if "\x00" in text:
        raise ConfigError("E_ENCODING", "e.encoding.nul", file=label)
    return Source(raw=raw, text=text, bom=bom)


def read_source(path: str) -> Source:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise ConfigError("E_FILE_NOT_FOUND", "e.file.missing", file=path) from None
    except OSError as exc:
        raise ConfigError("E_FILE_NOT_FOUND", "e.file.unreadable", file=path, reason=exc.strerror or type(exc).__name__) from None
    return decode_bytes(raw, path)


def sha256_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def to_bytes(text: str, bom: bool) -> bytes:
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def eol_class(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    kinds = [name for name, n in (("crlf", crlf), ("lf", lf), ("cr", cr)) if n]
    if not kinds:
        return "none"
    return kinds[0] if len(kinds) == 1 else "mixed"


# ---------------------------------------------------------------- paths

def format_segment(seg: str) -> str:
    if seg.startswith("@") and BARE_SEGMENT_RE.match(seg[1:] or "x"):
        return seg
    if BARE_SEGMENT_RE.match(seg):
        return seg
    escaped = seg.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_path(path: tuple) -> str:
    """Render a path tuple: keys joined by dots, indexes as [i], odd keys quoted."""
    out = ""
    for i, seg in enumerate(path):
        if isinstance(seg, DocTag):
            out += str(seg)
        elif isinstance(seg, int):
            out += f"[{seg}]"
        else:
            if out and not out.endswith(":"):
                out += "."
            out += format_segment(seg)
    return out


def parse_path(text: str) -> tuple:
    """Parse ``a.b[0]."c.d"`` into (segments tuple, document index)."""
    doc = 0
    s = text.strip()
    m = re.match(r"^(\d+):", s)
    if m:
        doc = int(m.group(1))
        s = s[m.end():]
    if s == "":
        raise ConfigError("E_USAGE", "e.usage.path_empty", path=text)
    segs: list = []
    i = 0
    n = len(s)
    need_segment = True
    while i < n:
        c = s[i]
        if need_segment:
            if c == '"':
                j = i + 1
                buf = []
                while j < n and s[j] != '"':
                    if s[j] == "\\" and j + 1 < n:
                        j += 1
                    buf.append(s[j])
                    j += 1
                if j >= n:
                    raise ConfigError("E_USAGE", "e.usage.path_quote", path=text)
                segs.append("".join(buf))
                i = j + 1
            else:
                j = i
                while j < n and s[j] not in '.["]':
                    j += 1
                if j == i or s[i:j].strip() == "":
                    raise ConfigError("E_USAGE", "e.usage.path_syntax", path=text, pos=i + 1)
                segs.append(s[i:j].strip())
                i = j
            need_segment = False
            while i < n and s[i] == "[":
                j = s.find("]", i)
                if j < 0 or not s[i + 1:j].isdigit():
                    raise ConfigError("E_USAGE", "e.usage.path_index", path=text, pos=i + 1)
                segs.append(int(s[i + 1:j]))
                i = j + 1
        else:
            if c != ".":
                raise ConfigError("E_USAGE", "e.usage.path_syntax", path=text, pos=i + 1)
            need_segment = True
            i += 1
    if need_segment:
        raise ConfigError("E_USAGE", "e.usage.path_syntax", path=text, pos=n)
    if doc > 0:
        segs.insert(0, DocTag(f"{doc}:"))
    return tuple(segs), doc


def strip_doc_tag(path: tuple) -> tuple:
    return path[1:] if path and isinstance(path[0], DocTag) else path


# ---------------------------------------------------------------- tree helpers

def typed(value: Any) -> tuple:
    """Type-tagged scalar so that 1, 1.0, '1' and True never compare equal."""
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", "nan" if value != value else value)
    if isinstance(value, str):
        return ("str", value)
    if hasattr(value, "isoformat"):
        return (type(value).__name__, value.isoformat())
    return (type(value).__name__, repr(value))


def flatten_python(obj: Any, prefix: tuple = (), out: Optional[dict] = None) -> dict:
    """Flatten nested dict/list data (tomllib output) into {path tuple: typed scalar}."""
    out = {} if out is None else out
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = ("map", 0)
        for k, v in obj.items():
            flatten_python(v, prefix + (str(k),), out)
    elif isinstance(obj, list):
        if not obj:
            out[prefix] = ("seq", 0)
        for i, v in enumerate(obj):
            flatten_python(v, prefix + (i,), out)
    else:
        out[prefix] = typed(obj)
    return out


def walk_tree(root: Node, prefix: tuple = ()):
    """Yield (path, node) for every node of a map/seq/scalar tree (aliases are not followed)."""
    yield prefix, root
    if root.kind == "map":
        for child in root.children:
            if child.key == "<<" and root.merges:
                continue
            yield from walk_tree(child, prefix + (child.key,))
    elif root.kind == "seq":
        for i, child in enumerate(root.children):
            yield from walk_tree(child, prefix + (i,))


def map_keys(node: Node) -> list:
    return [c.key for c in node.children if c.key is not None]


def collect_dups(roots: list, doc: "Doc", tags: Optional[list] = None) -> list:
    """Duplicate keys inside any mapping of the trees (each root may carry a doc tag)."""
    found: list = []
    for idx, root in enumerate(roots):
        tag = (tags[idx],) if tags and tags[idx] else ()
        for path, node in walk_tree(root, tag):
            if node.kind != "map":
                continue
            seen: dict = {}
            for child in node.children:
                if child.key is None or (child.key == "<<" and node.merges):
                    continue
                seen.setdefault(child.key, []).append(child)
            for key, items in seen.items():
                if len(items) > 1:
                    lines = [doc.line_of((c.key_node or c).start) for c in items]
                    found.append(DupKey(path=path, key=key, lines=lines))
    return found


def resolve_generic(doc: Doc, root: Node, segs: tuple, path_text: str) -> Node:
    """Strict path resolution for map/seq trees with the errors a user can act on."""
    import difflib

    node = root
    walked: list = []
    for seg in segs:
        here = format_path(tuple(walked)) or "(root)"
        if node.kind == "alias":
            raise ConfigError("E_UNSUPPORTED", "e.unsupported.alias_path", path=path_text, at=here)
        if isinstance(seg, int):
            if node.kind != "seq":
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.not_seq", path=path_text, at=here)
            if seg >= len(node.children):
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.index_range", path=path_text, at=here, index=seg, count=len(node.children))
            node = node.children[seg]
        else:
            if node.kind != "map":
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.not_map", path=path_text, at=here, key=seg)
            hits = [c for c in node.children if c.key == seg]
            if len(hits) > 1:
                lines = [doc.line_of((c.key_node or c).start) for c in hits]
                raise ConfigError("E_AMBIGUOUS_PATH", "e.path.duplicate", path=path_text, key=seg, num_lines=lines)
            if not hits:
                keys = map_keys(node)
                for target in node.merges:
                    if any(c.key == seg for c in target.children):
                        raise ConfigError("E_PATH_NOT_FOUND", "e.path.via_merge", path=path_text, key=seg)
                close = [format_segment(c) for c in difflib.get_close_matches(seg, [k for k in keys if k], n=3, cutoff=0.4)]
                if close:
                    raise ConfigError("E_PATH_NOT_FOUND", "e.path.missing_suggest", path=path_text, key=seg, at=here,
                                      suggest=", ".join(close))
                raise ConfigError("E_PATH_NOT_FOUND", "e.path.missing", path=path_text, key=seg, at=here,
                                  suggest=", ".join(format_segment(k) for k in keys[:5] if k))
            node = hits[0]
        walked.append(seg)
    return node


def lookup_generic(root: Node, segs: tuple) -> Optional[Node]:
    """Lenient lookup used by verify: first match, None when missing."""
    node = root
    for seg in segs:
        if isinstance(seg, int):
            if node.kind != "seq" or seg >= len(node.children):
                return None
            node = node.children[seg]
        else:
            if node.kind != "map":
                return None
            hits = [c for c in node.children if c.key == seg]
            if not hits:
                return None
            node = hits[0]
    return node


def children_summary(node: Node, limit: int = 50) -> list:
    out = []
    if node.kind == "map":
        for c in node.children[:limit]:
            out.append({"key": c.key, "kind": c.kind})
    elif node.kind == "seq":
        for i, c in enumerate(node.children[:limit]):
            out.append({"index": i, "kind": c.kind})
    return out
