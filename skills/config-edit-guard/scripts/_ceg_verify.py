"""Two-layer verification of a config edit.

Semantic layer: which paths changed, compared with the paths that were meant to change.
Text layer: what happened to comments, lines, quotes, key order, anchors, line endings.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from typing import Optional

import _ceg_formats
import _ceg_xml
import _ceg_yaml
from _ceg_model import (
    Comment, ConfigError, Doc, Source, eol_class, format_path, parse_path, sha256_of, split_lines,
)

ALLOW_CHOICES = ("comments", "order", "quotes", "blank_lines", "eol")
MAX_HUNKS = 20
MAX_LINES_SHOWN = 3


def parse_allow(text: str) -> set:
    out = set()
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part not in ALLOW_CHOICES:
            raise ConfigError("E_USAGE", "e.usage.allow", value=part, choices=", ".join(ALLOW_CHOICES))
        out.add(part)
    return out


def normalize_expect(fmt: str, text: str) -> tuple:
    segs, doc_idx = parse_path(text)
    if _ceg_formats.family(fmt) == "xml":
        pairs = _ceg_xml._normalize(tuple(s for s in segs))
        flat: list = []
        for name, idx in pairs:
            flat.append(name)
            if not name.startswith("@"):
                flat.append(idx)
        return tuple(flat)
    return segs


def _covers(path: tuple, expected: list) -> bool:
    return any(path[:len(e)] == e for e in expected)


def _line_set(doc: Doc, mod, expected: list) -> set:
    lines: set = set()
    for e in expected:
        node = mod.lookup(doc, e)
        if node is None:
            continue
        start = node.key_node.start if node.key_node is not None else node.start
        last = max(node.end - 1, start) if node.end > node.start else start
        for ln in range(doc.line_of(start), doc.line_of(last) + 1):
            lines.add(ln)
    return lines


def generic_comments(text: str, fam: str) -> list:
    """Comment finder that needs no parse (used by --text-only); quote tracking is a heuristic."""
    out: list = []
    if fam == "xml":
        for m in re.finditer(r"<!--.*?-->", text, re.S):
            line = text.count("\n", 0, m.start()) + 1
            out.append(Comment(line, 1, m.group(0)))
        return out
    for n, ln in enumerate(split_lines(text), start=1):
        quote = ""
        i = 0
        while i < len(ln):
            c = ln[i]
            if quote:
                if c == "\\" and quote == '"':
                    i += 2
                    continue
                if c == quote:
                    quote = ""
            elif c in "'\"" and (i == 0 or ln[i - 1] in " \t[{,:-="):
                quote = c
            elif fam == "json" and ln.startswith("//", i):
                out.append(Comment(n, i + 1, ln[i:].rstrip()))
                break
            elif fam == "json" and ln.startswith("/*", i):
                j = ln.find("*/", i + 2)
                end = len(ln) if j < 0 else j + 2
                out.append(Comment(n, i + 1, ln[i:end]))
                i = end
                continue
            elif fam != "json" and c == "#" and (i == 0 or ln[i - 1] in " \t"):
                out.append(Comment(n, i + 1, ln[i:].rstrip()))
                break
            i += 1
    return out


def _comment_diff(before: list, after: list) -> tuple:
    remaining = Counter(c.text.strip() for c in after)
    lost = []
    for c in before:
        t = c.text.strip()
        if remaining[t] > 0:
            remaining[t] -= 1
        else:
            lost.append({"line": c.line, "text": t})
    remaining_b = Counter(c.text.strip() for c in before)
    added = []
    for c in after:
        t = c.text.strip()
        if remaining_b[t] > 0:
            remaining_b[t] -= 1
        else:
            added.append({"line": c.line, "text": t})
    return lost, added


def _norm_chunk(lines: list, nums: range, doc: Optional[Doc], allow: set) -> list:
    cols: dict = {}
    if doc is not None and "comments" in allow:
        for c in doc.comments:
            cols.setdefault(c.line, c.col)
    out = []
    for text, n in zip(lines, nums):
        s = text
        if "comments" in allow and n in cols:
            s = s[:cols[n] - 1]
        if "quotes" in allow:
            s = s.replace("'", "").replace('"', "")
        s = s.rstrip()
        if "blank_lines" in allow and s.strip() == "":
            continue
        out.append(s)
    if "order" in allow:
        out.sort()
    return out


def _blank_count(lines: list, skip: set) -> int:
    return sum(1 for n, ln in enumerate(lines, start=1) if ln.strip() == "" and n not in skip)


def verify_sources(before: Source, after: Source, fmt: str, expect: list, allow: set, text_only: bool = False,
                   labels: tuple = ("before", "after"), check_unchanged: bool = True) -> dict:
    fam = _ceg_formats.family(fmt)
    expected = [normalize_expect(fmt, e) for e in expect]
    doc_b: Optional[Doc] = None
    doc_a: Optional[Doc] = None
    if not text_only:
        try:
            doc_b = _ceg_formats.parse_text(fmt, before.text)
        except ConfigError as exc:
            exc.params.setdefault("file", labels[0])
            raise
        try:
            doc_a = _ceg_formats.parse_text(fmt, after.text)
        except ConfigError as exc:
            exc.params.setdefault("file", labels[1])
            raise

    report: dict = {
        "ok": True, "violations": [], "format": fmt, "mode": "text_only" if text_only else "full",
        "expect": list(expect),
        "changed_paths": [], "collateral_paths": [], "via_alias": [], "unchanged_expected": [],
        "lost_comments": [], "added_comments": [], "unexpected_line_changes": [], "changed_lines": 0,
        "blank_line_delta": 0, "key_order_changed": [], "quote_style_changed": [], "yaml11_risky": [],
        "duplicate_keys": [], "anchors_lost": [], "eol_changed": False, "eol_before": "", "eol_after": "",
        "final_newline_changed": False, "bom_changed": False, "warnings": [],
        "before_sha256": sha256_of(before.raw), "after_sha256": sha256_of(after.raw),
    }

    # ---- semantic layer
    mod_b = _ceg_formats.module_for(doc_b) if doc_b else None
    mod_a = _ceg_formats.module_for(doc_a) if doc_a else None
    if doc_b is not None and doc_a is not None:
        fb, fa = mod_b.flatten(doc_b), mod_a.flatten(doc_a)

        def disp(p: tuple) -> str:
            return fa.display.get(p) or fb.display.get(p) or format_path(p)

        paths = set(fb.values) | set(fa.values)
        changed = sorted((p for p in paths if fb.values.get(p) != fa.values.get(p)), key=disp)
        changed_set = set(changed)
        report["changed_paths"] = [disp(p) for p in changed]
        for p in changed:
            if _covers(p, expected):
                continue
            src = fb.via.get(p) or fa.via.get(p)
            if src is not None and src in changed_set:
                report["via_alias"].append(disp(p))
            else:
                report["collateral_paths"].append(disp(p))
        for e, text in zip(expected, expect):
            if check_unchanged and not any(p[:len(e)] == e for p in changed):
                report["unchanged_expected"].append(text)

    # ---- text layer: comments
    if text_only:
        cb, ca = generic_comments(before.text, fam), generic_comments(after.text, fam)
    else:
        cb, ca = doc_b.comments, doc_a.comments
    report["lost_comments"], report["added_comments"] = _comment_diff(cb, ca)

    # ---- text layer: lines
    bl, al = split_lines(before.text), split_lines(after.text)
    eb = _line_set(doc_b, mod_b, expected) if doc_b is not None else set()
    ea = _line_set(doc_a, mod_a, expected) if doc_a is not None else set()
    hunks = []
    pending: list = []  # (hunk, normalized before lines, normalized after lines)
    total = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, bl, al, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        total += max(i2 - i1, j2 - j1)
        if text_only:
            continue
        # a replaced block of equal size is judged line by line, so that an expected edit next to an
        # unexpected one is not judged together
        if tag == "replace" and i2 - i1 == j2 - j1:
            pieces = [(i1 + k, i1 + k + 1, j1 + k, j1 + k + 1) for k in range(i2 - i1)]
        else:
            pieces = [(i1, i2, j1, j2)]
        for pi1, pi2, pj1, pj2 in pieces:
            b_nums, a_nums = range(pi1 + 1, pi2 + 1), range(pj1 + 1, pj2 + 1)
            if all(n in eb for n in b_nums) and all(n in ea for n in a_nums):
                continue
            nbl = _norm_chunk(bl[pi1:pi2], b_nums, doc_b, allow)
            nal = _norm_chunk(al[pj1:pj2], a_nums, doc_a, allow)
            if allow and nbl == nal:
                continue
            hunk = {
                "before_lines": [pi1 + 1, pi2] if pi2 > pi1 else [],
                "after_lines": [pj1 + 1, pj2] if pj2 > pj1 else [],
                "before": bl[pi1:pi2][:MAX_LINES_SHOWN],
                "after": al[pj1:pj2][:MAX_LINES_SHOWN],
            }
            pending.append((hunk, nbl, nal))
    if pending and "order" in allow:
        # moved lines show up as a deletion here and an insertion there: judge them together
        if sorted(x for _h, nb_, _na in pending for x in nb_) == sorted(x for _h, _nb, na_ in pending for x in na_):
            pending = []
    hunks = [h for h, _nb, _na in pending]
    report["changed_lines"] = total
    report["unexpected_line_changes"] = hunks[:MAX_HUNKS]
    report["blank_line_delta"] = _blank_count(al, ea) - _blank_count(bl, eb)

    # ---- text layer: structure (needs the parsed trees)
    if doc_b is not None and doc_a is not None:
        nb, na = dict(mod_b.walk(doc_b)), dict(mod_a.walk(doc_a))
        order_paths, quote_paths = [], []
        for p, node in nb.items():
            other = na.get(p)
            if other is None or _covers(p, expected):
                continue
            if node.kind in ("map", "elem") and other.kind == node.kind:
                kb = [c.key for c in node.children if c.key is not None]
                ka = [c.key for c in other.children if c.key is not None]
                sa, sb = set(ka), set(kb)
                if [k for k in kb if k in sa] != [k for k in ka if k in sb]:
                    order_paths.append(fb_name(fa, fb, p))
            elif node.kind == "scalar" and other.kind == "scalar" and node.style != other.style:
                if fb.values.get(p) == fa.values.get(p) and p in fb.values:
                    quote_paths.append(fb_name(fa, fb, p))
        report["key_order_changed"] = sorted(order_paths)
        report["quote_style_changed"] = sorted(quote_paths)
        if fam in ("yaml", "frontmatter"):
            for p, node in na.items():
                if node.kind != "scalar" or node.style != "plain" or node.tag or not node.raw:
                    continue
                reason = _ceg_yaml.risk_reason(node.raw)
                if not reason:
                    continue
                old = nb.get(p)
                if old is not None and old.kind == "scalar" and old.style == "plain" and old.raw == node.raw:
                    continue
                report["yaml11_risky"].append({"path": fb_name(fa, fb, p), "value": node.raw, "reason": reason})
            report["anchors_lost"] = sorted(set(doc_b.anchors) - set(doc_a.anchors))
        known = {(format_path(d.path), d.key) for d in _dups_of(doc_b)}
        for d in _dups_of(doc_a):
            if (format_path(d.path), d.key) not in known:
                report["duplicate_keys"].append({"path": format_path(d.path), "key": d.key, "lines": d.lines})

    # ---- text layer: bytes
    eol_b, eol_a = eol_class(before.text), eol_class(after.text)
    report["eol_before"], report["eol_after"] = eol_b, eol_a
    report["eol_changed"] = eol_b != eol_a
    report["final_newline_changed"] = before.text.endswith(("\n", "\r")) != after.text.endswith(("\n", "\r"))
    report["bom_changed"] = before.bom != after.bom

    # ---- verdict
    v = report["violations"]
    if report["collateral_paths"]:
        v.append("collateral_paths")
    if report["unchanged_expected"]:
        v.append("unchanged_expected")
    if report["lost_comments"] and "comments" not in allow:
        v.append("lost_comments")
    if report["unexpected_line_changes"]:
        v.append("unexpected_line_changes")
    if report["blank_line_delta"] and "blank_lines" not in allow:
        v.append("blank_line_delta")
    if report["key_order_changed"] and "order" not in allow:
        v.append("key_order_changed")
    if report["quote_style_changed"] and "quotes" not in allow:
        v.append("quote_style_changed")
    if report["yaml11_risky"]:
        v.append("yaml11_risky")
    if report["duplicate_keys"]:
        v.append("duplicate_keys")
    if report["anchors_lost"]:
        v.append("anchors_lost")
    if report["eol_changed"] and "eol" not in allow:
        v.append("eol_changed")
    if report["final_newline_changed"] and "eol" not in allow:
        v.append("final_newline_changed")
    if report["bom_changed"]:
        v.append("bom_changed")
    report["ok"] = not v
    return report


def fb_name(fa, fb, p: tuple) -> str:
    return fa.display.get(p) or fb.display.get(p) or format_path(p)


def _dups_of(doc: Doc) -> list:
    return _ceg_formats.module_for(doc).dups(doc)
