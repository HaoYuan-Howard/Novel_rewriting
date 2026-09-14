#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 生成侧的零成本闭环测试。"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

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
from contracts.schema.knowledge import KnowledgeResponse as GlobalGroundingCandidateResponse
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.plot import PlotBlueprintResponse
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
from novel.run import RunContext
from novel.run_identity import new_run_id
from novel.stages.stage2 import extract
from novel.stages.stage2.normalize import (
    EvidenceLocatorCorrection, normalize_global,
)
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.store import ArtifactStore
from tests.test_stage2 import global_grounding, main_text
from tests.stage2_fixtures import plot_blueprints as blueprints


#: 通用生成与 Global 消费链使用当前契约；退役响应在入口拒绝测试中单独举证。
@pytest.fixture(autouse=True)
def _pin_plot_contract(monkeypatch):
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
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
        ctx, Client(), requests, model_cls=PlotBlueprintResponse)

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
        ctx, Client(), requests, model_cls=PlotBlueprintResponse)

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
        model_cls=PlotBlueprintResponse)

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
        ctx, Client(), requests, model_cls=PlotBlueprintResponse)
    assert len(results) == chapters
    assert violations == [] and errors == []
    assert peak == expected_peak


def _response_global() -> dict:
    """Global provider 只返回必要知识；静态人物输入由独立夹具明确提供。"""
    from tests.stage2_fixtures import knowledge_response
    return knowledge_response()


def formal_grounding() -> dict:
    """当前 KnowledgeGrounding 正式形状；与 `_response_global` 同一编译路径。"""
    from tests.stage2_fixtures import formal_subjects
    _registry, knowledge = formal_subjects(view=None)
    return knowledge.model_dump(mode='json', by_alias=True)


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
    assert len(_ALL_GLOBAL_EVIDENCE_PATHS) == 12
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


@pytest.mark.parametrize(('branch', 'draft_message', 'formal_message'), [
    ('secret', 'secret_ref 局部句柄不得重复', 'arc_secret 正式 ID 不得重复'),
    ('world_rule', 'rule_ref 局部句柄不得重复', 'world_rule 正式 ID 不得重复'),
    ('relation', 'relation_ref 局部句柄不得重复', 'relation 正式 ID 不得重复'),
])
def test_global_response_and_formal_artifact_reject_duplicate_stable_ids(
    branch: str, draft_message: str, formal_message: str,
):
    """patch/apply-edit 按稳定 ID 定位；重复 ID 不能退化成“最后一条生效”。"""
    response = _response_global()
    formal = formal_grounding()
    if branch == 'secret':
        response['arc_secrets'].append(copy.deepcopy(response['arc_secrets'][0]))
        formal['arc_secrets'].append(copy.deepcopy(formal['arc_secrets'][0]))
    elif branch == 'world_rule':
        response['world_rules'].append(copy.deepcopy(response['world_rules'][0]))
        formal['world_rules'].append(copy.deepcopy(formal['world_rules'][0]))
    else:
        response['relations'].append(copy.deepcopy(response['relations'][0]))
        formal['relations'].append(copy.deepcopy(formal['relations'][0]))

    with pytest.raises(ValidationError, match=draft_message):
        GlobalGroundingCandidateResponse.model_validate(response)
    with pytest.raises(ValidationError, match=formal_message):
        GlobalGrounding.model_validate(formal)


