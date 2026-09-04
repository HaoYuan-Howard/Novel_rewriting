#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 生成侧的零成本闭环测试。"""
from __future__ import annotations

import copy
from functools import partial
import hashlib
import json
import re
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema import encode_artifact
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    ChapterBlueprints, ChapterBlueprintsResponseV31,
    GlobalGrounding, GlobalGroundingCandidateResponse,
)
from novel import prov
from novel.errors import (
    ArtifactUnreadable, ContextBudgetRefused, GateBlocked, ProvenanceRefused,
    ResponseRepairBatchRequired, ResponseRepairRequired, UpstreamMissing,
    UpstreamStale, TerminalState,
)
from novel.llm.budget import enforce_request_budget
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.ledger import AUDIT_REL, CostLedger
from novel.llm import response_repair, stage2_runs
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.core.evidence import iter_global_evidence_slots
from novel.quality.global_review import build_review
from novel.run import RunContext
from novel.run_identity import new_run_id
from novel.stages.stage2 import extract
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection_refs, canonical_ir_projection_v31,
)
from contracts.invariants import HINGE_FAILURE_ROUTE
from novel.stages.stage2.normalize import (
    EvidenceLocatorCorrection, normalize_global, normalize_verbatim,
)
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, global_grounding, main_text


#: 通用生成闭环固定在 v3.1 回归臂；v2 两相拓扑的断言
#: 集中在 `test_stage2_contract_dispatch.py`。
@pytest.fixture(autouse=True)
def _pin_ir31_arm(monkeypatch):
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
    monkeypatch.setattr(
        prov, 'code_version', lambda: 'test-stage2-generation')


def test_parallel_chapter_requests_report_batch_positions_without_schema_changes():
    calls = []
    events = []

    class Response:
        def __init__(self, ordinal):
            self.ordinal = ordinal

        def model_dump(self, **_kwargs):
            return {'blueprints': [{'chapter_ordinal': self.ordinal}]}

    class Client:
        concurrency = 2

        def ask_json(self, *_args, **kwargs):
            calls.append(kwargs)
            return Response(kwargs['progress'].chapter)

    ctx = SimpleNamespace(
        node='stage2.blueprint',
        observe=lambda event, **fields: events.append({'event': event, **fields}),
    )
    requests = [(4, 'sys4', 'user4'), (9, 'sys9', 'user9')]
    results, violations, errors = extract._run_chapter_requests(
        ctx, Client(), requests, model_cls=ChapterBlueprintsResponseV31)

    assert [result['chapter_ordinal'] for result in results] == [4, 9]
    assert violations == [] and errors == []
    assert sorted(
        (call['progress'].chapter, call['progress'].index, call['progress'].total)
        for call in calls
    ) == [(4, 1, 2), (9, 2, 2)]
    assert sorted(
        (event['chapter'], event['outcome'], event['chapter_total'])
        for event in events
    ) == [(4, 'ok', 2), (9, 'ok', 2)]


def test_one_llmfailed_chapter_does_not_abort_other_blueprint_chapters():
    """内容/契约恢复链按章耗尽；不能让一章失败取消其他已付费章节。"""
    calls = []
    events = []

    class Response:
        @staticmethod
        def model_dump(**_kwargs):
            return {'blueprints': [{'chapter_ordinal': 9}]}

    class Client:
        concurrency = 2

        def ask_json(self, *_args, **kwargs):
            ordinal = kwargs['progress'].chapter
            calls.append(ordinal)
            if ordinal == 4:
                raise LlmFailed('同配置两采与模型兜底均被内容闸拒绝')
            return Response()

    ctx = SimpleNamespace(
        node='stage2.blueprint',
        observe=lambda event, **fields: events.append({'event': event, **fields}),
    )
    requests = [(4, 'sys4', 'user4'), (9, 'sys9', 'user9')]
    results, violations, errors = extract._run_chapter_requests(
        ctx, Client(), requests, model_cls=ChapterBlueprintsResponseV31)

    assert sorted(calls) == [4, 9]
    assert results == [{'chapter_ordinal': 9}] and violations == []
    assert len(errors) == 1 and isinstance(errors[0], LlmFailed)
    assert sorted((event['chapter'], event['outcome']) for event in events) == [
        (4, 'failed'), (9, 'ok'),
    ]


def test_response_repair_waits_for_other_blueprint_chapters_before_batching():
    """请求波次先完整 drain；batch producer 再一次处理收齐的 pending。"""
    calls = []

    class Response:
        @staticmethod
        def model_dump(**_kwargs):
            return {'blueprints': [{'chapter_ordinal': 9}]}

    pause = ResponseRepairRequired(
        'ch4 等待修复', attempt_id='a' * 16,
        candidate_path='candidate.json', original_path='original.txt',
        hint='response-repair')

    class Client:
        concurrency = 2

        def ask_json(self, *_args, **kwargs):
            ordinal = kwargs['progress'].chapter
            calls.append(ordinal)
            if ordinal == 4:
                raise pause
            return Response()

    ctx = SimpleNamespace(
        node='stage2.blueprint', observe=lambda *_args, **_kwargs: None)
    results, violations, errors = extract._run_chapter_requests(
        ctx, Client(), [(4, 's4', 'u4'), (9, 's9', 'u9')],
        model_cls=ChapterBlueprintsResponseV31)

    assert sorted(calls) == [4, 9]
    assert results == [{'chapter_ordinal': 9}] and violations == []
    assert errors == [pause]


