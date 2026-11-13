# Paths, formats and report fields

Contents: path syntax, formats and limits, `set` rules, `verify` report fields, error codes.

## Path syntax

| Write | Meaning |
|---|---|
| `a.b.c` | key `c` inside `b` inside `a` |
| `items[2]` | third list item |
| `"editor.fontSize"` | one key that contains a dot (double quotes, `\"` and `\\` escapes) |
| `tool.ruff."line-length"` | quotes are optional when the key has no dot, bracket, quote or space |
| `1:spec.replicas` | document 1 of a multi-document YAML file (same as `--doc 1`) |
| `Project.PropertyGroup[0].TargetFramework` | XML: root element first, `[n]` picks among same-named siblings (default first) |
| `Project.PropertyGroup.Nullable.@enable` | XML attribute |

A path that goes through a YAML alias is refused; address the anchored node instead. A key that exists only through a `<<` merge is refused with a hint to edit the anchored mapping.

## Formats

| Format | Files | Notes |
|---|---|---|
| YAML | `.yaml` `.yml` | block mappings and lists, compact `- key: v`, one-line plain, single- and double-quoted scalars, one-line flow `[ ]` `{ }`, `\|` and `>` block scalars (read only), anchors, aliases, `<<` merges, tags, several documents |
| Front matter | `.md` `.markdown` | the first `---` block, parsed as YAML; the body is compared as plain lines |
| JSON / JSONC | `.json` `.jsonc` | comments and trailing commas are kept; duplicate keys are visible |
| TOML | `.toml` | needs Python 3.11+; tables, dotted keys, array tables (`bin[1].name`), inline tables, array items |
| XML | `.xml` `.csproj` `.fsproj` `.vbproj` `.props` `.targets` `.config` | text and attribute values; the declaration and untouched tags stay byte for byte |

The format comes from the extension (a trailing `.orig`, `.bak` or `.before` is ignored); `--format yaml\|json\|jsonc\|toml\|xml\|frontmatter` overrides it.

Not supported by `set`: block scalars, multi-line TOML strings, XML text with entities, CDATA or comments, self-closing XML elements, alias values, tagged values. JSON5, INI, `.env` and HCL are out of scope.

## `set` rules

- `--type auto` (default) keeps the old type. A string stays a string; a plain YAML string that would be read as something else is written in double quotes (`quote_reason`: `yaml11_bool` for `NO`, `on`, `y`; `yaml11_octal` for `0755`; `float_lossy` for `1.10`; `yaml12_non_string` for `true`, `null`, `42`; `yaml11_other` for dates and `1:30`; `indicator` for `: `, ` #`, leading `-`, `*`, `[`, empty text).
- An int, float, bool or null keeps its type: a value that does not read that way is `E_TYPE`. Use `--type str` to store it as a string, or `--type int|float|bool|null` to change the type on purpose.
- Original single or double quotes are kept (single quotes fall back to double quotes only for control characters). JSON strings use `json.dumps` escaping; TOML keeps basic or literal style; XML escapes `& < >` (and the quote character in attributes).
- Exit 0 written (or unchanged, or `--dry-run`), 1 self-check refused to save, 2 input error.

## `verify` report fields

| Field | Meaning | Makes exit 1 |
|---|---|---|
| `changed_paths` | every path whose value changed | no |
| `collateral_paths` | changed, not expected, not explained by an alias | yes |
| `via_alias` | changed only through an anchored value that itself changed | no |
| `unchanged_expected` | an expected path did not change | yes |
| `lost_comments`, `added_comments` | comments by line and text (added ones are information only) | lost: yes |
| `unexpected_line_changes` | changed lines outside the expected paths | yes |
| `blank_line_delta` | change in blank lines outside the expected lines | yes |
| `key_order_changed` | mappings whose common keys changed order | yes |
| `quote_style_changed` | same value, different quoting | yes |
| `yaml11_risky` | newly introduced plain values that YAML 1.1 reads differently | yes |
| `duplicate_keys` | newly introduced duplicate keys | yes |
| `anchors_lost` | anchors that existed before and do not now | yes |
| `eol_changed`, `final_newline_changed`, `bom_changed` | line endings, final newline, byte order mark | yes |
| `before_sha256`, `after_sha256` | hashes of the two files | no |

`--allow` accepts `comments`, `order`, `quotes`, `blank_lines`, `eol` and switches off only those checks. `--text-only` skips the semantic layer (changed, collateral, alias, expected, order, quotes, duplicates, anchors, YAML 1.1) and keeps comments, blank lines, line endings, final newline and BOM.

## Error codes

`E_USAGE` bad arguments or path syntax, `E_FILE_NOT_FOUND`, `E_ENCODING` (not UTF-8, UTF-16, NUL bytes), `E_PARSE` (with `file:line:column`), `E_UNSUPPORTED`, `E_PATH_NOT_FOUND` (with nearest keys), `E_AMBIGUOUS_PATH` (duplicate key, with both line numbers), `E_NOT_SCALAR`, `E_TYPE`, `E_FORMAT`, `E_PYTHON`. Every error line ends with the fix.