def test_global_response_rejects_order_dependent_timelines_and_lossy_values():
    cases: list[dict] = []

    duplicate_version_ref = _response_global()
    versions = duplicate_version_ref['world_rules'][0]['versions']
    versions.append(copy.deepcopy(versions[0]))
    cases.append(duplicate_version_ref)

    reversed_window = _response_global()
    reversed_window['relations'][0]['effective_from'] = 2
    reversed_window['relations'][0]['effective_until'] = 1
    cases.append(reversed_window)

    string_chapter = _response_global()
    string_chapter['arc_secrets'][0]['revealed_from'] = '1'
    cases.append(string_chapter)

    bool_chapter = _response_global()
    bool_chapter['relations'][0]['effective_from'] = True
    cases.append(bool_chapter)

    float_chapter = _response_global()
    float_chapter['world_rules'][0]['versions'][0]['effective_from'] = 1.0
    cases.append(float_chapter)

    for payload in cases:
        with pytest.raises(ValidationError):
            GlobalGroundingCandidateResponse.model_validate(payload)

    # 铺垫章不得晚于读者揭示章是正式载体的校验；草稿层不重复这把尺子。
    setup_after_reveal = formal_grounding()
    setup_after_reveal['arc_secrets'][0]['setup_chapters'] = [2]
    setup_after_reveal['arc_secrets'][0]['revealed_from'] = 1
    with pytest.raises(ValidationError, match='铺垫章不得晚于读者揭示章'):
        GlobalGrounding.model_validate(setup_after_reveal)


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

    _formal, formal_raw = grounding_io.read_own(store, extract.GLOBAL_REL)
    old_invalid = copy.deepcopy(formal_raw)
    old_invalid['_provenance']['evidence_locator_failures'] = [[
        'arc_secrets.secret_01.fact', 1,
        '信上说父亲已经回城。',
        'locator 在指定章内 0 次；旧契约未做跨章唯一回取',
    ]]
    store.write_invalid(
        extract.GLOBAL_REL,
        grounding_io.for_write(store, GlobalGrounding.model_validate(old_invalid)),
        gates_skipped=[])

    invalid_path = book / extract._GLOBAL_INVALID
    raw = json.loads(invalid_path.read_text(encoding='utf-8'))
    failure = next(
        row for row in raw['_provenance']['evidence_locator_failures']
        if row[0] == 'arc_secrets.secret_01.fact')
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
    landed = grounding_io.read(ArtifactStore(recovery_ctx))
    assert landed.provenance.evidence_locator_failures == []
    assert not [row for row in recovery_ctx.ledger.audit_events
                if row['kind'] == 'evidence_locator_rewrite'], \
        '新形状没有可回取的 locator；提升是纯复判，不产生改写账'


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
    _formal, formal_raw = grounding_io.read_own(store, extract.GLOBAL_REL)
    formal_before = (book / extract.GLOBAL_REL).read_bytes()

    stale = copy.deepcopy(formal_raw)
    stale.pop('_provenance')
    ref = stale['arc_secrets'][0]['evidence_refs'][0]
    anchor = stale['evidence_anchors'][ref]
    assert anchor['chapter_ordinal'] == 1
    # 把秘密证据锚点漂到别处：坐标对不上当前正文，恢复不得把它洗成 locator。
    stale['evidence_anchors'][ref] = {**anchor, 'chapter_ordinal': 2}

    artifact = GlobalGrounding.model_validate({
        **stale,
        '_provenance': {
            **formal_raw['_provenance'],
            'evidence_locator_failures': [],
        },
    })
    store.write_invalid(extract.GLOBAL_REL, grounding_io.for_write(store, artifact), gates_skipped=[])

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
    """独立合成原文的当前关闭式响应。"""
    from tests.test_plot_producer import responses
    return responses()


def _respond_blueprints(req: ChatRequest, by_ordinal: dict[int, dict]) -> ChatResponse:
    ordinal = json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']
    return _ok({'blueprints': [by_ordinal[ordinal]]})


def _ctx(book: Path, node: str, *, paid: bool = True) -> RunContext:
    from novel.work_units import require_workflow
    from novel.llm.ledger import ledger_path
    require_workflow(book.parent)
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True, exist_ok=True)
    if not clean.exists():
        clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    ctx = RunContext(
        novel_dir=book,
        node=node,
        paid=paid,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(book.parent)),
    )
    from contracts.artifacts import STAGE1_HOOK_COPY
    from contracts.schema.stage1 import HookCopy
    store = ArtifactStore(ctx)
    if not store.exists(STAGE1_HOOK_COPY):
        store.write(STAGE1_HOOK_COPY, HookCopy(text='李宝珠收到父亲已经回城的来信。'))
    # 生成用例以当前人物登记为前置；免费恢复 context 不替 missing 上游造数据。
    from contracts.artifacts import STAGE2_CHARACTERS
    if paid and node == 'stage2.global' and not store.exists(STAGE2_CHARACTERS):
        from tests.stage2_fixtures import seed_character_registry
        if not store.exists(extract.MAIN_TEXT):
            store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        seed_character_registry(ctx)
    return ctx


def _edit_static_character(book: Path, char_id: str, **changes):
    from contracts.schema.characters import CharactersEdit
    from novel.stages.stage2 import characters
    ctx = _ctx(book, 'stage2.characters', paid=False)
    store = ArtifactStore(ctx)
    current, sha = store.read_own_with_sha256(
        characters.CharacterRegistry, characters.REL, require_current=True)
    value = current.characters[char_id].model_dump(mode='json')
    value.update(changes)
    if 'canonical' in changes and 'canonical' not in {
            row['surface'] for row in value['aliases']}:
        # 载体要求 aliases 恰好包含 canonical surface 一次；改名时把首个
        # 别名连归属证据一起重绑到新字面在正文中的唯一命中。
        canonical = changes['canonical']
        value['aliases'][0]['surface'] = canonical
        sections = store.read(MainText, 'stage1/main_text.json').root
        section = next(s for s in sections if canonical in s.text)
        value['aliases'][0]['evidence_refs'] = [{
            'chapter_ordinal': section.chapter_ordinal, 'locator': canonical}]
        value['aliases'][0]['identity_revealed_from'] = section.chapter_ordinal
    return characters.apply_edit(ctx, CharactersEdit.model_validate({
        'source_sha256': sha,
        'operations': [{'kind': 'replace', 'char_id': char_id, 'character': value}],
    }), actor='test-reviewer', reason='按当前人物契约修改独立登记')


