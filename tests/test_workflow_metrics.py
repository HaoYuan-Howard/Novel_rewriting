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
         'completion_tokens': 3, 'reasoning_tokens': 1,
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
        {'sid': SID, 'kind': 'candidate_patch_applied', 'node': 'stage2.global',
         'changed_path_count': 1},
        {'sid': SID, 'kind': 'evidence_locator_rewrite', 'node': 'stage2.global',
         'match_mode': 'exact'},
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
    # 缓存不进 realized；失败 wire 已经发生的 usage 不能被 ok 条件吞掉。
    assert led['token_verdict'] == 'ok'
    assert led['prompt_tokens'] == 107
    assert led['completion_tokens'] == 43
    assert led['reasoning_tokens_within_completion'] == 26
    assert led['total_tokens'] == 150, 'reasoning 已含在 completion，不能重复相加'
    assert led['per_outcome']['error']['prompt_tokens'] == 7
    assert led['per_node']['stage4.design']['wire_error'] == 1
    assert 'stage4.design:agent_timeout' in led['error_kinds']


def test_real_wire_with_null_or_missing_usage_is_explicitly_unver(
        base: Path) -> None:
    """未知 usage 仍保留已知小计，但不得用 ``or 0`` 伪装成闭合总量。"""
    run_id = 'usage-gap-run'
    rows = [
        {
            'sid': SID, 'run_id': run_id, 'node': 'stage2.global',
            'code': CODE_CLEAN, 'cached': False, 'outcome': 'ok',
            'prompt_tokens': 10, 'completion_tokens': 4, 'reasoning_tokens': 2,
            'model': 'm', 'reasoning_effort': 'high',
        },
        {
            'sid': SID, 'run_id': run_id, 'node': 'stage2.global',
            'code': CODE_CLEAN, 'cached': False, 'outcome': 'error',
            'error_kind': 'provider_error', 'prompt_tokens': 3,
            'completion_tokens': None,
            # reasoning_tokens 刻意缺键；缺失与 null 都不能解释成 0。
            'model': 'm', 'reasoning_effort': 'high',
        },
        {
            'sid': SID, 'run_id': run_id, 'node': 'stage2.global',
            'code': CODE_CLEAN, 'cached': True, 'outcome': 'ok',
            'prompt_tokens': 999, 'completion_tokens': 999, 'reasoning_tokens': 999,
            'model': 'm', 'reasoning_effort': 'high',
        },
        {
            # 无 run_id 的旧行只参加历史汇总，不猜入目标 run。
            'sid': SID, 'node': 'stage4.design', 'code': CODE_CLEAN,
            'cached': False, 'outcome': 'ok', 'prompt_tokens': 8,
            'completion_tokens': 2, 'reasoning_tokens': 1,
            'model': 'm', 'reasoning_effort': 'high',
        },
        {
            # cache 身份也未知的旧坏行只能单列，不能因 falsy 猜成 wire。
            'sid': SID, 'node': 'stage4.design', 'code': CODE_CLEAN,
            'outcome': 'ok', 'prompt_tokens': 10_000,
            'completion_tokens': 10_000, 'reasoning_tokens': 10_000,
            'model': 'm', 'reasoning_effort': 'high',
        },
    ]
    (base / '_service/ledger.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')

    led = W.ledger_metrics(base, SID, run_id=run_id)
    assert (led['lines'], led['wire_calls'], led['cache_hits']) == (3, 2, 1)
    assert (led['wire_ok'], led['wire_error']) == (1, 1)
    assert led['token_verdict'] == 'unver'
    assert led['token_unver_fields'] == {
        'completion_tokens': 1, 'reasoning_tokens': 1}
    assert led['prompt_tokens'] == 13
    assert led['completion_tokens'] is None
    assert led['reasoning_tokens_within_completion'] is None
    assert led['total_tokens'] is None
    assert led['known_completion_tokens'] == 4
    assert led['known_reasoning_tokens_within_completion'] == 2
    assert led['per_outcome']['error']['token_verdict'] == 'unver'
    assert led['per_outcome']['error']['known_prompt_tokens'] == 3

    aggregate = W.ledger_metrics(base, SID)
    assert aggregate['wire_calls'] == 3, '未指定 run 时仍兼容旧无 run_id 汇总'
    assert aggregate['cache_state_unknown'] == 1


