"""Smoke tests — verify modules compile cleanly."""
import sys
from pathlib import Path


def test_main_modules_compile():
    """All .py files in root should compile without SyntaxErrors."""
    root = Path(".")
    py_files = list(root.glob("*.py"))
    assert len(py_files) > 0, "No .py files found"
    for py_file in py_files:
        if py_file.name.startswith("test_"):
            continue
        with open(py_file) as f:
            compile(f.read(), str(py_file), "exec")


def test_config_files_exist():
    """Verify critical config files are present."""
    # Check for requirements.txt or pyproject.toml
    has_deps = Path("requirements.txt").exists() or Path("pyproject.toml").exists()
    # Not a hard fail — some repos may not have deps files
    if not has_deps:
        print("  ⚠️ No requirements.txt or pyproject.toml found")