def _accept_fixture_global(ctx: RunContext) -> None:
    """Stage2 语义审阅退役后无需显式接受；保留调用点作机械准入的前置占位。"""
    return None


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


def _stopped_blueprint_request(tmp_path, monkeypatch, *, reasoning=False):
    """已购响应落 checkpoint、尚未进入 prepared 的窗口；恢复必须靠原账与原请求。"""
    from tests.test_plot_producer import prepare_book
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    rows = _response_blueprints()
    def answer(req, _index):
        ordinal = json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']
        return (_reasoning_ok if reasoning else _ok)({'blueprints': [rows[ordinal]]})
    client, wire = _client(ctx, answer)
    with monkeypatch.context() as patch:
        def stop(*_args, **_kwargs):
            raise RuntimeError('响应已经检查，准备聚合前中断')
        patch.setattr(extract, 'finish_blueprints', stop)
        with pytest.raises(RuntimeError, match='准备聚合前'):
            extract.run_blueprint(ctx, client)
    assert len(wire.calls) == len(main_text())
    assert not ArtifactStore(ctx).exists(extract.BLUEPRINT_REL)
    return ctx, wire


def _resume_stopped_blueprint(previous):
    resumed = _disk_blueprint_ctx(previous.novel_dir, previous.ledger.path,
                                  paid=True, run_id=previous.run_id)
    client, wire = _client(resumed, lambda *_args: pytest.fail('已有 checkpoint 不得重买'))
    return resumed, client, wire


@pytest.mark.parametrize('reasoning', [False, True])
def test_blueprint_verified_response_recovers_before_prepare_without_wire(tmp_path, monkeypatch, reasoning):
    ctx, first_wire = _stopped_blueprint_request(tmp_path, monkeypatch, reasoning=reasoning)
    resumed, client, wire = _resume_stopped_blueprint(ctx)
    assert extract.run_blueprint(resumed, client).passed
    assert wire.calls == []
    artifact = ArtifactStore(resumed).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    assert artifact.provenance.usage.calls_made == len(first_wire.calls) == 3
    assert [row.chapter_ordinal for row in artifact.blueprints] == [1, 2, 3]


@pytest.mark.parametrize('tamper', [
    'checkpoint_hash', 'ledger_identity', 'accepted_request', 'purpose_binding',
])
def test_blueprint_unclosed_checkpoint_refuses_recovery_before_wire(tmp_path, monkeypatch, tamper):
    ctx, _first = _stopped_blueprint_request(tmp_path, monkeypatch)
    checkpoint = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'blueprint', 1)
    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    if tamper == 'checkpoint_hash':
        raw['response_payload_sha256'] = '0' * 64
    elif tamper == 'ledger_identity':
        raw['request_fingerprint'] = 'f' * 64
        raw['attempt_links'][0]['request_fingerprint'] = 'f' * 64
        raw['accepted_request']['request_fingerprint'] = 'f' * 64
        raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(raw)
    elif tamper == 'accepted_request':
        raw['accepted_request']['user_sha256'] = 'f' * 64
        raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(raw)
    else:
        raw['purpose'] = 'stage2.blueprint:repair:ch1'
        raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(raw)
    checkpoint.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before = checkpoint.read_bytes()
    resumed, client, wire = _resume_stopped_blueprint(ctx)
    with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
        extract.run_blueprint(resumed, client)
    assert not wire.calls
    assert checkpoint.read_bytes() == before
    assert not ArtifactStore(resumed).exists(extract.BLUEPRINT_REL)


@pytest.mark.parametrize('resigned_checkpoint', [False, True])
def test_blueprint_reasoning_checkpoint_requires_original_answer_lost_evidence(
        tmp_path, monkeypatch, resigned_checkpoint):
    """同一 JSON 不等于同一原始回答；重签 checkpoint 不能替换 reasoning 留证。"""
    ctx, _first = _stopped_blueprint_request(tmp_path, monkeypatch, reasoning=True)
    checkpoint = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'blueprint', 1)
    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert raw['accepted_channel'] == 'reasoning'
    evidence = stage2_runs.paths.answer_lost_path(ctx.novel_dir, raw['attempt_id'])
    assert evidence.is_file()
    if resigned_checkpoint:
        raw['accepted_text'] += '\n'
        raw['accepted_text_sha256'] = hashlib.sha256(raw['accepted_text'].encode('utf-8')).hexdigest()
        raw['checkpoint_sha256'] = stage2_runs._checkpoint_hash(raw)
        checkpoint.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    resumed, client, wire = _resume_stopped_blueprint(ctx)
    if resigned_checkpoint:
        with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
            extract.run_blueprint(resumed, client)
        assert not ArtifactStore(resumed).exists(extract.BLUEPRINT_REL)
    else:
        assert extract.run_blueprint(resumed, client).passed
    assert not wire.calls


