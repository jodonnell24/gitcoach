#!/usr/bin/env python3
"""gitcoach: opinionated Git helpers for solo developers."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class GitCoachError(Exception):
    """Raised for expected gitcoach usage/runtime errors."""


class UserCancelled(Exception):
    """Raised when a user cancels an interactive prompt."""


def safe_input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError as err:
        raise UserCancelled from err
    except KeyboardInterrupt as err:
        print("")
        raise UserCancelled from err


def is_interactive_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def color_enabled() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.getenv("NO_COLOR"):
        return False
    term = os.getenv("TERM", "")
    return term.lower() not in {"", "dumb"}


def style_text(text: str, *, color: str | None = None, bold: bool = False, dim: bool = False) -> str:
    if not color_enabled():
        return text

    color_codes = {
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
        "cyan": "36",
        "gray": "90",
    }
    parts: list[str] = []
    if bold:
        parts.append("1")
    if dim:
        parts.append("2")
    if color and color in color_codes:
        parts.append(color_codes[color])
    if not parts:
        return text
    return f"\033[{';'.join(parts)}m{text}\033[0m"


def print_rule(char: str = "-", width: int = 70) -> None:
    print(style_text(char * width, color="gray", dim=True))


def print_box(title: str, lines: list[str]) -> None:
    width = max(52, len(title) + 6, *(len(line) + 4 for line in lines)) if lines else max(52, len(title) + 6)
    top = "+" + "-" * (width - 2) + "+"
    print(style_text(top, color="cyan"))
    print(style_text(f"| {title.ljust(width - 4)} |", color="cyan", bold=True))
    print(style_text(top, color="cyan"))
    for line in lines:
        print(f"| {line.ljust(width - 4)} |")
    print(style_text(top, color="cyan"))


def prompt_label(text: str) -> str:
    return style_text(text, color="blue", bold=True)


def can_use_gum() -> bool:
    if os.getenv("GITCOACH_NO_GUM"):
        return False
    return is_interactive_tty() and shutil.which("gum") is not None


def choose_option_with_gum(prompt: str, options: list[str], *, allow_cancel: bool = True) -> str:
    # Pass options as argv so gum keeps stdin connected to the user's TTY.
    # Capturing only stdout lets us read the selected value while still rendering UI.
    result = subprocess.run(
        ["gum", "filter", "--placeholder", prompt, *options],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )
    if result.returncode != 0:
        if allow_cancel:
            raise UserCancelled
        detail = (result.stderr or result.stdout or "").strip()
        raise GitCoachError(detail or "No selection made.")

    picked = (result.stdout or "").strip()
    if not picked:
        if allow_cancel:
            raise UserCancelled
        raise GitCoachError("No selection made.")
    if picked not in options:
        if allow_cancel:
            raise UserCancelled
        raise GitCoachError("Invalid selection.")
    return picked


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture,
        cwd=str(cwd) if cwd else None,
    )
    if check and result.returncode != 0:
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise GitCoachError(f"Command failed: {' '.join(cmd)}\n{detail}")
    return result


def git(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check, capture=capture)


def ensure_git_repo() -> None:
    try:
        git("rev-parse", "--git-dir")
    except GitCoachError as err:
        raise GitCoachError("Not inside a Git repository.") from err


def repo_has_commits() -> bool:
    result = git("rev-parse", "--verify", "HEAD", check=False)
    return result.returncode == 0


def current_branch() -> str:
    result = git("rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip()


def branch_exists(name: str) -> bool:
    result = git("show-ref", "--verify", f"refs/heads/{name}", check=False)
    return result.returncode == 0


def remote_exists(name: str) -> bool:
    result = git("remote", "get-url", name, check=False)
    return result.returncode == 0


def get_remote_url(name: str) -> str | None:
    result = git("remote", "get-url", name, check=False)
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def infer_github_repo_slug(remote_url: str) -> str | None:
    value = remote_url.strip()
    patterns = [
        r"^git@github\.com:(?P<slug>.+?)(?:\.git)?$",
        r"^https?://github\.com/(?P<slug>.+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<slug>.+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, value)
        if not match:
            continue
        slug = match.group("slug").strip("/")
        if slug.count("/") == 1:
            return slug
    return None


def get_config(key: str) -> str | None:
    result = git("config", "--get", key, check=False)
    value = result.stdout.strip()
    return value if value else None


def list_local_branches() -> list[str]:
    result = git("for-each-ref", "--format=%(refname:short)", "refs/heads", check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def list_remotes() -> list[str]:
    result = git("remote", check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def current_upstream() -> str | None:
    result = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False)
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    return value


def status_counts() -> tuple[int, int, int]:
    staged = 0
    unstaged = 0
    untracked = 0
    for raw in git("status", "--porcelain").stdout.splitlines():
        line = raw.rstrip("\n")
        if not line:
            continue
        if line.startswith("??"):
            untracked += 1
            continue
        if len(line) >= 1 and line[0] != " ":
            staged += 1
        if len(line) >= 2 and line[1] != " ":
            unstaged += 1
    return staged, unstaged, untracked


def ahead_behind(upstream: str) -> tuple[int, int]:
    result = git("rev-list", "--left-right", "--count", f"{upstream}...HEAD", check=False)
    if result.returncode != 0:
        return (0, 0)
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return (0, 0)
    behind = int(parts[0])
    ahead = int(parts[1])
    return ahead, behind


def ensure_clean_worktree() -> None:
    result = git("status", "--porcelain")
    if result.stdout.strip():
        raise GitCoachError("Working tree is not clean. Commit/stash changes first.")


def slugify_feature_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise GitCoachError("Feature name must contain letters or numbers.")
    return slug


def write_default_config(main_branch: str, dev_branch: str) -> None:
    path = Path(".gitcoach.yml")
    if path.exists():
        return
    content = (
        f"main_branch: {main_branch}\n"
        f"dev_branch: {dev_branch}\n"
        "save_tracked_only: true\n"
    )
    path.write_text(content, encoding="utf-8")


def pick_main_branch(preferred: str) -> str:
    if branch_exists(preferred):
        return preferred
    for candidate in ("main", "master"):
        if branch_exists(candidate):
            return candidate
    return preferred


def prompt_text(prompt: str, *, default: str | None = None, required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = safe_input(f"{prompt_label(prompt)}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("[warn] Value is required.")


def prompt_confirm(prompt: str, *, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = safe_input(f"{prompt_label(prompt)} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("[warn] Enter y or n.")


def choose_option(prompt: str, options: list[str], *, allow_cancel: bool = True) -> str:
    if not options:
        raise GitCoachError(f"No options available for: {prompt}")

    if can_use_gum():
        print(style_text(f"\n{prompt}", color="magenta", bold=True))
        print(style_text("Type to filter, Enter to select, Esc/Ctrl+C to cancel.", color="gray", dim=True))
        try:
            return choose_option_with_gum(prompt, options, allow_cancel=allow_cancel)
        except GitCoachError as err:
            print(f"[warn] gum selection failed, falling back to built-in menu: {err}")

    while True:
        print(style_text(f"\n{prompt}", color="magenta", bold=True))
        search_hint = "Search text (blank=all"
        if allow_cancel:
            search_hint += ", q=cancel"
        search_hint += ")"
        query = safe_input(f"{prompt_label(search_hint)}: ").strip()
        if allow_cancel and query.lower() in {"q", "quit", "cancel", "back"}:
            raise UserCancelled

        filtered = options
        if query:
            lowered = query.lower()
            filtered = [item for item in options if lowered in item.lower()]

        if not filtered:
            print("[warn] No matches. Try another search.")
            continue

        print_rule()
        for idx, item in enumerate(filtered, start=1):
            print(f"  {style_text(str(idx) + '.', color='cyan', bold=True)} {item}")
        print_rule()

        choice_prompt = "Choose number"
        if allow_cancel:
            choice_prompt += " (or q to cancel)"
        raw_choice = safe_input(f"{prompt_label(choice_prompt)}: ").strip().lower()
        if allow_cancel and raw_choice in {"q", "quit", "cancel", "back"}:
            raise UserCancelled
        if not raw_choice.isdigit():
            print("[warn] Enter a number from the list.")
            continue

        idx = int(raw_choice)
        if idx < 1 or idx > len(filtered):
            print("[warn] Number out of range.")
            continue
        return filtered[idx - 1]


def choose_multiple_options(prompt: str, options: list[str]) -> list[str]:
    selected: list[str] = []
    remaining = list(options)
    while remaining:
        title = prompt
        if selected:
            title += f" (selected {len(selected)}, q=done)"
        try:
            picked = choose_option(title, remaining, allow_cancel=True)
        except UserCancelled:
            break
        selected.append(picked)
        remaining = [item for item in remaining if item != picked]
        if not remaining:
            break
        if not prompt_confirm("Select another?", default=False):
            break
    return selected


def make_doctor_args(**overrides: object) -> argparse.Namespace:
    payload: dict[str, object] = {
        "target_email": None,
        "target_name": None,
        "old_email": [],
        "fix_email_history": False,
        "promote_main": False,
        "promote_source": None,
        "main_branch": "main",
        "remote": "origin",
        "set_github_default": True,
        "yes": False,
        "push": False,
    }
    payload.update(overrides)
    if payload.get("old_email") is None:
        payload["old_email"] = []
    return argparse.Namespace(**payload)


def command_init(args: argparse.Namespace) -> int:
    ensure_git_repo()
    main_branch = pick_main_branch(args.main_branch)
    dev_branch = args.dev_branch

    write_default_config(main_branch, dev_branch)
    print(f"[ok] Wrote .gitcoach.yml (main={main_branch}, dev={dev_branch})")

    user_name = get_config("user.name")
    user_email = get_config("user.email")
    if user_name and user_email:
        print(f"[ok] Git identity set: {user_name} <{user_email}>")
    else:
        print("[warn] Git identity missing. Set both values:")
        print("       git config --global user.name \"Your Name\"")
        print("       git config --global user.email \"you@example.com\"")

    if repo_has_commits() and branch_exists(main_branch) and not branch_exists(dev_branch):
        git("branch", dev_branch, main_branch)
        print(f"[ok] Created {dev_branch} from {main_branch}")
    elif branch_exists(dev_branch):
        print(f"[ok] {dev_branch} branch already exists")
    else:
        print("[warn] No commits yet. Create initial commit before branch setup.")

    return 0


def command_start(args: argparse.Namespace) -> int:
    ensure_git_repo()
    ensure_clean_worktree()

    feature_branch = f"feature/{slugify_feature_name(args.feature_name)}"
    dev_branch = args.dev_branch

    if not branch_exists(dev_branch):
        raise GitCoachError(f"Missing {dev_branch} branch. Run: gitcoach init")

    git("checkout", dev_branch, capture=False)
    if branch_exists(feature_branch):
        git("checkout", feature_branch, capture=False)
        print(f"[ok] Switched to existing branch: {feature_branch}")
    else:
        git("checkout", "-b", feature_branch, capture=False)
        print(f"[ok] Created and switched to: {feature_branch}")
    return 0


def print_untracked_preview() -> None:
    result = git("ls-files", "--others", "--exclude-standard", check=False)
    entries = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not entries:
        return
    print("[warn] Untracked files were not staged:")
    for item in entries[:15]:
        print(f"       - {item}")
    if len(entries) > 15:
        print(f"       ... {len(entries) - 15} more")
    print("       Use --include-untracked to stage everything.")


def command_save(args: argparse.Namespace) -> int:
    ensure_git_repo()

    if args.include_untracked:
        git("add", "-A", capture=False)
    else:
        git("add", "-u", capture=False)
        print_untracked_preview()

    staged = git("diff", "--cached", "--name-only")
    if not staged.stdout.strip():
        raise GitCoachError("No staged tracked changes to commit.")

    git("commit", "-m", args.message, capture=False)
    print("[ok] Commit created")
    return 0


def command_ship(args: argparse.Namespace) -> int:
    ensure_git_repo()
    ensure_clean_worktree()

    main_branch = args.main_branch
    dev_branch = args.dev_branch
    if not branch_exists(main_branch):
        raise GitCoachError(f"Missing {main_branch} branch.")
    if not branch_exists(dev_branch):
        raise GitCoachError(f"Missing {dev_branch} branch.")

    current = current_branch()
    if current != dev_branch:
        print(f"[info] Switching from {current} to {dev_branch} before ship")
        git("checkout", dev_branch, capture=False)

    git("checkout", main_branch, capture=False)
    git("merge", "--ff-only", dev_branch, capture=False)
    print(f"[ok] Merged {dev_branch} -> {main_branch} (fast-forward)")

    if args.push:
        git("push", "origin", main_branch, capture=False)
        print(f"[ok] Pushed origin/{main_branch}")
    return 0


def collect_history_emails() -> dict[str, int]:
    result = git("log", "--all", "--format=%ae%n%ce", check=False)
    counts: dict[str, int] = {}
    for raw in result.stdout.splitlines():
        email = raw.strip()
        if not email:
            continue
        counts[email] = counts.get(email, 0) + 1
    return counts


def print_email_table(counts: dict[str, int]) -> None:
    if not counts:
        print("[info] No commit history found.")
        return

    print("Email usage in commit history:")
    sorted_items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    for email, count in sorted_items[:20]:
        print(f"  - {email}: {count}")
    if len(sorted_items) > 20:
        print(f"  ... {len(sorted_items) - 20} more")


def select_emails_to_rewrite(
    history_counts: dict[str, int],
    target_email: str,
    explicit_old_emails: Iterable[str],
) -> list[str]:
    cleaned_explicit = [e.strip().lower() for e in explicit_old_emails if e.strip()]
    history_by_lower: dict[str, list[str]] = {}
    for exact in history_counts:
        history_by_lower.setdefault(exact.lower(), []).append(exact)
    target_email = target_email.lower()

    if cleaned_explicit:
        unknown = [e for e in cleaned_explicit if e not in history_by_lower]
        if unknown:
            print("[warn] These --old-email values were not found in history:")
            for email in unknown:
                print(f"       - {email}")
        lower_targets = [e for e in cleaned_explicit if e in history_by_lower and e != target_email]
    else:
        lower_targets = [lower for lower in history_by_lower if lower != target_email]

    selected: list[str] = []
    for lower in lower_targets:
        selected.extend(history_by_lower.get(lower, []))
    return sorted(set(selected))


def create_backup_branch() -> str:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"backup/email-rewrite-{timestamp}"
    git("branch", backup)
    return backup


def make_archive_main_name(main_branch: str) -> str:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    normalized = re.sub(r"[^a-zA-Z0-9._/-]+", "-", main_branch).strip("-")
    normalized = normalized.replace("/", "-")
    return f"archive/{normalized}-before-promote-{timestamp}"


def has_filter_repo() -> bool:
    probe = git("filter-repo", "--help", check=False)
    return probe.returncode == 0


def rewrite_history_filter_repo(old_emails: list[str], target_name: str, target_email: str) -> None:
    mailmap_path = Path(".gitcoach-mailmap")
    lines = [f"{target_name} <{target_email}> <{old_email}>\n" for old_email in old_emails]
    mailmap_path.write_text("".join(lines), encoding="utf-8")

    try:
        git("filter-repo", "--force", "--mailmap", str(mailmap_path), capture=False)
    finally:
        if mailmap_path.exists():
            mailmap_path.unlink()


def rewrite_history_filter_branch(old_emails: list[str], target_name: str, target_email: str) -> None:
    script_lines = []
    for old in old_emails:
        old_q = shlex.quote(old)
        target_email_q = shlex.quote(target_email)
        target_name_q = shlex.quote(target_name)
        script_lines.append(
            "if [ \"$GIT_AUTHOR_EMAIL\" = "
            f"{old_q}"
            " ]; then "
            f"GIT_AUTHOR_EMAIL={target_email_q}; "
            f"GIT_AUTHOR_NAME={target_name_q}; "
            "export GIT_AUTHOR_EMAIL GIT_AUTHOR_NAME; "
            "fi"
        )
        script_lines.append(
            "if [ \"$GIT_COMMITTER_EMAIL\" = "
            f"{old_q}"
            " ]; then "
            f"GIT_COMMITTER_EMAIL={target_email_q}; "
            f"GIT_COMMITTER_NAME={target_name_q}; "
            "export GIT_COMMITTER_EMAIL GIT_COMMITTER_NAME; "
            "fi"
        )
    env_filter = "\n".join(script_lines)
    run(
        [
            "env",
            "FILTER_BRANCH_SQUELCH_WARNING=1",
            "git",
            "filter-branch",
            "-f",
            "--env-filter",
            env_filter,
            "--tag-name-filter",
            "cat",
            "--",
            "--all",
        ],
        capture=False,
    )


def prompt_confirm_history_rewrite(target_email: str, old_emails: list[str], backup_branch: str) -> None:
    print("[warn] History rewrite is destructive to commit hashes.")
    print(f"[warn] A backup branch was created: {backup_branch}")
    print("[warn] You will need force-push to update remotes.")
    print(f"[warn] Rewrite plan: {len(old_emails)} email(s) -> {target_email}")
    typed = safe_input(f"{prompt_label('Type REWRITE to continue')}: ").strip()
    if typed != "REWRITE":
        raise GitCoachError("Cancelled.")


def prompt_confirm_promote_main(
    source_branch: str,
    main_branch: str,
    archive_branch: str | None,
    remote_name: str,
    will_push: bool,
) -> None:
    print("[warn] Promoting branches rewires local/remote branch layout.")
    print(f"[warn] Promote source branch: {source_branch} -> {main_branch}")
    if archive_branch:
        print(f"[warn] Old {main_branch} will be renamed to: {archive_branch}")
    if will_push:
        print(
            f"[warn] Remote {remote_name} will be updated (force push {main_branch})."
        )
    typed = safe_input(f"{prompt_label('Type PROMOTE to continue')}: ").strip()
    if typed != "PROMOTE":
        raise GitCoachError("Cancelled.")


def promote_branch_to_main(
    *,
    source_branch: str,
    main_branch: str,
    archive_branch_name: str | None = None,
) -> str | None:
    if not repo_has_commits():
        raise GitCoachError("Repository has no commits. Nothing to promote.")
    if not branch_exists(source_branch):
        raise GitCoachError(f"Source branch does not exist: {source_branch}")

    current = current_branch()
    if source_branch == main_branch:
        print(f"[ok] Source branch is already {main_branch}.")
        return None

    archive_branch: str | None = None
    if branch_exists(main_branch):
        archive_branch = archive_branch_name or make_archive_main_name(main_branch)
        if current == main_branch:
            git("checkout", source_branch, capture=False)
            current = source_branch
        git("branch", "-m", main_branch, archive_branch, capture=False)
        print(f"[ok] Archived old {main_branch} as {archive_branch}")

    if current != source_branch:
        git("checkout", source_branch, capture=False)

    git("branch", "-m", source_branch, main_branch, capture=False)
    print(f"[ok] Promoted {source_branch} to {main_branch}")
    return archive_branch


def maybe_push_promoted_branches(
    *,
    remote_name: str,
    main_branch: str,
    archive_branch: str | None,
) -> None:
    if not remote_exists(remote_name):
        print(f"[warn] Remote {remote_name} not found. Skipping push.")
        return

    if archive_branch:
        git("push", remote_name, f"{archive_branch}:{archive_branch}", capture=False)
        print(f"[ok] Pushed archive branch: {archive_branch}")

    lease_push = run(
        ["git", "push", "--force-with-lease", "-u", remote_name, main_branch],
        check=False,
    )
    if lease_push.returncode != 0:
        detail = (lease_push.stderr or lease_push.stdout or "").strip()
        print(f"[warn] Force-with-lease failed: {detail or 'unknown error'}")
        print("[warn] Retrying with --force for promote flow.")
        git("push", "--force", "-u", remote_name, main_branch, capture=False)
    else:
        if lease_push.stdout:
            print(lease_push.stdout.strip())
        if lease_push.stderr:
            print(lease_push.stderr.strip())

    git("remote", "set-head", remote_name, "-a", check=False, capture=False)
    print(f"[ok] Pushed promoted branch to {remote_name}/{main_branch}")


def maybe_set_github_default_branch(
    *,
    remote_name: str,
    main_branch: str,
) -> None:
    if shutil.which("gh") is None:
        print("[warn] gh CLI not installed. Skipping GitHub default branch update.")
        return

    remote_url = get_remote_url(remote_name)
    if not remote_url:
        print(f"[warn] Could not read URL for remote {remote_name}.")
        return

    slug = infer_github_repo_slug(remote_url)
    if not slug:
        print("[warn] Remote is not a GitHub URL. Skipping default branch update.")
        return

    auth = run(["gh", "auth", "status", "-h", "github.com"], check=False)
    if auth.returncode != 0:
        print("[warn] gh is not authenticated for github.com. Skipping default branch update.")
        return

    result = run(
        ["gh", "repo", "edit", slug, "--default-branch", main_branch],
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        print(f"[warn] Failed to set GitHub default branch: {detail}")
        return

    print(f"[ok] Updated GitHub default branch to {main_branch} for {slug}")


def run_interactive_doctor_scan() -> None:
    command_doctor(make_doctor_args())


def run_interactive_doctor_set_identity() -> None:
    ensure_git_repo()
    current_name = get_config("user.name") or ""
    current_email = get_config("user.email") or ""
    name = prompt_text("Git user.name", default=current_name or None, required=True)
    email = prompt_text("Git user.email", default=current_email or None, required=True)
    scope = choose_option(
        "Identity scope",
        ["Global (all repos)", "Local (this repo only)"],
        allow_cancel=False,
    )

    if scope == "Global (all repos)":
        git("config", "--global", "user.name", name)
        git("config", "--global", "user.email", email)
        print(f"[ok] Updated global identity: {name} <{email}>")
    else:
        git("config", "user.name", name)
        git("config", "user.email", email)
        print(f"[ok] Updated local identity: {name} <{email}>")


def run_interactive_doctor_fix() -> None:
    ensure_git_repo()
    history_counts = collect_history_emails()

    configured_email = (get_config("user.email") or "").strip().lower()
    configured_name = (get_config("user.name") or "").strip()
    target_email = prompt_text(
        "Target email for contributions",
        default=configured_email or None,
        required=True,
    ).lower()
    target_name = prompt_text(
        "Target commit name",
        default=configured_name or "Git User",
        required=True,
    )

    old_emails: list[str] = []
    mismatched = sorted([email for email in history_counts if email.lower() != target_email.lower()])
    if mismatched:
        mode = choose_option(
            "Rewrite scope",
            [
                "Rewrite all mismatched emails",
                "Select specific old emails",
            ],
            allow_cancel=False,
        )
        if mode == "Select specific old emails":
            old_emails = choose_multiple_options("Pick old email(s) to rewrite", mismatched)
            if not old_emails:
                raise UserCancelled

    push = prompt_confirm("Push rewritten history right away?", default=False)
    promote_main = prompt_confirm("Also promote a branch to main?", default=False)

    promote_source: str | None = None
    main_branch = pick_main_branch("main")
    remote = "origin"
    set_github_default = True
    if promote_main:
        branches = list_local_branches()
        if not branches:
            raise GitCoachError("No local branches found to promote.")
        promote_source = choose_option("Choose source branch to promote", branches, allow_cancel=False)
        main_branch = prompt_text("Main branch name", default=main_branch, required=True)
        remotes = list_remotes()
        if remotes:
            remote = choose_option("Choose remote", remotes, allow_cancel=False)
        else:
            remote = prompt_text("Remote name", default="origin", required=True)
        if push:
            set_github_default = prompt_confirm(
                "Try setting GitHub default branch via gh CLI?",
                default=True,
            )

    plan_lines = [
        f"target email: {target_email}",
        f"target name:  {target_name}",
    ]
    if old_emails:
        plan_lines.append(f"specific old emails: {', '.join(old_emails)}")
    else:
        plan_lines.append("old emails: auto-select all mismatches")
    plan_lines.append(f"push: {push}")
    plan_lines.append(f"promote main: {promote_main}")
    if promote_main:
        plan_lines.append(f"promote source: {promote_source}")
        plan_lines.append(f"main branch: {main_branch}")
        plan_lines.append(f"remote: {remote}")
        if push:
            plan_lines.append(f"set github default: {set_github_default}")

    print_box("Planned action", plan_lines)

    if not prompt_confirm("Run this now?", default=True):
        raise UserCancelled

    command_doctor(
        make_doctor_args(
            target_email=target_email,
            target_name=target_name,
            old_email=old_emails,
            fix_email_history=True,
            promote_main=promote_main,
            promote_source=promote_source,
            main_branch=main_branch,
            remote=remote,
            set_github_default=set_github_default,
            yes=True,
            push=push,
        )
    )


def run_interactive_promote_main() -> None:
    ensure_git_repo()
    branches = list_local_branches()
    if not branches:
        raise GitCoachError("No local branches found to promote.")

    source = choose_option("Choose branch to promote to main", branches, allow_cancel=False)
    main_branch = prompt_text("Main branch name", default=pick_main_branch("main"), required=True)
    remotes = list_remotes()
    if remotes:
        remote = choose_option("Choose remote", remotes, allow_cancel=False)
    else:
        remote = prompt_text("Remote name", default="origin", required=True)
    push = prompt_confirm("Push promoted branch to remote?", default=True)
    set_default = push and prompt_confirm(
        "Try setting GitHub default branch via gh CLI?",
        default=True,
    )

    plan_lines = [
        f"source: {source}",
        f"main branch: {main_branch}",
        f"remote: {remote}",
        f"push: {push}",
    ]
    if push:
        plan_lines.append(f"set github default: {set_default}")

    print_box("Planned promote action", plan_lines)

    if not prompt_confirm("Run this now?", default=True):
        raise UserCancelled

    command_doctor(
        make_doctor_args(
            promote_main=True,
            promote_source=source,
            main_branch=main_branch,
            remote=remote,
            set_github_default=set_default,
            yes=True,
            push=push,
        )
    )


def run_interactive_start_feature() -> None:
    ensure_git_repo()
    feature_name = prompt_text("Feature name", required=True)
    dev_branch = prompt_text("Dev branch", default="dev", required=True)
    command_start(argparse.Namespace(feature_name=feature_name, dev_branch=dev_branch))


def run_interactive_save_commit() -> None:
    ensure_git_repo()
    message = prompt_text("Commit message", required=True)
    include_untracked = prompt_confirm("Include untracked files?", default=False)
    command_save(argparse.Namespace(message=message, include_untracked=include_untracked))


def run_interactive_ship() -> None:
    ensure_git_repo()
    main_branch = prompt_text("Main branch", default=pick_main_branch("main"), required=True)
    dev_branch = prompt_text("Dev branch", default="dev", required=True)
    push = prompt_confirm("Push to origin after merge?", default=False)
    command_ship(argparse.Namespace(main_branch=main_branch, dev_branch=dev_branch, push=push))


def run_interactive_init() -> None:
    ensure_git_repo()
    main_branch = prompt_text("Main branch", default=pick_main_branch("main"), required=True)
    dev_branch = prompt_text("Dev branch", default="dev", required=True)
    command_init(argparse.Namespace(main_branch=main_branch, dev_branch=dev_branch))


def run_interactive_status_snapshot() -> None:
    ensure_git_repo()
    branch = current_branch()
    staged, unstaged, untracked = status_counts()
    upstream = current_upstream()
    remote_info = "none"
    if upstream:
        ahead, behind = ahead_behind(upstream)
        remote_info = f"{upstream} (ahead {ahead}, behind {behind})"

    lines = [
        f"branch: {branch}",
        f"upstream: {remote_info}",
        f"staged files: {staged}",
        f"unstaged files: {unstaged}",
        f"untracked files: {untracked}",
    ]
    print_box("Status snapshot", lines)


def run_interactive_switch_branch() -> None:
    ensure_git_repo()
    branches = list_local_branches()
    if not branches:
        raise GitCoachError("No local branches found.")

    current = current_branch()
    options = [b for b in branches if b != current] + [current]
    target = choose_option("Choose branch to checkout", options, allow_cancel=False)
    if target == current:
        print(f"[ok] Already on {current}")
        return
    git("checkout", target, capture=False)
    print(f"[ok] Switched to {target}")


def run_interactive_sync_current_branch() -> None:
    ensure_git_repo()
    branch = current_branch()
    upstream = current_upstream()

    if upstream:
        print(f"[info] Fetching and rebasing {branch} against {upstream}")
        remote_name = upstream.split("/", 1)[0]
        git("fetch", remote_name, capture=False)
        git("pull", "--rebase", "--autostash", capture=False)
        print("[ok] Sync complete")
        return

    remotes = list_remotes()
    if not remotes:
        raise GitCoachError("No remotes configured for this repository.")
    remote = choose_option("No upstream set. Choose remote", remotes, allow_cancel=False)
    print(f"[info] Fetching {remote}. Branch has no upstream, so pull is skipped.")
    git("fetch", remote, capture=False)
    print("[ok] Fetch complete")


def run_interactive_push_current_branch() -> None:
    ensure_git_repo()
    branch = current_branch()
    upstream = current_upstream()
    if upstream:
        git("push", capture=False)
        print(f"[ok] Pushed {branch} to {upstream}")
        return

    remotes = list_remotes()
    if not remotes:
        raise GitCoachError("No remotes configured for this repository.")
    remote = choose_option("No upstream set. Choose remote for push", remotes, allow_cancel=False)
    git("push", "-u", remote, branch, capture=False)
    print(f"[ok] Pushed {branch} to {remote}/{branch} and set upstream")


def interactive_context_lines() -> list[str]:
    repo_root = git("rev-parse", "--show-toplevel").stdout.strip()
    branch = current_branch()
    status_lines = git("status", "--porcelain").stdout.splitlines()
    dirty = "dirty" if status_lines else "clean"
    menu_backend = "gum filter" if can_use_gum() else "built-in"
    return [
        f"Repo:   {repo_root}",
        f"Branch: {branch}",
        f"State:  {dirty}",
        f"Menu:   {menu_backend}",
    ]


def command_interactive_doctor_menu() -> None:
    actions = [
        "Scan identity issues",
        "Set git identity (name/email)",
        "Fix email history",
        "Promote branch to main",
        "Back to main menu",
    ]
    dispatch = {
        "Scan identity issues": run_interactive_doctor_scan,
        "Set git identity (name/email)": run_interactive_doctor_set_identity,
        "Fix email history": run_interactive_doctor_fix,
        "Promote branch to main": run_interactive_promote_main,
    }

    while True:
        try:
            picked = choose_option("Doctor actions", actions, allow_cancel=True)
        except UserCancelled:
            return

        if picked == "Back to main menu":
            return

        action = dispatch[picked]
        try:
            action()
        except UserCancelled:
            print("[info] Doctor action cancelled.")
        except GitCoachError as err:
            print(f"[error] {err}")

        if not prompt_confirm("Run another Doctor action?", default=True):
            return


def command_interactive(_args: argparse.Namespace) -> int:
    ensure_git_repo()
    actions = [
        "Doctor",
        "Status snapshot",
        "Switch branch",
        "Sync current branch",
        "Push current branch",
        "Start feature branch",
        "Save commit",
        "Ship dev -> main",
        "Init repo defaults",
        "Exit",
    ]

    dispatch = {
        "Doctor": command_interactive_doctor_menu,
        "Status snapshot": run_interactive_status_snapshot,
        "Switch branch": run_interactive_switch_branch,
        "Sync current branch": run_interactive_sync_current_branch,
        "Push current branch": run_interactive_push_current_branch,
        "Start feature branch": run_interactive_start_feature,
        "Save commit": run_interactive_save_commit,
        "Ship dev -> main": run_interactive_ship,
        "Init repo defaults": run_interactive_init,
    }

    print_box(
        "gitcoach interactive mode",
        interactive_context_lines()
        + ["", "Search, pick, and run without memorizing flags."],
    )
    while True:
        try:
            picked = choose_option("Select an action", actions, allow_cancel=True)
        except UserCancelled:
            return 0

        if picked == "Exit":
            return 0

        action = dispatch[picked]
        try:
            action()
        except UserCancelled:
            print("[info] Action cancelled.")
        except GitCoachError as err:
            print(f"[error] {err}")

        if picked == "Doctor":
            continue

        if not prompt_confirm("Run another action?", default=True):
            return 0


def command_doctor(args: argparse.Namespace) -> int:
    ensure_git_repo()
    configured_name = get_config("user.name")
    configured_email = (args.target_email or get_config("user.email") or "").strip().lower()

    if configured_name:
        print(f"[ok] user.name: {configured_name}")
    else:
        print("[warn] user.name is not set.")

    if configured_email:
        print(f"[ok] target email: {configured_email}")
    else:
        print("[warn] user.email is not set.")

    history_counts = collect_history_emails()
    print_email_table(history_counts)

    if configured_email:
        mismatch = {e: c for e, c in history_counts.items() if e.lower() != configured_email}
        if mismatch:
            total = sum(mismatch.values())
            print(f"[warn] Found {total} commit identity entries not using {configured_email}.")
        else:
            print("[ok] Commit history email already matches configured email.")

    if not args.fix_email_history and not args.promote_main:
        if configured_email and history_counts and any(e.lower() != configured_email for e in history_counts):
            print("\nSuggested fix:")
            print(
                f"  gitcoach doctor --fix-email-history --target-email {configured_email} --yes"
            )
        return 0

    if args.fix_email_history:
        if not configured_email:
            raise GitCoachError("Need target email. Set git user.email or pass --target-email.")

        old_emails = select_emails_to_rewrite(history_counts, configured_email, args.old_email)
        if not old_emails:
            print("[ok] Nothing to rewrite.")
        else:
            ensure_clean_worktree()
            backup_branch = create_backup_branch()
            target_name = args.target_name or configured_name or "Git User"

            if not args.yes:
                prompt_confirm_history_rewrite(configured_email, old_emails, backup_branch)
            else:
                print(f"[warn] Proceeding without prompt (--yes). Backup branch: {backup_branch}")

            if has_filter_repo():
                print("[info] Using git filter-repo for rewrite")
                rewrite_history_filter_repo(old_emails, target_name, configured_email)
            else:
                print("[info] git filter-repo unavailable, falling back to filter-branch")
                rewrite_history_filter_branch(old_emails, target_name, configured_email)

            print("[ok] History rewrite completed.")
            if args.push and not args.promote_main:
                git("push", "--force-with-lease", "--all", capture=False)
                git("push", "--force-with-lease", "--tags", capture=False)
                print("[ok] Force push complete.")

    if args.promote_main:
        source_branch = args.promote_source or current_branch()
        if not branch_exists(source_branch):
            raise GitCoachError(f"Source branch does not exist: {source_branch}")
        archive_preview = make_archive_main_name(args.main_branch) if branch_exists(args.main_branch) else None

        ensure_clean_worktree()
        if not args.yes:
            prompt_confirm_promote_main(
                source_branch=source_branch,
                main_branch=args.main_branch,
                archive_branch=archive_preview,
                remote_name=args.remote,
                will_push=args.push,
            )
        else:
            print(
                f"[warn] Proceeding without prompt (--yes). "
                f"Promote {source_branch} -> {args.main_branch}."
            )

        archive_branch = promote_branch_to_main(
            source_branch=source_branch,
            main_branch=args.main_branch,
            archive_branch_name=archive_preview,
        )
        if args.push:
            maybe_push_promoted_branches(
                remote_name=args.remote,
                main_branch=args.main_branch,
                archive_branch=archive_branch,
            )
            if args.set_github_default:
                maybe_set_github_default_branch(
                    remote_name=args.remote,
                    main_branch=args.main_branch,
                )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitcoach",
        description="Opinionated Git helpers for solo developers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    p_init = subparsers.add_parser("init", help="Bootstrap branches and config.")
    p_init.add_argument("--main-branch", default="main")
    p_init.add_argument("--dev-branch", default="dev")
    p_init.set_defaults(func=command_init)

    p_start = subparsers.add_parser("start", help="Start a feature branch from dev.")
    p_start.add_argument("feature_name")
    p_start.add_argument("--dev-branch", default="dev")
    p_start.set_defaults(func=command_start)

    p_save = subparsers.add_parser("save", help="Commit tracked changes safely.")
    p_save.add_argument("message")
    p_save.add_argument("--include-untracked", action="store_true")
    p_save.set_defaults(func=command_save)

    p_ship = subparsers.add_parser("ship", help="Fast-forward dev into main.")
    p_ship.add_argument("--main-branch", default="main")
    p_ship.add_argument("--dev-branch", default="dev")
    p_ship.add_argument("--push", action="store_true")
    p_ship.set_defaults(func=command_ship)

    p_interactive = subparsers.add_parser(
        "interactive",
        aliases=["menu"],
        help="Searchable menu for common GitCoach workflows.",
    )
    p_interactive.set_defaults(func=command_interactive)

    p_doctor = subparsers.add_parser(
        "doctor",
        help="Diagnose Git identity problems and optionally rewrite email history.",
    )
    p_doctor.add_argument("--target-email", help="Email to enforce across history.")
    p_doctor.add_argument("--target-name", help="Name to enforce when rewriting.")
    p_doctor.add_argument(
        "--old-email",
        action="append",
        default=[],
        help="Email to rewrite. Repeat flag to pass multiple values.",
    )
    p_doctor.add_argument("--fix-email-history", action="store_true")
    p_doctor.add_argument(
        "--promote-main",
        action="store_true",
        help="Promote a rewritten branch to main by archiving old main and renaming source.",
    )
    p_doctor.add_argument(
        "--promote-source",
        help="Branch to promote into main. Defaults to current branch.",
    )
    p_doctor.add_argument(
        "--main-branch",
        default="main",
        help="Main branch name for promote flow.",
    )
    p_doctor.add_argument(
        "--remote",
        default="origin",
        help="Remote name used for push/promote.",
    )
    p_doctor.add_argument(
        "--set-github-default",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="With --promote-main --push, try setting GitHub default branch via gh.",
    )
    p_doctor.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    p_doctor.add_argument(
        "--push",
        action="store_true",
        help="Push rewritten/promoted branches to remote (uses force as needed).",
    )
    p_doctor.set_defaults(func=command_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "command", None) is None:
            return command_interactive(argparse.Namespace())
        return args.func(args)
    except UserCancelled:
        print("[info] Cancelled.")
        return 130
    except GitCoachError as err:
        print(f"[error] {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
