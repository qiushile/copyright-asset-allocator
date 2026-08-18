#!/usr/bin/env python3
"""Detect code duplication between copyrights at three levels."""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import init_database, get_connection


def normalize_code(code: str) -> str:
    """Normalize code for comparison: remove whitespace, comments."""
    # 移除单行注释
    code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
    # 移除多行注释
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
    # 移除 Python 注释
    code = re.sub(r'#.*$', '', code, flags=re.MULTILINE)
    # 移除空行和多余空白
    lines = [line.strip() for line in code.split('\n') if line.strip()]
    return '\n'.join(lines)


def tokenize_code(code: str) -> list:
    """Simple tokenization: split by whitespace and operators."""
    tokens = re.findall(r'[a-zA-Z_]\w*|[0-9]+(?:\.[0-9]+)?|[^\s\w]', code)
    return tokens


def _line_at(code: str, index: int) -> int:
    """1-based line number of character ``index`` in ``code``."""
    return code.count('\n', 0, index) + 1


def _end_line(start_line: int, func_code: str) -> int:
    """1-based line of the function's last non-blank character."""
    return start_line + func_code.rstrip().count('\n')


def extract_functions(code: str, language: str) -> list:
    """Extract function-level code blocks.

    Each block carries 1-based inclusive ``start_line``/``end_line`` so
    duplicate findings can point a human reviewer at exact locations.
    """
    functions = []
    
    if language in ('Java', 'JavaScript', 'TypeScript', 'C', 'C++', 'C#', 'Go'):
        # C-style languages: function pattern
        pattern = r'(?:public|private|protected|static|final|async|function)?\s*(?:\w+\s+)?(\w+)\s*\([^)]*\)\s*(?:throws\s+\w+\s*)?\{'
        matches = list(re.finditer(pattern, code))
        
        for i, match in enumerate(matches):
            start = match.start()
            brace_count = 0
            end = start
            for j, char in enumerate(code[start:], start):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = j + 1
                        break
            
            func_code = code[start:end]
            # Optional modifier/return-type prefixes can start matching on
            # leading whitespace (including the newline of a previous line).
            # Strip it so reported ranges start at the function's real
            # first token, not on trailing whitespace of an earlier line.
            stripped = func_code.lstrip()
            leading = len(func_code) - len(stripped)
            func_code = stripped
            start_line = _line_at(code, start + leading)
            functions.append({
                'name': match.group(1),
                'code': func_code,
                'lines': func_code.count('\n') + 1,
                'start_line': start_line,
                'end_line': _end_line(start_line, func_code),
            })

    elif language == 'Python':
        # Python: def pattern
        pattern = r'(?:async\s+)?def\s+(\w+)\s*\([^)]*\):'
        matches = list(re.finditer(pattern, code))
        
        for i, match in enumerate(matches):
            start = match.start()
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(code)
            
            func_code = code[start:end]
            start_line = _line_at(code, start)
            functions.append({
                'name': match.group(1),
                'code': func_code,
                'lines': func_code.count('\n') + 1,
                'start_line': start_line,
                'end_line': _end_line(start_line, func_code),
            })

    return functions


def compute_function_fingerprint(func_code: str) -> str:
    """Compute fingerprint for a function."""
    normalized = normalize_code(func_code)
    tokens = tokenize_code(normalized)
    token_str = '|'.join(tokens[:100])
    return hashlib.md5(token_str.encode()).hexdigest()


def detect_path_overlap(files_by_copyright: dict) -> list:
    """Level 1: Detect path overlap."""
    overlaps = []
    copyright_ids = list(files_by_copyright.keys())
    
    for i in range(len(copyright_ids)):
        for j in range(i + 1, len(copyright_ids)):
            cr1 = copyright_ids[i]
            cr2 = copyright_ids[j]
            
            paths1 = set(f['path'] for f in files_by_copyright[cr1])
            paths2 = set(f['path'] for f in files_by_copyright[cr2])
            
            common = paths1 & paths2
            if common:
                overlaps.append({
                    'copyright_1': cr1,
                    'copyright_2': cr2,
                    'type': 'path_overlap',
                    'count': len(common),
                    'files': list(common),
                })
    
    return overlaps


