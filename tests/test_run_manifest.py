"""Run manifest + Git truthfulness tests.

The manifest records which commit/config/rules/filters produced the current
allocation.  Workspace builds must read content from the LOCKED commit, never
from the (possibly moved) working tree, and must stay inside their root.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import commit_all, write_files

from allocate_files import allocate_files_from_config
from build_workspace import build_workspace, safe_workspace_path
from database import get_connection
from run_manifest import (
    TOOL_VERSION,
    ManifestCommitMismatch,
    load_run_manifest,
    require_commit_consistency,
    sha256_file,
    write_run_manifest,
)
from scan_repository import scan_repository


def _allocate_all(scan):
    return allocate_files_from_config(
        commit_id=scan["commit_id"],
        files=scan["files"],
        copyrights_config=[{
            "id": "alpha", "name": "alpha system", "version": "V1.0",
            "include_paths": ["services/**"], "exclude_paths": [],
        }],
    )


class TestWriteRunManifest:
    def test_manifest_records_commit_config_and_filters(self, tmp_cwd):
        (tmp_cwd / "copyright/config").mkdir(parents=True)
        config = tmp_cwd / "copyright/config/copyrights.yaml"
        config.write_text("copyrights: []\n")

        manifest = write_run_manifest(
            commit_id=1,
            commit_hash="abc123",
            repository="/tmp/repo",
            config_path=config,
            requirements_path=tmp_cwd / "no-such.yaml",
            filters={"skip_used": True, "skip_non_team": True},
            since="2025-01-01",
        )

        assert manifest["tool_version"] == TOOL_VERSION
        assert manifest["commit_hash"] == "abc123"
        assert manifest["since"] == "2025-01-01"
        assert manifest["filters"] == {"skip_used": True, "skip_non_team": True}
        assert manifest["config"]["sha256"] == sha256_file(config)
        assert manifest["requirements"]["sha256"] is None  # missing file -> None

        on_disk = json.loads(
            (tmp_cwd / "copyright/inventory/run-manifest.json").read_text()
        )
        assert on_disk == manifest

    def test_config_hash_tracks_content_changes(self, tmp_cwd):
        config = tmp_cwd / "copyrights.yaml"
        config.write_text("v1\n")
        m1 = write_run_manifest(1, "abc", None, config_path=config, path=tmp_cwd / "m1.json")
        config.write_text("v2\n")
        m2 = write_run_manifest(1, "abc", None, config_path=config, path=tmp_cwd / "m2.json")
        assert m1["config"]["sha256"] != m2["config"]["sha256"]


class TestCommitConsistency:
    def test_no_manifest_is_allowed(self, tmp_cwd):
        assert require_commit_consistency("abc", "test") is None

    def test_matching_commit_passes(self, tmp_cwd):
        path = tmp_cwd / "manifest.json"
        write_run_manifest(1, "abc", None, path=path)
        manifest = require_commit_consistency("abc", "test", path=path)
        assert manifest["commit_hash"] == "abc"

    def test_mismatched_commit_fails_loudly(self, tmp_cwd):
        path = tmp_cwd / "manifest.json"
        write_run_manifest(1, "abc", None, path=path)
        with pytest.raises(ManifestCommitMismatch):
            require_commit_consistency("def", "workspace build", path=path)


class TestWorkspaceGitTruthfulness:
    def test_workspace_reads_locked_commit_not_working_tree(self, tmp_cwd, seeded_scan):
        scan, repo = seeded_scan({"services/a/main.py": "original = 1\n"})
        _allocate_all(scan)

        # working tree drifts (uncommitted change) after the scan
        write_files(repo, {"services/a/main.py": "TAMPERED = 1\n"})

        result = build_workspace(
            commit_id=scan["commit_id"],
            copyright_id="alpha",
            output_base=Path("copyright/workspaces"),
        )
        assert result["status"] == "success", result["errors"]
        content = (Path("copyright/workspaces/alpha/source/services/a/main.py")
                   .read_text())
        assert content == "original = 1\n"  # locked commit bytes, not tree

    def test_workspace_stable_after_head_moves(self, tmp_cwd, seeded_scan):
        scan, repo = seeded_scan({"services/a/main.py": "v1 = 1\n"})
        _allocate_all(scan)

        # HEAD moves to a new commit that rewrites the file
        write_files(repo, {"services/a/main.py": "v2 = 2\nextra = 3\n"})
        commit_all(repo, "feat: move head")

        result = build_workspace(
            commit_id=scan["commit_id"],
            copyright_id="alpha",
            output_base=Path("copyright/workspaces"),
        )
        assert result["status"] == "success", result["errors"]
        content = (Path("copyright/workspaces/alpha/source/services/a/main.py")
                   .read_text())
        assert content == "v1 = 1\n"

        manifest = json.loads(
            Path("copyright/workspaces/alpha/SOURCE_MANIFEST.json").read_text()
        )
        assert manifest["repositoryCommit"] == scan["commit_hash"]

    def test_workspace_rejects_manifest_for_other_commit(self, tmp_cwd, seeded_scan):
        scan, repo = seeded_scan({"services/a/main.py": "v1 = 1\n"})
        _allocate_all(scan)
        write_run_manifest(999, "deadbeef", None)  # foreign manifest

        with pytest.raises(ManifestCommitMismatch):
            build_workspace(
                commit_id=scan["commit_id"],
                copyright_id="alpha",
                output_base=Path("copyright/workspaces"),
            )


class TestWorkspacePathSafety:
    def test_safe_path_accepts_nested_relative(self, tmp_cwd):
        root = tmp_cwd / "source"
        root.mkdir()
        target = safe_workspace_path(root, "a/b/c.py")
        assert target == (root / "a/b/c.py").resolve()

    @pytest.mark.parametrize("evil", [
        "../escape.py",
        "a/../../escape.py",
        "/etc/passwd",
    ])
    def test_safe_path_rejects_escapes(self, tmp_cwd, evil):
        root = tmp_cwd / "source"
        root.mkdir()
        with pytest.raises(ValueError):
            safe_workspace_path(root, evil)


class TestFinalAuditReferencesManifest:
    def test_audit_flags_manifest_commit_mismatch(self, tmp_cwd, seeded_scan):
        scan, repo = seeded_scan({"services/a/main.py": "v1 = 1\n"})
        _allocate_all(scan)
        build_workspace(scan["commit_id"], "alpha", Path("copyright/workspaces"))

        # manifest from a DIFFERENT commit must fail the audit loudly
        write_run_manifest(999, "deadbeef", None)

        from final_audit import audit_commit
        results = audit_commit(scan["commit_id"], Path("copyright/workspaces"))
        assert results["passed"] is False
        assert any(e["type"] == "run_manifest_commit_mismatch"
                   for e in results["errors"])

    def test_audit_accepts_consistent_manifest(self, tmp_cwd, seeded_scan):
        scan, repo = seeded_scan({"services/a/main.py": "v1 = 1\n"})
        _allocate_all(scan)
        build_workspace(scan["commit_id"], "alpha", Path("copyright/workspaces"))
        write_run_manifest(scan["commit_id"], scan["commit_hash"], str(repo))

        from final_audit import audit_commit
        results = audit_commit(scan["commit_id"], Path("copyright/workspaces"))
        assert results["run_manifest"]["found"] is True
        assert results["run_manifest"]["commit_match"] is True
        assert not any(e.get("type") == "run_manifest_commit_mismatch"
                       for e in results["errors"])
