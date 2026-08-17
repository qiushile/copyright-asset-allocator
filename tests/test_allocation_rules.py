"""Allocation rule tests.

Protects the P0 ambiguity fix and the eligibility filters:

* a file matched by exactly one copyright is allocated to it
* a file matched by no copyright is UNALLOCATED
* a file matched by >1 copyright is AMBIGUOUS -- never silently assigned by
  YAML ordering (tested in both orders)
* exclude_paths beat include_paths
* used files are skipped by default
* non-team files are skipped when ownership analysis exists
* force reallocation clears previous state
* re-running allocate without force reports conflicts instead of corrupting
* copyrights.yaml structural problems raise actionable ConfigError
"""

import pytest

from allocate_files import (
    ConfigError,
    allocate_files_from_config,
    match_copyrights,
    validate_copyrights_config,
)
from database import get_allocation, get_connection


def _cr(cr_id, include, exclude=None, name=None, version="V1.0"):
    return {
        "id": cr_id,
        "name": name or f"{cr_id} system",
        "version": version,
        "include_paths": include,
        "exclude_paths": exclude or [],
    }


def _allocate(scan, copyrights_config, **kwargs):
    return allocate_files_from_config(
        commit_id=scan["commit_id"],
        files=scan["files"],
        copyrights_config=copyrights_config,
        **kwargs,
    )


def _allocated_paths(results, cr_id):
    return sorted(f["path"] for f in results["allocations"][cr_id])


def _file_id(scan, path):
    return next(f["id"] for f in scan["files"] if f["path"] == path)