def test_blueprint_missing_checkpoint_cannot_borrow_other_matching_payloads(tmp_path, monkeypatch):
    """只接受已登记 run/章路径；内容相同的旁路副本不填补依赖链缺口。"""
    ctx, _first = _stopped_blueprint_request(tmp_path, monkeypatch)
    checkpoint = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'blueprint', 1)
    original = checkpoint.read_bytes()
    for suffix in ('copy_a.json', 'copy_b.json'):
        checkpoint.with_name(suffix).write_bytes(original)
    checkpoint.unlink()
    resumed, client, wire = _resume_stopped_blueprint(ctx)
    with pytest.raises(ProvenanceRefused, match='缺检查点'):
        extract.run_blueprint(resumed, client)
    assert not wire.calls
    assert not ArtifactStore(resumed).exists(extract.BLUEPRINT_REL)


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
        _accept_fixture_global(global_ctx)

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
        assert context.architecture == 'plot-blueprint-v1'
        assert context.source_format == 'numbered-paragraphs-v1'
        from novel.stages.stage2.plot_compile import validate_evidence
        for blueprint in stored.blueprints:
            validate_evidence(blueprint, main_text()[blueprint.chapter_ordinal - 1]['text'])
        assert not ArtifactStore(blueprint_ctx).exists(extract._INVALID)

        # 代码派生坐标损坏产生 INVALID 后，免费重算须归档问题稿，保留剧情身份。
        store = ArtifactStore(blueprint_ctx)
        _previous, formal_raw = store.read_own(
            ChapterBlueprints, extract.BLUEPRINT_REL)
        broken = copy.deepcopy(formal_raw)
        broken['blueprints'][0]['evidence_anchors'][0]['start'] += 1
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )
        assert store.exists(extract._INVALID)

        # 一次 RunContext 只代表一次节点执行。上面的报告读取已经在旧 ctx
        # 记入了正式产物边；完整重跑必须像真实 CLI 子进程一样开全新上下文。
        rerun_ctx = _ctx(book, 'stage2.blueprint', paid=False)
        rerun_report = extract.run_renormalize(rerun_ctx)
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
        _accept_fixture_global(global_ctx)

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
        adopted = [row for row in audit if row.get('kind') == 'artifact_adoption'
                   and row.get('artifact') == 'stage2/chapter_blueprints.json']
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
        _accept_fixture_global(global_ctx)

        stale_formal_bytes = None
        if stale_formal:
            old_ctx = _ctx(book, 'stage2.blueprint')
            old_client, _old_transport = _client(
                old_ctx,
                lambda req, _index: _respond_blueprints(
                    req, _response_blueprints()),
            )
            extract.run_blueprint(old_ctx, old_client)
            _edit_static_character(book, 'char_001', canonical='宝珠')
            assert extract.run_renormalize(
                _ctx(book, 'stage2.global', paid=False)).passed
            assert ArtifactStore(_ctx(book, 'stage2.blueprint')).freshness(
                extract.BLUEPRINT_REL).state == 'stale'
            stale_formal_bytes = (book / extract.BLUEPRINT_REL).read_bytes()
            _accept_fixture_global(global_ctx)
            probe = _ctx(book, 'stage2.blueprint')
            client, wire = _client(probe, lambda *_args: pytest.fail('已有主体不得首次重抽覆盖'))
            with pytest.raises(TerminalState, match='已有完整剧情蓝图'):
                extract.run_blueprint(probe, client)
            assert not wire.calls
            assert (book / extract.BLUEPRINT_REL).read_bytes() == stale_formal_bytes
            return

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
        assert grounding_io.read(store) == artifact
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
        bad['arc_secrets'][0]['revealed_from'] = 99

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
        assert 'canon_chapter_refs_dangling' in rejected.hard_hits
        feedback = rejected.feedback_rows()
        assert any(row['rule'] == 'canon_chapter_refs_dangling' for row in feedback)
        assert not (book / extract.GLOBAL_REL).exists()
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert not (book / extract.GLOBAL_REVIEW_REL).exists()
        assert not list((book / 'stage2/_rounds').glob('global_grounding*.json'))

        # 模拟同一 logical session 消费代码反馈后交回完整 replacement JSON。
        # user 加稳定反馈摘要只为让旧 LlmClient 测试缝产生第二次 wire；正式 agent
        # 会由 SessionAgentClient.resume 复用 provider session。
        second = client.ask_json(
            prepared.system,
            prepared.user + '\n\n代码反馈：canon_chapter_refs_dangling，请交回完整 JSON。',
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
        assert not (book / extract.GLOBAL_REVIEW_REL).exists()
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

        evaluation._response.relations[0].predicate = '评估后篡改的关系'
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
        bad['arc_secrets'][0]['revealed_from'] = 99
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

        with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
            extract.finalize_global_candidate(
                ctx, client, prepared, second)

        assert len(transport.calls) == 1
        assert (book / extract._GLOBAL_INVALID).is_file()
        assert not (book / extract.GLOBAL_REL).exists()
        assert not (book / extract.GLOBAL_REVIEW_REL).exists()

        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        blueprint_client, blueprint_transport = _client(
            blueprint_ctx,
            lambda req, _index: _respond_blueprints(
                req, _response_blueprints()),
        )
        # 知识终态拒绝后没有当前正式稿：Blueprint 在任何 wire 前显式缺上游。
        with pytest.raises(UpstreamMissing):
            extract.run_blueprint(blueprint_ctx, blueprint_client)
        assert blueprint_transport.calls == [], '知识未接受必须在任何 Blueprint wire 前停止'
        assert not list((book / 'stage2/_rounds').glob(
            'global_grounding.INVALID.r*.json'))
        invalid = grounding_io.read_own(ArtifactStore(ctx), extract._GLOBAL_INVALID, require_current=True)[0]
        assert invalid.provenance.usage is not None
        assert invalid.provenance.usage.calls_made == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_non_name_global_hard_keeps_projection_but_blocks_blueprint_before_wire(
        tmp_path):
    """称名投影可供诊断；任何 Global HARD 都不能越过独立接受闸生产 Blueprint。"""
    book = tmp_path / 'Global问题稿阻止蓝图_4531'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(global_ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    bad = _response_global()
    bad['arc_secrets'][0]['revealed_from'] = 99
    global_client, global_transport = _client(
        global_ctx, lambda _req, _index: _ok(bad))

    with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
        extract.run_global(global_ctx, global_client)

    assert len(global_transport.calls) == 1
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)

    by_ordinal = _response_blueprints()
    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, blueprint_transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(req, by_ordinal),
    )
    # 非称名 HARD 落成 INVALID 后没有当前正式稿：Blueprint 在 wire 前显式缺上游。
    with pytest.raises(UpstreamMissing):
        extract.run_blueprint(blueprint_ctx, blueprint_client)

    assert blueprint_transport.calls == [], '非称名 HARD 同样必须在 Blueprint wire 前停止'
    assert not store.exists(extract.BLUEPRINT_REL)
    assert not store.exists(extract._INVALID)
    assert store.exists(extract._GLOBAL_INVALID)


