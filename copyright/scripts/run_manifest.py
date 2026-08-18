#!/usr/bin/env python3
"""Run manifest: reproducible record of what produced the current allocation.

Every successful allocation writes ``copyright/inventory/run-manifest.json``
recording:

* tool version
* which repository + Git commit
* which ``copyrights.yaml`` (sha256 of the exact bytes)
* which ``material-requirements.yaml``
* the time filter (since) and eligibility filters active at allocation time
* the min/max line constraints of the last auto-split plan (if any)

Downstream steps (workspace build, final audit) must reference the same
commit; any mismatch is a hard, loud failure.  This answers:

    哪一个 Git Commit + 哪一套配置 + 哪一版规则 + 哪一组过滤条件
    产生了当前申请材料?
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# Single source of truth for the tool version (referenced by the manifest).
TOOL_VERSION = "1.2.0"

DEFAULT_MANIFEST_PATH = Path('copyright/inventory/run-manifest.json')
DEFAULT_CONFIG_PATH = Path('copyright/config/copyrights.yaml')
DEFAULT_REQUIREMENTS_PATH = Path('copyright/config/material-requirements.yaml')
DEFAULT_SPLIT_REPORT_PATH = Path('copyright/inventory/auto-split-report.json')


class ManifestCommitMismatch(RuntimeError):
    """Raised when an operation targets a commit different from the manifest."""


def sha256_file(path: Path) -> Optional[str]:
    """SHA-256 of a file's bytes, or None if it does not exist."""
    if path is None or not Path(path).is_file():
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _split_constraints(split_report_path: Path = DEFAULT_SPLIT_REPORT_PATH) -> dict:
    """Pull min/max lines out of the last auto-split report, if present."""
    try:
        report = json.loads(Path(split_report_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'min_lines': None, 'max_lines': None, 'source': None}
    return {
        'min_lines': report.get('min_lines'),
        'max_lines': report.get('max_lines'),
        'source': str(split_report_path),
    }


def write_run_manifest(
    commit_id: int,
    commit_hash: str,
    repository: Optional[str],
    config_path: Path = DEFAULT_CONFIG_PATH,
    requirements_path: Path = DEFAULT_REQUIREMENTS_PATH,
    filters: Optional[dict] = None,
    since: Optional[str] = None,
    allocation: Optional[dict] = None,
    path: Path = DEFAULT_MANIFEST_PATH,
) -> dict:
    """Write the run manifest and return it as a dict."""
    if allocation is None:
        allocation = _split_constraints()

    manifest = {
        'tool_version': TOOL_VERSION,
        'generated_at': datetime.now().astimezone().isoformat(),
        'repository': repository,
        'commit_id': commit_id,
        'commit_hash': commit_hash,
        'since': since,
        'filters': filters or {},
        'allocation': allocation,
        'config': {
            'path': str(config_path),
            'sha256': sha256_file(config_path),
        },
        'requirements': {
            'path': str(requirements_path),
            'sha256': sha256_file(requirements_path),
        },
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest


def load_run_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> Optional[dict]:
    """Load the run manifest, or None if it does not exist / is unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def require_commit_consistency(
    commit_hash: str,
    context: str,
    path: Path = DEFAULT_MANIFEST_PATH,
) -> Optional[dict]:
    """Fail loudly if the manifest was produced for a different commit.

    Returns the manifest when consistent (or None when no manifest exists).
    """
    manifest = load_run_manifest(path)
    if manifest is None:
        return None
    manifest_commit = manifest.get('commit_hash')
    if manifest_commit and manifest_commit != commit_hash:
        raise ManifestCommitMismatch(
            f"{context}: commit mismatch -- run manifest {path} was produced "
            f"for commit {manifest_commit}, but this operation targets "
            f"{commit_hash}. Re-run scan+allocate at one commit; do not mix "
            f"artifacts from different commits."
        )
    return manifest
