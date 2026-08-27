#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 脏候选逐条定向重试与成本/历史账。"""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.invariants import HOOK_RECIPE, HOOK_SLOTS
from contracts.schema.report import finding
from contracts.schema.stage6 import Mech
from novel.llm.cache import ResponseCache
from novel.llm.client import LlmClient, MAX_SAMPLES_PER_RUNG
from novel.llm.transport import ChatResponse, FakeTransport, TransportError
from novel.stages.stage6 import hook, prompts
from tests.test_stage2_generation import _ctx


def _response(data):
    return ChatResponse(
        content=json.dumps(data, ensure_ascii=False), finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 20, 'reasoning_tokens': 5},
        model='test-model')


def _run_case(monkeypatch, root: Path, initial_texts: list[str], retry_responder):
    book = root / 'case_622'
    book.mkdir()
    ctx = _ctx(book, 'stage6.hook')
    material = prompts.Material(blocks=('完整创作包。',), upto=1)
    upstreams = SimpleNamespace(
        mat=material, src_hook='', secrets=[], arc=None, characters={},
        hook_mechanical={}, upto=1, judged={})
    written = []
    state = {}

    class Store:
        def __init__(self, _ctx):
            pass

        def write(self, rel, artifact):
            state[rel] = artifact
            written.append((rel, artifact))

        def exists(self, rel):
            return rel in state

        def read_own(self, _model, rel, **_kwargs):
            return state[rel], {}

        def freshness(self, _rel):
            return SimpleNamespace(state='fresh', reason='match')

        @contextmanager
        def history_transaction(self, *_rels):
            yield

    monkeypatch.setattr(hook, 'ArtifactStore', Store)
    monkeypatch.setattr(hook, '_Upstreams', lambda _ctx, _store: upstreams)

    def deterministic_judge(text, _up):
        mech = Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0)
        findings = ([finding(
            'hook_truncated', detail=f'候选未完整：{text}', evidence=[text])]
            if text.startswith('坏候选') else [])
        return text, mech, findings

    monkeypatch.setattr(hook, '_normalize_and_judge', deterministic_judge)
    initial = {
        'candidates': [
            {'approach': approach, 'text': text}
            for approach, text in zip(HOOK_RECIPE, initial_texts)
        ],
        'rationale': '初始整组三候选说明。',
    }
    retry_no = 0

    def respond(request, _index):
        nonlocal retry_no
        marker = '── 单候选定向修复 ──\n'
        if marker not in request.user:
            return _response(initial)
        repair = json.loads(request.user.split(marker, 1)[1])
        result = retry_responder(request, repair, retry_no)
        retry_no += 1
        return result

    transport = FakeTransport(respond)
    client = LlmClient(
        ctx, transport, cache=ResponseCache(root / 'cache'), model='test-model',
        max_retries=0, budget_checker=lambda *_args, **_kwargs: None)
    return hook.run_hook(ctx, client), transport, written


