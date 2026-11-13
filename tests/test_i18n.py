"""AC-6: English and Korean screens, language precedence, argparse labels, equal key sets."""
from __future__ import annotations

import ast
import json
import re
import string
from pathlib import Path

import pytest
from conftest import SCRIPTS, clean_env, cli, run_cli, write
from samples import BASE_YAML

import _ceg_i18n as i18n

FORBIDDEN_IN_KO = ("usage:", "options:", "optional arguments:", "positional arguments:", "show this help message")


def fields(text: str) -> set:
    return {name for _, name, _, _ in string.Formatter().parse(text.replace("{{", "").replace("}}", "")) if name}


def test_message_tables_have_identical_keys_and_placeholders():
    assert set(i18n.EN) == set(i18n.KO)
    for key in i18n.EN:
        assert fields(i18n.EN[key]) == fields(i18n.KO[key]), key
        assert i18n.KO[key].strip(), key


def test_every_error_key_used_in_the_scripts_has_a_message_and_a_fix():
    used = set()
    for path in SCRIPTS.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ConfigError" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant):
                    used.add(arg.value)
    assert len(used) > 40
    for key in used:
        assert key in i18n.EN and key + ".fix" in i18n.EN, key
        assert key in i18n.KO and key + ".fix" in i18n.KO, key


def test_korean_table_is_really_korean():
    korean = sum(1 for v in i18n.KO.values() if re.search("[가-힣]", v))
    assert korean >= len(i18n.KO) - 12  # only a few entries (placeholders, loc.child) carry no Korean words


@pytest.mark.parametrize("cli_value,env,expected", [
    ("ko", {"LANG": "en_US.UTF-8"}, "ko"),
    ("en", {"CONFIG_EDIT_GUARD_LANG": "ko", "LANG": "ko_KR.UTF-8"}, "en"),
    (None, {"CONFIG_EDIT_GUARD_LANG": "ko", "LC_ALL": "en_US.UTF-8"}, "ko"),
    (None, {"CONFIG_EDIT_GUARD_LANG": "en", "LC_ALL": "ko_KR.UTF-8"}, "en"),
    (None, {"LC_ALL": "ko_KR.UTF-8", "LC_MESSAGES": "en_US", "LANG": "en_US"}, "ko"),
    (None, {"LC_ALL": "en_US.UTF-8", "LC_MESSAGES": "ko_KR", "LANG": "ko_KR"}, "en"),
    (None, {"LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US"}, "ko"),
    (None, {"LC_MESSAGES": "C", "LANG": "ko_KR.UTF-8"}, "en"),
    (None, {"LANG": "ko_KR.UTF-8"}, "ko"),
    (None, {"LANG": "ko"}, "ko"),
    (None, {"LANG": "ja_JP.UTF-8"}, "en"),
    (None, {"LANG": "C"}, "en"),
    (None, {"LANG": "POSIX"}, "en"),
    (None, {"LC_ALL": "", "LANG": "ko_KR.UTF-8"}, "ko"),
    (None, {}, "en"),
    ("fr", {"LANG": "ko_KR"}, "en"),
])
def test_language_precedence(cli_value, env, expected):
    assert i18n.resolve_lang(cli_value, env) == expected


def run_with(env_extra: dict, *args: str):
    env = clean_env(**env_extra)
    for name in list(env_extra):
        if env_extra[name] is None:
            del env[name]
    return run_cli(*args, env=env)


def test_precedence_through_the_real_script(tmp_path):
    f = write(tmp_path, "c.yaml", "a: 1\n")
    p = run_with({"LANG": "ko_KR.UTF-8"}, "locate", str(f), "zz")
    assert "오류 [E_PATH_NOT_FOUND]" in p.stderr
    p = run_with({"LANG": "ko_KR.UTF-8"}, "--lang", "en", "locate", str(f), "zz")
    assert "error [E_PATH_NOT_FOUND]" in p.stderr
    p = run_with({"LANG": "ko_KR.UTF-8", "CONFIG_EDIT_GUARD_LANG": "en"}, "locate", str(f), "zz")
    assert "error [E_PATH_NOT_FOUND]" in p.stderr
    p = run_with({"LANG": "en_US.UTF-8", "LC_ALL": "ko_KR.UTF-8"}, "locate", str(f), "zz")
    assert "오류" in p.stderr
    p = run_with({"LANG": "C"}, "locate", str(f), "zz", "--lang", "ko")
    assert "고치는 법:" in p.stderr


@pytest.mark.parametrize("sub", [[], ["locate"], ["set"], ["verify"]])
def test_korean_help_has_no_english_labels(sub):
    p = run_cli("--lang", "ko", *sub, "--help")
    assert p.returncode == 0
    assert "사용법:" in p.stdout and "옵션:" in p.stdout
    for word in FORBIDDEN_IN_KO:
        assert word not in p.stdout, word
    if sub:
        assert "위치 인자:" in p.stdout


