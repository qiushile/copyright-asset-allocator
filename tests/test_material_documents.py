import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_docx import (  # noqa: E402
    create_code_document,
    create_doc_document,
    write_code_to_docx,
    write_doc_pages_to_docx,
)
from generate_markdown_pdf import render_rows_pdf  # noqa: E402
from material_requirements import (  # noqa: E402
    DEFAULT_REQUIREMENTS_PATH,
    load_material_requirements,
)
from validate_materials import audit_docx, audit_material_directory  # noqa: E402


class MaterialDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requirements = load_material_requirements(DEFAULT_REQUIREMENTS_PATH)

    def test_generated_docx_has_explicit_60_pages_and_required_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_path = root / "测试系统-程序鉴别材料.docx"
            code_doc = create_code_document("测试系统", "V1.0")
            code_pages = [
                [f"value_{page}_{row} = {page + row}" for row in range(50)]
                for page in range(60)
            ]
            write_code_to_docx(code_doc, code_pages)
            code_doc.save(code_path)

            code_report = audit_docx(code_path, "program", self.requirements)
            self.assertTrue(code_report["passed"], code_report["issues"])
            self.assertEqual(code_report["page_count"], 60)
            self.assertEqual(code_report["minimum_body_rows"], 50)

            document_path = root / "测试系统-文档鉴别材料.docx"
            document = create_doc_document("测试系统", "V1.0")
            document_pages = [
                [f"第{page}页第{row}项操作说明及其对应检查条件" for row in range(30)]
                for page in range(60)
            ]
            write_doc_pages_to_docx(document, document_pages)
            document.save(document_path)

            document_report = audit_docx(document_path, "document", self.requirements)
            self.assertTrue(document_report["passed"], document_report["issues"])
            self.assertEqual(document_report["page_count"], 60)
            self.assertEqual(document_report["minimum_body_rows"], 30)

            directory_report = audit_material_directory(root, self.requirements)
            self.assertFalse(directory_report["passed"])
            self.assertEqual(
                {issue["material_type"] for issue in directory_report["errors"]},
                {"program", "document"},
            )
            self.assertTrue(
                all(
                    issue["code"] == "missing_rendered_material"
                    for issue in directory_report["errors"]
                )
            )

    def test_markdown_rows_render_directly_to_audited_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_rows = [f'value_{index} = {index}' for index in range(3000)]
            code_pdf = root / '源代码文档.pdf'
            code_report = render_rows_pdf(code_rows, code_pdf, 'program', '测试系统')
            self.assertEqual(code_report['page_count'], 60)
            self.assertGreaterEqual(code_report['minimum_body_rows'], 50)
            self.assertGreaterEqual(code_report['minimum_body_font_size'], 8.0)

            document_rows = [
                f'{index % 5 + 1}. 功能专题{chr(0x4E00 + index)}的操作说明、输入条件、处理结果和验收依据'
                for index in range(1800)
            ]
            document_pdf = root / '软件说明书.pdf'
            document_report = render_rows_pdf(
                document_rows,
                document_pdf,
                'document',
                '测试系统',
            )
            self.assertEqual(document_report['page_count'], 60)
            self.assertGreaterEqual(document_report['minimum_body_rows'], 30)
            self.assertGreaterEqual(document_report['minimum_body_font_size'], 10.0)

            line_numbered_rows = [
                f'{index % 50 + 1} value_{index} = {index}'
                for index in range(3000)
            ]
            with self.assertRaisesRegex(ValueError, '连续正文行号'):
                render_rows_pdf(
                    line_numbered_rows,
                    root / '带行号的源代码文档.pdf',
                    'program',
                    '测试系统',
                )


if __name__ == "__main__":
    unittest.main()
