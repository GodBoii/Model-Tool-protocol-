# Publishing MTPX

MTPX is published as the `mtpx` Python distribution. Packaging uses the
`setuptools.build_meta` backend configured in `pyproject.toml`; packages are
discovered beneath `src`, and the `mtp` console command is installed from
`mtp.cli.main:main`.

There is currently no automated publishing workflow in `.github/workflows`.
Publishing is therefore a maintainer-only manual operation. Do not upload from
an unreviewed or dirty checkout.

## 1. Prepare the release

1. Update the version in both `pyproject.toml` and `src/mtp/__init__.py`. The
   values must match; `tests/test_docs_consistency.py` enforces this invariant.
2. Update user-facing documentation and release notes for behavior changes.
3. Confirm the branch is current, reviewed, and has no unintended local files.
4. Run the release gates:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[all]"
python -m pip install pytest pytest-asyncio build twine
python -m pytest -q -m "not live"
python -m compileall -q src tests
```

Run any applicable live-provider smoke tests separately as described in
[Testing](TESTING.md). A live failure caused by credentials, quota, or a remote
service must be investigated rather than silently treated as a unit-test pass.

## 2. Build and inspect artifacts

Remove old `dist` artifacts, then build from the repository root. In
PowerShell:

```powershell
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
python -m build
python -m twine check dist/*
```

In a POSIX shell:

```bash
rm -rf dist
python -m build
python -m twine check dist/*
```

The build should produce one source distribution and one wheel. Inspect their
contents before upload:

```bash
python -m zipfile -l dist/*.whl
python -m tarfile -l dist/*.tar.gz
```

Verify the wheel contains the `mtp` package, CLI styles/templates, license, and
package metadata, and does not contain credentials, caches, tests generated at
runtime, or unrelated workspace files.

## 3. Test the built wheel

Installation from the wheel catches packaging omissions that an editable
install can hide. Create a clean environment and install the exact artifact:

```bash
python -m venv .release-venv
```

Activate it, then run:

```bash
python -m pip install --upgrade pip
python -m pip install dist/*.whl
mtp --version
mtp --help
mtp doctor
python -c "import mtp; print(mtp.__version__)"
```

Confirm both reported versions match the release version. Exercise one minimal
SDK import and any provider/CLI paths materially changed by the release.

## 4. Upload

TestPyPI is recommended for validating a new packaging configuration:

```bash
python -m twine upload --repository testpypi dist/*
```

Install that published version in another clean environment before uploading
to PyPI. TestPyPI may not mirror every runtime dependency, so dependencies can
be installed from PyPI when necessary.

For the production release:

```bash
python -m twine upload dist/*
```

Use a scoped PyPI API token supplied through Twine's prompt or secure CI/OS
credential storage. Never place a token in the repository, a committed config
file, or a shell command that will be retained in history. PyPI releases are
immutable; if an artifact is wrong, increment the version and publish a new
release rather than trying to overwrite it.

## 5. Tag and verify

After PyPI accepts the artifacts, create a signed or annotated tag matching the
version and push it:

```bash
git tag -a vX.Y.Z -m "MTPX X.Y.Z"
git push origin vX.Y.Z
```

Finally, install `mtpx==X.Y.Z` from PyPI in a clean environment and repeat the
version, help, doctor, import, and focused smoke checks. Publish release notes
that summarize user-visible changes, compatibility considerations, and any
migration steps.
