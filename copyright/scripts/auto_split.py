#!/usr/bin/env python3
"""Feature 3: Auto-split project code into multiple copyrights.

Algorithm:
1. Collect eligible files (not used, team-eligible)
2. Group files by top-level directory/module
3. Use greedy bin-packing to form copyrights with ≥ min_lines each
4. No overlap between copyrights (enforced by file-level allocation)
5. Generate YAML config for the allocate workflow
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from database import (
    init_database, get_connection,
    get_used_file_ids, get_team_eligible_file_ids
)


# ============================================================
# Module Grouping
# ============================================================

def get_module_key(file_path: str, depth: int = 2) -> str:
    """Extract module key from file path based on directory depth.
    
    Examples (depth=2):
        services/collector/main.py -> services/collector
        libs/protocol-common/utils.py -> libs/protocol-common
        src/core/engine/parser.py -> src/core
    """
    parts = Path(file_path).parts
    if len(parts) <= depth:
        # File is at or near root
        return str(Path(*parts[:-1])) if len(parts) > 1 else "."
    return str(Path(*parts[:depth]))


def group_files_by_module(
    files: List[Dict],
    depth: int = 2
) -> Dict[str, List[Dict]]:
    """Group files by module (directory prefix).
    
    Returns: {module_key: [file_dicts]}
    """
    modules = defaultdict(list)
    for f in files:
        key = get_module_key(f['path'], depth)
        modules[key].append(f)
    return dict(modules)


def compute_module_stats(modules: Dict[str, List[Dict]]) -> List[Dict]:
    """Compute statistics for each module."""
    stats = []
    for module_key, files in modules.items():
        total_lines = sum(f.get('effective_lines', 0) for f in files)
        stats.append({
            'module': module_key,
            'file_count': len(files),
            'total_lines': total_lines,
            'files': files,
        })
    # Sort by total_lines descending for better packing
    stats.sort(key=lambda x: x['total_lines'], reverse=True)
    return stats


# ============================================================
# Splitting Algorithm
# ============================================================

def estimate_copyright_count(total_lines: int, min_lines: int, max_lines: int = 0) -> int:
    """Estimate how many copyrights to create.
    
    Args:
        total_lines: Total effective lines available
        min_lines: Minimum lines per copyright (default 3000)
        max_lines: Maximum lines per copyright (0 = no limit, suggested ~15000)
    """
    if total_lines < min_lines:
        return 1  # Not enough for even one, still create one
    
    if max_lines > 0:
        # Use max_lines to determine upper bound
        n_by_max = math.ceil(total_lines / max_lines)
        n_by_min = total_lines // min_lines
        # Choose a count between these bounds
        return max(1, min(n_by_min, max(n_by_max, n_by_min)))
    
    # Default: aim for ~5000-8000 lines per copyright
    target_lines = 6000
    n = max(1, round(total_lines / target_lines))
    
    # Ensure each copyright can meet minimum
    while n > 1 and total_lines / n < min_lines:
        n -= 1
    
    return n


def greedy_bin_pack(
    module_stats: List[Dict],
    n_copyrights: int,
    min_lines: int = 3000
) -> List[List[Dict]]:
    """Greedy bin-packing: assign modules to copyrights.
    
    Strategy:
    1. Sort modules by size (largest first)
    2. Assign each module to the copyright with fewest lines so far
    3. Post-process: merge underflow bins if needed
    
    Returns: List of bins, each bin is a list of module_stats entries
    """
    # Initialize bins
    bins = [{'modules': [], 'total_lines': 0} for _ in range(n_copyrights)]
    
    # Sort modules largest first
    sorted_modules = sorted(module_stats, key=lambda x: x['total_lines'], reverse=True)
    
    for module in sorted_modules:
        # Find the bin with minimum total lines
        min_bin = min(bins, key=lambda b: b['total_lines'])
        min_bin['modules'].append(module)
        min_bin['total_lines'] += module['total_lines']
    
    # Post-process: merge bins that are below minimum
    final_bins = []
    underflow_bins = []
    
    for b in bins:
        if b['total_lines'] >= min_lines:
            final_bins.append(b)
        else:
            underflow_bins.append(b)
    
    # Redistribute underflow bins' modules to final bins
    if underflow_bins:
        if not final_bins:
            # All bins underflow - merge all into one
            merged = {'modules': [], 'total_lines': 0}
            for b in underflow_bins:
                merged['modules'].extend(b['modules'])
                merged['total_lines'] += b['total_lines']
            final_bins = [merged]
        else:
            # Add underflow modules to the smallest final bin
            for ub in underflow_bins:
                target = min(final_bins, key=lambda b: b['total_lines'])
                target['modules'].extend(ub['modules'])
                target['total_lines'] += ub['total_lines']
    
    # Convert to list of module lists
    return [b['modules'] for b in final_bins]


def try_split_large_modules(
    module_stats: List[Dict],
    n_copyrights: int,
    min_lines: int = 3000
) -> List[List[Dict]]:
    """Try a more aggressive split: split large modules by sub-directory.
    
    If a single module has enough lines, split it into sub-modules.
    """
    expanded_modules = []
    
    for module in module_stats:
        if module['total_lines'] > min_lines * 2 and len(module['files']) > 5:
            # Try splitting by sub-directory (depth + 1)
            sub_groups = defaultdict(list)
            for f in module['files']:
                parts = Path(f['path']).parts
                # Use one more level of depth
                base_parts = Path(module['module']).parts
                if len(parts) > len(base_parts) + 1:
                    sub_key = str(Path(*parts[:len(base_parts) + 1]))
                else:
                    sub_key = module['module']
                sub_groups[sub_key].append(f)
            
            if len(sub_groups) > 1:
                for sub_key, sub_files in sub_groups.items():
                    sub_lines = sum(f.get('effective_lines', 0) for f in sub_files)
                    expanded_modules.append({
                        'module': sub_key,
                        'file_count': len(sub_files),
                        'total_lines': sub_lines,
                        'files': sub_files,
                    })
            else:
                expanded_modules.append(module)
        else:
            expanded_modules.append(module)
    
    expanded_modules.sort(key=lambda x: x['total_lines'], reverse=True)
    return greedy_bin_pack(expanded_modules, n_copyrights, min_lines)


# ============================================================
# Name Generation
# ============================================================

def generate_copyright_name(modules: List[Dict], index: int) -> str:
    """Generate a meaningful copyright name from module names."""
    module_names = [m['module'] for m in modules]
    
    # Try to find common prefix
    if len(module_names) == 1:
        name_base = _humanize_module_name(module_names[0])
    elif len(module_names) <= 3:
        # Use the primary (largest) module name
        primary = max(modules, key=lambda m: m['total_lines'])
        name_base = _humanize_module_name(primary['module'])
    else:
        # Find common directory prefix
        common = _common_prefix(module_names)
        if common and common != '.':
            name_base = _humanize_module_name(common)
        else:
            name_base = f"模块{index + 1}"
    
    return name_base


def _humanize_module_name(module_path: str) -> str:
    """Convert module path to a human-readable name."""
    parts = Path(module_path).parts
    # Skip generic prefixes
    skip = {'src', 'lib', 'libs', 'app', 'pkg', 'internal', 'cmd'}
    meaningful = [p for p in parts if p.lower() not in skip]
    
    if not meaningful:
        meaningful = list(parts)
    
    # Take last 1-2 meaningful parts
    if len(meaningful) >= 2:
        return f"{meaningful[-2]}-{meaningful[-1]}"
    return meaningful[0] if meaningful else module_path


def _common_prefix(paths: List[str]) -> str:
    """Find common directory prefix among paths."""
    if not paths:
        return ""
    
    split_paths = [Path(p).parts for p in paths]
    common = []
    for parts in zip(*split_paths):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break
    
    return str(Path(*common)) if common else ""


# ============================================================
# Main Split Logic
# ============================================================

def auto_split(
    commit_id: int,
    min_lines: int = 3000,
    max_lines: int = 0,
    n_copyrights: int = 0,
    module_depth: int = 2,
    name_prefix: str = "",
    name_template: str = "",
    verbose: bool = False,
) -> Dict:
    """Auto-split eligible files into multiple copyrights.
    
    Args:
        commit_id: Database commit ID
        min_lines: Minimum effective lines per copyright (default 3000)
        max_lines: Maximum lines per copyright (0 = auto)
        n_copyrights: Force number of copyrights (0 = auto-determine)
        module_depth: Directory depth for module grouping
        name_prefix: Prefix for copyright names
        name_template: Template for names, e.g. "{prefix}系统V{index}"
        verbose: Print progress
    
    Returns:
        {
            'copyrights': [{id, name, version, description, include_paths, files, total_lines}],
            'total_files': int,
            'total_lines': int,
            'skipped_used': int,
            'skipped_non_team': int,
        }
    """
    # 1. Get all files for this commit
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, file_path, effective_lines, language
            FROM files WHERE commit_id = ?
            ORDER BY file_path
        """, (commit_id,))
        all_files = [
            {'id': r[0], 'path': r[1], 'effective_lines': r[2], 'language': r[3]}
            for r in cursor.fetchall()
        ]
    
    if verbose:
        print(f"Total files in commit: {len(all_files)}")
    
    # 2. Filter: exclude used files
    used_ids = get_used_file_ids(commit_id)
    eligible_files = [f for f in all_files if f['id'] not in used_ids]
    skipped_used = len(all_files) - len(eligible_files)
    
    if verbose:
        print(f"After excluding used files: {len(eligible_files)} (skipped {skipped_used})")
    
    # 3. Filter: team eligibility
    team_eligible_ids = get_team_eligible_file_ids(commit_id)
    skipped_non_team = 0
    if team_eligible_ids is not None:
        before = len(eligible_files)
        eligible_files = [f for f in eligible_files if f['id'] in team_eligible_ids]
        skipped_non_team = before - len(eligible_files)
        if verbose:
            print(f"After team eligibility filter: {len(eligible_files)} (skipped {skipped_non_team})")
    
    if not eligible_files:
        return {
            'copyrights': [],
            'total_files': 0,
            'total_lines': 0,
            'skipped_used': skipped_used,
            'skipped_non_team': skipped_non_team,
            'error': 'No eligible files available for splitting.',
        }
    
    # 4. Compute total lines
    total_lines = sum(f['effective_lines'] for f in eligible_files)
    
    if verbose:
        print(f"Total effective lines: {total_lines}")
    
    # 5. Determine number of copyrights
    if n_copyrights > 0:
        n = n_copyrights
    else:
        n = estimate_copyright_count(total_lines, min_lines, max_lines)
    
    if verbose:
        print(f"Target copyrights: {n}")
    
    # 6. Group by module
    modules = group_files_by_module(eligible_files, depth=module_depth)
    module_stats = compute_module_stats(modules)
    
    if verbose:
        print(f"Modules found: {len(module_stats)}")
        for ms in module_stats[:10]:
            print(f"  {ms['module']}: {ms['file_count']} files, {ms['total_lines']} lines")
        if len(module_stats) > 10:
            print(f"  ... and {len(module_stats) - 10} more")
    
    # 7. Bin-pack
    if n == 1:
        bins = [module_stats]
    else:
        # Try aggressive split first if modules are large
        bins = try_split_large_modules(module_stats, n, min_lines)
        
        # If we got fewer bins than requested, try standard packing
        if len(bins) < n and len(module_stats) >= n:
            bins = greedy_bin_pack(module_stats, n, min_lines)
    
    if verbose:
        print(f"Actual copyrights generated: {len(bins)}")
    
    # 8. Build copyright definitions
    copyrights = []
    for i, bin_modules in enumerate(bins):
        cr_id = f"auto-{i + 1:02d}"
        
        # Generate name
        if name_template:
            cr_name = name_template.format(
                prefix=name_prefix or "软件",
                index=i + 1,
                module=generate_copyright_name(bin_modules, i),
            )
        else:
            base_name = generate_copyright_name(bin_modules, i)
            prefix = f"{name_prefix}" if name_prefix else ""
            cr_name = f"{prefix}{base_name}系统" if prefix else f"{base_name}系统"
        
        # Collect include_paths and files
        include_paths = set()
        cr_files = []
        cr_total_lines = 0
        
        for module in bin_modules:
            include_paths.add(f"{module['module']}/**")
            for f in module['files']:
                cr_files.append(f)
                cr_total_lines += f['effective_lines']
        
        copyrights.append({
            'id': cr_id,
            'name': cr_name,
            'version': 'V1.0',
            # 自动拆分只能确定代码范围，不能代替申请人编写真实的主要功能说明。
            'description': '',
            'description_status': 'required_manual_input_500_to_1300_characters',
            'include_paths': sorted(include_paths),
            'exclude_paths': [],
            'files': cr_files,
            'total_lines': cr_total_lines,
            'file_count': len(cr_files),
        })
        
        if verbose:
            print(f"\n  {cr_id} ({cr_name}):")
            print(f"    Files: {len(cr_files)}, Lines: {cr_total_lines}")
            print(f"    Paths: {', '.join(sorted(include_paths)[:5])}")
    
    return {
        'copyrights': copyrights,
        'total_files': len(eligible_files),
        'total_lines': total_lines,
        'skipped_used': skipped_used,
        'skipped_non_team': skipped_non_team,
    }