def test_name_change_with_interrupted_projection_write_closes_blueprint_and_stage4(
        tmp_path, monkeypatch):
    """人物登记已换名而投影仍旧：两个真实 consumer 都必须关闭，且 0 wire。"""
    book = tmp_path / '称名投影中断关闭_4532'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(global_ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

    from contracts.schema.characters import CharacterRegistry
    from novel.stages.stage2 import characters
    raw = store.read_own(CharacterRegistry, characters.REL)[0].model_dump(
        mode='json', by_alias=True)
    renamed = raw['characters']['char_001']
    renamed['canonical'] = '宝珠'
    renamed['aliases'][0]['surface'] = '宝珠'
    # 模拟硬杀留下已提交的人物与未补尾的旧投影；不改知识文件或替旧审批重签。
    (book / characters.REL).write_text(
        json.dumps(encode_artifact(characters.REL, raw), ensure_ascii=False) + '\n',
        encoding='utf-8', newline='\n')
    assert store.freshness(characters.REL).state == 'fresh'
    assert store.freshness(extract.GLOBAL_REL).state == 'stale'
    assert store.freshness(extract.BLUEPRINT_CHARACTER_REL).state == 'stale'

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    blueprint_client, blueprint_transport = _client(
        blueprint_ctx,
        lambda req, _index: _respond_blueprints(req, _response_blueprints()),
    )
    # 投影落后于已提交人物时，blueprint 入口点名唯一的 0 LLM 恢复命令。
    with pytest.raises(ProvenanceRefused, match='stage2.characters --revalidate'):
        extract.run_blueprint(blueprint_ctx, blueprint_client)
    assert blueprint_transport.calls == []

    # Stage4 起的统一机械准入同样拒绝：Global 的人物引用已随换名漂移。
    import novel.admission as mechanical_admission

    gate = mechanical_admission.stage4_ready(book.parent, book.name.rsplit('_', 1)[-1])
    assert not gate.ok
    assert any('称名投影' in row and '已过期' in row for row in gate.blocking), gate.blocking
    assert any('Global 全书事实' in row and '已过期' in row for row in gate.blocking), gate.blocking


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
        original = response['arc_secrets'][0]['revealed_from']
        response['arc_secrets'][0]['revealed_from'] = 99
        client, transport = _client(ctx, lambda _req, _index: _ok(response))

        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        assert len(transport.calls) == 1

        invalid = book / extract._GLOBAL_INVALID
        raw = json.loads(invalid.read_text(encoding='utf-8'))
        raw['arc_secrets'][0]['revealed_from'] = original
        invalid.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

        promoted, report = extract.run_revalidate(
            _ctx(book, 'stage2.global', paid=False),
            actor='reviewer', reason='修正越界的揭示章')
        assert promoted
        assert len(transport.calls) == 1
        assert (book / extract.GLOBAL_REL).is_file()
        assert not invalid.exists()
        stored = grounding_io.read(ArtifactStore(ctx))
        assert stored.provenance.revalidated is True
        audit = stored.provenance.revalidation_audit
        assert audit is not None
        assert audit.actor == 'reviewer'
        assert audit.reason == '修正越界的揭示章'
        assert audit.content_changed is True
        assert audit.edited_by_human is True
        assert '/arc_secrets/0/revealed_from' in \
            audit.changed_json_pointers
        assert next(r for r in report.rules
                    if r.rule == 'canon_chapter_refs_dangling').status == 'ok'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_resample_bypasses_the_rejected_cache_and_preserves_its_round():
    """显式重采样生成新候选并归档问题稿，同时沿用原工作单元预算。"""
    root = Path(tempfile.mkdtemp())
    book = root / '全局重采样测试_460'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage2.global')
        store = ArtifactStore(ctx)
        store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
        bad = _response_global()
        bad['arc_secrets'][0]['revealed_from'] = 99

        def respond(_req: ChatRequest, index: int) -> ChatResponse:
            return _ok(bad if index == 0 else _response_global())

        client, transport = _client(ctx, respond)
        with pytest.raises(GateBlocked):
            extract.run_global(ctx, client)
        original_invalid = (book / extract._GLOBAL_INVALID).read_bytes()

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
        assert usage.calls_made == 2 and usage.calls_cached == 0
        from novel.work_units import budget_for
        assert budget_for(ctx, scope='book').used == 2
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
        rejected['arc_secrets'][0]['revealed_from'] = 98
        store.write_invalid(
            extract.GLOBAL_REL,
            grounding_io.for_write(store, GlobalGrounding.model_validate(rejected)),
            gates_skipped=[],
        )
        invalid_before = (book / extract._GLOBAL_INVALID).read_bytes()

        retry_ctx = _ctx(book, 'stage2.global')
        retry_bad = _response_global()
        retry_bad['arc_secrets'][0]['revealed_from'] = 99
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
    from novel import exit_codes as X
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
        _accept_fixture_global(global_ctx)

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
        raw['blueprints'][0]['beats'][0]['participants'] = [{'char_id': 'char_999'}]
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


def test_old_global_input_edge_cannot_be_resigned_as_current_blueprint(tmp_path):
    """现役载体缺称名投影来源时保持未知，不可由免费归一追认旧输入。"""
    book = tmp_path / '旧v6来源边零成本刷新_4581'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

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
    assert raw['_schema_version'] == 10
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
        'stale', 'consumed_changed')

    before = path.read_bytes()
    recovery_ctx = _ctx(book, 'stage2.blueprint', paid=False)
    with pytest.raises(ProvenanceRefused, match='缺当前抽取依赖来源'):
        extract.run_renormalize(recovery_ctx)
    assert path.read_bytes() == before
    assert len(blueprint_transport.calls) == calls_before
    assert recovery_ctx.ledger.usage() is None
    assert ArtifactStore(recovery_ctx).freshness(
        extract.BLUEPRINT_REL).state == 'stale'


