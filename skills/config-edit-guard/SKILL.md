---
name: config-edit-guard
description: Edits one value in YAML, JSON/JSONC, TOML, XML (.csproj, pom.xml) or Markdown front matter by replacing only that value's byte span, and verifies any config edit by comparing before and after on two layers - changed paths versus the paths meant to change, and text details such as comments, blank lines, quotes, key order, line endings, anchors, duplicate keys and YAML 1.1 traps (NO, on, 0755, 1.10). Use when asked to change a single setting, bump a version or image tag, edit a config file without reformatting it or losing comments, locate the exact line and byte offset of a value, or check whether an edited config changed anything besides the intended keys. Not for creating new config files, converting between formats, schema validation, linting or reformatting.
license: MIT
compatibility: Python 3.10+ (standard library only; TOML needs 3.11+). No network access.
metadata:
  version: "0.1.0"
---

# Config edit guard

Rewriting a whole config file to change one value is how comments vanish, quotes and indentation change, `NO` becomes `false` and a merged anchor silently changes a second setting. "The meaning is the same" is not "the bytes are the same", so the script replaces only the value's byte span and checks the result on two layers. Reading a diff by eye does not do either reliably.

Call the script by its path in this skill's folder: `python3 <skill-folder>/scripts/config_edit_guard.py <command>`, shortened below to `python3 scripts/config_edit_guard.py`. Run it from the user's project folder and give it the user's file paths; never `cd` into the skill's folder.

## Which command

1. The user wants one scalar value changed (a string, number, boolean): run `python3 scripts/config_edit_guard.py set FILE PATH VALUE`. Never edit that value by hand or rewrite the file with a library, a YAML or JSON dump, `sed` or a formatter. `set` replaces only the value, keeps the original quote style, line endings and BOM, checks the result in memory against the path you named and saves only if the check passes.
2. The user wants a structural change (add or remove a key or list item, move a block, change a block scalar): copy the file first (`cp FILE FILE.orig`), edit it by hand with the smallest possible change, then run `python3 scripts/config_edit_guard.py verify FILE.orig FILE --expect PATH` with every path you meant to change (repeat `--expect`).
3. The user asks whether a change touched anything else (a hand edit, a colleague's edit, your earlier edit): run `verify BEFORE AFTER --expect PATH` with the paths that were supposed to change. Do not read the two files yourself first.
4. The user wants the exact line, column or byte offset of a value, or the keys under a section: run `locate FILE PATH`.

Add `--report FILE.json` when the user wants the result saved in a file; the script writes it, you do not. Add `--json` to read the result as JSON.

## Paths

Dots between keys, `[0]` for list items, double quotes around a key that contains dots: `spec.template.spec.containers[0].image`, `"editor.fontSize"`, `tool.ruff."line-length"`. XML starts with the root element and uses `@name` for attributes: `Project.PropertyGroup[0].TargetFramework`, `Project.ItemGroup.PackageReference.@Version`. Several YAML documents: `--doc N` or a prefix `1:spec.replicas`. Details and limits per format are in `references/paths-and-formats.md`.

## Reading the result

- `set` prints one line per edit. It reports `quote_added` and `quote_reason` when it had to quote a value (`country: NO` is written as `country: "NO"` because YAML 1.1 loaders read `NO` as false). Exit 1 means the self-check refused to save: show the findings and do not try another way of writing the file.
- `verify` exits 0 when nothing unintended changed and 1 when it found something. Show the findings to the user as printed. `collateral_paths` are real unintended changes, `unchanged_expected` means an expected path did not change, `lost_comments` come with line numbers. `via_alias` lists paths that changed only because an anchored value they merge or alias was edited; that is normal propagation, not an error, and you can say so.
- After exit 1 from `verify`, restore the original from the `.orig` copy and make a smaller edit, or tell the user what the script found. Do not silence a finding with `--allow` unless the user said that kind of change is fine (`--allow comments,order,quotes,blank_lines,eol`).
- Exit 2 is an input problem. Show the error line as printed; it names the code and says how to fix the input. The file is never changed on exit 2.

## When the script says it cannot

- `E_UNSUPPORTED` names a YAML feature the scanner does not read (multi-line plain or quoted scalars, multi-line `[ ]` or `{ }`, complex `?` keys) or a value `set` does not rewrite (block scalars, multi-line TOML strings, XML text with entities or CDATA). Edit by hand, then `verify --text-only BEFORE AFTER`, which skips the semantic layer and still checks comments, blank lines and line endings.
- `E_AMBIGUOUS_PATH` means the key appears twice in the file. Do not guess which one: tell the user, or remove the duplicate first with their consent.
- TOML needs Python 3.11 or newer (`E_PYTHON` otherwise). The other formats work on 3.10.

## Rules

- Every number the user gets (offsets, hashes, changed paths, line numbers) must come from a script run in this session; never write them from your own reading of the files.
- Do not create sample files or search the disk for config files the user did not name.
- Output language: `--lang en|ko` or `CONFIG_EDIT_GUARD_LANG`; JSON keys and `E_*` codes never change.
