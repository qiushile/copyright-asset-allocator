#!/usr/bin/env python3
"""Generate DOCX materials for software copyright application."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:
    from docx import Document
    from docx.shared import Pt, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.style import WD_STYLE_TYPE
except ImportError:
    print("Error: python-docx not installed. Run: pip install python-docx")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))


LINES_PER_PAGE = 50
FRONT_PAGES = 30
BACK_PAGES = 30
TOTAL_PAGES_THRESHOLD = 60


def create_code_document(title: str, version: str) -> Document:
    """Create a new DOCX document for code material."""
    doc = Document()
    
    # 设置页面边距
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(2.5)
    
    # 添加页眉
    section = doc.sections[0]
    header = section.header
    header_para = header.paragraphs[0]
    header_para.text = f"{title} {version}"
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # 设置页眉字体
    for run in header_para.runs:
        run.font.size = Pt(9)
    
    return doc


def read_source_files(workspace_dir: Path, manifest: dict) -> list[dict]:
    """Read all source files from workspace."""
    source_dir = workspace_dir / 'source'
    files_content = []
    
    for file_info in manifest['allowedFiles']:
        file_path = source_dir / file_info['path']
        
        if not file_path.exists():
            continue
        
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            lines = content.split('\n')
            
            # 过滤空行和注释行（可选）
            effective_lines = [line for line in lines if line.strip()]
            
            files_content.append({
                'path': file_info['path'],
                'language': file_info['language'],
                'lines': lines,
                'effective_lines': effective_lines,
                'total_lines': len(lines),
                'effective_line_count': len(effective_lines),
            })
        except Exception as e:
            print(f"Warning: Could not read {file_path}: {e}")
    
    return files_content


def generate_code_pages(files_content: list[dict], pages_type: str = 'front') -> list[list[str]]:
    """
    Generate code pages (front or back).
    
    Each page has LINES_PER_PAGE lines.
    Front: first FRONT_PAGES pages
    Back: last BACK_PAGES pages
    """
    # 合并所有文件的代码行
    all_lines = []
    
    for file_info in files_content:
        # 添加文件路径注释
        all_lines.append(f"// File: {file_info['path']}")
        all_lines.append("")
        
        # 添加代码行
        for line in file_info['effective_lines']:
            all_lines.append(line)
        
        all_lines.append("")  # 文件间空行
    
    # 计算总页数
    total_lines = len(all_lines)
    total_pages = (total_lines + LINES_PER_PAGE - 1) // LINES_PER_PAGE
    
    # 分页
    pages = []
    for i in range(0, total_lines, LINES_PER_PAGE):
        page_lines = all_lines[i:i + LINES_PER_PAGE]
        # 补齐到 LINES_PER_PAGE 行
        while len(page_lines) < LINES_PER_PAGE:
            page_lines.append("")
        pages.append(page_lines)
    
    if pages_type == 'front':
        return pages[:FRONT_PAGES]
    elif pages_type == 'back':
        return pages[-BACK_PAGES:] if len(pages) >= BACK_PAGES else pages
    else:
        return pages


def write_code_to_docx(doc: Document, pages: list[list[str]]):
    """Write code pages to DOCX document."""
    # 设置默认字体
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Courier New'
    font.size = Pt(9)
    
    for page_idx, page_lines in enumerate(pages):
        for line_idx, line in enumerate(page_lines):
            para = doc.add_paragraph(line)
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.0
            
            # 设置字体
            for run in para.runs:
                run.font.name = 'Courier New'
                run.font.size = Pt(9)
        
        # 每页后添加分页符（除了最后一页）
        if page_idx < len(pages) - 1:
            doc.add_page_break()


def generate_code_material(
    workspace_dir: Path,
    output_dir: Path,
    software_name: str,
    version: str,
):
    """Generate front and back code material DOCX files."""
    # 加载 manifest
    manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    # 读取源文件
    files_content = read_source_files(workspace_dir, manifest)
    
    if not files_content:
        print("Error: No source files found in workspace")
        return
    
    # 计算总行数
    total_lines = sum(f['effective_line_count'] for f in files_content)
    total_pages = (total_lines + LINES_PER_PAGE - 1) // LINES_PER_PAGE
    
    print(f"Total effective lines: {total_lines}")
    print(f"Total pages: {total_pages}")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if total_pages <= TOTAL_PAGES_THRESHOLD:
        # 不足60页，生成全部代码
        print(f"Code less than {TOTAL_PAGES_THRESHOLD} pages, generating all code")
        
        doc = create_code_document(software_name, version)
        all_pages = generate_code_pages(files_content, 'all')
        write_code_to_docx(doc, all_pages)
        
        output_path = output_dir / f"{software_name}-代码材料.docx"
        doc.save(str(output_path))
        print(f"Generated: {output_path}")
        
    else:
        # 生成前30页
        print(f"Generating front {FRONT_PAGES} pages...")
        doc_front = create_code_document(software_name, version)
        front_pages = generate_code_pages(files_content, 'front')
        write_code_to_docx(doc_front, front_pages)
        
        output_path_front = output_dir / f"{software_name}-代码(前{FRONT_PAGES}页).docx"
        doc_front.save(str(output_path_front))
        print(f"Generated: {output_path_front}")
        
        # 生成后30页
        print(f"Generating back {BACK_PAGES} pages...")
        doc_back = create_code_document(software_name, version)
        back_pages = generate_code_pages(files_content, 'back')
        write_code_to_docx(doc_back, back_pages)
        
        output_path_back = output_dir / f"{software_name}-代码(后{BACK_PAGES}页).docx"
        doc_back.save(str(output_path_back))
        print(f"Generated: {output_path_back}")
    
    # 生成代码清单
    manifest_output = output_dir / f"{software_name}-源代码清单.json"
    with open(manifest_output, 'w', encoding='utf-8') as f:
        json.dump({
            'software_name': software_name,
            'version': version,
            'total_files': len(files_content),
            'total_effective_lines': total_lines,
            'files': [
                {
                    'path': f['path'],
                    'language': f['language'],
                    'total_lines': f['total_lines'],
                    'effective_lines': f['effective_line_count'],
                }
                for f in files_content
            ]
        }, f, indent=2, ensure_ascii=False)
    
    print(f"Generated: {manifest_output}")


def main():
    parser = argparse.ArgumentParser(description='Generate DOCX code materials')
    parser.add_argument('workspace', type=Path, help='Workspace directory')
    parser.add_argument('--output', type=Path, help='Output directory (default: workspace/../outputs)')
    
    args = parser.parse_args()
    
    if not args.workspace.exists():
        print(f"Error: Workspace not found: {args.workspace}")
        sys.exit(1)
    
    # 加载 manifest
    manifest_path = args.workspace / 'SOURCE_MANIFEST.json'
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)
    
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    software_name = manifest['softwareName']
    version = manifest['version']
    
    output_dir = args.output or (args.workspace.parent.parent / 'outputs' / manifest['copyrightId'])
    
    print(f"Generating code materials for: {software_name} {version}")
    print(f"Workspace: {args.workspace}")
    print(f"Output: {output_dir}")
    
    generate_code_material(args.workspace, output_dir, software_name, version)


if __name__ == '__main__':
    main()
