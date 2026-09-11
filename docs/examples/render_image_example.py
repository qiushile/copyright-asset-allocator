#!/usr/bin/env python3
"""Generate an offline source explorer and an optional screenshot/PDF preview."""
from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'copyright/scripts'))

from material_requirements import assert_output_path_allowed, load_material_requirements

MODULES = {
    'material_images': {
        'title': '图片与证据', 'tag': '输入约束',
        'description': '让每张图片都能找到来源',
        'features': ['核对图片、图注与文件摘要', '计算正文锚点和入选页码', '打包图片及对应证据'],
    },
    'generate_markdown_pdf': {
        'title': '排版与输出', 'tag': '材料生成',
        'description': '将正文与配图组合成页面',
        'features': ['选择前后各 30 页正文', '等比缩放图片并保留图注', '渲染 PDF 后检查结果'],
    },
    'validate_materials': {
        'title': '材料审计', 'tag': '结果检查',
        'description': '检查最终文件是否完整',
        'features': ['复核页数、行数和字号', '定位漏图、遮挡和图注问题', '核对图片清单及证据摘要'],
    },
}


def inspect_module(name: str, payload: bytes) -> dict:
    """Inspect exactly the bytes archived with the demo, without importing them."""
    tree = ast.parse(payload.decode('utf-8'))
    functions, dependencies = [], set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                'name': node.name, 'line': node.lineno, 'end_line': node.end_lineno,
                'kind': 'async' if isinstance(node, ast.AsyncFunctionDef) else 'def',
                'docstring': ast.get_docstring(node) or '',
            })
        elif isinstance(node, ast.ImportFrom) and node.module in MODULES:
            dependencies.add(node.module)
        elif isinstance(node, ast.Import):
            dependencies.update(alias.name for alias in node.names if alias.name in MODULES)
    return {
        'name': name, **MODULES[name],
        'file': f'copyright/scripts/{name}.py',
        'sha256': hashlib.sha256(payload).hexdigest(),
        'functions': functions, 'dependencies': sorted(dependencies),
    }


def collect_inventory(root: Path, stage: Path) -> dict:
    snapshots = stage / 'sources'
    snapshots.mkdir()
    modules = []
    for name in MODULES:
        payload = (root / 'copyright/scripts' / f'{name}.py').read_bytes()
        (snapshots / f'{name}.py').write_bytes(payload)
        modules.append(inspect_module(name, payload))
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--short=12', 'HEAD'],
                            capture_output=True, text=True, check=False)
    return {
        'schema_version': 1, 'purpose': '源码结构示意图，非运行截图或申报材料',
        'source_basis': '生成时工作区文件快照；以各文件SHA-256为准',
        'git_reference': result.stdout.strip() if result.returncode == 0 else '未提供',
        'modules': modules,
        'function_count': sum(len(module['functions']) for module in modules),
    }


