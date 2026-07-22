#!/usr/bin/env python3
"""CLI entry point for copyright asset allocation system."""

import argparse
import sys
from pathlib import Path

# 添加 scripts 目录到 path
SCRIPTS_DIR = Path(__file__).parent / 'copyright' / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))


def cmd_init(args):
    """Initialize database and directory structure."""
    from database import init_database
    
    init_database()
    
    # 创建目录结构
    dirs = [
        'copyright/config',
        'copyright/inventory',
        'copyright/allocations',
        'copyright/workspaces',
        'copyright/outputs',
    ]
    
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    
    # 创建示例配置文件
    config_path = Path('copyright/config/copyrights.yaml')
    if not config_path.exists():
        config_path.write_text(EXAMPLE_CONFIG, encoding='utf-8')
        print(f"Created example config: {config_path}")
    
    print("Initialization complete.")
    print("Next steps:")
    print("  1. Edit copyright/config/copyrights.yaml")
    print("  2. Run: python copyright_cli.py scan <repo-path>")


def cmd_scan(args):
    """Scan repository."""
    from scan_repository import scan_repository, export_inventory
    
    result = scan_repository(
        repo_path=Path(args.repo_path),
        commit_hash=args.commit,
        verbose=args.verbose
    )
    
    export_inventory(result, Path('copyright/inventory'))
    
    print(f"\nScan complete:")
    print(f"  Commit: {result['commit_hash']}")
    print(f"  Commit ID: {result['commit_id']}")
    print(f"  Files: {len(result['files'])}")
    print(f"  Excluded: {len(result['excluded'])}")


def cmd_allocate(args):
    """Allocate files to copyrights."""
    from allocate_files import load_config, allocate_files_from_config, export_allocations
    import json
    
    # 加载扫描结果
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    # 加载配置
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config not found: {config_path}")
        sys.exit(1)
    
    config = load_config(config_path)
    
    # 执行分配
    results = allocate_files_from_config(
        commit_id=scan_result['commit_id'],
        files=scan_result['files'],
        copyrights_config=config['copyrights'],
        verbose=args.verbose
    )
    
    # 导出结果
    export_allocations(results, Path('copyright/allocations'), config['copyrights'])
    
    print(f"\nAllocation complete:")
    for cr_id, files in results['allocations'].items():
        cr_name = next(c['name'] for c in config['copyrights'] if c['id'] == cr_id)
        total_lines = sum(f['effective_lines'] for f in files)
        print(f"  {cr_id} ({cr_name}): {len(files)} files, {total_lines} lines")
    
    if results['unallocated']:
        print(f"\n  Unallocated: {len(results['unallocated'])} files")


def cmd_validate(args):
    """Validate allocations."""
    from validate_allocations import validate_allocations, generate_validation_report
    import json
    
    # 获取 commit_id
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    results = validate_allocations(scan_result['commit_id'], args.min_lines)
    generate_validation_report(results, Path('copyright/inventory/validation-report.json'))
    
    print(f"\nValidation: {'PASSED ✓' if results['valid'] else 'FAILED ✗'}")
    print(f"  Errors: {len(results['errors'])}")
    print(f"  Warnings: {len(results['warnings'])}")


def cmd_workspace(args):
    """Build workspace for a copyright."""
    from build_workspace import build_workspace
    import json
    
    # 获取 commit_id
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    result = build_workspace(
        commit_id=scan_result['commit_id'],
        copyright_id=args.copyright_id,
        output_base=Path('copyright/workspaces'),
        verify_hashes=not args.no_verify,
        verbose=args.verbose
    )
    
    print(f"\nWorkspace built: {result['workspace_dir']}")
    print(f"  Status: {result['status']}")
    print(f"  Files: {result['total_files']}")
    print(f"  Lines: {result['total_effective_lines']}")


