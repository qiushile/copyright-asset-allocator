"""Ownership integration tests using REAL temporary git repositories.

These tests never mock git: they create throw-away repositories with
``git init``, real commits from different authors at different dates, and
exercise the actual ``git blame --line-porcelain`` pipeline.
"""

import subprocess
from datetime import datetime
from pathlib import Path

from conftest import commit_all, make_git_repo, write_files

from analyze_ownership import (
    add_team_member,
    analyze_file_ownership,
    analyze_repository_ownership,
    get_team_emails,
    run_git_blame,
)
from database import get_connection
from scan_repository import scan_repository

TEAM = "team@company.com"
OUTSIDER = "outsider@external.com"


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _blame_emails(repo, path, commit="HEAD"):
    return [info["author_email"] for info in run_git_blame(repo, path, commit)]


class TestGitBlame:
    def test_blame_attributes_lines_to_real_authors(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "a = 1\nb = 2\n"})
        commit_all(repo, "c1", author_name="Outsider", author_email=OUTSIDER)
        write_files(repo, {"f.py": "a = 1\nb = 99\n"})  # outsider-owned line rewritten
        commit_all(repo, "c2", author_name="Team Dev", author_email=TEAM)

        emails = _blame_emails(repo, "f.py")
        assert emails == [OUTSIDER, TEAM]

    def test_blame_at_locked_commit_ignores_later_changes(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "a = 1\nb = 2\n"})
        commit_all(repo, "c1", author_name="Team Dev", author_email=TEAM)
        locked = _head(repo)
        write_files(repo, {"f.py": "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n"})
        commit_all(repo, "c2", author_name="Outsider", author_email=OUTSIDER)

        # blame pinned at the locked commit sees only the original team lines
        locked_blame = run_git_blame(repo, "f.py", locked)
        assert len(locked_blame) == 2
        assert all(info["author_email"] == TEAM for info in locked_blame)
        assert all(info["commit_hash"] == locked for info in locked_blame)

        # HEAD blame sees the later outsider additions
        head_emails = _blame_emails(repo, "f.py")
        assert head_emails.count(OUTSIDER) == 3
        assert head_emails.count(TEAM) == 2

    def test_blame_missing_file_returns_empty(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "a = 1\n"})
        commit_all(repo, "c1")
        assert run_git_blame(repo, "nope.py") == []


class TestFileOwnership:
    def test_majority_team_file_is_eligible(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "a = 1\nb = 2\n"})
        commit_all(repo, "c1", author_name="Outsider", author_email=OUTSIDER)
        write_files(repo, {"f.py": "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\ng = 7\nh = 8\n"})
        commit_all(repo, "c2", author_name="Team Dev", author_email=TEAM)

        result = analyze_file_ownership(repo, "f.py", _head(repo), {TEAM})
        assert result["total_lines"] == 8
        assert result["team_lines"] == 6
        assert result["team_ratio"] == 0.75
        assert result["is_eligible"] is True

    def test_minority_team_file_is_ineligible(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "".join(f"x{i} = {i}\n" for i in range(8))})
        commit_all(repo, "c1", author_name="Outsider", author_email=OUTSIDER)
        lines = [f"x{i} = {i}\n" for i in range(8)]
        lines[0] = "x0 = 999\n"  # team touches a single line only
        write_files(repo, {"f.py": "".join(lines)})
        commit_all(repo, "c2", author_name="Team Dev", author_email=TEAM)

        result = analyze_file_ownership(repo, "f.py", _head(repo), {TEAM})
        assert result["team_ratio"] == 0.125
        assert result["is_eligible"] is False

    def test_team_email_matching_is_case_insensitive(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "a = 1\n"})
        commit_all(repo, "c1", author_name="Team Dev", author_email="Team@Company.COM")

        result = analyze_file_ownership(repo, "f.py", _head(repo), {TEAM})
        assert result["team_ratio"] == 1.0
        assert result["is_eligible"] is True

    def test_since_filter_accepts_recent_team_rewrite(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "".join(f"old{i} = {i}\n" for i in range(10))})
        commit_all(repo, "c1", author_name="Outsider", author_email=OUTSIDER,
                   date="2024-01-01T10:00:00 +0800")
        write_files(repo, {"f.py": "".join(f"new{i} = {i}\n" for i in range(10))})
        commit_all(repo, "c2", author_name="Team Dev", author_email=TEAM,
                   date="2025-06-01T10:00:00 +0800")

        since = int(datetime(2025, 1, 1).timestamp())
        result = analyze_file_ownership(repo, "f.py", _head(repo), {TEAM},
                                        since_timestamp=since)
        assert result["recent_ratio"] == 1.0
        assert result["is_eligible"] is True

    def test_since_filter_rejects_old_team_code(self, tmp_cwd):
        """Team-written but entirely before the cutoff is NOT eligible."""
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "".join(f"x{i} = {i}\n" for i in range(10))})
        commit_all(repo, "c1", author_name="Team Dev", author_email=TEAM,
                   date="2024-01-01T10:00:00 +0800")

        since = int(datetime(2025, 1, 1).timestamp())
        result = analyze_file_ownership(repo, "f.py", _head(repo), {TEAM},
                                        since_timestamp=since)
        assert result["team_ratio"] == 1.0  # still team code...
        assert result["recent_ratio"] == 0.0  # ...but nothing recent
        assert result["is_eligible"] is False


