#!/usr/bin/env python3
"""Feature 2: Analyze code ownership via git blame to identify team-written code.

For projects based on open-source code, only code added/modified by team members
(identified by email) should be eligible for copyright application.

Supports time-based filtering: only code added/modified after a specified date
is considered eligible (--since parameter).
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from database import (
    init_database, get_connection,
    set_since_date, get_since_date, get_since_timestamp,
    get_repository_id_for_commit, parse_since_date,
)


# ============================================================
# Team Member Management
# ============================================================

def add_team_member(name: str, email: str, role: str = "developer"):
    """Add a team member."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO team_members (name, email, role)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET name = excluded.name, role = excluded.role
        """, (name, email, role))


def remove_team_member(email: str):
    """Remove a team member."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM team_members WHERE email = ?", (email,))


def get_team_emails() -> Set[str]:
    """Get all team member emails (lowercase)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM team_members")
        return set(row[0].lower() for row in cursor.fetchall())


def get_team_members() -> List[Dict]:
    """Get all team members."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, email, role FROM team_members ORDER BY name")
        return [
            {'id': r[0], 'name': r[1], 'email': r[2], 'role': r[3]}
            for r in cursor.fetchall()
        ]


def load_team_from_config(config_path: Path):
    """Load team members from YAML config."""
    import yaml
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    team = config.get('team', [])
    for member in team:
        add_team_member(
            name=member.get('name', ''),
            email=member['email'],
            role=member.get('role', 'developer'),
        )
    return len(team)


# ============================================================
# Git Blame Analysis (with timestamp extraction)
# ============================================================

