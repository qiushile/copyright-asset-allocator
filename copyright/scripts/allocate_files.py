#!/usr/bin/env python3
"""Allocate files to copyrights based on configuration."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from database import (
    init_database, get_connection, allocate_file,
    check_file_allocated, get_allocation, clear_allocations
)


def load_config(config_path: Path) -> dict:
    """Load copyrights.yaml configuration."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def match_path(file_path: str, pattern: str) -> bool:
    """
    Match file path against glob pattern.
    Supports: ** (any depth), * (single level), exact match
    """
    import fnmatch
    
    # 处理 ** 通配符
    if '**' in pattern:
        parts = pattern.split('**')
        if len(parts) == 2:
            prefix, suffix = parts
            prefix = prefix.rstrip('/')
            suffix = suffix.lstrip('/')
            
            # 检查前缀
            if prefix and not file_path.startswith(prefix + '/') and file_path != prefix:
                return False
            
            # 检查后缀
            if suffix:
                return fnmatch.fnmatch(file_path, f"*{suffix}")
            return True
    
    # 简单通配符匹配
    return fnmatch.fnmatch(file_path, pattern)


def should_include_file(file_path: str, include_paths: list, exclude_paths: list) -> bool:
    """Check if file should be included based on include/exclude patterns."""
    # 先检查排除
    for pattern in exclude_paths:
        if match_path(file_path, pattern):
            return False
    
    # 再检查包含
    for pattern in include_paths:
        if match_path(file_path, pattern):
            return True
    
    return False


