# Contributing

Thanks for helping improve GitCoach.

## Local Setup

```bash
python3 -m pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run CLI locally:

```bash
gitcoach --help
gitcoach interactive
```

## Contribution Rules

- Keep UX beginner-first and low-cognitive-load.
- Prefer guardrails over raw power for destructive operations.
- For new risky operations, include rollback guidance and backups.
- Add or update tests for safety-critical behavior changes.

## PR Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] README reflects user-facing behavior changes
- [ ] Safety-critical changes include test coverage