def run_git_blame(repo_path: Path, file_path: str, commit_hash: str = "HEAD") -> List[Dict]:
    """Run git blame on a file and return line-by-line authorship with timestamps.
    
    Returns: [{line_number, author_email, author_name, commit_hash, author_time}]
    """
    try:
        result = subprocess.run(
            ['git', 'blame', '--line-porcelain', commit_hash, '--', file_path],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    
    lines_info = []
    current_commit = ""
    current_author = ""
    current_email = ""
    current_time = 0  # Unix timestamp
    line_number = 0
    
    for line in result.stdout.split('\n'):
        # Commit hash line (40 hex chars)
        if re.match(r'^[0-9a-f]{40}\s', line):
            current_commit = line.split()[0]
        elif line.startswith('author '):
            current_author = line[7:]
        elif line.startswith('author-mail '):
            # Format: <email@example.com>
            email = line[12:].strip('<>')
            current_email = email
        elif line.startswith('author-time '):
            # Unix timestamp of when the line was authored
            try:
                current_time = int(line[12:])
            except ValueError:
                current_time = 0
        elif line.startswith('\t'):
            # This is the actual content line
            line_number += 1
            lines_info.append({
                'line_number': line_number,
                'author_email': current_email.lower(),
                'author_name': current_author,
                'commit_hash': current_commit,
                'author_time': current_time,
            })
    
    return lines_info


def analyze_file_ownership(
    repo_path: Path,
    file_path: str,
    commit_hash: str,
    team_emails: Set[str],
    since_timestamp: Optional[int] = None,
) -> Dict:
    """Analyze ownership of a single file with optional time filter.
    
    Args:
        since_timestamp: If set, only lines modified after this Unix timestamp
                        are counted as eligible.
    
    Returns: {
        total_lines, team_lines, team_ratio,
        recent_lines, recent_ratio,
        is_eligible, authors: {email: line_count}
    }
    """
    blame_info = run_git_blame(repo_path, file_path, commit_hash)
    
    if not blame_info:
        return {
            'total_lines': 0,
            'team_lines': 0,
            'team_ratio': 0.0,
            'recent_lines': 0,
            'recent_ratio': 0.0,
            'is_eligible': True,  # 无法分析时默认合格
            'authors': {},
        }
    
    # 统计每个作者的行数
    author_lines = defaultdict(int)
    team_lines = 0
    recent_lines = 0  # 时间点之后新增/修改的行数
    recent_team_lines = 0  # 时间点之后且为团队编写的行数
    total_lines = len(blame_info)
    
    for info in blame_info:
        email = info['author_email']
        author_lines[email] += 1
        
        is_team = email in team_emails
        is_recent = (since_timestamp is None) or (info['author_time'] >= since_timestamp)
        
        if is_team:
            team_lines += 1
        
        if is_recent:
            recent_lines += 1
            if is_team:
                recent_team_lines += 1
    
    team_ratio = team_lines / total_lines if total_lines > 0 else 0.0
    recent_ratio = recent_lines / total_lines if total_lines > 0 else 0.0
    
    # 合格判定逻辑：
    # 1. 如果设置了时间过滤：需要时间点后的代码占比 >= 50%
    # 2. 团队代码占比 >= 50%
    # 3. 如果两者都设置了，需要时间点后的团队代码占比 >= 50%
    if since_timestamp is not None:
        # 时间过滤模式：以时间点后的代码为基准
        # 合格条件：时间点后代码占比 >= 50% 且 时间点后团队代码占时间点后总代码 >= 50%
        recent_team_ratio = recent_team_lines / recent_lines if recent_lines > 0 else 0.0
        is_eligible = (recent_ratio >= 0.5) and (recent_team_ratio >= 0.5)
    else:
        # 无时间过滤：仅看团队占比
        is_eligible = team_ratio >= 0.5
    
    return {
        'total_lines': total_lines,
        'team_lines': team_lines,
        'team_ratio': team_ratio,
        'recent_lines': recent_lines,
        'recent_ratio': recent_ratio,
        'recent_team_lines': recent_team_lines,
        'is_eligible': is_eligible,
        'authors': dict(author_lines),
    }


def analyze_repository_ownership(
    commit_id: int,
    repo_path: Path,
    commit_hash: str,
    team_emails: Set[str],
    since_timestamp: Optional[int] = None,
    verbose: bool = False
) -> Dict:
    """Analyze ownership for all files in the repository.
    
    Args:
        since_timestamp: Unix timestamp cutoff. Only lines modified after this
                        time are considered for eligibility.
    
    Stores results in database and returns summary.
    """
    # 获取所有文件
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, file_path FROM files WHERE commit_id = ?
        """, (commit_id,))
        files = cursor.fetchall()
    
    if verbose:
        print(f"Analyzing ownership for {len(files)} files...")
        print(f"Team members: {len(team_emails)} emails")
        if since_timestamp:
            since_dt = datetime.fromtimestamp(since_timestamp)
            print(f"Time filter: only code after {since_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {
        'total_files': len(files),
        'eligible_files': 0,
        'ineligible_files': 0,
        'skipped_files': 0,
        'total_team_lines': 0,
        'total_lines': 0,
        'total_recent_lines': 0,
        'since_date': datetime.fromtimestamp(since_timestamp).strftime('%Y-%m-%d') if since_timestamp else None,
        'file_details': [],
    }
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 清除旧的分析结果
        cursor.execute("DELETE FROM file_team_stats WHERE commit_id = ?", (commit_id,))
        cursor.execute("DELETE FROM code_ownership WHERE commit_id = ?", (commit_id,))
        
        for idx, (file_id, file_path) in enumerate(files):
            full_path = repo_path / file_path
            if not full_path.exists():
                results['skipped_files'] += 1
                continue
            
            # 分析文件归属（含时间过滤）
            ownership = analyze_file_ownership(
                repo_path, file_path, commit_hash, team_emails, since_timestamp
            )
            
            if ownership['total_lines'] == 0:
                results['skipped_files'] += 1
                continue
            
            # 存储文件级统计
            cursor.execute("""
                INSERT INTO file_team_stats 
                (commit_id, file_id, total_lines, team_lines, team_ratio, is_eligible)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                commit_id, file_id,
                ownership['total_lines'],
                ownership['team_lines'],
                ownership['team_ratio'],
                1 if ownership['is_eligible'] else 0,
            ))
            
            # 存储行级归属（按连续块压缩）
            _store_ownership_blocks(
                cursor, commit_id, file_id, repo_path, file_path,
                commit_hash, team_emails, since_timestamp
            )
            
            # 更新统计
            results['total_lines'] += ownership['total_lines']
            results['total_team_lines'] += ownership['team_lines']
            results['total_recent_lines'] += ownership['recent_lines']
            
            if ownership['is_eligible']:
                results['eligible_files'] += 1
            else:
                results['ineligible_files'] += 1
            
            results['file_details'].append({
                'file_id': file_id,
                'file_path': file_path,
                'total_lines': ownership['total_lines'],
                'team_lines': ownership['team_lines'],
                'team_ratio': ownership['team_ratio'],
                'recent_lines': ownership['recent_lines'],
                'recent_ratio': ownership['recent_ratio'],
                'is_eligible': ownership['is_eligible'],
                'top_authors': sorted(
                    ownership['authors'].items(),
                    key=lambda x: x[1], reverse=True
                )[:3],
            })
            
            if verbose and (idx + 1) % 10 == 0:
                print(f"  Analyzed {idx + 1}/{len(files)} files...")
    
    if verbose:
        print(f"\nOwnership analysis complete:")
        print(f"  Eligible files: {results['eligible_files']}")
        print(f"  Ineligible files: {results['ineligible_files']}")
        print(f"  Team code ratio: {results['total_team_lines']}/{results['total_lines']} "
              f"({results['total_team_lines']/max(results['total_lines'],1):.1%})")
        if since_timestamp:
            print(f"  Recent lines (after cutoff): {results['total_recent_lines']}/{results['total_lines']} "
                  f"({results['total_recent_lines']/max(results['total_lines'],1):.1%})")
    
    return results


