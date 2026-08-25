#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import encode_artifact
from contracts.schema.api import StructureApprovalRequest
from contracts.invariants import STAGE3_MEASUREMENT_CONTRACT, STRUCTURE_METRIC_CONTRACT
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage3 import StyleGrounding, StyleProvenance
from contracts.schema.stage4 import PlanChapters, PlanProvenance
from novel.orchestrator import approvals
from novel.orchestrator import queue as Q
from novel.run import RunContext
from novel.store import ArtifactStore


SID = '123'
MAIN_REL = 'stage1/main_text.json'
HOOK_REL = 'stage1/hook_copy.json'
STYLE_REL = 'stage3/style_grounding.json'
PLAN_REL = 'stage4/plan_chapters.json'


def _disk_json(book: Path, rel: str, value: object) -> None:
    path = book / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(encode_artifact(rel, value), ensure_ascii=False),
        encoding='utf-8', newline='',
    )


def _style(ctx: RunContext) -> StyleGrounding:
    block = dict(
        chars=20, paragraphs=1, sentences=2, avg_paragraph_chars=20.0,
        paragraph_sep_chars=20.0, avg_sentence_chars=10.0,
        dialogue_ratio=None, dialogue_ratio_applicable=True,
        quote_styles=['「」'],
    )
    body = dict(
        block, chapter_chars={'min': 20, 'max': 20, 'avg': 20.0},
        per_chapter_avg_sentence={'min': 10.0, 'max': 10.0},
    )
    provenance = ctx.free_provenance(
        StyleProvenance,
        measurement_contract=STAGE3_MEASUREMENT_CONTRACT,
    )
    return StyleGrounding(
        measurable={'body': body, 'hook': block},
        _provenance=provenance,
    )


def _plan(ctx: RunContext, *, strategy: str = '按原章次序推进。') -> PlanChapters:
    goal = lambda intent: {
        'intent': intent,
        'fact_refs': [{
            'kind': 'chapter_boundary', 'ref': 'chapter:1', 'chapter': 1,
        }],
        'rationale': 'fixture audit rationale',
    }
    provenance = ctx.free_provenance(
        PlanProvenance,
        params={
            'compiler': 'atomic-scenes-v2', 'fact_atoms': 1,
            'source_stages': 1,
            'structure_metric_contract': STRUCTURE_METRIC_CONTRACT,
        },
        origin='human',
        blind_candidate_sha256=None,
        evidence_binding_sha256=None,
    )
    return PlanChapters(
        reconstruction_strategy=strategy,
        output_chapters=[{
            'output_ordinal': 1, 'from_source_ordinals': [1],
            'chapter_goal': goal('advance'),
            'open_hook_goal': goal('open'),
            'end_hook_goal': goal('close'),
            'structure_decision': {
                'mode': 'retain',
                'reason': '本夹具只核对当前审批主体。',
                'declared_change_kinds': [],
                'computed_change_kinds': [],
            },
            'weight': 'even',
            'semantic_contract': {
                'chapter_function': '核对当前计划',
                'information_delta': ['f01_001'],
                'character_state_delta': [], 'relationship_delta': [],
                'reader_knowledge_delta': [],
                'must_land_fact_refs': ['f01_001'], 'context_fact_refs': [],
                'causal_edges': [], 'entry_state_refs': [],
                'ending_state_refs': [], 'material_refs': [],
            },
            'chapter_rewrite_brief': '用一场核对完成本章。',
            'scenes': [{
                'slot': 's01', 'budget_chars': 20,
                'weight': 'mid', 'purpose': '核对当前计划',
                'role': 'setup', 'delivery': 'scene', 'transition': 'start',
                'land_fact_refs': ['f01_001'], 'context_fact_refs': [],
                'material_refs': [], 'turn_fact_ref': 'f01_001',
                'entry_state_refs': [], 'exit_state_refs': [],
                'participant_refs': [], 'stage_refs': ['b01_01'],
            }],
        }],
        _provenance=provenance,
    )


@pytest.fixture
def current_book(tmp_path: Path) -> tuple[Path, Path]:
    book = tmp_path / f'审批证明_{SID}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n她核对了当前计划。\n', encoding='utf-8', newline='')
    main = MainText.model_validate([
        {
            'chapter_ordinal': 1,
            'text': '她核对了当前计划。',
        },
        {
            'chapter_ordinal': 2,
            'text': '另一章保持不变。',
        },
    ])
    _disk_json(book, MAIN_REL, main.model_dump(mode='json'))
    hook = HookCopy(text='她必须在天亮前查明账册为什么被换过。')
    _disk_json(book, HOOK_REL, hook.model_dump(mode='json'))

    style_ctx = RunContext.open(
        tmp_path, SID, 'stage3.style', paid=False, write_ledger=False)
    style_store = ArtifactStore(style_ctx)
    style_store.read(MainText, MAIN_REL)
    style_store.read(HookCopy, HOOK_REL)
    style_store.write(STYLE_REL, _style(style_ctx))

    plan_ctx = RunContext.open(
        tmp_path, SID, 'stage4.plan', paid=True, write_ledger=False)
    plan_store = ArtifactStore(plan_ctx)
    plan_store.read(MainText, MAIN_REL)
    plan_store.write(PLAN_REL, _plan(plan_ctx))
    return tmp_path, book


