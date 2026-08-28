#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tools/workflow_metrics.py` 的守卫。

【它守什么】读数器最容易坏的三处，每处一条正反：
  ① **扫描面**：只按注册表数会漏掉人产的 typed 决策集（判据 3）；
  ② **只读**：读数器一旦顺手写点什么，就成了第二个写盘的东西（`R-11.3` 同形）；
  ③ **输出面**：书名/绝对路径泄出去收不回来，所以那条是闸不是提示。

⚠ 结构那一节**不在这里重算一份读数**——它必须与生产 `structure_metrics_of`
逐格相同，因此断言直接调生产落点比对（判据 21：守卫要调用生产那一个落点）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.stages.stage4.measure import structure_metrics_of
from tools import workflow_metrics as W

SID = '2066575217393144601'
TITLE = '读数样书'
CODE_CLEAN = 'a' * 40 + '.1234567890abcdef'


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')


def _blueprint(ordinal: int) -> dict:
    return {
        'chapter_ordinal': ordinal,
        'fact_atoms': [
            {'fact_id': f'f{ordinal:02d}_01', 'obligation': 'land',
             'kind': 'information', 'claim': '这一句足够长，用来验证长串重复率的分母'},
            {'fact_id': f'f{ordinal:02d}_02', 'obligation': 'land',
             'kind': 'relationship', 'claim': '第二条同样足够长的事实主张文本'},
        ],
        'beats': [], 'causal_edges': [],
        'entry_state_refs': [], 'ending_state_refs': [],
    }


def _plan_chapter(ordinal: int) -> dict:
    return {
        'output_ordinal': ordinal,
        'scenes': [
            {'slot': 's01', 'land_fact_refs': [f'f{ordinal:02d}_01'],
             'purpose': '这一句足够长，用来验证长串重复率的分母'},
            {'slot': 's02', 'land_fact_refs': [f'f{ordinal:02d}_02'],
             'purpose': '这一句足够长，用来验证长串重复率的分母'},
        ],
    }


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    root = tmp_path / 'base'
    book = root / f'{TITLE}_{SID}'
    _write(book / 'stage2/global_grounding.json',
           {'_schema_version': 4, '_provenance': {'code': CODE_CLEAN}, 'canon': {}})
    _write(book / 'stage2/global_review.json', {
        '_schema_version': 1, 'source_sha256': '0' * 64,
        'items': [
            {'item_id': 'role:a:0', 'kind': 'role', 'subject_path': 'a',
             'subject_sha256': '0' * 64, 'state': 'PASS', 'evidence': []},
            {'item_id': 'fate:b:0', 'kind': 'fate', 'subject_path': 'b',
             'subject_sha256': '1' * 64, 'state': 'pending', 'evidence': []},
        ],
    })
    # 人产的 typed 决策集：**不在注册表里**，但真的占字节、真的被人读。
    _write(book / 'stage2/global_review_decisions_a.json',
           {'_schema_version': 1, 'actor': 'tester',
            'decisions': [{'item_id': 'role:a:0', 'state': 'PASS'}]})
    _write(book / 'stage2/chapter_blueprints.json', {
        '_schema_version': 6, '_provenance': {'code': CODE_CLEAN},
        'blueprints': [_blueprint(1), _blueprint(2)],
    })
    _write(book / 'stage4/blind_candidate.json', {
        '_schema_version': 2, '_provenance': {'code': CODE_CLEAN},
        'output_chapters': [
            {'output_ordinal': 1,
             'structure_decision': {'mode': 'retain', 'declared_change_kinds': []}},
            {'output_ordinal': 2,
             'structure_decision': {'mode': 'reconstruct',
                                    'declared_change_kinds': ['fact_grouping']}},
        ],
    })
    _write(book / 'stage4/plan_chapters.json', {
        '_schema_version': 4, '_provenance': {'code': CODE_CLEAN},
        'output_chapters': [_plan_chapter(1), _plan_chapter(2)],
    })
    (book / 'stage7').mkdir(parents=True, exist_ok=True)
    (book / f'stage7/{TITLE}_rewriting.txt').write_text('正文', encoding='utf-8')

    service = root / '_service'
    service.mkdir(parents=True, exist_ok=True)
    (service / 'ledger.jsonl').write_text('\n'.join(json.dumps(row) for row in (
        {'sid': SID, 'node': 'stage2.global', 'code': CODE_CLEAN, 'cached': False,
         'outcome': 'ok', 'prompt_tokens': 100, 'completion_tokens': 40,
         'reasoning_tokens': 25, 'model': 'm', 'reasoning_effort': 'high'},
        {'sid': SID, 'node': 'stage4.design', 'code': CODE_CLEAN, 'cached': False,
         'outcome': 'error', 'error_kind': 'agent_timeout', 'prompt_tokens': 7,
         'model': 'm', 'reasoning_effort': 'high'},
        {'sid': SID, 'node': 'stage4.design', 'code': CODE_CLEAN, 'cached': True,
         'outcome': 'ok', 'prompt_tokens': 999, 'completion_tokens': 999,
         'model': 'm', 'reasoning_effort': 'high'},
        {'sid': 'other-book', 'node': 'stage2.global', 'code': 'zzz', 'cached': False,
         'outcome': 'ok', 'prompt_tokens': 10 ** 6, 'model': 'm'},
    )) + '\n', encoding='utf-8')
    (service / 'llm_audit.jsonl').write_text('\n'.join(json.dumps(row) for row in (
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage2.global',
         'decision': 'accepted', 'gate': None, 'resample': False, 'source': 'wire'},
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage4.design',
         'decision': 'rejected', 'gate': 'validator', 'resample': True,
         'source': 'wire'},
        {'sid': SID, 'kind': 'artifact_adoption', 'node': 'stage4.design'},
    )) + '\n', encoding='utf-8')
    (service / 'agent_progress.jsonl').write_text('\n'.join(json.dumps(row) for row in (
        {'ts': 100, 'sid': SID, 'node': 'stage4.design', 'phase': 'start',
         'exit_code': None, 'note': None},
        {'ts': 160, 'sid': SID, 'node': 'stage4.design', 'phase': 'end',
         'exit_code': 70, 'note': None},
        {'ts': 200, 'sid': SID, 'node': 'stage4.design', 'phase': 'start',
         'exit_code': None, 'note': None},
        {'ts': 210, 'sid': SID, 'node': 'stage4.design', 'phase': 'end',
         'exit_code': 0, 'note': None},
        {'ts': 300, 'sid': 'other-book', 'node': 'stage4.design', 'phase': 'start',
         'exit_code': None, 'note': None},
    )) + '\n', encoding='utf-8')
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob('*')) if path.is_file()
    }


