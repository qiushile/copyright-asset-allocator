"""Auto-split size-constraint tests (P0).

Semantics under test:

* ``estimate_copyright_count`` never returns a count that would force a
  copyright below ``min_lines``; returns 0 when infeasible.
* ``max_lines`` is actually enforced (it used to be dead code).
* An explicitly requested ``--count N`` that cannot be satisfied reports
  ``status: infeasible`` with requested/feasible counts instead of silently
  producing fewer copyrights.
* A single unsplittable module larger than ``max_lines`` reports
  ``constraint_violation: oversized_atomic_module``.
* used / non-team files stay excluded from splitting.
"""

import pytest

from auto_split import (
    auto_split,
    estimate_copyright_count,
    feasible_count_range,
)
from database import (
    get_connection,
    get_or_create_commit,
    get_or_create_repository,
    init_database,
    insert_file,
)


@pytest.fixture
def commit(tmp_cwd):
    init_database()
    repo_id = get_or_create_repository("sample-repo", "/tmp/sample-repo")
    return get_or_create_commit(repo_id, "f" * 40)


def _seed_files(commit_id, spec):
    """spec: {path: effective_lines} -> insert files with exact line counts."""
    for i, (path, lines) in enumerate(spec.items()):
        insert_file(
            commit_id=commit_id,
            file_path=path,
            sha256=f"{i:064d}",
            language="Python",
            total_lines=lines,
            effective_lines=lines,
            file_size=lines * 20,
        )


def _module_files(module, count, lines_each):
    return {f"{module}/file_{i}.py": lines_each for i in range(count)}


class TestEstimateCount:
    def test_exactly_minimum_yields_one(self):
        assert estimate_copyright_count(3000, 3000) == 1

    def test_double_minimum_stays_feasible(self):
        n = estimate_copyright_count(6000, 3000)
        assert 1 <= n <= 2  # any count in feasible range
        assert n * 3000 <= 6000

    def test_max_lines_bounds_estimate(self):
        # 15000 lines, each copyright must be in [3000, 6000] -> 3..5
        n = estimate_copyright_count(15000, 3000, 6000)
        assert 3 <= n <= 5

    def test_tighter_max_lines_bounds_estimate(self):
        # each copyright in [3000, 5000] -> 3..5
        n = estimate_copyright_count(15000, 3000, 5000)
        assert 3 <= n <= 5

    def test_below_minimum_is_infeasible_not_one(self):
        assert estimate_copyright_count(2999, 3000) == 0
        assert feasible_count_range(2999, 3000) is None

    def test_min_max_conflict_is_infeasible(self):
        # 7000 lines cannot be partitioned into bins of [3000, 3200]
        assert feasible_count_range(7000, 3000, 3200) is None
        assert estimate_copyright_count(7000, 3000, 3200) == 0

    def test_feasible_range_math(self):
        assert feasible_count_range(15000, 3000, 6000) == (3, 5)
        assert feasible_count_range(20000, 3000) == (1, 6)