def cmd_detect(args):
    """Detect code duplication."""
    from detect_duplicates import detect_duplicates, save_report
    import json
    
    # 获取信息
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    # 获取仓库路径
    from database import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.path FROM repositories r
        JOIN commits c ON r.id = c.repository_id
        WHERE c.id = ?
    """, (scan_result['commit_id'],))
    repo_path = cursor.fetchone()[0]
    conn.close()
    
    report = detect_duplicates(scan_result['commit_id'], Path(repo_path))
    save_report(report, Path('copyright/inventory/duplicate-report.json'))
    
    print(f"\nDuplicate detection complete:")
    print(f"  Path overlaps: {report['summary']['total_path_overlaps']}")
    print(f"  Exact file overlaps: {report['summary']['total_exact_overlaps']}")
    print(f"  Similar code pairs: {report['summary']['total_similar_pairs']}")


def cmd_generate(args):
    """Generate DOCX materials."""
    from generate_docx import generate_code_material
    import json
    
    workspace_dir = Path('copyright/workspaces') / args.copyright_id
    
    if not workspace_dir.exists():
        print(f"Error: Workspace not found: {workspace_dir}")
        print("Run 'workspace' command first.")
        sys.exit(1)
    
    # 加载 manifest
    manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    output_dir = Path('copyright/outputs') / args.copyright_id
    
    generate_code_material(
        workspace_dir=workspace_dir,
        output_dir=output_dir,
        software_name=manifest['softwareName'],
        version=manifest['version'],
    )


def cmd_audit(args):
    """Final audit."""
    from final_audit import audit_commit, generate_audit_report
    import json
    
    # 获取 commit_id
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    results = audit_commit(scan_result['commit_id'], Path('copyright/workspaces'))
    generate_audit_report(results, Path('copyright/outputs/final-audit.json'))
    
    print(f"\nFinal Audit: {'PASSED ✓' if results['passed'] else 'FAILED ✗'}")
    print(f"  Errors: {len(results['errors'])}")
    print(f"  Warnings: {len(results['warnings'])}")


def main():
    parser = argparse.ArgumentParser(
        description='Copyright Asset Allocation System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  init        Initialize database and directory structure
  scan        Scan Git repository and build file inventory
  allocate    Allocate files to copyrights based on config
  validate    Validate allocations (completeness, conflicts)
  workspace   Build isolated workspace for a copyright
  detect      Detect code duplication between copyrights
  generate    Generate DOCX code materials
  audit       Final audit before submission

Example workflow:
  python copyright_cli.py init
  python copyright_cli.py scan /path/to/repo
  # Edit copyright/config/copyrights.yaml
  python copyright_cli.py allocate
  python copyright_cli.py validate
  python copyright_cli.py workspace collector
  python copyright_cli.py workspace parser
  python copyright_cli.py detect
  python copyright_cli.py generate collector
  python copyright_cli.py generate parser
  python copyright_cli.py audit
        """
    )
    
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # init
    subparsers.add_parser('init', help='Initialize system')
    
    # scan
    scan_parser = subparsers.add_parser('scan', help='Scan Git repository')
    scan_parser.add_argument('repo_path', help='Path to Git repository')
    scan_parser.add_argument('--commit', help='Specific commit hash (default: HEAD)')
    
    # allocate
    allocate_parser = subparsers.add_parser('allocate', help='Allocate files to copyrights')
    allocate_parser.add_argument('--config', default='copyright/config/copyrights.yaml',
                                help='Path to copyrights.yaml')
    
    # validate
    validate_parser = subparsers.add_parser('validate', help='Validate allocations')
    validate_parser.add_argument('--min-lines', type=int, default=3000,
                                 help='Minimum lines per copyright')
    
    # workspace
    workspace_parser = subparsers.add_parser('workspace', help='Build workspace')
    workspace_parser.add_argument('copyright_id', help='Copyright ID')
    workspace_parser.add_argument('--no-verify', action='store_true',
                                  help='Skip hash verification')
    
    # detect
    subparsers.add_parser('detect', help='Detect code duplication')
    
    # generate
    generate_parser = subparsers.add_parser('generate', help='Generate DOCX materials')
    generate_parser.add_argument('copyright_id', help='Copyright ID')
    
    # audit
    subparsers.add_parser('audit', help='Final audit')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # 路由到对应命令
    commands = {
        'init': cmd_init,
        'scan': cmd_scan,
        'allocate': cmd_allocate,
        'validate': cmd_validate,
        'workspace': cmd_workspace,
        'detect': cmd_detect,
        'generate': cmd_generate,
        'audit': cmd_audit,
    }
    
    commands[args.command](args)


EXAMPLE_CONFIG = """# 软著配置文件
# 定义要申请的软著及其代码范围

snapshot:
  repository: my-project
  created_at: "2026-07-22"

policies:
  path_overlap: forbidden
  exact_content_overlap: forbidden
  copied_code_detection: enabled
  unallocated_file_access: forbidden
  shared_file_policy: excluded
  generated_code_policy: excluded
  third_party_code_policy: excluded

copyrights:
  - id: service-a
    name: XX服务系统
    version: V1.0
    description: 提供XX功能的软件系统
    include_paths:
      - services/a/**
      - libs/a-core/**
    exclude_paths:
      - services/b/**
      - services/c/**
      - shared/**
      - vendor/**
      - generated/**
      - tests/**

  - id: service-b
    name: YY处理系统
    version: V1.0
    description: 处理YY数据的软件系统
    include_paths:
      - services/b/**
      - libs/b-core/**
    exclude_paths:
      - services/a/**
      - services/c/**
      - shared/**
      - vendor/**
      - generated/**
      - tests/**
"""


if __name__ == '__main__':
    main()
