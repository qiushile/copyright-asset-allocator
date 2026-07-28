import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import database  # noqa: E402
from final_audit import audit_commit  # noqa: E402
from material_requirements import DEFAULT_REQUIREMENTS_PATH, load_material_requirements  # noqa: E402
from validate_materials import audit_markdown, audit_material_directory  # noqa: E402


class MaterialAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requirements = load_material_requirements(DEFAULT_REQUIREMENTS_PATH)

    def test_markdown_audit_rejects_repeated_supplemental_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "软件说明书.md"
            repeated = "异常时保留任务状态、输入版本、配置参数、错误信息和处理记录。"
            manual.write_text(
                "# 软件说明书\n\n"
                "## 补充操作提示\n\n"
                "### 1.1 首次检查\n\n"
                f"{repeated}\n\n"
                "### 1.2 再次检查\n\n"
                f"{repeated}\n",
                encoding="utf-8",
            )
            report = audit_markdown(manual, self.requirements)
            self.assertFalse(report["passed"])
            self.assertIn(
                "repeated_supplemental_content",
                {issue["code"] for issue in report["issues"]},
            )

    def test_directory_audit_requires_both_rendered_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = audit_material_directory(tmp, self.requirements)
            self.assertFalse(report["passed"])
            self.assertEqual(
                {issue["material_type"] for issue in report["errors"]},
                {"program", "document"},
            )

    def test_final_audit_rejects_missing_material_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_db_path = database.DB_PATH
            database.DB_PATH = root / "assets.db"
            try:
                database.init_database()
                with database.get_connection() as connection:
                    cursor = connection.cursor()
                    cursor.execute(
                        "INSERT INTO repositories(name, path) VALUES (?, ?)",
                        ("example", str(root)),
                    )
                    repository_id = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO commits(repository_id, commit_hash) VALUES (?, ?)",
                        (repository_id, "abc123"),
                    )
                    commit_id = cursor.lastrowid
                    cursor.execute(
                        """
                        INSERT INTO copyrights(
                            commit_id, copyright_id, name, version, description
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (commit_id, "example-01", "示例系统", "V1.0", "功能" * 250),
                    )

                report = audit_commit(
                    commit_id,
                    root / "workspaces",
                    root / "outputs",
                    DEFAULT_REQUIREMENTS_PATH,
                )
                self.assertFalse(report["passed"])
                self.assertIn(
                    "missing_material_output",
                    {error["type"] for error in report["errors"]},
                )
            finally:
                database.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