@pytest.mark.parametrize(('chapters', 'expected_peak'), [(20, 20), (40, 32)])
def test_stage2_uses_one_concurrent_call_per_chapter_up_to_32(chapters, expected_peak):
    ready = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    class Response:
        def __init__(self, ordinal):
            self.ordinal = ordinal

        def model_dump(self, **_kwargs):
            return {'blueprints': [{'chapter_ordinal': self.ordinal}]}

    class Client:
        concurrency = 32

        def ask_json(self, *_args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == expected_peak:
                    ready.set()
            assert ready.wait(2), 'chapter extraction was serialized'
            with lock:
                active -= 1
            return Response(kwargs['progress'].chapter)

    ctx = SimpleNamespace(node='stage2.blueprint', observe=lambda *_a, **_kw: None)
    requests = [(n, 'SYS', f'USER {n}') for n in range(1, chapters + 1)]
    results, violations, errors = extract._run_chapter_requests(
        ctx, Client(), requests, model_cls=ChapterBlueprintsResponseV31)
    assert len(results) == chapters
    assert violations == [] and errors == []
    assert peak == expected_peak


def _response_global() -> dict:
    """正式 v4 fixture → 唯一现役 provider candidate-ID 响应形。"""
    data = copy.deepcopy(global_grounding())
    data.pop('_provenance')
    table = GlobalEvidenceCandidateTable.build(main_text())
    for claim_path, owner, field in iter_global_evidence_slots(data):
        evidence = owner.get(field)
        assert isinstance(evidence, dict) and isinstance(evidence.get('source'), dict)
        matches = [
            row for row in table.rows
            if row.chapter_ordinal == evidence['chapter_ordinal']
            and row.text == evidence['source']['text']
        ]
        assert len(matches) == 1, claim_path
        owner[field] = {
            'evidence_candidate_id': matches[0].evidence_candidate_id,
        }
    return GlobalGroundingCandidateResponse.model_validate(data).model_dump(
        by_alias=True, mode='json')


def _legacy_locator_response_global() -> dict:
    """人工/旧 ``.INVALID`` 恢复面用 locator；绝不交给 provider。"""
    data = copy.deepcopy(global_grounding())
    data.pop('_provenance')
    for _path, owner, field in iter_global_evidence_slots(data):
        evidence = owner.get(field)
        assert isinstance(evidence, dict) and isinstance(evidence.get('source'), dict)
        owner[field] = {
            'chapter_ordinal': evidence['chapter_ordinal'],
            'locator': evidence['source']['text'],
        }
    return data


def _legacy_locator_response_with_every_evidence_kind() -> dict:
    """补一条非空 fate，使人工恢复的全部证据槽共用同一把尺子。"""
    data = _legacy_locator_response_global()
    character = data['entities']['characters']['char_001']
    character['fate'] = '最终仍独自承担家中账目'
    character['fate_evidence'] = {
        'chapter_ordinal': 3,
        'locator': '她端起来吹了吹没有喝。',
    }
    return data


_ALL_GLOBAL_EVIDENCE_PATHS = tuple(
    path for path, _owner, _field in iter_global_evidence_slots(
        _legacy_locator_response_with_every_evidence_kind()))


@pytest.mark.parametrize('claim_path', _ALL_GLOBAL_EVIDENCE_PATHS)
def test_cross_chapter_unique_recovery_covers_every_global_evidence_slot(
    claim_path: str,
):
    response = _legacy_locator_response_with_every_evidence_kind()
    assert len(_ALL_GLOBAL_EVIDENCE_PATHS) == 11
    slots = {
        path: (owner, field)
        for path, owner, field in iter_global_evidence_slots(response)
    }
    owner, field = slots[claim_path]
    evidence = owner[field]
    original_chapter = evidence['chapter_ordinal']
    locator = evidence['locator']
    wrong_chapter = next(
        section['chapter_ordinal'] for section in main_text()
        if section['chapter_ordinal'] != original_chapter
        and locator not in section['text'])
    evidence['chapter_ordinal'] = wrong_chapter

    normalized, accounting = normalize_global(response, main_text())
    normalized_slots = {
        path: (slot_owner, slot_field)
        for path, slot_owner, slot_field in iter_global_evidence_slots(normalized)
    }
    normalized_owner, normalized_field = normalized_slots[claim_path]
    landed = normalized_owner[normalized_field]

    assert landed['chapter_ordinal'] == original_chapter
    assert landed['source'] is not None
    assert accounting['evidence_locator_failures'] == []
    assert accounting.evidence_locator_corrections == (
        EvidenceLocatorCorrection(
            claim_path=claim_path,
            original_chapter=wrong_chapter,
            resolved_chapter=original_chapter,
            recovery='exact',
        ),
    )


@pytest.mark.parametrize(('branch', 'message'), [
    ('secret', 'duplicate secret ids'),
    ('world_rule', 'duplicate world rule ids'),
    ('relation', 'duplicate relation ids'),
])
def test_global_response_and_formal_artifact_reject_duplicate_stable_ids(
    branch: str, message: str,
):
    """patch/apply-edit 按稳定 ID 定位；重复 ID 不能退化成“最后一条生效”。"""
    response = _response_global()
    formal = copy.deepcopy(global_grounding())
    if branch == 'secret':
        response['canon']['secrets'].append(copy.deepcopy(response['canon']['secrets'][0]))
        formal['canon']['secrets'].append(copy.deepcopy(formal['canon']['secrets'][0]))
    elif branch == 'world_rule':
        response['canon']['world_rules'].append(
            copy.deepcopy(response['canon']['world_rules'][0]))
        formal['canon']['world_rules'].append(
            copy.deepcopy(formal['canon']['world_rules'][0]))
    else:
        response['entities']['relations'].append(
            copy.deepcopy(response['entities']['relations'][0]))
        formal['entities']['relations'].append(
            copy.deepcopy(formal['entities']['relations'][0]))

    with pytest.raises(ValidationError, match=message):
        GlobalGroundingCandidateResponse.model_validate(response)
    with pytest.raises(ValidationError, match=message):
        GlobalGrounding.model_validate(formal)


def test_global_response_rejects_order_dependent_timelines_and_lossy_values():
    cases: list[dict] = []

    duplicate_knower = _response_global()
    events = duplicate_knower['canon']['secrets'][0]['knowledge_events']
    events.append({**copy.deepcopy(events[0]), 'known_from_chapter': 2})
    cases.append(duplicate_knower)

    duplicate_rule_start = _response_global()
    versions = duplicate_rule_start['canon']['world_rules'][0]['versions']
    versions.append(copy.deepcopy(versions[0]))
    cases.append(duplicate_rule_start)

    blank_group = _response_global()
    blank_group['canon']['secrets'][0]['knowledge_events'][0]['subject'] = {
        'kind': 'group', 'label': '   ',
    }
    cases.append(blank_group)

    string_chapter = _response_global()
    string_chapter['canon']['secrets'][0]['first_reveal_chapter'] = '1'
    cases.append(string_chapter)

    bool_chapter = _response_global()
    bool_chapter['entities']['relations'][0]['visibility'] = {
        'kind': 'public', 'from_chapter': True,
    }
    cases.append(bool_chapter)

    float_chapter = _response_global()
    float_chapter['canon']['secrets'][0]['knowledge_events'][0][
        'known_from_chapter'] = 1.0
    cases.append(float_chapter)

    for payload in cases:
        with pytest.raises(ValidationError):
            GlobalGroundingCandidateResponse.model_validate(payload)


@pytest.mark.parametrize(
    ('chapter', 'locator', 'recovery', 'failure_fragment', 'expected_chapter'), [
    (1, '信上说父亲已经回城。', 'exact', None, 1),
    (1, '信上说 父亲，已经回城', 'normalized', None, 1),
    (1, '这句话在原文里不存在', None, '找不到', 1),
    (1, '她', None, '多处命中', 1),
    (2, '信上说父亲已经回城。', 'exact', None, 1),
])
def test_global_evidence_locator_prefers_named_chapter_then_global_unique(
    chapter: int, locator: str, recovery: str | None,
    failure_fragment: str | None, expected_chapter: int,
):
    """原章优先；合法错章仅在全书唯一时确定性纠正。"""
    response = _legacy_locator_response_global()
    secret = response['canon']['secrets'][0]
    secret['fact_evidence'] = {'chapter_ordinal': chapter, 'locator': locator}

    normalized, meta = normalize_global(response, main_text())
    evidence = normalized['canon']['secrets'][0]['fact_evidence']

    assert evidence['chapter_ordinal'] == expected_chapter
    if recovery is not None:
        assert evidence['source']['text'] == '信上说父亲已经回城。'
        assert set(evidence['source']) == {'text', 'start', 'end'}
        assert meta['evidence_locator_failures'] == []
        assert 'evidence_locator_rewrites' not in meta
        corrections = meta.evidence_locator_corrections
        if expected_chapter != chapter:
            assert [(row.original_chapter, row.resolved_chapter, row.recovery)
                    for row in corrections] == [(chapter, expected_chapter, recovery)]
        else:
            assert corrections == ()
    else:
        assert evidence['source'] is None
        assert len(meta['evidence_locator_failures']) == 1
        path, failed_chapter, failed_locator, reason = meta['evidence_locator_failures'][0]
        assert path == 'canon.secrets.secret_01.fact'
        assert (failed_chapter, failed_locator) == (chapter, locator)
        assert failure_fragment in reason


def test_existing_invalid_uses_immutable_failure_ledger_for_zero_llm_revalidate(
        tmp_path):
    book = tmp_path / '存量问题稿跨章恢复_122'
    book.mkdir()
    initial_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(initial_ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    response = _response_global()
    client, transport = _client(
        initial_ctx, lambda _req, _index: _ok(response))
    extract.run_global(initial_ctx, client)
    assert len(transport.calls) == 1

    _formal, formal_raw = store.read_own(GlobalGrounding, extract.GLOBAL_REL)
    old_invalid = copy.deepcopy(formal_raw)
    old_invalid['entities']['relations'][0]['predicate_evidence'] = {
        'chapter_ordinal': 1, 'source': None,
    }
    old_invalid['_provenance']['evidence_locator_failures'] = [[
        'entities.relations.rel_01.predicate', 1,
        '回来时看见母亲在门口等她。',
        'locator 在指定章内 0 次；旧契约未做跨章唯一回取',
    ]]
    store.write_invalid(
        extract.GLOBAL_REL,
        GlobalGrounding.model_validate(old_invalid),
        gates_skipped=[])

    invalid_path = book / extract._GLOBAL_INVALID
    raw = json.loads(invalid_path.read_text(encoding='utf-8'))
    failure = next(
        row for row in raw['_provenance']['evidence_locator_failures']
        if row[0] == 'entities.relations.rel_01.predicate')
    # live provenance 是可编辑载体的一部分，但不是人工输入面；篡改它不得
    # 越过创建 INVALID 时的不可变 baseline。
    failure[2] = 'live provenance 里伪造的 locator'
    invalid_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')

    recovery_ctx = _ctx(book, 'stage2.global', paid=False)
    promoted, report = extract.run_revalidate(
        recovery_ctx, actor='operator', reason='使用现役唯一回取规则')

    assert promoted
    assert next(row for row in report.rules
                if row.rule == 'global_evidence_unlocated').status == 'ok'
    assert len(transport.calls) == 1
    assert recovery_ctx.ledger.usage() is None, \
        '旧 INVALID locator 恢复必须保持本 run 0 wire'
    assert not invalid_path.exists()
    landed = ArtifactStore(recovery_ctx).read(
        GlobalGrounding, extract.GLOBAL_REL)
    evidence = landed.entities.relations[0].predicate_evidence
    assert evidence.chapter_ordinal == 2 and evidence.source is not None
    assert landed.provenance.evidence_locator_failures == []
    event, = [row for row in recovery_ctx.ledger.audit_events
              if row['kind'] == 'evidence_locator_rewrite']
    assert event['artifact'] == extract.GLOBAL_REL
    assert event['artifact_sha256'] == ArtifactStore(
        recovery_ctx).full_sha256_or_none(extract.GLOBAL_REL)


def test_formal_span_drift_stays_invalid_across_real_revalidate(tmp_path):
    """formal 漂移失败账不能在下一次恢复时被洗成 locator 并跨章搬家。"""
    book = tmp_path / '正式坐标漂移不得搬家_123'
    book.mkdir()
    initial_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(initial_ctx)
    sections = main_text()
    store.write(extract.MAIN_TEXT, MainText.model_validate(sections))
    client, _transport = _client(
        initial_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(initial_ctx, client)
    _formal, formal_raw = store.read_own(
        GlobalGrounding, extract.GLOBAL_REL)
    formal_before = (book / extract.GLOBAL_REL).read_bytes()

    stale = copy.deepcopy(formal_raw)
    stale.pop('_provenance')
    evidence = stale['entities']['characters']['char_001']['role_evidence']
    assert evidence['source']['text'] in sections[2]['text']
    evidence['chapter_ordinal'] = 2
    normalized, accounting = normalize_global(stale, sections)
    failure = next(
        row for row in accounting['evidence_locator_failures']
        if row[0] == 'entities.characters.char_001.role')
    assert not failure[3].startswith('locator 在指定章内')

    artifact = GlobalGrounding.model_validate({
        **normalized,
        '_provenance': {
            **formal_raw['_provenance'],
            **accounting,
        },
    })
    store.write_invalid(extract.GLOBAL_REL, artifact, gates_skipped=[])

    recovery_ctx = _ctx(book, 'stage2.global', paid=False)
    promoted, report = extract.run_revalidate(
        recovery_ctx,
        actor='operator', reason='确认正式坐标漂移仍关闭式失败')

    assert not promoted
    assert next(row for row in report.rules
                if row.rule == 'global_evidence_unlocated').status == 'hit'
    assert (book / extract.GLOBAL_REL).read_bytes() == formal_before
    assert store.exists(extract._GLOBAL_INVALID)
    assert not [row for row in recovery_ctx.ledger.audit_events
                if row['kind'] == 'evidence_locator_rewrite']


def test_punctuation_only_evidence_locator_becomes_a_recoverable_failure():
    """唯一命中的纯标点也不能先造出没有内容字符的 span。"""
    response = _legacy_locator_response_global()
    path = 'canon.secrets.secret_01.fact'
    response['canon']['secrets'][0]['fact_evidence'] = {
        'chapter_ordinal': 1, 'locator': '※',
    }
    sections = copy.deepcopy(main_text())
    sections[0]['text'] += '※'

    normalized, meta = normalize_global(response, sections)

    assert normalized['canon']['secrets'][0]['fact_evidence']['source'] is None
    failure = next(row for row in meta['evidence_locator_failures'] if row[0] == path)
    assert failure[1:3] == (1, '※')
    assert 'no content characters' in failure[3]


def _response_blueprints() -> dict[int, dict]:
    """底座正式产物 → v3.1 关闭式假响应。"""
    texts = {section['chapter_ordinal']: section['text'] for section in main_text()}
    out: dict[int, dict] = {}
    for blueprint in copy.deepcopy(blueprints())['blueprints']:
        ordinal = blueprint['chapter_ordinal']
        out[ordinal] = canonical_ir_projection_v31(blueprint, texts[ordinal])
    return out


def _respond_blueprints(req: ChatRequest, by_ordinal: dict[int, dict]) -> ChatResponse:
    ordinals = re.findall(r'阅读序第(\d+)章', req.user)
    assert ordinals, '单章 user prompt 没有任务范围'
    return _ok({'blueprints': [by_ordinal[int(ordinals[-1])]]})


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.exists():
        clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    return RunContext(
        novel_dir=book,
        node=node,
        paid=paid,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _client(ctx: RunContext, responder) -> tuple[LlmClient, FakeTransport]:
    transport = FakeTransport(responder=responder)
    return (
        LlmClient(
            ctx,
            transport,
            cache=ResponseCache(),
            model='test-model',
            max_retries=0,
            concurrency=3,
            # 本文件测试 Stage2 形状与并发，不用一个虚构模型冒充供应商预算规格。
            budget_checker=lambda *_args, **_kwargs: None,
        ),
        transport,
    )


def _ok(data: dict) -> ChatResponse:
    return ChatResponse(
        content=json.dumps(data, ensure_ascii=False),
        finish_reason='stop',
        usage={
            'prompt_tokens': 10,
            'completion_tokens': 20,
            'reasoning_tokens': 5,
        },
        model='test-model',
    )


def _reasoning_ok(data: dict) -> ChatResponse:
    """模拟供应商把完整 JSON 错投 reasoning、content 为空的可救回响应。"""
    return ChatResponse(
        content='',
        reasoning=json.dumps(data, ensure_ascii=False),
        finish_reason='stop',
        usage={
            'prompt_tokens': 10,
            'completion_tokens': 20,
            'reasoning_tokens': 20,
        },
        model='test-model',
    )


def _disk_blueprint_ctx(
        book: Path, ledger_path: Path, *, paid: bool, run_id: str | None = None,
) -> RunContext:
    """给 checkpoint 恢复测试一条跨进程可重放的真实 ledger 路。"""
    resolved_run_id = run_id or new_run_id()
    return RunContext(
        novel_dir=book,
        node='stage2.blueprint',
        paid=paid,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path, run_id=resolved_run_id),
        run_id=resolved_run_id,
    )


@pytest.mark.parametrize('route', ['revalidate', 'renormalize'])
def test_blueprint_old_fact_failure_recovers_from_verified_checkpoint_without_wire(
        tmp_path, route):
    """旧 compiler 丢掉了 evidence_phrase；恢复只能取已封存响应，不能从 claim 猜。"""
    book = tmp_path / f'蓝图事实跨拍恢复_{route}_124'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _global_transport = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    phrase = '信上说父亲已经回城。'
    by_ordinal[1]['beats'][0]['fact_atoms'][0]['evidence_phrase'] = phrase
    ledger_path = tmp_path / f'blueprint-rehome-{route}.jsonl'
    producer_run_id = new_run_id()
    producer_ctx = _disk_blueprint_ctx(
        book, ledger_path, paid=True, run_id=producer_run_id)
    producer_client, transport = _client(
        producer_ctx, lambda req, _index: _respond_blueprints(req, by_ordinal))
    report = extract.run_blueprint(producer_ctx, producer_client)
    assert report.passed
    calls_before = len(transport.calls)

    store = ArtifactStore(producer_ctx)
    formal, formal_raw = store.read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    atom = formal_raw['blueprints'][0]['fact_atoms'][0]
    assert atom['source_beat_ref'] == 'b01_02'
    assert atom['source']['text'] == phrase
    source_account = {
        key: copy.deepcopy(formal_raw['_provenance'][key])
        for key in ('prompts', 'model', 'adopted_models', 'usage', 'context', 'inputs')
    }

    # 模拟现役 rehome 上线前已经落下的 INVALID：claim 仍是转述，原 locator 只在
    # 当前 run 的验证后 checkpoint 中。正式稿留在原位，确保提升也覆盖归档路径。
    broken = copy.deepcopy(formal_raw)
    broken_atom = broken['blueprints'][0]['fact_atoms'][0]
    broken_atom['source_beat_ref'] = 'b01_01'
    broken_atom['source'] = None
    broken['_provenance']['fact_atom_failures'] = [[
        'f01_001', 'b01_01', '证据短语不在所属拍区间',
    ]]
    store.write_invalid(
        extract.BLUEPRINT_REL,
        ChapterBlueprints.model_validate(broken),
        gates_skipped=[],
    )

    recovery_ctx = _disk_blueprint_ctx(book, ledger_path, paid=False)
    if route == 'revalidate':
        promoted, recovered_report = extract.run_revalidate(
            recovery_ctx,
            actor='operator',
            reason='按已验证章节响应恢复唯一跨拍事实证据',
        )
        assert promoted
    else:
        recovered_report = extract.run_renormalize(recovery_ctx)
    assert recovered_report.passed
    assert len(transport.calls) == calls_before
    assert recovery_ctx.ledger.usage() is None, '0-wire 恢复不得给当前 run 添成本'
    assert not (book / extract._INVALID).exists()

    landed, landed_raw = ArtifactStore(recovery_ctx).read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    landed_atom = landed_raw['blueprints'][0]['fact_atoms'][0]
    assert landed_atom['source_beat_ref'] == 'b01_02'
    assert landed_atom['source']['text'] == phrase
    assert landed.provenance.fact_atom_failures == []
    assert {
        key: landed_raw['_provenance'][key] for key in source_account
    } == source_account, '恢复只改代码派生证据，不得重写模型/成本/输入来源账'
    if route == 'revalidate':
        assert landed.provenance.revalidated is True
        assert landed.provenance.revalidation_audit is not None
    else:
        assert landed.provenance.partial_rewrite == '--renormalize'


@pytest.mark.parametrize('tamper', [
    'checkpoint_hash', 'ledger_identity', 'accepted_request', 'purpose_binding',
])
def test_blueprint_unclosed_checkpoint_keeps_old_fact_failure_hard(
        tmp_path, tamper):
    book = tmp_path / '蓝图事实恢复拒绝坏检查点_125'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    by_ordinal[1]['beats'][0]['fact_atoms'][0]['evidence_phrase'] = \
        '信上说父亲已经回城。'
    ledger_path = tmp_path / 'blueprint-rehome-tampered.jsonl'
    producer_run_id = new_run_id()
    producer_ctx = _disk_blueprint_ctx(
        book, ledger_path, paid=True, run_id=producer_run_id)
    producer_client, transport = _client(
        producer_ctx, lambda req, _index: _respond_blueprints(req, by_ordinal))
    assert extract.run_blueprint(producer_ctx, producer_client).passed

    store = ArtifactStore(producer_ctx)
    _formal, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    broken = copy.deepcopy(raw)
    broken['blueprints'][0]['fact_atoms'][0].update({
        'source_beat_ref': 'b01_01', 'source': None})
    broken['_provenance']['fact_atom_failures'] = [[
        'f01_001', 'b01_01', '证据短语不在所属拍区间']]
    store.write_invalid(
        extract.BLUEPRINT_REL, ChapterBlueprints.model_validate(broken),
        gates_skipped=[])

    checkpoint = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, producer_run_id, 'blueprint', 1)
    checkpoint_raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    if tamper == 'checkpoint_hash':
        checkpoint_raw['response_payload_sha256'] = '0' * 64
    elif tamper == 'ledger_identity':
        fake_fingerprint = 'f' * 64
        checkpoint_raw['request_fingerprint'] = fake_fingerprint
        checkpoint_raw['attempt_links'][0]['request_fingerprint'] = fake_fingerprint
        checkpoint_raw['accepted_request']['request_fingerprint'] = fake_fingerprint
        checkpoint_raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(
            checkpoint_raw)
    elif tamper == 'accepted_request':
        checkpoint_raw['accepted_request']['user_sha256'] = 'f' * 64
        checkpoint_raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(
            checkpoint_raw)
    else:
        checkpoint_raw['purpose'] = 'stage2.blueprint:repair:ch1'
        checkpoint_raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(
            checkpoint_raw)
    checkpoint.write_text(
        json.dumps(checkpoint_raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')

    calls_before = len(transport.calls)
    promoted, report = extract.run_revalidate(
        _disk_blueprint_ctx(book, ledger_path, paid=False),
        actor='operator', reason='坏 checkpoint 必须关闭式保留失败')

    assert not promoted and not report.passed
    assert len(transport.calls) == calls_before
    assert store.exists(extract._INVALID)
    still_bad, _ = store.read_own(ChapterBlueprints, extract._INVALID)
    assert still_bad.provenance.fact_atom_failures == [
        ('f01_001', 'b01_01', '证据短语不在所属拍区间')]


@pytest.mark.parametrize('resigned_checkpoint', [False, True])
def test_blueprint_reasoning_checkpoint_requires_original_answer_lost_evidence(
        tmp_path, resigned_checkpoint):
    """reasoning 正文不在 ledger content hash 中，必须再绑书内留证与审计指针。"""
    book = tmp_path / f'蓝图reasoning恢复_{resigned_checkpoint}_127'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    phrase = '信上说父亲已经回城。'
    by_ordinal[1]['beats'][0]['fact_atoms'][0]['evidence_phrase'] = phrase
    ledger_path = tmp_path / 'blueprint-rehome-reasoning.jsonl'
    producer_run_id = new_run_id()
    producer_ctx = _disk_blueprint_ctx(
        book, ledger_path, paid=True, run_id=producer_run_id)
    producer_client, transport = _client(
        producer_ctx,
        lambda req, _index: _reasoning_ok({
            'blueprints': [by_ordinal[int(
                re.findall(r'阅读序第(\d+)章', req.user)[-1])]],
        }),
    )
    assert extract.run_blueprint(producer_ctx, producer_client).passed

    store = ArtifactStore(producer_ctx)
    _formal, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    broken = copy.deepcopy(raw)
    broken['blueprints'][0]['fact_atoms'][0].update({
        'source_beat_ref': 'b01_01', 'source': None})
    broken['_provenance']['fact_atom_failures'] = [[
        'f01_001', 'b01_01', '证据短语不在所属拍区间']]
    store.write_invalid(
        extract.BLUEPRINT_REL, ChapterBlueprints.model_validate(broken),
        gates_skipped=[])

    checkpoint = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, producer_run_id, 'blueprint', 1)
    checkpoint_raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert checkpoint_raw['accepted_channel'] == 'reasoning'
    evidence = stage2_runs.paths.answer_lost_path(
        book, checkpoint_raw['attempt_id'])
    assert evidence.is_file()
    if resigned_checkpoint:
        # 只重签 checkpoint，业务 payload 不变。旧口会因 normalized JSON 仍相同而接受；
        # 新口必须发现 accepted_text 已不再是 answer_lost 原始 reasoning 字节。
        checkpoint_raw['accepted_text'] += '\n'
        checkpoint_raw['accepted_text_sha256'] = hashlib.sha256(
            checkpoint_raw['accepted_text'].encode('utf-8')).hexdigest()
        checkpoint_raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(
            checkpoint_raw)
        checkpoint.write_text(
            json.dumps(checkpoint_raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

    calls_before = len(transport.calls)
    recovery_ctx = _disk_blueprint_ctx(book, ledger_path, paid=False)
    promoted, report = extract.run_revalidate(
        recovery_ctx,
        actor='operator', reason='reasoning checkpoint 必须逐字绑定原响应留证')
    assert len(transport.calls) == calls_before
    assert recovery_ctx.ledger.usage() is None
    if resigned_checkpoint:
        assert not promoted and not report.passed
        assert ArtifactStore(recovery_ctx).exists(extract._INVALID)
    else:
        assert promoted and report.passed
        landed, _ = ArtifactStore(recovery_ctx).read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        atom = landed.blueprints[0].fact_atoms[0]
        assert atom.source_beat_ref == 'b01_02'
        assert atom.source is not None and atom.source.text == phrase


def test_blueprint_multiple_matching_checkpoint_payloads_do_not_guess_a_locator(
        tmp_path):
    """两个不同原始 phrase 即使编译成同一 SourceSpan，也没有唯一来源资格。"""
    book = tmp_path / '蓝图事实恢复拒绝多候选_126'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    ledger_path = tmp_path / 'blueprint-rehome-ambiguous.jsonl'
    total_calls = 0
    latest_ctx = None
    for phrase in ('信上说父亲已经回城。', '信上说父亲已经回城'):
        by_ordinal = _response_blueprints()
        by_ordinal[1]['beats'][0]['fact_atoms'][0]['evidence_phrase'] = phrase
        latest_ctx = _disk_blueprint_ctx(book, ledger_path, paid=True)
        client, transport = _client(
            latest_ctx, lambda req, _index, rows=by_ordinal:
            _respond_blueprints(req, rows))
        assert extract.run_blueprint(latest_ctx, client).passed
        total_calls += len(transport.calls)
    assert latest_ctx is not None and total_calls == 2 * len(main_text())

    store = ArtifactStore(latest_ctx)
    _formal, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    broken = copy.deepcopy(raw)
    broken['blueprints'][0]['fact_atoms'][0].update({
        'source_beat_ref': 'b01_01', 'source': None})
    broken['_provenance']['fact_atom_failures'] = [[
        'f01_001', 'b01_01', '证据短语不在所属拍区间']]
    store.write_invalid(
        extract.BLUEPRINT_REL, ChapterBlueprints.model_validate(broken),
        gates_skipped=[])

    recovery_ctx = _disk_blueprint_ctx(book, ledger_path, paid=False)
    promoted, report = extract.run_revalidate(
        recovery_ctx,
        actor='operator', reason='多份原响应没有唯一 locator 来源，保持 HARD')

    assert not promoted and not report.passed
    assert recovery_ctx.ledger.usage() is None
    still_bad, _ = ArtifactStore(recovery_ctx).read_own(
        ChapterBlueprints, extract._INVALID)
    assert still_bad.provenance.fact_atom_failures == [
        ('f01_001', 'b01_01', '证据短语不在所属拍区间')]


def test_stage2_refuses_stale_stage1_before_the_client_can_be_called(tmp_path):
    book = tmp_path / '旧上游_123'
    book.mkdir()
    ctx = _ctx(book, 'stage2.global')
    from contracts.schema.stage0 import Stage0RevisionContent
    from novel.stages.stage0.revisions import make_revision
    from novel.stages.stage1.partition import partition_text

    clean_text = (
        '开篇文案。\n\n'
        + '\n\n'.join(
            f'{row["chapter_ordinal"]}\n{row["text"]}' for row in main_text())
        + '\n'
    )
    clean = book / 'stage0/clean_text.txt'
    clean.write_text(clean_text, encoding='utf-8', newline='')
    source = tmp_path / 'FQ_旧上游_123.txt'
    source.write_text(clean_text, encoding='utf-8', newline='')
    content = Stage0RevisionContent(clean_text=clean_text, removed=[])
    revision = make_revision(
        parent_revision_id=None, origin='machine', actor=None, reason=None,
        code='test-stage2-generation', content=content,
    )
    revisions = {
        'source': {
            'source_ref': {'kind': 'external_file', 'absolute_path': str(source.resolve())},
            'normalized_source_sha1': prov.content_sha1(clean_text.encode('utf-8')),
            'total_code_points': len(clean_text),
        },
        'revisions': [revision.model_dump(mode='json')],
        'current': {'revision_id': revision.revision_id, 'approval': {'status': 'pending'}},
        'candidate_revision_id': None,
        'dismissed': [],
    }
    (book / 'stage0/revisions.json').write_text(
        json.dumps(encode_artifact('stage0/revisions.json', revisions), ensure_ascii=False),
        encoding='utf-8', newline='',
    )
    store = ArtifactStore(ctx)
    store.write(extract.MAIN_TEXT, partition_text(clean_text).main_text)
    clean.write_text('新的 stage0 根\n', encoding='utf-8', newline='')
    client, transport = _client(
        ctx, lambda *_args: pytest.fail('stale 上游不应触达 transport'))

    with pytest.raises(UpstreamStale, match='生成或付费请求前拒绝消费'):
        extract.run_global(ctx, client)

    assert transport.calls == []


def test_stage2_generation_reaches_a_reloadable_jointly_checked_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '生成测试_123'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))

        global_artifact = extract.run_global(global_ctx, global_client)
        assert isinstance(global_artifact, GlobalGrounding)
        assert len(global_transport.calls) == 1
        assert ArtifactStore(global_ctx).exists(extract.GLOBAL_REL)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx, respond)
        report = extract.run_blueprint(blueprint_ctx, blueprint_client)

        assert report.passed
        assert len(blueprint_transport.calls) == len(main_text())
        stored = ArtifactStore(blueprint_ctx).read(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 3
        context = stored.provenance.context
        # 产地必须如实记下本次运行选中的契约。
        from novel.stages.stage2.contract import active_contract
        assert context.architecture == active_contract().architecture
        assert context.architecture == 'extraction-ir-v3.1'
        assert context.stable_prefix
        for blueprint in stored.blueprints:
            for beat in blueprint.beats:
                assert beat.span is not None
                chapter = main_text()[blueprint.chapter_ordinal - 1]['text']
                assert chapter[beat.span.start:beat.span.end] == beat.span.text
                for hinge in beat.hinges:
                    assert hinge.source is not None
                    assert chapter[hinge.source.start:hinge.source.end] == hinge.source.text
        assert not ArtifactStore(blueprint_ctx).exists(extract._INVALID)

        # 一次失败尝试留下 `.INVALID` 后，完整重跑成功必须把它归档；否则复检会按
        # “问题稿优先”继续读旧失败版本，即使新的正式稿已经写好了。
        store = ArtifactStore(blueprint_ctx)
        _previous, formal_raw = store.read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        broken = copy.deepcopy(formal_raw)
        broken_hinge = broken['blueprints'][0]['beats'][0]['hinges'][0]
        broken_hinge['source'] = None
        broken['_provenance']['verbatim_failures'] = [
            ['b01_01', 'hinge', 'missing exact source span'],
        ]
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )
        assert store.exists(extract._INVALID)

        # 一次 RunContext 只代表一次节点执行。上面的报告读取已经在旧 ctx
        # 记入了正式产物边；完整重跑必须像真实 CLI 子进程一样开全新上下文。
        rerun_ctx = _ctx(book, 'stage2.blueprint')
        rerun_client, _rerun_transport = _client(rerun_ctx, respond)
        rerun_report = extract.run_blueprint(rerun_ctx, rerun_client)
        rerun_store = ArtifactStore(rerun_ctx)

        assert rerun_report.passed
        assert not rerun_store.exists(extract._INVALID)
        assert list((book / 'stage2' / '_rounds').glob(
            'chapter_blueprints.INVALID.r*.json'))
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('live_invalid', [False, True])
def test_blueprint_resume_after_formal_write_is_idempotent_before_commit_sentinel(
        monkeypatch, live_invalid):
    """断点收尾不重复写；残留 INVALID 只归档自己，不制造正式稿轮次。"""
    root = Path(tempfile.mkdtemp())
    book = root / '蓝图提交断点_125'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _ = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        run_id = new_run_id()
        ledger_path = root / '_service' / 'ledger.jsonl'

        def blueprint_ctx() -> RunContext:
            return RunContext(
                novel_dir=book, node='stage2.blueprint', paid=True,
                code='test-stage2-generation', cache=CachePolicy.default(),
                ledger=CostLedger(path=ledger_path, run_id=run_id),
                run_id=run_id,
            )

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        from novel.llm import stage2_runs
        real_mark = stage2_runs.mark_committed

        def crash_before_sentinel(*_args, **_kwargs):
            raise RuntimeError('synthetic crash before committed sentinel')

        first_ctx = blueprint_ctx()
        first_transport = FakeTransport(responder=respond)
        first_client = LlmClient(
            first_ctx, first_transport, cache=ResponseCache(root / 'cache'),
            model='test-model', max_retries=0, concurrency=3,
            budget_checker=lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(stage2_runs, 'mark_committed', crash_before_sentinel)
        with pytest.raises(RuntimeError, match='before committed'):
            extract.run_blueprint(first_ctx, first_client)
        monkeypatch.setattr(stage2_runs, 'mark_committed', real_mark)

        assert ArtifactStore(first_ctx).exists(extract.BLUEPRINT_REL)
        assert stage2_runs.committed(first_ctx) is None
        if live_invalid:
            first_store = ArtifactStore(first_ctx)
            formal, _formal_raw = first_store.read_own(
                ChapterBlueprints, extract.BLUEPRINT_REL)
            first_store.write_invalid(
                extract.BLUEPRINT_REL, formal, gates_skipped=[])
            assert first_store.exists(extract._INVALID)
        rounds = book / 'stage2' / '_rounds'
        before_rounds = sorted(path.name for path in rounds.glob(
            'chapter_blueprints.r*.json')) if rounds.exists() else []

        resumed_ctx = blueprint_ctx()
        resumed_transport = FakeTransport(
            responder=lambda *_args: pytest.fail('checkpoint resume 不得新增 wire'))
        resumed_client = LlmClient(
            resumed_ctx, resumed_transport,
            cache=ResponseCache(root / 'cache'), model='test-model',
            max_retries=0, concurrency=3,
            budget_checker=lambda *_args, **_kwargs: None,
        )
        report = extract.run_blueprint(resumed_ctx, resumed_client)

        assert report.passed and resumed_transport.calls == []
        after_rounds = sorted(path.name for path in rounds.glob(
            'chapter_blueprints.r*.json')) if rounds.exists() else []
        assert after_rounds == before_rounds
        if live_invalid:
            resumed_store = ArtifactStore(resumed_ctx)
            assert not resumed_store.exists(extract._INVALID)
            assert list(rounds.glob(
                'chapter_blueprints.INVALID.r*.json'))
        assert stage2_runs.committed(resumed_ctx) is not None
        audit_path = ledger_path.with_name(AUDIT_REL[-1])
        audit = [json.loads(line) for line in audit_path.read_text('utf-8').splitlines()]
        adopted = [row for row in audit if row.get('kind') == 'artifact_adoption']
        assert len(adopted) == len(main_text()), 'resume 不得重复追加 adoption'
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('stale_formal', [False, True])
def test_blueprint_resume_after_prepare_before_formal_uses_sealed_candidate(
        monkeypatch, stale_formal):
    """prepare→formal 崩溃不得丢掉已付费、已过联合 HARD 的候选。"""
    root = Path(tempfile.mkdtemp())
    book = root / '蓝图写前提交断点_126'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _ = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        stale_formal_bytes = None
        if stale_formal:
            old_ctx = _ctx(book, 'stage2.blueprint')
            old_client, _old_transport = _client(
                old_ctx,
                lambda req, _index: _respond_blueprints(
                    req, _response_blueprints()),
            )
            extract.run_blueprint(old_ctx, old_client)
            store = ArtifactStore(global_ctx)
            grounding, _raw = store.read_own(
                GlobalGrounding, extract.GLOBAL_REL)
            changed = grounding.model_dump(by_alias=True, mode='json')
            changed['entities']['characters']['char_001']['canonical'] = '管家'
            extract._write_formal(
                store,
                extract.GLOBAL_REL,
                GlobalGrounding.model_validate(changed),
            )
            assert ArtifactStore(_ctx(book, 'stage2.blueprint')).freshness(
                extract.BLUEPRINT_REL).state == 'stale'
            stale_formal_bytes = (book / extract.BLUEPRINT_REL).read_bytes()

        run_id = new_run_id()
        ledger_path = root / '_service' / 'ledger.jsonl'

        def blueprint_ctx() -> RunContext:
            return RunContext(
                novel_dir=book, node='stage2.blueprint', paid=True,
                code='test-stage2-generation', cache=CachePolicy.default(),
                ledger=CostLedger(path=ledger_path, run_id=run_id),
                run_id=run_id,
            )

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        real_write = extract._write_formal

        def crash_before_formal(*_args, **_kwargs):
            raise RuntimeError('synthetic crash before formal write')

        first = blueprint_ctx()
        first_transport = FakeTransport(responder=respond)
        first_client = LlmClient(
            first, first_transport, cache=ResponseCache(root / 'cache'),
            model='test-model', max_retries=0, concurrency=3,
            budget_checker=lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(extract, '_write_formal', crash_before_formal)
        with pytest.raises(RuntimeError, match='before formal write'):
            extract.run_blueprint(first, first_client)
        monkeypatch.setattr(extract, '_write_formal', real_write)

        from novel.llm import stage2_runs
        if stale_formal:
            assert (book / extract.BLUEPRINT_REL).read_bytes() == stale_formal_bytes
        else:
            assert not ArtifactStore(first).exists(extract.BLUEPRINT_REL)
        assert stage2_runs.commit_state(first)['state'] == 'prepared'
        sealed, finalization = stage2_runs.restore_commit_payloads(first)
        assert sealed['blueprints']
        assert finalization['operation'] == 'stage2.blueprint full'
        assert 'live_before' in finalization

        resumed = blueprint_ctx()
        resumed_transport = FakeTransport(
            responder=lambda *_args: pytest.fail(
                'prepared journal 已封存候选，不得重调模型'))
        resumed_client = LlmClient(
            resumed, resumed_transport, cache=ResponseCache(root / 'cache'),
            model='test-model', max_retries=0, concurrency=3,
            budget_checker=lambda *_args, **_kwargs: None,
        )
        report = extract.run_blueprint(resumed, resumed_client)

        assert report.passed and resumed_transport.calls == []
        assert ArtifactStore(resumed).exists(extract.BLUEPRINT_REL)
        assert stage2_runs.committed(resumed) is not None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_rebuild_is_not_locked_by_an_obsolete_blueprint_schema():
    """旧版下游不是可消费蓝图；它不能反向锁死 global producer 的重建路。"""
    root = Path(tempfile.mkdtemp())
    book = root / '旧蓝图升级恢复_124'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        obsolete = book / extract.BLUEPRINT_REL
        obsolete.parent.mkdir(parents=True, exist_ok=True)
        obsolete.write_text(
            json.dumps({'_schema_version': 2}, ensure_ascii=False) + '\n',
            encoding='utf-8', newline='\n')
        client, transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))

        artifact = extract.run_global(ctx, client)

        assert isinstance(artifact, GlobalGrounding)
        assert len(transport.calls) == 1
        assert obsolete.read_text(encoding='utf-8').startswith('{"_schema_version": 2}')
        assert store.read(GlobalGrounding, extract.GLOBAL_REL) == artifact
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write_current_blueprint_fixture(book: Path, ctx: RunContext) -> None:
    """落一份可严格读取的当前蓝图，用于覆盖逐产物升级的中间态。"""
    payload = copy.deepcopy(blueprints())
    payload['_provenance'].update({
        'code': ctx.code,
        'root_clean_sha1': ctx.root_clean_sha1,
        'inputs': {},
        'prompts': {'BP_SYS': '0123456789'},
        'model': 'fixture-model',
    })
    artifact = ChapterBlueprints.model_validate(payload)
    envelope = encode_artifact(
        extract.BLUEPRINT_REL, artifact.model_dump(by_alias=True, mode='json'))
    target = book / extract.BLUEPRINT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n',
    )


def test_global_producer_rebuilds_obsolete_global_and_review_as_one_transaction():
    """v3 producer 可越过 v1 正式稿；旧 global/review 原字节分别进入历史。"""
    root = Path(tempfile.mkdtemp())
    book = root / '逐产物升级恢复_125'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        _write_current_blueprint_fixture(book, ctx)
        old_global = b'{"_schema_version":1,"legacy":"global"}\n'
        old_review_payload = build_review(global_grounding()).model_dump(
            by_alias=True, mode='json')
        old_review = (
            json.dumps(
                encode_artifact(extract.GLOBAL_REVIEW_REL, old_review_payload),
                ensure_ascii=False, indent=2,
            ) + '\n'
        ).encode('utf-8')
        global_path = book / extract.GLOBAL_REL
        review_path = book / extract.GLOBAL_REVIEW_REL
        global_path.write_bytes(old_global)
        review_path.write_bytes(old_review)
        client, transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))

        artifact = extract.run_global(ctx, client)

        assert len(transport.calls) == 1
        assert store.read(GlobalGrounding, extract.GLOBAL_REL) == artifact
        review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        assert review.items and all(item.state == 'pending' for item in review.items)
        global_rounds = list((book / 'stage2/_rounds').glob(
            'global_grounding.r*.json'))
        review_rounds = list((book / 'stage2/_rounds').glob(
            'global_review.r*.json'))
        assert len(global_rounds) == len(review_rounds) == 1
        assert global_rounds[0].read_bytes() == old_global
        assert review_rounds[0].read_bytes() == old_review
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('marker', [999, 'v1'])
def test_global_rebuild_refuses_non_obsolete_global_baseline(marker):
    """未来版本与非法 marker 不能借 producer 重建口被当成低版本跳过。"""
    root = Path(tempfile.mkdtemp())
    book = root / f'坏global基线_{abs(hash(str(marker))) % 1000}'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        _write_current_blueprint_fixture(book, ctx)
        broken = book / extract.GLOBAL_REL
        before = (json.dumps({'_schema_version': marker}, ensure_ascii=False) + '\n').encode()
        broken.write_bytes(before)
        client, _transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))

        with pytest.raises(ArtifactUnreadable):
            extract.run_global(ctx, client)

        assert broken.read_bytes() == before
        assert not list((book / 'stage2/_rounds').glob('global_grounding.r*.json'))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_global_review_rolls_back_an_obsolete_global_rebuild():
    """companion 不是低版本时，global 正式稿与刚建的归档必须一起回滚。"""
    root = Path(tempfile.mkdtemp())
    book = root / '坏review事务回滚_126'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        _write_current_blueprint_fixture(book, ctx)
        old_global = b'{"_schema_version":1,"legacy":"global"}\n'
        bad_review = b'{"_schema_version":999}\n'
        global_path = book / extract.GLOBAL_REL
        review_path = book / extract.GLOBAL_REVIEW_REL
        global_path.write_bytes(old_global)
        review_path.write_bytes(bad_review)
        client, _transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))

        with pytest.raises(ArtifactUnreadable):
            extract.run_global(ctx, client)

        assert global_path.read_bytes() == old_global
        assert review_path.read_bytes() == bad_review
        assert not list((book / 'stage2/_rounds').glob('global_grounding.r*.json'))
        assert not list((book / 'stage2/_rounds').glob('global_review.r*.json'))
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('marker,label', [
    (999, '比当前还高的版本'),
    ('v2', '非法 marker'),
])
def test_global_rebuild_still_refuses_a_blueprint_that_is_not_merely_old(marker, label):
    """⭐ **「跳过旧蓝图」不许写成「跳过整族 `ArtifactVersionError`」。**

    那一族有六个抛出点：缺 marker、非法 marker、root envelope 形状坏、
    **版本比当前还高**、以及真正的「旧版本」。按异常类型放宽会把前四种一起放掉，
    于是一份 `_schema_version: 999` 的蓝图让这道守卫整个消失 ——
    这次 global 重跑可以新造悬空 char_id 而不被任何东西挡住（已复现）。
    """
    root = Path(tempfile.mkdtemp())
    book = root / f'坏蓝图不放行_{abs(hash(label)) % 1000}'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        broken = book / extract.BLUEPRINT_REL
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text(
            json.dumps({'_schema_version': marker}, ensure_ascii=False) + '\n',
            encoding='utf-8', newline='\n')
        client, _transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))

        with pytest.raises(ArtifactUnreadable):
            extract.run_global(ctx, client)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_candidate_boundary_keeps_intermediate_hard_in_memory():
    """agent 可把 HARD 反馈接回同一 session；中间轮不落 INVALID，也不提前结算 usage。"""
    root = Path(tempfile.mkdtemp())
    book = root / '全局候选循环边界_452'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        bad = _response_global()
        bad['entities']['characters']['char_002']['canonical'] = \
            bad['entities']['characters']['char_001']['canonical']

        def respond(_req: ChatRequest, index: int) -> ChatResponse:
            return _ok(bad if index == 0 else _response_global())

        client, transport = _client(ctx, respond)
        prepared = extract.prepare_global_candidate(ctx)
        first = client.ask_json(
            prepared.system, prepared.user,
            model_cls=prepared.response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS)
        rejected = extract.evaluate_global_candidate(
            ctx, client, prepared, first)

        assert not rejected.passed
        assert 'canonical_unique' in rejected.hard_hits
        feedback = rejected.feedback_rows()
        assert any(row['rule'] == 'canonical_unique' for row in feedback)
        assert not (book / extract.GLOBAL_REL).exists()
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert not (book / extract.GLOBAL_REVIEW_REL).exists()
        assert not list((book / 'stage2/_rounds').glob('global_grounding*.json'))

        # 模拟同一 logical session 消费代码反馈后交回完整 replacement JSON。
        # user 加稳定反馈摘要只为让旧 LlmClient 测试缝产生第二次 wire；正式 agent
        # 会由 SessionAgentClient.resume 复用 provider session。
        second = client.ask_json(
            prepared.system,
            prepared.user + '\n\n代码反馈：canonical_unique，请交回完整 JSON。',
            model_cls=prepared.response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS)
        accepted = extract.evaluate_global_candidate(
            ctx, client, prepared, second)
        assert accepted.passed
        assert not (book / extract.GLOBAL_REL).exists(), '评估口不得冒充终态提交'

        landed = extract.finalize_global_candidate(
            ctx, client, prepared, accepted)
        assert isinstance(landed, GlobalGrounding)
        assert len(transport.calls) == 2
        assert landed.provenance.usage is not None
        assert landed.provenance.usage.calls_made == 2, \
            '中间 settle 会让正式稿漏掉第一轮已发生调用'
        assert (book / extract.GLOBAL_REL).is_file()
        assert (book / extract.GLOBAL_REVIEW_REL).is_file()
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_finalize_refuses_a_mutated_raw_candidate_snapshot():
    root = Path(tempfile.mkdtemp())
    book = root / 'global候选变异防线_4521'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        candidate = _response_global()
        client, _transport = _client(
            ctx, lambda _req, _index: _ok(candidate))
        prepared = extract.prepare_global_candidate(ctx)
        response = client.ask_json(
            prepared.system, prepared.user,
            model_cls=prepared.response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS)
        evaluation = extract.evaluate_global_candidate(
            ctx, client, prepared, response)
        assert evaluation.passed

        evaluation._response.entities.relations[0].predicate = '评估后篡改的关系'
        with pytest.raises(ProvenanceRefused, match='raw candidate 已变异'):
            extract.finalize_global_candidate(
                ctx, client, prepared, evaluation)
        assert not (book / extract.GLOBAL_REL).exists()
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_candidate_terminal_rejection_writes_invalid_only_once():
    """重复评估是纯操作；只有显式 terminal finalize 才产生一轮可恢复 INVALID。"""
    root = Path(tempfile.mkdtemp())
    book = root / '全局候选终态拒绝_453'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        bad = _response_global()
        bad['entities']['characters']['char_002']['canonical'] = \
            bad['entities']['characters']['char_001']['canonical']
        client, transport = _client(ctx, lambda _req, _index: _ok(bad))
        prepared = extract.prepare_global_candidate(ctx)
        response = client.ask_json(
            prepared.system, prepared.user,
            model_cls=prepared.response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS)

        first = extract.evaluate_global_candidate(
            ctx, client, prepared, response)
        second = extract.evaluate_global_candidate(
            ctx, client, prepared, response)
        assert first.hard_hits == second.hard_hits
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert not list((book / 'stage2/_rounds').glob(
            'global_grounding.INVALID.r*.json'))

        with pytest.raises(GateBlocked, match='canonical_unique'):
            extract.finalize_global_candidate(
                ctx, client, prepared, second)

        assert len(transport.calls) == 1
        assert (book / extract._GLOBAL_INVALID).is_file()
        assert not (book / extract.GLOBAL_REL).exists()
        assert not (book / extract.GLOBAL_REVIEW_REL).exists()
        projection = ArtifactStore(ctx).freshness(
            extract.BLUEPRINT_CHARACTER_REL)
        assert (projection.state, projection.reason) == (
            'unknown', 'global_naming_blocked')

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx,
            lambda req, _index: _respond_blueprints(
                req, _response_blueprints()),
        )
        with pytest.raises(ProvenanceRefused, match='不得启动付费 Blueprint'):
            extract.run_blueprint(blueprint_ctx, blueprint_client)
        assert blueprint_transport.calls == [], '称名未收口必须在任何 Blueprint wire 前停止'
        assert not list((book / 'stage2/_rounds').glob(
            'global_grounding.INVALID.r*.json'))
        invalid = ArtifactStore(ctx).read(
            GlobalGrounding, extract._GLOBAL_INVALID)
        assert invalid.provenance.usage is not None
        assert invalid.provenance.usage.calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_non_name_global_hard_still_materializes_projection_and_blueprint_lands(
        tmp_path):
    """Global 非称名 HARD 留在 INVALID；Blueprint 只按自身 HARD 决定正式落盘。"""
    book = tmp_path / 'Global问题稿仍可跑蓝图_4531'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(global_ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    bad = _response_global()
    secret = bad['canon']['secrets'][0]
    secret['setup'] = {
        'kind': 'foreshadowed',
        'chapters': [secret['first_reveal_chapter']],
    }
    global_client, global_transport = _client(
        global_ctx, lambda _req, _index: _ok(bad))

    with pytest.raises(GateBlocked, match='secret_setup_order'):
        extract.run_global(global_ctx, global_client)

    assert len(global_transport.calls) == 1
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)
    assert store.exists(extract.BLUEPRINT_CHARACTER_REL)
    assert store.freshness(extract.BLUEPRINT_CHARACTER_REL).state == 'fresh'

    by_ordinal = _response_blueprints()
    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, blueprint_transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(req, by_ordinal),
    )
    report = extract.run_blueprint(blueprint_ctx, blueprint_client)

    assert len(blueprint_transport.calls) == len(main_text())
    assert not report.passed, '联合报告仍应披露 Global HARD，不能洗成全绿'
    assert store.exists(extract.BLUEPRINT_REL)
    assert not store.exists(extract._INVALID)
    artifact = ArtifactStore(blueprint_ctx).read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert set(artifact.provenance.inputs) == {
        extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL,
    }
    assert extract.GLOBAL_REL not in artifact.provenance.inputs

    from novel.stages.stage2.global_review import require_approved

    with pytest.raises(GateBlocked, match='global'):
        require_approved(_ctx(book, 'stage4.design', paid=False))


