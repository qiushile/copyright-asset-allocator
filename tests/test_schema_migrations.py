"""Schema versioning and migration mechanism.

Invariants under test:

* a fresh database reaches SCHEMA_VERSION and records every migration;
* init_database() is idempotent (re-runs must not duplicate versions);
* legacy (pre-versioning) databases are adopted without data loss;
* pending migrations apply in ascending version order, exactly once;
* the allocations uniqueness hard-constraint survives migration-based init.
"""

import sqlite3

import pytest

from database import (
    MIGRATIONS,
    SCHEMA_VERSION,
    allocate_file,
    get_applied_migrations,
    get_connection,
    get_or_create_commit,
    get_or_create_repository,
    get_schema_version,
    init_database,
    insert_file,
)


def test_fresh_database_reaches_latest_version(tmp_cwd):
    init_database()
    assert get_schema_version() == SCHEMA_VERSION
    applied = get_applied_migrations()
    assert [version for version, _, _ in applied] == [v for v, _, _ in MIGRATIONS]
    assert applied[0][1] == "baseline_schema"


def test_init_is_idempotent(tmp_cwd):
    init_database()
    init_database()
    init_database()
    assert get_schema_version() == SCHEMA_VERSION
    versions = [version for version, _, _ in get_applied_migrations()]
    assert versions == sorted(set(versions)), "migration versions must not repeat"


def test_legacy_database_adopted_without_data_loss(tmp_cwd):
    """A DB created by the old CREATE-TABLE-IF-NOT-EXISTS code must be
    stamped at version 1 while keeping all existing rows."""
    from pathlib import Path

    Path("copyright/inventory").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect("copyright/inventory/assets.db")
    conn.execute(
        """
        CREATE TABLE repositories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO repositories (name, path) VALUES ('legacy', '/tmp/legacy')")
    conn.commit()
    conn.close()

    init_database()

    assert get_schema_version() == SCHEMA_VERSION
    with get_connection() as conn:
        rows = conn.execute("SELECT name, path FROM repositories").fetchall()
    assert ("legacy", "/tmp/legacy") in rows, "legacy data must survive adoption"


def test_pending_migrations_apply_in_order_exactly_once(tmp_cwd):
    """A higher-version migration applies after the baseline, once."""
    calls = []

    def migration_two(cursor):
        calls.append(2)
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS migration_probe (id INTEGER PRIMARY KEY)"
        )

    probe_version = SCHEMA_VERSION + 1
    extended = list(MIGRATIONS) + [(probe_version, "probe", migration_two)]

    init_database(migrations=extended)
    assert calls == [2]
    assert get_schema_version() == probe_version

    # Re-init with the same registry must not re-apply the probe migration.
    init_database(migrations=extended)
    assert calls == [2]
    with get_connection() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_probe'"
        ).fetchone()
    assert table is not None


def test_migration_versions_must_be_strictly_increasing(tmp_cwd):
    bad = list(MIGRATIONS) + [(1, "duplicate", lambda cursor: None)]
    with pytest.raises(ValueError):
        init_database(migrations=bad)

    gaps = list(MIGRATIONS) + [(SCHEMA_VERSION + 2, "gap", lambda cursor: None)]
    # gaps are fine as long as ordering is strict; duplicates are not
    init_database(migrations=gaps)
    assert get_schema_version() == SCHEMA_VERSION + 2


def test_failed_migration_rolls_back_completely(tmp_cwd):
    init_database()

    def broken(cursor):
        cursor.execute("CREATE TABLE probe_partial (id INTEGER PRIMARY KEY)")
        raise RuntimeError("simulated migration failure")

    extended = list(MIGRATIONS) + [(SCHEMA_VERSION + 1, "broken", broken)]
    with pytest.raises(RuntimeError):
        init_database(migrations=extended)

    # Neither the partial table nor the version stamp may survive; the
    # database stays at the last successfully applied real migration.
    assert get_schema_version() == SCHEMA_VERSION
    with get_connection() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='probe_partial'"
        ).fetchone()
    assert table is None, "failed migration must roll back all of its changes"


def test_unique_allocation_constraint_survives_migration_init(tmp_cwd):
    """The hard constraint (same commit + same file -> one copyright) must
    hold on databases initialized through the migration path."""
    init_database()
    repo_id = get_or_create_repository("invariant-repo", "/tmp/invariant-repo")
    commit_id = get_or_create_commit(repo_id, "c" * 40)
    file_id = insert_file(commit_id, "a/b.py", "0" * 64, "Python", 10, 8, 100)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO copyrights (commit_id, copyright_id, name, version) "
            "VALUES (?, 'A', 'Soft A', 'V1.0')",
            (commit_id,),
        )
        conn.execute(
            "INSERT INTO copyrights (commit_id, copyright_id, name, version) "
            "VALUES (?, 'B', 'Soft B', 'V1.0')",
            (commit_id,),
        )
        cr_a = conn.execute(
            "SELECT id FROM copyrights WHERE copyright_id = 'A'"
        ).fetchone()[0]
        cr_b = conn.execute(
            "SELECT id FROM copyrights WHERE copyright_id = 'B'"
        ).fetchone()[0]

    assert allocate_file(commit_id, file_id, cr_a) is True
    assert allocate_file(commit_id, file_id, cr_b) is False

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM allocations WHERE commit_id = ? AND file_id = ?",
            (commit_id, file_id),
        ).fetchone()[0]
    assert count == 1
