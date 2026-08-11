#!/usr/bin/env python3
"""Feature 1: Parse existing copyright documents and mark used code.

Supports: Markdown (.md), Word (.docx), PDF (.pdf)
Extracts code content from previously applied copyright materials,
then matches against repository files to mark them as "used".
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from database import init_database, get_connection


# ============================================================
# Document Parsers
# ============================================================

def extract_code_from_markdown(file_path: Path) -> List[str]:
    """Extract code blocks and inline code from markdown."""
    content = file_path.read_text(encoding='utf-8', errors='ignore')
    code_lines = []
    
    # 提取 fenced code blocks (```...```)
    fenced_pattern = re.compile(r'```[\w]*\n(.*?)```', re.DOTALL)
    for match in fenced_pattern.finditer(content):
        block = match.group(1)
        code_lines.extend(line for line in block.split('\n') if line.strip())
    
    # 提取 indented code blocks (4 spaces)
    in_code_block = False
    for line in content.split('\n'):
        if line.startswith('    ') and not line.startswith('     '):
            stripped = line[4:]
            if stripped.strip():
                code_lines.append(stripped)
    
    # 如果没有 code blocks，尝试提取所有非标题、非列表行（可能是纯代码文档）
    if not code_lines:
        for line in content.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                continue
            if stripped.startswith(('-', '*', '>')):
                continue
            if stripped.startswith('|') and stripped.endswith('|'):
                continue
            # 看起来像代码的行
            if any(kw in stripped for kw in ['def ', 'class ', 'import ', 'function ', 
                                              'public ', 'private ', 'return ', 'if (',
                                              'for (', 'while (', '{', '}', '=>', '->']):
                code_lines.append(stripped)
    
    return code_lines


def extract_code_from_docx(file_path: Path) -> List[str]:
    """Extract code content from Word document."""
    try:
        from docx import Document
    except ImportError:
        print("Error: python-docx not installed. Run: pip install python-docx")
        return []
    
    doc = Document(str(file_path))
    code_lines = []
    
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        
        # 检查是否是代码样式
        style_name = para.style.name.lower() if para.style else ""
        is_code_style = any(kw in style_name for kw in ['code', 'mono', 'source', 'program'])
        
        # 检查字体是否是等宽字体
        is_mono_font = False
        for run in para.runs:
            if run.font and run.font.name:
                font_name = run.font.name.lower()
                if any(f in font_name for f in ['courier', 'consolas', 'mono', 'menlo']):
                    is_mono_font = True
                    break
        
        if is_code_style or is_mono_font:
            code_lines.append(text)
        else:
            # 启发式：看起来像代码的行
            if _looks_like_code(text):
                code_lines.append(text)
    
    # 也检查表格中的代码
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text and _looks_like_code(text):
                    code_lines.extend(
                        line.strip() for line in text.split('\n') if line.strip()
                    )
    
    return code_lines


def extract_code_from_pdf(file_path: Path) -> List[str]:
    """Extract code content from PDF."""
    code_lines = []
    
    # 尝试使用 PyPDF2 / pypdf
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            print("Warning: pypdf/PyPDF2 not installed. Trying pdftotext...")
            return _extract_pdf_via_cli(file_path)
    
    try:
        reader = PdfReader(str(file_path))
        for page in reader.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split('\n'):
                stripped = line.strip()
                if stripped and _looks_like_code(stripped):
                    code_lines.append(stripped)
    except Exception as e:
        print(f"Warning: PDF extraction error: {e}")
    
    return code_lines


def _extract_pdf_via_cli(file_path: Path) -> List[str]:
    """Fallback: use pdftotext CLI tool."""
    import subprocess
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', str(file_path), '-'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            code_lines = []
            for line in result.stdout.split('\n'):
                stripped = line.strip()
                if stripped and _looks_like_code(stripped):
                    code_lines.append(stripped)
            return code_lines
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    print(f"Warning: Cannot extract PDF content from {file_path}")
    print("  Install pypdf: pip install pypdf")
    return []


def _looks_like_code(text: str) -> bool:
    """Heuristic: does this line look like source code?"""
    code_indicators = [
        r'^\s*(def |class |import |from .+ import)',  # Python
        r'^\s*(public |private |protected |static |final )',  # Java/C#
        r'^\s*(function |const |let |var |export )',  # JS/TS
        r'^\s*(func |package |type |struct )',  # Go
        r'^\s*(fn |pub |mod |use |impl )',  # Rust
        r'[{};]\s*$',  # C-style ending
        r'^\s*(if|for|while|switch|return|try|catch)\s*[\(]',
        r'^\s*#\s*(include|define|ifdef|pragma)',  # C preprocessor
        r'^\s*<\?php',
        r'^\s*(@\w+|@\{)',  # Annotations
        r'=>\s*[{(]',  # Arrow functions
        r'^\s*\w+\s*=\s*[\[\{\'\"]',  # Assignments
        r'^\s*(self|this)\.',  # Member access
        r'^\s*\w+\.\w+\(',  # Method calls
        r'^\s*//',  # Comments
        r'^\s*/\*',
        r'^\s*\*',
    ]
    
    for pattern in code_indicators:
        if re.search(pattern, text):
            return True
    
    # 缩进 + 非自然语言特征
    if text.startswith('    ') and not text[4].isupper():
        if any(c in text for c in ['(', ')', '{', '}', '=', ';', '[', ']']):
            return True
    
    return False


# ============================================================
# Code Matching
# ============================================================

def normalize_for_matching(code: str) -> str:
    """Normalize code line for fuzzy matching."""
    # 去除首尾空白
    code = code.strip()
    # 压缩连续空白
    code = re.sub(r'\s+', ' ', code)
    return code


def build_file_code_index(commit_id: int) -> Dict[int, List[str]]:
    """Build normalized code index for all files in commit.
    Returns: {file_id: [normalized_lines]}
    """
    index = {}
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, file_path FROM files WHERE commit_id = ?
        """, (commit_id,))
        files = cursor.fetchall()
    
    # 需要仓库路径来读取文件内容
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.path FROM commits c
            JOIN repositories r ON c.repository_id = r.id
            WHERE c.id = ?
        """, (commit_id,))
        row = cursor.fetchone()
    
    if not row:
        return index
    
    repo_path = Path(row[0])
    
    for file_id, file_path in files:
        full_path = repo_path / file_path
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')
            lines = [normalize_for_matching(l) for l in content.split('\n') if l.strip()]
            index[file_id] = lines
        except Exception:
            continue
    
    return index


def match_used_code(
    used_code_lines: List[str],
    file_code_index: Dict[int, List[str]],
    min_match_lines: int = 3,
    threshold: float = 0.6
) -> List[Dict]:
    """Match extracted code against repository files.
    
    Returns list of matches: [{file_id, match_lines, match_ratio, copyright_name}]
    """
    # 构建 used code 的 normalized set
    used_normalized = set()
    for line in used_code_lines:
        norm = normalize_for_matching(line)
        if len(norm) > 5:  # 忽略太短的行
            used_normalized.add(norm)
    
    if not used_normalized:
        return []
    
    matches = []
    
    for file_id, file_lines in file_code_index.items():
        if not file_lines:
            continue
        
        # 计算匹配行数
        file_line_set = set(file_lines)
        matched = used_normalized & file_line_set
        match_count = len(matched)
        
        if match_count < min_match_lines:
            continue
        
        # 匹配比例（相对于 used code 和文件两者的较小值）
        ratio = match_count / min(len(used_normalized), len(file_line_set))
        
        if ratio >= threshold:
            matches.append({
                'file_id': file_id,
                'match_lines': match_count,
                'match_ratio': ratio,
                'total_used_lines': len(used_normalized),
                'total_file_lines': len(file_line_set),
            })
    
    # 按匹配比例排序
    matches.sort(key=lambda x: x['match_ratio'], reverse=True)
    return matches


# ============================================================
# Main Logic
# ============================================================

def mark_used_code(
    commit_id: int,
    document_path: Path,
    copyright_name: str,
    min_match_lines: int = 3,
    threshold: float = 0.6,
    verbose: bool = False
) -> Dict:
    """Parse a copyright document and mark matched files as used.
    
    Args:
        commit_id: Current commit ID
        document_path: Path to the copyright document (md/docx/pdf)
        copyright_name: Name of the previously applied copyright
        min_match_lines: Minimum matching lines to consider a match
        threshold: Minimum match ratio (0-1) to mark as used
        verbose: Print detailed output
    
    Returns:
        dict with marking results
    """
    # 1. 提取代码
    ext = document_path.suffix.lower()
    
    if ext == '.md':
        code_lines = extract_code_from_markdown(document_path)
    elif ext in ('.docx', '.doc'):
        code_lines = extract_code_from_docx(document_path)
    elif ext == '.pdf':
        code_lines = extract_code_from_pdf(document_path)
    else:
        # 尝试作为纯文本/代码文件处理
        try:
            content = document_path.read_text(encoding='utf-8', errors='ignore')
            code_lines = [l.strip() for l in content.split('\n') if l.strip()]
        except Exception:
            code_lines = []
    
    if verbose:
        print(f"Extracted {len(code_lines)} code lines from {document_path.name}")
    
    if not code_lines:
        return {
            'document': str(document_path),
            'copyright_name': copyright_name,
            'extracted_lines': 0,
            'matched_files': 0,
            'marked_files': [],
        }
    
    # 2. 构建文件代码索引
    if verbose:
        print("Building file code index...")
    file_code_index = build_file_code_index(commit_id)
    
    if verbose:
        print(f"Indexed {len(file_code_index)} files")
    
    # 3. 匹配
    matches = match_used_code(code_lines, file_code_index, min_match_lines, threshold)
    
    if verbose:
        print(f"Found {len(matches)} matching files")
    
    # 4. 标记为已使用
    marked_files = []
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        for match in matches:
            file_id = match['file_id']
            
            # 获取文件路径
            cursor.execute("SELECT file_path FROM files WHERE id = ?", (file_id,))
            row = cursor.fetchone()
            file_path = row[0] if row else "unknown"
            
            # 插入 used_files 记录
            cursor.execute("""
                INSERT OR IGNORE INTO used_files 
                (commit_id, file_id, copyright_name, source_document, match_type)
                VALUES (?, ?, ?, ?, ?)
            """, (commit_id, file_id, copyright_name, str(document_path), 'content_match'))
            
            # 插入 used_code_markers 详细记录
            cursor.execute("""
                INSERT INTO used_code_markers
                (commit_id, file_id, copyright_name, source_document, code_hash)
                VALUES (?, ?, ?, ?, ?)
            """, (commit_id, file_id, copyright_name, str(document_path),
                  hashlib.md5(str(match['match_lines']).encode()).hexdigest()))
            
            marked_files.append({
                'file_id': file_id,
                'file_path': file_path,
                'match_lines': match['match_lines'],
                'match_ratio': match['match_ratio'],
            })
            
            if verbose:
                print(f"  ✓ Marked: {file_path} (ratio: {match['match_ratio']:.1%})")
    
    return {
        'document': str(document_path),
        'copyright_name': copyright_name,
        'extracted_lines': len(code_lines),
        'matched_files': len(marked_files),
        'marked_files': marked_files,
    }


def get_used_files_report(commit_id: int) -> Dict:
    """Get report of all used files."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT uf.file_id, f.file_path, uf.copyright_name, uf.source_document, 
                   uf.match_type, uf.marked_at
            FROM used_files uf
            JOIN files f ON uf.file_id = f.id
            WHERE uf.commit_id = ?
            ORDER BY uf.copyright_name, f.file_path
        """, (commit_id,))
        rows = cursor.fetchall()
    
    by_copyright = {}
    for file_id, file_path, cr_name, source_doc, match_type, marked_at in rows:
        if cr_name not in by_copyright:
            by_copyright[cr_name] = []
        by_copyright[cr_name].append({
            'file_id': file_id,
            'file_path': file_path,
            'source_document': source_doc,
            'match_type': match_type,
        })
    
    return {
        'total_used_files': len(rows),
        'by_copyright': by_copyright,
    }


def mark_used_paths_from_csv(
    commit_id: int,
    manifest_path: Path,
    copyright_name: str,
    path_column: str = 'path',
) -> Dict:
    """Mark exact repository paths from an evidence CSV as previously used.

    A cell may contain multiple pipe-delimited paths.  Unlike fuzzy document
    matching, this operation never infers a match from similar code.
    """
    with manifest_path.open('r', encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or path_column not in reader.fieldnames:
            raise ValueError(
                f"CSV column not found: {path_column}; "
                f"available: {', '.join(reader.fieldnames or [])}"
            )
        requested_paths = []
        for row in reader:
            requested_paths.extend(
                item.strip() for item in (row.get(path_column) or '').split('|') if item.strip()
            )

    requested_paths = sorted(set(requested_paths))
    marked_files = []
    unmatched_paths = []
    with get_connection() as conn:
        cursor = conn.cursor()
        for file_path in requested_paths:
            cursor.execute(
                "SELECT id, sha256 FROM files WHERE commit_id = ? AND file_path = ?",
                (commit_id, file_path),
            )
            row = cursor.fetchone()
            if not row:
                unmatched_paths.append(file_path)
                continue
            file_id, file_sha256 = row
            cursor.execute(
                """
                INSERT INTO used_files
                (commit_id, file_id, copyright_name, source_document, match_type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(commit_id, file_id, copyright_name) DO UPDATE SET
                    source_document = excluded.source_document,
                    match_type = excluded.match_type
                """,
                (
                    commit_id,
                    file_id,
                    copyright_name,
                    str(manifest_path),
                    'manifest_path',
                ),
            )
            cursor.execute(
                """
                INSERT INTO used_code_markers
                (commit_id, file_id, copyright_name, source_document, code_hash)
                SELECT ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM used_code_markers
                    WHERE commit_id = ? AND file_id = ? AND copyright_name = ?
                      AND source_document = ? AND code_hash = ?
                )
                """,
                (
                    commit_id,
                    file_id,
                    copyright_name,
                    str(manifest_path),
                    file_sha256,
                    commit_id,
                    file_id,
                    copyright_name,
                    str(manifest_path),
                    file_sha256,
                ),
            )
            marked_files.append({'file_id': file_id, 'file_path': file_path})

    return {
        'manifest': str(manifest_path),
        'copyright_name': copyright_name,
        'path_column': path_column,
        'requested_paths': len(requested_paths),
        'marked_files': marked_files,
        'unmatched_paths': unmatched_paths,
    }


def main():
    parser = argparse.ArgumentParser(description='Mark code used in previous copyrights')
    parser.add_argument('document', type=Path, help='Path to copyright document (md/docx/pdf)')
    parser.add_argument('--copyright-name', required=True, help='Name of the previous copyright')
    parser.add_argument('--commit-id', type=int, required=True, help='Commit ID')
    parser.add_argument('--min-match-lines', type=int, default=3,
                       help='Minimum matching lines (default: 3)')
    parser.add_argument('--threshold', type=float, default=0.6,
                       help='Match ratio threshold (default: 0.6)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    if not args.document.exists():
        print(f"Error: Document not found: {args.document}")
        sys.exit(1)
    
    init_database()
    
    result = mark_used_code(
        commit_id=args.commit_id,
        document_path=args.document,
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


if __name__ == '__main__':
    main()
