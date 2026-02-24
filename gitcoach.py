#!/usr/bin/env python3
"""gitcoach: opinionated Git helpers for solo developers."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable


class GitCoachError(Exception):
    """Raised for expected gitcoach usage/runtime errors."""


class UserCancelled(Exception):
    """Raised when a user cancels an interactive prompt."""


PROFILE_PRESETS: dict[str, dict[str, bool | str]] = {
    "solo-safe": {
        "guard_commit_main": True,
        "guard_push_main": True,
        "guard_force_push": True,
        "guard_push_dirty": True,
        "commit_untracked_policy": "ask",
    },
    "fast": {
        "guard_commit_main": False,
        "guard_push_main": False,
        "guard_force_push": False,
        "guard_push_dirty": False,
        "commit_untracked_policy": "allow",
    },
    "strict": {
        "guard_commit_main": True,
        "guard_push_main": True,
        "guard_force_push": True,
        "guard_push_dirty": True,
        "commit_untracked_policy": "block",
    },
}

CONFIG_DEFAULTS: dict[str, bool | str] = {
    "main_branch": "main",
    "dev_branch": "dev",
    "save_tracked_only": True,
    "workflow_profile": "solo-safe",
    **PROFILE_PRESETS["solo-safe"],
}

CONFIG_BOOL_KEYS = {
    "save_tracked_only",
    "guard_commit_main",
    "guard_push_main",
    "guard_force_push",
    "guard_push_dirty",
}

CONFIG_STRING_KEYS = {
    "main_branch",
    "dev_branch",
    "workflow_profile",
    "commit_untracked_policy",
}


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


def get_global_config(key: str) -> str | None:
    result = run(["git", "config", "--global", "--get", key], check=False)
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def git_in_repo(
    repo: Path,
    *args: str,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], check=check, capture=capture)


def find_git_repos(root: Path, max_depth: int = 3) -> list[Path]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise GitCoachError(f"Path does not exist or is not a directory: {root}")

    repos: list[Path] = []
    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        if depth > max_depth:
            dirs[:] = []
            continue

        if ".git" in dirs:
            repos.append(current_path)
            dirs[:] = []
            continue

        if current_path == root and current_path.joinpath(".git").exists():
            repos.append(current_path)
            dirs[:] = []

    return sorted(set(repos))


def collect_history_emails_for_repo(repo: Path) -> dict[str, int]:
    result = git_in_repo(repo, "log", "--all", "--format=%ae%n%ce", check=False)
    if result.returncode != 0:
        return {}
    counts: dict[str, int] = {}
    for raw in (result.stdout or "").splitlines():
        email = raw.strip()
        if not email:
            continue
        counts[email] = counts.get(email, 0) + 1
    return counts


def summarize_repo_identity(
    repo: Path,
    target_email: str | None,
) -> tuple[str, str | None, int, int]:
    local_email = (git_in_repo(repo, "config", "--get", "user.email", check=False).stdout or "").strip() or None
    effective_email = local_email or get_global_config("user.email")
    counts = collect_history_emails_for_repo(repo)
    total_entries = sum(counts.values())
    mismatch = 0
    if target_email:
        mismatch = sum(count for email, count in counts.items() if email.lower() != target_email.lower())
    return (repo.name, effective_email, total_entries, mismatch)


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


def has_staged_changes() -> bool:
    for raw in git("status", "--porcelain").stdout.splitlines():
        if raw and not raw.startswith("??") and raw[0] != " ":
            return True
    return False


def has_unstaged_changes() -> bool:
    for raw in git("status", "--porcelain").stdout.splitlines():
        if raw and not raw.startswith("??") and len(raw) > 1 and raw[1] != " ":
            return True
    return False


def has_untracked_files() -> bool:
    return bool(git("ls-files", "--others", "--exclude-standard").stdout.strip())


def worktree_dirty() -> bool:
    return bool(git("status", "--porcelain", check=False).stdout.strip())


def has_parent_commit() -> bool:
    result = git("rev-parse", "--verify", "HEAD~1", check=False)
    return result.returncode == 0


def changed_tracked_files() -> list[str]:
    files: set[str] = set()
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
        result = git(*args, check=False)
        for line in (result.stdout or "").splitlines():
            name = line.strip()
            if name:
                files.add(name)
    return sorted(files)


def recent_commit_choices(limit: int = 30) -> list[tuple[str, str]]:
    result = git("log", f"-n{limit}", "--pretty=format:%h%x09%s", check=False)
    choices: list[tuple[str, str]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t", 1)
        if not parts or not parts[0].strip():
            continue
        sha = parts[0].strip()
        summary = parts[1].strip() if len(parts) > 1 else ""
        choices.append((sha, f"{sha}  {summary}".strip()))
    return choices


def create_safety_stash(label: str) -> str | None:
    result = git("stash", "push", "-u", "-m", label, check=False)
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if "No local changes to save" in output:
        return None
    ref = git("stash", "list", "-n", "1", "--format=%gd", check=False).stdout.strip()
    return ref or "stash@{0}"


def unique_branch_name(base: str) -> str:
    if not branch_exists(base):
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if not branch_exists(candidate):
            return candidate
    return f"{base}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"


def suggest_feature_branch_from_message(message: str) -> str:
    subject = message.strip().splitlines()[0].strip() if message.strip() else "work"
    summary = subject.split(": ", 1)[1] if ": " in subject else subject
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", summary.strip().lower()).strip("-")
    if not slug:
        slug = "work"
    return f"feature/{slug[:48]}"


def checkout_branch_with_changes(branch: str, *, autostash: bool = True) -> bool:
    """Checkout existing branch or create it while carrying local changes.

    Returns True when a temporary stash was used.
    """
    if not branch_exists(branch):
        git("checkout", "-b", branch, capture=False)
        return False

    attempt = run(["git", "checkout", branch], check=False, capture=False)
    if attempt.returncode == 0:
        return False

    if not autostash:
        raise GitCoachError(
            f"Could not switch to {branch} with local changes. "
            "Retry with --autostash."
        )

    stash_ref = create_safety_stash(f"gitcoach-branch-switch-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    git("checkout", branch, capture=False)
    if stash_ref:
        pop = run(["git", "stash", "pop"], check=False, capture=False)
        if pop.returncode != 0:
            print("[warn] Stash pop had conflicts. Resolve and continue.")
        else:
            print("[ok] Restored local changes after branch switch.")
    return True


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


def parse_config_bool(raw: str, default: bool) -> bool:
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_commit_untracked_policy(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"allow", "off", "false", "no"}:
        return "allow"
    if lowered in {"block", "deny", "strict", "true", "on"}:
        return "block"
    return "ask"


def repo_file_path(name: str) -> Path:
    top = run(["git", "rev-parse", "--show-toplevel"], check=False)
    root = (top.stdout or "").strip()
    if top.returncode == 0 and root:
        return Path(root) / name
    return Path(name)


def load_gitcoach_config() -> dict[str, bool | str]:
    path = repo_file_path(".gitcoach.yml")
    parsed: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip().strip("'").strip('"')

    profile = str(parsed.get("workflow_profile", CONFIG_DEFAULTS["workflow_profile"])).strip().lower()
    if profile not in PROFILE_PRESETS:
        profile = str(CONFIG_DEFAULTS["workflow_profile"])

    config: dict[str, bool | str] = dict(CONFIG_DEFAULTS)
    config.update(PROFILE_PRESETS[profile])
    config["workflow_profile"] = profile

    for key in CONFIG_STRING_KEYS:
        if key in parsed and parsed[key]:
            config[key] = parsed[key]
    for key in CONFIG_BOOL_KEYS:
        if key in parsed:
            config[key] = parse_config_bool(parsed[key], bool(config[key]))

    policy_raw = str(parsed.get("commit_untracked_policy", config["commit_untracked_policy"]))
    config["commit_untracked_policy"] = normalize_commit_untracked_policy(policy_raw)
    return config


def save_gitcoach_config(config: dict[str, bool | str]) -> None:
    normalized = dict(CONFIG_DEFAULTS)
    profile = str(config.get("workflow_profile", normalized["workflow_profile"])).strip().lower()
    if profile not in PROFILE_PRESETS:
        profile = str(CONFIG_DEFAULTS["workflow_profile"])
    normalized.update(PROFILE_PRESETS[profile])
    normalized["workflow_profile"] = profile

    for key in CONFIG_STRING_KEYS:
        value = config.get(key)
        if value not in {None, ""}:
            normalized[key] = str(value)
    for key in CONFIG_BOOL_KEYS:
        value = config.get(key)
        if isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, str):
            normalized[key] = parse_config_bool(value, bool(normalized[key]))

    normalized["commit_untracked_policy"] = normalize_commit_untracked_policy(
        str(config.get("commit_untracked_policy", normalized["commit_untracked_policy"]))
    )

    lines = [
        f"main_branch: {normalized['main_branch']}",
        f"dev_branch: {normalized['dev_branch']}",
        f"save_tracked_only: {str(normalized['save_tracked_only']).lower()}",
        f"workflow_profile: {normalized['workflow_profile']}",
        f"guard_commit_main: {str(normalized['guard_commit_main']).lower()}",
        f"guard_push_main: {str(normalized['guard_push_main']).lower()}",
        f"guard_force_push: {str(normalized['guard_force_push']).lower()}",
        f"guard_push_dirty: {str(normalized['guard_push_dirty']).lower()}",
        f"commit_untracked_policy: {normalized['commit_untracked_policy']}",
    ]
    repo_file_path(".gitcoach.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_profile(profile: str) -> dict[str, bool | str]:
    profile_name = profile.strip().lower()
    if profile_name not in PROFILE_PRESETS:
        supported = ", ".join(sorted(PROFILE_PRESETS))
        raise GitCoachError(f"Unknown profile: {profile}. Choose one of: {supported}")

    config = load_gitcoach_config()
    config["workflow_profile"] = profile_name
    config.update(PROFILE_PRESETS[profile_name])
    save_gitcoach_config(config)
    return config


def git_dir_path() -> Path:
    return Path(git("rev-parse", "--git-dir").stdout.strip())


def actions_log_path() -> Path:
    return git_dir_path() / ".gitcoach-actions.jsonl"


def log_action(action: str, details: dict[str, object] | None = None) -> None:
    try:
        entry: dict[str, object] = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": action,
            "branch": (git("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout or "").strip() or "(unknown)",
        }
        if details:
            clean_details: dict[str, object] = {}
            for key, value in details.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    clean_details[key] = value
                elif isinstance(value, (list, tuple, set)):
                    clean_details[key] = list(value)
                else:
                    clean_details[key] = str(value)
            entry["details"] = clean_details

        path = actions_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        # Logging should never block the main action.
        return


def read_recent_actions(limit: int = 20) -> list[dict[str, object]]:
    path = actions_log_path()
    if not path.exists():
        return []

    entries: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries[-limit:]


def install_safety_hooks(*, force: bool = False) -> tuple[list[str], list[str]]:
    config = load_gitcoach_config()
    block_commit_main = bool(config["guard_commit_main"])
    block_push_main = bool(config["guard_push_main"])
    block_force_push = bool(config["guard_force_push"])
    block_push_dirty = bool(config["guard_push_dirty"])
    block_commit_untracked = str(config["commit_untracked_policy"]) == "block"

    hook_dir = Path(git("rev-parse", "--git-path", "hooks").stdout.strip())
    hook_dir.mkdir(parents=True, exist_ok=True)

    pre_commit = hook_dir / "pre-commit"
    pre_push = hook_dir / "pre-push"
    marker = "gitcoach guard hook"

    pre_commit_body = f"""#!/bin/sh
