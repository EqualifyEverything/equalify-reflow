#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Lint modified Python files and report issues to Claude.

This hook runs when Claude finishes a response (Stop event).
It detects modified Python files via git and runs ruff + mypy on them.
"""

import os
import subprocess
import sys


def get_modified_files() -> list[str]:
    """Get list of modified Python files from git."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")

    try:
        # Get staged and unstaged changes
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

        # Also get untracked files that are new
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        untracked = [
            f.strip() for f in result_untracked.stdout.strip().split("\n") if f.strip()
        ]
        files.extend(untracked)

        # Filter to only .py files and remove duplicates
        py_files = list(set(f for f in files if f.endswith(".py")))
        return py_files
    except Exception:
        return []


def run_ruff(files: list[str], project_dir: str) -> tuple[bool, str]:
    """Run ruff on files, return (success, output)."""
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "--no-fix"] + files,
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except FileNotFoundError:
        return True, ""  # ruff not installed


def run_mypy(files: list[str], project_dir: str) -> tuple[bool, str]:
    """Run mypy on files, return (success, output)."""
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["uv", "run", "mypy", "--no-incremental", "--no-error-summary"] + files,
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except FileNotFoundError:
        return True, ""  # mypy not installed


def main() -> None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    files = get_modified_files()

    if not files:
        sys.exit(0)

    issues: list[str] = []

    ruff_ok, ruff_out = run_ruff(files, project_dir)
    if not ruff_ok and ruff_out:
        issues.append(f"Ruff issues:\n{ruff_out}")

    mypy_ok, mypy_out = run_mypy(files, project_dir)
    if not mypy_ok and mypy_out:
        issues.append(f"Mypy issues:\n{mypy_out}")

    if issues:
        print("\n--- LINT ISSUES (please address) ---\n")
        print("\n\n".join(issues))
        sys.exit(0)  # Warn only, don't block Claude

    print(f"✓ Linting passed for {len(files)} file(s)")
    sys.exit(0)


if __name__ == "__main__":
    main()