def test_name_change_with_interrupted_projection_write_closes_blueprint_and_stage4(
        tmp_path, monkeypatch):
    """global 字节已换名而投影仍旧：两个真实 consumer 都必须关闭，且 0 wire。"""
    book = tmp_path / '称名投影中断关闭_4532'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(global_ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    _grounding, raw = store.read_own(GlobalGrounding, extract.GLOBAL_REL)
    renamed = raw['entities']['characters']['char_001']
    renamed['canonical'] = '中断后新名'
    renamed['address'][0]['surface'] = '中断后新名'
    (book / extract.GLOBAL_REL).write_text(
        json.dumps(encode_artifact(extract.GLOBAL_REL, raw),
                   ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    assert store.freshness(extract.GLOBAL_REL).state == 'fresh'
    assert store.freshness(extract.BLUEPRINT_CHARACTER_REL).state == 'stale'

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, blueprint_transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(req, _response_blueprints()),
    )
    with pytest.raises(ProvenanceRefused, match='stage2.global --renormalize'):
        extract.run_blueprint(blueprint_ctx, blueprint_client)
    assert blueprint_transport.calls == []

    # 隔离掉 review 的更早阻塞，只验证 Stage4 自己确实显式复查 projection；
    # 否则普通 Blueprint freshness 不递归 special artifact，旧字节可能漏过。
    from novel.stages.stage2 import global_review

    monkeypatch.setattr(
        global_review, 'generation_status',
        lambda _ctx: (
            SimpleNamespace(
                safe=True, current=True, blocking_item_ids=(),
            ),
            {'PASS': 1},
        ),
    )
    with pytest.raises(UpstreamStale, match='blueprint_character_projection'):
        global_review.require_approved(
            _ctx(book, 'stage4.design', paid=False))


def test_global_candidate_refuses_code_drift_before_settle_or_write(monkeypatch):
    """长 session 不能先落旧代码候选；拒绝也不能吞掉本轮 usage 窗口。"""
    root = Path(tempfile.mkdtemp())
    book = root / '全局候选代码漂移_454'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        client, transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))
        prepared = extract.prepare_global_candidate(ctx)
        response = client.ask_json(
            prepared.system, prepared.user,
            model_cls=prepared.response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS)
        evaluation = extract.evaluate_global_candidate(
            ctx, client, prepared, response)
        original_code_version = extract.prov.code_version

        monkeypatch.setattr(
            extract.prov, 'code_version', lambda: 'changed-during-agent-session')
        with pytest.raises(ProvenanceRefused, match='代码版本发生漂移'):
            extract.finalize_global_candidate(
                ctx, client, prepared, evaluation)

        assert not (book / extract.GLOBAL_REL).exists()
        assert not (book / extract.GLOBAL_REVIEW_REL).exists()
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert not list((book / 'stage2/_rounds').glob('global_grounding*.json'))

        # 拒绝发生在 settle 之前：恢复同一启动代码后，原候选仍能一次认领完整调用窗。
        monkeypatch.setattr(extract.prov, 'code_version', original_code_version)
        landed = extract.finalize_global_candidate(
            ctx, client, prepared, evaluation)
        assert len(transport.calls) == 1
        assert landed.provenance.usage is not None
        assert landed.provenance.usage.calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_global_invalid_can_be_revalidated_without_another_llm_call():
    root = Path(tempfile.mkdtemp())
    book = root / '提升测试_456'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        response = _response_global()
        original = response['entities']['characters']['char_002']['canonical']
        response['entities']['characters']['char_002']['canonical'] = \
            response['entities']['characters']['char_001']['canonical']
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1

        invalid = book / extract._GLOBAL_INVALID
        raw = json.loads(invalid.read_text(encoding='utf-8'))
        raw['entities']['characters']['char_002']['canonical'] = original
        invalid.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        promoted, report = extract.run_revalidate(
            _ctx(book, 'stage2.global', paid=False),
            actor='reviewer', reason='fix duplicate canonical name')
        assert promoted
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not invalid.exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert stored.provenance.revalidated is True
        audit = stored.provenance.revalidation_audit
        assert audit is not None
        assert audit.actor == 'reviewer'
        assert audit.reason == 'fix duplicate canonical name'
        assert audit.content_changed is True
        assert audit.edited_by_human is True
        assert '/entities/characters/char_002/canonical' in \
            audit.changed_json_pointers
        assert next(r for r in report.rules
                    if r.rule == 'canonical_unique').status == 'ok'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_resample_bypasses_the_rejected_cache_and_preserves_its_round():
    """普通重跑仍命中问题缓存；显式重采样才产生第二次调用与新轮次。"""
    root = Path(tempfile.mkdtemp())
    book = root / '全局重采样测试_460'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        bad = _response_global()
        bad['entities']['characters']['char_002']['canonical'] = \
            bad['entities']['characters']['char_001']['canonical']

        def respond(_req: ChatRequest, index: int) -> ChatResponse:
            return _ok(bad if index == 0 else _response_global())

        client, transport = _client(ctx, respond)
        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        original_invalid = (book / extract._GLOBAL_INVALID).read_bytes()

        # 参数未变的普通重跑必须仍然读缓存，不能暗中把“重跑”解释成“再花钱”。
        with pytest.raises(GateBlocked) as blocked:
            extract.run_global(ctx, client)
        assert ('stage2.global --revalidate --actor <id> --reason <why>'
                in str(blocked.value))
        assert len(transport.calls) == 1
        assert ctx.ledger.usage().calls_cached == 1

        artifact = extract.run_resample(ctx, client)
        assert isinstance(artifact, GlobalGrounding)
        assert ctx.cache.name == 'refresh-write'
        assert len(transport.calls) == 2, '重采样仍命中了旧问题缓存'
        assert (book / extract.GLOBAL_REL).is_file()
        assert not (book / extract._GLOBAL_INVALID).exists()
        archived = list((book / 'stage2' / '_rounds').glob(
            'global_grounding.INVALID.r*.json'))
        assert archived and any(path.read_bytes() == original_invalid for path in archived), \
            '原问题稿没有进入轮次归档'
        usage = ctx.ledger.usage()
        assert usage.calls_made == 2 and usage.calls_cached == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_global_over_limit_stops_before_transport_cache_ledger_or_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '超限测试_123'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        transport = FakeTransport.always(_ok(_response_global()))
        cache = ResponseCache(root / 'cache')
        checker = partial(enforce_request_budget, counter=lambda _request: 904_001)
        client = LlmClient(ctx, transport, cache=cache, model='deepseek-v4-pro',
                           max_retries=0, budget_checker=checker)

        with pytest.raises(ContextBudgetRefused, match='input_tokens=904001'):
            extract.run_global(ctx, client)

        store = ArtifactStore(ctx)
        assert not store.exists(extract.GLOBAL_REL)
        assert not store.exists(extract._INVALID)
        assert transport.calls == []
        assert ctx.ledger.usage() is None
        assert not (root / 'cache').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage4_plan_over_limit_with_candidate_stops_before_b_transport_or_outputs():
    from novel.stages.stage4 import plan as stage4_plan
    from tests.factories import approved_global_review
    from tests.test_stage4_v3_integration import _split_a_responder

    root = Path(tempfile.mkdtemp())
    book = root / '规划超限测试_123'
    book.mkdir()
    try:
        # 先由当前 Stage2 正式 producer 生成 Stage4 所需的三份上游。
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _ = _client(
            global_ctx, lambda _request, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)
        global_store = ArtifactStore(global_ctx)
        current_global = global_store.read_own(
            GlobalGrounding, extract.GLOBAL_REL)[0]
        global_store.write(
            extract.GLOBAL_REVIEW_REL,
            approved_global_review(current_global),
        )

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, _ = _client(blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

        # v3 的 B 相只接受已经正式落盘的冻结 A 候选。先单独走 A producer，
        # 否则这个用例只会在预算闸之前因缺 candidate 终止，根本没测到 B 的预算前置。
        design_ctx = _ctx(book, 'stage4.design')
        design_store = ArtifactStore(design_ctx)
        produced_blueprints = design_store.read(
            ChapterBlueprints, stage4_plan.BLUEPRINTS)
        design_response = _split_a_responder(
            produced_blueprints.model_dump(by_alias=True, mode='json'))
        design_client, design_transport = _client(
            design_ctx,
            lambda request, _index: _ok(design_response(request)))
        stage4_plan.run_design(design_ctx, design_client)
        assert len(design_transport.calls) == 1 + len(
            produced_blueprints.blueprints)
        candidate_before = (book / stage4_plan.CANDIDATE_REL).read_bytes()

        ctx = _ctx(book, 'stage4.plan')
        transport = FakeTransport.always(ChatResponse(content='{}'))
        # Stage4B 当前声明 48k 输出；952001 + 48000 = 1000001，恰越过
        # DeepSeek V4 的 1M context。不能沿用 Stage2 global 的 904001，后者配
        # global 的 96k 输出才越线，放到 B 已经是合法预算。
        checker = partial(enforce_request_budget, counter=lambda _request: 952_001)
        client = LlmClient(ctx, transport, cache=ResponseCache(root / 'cache'),
                           model='deepseek-v4-pro', max_retries=0,
                           budget_checker=checker)

        with pytest.raises(ContextBudgetRefused, match='stage4.plan'):
            stage4_plan.run_plan(ctx, client)

        store = ArtifactStore(ctx)
        assert store.exists(stage4_plan.CANDIDATE_REL)
        assert (book / stage4_plan.CANDIDATE_REL).read_bytes() == candidate_before
        assert not store.exists(stage4_plan.BINDING_REL)
        assert not store.exists(stage4_plan.REL)
        assert not store.exists(stage4_plan._INVALID)
        assert not store.exists(stage4_plan.CTX_REL)
        assert transport.calls == []
        assert ctx.ledger.usage() is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_resample_requires_a_rejected_candidate_before_spending():
    root = Path(tempfile.mkdtemp())
    book = root / '无问题稿重采样测试_461'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        client, transport = _client(
            ctx, lambda _req, _index: _ok(_response_global()))
        with pytest.raises(UpstreamMissing, match='尚未发起请求'):
            extract.run_resample(ctx, client)
        assert transport.calls == []
        assert ctx.cache.name == 'default'
        assert ctx.ledger.usage() is None, '没有任何调用的账本不能伪装成一份全零 usage'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_failed_global_resample_keeps_the_formal_round_and_archives_the_problem():
    root = Path(tempfile.mkdtemp())
    book = root / '重采样失败测试_462'
    book.mkdir()
    try:
        first_ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(first_ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        first_client, _ = _client(
            first_ctx, lambda _req, _index: _ok(_response_global()))
        formal = extract.run_global(first_ctx, first_client)
        formal_before = (book / extract.GLOBAL_REL).read_bytes()

        rejected = formal.model_dump(by_alias=True)
        rejected['entities']['characters']['char_002']['canonical'] = \
            rejected['entities']['characters']['char_001']['canonical']
        store.write_invalid(
            extract.GLOBAL_REL,
            GlobalGrounding.model_validate(rejected),
            gates_skipped=[],
        )
        invalid_before = (book / extract._GLOBAL_INVALID).read_bytes()

        retry_ctx = _ctx(book, 'stage2.global')
        retry_bad = _response_global()
        retry_bad['entities']['characters']['char_002']['canonical'] = \
            retry_bad['entities']['characters']['char_001']['canonical']
        retry_client, retry_transport = _client(
            retry_ctx, lambda _req, _index: _ok(retry_bad))
        with pytest.raises(GateBlocked):
            extract.run_resample(retry_ctx, retry_client)

        assert len(retry_transport.calls) == 1
        assert (book / extract.GLOBAL_REL).read_bytes() == formal_before, \
            '失败重采样覆盖了正式产物'
        archived = list((book / 'stage2' / '_rounds').glob(
            'global_grounding.INVALID.r*.json'))
        assert archived and any(path.read_bytes() == invalid_before for path in archived)
        assert retry_ctx.ledger.usage().calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cli_dispatches_global_resample_through_the_dedicated_entry(monkeypatch):
    from novel.cli import exit_codes as X
    from novel.cli import main as cli

    ctx = SimpleNamespace(node='stage2.global')
    marker = object()
    called = []
    monkeypatch.setattr(cli, '_llm_client', lambda got: marker if got is ctx else None)
    monkeypatch.setattr(
        extract, 'run_resample', lambda got_ctx, got_client:
        called.append((got_ctx, got_client)))
    assert cli._run_stage2(ctx, ['--resample']) == X.OK
    assert called == [(ctx, marker)]


def test_stage2_global_renormalize_promotes_deterministically_pruned_invalid():
    root = Path(tempfile.mkdtemp())
    book = root / '全局重归一测试_457'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        ArtifactStore(ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        response = _response_global()
        secret = response['canon']['secrets'][0]
        reveal = secret['first_reveal_chapter']
        secret['setup'] = {'kind': 'foreshadowed', 'chapters': [reveal]}
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1
        assert (book / extract._GLOBAL_INVALID).is_file()

        report = extract.run_renormalize(
            _ctx(book, 'stage2.global', paid=False))

        assert report.passed
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not (book / extract._GLOBAL_INVALID).exists()
        stored = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        # 埋点全裁光 ⇒ 降级成 cold。⚠ 它与真·冷揭示在字段值上同形，
        # 分得开的只有下一行那份裁剪账。
        assert stored.canon.secrets[0].setup.kind == 'cold'
        assert stored.provenance.setup_chapters_pruned == []
        assert stored.provenance.partial_rewrite == '--renormalize'
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_blueprint_renormalize_does_not_invent_character_refs():
    root = Path(tempfile.mkdtemp())
    book = root / '蓝图重归一测试_458'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
        calls_before = len(blueprint_transport.calls)

        path = book / extract.BLUEPRINT_REL
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['blueprints'][0]['beats'][0]['present_char_ids'] = ['char_999']
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        report = extract.run_renormalize(
            _ctx(book, 'stage2.blueprint', paid=False))

        assert not report.passed
        assert len(blueprint_transport.calls) == calls_before
        dangling = next(line for line in report.rules
                        if line.rule == 'present_ids_dangling')
        assert dangling.findings, '没有显示名时不得凭空修复人物 ID'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_old_global_input_edge_refreshes_current_v7_without_wire(tmp_path):
    """v7 载体不升版；旧 global provenance 诚实 unknown，再免费换成真实输入。"""
    book = tmp_path / '旧v6来源边零成本刷新_4581'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, blueprint_transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(req, by_ordinal),
    )
    assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
    calls_before = len(blueprint_transport.calls)

    path = book / extract.BLUEPRINT_REL
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['_schema_version'] == 7
    inputs = raw['_provenance']['inputs']
    old_edge = inputs.pop(extract.BLUEPRINT_CHARACTER_REL)
    old_edge['file'] = prov.fingerprint_bytes(
        (book / extract.GLOBAL_REL).read_bytes())
    inputs[extract.GLOBAL_REL] = old_edge
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')

    legacy = ArtifactStore(
        _ctx(book, 'stage2.blueprint', paid=False)).freshness(
            extract.BLUEPRINT_REL)
    assert (legacy.state, legacy.reason) == (
        'unknown', 'legacy_blueprint_inputs')

    recovery_ctx = _ctx(book, 'stage2.blueprint', paid=False)
    report = extract.run_renormalize(recovery_ctx)

    assert report.passed
    assert len(blueprint_transport.calls) == calls_before
    assert recovery_ctx.ledger.usage() is None
    refreshed = ArtifactStore(recovery_ctx).read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert json.loads(path.read_text(encoding='utf-8'))['_schema_version'] == 7
    assert set(refreshed.provenance.inputs) == {
        extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL,
    }
    assert ArtifactStore(recovery_ctx).freshness(
        extract.BLUEPRINT_REL).state == 'fresh'


@pytest.mark.parametrize('formal_exists', [False, True])
def test_old_v6_live_invalid_routes_to_renormalize_without_wire(
        tmp_path, formal_exists):
    """INVALID-only / formal+INVALID 都保留精确 legacy marker，且误敲先正常拒绝。"""
    from novel.orchestrator import state as orchestrator_state

    book = tmp_path / f'旧v6问题稿零成本刷新_{int(formal_exists)}'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, blueprint_transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(req, by_ordinal),
    )
    assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
    calls_before = len(blueprint_transport.calls)

    formal_path = book / extract.BLUEPRINT_REL
    raw = json.loads(formal_path.read_text(encoding='utf-8'))
    old_edge = raw['_provenance']['inputs'].pop(
        extract.BLUEPRINT_CHARACTER_REL)
    old_edge['file'] = prov.fingerprint_bytes(
        (book / extract.GLOBAL_REL).read_bytes())
    raw['_provenance']['inputs'][extract.GLOBAL_REL] = old_edge
    invalid_path = book / extract._INVALID
    invalid_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    if not formal_exists:
        formal_path.unlink()

    recovery_ctx = _ctx(book, 'stage2.blueprint', paid=False)
    store = ArtifactStore(recovery_ctx)
    invalid_freshness = store.freshness(extract._INVALID)
    assert (invalid_freshness.state, invalid_freshness.reason) == (
        'unknown', 'legacy_blueprint_inputs')
    state = orchestrator_state.artifact_state(store, extract.BLUEPRINT_REL)
    assert state.exists is formal_exists and state.invalid
    assert (state.invalid_freshness, state.invalid_reason) == (
        'unknown', 'legacy_blueprint_inputs')
    if formal_exists:
        assert (state.freshness, state.reason) == ('fresh', 'match')
    else:
        assert (state.freshness, state.reason) == (
            'unknown', 'no_provenance')

    before = invalid_path.read_bytes()
    with pytest.raises(TerminalState, match='stage2.blueprint --renormalize'):
        extract.run_revalidate(
            recovery_ctx, actor='agent:test', reason='错误入口应在写盘前拒绝')
    assert invalid_path.read_bytes() == before
    assert formal_path.exists() is formal_exists

    report = extract.run_renormalize(recovery_ctx)

    assert report.passed
    assert len(blueprint_transport.calls) == calls_before
    assert recovery_ctx.ledger.usage() is None
    assert formal_path.is_file() and not invalid_path.exists()
    refreshed = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert set(refreshed.provenance.inputs) == {
        extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL,
    }
    assert store.freshness(extract.BLUEPRINT_REL).state == 'fresh'


@pytest.mark.parametrize('formal_exists', [False, True])
def test_non_exact_legacy_invalid_never_borrows_the_renormalize_marker(
        tmp_path, formal_exists):
    """多一条未来来源边仍是通用 unknown，不能冒充精确旧键形。"""
    from contracts.schema.orchestration import StageState
    from novel.orchestrator import state as orchestrator_state

    book = tmp_path / f'非精确旧来源边_{int(formal_exists)}_4582'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, _transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(
            req, _response_blueprints()),
    )
    assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

    formal_path = book / extract.BLUEPRINT_REL
    raw = json.loads(formal_path.read_text(encoding='utf-8'))
    projection_edge = raw['_provenance']['inputs'][
        extract.BLUEPRINT_CHARACTER_REL]
    # 保留现役两边不动，并复制称名投影字节，让额外边的 file 指纹仍 match；它声明了 consumed，
    # 但当前 producer 没登记这条未来投影，因此 aggregate 仍是通用
    # unknown/consumed_unresolved，恰好覆盖曾被误认成 legacy 的形状。
    extra_rel = 'stage2/future_projection.json'
    (book / extra_rel).write_bytes(
        (book / extract.BLUEPRINT_CHARACTER_REL).read_bytes())
    raw['_provenance']['inputs'][extra_rel] = copy.deepcopy(projection_edge)
    invalid_path = book / extract._INVALID
    invalid_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    if not formal_exists:
        formal_path.unlink()

    recovery_ctx = _ctx(book, 'stage2.blueprint', paid=False)
    store = ArtifactStore(recovery_ctx)
    report = store.freshness(extract._INVALID)
    assert (report.state, report.reason) == (
        'unknown', 'consumed_unresolved')
    assert {edge.key for edge in report.edges} == {
        extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL, extra_rel,
    }
    blueprint_state = orchestrator_state.artifact_state(
        store, extract.BLUEPRINT_REL)
    projection_state = orchestrator_state.artifact_state(
        store, extract.BLUEPRINT_CHARACTER_REL)
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[projection_state, blueprint_state],
        done=False, reviewable=True)])
    assert (blueprint_state.invalid_freshness,
            blueprint_state.invalid_reason) == (
                'unknown', 'consumed_unresolved')
    assert not orchestrator_state.stage2_legacy_blueprint_recovery_needed(ov)
    assert orchestrator_state.stage2_blueprint_invalid_recovery_needed(ov)

    before = invalid_path.read_bytes()
    with pytest.raises(ProvenanceRefused) as refused:
        extract.run_revalidate(
            recovery_ctx, actor='agent:test', reason='通用未知不得借迁移 marker')
    assert '--renormalize' not in str(refused.value)
    assert invalid_path.read_bytes() == before
    assert formal_path.exists() is formal_exists