@pytest.mark.parametrize("sub", [[], ["locate"], ["set"], ["verify"]])
def test_english_help_uses_english_labels(sub):
    p = run_cli("--lang", "en", *sub, "--help")
    assert p.returncode == 0 and "usage:" in p.stdout and "options:" in p.stdout
    assert "show this help message and exit" in p.stdout
    assert not re.search("[가-힣]", p.stdout)


def test_language_flag_after_the_subcommand_also_works():
    p = run_cli("verify", "--help", "--lang", "ko")
    assert "사용법:" in p.stdout


def test_missing_arguments_are_translated():
    p = run_cli("--lang", "ko", "set", "x.yaml")
    assert p.returncode == 2 and "다음 인자가 필요합니다" in p.stderr and "오류 [E_USAGE]" in p.stderr
    assert "the following arguments are required" not in p.stderr
    p = run_cli("--lang", "ko")
    assert p.returncode == 2 and "다음 인자가 필요합니다" in p.stderr
    p = run_cli("--lang", "ko", "locate", "f", "p", "--bad")
    assert "알 수 없는 인자" in p.stderr
    p = run_cli("--lang", "ko", "set", "f", "p", "v", "--type", "zzz")
    assert "잘못된 선택" in p.stderr and "선택 가능" in p.stderr
    p = run_cli("--lang", "en", "set", "x.yaml")
    assert "the following arguments are required" in p.stderr and "error [E_USAGE]" in p.stderr


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(v) for v in value[:1]]
    return type(value).__name__


def test_json_report_keys_do_not_depend_on_the_language(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    g = write(tmp_path, "d.yaml", BASE_YAML.replace("   # seconds", ""))
    for args in (("locate", str(f), "service.name"), ("set", str(f), "service.name", "x", "--dry-run"),
                 ("verify", str(f), str(g), "--expect", "service.name")):
        en = json.loads(cli("--lang", "en", *args, "--json")[1])
        ko = json.loads(cli("--lang", "ko", *args, "--json")[1])
        assert list(en) == list(ko)
        en.pop("command"), ko.pop("command")
        assert shape(en) == shape(ko)
        assert en == ko  # no translated text inside the report


def test_human_output_switches_language(tmp_path):
    f = write(tmp_path, "c.yaml", BASE_YAML)
    g = write(tmp_path, "d.yaml", BASE_YAML.replace("   # seconds", ""))
    en = cli("--lang", "en", "verify", str(f), str(g))[1]
    ko = cli("--lang", "ko", "verify", str(f), str(g))[1]
    assert "lost comment at line 3" in en and "사라진 주석(lost comment, 3번째 줄)" in ko
    assert not re.search("[가-힣]", en)
    assert "Revert the unintended changes" in en and "되돌리거나" in ko
    loc_en = cli("--lang", "en", "locate", str(f), "service.name")[1]
    loc_ko = cli("--lang", "ko", "locate", str(f), "service.name")[1]
    assert "is a value" in loc_en and "값입니다" in loc_ko


def test_every_error_message_renders_in_both_languages():
    from _ceg_model import ConfigError

    params = dict(file="f", line=1, col=2, path="a.b", key="k", at="x", count=3, index=4, suggest="s", esc="\\q", text="t",
                  ch="c", name="n", detail="d", known="k", choices="c", value="v", want="int", kind="map", keys="a, b",
                  tag="!t", old="int", root="r", doc=1, num_lines=[1, 2], reason="r", offset=5, pos=6, version="3.10")
    for key in i18n.EN:
        if key.startswith("e.") and not key.endswith(".fix"):
            err = ConfigError("E_X", key, **params)
            for lang in ("en", "ko"):
                line = i18n.error_line(lang, err)
                assert "[E_X]" in line and not re.search(r"\{[a-z_]+\}", line), line


def test_argparse_error_translation_covers_known_shapes():
    cases = {
        "the following arguments are required: a, b": "다음 인자가 필요합니다: a, b",
        "unrecognized arguments: --x": "알 수 없는 인자: --x",
        "argument --type: invalid choice: 'q' (choose from 'a', 'b')": "잘못된 선택 'q' (선택 가능: 'a', 'b')",
        "argument --report: expected one argument": "값 하나가 필요합니다",
        "argument --doc: invalid int value: 'x'": "잘못된 int 값: 'x'",
    }
    for message, expected in cases.items():
        assert expected in i18n.translate_argparse_error("ko", message)
    assert i18n.translate_argparse_error("ko", "something new") == "something new"


def test_scripts_directory_has_no_stray_translation_files():
    assert not list(Path(SCRIPTS).glob("*.po")) and not list(Path(SCRIPTS).glob("*.mo"))