def render_demo(inventory: dict) -> str:
    escape = html.escape
    cards, sections = [], []
    modules = inventory['modules']
    for index, module in enumerate(modules, 1):
        cards.append(f'''<article class="module-card">
<div class="card-top"><span class="step">0{index}</span><span>{escape(module['tag'])}</span></div>
<h2>{escape(module['title'])}</h2><p class="description">{escape(module['description'])}</p>
<ul>{''.join('<li>' + escape(item) + '</li>' for item in module['features'])}</ul>
<div class="card-bottom"><code>{escape(module['name'])}.py</code>
<span>{len(module['functions'])} 个顶层函数</span></div></article>''')
        rows = []
        for function in module['functions']:
            summary = function['docstring'].split('\n', 1)[0] or '源码未提供 docstring'
            rows.append(f'''<tr class="function-row"><td><code>{escape(function['name'])}</code>
<span class="function-kind">{function['kind']}</span></td><td class="line">{function['line']}–{function['end_line']}</td>
<td>{escape(summary)}</td></tr>''')
        sections.append(f'''<section class="source-section" data-module="{escape(module['name'])}">
<div class="section-title"><div><h3>{escape(module['title'])}</h3>
<a href="sources/{escape(module['name'])}.py" download>下载 {escape(module['name'])}.py 源码快照 ↓</a></div>
<span class="count">{len(rows)} 个函数</span></div>
<p class="hash">SHA-256 <code>{module['sha256']}</code></p>
<div class="table-scroll"><table><thead><tr><th scope="col">顶层函数</th><th scope="col">源码行</th><th scope="col">源码说明</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></section>''')
    edges = []
    titles = {module['name']: module['title'] for module in modules}
    for module in modules:
        if module['dependencies']:
            edges.append(escape(module['title']) + ' → ' + '、'.join(escape(titles[name]) for name in module['dependencies']))
    payload = json.dumps(inventory, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    template = Template(Path(__file__).with_name('source_inventory.html').read_text(encoding='utf-8'))
    return template.substitute(
        cards=''.join(cards), sections=''.join(sections), dependencies='；'.join(edges) or '无模块间顶层导入',
        module_count=len(modules), function_count=inventory['function_count'],
        git_reference=escape(inventory['git_reference']), inventory_json=payload,
        options=''.join(f'<option value="{escape(m["name"])}">{escape(m["title"])}</option>' for m in modules),
    )


def render_preview(stage: Path, inventory: dict, requirements_path: Path | None) -> None:
    # HTML-only generation does not import browser or PDF dependencies.
    import pdfplumber
    from weasyprint import HTML
    from capture_screens import capture
    from generate_markdown_pdf import DOCUMENT_CSS, _rows_html
    from material_images import load_images
    from validate_materials import _audit_page_image, _body_font_sizes, _body_top_values

    capture(argparse.Namespace(
        mode='html', html=stage / 'source-inventory.html', selector='#diagram',
        output=stage / 'bundle', id='source-inventory',
        caption='图1 说明书图片处理模块源码结构示意图',
        evidence=[stage / 'source-inventory.json', *(stage / 'sources').glob('*.py')],
        requirements=requirements_path, width=1440, height=1000, timeout=30,
    ))
    bundle = stage / 'bundle'
    snippet = (bundle / 'image-snippet.md').read_text(encoding='utf-8').splitlines()
    images = load_images(bundle / 'preview.md', snippet)
    images[0]['pdf_page'] = 1
    rows = [
        '# 说明书图片处理：源码与排版预览',
        '本页为单页排版示例，不是业务软件申报材料。',
        '下列函数名称来自随HTML归档的同一份源码快照。',
        'HTML提供完整函数目录、搜索和源码摘要。',
        '本页保留30行正文；图片及图注不计入正文行数。',
    ]
    for module in inventory['modules']:
        rows.append('## ' + module['title'])
        rows.extend(f'函数：{function["name"]}' for function in module['functions'])
    rows = rows[:30]
    if len(rows) != 30:
        raise ValueError('源码函数数量不足以构成30行排版预览')
    body = _rows_html([rows], 'document', images)
    css = DOCUMENT_CSS.replace('__TITLE__', '源码结构 · 非申报材料').replace('__LABEL__', '图片排版预览').replace('__FONT_SIZE__', '10.15')
    preview = stage / '图片排版预览.pdf'
    HTML(string=f'<meta charset="utf-8"><style>{css}</style>{body}', base_url=str(bundle)).write_pdf(preview)
    with pdfplumber.open(preview) as pdf:
        if len(pdf.pages) != 1:
            raise ValueError('排版预览超出1页')
        page = pdf.pages[0]
        issues, caption_tops = _audit_page_image(page, images[0], preview, 1)
        body_rows = len(_body_top_values(page, caption_tops))
        sizes = _body_font_sizes(page)
        if issues or body_rows != 30 or not sizes or min(sizes) < 10:
            raise ValueError(f'排版预览检查失败：rows={body_rows}, issues={issues}')
    (stage / 'preview-verification.json').write_text(json.dumps({
        'purpose': '单页排版示例，非申报材料', 'pages': 1, 'body_rows': body_rows,
        'minimum_body_font_size': min(sizes), 'images': images,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def generate_demo(output: Path, html_only: bool = False, requirements_path: Path | None = None) -> Path:
    output = output.resolve()
    rules = load_material_requirements(requirements_path)
    assert_output_path_allowed(output, rules)
    if output.exists():
        raise FileExistsError(f'输出目录已存在，请指定新目录：{output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.html-demo-', dir=output.parent) as temporary:
        stage = Path(temporary)
        inventory = collect_inventory(ROOT, stage)
        (stage / 'source-inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (stage / 'source-inventory.html').write_text(render_demo(inventory), encoding='utf-8')
        if not html_only:
            render_preview(stage, inventory, requirements_path)
        assert_output_path_allowed(output, rules)
        # Publish only completed results; an existing destination is never overwritten.
        shutil.copytree(stage, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='新的输出目录；默认自动生成带时间戳的目录')
    parser.add_argument('--html-only', action='store_true', help='只生成HTML、JSON和源码快照，无需浏览器/PDF依赖')
    parser.add_argument('--requirements', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'copyright/outputs/html-demos' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    try:
        generate_demo(output, args.html_only, args.requirements)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f'生成失败：{exc}\n')
    print(output.resolve() / 'source-inventory.html')
    if not args.html_only:
        print(output.resolve() / '图片排版预览.pdf')


if __name__ == '__main__':
    main()
