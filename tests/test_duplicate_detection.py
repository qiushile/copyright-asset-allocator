"""Duplicate-detection (overlap) tests for the three-level detector.

Covers:
* same path across two copyrights          -> path_overlap
* different path + identical SHA-256        -> exact_file_overlap
* high-similarity code in different files   -> similar_code
* dissimilar code                           -> no findings
"""

import hashlib
from pathlib import Path

from detect_duplicates import (
    compute_function_fingerprint,
    detect_exact_file_overlap,
    detect_path_overlap,
    detect_similar_code,
    extract_functions,
)

PY_FUNC = '''def process_batch(records):
    results = []
    for record in records:
        if record.is_valid():
            results.append(record.normalize())
    return results
'''


def _file(path, sha256=None, language="Python", content=None):
    return {
        "id": abs(hash(path)) % 100000,
        "path": path,
        "sha256": sha256 or hashlib.sha256((content or path).encode()).hexdigest(),
        "language": language,
        "effective_lines": 10,
    }


class TestPathOverlap:
    def test_same_path_in_two_copyrights_detected(self):
        files_by_copyright = {
            "alpha": [_file("services/shared/util.py")],
            "beta": [_file("services/shared/util.py")],
        }
        overlaps = detect_path_overlap(files_by_copyright)
        assert len(overlaps) == 1
        assert overlaps[0]["type"] == "path_overlap"
        assert overlaps[0]["count"] == 1
        assert "services/shared/util.py" in overlaps[0]["files"]

    def test_disjoint_paths_not_flagged(self):
        files_by_copyright = {
            "alpha": [_file("services/a/main.py")],
            "beta": [_file("services/b/main.py")],
        }
        assert detect_path_overlap(files_by_copyright) == []


class TestExactFileOverlap:
    def test_different_path_same_sha_detected(self):
        shared_sha = hashlib.sha256(b"identical content").hexdigest()
        files_by_copyright = {
            "alpha": [_file("services/a/copy_of_util.py", sha256=shared_sha)],
            "beta": [_file("services/b/util.py", sha256=shared_sha)],
        }
        overlaps = detect_exact_file_overlap(files_by_copyright)
        assert len(overlaps) == 1
        assert overlaps[0]["type"] == "exact_file_overlap"
        assert overlaps[0]["sha256"] == shared_sha
        assert set(overlaps[0]["copyrights"]) == {"alpha", "beta"}

    def test_same_sha_within_one_copyright_not_flagged(self):
        shared_sha = hashlib.sha256(b"identical content").hexdigest()
        files_by_copyright = {
            "alpha": [
                _file("services/a/one.py", sha256=shared_sha),
                _file("services/a/two.py", sha256=shared_sha),
            ],
            "beta": [_file("services/b/other.py")],
        }
        assert detect_exact_file_overlap(files_by_copyright) == []


class TestSimilarCode:
    def test_identical_function_in_two_copyrights_detected(self, tmp_cwd):
        (tmp_cwd / "services" / "a").mkdir(parents=True)
        (tmp_cwd / "services" / "b").mkdir(parents=True)
        (tmp_cwd / "services" / "a" / "core.py").write_text(
            PY_FUNC, encoding="utf-8"
        )
        # Same body, different comments/whitespace -> identical token stream.
        (tmp_cwd / "services" / "b" / "core.py").write_text(
            "# helper\n\n" + PY_FUNC.replace("\n", "\n\n").replace(
                "results = []", "results   =   []"),
            encoding="utf-8",
        )

        files_by_copyright = {
            "alpha": [_file("services/a/core.py")],
            "beta": [_file("services/b/core.py")],
        }
        pairs = detect_similar_code(files_by_copyright, Path(tmp_cwd))
        assert len(pairs) == 1
        pair = pairs[0]
        assert pair["type"] == "similar_code"
        assert pair["similar_function_count"] >= 1
        assert pair["similar_functions"][0]["function_1"].endswith("::process_batch")

    def test_dissimilar_code_not_flagged(self, tmp_cwd):
        (tmp_cwd / "services" / "a").mkdir(parents=True)
        (tmp_cwd / "services" / "b").mkdir(parents=True)
        (tmp_cwd / "services" / "a" / "core.py").write_text(
            PY_FUNC, encoding="utf-8"
        )
        (tmp_cwd / "services" / "b" / "core.py").write_text(
            '''def render_page(document):
    page = Page(document)
    page.apply_style("a4")
    page.render()
    return page.output
''',
            encoding="utf-8",
        )
        files_by_copyright = {
            "alpha": [_file("services/a/core.py")],
            "beta": [_file("services/b/core.py")],
        }
        assert detect_similar_code(files_by_copyright, Path(tmp_cwd)) == []

    def test_fingerprint_ignores_whitespace_and_comments(self):
        variant = PY_FUNC.replace("results = []", "results=[]  # init")
        assert compute_function_fingerprint(PY_FUNC) == compute_function_fingerprint(
            variant
        )

    def test_short_python_functions_are_extracted_with_names(self):
        functions = extract_functions(PY_FUNC, "Python")
        assert [f["name"] for f in functions] == ["process_batch"]
