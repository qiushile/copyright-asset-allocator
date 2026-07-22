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
        
        # 索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_commit ON files(commit_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_allocations_commit ON allocations(commit_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_allocations_file ON allocations(file_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_allocations_copyright ON allocations(copyright_id)")


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
        # 使用 INSERT OR REPLACE 支持重复扫描
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
        
        # 获取 file_id（可能是已有的或新插入的）
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
        
        # 检查是否已分配
        cursor.execute("""
            SELECT copyright_id FROM allocations
            WHERE commit_id = ? AND file_id = ?
        """, (commit_id, file_id))
        
        existing = cursor.fetchone()
        if existing:
            return False
        
        # 插入分配记录
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


if __name__ == "__main__":
    init_database()
    print(f"Database initialized at {DB_PATH}")
