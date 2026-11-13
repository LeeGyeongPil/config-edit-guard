"""AC-9: the evaluation set is complete and its graders match what the script really produces."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT, SCRIPT, clean_env

EVALS = ROOT / "evals"

TASK_COMMANDS = {
    "task-yaml-image-tag": ["set", "deploy.yaml", "spec.template.spec.containers[0].image", "registry.example.com/api:1.4.2", "--report", "report.json"],
    "task-yaml-norway": ["set", "locale.yaml", "country", "NO", "--report", "report.json"],
    "task-verify-collateral": ["verify", "before.yaml", "after.yaml", "--expect", "service.name", "--report", "report.json"],
    "task-jsonc-setting": ["set", "settings.jsonc", '"workbench.colorTheme"', "Solarized Light", "--report", "report.json"],
    "task-csproj-framework": ["set", "App.csproj", "Project.PropertyGroup[0].TargetFramework", "net9.0", "--report", "report.json"],
    "task-toml-version": ["set", "pyproject.toml", "project.version", "0.4.0", "--report", "report.json"],
    "task-error-duplicate-key": ["set", "server.yaml", "server.port", "8443"],
}

FAKE_SCRIPT = (
    "import sys\n"
    "args = sys.argv[1:]\n"
    "if '--report' in args:\n"
    "    open(args[args.index('--report') + 1], 'w').write('{}')\n"
    "print('{}')\n"
)


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    block = text.split("---", 2)[1]
    out = {}
    for line in block.strip().splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def grader_regexes(case: Path) -> list:
    found = []
    for g in sorted((case / "graders").glob("*.md")):
        fm = frontmatter(g)
        if fm.get("type") != "regex":
            continue
        pattern = fm["pattern"]
        assert pattern.startswith("'") and pattern.endswith("'"), g
        pattern = pattern[1:-1].replace("''", "'")
        target = fm["target"]
        path = None if target == "trace" else re.search(r"path:\s*([^\s}]+)", target).group(1)
        found.append((g.stem, pattern, path, fm.get("match", "")))
    return found


def case_dirs(prefix: str) -> list:
    return sorted(p for p in EVALS.iterdir() if p.is_dir() and p.name.startswith(prefix))


def test_eval_set_counts_and_shapes():
    triggers = [p for p in case_dirs("trigger-")]
    near = case_dirs("notrigger-near-")
    far = [p for p in case_dirs("notrigger-") if not p.name.startswith("notrigger-near-")]
    tasks = case_dirs("task-")
    assert (len(triggers), len(near), len(far)) == (12, 6, 5)
    assert sorted(p.name for p in tasks) == sorted(TASK_COMMANDS)
    korean = [p for p in triggers if re.search("[가-힣]", (p / "prompt.md").read_text(encoding="utf-8"))]
    assert len(korean) == 3
    for p in triggers:
        assert "config-edit-guard" in (p / "graders" / "skill-fired.md").read_text(encoding="utf-8")
        body = (p / "prompt.md").read_text(encoding="utf-8").lower()
        assert "skill" not in body and "config-edit-guard" not in body
    for p in near + far:
        assert "max: 0" in (p / "graders" / "skill-not-fired.md").read_text(encoding="utf-8")


def test_every_task_has_fixtures_a_script_grader_and_script_only_values():
    for task in case_dirs("task-"):
        assert (EVALS / "fixtures" / task.name).is_dir() and any((EVALS / "fixtures" / task.name).iterdir())
        ran = (task / "graders" / "script-ran.md").read_text(encoding="utf-8")
        assert "tool: Bash" in ran and "scripts/config_edit_guard" in ran and "arm: with-only" in ran
        patterns = " ".join(p for _, p, _, _ in grader_regexes(task))
        assert re.search(r"after_sha256|collateral_paths|E_AMBIGUOUS_PATH", patterns), task.name
    all_patterns = " ".join(p for t in case_dirs("task-") for _, p, _, _ in grader_regexes(t))
    for needle in ("after_sha256", "start_byte", "collateral_paths", "quote_reason", "E_AMBIGUOUS_PATH"):
        assert needle in all_patterns, needle
    assert any(t.name.startswith("task-error-") for t in case_dirs("task-"))


def run_task(task: str, tmp_path: Path, script: Path) -> tuple:
    work = tmp_path / task
    shutil.copytree(EVALS / "fixtures" / task, work)
    proc = subprocess.run([sys.executable, str(script), *TASK_COMMANDS[task]], cwd=work, capture_output=True, text=True,
                          encoding="utf-8", env=clean_env(), timeout=60)
    return work, proc.stdout + proc.stderr


def matches(pattern: str, kind: str, work: Path, rel, trace: str) -> bool:
    if rel is None:
        text = trace
    else:
        f = work / rel
        text = f.read_text(encoding="utf-8") if f.exists() else ""
    assert "(?P<" not in pattern and "\\A" not in pattern and "\\Z" not in pattern  # evaluator is JavaScript
    hits = re.findall(pattern, text)
    if kind == "not_contains":
        return not hits
    if kind.startswith("count:"):
        return len(hits) == int(kind.split(":")[1])
    return bool(hits)


@pytest.mark.parametrize("task", sorted(TASK_COMMANDS))
def test_every_regex_grader_matches_the_real_script_output(task, tmp_path):
    if task == "task-toml-version" and sys.version_info < (3, 11):
        pytest.skip("TOML support needs Python 3.11+ (tomllib)")
    work, trace = run_task(task, tmp_path, SCRIPT)
    graders = grader_regexes(EVALS / task)
    assert graders
    for name, pattern, rel, kind in graders:
        assert matches(pattern, kind, work, rel, trace), f"{task}/{name}: {pattern!r} (target {rel or 'trace'})"


@pytest.mark.parametrize("task", sorted(TASK_COMMANDS))
def test_a_fake_script_printing_empty_json_does_not_match(task, tmp_path):
    fake = tmp_path / "fake_script.py"
    fake.write_text(FAKE_SCRIPT, encoding="utf-8")
    work, trace = run_task(task, tmp_path / "fake", fake)
    graders = grader_regexes(EVALS / task)
    failing = [name for name, pattern, rel, kind in graders if not matches(pattern, kind, work, rel, trace)]
    script_only = [name for name, _p, rel, kind in graders if (rel in (None, "report.json")) and kind != "not_contains"]
    assert script_only and set(script_only) <= set(failing), (task, script_only, failing)
    assert len(failing) >= 2


def test_graders_hold_exact_values_not_loose_patterns():
    for task in case_dirs("task-"):
        for name, pattern, _rel, _kind in grader_regexes(task):
            assert len(pattern) >= 12 and pattern not in (".*", ".+"), (task.name, name)
    for task in ("task-yaml-image-tag", "task-yaml-norway", "task-jsonc-setting", "task-csproj-framework", "task-toml-version"):
        assert any(re.search(r'"after_sha256": "[0-9a-f]{64}"', p) for _, p, _r, _k in grader_regexes(EVALS / task))
