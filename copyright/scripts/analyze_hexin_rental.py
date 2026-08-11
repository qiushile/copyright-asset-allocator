#!/usr/bin/env python3
"""Audit Hexin rental-platform source assets for later copyright filings.

This script is deliberately evidence-oriented.  It reads committed Git objects,
not working-tree contents, and produces:

* the conservative three-repository snapshot declared by the first filing;
* the 22 Java files actually shown in the filed 60-page source material;
* line-age and prior-filing overlap evidence for later candidate repositories;
* a unique, non-overlapping file allocation for generic filing directions.

It does not generate, expand, splice, or pad source code.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


SOURCE_SUFFIXES = {
    ".java",
    ".vue",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".less",
}

EXCLUDED_PARTS = {
    "node_modules",
    "uni_modules",
    "dist",
    "build",
    "target",
    "vendor",
    "coverage",
    ".idea",
    ".vscode",
    ".hbuilderx",
}

FIRST_FILING_REPOSITORIES = (
    "rental-platform-backend",
    "rental-platform-admin",
    "rental-platform-app",
)

CANDIDATE_REPOSITORIES = (
    "rental-platform-merchant-app",
    "rental-platform-app-admin",
    "rental-platform-promotion-app",
    "rental-platform-h5-page",
)

TOPICS = (
    {
        "id": "mobile-role-collaboration",
        "name": "移动端多角色业务协同管理系统",
        "sources": {
            "rental-platform-merchant-app": (
                "api/merchant/**",
                "pages/index/**",
                "pages/order/**",
                "pages/revenue/**",
                "pages/withdraw/**",
                "components/RentalDetailPopup/**",
                "utils/echarts.js",
                "utils/numberCalc.js",
                "utils/websocket.js",
            ),
            "rental-platform-app-admin": (
                "api/order/**",
                "pages/order/**",
            ),
        },
    },
    {
        "id": "channel-identity-verification",
        "name": "渠道推广与身份核验管理系统",
        "sources": {
            "rental-platform-promotion-app": (
                "api/promoter/**",
                "pages/promoterPage/**",
                "store/modules/promoter.js",
                "subpkg/**",
            ),
            "rental-platform-h5-page": (
                "src/Project/referralPage/**",
                "src/api/referralPage.ts",
                "src/store/modules/promoter.ts",
            ),
        },
    },
    {
        "id": "electronic-agreement-compliance",
        "name": "电子协议展示与合规授权管理系统",
        "sources": {
            "rental-platform-h5-page": (
                "src/Project/orderAgreementAuthPage/**",
                "src/Project/privacyAgreement/**",
                "src/Project/serviceAgreement/**",
            ),
        },
    },
)

PAGE_HEADING_RE = re.compile(r"^## 第(?P<page>\d+)页：(?P<file>[^ ]+)\s+-\s+")
BLAME_HEADER_RE = re.compile(r"^[0-9a-f^]{40}\s+\d+\s+\d+(?:\s+\d+)?$")


def git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    """Run a read-only Git command and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=text,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if text else result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {stderr}")
    return result.stdout


def git_bytes(repo: Path, *args: str) -> bytes:
    return git(repo, *args, text=False)  # type: ignore[return-value]


def is_source_path(path: str) -> bool:
    item = Path(path)
    lower_parts = {part.lower() for part in item.parts}
    if lower_parts & EXCLUDED_PARTS:
        return False
    if item.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    lower_name = item.name.lower()
    if lower_name.endswith((".min.js", ".min.css")) or "lock." in lower_name:
        return False
    return True


def list_source_paths(repo: Path, commit: str) -> List[str]:
    output = git(repo, "ls-tree", "-r", "--name-only", "-z", commit)
    return sorted(path for path in output.split("\0") if path and is_source_path(path))


def blob(repo: Path, commit: str, path: str) -> bytes:
    return git_bytes(repo, "show", f"{commit}:{path}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_source(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def effective_lines(text: str) -> List[str]:
    return [line for line in text.splitlines() if line.strip()]


def normalized_source(text: str) -> str:
    """Normalize whitespace for conservative same-path similarity checks."""
    lines = []
    for line in effective_lines(text):
        normalized = re.sub(r"\s+", " ", line.strip())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def parse_iso_epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def cutoff_epoch(date_text: str) -> int:
    tz = timezone(timedelta(hours=8))
    return int(datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=tz).timestamp())


