# Releasing GitCoach

## 1. Prepare release

1. Ensure tests pass locally: `pytest`
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
- Publish to PyPI (`pypa/gh-action-pypi-publish`)
- Create a GitHub release with generated notes

## 4. Verify

1. Confirm package on PyPI
2. Test install in clean env:

```bash
pipx install gitcoach
gitcoach --help
```