@pytest.mark.parametrize('invalid_carrier', [False, True])
def test_exact_legacy_keys_never_hide_a_changed_global_upstream(
        tmp_path, invalid_carrier):
    """键形虽旧且精确，Global 字节已漂移时仍必须保持真实 stale。"""
    from contracts.schema.orchestration import StageState
    from novel.orchestrator import state as orchestrator_state

    book = tmp_path / f'旧来源边上游漂移_{int(invalid_carrier)}_4583'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, _transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(
            req, _response_blueprints()),
    )
    assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

    formal_path = book / extract.BLUEPRINT_REL
    raw = json.loads(formal_path.read_text(encoding='utf-8'))
    old_edge = raw['_provenance']['inputs'].pop(
        extract.BLUEPRINT_CHARACTER_REL)
    old_edge['file'] = prov.fingerprint_bytes(
        (book / extract.GLOBAL_REL).read_bytes())
    raw['_provenance']['inputs'][extract.GLOBAL_REL] = old_edge
    source_path = book / (
        extract._INVALID if invalid_carrier else extract.BLUEPRINT_REL)
    source_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    if invalid_carrier:
        formal_path.unlink()

    # 仅改 JSON 空白，业务 payload 不变，但旧 Blueprint 记录的 Global 文件身份
    # 已明确漂移；兼容桥不能用 projection 当前值相同来改写这条历史事实。
    global_path = book / extract.GLOBAL_REL
    global_path.write_bytes(global_path.read_bytes() + b'\n')

    recovery_ctx = _ctx(book, 'stage2.blueprint', paid=False)
    store = ArtifactStore(recovery_ctx)
    report = store.freshness(
        extract._INVALID if invalid_carrier else extract.BLUEPRINT_REL)
    assert (report.state, report.reason) == ('stale', 'file_changed')

    blueprint_state = orchestrator_state.artifact_state(
        store, extract.BLUEPRINT_REL)
    projection_state = orchestrator_state.artifact_state(
        store, extract.BLUEPRINT_CHARACTER_REL)
    ov = SimpleNamespace(stages=[StageState(
        stage='stage2', artifacts=[projection_state, blueprint_state],
        done=False, reviewable=True)])
    assert not orchestrator_state.stage2_legacy_blueprint_recovery_needed(ov)
    verdict, why = orchestrator_state.resume_verdict(ov, 'stage2.blueprint')
    assert '--renormalize' not in why
    if invalid_carrier:
        assert (blueprint_state.invalid_freshness,
                blueprint_state.invalid_reason) == ('stale', 'file_changed')
        assert orchestrator_state.stage2_blueprint_invalid_recovery_needed(ov)
        assert verdict == orchestrator_state.RESUME_RUN
        assert '--revalidate' in why
    else:
        assert (blueprint_state.freshness,
                blueprint_state.reason) == ('stale', 'file_changed')
        assert verdict == orchestrator_state.RESUME_REDO


