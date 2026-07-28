import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import copyright_cli  # noqa: E402


class MaterialCommandTests(unittest.TestCase):
    def test_generate_passes_selected_requirements_to_both_generators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "copyright" / "workspaces" / "example-01"
            workspace.mkdir(parents=True)
            (workspace / "SOURCE_MANIFEST.json").write_text(
                json.dumps(
                    {
                        "softwareName": "示例系统",
                        "version": "V1.0",
                        "description": "功能" * 250,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                copyright_id="example-01",
                requirements=str(
                    ROOT / "copyright" / "config" / "material-requirements.yaml"
                ),
                manual=None,
                code_only=False,
            )

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch(
                    "generate_markdown_pdf.generate_program_markdown_pdf"
                ) as program_generator, patch(
                    "generate_markdown_pdf.generate_document_markdown_pdf"
                ) as document_generator:
                    copyright_cli.cmd_generate(args)
            finally:
                os.chdir(previous_cwd)

            program_requirements = program_generator.call_args.args[-1]
            document_requirements = document_generator.call_args.args[-1]
            self.assertIs(program_requirements, document_requirements)
            self.assertEqual(program_requirements["page_selection"]["total_pages"], 60)
            self.assertEqual(program_requirements["protected_assets"], [])


if __name__ == "__main__":
    unittest.main()
