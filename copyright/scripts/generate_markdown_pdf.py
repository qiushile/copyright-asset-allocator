#!/usr/bin/env python3
"""Generate copyright identification materials through Markdown -> PDF -> audit."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path

from weasyprint import HTML

sys.path.insert(0, str(Path(__file__).parent))

from generate_docx import (
    DOCUMENT_FONT_SIZE,
    DOC_LINES_PER_PAGE,
    LINES_PER_PAGE,
    MATERIAL_REQUIREMENTS,
    PROGRAM_FONT_SIZE,
    TOTAL_PAGES,
    _assert_generation_target,
    _build_document_content,
    _normalize_document_rows,
    _validate_document_content,
    _validate_manual_source,
    collect_pure_code_lines,
    paginate_lines,
    read_source_files,
    select_material_rows,
)
from material_requirements import load_material_requirements
from validate_materials import audit_pdf


PAGE_CSS = """
@page {
    size: A4;
    margin: 18mm 15mm 18mm 15mm;
    @top-left {
        content: "__TITLE__";
        font-family: "PingFang SC", "Songti SC", sans-serif;
        font-size: 8pt;
        color: #596773;
    }
    @top-right {
        content: "__LABEL__";
        font-family: "PingFang SC", "Songti SC", sans-serif;
        font-size: 8pt;
        color: #596773;
    }
    @bottom-center {
        content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
        font-family: "PingFang SC", "Songti SC", sans-serif;
        font-size: 8pt;
        color: #596773;
    }
}
html, body { margin: 0; padding: 0; }
.material-page {
    break-after: page;
    page-break-after: always;
    margin: 0;
    padding: 0;
}
.material-page:last-child { break-after: auto; page-break-after: auto; }
.row { margin: 0; padding: 0; overflow: hidden; }
"""


PROGRAM_CSS = PAGE_CSS + """
body {
    font-family: "Courier New", "SF Mono", Menlo, Monaco, "PingFang SC", monospace;
    font-size: __FONT_SIZE__pt;
    line-height: 1.77;
    color: #111;
}
.row { white-space: pre; height: 1.77em; }
"""


DOCUMENT_CSS = PAGE_CSS + """
body {
    font-family: "Songti SC", "Noto Serif CJK SC", SimSun, serif;
    font-size: __FONT_SIZE__pt;
    line-height: 2.28;
    color: #202830;
}
.row { white-space: nowrap; height: 2.28em; }
.heading { font-weight: 700; color: #17324d; }
"""


def _manual_path(workspace_dir: Path, manifest: dict, explicit: Path | None) -> Path:
    copyright_id = str(manifest.get('copyrightId', workspace_dir.name))
    candidates = [
        explicit,
        workspace_dir / 'SOFTWARE_MANUAL.md',
        workspace_dir.parents[1] / 'manuals' / f'{copyright_id}.md',
    ]
    selected = next(
        (Path(path) for path in candidates if path and Path(path).is_file()),
        None,
    )
    if selected is None:
        raise FileNotFoundError(
            '缺少系统专用说明书。请通过 --manual 指定 Markdown 文件，或放置到 '
            f'{workspace_dir / "SOFTWARE_MANUAL.md"} / '
            f'{workspace_dir.parents[1] / "manuals" / f"{copyright_id}.md"}'
        )
    return selected


def _display_manual_row(row: str) -> tuple[str, bool]:
    text = row.strip()
    heading = re.match(r'^(#{1,6})\s+(.+)$', text)
    is_heading = bool(heading)
    if heading:
        text = heading.group(2)
    text = re.sub(r'^\*\*(.+?)\*\*$', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = text.replace('`', '')
    if text.startswith('- '):
        text = f'• {text[2:]}'
    if text.startswith('|') and text.endswith('|'):
        text = ' ｜ '.join(part.strip() for part in text.strip('|').split('|'))
    return text, is_heading


def _rows_html(pages: list[list[str]], material_type: str) -> str:
    page_fragments = []
    for page in pages:
        rows = []
        for row in page:
            if material_type == 'document':
                display, is_heading = _display_manual_row(row)
                css_class = 'row heading' if is_heading else 'row'
            else:
                display = row
                css_class = 'row'
            rows.append(f'<div class="{css_class}">{html.escape(display)}</div>')
        page_fragments.append('<section class="material-page">' + ''.join(rows) + '</section>')
    return ''.join(page_fragments)


def render_rows_pdf(
    rows: list[str],
    output_path: Path,
    material_type: str,
    software_name: str,
    requirements: dict | None = None,
) -> dict:
    rules = requirements or MATERIAL_REQUIREMENTS
    lines_per_page = LINES_PER_PAGE if material_type == 'program' else DOC_LINES_PER_PAGE
    font_size = PROGRAM_FONT_SIZE if material_type == 'program' else DOCUMENT_FONT_SIZE
    pages = paginate_lines(rows, lines_per_page)
    if len(pages) != TOTAL_PAGES or any(len(page) != lines_per_page for page in pages):
        raise AssertionError(f'{material_type}材料必须恰好形成{TOTAL_PAGES}个完整页面')
    css_template = PROGRAM_CSS if material_type == 'program' else DOCUMENT_CSS
    label = '程序鉴别材料' if material_type == 'program' else '文档鉴别材料'
    css = (
        css_template.replace('__TITLE__', software_name.replace('"', ''))
        .replace('__LABEL__', label)
        .replace('__FONT_SIZE__', f'{font_size:.2f}')
    )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{css}</style></head>
<body>{_rows_html(pages, material_type)}</body></html>"""
    rendered = HTML(string=document, base_url=str(output_path.parent)).render()
    if len(rendered.pages) != TOTAL_PAGES:
        raise ValueError(
            f'Markdown渲染后应为{TOTAL_PAGES}页，实际{len(rendered.pages)}页；'
            '请检查字号、行宽或分页样式'
        )
    rendered.write_pdf(str(output_path))
    report = audit_pdf(output_path, material_type, rules)
    if not report['passed']:
        messages = '；'.join(issue['message'] for issue in report['issues'][:10])
        raise ValueError(f'最终PDF未通过复核：{messages}')
    return report


def generate_program_markdown_pdf(
    workspace_dir: Path,
    output_dir: Path,
    software_name: str,
    version: str,
    requirements: dict | None = None,
) -> tuple[Path, Path]:
    manifest = json.loads(
        (workspace_dir / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8')
    )
    rules = requirements or MATERIAL_REQUIREMENTS
    _assert_generation_target(manifest, output_dir, software_name, rules)
    files_content = read_source_files(workspace_dir, manifest)
    if not files_content:
        raise ValueError('工作区中没有可用于程序鉴别材料的真实源文件')
    all_rows = collect_pure_code_lines(files_content)
    selected_rows = select_material_rows(all_rows, LINES_PER_PAGE)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / '源代码文档.md'
    pdf_path = output_dir / '源代码文档.pdf'
    with tempfile.TemporaryDirectory(prefix='.program-material-', dir=output_dir) as stage:
        staged_markdown = Path(stage) / markdown_path.name
        staged_pdf = Path(stage) / pdf_path.name
        staged_markdown.write_text('\n'.join(selected_rows) + '\n', encoding='utf-8')
        markdown_rows = [
            line
            for line in staged_markdown.read_text(encoding='utf-8').splitlines()
            if line.strip()
        ]
        report = render_rows_pdf(
            markdown_rows,
            staged_pdf,
            'program',
            software_name,
            rules,
        )
        staged_markdown.replace(markdown_path)
        staged_pdf.replace(pdf_path)
        report['path'] = str(pdf_path)

    manifest_output = output_dir / f'{software_name}-源代码清单.json'
    manifest_output.write_text(
        json.dumps(
            {
                'software_name': software_name,
                'version': version,
                'source_format': 'markdown',
                'rendered_format': 'pdf',
                'total_files': len(files_content),
                'total_effective_print_rows': len(all_rows),
                'output_pages': report['page_count'],
                'lines_per_page': LINES_PER_PAGE,
                'line_numbers': False,
                'files': [
                    {
                        'path': item['path'],
                        'language': item['language'],
                        'total_lines': item['total_lines'],
                        'effective_lines': item['effective_line_count'],
                    }
                    for item in files_content
                ],
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
        encoding='utf-8',
    )
    return markdown_path, pdf_path


def generate_document_markdown_pdf(
    workspace_dir: Path,
    output_dir: Path,
    software_name: str,
    version: str,
    description: str,
    manual_path: Path | None = None,
    requirements: dict | None = None,
) -> tuple[Path, Path]:
    manifest = json.loads(
        (workspace_dir / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8')
    )
    rules = requirements or MATERIAL_REQUIREMENTS
    _assert_generation_target(manifest, output_dir, software_name, rules)
    files_content = read_source_files(workspace_dir, manifest)
    if not files_content:
        raise ValueError('工作区中没有与说明书对应的真实源文件')
    selected_manual = _manual_path(workspace_dir, manifest, manual_path)
    manual_text = selected_manual.read_text(encoding='utf-8', errors='ignore')
    _validate_manual_source(selected_manual, manual_text)
    full_lines = _build_document_content(
        software_name,
        version,
        description,
        files_content,
        manual_text.splitlines(),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / '软件说明书.md'
    pdf_path = output_dir / '软件说明书.pdf'
    with tempfile.TemporaryDirectory(prefix='.document-material-', dir=output_dir) as stage:
        staged_markdown = Path(stage) / markdown_path.name
        staged_pdf = Path(stage) / pdf_path.name
        staged_markdown.write_text(
            '\n'.join(full_lines).rstrip() + '\n',
            encoding='utf-8',
        )
        rendered_markdown = staged_markdown.read_text(encoding='utf-8')
        _validate_manual_source(staged_markdown, rendered_markdown)
        normalized_rows = _normalize_document_rows(rendered_markdown.splitlines())
        _validate_document_content(normalized_rows)
        selected_rows = select_material_rows(normalized_rows, DOC_LINES_PER_PAGE)
        render_rows_pdf(
            selected_rows,
            staged_pdf,
            'document',
            software_name,
            rules,
        )
        staged_markdown.replace(markdown_path)
        staged_pdf.replace(pdf_path)
    return markdown_path, pdf_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manual', type=Path)
    parser.add_argument('--requirements', type=Path)
    parser.add_argument('--code-only', action='store_true')
    args = parser.parse_args()

    manifest = json.loads(
        (args.workspace / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8')
    )
    output_dir = args.output or (
        args.workspace.parents[1] / 'outputs' / str(manifest['copyrightId'])
    )
    requirements = load_material_requirements(args.requirements)
    generate_program_markdown_pdf(
        args.workspace,
        output_dir,
        manifest['softwareName'],
        manifest['version'],
        requirements,
    )
    if not args.code_only:
        generate_document_markdown_pdf(
            args.workspace,
            output_dir,
            manifest['softwareName'],
            manifest['version'],
            manifest.get('description', ''),
            args.manual,
            requirements,
        )


if __name__ == '__main__':
    main()
