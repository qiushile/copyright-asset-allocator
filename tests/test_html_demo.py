"""Offline demo snapshot integrity, safe HTML and failure-safe publication."""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docs/examples'))
import render_image_example as demo


def test_ast_scope_includes_async_but_not_nested_or_class_methods():
    source = b'''import material_images
from validate_materials import audit_pdf
async def fetch():
    """Fetch example."""
    def nested(): pass
class Client:
    def method(self): pass
'''
    module = demo.inspect_module('generate_markdown_pdf', source)
    assert [fn['name'] for fn in module['functions']] == ['fetch']
    assert module['functions'][0]['kind'] == 'async'
    assert module['dependencies'] == ['material_images', 'validate_materials']
    assert module['sha256'] == hashlib.sha256(source).hexdigest()


def test_html_only_is_self_contained_and_archives_exact_source(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, 'render_preview', lambda *_: pytest.fail('HTML-only invoked PDF renderer'))
    output = demo.generate_demo(tmp_path / 'html', html_only=True)
    inventory = json.loads((output / 'source-inventory.json').read_text())
    page = (output / 'source-inventory.html').read_text()
    assert len(inventory['modules']) == 3
    assert page.count('class="function-row"') == inventory['function_count']
    for module in inventory['modules']:
        source = output / 'sources' / (module['name'] + '.py')
        assert hashlib.sha256(source.read_bytes()).hexdigest() == module['sha256']
    embedded = re.search(r'<script type="application/json" id="inventory">(.*?)</script>', page, re.S).group(1)
    assert json.loads(embedded) == inventory
    assert not re.search(r'(?:src|href)=["\']https?://', page)
    assert not list(output.glob('*.pdf'))


def test_docstring_cannot_escape_json_or_html(tmp_path):
    inventory = demo.collect_inventory(demo.ROOT, tmp_path)
    attack = '</script><script>alert("x")</script>&'
    inventory['modules'][0]['functions'][0]['docstring'] = attack
    page = demo.render_demo(inventory)
    assert attack not in page
    assert '&lt;/script&gt;' in page
    embedded = re.search(r'<script type="application/json" id="inventory">(.*?)</script>', page, re.S).group(1)
    assert json.loads(embedded)['modules'][0]['functions'][0]['docstring'] == attack


def test_render_failure_does_not_publish_partial_output(tmp_path, monkeypatch):
    def fail(*_):
        raise RuntimeError('Browser unavailable')
    monkeypatch.setattr(demo, 'render_preview', fail)
    with pytest.raises(RuntimeError, match='Browser unavailable'):
        demo.generate_demo(tmp_path / 'failed')
    assert not list(tmp_path.iterdir())


def test_existing_output_never_overwritten(tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'keep.txt').write_text('original')
    with pytest.raises(FileExistsError):
        demo.generate_demo(existing, html_only=True)
    assert (existing / 'keep.txt').read_text() == 'original'


def test_frozen_output_rejected_before_writes(tmp_path, monkeypatch):
    rules = {'protected_assets': [{'immutable': True, 'copyright_id': 'test',
                                  'software_name': 'test', 'path': str(tmp_path / 'frozen')}]}
    monkeypatch.setattr(demo, 'load_material_requirements', lambda _: rules)
    with pytest.raises(PermissionError):
        demo.generate_demo(tmp_path / 'frozen' / 'child', html_only=True)
    assert not (tmp_path / 'frozen').exists()
