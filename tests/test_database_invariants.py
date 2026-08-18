"""Core database invariant tests.

These tests protect the evidence-layer guarantees of the system:

* I1: same commit + same file can only be allocated to one copyright
* I3: repeated scans are idempotent
* foreign keys and transaction rollback behave correctly

They exercise the real SQLite schema, not mocks.
"""

import sqlite3

import pytest

from database import (
    allocate_file,
    check_file_allocated,
    get_connection,
    get_or_create_commit,
    get_or_create_repository,
    init_database,
    insert_file,
    insert_files,
)


@pytest.fixture
def db(tmp_cwd):
    init_database()
    repo_id = get_or_create_repository("sample-repo", "/tmp/sample-repo")
    commit_id = get_or_create_commit(repo_id, "a" * 40)
    return commit_id


def _insert_file(commit_id, path="services/a/main.py", lines=100):
    return insert_file(
        commit_id=commit_id,
        file_path=path,
        sha256="0" * 64,
        language="Python",
        total_lines=lines,
        effective_lines=lines,
        file_size=lines * 20,
    )


def _insert_copyright(commit_id, cr_id="alpha"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO copyrights (commit_id, copyright_id, name, version) "
            "VALUES (?, ?, ?, ?)",
            (commit_id, cr_id, f"{cr_id} system", "V1.0"),
        )
        return cursor.lastrowid


class TestAllocationUniqueness:
    """I1: same commit + same file can never belong to two copyrights."""

    def test_allocate_file_rejects_second_copyright(self, db):
        file_id = _insert_file(db)
        cr_a = _insert_copyright(db, "alpha")
        cr_b = _insert_copyright(db, "beta")

        assert allocate_file(db, file_id, cr_a) is True
        assert allocate_file(db, file_id, cr_b) is False

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT copyright_id FROM allocations WHERE commit_id = ? AND file_id = ?",
                (db, file_id),
            ).fetchall()
        assert [r[0] for r in rows] == [cr_a]

    def test_raw_insert_violates_unique_constraint(self, db):
        """Even bypassing the helper, the schema must reject duplicates."""
        file_id = _insert_file(db)
        cr_a = _insert_copyright(db, "alpha")
        cr_b = _insert_copyright(db, "beta")

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO allocations (commit_id, file_id, copyright_id) VALUES (?, ?, ?)",
                (db, file_id, cr_a),
            )
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO allocations (commit_id, file_id, copyright_id) VALUES (?, ?, ?)",
                    (db, file_id, cr_b),
                )

    def test_check_file_allocated_reflects_state(self, db):
        file_id = _insert_file(db)
        cr_a = _insert_copyright(db, "alpha")
        assert check_file_allocated(db, file_id) is False
        allocate_file(db, file_id, cr_a)
        assert check_file_allocated(db, file_id) is True


class TestHistoricalSnapshots:
    """I2-related: same path in different commits are separate snapshots."""

    def test_same_path_different_commits_coexist(self, tmp_cwd):
        init_database()
        repo_id = get_or_create_repository("sample-repo", "/tmp/sample-repo")
        commit_1 = get_or_create_commit(repo_id, "1" * 40)
        commit_2 = get_or_create_commit(repo_id, "2" * 40)

        file_1 = _insert_file(commit_1, path="app/main.py", lines=100)
        file_2 = _insert_file(commit_2, path="app/main.py", lines=120)

        assert file_1 != file_2

        cr_1 = _insert_copyright(commit_1, "old-release")
        cr_2 = _insert_copyright(commit_2, "new-release")
        assert allocate_file(commit_1, file_1, cr_1) is True
        assert allocate_file(commit_2, file_2, cr_2) is True


class TestScanIdempotency:
    """I3: rescanning the same commit must not duplicate rows."""

    def test_rescan_same_commit_is_idempotent(self, tmp_cwd, seeded_scan):
        files = {
            "services/a/main.py": "print('a')\n",
            "services/b/main.py": "print('b')\nprint('b2')\n",
        }
        first, repo = seeded_scan(files)
        second_scan = __import__("scan_repository").scan_repository(
            repo, first["commit_hash"]
        )

        assert first["commit_id"] == second_scan["commit_id"]
        assert len(first["files"]) == len(second_scan["files"]) == 2

        first_by_path = {f["path"]: f for f in first["files"]}
        second_by_path = {f["path"]: f for f in second_scan["files"]}
        for path, file in first_by_path.items():
            assert second_by_path[path]["sha256"] == file["sha256"]
            assert second_by_path[path]["effective_lines"] == file["effective_lines"]

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM files WHERE commit_id = ?",
                (first["commit_id"],),
            ).fetchone()[0]
        assert count == 2

    def test_insert_file_updates_content_on_rescan(self, db):
        file_id_1 = insert_file(
            commit_id=db, file_path="app/x.py", sha256="a" * 64,
            language="Python", total_lines=10, effective_lines=9, file_size=100,
        )
        file_id_2 = insert_file(
            commit_id=db, file_path="app/x.py", sha256="b" * 64,
            language="Python", total_lines=12, effective_lines=11, file_size=120,
        )
        assert file_id_1 == file_id_2
        with get_connection() as conn:
            row = conn.execute(
                "SELECT sha256, effective_lines FROM files WHERE id = ?",
                (file_id_1,),
            ).fetchone()
        assert row == ("b" * 64, 11)


