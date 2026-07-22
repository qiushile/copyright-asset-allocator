#!/usr/bin/env python3
"""Scan Git repository and build file inventory."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

# 添加 scripts 目录到 path
sys.path.insert(0, str(Path(__file__).parent))

from database import (
    init_database, get_connection, get_or_create_repository,
    get_or_create_commit, insert_file
)

# 语言扩展名映射
LANGUAGE_MAP = {
    '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
    '.tsx': 'TypeScript', '.jsx': 'JavaScript', '.java': 'Java',
    '.go': 'Go', '.rs': 'Rust', '.cpp': 'C++', '.c': 'C',
    '.h': 'C', '.hpp': 'C++', '.cs': 'C#', '.rb': 'Ruby',
    '.php': 'PHP', '.swift': 'Swift', '.kt': 'Kotlin',
    '.vue': 'Vue', '.html': 'HTML', '.css': 'CSS',
    '.scss': 'SCSS', '.less': 'Less', '.sql': 'SQL',
    '.sh': 'Shell', '.bash': 'Shell', '.yaml': 'YAML',
    '.yml': 'YAML', '.json': 'JSON', '.xml': 'XML',
    '.md': 'Markdown', '.txt': 'Text',
}

# 默认排除目录
DEFAULT_EXCLUDE_DIRS = {
    '.git', 'node_modules', 'vendor', 'dist', 'build', 'target',
    'coverage', '.venv', 'venv', '__pycache__', '.idea', '.vscode',
    '.next', '.nuxt', 'out', '.cache', '.pytest_cache', '.mypy_cache',
}

# 默认排除文件模式
DEFAULT_EXCLUDE_PATTERNS = [
    '*.min.js', '*.min.css', '*.map', '*.lock',
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    '*.pyc', '*.pyo', '*.class', '*.o', '*.so', '*.dylib',
    '*.dll', '*.exe', '*.bin', '*.dat',
    '*.png', '*.jpg', '*.jpeg', '*.gif', '*.svg', '*.ico',
    '*.zip', '*.tar', '*.gz', '*.rar', '*.7z',
    '*.pdf', '*.doc', '*.docx', '*.xls', '*.xlsx',
    '*.log', '*.bak', '*.tmp',
    '.env', '.env.*', '*.key', '*.pem', '*.crt',
]


def run_git_command(cmd: list, cwd: Path) -> str:
    """Run git command and return output."""
    result = subprocess.run(
        ['git'] + cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def get_current_commit(repo_path: Path) -> str:
    """Get current HEAD commit hash."""
    return run_git_command(['rev-parse', 'HEAD'], repo_path)


def get_git_tracked_files(repo_path: Path) -> list:
    """Get all git tracked files."""
    output = run_git_command(['ls-files'], repo_path)
    return [f for f in output.split('\n') if f]


def should_exclude(file_path: str, exclude_dirs: set, exclude_patterns: list) -> bool:
    """Check if file should be excluded."""
    parts = Path(file_path).parts
    
    # 检查目录
    for part in parts:
        if part in exclude_dirs:
            return True
    
    # 检查文件模式
    filename = Path(file_path).name
    for pattern in exclude_patterns:
        if pattern.startswith('*'):
            if filename.endswith(pattern[1:]):
                return True
        elif pattern == filename:
            return True
    
    return False


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA-256 hash of file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def count_lines(file_path: Path) -> tuple:
    """Count total and effective lines."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        total = len(lines)
        effective = sum(1 for line in lines if line.strip())
        
        return total, effective
    except Exception:
        return 0, 0


def detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(ext, 'Unknown')


def validate_repo_path(repo_path: Path):
    """Validate repository path exists and is a git repo."""
    if not repo_path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {repo_path}")
    if not (repo_path / '.git').exists():
        raise ValueError(f"Not a Git repository (no .git found): {repo_path}")


