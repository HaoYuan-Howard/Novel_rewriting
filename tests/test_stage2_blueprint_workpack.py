#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Blueprint 只读、异常优先审阅包。"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema.api import BlueprintReviewWorkpack, BlueprintSeenRequest
from contracts.schema.common import Finding
from contracts.schema.report import (
    AccountingLine, CheckReport, ProvenanceSection, RuleLine, StatusCounts,
    badge_of,
)
from novel.errors import ApprovalRequired, ArtifactUnreadable
from novel.orchestrator import approvals
from novel.prov import FreshnessReport
from novel.report.builder import build
from novel.stages.stage2 import checks, extract
from novel.views import stage2 as view
from tests.test_stage2 import blueprints as production_blueprints
from tests.test_stage2 import global_grounding as production_global_grounding
from tests.test_stage2 import main_text as production_main_text


def _finding(
    rule: str, sev: str, *, ordinal: int | None, evidence: list[str],
) -> Finding:
    return Finding(
        output_ordinal=ordinal,
        rule=rule,
        sev=sev,
        item=f'{rule} item',
        detail=f'{rule} detail',
        evidence=evidence,
        evidence_key='|'.join(evidence),
        owner='stage2' if sev in ('hard', 'info') else None,
        actionable=False,
        identity_hit=False,
        converge='new',
        unresolved_rounds=0,
    )


def _report() -> CheckReport:
    hit = RuleLine(
        rule='beats_coarse', sev='info', status='hit', item='粗拍',
        requirement='R-3.9', scanned='2 章 / 3 拍',
        findings=[_finding('beats_coarse', 'info', ordinal=2, evidence=['ch02'])],
    )
    unver = RuleLine(
        rule='role_fate_unverifiable', sev='unver', status='unver',
        item='语义无法机械验证', requirement='R-2.6',
        why='需人读', scanned='2 章',
    )
    metric = RuleLine(
        rule='span_coverage_per_chapter', sev='metric', status='ok',
        item='逐章覆盖率', requirement='R-3.2', scanned='2 章',
        remediation='none',
        findings=[_finding(
            'span_coverage_per_chapter', 'metric', ordinal=None,
            evidence=['ch1=60%', 'ch2=100%'])],
    )
    ok = RuleLine(
        rule='beat_id_unique', sev='hard', status='ok', item='beat id 唯一',
        requirement='R-3.1', scanned='3 拍',
    )
    rules = [ok, hit, unver, metric]
    return CheckReport(
        sid='1234567890123456789', node='stage2.blueprint', stage='stage2',
        provenance=ProvenanceSection(
            freshness='fresh', reason='match', ghosts=[], edges=[], invalid=False),
        rules=rules,
        accounting=[AccountingLine(
            key='fact_atom_failures', item='事实证据回取失败',
            recorded=True, count=0, detail=[])],
        badge=badge_of(rules),
        passed=True,
        counts=StatusCounts.of(rules),
    )


def _source(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, start=0, end=len(text))


def _fact(
    fact_id: str, claim: str, obligation: str, beat_id: str,
    *, participant: str = 'char_001',
) -> SimpleNamespace:
    return SimpleNamespace(
        fact_id=fact_id,
        claim=claim,
        kind='character_state' if obligation == 'context' else 'event',
        obligation=obligation,
        source_beat_ref=beat_id,
        source=_source(claim),
        participant_char_ids=[participant],
    )


def _beat(
    beat_id: str, start: int, end: int, *, hinge: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        beat_id=beat_id,
        span=SimpleNamespace(start=start, end=end, text='unused'),
        time_layer='present',
        place='城门',
        present_char_ids=['char_001'],
        hinges=[SimpleNamespace(
            hinge_id='h' + beat_id[1:].replace('_', '') + '0',
            kind='因果前提', source=_source(hinge))],
    )


