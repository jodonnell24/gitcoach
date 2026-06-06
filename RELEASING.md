# Releasing GitCoach

GitCoach is not currently published to PyPI. The `gitcoach` name on PyPI belongs to an unrelated older project, so tagged releases currently build artifacts and attach them to a GitHub Release only.

## 1. Prepare release

1. Ensure tests pass locally: `python -m pytest`
2. Update version in `pyproject.toml`
3. Update `CHANGELOG.md`
4. Commit changes

## 2. Tag release

```bash
git tag v0.1.0
git push origin v0.1.0
```

## 3. Automated publish

On `v*` tags, GitHub Actions will:

- Build `sdist` and `wheel`
- Create a GitHub Release with generated notes
- Attach the built `sdist` and `wheel` artifacts

## 4. Verify

Test the GitHub install path in a clean environment:

```bash
python -m venv /tmp/gitcoach-release-check
. /tmp/gitcoach-release-check/bin/activate
python -m pip install git+https://github.com/jodonnell24/gitcoach.git
gitcoach --help
```

If a PyPI release becomes a goal later, choose and reserve a package name before adding PyPI publishing back to the release workflow.