def test_unregistered_human_decision_sets_are_counted_too(base: Path) -> None:
    """只按注册表数会把人产 typed 决策集整类漏掉——那正是 v8 少了 195 KB 的形状。"""
    report = W.collect(base, SID)
    rels = {row['rel'] for row in report['artifacts']['artifacts']}
    assert 'stage2/global_review_decisions_a.json' in rels
    unregistered = [row for row in report['artifacts']['artifacts']
                    if row['version_state'] == 'unregistered']
    assert [row['rel'] for row in unregistered] == [
        'stage2/global_review_decisions_a.json']
    # 未登记的那一份没有注册表版本可比 ⇒ 不许伪装成 current。
    assert unregistered[0]['current_version'] is None
    stage2 = report['artifacts']['per_stage']['stage2']
    on_disk = sum(path.stat().st_size
                  for path in sorted((base / f'{TITLE}_{SID}' / 'stage2').iterdir()))
    assert stage2['raw_bytes'] == on_disk


def test_ledger_reads_only_this_sid_and_never_adds_reasoning_to_completion(
        base: Path) -> None:
    led = W.collect(base, SID)['ledger']
    assert led['lines'] == 3, '另一本书的行不许混进来'
    assert (led['wire_calls'], led['wire_ok'], led['wire_error']) == (2, 1, 1)
    assert led['cache_hits'] == 1, '缓存命中单独记账，不写成「没有执行过」'
    # 缓存那一行的 token 不进 ok 合计；reasoning 是 completion 的子口径。
    assert led['prompt_tokens'] == 100
    assert led['completion_tokens'] == 40
    assert led['reasoning_tokens_within_completion'] == 25
    assert 'stage4.design:agent_timeout' in led['error_kinds']


def test_cli_track_keeps_every_exit_code_and_never_folds_them_into_a_verdict(
        base: Path) -> None:
    cli = W.collect(base, SID)['cli']
    design = cli['per_node']['stage4.design']
    assert design['exit_codes'] == {'70': 1, '0': 1}, 'exit 0 ≠ 合格，两个码都要留着'
    assert design['paired_runs'] == 2
    assert design['total_seconds'] == 70
    assert cli['unfinished_starts'] == 0
    assert 'other-book' not in json.dumps(cli, ensure_ascii=False)