def _cause_for(rule: str) -> str:
    """反查「产地说哪句话会落到这条判据」——从 `HINGE_FAILURE_ROUTE` 现算，不手抄。"""
    causes = [c for c, r in HINGE_FAILURE_ROUTE.items() if r == rule]
    assert causes, f'{rule} 不在 `HINGE_FAILURE_ROUTE` 里 —— 它收不到任何根因'
    return causes[0]


def _one_beat(span: dict, hinges: list[dict]) -> list[dict]:
    return [{'chapter_ordinal': 1, 'beats': [
        {'beat_id': 'b01_01', 'span': span, 'hinges': hinges}]}]


def test_hinge_source_does_not_absorb_colon_or_unmatched_quote():
    """材料区间只接住**句末**闭合标点，不把冒号、开引号等下一段语义吞进来。

    ⚠ 这条实测教训跨过了架构改造：区间的两端现在由段号决定，
    但 hinge 的 `phrase` 仍要在段内逐字定位，`extend_terminal_punct` 那一格没变。
    """
    chapter = '他摸到冰冷链子：「别动！」'
    blueprint = _one_beat(
        {'start_para': 1, 'end_para': 1},
        [{'kind': '物件', 'para': 1, 'phrase': '冰冷链子'}])
    accounting = normalize_verbatim(blueprint, {1: chapter})
    source = blueprint[0]['beats'][0]['hinges'][0]['source']
    assert accounting['verbatim_failures'] == []
    assert source['text'] == '冰冷链子', '冒号与后面那句台词不该被吞进材料'
    assert chapter[source['start']:source['end']] == source['text']


