#!/usr/bin/env python3
"""config-edit-guard: edit one config value by its byte span, then verify the edit.

Commands
  locate <file> <path>        where a value sits (span, line, column, style)
  set <file> <path> <value>   replace one scalar value in place; saved only after a self-check
  verify <before> <after>     compare two files: changed paths vs expected paths + text details

Exit codes: 0 ok, 1 unintended change found (or set refused to save), 2 input error.
Standard library only (TOML needs Python 3.11+).  Output language: --lang en|ko,
CONFIG_EDIT_GUARD_LANG, then LC_ALL / LC_MESSAGES / LANG.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

import _ceg_edit
import _ceg_formats
import _ceg_i18n as i18n
import _ceg_verify
import _ceg_xml
from _ceg_model import (
    TOOL, VERSION, ConfigError, Source, format_path, parse_path, read_source, sha256_of,
)

EXIT_OK, EXIT_UNINTENDED, EXIT_INPUT = 0, 1, 2


# ---------------------------------------------------------------- argument parsing

def prescan_lang(argv: list) -> Optional[str]:
    """Find --lang before argparse runs, so that help and errors already use that language."""
    value = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            break
        if a == "--lang" and i + 1 < len(argv):
            value = argv[i + 1]
            i += 1
        elif a.startswith("--lang="):
            value = a.split("=", 1)[1]
        i += 1
    return value


def _add_lang(parser, lang: str) -> None:
    import argparse

    parser.add_argument("--lang", choices=i18n.LANGS, metavar=i18n.t(lang, "arg.metavar.lang"), default=argparse.SUPPRESS,
                        help=i18n.t(lang, "arg.lang"))


def _add_common(parser, lang: str, with_doc: bool) -> None:
    t = lambda k, /: i18n.t(lang, k)  # noqa: E731 - tiny local alias
    parser.add_argument("--format", choices=_ceg_formats.FORMATS, metavar=t("arg.metavar.format"), default=None,
                        help=t("arg.format"))
    _add_lang(parser, lang)
    parser.add_argument("--json", action="store_true", help=t("arg.json"))
    parser.add_argument("--report", metavar=t("arg.metavar.file"), default=None, help=t("arg.report"))
    if with_doc:
        parser.add_argument("--doc", type=int, metavar=t("arg.metavar.n"), default=0, help=t("arg.doc"))


def build_parser(lang: str, prog: Optional[str] = None):
    t = lambda key: i18n.t(lang, key)  # noqa: E731
    parser_cls = i18n.make_parser_class(lang)
    top = parser_cls(prog=prog, description=t("cli.description"), epilog=t("cli.epilog"))
    _add_lang(top, lang)
    sub = top.add_subparsers(dest="command", parser_class=parser_cls, title=t("label.commands"),
                             metavar="{locate,set,verify}")
    sub.required = True

    loc = sub.add_parser("locate", help=t("cmd.locate"), description=t("cmd.locate.desc"), epilog=t("cli.epilog"))
    loc.add_argument("file", metavar=t("arg.metavar.file"), help=t("arg.file"))
    loc.add_argument("path", metavar=t("arg.metavar.path"), help=t("arg.path"))
    _add_common(loc, lang, with_doc=True)

    st = sub.add_parser("set", help=t("cmd.set"), description=t("cmd.set.desc"), epilog=t("cli.epilog"))
    st.add_argument("file", metavar=t("arg.metavar.file"), help=t("arg.file"))
    st.add_argument("path", metavar=t("arg.metavar.path"), help=t("arg.path"))
    st.add_argument("value", metavar=t("arg.metavar.value"), help=t("arg.value"))
    st.add_argument("--type", choices=_ceg_edit.TYPES, default="auto", metavar=t("arg.metavar.type"), help=t("arg.type"))
    st.add_argument("--dry-run", action="store_true", help=t("arg.dry_run"))
    st.add_argument("--allow", default="", metavar=t("arg.metavar.checks"), help=t("arg.allow"))
    _add_common(st, lang, with_doc=True)

    ve = sub.add_parser("verify", help=t("cmd.verify"), description=t("cmd.verify.desc"), epilog=t("cli.epilog"))
    ve.add_argument("before", metavar=t("arg.metavar.file"), help=t("arg.before"))
    ve.add_argument("after", metavar=t("arg.metavar.file"), help=t("arg.after"))
    ve.add_argument("--expect", action="append", default=[], metavar=t("arg.metavar.path"), help=t("arg.expect"))
    ve.add_argument("--allow", default="", metavar=t("arg.metavar.checks"), help=t("arg.allow"))
    ve.add_argument("--text-only", action="store_true", help=t("arg.text_only"))
    _add_common(ve, lang, with_doc=False)
    return top


# ---------------------------------------------------------------- reports

def _base_report(argv: list, status: str) -> dict:
    return {"tool": TOOL, "version": VERSION, "command": list(argv), "status": status}


def _write_report_file(path: str, report: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise ConfigError("E_USAGE", "e.usage.report", file=path, reason=exc.strerror or type(exc).__name__) from None


def _emit(report: dict, human: list, args, lang: str) -> None:
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    out = args.report
    if out:
        _write_report_file(out, report)


def _span(doc, source: Source, node) -> dict:
    return _ceg_edit.span_info(doc, source.bom, node.start, node.end)


def _coerce(kind: str, text: str):
    try:
        if kind == "int":
            return int(text.replace("_", ""), 0) if text.lstrip("+-")[:2] in ("0x", "0o", "0b") else int(text.replace("_", ""))
        if kind == "float":
            return float(text.replace("_", ""))
        if kind == "bool":
            return text.strip().lower() == "true"
        if kind == "null":
            return None
    except ValueError:
        return text
    return text


def _doc_index(args, path_doc: int) -> int:
    return args.doc if args.doc else path_doc


# ---------------------------------------------------------------- commands

def cmd_locate(args, lang: str, argv: list) -> int:
    source = read_source(args.file)
    fmt = _ceg_formats.detect_format(args.file, args.format)
    segs, path_doc = parse_path(args.path)
    try:
        doc = _ceg_formats.parse_text(fmt, source.text)
    except ConfigError as exc:
        exc.params.setdefault("file", args.file)
        raise
    mod = _ceg_formats.module_for(doc)
    try:
        node, norm = mod.resolve(doc, segs, args.path, _doc_index(args, path_doc))
    except ConfigError as exc:
        exc.params.setdefault("file", args.file)
        raise
    flat = mod.flatten(doc)
    report = _base_report(argv, "ok")
    report.update({"file": args.file, "format": doc.fmt, "path": args.path, "normalized_path": _ceg_formats.display_path(doc, norm)})
    target = node
    is_scalar = node.kind in ("scalar", "alias")
    if _ceg_formats.family(doc.fmt) == "xml":
        vn = _ceg_xml.value_node(node)
        is_scalar = vn is not None and not _ceg_xml.is_container(node)
        target = vn if vn is not None else node
    human: list = []
    if is_scalar:
        typ, value = flat.values.get(norm, ("str", target.raw))
        report.update({"kind": "scalar", "value": value, "type": typ, "style": node.style if node.kind == "alias" else target.style,
                       "span": _span(doc, source, target)})
        sp = report["span"]
        human.append(i18n.t(lang, "loc.scalar", file=args.file, path=report["normalized_path"], type=typ, style=report["style"]))
        human.append(i18n.t(lang, "loc.value", value=json.dumps(value, ensure_ascii=False)))
        human.append(i18n.t(lang, "loc.span", start=sp["start_byte"], end=sp["end_byte"], line=sp["line"], col=sp["column"],
                            eline=sp["end_line"], ecol=sp["end_column"]))
    else:
        kids = _children(node)
        report.update({"kind": node.kind if node.kind != "elem" else "map", "child_count": len(kids), "children": kids[:50],
                       "span": _span(doc, source, node)})
        human.append(i18n.t(lang, "loc.collection", file=args.file, path=report["normalized_path"], kind=report["kind"], count=len(kids)))
        for c in kids[:50]:
            human.append(i18n.t(lang, "loc.child", name=c["name"], kind=c["kind"]))
    report["warnings"] = list(doc.warnings)
    for w in doc.warnings:
        if w.startswith("xml_multiple:"):
            _, name, count = w.split(":")
            human.append(i18n.t(lang, "warn.xml_multiple", name=name, count=count))
    _emit(report, human, args, lang)
    return EXIT_OK


def _children(node) -> list:
    out: list = []
    if node.kind == "map":
        out = [{"name": c.key, "kind": c.kind} for c in node.children]
    elif node.kind == "seq":
        out = [{"name": f"[{i}]", "kind": c.kind} for i, c in enumerate(node.children)]
    elif node.kind == "elem":
        out = [{"name": c.key, "kind": "scalar" if c.kind == "scalar" else "map" if _ceg_xml.is_container(c) else "scalar"}
               for c in node.children]
    return out


def _verify_lines(lang: str, report: dict) -> list:
    """Human lines for the findings of a verify report (violations plus a few info lines)."""
    t = lambda k, /, **kw: i18n.t(lang, k, **kw)  # noqa: E731
    v = set(report["violations"])
    out: list = []
    if report["changed_paths"] and report["mode"] == "full":
        out.append(t("verify.changed", paths=", ".join(report["changed_paths"][:12]) + (" ..." if len(report["changed_paths"]) > 12 else "")))
    if "collateral_paths" in v:
        out.append(t("v.collateral_paths", paths=", ".join(report["collateral_paths"])))
    if report["via_alias"]:
        out.append(t("v.via_alias", paths=", ".join(report["via_alias"])))
    if "unchanged_expected" in v:
        out.append(t("v.unchanged_expected", paths=", ".join(report["unchanged_expected"])))
    if "lost_comments" in v:
        for c in report["lost_comments"]:
            out.append(t("v.lost_comments", where=t("word.line", n=c["line"]), text=c["text"]))
    for c in report["added_comments"][:5]:
        out.append(t("v.added_comments", where=t("word.line", n=c["line"]), text=c["text"]))
    if "unexpected_line_changes" in v:
        for h in report["unexpected_line_changes"]:
            out.append(t("v.unexpected_line_changes", before=_range_text(lang, h["before_lines"]),
                         after=_range_text(lang, h["after_lines"])))
    if "blank_line_delta" in v:
        out.append(t("v.blank_line_delta", delta=f"{report['blank_line_delta']:+d}"))
    if "key_order_changed" in v:
        out.append(t("v.key_order_changed", paths=", ".join(report["key_order_changed"])))
    if "quote_style_changed" in v:
        out.append(t("v.quote_style_changed", paths=", ".join(report["quote_style_changed"])))
    if "yaml11_risky" in v:
        for r in report["yaml11_risky"]:
            out.append(t("v.yaml11_risky", path=r["path"] or "(root)", value=r["value"], reason=t("reason." + r["reason"])))
    if "duplicate_keys" in v:
        for d in report["duplicate_keys"]:
            out.append(t("v.duplicate_keys", key=d["key"], path=d["path"] or "(root)",
                         where=", ".join(t("word.line", n=n) for n in d["lines"])))
    if "anchors_lost" in v:
        out.append(t("v.anchors_lost", names=", ".join(report["anchors_lost"])))
    if "eol_changed" in v:
        out.append(t("v.eol_changed", before=report["eol_before"], after=report["eol_after"]))
    if "final_newline_changed" in v:
        out.append(t("v.final_newline_changed"))
    if "bom_changed" in v:
        out.append(t("v.bom_changed"))
    return out


def _range_text(lang: str, rng: list) -> str:
    if not rng:
        return i18n.t(lang, "word.none")
    if rng[0] == rng[1]:
        return i18n.t(lang, "word.line", n=rng[0])
    return i18n.t(lang, "word.line", n=f"{rng[0]}-{rng[1]}")


def cmd_verify(args, lang: str, argv: list) -> int:
    before = read_source(args.before)
    after = read_source(args.after)
    fmt = _ceg_formats.detect_format(args.after, args.format, fallback=args.before)
    allow = _ceg_verify.parse_allow(args.allow)
    result = _ceg_verify.verify_sources(before, after, fmt, list(args.expect), allow, args.text_only,
                                        labels=(args.before, args.after))
    report = _base_report(argv, "ok" if result["ok"] else "unintended_changes")
    report.update({"before": args.before, "after": args.after})
    report.update(result)
    t = lambda k, /, **kw: i18n.t(lang, k, **kw)  # noqa: E731
    if result["ok"]:
        human = [t("verify.ok", before=args.before, after=args.after)]
    else:
        human = [t("verify.fail", before=args.before, after=args.after, count=len(result["violations"]))]
    if args.text_only:
        human.append(t("verify.mode_text"))
    human.extend(_verify_lines(lang, result))
    if not result["ok"]:
        human.append(t("verify.hint"))
    _emit(report, human, args, lang)
    return EXIT_OK if result["ok"] else EXIT_UNINTENDED


def cmd_set(args, lang: str, argv: list) -> int:
    source = read_source(args.file)
    fmt = _ceg_formats.detect_format(args.file, args.format)
    allow = _ceg_verify.parse_allow(args.allow)
    segs, path_doc = parse_path(args.path)
    try:
        plan = _ceg_edit.plan_set(source, fmt, args.path, segs, _doc_index(args, path_doc), args.value, args.type)
    except ConfigError as exc:
        exc.params.setdefault("file", args.file)
        raise
    t = lambda k, /, **kw: i18n.t(lang, k, **kw)  # noqa: E731
    pp = _ceg_formats.display_path(plan.doc, plan.norm)
    r = plan.rendered
    after_raw = _ceg_edit.final_bytes(source, plan.new_text)
    report = _base_report(argv, "unchanged")
    report.update({
        "file": args.file, "format": plan.doc.fmt, "path": args.path, "normalized_path": pp,
        "old_value": plan.old_value, "new_value": _coerce(r.type, args.value), "type": r.type, "style": r.style,
        "quote_added": r.quote_added, "quote_reason": r.quote_reason,
        "span": _span(plan.doc, source, plan.node),
        "before_sha256": sha256_of(source.raw), "after_sha256": sha256_of(after_raw),
        "changed_lines": 0, "verify": None,
    })
    shown_old = json.dumps(plan.old_value, ensure_ascii=False)
    shown_new = json.dumps(report["new_value"], ensure_ascii=False)
    human: list = []
    if plan.new_text == source.text:
        human.append(t("set.unchanged", file=args.file, path=pp))
        _emit(report, human, args, lang)
        return EXIT_OK
    after = Source(raw=after_raw, text=plan.new_text, bom=source.bom)
    try:
        result = _ceg_verify.verify_sources(source, after, fmt, [pp], allow, False,
                                            labels=(args.file, args.file), check_unchanged=False)
    except ConfigError as exc:
        exc.params.setdefault("file", args.file)
        raise
    report["verify"] = result
    report["changed_lines"] = result["changed_lines"]
    if not result["ok"]:
        report["status"] = "rejected"
        human.append(t("set.rejected", file=args.file))
        human.extend(_verify_lines(lang, result))
        human.append(t("verify.hint"))
        _emit(report, human, args, lang)
        return EXIT_UNINTENDED
    if args.dry_run:
        report["status"] = "dry_run"
        human.append(t("set.dry_run", path=pp, new=shown_new, old=shown_old, lines=result["changed_lines"]))
    else:
        try:
            _ceg_edit.write_atomic(args.file, after_raw)
        except OSError as exc:
            raise ConfigError("E_FILE_NOT_FOUND", "e.file.unwritable", file=args.file,
                              reason=exc.strerror or type(exc).__name__) from None
        report["status"] = "written"
        human.append(t("set.written", file=args.file, path=pp, new=shown_new, old=shown_old,
                       lines=result["changed_lines"]))
    if r.quote_added and r.quote_reason:
        human.append(t("set.quoted", reason=t("reason." + r.quote_reason)))
    if result["via_alias"]:
        human.append(t("v.via_alias", paths=", ".join(result["via_alias"])))
    human.append(t("set.sha", sha=report["after_sha256"]))
    for w in plan.doc.warnings:
        if w.startswith("xml_multiple:"):
            _, name, count = w.split(":")
            human.append(t("warn.xml_multiple", name=name, count=count))
    report["warnings"] = list(plan.doc.warnings)
    _emit(report, human, args, lang)
    return EXIT_OK


# ---------------------------------------------------------------- entry

def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[list] = None, prog: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lang = i18n.resolve_lang(prescan_lang(argv))
    wants_json = "--json" in argv
    try:
        args = build_parser(lang, prog).parse_args(argv)
        handler = {"locate": cmd_locate, "set": cmd_set, "verify": cmd_verify}[args.command]
        return handler(args, lang, argv)
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    except RecursionError:
        err = ConfigError("E_PARSE", "e.parse.too_deep", line=1, col=1)
    except ConfigError as exc:
        err = exc
    print(i18n.error_line(lang, err), file=sys.stderr)
    if wants_json:
        message, hint = i18n.error_parts(lang, err)
        payload = _base_report(argv, "error")
        payload["error"] = {"code": err.code, "message": message, "fix": hint}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_INPUT


if __name__ == "__main__":
    _utf8_streams()
    sys.exit(main())
