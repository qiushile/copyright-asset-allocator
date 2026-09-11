"""Image provenance, portable export, selection and rendered-PDF regressions."""
import json
import shutil

import pytest
import yaml
from PIL import Image
from pypdf import PdfWriter

from material_images import load_images, plan_images, sha256, check_image_policy
from generate_docx import _normalize_document_rows
from generate_markdown_pdf import generate_document_markdown_pdf
from material_requirements import load_material_requirements, FrozenAssetError
from validate_materials import audit_pdf, audit_material_directory


def image_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (1200, 500), '#17324d').save(root / 'diagram.png')
    (root / 'source.txt').write_text('Layout test fixture only, not production evidence.', encoding='utf-8')
    entry = {'id': 'fig-1', 'file': 'diagram.png', 'caption': '图1 排版测试示意图',
             'source_type': 'generated_from_source', 'sha256': sha256(root / 'diagram.png'),
             'evidence': [{'file': 'source.txt', 'sha256': sha256(root / 'source.txt')}]}
    (root / 'images.yaml').write_text(yaml.safe_dump({'schema_version': 1, 'images': [entry]}, allow_unicode=True), encoding='utf-8')
    return entry


def write_sidecar(root, entries):
    (root / 'images.yaml').write_text(yaml.safe_dump({'schema_version': 1, 'images': entries}, allow_unicode=True), encoding='utf-8')


def test_image_validation_and_tampering(tmp_path):
    entry = image_fixture(tmp_path)
    manual = tmp_path / 'manual.md'
    lines = [f'![{entry["caption"]}](diagram.png)']
    assert load_images(manual, lines)[0]['width_px'] == 1200
    (tmp_path / 'source.txt').write_text('changed')
    with pytest.raises(ValueError, match='证据SHA'):
        load_images(manual, lines)
    entry['evidence'][0]['sha256'] = sha256(tmp_path / 'source.txt')
    write_sidecar(tmp_path, [entry])
    Image.new('RGB', (10, 10)).save(tmp_path / 'diagram.png')
    with pytest.raises(ValueError, match='图片SHA'):
        load_images(manual, lines)


@pytest.mark.parametrize('reference', ['![x](https://example.org/a.png)', 'text ![x](diagram.png)', '<img src="diagram.png">', '![x][ref]'])
def test_unsupported_images_never_silently_become_text(tmp_path, reference):
    image_fixture(tmp_path)
    with pytest.raises(ValueError):
        load_images(tmp_path / 'manual.md', [reference])


def test_symlink_escape_and_duplicate_ids(tmp_path):
    root = tmp_path / 'manual'
    entry = image_fixture(root)
    outside = tmp_path / 'outside.png'
    shutil.copyfile(root / 'diagram.png', outside)
    (root / 'diagram.png').unlink()
    (root / 'diagram.png').symlink_to(outside)
    with pytest.raises(ValueError, match='越界'):
        load_images(root / 'manual.md', [f'![{entry["caption"]}](diagram.png)'])
    write_sidecar(root, [entry, entry])
    with pytest.raises(ValueError, match='重复'):
        load_images(root / 'manual.md', [f'![{entry["caption"]}](diagram.png)'])


def test_image_rows_not_counted_and_cut_mapping():
    lines = ['body'] * 2100
    images = [{'id': 'front', 'line_index': 30}, {'id': 'middle', 'line_index': 1050}, {'id': 'back', 'line_index': 2070}]
    for image in images:
        lines[image['line_index']] = '![caption](file.png)'
    rows, planned = plan_images(lines, images, _normalize_document_rows)
    assert len(rows) == 2097
    assert [entry['pdf_page'] for entry in planned] == [1, None, 60]
    with pytest.raises(ValueError, match='多张图片'):
        plan_images(['body', 'image', 'image'], [{'line_index': 1}, {'line_index': 2}], _normalize_document_rows)


def test_minimum_real_captures_cannot_be_satisfied_by_diagrams():
    rules = load_material_requirements()
    rules['document_material']['images']['minimum_real_captures'] = 1
    with pytest.raises(ValueError, match='真实运行截图数量不足'):
        check_image_policy([{'pdf_page': 1, 'source_type': 'generated_from_source'}], rules)


def test_generated_caption_required(tmp_path):
    entry = image_fixture(tmp_path)
    entry['caption'] = '图1 系统运行截图'
    write_sidecar(tmp_path, [entry])
    with pytest.raises(ValueError, match='示意图'):
        load_images(tmp_path / 'manual.md', [f'![{entry["caption"]}](diagram.png)'])


@pytest.fixture(scope='module')
def rendered_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp('image-render')
    manual_dir = root / 'manual'
    entry = image_fixture(manual_dir)
    manual = manual_dir / 'manual.md'
    lines = ['# 排版测试说明书', '## 输入条件', '1. 此内容仅用于排版自动化测试。', f'![{entry["caption"]}](diagram.png)']
    lines += [f'测试项目{chr(0x4e00 + i)}及输出检查。' for i in range(1800)]
    lines += ['## 补充操作提示', '### 1.1 测试范围', '1. 此夹具不能作为申请材料。']
    manual.write_text('\n'.join(lines), encoding='utf-8')
    workspace = root / 'workspace'
    workspace.mkdir()
    (workspace / 'SOURCE_MANIFEST.json').write_text(json.dumps({'copyrightId': 'layout-test'}))
    # Stub only the code inventory: exercise the real manual/parser/renderer/auditor.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr('generate_markdown_pdf.read_source_files', lambda *_: [{'language': 'python'}])
        generate_document_markdown_pdf(workspace, root / 'output', '排版测试', 'V1.0',
            ''.join(chr(0x6000+i) for i in range(500)), manual)
    return root / 'output'