@pytest.mark.parametrize(('chapter', 'para', 'phrase'), [
    (
        '说出这话的瞬间。\n\n温夏白脸上的笑意顿时僵住。\n\n'
        '师姐想了想：「哦，所以呢？」\n\n温夏白:「......」\n\n若不是现在看着这两人就烦。',
        4, '温夏白:「......」',
    ),
    (
        '我母妃心系百姓，却被父皇推出去做了罪人。\n\n她从城楼跳了下去。\n\n'
        '我的喉咙里只能发出呜咽。\n\n「母......」\n\n下一刻，有人蒙住我的眼睛。',
        4, '「母......」',
    ),
])
def test_hinge_phrase_is_located_inside_its_own_paragraph(chapter, para, phrase):
    """⭐⭐ **同名字面的消歧**：短语只在它自己那一段里找，不在整章里找。

    这两份样本原本守的是「标点是唯一消歧信息时不许先抹掉标点」——
    `温夏白:「......」` 在内容空间里会坍缩成 `温夏白`，而 `温夏白` 在第 2 段也出现过。
    `bounded-fidelity-v3` 把这一整类歧义**从结构上**消掉了：搜索面就是那一段。
    ⚠ 断言因此是**位置**而不只是字节：命中必须落在第 `para` 段里，
    而不是前面那个同名字面上——那正是旧实现踩过的坑。
    """
    blueprint = _one_beat(
        {'start_para': 1, 'end_para': len(paragraph_spans(chapter))},
        [{'kind': '要害台词', 'para': para, 'phrase': phrase}])

    accounting = normalize_verbatim(blueprint, {1: chapter})

    assert accounting['verbatim_failures'] == []
    source = blueprint[0]['beats'][0]['hinges'][0]['source']
    p_start, p_end = paragraph_spans(chapter)[para - 1]
    assert p_start <= source['start'] and source['end'] <= p_end, \
        '命中跑到别的段去了 —— 段内定位那道闸没生效'
    assert chapter[source['start']:source['end']] == source['text']
    assert phrase in source['text']


