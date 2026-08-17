"""Decision evidence tests.

Every scanned file must get a machine-readable decision record explaining:

* eligibility -- used_before (+ which copyright used it), team_ratio vs
  threshold, final eligible flag
* allocation  -- the FULL matched/excluded rule set, the decision, and a
  status of allocated / unallocated / ambiguous / skipped_* / already_allocated

so that "why is this file in this copyright?" can always be answered from
``allocation-decisions.json`` alone.
"""

import json
from pathlib import Path

from allocate_files import (
    TEAM_RATIO_THRESHOLD,
    allocate_files_from_config,
    export_allocations,
)
from database import get_connection


def _cr(cr_id, include, exclude=None, version="V1.0"):
    return {
        "id": cr_id,
        "name": f"{cr_id} system",
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


def _file_id(scan, path):
    return next(f["id"] for f in scan["files"] if f["path"] == path)


def _decision_by_path(results, path):
    matches = [d for d in results["decisions"] if d["path"] == path]
    assert len(matches) == 1, f"expected exactly one decision record for {path}"
    return matches[0]


class TestDecisionRecords:
    def test_allocated_file_records_full_evidence(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        results = _allocate(scan, [_cr("alpha", ["services/**"])])

        record = _decision_by_path(results, "services/a/main.py")
        assert record["sha256"]
        assert record["eligibility"]["eligible"] is True
        assert record["eligibility"]["used_before"] is False
        assert record["allocation"]["matched_rules"] == ["alpha"]
        assert record["allocation"]["excluded_rules"] == []
        assert record["allocation"]["decision"] == "alpha"
        assert record["allocation"]["status"] == "allocated"

    def test_unallocated_file_has_null_decision(self, seeded_scan):
        scan, _ = seeded_scan({"other/thing.py": "print(1)\n"})
        results = _allocate(scan, [_cr("alpha", ["services/**"])])

        record = _decision_by_path(results, "other/thing.py")
        assert record["allocation"]["status"] == "unallocated"
        assert record["allocation"]["decision"] is None
        assert record["allocation"]["matched_rules"] == []
        assert record["eligibility"]["eligible"] is True

    def test_ambiguous_file_lists_every_candidate(self, seeded_scan):
        scan, _ = seeded_scan({"services/parser/main.py": "print(1)\n"})
        config = [_cr("A", ["services/**"]), _cr("B", ["services/parser/**"])]
        results = _allocate(scan, config)

        record = _decision_by_path(results, "services/parser/main.py")
        assert record["allocation"]["status"] == "ambiguous"
        assert record["allocation"]["decision"] is None
        assert sorted(record["allocation"]["matched_rules"]) == ["A", "B"]

    def test_used_file_evidence_names_the_prior_copyright(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        used_id = _file_id(scan, "services/a/main.py")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO used_files (commit_id, file_id, copyright_name) "
                "VALUES (?, ?, 'previous-copyright')",
                (scan["commit_id"], used_id),
            )

        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        record = _decision_by_path(results, "services/a/main.py")
        assert record["eligibility"]["eligible"] is False
        assert record["eligibility"]["used_before"] is True
        assert record["eligibility"]["used_in"] == ["previous-copyright"]
        assert record["allocation"]["status"] == "skipped_used"
        assert record["allocation"]["decision"] is None

    def test_non_team_file_evidence_carries_ratio_and_threshold(self, seeded_scan):
        scan, _ = seeded_scan({"services/fork.py": "print(1)\n"})
        fork_id = _file_id(scan, "services/fork.py")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO file_team_stats (commit_id, file_id, team_ratio, is_eligible) "
                "VALUES (?, ?, 0.1, 0)",
                (scan["commit_id"], fork_id),
            )

        results = _allocate(scan, [_cr("alpha", ["services/**"])])
        record = _decision_by_path(results, "services/fork.py")
        assert record["eligibility"]["eligible"] is False
        assert record["eligibility"]["team_ratio"] == 0.1
        assert record["eligibility"]["team_threshold"] == TEAM_RATIO_THRESHOLD
        assert record["eligibility"]["team_eligible"] is False
        assert record["allocation"]["status"] == "skipped_non_team"
        # evidence still explains which rules WOULD have matched
        assert record["allocation"]["matched_rules"] == ["alpha"]

    def test_already_allocated_file_is_explained(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        config = [_cr("alpha", ["services/**"])]
        _allocate(scan, config)
        results = _allocate(scan, config)  # second run, no force

        record = _decision_by_path(results, "services/a/main.py")
        assert record["allocation"]["status"] == "already_allocated"
        assert "already_allocated_to_copyright_id_" in record["allocation"]["detail"]

    def test_every_scanned_file_gets_exactly_one_record(self, seeded_scan):
        scan, _ = seeded_scan(
            {
                "services/a/main.py": "print(1)\n",
                "services/b/main.py": "print(2)\n",
                "other/thing.py": "print(3)\n",
            }
        )
        config = [_cr("A", ["services/a/**"]), _cr("B", ["services/**"])]
        results = _allocate(scan, config)

        paths = [d["path"] for d in results["decisions"]]
        assert sorted(paths) == sorted(f["path"] for f in scan["files"])
        assert len(paths) == len(set(paths))

    def test_disabled_filters_are_recorded(self, seeded_scan):
        scan, _ = seeded_scan({"services/a/main.py": "print(1)\n"})
        results = _allocate(
            scan, [_cr("alpha", ["services/**"])],
            skip_used=False, skip_non_team=False,
        )
        assert results["filters"] == {"skip_used": False, "skip_non_team": False}


class TestDecisionExport:
    def test_export_writes_allocation_decisions_json(self, seeded_scan, tmp_cwd):
        scan, repo = seeded_scan({"services/a/main.py": "print(1)\n"})
        config = [_cr("alpha", ["services/**"])]
        results = _allocate(scan, config)

        out_dir = Path("copyright/allocations")
        export_allocations(results, out_dir, config)

        payload = json.loads((out_dir / "allocation-decisions.json").read_text())
        assert payload["commit_id"] == scan["commit_id"]
        assert payload["commit_hash"] == scan["commit_hash"]
        assert payload["filters"] == {"skip_used": True, "skip_non_team": True}
        assert payload["team_threshold"] == TEAM_RATIO_THRESHOLD
        assert payload["file_count"] == 1
        assert payload["decisions"][0]["allocation"]["decision"] == "alpha"

    def test_export_still_writes_legacy_reports(self, seeded_scan, tmp_cwd):
        scan, _ = seeded_scan(
            {
                "services/a/main.py": "print(1)\n",
                "loose/none.py": "print(2)\n",
            }
        )
        config = [_cr("alpha", ["services/**"])]
        results = _allocate(scan, config)
        out_dir = Path("copyright/allocations")
        export_allocations(results, out_dir, config)

        assert (out_dir / "alpha-files.txt").exists()
        assert (out_dir / "allocation-summary.json").exists()
        assert (out_dir / "unallocated-files.json").exists()
