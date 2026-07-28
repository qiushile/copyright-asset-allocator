import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_docx import (  # noqa: E402
    _validate_document_content,
    _validate_manual_source,
    collect_pure_code_lines,
    paginate_lines,
    select_material_rows,
)
from material_requirements import (  # noqa: E402
    DEFAULT_REQUIREMENTS_PATH,
    load_material_requirements,
    requirement_summary,
)
from validate_materials import audit_markdown  # noqa: E402


class MaterialRequirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requirements = load_material_requirements(DEFAULT_REQUIREMENTS_PATH)

    def test_rule_summary_contains_all_hard_thresholds(self):
        summary = requirement_summary(self.requirements)
        self.assertEqual(summary["page_size"], "A4")
        self.assertEqual(summary["total_pages"], 60)
        self.assertEqual(summary["front_pages"], 30)
        self.assertEqual(summary["back_pages"], 30)
        self.assertEqual(summary["program_lines_per_page"], 50)
        self.assertEqual(summary["document_lines_per_page"], 30)
        self.assertEqual(summary["line_numbers"], "forbidden")
        self.assertEqual(summary["program_font_point_size"], 8.15)
        self.assertEqual(summary["document_font_point_size"], 10.15)
        self.assertEqual(summary["main_function_characters"], [500, 1300])
        self.assertEqual(
            self.requirements["generation"]["output_formats"],
            ["markdown", "pdf"],
        )

    def test_page_selection_requires_sixty_complete_pages(self):
        with self.assertRaises(ValueError):
            select_material_rows(["row"] * 2999, 50)
        sixty = [f"row-{index}" for index in range(3000)]
        self.assertEqual(select_material_rows(sixty, 50), sixty)
        sixty_plus_one = [f"row-{index}" for index in range(3001)]
        selected = select_material_rows(sixty_plus_one, 50)
        self.assertEqual(len(selected), 3000)
        self.assertEqual(selected[0], "row-0")
        self.assertEqual(selected[1499], "row-1499")
        self.assertEqual(selected[1500], "row-1501")
        self.assertEqual(selected[-1], "row-3000")

    def test_pure_code_rows_exclude_blank_lines(self):
        rows = collect_pure_code_lines(
            [{"effective_lines": ["value = 1", "return value"], "path": "app.py"}]
        )
        self.assertEqual(rows, ["value = 1", "return value"])
        self.assertEqual(len(paginate_lines(rows, 1)), 2)

    def test_document_rejects_short_or_repeated_padding(self):
        with self.assertRaises(ValueError):
            _validate_document_content(["有效正文"] * 1799)
        unique_rows = [
            f"专题{chr(0x4E00 + index)}的系统专用说明具有不同业务含义、输入条件、处理步骤、输出结果和验收依据"
            for index in range(1800)
        ]
        _validate_document_content(unique_rows)
        unique_rows[-1] = unique_rows[0]
        with self.assertRaises(ValueError):
            _validate_document_content(unique_rows)

    def test_manual_tips_must_be_structured_and_unique(self):
        valid = """# 软件说明书

## 一、功能说明

1. 用户进入任务页并核对输入信息。

## 二、操作流程

2. 用户提交任务并检查处理结果。

## 十一、补充操作提示

### 11.1 提交前检查

提交前核对任务标识、输入版本、参数范围和目标格式，确认记录与本次操作一致。

### 11.2 异常处理

出现异常时保留当前任务状态与错误信息，再按输入、配置、环境和处理流程顺序定位。
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "软件说明书.md"
            _validate_manual_source(path, valid)
            repeated = valid + "\n" + (
                "出现异常时保留当前任务状态与错误信息，再按输入、配置、环境和处理流程顺序定位。\n\n"
            ) * 2
            with self.assertRaises(ValueError):
                _validate_manual_source(path, repeated)
            numbered_template = valid + "\n" + (
                "专项1仅修改了编号，其余操作说明、输入检查、处理步骤和验收条件完全相同。\n\n"
                "专项2仅修改了编号，其余操作说明、输入检查、处理步骤和验收条件完全相同。\n"
            )
            with self.assertRaises(ValueError):
                _validate_manual_source(path, numbered_template)

    def test_feature_summary_main_function_is_500_to_1300_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid_path = Path(tmp) / "软件功能和特点.md"
            valid_path.write_text(
                "# 软件功能和特点\n\n## 软件的主要功能\n\n" + "功能" * 250,
                encoding="utf-8",
            )
            valid = audit_markdown(valid_path, self.requirements)
            self.assertTrue(valid["passed"], valid["issues"])
            self.assertEqual(valid["metrics"]["main_function_characters"], 500)

            valid_path.write_text(
                "# 软件功能和特点\n\n## 软件的主要功能\n\n" + "功能" * 249,
                encoding="utf-8",
            )
            invalid = audit_markdown(valid_path, self.requirements)
            self.assertFalse(invalid["passed"])


if __name__ == "__main__":
    unittest.main()