@pytest.mark.parametrize('formal_exists', [False, True])
def test_old_input_invalid_preserves_bytes_and_refuses_free_migration(
        tmp_path, formal_exists):
    """INVALID-only / formal+INVALID 均拒绝来源重签，不覆盖正式稿或重买。"""
    from novel.orchestrator import state as orchestrator_state

    book = tmp_path / f'旧v6问题稿零成本刷新_{int(formal_exists)}'
    book.mkdir()
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _ = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)
    _accept_fixture_global(global_ctx)

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
        'stale', 'consumed_changed')
    state = orchestrator_state.artifact_state(store, extract.BLUEPRINT_REL)
    assert state.exists is formal_exists and state.invalid
    assert (state.invalid_freshness, state.invalid_reason) == (
        'stale', 'consumed_changed')
    if formal_exists:
        assert (state.freshness, state.reason) == ('fresh', 'match')
    else:
        assert (state.freshness, state.reason) == (
            'unknown', 'no_provenance')

    before = invalid_path.read_bytes()
    formal_before = formal_path.read_bytes() if formal_exists else None
    with pytest.raises(ProvenanceRefused):
        extract.run_revalidate(
            recovery_ctx, actor='agent:test', reason='旧来源不可经提升追认')
    with pytest.raises(ProvenanceRefused, match='缺当前抽取依赖来源'):
        extract.run_renormalize(recovery_ctx)
    assert invalid_path.read_bytes() == before
    assert (formal_path.read_bytes() if formal_exists else None) == formal_before
    assert formal_path.exists() is formal_exists
    assert len(blueprint_transport.calls) == calls_before
    assert recovery_ctx.ledger.usage() is None


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
    _accept_fixture_global(global_ctx)

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
        extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL, extract.GLOBAL_REL, extra_rel,
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
    assert orchestrator_state.stage2_blueprint_invalid_recovery_needed(ov)

    before = invalid_path.read_bytes()
    with pytest.raises(ProvenanceRefused) as refused:
        extract.run_revalidate(
            recovery_ctx, actor='agent:test', reason='通用未知不得借迁移 marker')
    assert '--renormalize' not in str(refused.value)
    with pytest.raises(ProvenanceRefused, match='来源未知'):
        extract.run_renormalize(recovery_ctx)
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
    _accept_fixture_global(global_ctx)

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
    old_edge['consumed'] = None  # 模拟只记录文件身份的历史输入边。
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