def scan_repository(
    repo_path: Path,
    commit_hash: Optional[str] = None,
    exclude_dirs: Optional[set] = None,
    exclude_patterns: Optional[list] = None,
    verbose: bool = False
) -> dict:
    """
    Scan repository and return file inventory.
    
    Returns:
        dict with keys: repository_id, commit_id, commit_hash, files, excluded
    """
    if exclude_dirs is None:
        exclude_dirs = DEFAULT_EXCLUDE_DIRS
    if exclude_patterns is None:
        exclude_patterns = DEFAULT_EXCLUDE_PATTERNS
    
    # 验证仓库路径
    validate_repo_path(repo_path)
    
    # 初始化数据库
    init_database()
    
    # 获取 commit
    if commit_hash is None:
        commit_hash = get_current_commit(repo_path)
    
    if verbose:
        print(f"Scanning commit: {commit_hash}")
    
    # 获取或创建仓库记录
    repo_name = repo_path.name
    repo_id = get_or_create_repository(repo_name, str(repo_path))
    
    # 获取或创建 commit 记录
    commit_id = get_or_create_commit(repo_id, commit_hash)
    
    # 获取所有 git 跟踪的文件
    tracked_files = get_git_tracked_files(repo_path)
    
    if verbose:
        print(f"Found {len(tracked_files)} tracked files")
    
    # 扫描文件
    files = []
    excluded = []
    
    for file_path in tracked_files:
        full_path = repo_path / file_path
        
        # 检查是否排除
        if should_exclude(file_path, exclude_dirs, exclude_patterns):
            excluded.append({
                'path': file_path,
                'reason': 'exclusion_pattern'
            })
            continue
        
        # 检查文件是否存在
        if not full_path.exists():
            excluded.append({
                'path': file_path,
                'reason': 'file_not_found'
            })
            continue
        
        # 检查是否是文件
        if not full_path.is_file():
            continue
        
        # 计算哈希
        sha256 = calculate_sha256(full_path)
        
        # 计算行数
        total_lines, effective_lines = count_lines(full_path)
        
        # 检测语言
        language = detect_language(file_path)
        
        # 获取文件大小
        file_size = full_path.stat().st_size
        
        # 插入数据库（幂等：重复扫描会更新而非报错）
        file_id = insert_file(
            commit_id=commit_id,
            file_path=file_path,
            sha256=sha256,
            language=language,
            total_lines=total_lines,
            effective_lines=effective_lines,
            file_size=file_size
        )
        
        files.append({
            'id': file_id,
            'path': file_path,
            'sha256': sha256,
            'language': language,
            'total_lines': total_lines,
            'effective_lines': effective_lines,
            'file_size': file_size,
        })
        
        if verbose and len(files) % 100 == 0:
            print(f"  Scanned {len(files)} files...")
    
    if verbose:
        print(f"Scanned {len(files)} files, excluded {len(excluded)} files")
    
    return {
        'repository_id': repo_id,
        'commit_id': commit_id,
        'commit_hash': commit_hash,
        'files': files,
        'excluded': excluded,
    }


def export_inventory(result: dict, output_dir: Path):
    """Export scan results to CSV and JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 导出 CSV
    csv_path = output_dir / 'repository-files.csv'
    with open(csv_path, 'w', encoding='utf-8') as f:
        f.write('id,file_path,sha256,language,total_lines,effective_lines,file_size\n')
        for file in result['files']:
            f.write(f"{file['id']},{file['path']},{file['sha256']},{file['language']},"
                   f"{file['total_lines']},{file['effective_lines']},{file['file_size']}\n")
    
    # 导出 JSON
    json_path = output_dir / 'scan-result.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'repository_id': result['repository_id'],
            'commit_id': result['commit_id'],
            'commit_hash': result['commit_hash'],
            'total_files': len(result['files']),
            'excluded_count': len(result['excluded']),
            'files': result['files'],
            'excluded': result['excluded'],
        }, f, indent=2, ensure_ascii=False)
    
    print(f"Exported to {csv_path}")
    print(f"Exported to {json_path}")


def main():
    parser = argparse.ArgumentParser(description='Scan Git repository for copyright asset allocation')
    parser.add_argument('repo_path', type=Path, help='Path to Git repository')
    parser.add_argument('--commit', type=str, help='Specific commit hash (default: HEAD)')
    parser.add_argument('--output', type=Path, default=Path('copyright/inventory'),
                       help='Output directory for inventory files')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    try:
        validate_repo_path(args.repo_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    result = scan_repository(args.repo_path, args.commit, verbose=args.verbose)
    export_inventory(result, args.output)
    
    print(f"\nScan complete:")
    print(f"  Commit: {result['commit_hash']}")
    print(f"  Files: {len(result['files'])}")
    print(f"  Excluded: {len(result['excluded'])}")


if __name__ == '__main__':
    main()
