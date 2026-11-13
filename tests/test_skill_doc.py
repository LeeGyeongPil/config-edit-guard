"""AC-8 (local mirror): the skill folder, manifests and docs follow the Agent Skills rules."""
from __future__ import annotations

import json
import re

from conftest import ROOT

SKILL = ROOT / "skills" / "config-edit-guard"
STANDARD_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def split_frontmatter() -> tuple:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    block, body = text[4:].split("\n---\n", 1)
    keys = re.findall(r"^([A-Za-z-]+):", block, re.M)
    return block, keys, body


def test_frontmatter_uses_only_standard_keys_and_a_good_name():
    block, keys, _ = split_frontmatter()
    assert set(keys) <= STANDARD_KEYS and {"name", "description", "license", "compatibility"} <= set(keys)
    name = re.search(r"^name: (.+)$", block, re.M).group(1)
    assert name == "config-edit-guard" == SKILL.name and re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name)
    assert "claude" not in name and "anthropic" not in name


def test_description_rules():
    block, _, _ = split_frontmatter()
    desc = re.search(r"^description: (.+)$", block, re.M).group(1)
    assert 40 <= len(desc) <= 1024 and "<" not in desc and ">" not in desc
    assert re.match(r"^[A-Z][a-z]+s ", desc) and "Use when" in desc and "Not for" in desc


def test_body_size_and_single_level_references():
    _, _, body = split_frontmatter()
    assert len(body.splitlines()) <= 500 and len(body) / 4 <= 5000
    refs = set(re.findall(r"references/[\w.-]+\.md", body))
    assert refs == {f"references/{p.name}" for p in (SKILL / "references").glob("*.md")}
    for ref in refs:
        assert (SKILL / ref).is_file()
        assert "references/" not in (SKILL / ref).read_text(encoding="utf-8")  # one level deep only
    assert "\\" not in re.sub(r"`[^`]*`", "", body).replace("\\|", "")


def test_no_hidden_text_or_dangerous_commands():
    hidden = re.compile("[​-‏‪-‮⁦-⁩﻿­]")
    danger = re.compile(r"\bsudo\b|curl[^\n|]*\|\s*(ba)?sh|rm\s+-rf\s+/|--dangerously|dangerouslyDisableSandbox|git\s+push\s+(-f|--force)")
    for path in [SKILL / "SKILL.md", *(SKILL / "references").glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        assert "<!--" not in text and not hidden.search(text) and not danger.search(text), path.name


def test_manifests_match_the_skill_and_have_no_extras():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "config-edit-guard" and re.match(r"^\d+\.\d+\.\d+$", plugin["version"])
    assert set(plugin) <= {"name", "version", "description", "author", "license", "homepage", "repository", "keywords"}
    assert [p["name"] for p in market["plugins"]] == [plugin["name"]] and market["plugins"][0]["source"] == "./"
    assert market["name"] == "config-edit-guard-skill"
    for forbidden in ("hooks", "mcpServers", "agents", "commands"):
        assert forbidden not in plugin and forbidden not in market["plugins"][0]
        assert not (ROOT / forbidden).exists()
    assert f"## [{plugin['version']}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert plugin["version"] in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version: "{plugin["version"]}"' in (SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_skill_folder_layout_and_no_agent_config_files():
    assert [p.name for p in (ROOT / "skills").iterdir()] == ["config-edit-guard"]
    assert not (ROOT / "SKILL.md").exists()
    for name in (".claude", "CLAUDE.md", ".mcp.json", "AGENTS.md"):
        assert not (ROOT / name).exists()
    for f in (ROOT / "evals" / "fixtures").rglob("*"):
        assert not any(part.startswith(".") for part in f.relative_to(ROOT / "evals" / "fixtures").parts)
    for script in (SKILL / "scripts").glob("*.py"):
        assert not script.is_symlink()


def test_each_public_script_answers_help():
    from conftest import run_cli

    assert run_cli("--help").returncode == 0
