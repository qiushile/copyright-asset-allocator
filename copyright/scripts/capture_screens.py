#!/usr/bin/env python3
"""Capture a real web page, render an HTML diagram, or register an existing capture.

The source type is an author's declaration, never a certification of authenticity.
Each invocation creates a NEW image directory containing a ready-to-use Markdown
snippet, images.yaml and hashed evidence. It never runs a system under test.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml
from PIL import Image

from material_images import SOURCE_TYPES, sha256
from material_requirements import assert_output_path_allowed, load_material_requirements


def capture(args) -> Path:
    rules = load_material_requirements(args.requirements)
    target = args.output.resolve()
    assert_output_path_allowed(target, rules)
    if target.exists():
        raise FileExistsError(f'为保留已有证据，输出目录必须尚不存在：{target}')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.id):
        raise ValueError('id仅支持字母、数字、下划线和短横线')
    kind = 'generated_from_source' if args.mode == 'html' else ('real_web_capture' if args.mode == 'web' else args.source_type)
    caption = args.caption
    if kind not in {'real_web_capture', 'real_terminal_capture'}:
        if '示意图' not in caption or re.search(r'运行截图|界面截图|实拍', caption):
            raise ValueError('生成图/日志重排图的图注须包含“示意图”，不能称为运行截图')
    if not 1 <= len(caption) <= 45 or any(c in caption for c in '[]\n\r'):
        raise ValueError('图注须为1至45字符且不能包含方括号或换行')
    if args.mode == 'web':
        url = urlsplit(args.url)
        if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password:
            raise ValueError('Web截图须使用不含用户名密码的HTTP(S)运行地址')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.capture-', dir=target.parent) as temporary:
        stage = Path(temporary)
        assets, evidence_dir = stage / 'assets', stage / 'evidence' / args.id
        assets.mkdir()
        evidence_dir.mkdir(parents=True)
        output = assets / f'{args.id}.png'
        evidence = []
        sources = list(args.evidence)
        if args.mode == 'html':
            sources.insert(0, args.html)
        if args.mode == 'import':
            sources.insert(0, args.image)
        for index, source in enumerate(sources):
            source = source.resolve(strict=True)
            destination = evidence_dir / f'{index:02d}-{source.name}'
            shutil.copyfile(source, destination)
            evidence.append({'file': destination.relative_to(stage).as_posix(), 'sha256': sha256(destination)})
        metadata = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source_type': kind,
                    'mode': args.mode, 'declaration': '作者须核实真实性；HTML渲染属于示意图'}
        if args.mode == 'import':
            with Image.open(evidence_dir / f'00-{args.image.resolve().name}') as original:
                original.convert('RGB').save(output)
        else:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError('截图需安装可选依赖：pip install playwright && playwright install chromium') from exc
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={'width': args.width, 'height': args.height}, device_scale_factor=1)
                    page.set_default_timeout(args.timeout * 1000)
                    if args.mode == 'html':
                        # Local diagrams must render offline, not fetch an unrelated live UI.
                        page.route(re.compile(r'^https?://'), lambda route: route.abort())
                        address = (evidence_dir / f'00-{args.html.resolve().name}').as_uri()
                    else:
                        address = args.url
                        metadata['url'] = urlunsplit((url.scheme, url.netloc, url.path, '', ''))
                    response = page.goto(address, wait_until='load')
                    if response is not None and response.status >= 400:
                        raise ValueError(f'页面返回HTTP {response.status}，未保存截图')
                    if args.selector:
                        page.locator(args.selector).wait_for(state='visible')
                    page.evaluate('document.fonts.ready')
                    if args.selector:
                        page.locator(args.selector).screenshot(path=str(output), animations='disabled')
                    else:
                        page.screenshot(path=str(output), full_page=False, animations='disabled')
                    metadata.update({'title': page.title(), 'viewport': {'width': args.width, 'height': args.height},
                                     'selector': args.selector, 'browser_version': browser.version})
                finally:
                    browser.close()
        metadata_path = evidence_dir / 'capture.json'
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        evidence.append({'file': f'evidence/{args.id}/capture.json', 'sha256': sha256(metadata_path)})
        entry = {'id': args.id, 'file': f'assets/{args.id}.png', 'caption': caption,
                 'source_type': kind, 'sha256': sha256(output), 'evidence': evidence}
        (stage / 'images.yaml').write_text(yaml.safe_dump({'schema_version': 1, 'images': [entry]}, allow_unicode=True, sort_keys=False), encoding='utf-8')
        (stage / 'image-snippet.md').write_text(f'![{caption}]({entry["file"]})\n', encoding='utf-8')
        assert_output_path_allowed(target, rules)
        if target.exists():
            raise FileExistsError(target)
        # mkdir is exclusive; copytree will refuse a concurrently created destination.
        shutil.copytree(stage, target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['html', 'web', 'import'])
    parser.add_argument('--output', type=Path, required=True, help='新建的图片包目录，不得已存在')
    parser.add_argument('--id', required=True)
    parser.add_argument('--caption', required=True)
    parser.add_argument('--html', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--source-type', choices=sorted(SOURCE_TYPES), default='real_terminal_capture')
    parser.add_argument('--evidence', type=Path, action='append', default=[], help='可重复：源码、原始日志或运行记录')
    parser.add_argument('--selector', help='等候并截取的元素；省略则截取固定视口')
    parser.add_argument('--width', type=int, default=1440)
    parser.add_argument('--height', type=int, default=900)
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--requirements', type=Path)
    args = parser.parse_args()
    if not getattr(args, {'html': 'html', 'web': 'url', 'import': 'image'}[args.mode]):
        parser.error('html/web/import分别需要 --html/--url/--image')
    if not 320 <= args.width <= 4096 or not 240 <= args.height <= 4096 or not 1 <= args.timeout <= 120:
        parser.error('视口宽须320至4096、高须240至4096，超时须1至120秒')
    print(capture(args))


if __name__ == '__main__':
    main()