# gitcoach guard hook
BLOCK_COMMIT_MAIN="{1 if block_commit_main else 0}"
BLOCK_COMMIT_UNTRACKED="{1 if block_commit_untracked else 0}"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BLOCK_COMMIT_MAIN" = "1" ]; then
  if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    if [ -z "${{GITCOACH_ALLOW_MAIN_COMMIT:-}}" ]; then
      echo "[gitcoach] Commit blocked on $branch."
      echo "[gitcoach] Use a feature branch, or bypass once with GITCOACH_ALLOW_MAIN_COMMIT=1."
      exit 1
    fi
  fi
fi

if [ "$BLOCK_COMMIT_UNTRACKED" = "1" ]; then
  if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    if [ -z "${{GITCOACH_ALLOW_UNTRACKED_COMMIT:-}}" ]; then
      echo "[gitcoach] Commit blocked: untracked files detected."
      echo "[gitcoach] Add/ignore those files, or bypass once with GITCOACH_ALLOW_UNTRACKED_COMMIT=1."
      exit 1
    fi
  fi
fi
exit 0
"""

    pre_push_body = f"""#!/bin/sh
# gitcoach guard hook
BLOCK_PUSH_MAIN="{1 if block_push_main else 0}"
BLOCK_FORCE_PUSH="{1 if block_force_push else 0}"
BLOCK_PUSH_DIRTY="{1 if block_push_dirty else 0}"
zero="0000000000000000000000000000000000000000"

if [ "$BLOCK_PUSH_DIRTY" = "1" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    if [ -z "${{GITCOACH_ALLOW_DIRTY_PUSH:-}}" ]; then
      echo "[gitcoach] Push blocked: working tree has local changes."
      echo "[gitcoach] Commit/stash first, or bypass once with GITCOACH_ALLOW_DIRTY_PUSH=1."
      exit 1
    fi
  fi
fi

while read local_ref local_sha remote_ref remote_sha
do
  case "$remote_ref" in
    refs/heads/main|refs/heads/master)
      if [ "$BLOCK_PUSH_MAIN" = "1" ]; then
        if [ -z "${{GITCOACH_ALLOW_MAIN_PUSH:-}}" ]; then
          echo "[gitcoach] Push blocked to ${{remote_ref#refs/heads/}}."
          echo "[gitcoach] Push from dev/feature branches and merge intentionally."
          echo "[gitcoach] Bypass once with GITCOACH_ALLOW_MAIN_PUSH=1."
          exit 1
        fi
      fi
      ;;
  esac

  if [ "$local_sha" = "$zero" ]; then
    continue
  fi
  if [ "$remote_sha" = "$zero" ]; then
    continue
  fi

  if ! git merge-base --is-ancestor "$remote_sha" "$local_sha" >/dev/null 2>&1; then
    if [ "$BLOCK_FORCE_PUSH" = "1" ]; then
      if [ -z "${{GITCOACH_ALLOW_FORCE_PUSH:-}}" ]; then
        echo "[gitcoach] Non-fast-forward push blocked on ${{remote_ref#refs/heads/}}."
        echo "[gitcoach] Bypass once with GITCOACH_ALLOW_FORCE_PUSH=1."
        exit 1
      fi
    fi
  fi