def test_run_rows_map_epoch_producer_identity_to_explicit_parent() -> None:
    rows = [
        {'sid': SID, 'run_id': 'epoch-current',
         'parent_run_id': 'agent-parent', 'outcome': 'ok'},
        {'sid': SID, 'run_id': 'epoch-other',
         'parent_run_id': 'other-parent', 'outcome': 'error'},
        {'sid': SID, 'run_id': 'agent-parent', 'outcome': 'ok'},
    ]

    selected = W._run_rows(rows, SID, 'agent-parent')

    assert [row['run_id'] for row in selected] == [
        'epoch-current', 'agent-parent']


def test_stage2_narrow_repair_and_zero_wire_rewrite_are_visible(base: Path) -> None:
    audit = W.collect(base, SID)['audit']
    assert audit['candidate_patches_applied'] == 1
    assert audit['candidate_patches_by_kind'] == {'legacy_unspecified': 1}
    assert audit['candidate_patch_changed_paths'] == 1
    assert audit['candidate_patch_changed_fields'] == 0
    assert audit['candidate_patch_changes_by_kind'] == {
        'legacy_unspecified': {
            'events': 1, 'changed_paths': 1, 'changed_fields': 0,
        },
    }
    assert audit['evidence_locator_rewrites'] == 1
    assert audit['evidence_locator_rewrite_modes'] == {'exact': 1}


def test_cli_track_keeps_every_exit_code_and_never_folds_them_into_a_verdict(
        base: Path) -> None:
    cli = W.collect(base, SID)['cli']
    design = cli['per_node']['stage4.design']
    assert design['exit_codes'] == {'70': 1, '0': 1}, 'exit 0 ≠ 合格，两个码都要留着'
    assert design['paired_runs'] == 2
    assert design['total_seconds'] == 70
    assert design['closed_segment_seconds'] == 70
    assert design['first_start_to_final_terminal_seconds'] == 110
    assert cli['closed_segment_seconds'] == 70
    assert cli['first_start_to_final_terminal_seconds'] == 110
    assert cli['unfinished_starts'] == 0
    assert 'other-book' not in json.dumps(cli, ensure_ascii=False)


def test_cli_separates_closed_runtime_from_elapsed_waiting_gaps(base: Path) -> None:
    run_id = 'elapsed-run'
    old_run = 'old-elapsed-run'
    rows = [
        {'ts': 10, 'sid': SID, 'run_id': old_run, 'execution_id': 'old',
         'node': 'stage2.global', 'phase': 'start', 'exit_code': None, 'note': None},
        {'ts': 999, 'sid': SID, 'run_id': old_run, 'execution_id': 'old',
         'node': 'stage2.global', 'phase': 'end', 'exit_code': 70, 'note': None},
        {'ts': 100, 'sid': SID, 'run_id': run_id, 'execution_id': 'e1',
         'node': 'stage2.global', 'phase': 'start', 'exit_code': None, 'note': None},
        {'ts': 110, 'sid': SID, 'run_id': run_id, 'execution_id': 'e1',
         'node': 'stage2.global', 'phase': 'end', 'exit_code': 70, 'note': None},
        {'ts': 200, 'sid': SID, 'run_id': run_id, 'execution_id': 'e2',
         'node': 'stage2.global', 'phase': 'start', 'exit_code': None, 'note': None},
        {'ts': 205, 'sid': SID, 'run_id': run_id, 'execution_id': 'e2',
         'node': 'stage2.global', 'phase': 'end', 'exit_code': 0, 'note': None},
    ]
    (base / '_service/agent_progress.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')

    cli = W.cli_metrics(base, SID, run_id=run_id)
    node = cli['per_node']['stage2.global']
    assert cli['events'] == 4
    assert cli['closed_segments'] == 2
    assert cli['closed_segment_seconds'] == 15
    assert cli['first_start_to_final_terminal_seconds'] == 105
    assert cli['elapsed_verdict'] == 'ok'
    assert node['closed_segment_seconds'] == 15
    assert node['first_start_to_final_terminal_seconds'] == 105
    assert node['exit_codes'] == {'70': 1, '0': 1}


