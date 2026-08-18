"""Append-only audit event log tests.

Covers:

* ``record_audit_event`` / ``list_audit_events`` round-trip (all fields)
* append-only semantics: repeated events are never coalesced or upserted
* JSON details round-trip and type validation
* the CLI actually records an ordered trail across scan -> allocate,
  including the ``rejected_ambiguous`` status for ambiguous configs
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import copyright_cli  # noqa: E402

from conftest import commit_all, make_git_repo, write_files  # noqa: E402
from database import (  # noqa: E402
    AUDIT_OPERATIONS,
    init_database,
    list_audit_events,
    record_audit_event,
)


class TestRecordAndList:
    def test_round_trip_all_fields(self, tmp_cwd):
        init_database()
        event_id = record_audit_event(
            'SCAN_COMPLETED',
            repository='/tmp/repo',
            commit_hash='abc123',
            config_sha256='sha-1',
            status='ok',
            details={'files': 3},
        )
        events = list_audit_events()
        assert len(events) == 1
        event = events[0]
        assert event['id'] == event_id
        assert event['operation'] == 'SCAN_COMPLETED'
        assert event['repository'] == '/tmp/repo'
        assert event['commit_hash'] == 'abc123'
        assert event['config_sha256'] == 'sha-1'
        assert event['status'] == 'ok'
        assert event['details'] == {'files': 3}
        assert event['created_at']  # timestamp populated by SQLite

    def test_append_only_keeps_repeated_events(self, tmp_cwd):
        init_database()
        first = record_audit_event('SCAN_COMPLETED', status='ok')
        second = record_audit_event('SCAN_COMPLETED', status='ok')
        events = list_audit_events()
        assert len(events) == 2
        assert {e['id'] for e in events} == {first, second}

    def test_details_json_round_trip(self, tmp_cwd):
        init_database()
        payload = {
            'copyrights': {'alpha': 2, 'beta': 5},
            'nested': {'unicode': '软著'},
            'flag': False,
        }
        record_audit_event('ALLOCATION_APPLIED', details=payload)
        events = list_audit_events()
        assert events[0]['details'] == payload

    def test_details_none_allowed(self, tmp_cwd):
        init_database()
        record_audit_event('USED_CODE_CLEARED')
        events = list_audit_events()
        assert events[0]['details'] is None

    def test_non_dict_details_rejected(self, tmp_cwd):
        init_database()
        with pytest.raises(TypeError):
            record_audit_event('SCAN_COMPLETED', details=['not', 'a', 'dict'])

    def test_operation_filter_and_limit(self, tmp_cwd):
        init_database()
        for _ in range(3):
            record_audit_event('SCAN_COMPLETED')
        record_audit_event('FINAL_AUDIT', status='passed')
        scans = list_audit_events(operation='SCAN_COMPLETED')
        assert len(scans) == 3
        assert all(e['operation'] == 'SCAN_COMPLETED' for e in scans)
        limited = list_audit_events(limit=2)
        assert len(limited) == 2
        # newest first
        assert limited[0]['id'] > limited[1]['id']

    def test_all_cli_operations_are_canonical(self):
        """The CLI hooks must only emit operations from the canonical set."""
        import re

        cli_source = (ROOT / 'copyright_cli.py').read_text(encoding='utf-8')
        emitted = set(re.findall(r"_audit_event\(\s*\n?\s*'([A-Z_]+)'", cli_source))
        assert emitted, "no audit hooks found in copyright_cli.py"
        unknown = emitted - set(AUDIT_OPERATIONS)
        assert not unknown, f"CLI emits non-canonical operations: {unknown}"


class TestCliEventTrail:
    def _scan_args(self, repo):
        return SimpleNamespace(repo_path=str(repo), commit=None, verbose=False)

    def _allocate_args(self, config_path):
        return SimpleNamespace(
            config=str(config_path),
            force=False,
            no_skip_used=False,
            no_skip_non_team=False,
            verbose=False,
        )

    def test_scan_then_allocate_records_ordered_trail(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {
            'services/a/main.py': 'print(1)\n',
            'services/a/util.py': 'print(2)\n',
        })
        commit_hash = commit_all(repo, 'seed')

        copyright_cli.cmd_scan(self._scan_args(repo))

        config_path = tmp_cwd / 'copyrights.yaml'
        config_path.write_text(
            'copyrights:\n'
            '  - id: alpha\n'
            '    name: Alpha System\n'
            '    version: V1.0\n'
            '    include_paths:\n'
            '      - services/**\n',
            encoding='utf-8',
        )
        copyright_cli.cmd_allocate(self._allocate_args(config_path))

        events = list_audit_events()
        operations = [e['operation'] for e in reversed(events)]  # oldest first
        assert operations == ['SCAN_COMPLETED', 'ALLOCATION_APPLIED']

        scan_event, alloc_event = events[1], events[0]  # newest first
        assert scan_event['commit_hash'] == commit_hash
        assert scan_event['details']['files'] == 2

        assert alloc_event['status'] == 'ok'
        assert alloc_event['commit_hash'] == commit_hash
        assert alloc_event['config_sha256']  # config pinned by hash
        assert alloc_event['details']['copyrights'] == {'alpha': 2}
        assert alloc_event['details']['unallocated'] == 0

    def test_ambiguous_allocate_records_rejection(self, tmp_cwd):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {'services/parser/main.py': 'print(1)\n'})
        commit_all(repo, 'seed')
        copyright_cli.cmd_scan(self._scan_args(repo))

        # Two copyrights both match services/parser/main.py -> ambiguous.
        config_path = tmp_cwd / 'copyrights.yaml'
        config_path.write_text(
            'copyrights:\n'
            '  - id: alpha\n'
            '    name: Alpha System\n'
            '    version: V1.0\n'
            '    include_paths:\n'
            '      - services/**\n'
            '  - id: beta\n'
            '    name: Beta System\n'
            '    version: V1.0\n'
            '    include_paths:\n'
            '      - services/parser/**\n',
            encoding='utf-8',
        )
        with pytest.raises(SystemExit) as exc_info:
            copyright_cli.cmd_allocate(self._allocate_args(config_path))
        assert exc_info.value.code == 1

        events = list_audit_events()
        alloc_events = [e for e in events if e['operation'] == 'ALLOCATION_APPLIED']
        assert len(alloc_events) == 1
        assert alloc_events[0]['status'] == 'rejected_ambiguous'
        assert alloc_events[0]['details']['ambiguous_files'] == 1

    def test_events_command_prints_trail(self, tmp_cwd, capsys):
        repo = make_git_repo(tmp_cwd)
        write_files(repo, {'services/a/main.py': 'print(1)\n'})
        commit_all(repo, 'seed')
        copyright_cli.cmd_scan(self._scan_args(repo))

        copyright_cli.cmd_events(SimpleNamespace(limit=50, operation=None))
        out = capsys.readouterr().out
        assert 'SCAN_COMPLETED' in out
        assert 'oldest first' in out

    def test_events_command_empty_database(self, tmp_cwd, capsys):
        copyright_cli.cmd_events(SimpleNamespace(limit=50, operation=None))
        out = capsys.readouterr().out
        assert 'No audit events recorded yet.' in out