def test_each_initially_dirty_candidate_retries_once_and_usage_includes_retries(monkeypatch):
    root = Path(tempfile.mkdtemp())
    book = root / '文案候选重试测试_621'
    book.mkdir()
    try:
        ctx = _ctx(book, 'stage6.hook')
        material = prompts.Material(blocks=('完整创作包。',), upto=1)
        upstreams = SimpleNamespace(
            mat=material, src_hook='', secrets=[], arc=None, characters={},
            hook_mechanical={}, upto=1, judged={})
        written = []
        state = {}

        class Store:
            def __init__(self, _ctx):
                pass

            def write(self, rel, artifact):
                state[rel] = artifact
                written.append((rel, artifact))

            def exists(self, rel):
                return rel in state

            def read_own(self, _model, rel, **_kwargs):
                return state[rel], {}

            def freshness(self, _rel):
                return SimpleNamespace(state='fresh', reason='match')

            @contextmanager
            def history_transaction(self, *_rels):
                yield

        monkeypatch.setattr(hook, 'ArtifactStore', Store)
        monkeypatch.setattr(hook, '_Upstreams', lambda _ctx, _store: upstreams)

        def deterministic_judge(text, _up):
            mech = Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0)
            findings = ([finding(
                'hook_truncated', detail=f'候选未完整：{text}', evidence=[text])]
                if text.startswith('坏候选') else [])
            return text, mech, findings

        monkeypatch.setattr(hook, '_normalize_and_judge', deterministic_judge)
        initial = {
            'candidates': [
                {'approach': HOOK_RECIPE[0], 'text': '坏候选甲'},
                {'approach': HOOK_RECIPE[1], 'text': '坏候选乙'},
                {'approach': HOOK_RECIPE[2], 'text': '原本干净。'},
            ],
            'rationale': '三条各赌一个角度。',
        }

        def respond(request, _index):
            marker = '── 单候选定向修复 ──\n'
            if marker not in request.user:
                return _response(initial)
            repair = json.loads(request.user.split(marker, 1)[1])
            # ⚠ 认 `target_slot` 而不是 `target_approach`：当前配方里
            #   `scene_punchline` 占两槽，按策略造文本会让两条修复稿撞成同一句，
            #   于是第二条被判 `duplicate`——那是**假样本**，不是被测行为。
            slot = repair['target_slot']
            return _response({
                'approach': repair['target_approach'],
                'text': f'修好-{slot}。',
                'rationale': f'整组三条候选说明已更新到 {slot}。',
            })

        transport = FakeTransport(respond)
        client = LlmClient(
            ctx, transport, cache=ResponseCache(root / 'cache'), model='test-model',
            max_retries=0, budget_checker=lambda *_args, **_kwargs: None)
        artifact = hook.run_hook(ctx, client)

        assert len(transport.calls) == 3, '一次初始请求 + 两条脏候选各一次重试'
        assert [row.slot for row in artifact.provenance.candidate_retries] \
            == list(HOOK_SLOTS[:2]), (
                '重试账记的是**哪一条候选**被重试了（位置身份），不是哪种策略——'
                '同一种策略采两条样本时后者答不出前一个问题')
        assert all(row.accepted and row.before_hard == 1 and row.after_hard == 0
                   for row in artifact.provenance.candidate_retries)
        assert all(row.status == 'accepted' and row.reason is None
                   for row in artifact.provenance.candidate_retries)
        assert all(not candidate.findings for candidate in artifact.candidates)
        assert artifact.rationale == f'整组三条候选说明已更新到 {HOOK_SLOTS[1]}。'
        second_repair = json.loads(
            transport.calls[2].user.split('── 单候选定向修复 ──\n', 1)[1])
        assert second_repair['current_rationale'] \
            == f'整组三条候选说明已更新到 {HOOK_SLOTS[0]}。'
        assert second_repair['current_candidates'][0]['text'] \
            == f'修好-{HOOK_SLOTS[0]}。'
        assert artifact.provenance.usage is not None
        assert artifact.provenance.usage.calls_made == 3
        assert 'HOOK_RETRY_SYS' in artifact.provenance.prompts
        assert written and written[0][0] == hook.REL
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('dirty_count', [0, 1, 2])
def test_retry_call_count_tracks_only_initially_dirty_candidates(monkeypatch, dirty_count):
    root = Path(tempfile.mkdtemp())
    try:
        initial = [
            (f'坏候选{i}' if i < dirty_count else f'原本干净{i}。')
            for i in range(3)
        ]

        def repaired(_request, repair, _retry_no):
            slot = repair['target_slot']
            return _response({
                'approach': repair['target_approach'],
                'text': f'修好-{slot}。',
                'rationale': f'更新后的整组说明-{slot}。',
            })

        artifact, transport, _written = _run_case(
            monkeypatch, root, initial, repaired)
        assert len(transport.calls) == 1 + dirty_count
        assert len(artifact.provenance.candidate_retries) == dirty_count
        assert artifact.provenance.usage is not None
        assert artifact.provenance.usage.calls_made == 1 + dirty_count
        for index in range(dirty_count, 3):
            assert artifact.candidates[index].text == initial[index]
        assert artifact.rationale == (
            '初始整组三候选说明。' if dirty_count == 0
            else f'更新后的整组说明-{HOOK_SLOTS[dirty_count - 1]}。')
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize(
    'mode,expected_status,expected_after,has_output',
    [
        ('wrong', 'wrong_approach', None, True),
        ('duplicate', 'duplicate', 0, True),
        ('same_hard', 'hard_not_reduced', 1, True),
        ('transport', 'request_failed', None, False),
        ('invalid', 'request_failed', None, False),
    ],
)
def test_failed_retry_keeps_paid_initial_candidate_and_rationale(
        monkeypatch, mode, expected_status, expected_after, has_output):
    root = Path(tempfile.mkdtemp())
    initial = ['坏候选目标', '原本干净一。', '原本干净二。']
    try:
        def failed(_request, repair, _retry_no):
            target = repair['target_approach']
            if mode == 'transport':
                return TransportError('synthetic offline', retryable=False)
            if mode == 'invalid':
                return _response({'approach': target, 'text': '缺 rationale 的响应。'})
            if mode == 'wrong':
                # ⚠ 挑一个**与目标槽的写法不同**的合法写法：目标是 c1
                #   （配方里的 `HOOK_RECIPE[0]`），这里回 `HOOK_RECIPE[1]`。
                #   写成「配方里的第二种」而不是写死名字，配方一改这条仍然成立。
                target = next(a for a in HOOK_RECIPE if a != HOOK_RECIPE[0])
                text = '本不属于目标策略的文本。'
            elif mode == 'duplicate':
                text = initial[1]
            else:
                text = '坏候选仍然截断'
            return _response({
                'approach': target,
                'text': text,
                'rationale': '不应被采纳的整组说明。',
            })

        artifact, transport, written = _run_case(
            monkeypatch, root, initial, failed)
        retry = artifact.provenance.candidate_retries[0]
        # `invalid` 是这里唯一被**载体闸**拒的一种（`require`：缺 rationale）。
        # 除截断外的每一种拒绝都走有界同档重采（`_worth_resampling`）
        # ⇒ 它最多烧两采；上限没破（`HOOK_RETRY_RUNGS` 单档 × `MAX_SAMPLES_PER_RUNG`，
        # `tools/current_corpus_paid_preview.py` 早按这个口径算）。
        # 其余四种要么过了载体闸、被 stage6 自己的业务判据拒（wrong/duplicate/same_hard），
        # 要么根本没到内容闸（transport）。
        wire = 1 + MAX_SAMPLES_PER_RUNG if mode == 'invalid' else 2
        assert len(transport.calls) == wire
        assert retry.status == expected_status
        assert retry.after_hard == expected_after
        assert (retry.output is not None) is has_output
        assert retry.accepted is False and retry.reason
        assert artifact.candidates[0].text == initial[0]
        assert [candidate.text for candidate in artifact.candidates[1:]] == initial[1:]
        assert artifact.rationale == '初始整组三候选说明。'
        assert artifact.provenance.usage is not None
        assert artifact.provenance.usage.calls_made == wire
        assert written and written[0][1] is artifact
    finally:
        shutil.rmtree(root, ignore_errors=True)
