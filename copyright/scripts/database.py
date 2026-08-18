#!/usr/bin/env python3
"""Database schema and operations for copyright asset allocation."""

import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

DB_PATH = Path("copyright/inventory/assets.db")


@contextmanager
def get_connection():
    """Get database connection with WAL mode for better concurrency.
    
    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            ...
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema versioning & migrations
# ---------------------------------------------------------------------------
#
# Every schema change ships as a numbered, ordered migration in MIGRATIONS.
# init_database() applies pending migrations inside ONE transaction, so a
# failed migration rolls back completely and never leaves a half-upgraded
# evidence database.  Already-applied versions are recorded in
# ``schema_migrations`` and never re-run.
#
# Legacy databases created before versioning (plain CREATE TABLE IF NOT
# EXISTS) are adopted non-destructively: migration 0001 uses idempotent DDL,
# so existing rows survive and the DB is stamped at version 1.

MIGRATION_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


def _migration_0001_baseline_schema(cursor):
    """Baseline schema: all core tables and indexes (idempotent DDL)."""

    # 仓库表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS repositories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Commit 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS commits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repository_id INTEGER NOT NULL,
            commit_hash TEXT NOT NULL,
            tag TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (repository_id) REFERENCES repositories(id),
            UNIQUE(repository_id, commit_hash)
        )
    """)

    # 文件表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            language TEXT,
            total_lines INTEGER,
            effective_lines INTEGER,
            file_size INTEGER,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            UNIQUE(commit_id, file_path)
        )
    """)

    # 软著表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS copyrights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            copyright_id TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            locked_at TIMESTAMP,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            UNIQUE(commit_id, copyright_id)
        )
    """)

    # 分配表 - 核心：唯一约束防止重复分配
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            copyright_id INTEGER NOT NULL,
            allocated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (file_id) REFERENCES files(id),
            FOREIGN KEY (copyright_id) REFERENCES copyrights(id),
            UNIQUE(commit_id, file_id)
        )
    """)

    # 排除文件表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS excluded_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (file_id) REFERENCES files(id),
            UNIQUE(commit_id, file_id)
        )
    """)

    # 重合检测报告表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS duplicate_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            copyright_id_1 INTEGER NOT NULL,
            copyright_id_2 INTEGER NOT NULL,
            path_overlap_count INTEGER DEFAULT 0,
            exact_file_overlap_count INTEGER DEFAULT 0,
            similar_function_count INTEGER DEFAULT 0,
            similar_block_count INTEGER DEFAULT 0,
            max_similar_block_lines INTEGER DEFAULT 0,
            overall_similarity_ratio REAL DEFAULT 0.0,
            report_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (copyright_id_1) REFERENCES copyrights(id),
            FOREIGN KEY (copyright_id_2) REFERENCES copyrights(id)
        )
    """)

    # ========== 新增表 ==========

    # 已申请软著代码标记表（功能1）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS used_code_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            copyright_name TEXT NOT NULL,
            source_document TEXT,
            line_start INTEGER,
            line_end INTEGER,
            code_hash TEXT,
            marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    """)

    # 已使用文件表（文件级别标记）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS used_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            copyright_name TEXT NOT NULL,
            source_document TEXT,
            match_type TEXT DEFAULT 'exact',
            marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (file_id) REFERENCES files(id),
            UNIQUE(commit_id, file_id, copyright_name)
        )
    """)

    # 团队人员表（功能2）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            role TEXT DEFAULT 'developer',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 代码归属表（功能2：基于 git blame 的行级归属）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_ownership (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            author_email TEXT NOT NULL,
            author_name TEXT,
            is_team_member INTEGER DEFAULT 0,
            commit_hash TEXT,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    """)

    # 文件团队代码统计表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_team_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            total_lines INTEGER DEFAULT 0,
            team_lines INTEGER DEFAULT 0,
            team_ratio REAL DEFAULT 0.0,
            is_eligible INTEGER DEFAULT 0,
            FOREIGN KEY (commit_id) REFERENCES commits(id),
            FOREIGN KEY (file_id) REFERENCES files(id),
            UNIQUE(commit_id, file_id)
        )
    """)

    # 项目设置表（全局设置；仓库级设置见 repository_settings，migration 0004）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS project_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_commit ON files(commit_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_allocations_commit ON allocations(commit_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_allocations_file ON allocations(file_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_allocations_copyright ON allocations(copyright_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_used_files_commit ON used_files(commit_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_used_files_file ON used_files(file_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_code_ownership_commit ON code_ownership(commit_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_code_ownership_file ON code_ownership(file_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_code_ownership_email ON code_ownership(author_email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_team_stats_commit ON file_team_stats(commit_id)")


def _migration_0002_audit_events(cursor):
    """Append-only audit event log.

    Every significant operation records WHO ran WHAT against WHICH
    commit/config, so the evidence trail can be replayed without parsing
    shell history.  Rows are INSERT-only by design: the module exposes no
    update/delete helper for this table.
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            operation TEXT NOT NULL,
            repository TEXT,
            commit_hash TEXT,
            config_sha256 TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            details TEXT
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_operation ON audit_events(operation)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events(created_at)"
    )