def detect_exact_file_overlap(files_by_copyright: dict) -> list:
    """Level 2: Detect exact file content overlap (same SHA-256)."""
    overlaps = []
    
    # 构建哈希到文件的映射
    hash_to_files = defaultdict(list)
    for cr_id, files in files_by_copyright.items():
        for file in files:
            hash_to_files[file['sha256']].append({
                'copyright_id': cr_id,
                'path': file['path'],
            })
    
    # 检查同一哈希出现在不同软著中
    for sha256, file_list in hash_to_files.items():
        cr_ids = set(f['copyright_id'] for f in file_list)
        if len(cr_ids) > 1:
            overlaps.append({
                'type': 'exact_file_overlap',
                'sha256': sha256,
                'copyrights': list(cr_ids),
                'files': file_list,
            })
    
    return overlaps


def detect_similar_code(files_by_copyright: dict, repo_path: Path) -> list:
    """Level 3: Detect similar code blocks between copyrights."""
    similar_pairs = []
    copyright_ids = list(files_by_copyright.keys())
    
    # 为每个软著提取函数指纹
    fingerprints_by_copyright = {}
    
    for cr_id, files in files_by_copyright.items():
        fingerprints = {}
        for file in files:
            file_path = repo_path / file['path']
            if not file_path.exists():
                continue
            
            try:
                code = file_path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            
            functions = extract_functions(code, file['language'])
            for func in functions:
                if func['lines'] < 5:  # 忽略太短的函数
                    continue
                fp = compute_function_fingerprint(func['code'])
                fingerprints[fp] = {
                    'file': file['path'],
                    'function': func['name'],
                    'lines': func['lines'],
                    'start_line': func.get('start_line'),
                    'end_line': func.get('end_line'),
                }
        
        fingerprints_by_copyright[cr_id] = fingerprints
    
    # 比较不同软著间的指纹
    for i in range(len(copyright_ids)):
        for j in range(i + 1, len(copyright_ids)):
            cr1 = copyright_ids[i]
            cr2 = copyright_ids[j]
            
            fps1 = fingerprints_by_copyright.get(cr1, {})
            fps2 = fingerprints_by_copyright.get(cr2, {})
            
            common_fps = set(fps1.keys()) & set(fps2.keys())
            
            if common_fps:
                similar_functions = []
                total_lines = 0
                max_lines = 0
                
                for fp in common_fps:
                    f1 = fps1[fp]
                    f2 = fps2[fp]
                    similar_functions.append({
                        # Legacy keys (kept for compatibility with existing
                        # reports/tests):
                        'function_1': f"{f1['file']}::{f1['function']}",
                        'function_2': f"{f2['file']}::{f2['function']}",
                        'lines': max(f1['lines'], f2['lines']),
                        # Reviewer-facing evidence:
                        'type': 'similar_function',
                        'file_a': f1['file'],
                        'file_b': f2['file'],
                        'function_a': f1['function'],
                        'function_b': f2['function'],
                        'lines_a': [f1['start_line'], f1['end_line']],
                        'lines_b': [f2['start_line'], f2['end_line']],
                    })
                    total_lines += max(f1['lines'], f2['lines'])
                    max_lines = max(max_lines, f1['lines'], f2['lines'])
                
                # 计算相似度
                total_funcs = max(len(fps1), len(fps2), 1)
                similarity_ratio = len(common_fps) / total_funcs
                
                similar_pairs.append({
                    'copyright_1': cr1,
                    'copyright_2': cr2,
                    'type': 'similar_code',
                    'similar_function_count': len(common_fps),
                    'total_similar_lines': total_lines,
                    'max_similar_block_lines': max_lines,
                    'similarity_ratio': similarity_ratio,
                    'similar_functions': similar_functions[:10],
                })
    
    return similar_pairs