def _store_ownership_blocks(cursor, commit_id, file_id, repo_path, file_path,
                            commit_hash, team_emails, since_timestamp=None):
    """Store compressed ownership blocks (consecutive lines by same author)."""
    blame_info = run_git_blame(repo_path, file_path, commit_hash)
    if not blame_info:
        return
    
    # 压缩连续行
    blocks = []
    current_block = None
    
    for info in blame_info:
        email = info['author_email']
        if current_block and current_block['email'] == email:
            current_block['line_end'] = info['line_number']
        else:
            if current_block:
                blocks.append(current_block)
            current_block = {
                'line_start': info['line_number'],
                'line_end': info['line_number'],
                'email': email,
                'name': info['author_name'],
                'commit': info['commit_hash'],
                'time': info['author_time'],
            }
    
    if current_block:
        blocks.append(current_block)
    
    # 存储（限制块数量避免数据库膨胀）
    for block in blocks[:200]:  # 最多200个块
        cursor.execute("""
            INSERT INTO code_ownership
            (commit_id, file_id, line_start, line_end, author_email, author_name, is_team_member, commit_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            commit_id, file_id,
            block['line_start'], block['line_end'],
            block['email'], block['name'],
            1 if block['email'] in team_emails else 0,
            block['commit'],
        ))


# ============================================================
# Reporting
# ============================================================

def generate_ownership_report(commit_id: int, output_path: Path):
    """Generate ownership analysis report."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 文件级统计
        cursor.execute("""
            SELECT fts.file_id, f.file_path, fts.total_lines, fts.team_lines, 
                   fts.team_ratio, fts.is_eligible
            FROM file_team_stats fts
            JOIN files f ON fts.file_id = f.id
            WHERE fts.commit_id = ?
            ORDER BY fts.team_ratio DESC
        """, (commit_id,))
        file_stats = cursor.fetchall()
        
        # 作者统计
        cursor.execute("""
            SELECT author_email, author_name, 
                   SUM(line_end - line_start + 1) as total_lines,
                   COUNT(DISTINCT file_id) as file_count
            FROM code_ownership
            WHERE commit_id = ?
            GROUP BY author_email
            ORDER BY total_lines DESC
        """, (commit_id,))
        author_stats = cursor.fetchall()
    
    # 获取时间过滤设置（按该 commit 所属仓库解析：仓库级优先，全局兜底）
    since_date = get_since_date(get_repository_id_for_commit(commit_id))
    
    report = {
        'total_files': len(file_stats),
        'eligible_files': sum(1 for r in file_stats if r[5]),
        'ineligible_files': sum(1 for r in file_stats if not r[5]),
        'since_date': since_date,
        'files': [
            {
                'file_path': r[1],
                'total_lines': r[2],
                'team_lines': r[3],
                'team_ratio': r[4],
                'is_eligible': bool(r[5]),
            }
            for r in file_stats
        ],
        'authors': [
            {
                'email': r[0],
                'name': r[1],
                'total_lines': r[2],
                'file_count': r[3],
            }
            for r in author_stats
        ],
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    return report


def main():
    parser = argparse.ArgumentParser(description='Analyze code ownership via git blame')
    subparsers = parser.add_subparsers(dest='action')
    
    # analyze 子命令
    analyze_parser = subparsers.add_parser('analyze', help='Analyze repository ownership')
    analyze_parser.add_argument('--commit-id', type=int, required=True)
    analyze_parser.add_argument('--repo-path', type=Path, required=True)
    analyze_parser.add_argument('--commit-hash', default='HEAD')
    analyze_parser.add_argument('--since', help='Only count code after this date (YYYY-MM-DD)')
    analyze_parser.add_argument('--verbose', '-v', action='store_true')
    
    # add-member 子命令
    add_parser = subparsers.add_parser('add-member', help='Add team member')
    add_parser.add_argument('--name', required=True)
    add_parser.add_argument('--email', required=True)
    add_parser.add_argument('--role', default='developer')
    
    # list-members 子命令
    subparsers.add_parser('list-members', help='List team members')
    
    # load-config 子命令
    load_parser = subparsers.add_parser('load-config', help='Load team from YAML')
    load_parser.add_argument('config', type=Path)
    
    args = parser.parse_args()
    
    init_database()
    
    if args.action == 'analyze':
        team_emails = get_team_emails()
        if not team_emails:
            print("Error: No team members configured.")
            print("Add members with: analyze_ownership.py add-member --name X --email Y")
            sys.exit(1)
        
        # 解析时间过滤（按 commit 所属仓库作用域保存/读取，避免跨仓库泄漏）
        repository_id = get_repository_id_for_commit(args.commit_id)
        since_timestamp = None
        if args.since:
            if parse_since_date(args.since) is None:
                print(f"Error: Invalid date format: {args.since}")
                print("Supported formats: YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, YYYY/MM/DD")
                sys.exit(1)
            set_since_date(args.since, repository_id=repository_id)
            since_timestamp = get_since_timestamp(repository_id)
        else:
            # 使用已保存的设置
            since_timestamp = get_since_timestamp(repository_id)
        
        results = analyze_repository_ownership(
            commit_id=args.commit_id,
            repo_path=args.repo_path,
            commit_hash=args.commit_hash,
            team_emails=team_emails,
            since_timestamp=since_timestamp,
            verbose=args.verbose,
        )
        
        # 生成报告
        generate_ownership_report(args.commit_id, Path('copyright/inventory/ownership-report.json'))
        
    elif args.action == 'add-member':
        add_team_member(args.name, args.email, args.role)
        print(f"Added: {args.name} <{args.email}> ({args.role})")
        
    elif args.action == 'list-members':
        members = get_team_members()
        if not members:
            print("No team members configured.")
        else:
            print(f"Team members ({len(members)}):")
            for m in members:
                print(f"  {m['name']} <{m['email']}> ({m['role']})")
                
    elif args.action == 'load-config':
        count = load_team_from_config(args.config)
        print(f"Loaded {count} team members from {args.config}")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
