"""Repository-scoped settings (since_date) tests.

Covers task book §9.2: project-level settings must not leak between
repositories.  A repository-scoped ``since_date`` wins over the global
one; clearing it restores the global fallback.  ``team_members`` stays
global by an explicit, documented design decision.
"""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import copyright_cli  # noqa: E402

from conftest import commit_all, make_git_repo, write_files  # noqa: E402
from database import (  # noqa: E402
    SCHEMA_VERSION,
    clear_repository_setting,
    find_repository_by_path,
    get_connection,
    get_or_create_commit,
    get_or_create_repository,
    get_repository_id_for_commit,
    get_repository_setting,
    get_since_date,
    get_since_timestamp,
    get_time_eligible_file_ids,
    init_database,
    list_repositories,
    list_repository_settings,
    parse_since_date,
    set_repository_setting,
    set_since_date,
)


@pytest.fixture
def two_repos(tmp_cwd):
    """Two scanned repositories with one commit each."""
    init_database()
    repo_a_id = get_or_create_repository('repo-a', str(tmp_cwd / 'repo-a'))
    repo_b_id = get_or_create_repository('repo-b', str(tmp_cwd / 'repo-b'))
    commit_a = get_or_create_commit(repo_a_id, 'a' * 40)
    commit_b = get_or_create_commit(repo_b_id, 'b' * 40)
    return repo_a_id, repo_b_id, commit_a, commit_b


class TestMigration:
    def test_repository_settings_table_exists(self, tmp_cwd):
        init_database()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='repository_settings'"
            ).fetchone()
        assert row is not None
        assert SCHEMA_VERSION >= 3

    def test_unique_per_repository_key(self, tmp_cwd):
        init_database()
        repo_id = get_or_create_repository('r', '/tmp/r')
        set_repository_setting(repo_id, 'since_date', '2024-01-01')
        set_repository_setting(repo_id, 'since_date', '2025-06-01')  # upsert
        assert get_repository_setting(repo_id, 'since_date') == '2025-06-01'

    def test_foreign_key_enforced(self, tmp_cwd):
        init_database()
        with pytest.raises(sqlite3.IntegrityError):
            set_repository_setting(99999, 'since_date', '2024-01-01')


class TestSinceScoping:
    def test_global_only_legacy_behavior(self, tmp_cwd):
        init_database()
        set_since_date('2024-03-01')
        assert get_since_date() == '2024-03-01'
        assert get_since_timestamp() == parse_since_date('2024-03-01')
        # Unknown repository falls back to the global value.
        assert get_since_date(repository_id=42) == '2024-03-01'

    def test_repo_scoped_wins_over_global(self, tmp_cwd, two_repos):
        repo_a, repo_b, _, _ = two_repos
        set_since_date('2023-01-01')  # global
        set_since_date('2025-05-05', repository_id=repo_a)
        assert get_since_date(repo_a) == '2025-05-05'
        assert get_since_date(repo_b) == '2023-01-01'  # untouched repo
        assert get_since_timestamp(repo_a) == parse_since_date('2025-05-05')

    def test_clear_repo_override_restores_global_fallback(self, tmp_cwd, two_repos):
        repo_a, _, _, _ = two_repos
        set_since_date('2023-01-01')
        set_since_date('2025-05-05', repository_id=repo_a)
        set_since_date('', repository_id=repo_a)  # clear scoped override
        assert get_repository_setting(repo_a, 'since_date') is None
        assert get_since_date(repo_a) == '2023-01-01'

    def test_two_repos_isolated(self, tmp_cwd, two_repos):
        repo_a, repo_b, _, _ = two_repos
        set_since_date('2024-01-01', repository_id=repo_a)
        set_since_date('2026-07-01', repository_id=repo_b)
        assert get_since_date(repo_a) == '2024-01-01'
        assert get_since_date(repo_b) == '2026-07-01'
        overrides = list_repository_settings('since_date')
        assert {o['repository_id']: o['value'] for o in overrides} == {
            repo_a: '2024-01-01', repo_b: '2026-07-01'}

    def test_no_filter_returns_none(self, tmp_cwd):
        init_database()
        assert get_since_date() is None
        assert get_since_timestamp() is None

    def test_parse_since_date_formats(self):
        assert parse_since_date('2025-01-02') is not None
        assert parse_since_date('2025-01-02 03:04:05') is not None
        assert parse_since_date('2025/01/02') is not None
        assert parse_since_date('2025-01') is not None
        assert parse_since_date('not-a-date') is None
        assert parse_since_date('') is None
        assert parse_since_date(None) is None