def test_run_scope_excludes_old_failures_and_keeps_snapshot_surfaces(
        base: Path) -> None:
    """同 sid 的旧 run 再糟，也不能污染本轮成本、闸、CLI 与代码身份。"""
    current_run = 'current-run'
    old_run = 'old-run'
    service = base / '_service'

    old_ledger = [
        {
            'sid': SID, 'run_id': old_run, 'node': 'stage2.global',
            'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': f'old-attempt-{index}', 'code': f'{CODE_CLEAN}-dirty',
            'cached': False, 'outcome': 'error', 'error_kind': 'agent_timeout',
            'prompt_tokens': 10_000, 'completion_tokens': 0,
            'model': 'old-model', 'reasoning_effort': 'high',
        }
        for index in range(20)
    ]
    current_ledger = {
        'sid': SID, 'run_id': current_run, 'node': 'stage2.global',
        'purpose': 'stage2.global', 'chapter': None,
        'attempt_id': 'current-attempt', 'code': CODE_CLEAN,
        'cached': False, 'outcome': 'ok', 'prompt_tokens': 101,
        'completion_tokens': 41, 'reasoning_tokens': 17,
        'model': 'current-model', 'reasoning_effort': 'high',
    }
    (service / 'ledger.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in [*old_ledger, current_ledger]) + '\n',
        encoding='utf-8')

    old_audit = [
        {
            'sid': SID, 'run_id': old_run, 'kind': 'gate_decision',
            'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': f'old-attempt-{index}', 'source': 'wire',
            'decision': 'rejected', 'gate': 'validator', 'resample': True,
        }
        for index in range(20)
    ]
    old_audit.append({
        'sid': SID, 'run_id': old_run, 'kind': 'candidate_patch_applied',
        'node': 'stage2.global', 'changed_path_count': 9,
    })
    current_audit = [
        {
            'sid': SID, 'run_id': current_run, 'kind': 'gate_decision',
            'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': 'current-attempt', 'source': 'wire',
            'decision': 'accepted', 'resample': False,
        },
        {
            'sid': SID, 'run_id': current_run, 'kind': 'artifact_adoption',
            'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': 'current-attempt',
        },
        {
            'sid': SID, 'run_id': current_run,
            'kind': 'candidate_patch_applied', 'node': 'stage2.global',
            'patch_kind': 'role_fate', 'changed_field_count': 2,
            'evidence_table_sha256': 'a' * 64,
        },
    ]
    (service / 'llm_audit.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in [*old_audit, *current_audit]) + '\n',
        encoding='utf-8')

    progress: list[dict[str, object]] = []
    for index in range(20):
        progress += [
            {
                'ts': index * 10, 'sid': SID, 'run_id': old_run,
                'execution_id': f'old-execution-{index}',
                'node': 'stage2.global', 'phase': 'start',
                'exit_code': None, 'note': None,
            },
            {
                'ts': index * 10 + 9, 'sid': SID, 'run_id': old_run,
                'execution_id': f'old-execution-{index}',
                'node': 'stage2.global', 'phase': 'end',
                'exit_code': 70, 'note': None,
            },
        ]
    progress += [
        {
            'ts': 1_000, 'sid': SID, 'run_id': current_run,
            'execution_id': 'current-execution', 'node': 'stage2.global',
            'phase': 'start', 'exit_code': None, 'note': None,
        },
        {
            'ts': 1_005, 'sid': SID, 'run_id': current_run,
            'execution_id': 'current-execution', 'node': 'stage2.global',
            'phase': 'end', 'exit_code': 0, 'note': None,
        },
    ]
    (service / 'agent_progress.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in progress) + '\n', encoding='utf-8')

    # current artifact 是快照，不携 run_id；它仍进 artifact/human 面，却不能
    # 反向冒充目标 run 的代码身份。
    grounding_path = base / f'{TITLE}_{SID}/stage2/global_grounding.json'
    grounding = json.loads(grounding_path.read_text(encoding='utf-8'))
    grounding['_provenance']['code'] = f'{CODE_CLEAN}-dirty'
    _write(grounding_path, grounding)

    report = W.collect(base, SID, run_id=current_run)
    assert report['run_id'] == current_run
    assert report['ledger']['lines'] == 1
    assert report['ledger']['wire_error'] == 0
    assert report['ledger']['prompt_tokens'] == 101
    assert report['audit']['gate_by_decision'] == {'accepted': 1}
    assert report['audit']['resamples'] == 0
    assert report['audit']['first_call_landing']['stage2.global']['first_call'] == 1
    assert report['audit']['candidate_patches_by_kind'] == {'role_fate': 1}
    assert report['audit']['candidate_patch_changed_paths'] == 0
    assert report['audit']['candidate_patch_changed_fields'] == 2
    assert report['audit']['candidate_patch_changes_by_kind'] == {
        'role_fate': {
            'events': 1, 'changed_paths': 0, 'changed_fields': 2,
        },
    }
    assert report['cli']['events'] == 2
    assert report['cli']['per_node']['stage2.global']['exit_codes'] == {'0': 1}
    assert report['cli']['per_node']['stage2.global']['total_seconds'] == 5
    assert report['identity']['code_identities'] == [CODE_CLEAN]
    assert report['identity']['experiment_valid'] is True
    assert report['artifacts']['artifacts'], '产物面仍是 current snapshot'
    assert report['human']['stage2_review_items'] == 2

    aggregate = W.collect(base, SID)['audit']
    assert aggregate['candidate_patches_by_kind'] == {
        'legacy_unspecified': 1, 'role_fate': 1,
    }
    assert aggregate['candidate_patch_changed_paths'] == 9
    assert aggregate['candidate_patch_changed_fields'] == 2


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


def test_stage2_run_metrics_bind_formal_artifacts_and_measure_real_overlap(
        base: Path) -> None:
    """章耗时认 ledger，节点墙钟认 beacon，预审重叠只取两者区间交集。"""
    run_id = 'stage2-acceptance-run'
    book = base / f'{TITLE}_{SID}'
    blueprint_path = book / 'stage2/chapter_blueprints.json'
    blueprint = json.loads(blueprint_path.read_text(encoding='utf-8'))
    blueprint['_provenance']['context'] = {
        'architecture': 'extraction-ir-v3.2'}
    _write(blueprint_path, blueprint)
    grounding_path = book / 'stage2/global_grounding.json'
    grounding = json.loads(grounding_path.read_text(encoding='utf-8'))

    rows = [
        {
            'at': '1970-01-01T00:01:55+00:00', 'sid': SID,
            'run_id': run_id, 'node': 'stage2.global', 'code': CODE_CLEAN,
            'cached': False, 'outcome': 'ok', 'prompt_tokens': 10,
            'completion_tokens': 5, 'reasoning_tokens': 2,
            'model': 'executor-m', 'reasoning_effort': 'high',
            'latency_ms': 10_000, 'chapter': None,
        },
        {
            'at': '1970-01-01T00:03:40+00:00', 'sid': SID,
            'run_id': run_id, 'node': 'stage2.blueprint', 'code': CODE_CLEAN,
            'cached': False, 'outcome': 'ok', 'prompt_tokens': 10,
            'completion_tokens': 5, 'reasoning_tokens': 2,
            'model': 'executor-m', 'reasoning_effort': 'high',
            'latency_ms': 100_000, 'chapter': 1,
        },
        {
            'at': '1970-01-01T00:05:20+00:00', 'sid': SID,
            'run_id': run_id, 'node': 'stage2.blueprint', 'code': CODE_CLEAN,
            'cached': False, 'outcome': 'ok', 'prompt_tokens': 10,
            'completion_tokens': 5, 'reasoning_tokens': 2,
            'model': 'executor-m', 'reasoning_effort': 'high',
            'latency_ms': 200_000, 'chapter': 2,
        },
        {
            'at': '1970-01-01T00:04:10+00:00', 'sid': SID,
            'run_id': run_id, 'role': 'commander', 'node': None,
            'code': CODE_CLEAN, 'cached': False, 'outcome': 'ok',
            'prompt_tokens': 10, 'completion_tokens': 5, 'reasoning_tokens': 2,
            'model': 'commander-m', 'request_model': 'commander-m',
            'reasoning_effort': 'high', 'latency_ms': 100_000,
            'activity': 'stage2.global.prereview',
        },
        {
            'at': '1970-01-01T00:05:30+00:00', 'sid': SID,
            'run_id': run_id, 'role': 'commander', 'node': None,
            'code': CODE_CLEAN, 'cached': False, 'outcome': 'ok',
            'prompt_tokens': 10, 'completion_tokens': 5, 'reasoning_tokens': 2,
            'model': 'commander-m', 'request_model': 'commander-m',
            'reasoning_effort': 'high', 'latency_ms': 10_000,
            'activity': 'stage2.blueprint.review',
        },
    ]
    (base / '_service/ledger.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')

    blueprint_fp = W.prov.projection_fingerprint(blueprint)
    grounding_fp = W.prov.projection_fingerprint(grounding)
    adoptions = [
        {'sid': SID, 'run_id': run_id, 'kind': 'artifact_adoption',
         'node': 'stage2.global', 'artifact': 'stage2/global_grounding.json',
         'artifact_fingerprint': grounding_fp, 'chapter': None,
         'attempt_id': 'global'},
        *[
            {'sid': SID, 'run_id': run_id, 'kind': 'artifact_adoption',
             'node': 'stage2.blueprint',
             'artifact': 'stage2/chapter_blueprints.json',
             'artifact_fingerprint': blueprint_fp, 'chapter': chapter,
             'attempt_id': f'chapter-{chapter}'}
            for chapter in (1, 2)
        ],
    ]
    (base / '_service/llm_audit.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in adoptions) + '\n', encoding='utf-8')
    progress = [
        {'ts': 100, 'sid': SID, 'run_id': run_id, 'execution_id': 'global',
         'node': 'stage2.global', 'phase': 'start', 'exit_code': None, 'note': None},
        {'ts': 120, 'sid': SID, 'run_id': run_id, 'execution_id': 'global',
         'node': 'stage2.global', 'phase': 'end', 'exit_code': 0, 'note': None},
        {'ts': 120, 'sid': SID, 'run_id': run_id, 'execution_id': 'blueprint',
         'node': 'stage2.blueprint', 'phase': 'start', 'exit_code': None, 'note': None},
        {'ts': 320, 'sid': SID, 'run_id': run_id, 'execution_id': 'blueprint',
         'node': 'stage2.blueprint', 'phase': 'end', 'exit_code': 0, 'note': None},
    ]
    (base / '_service/agent_progress.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in progress) + '\n', encoding='utf-8')

    collected = W.collect(base, SID, run_id=run_id)
    assert collected['schema'] == W.WORKFLOW_REPORT_SCHEMA
    stage2 = collected['stage2']

    assert stage2['architecture'] == 'extraction-ir-v3.2'
    assert stage2['cold_cache'] == {
        'state': 'ok', 'hits': 0, 'unknown_rows': 0}
    assert stage2['global_artifact']['run_binding']['state'] == 'ok'
    assert stage2['blueprint_artifact']['run_binding']['state'] == 'ok'
    assert stage2['blueprint_wire']['chapter_latency_seconds'] == {
        'chapters': 2, 'p50': 100.0, 'p95': 200.0, 'max': 200.0}
    assert stage2['commander']['calls'] == 2
    assert stage2['overlap'] == {
        'state': 'retired_observation', 'historical_state': 'ok',
        'prereview_blueprint_seconds': 100.0}
    assert stage2['role_lifecycle']['state'] == 'unver'
    assert stage2['wall_clock']['state'] == 'ok'
    assert stage2['wall_clock']['seconds'] == 230.0


def test_stage2_role_lifecycle_counts_separate_commanders_and_acceptance_order():
    units = [dict(node=node, role='commander', content_used=4,
                  content_limit=4, content_remaining=0)
             for node in ('stage2.global', 'stage2.blueprint')]
    reviews = {
        'stage2.global': {'state': 'accepted', 'current_decision': {
            'decided_at': '2026-09-08T00:00:10+00:00'}},
        'stage2.blueprint': {'state': 'accepted', 'current_decision': {
            'decided_at': '2026-09-08T00:00:50+00:00'}},
    }
    t = W._iso_epoch('2026-09-08T00:00:00+00:00')
    intervals = [('stage2.blueprint', t + 20, t + 40)]
    assert W._stage2_role_lifecycle(units, reviews, intervals)['state'] == 'ok'
    units[1]['content_used'] = 5
    assert W._stage2_role_lifecycle(units, reviews, intervals)['state'] == 'fail'
    units[1]['content_used'] = 4
    assert W._stage2_role_lifecycle(units, reviews, [('stage2.blueprint', t, t+40)])['sequence'] == 'fail'
    assert W._stage2_role_lifecycle(None, reviews, intervals)['state'] == 'unver'


def test_a_leaking_filename_fails_closed_instead_of_being_printed(base: Path) -> None:
    """人可以随手把书名写进决策集文件名——那一刻必须炸，而不是「大概不会吧」。"""
    book = base / f'{TITLE}_{SID}'
    leaked = book / f'stage2/global_review_decisions_{TITLE}.json'
    leaked.write_text(json.dumps({'_schema_version': 1, 'actor': 'x', 'decisions': []}),
                      encoding='utf-8')
    with pytest.raises(SystemExit) as excinfo:
        W.collect(base, SID)
    assert '书名' in str(excinfo.value)


def _write_metric_ledger(base: Path, rows: list[dict[str, object]]) -> None:
    common = {
        'sid': SID, 'code': CODE_CLEAN, 'model': 'm', 'outcome': 'ok',
        'reasoning_effort': 'high',
    }
    (base / '_service/ledger.jsonl').write_text(
        '\n'.join(json.dumps({**common, **row}) for row in rows) + '\n',
        encoding='utf-8')


def test_first_call_landing_keeps_retried_and_never_accepted_apart(
        base: Path, tmp_path: Path) -> None:
    """中间 accepted 不算落盘；只认同 run adopted attempt 的 wire 排位。"""
    audit = base / '_service/llm_audit.jsonl'
    audit.write_text('\n'.join(json.dumps(row) for row in (
        # 一次就过
        {'sid': SID, 'run_id': 'r1', 'kind': 'gate_decision',
         'node': 'stage5.write', 'purpose': 'stage5.write:ch1',
         'chapter': 1, 'attempt_id': 'a1', 'source': 'wire',
         'decision': 'accepted'},
        {'sid': SID, 'run_id': 'r1', 'kind': 'artifact_adoption',
         'node': 'stage5.write', 'purpose': 'stage5.write:ch1',
         'chapter': 1, 'attempt_id': 'a1'},
        # 先拒后过
        {'sid': SID, 'run_id': 'r1', 'kind': 'gate_decision',
         'node': 'stage5.write', 'purpose': 'stage5.write:ch2',
         'chapter': 2, 'attempt_id': 'a2', 'source': 'wire',
         'decision': 'rejected'},
        {'sid': SID, 'run_id': 'r1', 'kind': 'gate_decision',
         'node': 'stage5.write', 'purpose': 'stage5.write:ch2',
         'chapter': 2, 'attempt_id': 'a3', 'source': 'wire',
         'decision': 'accepted'},
        {'sid': SID, 'run_id': 'r1', 'kind': 'artifact_adoption',
         'node': 'stage5.write', 'purpose': 'stage5.write:ch2',
         'chapter': 2, 'attempt_id': 'a3'},
        # 一直没落盘
        {'sid': SID, 'run_id': 'r2', 'kind': 'gate_decision',
         'node': 'stage4.design', 'purpose': 'stage4.design',
         'chapter': None, 'attempt_id': 'a4', 'source': 'wire',
         'decision': 'rejected'},
        {'sid': SID, 'run_id': 'r2', 'kind': 'gate_decision',
         'node': 'stage4.design', 'purpose': 'stage4.design',
         'chapter': None, 'attempt_id': 'a5', 'source': 'wire',
         'decision': 'rejected'},
    )) + '\n', encoding='utf-8')
    _write_metric_ledger(base, [
        {'run_id': 'r1', 'node': 'stage5.write',
         'purpose': 'stage5.write:ch1', 'chapter': 1,
         'attempt_id': 'a1', 'cached': False},
        {'run_id': 'r1', 'node': 'stage5.write',
         'purpose': 'stage5.write:ch2', 'chapter': 2,
         'attempt_id': 'a2', 'cached': False, 'outcome': 'error'},
        {'run_id': 'r1', 'node': 'stage5.write',
         'purpose': 'stage5.write:ch2', 'chapter': 2,
         'attempt_id': 'a3', 'cached': False},
        {'run_id': 'r2', 'node': 'stage4.design',
         'purpose': 'stage4.design', 'chapter': None,
         'attempt_id': 'a4', 'cached': False, 'outcome': 'error'},
        {'run_id': 'r2', 'node': 'stage4.design',
         'purpose': 'stage4.design', 'chapter': None,
         'attempt_id': 'a5', 'cached': False, 'outcome': 'error'},
    ])

    landing = W.collect(base, SID)['audit']['first_call_landing']
    assert landing['stage5.write'] == {
        'units': 2, 'first_call': 1, 'retried': 1,
        'zero_wire_landed': 0, 'never_landed': 0, 'unknown': 0}
    assert landing['stage4.design'] == {
        'units': 1, 'first_call': 0, 'retried': 0,
        'zero_wire_landed': 0, 'never_landed': 1, 'unknown': 0}


def test_first_call_landing_dedupes_business_rejection_and_uses_patch_adoption(
        base: Path) -> None:
    audit = base / '_service/llm_audit.jsonl'
    common = {
        'sid': SID, 'run_id': 'patch-run', 'node': 'stage2.global',
        'purpose': 'stage2.global', 'chapter': None,
    }
    audit.write_text('\n'.join(json.dumps(row) for row in (
        {**common, 'kind': 'gate_decision', 'attempt_id': 'full-attempt',
         'source': 'wire', 'decision': 'accepted'},
        # 同一 wire 随后被代码业务闸拒绝，不能算成第二次调用。
        {**common, 'kind': 'gate_decision', 'attempt_id': 'full-attempt',
         'source': 'wire', 'decision': 'rejected', 'gate': 'validator'},
        {**common, 'kind': 'gate_decision', 'attempt_id': 'patch-attempt',
         'source': 'wire', 'decision': 'accepted'},
        {**common, 'kind': 'artifact_adoption', 'attempt_id': 'patch-attempt'},
    )) + '\n', encoding='utf-8')
    _write_metric_ledger(base, [
        {**common, 'attempt_id': 'full-attempt', 'cached': False},
        {**common, 'attempt_id': 'patch-attempt', 'cached': False},
    ])

    assert W.collect(base, SID)['audit']['first_call_landing'][
        'stage2.global'] == {
            'units': 1, 'first_call': 0, 'retried': 1,
            'zero_wire_landed': 0, 'never_landed': 0, 'unknown': 0,
        }


def test_first_call_landing_separates_runs_zero_wire_and_broken_links(
        base: Path) -> None:
    audit = base / '_service/llm_audit.jsonl'
    rows = [
        {'sid': SID, 'run_id': 'r1', 'kind': 'gate_decision',
         'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
         'attempt_id': 'wire-1', 'source': 'wire', 'decision': 'accepted'},
        {'sid': SID, 'run_id': 'r1', 'kind': 'artifact_adoption',
         'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
         'attempt_id': 'wire-1'},
        {'sid': SID, 'run_id': 'r2', 'kind': 'gate_decision',
         'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
         # cache 合法复用旧 run 的原始 attempt id，不能跨 run 判成冲突。
         'attempt_id': 'wire-1', 'source': 'cache', 'decision': 'accepted'},
        {'sid': SID, 'run_id': 'r2', 'kind': 'artifact_adoption',
         'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
         'attempt_id': 'wire-1'},
        # adoption 指不到本 run 的任何 gate，必须 unknown。
        {'sid': SID, 'run_id': 'r3', 'kind': 'artifact_adoption',
         'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
         'attempt_id': 'orphan-3'},
    ]
    audit.write_text('\n'.join(json.dumps(row) for row in rows) + '\n',
                     encoding='utf-8')
    _write_metric_ledger(base, [
        {'run_id': 'r1', 'node': 'stage2.global',
         'purpose': 'stage2.global', 'chapter': None,
         'attempt_id': 'wire-1', 'cached': False},
        {'run_id': 'r2', 'node': 'stage2.global',
         'purpose': 'stage2.global', 'chapter': None,
         'attempt_id': 'wire-1', 'cached': True},
    ])

    assert W.collect(base, SID)['audit']['first_call_landing'][
        'stage2.global'] == {
            'units': 3, 'first_call': 1, 'retried': 0,
            'zero_wire_landed': 1, 'never_landed': 0, 'unknown': 1,
        }


def test_first_call_landing_counts_transport_failure_before_adopted_attempt(
        base: Path) -> None:
    common = {
        'sid': SID, 'run_id': 'transport-run', 'node': 'stage2.global',
        'purpose': 'stage2.global', 'chapter': None,
    }
    _write_metric_ledger(base, [
        {**common, 'attempt_id': 'timeout-attempt', 'cached': False,
         'outcome': 'error', 'error_kind': 'agent_timeout'},
        {**common, 'attempt_id': 'accepted-attempt', 'cached': False},
    ])
    (base / '_service/llm_audit.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in (
            {**common, 'kind': 'gate_decision',
             'attempt_id': 'accepted-attempt', 'source': 'wire',
             'decision': 'accepted'},
            {**common, 'kind': 'artifact_adoption',
             'attempt_id': 'accepted-attempt'},
        )) + '\n', encoding='utf-8')

    assert W.collect(base, SID)['audit']['first_call_landing'][
        'stage2.global'] == {
            'units': 1, 'first_call': 0, 'retried': 1,
            'zero_wire_landed': 0, 'never_landed': 0, 'unknown': 0,
        }


def test_first_call_landing_refuses_an_adopted_transport_error(base: Path) -> None:
    common = {
        'sid': SID, 'run_id': 'broken-adoption', 'node': 'stage2.global',
        'purpose': 'stage2.global', 'chapter': None,
    }
    _write_metric_ledger(base, [
        {**common, 'attempt_id': 'failed-attempt', 'cached': False,
         'outcome': 'error', 'error_kind': 'agent_protocol'},
    ])
    (base / '_service/llm_audit.jsonl').write_text(
        json.dumps({**common, 'kind': 'artifact_adoption',
                    'attempt_id': 'failed-attempt'}) + '\n',
        encoding='utf-8')

    assert W.collect(base, SID)['audit']['first_call_landing'][
        'stage2.global'] == {
            'units': 1, 'first_call': 0, 'retried': 0,
            'zero_wire_landed': 0, 'never_landed': 0, 'unknown': 1,
        }