def test_a_phrase_that_exists_elsewhere_is_misplaced_not_invented():
    """⭐ 「短语在本章、只是不在它指的那一段」与「整章都没有」是**两种病**。

    说错了会让人去查一句原文里明明有的话。两者的根因分别落到
    `hinge_drift` 与 `hinge_anchor_void`（对应表见 `HINGE_FAILURE_ROUTE`）。
    """
    chapter = '甲推门进屋。\n\n乙抬头看了他一眼。\n\n他说了一句冷话。'
    misplaced = _one_beat(
        {'start_para': 1, 'end_para': 3},
        [{'kind': '要害台词', 'para': 1, 'phrase': '乙抬头看了他一眼'}])
    invented = _one_beat(
        {'start_para': 1, 'end_para': 3},
        [{'kind': '要害台词', 'para': 1, 'phrase': '这句话整章都没有'}])

    causes = {}
    for name, bp in (('misplaced', misplaced), ('invented', invented)):
        rows = normalize_verbatim(bp, {1: chapter})['verbatim_failures']
        assert len(rows) == 1 and rows[0][1] == 'hinge'
        causes[name] = rows[0][2]
        assert bp[0]['beats'][0]['hinges'][0]['source'] is None

    assert HINGE_FAILURE_ROUTE[causes['misplaced']] == 'hinge_drift'
    assert HINGE_FAILURE_ROUTE[causes['invented']] == 'hinge_anchor_void'


def test_every_cause_the_producer_emits_is_registered_in_the_routing_table():
    """⭐⭐ **产地只许说分流表里那几句话。**

    此前判据按中文子串猜根因（`'空档' in cause`），产地改一句措辞就能让
    `hinge_anchor_void` 静默降级成 `hinge_drift` 而报告照样绿。
    这一条把那条隐式契约变成会红的断言：**扫遍产地源码里所有 hinge 根因字面**，
    每一句都必须在 `HINGE_FAILURE_ROUTE` 里。
    ⚠ 扫的是源码而不是「跑几个样本看看」——样本覆盖不到的那一句正是会漏的那一句。
    """
    import ast
    import inspect

    from novel.stages.stage2 import compile_v2 as producer_v2
    from novel.stages.stage2 import normalize as producer

    emitted: set[str] = set()
    # ⚠ 产地有两个（v3 的 normalize_verbatim 与 ir1 的 _compile_beats），
    #   同一把扫描尺子逐个扫——漏扫一个产地，它的新根因就会静默落进 catch-all。
    for fn in (producer.normalize_verbatim, producer_v2._compile_beats):
        tree = ast.parse(inspect.getsource(fn))
        found = {
            node.args[0].elts[2].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == 'append'
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'failures'
            and isinstance(node.args[0], ast.Tuple) and len(node.args[0].elts) == 3
            and isinstance(node.args[0].elts[1], ast.Constant)
            and node.args[0].elts[1].value == 'hinge'
            and isinstance(node.args[0].elts[2], ast.Constant)
        }
        assert found, (
            f'AST 在 {fn.__qualname__} 一句都没抓到 —— 产地的写法变了，'
            f'这条守卫对它已经变成空验证')
        emitted |= found
    unknown = emitted - set(HINGE_FAILURE_ROUTE)
    assert not unknown, (
        f'产地发了分流表里没有的根因 {sorted(unknown)}\n'
        f'→ 判据会把它落进 catch-all 的 `hinge_drift`，而报告看起来一切正常')


def test_blueprint_context_keeps_current_full_hides_future_and_bounds_past():
    sections = []
    for ordinal in range(1, 7):
        text = (f'BEGIN-{ordinal}|' + chr(64 + ordinal) * 4_800
                + f'|TAIL-{ordinal}')
        if ordinal == 2:
            # 章首首现的名字：既进不了相邻尾窗也进不了尾摘，只有已现称名表带得进 user。
            text = '甲乙抱着包裹进了城。' + text
        if ordinal == 6:
            # 未来章才首现的名字：不许经名册绕过「未来不可见」。
            text = '丙丁掀帘进来。' + text
        sections.append({
            'chapter_ordinal': ordinal,
            'text': text,
        })
    characters = {
        'char_001': {'canonical': '甲乙',
                     'address': [{'surface': '甲乙', 'by': 'narration',
                                  'context': 'reference', 'from_chapter': 2}]},
        'char_002': {'canonical': '丙丁', 'address': []},
    }

    from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
    inventory = blueprint_character_prompt_inventory(characters, sections)
    user = extract.build_blueprint_user(
        '边界测试', sections, (5,),
        past_names=inventory['past_attested_surfaces'][5])
    prefix = extract._blueprint_stable_prefix('边界测试', sections)

    assert user.startswith(prefix)
    assert sections[4]['text'] in user
    assert sections[5]['text'] not in user
    assert 'private-id' not in user
    assert 'BEGIN-4' not in user and 'TAIL-4' in user
    assert 'BEGIN-3' not in user and 'TAIL-3' in user
    assert '此前章节已出现的称名（代码派生，仅过去）' in user
    assert '甲乙' in user
    assert '丙丁' not in user
    first = extract.build_blueprint_user(
        '边界测试', sections, (1,),
        past_names=inventory['past_attested_surfaces'][1])
    assert '无（此前正文未出现名册称名）' in first
    assert '甲乙' not in first
    block = extract._past_names_block(['名' * 300, '姓' * 300])
    assert '名' * 300 in block and '姓' not in block
    neighbor, continuity = extract._bounded_past(sections, 5)
    assert len(neighbor.split('\n', 1)[1]) == extract.PAST_NEIGHBOR_CHARS_MAX
    assert len(continuity) <= extract.CONTINUITY_CHARS_MAX
    many_short = [
        {'chapter_ordinal': ordinal, 'text': '短'}
        for ordinal in range(1, 221)
    ]
    _neighbor, short_continuity = extract._bounded_past(many_short, 220)
    assert len(short_continuity) <= extract.CONTINUITY_CHARS_MAX
    context = extract.blueprint_context('边界测试', sections)
    assert context['stable_prefix'] == extract.prov.fingerprint(prefix)
    assert context['past_names_chars_max'] == extract.PAST_NAMES_CHARS_MAX