@pytest.fixture
def isolated_stage34(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this focused fixture on the Stage5 spending surface.

    The complete s0-s2 typed gates have their own tests.  Here we remove only
    their ordering prerequisites so s3/s4 proofs still use the real schema,
    root and ArtifactStore freshness implementation.
    """
    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's3', ())
    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's4', ())
    monkeypatch.setattr(approvals.prov, 'code_version', lambda: 'fixture-readonly-code')
    monkeypatch.setattr(approvals, '_spawns', lambda _base, _sid: {})


def _approve_spending_surface(base: Path) -> None:
    approvals.approve_gate(base, SID, 's3')
    novel_dir = approvals.paths.resolve_novel_dir(base, SID)
    subject, chapters = approvals._current_structure_subject_and_chapters(novel_dir)
    approvals.approve_structure(
        base,
        SID,
        StructureApprovalRequest(
            actor='freshness-reviewer',
            reason='核对当前结构主体与精确章集',
            subject_sha256=subject,
            chapters=[{
                'output_ordinal': ordinal,
                'decision': 'accept',
                'reason': f'第 {ordinal} 章结构已逐项核对',
            } for ordinal in chapters],
        ),
    )
    approvals.begin_writing(base, SID)


def _queue_stage5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))


def test_fresh_proofs_authorize_canonical_whole_book_queue_plan(
    current_book, isolated_stage34, monkeypatch,
):
    base, _book = current_book
    _approve_spending_surface(base)

    raw = json.loads(approvals._path(base, SID).read_text(encoding='utf-8'))
    assert raw['v'] == 4
    assert raw['gate_proofs']['s3']['root_clean_sha1']
    assert raw['gate_proofs']['s4']['artifacts'][PLAN_REL]
    assert raw['writing_proof']['artifacts'] == {
        STYLE_REL: raw['gate_proofs']['s3']['artifacts'][STYLE_REL],
        PLAN_REL: raw['gate_proofs']['s4']['artifacts'][PLAN_REL],
    }
    assert approvals.has_record(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert blocked == [] and len(todo) == 1
    assert todo[0].flags == []
    assert 'mode' not in todo[0].model_dump()


@pytest.mark.parametrize('mutation', ['root', 'upstream', 'subject', 'invalid', 'corrupt'])
def test_stale_or_changed_subject_revokes_spending_and_queue_emits_no_paid_plan(
    current_book, isolated_stage34, monkeypatch, mutation,
):
    base, book = current_book
    _approve_spending_surface(base)

    if mutation == 'root':
        (book / 'stage0' / 'clean_text.txt').write_text(
            '另一版正文根。', encoding='utf-8', newline='')
    elif mutation == 'upstream':
        main = MainText.model_validate([
            {
                'chapter_ordinal': 1,
                'text': '上游正文已经变化。',
            },
            {
                'chapter_ordinal': 2,
                'text': '另一章保持不变。',
            },
        ])
        _disk_json(book, MAIN_REL, main.model_dump(mode='json'))
    elif mutation == 'subject':
        path = book / PLAN_REL
        data = json.loads(path.read_text(encoding='utf-8'))
        data['reconstruction_strategy'] = '当前计划字节已由人改动。'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8', newline='')
    elif mutation == 'invalid':
        path = book / 'stage4' / 'plan_chapters.INVALID.json'
        path.write_bytes((book / PLAN_REL).read_bytes())
    else:
        (book / PLAN_REL).write_text('{broken', encoding='utf-8', newline='')

    effective = approvals.get(base, SID)
    assert not effective.gate['s4']
    assert not effective.writing
    assert not approvals.has_record(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL
    assert blocked[0].steps == []


def test_writing_cannot_be_authorized_without_current_s3_s4_approval(
    current_book, isolated_stage34, monkeypatch,
):
    base, _book = current_book
    assert not approvals.has_record(base, SID)
    with pytest.raises(
        approvals.ApprovalRequired,
        match='stage3 mechanical baseline and stage4 plan',
    ):
        approvals.begin_writing(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and blocked[0].steps == []


def test_backfill_never_manufactures_typed_s4_and_invalid_revokes_explicit_decision(
    current_book, isolated_stage34, monkeypatch,
):
    base, book = current_book
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s3', 's4'))
    first = approvals.backfill_from_disk(base, SID)
    assert first.backfilled == ['s3']
    assert not first.approvals.gate['s4']
    novel_dir = approvals.paths.resolve_novel_dir(base, SID)
    subject, chapters = approvals._current_structure_subject_and_chapters(novel_dir)
    approvals.approve_structure(base, SID, StructureApprovalRequest(
        actor='backfill-reviewer', reason='旧记录不能代造结构裁决，现逐章补审',
        subject_sha256=subject,
        chapters=[{
            'output_ordinal': ordinal, 'decision': 'accept',
            'reason': f'第 {ordinal} 章当前结构已核对',
        } for ordinal in chapters],
    ))
    approvals.begin_writing(base, SID)
    assert approvals.has_record(base, SID)

    (book / 'stage4' / 'plan_chapters.INVALID.json').write_bytes(
        (book / PLAN_REL).read_bytes())
    assert not approvals.has_record(base, SID)
    second = approvals.backfill_from_disk(base, SID)
    assert second.backfilled == []
    assert not second.approvals.gate['s4']


def test_backfill_rejects_legacy_arm_layout_and_does_not_stamp_stage5(
    current_book, isolated_stage34, monkeypatch,
):
    base, book = current_book
    _approve_spending_surface(base)
    for mode in ('deep', 'shallow'):
        path = book / 'stage5' / mode / 'ch01.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
    (book / 'stage5' / 'draft.md').write_text('正文。', encoding='utf-8')
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s5',))

    result = approvals.backfill_from_disk(base, SID)
    assert result.backfilled == []
    assert not result.approvals.gate['s5']


def test_v1_or_corrupt_service_record_never_authorizes_queue(
    current_book, isolated_stage34, monkeypatch,
):
    base, _book = current_book
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'v': 1, 'sid': SID,
        'gate': {f's{i}': True for i in range(8)},
        'seen': {'read': True, 'global': True},
        'deep': [1], 'writing': True, 'spawns': {},
    }), encoding='utf-8')
    assert not approvals.has_record(base, SID)
    with pytest.raises(approvals.HumanEditProtected, match='unsupported v1'):
        approvals.get(base, SID)

    with pytest.raises(approvals.HumanEditProtected, match='explicitly backfilled as v2'):
        approvals.backfill_from_disk(base, SID)

    path.write_text('{broken', encoding='utf-8')
    assert not approvals.has_record(base, SID)


def _write_legacy_v2(base: Path, book: Path, *, deep: list[int]) -> None:
    gate = {f's{i}': False for i in range(8)}
    gate.update({'s3': True, 's4': True})
    gate_proofs = {f's{i}': None for i in range(8)}
    gate_proofs.update({
        's3': approvals._legacy_gate_proof(book, 's3').model_dump(mode='json'),
        's4': approvals._legacy_gate_proof(book, 's4').model_dump(mode='json'),
    })
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'v': 2, 'sid': SID, 'gate': gate,
        'seen': {'read': False, 'global': False},
        'deep': deep, 'writing': True,
        'gate_proofs': gate_proofs,
        'seen_proofs': {'read': None, 'global': None},
        'deep_proof': approvals._proof_for_legacy_depth(
            book, deep).model_dump(mode='json'),
        'writing_proof': approvals._proof_for_legacy_writing(
            book, deep).model_dump(mode='json'),
    }, ensure_ascii=False), encoding='utf-8')


def test_explicit_v2_backfill_never_resigns_s4_or_writing_even_when_all_deep(
    current_book, isolated_stage34, monkeypatch,
):
    base, book = current_book
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s3', 's4'))
    _write_legacy_v2(base, book, deep=[1])

    result = approvals.backfill_from_disk(base, SID)
    assert result.approvals.writing is False
    assert result.approvals.gate['s3'] and not result.approvals.gate['s4']
    assert not any(result.approvals.gate[f's{i}'] for i in range(5, 8))
    raw = json.loads(approvals._path(base, SID).read_text(encoding='utf-8'))
    assert raw['v'] == 4 and 'deep' not in raw and 'deep_proof' not in raw


def test_explicit_v2_backfill_also_clears_s4_when_any_chapter_was_shallow(
    current_book, isolated_stage34, monkeypatch,
):
    base, book = current_book
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s3', 's4'))
    _write_legacy_v2(base, book, deep=[])

    result = approvals.backfill_from_disk(base, SID)
    assert result.approvals.writing is False
    assert result.approvals.gate['s3'] and not result.approvals.gate['s4']
    assert not any(result.approvals.gate[f's{i}'] for i in range(5, 8))