def test_blueprint_context_keeps_current_full_and_only_attested_subjects():
    from novel.consumption import plot_global_projection
    from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
    from novel.stages.stage2.plot_prompts import build_plot_user
    sections = [{'chapter_ordinal': o, 'text': f'BEGIN-{o}|' + chr(64 + o) * 4800 + f'|TAIL-{o}'}
                for o in range(1, 7)]
    sections[1]['text'] = '甲乙抱着包裹进了城。' + sections[1]['text']
    sections[5]['text'] = '丙丁掀帘进来。' + sections[5]['text']
    characters = {
        'char_001': {'canonical': '甲乙', 'first_chapter': 2,
                     'aliases': [{'surface': '甲乙', 'identity_revealed_from': 2}]},
        'char_002': {'canonical': '丙丁', 'first_chapter': 6,
                     'aliases': [{'surface': '丙丁', 'identity_revealed_from': 6}]},
    }
    inventory = blueprint_character_prompt_inventory(characters, sections)
    from tests.plot_fixtures import GLOBAL
    shared = copy.deepcopy(GLOBAL)
    proof = 'ev_' + '1' * 24
    shared['evidence_anchors'][proof] = {'chapter_ordinal': 6, 'start': 0, 'end': 7,
        'text_sha256': hashlib.sha256(sections[5]['text'][:7].encode('utf-8')).hexdigest()}
    shared['arc_secrets'] = [{
        'secret_id': 'secret_01', 'fact': '尚未揭示的后台身份', 'fact_ref': None,
        'setup_chapters': [], 'revealed_from': 6, 'evidence_refs': [proof]}]
    projection = plot_global_projection(shared, 5)
    user = build_plot_user('边界测试', sections[4]['text'], 5,
        character_refs=inventory['visible_character_refs'][5], global_projection=projection,
        character_bindings=inventory['character_bindings'][5])
    assert user.count(sections[4]['text']) == 1
    assert all(section['text'] not in user for section in sections if section['chapter_ordinal'] != 5)
    assert '甲乙' in user and '丙丁' not in user and '尚未揭示的后台身份' not in user
    assert json.loads(user.split('\n', 1)[0])['entry_states'] == []
    assert extract.blueprint_context('边界测试', sections) == {
        'architecture': 'plot-blueprint-v1', 'source_format': 'numbered-paragraphs-v1',
        'global_projection_contract': 'plot-global-scope-v2',
    }


def test_past_attested_surfaces_only_hand_out_past_attested_forms():
    from novel.stages.stage2.normalize import past_attested_surfaces
    from tests.stage2_fixtures import formal_subjects
    registry, _ = formal_subjects(global_grounding())
    table = past_attested_surfaces(
        registry.model_dump(mode='json')['characters'], main_text())
    assert table[1] == []
    assert table[2] == ['李宝珠']
    # 「沈母」只是 canonical、正文里从未出现过 ⇒ 不进清单；「母亲」按首现先后排在后面。
    assert table[3] == ['李宝珠', '母亲']


def test_stage2_renormalize_rejects_wire_shaped_formal_artifact(tmp_path):
    from tests.test_plot_producer import prepare_book, generate_plot
    from novel.stages.stage2.plot_compile import response_projection
    book = prepare_book(tmp_path)
    ctx, wire = generate_plot(book)
    path = book / extract.BLUEPRINT_REL
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['blueprints'] = [response_projection(row, main_text()[row['chapter_ordinal'] - 1]['text'])
                         for row in raw['blueprints']]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ArtifactUnreadable):
        extract.run_renormalize(_ctx(book, 'stage2.blueprint', paid=False))
    assert path.read_bytes() == before
    assert len(wire.calls) == 3


