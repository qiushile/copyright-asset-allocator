import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from auto_split import generate_config_yaml  # noqa: E402


class AutoSplitDescriptionTests(unittest.TestCase):
    def test_generated_config_requires_manual_description(self):
        result = {
            "copyrights": [
                {
                    "id": "auto-01",
                    "name": "示例系统",
                    "version": "V1.0",
                    "description": "",
                    "description_status": (
                        "required_manual_input_500_to_1300_characters"
                    ),
                    "include_paths": ["services/example/**"],
                    "exclude_paths": [],
                }
            ]
        }

        config = yaml.safe_load(generate_config_yaml(result))
        copyright = config["copyrights"][0]
        self.assertEqual(copyright["description"], "")
        self.assertEqual(
            copyright["description_status"],
            "required_manual_input_500_to_1300_characters",
        )


if __name__ == "__main__":
    unittest.main()