def test_past_attested_surfaces_only_hand_out_past_attested_forms():
    from novel.stages.stage2.normalize import past_attested_surfaces
    table = past_attested_surfaces(
        global_grounding()['entities']['characters'], main_text())
    assert table[1] == []
    assert table[2] == ['李宝珠']
    # 「沈母」只是 canonical、正文里从未出现过 ⇒ 不进清单；「母亲」按首现先后排在后面。
    assert table[3] == ['李宝珠', '母亲']


def test_stage2_renormalize_rejects_locator_shaped_formal_artifact():
    root = Path(tempfile.mkdtemp())
    book = root / '非法蓝图重归一测试_460'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _ = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, transport = _client(blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed
        path = book / extract.BLUEPRINT_REL
        raw = json.loads(path.read_text(encoding='utf-8'))
        texts = {section['chapter_ordinal']: section['text'] for section in main_text()}
        raw['blueprints'] = [canonical_ir_projection_v31(
            bp, texts[bp['chapter_ordinal']])
                             for bp in raw['blueprints']]
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')
        calls_before = len(transport.calls)

        with pytest.raises(ArtifactUnreadable):
            extract.run_renormalize(_ctx(book, 'stage2.blueprint', paid=False))
        assert len(transport.calls) == calls_before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage2_targeted_regeneration_refreshes_one_chapter_and_merges_book():
    root = Path(tempfile.mkdtemp())
    book = root / '定向重生成测试_459'
    book.mkdir()
    try:
        global_ctx = _ctx(book, 'stage2.global')
        ArtifactStore(global_ctx).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        global_client, _global_transport = _client(
            global_ctx, lambda _req, _index: _ok(_response_global()))
        extract.run_global(global_ctx, global_client)

        by_ordinal = _response_blueprints()

        def respond(req: ChatRequest, _index: int) -> ChatResponse:
            return _respond_blueprints(req, by_ordinal)

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, _blueprint_transport = _client(
            blueprint_ctx, respond)
        assert extract.run_blueprint(blueprint_ctx, blueprint_client).passed

        store = ArtifactStore(blueprint_ctx)
        previous, raw = store.read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        broken = copy.deepcopy(raw)
        hinge = broken['blueprints'][0]['beats'][0]['hinges'][0]
        hinge['source'] = None
        # ⚠ 根因从分流表现取：手抄一句中文会静默落进 catch-all 的 `hinge_drift`，
        #   而下面那条 `failure_items` 断言看起来照样在测「修复路由带上了哪几条」。
        broken['_provenance']['verbatim_failures'] = [
            ['b01_01', 'hinge', _cause_for('hinge_anchor_void')]]
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )

        repair_ctx = _ctx(book, 'stage2.blueprint')
        repair_ctx.cache = CachePolicy.refresh()
        repair_client, repair_transport = _client(repair_ctx, respond)
        report = extract.run_blueprint(
            repair_ctx, repair_client, chapters=(1,))

        assert report.passed
        assert len(repair_transport.calls) == 1
        assert '上一版结构基线' in repair_transport.calls[0].user
        assert '上一版必须修正的问题' in repair_transport.calls[0].user
        assert 'b01_01' in repair_transport.calls[0].user
        assert len(_blueprint_transport.calls) == len(main_text())
        stored = store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
        assert 'repairs' not in stored.provenance.model_dump()
        assert stored.provenance.usage is not None
        assert stored.provenance.usage.calls_made == 4
        assert not (book / extract._INVALID).exists()
        assert previous.provenance.usage is not None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_targeted_refresh_drains_two_chapters_and_resumes_once_from_checkpoints(
        tmp_path, monkeypatch):
    """定向 batch 的 formal→committed 尾窗可同 run、零 wire 幂等收口。"""
    book = tmp_path / '定向批量恢复_461'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _global_transport = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    baseline_ctx = _ctx(book, 'stage2.blueprint')
    baseline_client, _baseline_transport = _client(
        baseline_ctx, lambda req, _index: _respond_blueprints(req, by_ordinal))
    assert extract.run_blueprint(baseline_ctx, baseline_client).passed

    run_id = new_run_id()
    ledger_path = tmp_path / 'refresh-ledger.jsonl'

    def refresh_ctx(*, events=None):
        return RunContext(
            novel_dir=book,
            node='stage2.blueprint',
            paid=True,
            code='test-stage2-generation',
            cache=CachePolicy.refresh(),
            ledger=CostLedger(path=ledger_path, run_id=run_id),
            run_id=run_id,
            repair_rejected_responses=True,
            observer=(events.append if events is not None else lambda _row: None),
        )

    first = refresh_ctx()

    def first_wave(req: ChatRequest, _index: int) -> ChatResponse:
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        if ordinal == 1:
            return _ok({'blueprints': [by_ordinal[ordinal]]})
        return ChatResponse(
            content='{"blueprints":[}', finish_reason='stop', model='test-model')

    first_client, first_transport = _client(first, first_wave)
    with pytest.raises(ResponseRepairBatchRequired) as paused:
        extract.run_blueprint(first, first_client, chapters=(1, 2))

    assert len(first_transport.calls) == 2
    manifest = Path(paused.value.manifest_path)
    assert paused.value.run_id == run_id
    assert len(paused.value.attempts) == 1
    assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, run_id, 'repair', 1).is_file()
    assert not stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, run_id, 'repair', 2).exists()

    premature = refresh_ctx()
    premature_client, premature_transport = _client(
        premature,
        lambda _req, _index: AssertionError(
            'batch resolution 落盘前不得把同 run 再执行冒充 resume'))
    with pytest.raises(ProvenanceRefused, match='batch resolution'):
        extract.run_blueprint(
            premature, premature_client, chapters=(1, 2))
    assert premature_transport.calls == []

    attempt_id = paused.value.attempts[0]
    record = response_repair.find_attempt(book, attempt_id)
    response_repair.candidate_path(record).write_text(
        json.dumps({'blueprints': [by_ordinal[2]]}, ensure_ascii=False),
        encoding='utf-8')
    decisions = tmp_path / 'targeted-refresh-decisions.json'
    decisions.write_text(json.dumps({'decisions': [{
        'attempt_id': attempt_id,
        'action': 'submit',
        'reason': '候选完整保留原响应中的第二章结构，只修复 JSON 容器',
    }]}, ensure_ascii=False), encoding='utf-8')
    resolution = stage2_runs.submit_batch(
        book, manifest, decisions, actor='operator')

    events: list[dict[str, object]] = []
    resumed = refresh_ctx(events=events)
    resumed_client, resumed_transport = _client(
        resumed,
        lambda _req, _index: AssertionError(
            '成功章 checkpoint 与 submit candidate 都不应新增 wire'))
    real_mark = stage2_runs.mark_committed

    def crash_before_sentinel(*_args, **_kwargs):
        raise RuntimeError('synthetic targeted crash before committed sentinel')

    monkeypatch.setattr(stage2_runs, 'mark_committed', crash_before_sentinel)
    with pytest.raises(RuntimeError, match='targeted crash'):
        extract.run_blueprint(resumed, resumed_client, chapters=(1, 2))
    monkeypatch.setattr(stage2_runs, 'mark_committed', real_mark)

    assert resumed_transport.calls == []
    assert [event['chapter'] for event in events
            if event.get('event') == 'checkpoint_restored'] == [1]
    assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, run_id, 'repair', 2).is_file()
    assert list(manifest.parent.glob(stage2_runs.MANIFEST_FILE)) == [manifest]
    assert list(manifest.parent.glob(stage2_runs.RESOLUTION_FILE)) == [resolution]
    assert stage2_runs.committed(resumed) is None
    assert stage2_runs.commit_state(resumed)['state'] == 'prepared'
    crash_store = ArtifactStore(resumed)
    formal, _formal_raw = crash_store.read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    crash_store.write_invalid(
        extract.BLUEPRINT_REL, formal, gates_skipped=[])
    assert crash_store.exists(extract._INVALID)
    rounds = book / 'stage2' / '_rounds'
    before_rounds = sorted(
        path.name for path in rounds.glob('chapter_blueprints.r*.json'))
    audit_path = ledger_path.with_name(AUDIT_REL[-1])
    before_adoptions = [
        row for row in map(json.loads, audit_path.read_text('utf-8').splitlines())
        if row.get('kind') == 'artifact_adoption'
    ]
    assert len(before_adoptions) == 2

    finalized = refresh_ctx()
    finalized_client, finalized_transport = _client(
        finalized,
        lambda _req, _index: AssertionError(
            'formal 已落的 commit 尾窗不得重建 prompt 或新增 wire'))
    report = extract.run_blueprint(
        finalized, finalized_client, chapters=(1, 2))

    assert report.passed and finalized_transport.calls == []
    assert stage2_runs.committed(finalized) is not None
    assert not ArtifactStore(finalized).exists(extract._INVALID)
    assert list(rounds.glob('chapter_blueprints.INVALID.r*.json'))
    assert sorted(path.name for path in rounds.glob(
        'chapter_blueprints.r*.json')) == before_rounds
    after_adoptions = [
        row for row in map(json.loads, audit_path.read_text('utf-8').splitlines())
        if row.get('kind') == 'artifact_adoption'
    ]
    assert after_adoptions == before_adoptions, 'commit 尾窗不得重复 adoption'


def test_targeted_v2_refresh_cannot_open_a_second_batch_in_detail_phase(
        tmp_path, monkeypatch):
    """v2 定向重切边界与细节相共享 refresh run 的唯一人工 workpack。"""
    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v2')
    book = tmp_path / '定向v2单批次_462'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _global_transport = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    texts = {row['chapter_ordinal']: row['text'] for row in main_text()}
    by_ordinal = {
        row['chapter_ordinal']: canonical_ir_projection_refs(
            row, texts[row['chapter_ordinal']])
        for row in copy.deepcopy(blueprints())['blueprints']
    }

    def boundary_payload(ordinal: int) -> dict:
        rows = [
            {
                'end_para': beat['end_para'],
                'event': '；'.join(
                    atom['claim'] for atom in beat['fact_atoms']),
            }
            for beat in by_ordinal[ordinal]['beats']
        ]
        return {'blueprints': [{
            'chapter_ordinal': ordinal,
            'micro_events': rows,
            'beats': rows,
        }]}

    def baseline_response(req: ChatRequest, _index: int) -> ChatResponse:
        ordinal = int(re.findall(r'阅读序第(\d+)章', req.user)[-1])
        if '第一张表 `micro_events`' in req.system:
            return _ok(boundary_payload(ordinal))
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    baseline_ctx = _ctx(book, 'stage2.blueprint')
    baseline_client, _baseline_transport = _client(
        baseline_ctx, baseline_response)
    assert extract.run_blueprint(baseline_ctx, baseline_client).passed

    # 真实问题路由只决定是否重切边界；测试关心的是两相恢复状态机，不复制
    # `beats_collapsed` 判据的输入构造。
    monkeypatch.setattr(extract, '_repair_findings', lambda *_args: [{
        'rule': 'beats_collapsed',
        'item': 'b01',
        'detail': '合成问题要求重新切分边界',
    }])

    run_id = new_run_id()
    ledger_path = tmp_path / 'v2-refresh-ledger.jsonl'

    def refresh_ctx():
        return RunContext(
            novel_dir=book,
            node='stage2.blueprint',
            paid=True,
            code='test-stage2-generation',
            cache=CachePolicy.refresh(),
            ledger=CostLedger(path=ledger_path, run_id=run_id),
            run_id=run_id,
            repair_rejected_responses=True,
            observer=lambda _row: None,
        )

    first = refresh_ctx()
    first_client, first_transport = _client(
        first,
        lambda _req, _index: ChatResponse(
            content='{"blueprints":[}', finish_reason='stop', model='test-model'))
    with pytest.raises(ResponseRepairBatchRequired) as paused:
        extract.run_blueprint(first, first_client, chapters=(1,))
    assert len(first_transport.calls) == 1
    manifest = Path(paused.value.manifest_path)
    manifest_before = manifest.read_bytes()

    attempt_id = paused.value.attempts[0]
    record = response_repair.find_attempt(book, attempt_id)
    response_repair.candidate_path(record).write_text(
        json.dumps(boundary_payload(1), ensure_ascii=False), encoding='utf-8')
    decisions = tmp_path / 'targeted-v2-decisions.json'
    decisions.write_text(json.dumps({'decisions': [{
        'attempt_id': attempt_id,
        'action': 'submit',
        'reason': '候选忠实保留边界表，仅修复 JSON 容器',
    }]}, ensure_ascii=False), encoding='utf-8')
    resolution = stage2_runs.submit_batch(
        book, manifest, decisions, actor='operator')

    resumed = refresh_ctx()
    resumed_client, resumed_transport = _client(
        resumed,
        lambda _req, _index: ChatResponse(
            content='{"blueprints":[}', finish_reason='stop', model='test-model'))
    with pytest.raises(LlmFailed):
        extract.run_blueprint(resumed, resumed_client, chapters=(1,))

    assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, run_id, 'repair-boundaries', 1).is_file()
    assert not stage2_runs.paths.stage2_blueprint_checkpoint_path(
        book, run_id, 'repair', 1).exists()
    assert resumed_transport.calls, 'detail 相必须真实进入自动恢复而非提前返回'
    assert manifest.read_bytes() == manifest_before
    assert list(manifest.parent.glob(stage2_runs.MANIFEST_FILE)) == [manifest]
    assert list(manifest.parent.glob(stage2_runs.RESOLUTION_FILE)) == [resolution]