class TestCommitResolution:
    def test_get_repository_id_for_commit(self, tmp_cwd, two_repos):
        repo_a, repo_b, commit_a, commit_b = two_repos
        assert get_repository_id_for_commit(commit_a) == repo_a
        assert get_repository_id_for_commit(commit_b) == repo_b
        assert get_repository_id_for_commit(99999) is None

    def test_time_eligibility_uses_own_repo_cutoff(self, tmp_cwd, two_repos):
        """get_time_eligible_file_ids must resolve the commit's repository.

        Repo A has a cutoff set, repo B has none: only repo A's commit is
        filtered.  file_team_stats rows are seeded directly because the
        eligibility flags are computed by ownership analysis.
        """
        repo_a, repo_b, commit_a, commit_b = two_repos
        with get_connection() as conn:
            cur = conn.cursor()
            # One file per commit; both marked eligible by analysis.
            cur.execute("""
                INSERT INTO files (commit_id, file_path, sha256, language,
                                   total_lines, effective_lines, file_size)
                VALUES (?, 'a.py', 'h1', 'Python', 10, 10, 100)
            """, (commit_a,))
            file_a = cur.lastrowid
            cur.execute("""
                INSERT INTO files (commit_id, file_path, sha256, language,
                                   total_lines, effective_lines, file_size)
                VALUES (?, 'b.py', 'h2', 'Python', 10, 10, 100)
            """, (commit_b,))
            file_b = cur.lastrowid
            cur.execute("""
                INSERT INTO file_team_stats
                    (commit_id, file_id, team_ratio, is_eligible)
                VALUES (?, ?, 1.0, 1)
            """, (commit_a, file_a))
            cur.execute("""
                INSERT INTO file_team_stats
                    (commit_id, file_id, team_ratio, is_eligible)
                VALUES (?, ?, 1.0, 1)
            """, (commit_b, file_b))

        # No cutoff anywhere: both commits unfiltered.
        assert get_time_eligible_file_ids(commit_a) is None
        assert get_time_eligible_file_ids(commit_b) is None

        # Cutoff only for repo A: only repo A's commit is filtered.
        set_since_date('2025-01-01', repository_id=repo_a)
        assert get_time_eligible_file_ids(commit_a) == {file_a}
        assert get_time_eligible_file_ids(commit_b) is None


class TestFindRepositoryByPath:
    def test_matches_resolved_paths(self, tmp_cwd):
        init_database()
        repo = make_git_repo(tmp_cwd)
        get_or_create_repository(repo.name, str(repo))
        found = find_repository_by_path(repo)
        assert found is not None and found['name'] == repo.name
        # Relative spelling of the same directory still matches.
        import os
        rel = os.path.relpath(repo, tmp_cwd)
        assert find_repository_by_path(rel)['id'] == found['id']
        assert find_repository_by_path(tmp_cwd / 'missing') is None

    def test_list_repositories_ordered(self, tmp_cwd, two_repos):
        repos = list_repositories()
        assert [r['name'] for r in repos] == ['repo-a', 'repo-b']


class TestCliSetSince:
    def _scan(self, files):
        repo = make_git_repo(Path.cwd())
        write_files(repo, files)
        commit_all(repo, 'seed')
        copyright_cli.cmd_scan(SimpleNamespace(
            repo_path=str(repo), commit=None, verbose=False))
        return repo

    def test_unknown_repo_path_errors(self, tmp_cwd, capsys):
        init_database()
        with pytest.raises(SystemExit) as exc:
            copyright_cli.cmd_set_since(SimpleNamespace(
                action='set', date='2025-01-01',
                repo_path=str(tmp_cwd / 'never-scanned')))
        assert exc.value.code == 1
        assert 'has not been scanned' in capsys.readouterr().out

    def test_set_show_clear_scoped(self, tmp_cwd, capsys):
        repo = self._scan({'services/main.py': 'print(1)\n'})

        copyright_cli.cmd_set_since(SimpleNamespace(
            action='set', date='2025-02-01', repo_path=str(repo)))
        out = capsys.readouterr().out
        assert '2025-02-01' in out and 'repository' in out

        copyright_cli.cmd_set_since(SimpleNamespace(
            action='show', repo_path=str(repo)))
        out = capsys.readouterr().out
        assert '2025-02-01' in out
        assert 'Repository-scoped' in out

        copyright_cli.cmd_set_since(SimpleNamespace(
            action='clear', repo_path=str(repo)))
        out = capsys.readouterr().out
        assert 'cleared' in out
        # Override really gone from the database.
        from database import find_repository_by_path
        assert get_repository_setting(
            find_repository_by_path(repo)['id'], 'since_date') is None

    def test_invalid_date_not_stored(self, tmp_cwd, capsys):
        repo = self._scan({'services/main.py': 'print(1)\n'})
        from database import find_repository_by_path
        repo_id = find_repository_by_path(repo)['id']
        with pytest.raises(SystemExit) as exc:
            copyright_cli.cmd_set_since(SimpleNamespace(
                action='set', date='garbage', repo_path=str(repo)))
        assert exc.value.code == 1
        capsys.readouterr()
        assert get_repository_setting(repo_id, 'since_date') is None

    def test_global_show_lists_overrides(self, tmp_cwd, capsys):
        repo = self._scan({'services/main.py': 'print(1)\n'})
        copyright_cli.cmd_set_since(SimpleNamespace(
            action='set', date='2025-02-01', repo_path=str(repo)))
        capsys.readouterr()
        copyright_cli.cmd_set_since(SimpleNamespace(
            action='set', date='2024-01-01', repo_path=None))
        capsys.readouterr()
        copyright_cli.cmd_set_since(SimpleNamespace(
            action='show', repo_path=None))
        out = capsys.readouterr().out
        assert 'global' in out.lower()
        assert 'overrides' in out.lower()
        assert '2025-02-01' in out

    def test_scan_result_carries_repository_id(self, tmp_cwd):
        """Ownership analyze scopes --since via scan_result['repository_id']."""
        import json
        self._scan({'services/main.py': 'print(1)\n'})
        scan_result = json.loads(
            (Path.cwd() / 'copyright/inventory/scan-result.json')
            .read_text(encoding='utf-8'))
        assert scan_result['repository_id']
        assert get_repository_id_for_commit(
            scan_result['commit_id']) == scan_result['repository_id']


class TestTeamMembersGlobalByDesign:
    def test_team_members_has_no_repository_column(self, tmp_cwd):
        """Locks the documented §9.2 decision: the roster is a shared
        people directory; per-commit git-blame evidence decides
        eligibility, so membership itself must not be repo-scoped."""
        init_database()
        with get_connection() as conn:
            cols = {row[1] for row in
                    conn.execute("PRAGMA table_info(team_members)")}
        assert 'repository_id' not in cols