def _book_subjects():
    prefix = '甲已在门外。'
    suffix = '甲随后进城。'
    text1 = prefix + ('过渡' * 20) + suffix
    text2 = '乙关上了门。'
    b11 = _beat('b01_01', 0, len(prefix), hinge='门外')
    b12 = _beat(
        'b01_02', len(text1) - len(suffix), len(text1), hinge='进城')
    f11 = _fact('f01_001', '甲已在门外', 'context', b11.beat_id)
    f12 = _fact('f01_002', '甲进城', 'land', b12.beat_id)
    bp1 = SimpleNamespace(
        chapter_ordinal=1,
        chapter_function='建立进城前提',
        beats=[b11, b12],
        fact_atoms=[f11, f12],
        causal_edges=[SimpleNamespace(before=f11.fact_id, after=f12.fact_id)],
        narrative_relations=[
            SimpleNamespace(
                kind='story_before', enforcement='hard',
                before_fact_ref=f11.fact_id, after_fact_ref=f12.fact_id,
            ),
            SimpleNamespace(
                kind='response_to', enforcement='review',
                stimulus_fact_ref=f11.fact_id, response_fact_ref=f12.fact_id,
            ),
            SimpleNamespace(
                kind='same_scene', enforcement='hard',
                left_fact_ref=f11.fact_id, right_fact_ref=f12.fact_id,
            ),
            SimpleNamespace(
                kind='observes', enforcement='review',
                observer_char_id='char_001',
                observed_fact_ref=f11.fact_id,
                observation_fact_ref=f12.fact_id,
            ),
        ],
        entry_state_refs=[f11.fact_id],
        ending_state_refs=[f12.fact_id],
        narration=[SimpleNamespace(
            grammatical_person='third', narrator_char_id=None,
            focalizer_char_id='char_001', pronoun_surfaces=[])],
    )

    b21 = _beat('b02_01', 0, len(text2), hinge='关上')
    f21 = _fact('f02_001', '乙关门', 'land', b21.beat_id,
                participant='char_002')
    bp2 = SimpleNamespace(
        chapter_ordinal=2,
        chapter_function='封闭退路',
        beats=[b21], fact_atoms=[f21], causal_edges=[],
        narrative_relations=[],
        entry_state_refs=[], ending_state_refs=[f21.fact_id],
        narration=[SimpleNamespace(
            grammatical_person='third', narrator_char_id=None,
            focalizer_char_id='char_002', pronoun_surfaces=[])],
    )
    main = SimpleNamespace(root=[
        SimpleNamespace(chapter_ordinal=1, text=text1),
        SimpleNamespace(chapter_ordinal=2, text=text2),
    ])
    blueprints = _DumpNamespace(blueprints=[bp1, bp2])
    return main, blueprints, {1: text1, 2: text2}