#: Ordered list of (version, name, apply_fn).  Versions must be strictly
#: increasing and gaps are forbidden; add new migrations at the end, never
#: edit an already-released one.
MIGRATIONS = [
    (1, 'baseline_schema', _migration_0001_baseline_schema),
    (2, 'audit_events', _migration_0002_audit_events),
]

#: Highest schema version the current tool knows about.
SCHEMA_VERSION = MIGRATIONS[-1][0]


def _validate_migrations(migrations):
    versions = [v for v, _, _ in migrations]
    if versions != sorted(set(versions)):
        raise ValueError("MIGRATIONS must have unique, strictly increasing versions")


def init_database(migrations=None):
    """Initialize/upgrade the database schema by applying pending migrations.

    Idempotent: safe to call before every operation.  All pending migrations
    run inside a single transaction (get_connection commits on success and
    rolls back on any error), so the schema is never half-upgraded.
    """
    migrations = migrations if migrations is not None else MIGRATIONS
    _validate_migrations(migrations)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(MIGRATION_TABLE_SQL)
        applied = {row[0] for row in
                   cursor.execute("SELECT version FROM schema_migrations")}
        pending = [m for m in migrations if m[0] not in applied]
        if not pending:
            return
        # Explicit transaction: Python sqlite3 does not wrap DDL in an
        # implicit transaction, so without BEGIN a failed migration would
        # leave partially-created tables behind.  get_connection() commits
        # on success and rolls back on any error.
        conn.execute("BEGIN")
        for version, name, apply_fn in pending:
            apply_fn(cursor)
            cursor.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )


def get_schema_version() -> int:
    """Return the highest applied migration version (0 = uninitialized)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cursor.fetchone() is None:
            return 0
        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        row = cursor.fetchone()
        return row[0] or 0


def get_applied_migrations() -> list:
    """Return applied migrations as [(version, name, applied_at), ...]."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cursor.fetchone() is None:
            return []
        cursor.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        )
        return cursor.fetchall()


# Canonical audit event operations.  Kept as constants so tests and the CLI
# cannot drift apart on spelling.
AUDIT_OPERATIONS = (
    'SCAN_COMPLETED',
    'OWNERSHIP_ANALYZED',
    'USED_CODE_MARKED',
    'USED_CODE_IMPORTED',
    'USED_CODE_CLEARED',
    'SPLIT_PLANNED',
    'ALLOCATION_APPLIED',
    'WORKSPACE_BUILT',
    'DUPLICATE_DETECTED',
    'MATERIAL_GENERATED',
    'MATERIAL_AUDITED',
    'PROTECTED_ASSETS_AUDITED',
    'FINAL_AUDIT',
)


def record_audit_event(operation: str, repository: Optional[str] = None,
                       commit_hash: Optional[str] = None,
                       config_sha256: Optional[str] = None,
                       status: str = 'ok',
                       details: Optional[dict] = None) -> int:
    """Append one audit event.  INSERT-only: events are never rewritten.

    Returns the new event id.  ``details`` is stored as JSON text.
    """
    import json as _json
    if details is not None and not isinstance(details, dict):
        raise TypeError("details must be a dict (stored as JSON)")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_events
                (operation, repository, commit_hash, config_sha256, status, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                operation,
                repository,
                commit_hash,
                config_sha256,
                status,
                _json.dumps(details, ensure_ascii=False) if details is not None else None,
            ),
        )
        return cursor.lastrowid