class TestSplitOutcomes:
    def test_total_below_min_reports_infeasible(self, commit):
        _seed_files(commit, _module_files("modA", 2, 1000))  # total 2000
        result = auto_split(commit_id=commit, min_lines=3000)
        assert result["status"] == "infeasible"
        assert result["feasible_count"] == 0
        assert result["reason"] == "insufficient_eligible_code"
        assert result["copyrights"] == []

    def test_exactly_min_lines_produces_one_copyright(self, commit):
        _seed_files(commit, _module_files("modA", 3, 1000))  # total 3000
        result = auto_split(commit_id=commit, min_lines=3000)
        assert result["status"] == "ok"
        assert len(result["copyrights"]) == 1
        assert result["copyrights"][0]["total_lines"] == 3000

    def test_requested_count_satisfied(self, commit):
        # 20000 lines, request 4 copyrights
        for i, mod in enumerate(["modA", "modB", "modC", "modD", "modE"]):
            _seed_files(commit, _module_files(mod, 1, 4000))
        result = auto_split(commit_id=commit, min_lines=3000, n_copyrights=4)
        assert result["status"] == "ok"
        assert len(result["copyrights"]) == 4
        for cr in result["copyrights"]:
            assert cr["total_lines"] >= 3000
        assert sum(cr["total_lines"] for cr in result["copyrights"]) == 20000

    def test_requested_count_above_capacity_reports_infeasible(self, commit):
        # 9000 lines can support at most 3 copyrights of >= 3000 lines
        for mod in ["modA", "modB", "modC"]:
            _seed_files(commit, _module_files(mod, 3, 1000))
        result = auto_split(commit_id=commit, min_lines=3000, n_copyrights=5)
        assert result["status"] == "infeasible"
        assert result["requested_count"] == 5
        assert result["feasible_count"] == 3
        assert result["reason"] == "insufficient_eligible_code"
        assert result["copyrights"] == []

    def test_module_granularity_limits_feasible_count(self, commit):
        # 15000 lines in 3 atomic modules: range says up to 5, but only 3
        # bins can actually reach min_lines.
        for mod in ["modA", "modB", "modC"]:
            _seed_files(commit, _module_files(mod, 5, 1000))
        result = auto_split(commit_id=commit, min_lines=3000, n_copyrights=5)
        assert result["status"] == "infeasible"
        assert result["requested_count"] == 5
        assert result["feasible_count"] == 3
        assert result["reason"] == "insufficient_eligible_code"
        assert result["detail"] == "module_granularity_too_coarse"

    def test_max_lines_enforced_by_adding_copyrights(self, commit):
        # 15000 lines as 5 modules of 3000, max 5000 -> needs >= 3 copyrights,
        # packing must not leave a 6000-line bin.
        for mod in ["modA", "modB", "modC", "modD", "modE"]:
            _seed_files(commit, _module_files(mod, 3, 1000))
        result = auto_split(commit_id=commit, min_lines=3000, max_lines=5000)
        assert result["status"] == "ok"
        assert len(result["copyrights"]) >= 3
        for cr in result["copyrights"]:
            assert 3000 <= cr["total_lines"] <= 5000

    def test_max_lines_range_respected(self, commit):
        for mod in ["modA", "modB", "modC", "modD", "modE"]:
            _seed_files(commit, _module_files(mod, 3, 1000))
        result = auto_split(commit_id=commit, min_lines=3000, max_lines=6000)
        assert result["status"] == "ok"
        for cr in result["copyrights"]:
            assert 3000 <= cr["total_lines"] <= 6000

    def test_min_max_conflict_reports_infeasible(self, commit):
        # 7000 lines: 2 bins -> 3500 each (> max 3200); 3 bins -> 2333 (< min)
        _seed_files(commit, {"modA/f1.py": 4000, "modB/f2.py": 3000})
        result = auto_split(commit_id=commit, min_lines=3000, max_lines=3200)
        assert result["status"] == "infeasible"
        assert result["reason"] == "min_max_constraints_conflict"

    def test_oversized_atomic_module_reported(self, commit):
        # one unsplittable 12000-line module (<= 5 files, no sub-dirs)
        _seed_files(commit, {f"big/file_{i}.py": 4000 for i in range(3)})
        result = auto_split(commit_id=commit, min_lines=3000, max_lines=8000)
        assert result["status"] == "constraint_violation"
        violations = result["constraint_violations"]
        assert any(v["type"] == "oversized_atomic_module" for v in violations)
        v = next(v for v in violations if v["type"] == "oversized_atomic_module")
        assert v["module"] == "big"
        assert v["total_lines"] == 12000
        assert v["max_lines"] == 8000
        assert result["copyrights"] == []

    def test_large_module_split_by_subdirectory(self, commit):
        # 12000-line module that CAN be split into two 6000-line sub-modules
        files = {}
        for sub in ("a", "b"):
            for i in range(3):
                files[f"big/{sub}/file_{i}.py"] = 2000
        _seed_files(commit, files)
        result = auto_split(commit_id=commit, min_lines=3000, max_lines=8000)
        assert result["status"] == "ok"
        assert len(result["copyrights"]) == 2
        for cr in result["copyrights"]:
            assert 3000 <= cr["total_lines"] <= 8000


class TestEligibilityInSplit:
    def test_used_files_excluded(self, commit):
        _seed_files(commit, _module_files("modA", 3, 1000))
        _seed_files(commit, _module_files("modB", 3, 1000))
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM files WHERE file_path = 'modA/file_0.py'"
            ).fetchone()
            conn.execute(
                "INSERT INTO used_files (commit_id, file_id, copyright_name) "
                "VALUES (?, ?, 'earlier-copyright')",
                (commit, row[0]),
            )
        result = auto_split(commit_id=commit, min_lines=3000)
        assert result["status"] == "ok"
        assert result["skipped_used"] == 1
        assert result["total_lines"] == 5000
        all_paths = [f["path"] for cr in result["copyrights"] for f in cr["files"]]
        assert "modA/file_0.py" not in all_paths

    def test_non_team_files_excluded(self, commit):
        _seed_files(commit, _module_files("modA", 3, 1000))
        _seed_files(commit, _module_files("modB", 3, 1000))
        with get_connection() as conn:
            for path, eligible in [("modA/file_0.py", 0), ("modA/file_1.py", 1),
                                   ("modA/file_2.py", 1), ("modB/file_0.py", 1),
                                   ("modB/file_1.py", 1), ("modB/file_2.py", 1)]:
                row = conn.execute(
                    "SELECT id FROM files WHERE file_path = ?", (path,)
                ).fetchone()
                conn.execute(
                    "INSERT INTO file_team_stats (commit_id, file_id, team_ratio, is_eligible) "
                    "VALUES (?, ?, ?, ?)",
                    (commit, row[0], 0.9 if eligible else 0.1, eligible),
                )
        result = auto_split(commit_id=commit, min_lines=3000)
        assert result["status"] == "ok"
        assert result["skipped_non_team"] == 1
        all_paths = [f["path"] for cr in result["copyrights"] for f in cr["files"]]
        assert "modA/file_0.py" not in all_paths

    def test_zero_eligible_files_reports_infeasible(self, commit):
        _seed_files(commit, _module_files("modA", 3, 2000))
        with get_connection() as conn:
            for row in conn.execute("SELECT id FROM files").fetchall():
                conn.execute(
                    "INSERT INTO used_files (commit_id, file_id, copyright_name) "
                    "VALUES (?, ?, 'earlier-copyright')",
                    (commit, row[0]),
                )
        result = auto_split(commit_id=commit, min_lines=3000)
        assert result["status"] == "infeasible"
        assert result["reason"] == "no_eligible_files"
        assert result["error"]