class TestMatchSetSemantics:
    def test_single_match_allocates(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        assert _allocated_paths(results, "alpha") == ["services/a/main.py"]
        assert results["unallocated"] == []
        assert results["ambiguous"] == []

    def test_no_match_is_unallocated(self, seeded_scan):
        scan, _ = seeded_scan({"tools/gen.py": "print(1)\n"})
        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        assert results["allocations"]["alpha"] == []
        assert [u["file"] for u in results["unallocated"]] == ["tools/gen.py"]

    def test_exclude_beats_include(self, seeded_scan):
        scan, _ = seeded_scan(
            {
                "services/a/main.py": "print(1)\n",
                "services/a/legacy/lib.py": "print(2)\n",
            }
        )
        results = _allocate(
            scan, [_cr("alpha", ["services/**"], exclude=["services/a/legacy/**"])]
        )
        assert _allocated_paths(results, "alpha") == ["services/a/main.py"]
        assert [u["file"] for u in results["unallocated"]] == ["services/a/legacy/lib.py"]


class TestAmbiguity:
    """A file matched by multiple copyrights must never be silently assigned."""

    CONFIGS = [
        # broad rule first, narrow rule second
        [_cr("A", ["services/**"]), _cr("B", ["services/parser/**"])],
        # narrow rule first, broad rule second -- result must be identical
        [_cr("B", ["services/parser/**"]), _cr("A", ["services/**"])],
    ]

    @pytest.mark.parametrize("copyrights_config", CONFIGS, ids=["broad-first", "narrow-first"])
    def test_double_match_is_ambiguous_regardless_of_yaml_order(
        self, seeded_scan, copyrights_config
    ):
        scan, _ = seeded_scan(
            {
                "services/parser/main.py": "print(1)\n",
                "services/other/main.py": "print(2)\n",
            }
        )
        results = _allocate(scan, copyrights_config)

        # the file matching both rules is NOT allocated anywhere
        assert _allocated_paths(results, "A") == ["services/other/main.py"]
        assert _allocated_paths(results, "B") == []

        assert len(results["ambiguous"]) == 1
        item = results["ambiguous"][0]
        assert item["file"] == "services/parser/main.py"
        assert sorted(item["matched_copyrights"]) == ["A", "B"]
        assert item["reason"] == "multiple_copyrights_match"

        # nothing landed in the database for the ambiguous file
        file_id = _file_id(scan, "services/parser/main.py")
        assert get_allocation(scan["commit_id"], file_id) is None

    def test_three_way_match_reports_all_candidates(self, seeded_scan):
        scan, _ = seeded_scan({"shared/core.py": "print(1)\n"})
        config = [
            _cr("A", ["shared/**"]),
            _cr("B", ["shared/core.py"]),
            _cr("C", ["**/core.py"]),
        ]
        results = _allocate(scan, config)
        assert len(results["ambiguous"]) == 1
        assert sorted(results["ambiguous"][0]["matched_copyrights"]) == ["A", "B", "C"]

    def test_match_copyrights_decision_record(self):
        config = [_cr("A", ["services/**"]), _cr("B", ["services/parser/**"])]
        assert match_copyrights("services/parser/main.py", config)["status"] == "ambiguous"
        single = match_copyrights("services/other/main.py", config)
        assert single == {
            "matched_rules": ["A"],
            "excluded_rules": [],
            "decision": "A",
            "status": "allocated",
        }
        assert match_copyrights("tools/x.py", config)["status"] == "unallocated"
        excluded = match_copyrights(
            "services/legacy/x.py",
            [_cr("A", ["services/**"], exclude=["services/legacy/**"])],
        )
        assert excluded["excluded_rules"] == ["A"]
        assert excluded["status"] == "unallocated"


class TestEligibilityFilters:
    def test_used_file_is_skipped_by_default(self, seeded_scan):
        scan, _ = seeded_scan(
            {
                "services/a/main.py": "print(1)\n",
                "services/b/main.py": "print(2)\n",
            }
        )
        used_id = _file_id(scan, "services/a/main.py")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO used_files (commit_id, file_id, copyright_name) "
                "VALUES (?, ?, 'previous-copyright')",
                (scan["commit_id"], used_id),
            )

        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        assert _allocated_paths(results, "alpha") == ["services/b/main.py"]
        assert [s["file"] for s in results["skipped_used"]] == ["services/a/main.py"]

    def test_used_file_included_when_skip_disabled(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        used_id = _file_id(scan, "services/a/main.py")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO used_files (commit_id, file_id, copyright_name) "
                "VALUES (?, ?, 'previous-copyright')",
                (scan["commit_id"], used_id),
            )
        results = _allocate(scan, [_cr("alpha", ["services/**"])], skip_used=False)
        assert _allocated_paths(results, "alpha") == ["services/a/main.py"]

    def test_non_team_file_is_skipped_when_analysis_exists(self, seeded_scan):
        scan, _ = seeded_scan(
            {
                "services/team.py": "print(1)\n",
                "services/fork.py": "print(2)\n",
            }
        )
        team_id = _file_id(scan, "services/team.py")
        fork_id = _file_id(scan, "services/fork.py")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO file_team_stats (commit_id, file_id, team_ratio, is_eligible) "
                "VALUES (?, ?, 0.9, 1)",
                (scan["commit_id"], team_id),
            )
            conn.execute(
                "INSERT INTO file_team_stats (commit_id, file_id, team_ratio, is_eligible) "
                "VALUES (?, ?, 0.1, 0)",
                (scan["commit_id"], fork_id),
            )

        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        assert _allocated_paths(results, "alpha") == ["services/team.py"]
        assert [s["file"] for s in results["skipped_non_team"]] == ["services/fork.py"]

    def test_no_ownership_analysis_means_pass_through(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        assert _allocated_paths(results, "alpha") == ["services/a/main.py"]
        assert results["skipped_non_team"] == []


class TestReallocation:
    def test_repeat_allocate_without_force_reports_conflict(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        config = [_cr("alpha", ["services/**"])]
        first = _allocate(scan, config)
        assert _allocated_paths(first, "alpha") == ["services/a/main.py"]

        second = _allocate(scan, config)
        assert second["allocations"]["alpha"] == []
        assert [c["file"] for c in second["conflicts"]] == ["services/a/main.py"]

    def test_force_reallocates_and_can_change_owner(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        first = _allocate(scan, [_cr("alpha", ["services/**"])])
        assert _allocated_paths(first, "alpha") == ["services/a/main.py"]

        second = _allocate(scan, [_cr("beta", ["services/**"])], force=True)
        assert second["reallocated"] is True
        assert _allocated_paths(second, "beta") == ["services/a/main.py"]
        # alpha is not part of the new config, so it has no allocation bucket
        assert "alpha" not in second["allocations"]

        file_id = _file_id(scan, "services/a/main.py")
        cr_db_id = get_allocation(scan["commit_id"], file_id)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT copyright_id FROM copyrights WHERE id = ?", (cr_db_id,)
            ).fetchone()
        assert row[0] == "beta"


class TestConfigValidation:
    def _base(self):
        return {"copyrights": [_cr("alpha", ["services/**"])]}

    def test_valid_config_passes(self):
        assert validate_copyrights_config(self._base())

    def test_missing_version_reports_index_and_key(self):
        config = self._base()
        del config["copyrights"][0]["version"]
        with pytest.raises(ConfigError, match=r"copyrights\[0\]\.version is required"):
            validate_copyrights_config(config)

    def test_second_entry_error_is_indexed(self):
        config = self._base()
        second = _cr("beta", ["tools/**"])
        del second["name"]
        config["copyrights"].append(second)
        with pytest.raises(ConfigError, match=r"copyrights\[1\]\.name is required"):
            validate_copyrights_config(config)

    def test_duplicate_ids_rejected(self):
        config = self._base()
        config["copyrights"].append(_cr("alpha", ["tools/**"]))
        with pytest.raises(ConfigError, match="duplicates"):
            validate_copyrights_config(config)

    def test_empty_include_paths_rejected(self):
        config = self._base()
        config["copyrights"][0]["include_paths"] = []
        with pytest.raises(ConfigError, match="include_paths"):
            validate_copyrights_config(config)

    def test_missing_copyrights_key_rejected(self):
        with pytest.raises(ConfigError, match="copyrights"):
            validate_copyrights_config({})

    def test_allocate_rejects_invalid_config(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        config = [_cr("alpha", ["services/**"])]
        del config[0]["version"]
        with pytest.raises(ConfigError):
            _allocate(scan, config)