def test_stage2_targeted_regeneration_refreshes_one_chapter_and_merges_book():
    root = Path(tempfile.mkdtemp())
    try:
        from tests.test_blueprint_repair_queue import prepare_complete_book
        book = prepare_complete_book(root)
        global_ctx = _ctx(book, 'stage2.global')

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
        broken['blueprints'][0]['beats'][0]['participants'] = [{'char_id': 'char_999'}]
        store.write_invalid(
            extract.BLUEPRINT_REL,
            ChapterBlueprints.model_validate(broken),
            gates_skipped=[],
        )

        repair_ctx = _ctx(book, 'stage2.blueprint')
        repair_ctx.cache = CachePolicy.refresh()
        def repair(req, _index):
            metadata = json.loads(req.user.split('\n', 1)[0])
            row = metadata['repair_baseline']
            row['identities'] = metadata['baseline_identities']
            row['beats'][0]['participants'] = [{'char_id': 'char_001'}]
            return _ok({'blueprints': [row]})
        repair_client, repair_transport = _client(repair_ctx, repair)
        report = extract.run_blueprint(
            repair_ctx, repair_client, chapters=(1,))

        assert report.passed
        assert len(repair_transport.calls) == 1
        metadata = json.loads(repair_transport.calls[0].user.split('\n', 1)[0])
        assert metadata['repair_baseline']['beats'][0]['participants'] == [
            {'char_id': 'char_999', 'presentation_ref': None}]
        assert metadata['baseline_identities']
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


@pytest.mark.parametrize('new_invalid', [False, True])
def test_targeted_refresh_drains_two_chapters_and_resumes_once_from_checkpoints(
        tmp_path, monkeypatch, new_invalid):
    """定向 batch 可免费收口；提交后新增的问题稿不能被旧尾窗清除。"""
    from tests.test_blueprint_repair_queue import prepare_complete_book
    book = prepare_complete_book(tmp_path)
    global_ctx = _ctx(book, 'stage2.global')

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
    revision_candidates = {}

    def first_wave(req: ChatRequest, _index: int) -> ChatResponse:
        metadata = json.loads(req.user.split('\n', 1)[0])
        ordinal = metadata['chapter_ordinal']
        row = metadata['repair_baseline']
        row['identities'] = metadata['baseline_identities']
        revision_candidates[ordinal] = copy.deepcopy(row)
        if ordinal == 1:
            return _ok({'blueprints': [row]})
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
        json.dumps({'blueprints': [revision_candidates[2]]}, ensure_ascii=False),
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
    if new_invalid:
        crash_store.write_invalid(
            extract.BLUEPRINT_REL, formal, gates_skipped=[])
        invalid_sha = crash_store.full_sha256_or_none(extract._INVALID)
        assert invalid_sha is not None
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
    if new_invalid:
        with pytest.raises(ProvenanceRefused, match='问题稿已更新'):
            extract.run_blueprint(finalized, finalized_client, chapters=(1, 2))
        assert crash_store.full_sha256_or_none(extract._INVALID) == invalid_sha
        assert stage2_runs.committed(finalized) is None
    else:
        report = extract.run_blueprint(finalized, finalized_client, chapters=(1, 2))
        assert report.passed
        assert stage2_runs.committed(finalized) is not None
        assert not ArtifactStore(finalized).exists(extract._INVALID)
    assert finalized_transport.calls == []
    assert sorted(path.name for path in rounds.glob(
        'chapter_blueprints.r*.json')) == before_rounds
    after_adoptions = [
        row for row in map(json.loads, audit_path.read_text('utf-8').splitlines())
        if row.get('kind') == 'artifact_adoption'
    ]
    assert after_adoptions == before_adoptions, 'commit 尾窗不得重复 adoption'


def test_pending_targeted_revision_cannot_change_scope_and_buy_another_batch(tmp_path):
    from tests.test_plot_producer import prepare_book, generate_plot
    book = prepare_book(tmp_path)
    initial, _ = generate_plot(book)
    current = _ctx(book, 'stage2.blueprint')
    current.cache = CachePolicy.refresh()
    current.repair_rejected_responses = True
    client, first_wire = _client(current, lambda *_args: ChatResponse(
        content='{"blueprints":[}', finish_reason='stop', model='test-model'))
    with pytest.raises(ResponseRepairBatchRequired) as paused:
        extract.run_blueprint(current, client, chapters=(1,))
    assert len(first_wire.calls) == 1
    manifest = Path(paused.value.manifest_path)
    before = manifest.read_bytes()
    formal = ArtifactStore(initial).full_sha256_or_none(extract.BLUEPRINT_REL)
    for chapters in ((1,), (2,), (1, 2)):
        resumed = _disk_blueprint_ctx(book, current.ledger.path, paid=True, run_id=current.run_id)
        resumed.cache = CachePolicy.refresh()
        resumed.repair_rejected_responses = True
        candidate, wire = _client(resumed, lambda *_args: pytest.fail('范围变化不能绕过 pending batch'))
        with pytest.raises(ProvenanceRefused):
            extract.run_blueprint(resumed, candidate, chapters=chapters)
        assert not wire.calls
    assert manifest.read_bytes() == before
    assert ArtifactStore(initial).full_sha256_or_none(extract.BLUEPRINT_REL) == formal
