#!/usr/bin/env python3
"""Build isolated workspace for each copyright."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import init_database, get_connection
from run_manifest import ManifestCommitMismatch, require_commit_consistency


def get_repository_info(commit_id: int) -> dict:
    """Get repository path and commit hash."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.path, r.name, c.commit_hash
            FROM commits c
            JOIN repositories r ON c.repository_id = r.id
            WHERE c.id = ?
        """, (commit_id,))
        row = cursor.fetchone()
    
    if not row:
        raise ValueError(f"Commit ID {commit_id} not found")
    
    return {
        'repo_path': row[0],
        'repo_name': row[1],
        'commit_hash': row[2],
    }


def get_copyright_info(commit_id: int, copyright_id: str) -> dict:
    """Get copyright metadata."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, version, description
            FROM copyrights
            WHERE commit_id = ? AND copyright_id = ?
        """, (commit_id, copyright_id))
        row = cursor.fetchone()
    
    if not row:
        raise ValueError(f"Copyright '{copyright_id}' not found for commit {commit_id}")
    
    return {
        'db_id': row[0],
        'name': row[1],
        'version': row[2],
        'description': row[3],
    }


def get_allocated_files(commit_id: int, copyright_db_id: int) -> list:
    """Get all files allocated to a copyright."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.id, f.file_path, f.sha256, f.language, f.effective_lines, f.total_lines
            FROM files f
            JOIN allocations a ON f.id = a.file_id
            WHERE a.commit_id = ? AND a.copyright_id = ?
            ORDER BY f.file_path
        """, (commit_id, copyright_db_id))
        files = cursor.fetchall()
    
    return [
        {
            'id': f[0],
            'path': f[1],
            'sha256': f[2],
            'language': f[3],
            'effective_lines': f[4],
            'total_lines': f[5],
        }
        for f in files
    ]


def verify_file_hash(file_path: Path, expected_sha256: str) -> bool:
    """Verify file hasn't been modified since scan."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest() == expected_sha256


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_workspace_path(source_dir: Path, rel_path: str) -> Path:
    """Join ``rel_path`` onto ``source_dir`` refusing traversal/absolute paths.

    Guarantees the resolved destination stays inside ``source_dir`` even for
    hostile path values (``../``, absolute paths).  Symlinked components in
    the destination root are resolved before the containment check.
    """
    rel = Path(rel_path)
    if rel.is_absolute() or '..' in rel.parts or str(rel_path).startswith('\\\\'):
        raise ValueError(f"Unsafe file path rejected: {rel_path!r}")
    root = source_dir.resolve()
    candidate = (source_dir / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path escapes workspace root: {rel_path!r}")
    return candidate


def read_locked_commit_file(repo_path: Path, commit_hash: str, file_path: str) -> bytes:
    """Read file content from the LOCKED commit (never the working tree).

    This keeps workspace builds reproducible: even if HEAD or the working
    tree moved after the scan, the workspace contains exactly the bytes of
    the audited commit.
    """
    import subprocess
    result = subprocess.run(
        ['git', 'show', f'{commit_hash}:{file_path}'],
        cwd=str(repo_path),
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise FileNotFoundError(
            f"Cannot read {file_path} at commit {commit_hash}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def build_workspace(
    commit_id: int,
    copyright_id: str,
    output_base: Path,
    verify_hashes: bool = True,
    verbose: bool = False
) -> dict:
    """
    Build isolated workspace for a copyright.
    
    Creates:
    - workspaces/<copyright_id>/source/ (copied source files)
    - workspaces/<copyright_id>/SOURCE_MANIFEST.json
    - workspaces/<copyright_id>/PROJECT_CONTEXT.md
    """
    # 获取信息
    repo_info = get_repository_info(commit_id)
    cr_info = get_copyright_info(commit_id, copyright_id)
    files = get_allocated_files(commit_id, cr_info['db_id'])

    # Git 真实性: 工作区必须与 run manifest 锁定的 commit 一致（若存在 manifest）
    require_commit_consistency(
        repo_info['commit_hash'],
        context=f"workspace build for {copyright_id}",
    )

    repo_path = Path(repo_info['repo_path'])
    workspace_dir = output_base / copyright_id
    source_dir = workspace_dir / 'source'
    
    # 清理旧工作区
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    
    workspace_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    
    if verbose:
        print(f"Building workspace for {copyright_id}: {cr_info['name']}")
        print(f"  Repository: {repo_path}")
        print(f"  Commit: {repo_info['commit_hash']}")
        print(f"  Files: {len(files)}")
    
    # 复制文件
    manifest_files = []
    errors = []
    
    for file in files:
        # 路径边界: 拒绝任何会逃出 source/ 根目录的目标路径
        try:
            dst_path = safe_workspace_path(source_dir, file['path'])
        except ValueError as exc:
            errors.append({'file': file['path'], 'error': 'unsafe_path',
                           'message': str(exc)})
            continue

        # 内容来源: 锁定 Commit（git show），而非工作区当前状态
        try:
            content = read_locked_commit_file(repo_path, repo_info['commit_hash'], file['path'])
        except (FileNotFoundError, OSError) as exc:
            errors.append({
                'file': file['path'],
                'error': 'git_object_unreadable',
                'message': str(exc),
            })
            continue

        # 验证哈希（对锁定 Commit 读出的字节再次核验，纵深防御）
        if verify_hashes and sha256_bytes(content) != file['sha256']:
            errors.append({
                'file': file['path'],
                'error': 'hash_mismatch',
                'expected': file['sha256'],
            })
            continue

        # 创建目标目录并写入字节（不复制工作区文件，符号链接也被中和为普通文件）
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dst_path, 'wb') as out:
            out.write(content)
        
        manifest_files.append({
            'path': file['path'],
            'sha256': file['sha256'],
            'language': file['language'],
            'effective_lines': file['effective_lines'],
            'total_lines': file['total_lines'],
        })
        
        if verbose and len(manifest_files) % 50 == 0:
            print(f"  Copied {len(manifest_files)} files...")
    
    # 生成 SOURCE_MANIFEST.json
    manifest = {
        'copyrightId': copyright_id,
        'softwareName': cr_info['name'],
        'version': cr_info['version'],
        'description': cr_info['description'],
        'repositoryName': repo_info['repo_name'],
        'repositoryCommit': repo_info['commit_hash'],
        'commitId': commit_id,
        'allowedFiles': manifest_files,
        'totalFiles': len(manifest_files),
        'totalEffectiveLines': sum(f['effective_lines'] for f in manifest_files),
    }
    
    manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    # 生成 PROJECT_CONTEXT.md
    context_md = f"""# {cr_info['name']}

- **版本**: {cr_info['version']}
- **描述**: {cr_info['description']}
- **仓库**: {repo_info['repo_name']}
- **Commit**: {repo_info['commit_hash']}
- **文件数**: {len(manifest_files)}
- **有效代码行数**: {manifest['totalEffectiveLines']}

## 文件清单

| 文件路径 | 语言 | 有效行数 |
|---------|------|---------|
"""
    
    for file in manifest_files:
        context_md += f"| {file['path']} | {file['language']} | {file['effective_lines']} |\n"
    
    context_path = workspace_dir / 'PROJECT_CONTEXT.md'
    with open(context_path, 'w', encoding='utf-8') as f:
        f.write(context_md)
    
    # 生成结果
    result = {
        'copyright_id': copyright_id,
        'workspace_dir': str(workspace_dir),
        'source_dir': str(source_dir),
        'manifest_path': str(manifest_path),
        'total_files': len(manifest_files),
        'total_effective_lines': manifest['totalEffectiveLines'],
        'errors': errors,
    }
    
    if errors:
        result['status'] = 'partial'
        if verbose:
            print(f"\n  ⚠️ {len(errors)} files had errors")
    else:
        result['status'] = 'success'
    
    return result


def main():
    parser = argparse.ArgumentParser(description='Build isolated workspace for copyright')
    parser.add_argument('copyright_id', type=str, help='Copyright ID to build workspace for')
    parser.add_argument('--commit-id', type=int, required=True, help='Commit ID')
    parser.add_argument('--output', type=Path, default=Path('copyright/workspaces'),
                       help='Output base directory')
    parser.add_argument('--no-verify', action='store_true', help='Skip hash verification')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    init_database()
    
    try:
        result = build_workspace(
            commit_id=args.commit_id,
            copyright_id=args.copyright_id,
            output_base=args.output,
            verify_hashes=not args.no_verify,
            verbose=args.verbose,
        )
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    print(f"\nWorkspace built: {result['workspace_dir']}")
    print(f"  Status: {result['status']}")
    print(f"  Files: {result['total_files']}")
    print(f"  Lines: {result['total_effective_lines']}")
    
    if result['errors']:
        print(f"  Errors: {len(result['errors'])}")
        for err in result['errors'][:5]:
            print(f"    - {err['file']}: {err['error']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
