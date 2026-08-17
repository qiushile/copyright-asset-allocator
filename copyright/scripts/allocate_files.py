#!/usr/bin/env python3
"""Allocate files to copyrights based on configuration.

Integrates with Feature 1 (used code markers) and Feature 2 (team ownership)
to automatically skip files that are:
- Already used in a previous copyright application
- Not primarily written by team members (for open-source based projects)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from database import (
    init_database, get_connection, allocate_file,
    check_file_allocated, get_allocation, clear_allocations,
    get_used_file_ids, get_team_eligible_file_ids
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


class ConfigError(ValueError):
    """Raised when copyrights.yaml is structurally invalid."""


def validate_copyrights_config(config: dict) -> list:
    """
    Validate the loaded copyrights.yaml structure.

    Raises ConfigError with an actionable message, e.g.
    ``copyrights[1].version is required``.  Returns the copyright list.
    """
    if not isinstance(config, dict):
        raise ConfigError("copyrights.yaml: top level must be a mapping")
    copyrights = config.get('copyrights')
    if not isinstance(copyrights, list) or not copyrights:
        raise ConfigError("copyrights.yaml: 'copyrights' list is required and must be non-empty")

    seen_ids = {}
    for index, cr in enumerate(copyrights):
        if not isinstance(cr, dict):
            raise ConfigError(f"copyrights[{index}] must be a mapping")
        for key in ('id', 'name', 'version'):
            if not cr.get(key):
                raise ConfigError(f"copyrights[{index}].{key} is required")
        include_paths = cr.get('include_paths', [])
        if not isinstance(include_paths, list) or not include_paths:
            raise ConfigError(
                f"copyrights[{index}].include_paths is required and must be a non-empty list"
            )
        for key in ('include_paths', 'exclude_paths'):
            patterns = cr.get(key, [])
            if not isinstance(patterns, list) or not all(isinstance(x, str) for x in patterns):
                raise ConfigError(f"copyrights[{index}].{key} must be a list of glob strings")
        cr_id = cr['id']
        if cr_id in seen_ids:
            raise ConfigError(
                f"copyrights[{index}].id '{cr_id}' duplicates copyrights[{seen_ids[cr_id]}].id"
            )
        seen_ids[cr_id] = index
    return copyrights


def match_copyrights(file_path: str, copyrights_config: list) -> dict:
    """
    Compute the FULL set of copyrights whose rules match ``file_path``.

    Returns a decision record:

        {
            'matched_rules': [...],    # copyrights whose include patterns match
            'excluded_rules': [...],   # copyrights whose exclude patterns match
            'decision': cr_id | None,
            'status': 'allocated' | 'unallocated' | 'ambiguous',
        }

    A file matched by more than one copyright is AMBIGUOUS.  YAML ordering is
    never used as a tie-breaker (no implicit priority).
    """
    matched = []
    excluded = []
    for cr_config in copyrights_config:
        cr_id = cr_config['id']
        include_paths = cr_config.get('include_paths', [])
        exclude_paths = cr_config.get('exclude_paths', [])

        if any(match_path(file_path, pattern) for pattern in exclude_paths):
            excluded.append(cr_id)
            continue
        if any(match_path(file_path, pattern) for pattern in include_paths):
            matched.append(cr_id)

    if len(matched) == 1:
        return {'matched_rules': matched, 'excluded_rules': excluded,
                'decision': matched[0], 'status': 'allocated'}
    if len(matched) > 1:
        return {'matched_rules': matched, 'excluded_rules': excluded,
                'decision': None, 'status': 'ambiguous'}
    return {'matched_rules': matched, 'excluded_rules': excluded,
            'decision': None, 'status': 'unallocated'}


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
    force: bool = False,
    skip_used: bool = True,
    skip_non_team: bool = True,
) -> dict:
    """
    Allocate files to copyrights based on configuration.
    
    Args:
        commit_id: Database commit ID
        files: List of file dicts from scan result
        copyrights_config: List of copyright configs from YAML
        verbose: Print progress
        force: If True, clear existing allocations first (re-allocation)
        skip_used: If True, skip files marked as used in previous copyrights
        skip_non_team: If True, skip files not primarily written by team
    
    Returns:
        dict with allocation results, ambiguous files and conflicts

    Raises:
        ConfigError: if copyrights_config is structurally invalid.
    """
    validate_copyrights_config({'copyrights': copyrights_config})

    results = {
        'allocations': {},
        'unallocated': [],
        'ambiguous': [],
        'conflicts': [],
        'excluded': [],
        'skipped_used': [],
        'skipped_non_team': [],
        'reallocated': False,
    }
    
    # 如果 force 模式，清除已有分配
    if force:
        clear_allocations(commit_id)
        results['reallocated'] = True
        if verbose:
            print("Cleared existing allocations for re-allocation")
    
    # 获取已使用文件集合（Feature 1）
    used_file_ids = set()
    if skip_used:
        used_file_ids = get_used_file_ids(commit_id)
        if verbose and used_file_ids:
            print(f"Excluding {len(used_file_ids)} previously used files")
    
    # 获取团队合格文件集合（Feature 2）
    team_eligible_ids = None
    if skip_non_team:
        team_eligible_ids = get_team_eligible_file_ids(commit_id)
        if team_eligible_ids is not None and verbose:
            print(f"Team eligibility active: {len(team_eligible_ids)} eligible files")
    
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
        
        # Feature 1: 跳过已使用文件
        if skip_used and file_id in used_file_ids:
            results['skipped_used'].append({
                'file': file_path,
                'file_id': file_id,
                'reason': 'used_in_previous_copyright',
            })
            if verbose:
                print(f"  ⊘ Skipped (used): {file_path}")
            continue
        
        # Feature 2: 跳过非团队文件
        if team_eligible_ids is not None and file_id not in team_eligible_ids:
            results['skipped_non_team'].append({
                'file': file_path,
                'file_id': file_id,
                'reason': 'not_team_eligible',
            })
            if verbose:
                print(f"  ⊘ Skipped (non-team): {file_path}")
            continue
        
        # 检查是否已分配
        if check_file_allocated(commit_id, file_id):
            existing_cr = get_allocation(commit_id, file_id)
            results['conflicts'].append({
                'file': file_path,
                'file_id': file_id,
                'reason': f'already_allocated_to_copyright_id_{existing_cr}'
            })
            continue
        
        # 计算完整匹配集合：0 -> 未分配，1 -> 分配，>1 -> 歧义（禁止按 YAML 顺序静默选择）
        match = match_copyrights(file_path, copyrights_config)

        if match['status'] == 'ambiguous':
            results['ambiguous'].append({
                'file': file_path,
                'file_id': file_id,
                'effective_lines': file.get('effective_lines', 0),
                'matched_copyrights': match['matched_rules'],
                'reason': 'multiple_copyrights_match',
            })
            if verbose:
                print(f"  ⚠ AMBIGUOUS {file_path} matches: {', '.join(match['matched_rules'])}")
            continue

        if match['status'] == 'unallocated':
            results['unallocated'].append({
                'file': file_path,
                'file_id': file_id,
                'effective_lines': file.get('effective_lines', 0),
            })
            continue

        cr_id = match['decision']
        db_id = copyright_db_ids[cr_id]
        if allocate_file(commit_id, file_id, db_id):
            results['allocations'][cr_id].append(file)
            if verbose:
                print(f"  ✓ {file_path} -> {cr_id}")
        else:
            results['conflicts'].append({
                'file': file_path,
                'file_id': file_id,
                'reason': f'conflict_allocating_to_{cr_id}'
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
    
    # 生成歧义文件报告（多个软著同时匹配，禁止静默选择）
    if results.get('ambiguous'):
        ambiguous_path = output_dir / 'ambiguous-files.json'
        with open(ambiguous_path, 'w', encoding='utf-8') as f:
            json.dump(results['ambiguous'], f, indent=2, ensure_ascii=False)

    # 生成冲突报告
    if results['conflicts']:
        conflict_path = output_dir / 'allocation-conflicts.json'
        with open(conflict_path, 'w', encoding='utf-8') as f:
            json.dump(results['conflicts'], f, indent=2, ensure_ascii=False)
    
    # 生成跳过文件报告（Feature 1 & 2）
    skipped = results.get('skipped_used', []) + results.get('skipped_non_team', [])
    if skipped:
        skipped_path = output_dir / 'skipped-files.json'
        with open(skipped_path, 'w', encoding='utf-8') as f:
            json.dump(skipped, f, indent=2, ensure_ascii=False)
    
    # 生成汇总报告
    summary_path = output_dir / 'allocation-summary.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        summary = {
            'total_copyrights': len(copyrights_config),
            'total_allocated': sum(len(files) for files in results['allocations'].values()),
            'total_unallocated': len(results['unallocated']),
            'total_ambiguous': len(results.get('ambiguous', [])),
            'total_conflicts': len(results['conflicts']),
            'total_skipped_used': len(results.get('skipped_used', [])),
            'total_skipped_non_team': len(results.get('skipped_non_team', [])),
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
    parser.add_argument('--no-skip-used', action='store_true',
                       help='Do NOT skip files marked as used in previous copyrights')
    parser.add_argument('--no-skip-non-team', action='store_true',
                       help='Do NOT skip files not written by team members')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    # 加载配置
    if not args.config.exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)
    
    config = load_config(args.config)
    try:
        validate_copyrights_config(config)
    except ConfigError as exc:
        print(f"Error: invalid config {args.config}: {exc}")
        sys.exit(1)

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
    results = allocate_files_from_config(
        commit_id=commit_id,
        files=files,
        copyrights_config=copyrights_config,
        verbose=args.verbose,
        force=args.force,
        skip_used=not args.no_skip_used,
        skip_non_team=not args.no_skip_non_team,
    )
    
    # 导出结果
    export_allocations(results, args.output, copyrights_config)
    
    # 打印汇总
    print(f"\nAllocation complete:")
    for cr_id, files in results['allocations'].items():
        cr_name = next(c['name'] for c in copyrights_config if c['id'] == cr_id)
        total_lines = sum(f['effective_lines'] for f in files)
        print(f"  {cr_id} ({cr_name}): {len(files)} files, {total_lines} lines")
    
    if results.get('skipped_used'):
        print(f"\n  Skipped (used): {len(results['skipped_used'])} files")
    
    if results.get('skipped_non_team'):
        print(f"\n  Skipped (non-team): {len(results['skipped_non_team'])} files")
    
    if results['unallocated']:
        print(f"\n  Unallocated: {len(results['unallocated'])} files")
        print(f"  See: {args.output}/unallocated-files.json")
    
    if results['conflicts']:
        print(f"\n  Conflicts: {len(results['conflicts'])} files")
        print(f"  See: {args.output}/allocation-conflicts.json")

    if results.get('ambiguous'):
        print(f"\n  ERROR: {len(results['ambiguous'])} files matched more than one copyright.")
        print(f"  Ambiguous files were NOT allocated; YAML ordering is never used as a tie-breaker.")
        print(f"  Narrow include_paths/exclude_paths so each file matches exactly one copyright.")
        print(f"  See: {args.output}/ambiguous-files.json")
        sys.exit(1)


if __name__ == '__main__':
    main()
