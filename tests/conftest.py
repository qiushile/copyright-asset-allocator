"""Shared fixtures for core engine tests.

The engine scripts use relative paths (``copyright/inventory/assets.db`` and
friends), so every test that touches the database must run inside a fresh
temporary working directory.  ``tmp_cwd`` provides that isolation.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def tmp_cwd(tmp_path, monkeypatch):
    """Run the test inside a fresh temporary working directory.

    Guarantees the SQLite database and all ``copyright/...`` outputs are
    created inside ``tmp_path`` and never touch the real project state.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_git(repo: Path, *args, env_extra=None):
    """Run a git command inside ``repo``."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def make_git_repo(base: Path, name: str = "sample-repo") -> Path:
    """Create an initialized git repository with identity configured."""
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.name", "Test Author")
    run_git(repo, "config", "user.email", "author@example.com")
    run_git(repo, "config", "commit.gpgsign", "false")
    # Hermetic test repos: neutralize any user-global hooks (e.g. a global
    # commit-msg hook enforcing Conventional Commits) so tests never depend
    # on the host machine's git configuration.
    hooks_dir = repo / ".test-hooks-disabled"
    hooks_dir.mkdir(exist_ok=True)
    run_git(repo, "config", "core.hooksPath", str(hooks_dir))
    return repo


def commit_all(repo: Path, message: str, author_name=None, author_email=None,
               date=None) -> str:
    """Stage everything and commit; returns the commit hash."""
    run_git(repo, "add", "-A")
    env_extra = {}
    if author_name:
        env_extra["GIT_AUTHOR_NAME"] = author_name
        env_extra["GIT_COMMITTER_NAME"] = author_name
    if author_email:
        env_extra["GIT_AUTHOR_EMAIL"] = author_email
        env_extra["GIT_COMMITTER_EMAIL"] = author_email
    if date:
        env_extra["GIT_AUTHOR_DATE"] = date
        env_extra["GIT_COMMITTER_DATE"] = date
    run_git(repo, "commit", "-qm", message, env_extra=env_extra or None)
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def write_files(repo: Path, files: dict):
    """Write ``{path: content}`` into the repository working tree."""
    for rel_path, content in files.items():
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


@pytest.fixture
def seeded_scan(tmp_cwd):
    """Factory: build a git repo with the given files, scan it, return
    ``(scan_result, repo_path)``.

    Usage:
        scan, repo = seeded_scan({"services/a/main.py": "print(1)\\n"})
    """
    from scan_repository import scan_repository

    def _seed(files: dict, message: str = "test: seed files"):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, files)
        commit_all(repo, message)
        commit_hash = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        result = scan_repository(repo, commit_hash)
        return result, repo

    return _seed