def _namespace(value):
    """把生产判据使用的 dict fixture 投影成 workpack builder 的只读模型面。"""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def _plain(value):
    """把测试只读 namespace 投影成生产 ``model_dump`` 的 JSON 形状。"""
    if isinstance(value, SimpleNamespace):
        return {key: _plain(item) for key, item in vars(value).items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


class _DumpNamespace(SimpleNamespace):
    """只补替身真实缺少的 Pydantic 读面，不重拼生产身份算法。"""

    def model_dump(self, *, exclude=None, **_kwargs):
        body = _plain(self)
        for key in exclude or ():
            body.pop(key, None)
        return body


class _Store:
    def __init__(self, main, blueprints) -> None:
        self.ctx = SimpleNamespace(
            sid='1234567890123456789', code='test-code', readonly=True,
            node='stage2.blueprint')
        self.rules_fingerprint = 'rules-fp'
        self.main = main
        self.blueprints = blueprints
        self.main_sha = 'a' * 64
        self.blueprint_sha = 'b' * 64
        self.blueprint_sha_reads: list[str] = []

    def exists(self, rel: str) -> bool:
        return rel in {STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS}

    def read_own_with_sha256(self, _model, rel: str, *, require_current: bool):
        assert require_current is True
        if rel == STAGE1_MAIN_TEXT:
            return self.main, self.main_sha
        if rel == STAGE2_CHAPTER_BLUEPRINTS:
            digest = (self.blueprint_sha_reads.pop(0)
                      if self.blueprint_sha_reads else self.blueprint_sha)
            return self.blueprints, digest
        raise AssertionError(f'未预期读取 {rel}')


def _install(monkeypatch, store: _Store, report: CheckReport):
    calls: list[object] = []
    monkeypatch.setattr(
        view.paths, 'resolve_novel_dir',
        lambda _base, _sid: Path('平台_测试书_1234567890123456789'))
    monkeypatch.setattr(view.paths, 'title_of', lambda _novel_dir: '测试书')
    monkeypatch.setattr(view.prov, 'code_version', lambda: store.ctx.code)
    monkeypatch.setattr(
        view.prov, 'rules_fingerprint', lambda _stage: store.rules_fingerprint)
    monkeypatch.setattr(view, 'readonly_ctx', lambda *_args: store.ctx)
    monkeypatch.setattr(view, 'ArtifactStore', lambda _ctx: store)

    def check(ctx):
        calls.append(ctx)
        return report

    monkeypatch.setattr(extract, 'run_check_only', check)
    return calls


def test_workpack_is_complete_exception_first_and_semantic_not_id_first(
    monkeypatch,
) -> None:
    main, blueprints, texts = _book_subjects()
    store = _Store(main, blueprints)
    report = _report()
    calls = _install(monkeypatch, store, report)

    pack = view.blueprint_review_workpack('ignored', store.ctx.sid)

    assert len(calls) == 2, '首尾重算生产 CheckReport 才能拒绝混合快照'
    assert all(ctx.readonly for ctx in calls), '工作包必须只读，不能借复检写盘'
    assert pack.read_only is True and pack.auto_decisions is False
    assert pack.review_contract_version == 'stage2-blueprint-review-v4'
    assert pack.subject_sha256 == view._canonical_sha256(
        blueprints.model_dump(exclude={'provenance'}))
    assert pack.review_token == 'bpr3_' + pack.binding_sha256
    assert len(pack.review_identity_sha256) == 64
    assert len(pack.validation_seal_sha256) == 64
    assert pack.binding.main_text_artifact_sha256 == store.main_sha
    assert pack.binding.blueprint_artifact_sha256 == store.blueprint_sha
    assert pack.binding.check_report_sha256 == view._canonical_sha256(
        report.model_dump(by_alias=True, mode='json'))
    assert 'code_version' not in pack.binding.model_dump(mode='json')
    assert 'rules_fingerprint' not in pack.binding.model_dump(mode='json')

    # ch2 有 typed output_ordinal 命中，所以它排在 ch1 前；两章仍一章不少。
    assert [row.chapter_ordinal for row in pack.chapters] == [2, 1]
    assert pack.chapters[0].attention_rules == ['beats_coarse']
    assert set(pack.binding.chapter_ordinals) == {1, 2}
    for chapter in pack.chapters:
        assert ''.join(row.text for row in chapter.coverage.segments) \
            == texts[chapter.chapter_ordinal]
        assert (chapter.coverage.covered_chars + chapter.coverage.loose_chars
                == chapter.coverage.text_chars)
    chapter1 = next(row for row in pack.chapters if row.chapter_ordinal == 1)
    assert [row.model_dump(mode='json') for row in chapter1.narrative_relations] == [
        {
            'kind': 'story_before', 'enforcement': 'hard',
            'before_claim': '甲已在门外', 'after_claim': '甲进城',
        },
        {
            'kind': 'response_to', 'enforcement': 'review',
            'stimulus_claim': '甲已在门外', 'response_claim': '甲进城',
        },
        {
            'kind': 'same_scene', 'enforcement': 'hard',
            'left_claim': '甲已在门外', 'right_claim': '甲进城',
        },
        {
            'kind': 'observes', 'enforcement': 'review',
            'observer_char_id': 'char_001',
            'observed_claim': '甲已在门外', 'observation_claim': '甲进城',
        },
    ]
    assert 'f01_001' not in chapter1.model_dump_json(), \
        '人读关系必须直接展开 claim，不能让审阅者回查机械 fact id'

    # 非 ok 与 unver 原行全留，正常 metric 原行全留，分母/四态不丢。
    assert [row.rule for row in pack.check.attention] == [
        'beats_coarse', 'role_fate_unverifiable']
    assert [row.rule for row in pack.check.metrics] == [
        'span_coverage_per_chapter']
    assert pack.check.rules_scanned == len(report.rules)
    assert pack.check.counts == report.counts
    assert pack.check.attention[0].scanned == '2 章 / 3 拍'
    assert pack.accounting == report.accounting

    chapter1 = next(row for row in pack.chapters if row.chapter_ordinal == 1)
    assert chapter1.causal_links[0].before_claim == '甲已在门外'
    assert chapter1.causal_links[0].after_claim == '甲进城'
    assert chapter1.entry_state_claims == ['甲已在门外']
    assert chapter1.ending_state_claims == ['甲进城']
    assert chapter1.beats[0].facts[0].evidence_text == '甲已在门外'
    assert chapter1.beats[0].facts[0].participant_char_ids == ['char_001']
    assert any(row.flagged for row in chapter1.coverage.segments)

    body = pack.model_dump(by_alias=True, mode='json')
    rendered = str(body)
    assert 'b01_01' not in rendered and 'f01_001' not in rendered
    assert 'blueprints[].beats[].beat_id' in pack.mechanical_fields_hidden
    unsigned = dict(body)
    unsigned.pop('binding_sha256')
    unsigned.pop('review_token')
    assert pack.binding_sha256 == view._canonical_sha256(unsigned)


def test_code_or_rules_roll_only_validation_seal_and_exact_review_token(
    monkeypatch,
) -> None:
    main, blueprints, _texts = _book_subjects()
    store = _Store(main, blueprints)
    _install(monkeypatch, store, _report())
    before = view.blueprint_review_workpack('ignored', store.ctx.sid)

    store.ctx.code = 'next-code'
    store.rules_fingerprint = 'next-rules'
    after = view.blueprint_review_workpack('ignored', store.ctx.sid)

    assert after.subject_sha256 == before.subject_sha256
    assert after.review_identity_sha256 == before.review_identity_sha256
    assert after.validation_seal_sha256 != before.validation_seal_sha256
    assert after.review_token != before.review_token


def test_seen_request_refuses_the_legacy_workpack_token() -> None:
    """旧 bpr2 包可留档阅读，但不能再制造新的 seen.read。"""
    with pytest.raises(ValueError):
        BlueprintSeenRequest(review_token='bpr2_' + 'f' * 64)


def test_visible_blueprint_content_or_finding_rolls_review_identity(
    monkeypatch,
) -> None:
    main, blueprints, _texts = _book_subjects()
    store = _Store(main, blueprints)
    report = _report()
    _install(monkeypatch, store, report)
    baseline = view.blueprint_review_workpack('ignored', store.ctx.sid)

    blueprints.blueprints[0].fact_atoms[0].claim = '甲仍在城外等待'
    content_changed = view.blueprint_review_workpack('ignored', store.ctx.sid)
    assert content_changed.subject_sha256 != baseline.subject_sha256
    assert content_changed.review_identity_sha256 != baseline.review_identity_sha256

    blueprints.blueprints[0].fact_atoms[0].claim = '甲已在门外'
    changed_finding = report.rules[1].findings[0].model_copy(update={
        'detail': '审阅者可见的新诊断',
    })
    changed_rule = report.rules[1].model_copy(update={'findings': [changed_finding]})
    changed_report = report.model_copy(update={
        'rules': [report.rules[0], changed_rule, report.rules[2], report.rules[3]],
    })
    _install(monkeypatch, store, changed_report)
    finding_changed = view.blueprint_review_workpack('ignored', store.ctx.sid)
    assert finding_changed.subject_sha256 == baseline.subject_sha256
    assert finding_changed.review_identity_sha256 != baseline.review_identity_sha256


def test_production_checks_drive_multichapter_anomaly_priority_in_workpack(
    monkeypatch,
) -> None:
    """真实 checks→CheckReport→workpack，不用手造带 output_ordinal 的 Finding。"""
    main_raw = production_main_text()
    blueprint_raw = production_blueprints()
    for chapter in blueprint_raw['blueprints']:
        if chapter['chapter_ordinal'] in (1, 3):
            for beat in chapter['beats']:
                beat['hinges'] = []

    report = build(
        sid='1234567890123456789',
        node='stage2.blueprint',
        freshness=FreshnessReport('fresh', 'match'),
        outcomes=checks.check(
            global_g=production_global_grounding(),
            blueprints=blueprint_raw,
            main_text=main_raw,
        ),
    )
    no_hinge = next(row for row in report.rules if row.rule == 'chapter_no_hinge')
    assert [(row.output_ordinal, row.fix_args) for row in no_hinge.findings] == [
        (1, {'--chapters': '1'}),
        (3, {'--chapters': '3'}),
    ]

    main = SimpleNamespace(root=_namespace(main_raw))
    blueprints = _DumpNamespace(
        blueprints=_namespace(blueprint_raw['blueprints']))
    store = _Store(main, blueprints)
    _install(monkeypatch, store, report)

    pack = view.blueprint_review_workpack('ignored', store.ctx.sid)

    assert [row.chapter_ordinal for row in pack.chapters] == [1, 3, 2]
    assert pack.chapters[0].attention_rules == ['chapter_no_hinge']
    assert pack.chapters[1].attention_rules == ['chapter_no_hinge']
    assert pack.chapters[2].attention_rules == []


def test_cross_chapter_hinge_repairs_only_its_typed_owner_chapter() -> None:
    main_raw = production_main_text()
    blueprint_raw = production_blueprints()
    foreign_text = main_raw[1]['text']
    phrase = '街上已经有小贩在吆喝。'
    start = foreign_text.index(phrase)
    blueprint_raw['blueprints'][0]['beats'][0]['hinges'][0]['source'] = {
        'text': phrase,
        'start': start,
        'end': start + len(phrase),
    }

    outcome = next(row for row in checks.check(
        global_g=production_global_grounding(),
        blueprints=blueprint_raw,
        main_text=main_raw,
    ) if row.rule == 'hinge_cross_chapter')

    assert len(outcome.findings) == 1
    hit = outcome.findings[0]
    assert hit.output_ordinal == 1
    assert hit.fix_args == {'--chapters': '1'}
    assert 'ch2' in hit.evidence, '被误引章只作证据，不得混成付费重跑目标'


def test_scope_violation_uses_the_production_json_subject_not_its_prose() -> None:
    blueprint_raw = production_blueprints()
    blueprint_raw['_provenance']['scope_violations'] = [
        '{"batch":2,"requested":[2],"produced":[2,99],'
        '"discarded_chapters":[99],"duplicate_target":0}',
    ]

    outcome = next(row for row in checks.check(
        global_g=production_global_grounding(),
        blueprints=blueprint_raw,
        main_text=production_main_text(),
    ) if row.rule == 'scope_violation')

    assert len(outcome.findings) == 1
    assert outcome.findings[0].output_ordinal == 2


def test_workpack_schema_closes_full_chapter_coverage_and_anomaly_order(
    monkeypatch,
) -> None:
    main, blueprints, _texts = _book_subjects()
    store = _Store(main, blueprints)
    # 先安装快照依赖，再取一份真实 builder 输出作 schema 正例。
    _install(monkeypatch, store, _report())
    pack = view.blueprint_review_workpack('ignored', store.ctx.sid)
    body = pack.model_dump(by_alias=True, mode='json')

    missing = dict(body)
    missing['chapters'] = list(body['chapters'][:-1])
    with pytest.raises(ValidationError, match='不重不漏'):
        BlueprintReviewWorkpack.model_validate(missing)

    wrong_order = dict(body)
    wrong_order['chapters'] = list(reversed(body['chapters']))
    with pytest.raises(ValidationError, match='异常/命中章放在前面'):
        BlueprintReviewWorkpack.model_validate(wrong_order)


def test_workpack_rejects_a_subject_that_changes_between_the_two_reads(
    monkeypatch,
) -> None:
    main, blueprints, _texts = _book_subjects()
    store = _Store(main, blueprints)
    store.blueprint_sha_reads = ['b' * 64, 'c' * 64]
    _install(monkeypatch, store, _report())

    with pytest.raises(ArtifactUnreadable, match='构造期间发生变化'):
        view.blueprint_review_workpack('ignored', store.ctx.sid)


def test_typed_unver_chapter_is_prioritized_together_with_hit() -> None:
    report = _report()
    unver = report.rules[2].model_copy(update={
        'findings': [_finding(
            'role_fate_unverifiable', 'unver', ordinal=1,
            evidence=['ch01'],
        )],
    })
    report = report.model_copy(update={
        'rules': [report.rules[0], report.rules[1], unver, report.rules[3]],
    })

    assert view._chapter_attention(report) == {
        2: ['beats_coarse'],
        1: ['role_fate_unverifiable'],
    }


def test_seen_read_rechecks_the_workpack_subject_before_writing(
    monkeypatch,
) -> None:
    sid = '1234567890123456789'
    binding_sha = 'f' * 64
    review_token = 'bpr3_' + binding_sha
    review_identity = 'b' * 64
    validation_seal = 'c' * 64
    record = approvals._fresh_record(sid)
    proof = approvals._ApprovalProof(
        root_clean_sha1='e' * 40,
        artifacts={STAGE2_CHAPTER_BLUEPRINTS: review_identity},
        decision_sha256='d' * 64,
    )
    writes = []
    monkeypatch.setattr(
        approvals, 'approval_file_lock', lambda *_args: nullcontext())
    monkeypatch.setattr(approvals, '_read_state', lambda *_args: (record, record))
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda *_args: Path('book'))
    workpack = SimpleNamespace(
        binding_sha256=binding_sha,
        review_token=review_token,
        review_identity_sha256=review_identity,
        validation_seal_sha256=validation_seal,
        review_contract_version='stage2-blueprint-review-v4',
        binding=SimpleNamespace(subject_kind='formal'),
    )
    monkeypatch.setattr(
        view, 'blueprint_review_workpack', lambda *_args: workpack)
    identity = {
        'review_token': review_token,
        'review_identity': review_identity,
        'validation_seal': validation_seal,
    }
    proof_reader = lambda *_args: (proof, dict(identity))
    monkeypatch.setattr(approvals, '_blueprint_seen_proof', proof_reader)
    monkeypatch.setattr(approvals, '_blueprint_seen_snapshot', proof_reader)
    monkeypatch.setattr(
        approvals, '_write', lambda _base, updated: writes.append(updated) or updated)

    request = BlueprintSeenRequest(review_token=review_token)
    with pytest.raises(ApprovalRequired, match='必须携带实际读过'):
        approvals.mark_seen('base', sid, 'read')
    assert writes == []

    updated = approvals.mark_seen(
        'base', sid, 'read', blueprint_subject=request)
    assert updated.seen['read'] is True
    assert len(writes) == 1

    stale = request.model_copy(update={'review_token': 'bpr3_' + 'd' * 64})
    with pytest.raises(ApprovalRequired, match='工作包已过期'):
        approvals.mark_seen('base', sid, 'read', blueprint_subject=stale)
    assert len(writes) == 1, '主体漂移时审批账必须零写入'


