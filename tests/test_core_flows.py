from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import gitcoach
import pytest


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def init_repo(base: Path, name: str = "repo") -> Path:
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-q", "--initial-branch", "main")
    run_git(repo, "config", "user.name", "Tester")
    run_git(repo, "config", "user.email", "tester@example.com")
    (repo / "app.txt").write_text("one\n", encoding="utf-8")
    run_git(repo, "add", "app.txt")
    run_git(repo, "commit", "-q", "-m", "init")
    return repo


def test_start_carries_dirty_changes_to_feature_branch(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path)
    run_git(repo, "branch", "dev")
    (repo / "app.txt").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    result = gitcoach.command_start(
        argparse.Namespace(feature_name="dirty carry", dev_branch="dev", autostash=True)
    )

    assert result == 0
    assert run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feature/dirty-carry"
    status = run_git(repo, "status", "--short").stdout
    assert " M app.txt" in status or "M app.txt" in status


def test_save_guard_block_on_main_auto_creates_feature_branch(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path)
    monkeypatch.chdir(repo)
    gitcoach.command_guard(argparse.Namespace(force=False))

    (repo / "app.txt").write_text("one\ntwo\n", encoding="utf-8")
    result = gitcoach.command_save(
        argparse.Namespace(
            message="feat: update app",
            include_untracked=False,
            guided=False,
            strict_message=False,
        )
    )

    assert result == 0
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert branch.startswith("feature/")
    assert run_git(repo, "log", "-1", "--pretty=%s").stdout.strip() == "feat: update app"


def test_undo_actions_unstage_and_uncommit(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path)
    monkeypatch.chdir(repo)

    (repo / "app.txt").write_text("one\ntwo\n", encoding="utf-8")
    run_git(repo, "add", "app.txt")
    assert gitcoach.has_staged_changes()

    gitcoach.undo_unstage_all()
    assert not gitcoach.has_staged_changes()

    run_git(repo, "add", "app.txt")
    run_git(repo, "commit", "-q", "-m", "second")
    before = int(run_git(repo, "rev-list", "--count", "HEAD").stdout.strip())
    gitcoach.undo_last_commit(keep_staged=False)
    after = int(run_git(repo, "rev-list", "--count", "HEAD").stdout.strip())
    backup_refs = run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/backup/undo-reset-*")
    assert before == 2
    assert after == 1
    assert backup_refs.stdout.strip()


def test_doctor_all_repos_scan_mode(tmp_path: Path, capsys) -> None:
    root = tmp_path / "workspace"
    repo_a = init_repo(root, "a")
    repo_b = root / "nested" / "b"
    repo_b.mkdir(parents=True, exist_ok=True)
    run_git(repo_b, "init", "-q", "--initial-branch", "main")
    run_git(repo_a, "config", "user.email", "wrong@example.com")

    # Ensure command can run outside any repo.
    cwd_before = Path.cwd()
    try:
        os.chdir(tmp_path)
        rc = gitcoach.command_doctor(
            argparse.Namespace(
                target_email="right@example.com",
                all_repos=str(root),
                max_depth=4,
                target_name=None,
                old_email=[],
                fix_email_history=False,
                promote_main=False,
                promote_source=None,
                main_branch="main",
                remote="origin",
                set_github_default=True,
                yes=False,
                push=False,
            )
        )
    finally:
        os.chdir(cwd_before)

    captured = capsys.readouterr()
    assert rc == 0
    assert "Scanning 2 repo(s)" in captured.out
    assert "[warn] a:" in captured.out


def test_profile_set_fast_updates_config_and_hooks(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path)
    monkeypatch.chdir(repo)

    gitcoach.command_init(
        argparse.Namespace(main_branch="main", dev_branch="dev", install_guards=True)
    )

    rc = gitcoach.command_profile(
        argparse.Namespace(set="fast", install_guards=True)
    )

    assert rc == 0
    config_text = (repo / ".gitcoach.yml").read_text(encoding="utf-8")
    assert "workflow_profile: fast" in config_text
    assert "guard_commit_main: false" in config_text
    assert "guard_push_main: false" in config_text

    pre_commit_text = (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    pre_push_text = (repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")
    assert 'BLOCK_COMMIT_MAIN="0"' in pre_commit_text
    assert 'BLOCK_PUSH_MAIN="0"' in pre_push_text


def test_ignore_apply_adds_patterns_and_logs_action(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path)
    monkeypatch.chdir(repo)

    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("x", encoding="utf-8")
    (repo / "dist" / "bundle.css").write_text("x", encoding="utf-8")

    rc = gitcoach.command_ignore(
        argparse.Namespace(apply=True, pattern=[], yes=True)
    )

    assert rc == 0
    ignore_text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "dist/" in ignore_text

    actions_text = (repo / ".git" / ".gitcoach-actions.jsonl").read_text(encoding="utf-8")
    assert '"action": "ignore.apply"' in actions_text


def test_actions_command_outputs_recent_entries(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = init_repo(tmp_path)
    monkeypatch.chdir(repo)

    gitcoach.command_profile(
        argparse.Namespace(set="solo-safe", install_guards=False)
    )

    rc = gitcoach.command_actions(argparse.Namespace(limit=10))
    captured = capsys.readouterr()

    assert rc == 0
    assert "Recent actions" in captured.out
    assert "profile.set" in captured.out


def test_save_respects_strict_untracked_policy(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path)
    monkeypatch.chdir(repo)

    gitcoach.command_profile(
        argparse.Namespace(set="strict", install_guards=False)
    )
    (repo / "scratch.tmp").write_text("temp\n", encoding="utf-8")
    (repo / "app.txt").write_text("one\ntwo\n", encoding="utf-8")

    with pytest.raises(gitcoach.GitCoachError):
        gitcoach.command_save(
            argparse.Namespace(
                message="feat: update app",
                include_untracked=False,
                guided=False,
                strict_message=False,
            )
        )