def list_audit_events(limit: int = 50, operation: Optional[str] = None) -> list:
    """Read recent audit events, newest first.  Read-only view."""
    import json as _json
    with get_connection() as conn:
        cursor = conn.cursor()
        if operation:
            cursor.execute(
                """
                SELECT id, created_at, operation, repository, commit_hash,
                       config_sha256, status, details
                FROM audit_events WHERE operation = ?
                ORDER BY id DESC LIMIT ?
                """,
                (operation, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, created_at, operation, repository, commit_hash,
                       config_sha256, status, details
                FROM audit_events ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            )
        rows = cursor.fetchall()
    events = []
    for row in rows:
        events.append({
            'id': row[0],
            'created_at': row[1],
            'operation': row[2],
            'repository': row[3],
            'commit_hash': row[4],
            'config_sha256': row[5],
            'status': row[6],
            'details': _json.loads(row[7]) if row[7] else None,
        })
    return events


def get_or_create_repository(name: str, path: str) -> int:
    """Get or create repository record."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM repositories WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute(
            "INSERT INTO repositories (name, path) VALUES (?, ?)",
            (name, path)
        )
        return cursor.lastrowid


def get_or_create_commit(repository_id: int, commit_hash: str, tag: Optional[str] = None) -> int:
    """Get or create commit record."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM commits WHERE repository_id = ? AND commit_hash = ?",
            (repository_id, commit_hash)
        )
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute(
            "INSERT INTO commits (repository_id, commit_hash, tag) VALUES (?, ?, ?)",
            (repository_id, commit_hash, tag)
        )
        return cursor.lastrowid


def insert_file(commit_id: int, file_path: str, sha256: str, language: str,
                total_lines: int, effective_lines: int, file_size: int) -> int:
    """Insert or update file record (idempotent for re-scans)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO files (commit_id, file_path, sha256, language, total_lines, effective_lines, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(commit_id, file_path) DO UPDATE SET
                sha256 = excluded.sha256,
                language = excluded.language,
                total_lines = excluded.total_lines,
                effective_lines = excluded.effective_lines,
                file_size = excluded.file_size
        """, (commit_id, file_path, sha256, language, total_lines, effective_lines, file_size))
        
        cursor.execute(
            "SELECT id FROM files WHERE commit_id = ? AND file_path = ?",
            (commit_id, file_path)
        )
        return cursor.fetchone()[0]


def allocate_file(commit_id: int, file_id: int, copyright_id: int) -> bool:
    """
    Allocate file to copyright.
    Returns False if file already allocated to another copyright.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT copyright_id FROM allocations
            WHERE commit_id = ? AND file_id = ?
        """, (commit_id, file_id))
        
        existing = cursor.fetchone()
        if existing:
            return False
        
        cursor.execute("""
            INSERT INTO allocations (commit_id, file_id, copyright_id)
            VALUES (?, ?, ?)
        """, (commit_id, file_id, copyright_id))
        return True


def get_allocation(commit_id: int, file_id: int) -> Optional[int]:
    """Get copyright_id for allocated file."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT copyright_id FROM allocations
            WHERE commit_id = ? AND file_id = ?
        """, (commit_id, file_id))
        row = cursor.fetchone()
        return row[0] if row else None


def get_copyright_files(commit_id: int, copyright_id: int) -> list:
    """Get all files allocated to a copyright."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.id, f.file_path, f.sha256, f.language, f.effective_lines
            FROM files f
            JOIN allocations a ON f.id = a.file_id
            WHERE a.commit_id = ? AND a.copyright_id = ?
            ORDER BY f.file_path
        """, (commit_id, copyright_id))
        return cursor.fetchall()


def check_file_allocated(commit_id: int, file_id: int) -> bool:
    """Check if file is already allocated."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM allocations
            WHERE commit_id = ? AND file_id = ?
        """, (commit_id, file_id))
        return cursor.fetchone()[0] > 0


