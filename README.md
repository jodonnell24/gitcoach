# gitcoach

Small, opinionated Git helper for solo developers.

![GitCoach mascot](gitcoach-mascot.svg)

## Why

`gitcoach` focuses on common pain points:

- Keeping `dev` and `main` separated without extra ceremony
- Avoiding accidental commits of untracked files
- Switching between safety profiles (`solo-safe`, `fast`, `strict`) without editing hooks manually
- Safe undo/rollback actions without scary git commands
- Shipping safely with fast-forward merges
- Diagnosing and fixing GitHub contributions issues caused by wrong commit email

## Commands

Run via:

```bash
gitcoach <command> [flags]
```

During development you can still run:

```bash
python3 gitcoach.py <command> [flags]
```

No-flag interactive menu (search + select):

```bash
python3 gitcoach.py
# or
python3 gitcoach.py interactive
```

If [`gum`](https://github.com/charmbracelet/gum) is installed, the menu automatically uses `gum filter` for fuzzy selection, and falls back to the built-in menu if gum errors.

To force built-in menus even when gum is installed:

```bash
GITCOACH_NO_GUM=1 python3 gitcoach.py
```

Interactive menu now includes:

- Goal-first prompts (`What would you like GitCoach to help with?`) so you can pick intent before Git terminology.
- Plain-language paths for:
  - start new work
  - save changes
  - share work safely (`sync + push`)
  - get latest remote updates (`sync only`)
  - undo/recover
  - fix identity/contributions
  - handle untracked files
  - adjust safety settings
- A built-in workflow-basics helper and a smaller `More options` menu for advanced actions.
- Interactive errors now suggest a next fix inside GitCoach instead of only showing raw failure text.
- Friendly helper tone is used in interactive mode, while command-mode output stays mostly formal.

Core commands:

```bash
python3 gitcoach.py init                  # installs safety guards by default
python3 gitcoach.py guard                 # install/refresh safety hooks
python3 gitcoach.py profile --set strict  # switch guard behavior preset
python3 gitcoach.py actions               # recent destructive/safety actions
python3 gitcoach.py ignore                # suggest .gitignore patterns
python3 gitcoach.py ignore --apply        # apply suggested patterns
python3 gitcoach.py start "my feature"    # carries dirty changes to new branch
python3 gitcoach.py message               # suggest a good commit message
python3 gitcoach.py save --guided         # commit with guided message helper
python3 gitcoach.py save "commit message"
python3 gitcoach.py undo                  # open undo/rollback actions
python3 gitcoach.py ship --push
python3 gitcoach.py doctor
python3 gitcoach.py interactive
```

## Installation

From PyPI:

```bash
pipx install gitcoach
```

From local checkout:

```bash
pipx install .
```

## Commit Message Help

Draft suggestions:

```bash
python3 gitcoach.py message
```

Interactive guided writer:

```bash
python3 gitcoach.py message --guided
python3 gitcoach.py save --guided
```

Optional strict check:

```bash
python3 gitcoach.py save --guided --strict-message
```

## 60-Second Quickstart

Install with pipx:

```bash
pipx install gitcoach
```

Try a safe beginner flow:

```bash
gitcoach init
gitcoach start "my first change"
gitcoach save --guided
gitcoach ship
```

## Workflow Profiles

Profiles are saved in `.gitcoach.yml` and control guard behavior:

- `solo-safe`: main/push/force/dirty guards on, untracked commit policy = `ask`
- `fast`: minimal guard friction
- `strict`: all guards on, untracked commit policy = `block`

Use:

```bash
python3 gitcoach.py profile
python3 gitcoach.py profile --set solo-safe
python3 gitcoach.py profile --set fast
python3 gitcoach.py profile --set strict
```

## Safety Guards

`gitcoach guard` installs:

- `pre-commit`: blocks commits directly on `main`/`master` unless explicitly bypassed
- `pre-push`: blocks pushes to `main`/`master`, dirty-tree pushes, and non-fast-forward pushes (profile-controlled)

Bypass env vars (one-off, advanced users):

```bash
GITCOACH_ALLOW_MAIN_COMMIT=1 git commit -m "..."
GITCOACH_ALLOW_MAIN_PUSH=1 git push origin main
GITCOACH_ALLOW_FORCE_PUSH=1 git push --force-with-lease
GITCOACH_ALLOW_DIRTY_PUSH=1 git push
GITCOACH_ALLOW_UNTRACKED_COMMIT=1 git commit -m "..."
```

If you already have custom hooks and want to overwrite them:

```bash
python3 gitcoach.py guard --force
```

Noob-friendly behavior when guards are enabled:

- If `save` is blocked on `main`, `gitcoach` offers to auto-create a feature branch and retries commit there.
- `start` works even with dirty changes; it carries your work into the feature branch automatically.

## .gitignore Helper

Inspect untracked files and get suggested ignore patterns:

```bash
python3 gitcoach.py ignore
```

Apply all suggestions:

```bash
python3 gitcoach.py ignore --apply
```

Apply explicit patterns:

```bash
python3 gitcoach.py ignore --apply --pattern dist/ --pattern '*.log'
```

## Recent Actions Log

GitCoach records safety-critical actions (history rewrite, promote-main, undo resets, profile/guard changes, ignore apply) in:

`<repo>/.git/.gitcoach-actions.jsonl`

View recent entries:

```bash
python3 gitcoach.py actions
python3 gitcoach.py actions --limit 50
```

## Contribution Email Fix

The `doctor` command can rewrite old commits to a correct email so GitHub can attribute contributions.

Preview problems:

```bash
python3 gitcoach.py doctor
```

Scan many repos at once (scan-only):

```bash
python3 gitcoach.py doctor --all-repos ~/projects --target-email you@example.com
```

`--all-repos` is scan-only by design; run per-repo `doctor --fix-email-history` when you decide to rewrite.

Rewrite history to configured `user.email`:

```bash
python3 gitcoach.py doctor --fix-email-history --yes
```

Rewrite to a specific email:

```bash
python3 gitcoach.py doctor --fix-email-history --target-email you@example.com --yes
```

Only rewrite selected old emails:

```bash
python3 gitcoach.py doctor \
  --fix-email-history \
  --target-email you@example.com \
  --old-email old1@example.com \
  --old-email old2@example.com \
  --yes
```

Auto force-push right after rewrite:

```bash
python3 gitcoach.py doctor --fix-email-history --target-email you@example.com --yes --push
```

Promote rewritten branch to `main`, archive old `main`, push, and update GitHub default branch:

```bash
python3 gitcoach.py doctor \
  --fix-email-history \
  --target-email you@example.com \
  --promote-main \
  --promote-source email-fix \
  --push \
  --yes
```

By default, with `--promote-main --push`, `gitcoach` also tries to set the GitHub default branch using `gh`. Disable that with `--no-set-github-default`.

If you only need the branch promotion step:

```bash
python3 gitcoach.py doctor --promote-main --promote-source email-fix --push --yes
```

## Undo / Rollback

Open guided rollback actions:

```bash
python3 gitcoach.py undo
```

Includes:

- Unstage all files
- Discard unstaged tracked changes (optional safety stash)
- Undo last commit (keep staged or unstaged changes)
- Revert a chosen commit
- Restore one file back to `HEAD`

## Safety Notes

- Rewriting history changes commit hashes.
- `gitcoach` creates a backup branch before rewriting.
- Team repos are sensitive: everyone must rebase/reclone after rewritten history.
- GitHub contributions also require the target email to be added and verified on your GitHub account.
