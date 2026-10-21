"""零成本真实CLI子进程：摄入、分章和当前付费边界的授权停止。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from novel.llm.ledger import read_cost_snapshot


def test_current_cli_reaches_stage2_from_new_source_without_buying(tmp_path):
    source = tmp_path / '中文样书_123.txt'
    original = '雨夜有人敲门，我终于等到了他的解释。\n1\n我抱着孩子走进屋。\n2\n来客在门前说明来意。\n'
    source.write_bytes(original.encode('utf8'))
    output = tmp_path / 'output'
    root = Path(__file__).resolve().parents[1]

    def run(*args):
        return subprocess.run([sys.executable, '-X', 'utf8', '-m', 'novel.cli', *args],
            cwd=root, capture_output=True, text=True, encoding='utf8', timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    ingest = run('run', 'stage0.remove', '--base', str(output), '--source', str(source))
    assert ingest.returncode == 0, ingest.stdout + ingest.stderr
    partition = run('run', 'stage1.partition', '123', '--base', str(output))
    assert partition.returncode == 0, partition.stdout + partition.stderr
    stopped = run('run', 'str06.stage2.chapters', '123', '--base', str(output), '--check-only')
    assert stopped.returncode == 2, stopped.stdout + stopped.stderr
    books = list(output.glob('*/runtime/candidate_workflow.json'))
    assert len(books) == 1
    book = books[0].parent.parent
    workflow = json.loads(books[0].read_bytes())
    assert workflow['epoch']['contract_version'] == 'story-v1'
    status = json.loads((book / 'runtime/workflow_status.json').read_bytes())
    assert status['status'] == 'needs_authorization'
    assert status['failed_node'] == 'str06.stage2.chapters'
    snapshot = json.loads((book / 'runtime/source_snapshot.json').read_bytes())
    assert [row['chapter_ordinal'] for row in snapshot['chapters']] == [1, 2]
    assert read_cost_snapshot(output, readonly=True)[0] == []
    assert source.read_bytes() == original.encode('utf8')
