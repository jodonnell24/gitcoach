#!/usr/bin/env python3
"""gitcoach: opinionated Git helpers for solo developers."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class GitCoachError(Exception):
    """Raised for expected gitcoach usage/runtime errors."""


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
    typed = input("Type REWRITE to continue: ").strip()
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
    typed = input("Type PROMOTE to continue: ").strip()
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
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        return args.func(args)
    except GitCoachError as err:
        print(f"[error] {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
