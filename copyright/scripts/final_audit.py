#!/usr/bin/env python3
"""Final audit: cross-check all copyrights before submission."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import init_database, get_connection
from material_requirements import (
    audit_protected_assets,
    load_material_requirements,
    requirement_summary,
)
from run_manifest import load_run_manifest
from validate_materials import audit_material_directory


def audit_commit(
    commit_id: int,
    workspaces_dir: Path,
    outputs_dir: Path = Path('copyright/outputs'),
    requirements_path: Path | None = None,
) -> dict:
    """
    Final audit before submission.
    
    Checks:
    1. Code path overlap = 0
    2. Exact file overlap = 0
    3. Software name consistency
    4. Version consistency
    5. All files in manifest exist
    6. No files outside manifest in workspace
    """
    results = {
        'passed': True,
        'errors': [],
        'warnings': [],
        'copyrights': [],
    }

    requirements = load_material_requirements(requirements_path)
    results['material_requirements'] = requirement_summary(requirements)
    protected_assets = audit_protected_assets(requirements)
    results['protected_assets'] = protected_assets
    if not protected_assets['passed']:
        results['passed'] = False
        for asset in protected_assets['assets']:
            if not asset['passed']:
                results['errors'].append({
                    'type': 'protected_asset_changed',
                    'copyright_id': asset['copyright_id'],
                    'message': (
                        f"冻结材料{asset['copyright_id']}校验失败："
                        f"expected={asset['expected_digest']}, actual={asset.get('actual_digest')}"
                    ),
                    'details': asset,
                })
    
    # Run manifest: 最终审计必须能追溯到产生分配的 commit + 配置
    manifest = load_run_manifest()
    if manifest is None:
        results['run_manifest'] = {'found': False}
        results['warnings'].append({
            'type': 'run_manifest_missing',
            'message': 'run-manifest.json not found; allocation provenance '
                       '(commit/config/filters) cannot be verified',
        })
    else:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT commit_hash FROM commits WHERE id = ?", (commit_id,)
            ).fetchone()
        audited_commit = row[0] if row else None
        manifest_commit = manifest.get('commit_hash')
        commit_match = bool(audited_commit) and audited_commit == manifest_commit
        results['run_manifest'] = {
            'found': True,
            'tool_version': manifest.get('tool_version'),
            'commit_hash': manifest_commit,
            'config_sha256': (manifest.get('config') or {}).get('sha256'),
            'commit_match': commit_match,
        }
        if not commit_match:
            results['passed'] = False
            results['errors'].append({
                'type': 'run_manifest_commit_mismatch',
                'manifest_commit': manifest_commit,
                'audited_commit': audited_commit,
                'message': (
                    f'run manifest was produced for commit {manifest_commit}, '
                    f'but the audited commit is {audited_commit}'
                ),
            })

    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 获取所有软著
        cursor.execute("""
            SELECT id, copyright_id, name, version, status, description
            FROM copyrights
            WHERE commit_id = ?
        """, (commit_id,))
        
        copyrights = cursor.fetchall()
        
        # 收集所有分配的文件路径
        all_paths = {}  # path -> copyright_id
        all_hashes = {}  # sha256 -> [(copyright_id, path)]
        
        for cr_db_id, cr_id, cr_name, cr_version, cr_status, cr_description in copyrights:
            description = (cr_description or '').strip()
            main_function_rule = requirements['document_material']['main_function_section']
            main_function_characters = len(re.sub(r'\s+', '', description))
            cr_result = {
                'copyright_id': cr_id,
                'name': cr_name,
                'version': cr_version,
                'status': cr_status,
                'file_count': 0,
                'total_lines': 0,
                'workspace_exists': False,
                'manifest_valid': False,
                'main_function_characters': main_function_characters,
            }
            if not (
                int(main_function_rule['minimum_characters'])
                <= main_function_characters
                <= int(main_function_rule['maximum_characters'])
            ):
                results['passed'] = False
                results['errors'].append({
                    'type': 'main_function_characters',
                    'copyright_id': cr_id,
                    'message': (
                        f'{cr_id}: 《软件的主要功能》必须为'
                        f"{main_function_rule['minimum_characters']}至"
                        f"{main_function_rule['maximum_characters']}字，"
                        f'实际{main_function_characters}字'
                    ),
                })
            
            # 获取分配的文件
            cursor.execute("""
                SELECT f.file_path, f.sha256, f.effective_lines
                FROM files f
                JOIN allocations a ON f.id = a.file_id
                WHERE a.commit_id = ? AND a.copyright_id = ?
            """, (commit_id, cr_db_id))
            
            files = cursor.fetchall()
            cr_result['file_count'] = len(files)
            cr_result['total_lines'] = sum(f[2] for f in files)
            
            # 检查路径重复
            for file_path, sha256, _ in files:
                if file_path in all_paths:
                    results['passed'] = False
                    results['errors'].append({
                        'type': 'path_overlap',
                        'file': file_path,
                        'copyright_1': all_paths[file_path],
                        'copyright_2': cr_id,
                        'message': f'File {file_path} allocated to both {all_paths[file_path]} and {cr_id}'
                    })
                else:
                    all_paths[file_path] = cr_id
                
                # 收集哈希
                if sha256 not in all_hashes:
                    all_hashes[sha256] = []
                all_hashes[sha256].append((cr_id, file_path))
            
            # 检查工作区
            workspace_dir = workspaces_dir / cr_id
            manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
            
            if workspace_dir.exists():
                cr_result['workspace_exists'] = True
                
                if manifest_path.exists():
                    cr_result['manifest_valid'] = True
                    
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                    
                    # 检查 manifest 中的文件数量
                    if manifest['totalFiles'] != len(files):
                        results['warnings'].append({
                            'type': 'file_count_mismatch',
                            'copyright_id': cr_id,
                            'manifest_count': manifest['totalFiles'],
                            'db_count': len(files),
                            'message': f'{cr_id}: manifest has {manifest["totalFiles"]} files, DB has {len(files)}'
                        })
                    
                    # 检查 commit 一致性
                    cursor.execute("SELECT commit_hash FROM commits WHERE id = ?", (commit_id,))
                    expected_commit = cursor.fetchone()[0]
                    
                    if manifest['repositoryCommit'] != expected_commit:
                        results['passed'] = False
                        results['errors'].append({
                            'type': 'commit_mismatch',
                            'copyright_id': cr_id,
                            'manifest_commit': manifest['repositoryCommit'],
                            'expected_commit': expected_commit,
                            'message': f'{cr_id}: manifest commit mismatch'
                        })
                    
                    # 检查软件名称一致性
                    if manifest['softwareName'] != cr_name:
                        results['passed'] = False
                        results['errors'].append({
                            'type': 'name_mismatch',
                            'copyright_id': cr_id,
                            'manifest_name': manifest['softwareName'],
                            'db_name': cr_name,
                            'message': f'{cr_id}: software name mismatch'
                        })
                    
                    # 检查版本一致性
                    if manifest['version'] != cr_version:
                        results['passed'] = False
                        results['errors'].append({
                            'type': 'version_mismatch',
                            'copyright_id': cr_id,
                            'manifest_version': manifest['version'],
                            'db_version': cr_version,
                            'message': f'{cr_id}: version mismatch'
                        })
                else:
                    results['warnings'].append({
                        'type': 'missing_manifest',
                        'copyright_id': cr_id,
                        'message': f'{cr_id}: workspace exists but manifest missing'
                    })
            else:
                results['warnings'].append({
                    'type': 'missing_workspace',
                    'copyright_id': cr_id,
                    'message': f'{cr_id}: workspace not built'
                })

            material_dir = outputs_dir / cr_id
            if material_dir.exists():
                material_audit = audit_material_directory(
                    material_dir,
                    requirements,
                    require_rendered_pair=True,
                )
                cr_result['material_audit'] = material_audit
                if not material_audit['passed']:
                    results['passed'] = False
                    for issue in material_audit['errors']:
                        results['errors'].append({
                            'type': f"material_{issue['code']}",
                            'copyright_id': cr_id,
                            'message': f"{cr_id}: {issue['message']}",
                            'details': issue,
                        })
                for issue in material_audit['warnings']:
                    results['warnings'].append({
                        'type': f"material_{issue['code']}",
                        'copyright_id': cr_id,
                        'message': f"{cr_id}: {issue['message']}",
                        'details': issue,
                    })
            else:
                cr_result['material_audit'] = None
                results['passed'] = False
                results['errors'].append({
                    'type': 'missing_material_output',
                    'copyright_id': cr_id,
                    'message': f'{cr_id}: 缺少鉴别材料输出目录，不能通过提交前审计',
                })
            
            results['copyrights'].append(cr_result)
    
    # 检查完全相同文件（不同路径但相同哈希）
    for sha256, file_list in all_hashes.items():
        cr_ids = set(cr_id for cr_id, _ in file_list)
        if len(cr_ids) > 1:
            results['passed'] = False
            results['errors'].append({
                'type': 'exact_content_overlap',
                'sha256': sha256[:16] + '...',
                'copyrights': list(cr_ids),
                'files': [{'copyright_id': cr_id, 'path': path} for cr_id, path in file_list],
                'message': f'Files with same content allocated to multiple copyrights'
            })
    
    return results


def generate_audit_report(results: dict, output_path: Path):
    """Generate audit report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # JSON 报告
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # 文本报告
    txt_path = output_path.with_suffix('.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write("最终审计报告\n")
        f.write("=" * 70 + "\n\n")
        
        if results['passed']:
            f.write("状态: ✅ 通过 - 可以提交\n\n")
        else:
            f.write("状态: ❌ 失败 - 存在错误需要修复\n\n")
        
        if results['errors']:
            f.write("错误 (必须修复):\n")
            f.write("-" * 70 + "\n")
            for err in results['errors']:
                f.write(f"  [{err['type']}] {err['message']}\n")
            f.write("\n")
        
        if results['warnings']:
            f.write("警告 (建议检查):\n")
            f.write("-" * 70 + "\n")
            for warn in results['warnings']:
                f.write(f"  [{warn['type']}] {warn['message']}\n")
            f.write("\n")
        
        f.write("软著汇总:\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'软著ID':<15} {'名称':<25} {'版本':<8} {'文件数':<8} {'行数':<10} {'状态':<10}\n")
        f.write("-" * 70 + "\n")
        
        for cr in results['copyrights']:
            workspace_status = '✓' if cr['workspace_exists'] else '✗'
            f.write(f"{cr['copyright_id']:<15} {cr['name'][:24]:<25} {cr['version']:<8} "
                   f"{cr['file_count']:<8} {cr['total_lines']:<10} {workspace_status:<10}\n")
        
        f.write("\n")
        f.write("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description='Final audit before submission')
    parser.add_argument('--commit-id', type=int, required=True, help='Commit ID to audit')
    parser.add_argument('--workspaces', type=Path, default=Path('copyright/workspaces'),
                       help='Workspaces directory')
    parser.add_argument('--outputs', type=Path, default=Path('copyright/outputs'),
                       help='Generated material outputs directory')
    parser.add_argument('--requirements', type=Path,
                       help='Material requirements YAML')
    parser.add_argument('--output', type=Path, default=Path('copyright/outputs/final-audit.json'),
                       help='Output path for audit report')
    
    args = parser.parse_args()
    
    init_database()
    
    results = audit_commit(args.commit_id, args.workspaces, args.outputs, args.requirements)
    generate_audit_report(results, args.output)
    
    print(f"\nFinal Audit: {'PASSED ✓' if results['passed'] else 'FAILED ✗'}")
    print(f"  Errors: {len(results['errors'])}")
    print(f"  Warnings: {len(results['warnings'])}")
    print(f"\nReport: {args.output}")
    
    if not results['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