def clear_allocations(commit_id: int):
    """Clear all allocations for a commit (for re-allocation)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM allocations WHERE commit_id = ?", (commit_id,))
        cursor.execute("DELETE FROM copyrights WHERE commit_id = ?", (commit_id,))


def is_file_used(commit_id: int, file_id: int) -> bool:
    """Check if file has been marked as used in a previous copyright."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM used_files
            WHERE commit_id = ? AND file_id = ?
        """, (commit_id, file_id))
        return cursor.fetchone()[0] > 0


def get_used_file_ids(commit_id: int) -> set:
    """Get all file IDs marked as used."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_id FROM used_files WHERE commit_id = ?
        """, (commit_id,))
        return set(row[0] for row in cursor.fetchall())


def is_file_team_eligible(commit_id: int, file_id: int) -> bool:
    """Check if file is eligible (majority team-written)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT is_eligible FROM file_team_stats
            WHERE commit_id = ? AND file_id = ?
        """, (commit_id, file_id))
        row = cursor.fetchone()
        return bool(row[0]) if row else True  # 默认合格（未分析时）


def get_team_eligible_file_ids(commit_id: int) -> Optional[set]:
    """Get all team-eligible file IDs. Returns None if analysis not done."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM file_team_stats WHERE commit_id = ?
        """, (commit_id,))
        if cursor.fetchone()[0] == 0:
            return None  # 未执行分析
        cursor.execute("""
            SELECT file_id FROM file_team_stats
            WHERE commit_id = ? AND is_eligible = 1
        """, (commit_id,))
        return set(row[0] for row in cursor.fetchall())


def get_commit_info(commit_id: int) -> Optional[dict]:
    """Get commit hash / repository info for evidence records."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.commit_hash, r.name, r.path
            FROM commits c
            JOIN repositories r ON r.id = c.repository_id
            WHERE c.id = ?
        """, (commit_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {'commit_hash': row[0], 'repository': row[1], 'repository_path': row[2]}


def get_used_files_map(commit_id: int) -> dict:
    """Map file_id -> list of copyright names that previously used the file."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_id, copyright_name FROM used_files WHERE commit_id = ?
        """, (commit_id,))
        result = {}
        for file_id, copyright_name in cursor.fetchall():
            result.setdefault(file_id, []).append(copyright_name)
        return result


def get_team_stats_map(commit_id: int) -> dict:
    """Map file_id -> team ownership stats. Empty dict if analysis not done."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_id, team_ratio, is_eligible FROM file_team_stats
            WHERE commit_id = ?
        """, (commit_id,))
        return {row[0]: {'team_ratio': row[1], 'is_eligible': bool(row[2])}
                for row in cursor.fetchall()}


def set_project_setting(key: str, value: str):
    """Set a project-level setting."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO project_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (key, value))


def get_project_setting(key: str) -> Optional[str]:
    """Get a project-level setting."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM project_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None


def set_since_date(date_str: str):
    """Set the time cutoff date for code eligibility."""
    set_project_setting('since_date', date_str)


def get_since_date() -> Optional[str]:
    """Get the time cutoff date."""
    return get_project_setting('since_date')


def get_since_timestamp() -> Optional[int]:
    """Get the time cutoff as Unix timestamp. Returns None if not set."""
    date_str = get_since_date()
    if not date_str:
        return None
    from datetime import datetime
    try:
        # Support multiple formats
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d', '%Y-%m'):
            try:
                dt = datetime.strptime(date_str, fmt)
                return int(dt.timestamp())
            except ValueError:
                continue
        return None
    except Exception:
        return None


def get_time_eligible_file_ids(commit_id: int) -> Optional[set]:
    """Get file IDs eligible by time filter. Returns None if no time filter set."""
    since_ts = get_since_timestamp()
    if since_ts is None:
        return None
    
    with get_connection() as conn:
        cursor = conn.cursor()
        # Check if time analysis has been done
        cursor.execute("""
            SELECT COUNT(*) FROM file_team_stats WHERE commit_id = ?
        """, (commit_id,))
        if cursor.fetchone()[0] == 0:
            return None  # Analysis not done yet
        
        # Get files where time-eligible lines >= 50%
        cursor.execute("""
            SELECT file_id FROM file_team_stats
            WHERE commit_id = ? AND is_eligible = 1
        """, (commit_id,))
        return set(row[0] for row in cursor.fetchall())


if __name__ == "__main__":
    init_database()
    print(f"Database initialized at {DB_PATH}")