def test_seen_read_persistent_proof_ignores_validation_epoch_but_not_visible_identity(
    monkeypatch,
) -> None:
    def pack(
        token_body: str,
        *,
        identity: str = 'b' * 64,
        contract: str = 'stage2-blueprint-review-v4',
    ):
        return SimpleNamespace(
            review_token='bpr3_' + token_body,
            review_identity_sha256=identity,
            validation_seal_sha256=token_body,
            review_contract_version=contract,
            binding=SimpleNamespace(subject_kind='formal'),
        )

    monkeypatch.setattr(approvals, '_root_clean_sha1', lambda _path: 'e' * 40)

    first, first_identity = approvals._blueprint_seen_proof(
        Path('book'), pack('c' * 64))
    revalidated, revalidated_identity = approvals._blueprint_seen_proof(
        Path('book'), pack('d' * 64))
    visible_changed, _ = approvals._blueprint_seen_proof(
        Path('book'), pack('e' * 64, identity='a' * 64))
    legacy_contract, _ = approvals._blueprint_seen_proof(
        Path('book'), pack(
            'c' * 64, contract='stage2-blueprint-review-v2'))

    assert first == revalidated, '只换 validation seal/token 不得撤销人工已读事实'
    assert first_identity != revalidated_identity, '提交窗口身份仍须精确变化'
    assert first != visible_changed, '可见内容/finding identity 变化必须使 proof 过期'
    assert first != legacy_contract, 'v2 没展示关系，旧 seen proof 不得在 v3 自动复活'