def test_human_surface_keeps_items_and_decisions_apart(base: Path) -> None:
    human = W.collect(base, SID)['human']
    assert human['stage2_review_items'] == 2
    assert human['stage2_items_by_state'] == {'PASS': 1, 'pending': 1}
    # 「多少项」与「人要点开多少张卡」是两个读数；分级只从生产落点取。
    assert human['stage2_items_by_effect'] == {
        'blocking': 1, 'disclosure': 1, 'unregistered': 0}
    assert human['stage2_cards_by_effect']['blocking'] == 1
    assert human['stage2_decision_files'] == {'global_review_decisions_a.json': 1}
    assert human['stage4_structure_decisions'] == 2
    assert human['stage4_structure_modes'] == {'reconstruct': 1, 'retain': 1}


def test_structure_section_is_exactly_what_production_returns(base: Path) -> None:
    """守卫调生产那一个落点，不重拼一份读数（判据 21）。"""
    report = W.collect(base, SID)
    book = base / f'{TITLE}_{SID}'
    blueprints = json.loads((book / 'stage2/chapter_blueprints.json').read_text('utf-8'))
    plan = json.loads((book / 'stage4/plan_chapters.json').read_text('utf-8'))
    bp_of = {int(row['chapter_ordinal']): row for row in blueprints['blueprints']}
    expected = [
        structure_metrics_of(bp_of[int(chapter['output_ordinal'])], chapter)
        for chapter in plan['output_chapters']
    ]
    got = report['structure']['rows']
    assert [row['state'] for row in got] == [row['state'] for row in expected]
    assert [row['chapter_ordinal'] for row in got] == \
        [row['chapter_ordinal'] for row in expected]
    for actual, wanted in zip(got, expected):
        for section, state in actual['section_states'].items():
            assert state == (wanted[section] or {}).get('state')


def test_experiment_valid_is_false_whenever_the_bytes_are_not_reproducible(
        base: Path) -> None:
    assert W.collect(base, SID)['identity']['experiment_valid'] is True

    ledger = base / '_service/ledger.jsonl'
    rows = [json.loads(line) for line in ledger.read_text('utf-8').splitlines()]
    rows[0]['code'] = f'{CODE_CLEAN}-dirty'
    ledger.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')

    identity = W.collect(base, SID)['identity']
    assert identity['experiment_valid'] is False
    assert len(identity['experiment_invalid_reasons']) == 2, '脏树与身份不唯一各算一条'


def test_collect_writes_nothing_anywhere_under_the_base(base: Path) -> None:
    before = _snapshot(base)
    W.render(W.collect(base, SID))
    assert _snapshot(base) == before


def test_json_output_is_refused_inside_the_base(base: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        W.main(['--base', str(base), '--sid', SID,
                '--json', str(base / '_service/metrics.json')])
    outside = tmp_path / 'report.json'
    assert W.main(['--base', str(base), '--sid', SID, '--json', str(outside)]) == 0
    assert outside.is_file()


def test_output_never_carries_the_book_identity(base: Path) -> None:
    report = W.collect(base, SID)
    blob = json.dumps(report, ensure_ascii=False) + W.render(report)
    assert TITLE not in blob
    assert str(base.resolve()) not in blob
    assert '正文' not in blob


def test_a_leaking_filename_fails_closed_instead_of_being_printed(base: Path) -> None:
    """人可以随手把书名写进决策集文件名——那一刻必须炸，而不是「大概不会吧」。"""
    book = base / f'{TITLE}_{SID}'
    leaked = book / f'stage2/global_review_decisions_{TITLE}.json'
    leaked.write_text(json.dumps({'_schema_version': 1, 'actor': 'x', 'decisions': []}),
                      encoding='utf-8')
    with pytest.raises(SystemExit) as excinfo:
        W.collect(base, SID)
    assert '书名' in str(excinfo.value)


def test_first_call_landing_keeps_retried_and_never_accepted_apart(
        base: Path, tmp_path: Path) -> None:
    """「重试后成功」与「一次都没被接受」是完全不同的处境，不许合成一个比例。"""
    audit = base / '_service/llm_audit.jsonl'
    audit.write_text('\n'.join(json.dumps(row) for row in (
        # 一次就过
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage5.write',
         'chapter': 1, 'decision': 'accepted'},
        # 先拒后过
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage5.write',
         'chapter': 2, 'decision': 'rejected'},
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage5.write',
         'chapter': 2, 'decision': 'accepted'},
        # 一直没过
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage4.design',
         'chapter': None, 'decision': 'rejected'},
        {'sid': SID, 'kind': 'gate_decision', 'node': 'stage4.design',
         'chapter': None, 'decision': 'rejected'},
    )) + '\n', encoding='utf-8')

    landing = W.collect(base, SID)['audit']['first_call_landing']
    assert landing['stage5.write'] == {
        'units': 2, 'first_call': 1, 'retried': 1, 'never_accepted': 0}
    assert landing['stage4.design'] == {
        'units': 1, 'first_call': 0, 'retried': 0, 'never_accepted': 1}
