import copy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from material_requirements import (  # noqa: E402
    DEFAULT_REQUIREMENTS_PATH,
    FrozenAssetError,
    assert_generation_allowed,
    assert_output_path_allowed,
    audit_protected_assets,
    compute_openssl_directory_digest,
    load_material_requirements,
)


class MaterialRequirementsLoaderTests(unittest.TestCase):
    def test_repository_rules_do_not_require_machine_local_assets(self):
        requirements = load_material_requirements(DEFAULT_REQUIREMENTS_PATH)
        self.assertEqual(requirements["protected_assets"], [])
        self.assertTrue(audit_protected_assets(requirements)["passed"])

    def test_recursive_freeze_digest_detects_nested_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protected = root / "submitted"
            nested = protected / "nested"
            nested.mkdir(parents=True)
            (protected / "material.pdf").write_bytes(b"pdf")
            nested_file = nested / "manual.md"
            nested_file.write_text("original", encoding="utf-8")

            requirements = copy.deepcopy(
                load_material_requirements(DEFAULT_REQUIREMENTS_PATH)
            )
            requirements["_config_path"] = str(root / "requirements.yaml")
            requirements["protected_assets"] = [
                {
                    "copyright_id": "example-01",
                    "software_name": "示例系统",
                    "immutable": True,
                    "path": "submitted",
                    "digest_algorithm": "openssl-directory-v1",
                    "expected_digest": compute_openssl_directory_digest(protected),
                }
            ]

            report = audit_protected_assets(requirements)
            self.assertTrue(report["passed"], report)
            with self.assertRaises(FrozenAssetError):
                assert_generation_allowed("example-01", "其他名称", requirements)
            with self.assertRaises(FrozenAssetError):
                assert_output_path_allowed(nested_file, requirements)

            nested_file.write_text("changed", encoding="utf-8")
            self.assertFalse(audit_protected_assets(requirements)["passed"])


if __name__ == "__main__":
    unittest.main()
