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
    
    dirs = [
        'copyright/config',
        'copyright/inventory',
        'copyright/allocations',
        'copyright/workspaces',
        'copyright/outputs',
    ]
    
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    
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
    from scan_repository import scan_repository, export_inventory, validate_repo_path
    
    repo_path = Path(args.repo_path)
    
    try:
        validate_repo_path(repo_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    try:
        result = scan_repository(
            repo_path=repo_path,
            commit_hash=args.commit,
            verbose=args.verbose
        )
    except Exception as e:
        print(f"Error during scan: {e}")
        sys.exit(1)
    
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
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config not found: {config_path}")
        sys.exit(1)
    
    config = load_config(config_path)
    
    if 'copyrights' not in config or not config['copyrights']:
        print("Error: No copyrights defined in config file.")
        sys.exit(1)
    
    results = allocate_files_from_config(
        commit_id=scan_result['commit_id'],
        files=scan_result['files'],
        copyrights_config=config['copyrights'],
        verbose=args.verbose,
        force=args.force,
        skip_used=not args.no_skip_used,
        skip_non_team=not args.no_skip_non_team,
    )
    
    export_allocations(results, Path('copyright/allocations'), config['copyrights'])
    
    print(f"\nAllocation complete:")
    if results.get('reallocated'):
        print("  (Re-allocation: cleared previous assignments)")
    
    for cr_id, files in results['allocations'].items():
        cr_name = next(c['name'] for c in config['copyrights'] if c['id'] == cr_id)
        total_lines = sum(f['effective_lines'] for f in files)
        print(f"  {cr_id} ({cr_name}): {len(files)} files, {total_lines} lines")
    
    if results.get('skipped_used'):
        print(f"\n  Skipped (used): {len(results['skipped_used'])} files")
    
    if results.get('skipped_non_team'):
        print(f"\n  Skipped (non-team): {len(results['skipped_non_team'])} files")
    
    if results['unallocated']:
        print(f"\n  Unallocated: {len(results['unallocated'])} files")
    
    if results['conflicts']:
        print(f"\n  Conflicts: {len(results['conflicts'])} files (already allocated)")
        if not args.force:
            print("  Tip: Use --force to clear and re-allocate")


def cmd_validate(args):
    """Validate allocations."""
    from validate_allocations import validate_allocations, generate_validation_report
    import json
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    results = validate_allocations(scan_result['commit_id'], args.min_lines)
    generate_validation_report(results, Path('copyright/inventory/validation-report.json'))
    
    print(f"\nValidation: {'PASSED' if results['valid'] else 'FAILED'}")
    print(f"  Errors: {len(results['errors'])}")
    print(f"  Warnings: {len(results['warnings'])}")
    
    if results['errors']:
        for err in results['errors']:
            print(f"  X [{err['type']}] {err['message']}")
    
    if args.verbose and results['warnings']:
        for warn in results['warnings']:
            print(f"  ! [{warn['type']}] {warn['message']}")


def cmd_workspace(args):
    """Build workspace."""
    from build_workspace import build_workspace
    import json
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    try:
        result = build_workspace(
            commit_id=scan_result['commit_id'],
            copyright_id=args.copyright_id,
            output_base=Path('copyright/workspaces'),
            verify_hashes=not args.no_verify,
            verbose=args.verbose,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    print(f"\nWorkspace built: {result['workspace_dir']}")
    print(f"  Status: {result['status']}")
    print(f"  Files: {result['total_files']}")
    print(f"  Lines: {result['total_effective_lines']}")


def cmd_detect(args):
    """Detect code duplication."""
    from detect_duplicates import detect_duplicates, save_report
    import json
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    repo_path = Path(scan_result.get('repo_path', '.'))
    report = detect_duplicates(scan_result['commit_id'], repo_path)
    save_report(report, Path('copyright/inventory/duplicate-report.json'))
    
    print(f"\nDuplicate detection complete:")
    print(f"  Path overlaps: {report['summary']['total_path_overlaps']}")
    print(f"  Exact file overlaps: {report['summary']['total_exact_overlaps']}")
    print(f"  Similar code pairs: {report['summary']['total_similar_pairs']}")
    
    if report['summary']['total_path_overlaps'] > 0 or report['summary']['total_exact_overlaps'] > 0:
        print("\n  CRITICAL: Path or exact file overlaps detected!")
    elif report['summary']['total_similar_pairs'] > 0:
        print("\n  WARNING: Similar code blocks detected, manual review recommended")


def cmd_generate(args):
    """Generate materials through Markdown -> PDF -> final PDF audit."""
    from generate_markdown_pdf import (
        generate_document_markdown_pdf,
        generate_program_markdown_pdf,
    )
    import json
    
    workspace_dir = Path('copyright/workspaces') / args.copyright_id
    if not workspace_dir.exists():
        print(f"Error: Workspace not found: {workspace_dir}")
        print(f"Run 'workspace {args.copyright_id}' first.")
        sys.exit(1)
    
    manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)
    
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    software_name = manifest['softwareName']
    version = manifest['version']
    description = manifest.get('description', '')
    output_dir = Path('copyright/outputs') / args.copyright_id

    from material_requirements import (
        FrozenAssetError,
        assert_generation_allowed,
        load_material_requirements,
    )
    requirements = load_material_requirements(getattr(args, 'requirements', None))
    try:
        assert_generation_allowed(args.copyright_id, software_name, requirements)
    except FrozenAssetError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    
    print(f"Generating materials for: {software_name} {version}")
    print()
    
    # 程序鉴别材料：真实代码 Markdown -> PDF -> 复核。
    print("=== 程序鉴别材料 ===")
    generate_program_markdown_pdf(
        workspace_dir,
        output_dir,
        software_name,
        version,
        requirements,
    )
    print()
    
    # 文档鉴别材料：系统专用 Markdown -> PDF -> 复核。
    if not getattr(args, 'code_only', False):
        print("=== 文档鉴别材料 ===")
        generate_document_markdown_pdf(
            workspace_dir,
            output_dir,
            software_name,
            version,
            description,
            Path(args.manual) if args.manual else None,
            requirements,
        )
    
    print(f"\nAll materials generated in: {output_dir}")


def cmd_audit(args):
    """Final audit."""
    from final_audit import audit_commit, generate_audit_report
    import json
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    results = audit_commit(
        scan_result['commit_id'],
        Path('copyright/workspaces'),
        Path('copyright/outputs'),
        getattr(args, 'requirements', None),
    )
    generate_audit_report(results, Path('copyright/outputs/final-audit.json'))
    
    print(f"\nFinal Audit: {'PASSED' if results['passed'] else 'FAILED'}")
    print(f"  Errors: {len(results['errors'])}")
    print(f"  Warnings: {len(results['warnings'])}")
    
    if not results['passed']:
        for err in results['errors']:
            print(f"  X {err}")


def cmd_requirements(args):
    """Validate the material rule source and frozen submitted assets."""
    from material_requirements import (
        audit_protected_assets,
        load_material_requirements,
        requirement_summary,
    )
    import json

    requirements = load_material_requirements(args.config)
    protected = audit_protected_assets(requirements)
    report = {
        'passed': protected['passed'],
        'config': requirements['_config_path'],
        'summary': requirement_summary(requirements),
        'protected_assets': protected,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        sys.exit(1)


def cmd_material_audit(args):
    """Audit one copyright material directory against the strict rules."""
    from material_requirements import load_material_requirements
    from validate_materials import audit_material_directory
    import json

    requirements = load_material_requirements(args.requirements)
    report = audit_material_directory(Path(args.directory), requirements)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        sys.exit(1)


# ============================================================
# Feature 1: Mark Used Code
# ============================================================

def cmd_mark_used(args):
    """Mark code from previous copyright documents as used."""
    from mark_used_code import mark_used_code, get_used_files_report
    import json
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    if args.action == 'mark':
        doc_path = Path(args.document)
        if not doc_path.exists():
            print(f"Error: Document not found: {doc_path}")
            sys.exit(1)
        
        result = mark_used_code(
            commit_id=scan_result['commit_id'],
            document_path=doc_path,
            copyright_name=args.copyright_name,
            min_match_lines=args.min_match_lines,
            threshold=args.threshold,
            verbose=args.verbose,
        )
        
        print(f"\nMarking complete:")
        print(f"  Document: {result['document']}")
        print(f"  Copyright: {result['copyright_name']}")
        print(f"  Extracted lines: {result['extracted_lines']}")
        print(f"  Matched files: {result['matched_files']}")
        
        if result['marked_files']:
            print(f"\n  Marked as used:")
            for f in result['marked_files']:
                print(f"    - {f['file_path']} (ratio: {f['match_ratio']:.1%})")
    
    elif args.action == 'report':
        report = get_used_files_report(scan_result['commit_id'])
        print(f"\nUsed files report:")
        print(f"  Total used files: {report['total_used_files']}")
        for cr_name, files in report['by_copyright'].items():
            print(f"\n  {cr_name}: {len(files)} files")
            for f in files:
                print(f"    - {f['file_path']}")
    
    elif args.action == 'clear':
        from database import get_connection
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM used_files WHERE commit_id = ?",
                         (scan_result['commit_id'],))
            cursor.execute("DELETE FROM used_code_markers WHERE commit_id = ?",
                         (scan_result['commit_id'],))
        print("Cleared all used-code markers.")


# ============================================================
# Feature 2: Ownership Analysis
# ============================================================

def cmd_ownership(args):
    """Analyze code ownership via git blame."""
    from analyze_ownership import (
        add_team_member, remove_team_member, get_team_members,
        load_team_from_config, analyze_repository_ownership,
        get_team_emails, generate_ownership_report
    )
    import json
    
    if args.action == 'add-member':
        add_team_member(args.name, args.email, args.role)
        print(f"Added: {args.name} <{args.email}> ({args.role})")
    
    elif args.action == 'remove-member':
        remove_team_member(args.email)
        print(f"Removed: {args.email}")
    
    elif args.action == 'list-members':
        members = get_team_members()
        if not members:
            print("No team members configured.")
        else:
            print(f"Team members ({len(members)}):")
            for m in members:
                print(f"  {m['name']} <{m['email']}> ({m['role']})")
    
    elif args.action == 'load-config':
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: Config not found: {config_path}")
            sys.exit(1)
        count = load_team_from_config(config_path)
        print(f"Loaded {count} team members from {config_path}")
    
    elif args.action == 'analyze':
        scan_result_path = Path('copyright/inventory/scan-result.json')
        if not scan_result_path.exists():
            print("Error: scan-result.json not found. Run 'scan' first.")
            sys.exit(1)
        
        with open(scan_result_path, 'r', encoding='utf-8') as f:
            scan_result = json.load(f)
        
        team_emails = get_team_emails()
        if not team_emails:
            print("Error: No team members configured.")
            print("Add members: python copyright_cli.py ownership add-member --name X --email Y")
            print("Or load:     python copyright_cli.py ownership load-config <yaml>")
            sys.exit(1)
        
        repo_path = Path(args.repo_path) if args.repo_path else Path(scan_result.get('repo_path', '.'))
        commit_hash = args.commit_hash if args.commit_hash else scan_result.get('commit_hash', 'HEAD')
        
        # Handle --since parameter
        from database import set_since_date, get_since_timestamp
        since_timestamp = None
        if hasattr(args, 'since') and args.since:
            set_since_date(args.since)
            since_timestamp = get_since_timestamp()
            if since_timestamp is None:
                print(f"Error: Invalid date format: {args.since}")
                sys.exit(1)
        else:
            since_timestamp = get_since_timestamp()
        
        results = analyze_repository_ownership(
            commit_id=scan_result['commit_id'],
            repo_path=repo_path,
            commit_hash=commit_hash,
            team_emails=team_emails,
            since_timestamp=since_timestamp,
            verbose=args.verbose,
        )
        
        report = generate_ownership_report(
            scan_result['commit_id'],
            Path('copyright/inventory/ownership-report.json')
        )
        
        print(f"\nOwnership analysis complete:")
        print(f"  Total files: {report['total_files']}")
        print(f"  Eligible (team): {report['eligible_files']}")
        print(f"  Ineligible: {report['ineligible_files']}")
        
        if report['authors']:
            print(f"\n  Authors:")
            for a in report['authors'][:10]:
                print(f"    {a['name']} <{a['email']}>: {a['total_lines']} lines, {a['file_count']} files")



# ============================================================
# Time Filter: Set Since Date
# ============================================================

def cmd_set_since(args):
    """Set or show the time cutoff for code eligibility."""
    from database import init_database, set_since_date, get_since_date, get_since_timestamp
    from datetime import datetime
    
    init_database()
    
    if args.action == 'set':
        date_str = args.date
        # Validate date format
        set_since_date(date_str)
        ts = get_since_timestamp()
        if ts is None:
            print(f"Error: Invalid date format: {date_str}")
            print("Supported: YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, YYYY/MM/DD, YYYY-MM")
            sys.exit(1)
        dt = datetime.fromtimestamp(ts)
        print(f"Time filter set: only code after {dt.strftime('%Y-%m-%d %H:%M:%S')} is eligible")
        print(f"  (Stored: {date_str})")
        print(f"\nNext: run 'ownership analyze' to re-evaluate file eligibility")
    
    elif args.action == 'show':
        date_str = get_since_date()
        if date_str:
            ts = get_since_timestamp()
            dt = datetime.fromtimestamp(ts) if ts else None
            print(f"Current time filter: {date_str}")
            if dt:
                print(f"  Parsed as: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print("No time filter set. All code is eligible regardless of date.")
    
    elif args.action == 'clear':
        set_since_date('')
        print("Time filter cleared. All code is now eligible regardless of date.")


# ============================================================
# Feature 3: Auto-Split
# ============================================================

def cmd_auto_split(args):
    """Auto-split project code into multiple copyrights."""
    from auto_split import auto_split, generate_config_yaml, export_split_report
    import json
    
    scan_result_path = Path('copyright/inventory/scan-result.json')
    if not scan_result_path.exists():
        print("Error: scan-result.json not found. Run 'scan' first.")
        sys.exit(1)
    
    with open(scan_result_path, 'r', encoding='utf-8') as f:
        scan_result = json.load(f)
    
    result = auto_split(
        commit_id=scan_result['commit_id'],
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
    
    repo_name = scan_result.get('repo_name', 'my-project')
    output_config = Path(args.output_config)
    yaml_str = generate_config_yaml(result, repo_name, output_config)
    
    output_report = Path(args.output_report)
    export_split_report(result, output_report)
    
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
        status = "OK" if cr['total_lines'] >= args.min_lines else "WARN"
        print(f"  [{status}] {cr['id']} ({cr['name']})")
        print(f"      {cr['file_count']} files, {cr['total_lines']} lines")
    
    print(f"\n  Config: {output_config}")
    print(f"  Report: {output_report}")
    print(f"\n  Next: python copyright_cli.py allocate --config {output_config} --force")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        prog='copyright_cli.py',
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
  generate    Generate Markdown, render PDF, then audit final PDF
  audit       Final audit before submission
  mark-used   [Feature 1] Mark code used in previous copyrights
  ownership   [Feature 2] Analyze code ownership (git blame)
  set-since   [Feature 4] Set time cutoff for code eligibility
  auto-split  [Feature 3] Auto-split code into multiple copyrights

Example workflow:
  python copyright_cli.py init
  python copyright_cli.py scan /path/to/repo
  
  # (Optional) Feature 1: Mark previously used code
  python copyright_cli.py mark-used mark doc.md --copyright-name "old copyright"
  
  # (Optional) Feature 2: Team ownership analysis
  python copyright_cli.py ownership add-member --name "Zhang" --email "zhang@team.com"
  python copyright_cli.py ownership analyze --repo-path /path/to/repo
  
  # (Optional) Feature 4: Time filter - only code after a date
  python copyright_cli.py set-since set 2025-01-01
  python copyright_cli.py ownership analyze --repo-path /path/to/repo
  
  # (Option A) Manual allocation with config
  python copyright_cli.py allocate
  
  # (Option B) Feature 3: Auto-split into multiple copyrights
  python copyright_cli.py auto-split --min-lines 3000
  python copyright_cli.py allocate --config copyright/config/copyrights-auto.yaml --force
  
  python copyright_cli.py validate
  python copyright_cli.py workspace <copyright-id>
  python copyright_cli.py detect
  python copyright_cli.py generate <copyright-id>
  python copyright_cli.py audit
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # init
    subparsers.add_parser('init', help='Initialize system')
    
    # scan
    scan_parser = subparsers.add_parser('scan', help='Scan Git repository')
    scan_parser.add_argument('repo_path', help='Path to Git repository')
    scan_parser.add_argument('--commit', help='Specific commit hash (default: HEAD)')
    scan_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # allocate
    allocate_parser = subparsers.add_parser('allocate', help='Allocate files to copyrights')
    allocate_parser.add_argument('--config', default='copyright/config/copyrights.yaml',
                                help='Path to copyrights.yaml')
    allocate_parser.add_argument('--force', action='store_true',
                                help='Clear existing allocations and re-allocate')
    allocate_parser.add_argument('--no-skip-used', action='store_true',
                                help='Do NOT skip files marked as used')
    allocate_parser.add_argument('--no-skip-non-team', action='store_true',
                                help='Do NOT skip non-team files')
    allocate_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # validate
    validate_parser = subparsers.add_parser('validate', help='Validate allocations')
    validate_parser.add_argument('--min-lines', type=int, default=3000,
                                 help='Minimum lines per copyright')
    validate_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # workspace
    workspace_parser = subparsers.add_parser('workspace', help='Build workspace')
    workspace_parser.add_argument('copyright_id', help='Copyright ID')
    workspace_parser.add_argument('--no-verify', action='store_true',
                                  help='Skip hash verification')
    workspace_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # detect
    detect_parser = subparsers.add_parser('detect', help='Detect code duplication')
    detect_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # generate
    generate_parser = subparsers.add_parser(
        'generate', help='Generate Markdown and audited PDF materials')
    generate_parser.add_argument('copyright_id', help='Copyright ID')
    generate_parser.add_argument('--code-only', action='store_true',
                                help='Only generate code material (skip document material)')
    generate_parser.add_argument('--requirements',
                                help='Material requirements YAML')
    generate_parser.add_argument('--manual',
                                help='System-specific Markdown manual for document material')
    generate_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # audit
    audit_parser = subparsers.add_parser('audit', help='Final audit')
    audit_parser.add_argument('--requirements', type=Path,
                              help='Material requirements YAML')
    audit_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    # requirement source and submitted-material freeze
    requirements_parser = subparsers.add_parser(
        'requirements', help='Validate strict material rules and frozen assets')
    requirements_parser.add_argument('--config', type=Path,
                                     help='Material requirements YAML')

    # standalone material audit
    material_audit_parser = subparsers.add_parser(
        'material-audit', help='Audit one material directory')
    material_audit_parser.add_argument('directory', help='Material directory')
    material_audit_parser.add_argument('--requirements', type=Path,
                                       help='Material requirements YAML')
    material_audit_parser.add_argument('--output', help='Optional JSON report path')
    
    # mark-used (Feature 1)
    mark_used_parser = subparsers.add_parser('mark-used',
        help='[Feature 1] Mark code used in previous copyrights')
    mark_used_sub = mark_used_parser.add_subparsers(dest='action')
    
    mark_parser = mark_used_sub.add_parser('mark', help='Mark a document as used')
    mark_parser.add_argument('document', help='Path to copyright document (md/docx/pdf)')
    mark_parser.add_argument('--copyright-name', required=True,
                            help='Name of the previous copyright')
    mark_parser.add_argument('--min-match-lines', type=int, default=3,
                            help='Minimum matching lines (default: 3)')
    mark_parser.add_argument('--threshold', type=float, default=0.6,
                            help='Match ratio threshold (default: 0.6)')
    mark_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    mark_report_parser = mark_used_sub.add_parser('report', help='Show used files report')
    mark_report_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    mark_clear_parser = mark_used_sub.add_parser('clear', help='Clear all used markers')
    mark_clear_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # ownership (Feature 2)
    ownership_parser = subparsers.add_parser('ownership',
        help='[Feature 2] Analyze code ownership via git blame')
    ownership_sub = ownership_parser.add_subparsers(dest='action')
    
    add_member_parser = ownership_sub.add_parser('add-member', help='Add team member')
    add_member_parser.add_argument('--name', required=True, help='Member name')
    add_member_parser.add_argument('--email', required=True, help='Member email')
    add_member_parser.add_argument('--role', default='developer', help='Role')
    add_member_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    rm_member_parser = ownership_sub.add_parser('remove-member', help='Remove team member')
    rm_member_parser.add_argument('--email', required=True, help='Member email')
    rm_member_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    list_members_parser = ownership_sub.add_parser('list-members', help='List team members')
    list_members_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    load_config_parser = ownership_sub.add_parser('load-config',
        help='Load team members from YAML config')
    load_config_parser.add_argument('config', help='Path to YAML config with team section')
    load_config_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    analyze_parser = ownership_sub.add_parser('analyze', help='Run ownership analysis')
    analyze_parser.add_argument('--repo-path', help='Path to git repository')
    analyze_parser.add_argument('--commit-hash', help='Commit hash (default: from scan)')
    analyze_parser.add_argument('--since', help='Only code after this date (YYYY-MM-DD)')
    analyze_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # set-since (Time Filter)
    set_since_parser = subparsers.add_parser('set-since',
        help='Set time cutoff: only code after this date is eligible')
    set_since_sub = set_since_parser.add_subparsers(dest='action')
    
    since_set_parser = set_since_sub.add_parser('set', help='Set the cutoff date')
    since_set_parser.add_argument('date', help='Cutoff date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)')
    
    set_since_sub.add_parser('show', help='Show current time filter')
    set_since_sub.add_parser('clear', help='Clear time filter')
    
    # auto-split (Feature 3)
    auto_split_parser = subparsers.add_parser('auto-split',
        help='[Feature 3] Auto-split code into multiple copyrights')
    auto_split_parser.add_argument('--min-lines', type=int, default=3000,
                                   help='Minimum effective lines per copyright (default: 3000)')
    auto_split_parser.add_argument('--max-lines', type=int, default=0,
                                   help='Maximum lines per copyright (0 = auto)')
    auto_split_parser.add_argument('--count', type=int, default=0,
                                   help='Force number of copyrights (0 = auto)')
    auto_split_parser.add_argument('--module-depth', type=int, default=2,
                                   help='Directory depth for module grouping (default: 2)')
    auto_split_parser.add_argument('--name-prefix', default='',
                                   help='Prefix for copyright names')
    auto_split_parser.add_argument('--name-template', default='',
                                   help='Name template: {prefix}{module}V{index}')
    auto_split_parser.add_argument('--output-config',
                                   default='copyright/config/copyrights-auto.yaml',
                                   help='Output YAML config path')
    auto_split_parser.add_argument('--output-report',
                                   default='copyright/inventory/auto-split-report.json',
                                   help='Output report JSON path')
    auto_split_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if not hasattr(args, 'verbose'):
        args.verbose = False
    
    commands = {
        'init': cmd_init,
        'scan': cmd_scan,
        'allocate': cmd_allocate,
        'validate': cmd_validate,
        'workspace': cmd_workspace,
        'detect': cmd_detect,
        'generate': cmd_generate,
        'audit': cmd_audit,
        'requirements': cmd_requirements,
        'material-audit': cmd_material_audit,
        'mark-used': cmd_mark_used,
        'ownership': cmd_ownership,
        'set-since': cmd_set_since,
        'auto-split': cmd_auto_split,
    }
    
    if args.command in ('mark-used', 'ownership', 'set-since') and not getattr(args, 'action', None):
        if args.command == 'mark-used':
            mark_used_parser.print_help()
        elif args.command == 'ownership':
            ownership_parser.print_help()
        else:
            set_since_parser.print_help()
        sys.exit(1)
    
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

# 团队成员（用于 Feature 2: 代码归属分析）
# 基于邮箱地址判别，只有团队成员编写的代码才纳入软著申请
team:
  - name: 张三
    email: zhangsan@company.com
    role: lead
  - name: 李四
    email: lisi@company.com
    role: developer
  - name: 王五
    email: wangwu@company.com
    role: developer

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