def detect_duplicates(commit_id: int, repo_path: Path) -> dict:
    """Run all three levels of duplicate detection."""
    # 获取所有软著的文件
    files_by_copyright = {}
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, copyright_id FROM copyrights WHERE commit_id = ?
        """, (commit_id,))
        copyrights = cursor.fetchall()
        
        for cr_db_id, cr_id in copyrights:
            cursor.execute("""
                SELECT f.id, f.file_path, f.sha256, f.language, f.effective_lines
                FROM files f
                JOIN allocations a ON f.id = a.file_id
                WHERE a.commit_id = ? AND a.copyright_id = ?
            """, (commit_id, cr_db_id))
            
            files = cursor.fetchall()
            files_by_copyright[cr_id] = [
                {
                    'id': f[0],
                    'path': f[1],
                    'sha256': f[2],
                    'language': f[3],
                    'effective_lines': f[4],
                }
                for f in files
            ]
    
    # 执行三级检测
    path_overlaps = detect_path_overlap(files_by_copyright)
    exact_overlaps = detect_exact_file_overlap(files_by_copyright)
    similar_overlaps = detect_similar_code(files_by_copyright, repo_path)
    
    return {
        'commit_id': commit_id,
        'path_overlaps': path_overlaps,
        'exact_file_overlaps': exact_overlaps,
        'similar_code_blocks': similar_overlaps,
        'summary': {
            'total_path_overlaps': sum(o['count'] for o in path_overlaps),
            'total_exact_overlaps': len(exact_overlaps),
            'total_similar_pairs': len(similar_overlaps),
        }
    }


def save_report(report: dict, output_path: Path):
    """Save duplicate detection report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # 生成可读报告
    txt_path = output_path.with_suffix('.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("重合检测报告\n")
        f.write("=" * 60 + "\n\n")
        
        f.write(f"路径重合: {report['summary']['total_path_overlaps']} 个文件\n")
        f.write(f"完全相同文件: {report['summary']['total_exact_overlaps']} 个\n")
        f.write(f"相似代码对: {report['summary']['total_similar_pairs']} 对\n\n")
        
        if report['path_overlaps']:
            f.write("路径重合详情:\n")
            for overlap in report['path_overlaps']:
                f.write(f"  {overlap['copyright_1']} <-> {overlap['copyright_2']}: {overlap['count']} 个文件\n")
                for file in overlap['files'][:5]:
                    f.write(f"    - {file}\n")
            f.write("\n")
        
        if report['exact_file_overlaps']:
            f.write("完全相同文件详情:\n")
            for overlap in report['exact_file_overlaps']:
                f.write(f"  SHA256: {overlap['sha256'][:16]}...\n")
                for file in overlap['files']:
                    f.write(f"    - [{file['copyright_id']}] {file['path']}\n")
            f.write("\n")
        
        if report['similar_code_blocks']:
            f.write("相似代码详情:\n")
            for overlap in report['similar_code_blocks']:
                f.write(f"  {overlap['copyright_1']} <-> {overlap['copyright_2']}:\n")
                f.write(f"    相似函数: {overlap['similar_function_count']} 个\n")
                f.write(f"    相似行数: {overlap['total_similar_lines']} 行\n")
                f.write(f"    最大代码块: {overlap['max_similar_block_lines']} 行\n")
                f.write(f"    相似度: {overlap['similarity_ratio'] * 100:.1f}%\n")
                
                for func in overlap['similar_functions'][:5]:
                    if func.get('lines_a') and func.get('lines_b'):
                        f.write(
                            f"    - {func['file_a']}::{func['function_a']} "
                            f"(行 {func['lines_a'][0]}-{func['lines_a'][1]}) <-> "
                            f"{func['file_b']}::{func['function_b']} "
                            f"(行 {func['lines_b'][0]}-{func['lines_b'][1]})\n"
                        )
                    else:  # legacy report without ranges
                        f.write(f"    - {func['function_1']} <-> {func['function_2']} ({func['lines']} 行)\n")
            f.write("\n")


def main():
    parser = argparse.ArgumentParser(description='Detect code duplication between copyrights')
    parser.add_argument('--commit-id', type=int, required=True, help='Commit ID')
    parser.add_argument('--repo-path', type=Path, required=True, help='Repository path')
    parser.add_argument('--output', type=Path, default=Path('copyright/inventory/duplicate-report.json'),
                       help='Output path for report')
    
    args = parser.parse_args()
    
    init_database()
    
    report = detect_duplicates(args.commit_id, args.repo_path)
    save_report(report, args.output)
    
    print(f"\nDuplicate detection complete:")
    print(f"  Path overlaps: {report['summary']['total_path_overlaps']}")
    print(f"  Exact file overlaps: {report['summary']['total_exact_overlaps']}")
    print(f"  Similar code pairs: {report['summary']['total_similar_pairs']}")
    print(f"\nReport: {args.output}")
    
    # 如果有路径重合或完全相同文件，返回错误
    if report['summary']['total_path_overlaps'] > 0 or report['summary']['total_exact_overlaps'] > 0:
        print("\n❌ CRITICAL: Path or exact file overlaps detected!")
        sys.exit(1)
    elif report['summary']['total_similar_pairs'] > 0:
        print("\n⚠️ WARNING: Similar code blocks detected, manual review recommended")


if __name__ == '__main__':
    main()