def blame_line_age(repo: Path, commit: str, path: str, cutoff: int) -> Dict[str, object]:
    """Count nonblank current lines on each side of the inclusive cutoff."""
    output = git(repo, "blame", "--line-porcelain", commit, "--", path)
    current_time: Optional[int] = None
    recent = 0
    old = 0
    unknown = 0
    oldest: Optional[int] = None
    newest: Optional[int] = None

    for line in output.splitlines():
        if BLAME_HEADER_RE.match(line):
            current_time = None
        elif line.startswith("author-time "):
            current_time = int(line[12:])
        elif line.startswith("\t") and line[1:].strip():
            if current_time is None:
                unknown += 1
                continue
            oldest = current_time if oldest is None else min(oldest, current_time)
            newest = current_time if newest is None else max(newest, current_time)
            if current_time >= cutoff:
                recent += 1
            else:
                old += 1

    total = recent + old + unknown
    return {
        "effective_lines": total,
        "recent_lines": recent,
        "pre_cutoff_lines": old,
        "unknown_date_lines": unknown,
        "recent_ratio": recent / total if total else 0.0,
        "oldest_line_time": (
            datetime.fromtimestamp(oldest, timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat()
            if oldest is not None
            else None
        ),
        "newest_line_time": (
            datetime.fromtimestamp(newest, timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat()
            if newest is not None
            else None
        ),
        "date_status": (
            "strict_eligible"
            if total > 0 and old == 0 and unknown == 0
            else "partial"
            if total > 0 and recent / total >= 0.5
            else "ineligible"
        ),
    }


def last_commit_on_or_before(repo: Path, date_text: str) -> str:
    tz = "+08:00"
    end = f"{date_text} 23:59:59 {tz}"
    commit = git(repo, "rev-list", "-1", f"--before={end}", "HEAD").strip()
    if not commit:
        raise RuntimeError(f"No commit in {repo} on or before {date_text}")
    return commit


def commit_record(repo: Path, commit: str) -> Dict[str, str]:
    fields = git(repo, "show", "-s", "--format=%H%x00%aI%x00%s", commit).strip().split("\0")
    return {"commit": fields[0], "author_time": fields[1], "subject": fields[2]}


def dirty_paths(repo: Path) -> List[str]:
    return [line for line in git(repo, "status", "--short").splitlines() if line]


def parse_filing_pages(documents: Sequence[Path]) -> List[Dict[str, object]]:
    grouped: Dict[str, Dict[str, object]] = {}
    for document in documents:
        text = document.read_text(encoding="utf-8", errors="ignore")
        current_file: Optional[str] = None
        for line in text.splitlines():
            heading = PAGE_HEADING_RE.match(line)
            if heading:
                current_file = heading.group("file")
                entry = grouped.setdefault(
                    current_file,
                    {"file_name": current_file, "pages": [], "documents": set(), "material_lines": []},
                )
                entry["pages"].append(int(heading.group("page")))
                entry["documents"].add(str(document))
                continue
            if current_file and line.strip() and not line.startswith("```"):
                grouped[current_file]["material_lines"].append(line)

    records = []
    for file_name, entry in sorted(grouped.items()):
        material_text = "\n".join(entry.pop("material_lines"))
        entry["pages"] = sorted(set(entry["pages"]))
        entry["documents"] = sorted(entry["documents"])
        entry["material_effective_lines"] = len(effective_lines(material_text))
        entry["material_sha256"] = sha256(material_text.encode("utf-8"))
        records.append(entry)
    return records


def historical_paths_for_name(repo: Path, file_name: str) -> List[str]:
    output = git(repo, "log", "--all", "--name-only", "--format=", "--", f"*{file_name}")
    return sorted({line.strip() for line in output.splitlines() if line.strip().endswith(file_name)})


def latest_complete_snapshot(repo: Path, anchor: str, file_names: Sequence[str]) -> str:
    """Find the newest ancestor in which every shown source filename exists."""
    required = set(file_names)
    for commit in git(repo, "rev-list", "--first-parent", anchor).splitlines():
        names = {
            Path(path).name
            for path in git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()
        }
        if required <= names:
            return commit
    raise RuntimeError("No common historical snapshot contains every shown source file")


def topic_for(repo_name: str, path: str) -> Optional[Dict[str, object]]:
    for topic in TOPICS:
        patterns = topic["sources"].get(repo_name, ())
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return topic
    return None


def write_csv(path: Path, records: Sequence[Dict[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            for key, value in row.items():
                if isinstance(value, (list, tuple, set)):
                    row[key] = "|".join(str(item) for item in value)
            writer.writerow(row)


def audit(args: argparse.Namespace) -> Dict[str, object]:
    project_root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    cutoff = cutoff_epoch(args.cutoff)

    repositories = {
        name: project_root / name for name in FIRST_FILING_REPOSITORIES + CANDIDATE_REPOSITORIES
    }
    missing = [str(path) for path in repositories.values() if not (path / ".git").exists()]
    if missing:
        raise FileNotFoundError(f"Missing Git repositories: {', '.join(missing)}")

    repo_state = {}
    for name, repo in repositories.items():
        head = git(repo, "rev-parse", "HEAD").strip()
        state = commit_record(repo, head)
        state["dirty_paths"] = dirty_paths(repo)
        repo_state[name] = state

    first_snapshot_records: List[Dict[str, object]] = []
    first_snapshot_content: Dict[Tuple[str, str], str] = {}
    prior_hashes: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    first_anchors = {}

    for repo_name in FIRST_FILING_REPOSITORIES:
        repo = repositories[repo_name]
        anchor = last_commit_on_or_before(repo, args.first_filing_completed)
        anchor_record = commit_record(repo, anchor)
        anchor_record["source_file_count"] = 0
        anchor_record["effective_lines"] = 0
        first_anchors[repo_name] = anchor_record
        for path in list_source_paths(repo, anchor):
            data = blob(repo, anchor, path)
            text = decode_source(data)
            digest = sha256(data)
            count = len(effective_lines(text))
            record = {
                "repository": repo_name,
                "anchor_commit": anchor,
                "path": path,
                "sha256": digest,
                "effective_lines": count,
            }
            first_snapshot_records.append(record)
            first_snapshot_content[(repo_name, path)] = normalized_source(text)
            prior_hashes[digest].append({"repository": repo_name, "path": path})
            anchor_record["source_file_count"] += 1
            anchor_record["effective_lines"] += count

    filing_documents = (
        args.filing_source_dir / "程序鉴别材料-源程序前30页.md",
        args.filing_source_dir / "程序鉴别材料-源程序后30页.md",
    )
    for document in filing_documents:
        if not document.exists():
            raise FileNotFoundError(document)

    shown_records = parse_filing_pages(filing_documents)
    backend_repo = repositories["rental-platform-backend"]
    backend_anchor = first_anchors["rental-platform-backend"]["commit"]
    backend_anchor_paths = set(list_source_paths(backend_repo, backend_anchor))
    shown_snapshot = latest_complete_snapshot(
        backend_repo,
        backend_anchor,
        [str(record["file_name"]) for record in shown_records],
    )
    shown_snapshot_record = commit_record(backend_repo, shown_snapshot)
    shown_snapshot_paths = set(list_source_paths(backend_repo, shown_snapshot))
    for record in shown_records:
        paths = historical_paths_for_name(backend_repo, record["file_name"])
        at_anchor = [path for path in paths if path in backend_anchor_paths]
        at_shown_snapshot = [path for path in paths if path in shown_snapshot_paths]
        record["repository"] = "rental-platform-backend"
        record["historical_paths"] = paths
        record["anchor_paths"] = at_anchor
        record["anchor_status"] = "present" if at_anchor else "absent_or_deleted"
        record["anchor_sha256"] = [
            sha256(blob(backend_repo, backend_anchor, path)) for path in at_anchor
        ]
        record["shown_snapshot_commit"] = shown_snapshot
        record["shown_snapshot_paths"] = at_shown_snapshot
        record["shown_snapshot_sha256"] = [
            sha256(blob(backend_repo, shown_snapshot, path)) for path in at_shown_snapshot
        ]

    candidate_records: List[Dict[str, object]] = []
    for repo_name in CANDIDATE_REPOSITORIES:
        repo = repositories[repo_name]
        head = repo_state[repo_name]["commit"]
        for path in list_source_paths(repo, head):
            topic = topic_for(repo_name, path)
            if not topic:
                continue
            data = blob(repo, head, path)
            text = decode_source(data)
            digest = sha256(data)
            age = blame_line_age(repo, head, path, cutoff)

            prior_matches = prior_hashes.get(digest, [])
            same_path_similarity = 0.0
            similar_prior_paths: List[str] = []
            candidate_normalized = normalized_source(text)
            for prior_repo in FIRST_FILING_REPOSITORIES:
                prior_key = (prior_repo, path)
                if prior_key not in first_snapshot_content:
                    continue
                similarity = SequenceMatcher(
                    None,
                    candidate_normalized,
                    first_snapshot_content[prior_key],
                    autojunk=False,
                ).ratio()
                if similarity > same_path_similarity:
                    same_path_similarity = similarity
                if similarity >= args.similarity_threshold:
                    similar_prior_paths.append(f"{prior_repo}/{path}")

            overlap_status = (
                "exact_prior_hash"
                if prior_matches
                else "similar_prior_path"
                if similar_prior_paths
                else "clear"
            )
            eligible = age["date_status"] == "strict_eligible" and overlap_status == "clear"
            candidate_records.append(
                {
                    "topic_id": topic["id"],
                    "topic_name": topic["name"],
                    "repository": repo_name,
                    "head_commit": head,
                    "path": path,
                    "sha256": digest,
                    **age,
                    "prior_overlap_status": overlap_status,
                    "prior_exact_matches": [
                        f"{item['repository']}/{item['path']}" for item in prior_matches
                    ],
                    "same_path_similarity": round(same_path_similarity, 6),
                    "similar_prior_paths": similar_prior_paths,
                    "strict_candidate": eligible,
                    "allocation_status": "pending" if eligible else "excluded",
                    "exclusion_reason": (
                        ""
                        if eligible
                        else overlap_status
                        if overlap_status != "clear"
                        else str(age["date_status"])
                    ),
                }
            )

    # Unique allocation: identical candidate blobs go to the first topic/repository
    # in the declared priority order.  This protects later filing packages from
    # reusing the same source file under a different path.
    topic_order = {topic["id"]: index for index, topic in enumerate(TOPICS)}
    repo_order = {name: index for index, name in enumerate(CANDIDATE_REPOSITORIES)}
    allocated_hashes: Dict[str, Tuple[str, str, str]] = {}
    for record in sorted(
        candidate_records,
        key=lambda item: (
            topic_order[item["topic_id"]],
            repo_order[item["repository"]],
            item["path"],
        ),
    ):
        if not record["strict_candidate"]:
            continue
        digest = str(record["sha256"])
        if digest in allocated_hashes:
            owner = allocated_hashes[digest]
            record["allocation_status"] = "excluded"
            record["exclusion_reason"] = "duplicate_candidate_hash"
            record["duplicate_candidate_owner"] = "/".join(owner)
            record["strict_candidate"] = False
            continue
        allocated_hashes[digest] = (
            str(record["topic_id"]),
            str(record["repository"]),
            str(record["path"]),
        )
        record["allocation_status"] = "allocated"

    topic_summary = []
    for topic in TOPICS:
        scoped = [record for record in candidate_records if record["topic_id"] == topic["id"]]
        allocated = [record for record in scoped if record["allocation_status"] == "allocated"]
        excluded = [record for record in scoped if record["allocation_status"] == "excluded"]
        topic_summary.append(
            {
                "id": topic["id"],
                "name": topic["name"],
                "scoped_files": len(scoped),
                "allocated_files": len(allocated),
                "allocated_effective_lines": sum(int(item["effective_lines"]) for item in allocated),
                "excluded_files": len(excluded),
                "excluded_pre_cutoff_or_partial": sum(
                    1 for item in excluded if item["exclusion_reason"] in {"partial", "ineligible"}
                ),
                "excluded_prior_overlap": sum(
                    1
                    for item in excluded
                    if item["exclusion_reason"] in {"exact_prior_hash", "similar_prior_path"}
                ),
                "excluded_candidate_duplicate": sum(
                    1 for item in excluded if item["exclusion_reason"] == "duplicate_candidate_hash"
                ),
                "meets_3000_effective_lines": sum(
                    int(item["effective_lines"]) for item in allocated
                )
                >= 3000,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_dir / "first-filing-snapshot-files.csv",
        first_snapshot_records,
        ("repository", "anchor_commit", "path", "sha256", "effective_lines"),
    )
    write_csv(
        output_dir / "first-filing-shown-code.csv",
        shown_records,
        (
            "file_name",
            "pages",
            "repository",
            "historical_paths",
            "anchor_paths",
            "anchor_status",
            "anchor_sha256",
            "shown_snapshot_commit",
            "shown_snapshot_paths",
            "shown_snapshot_sha256",
            "material_effective_lines",
            "material_sha256",
            "documents",
        ),
    )
    write_csv(
        output_dir / "candidate-code-audit.csv",
        candidate_records,
        (
            "topic_id",
            "topic_name",
            "repository",
            "head_commit",
            "path",
            "sha256",
            "effective_lines",
            "recent_lines",
            "pre_cutoff_lines",
            "unknown_date_lines",
            "recent_ratio",
            "oldest_line_time",
            "newest_line_time",
            "date_status",
            "prior_overlap_status",
            "prior_exact_matches",
            "same_path_similarity",
            "similar_prior_paths",
            "strict_candidate",
            "allocation_status",
            "exclusion_reason",
            "duplicate_candidate_owner",
        ),
    )

    summary = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "project_root": str(project_root),
        "cutoff": f"{args.cutoff}T00:00:00+08:00",
        "first_filing_completed": args.first_filing_completed,
        "similarity_threshold": args.similarity_threshold,
        "method": {
            "working_tree_policy": "committed Git objects only; dirty paths excluded",
            "date_policy": "every current nonblank line must have author-time on or after cutoff",
            "overlap_policy": "exclude exact hashes and same-path normalized similarity at threshold",
            "third_party_policy": "exclude node_modules, uni_modules, dist, build, target, vendor",
        },
        "repository_state": repo_state,
        "first_filing": {
            "anchors": first_anchors,
            "declared_scope_repositories": list(FIRST_FILING_REPOSITORIES),
            "snapshot_source_files": len(first_snapshot_records),
            "snapshot_effective_lines": sum(
                int(record["effective_lines"]) for record in first_snapshot_records
            ),
            "shown_source_files": len(shown_records),
            "shown_pages": sum(len(record["pages"]) for record in shown_records),
            "shown_complete_snapshot": shown_snapshot_record,
            "shown_present_at_anchor": sum(
                1 for record in shown_records if record["anchor_status"] == "present"
            ),
            "shown_absent_or_deleted_at_anchor": sum(
                1 for record in shown_records if record["anchor_status"] != "present"
            ),
            "source_documents": [
                {"path": str(path), "sha256": sha256(path.read_bytes())}
                for path in filing_documents
            ],
        },
        "topics": topic_summary,
        "candidate_totals": {
            "audited_files": len(candidate_records),
            "allocated_files": sum(
                1 for record in candidate_records if record["allocation_status"] == "allocated"
            ),
            "allocated_effective_lines": sum(
                int(record["effective_lines"])
                for record in candidate_records
                if record["allocation_status"] == "allocated"
            ),
        },
        "outputs": {
            "first_filing_snapshot": "first-filing-snapshot-files.csv",
            "first_filing_shown_code": "first-filing-shown-code.csv",
            "candidate_code_audit": "candidate-code-audit.csv",
        },
    }
    (output_dir / "audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--filing-source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cutoff", default="2025-08-02")
    parser.add_argument("--first-filing-completed", default="2025-10-24")
    parser.add_argument("--similarity-threshold", type=float, default=0.8)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = audit(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
