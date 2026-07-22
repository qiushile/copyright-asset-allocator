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


def init_database():
    """Initialize database schema."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
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
        
        # 项目设置表（存储时间过滤等全局配置）
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
