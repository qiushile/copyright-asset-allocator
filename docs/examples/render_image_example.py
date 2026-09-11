#!/usr/bin/env python3
"""Build an honest source-inventory diagram, capture it and preview one image page.

This is a one-page layout demonstration of this repository, not a filing manual.
Requires the optional Playwright dependency and Chromium.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'copyright/scripts'))

import pdfplumber
from weasyprint import HTML
from generate_markdown_pdf import DOCUMENT_CSS, _rows_html
from material_images import load_images
from material_requirements import assert_output_path_allowed, load_material_requirements
from validate_materials import _audit_page_image, _body_top_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'copyright/outputs/image-example')
    args = parser.parse_args()
    output = args.output.resolve()
    assert_output_path_allowed(output, load_material_requirements())
    output.mkdir(parents=True, exist_ok=False)
    modules = ['material_images', 'generate_markdown_pdf', 'validate_materials']
    cards, rows, sources = [], [], []
    for name in modules:
        source = ROOT / 'copyright/scripts' / f'{name}.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        imports = sorted({node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.module in modules})
        cards.append(f'<article><h2>{html.escape(name)}.py</h2><strong>{len(functions)}</strong><p>顶层函数</p><ul>' + ''.join(f'<li>{html.escape(fn)}</li>' for fn in functions[:5]) + '</ul><footer>本图模块依赖：' + html.escape('、'.join(imports) or '无') + '</footer></article>')
        rows.extend(f'函数：{fn}' for fn in functions)
        sources.append(source)
    diagram = output / 'source-inventory.html'
    diagram.write_text('''<!doctype html><meta charset="utf-8"><title>源码结构示意图</title>
<style>*{box-sizing:border-box}body{margin:0;background:#edf2f7;color:#183249;font:20px "PingFang SC",sans-serif}main{width:1320px;padding:40px}header{display:flex;justify-content:space-between;align-items:center}h1{font-size:32px;margin:0 0 12px}header span{font-size:16px;background:#d9e9ea;color:#176266;padding:8px 14px;border-radius:20px}.cards{display:flex;gap:20px;margin-top:28px}article{width:400px;padding:24px;background:white;border-top:5px solid #238183;border-radius:10px}h2{font:20px monospace;margin:0 0 20px}strong{font-size:52px}p{display:inline-block;margin-left:12px;color:#5a7285}ul{padding:0;list-style:none;height:180px;font:17px/2 monospace}footer{font-size:14px;min-height:42px;color:#5a7285;overflow-wrap:anywhere}.note{display:block;font-size:16px;margin:24px 0 0;color:#5a7285}</style>
<main id="diagram"><header><div><h1>说明书图片处理 · 源码结构</h1>基于当前 Python 源文件的 AST 静态统计</div><span>由源码生成的示意图</span></header><div class="cards">''' + ''.join(cards) + '</div><p class="note">用于说明代码结构；不表示业务系统运行状态，也不作为实际运行截图。</p></main>', encoding='utf-8')
    command = [sys.executable, str(ROOT / 'copyright/scripts/capture_screens.py'), 'html',
               '--html', str(diagram), '--selector', '#diagram', '--output', str(output / 'bundle'),
               '--id', 'source-inventory', '--caption', '图1 说明书图片处理模块源码结构示意图']
    for source in sources:
        command += ['--evidence', str(source)]
    subprocess.run(command, check=True)
    bundle = output / 'bundle'
    snippet = (bundle / 'image-snippet.md').read_text(encoding='utf-8').splitlines()
    images = load_images(bundle / 'preview.md', snippet)
    images[0]['pdf_page'] = 1
    rows = ['# 图片排版预览（单页示例，非申报材料）',
            '以下函数名称来自当前仓库源文件的静态解析。',
            '本页展示30行正文与一张源码结构示意图的组合。',
            '图片与图注均不计入这30行正文。',
            '完整申报材料仍须使用经核实的系统专用说明书。', *rows][:30]
    if len(rows) != 30:
        raise ValueError('源码函数数量不足以构成本预览')
    body = _rows_html([rows], 'document', images)
    css = DOCUMENT_CSS.replace('__TITLE__', '图片排版预览 · 非申报材料').replace('__LABEL__', '单页演示').replace('__FONT_SIZE__', '10.15')
    preview = output / '图片排版预览.pdf'
    HTML(string=f'<meta charset="utf-8"><style>{css}</style>{body}', base_url=str(bundle)).write_pdf(preview)
    with pdfplumber.open(preview) as pdf:
        assert len(pdf.pages) == 1
        issues, caption_tops = _audit_page_image(pdf.pages[0], images[0], preview, 1)
        assert not issues, issues
        assert len(_body_top_values(pdf.pages[0], caption_tops)) == 30
    (output / 'preview-verification.json').write_text(json.dumps({'purpose': '单页排版示例，非申报材料', 'pages': 1, 'body_rows': 30, 'images': images}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(preview)


if __name__ == '__main__':
    main()
