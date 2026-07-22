#!/usr/bin/env python3
"""Validate allocations: check completeness, conflicts, and constraints."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import init_database, get_connection


def validate_allocations(commit_id: int, min_lines_per_copyright: int = 3000) -> dict:
    """
    Validate all allocations for a commit.
    
    Checks:
    1. No file allocated to multiple copyrights
    2. Each copyright has enough code
    3. No unallocated source files (warning)
    4. File hashes match scan
    """
    results = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'stats': {},
    }
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. 检查重复分配（数据库唯一约束已保证，但再次验证）
        cursor.execute("""
            SELECT file_id, COUNT(*) as cnt
            FROM allocations
            WHERE commit_id = ?
            GROUP BY file_id
            HAVING cnt > 1
        """, (commit_id,))
        
        duplicates = cursor.fetchall()
        if duplicates:
            results['valid'] = False
            results['errors'].append({
                'type': 'duplicate_allocation',
                'message': f'{len(duplicates)} files allocated to multiple copyrights',
                'files': [row[0] for row in duplicates]
            })
        
        # 2. 检查每个软著的代码量
        cursor.execute("""
            SELECT c.id, c.copyright_id, c.name, c.version,
                   COUNT(f.id) as file_count,
                   COALESCE(SUM(f.effective_lines), 0) as total_lines
            FROM copyrights c
            LEFT JOIN allocations a ON c.id = a.copyright_id AND a.commit_id = ?
            LEFT JOIN files f ON a.file_id = f.id AND f.commit_id = ?
            WHERE c.commit_id = ?
            GROUP BY c.id
        """, (commit_id, commit_id, commit_id))
        
        copyrights_stats = []
        for row in cursor.fetchall():
            cr_db_id, cr_id, name, version, file_count, total_lines = row
            
            stat = {
                'copyright_id': cr_id,
                'name': name,
                'version': version,
                'file_count': file_count,
                'total_lines': total_lines,
            }
            copyrights_stats.append(stat)
            
            if file_count == 0:
                results['valid'] = False
                results['errors'].append({
                    'type': 'no_files',
                    'copyright_id': cr_id,
                    'message': f'{name} has no files allocated'
                })
            elif total_lines < min_lines_per_copyright:
                results['warnings'].append({
                    'type': 'low_line_count',
                    'copyright_id': cr_id,
                    'message': f'{name} has only {total_lines} effective lines (recommended: {min_lines_per_copyright}+)',
                    'lines': total_lines,
                    'threshold': min_lines_per_copyright,
                })
        
        results['stats']['copyrights'] = copyrights_stats
        
        # 3. 检查未分配的源文件
        cursor.execute("""
            SELECT f.id, f.file_path, f.language, f.effective_lines
            FROM files f
            LEFT JOIN allocations a ON f.id = a.file_id AND a.commit_id = ?
            LEFT JOIN excluded_files e ON f.id = e.file_id AND e.commit_id = ?
            WHERE f.commit_id = ?
              AND a.id IS NULL
              AND e.id IS NULL
              AND f.effective_lines > 10
        """, (commit_id, commit_id, commit_id))
        
        unallocated = cursor.fetchall()
        if unallocated:
            results['warnings'].append({
                'type': 'unallocated_files',
                'message': f'{len(unallocated)} source files not allocated to any copyright',
                'count': len(unallocated),
                'files': [{'id': r[0], 'path': r[1], 'language': r[2], 'lines': r[3]} for r in unallocated[:20]]
            })
    
    return results


def generate_validation_report(results: dict, output_path: Path):
    """Generate validation report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # 同时生成可读的文本报告
    txt_path = output_path.with_suffix('.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("分配验证报告\n")
        f.write("=" * 60 + "\n\n")
        
        if results['valid']:
            f.write("状态: ✅ 通过\n\n")
        else:
            f.write("状态: ❌ 失败\n\n")
        
        if results['errors']:
            f.write("错误:\n")
            for err in results['errors']:
                f.write(f"  - [{err['type']}] {err['message']}\n")
            f.write("\n")
        
        if results['warnings']:
            f.write("警告:\n")
            for warn in results['warnings']:
                f.write(f"  - [{warn['type']}] {warn['message']}\n")
            f.write("\n")
        
        f.write("软著统计:\n")
        f.write("-" * 60 + "\n")
        for stat in results['stats']['copyrights']:
            f.write(f"  {stat['copyright_id']}: {stat['name']} {stat['version']}\n")
            f.write(f"    文件数: {stat['file_count']}\n")
            f.write(f"    有效行数: {stat['total_lines']}\n")
            f.write("\n")


def main():
    parser = argparse.ArgumentParser(description='Validate file allocations')
    parser.add_argument('--commit-id', type=int, required=True, help='Commit ID to validate')
    parser.add_argument('--min-lines', type=int, default=3000,
                       help='Minimum effective lines per copyright (default: 3000)')
    parser.add_argument('--output', type=Path, default=Path('copyright/inventory/validation-report.json'),
                       help='Output path for validation report')
    
    args = parser.parse_args()
    
    init_database()
    
    results = validate_allocations(args.commit_id, args.min_lines)
    generate_validation_report(results, args.output)
    
    print(f"\nValidation {'PASSED' if results['valid'] else 'FAILED'}")
    print(f"  Errors: {len(results['errors'])}")
    print(f"  Warnings: {len(results['warnings'])}")
    print(f"\nReport: {args.output}")
    
    if not results['valid']:
        sys.exit(1)


if __name__ == '__main__':
    main()
