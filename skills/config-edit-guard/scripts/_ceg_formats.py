"""Format detection and dispatch to the per-format modules."""
from __future__ import annotations

import os
from typing import Optional

import _ceg_jsonc
import _ceg_toml
import _ceg_xml
import _ceg_yaml
from _ceg_model import ConfigError, Doc

FORMATS = ("yaml", "json", "jsonc", "toml", "xml", "frontmatter")

_EXT = {
    ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".jsonc": "jsonc",
    ".toml": "toml",
    ".xml": "xml", ".csproj": "xml", ".fsproj": "xml", ".vbproj": "xml", ".props": "xml", ".targets": "xml",
    ".config": "xml",
    ".md": "frontmatter", ".markdown": "frontmatter",
}
_BACKUP_SUFFIXES = (".orig", ".bak", ".old", ".before", ".after", ".new", ".tmp", ".save", "~")


def ext_format(path: str) -> Optional[str]:
    name = os.path.basename(path).lower()
    for _ in range(2):
        ext = os.path.splitext(name)[1]
        if ext in _EXT:
            return _EXT[ext]
        stripped = name
        for suffix in _BACKUP_SUFFIXES:
            if name.endswith(suffix):
                stripped = name[: -len(suffix)]
                break
        if stripped == name:
            return None
        name = stripped
    return None


def detect_format(path: str, override: Optional[str] = None, fallback: Optional[str] = None) -> str:
    if override:
        return override
    fmt = ext_format(path) or (ext_format(fallback) if fallback else None)
    if fmt is None:
        raise ConfigError("E_FORMAT", "e.format.unknown", file=path, known=", ".join(sorted({e for e in _EXT})))
    return fmt


def family(fmt: str) -> str:
    return "json" if fmt in ("json", "jsonc") else fmt


def parse_text(fmt: str, text: str) -> Doc:
    fam = family(fmt)
    if fam == "yaml":
        return _ceg_yaml.parse_yaml(text)
    if fam == "frontmatter":
        return _ceg_yaml.parse_frontmatter(text)
    if fam == "json":
        return _ceg_jsonc.parse(text)
    if fam == "toml":
        return _ceg_toml.parse(text)
    if fam == "xml":
        return _ceg_xml.parse(text)
    raise ConfigError("E_FORMAT", "e.format.bad_name", name=fmt, known=", ".join(FORMATS))


def module_for(doc: Doc):
    fam = family(doc.fmt)
    if fam in ("yaml", "frontmatter"):
        return _ceg_yaml
    if fam == "json":
        return _ceg_jsonc
    if fam == "toml":
        return _ceg_toml
    return _ceg_xml


def display_path(doc: Doc, path: tuple) -> str:
    """Path text that parse_path reads back: XML indexes appear only where siblings share a name."""
    from _ceg_model import format_path

    if family(doc.fmt) == "xml":
        counts = _ceg_xml._count_siblings(doc.roots[0])
        return _ceg_xml._display(path, counts)
    return format_path(path)