# ============================================================
# Config Generation
# ============================================================

def generate_config_yaml(
    split_result: Dict,
    repo_name: str = "my-project",
    output_path: Optional[Path] = None,
) -> str:
    """Generate copyrights.yaml from split result."""
    config = {
        'snapshot': {
            'repository': repo_name,
            'created_at': '2026-07-22',
            'auto_generated': True,
        },
        'policies': {
            'path_overlap': 'forbidden',
            'exact_content_overlap': 'forbidden',
            'copied_code_detection': 'enabled',
            'unallocated_file_access': 'forbidden',
            'shared_file_policy': 'excluded',
            'generated_code_policy': 'excluded',
            'third_party_code_policy': 'excluded',
        },
        'copyrights': [],
    }
    
    for cr in split_result['copyrights']:
        config['copyrights'].append({
            'id': cr['id'],
            'name': cr['name'],
            'version': cr['version'],
            'description': cr['description'],
            'description_status': cr['description_status'],
            'include_paths': cr['include_paths'],
            'exclude_paths': cr['exclude_paths'],
        })
    
    yaml_str = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_str, encoding='utf-8')
    
    return yaml_str


def export_split_report(split_result: Dict, output_path: Path):
    """Export detailed split report as JSON."""
    report = {
        'total_files': split_result['total_files'],
        'total_lines': split_result['total_lines'],
        'skipped_used': split_result['skipped_used'],
        'skipped_non_team': split_result['skipped_non_team'],
        'copyright_count': len(split_result['copyrights']),
        'copyrights': [],
    }
    
    for cr in split_result['copyrights']:
        report['copyrights'].append({
            'id': cr['id'],
            'name': cr['name'],
            'file_count': cr['file_count'],
            'total_lines': cr['total_lines'],
            'include_paths': cr['include_paths'],
            'files': [f['path'] for f in cr['files']],
        })
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Auto-split project code into multiple copyrights'
    )
    parser.add_argument('--commit-id', type=int, required=True,
                       help='Database commit ID')
    parser.add_argument('--min-lines', type=int, default=3000,
                       help='Minimum effective lines per copyright (default: 3000)')
    parser.add_argument('--max-lines', type=int, default=0,
                       help='Maximum lines per copyright (0 = auto)')
    parser.add_argument('--count', type=int, default=0,
                       help='Force number of copyrights (0 = auto)')
    parser.add_argument('--module-depth', type=int, default=2,
                       help='Directory depth for module grouping (default: 2)')
    parser.add_argument('--name-prefix', default='',
                       help='Prefix for copyright names')
    parser.add_argument('--name-template', default='',
                       help='Name template, e.g. "{prefix}{module}系统V{index}"')
    parser.add_argument('--repo-name', default='my-project',
                       help='Repository name for config')
    parser.add_argument('--output-config', type=Path,
                       default=Path('copyright/config/copyrights-auto.yaml'),
                       help='Output path for generated YAML config')
    parser.add_argument('--output-report', type=Path,
                       default=Path('copyright/inventory/auto-split-report.json'),
                       help='Output path for split report JSON')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    init_database()
    
    result = auto_split(
        commit_id=args.commit_id,
        min_lines=args.min_lines,
        max_lines=args.max_lines,
        n_copyrights=args.count,
        module_depth=args.module_depth,
        name_prefix=args.name_prefix,
        name_template=args.name_template,
        verbose=args.verbose,
    )
    
    if result.get('error'):
        print(f"Error: {result['error']}")
        sys.exit(1)
    
    # Generate config
    yaml_str = generate_config_yaml(result, args.repo_name, args.output_config)
    
    # Export report
    export_split_report(result, args.output_report)
    
    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Auto-Split Complete")
    print(f"{'=' * 60}")
    print(f"  Eligible files: {result['total_files']}")
    print(f"  Total lines: {result['total_lines']}")
    print(f"  Skipped (used): {result['skipped_used']}")
    print(f"  Skipped (non-team): {result['skipped_non_team']}")
    print(f"  Copyrights generated: {len(result['copyrights'])}")
    print()
    
    for cr in result['copyrights']:
        status = "✓" if cr['total_lines'] >= args.min_lines else "⚠"
        print(f"  {status} {cr['id']} ({cr['name']})")
        print(f"      {cr['file_count']} files, {cr['total_lines']} lines")
    
    print(f"\n  Config: {args.output_config}")
    print(f"  Report: {args.output_report}")
    print(f"\n  Next: python copyright_cli.py allocate --config {args.output_config}")


if __name__ == '__main__':
    main()
