# gitcoach

Small, opinionated Git helper for solo developers.

## Why

`gitcoach` focuses on common pain points:

- Keeping `dev` and `main` separated without extra ceremony
- Avoiding accidental commits of untracked files
- Shipping safely with fast-forward merges
- Diagnosing and fixing GitHub contributions issues caused by wrong commit email

## Commands

Run via:

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

- `Doctor` submenu (scan, set identity, fix email history, promote to main, back)
- `Status snapshot`
- `Switch branch`
- `Sync current branch`
- `Push current branch`
- Existing start/save/ship/init flows

Core commands:

```bash
python3 gitcoach.py init
python3 gitcoach.py start "my feature"
python3 gitcoach.py save "commit message"
python3 gitcoach.py ship --push
python3 gitcoach.py doctor
python3 gitcoach.py interactive
```

## Contribution Email Fix

The `doctor` command can rewrite old commits to a correct email so GitHub can attribute contributions.

Preview problems:

```bash
python3 gitcoach.py doctor
```

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

## Safety Notes

- Rewriting history changes commit hashes.
- `gitcoach` creates a backup branch before rewriting.
- Team repos are sensitive: everyone must rebase/reclone after rewritten history.
- GitHub contributions also require the target email to be added and verified on your GitHub account.