class TestRepositoryOwnershipEndToEnd:
    def test_analyze_stores_stats_and_allocation_skips_non_team(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"services/vendor.py": "".join(f"v{i} = {i}\n" for i in range(5))})
        commit_all(repo, "c1", author_name="Outsider", author_email=OUTSIDER)
        write_files(repo, {"services/team.py": "".join(f"t{i} = {i}\n" for i in range(5))})
        commit_all(repo, "c2", author_name="Team Dev", author_email=TEAM)
        commit_hash = _head(repo)

        scan = scan_repository(repo, commit_hash)
        add_team_member("Team Dev", TEAM)
        team_emails = get_team_emails()
        assert team_emails == {TEAM}

        summary = analyze_repository_ownership(
            scan["commit_id"], repo, commit_hash, team_emails
        )
        assert summary["total_files"] == 2
        assert summary["eligible_files"] == 1
        assert summary["ineligible_files"] == 1

        with get_connection() as conn:
            rows = dict(conn.execute(
                "SELECT f.file_path, s.is_eligible FROM file_team_stats s "
                "JOIN files f ON f.id = s.file_id WHERE s.commit_id = ?",
                (scan["commit_id"],),
            ).fetchall())
        assert rows == {"services/team.py": 1, "services/vendor.py": 0}

        # allocation must now skip the non-team file by default
        from allocate_files import allocate_files_from_config
        results = allocate_files_from_config(
            commit_id=scan["commit_id"],
            files=scan["files"],
            copyrights_config=[{
                "id": "alpha", "name": "alpha system", "version": "V1.0",
                "include_paths": ["services/**"], "exclude_paths": [],
            }],
        )
        assert [f["path"] for f in results["allocations"]["alpha"]] == ["services/team.py"]
        assert [s["file"] for s in results["skipped_non_team"]] == ["services/vendor.py"]

        evidence = {d["path"]: d for d in results["decisions"]}
        assert evidence["services/vendor.py"]["eligibility"]["eligible"] is False
        assert evidence["services/vendor.py"]["eligibility"]["team_ratio"] == 0.0
        assert evidence["services/team.py"]["eligibility"]["team_ratio"] == 1.0

    def test_reanalysis_is_idempotent(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {"f.py": "a = 1\n"})
        commit_all(repo, "c1", author_name="Team Dev", author_email=TEAM)
        commit_hash = _head(repo)
        scan = scan_repository(repo, commit_hash)

        analyze_repository_ownership(scan["commit_id"], repo, commit_hash, {TEAM})
        analyze_repository_ownership(scan["commit_id"], repo, commit_hash, {TEAM})

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM file_team_stats WHERE commit_id = ?",
                (scan["commit_id"],),
            ).fetchone()[0]
        assert count == 1  # old rows cleared, not duplicated
