#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Lint Python files after Edit/Write operations.

This hook runs after Claude edits or creates Python files.
It reads the file path from stdin (PostToolUse event) and runs ruff + mypy.
"""

import json
import os
import subprocess
import sys


def run_ruff(file_path: str, project_dir: str) -> tuple[bool, str]:
    """Run ruff on file, return (success, output)."""
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "--no-fix", file_path],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=30,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except FileNotFoundError:
        return True, ""  # ruff not installed
    except subprocess.TimeoutExpired:
        return True, "ruff timed out"


def run_mypy(file_path: str, project_dir: str) -> tuple[bool, str]:
    """Run mypy on file, return (success, output)."""
    try:
        result = subprocess.run(
            ["uv", "run", "mypy", "--no-incremental", "--no-error-summary", file_path],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=30,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except FileNotFoundError:
        return True, ""  # mypy not installed
    except subprocess.TimeoutExpired:
        return True, "mypy timed out"


def main() -> None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")

    # Read hook input from stdin (PostToolUse provides JSON with tool_input)
    try:
        input_data = json.load(sys.stdin)
        file_path = input_data.get("tool_input", {}).get("file_path", "")
    except (json.JSONDecodeError, AttributeError):
        # Fallback: no valid input, exit silently
        sys.exit(0)

    # Only lint Python files
    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)

    # Skip test files and other non-src files if desired (optional)
    skip_patterns = ["__pycache__", ".venv", "venv", ".git"]
    if any(pattern in file_path for pattern in skip_patterns):
        sys.exit(0)

    issues: list[str] = []

    ruff_ok, ruff_out = run_ruff(file_path, project_dir)
    if not ruff_ok and ruff_out:
        issues.append(f"Ruff issues:\n{ruff_out}")

    mypy_ok, mypy_out = run_mypy(file_path, project_dir)
    if not mypy_ok and mypy_out:
        issues.append(f"Mypy issues:\n{mypy_out}")

    if issues:
        print("\n--- LINT ISSUES (please address) ---\n")
        print("\n\n".join(issues))
        sys.exit(0)  # Warn only, don't block Claude

    # Success - brief confirmation
    short_path = os.path.basename(file_path)
    print(f"✓ {short_path} passed ruff + mypy")
    sys.exit(0)


if __name__ == "__main__":
    main()