def test_portable_bundle_and_caption_excluded_from_body_rows(rendered_bundle, tmp_path):
    copied = tmp_path / 'moved'
    shutil.copytree(rendered_bundle, copied)
    rules = load_material_requirements()
    report = audit_material_directory(copied, rules, require_rendered_pair=False)
    assert report['passed'], report['errors']
    pdf = next(item for item in report['materials'] if item['format'] == 'pdf')
    assert pdf['page_count'] == 60
    assert pdf['minimum_body_rows'] == pdf['maximum_body_rows'] == 30
    assert pdf['image_count'] == 1
    assert pdf['minimum_body_font_size'] >= 10
    manifest = json.loads((copied / '软件说明书-图片清单.json').read_text())
    assert manifest['images'][0]['pdf_page'] == 1
    assert manifest['images'][0]['print_height_mm'] > 30
    # A changed PDF must not pass with the original image manifest.
    writer = PdfWriter(clone_from=copied / '软件说明书.pdf')
    writer.add_metadata({'/test': 'tampered'})
    writer.write(copied / '软件说明书.pdf')
    report = audit_pdf(copied / '软件说明书.pdf', 'document', rules)
    assert not report['passed']
    assert any(i['code'] == 'image_evidence' for i in report['issues'])


def test_missing_manifest_and_assets_fail(rendered_bundle, tmp_path):
    copied = tmp_path / 'copy'
    shutil.copytree(rendered_bundle, copied)
    (copied / '软件说明书-图片清单.json').unlink()
    report = audit_pdf(copied / '软件说明书.pdf', 'document', load_material_requirements())
    assert not report['passed']
    shutil.rmtree(copied / 'image-assets')
    report = audit_material_directory(copied, require_rendered_pair=False)
    assert not report['passed']


def test_frozen_target_is_rejected_before_writing(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'SOURCE_MANIFEST.json').write_text('{"copyrightId":"frozen"}')
    rules = load_material_requirements()
    rules['protected_assets'] = [{'copyright_id': 'frozen', 'software_name': '冻结系统',
                                 'immutable': True, 'path': str(tmp_path / 'output')}]
    with pytest.raises(FrozenAssetError):
        generate_document_markdown_pdf(workspace, tmp_path / 'output', '冻结系统', 'V1.0', '', requirements=rules)
    assert not (tmp_path / 'output').exists()


def test_unreadable_image_proportions_fail():
    from generate_markdown_pdf import _rows_html
    with pytest.raises(ValueError, match='过窄'):
        _rows_html([['body'] * 30], 'document', [{'pdf_page': 1, 'width_px': 4000,
            'height_px': 100, 'caption': '图1 示意图', 'file': 'image.png'}])


def test_capture_import_packages_original_and_never_overwrites(tmp_path):
    from argparse import Namespace
    from capture_screens import capture
    image_fixture(tmp_path)
    args = Namespace(mode='import', output=tmp_path / 'capture', id='terminal',
        caption='图1 终端窗口截图', source_type='real_terminal_capture',
        evidence=[tmp_path / 'source.txt'], image=tmp_path / 'diagram.png', requirements=None)
    capture(args)
    lines = (args.output / 'image-snippet.md').read_text().splitlines()
    assert load_images(args.output / 'manual.md', lines)[0]['source_type'] == 'real_terminal_capture'
    with pytest.raises(FileExistsError):
        capture(args)


def test_publish_rejects_symlink_directory(tmp_path):
    from material_images import publish_image_assets
    stage, output, elsewhere = tmp_path / 'stage', tmp_path / 'output', tmp_path / 'elsewhere'
    (stage / 'image-assets').mkdir(parents=True)
    (stage / 'image-assets' / 'file.png').write_bytes(b'fixture')
    output.mkdir()
    elsewhere.mkdir()
    (output / 'image-assets').symlink_to(elsewhere)
    with pytest.raises(ValueError, match='符号链接'):
        publish_image_assets(stage, output)
    assert not list(elsewhere.iterdir())


def test_pdf_without_image_fails_even_with_refreshed_pdf_digest(rendered_bundle, tmp_path):
    copied = tmp_path / 'missing-pdf-image'
    shutil.copytree(rendered_bundle, copied)
    pdf = copied / '软件说明书.pdf'
    writer = PdfWriter(clone_from=pdf)
    writer.remove_images()
    writer.write(pdf)
    manifest_path = copied / '软件说明书-图片清单.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['pdf_sha256'] = sha256(pdf)
    manifest_path.write_text(json.dumps(manifest))
    report = audit_pdf(pdf, 'document', load_material_requirements())
    assert any(issue['code'] == 'image_count' for issue in report['issues'])


def test_overlap_and_caption_checked_independently():
    from pathlib import Path
    from validate_materials import _audit_page_image
    class Page:
        width, height = 595.276, 841.89
        images = [{'x0': 50, 'x1': 550, 'top': 510, 'bottom': 750}]
        chars = [{'text': 'overlap', 'x0': 60, 'x1': 100, 'top': 520, 'bottom': 532}]
        def extract_words(self):
            return []
    issues, _ = _audit_page_image(Page(), {'caption': 'required',
        'print_width_mm': 500*25.4/72, 'print_height_mm': 240*25.4/72}, Path('fixture.pdf'), 1)
    assert {issue['code'] for issue in issues} == {'image_overlap', 'image_caption'}
