"""Local, evidence-backed figures and deterministic attachment to print rows."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml
from PIL import Image

IMAGE_RE = re.compile(r'^!\[([^\]\n]+)\]\(([^\n]+)\)$')
REAL_TYPES = {'real_web_capture', 'real_terminal_capture'}
SOURCE_TYPES = REAL_TYPES | {'generated_from_source', 'rendered_log'}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError(f'图片及证据必须使用目录内相对路径：{value}')
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f'图片或证据不存在或越界：{value}')
    return resolved


def image_references(lines: list[str]) -> list[tuple[int, str, str]]:
    refs = []
    for index, line in enumerate(lines):
        match = IMAGE_RE.fullmatch(line.strip())
        if match:
            refs.append((index, *match.groups()))
        elif '![' in line or re.search(r'<img\b', line, re.I):
            raise ValueError('图片须独占一行并使用 ![图注](相对路径)，不支持内联、远程或HTML图片')
    return refs


def load_images(manual: Path, lines: list[str]) -> list[dict]:
    refs = image_references(lines)
    if not refs:
        return []
    specific = manual.with_suffix('.images.yaml')
    sidecar = specific if specific.exists() else manual.parent / 'images.yaml'
    data = yaml.safe_load(sidecar.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('images'), list):
        raise ValueError('图片清单须为 schema_version: 1 和 images 列表')
    by_file, ids = {}, set()
    for entry in data['images']:
        if not isinstance(entry, dict):
            raise ValueError('图片记录必须为对象')
        ident = entry.get('id', '')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', ident) or ident in ids:
            raise ValueError(f'图片id无效或重复：{ident}')
        ids.add(ident)
        file = entry.get('file')
        if not isinstance(file, str) or file in by_file:
            raise ValueError(f'图片file无效或重复：{file}')
        by_file[file] = entry
    if len({ref[2] for ref in refs}) != len(refs) or set(by_file) != {ref[2] for ref in refs}:
        raise ValueError('Markdown图片与清单须一一对应，不得重复引用或遗漏')
    result = []
    for line_index, caption, file in refs:
        entry = dict(by_file[file])
        if entry.get('caption') != caption:
            raise ValueError(f'图注与清单不一致：{file}')
        kind = entry.get('source_type')
        if kind not in SOURCE_TYPES:
            raise ValueError(f'不支持的图片来源类型：{kind}')
        if kind not in REAL_TYPES:
            if '示意图' not in caption or re.search(r'运行截图|界面截图|实拍', caption):
                raise ValueError('生成图和日志重排图必须标注“示意图”，不得标注运行截图')
        path = local_file(manual.parent, file)
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
            raise ValueError('仅支持PNG/JPEG图片；SVG请先栅格化')
        with Image.open(path) as img:
            if img.format not in {'PNG', 'JPEG'} or getattr(img, 'n_frames', 1) != 1:
                raise ValueError('图片须为单帧PNG/JPEG')
            img.verify()
        with Image.open(path) as img:
            width, height = img.size
        if entry.get('sha256') != sha256(path):
            raise ValueError(f'图片SHA-256不匹配：{file}')
        evidence = entry.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f'图片缺少证据文件清单：{file}')
        for proof in evidence:
            proof_path = local_file(manual.parent, proof['file'])
            if proof.get('sha256') != sha256(proof_path):
                raise ValueError(f'证据SHA-256不匹配：{proof["file"]}')
        result.append({**entry, 'line_index': line_index, 'width_px': width, 'height_px': height})
    return result


def plan_images(lines: list[str], images: list[dict], normalize, lines_per_page=30) -> tuple[list[str], list[dict]]:
    """Anchor to preceding text row; use the same first/last row selection as text."""
    by_line = {entry['line_index']: entry for entry in images}
    rows, anchored = [], []
    for index, line in enumerate(lines):
        if index in by_line:
            anchored.append({**by_line[index], 'anchor_row': max(0, len(rows) - 1)})
        else:
            rows.extend(normalize([line]))
    side_rows = 30 * lines_per_page
    selected_indices = (list(range(len(rows))) if len(rows) <= 2 * side_rows else
                        list(range(side_rows)) + list(range(len(rows) - side_rows, len(rows))))
    positions = {index: output for output, index in enumerate(selected_indices)}
    occupied = set()
    for entry in anchored:
        pos = positions.get(entry['anchor_row'])
        entry['pdf_page'] = pos // lines_per_page + 1 if pos is not None else None
        if pos is not None:
            if entry['pdf_page'] in occupied:
                raise ValueError(f'第{entry["pdf_page"]}页有多张图片；请将图片分散到相隔30行正文的位置')
            occupied.add(entry['pdf_page'])
    return rows, anchored


def check_image_policy(images: list[dict], requirements: dict) -> None:
    policy = requirements['document_material'].get('images', {})
    selected = [entry for entry in images if entry['pdf_page'] is not None]
    if len(selected) < policy.get('minimum_count', 0):
        raise ValueError('最终选取的60页中图片数量不足')
    if sum(entry['source_type'] in REAL_TYPES for entry in selected) < policy.get('minimum_real_captures', 0):
        raise ValueError('最终选取的60页中真实运行截图数量不足；示意图不能代替运行截图')


def package_images(manual: Path, lines: list[str], images: list[dict], stage: Path) -> tuple[list[str], list[dict]]:
    """Copy immutable content-addressed files so exported Markdown is portable."""
    lines, packaged = list(lines), []
    for entry in images:
        item = dict(entry)
        files = [{'file': entry['file'], 'sha256': entry['sha256']}, *entry['evidence']]
        rewritten = []
        for file_index, record in enumerate(files):
            source = local_file(manual.parent, record['file'])
            prefix = entry["id"] + "-" if file_index == 0 else ""
            target_rel = f'image-assets/{prefix}{record["sha256"]}{source.suffix.lower()}'
            target = stage / target_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != record['sha256']:
                raise ValueError(f'打包时文件已变化：{source}')
            target.write_bytes(payload)
            rewritten.append({'file': target_rel, 'sha256': record['sha256']})
        item['file'] = rewritten[0]['file']
        item['evidence'] = rewritten[1:]
        lines[item['line_index']] = f'![{item["caption"]}]({item["file"]})'
        packaged.append(item)
    if packaged:
        (stage / '软件说明书.images.yaml').write_text(yaml.safe_dump({'schema_version': 1, 'images': packaged}, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return lines, packaged


def publish_image_assets(stage: Path, output: Path) -> None:
    source_dir = stage / 'image-assets'
    if not source_dir.exists():
        return
    destination = output / 'image-assets'
    if destination.is_symlink():
        raise ValueError('输出图片目录不得为符号链接')
    destination.mkdir(exist_ok=True)
    for source in source_dir.iterdir():
        target = destination / source.name
        if target.is_symlink():
            raise ValueError(f'输出图片文件不得为符号链接：{target}')
        if target.exists():
            if sha256(target) != sha256(source):
                raise ValueError(f'已有图片文件与内容摘要不符：{target}')
        else:
            with target.open('xb') as handle:
                handle.write(source.read_bytes())


def write_image_manifest(stage: Path, images: list[dict]) -> None:
    (stage / '软件说明书-图片清单.json').write_text(json.dumps({
        'schema_version': 1,
        'authenticity': '来源为作者声明；哈希与文件完整性检查不能证明实际运行或审查通过',
        'pdf_sha256': sha256(stage / '软件说明书.pdf'),
        'markdown_sha256': sha256(stage / '软件说明书.md'),
        'images': images,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