done
exit 0
"""

    installed: list[str] = []
    skipped: list[str] = []

    for hook_path, body in ((pre_commit, pre_commit_body), (pre_push, pre_push_body)):
        if hook_path.exists():
            existing = hook_path.read_text(encoding="utf-8", errors="ignore")
            if marker not in existing and not force:
                skipped.append(hook_path.name)
                continue
        hook_path.write_text(body, encoding="utf-8")
        hook_path.chmod(0o755)
        installed.append(hook_path.name)

    return installed, skipped


def slugify_feature_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise GitCoachError("Feature name must contain letters or numbers.")
    return slug


def write_default_config(main_branch: str, dev_branch: str) -> None:
    path = repo_file_path(".gitcoach.yml")
    if path.exists():
        return
    config = dict(CONFIG_DEFAULTS)
    config["main_branch"] = main_branch
    config["dev_branch"] = dev_branch
    save_gitcoach_config(config)


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
        "all_repos": None,
        "max_depth": 3,
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
    config = load_gitcoach_config()
    config["main_branch"] = main_branch
    config["dev_branch"] = dev_branch
    save_gitcoach_config(config)
    print(f"[ok] Wrote .gitcoach.yml (main={main_branch}, dev={dev_branch})")
    print(f"[ok] Workflow profile: {config['workflow_profile']}")

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

    if args.install_guards:
        installed, skipped = install_safety_hooks(force=False)
        if installed:
            print(f"[ok] Installed safety hooks: {', '.join(installed)}")
        if skipped:
            print(f"[warn] Skipped existing non-gitcoach hooks: {', '.join(skipped)}")
            print("       Re-run with: gitcoach guard --force")
        log_action(
            "guard.install",
            {
                "force": False,
                "installed": installed,
                "skipped": skipped,
                "profile": config["workflow_profile"],
            },
        )

    log_action(
        "repo.init",
        {
            "main_branch": main_branch,
            "dev_branch": dev_branch,
            "install_guards": args.install_guards,
            "profile": config["workflow_profile"],
        },
    )

    return 0


def command_guard(args: argparse.Namespace) -> int:
    ensure_git_repo()
    config = load_gitcoach_config()
    installed, skipped = install_safety_hooks(force=args.force)
    if installed:
        print(f"[ok] Installed/updated safety hooks: {', '.join(installed)}")
    if skipped:
        print(f"[warn] Existing non-gitcoach hooks preserved: {', '.join(skipped)}")
        print("       Re-run with --force to overwrite them.")
    if not installed and not skipped:
        print("[info] No hook changes made.")
    print(f"[info] Active profile: {config['workflow_profile']}")
    log_action(
        "guard.install",
        {
            "force": args.force,
            "installed": installed,
            "skipped": skipped,
            "profile": config["workflow_profile"],
        },
    )
    return 0


def profile_summary_lines(config: dict[str, bool | str]) -> list[str]:
    return [
        f"profile: {config['workflow_profile']}",
        f"guard commit on main: {'on' if config['guard_commit_main'] else 'off'}",
        f"guard push to main: {'on' if config['guard_push_main'] else 'off'}",
        f"guard force push: {'on' if config['guard_force_push'] else 'off'}",
        f"guard dirty push: {'on' if config['guard_push_dirty'] else 'off'}",
        f"commit untracked policy: {config['commit_untracked_policy']}",
    ]


def command_profile(args: argparse.Namespace) -> int:
    ensure_git_repo()

    if args.set:
        config = apply_profile(args.set)
        print(f"[ok] Set workflow profile: {config['workflow_profile']}")
        print_box("Profile settings", profile_summary_lines(config))
        if args.install_guards:
            installed, skipped = install_safety_hooks(force=False)
            if installed:
                print(f"[ok] Installed/updated safety hooks: {', '.join(installed)}")
            if skipped:
                print(f"[warn] Existing non-gitcoach hooks preserved: {', '.join(skipped)}")
                print("       Re-run `gitcoach guard --force` if you want to replace them.")
        log_action(
            "profile.set",
            {
                "profile": config["workflow_profile"],
                "install_guards": args.install_guards,
            },
        )
        return 0

    config = load_gitcoach_config()
    print_box("Workflow profile", profile_summary_lines(config))
    return 0


def format_action_details(details: object) -> str:
    if not isinstance(details, dict):
        return ""
    pieces: list[str] = []
    for key in sorted(details):
        value = details[key]
        if isinstance(value, list):
            rendered = ",".join(str(item) for item in value)
        else:
            rendered = str(value)
        if len(rendered) > 80:
            rendered = rendered[:77] + "..."
        pieces.append(f"{key}={rendered}")
    return "; ".join(pieces)


def command_actions(args: argparse.Namespace) -> int:
    ensure_git_repo()
    limit = max(1, args.limit)
    entries = read_recent_actions(limit=limit)
    if not entries:
        print("[info] No gitcoach actions logged yet.")
        return 0

    print(f"Recent actions (latest {len(entries)}):")
    for entry in reversed(entries):
        timestamp = str(entry.get("timestamp", "?"))
        action = str(entry.get("action", "unknown"))
        branch = str(entry.get("branch", "?"))
        details = format_action_details(entry.get("details"))
        line = f"- {timestamp} | {action} | branch={branch}"
        if details:
            line += f" | {details}"
        print(line)
    return 0


def list_untracked_files() -> list[str]:
    result = git("ls-files", "--others", "--exclude-standard", check=False)
    return sorted([line.strip() for line in (result.stdout or "").splitlines() if line.strip()])


def suggest_ignore_patterns(untracked_files: list[str]) -> list[str]:
    if not untracked_files:
        return []

    safe_top_dirs = {
        "dist",
        "build",
        "coverage",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".DS_Store",
    }
    safe_exts = {".log", ".tmp", ".cache", ".bak", ".swp", ".pid", ".out", ".pyc"}
    keep_dirs = {"src", "app", "lib", "docs", "test", "tests"}

    top_counts: dict[str, int] = {}
    ext_counts: dict[str, int] = {}
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(pattern: str) -> None:
        value = pattern.strip()
        if not value or value in seen:
            return
        seen.add(value)
        suggestions.append(value)

    for path in untracked_files:
        parts = path.split("/")
        top = parts[0]
        top_counts[top] = top_counts.get(top, 0) + 1
        base = parts[-1]

        if base == ".DS_Store":
            add(".DS_Store")
        if base.startswith(".env"):
            add(".env*")

        suffix = Path(base).suffix.lower()
        if suffix:
            ext_counts[suffix] = ext_counts.get(suffix, 0) + 1

    for top, count in sorted(top_counts.items(), key=lambda item: (-item[1], item[0])):
        if top in safe_top_dirs:
            add(f"{top}/")
            continue
        if count >= 8 and top not in keep_dirs:
            add(f"{top}/")

    for suffix, count in sorted(ext_counts.items(), key=lambda item: (-item[1], item[0])):
        if suffix in safe_exts and count >= 2:
            add(f"*{suffix}")

    return suggestions


def apply_ignore_patterns(patterns: list[str]) -> list[str]:
    path = repo_file_path(".gitignore")
    current = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    existing = {
        line.strip()
        for line in current.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    additions: list[str] = []
    for raw in patterns:
        pattern = raw.strip()
        if not pattern or pattern in existing or pattern in additions:
            continue
        additions.append(pattern)

    if not additions:
        return []

    with path.open("a", encoding="utf-8") as handle:
        if current and not current.endswith("\n"):
            handle.write("\n")
        for pattern in additions:
            handle.write(pattern + "\n")
    return additions


def command_ignore(args: argparse.Namespace) -> int:
    ensure_git_repo()
    untracked_files = list_untracked_files()
    if not untracked_files:
        print("[ok] No untracked files found.")
        return 0

    suggestions = suggest_ignore_patterns(untracked_files)
    preview = [f"untracked files: {len(untracked_files)}"] + [f"- {item}" for item in untracked_files[:10]]
    if len(untracked_files) > 10:
        preview.append(f"... {len(untracked_files) - 10} more")
    print_box("Untracked snapshot", preview)

    candidate_patterns = [pattern.strip() for pattern in (args.pattern or []) if pattern.strip()]
    if not candidate_patterns:
        candidate_patterns = suggestions

    if not args.apply:
        if suggestions:
            print("Suggested .gitignore patterns:")
            for pattern in suggestions:
                print(f"  - {pattern}")
            print("\nApply all suggestions:")
            print("  gitcoach ignore --apply")
            print("Apply specific pattern(s):")
            print("  gitcoach ignore --apply --pattern dist/ --pattern '*.log'")
        else:
            print("[info] No obvious ignore suggestions yet. Pass custom patterns with --pattern.")
        return 0

    if not candidate_patterns:
        raise GitCoachError("No patterns to apply. Pass --pattern or run without --apply to inspect suggestions.")

    if not args.yes:
        print("Patterns to add to .gitignore:")
        for pattern in candidate_patterns:
            print(f"  - {pattern}")
        if not prompt_confirm("Apply these patterns?", default=True):
            raise UserCancelled

    added = apply_ignore_patterns(candidate_patterns)
    if added:
        print("[ok] Added patterns to .gitignore:")
        for pattern in added:
            print(f"  - {pattern}")
    else:
        print("[info] No changes made; patterns were already present.")

    log_action(
        "ignore.apply",
        {
            "requested": candidate_patterns,
            "added": added,
            "untracked_count": len(untracked_files),
        },
    )
    return 0


def command_start(args: argparse.Namespace) -> int:
    ensure_git_repo()
    feature_branch = f"feature/{slugify_feature_name(args.feature_name)}"
    dev_branch = args.dev_branch
    dirty = worktree_dirty()

    if dirty:
        current = current_branch()
        print(
            f"[info] Detected local changes on {current}. "
            "Carrying them to feature branch."
        )
        created = not branch_exists(feature_branch)
        used_stash = checkout_branch_with_changes(feature_branch, autostash=args.autostash)
        if created:
            print(f"[ok] Created and switched to: {feature_branch}")
        else:
            print(f"[ok] Switched to existing branch: {feature_branch}")
        if used_stash:
            print("[ok] Used temporary stash to preserve changes during switch.")
        log_action(
            "start.feature_branch",
            {
                "feature_branch": feature_branch,
                "dev_branch": dev_branch,
                "dirty_switch": True,
                "used_stash": used_stash,
            },
        )
        return 0

    if not branch_exists(dev_branch):
        raise GitCoachError(f"Missing {dev_branch} branch. Run: gitcoach init")

    git("checkout", dev_branch, capture=False)
    if branch_exists(feature_branch):
        git("checkout", feature_branch, capture=False)
        print(f"[ok] Switched to existing branch: {feature_branch}")
    else:
        git("checkout", "-b", feature_branch, capture=False)
        print(f"[ok] Created and switched to: {feature_branch}")
    log_action(
        "start.feature_branch",
        {
            "feature_branch": feature_branch,
            "dev_branch": dev_branch,
            "dirty_switch": False,
        },
    )
    return 0


def print_untracked_preview() -> None:
    entries = list_untracked_files()
    if not entries:
        return
    print("[warn] Untracked files were not staged:")
    for item in entries[:15]:
        print(f"       - {item}")
    if len(entries) > 15:
        print(f"       ... {len(entries) - 15} more")
    print("       Use --include-untracked to stage everything.")


def enforce_untracked_commit_policy(*, include_untracked: bool) -> None:
    if include_untracked:
        return

    untracked_entries = list_untracked_files()
    if not untracked_entries:
        return

    config = load_gitcoach_config()
    policy = str(config["commit_untracked_policy"])
    print_untracked_preview()

    if policy == "allow":
        return
    if policy == "block":
        raise GitCoachError(
            "Untracked files detected and policy is `block`. "
            "Ignore them, or re-run with --include-untracked."
        )

    if not is_interactive_tty():
        return

    if prompt_confirm("Continue with tracked files only?", default=True):
        return
    if prompt_confirm("Stage untracked files too and include them?", default=False):
        git("add", "-A", capture=False)
        return
    raise UserCancelled


def staged_files() -> list[str]:
    result = git("diff", "--cached", "--name-only", check=False)
    return sorted([line.strip() for line in (result.stdout or "").splitlines() if line.strip()])


def infer_commit_type(files: list[str]) -> str:
    if not files:
        return "chore"

    def file_kind(path: str) -> str:
        p = path.lower()
        parts = p.split("/")
        name = parts[-1]
        if p.startswith("docs/") or name.endswith((".md", ".rst", ".txt")):
            return "docs"
        if "test" in parts or name.endswith(("_test.py", ".test.js", ".spec.ts", ".spec.js")):
            return "test"
        if p.startswith(".github/") or "workflow" in parts:
            return "ci"
        if name in {"package-lock.json", "poetry.lock", "yarn.lock", "pnpm-lock.yaml", "cargo.lock"}:
            return "chore"
        return "code"

    kinds = {file_kind(path) for path in files}
    if kinds == {"docs"}:
        return "docs"
    if kinds == {"test"}:
        return "test"
    if kinds == {"ci"}:
        return "ci"
    if kinds == {"chore"}:
        return "chore"
    return "feat"


def infer_commit_scope(files: list[str]) -> str | None:
    tops = {path.split("/", 1)[0] for path in files if "/" in path}
    if len(tops) == 1:
        scope = next(iter(tops))
        if scope not in {".github"}:
            return scope
    return None


def infer_commit_subject(files: list[str]) -> str:
    commit_type = infer_commit_type(files)
    scope = infer_commit_scope(files)

    if len(files) == 1:
        target = files[0]
    elif scope:
        target = f"{scope} files"
    else:
        target = "project files"

    verbs = {
        "feat": "update",
        "fix": "fix",
        "docs": "document",
        "test": "add tests for",
        "chore": "update",
        "ci": "adjust",
    }
    verb = verbs.get(commit_type, "update")
    prefix = f"{commit_type}({scope})" if scope else commit_type
    return f"{prefix}: {verb} {target}"


def commit_message_warnings(message: str) -> list[str]:
    warnings: list[str] = []
    subject = message.strip().splitlines()[0].strip() if message.strip() else ""
    if not subject:
        return ["Subject line is empty."]
    if len(subject) < 12:
        warnings.append("Subject is very short. Add more context.")
    if len(subject) > 72:
        warnings.append("Subject is longer than 72 characters.")
    if subject.endswith("."):
        warnings.append("Subject ends with a period; style is usually without trailing punctuation.")
    if re.search(r"\b(wip|temp|misc|stuff|quick fix)\b", subject, re.IGNORECASE):
        warnings.append("Subject uses vague wording (wip/temp/misc/stuff).")
    if not re.match(r"^[a-z]+(\([^)]+\))?: .+", subject):
        warnings.append("Subject does not follow `type(scope): summary` pattern.")
    return warnings


def write_commit_with_message(message: str) -> None:
    git_dir = Path(git("rev-parse", "--git-dir").stdout.strip())
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="gitcoach-msg-",
        suffix=".txt",
        dir=str(git_dir),
        delete=False,
    ) as handle:
        handle.write(message.rstrip() + "\n")
        msg_path = handle.name

    try:
        git("commit", "-F", msg_path)
    finally:
        Path(msg_path).unlink(missing_ok=True)


def compose_commit_message_guided(files: list[str]) -> str:
    default_type = infer_commit_type(files)
    default_scope = infer_commit_scope(files)
    default_subject = infer_commit_subject(files)

    preview_files = files[:8]
    preview_lines = [f"staged files: {len(files)}"] + [f"- {item}" for item in preview_files]
    if len(files) > 8:
        preview_lines.append(f"... {len(files) - 8} more")
    print_box("Commit message helper", preview_lines)

    type_options = [
        "feat - behavior or feature change",
        "fix - bug fix",
        "refactor - internal code cleanup",
        "docs - docs/content updates",
        "test - tests only",
        "chore - maintenance and tooling",
        "ci - pipeline/workflow change",
    ]
    picked_type = choose_option("Choose commit type", type_options, allow_cancel=False).split(" - ", 1)[0]
    scope = prompt_text("Scope (optional)", default=default_scope or None).strip()

    suggested_summary = default_subject.split(": ", 1)[1] if ": " in default_subject else default_subject
    summary = prompt_text("Summary (imperative, short)", default=suggested_summary, required=True).strip()

    subject = f"{picked_type}({scope}): {summary}" if scope else f"{picked_type}: {summary}"

    body_raw = prompt_text("Optional details (use ';' for bullet splits)").strip()
    message = subject
    if body_raw:
        parts = [item.strip() for item in body_raw.split(";") if item.strip()]
        if len(parts) > 1:
            message += "\n\n" + "\n".join(f"- {part}" for part in parts)
        else:
            message += "\n\n" + parts[0]

    warnings = commit_message_warnings(message)
    preview = message.splitlines() or [message]
    if warnings:
        preview += ["", "Warnings:"] + [f"- {item}" for item in warnings]
    print_box("Proposed commit message", preview)
    if not prompt_confirm("Use this message?", default=True):
        raise UserCancelled
    return message


def choose_commit_message(
    *,
    explicit_message: str | None,
    guided: bool,
    files: list[str],
) -> str:
    message = (explicit_message or "").strip()
    if guided or not message:
        if not is_interactive_tty():
            raise GitCoachError("No commit message provided. Use --guided in an interactive terminal or pass a message.")
        return compose_commit_message_guided(files)
    return message


def print_commit_message_hints(files: list[str]) -> None:
    suggestion = infer_commit_subject(files)
    print("[info] Suggested subject format:")
    print(f"       {suggestion}")
    print("       Example with details:")
    print(f"       {suggestion}\n")
    print("       - explain why")
    print("       - note risk/test impact")


def command_message(args: argparse.Namespace) -> int:
    ensure_git_repo()
    files = staged_files()
    if not files:
        files = changed_tracked_files()
    if not files:
        raise GitCoachError("No changed files found. Stage or modify files first.")

    if args.guided:
        if not is_interactive_tty():
            raise GitCoachError("--guided requires an interactive terminal.")
        message = compose_commit_message_guided(files)
        print("\n" + message)
        return 0

    print_commit_message_hints(files)
    return 0


def handle_main_commit_block(
    message: str,
    err: GitCoachError,
) -> bool:
    branch = current_branch()
    if branch not in {"main", "master"}:
        return False

    detail = str(err).lower()
    if "commit blocked" not in detail and "pre-commit hook" not in detail and "main" not in detail:
        return False

    suggested = unique_branch_name(suggest_feature_branch_from_message(message))
    if is_interactive_tty():
        print(
            f"[warn] Commit on {branch} was blocked by safety guard."
        )
        if not prompt_confirm(
            f"Create {suggested} and commit there instead?",
            default=True,
        ):
            return False
    else:
        print(
            f"[warn] Commit on {branch} was blocked. "
            f"Auto-creating {suggested} and retrying commit."
        )

    checkout_branch_with_changes(suggested, autostash=True)
    write_commit_with_message(message)
    print(f"[ok] Commit created on {suggested}")
    log_action(
        "save.auto_branch_commit",
        {
            "from_branch": branch,
            "to_branch": suggested,
            "subject": message.splitlines()[0].strip() if message.strip() else "",
        },
    )
    return True


def command_save(args: argparse.Namespace) -> int:
    ensure_git_repo()

    if args.include_untracked:
        git("add", "-A", capture=False)
    else:
        git("add", "-u", capture=False)
    enforce_untracked_commit_policy(include_untracked=args.include_untracked)

    files = staged_files()
    if not files:
        raise GitCoachError("No staged tracked changes to commit.")

    message = choose_commit_message(
        explicit_message=args.message,
        guided=args.guided,
        files=files,
    )
    warnings = commit_message_warnings(message)
    if warnings:
        print("[warn] Commit message quality hints:")
        for item in warnings:
            print(f"       - {item}")
        if args.strict_message:
            raise GitCoachError("Commit message did not pass --strict-message checks.")
        if is_interactive_tty() and not prompt_confirm("Commit anyway?", default=True):
            raise UserCancelled

    try:
        write_commit_with_message(message)
    except GitCoachError as err:
        if handle_main_commit_block(message, err):
            return 0
        raise
    print("[ok] Commit created")
    log_action(
        "save.commit",
        {
            "subject": message.splitlines()[0].strip() if message.strip() else "",
            "guided": args.guided,
            "include_untracked": args.include_untracked,
        },
    )
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
    log_action(
        "ship.dev_to_main",
        {
            "dev_branch": dev_branch,
            "main_branch": main_branch,
            "push": args.push,
        },
    )
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


def create_backup_branch(prefix: str = "backup/email-rewrite") -> str:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{prefix}-{timestamp}"
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


def doctor_scan_all_repos(root: Path, max_depth: int, target_email: str | None) -> int:
    repos = find_git_repos(root, max_depth=max_depth)
    if not repos:
        print(f"[warn] No Git repositories found under {root}")
        return 0

    print(
        f"[info] Scanning {len(repos)} repo(s) under {root} "
        f"(depth={max_depth}, target={target_email or 'none'})"
    )
    print_rule()

    ok_count = 0
    warn_count = 0
    missing_email = 0
    no_commits = 0

    for repo in repos:
        name, effective_email, total_entries, mismatch = summarize_repo_identity(repo, target_email)
        email_display = effective_email or "(unset)"
        if effective_email is None:
            missing_email += 1

        if total_entries == 0:
            no_commits += 1
            status = "[info]"
            detail = "no commits"
        elif target_email and mismatch > 0:
            warn_count += 1
            status = "[warn]"
            detail = f"{mismatch}/{total_entries} identity entries differ from target"
        else:
            ok_count += 1
            status = "[ok]"
            detail = f"{total_entries} identity entries checked"

        print(f"{status} {name}: {detail}")
        print(f"       path: {repo}")
        print(f"       email: {email_display}")

    print_rule()
    print(
        f"[info] Summary: ok={ok_count}, warn={warn_count}, "
        f"missing-email={missing_email}, no-commits={no_commits}"
    )
    if warn_count > 0 and target_email:
        print("[info] Use per-repo doctor fix where needed:")
        print("       cd <repo> && gitcoach doctor --fix-email-history --target-email <email> --yes")

    return 0


def run_interactive_doctor_scan() -> None:
    command_doctor(make_doctor_args())


def run_interactive_doctor_scan_folder() -> None:
    ensure_git_repo()
    repo_root = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    default_root = str(repo_root.parent)
    configured_email = get_config("user.email") or get_global_config("user.email") or ""

    root_text = prompt_text("Folder to scan for repos", default=default_root, required=True)
    depth_text = prompt_text("Max folder depth", default="3", required=True)
    target = prompt_text("Target email (blank to only inventory)", default=configured_email or None).strip().lower()

    try:
        max_depth = int(depth_text)
    except ValueError as err:
        raise GitCoachError("Max folder depth must be an integer.") from err
    if max_depth < 0:
        raise GitCoachError("Max folder depth must be >= 0.")

    doctor_scan_all_repos(Path(root_text).expanduser(), max_depth=max_depth, target_email=target or None)


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
    autostash = prompt_confirm("Auto-stash if branch switch needs it?", default=True)
    command_start(
        argparse.Namespace(
            feature_name=feature_name,
            dev_branch=dev_branch,
            autostash=autostash,
        )
    )


def run_interactive_save_commit() -> None:
    ensure_git_repo()
    include_untracked = prompt_confirm("Include untracked files?", default=False)
    guided = prompt_confirm("Use commit message helper?", default=True)
    message = None
    if not guided:
        message = prompt_text("Commit message", required=True)
    command_save(
        argparse.Namespace(
            message=message,
            include_untracked=include_untracked,
            guided=guided,
            strict_message=False,
        )
    )


def run_interactive_draft_commit_message() -> None:
    ensure_git_repo()
    command_message(argparse.Namespace(guided=True))


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
    install_guards = prompt_confirm("Install safety guard hooks?", default=True)
    command_init(
        argparse.Namespace(
            main_branch=main_branch,
            dev_branch=dev_branch,
            install_guards=install_guards,
        )
    )


def run_interactive_install_safety_guards() -> None:
    ensure_git_repo()
    force = prompt_confirm("Overwrite existing non-gitcoach hooks?", default=False)
    command_guard(argparse.Namespace(force=force))


def run_interactive_profile_menu() -> None:
    ensure_git_repo()
    actions = [
        "Show current profile",
        "Set profile: solo-safe",
        "Set profile: fast",
        "Set profile: strict",
        "Back to main menu",
    ]
    while True:
        config = load_gitcoach_config()
        print_box("Workflow profile", profile_summary_lines(config))
        try:
            picked = choose_option("Profile actions", actions, allow_cancel=True)
        except UserCancelled:
            return
        if picked == "Back to main menu":
            return
        if picked == "Show current profile":
            continue

        profile_name = picked.split(":", 1)[1].strip()
        install_guards = prompt_confirm("Reinstall safety hooks now?", default=True)
        command_profile(
            argparse.Namespace(
                set=profile_name,
                install_guards=install_guards,
            )
        )
        if not prompt_confirm("Adjust profile again?", default=True):
            return


def run_interactive_actions_log() -> None:
    ensure_git_repo()
    limit_text = prompt_text("How many recent actions", default="20", required=True)
    try:
        limit = int(limit_text)
    except ValueError as err:
        raise GitCoachError("Limit must be an integer.") from err
    command_actions(argparse.Namespace(limit=max(1, limit)))


def run_interactive_ignore_helper() -> None:
    ensure_git_repo()
    untracked_files = list_untracked_files()
    if not untracked_files:
        print("[ok] No untracked files found.")
        return

    suggestions = suggest_ignore_patterns(untracked_files)
    actions = [
        "Preview untracked + suggestions",
        "Apply all suggested patterns",
        "Pick suggested patterns to apply",
        "Add one custom ignore pattern",
        "Back to main menu",
    ]

    while True:
        try:
            picked = choose_option(".gitignore helper", actions, allow_cancel=True)
        except UserCancelled:
            return
        if picked == "Back to main menu":
            return
        if picked == "Preview untracked + suggestions":
            command_ignore(argparse.Namespace(apply=False, pattern=[], yes=False))
            continue
        if picked == "Apply all suggested patterns":
            command_ignore(argparse.Namespace(apply=True, pattern=[], yes=False))
            return
        if picked == "Pick suggested patterns to apply":
            if not suggestions:
                print("[info] No suggested patterns available right now.")
                continue
            selected = choose_multiple_options("Pick ignore pattern(s)", suggestions)
            if not selected:
                print("[info] No patterns selected.")
                continue
            command_ignore(argparse.Namespace(apply=True, pattern=selected, yes=False))
            return
        if picked == "Add one custom ignore pattern":
            custom = prompt_text("Pattern to add (example: dist/ or *.log)", required=True).strip()
            command_ignore(argparse.Namespace(apply=True, pattern=[custom], yes=False))
            return


def undo_unstage_all() -> None:
    if not has_staged_changes():
        print("[info] No staged changes to unstage.")
        return
    git("restore", "--staged", ".", capture=False)
    print("[ok] Unstaged all staged files.")


def undo_discard_unstaged() -> None:
    if not has_unstaged_changes():
        print("[info] No unstaged tracked changes to discard.")
        return

    stash_ref = None
    if prompt_confirm("Create safety stash before discard?", default=True):
        stash_ref = create_safety_stash(f"gitcoach-undo-discard-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    git("restore", ".", capture=False)
    if stash_ref:
        print(f"[ok] Discarded unstaged changes (safety stash: {stash_ref})")
    else:
        print("[ok] Discarded unstaged changes.")
    log_action(
        "undo.discard_unstaged",
        {
            "stash_ref": stash_ref,
        },
    )


def undo_last_commit(*, keep_staged: bool) -> None:
    if not repo_has_commits() or not has_parent_commit():
        raise GitCoachError("Need at least two commits to undo the last commit safely.")

    backup = create_backup_branch("backup/undo-reset")
    if keep_staged:
        git("reset", "--soft", "HEAD~1", capture=False)
        print(f"[ok] Undid last commit and kept changes staged. Backup: {backup}")
    else:
        git("reset", "HEAD~1", capture=False)
        print(f"[ok] Undid last commit and left changes unstaged. Backup: {backup}")
    log_action(
        "undo.last_commit",
        {
            "keep_staged": keep_staged,
            "backup_branch": backup,
        },
    )


def undo_revert_commit() -> None:
    commits = recent_commit_choices(limit=30)
    if not commits:
        raise GitCoachError("No commits found to revert.")

    picked = choose_option(
        "Choose commit to revert",
        [label for _sha, label in commits],
        allow_cancel=False,
    )
    lookup = {label: sha for sha, label in commits}
    sha = lookup[picked]
    git("revert", "--no-edit", sha, capture=False)
    print(f"[ok] Reverted commit {sha}.")
    log_action("undo.revert_commit", {"sha": sha})


def undo_restore_file_to_head() -> None:
    files = changed_tracked_files()
    if not files:
        print("[info] No tracked file changes to restore.")
        return
    file_path = choose_option("Choose file to restore from HEAD", files, allow_cancel=False)
    stash_ref = None
    if prompt_confirm("Create safety stash before restore?", default=True):
        stash_ref = create_safety_stash(f"gitcoach-undo-file-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    git("restore", "--source=HEAD", "--staged", "--worktree", "--", file_path, capture=False)
    if stash_ref:
        print(f"[ok] Restored {file_path} to HEAD (safety stash: {stash_ref})")
    else:
        print(f"[ok] Restored {file_path} to HEAD.")
    log_action(
        "undo.restore_file",
        {
            "file": file_path,
            "stash_ref": stash_ref,
        },
    )


def command_interactive_undo_menu() -> None:
    actions = [
        "Unstage all staged files",
        "Discard unstaged tracked changes",
        "Undo last commit (keep changes staged)",
        "Undo last commit (keep changes unstaged)",
        "Revert a commit (safe history)",
        "Restore one file to HEAD",
        "Back to main menu",
    ]
    dispatch = {
        "Unstage all staged files": undo_unstage_all,
        "Discard unstaged tracked changes": undo_discard_unstaged,
        "Undo last commit (keep changes staged)": lambda: undo_last_commit(keep_staged=True),
        "Undo last commit (keep changes unstaged)": lambda: undo_last_commit(keep_staged=False),
        "Revert a commit (safe history)": undo_revert_commit,
        "Restore one file to HEAD": undo_restore_file_to_head,
    }

    while True:
        try:
            picked = choose_option("Undo actions", actions, allow_cancel=True)
        except UserCancelled:
            return

        if picked == "Back to main menu":
            return

        if picked in {
            "Discard unstaged tracked changes",
            "Undo last commit (keep changes staged)",
            "Undo last commit (keep changes unstaged)",
            "Restore one file to HEAD",
        }:
            if not prompt_confirm("This changes local history/worktree. Continue?", default=False):
                continue

        action = dispatch[picked]
        try:
            action()
        except UserCancelled:
            print("[info] Undo action cancelled.")
        except GitCoachError as err:
            print(f"[error] {err}")

        if not prompt_confirm("Run another Undo action?", default=True):
            return


def command_undo(_args: argparse.Namespace) -> int:
    ensure_git_repo()
    command_interactive_undo_menu()
    return 0


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


def run_interactive_publish_after_sync() -> None:
    ensure_git_repo()
    branch = current_branch()
    lines = [
        "When: you want your local commits on GitHub.",
        "Step 1 (sync): bring in remote changes first so push is less likely to fail.",
        "Step 2 (push): publish your local commits.",
        f"Current branch: {branch}",
    ]
    print_box("Publish safely (sync + push)", lines)
    if not prompt_confirm("Run sync, then push?", default=True):
        raise UserCancelled

    run_interactive_sync_current_branch()
    run_interactive_push_current_branch()


def run_interactive_sync_vs_push_explainer() -> None:
    lines = [
        "Sync = get remote commits into your local branch (fetch + rebase/pull).",
        "Use sync when: you were away, changed machines, or branch may be behind.",
        "Push = send your local commits to remote (GitHub).",
        "Use push when: your local commits are ready to share.",
        "Safe default when unsure: sync first, then push.",
    ]
    print_box("Sync vs Push", lines)


def run_interactive_quick_guide() -> None:
    lines = [
        "1) Keep main stable. Start work on feature branches.",
        "2) Commit small, clear changes. Save often.",
        "3) Sync = pull remote updates into your local branch.",
        "4) Push = publish your local commits to GitHub.",
        "5) Safe default: sync first, then push.",
        "6) Merge feature/dev back into main intentionally.",
        "7) Use Doctor for identity checks and contribution fixes.",
        "8) Use Undo menu for safe rollbacks instead of panic commands.",
    ]
    print_box("Simple Git workflow (solo/noob friendly)", lines)


def interactive_context_lines() -> list[str]:
    repo_root = git("rev-parse", "--show-toplevel").stdout.strip()
    branch = current_branch()
    profile = str(load_gitcoach_config()["workflow_profile"])
    upstream = current_upstream()
    remote_state = "no upstream"
    if upstream:
        ahead, behind = ahead_behind(upstream)
        remote_state = f"{upstream} (ahead {ahead}, behind {behind})"
    status_lines = git("status", "--porcelain").stdout.splitlines()
    dirty = "dirty" if status_lines else "clean"
    menu_backend = "gum filter" if can_use_gum() else "built-in"
    return [
        f"Repo:   {repo_root}",
        f"Branch: {branch}",
        f"Remote: {remote_state}",
        f"Profile: {profile}",
        f"State:  {dirty}",
        f"Menu:   {menu_backend}",
    ]


def command_interactive_doctor_menu() -> None:
    actions = [
        "Scan identity issues",
        "Scan folder for identity issues",
        "Set git identity (name/email)",
        "Fix email history",
        "Promote branch to main",
        "Back to main menu",
    ]
    dispatch = {
        "Scan identity issues": run_interactive_doctor_scan,
        "Scan folder for identity issues": run_interactive_doctor_scan_folder,
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


def command_interactive_more_menu() -> None:
    actions = [
        "Switch branch directly",
        "Sync current branch (no push)",
        "Push current branch only",
        "Draft commit message",
        "Ship dev -> main",
        "Doctor tools",
        "Undo / rollback tools",
        "Workflow profile settings",
        "Install safety guards",
        "Init repo defaults",
        "Recent actions log",
        "Quick guide",
        "Back to main menu",
    ]

    dispatch = {
        "Switch branch directly": run_interactive_switch_branch,
        "Sync current branch (no push)": run_interactive_sync_current_branch,
        "Push current branch only": run_interactive_push_current_branch,
        "Draft commit message": run_interactive_draft_commit_message,
        "Ship dev -> main": run_interactive_ship,
        "Doctor tools": command_interactive_doctor_menu,
        "Undo / rollback tools": command_interactive_undo_menu,
        "Workflow profile settings": run_interactive_profile_menu,
        "Install safety guards": run_interactive_install_safety_guards,
        "Init repo defaults": run_interactive_init,
        "Recent actions log": run_interactive_actions_log,
        "Quick guide": run_interactive_quick_guide,
    }

    while True:
        try:
            picked = choose_option("More options", actions, allow_cancel=True)
        except UserCancelled:
            return

        if picked == "Back to main menu":
            return

        action = dispatch[picked]
        try:
            action()
        except UserCancelled:
            print("[info] Action cancelled.")
        except GitCoachError as err:
            print(f"[error] {err}")

        if picked in {"Doctor tools", "Undo / rollback tools", "Workflow profile settings"}:
            continue

        if not prompt_confirm("Run another advanced action?", default=True):
            return


def goal_help_lines(goal: str) -> list[str] | None:
    hints = {
        "Start new work on a branch": [
            "Use when: you're starting a feature or fix.",
            "Why: keeps main clean and avoids branch/switch confusion.",
        ],
        "Save my current changes (commit)": [
            "Use when: you want a safe checkpoint in Git.",
            "Why: creates a commit and can guide commit message quality.",
        ],
        "Share my work to GitHub (sync + push)": [
            "Use when: your local commits are ready to publish.",
            "Why: sync first reduces push conflicts, then push uploads commits.",
        ],
        "Get latest remote updates (sync only)": [
            "Use when: your branch may be behind remote.",
            "Why: updates local branch without publishing anything.",
        ],
        "Undo / recover something": [
            "Use when: you staged/committed/restored the wrong thing.",
            "Why: guided rollback options are safer than ad-hoc reset commands.",
        ],
        "Fix identity / contribution issues": [
            "Use when: GitHub contributions are missing or email is wrong.",
            "Why: Doctor scans identity and can rewrite old commit emails.",
        ],
        "See repo status right now": [
            "Use when: you are unsure what state your branch is in.",
            "Why: shows staged/unstaged/untracked + ahead/behind in one snapshot.",
        ],
        "Handle untracked files (.gitignore)": [
            "Use when: random files keep showing up in status.",
            "Why: suggests/apply .gitignore patterns from untracked files.",
        ],
        "Adjust safety settings": [
            "Use when: you want stricter or faster Git behavior.",
            "Why: switch workflow profiles (solo-safe/fast/strict) cleanly.",
        ],
        "Learn sync vs push": [
            "Use when: you're unsure if you need sync, push, or both.",
            "Why: simple explanation with a safe default workflow.",
        ],
    }
    return hints.get(goal)


def command_interactive(_args: argparse.Namespace) -> int:
    ensure_git_repo()
    actions = [
        "Start new work on a branch",
        "Save my current changes (commit)",
        "Share my work to GitHub (sync + push)",
        "Get latest remote updates (sync only)",
        "Undo / recover something",
        "Fix identity / contribution issues",
        "See repo status right now",
        "Handle untracked files (.gitignore)",
        "Adjust safety settings",
        "Learn sync vs push",
        "More options",
        "Exit",
    ]

    dispatch = {
        "Start new work on a branch": run_interactive_start_feature,
        "Save my current changes (commit)": run_interactive_save_commit,
        "Share my work to GitHub (sync + push)": run_interactive_publish_after_sync,
        "Get latest remote updates (sync only)": run_interactive_sync_current_branch,
        "Undo / recover something": command_interactive_undo_menu,
        "Fix identity / contribution issues": command_interactive_doctor_menu,
        "See repo status right now": run_interactive_status_snapshot,
        "Handle untracked files (.gitignore)": run_interactive_ignore_helper,
        "Adjust safety settings": run_interactive_profile_menu,
        "Learn sync vs push": run_interactive_sync_vs_push_explainer,
        "More options": command_interactive_more_menu,
    }

    print_box(
        "What would you like to do?",
        interactive_context_lines()
        + ["", "Pick a goal first. GitCoach will handle the Git steps."],
    )
    while True:
        try:
            picked = choose_option("Choose your goal", actions, allow_cancel=True)
        except UserCancelled:
            return 0

        if picked == "Exit":
            return 0

        hint_lines = goal_help_lines(picked)
        if hint_lines:
            print_box("Why this action", hint_lines)

        action = dispatch[picked]
        try:
            action()
        except UserCancelled:
            print("[info] Action cancelled.")
        except GitCoachError as err:
            print(f"[error] {err}")

        if picked in {
            "Undo / recover something",
            "Fix identity / contribution issues",
            "Adjust safety settings",
            "More options",
        }:
            continue

        if not prompt_confirm("Do you want to do another task?", default=True):
            return 0


def command_doctor(args: argparse.Namespace) -> int:
    if args.all_repos:
        if args.fix_email_history or args.promote_main:
            raise GitCoachError("--all-repos is scan-only. Run per-repo doctor for rewrite/promote actions.")
        if args.max_depth < 0:
            raise GitCoachError("--max-depth must be >= 0.")
        target = (args.target_email or get_global_config("user.email") or "").strip().lower()
        rc = doctor_scan_all_repos(
            Path(args.all_repos).expanduser(),
            max_depth=args.max_depth,
            target_email=target or None,
        )
        log_action(
            "doctor.scan_all_repos",
            {
                "root": str(Path(args.all_repos).expanduser()),
                "max_depth": args.max_depth,
                "target_email": target or None,
            },
        )
        return rc

    ensure_git_repo()
    configured_name = get_config("user.name")
    configured_email = (args.target_email or get_config("user.email") or get_global_config("user.email") or "").strip().lower()

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
            log_action(
                "doctor.rewrite_email_history",
                {
                    "target_email": configured_email,
                    "target_name": target_name,
                    "old_emails": old_emails,
                    "backup_branch": backup_branch,
                    "push": args.push and not args.promote_main,
                },
            )

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
        log_action(
            "doctor.promote_main",
            {
                "source_branch": source_branch,
                "main_branch": args.main_branch,
                "archive_branch": archive_branch,
                "remote": args.remote,
                "push": args.push,
                "set_github_default": args.set_github_default and args.push,
            },
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
    p_init.add_argument(
        "--install-guards",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Install safety pre-commit/pre-push hooks.",
    )
    p_init.set_defaults(func=command_init)

    p_guard = subparsers.add_parser("guard", help="Install or refresh gitcoach safety hooks.")
    p_guard.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-gitcoach hooks.",
    )
    p_guard.set_defaults(func=command_guard)

    p_profile = subparsers.add_parser(
        "profile",
        help="Show or switch workflow profile presets (solo-safe, fast, strict).",
    )
    p_profile.add_argument(
        "--set",
        choices=sorted(PROFILE_PRESETS),
        help="Apply this profile preset and save it to .gitcoach.yml.",
    )
    p_profile.add_argument(
        "--install-guards",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="With --set, reinstall hooks so guard behavior matches the profile.",
    )
    p_profile.set_defaults(func=command_profile)

    p_actions = subparsers.add_parser(
        "actions",
        help="Show recent gitcoach actions (rewrites, undo, guard/profile changes).",
    )
    p_actions.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many recent entries to show.",
    )
    p_actions.set_defaults(func=command_actions)

    p_ignore = subparsers.add_parser(
        "ignore",
        help="Suggest or apply .gitignore patterns from current untracked files.",
    )
    p_ignore.add_argument(
        "--apply",
        action="store_true",
        help="Apply patterns to .gitignore.",
    )
    p_ignore.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="Pattern to apply. Repeat for multiple patterns.",
    )
    p_ignore.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation when applying patterns.",
    )
    p_ignore.set_defaults(func=command_ignore)

    p_start = subparsers.add_parser("start", help="Start a feature branch from dev.")
    p_start.add_argument("feature_name")
    p_start.add_argument("--dev-branch", default="dev")
    p_start.add_argument(
        "--autostash",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When local changes block switching, stash/pop automatically.",
    )
    p_start.set_defaults(func=command_start)

    p_save = subparsers.add_parser("save", help="Commit tracked changes safely.")
    p_save.add_argument("message", nargs="?")
    p_save.add_argument("--include-untracked", action="store_true")
    p_save.add_argument(
        "--guided",
        action="store_true",
        help="Use guided commit message helper.",
    )
    p_save.add_argument(
        "--strict-message",
        action="store_true",
        help="Fail commit when message quality warnings are detected.",
    )
    p_save.set_defaults(func=command_save)

    p_message = subparsers.add_parser(
        "message",
        help="Draft helpful commit message suggestions from changed files.",
    )
    p_message.add_argument(
        "--guided",
        action="store_true",
        help="Open interactive message composer and print resulting message.",
    )
    p_message.set_defaults(func=command_message)

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

    p_undo = subparsers.add_parser(
        "undo",
        aliases=["rollback"],
        help="Open interactive undo/rollback actions.",
    )
    p_undo.set_defaults(func=command_undo)

    p_doctor = subparsers.add_parser(
        "doctor",
        help="Diagnose Git identity problems and optionally rewrite email history.",
    )
    p_doctor.add_argument("--target-email", help="Email to enforce across history.")
    p_doctor.add_argument(
        "--all-repos",
        help="Scan all Git repos under this folder for identity issues (scan-only mode).",
    )
    p_doctor.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Maximum folder depth for --all-repos scans.",
    )
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