class TestTransactionSafety:
    def test_failed_transaction_rolls_back(self, db):
        with pytest.raises(RuntimeError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO copyrights (commit_id, copyright_id, name, version) "
                    "VALUES (?, 'ghost', 'ghost', 'V1.0')",
                    (db,),
                )
                raise RuntimeError("simulated failure mid-transaction")

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM copyrights WHERE copyright_id = 'ghost'"
            ).fetchone()[0]
        assert count == 0


class TestForeignKeys:
    def test_allocation_requires_existing_file(self, db):
        cr = _insert_copyright(db, "alpha")
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO allocations (commit_id, file_id, copyright_id) "
                    "VALUES (?, 999999, ?)",
                    (db, cr),
                )

    def test_allocation_requires_existing_copyright(self, db):
        file_id = _insert_file(db)
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO allocations (commit_id, file_id, copyright_id) "
                    "VALUES (?, ?, 999999)",
                    (db, file_id),
                )

    def test_file_requires_existing_commit(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO files (commit_id, file_path, sha256) "
                    "VALUES (999999, 'orphan.py', 'c' * 64)"
                )


def _batch_rows(paths, lines=100, sha="0" * 64):
    return [
        {
            "file_path": path,
            "sha256": sha,
            "language": "Python",
            "total_lines": lines,
            "effective_lines": lines,
            "file_size": lines * 20,
        }
        for path in paths
    ]


class TestBatchInsertFiles:
    """Batch scan inserts: one connection, one atomic transaction."""

    def test_batch_insert_returns_path_to_id_mapping(self, db):
        paths = ["a.py", "b/c.py", "d/e/f.py"]
        mapping = insert_files(db, _batch_rows(paths))
        assert set(mapping) == set(paths)
        assert len(set(mapping.values())) == 3  # distinct ids

    def test_accepts_tuple_rows_like_dict_rows(self, db):
        mapping = insert_files(
            db, [("t.py", "1" * 64, "Python", 5, 5, 100)]
        )
        assert list(mapping) == ["t.py"]

    def test_batch_rescan_is_idempotent_and_updates_content(self, db):
        paths = ["a.py", "b.py"]
        first = insert_files(db, _batch_rows(paths, lines=100))
        second = insert_files(db, _batch_rows(paths, lines=250, sha="f" * 64))
        assert first == second  # ids stable across re-scans

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM files WHERE commit_id = ?", (db,)
            ).fetchone()[0]
            row = conn.execute(
                "SELECT effective_lines, sha256 FROM files "
                "WHERE commit_id = ? AND file_path = 'a.py'",
                (db,),
            ).fetchone()
        assert count == 2  # no duplicate rows
        assert row == (250, "f" * 64)  # content refreshed

    def test_batch_is_atomic_when_one_row_is_invalid(self, db):
        rows = _batch_rows(["good1.py", "good2.py"])
        # sha256 is NOT NULL: this row must abort the WHOLE batch.
        bad = _batch_rows(["bad.py"])
        bad[0]["sha256"] = None
        rows.insert(1, bad[0])

        with pytest.raises(sqlite3.IntegrityError):
            insert_files(db, rows)

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM files WHERE commit_id = ?", (db,)
            ).fetchone()[0]
        assert count == 0, "failed batch must roll back every row"

    def test_empty_rows_returns_empty_dict(self, db):
        assert insert_files(db, []) == {}


class TestScanUsesBatchInsert:
    """End-to-end: a real scan still works through the batch path."""

    def test_scan_populates_ids_via_batch_insert(self, seeded_scan):
        scan, _ = seeded_scan(
            {
                "services/a/one.py": "print(1)\n",
                "services/a/two.py": "print(2)\n",
                "services/b/three.py": "print(3)\n",
            }
        )
        ids = [f["id"] for f in scan["files"]]
        assert len(ids) == 3
        assert len(set(ids)) == 3  # distinct real ids
        # Re-scan the same commit: ids must be stable (upsert, no dupes).
        from scan_repository import scan_repository

        rescan = scan_repository(scan_repo_path(scan), scan["commit_hash"])
        assert sorted(f["id"] for f in rescan["files"]) == sorted(ids)


def scan_repo_path(scan_result):
    from pathlib import Path as _Path

    return _Path(scan_result["repo_path"])
