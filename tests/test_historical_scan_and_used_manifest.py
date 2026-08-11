import csv
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "copyright" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from database import get_connection  # noqa: E402
from mark_used_code import mark_used_paths_from_csv  # noqa: E402
from scan_repository import scan_repository  # noqa: E402


class HistoricalScanAndManifestTests(unittest.TestCase):
    def _git(self, repo, *args):
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_scan_reads_commit_object_instead_of_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "example-repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.name", "Test")
            self._git(repo, "config", "user.email", "test@example.com")
            source = repo / "example.py"
            committed = b"print('committed')\n"
            source.write_bytes(committed)
            self._git(repo, "add", "example.py")
            self._git(repo, "commit", "-qm", "test: create initial snapshot")
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source.write_text("print('dirty')\n", encoding="utf-8")

            previous = Path.cwd()
            try:
                os.chdir(root)
                result = scan_repository(repo, commit)
            finally:
                os.chdir(previous)

            record = next(item for item in result["files"] if item["path"] == "example.py")
            self.assertEqual(record["sha256"], hashlib.sha256(committed).hexdigest())
            self.assertEqual(result["repo_path"], str(repo.resolve()))

    def test_exact_manifest_import_marks_only_requested_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "manifest-repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.name", "Test")
            self._git(repo, "config", "user.email", "test@example.com")
            (repo / "used.py").write_text("print('used')\n", encoding="utf-8")
            (repo / "unused.py").write_text("print('unused')\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-qm", "test: create manifest fixture")
            manifest = root / "used.csv"
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["paths"])
                writer.writeheader()
                writer.writerow({"paths": "used.py|missing.py"})

            previous = Path.cwd()
            try:
                os.chdir(root)
                result = scan_repository(repo)
                imported = mark_used_paths_from_csv(
                    result["commit_id"], manifest, "Existing V1.0", "paths"
                )
                with get_connection() as connection:
                    rows = connection.execute(
                        """
                        SELECT f.file_path FROM used_files uf
                        JOIN files f ON f.id = uf.file_id
                        WHERE uf.commit_id = ?
                        """,
                        (result["commit_id"],),
                    ).fetchall()
            finally:
                os.chdir(previous)

            self.assertEqual(imported["unmatched_paths"], ["missing.py"])
            self.assertEqual(rows, [("used.py",)])


if __name__ == "__main__":
    unittest.main()