def test_seen_read_rejects_the_workpack_to_first_proof_race(
    monkeypatch,
) -> None:
    sid = '1234567890123456789'
    request = BlueprintSeenRequest(review_token='bpr3_' + 'f' * 64)
    workpack = SimpleNamespace(
        review_token=request.review_token,
        binding=SimpleNamespace(subject_kind='formal'),
    )
    changed_proof = approvals._ApprovalProof(
        root_clean_sha1='e' * 40,
        artifacts={STAGE2_CHAPTER_BLUEPRINTS: 'c' * 64},
        decision_sha256='d' * 64,
    )
    writes = []
    monkeypatch.setattr(
        approvals, 'approval_file_lock', lambda *_args: nullcontext())
    monkeypatch.setattr(
        approvals, '_read_state', lambda *_args: (
            approvals._fresh_record(sid), approvals._fresh_record(sid)))
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda *_args: Path('book'))
    monkeypatch.setattr(
        view, 'blueprint_review_workpack', lambda *_args: workpack)
    monkeypatch.setattr(
        approvals, '_blueprint_seen_proof',
        lambda *_args: (changed_proof, {
            'review_token': 'bpr3_' + 'e' * 64,
            'review_identity': 'c' * 64,
            'validation_seal': 'd' * 64,
        }))
    monkeypatch.setattr(
        approvals, '_write', lambda *_args: writes.append(True))

    with pytest.raises(ApprovalRequired, match='工作包校验后发生变化'):
        approvals.mark_seen(
            'base', sid, 'read', blueprint_subject=request)
    assert writes == [], 'workpack→proof 竞态不得落审批账'


def test_backfill_never_manufactures_stage2_seen_or_gate(monkeypatch) -> None:
    sid = '1234567890123456789'
    record = approvals._fresh_record(sid)
    writes = []
    monkeypatch.setattr(
        approvals, 'approval_file_lock', lambda *_args: nullcontext())
    monkeypatch.setattr(approvals, '_read_current', lambda *_args: record)
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda *_args: Path('book'))
    monkeypatch.setattr(approvals, '_effective_record', lambda _path, raw: raw)
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s2',))
    monkeypatch.setattr(approvals, '_GATE_REQUIREMENTS', {'s2': ()})
    monkeypatch.setattr(approvals, '_view', lambda _base, raw: raw)
    monkeypatch.setattr(
        approvals, '_write', lambda *_args: writes.append(True))
    monkeypatch.setattr(
        approvals, 'ApprovalBackfillResult',
        lambda *, approvals, backfilled: SimpleNamespace(
            approvals=approvals, backfilled=backfilled))

    result = approvals.backfill_from_disk('base', sid)

    assert result.backfilled == []
    assert result.approvals.seen == {'read': False, 'global': False}
    assert result.approvals.gate['s2'] is False
    assert writes == []