def create_copyright(commit_id: int, copyright_id: str, name: str, version: str, description: str) -> int:
    """Create copyright record in database (idempotent)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 先检查是否已存在
        cursor.execute("""
            SELECT id FROM copyrights
            WHERE commit_id = ? AND copyright_id = ?
        """, (commit_id, copyright_id))
        row = cursor.fetchone()
        if row:
            # 更新已有记录
            cursor.execute("""
                UPDATE copyrights SET name = ?, version = ?, description = ?
                WHERE commit_id = ? AND copyright_id = ?
            """, (name, version, description, commit_id, copyright_id))
            return row[0]
        
        cursor.execute("""
            INSERT INTO copyrights (commit_id, copyright_id, name, version, description)
            VALUES (?, ?, ?, ?, ?)
        """, (commit_id, copyright_id, name, version, description))
        return cursor.lastrowid


def get_copyright_db_id(commit_id: int, copyright_id: str) -> Optional[int]:
    """Get database ID for copyright."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM copyrights
            WHERE commit_id = ? AND copyright_id = ?
        """, (commit_id, copyright_id))
        row = cursor.fetchone()
        return row[0] if row else None


def allocate_files_from_config(
    commit_id: int,
    files: list,
    copyrights_config: list,
    verbose: bool = False,
    force: bool = False
) -> dict:
    """
    Allocate files to copyrights based on configuration.
    
    Args:
        force: If True, clear existing allocations first (re-allocation).
    
    Returns:
        dict with allocation results and conflicts
    """
    results = {
        'allocations': {},
        'unallocated': [],
        'conflicts': [],
        'excluded': [],
        'reallocated': False,
    }
    
    # 如果 force 模式，清除已有分配
    if force:
        clear_allocations(commit_id)
        results['reallocated'] = True
        if verbose:
            print("Cleared existing allocations for re-allocation")
    
    # 为每个软著创建记录
    copyright_db_ids = {}
    for cr_config in copyrights_config:
        cr_id = cr_config['id']
        db_id = create_copyright(
            commit_id=commit_id,
            copyright_id=cr_id,
            name=cr_config['name'],
            version=cr_config['version'],
            description=cr_config.get('description', '')
        )
        copyright_db_ids[cr_id] = db_id
        results['allocations'][cr_id] = []
    
    # 分配文件
    for file in files:
        file_path = file['path']
        file_id = file['id']
        
        # 检查是否已分配
        if check_file_allocated(commit_id, file_id):
            existing_cr = get_allocation(commit_id, file_id)
            results['conflicts'].append({
                'file': file_path,
                'file_id': file_id,
                'reason': f'already_allocated_to_copyright_id_{existing_cr}'
            })
            continue
        
        # 尝试匹配每个软著
        allocated = False
        for cr_config in copyrights_config:
            cr_id = cr_config['id']
            include_paths = cr_config.get('include_paths', [])
            exclude_paths = cr_config.get('exclude_paths', [])
            
            if should_include_file(file_path, include_paths, exclude_paths):
                # 尝试分配
                success = allocate_file(commit_id, file_id, copyright_db_ids[cr_id])
                
                if success:
                    results['allocations'][cr_id].append({
                        'file_id': file_id,
                        'path': file_path,
                        'sha256': file['sha256'],
                        'language': file['language'],
                        'effective_lines': file['effective_lines'],
                    })
                    allocated = True
                    if verbose:
                        print(f"  {file_path} -> {cr_id}")
                else:
                    results['conflicts'].append({
                        'file': file_path,
                        'file_id': file_id,
                        'reason': f'allocation_failed_for_{cr_id}'
                    })
                break
        
        if not allocated and not any(c['file'] == file_path for c in results['conflicts']):
            results['unallocated'].append({
                'file_id': file_id,
                'path': file_path,
                'language': file['language'],
                'effective_lines': file['effective_lines'],
            })
    
    return results


def export_allocations(results: dict, output_dir: Path, copyrights_config: list):
    """Export allocation results to files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 为每个软著生成文件清单
    for cr_config in copyrights_config:
        cr_id = cr_config['id']
        files = results['allocations'].get(cr_id, [])
        
        # 生成 txt 文件清单
        txt_path = output_dir / f'{cr_id}-files.txt'
        with open(txt_path, 'w', encoding='utf-8') as f:
            for file in files:
                f.write(f"{file['path']}\n")
        
        # 生成 JSON 清单
        json_path = output_dir / f'{cr_id}-files.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({
                'copyright_id': cr_id,
                'copyright_name': cr_config['name'],
                'version': cr_config['version'],
                'file_count': len(files),
                'total_effective_lines': sum(f['effective_lines'] for f in files),
                'files': files,
            }, f, indent=2, ensure_ascii=False)
    
    # 生成未分配文件清单
    if results['unallocated']:
        unallocated_path = output_dir / 'unallocated-files.json'
        with open(unallocated_path, 'w', encoding='utf-8') as f:
            json.dump(results['unallocated'], f, indent=2, ensure_ascii=False)
    
    # 生成冲突报告
    if results['conflicts']:
        conflict_path = output_dir / 'allocation-conflicts.json'
        with open(conflict_path, 'w', encoding='utf-8') as f:
            json.dump(results['conflicts'], f, indent=2, ensure_ascii=False)
    
    # 生成汇总报告
    summary_path = output_dir / 'allocation-summary.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        summary = {
            'total_copyrights': len(copyrights_config),
            'total_allocated': sum(len(files) for files in results['allocations'].values()),
            'total_unallocated': len(results['unallocated']),
            'total_conflicts': len(results['conflicts']),
            'reallocated': results.get('reallocated', False),
            'by_copyright': {
                cr_id: {
                    'name': next(c['name'] for c in copyrights_config if c['id'] == cr_id),
                    'file_count': len(files),
                    'total_lines': sum(f['effective_lines'] for f in files),
                }
                for cr_id, files in results['allocations'].items()
            }
        }
        json.dump(summary, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description='Allocate files to copyrights')
    parser.add_argument('--config', type=Path, default=Path('copyright/config/copyrights.yaml'),
                       help='Path to copyrights.yaml')
    parser.add_argument('--scan-result', type=Path, default=Path('copyright/inventory/scan-result.json'),
                       help='Path to scan result JSON')
    parser.add_argument('--output', type=Path, default=Path('copyright/allocations'),
                       help='Output directory for allocation files')
    parser.add_argument('--force', action='store_true',
                       help='Clear existing allocations and re-allocate')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    # 加载配置
    if not args.config.exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)
    
    config = load_config(args.config)
    
    # 加载扫描结果
    if not args.scan_result.exists():
        print(f"Error: Scan result not found: {args.scan_result}")
        print("Run scan_repository.py first")
        sys.exit(1)
    
    with open(args.scan_result, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    commit_id = scan_result['commit_id']
    files = scan_result['files']
    
    if args.verbose:
        print(f"Loaded {len(files)} files from scan result")
        print(f"Commit ID: {commit_id}")
    
    # 执行分配
    copyrights_config = config['copyrights']
    results = allocate_files_from_config(commit_id, files, copyrights_config, args.verbose, args.force)
    
    # 导出结果
    export_allocations(results, args.output, copyrights_config)
    
    # 打印汇总
    print(f"\nAllocation complete:")
    for cr_id, files in results['allocations'].items():
        cr_name = next(c['name'] for c in copyrights_config if c['id'] == cr_id)
        total_lines = sum(f['effective_lines'] for f in files)
        print(f"  {cr_id} ({cr_name}): {len(files)} files, {total_lines} lines")
    
    if results['unallocated']:
        print(f"\n  Unallocated: {len(results['unallocated'])} files")
        print(f"  See: {args.output}/unallocated-files.json")
    
    if results['conflicts']:
        print(f"\n  Conflicts: {len(results['conflicts'])} files")
        print(f"  See: {args.output}/allocation-conflicts.json")


if __name__ == '__main__':
    main()
