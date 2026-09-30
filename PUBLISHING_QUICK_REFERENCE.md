# MTPX Publishing Quick Reference

## 🚀 First Time Publishing (DONE ✅)

```bash
pip install build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

**Live at:** https://pypi.org/project/mtpx/

---

## 🔄 Publishing Updates (Future Releases)

### Quick Commands

```bash
# 1. Update version in pyproject.toml (e.g., 0.1.0 → 0.1.1)
# 2. Update CHANGELOG.md with changes
# 3. Update __version__ in src/mtp/__init__.py
# 4. Run these commands:

rm -rf dist/ build/ src/*.egg-info
python -m build
python -m twine check dist/*
python -m twine upload dist/*

# 5. Tag and push
git add .
git commit -m "Release v0.1.1"
git tag v0.1.1
git push origin main --tags
```

---

## 📋 Version Numbering Guide

**Format:** MAJOR.MINOR.PATCH (e.g., 1.2.3)

- **PATCH** (0.1.0 → 0.1.1): Bug fixes, no breaking changes
- **MINOR** (0.1.0 → 0.2.0): New features, backward compatible
- **MAJOR** (0.1.0 → 1.0.0): Breaking changes

---

## 📝 Files to Update for Each Release

1. **pyproject.toml** - Update `version = "0.1.1"`
2. **src/mtp/__init__.py** - Update `__version__ = "0.1.1"`
3. **CHANGELOG.md** - Add new version section with changes
4. **Git tag** - Create tag matching version: `v0.1.1`

---

## 🔑 API Token Management

**Create Token:**
- Go to: https://pypi.org/manage/account/token/
- Scope: "Entire account" (for first upload) or "Project: mtpx"
- Save token securely

**Save Token (Optional):**
Create `~/.pypirc`:
```ini
[pypi]
username = __token__
password = pypi-AgEIcHlwaS5vcmc...
```

---

## ✅ Pre-Release Checklist

- [ ] All tests passing
- [ ] Version bumped in pyproject.toml
- [ ] Version bumped in src/mtp/__init__.py
- [ ] CHANGELOG.md updated
- [ ] README.md updated (if needed)
- [ ] Changes committed to git
- [ ] Clean build: `rm -rf dist/ build/ src/*.egg-info`
- [ ] Build successful: `python -m build`
- [ ] Validation passed: `python -m twine check dist/*`

---

## 🐛 Troubleshooting

**"File already exists"**
→ Version already published. Bump version number.

**"403 Forbidden"**
→ Check API token. Create new one if expired.

**"Invalid distribution"**
→ Clean old builds: `rm -rf dist/ build/`

**Import errors after install**
→ Check package structure in `src/mtp/`

---

## 📊 Package Stats

- **Package Name:** mtpx
- **Current Version:** 0.1.0
- **PyPI URL:** https://pypi.org/project/mtpx/
- **Install Command:** `pip install mtpx`
- **Python Support:** 3.10+

---

## 🎯 Common Tasks

**Check current PyPI version:**
```bash
pip index versions mtpx
```

**Test installation in clean environment:**
```bash
python -m venv test_env
test_env\Scripts\activate
pip install mtpx
python -c "import mtp; print(mtp.__version__)"
deactivate
```

**View package info:**
```bash
pip show mtpx
```

**Uninstall:**
```bash
pip uninstall mtpx
```
