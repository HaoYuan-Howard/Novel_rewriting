#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 生成侧的零成本故障回归测试。"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.artifacts import STAGE4_WRITER_CONTEXT
from contracts.schema import encode_artifact
from contracts.schema.stage4 import CtxProvenance, WriterContext
from novel import prov
from novel.cli import exit_codes as X
from novel.errors import (
    GateBlocked, ProvenanceRefused, ResponseRepairRequired, SourceConflict,
    TerminalState,
)
from novel.llm.cache import (
    CachePolicy, ResponseCache, cache_key, request_fingerprint,
)
from novel.llm.client import (
    DEFAULT_RUNGS, FLASH_MODEL, JSON_OBJECT_MODE, PRO_FALLBACK_MODEL,
    LlmClient, LlmFailed,
)
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext
from novel.stages.stage5 import write
from novel.store import ArtifactStore
from tests import factories


class _MemoryTrimStore:
    """只承载 Stage5 正文/trim 状态机的内存盘面。"""

    def __init__(self, docs=None, *, baselines=None):
        self.docs = dict(docs or {})
        self.baselines = dict(baselines or {})
        self.archived = []
        self.transactions = []
        self.promoted = None
        self.refreshed = []

    def exists(self, rel):
        return rel in self.docs

    def path(self, rel):
        return Path(rel)

    def read_own(self, _model, rel, **_kwargs):
        artifact = self.docs[rel]
        raw = (artifact.model_dump(by_alias=True, mode='json')
               if hasattr(artifact, 'model_dump') else {})
        return artifact, raw

    def read_with_sha256(self, _model, rel):
        return self.docs[rel], 'a' * 64

    def read_own_with_sha256(self, _model, rel, **_kwargs):
        return self.docs[rel], 'a' * 64

    def prospective_sha256(self, _rel, _artifact):
        # 本 fake 的写后读回摘要恒为同一占位；prepared 必须与它逐字一致。
        return 'a' * 64

    def revalidation_baseline(self, rel, _model, **_kwargs):
        invalid_rel = rel.replace('.json', '.INVALID.json')
        return self.baselines.get(invalid_rel, self.docs[invalid_rel])

    def write(self, rel, artifact):
        self.docs[rel] = artifact
        invalid_rel = rel.replace('.json', '.INVALID.json')
        if invalid_rel in self.docs:
            self.archive_round(invalid_rel)
        return rel

    def refresh_formal(self, rel, artifact):
        assert rel in self.docs
        self.refreshed.append(rel)
        self.docs[rel] = artifact
        return rel

    def write_round(self, rel, artifact):
        self.docs[rel] = artifact
        invalid_rel = rel.replace('.json', '.INVALID.json')
        if invalid_rel in self.docs:
            self.archive_round(invalid_rel)
        return rel

    def write_invalid(self, rel, artifact, *, gates_skipped):
        assert gates_skipped == []
        invalid_rel = rel.replace('.json', '.INVALID.json')
        self.docs[invalid_rel] = artifact
        return invalid_rel

    def promote_invalid(self, rel, _model, **kwargs):
        invalid_rel = rel.replace('.json', '.INVALID.json')
        assert invalid_rel in self.docs
        self.promoted = kwargs['artifact_override']
        self.docs[rel] = self.promoted
        del self.docs[invalid_rel]
        return rel

    def archive_round(self, rel, *, keep=False):
        self.archived.append(rel)
        if not keep:
            self.docs.pop(rel)
        return rel

    def next_round(self, _rel):
        return 2

    @contextmanager
    def history_transaction(self, *rels):
        self.transactions.append(rels)
        yield


class _TransactionalEditStore(_MemoryTrimStore):
    """为 edit 单测保留真实 history_transaction 的回滚/轮次语义。"""

    def __init__(self, docs=None, *, baselines=None):
        super().__init__(docs, baselines=baselines)
        self.rounds = []
        self.write_round_calls = 0

    @staticmethod
    def _artifact_sha256(artifact) -> str:
        payload = artifact.model_dump(by_alias=True, mode='json')
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()

    def read_own_with_sha256(self, _model, rel, **_kwargs):
        artifact = self.docs[rel]
        return artifact, self._artifact_sha256(artifact)

    def prospective_sha256(self, _rel, artifact):
        return self._artifact_sha256(artifact)

    def next_round(self, rel):
        current = self.docs.get(rel)
        current_round = (
            int(current.provenance.round)
            if current is not None and current.provenance is not None
            else 0
        )
        archived = [
            int(artifact.provenance.round)
            for artifact in self.rounds
            if artifact.provenance is not None
        ]
        return max([current_round, *archived], default=0) + 1

    def write_round(self, rel, artifact):
        if rel in self.docs:
            self.rounds.append(copy.deepcopy(self.docs[rel]))
        self.write_round_calls += 1
        self.docs[rel] = artifact
        return rel

    @contextmanager
    def history_transaction(self, *rels):
        self.transactions.append(rels)
        before_docs = copy.deepcopy(self.docs)
        before_rounds = copy.deepcopy(self.rounds)
        before_calls = self.write_round_calls
        try:
            yield
        except BaseException as exc:
            # 只供“操作系统在 canonical replace 后硬杀进程”的恢复测试：真实进程
            # 此时根本没有机会执行 Python rollback。普通异常仍完整回滚。
            if getattr(exc, 'simulate_process_kill', False):
                raise
            self.docs = before_docs
            self.rounds = before_rounds
            self.write_round_calls = before_calls
            raise


def _stage5_chapter(*, text, budget_chars=10, errs=None, output_ordinal=1):
    provenance = write.RoundFreeProvenance(
        code='test-code', root_clean_sha1='a' * 40, inputs={},
        round=7, origin='human')
    return write.Chapter(output_ordinal=output_ordinal, segments=[write.ChapterSegment(slot='s01', text=text)], _provenance=provenance)


def _trim_book(entry):
    return SimpleNamespace(
        chapters={1: entry}, source_text={}, quote_style=None, sep_chars=None)


def _edit_unit(tmp_path, monkeypatch, judge):
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 100,
        'budget_chars': 1000,
        'scenes': [{'slot': 's01', 'budget_chars': 1000}],
    }
    book = _trim_book(entry)
    current = _stage5_chapter(text='当前正式稿。', budget_chars=1000)
    store = _TransactionalEditStore({
        'stage5/ch01.json': current,
        'stage5/review_1.json': {'sentinel': '旧 Review 不得被 edit 改写'},
    })
    novel_dir = tmp_path / '人工改稿_001'
    ctx = SimpleNamespace(
        novel_dir=novel_dir,
        sid='人工改稿_001',
        run_id='run-edit-1',
        code='test-edit-preview',
        free_provenance=lambda cls, **kwargs: cls(
            code='test-edit-preview', root_clean_sha1='a' * 40,
            inputs={}, **kwargs),
    )
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', judge)
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(
        write, '_report',
        lambda *_args, **_kwargs: SimpleNamespace(passed=True))
    edit = tmp_path / 'ch01.json'
    response = write.ChapterResponse(
        segments=[{'slot': 's01', 'text': '候选人工稿。'}])
    edit.write_text(response.model_dump_json(), encoding='utf-8')
    return ctx, store, response, edit


def test_length_capped_nonempty_draft_is_not_usable():
    response = ChatResponse(
        content='我人还没走到门前',
        reasoning='推理占满了预算',
        finish_reason='length',
    )
    assert write._reject_length_capped(response.text, response)
    complete = ChatResponse(content='这一次完整结束。', finish_reason='stop')
    assert write._reject_length_capped(complete.text, complete) is None


def test_apply_edit_cannot_formalize_a_paragraph_budget_hard(tmp_path, monkeypatch):
    """人工入口与模型入口共用段落硬闸；拒绝时原编辑文件与盘上正式稿都不变。"""
    edit = tmp_path / 'ch01.json'
    original = '{"segments":[{"slot":"s01","text":"第一段。"}]}'
    edit.write_text(original, encoding='utf-8')
    entry = {
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'source_chars': 100, 'budget_chars': 100,
        'scenes': [{'slot': 's01'}],
    }
    book = SimpleNamespace(chapters={1: entry})

    class Store:
        writes = []

        current = _stage5_chapter(text='当前正式稿。', budget_chars=100)

        @staticmethod
        def exists(rel):
            return rel == 'stage5/ch01.json'

        @classmethod
        def read_own_with_sha256(cls, _model, rel, **_kwargs):
            assert rel == 'stage5/ch01.json'
            return cls.current, 'a' * 64

        @staticmethod
        def next_round(_rel):
            return 1

    store = Store()
    ctx = SimpleNamespace(
        novel_dir=tmp_path / '人工改稿_001', sid='人工改稿_001',
        run_id='run-paragraph-hard', code='test-paragraph-hard',
        free_provenance=lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    hit = write.checks.finding(
        'paragraph_budget_missed',
        detail='自然段数量未命中明确目标：1 段，不在 3~4 段内',
        evidence=['actual=1', 'target=3~4'],
    )
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([hit], ['hard'], []))

    preview = write.prepare_edit_preview(
        ctx, 1, write.ChapterResponse.model_validate_json(original)).preview()
    for confirmation in ('formal', 'working_draft'):
        with pytest.raises(TerminalState, match='段落数硬闸'):
            write.run_apply_edit(
                ctx,
                str(edit),
                actor='editor',
                reason='补足段落结构',
                current_chapter_sha256=preview.current_chapter_sha256,
                preview_subject_sha256=preview.preview_subject_sha256,
                confirmation=confirmation,
            )
    assert edit.read_text(encoding='utf-8') == original
    assert store.writes == []


def test_over_length_chapter_keeps_pretrim_and_records_why(monkeypatch):
    """超上限 ⇒ 正文原样、落一条说得出理由的 audit。收缩臂已整臂下线，没有候选可采用。"""
    original = '甲' * 1200 + '。'
    before = [{'slot': 's01', 'text': original}]
    notes, audit = write._over_length_audit(
        before, {'output_ordinal': 1, 'budget_chars': 1000})

    assert notes and '保留 pretrim' in notes[0]
    assert audit is not None and audit.reason == notes[0]
    assert audit.before_sha256 == write._sha256_text(original)


def test_model_landing_builds_trim_audit_from_the_final_normalized_segments(
        tmp_path, monkeypatch):
    """首写/修订共用的落盘口，只能按最终 canonical 字节同步 trim。"""
    final_text = '终稿正文已经明显超过当前预算上限。'
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 5,
        'budget_chars': 5,
        'scenes': [{'slot': 's01', 'budget_chars': 5}],
    }
    book = _trim_book(entry)
    store = _MemoryTrimStore()
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda _segments, _book: (
            [{'slot': 's01', 'text': final_text}], write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(
        novel_dir=tmp_path / 'book',
        accounting={}, account=lambda *_args, **_kwargs: None)

    where, errors, _findings, trim_notes = write._write_chapter(
        ctx,
        store,
        entry,
        book,
        segments=[{'slot': 's01', 'text': '归一前很短。'}],
        mat='given',
        account=write.M.NormalizeAccount(),
        provenance=write.RoundFreeProvenance(
            code='test-code', root_clean_sha1='a' * 40, inputs={},
            round=1, origin='human'),
    )

    audit_rel = 'stage5/trim_audit_ch01.json'
    assert where == 'stage5/ch01.json' and errors == [] and trim_notes
    assert store.docs['stage5/ch01.json'].text == final_text
    assert store.docs[audit_rel].before_sha256 == write._sha256_text(final_text)


def test_apply_edit_commits_the_chapter_and_its_trim_audit_together(
        tmp_path, monkeypatch):
    text = '人工正文已经明显超过当前预算上限。'
    edit = tmp_path / 'ch01.json'
    edit.write_text(json.dumps({
        'segments': [{'slot': 's01', 'text': text}],
    }, ensure_ascii=False), encoding='utf-8')
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 5,
        'budget_chars': 5,
        'scenes': [{'slot': 's01', 'budget_chars': 5}],
    }
    book = _trim_book(entry)
    store = _MemoryTrimStore({
        'stage5/ch01.json': _stage5_chapter(
            text='当前正式稿。', budget_chars=5),
    })
    ctx = SimpleNamespace(
        novel_dir=tmp_path / '人工改稿_001', sid='人工改稿_001',
        run_id='run-edit-1', code='test-code',
        free_provenance=lambda cls, **kwargs: cls(
            code='test-code', root_clean_sha1='a' * 40, inputs={}, **kwargs),
    )
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], ['新读数']))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())

    response = write.ChapterResponse.model_validate_json(
        edit.read_text(encoding='utf-8'))
    preview = write.prepare_edit_preview(ctx, 1, response).preview()
    write.run_apply_edit(
        ctx,
        str(edit),
        actor='editor',
        reason='人工调整正文并保留超长 trim 工作面',
        current_chapter_sha256=preview.current_chapter_sha256,
        preview_subject_sha256=preview.preview_subject_sha256,
        confirmation=preview.required_confirmation,
    )

    chapter = store.docs['stage5/ch01.json']
    audit = store.docs['stage5/trim_audit_ch01.json']
    assert chapter.text == text
    assert audit.before_sha256 == write._sha256_text(chapter.text)


def test_edit_preview_is_readonly_and_working_draft_is_explicit(
        tmp_path, monkeypatch):
    """新增一般 HARD 只有 working_draft 可落；edit 不追跑 Review。"""
    hit = write.checks.finding(
        'meta_leak', detail='候选出现任务术语', evidence=['候选人工稿'])

    def judge(text, *_args, **_kwargs):
        return (([hit], ['候选出现任务术语'], [])
                if text == '候选人工稿。' else ([], [], []))

    ctx, store, response, edit = _edit_unit(tmp_path, monkeypatch, judge)
    before = copy.deepcopy(store.docs)
    preview = write.prepare_edit_preview(ctx, 1, response).preview()

    assert store.docs == before, 'preview 不得写章、归档、Review 或审计账'
    assert preview.required_confirmation == 'working_draft'
    assert [row.rule for row in preview.introduced_hard_findings] == ['meta_leak']
    with pytest.raises(SourceConflict, match='不得把新增 HARD 伪装成 formal'):
        write.run_apply_edit(
            ctx, str(edit), actor='editor', reason='先保存工作稿',
            current_chapter_sha256=preview.current_chapter_sha256,
            preview_subject_sha256=preview.preview_subject_sha256,
            confirmation='formal')
    assert store.write_round_calls == 0

    report = write.run_apply_edit(
        ctx, str(edit), actor='editor', reason='先保存工作稿',
        current_chapter_sha256=preview.current_chapter_sha256,
        preview_subject_sha256=preview.preview_subject_sha256,
        confirmation='working_draft')

    assert report.passed
    assert store.docs['stage5/ch01.json'].text == '候选人工稿。'
    assert 'errs' not in store.docs['stage5/ch01.json'].model_dump()
    assert judge(store.docs['stage5/ch01.json'].text)[1] == ['候选出现任务术语']
    assert store.docs['stage5/review_1.json'] == before['stage5/review_1.json']
    assert 'stage5/review_2.json' not in store.docs, '逐次 edit 不得自动追跑 Review'
    actions = write.human_actions.read_events(tmp_path)
    assert len(actions) == 1
    assert actions[0].confirmation == 'working_draft'
    assert actions[0].introduced_hard_rules == ['meta_leak']


def test_apply_edit_requires_both_preview_locks(tmp_path, monkeypatch):
    ctx, store, response, edit = _edit_unit(
        tmp_path, monkeypatch, lambda *_args, **_kwargs: ([], [], []))
    preview = write.prepare_edit_preview(ctx, 1, response).preview()

    with pytest.raises(SourceConflict, match='已在 preview 后变化'):
        write.run_apply_edit(
            ctx, str(edit), actor='editor', reason='确认正式稿',
            current_chapter_sha256='f' * 64,
            preview_subject_sha256=preview.preview_subject_sha256,
            confirmation='formal')
    with pytest.raises(SourceConflict, match='preview 主体已漂移'):
        write.run_apply_edit(
            ctx, str(edit), actor='editor', reason='确认正式稿',
            current_chapter_sha256=preview.current_chapter_sha256,
            preview_subject_sha256='f' * 64,
            confirmation='formal')

    assert store.write_round_calls == 0
    assert write.human_actions.read_events(tmp_path) == []


def test_preview_subject_binds_the_complete_typed_finding(tmp_path, monkeypatch):
    converge = {'value': 'new'}

    def judge(text, *_args, **_kwargs):
        if text != '候选人工稿。':
            return [], [], []
        hit = write.checks.finding(
            'meta_leak', detail='候选出现任务术语', evidence=['候选人工稿'],
            converge=converge['value'])
        return [hit], ['候选出现任务术语'], []

    ctx, _store, response, _edit = _edit_unit(tmp_path, monkeypatch, judge)
    before = write.prepare_edit_preview(ctx, 1, response).preview()
    converge['value'] = 'same_draft'
    after = write.prepare_edit_preview(ctx, 1, response).preview()

    assert before.normalized_segments == after.normalized_segments
    assert before.introduced_hard_findings[0].converge != \
        after.introduced_hard_findings[0].converge
    assert before.preview_subject_sha256 != after.preview_subject_sha256


def test_append_failure_rolls_back_edit_and_retry_creates_one_round_one_action(
        tmp_path, monkeypatch):
    ctx, store, response, edit = _edit_unit(
        tmp_path, monkeypatch, lambda *_args, **_kwargs: ([], [], []))
    preview = write.prepare_edit_preview(ctx, 1, response).preview()
    before = copy.deepcopy(store.docs)
    original_commit = write.human_actions.commit_edit
    monkeypatch.setattr(
        write.human_actions,
        'commit_edit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('disk full')))

    with pytest.raises(OSError, match='disk full'):
        write.run_apply_edit(
            ctx, str(edit), actor='editor', reason='确认正式稿',
            current_chapter_sha256=preview.current_chapter_sha256,
            preview_subject_sha256=preview.preview_subject_sha256,
            confirmation='formal')

    assert store.docs == before
    assert store.rounds == [] and store.write_round_calls == 0
    assert write.human_actions.read_events(tmp_path) == []
    assert len(write.human_actions.actions_path(tmp_path).read_text(
        encoding='utf-8').splitlines()) == 1, 'prepared 留作可恢复证据，不冒充已提交动作'

    monkeypatch.setattr(
        write.human_actions,
        'commit_edit',
        original_commit)
    # 新作业拥有不同 code/run，prospective result 也随来源账变化；旧 orphan prepared
    # 不得用 request_id 抢占重试，新的 result-addressed action 应能正常提交。
    retry_ctx = SimpleNamespace(
        **{**ctx.__dict__, 'run_id': 'run-edit-retry', 'code': 'test-edit-retry'},
    )
    retry_ctx.free_provenance = lambda cls, **kwargs: cls(
        code='test-edit-retry', root_clean_sha1='a' * 40, inputs={}, **kwargs)
    write.run_apply_edit(
        retry_ctx, str(edit), actor='editor', reason='确认正式稿',
        current_chapter_sha256=preview.current_chapter_sha256,
        preview_subject_sha256=preview.preview_subject_sha256,
        confirmation='formal')
    assert store.write_round_calls == 1 and len(store.rounds) == 1
    assert len(write.human_actions.read_events(tmp_path)) == 1

    # 客户端没收到成功响应而重放同一请求：按 landed SHA 读回 committed，幂等成功；
    # 不再造一轮，也不再追加第二份公开动作。
    write.run_apply_edit(
        retry_ctx, str(edit), actor='editor', reason='确认正式稿',
        current_chapter_sha256=preview.current_chapter_sha256,
        preview_subject_sha256=preview.preview_subject_sha256,
        confirmation='formal')
    assert store.write_round_calls == 1 and len(store.rounds) == 1
    assert len(write.human_actions.read_events(tmp_path)) == 1


def test_kill_after_canonical_write_is_reconciled_by_a_new_run_without_new_round(
        tmp_path, monkeypatch):
    """prepared + landed canonical 是 kill 后的恢复证据；公开账只认 committed。"""
    ctx, store, response, edit = _edit_unit(
        tmp_path, monkeypatch, lambda *_args, **_kwargs: ([], [], []))
    preview = write.prepare_edit_preview(ctx, 1, response).preview()
    original_commit = write.human_actions.commit_edit

    class SimulatedProcessKill(BaseException):
        simulate_process_kill = True

    monkeypatch.setattr(
        write.human_actions, 'commit_edit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SimulatedProcessKill()))
    with pytest.raises(SimulatedProcessKill):
        write.run_apply_edit(
            ctx, str(edit), actor='editor', reason='确认正式稿',
            current_chapter_sha256=preview.current_chapter_sha256,
            preview_subject_sha256=preview.preview_subject_sha256,
            confirmation='formal')

    assert store.docs['stage5/ch01.json'].text == '候选人工稿。'
    assert store.write_round_calls == 1 and len(store.rounds) == 1
    assert write.human_actions.read_events(tmp_path) == []
    assert len(write.human_actions.actions_path(tmp_path).read_text(
        encoding='utf-8').splitlines()) == 1

    monkeypatch.setattr(write.human_actions, 'commit_edit', original_commit)
    retry_edit = tmp_path / 'retry-upload' / 'ch01.json'
    retry_edit.parent.mkdir()
    retry_edit.write_text(response.model_dump_json(), encoding='utf-8')
    retry_ctx = SimpleNamespace(
        **{**ctx.__dict__, 'run_id': 'run-after-kill', 'code': 'new-worker-code'},
    )
    retry_ctx.free_provenance = lambda cls, **kwargs: cls(
        code='new-worker-code', root_clean_sha1='a' * 40, inputs={}, **kwargs)
    write.run_apply_edit(
        retry_ctx, str(retry_edit), actor='editor', reason='确认正式稿',
        current_chapter_sha256=preview.current_chapter_sha256,
        preview_subject_sha256=preview.preview_subject_sha256,
        confirmation='formal')

    assert store.write_round_calls == 1 and len(store.rounds) == 1
    events = write.human_actions.read_events(tmp_path)
    assert len(events) == 1 and events[0].phase == 'committed'
    assert len(write.human_actions.actions_path(tmp_path).read_text(
        encoding='utf-8').splitlines()) == 2


def test_truncated_committed_tail_is_quarantined_and_rebuilt_without_new_round(
        tmp_path, monkeypatch):
    ctx, store, response, edit = _edit_unit(
        tmp_path, monkeypatch, lambda *_args, **_kwargs: ([], [], []))
    preview = write.prepare_edit_preview(ctx, 1, response).preview()
    original_append = write.human_actions.append_line_under_process_lock

    class SimulatedProcessKill(BaseException):
        simulate_process_kill = True

    def torn_committed(path, line):
        if json.loads(line)['phase'] != 'committed':
            return original_append(path, line)
        payload = (line + '\n').encode('utf-8')
        with Path(path).open('ab') as handle:
            handle.write(payload[:len(payload) // 2])
            handle.flush()
        raise SimulatedProcessKill()

    monkeypatch.setattr(
        write.human_actions, 'append_line_under_process_lock', torn_committed)
    with pytest.raises(SimulatedProcessKill):
        write.run_apply_edit(
            ctx, str(edit), actor='editor', reason='确认正式稿',
            current_chapter_sha256=preview.current_chapter_sha256,
            preview_subject_sha256=preview.preview_subject_sha256,
            confirmation='formal')

    assert store.write_round_calls == 1 and len(store.rounds) == 1
    assert write.human_actions.read_events(tmp_path) == []
    monkeypatch.setattr(
        write.human_actions, 'append_line_under_process_lock', original_append)
    retry_ctx = SimpleNamespace(
        **{**ctx.__dict__, 'run_id': 'run-after-torn-commit', 'code': 'retry-code'},
    )
    retry_ctx.free_provenance = lambda cls, **kwargs: cls(
        code='retry-code', root_clean_sha1='a' * 40, inputs={}, **kwargs)
    write.run_apply_edit(
        retry_ctx, str(edit), actor='editor', reason='确认正式稿',
        current_chapter_sha256=preview.current_chapter_sha256,
        preview_subject_sha256=preview.preview_subject_sha256,
        confirmation='formal')

    assert store.write_round_calls == 1 and len(store.rounds) == 1
    assert len(write.human_actions.read_events(tmp_path)) == 1
    assert len(list(write.human_actions.tail_recovery_dir(
        tmp_path).glob('*.json'))) == 1


def test_two_independent_stores_serialize_the_same_chapter_commit(
        tmp_path, monkeypatch):
    """直 CLI/多服务实例共用书章锁；输家只报 stale，不能回滚覆盖赢家。"""
    ctx_a, store_a, response, edit = _edit_unit(
        tmp_path, monkeypatch, lambda *_args, **_kwargs: ([], [], []))
    preview = write.prepare_edit_preview(ctx_a, 1, response).preview()
    ctx_b = SimpleNamespace(
        **{**ctx_a.__dict__, 'run_id': 'run-edit-b', 'code': 'test-edit-b'},
    )
    ctx_b.free_provenance = lambda cls, **kwargs: cls(
        code='test-edit-b', root_clean_sha1='a' * 40, inputs={}, **kwargs)
    store_b = _TransactionalEditStore()
    store_b.docs = store_a.docs
    monkeypatch.setattr(
        write, 'ArtifactStore',
        lambda chosen: store_a if chosen is ctx_a else store_b)

    barrier = threading.Barrier(3)
    results: list[str] = []
    failures: list[BaseException] = []

    def apply(chosen, actor):
        try:
            barrier.wait()
            write.run_apply_edit(
                chosen, str(edit), actor=actor, reason=f'{actor} 确认当前预览',
                current_chapter_sha256=preview.current_chapter_sha256,
                preview_subject_sha256=preview.preview_subject_sha256,
                confirmation='formal')
            results.append(actor)
        except BaseException as exc:  # noqa: BLE001 - 子线程错误必须回传断言
            failures.append(exc)

    threads = [
        threading.Thread(target=apply, args=(ctx_a, 'editor-a')),
        threading.Thread(target=apply, args=(ctx_b, 'editor-b')),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 1
    assert len(failures) == 1 and isinstance(failures[0], SourceConflict)
    assert store_a.docs['stage5/ch01.json'].text == '候选人工稿。'
    assert store_a.write_round_calls + store_b.write_round_calls == 1
    assert len(store_a.rounds) + len(store_b.rounds) == 1
    assert len(write.human_actions.read_events(tmp_path)) == 1


def test_preview_edit_relative_path_is_resolved_from_the_book_directory(
        tmp_path, monkeypatch):
    ctx, _store, response, _outside = _edit_unit(
        tmp_path, monkeypatch, lambda *_args, **_kwargs: ([], [], []))
    relative = ctx.novel_dir / 'incoming' / 'ch01.json'
    relative.parent.mkdir(parents=True)
    relative.write_text(response.model_dump_json(), encoding='utf-8')

    preview = write.run_edit_preview(ctx, 'incoming/ch01.json')
    assert preview.output_ordinal == 1
    with pytest.raises(TerminalState, match='相对路径必须留在当前书目录'):
        write.run_edit_preview(ctx, '../ch01.json')


def test_recheck_preserves_paid_payload_and_backfills_trim_audit(
        tmp_path, monkeypatch):
    original_text = '甲,乙。\n丙丁戊己庚辛。'
    chapter = _stage5_chapter(
        text=original_text, budget_chars=4, errs=['旧阻断读数'])
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    rel = 'stage5/ch01.json'
    invalid_rel = 'stage5/ch01.INVALID.json'
    invalid = _stage5_chapter(
        text='这份问题稿仍待独立重验证。', budget_chars=4, errs=['待重验证'])
    store = _MemoryTrimStore({rel: chapter, invalid_rel: invalid})
    judged = {}
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda _segments, _book: (
            [{'slot': 's01', 'text': '若采用返回值就会破坏原稿。'}],
            write.M.NormalizeAccount(punct_fixed=3, quote_problems=['只观察'])))
    monkeypatch.setattr(
        write, '_judge',
        lambda text, _entry, _book, **kwargs: (
            judged.update(text=text, segments=kwargs['segments']) or [],
            [],
            ['新读数'],
        ))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: 0.25)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())

    write.run_recheck(SimpleNamespace(novel_dir=tmp_path / 'book'))

    updated = store.docs[rel]
    assert judged == {
        'text': chapter.text,
        'segments': [segment.model_dump() for segment in chapter.segments],
    }
    assert updated.segments == chapter.segments
    assert updated.model_dump(by_alias=True) == chapter.model_dump(by_alias=True)
    assert set(updated.model_dump(by_alias=True)) == {
        'output_ordinal', 'segments', '_provenance'}
    assert store.refreshed == [], 'recheck 不再改写正文或派生字段副本'
    assert store.docs[invalid_rel] is invalid
    assert invalid_rel not in store.archived
    assert store.docs['stage5/trim_audit_ch01.json'].before_sha256 == (
        write._sha256_text(chapter.text))


def test_revalidate_judges_and_promotes_the_live_invalid_segments_verbatim(
        tmp_path, monkeypatch):
    original_text = '甲,乙。\n丙丁戊己庚辛。'
    invalid = _stage5_chapter(
        text=original_text, budget_chars=4, errs=['旧阻断读数'])
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore({inv_rel: invalid})
    judged = {}
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda _segments, _book: (
            [{'slot': 's01', 'text': '若采用返回值就不再是原 INVALID。'}],
            write.M.NormalizeAccount(punct_fixed=3, quote_problems=['只观察'])))
    monkeypatch.setattr(
        write, '_judge',
        lambda text, _entry, _book, **kwargs: (
            judged.update(text=text, segments=kwargs['segments']) or [],
            [],
            ['当前判据读数'],
        ))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: 0.5)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())

    write.run_revalidate(
        SimpleNamespace(novel_dir=tmp_path / 'book'),
        actor='tester', reason='按当前规则重判通过')

    assert judged == {
        'text': invalid.text,
        'segments': [segment.model_dump() for segment in invalid.segments],
    }
    assert inv_rel not in store.docs
    assert store.promoted.segments == invalid.segments
    assert store.promoted.model_dump(by_alias=True) == invalid.model_dump(by_alias=True)
    assert set(store.promoted.model_dump(by_alias=True)) == {
        'output_ordinal', 'segments', '_provenance'}
    assert store.docs['stage5/trim_audit_ch01.json'].before_sha256 == (
        write._sha256_text(invalid.text))


def test_recheck_refuses_a_formal_chapter_whose_payload_ordinal_mismatches_path(
        tmp_path, monkeypatch):
    chapter = _stage5_chapter(
        text='路径说第一章，载体却自报第二章。', output_ordinal=2)
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    rel = 'stage5/ch01.json'
    store = _MemoryTrimStore({rel: chapter})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    with pytest.raises(ProvenanceRefused, match='提交主体错配'):
        write.run_recheck(SimpleNamespace(novel_dir=tmp_path / 'book'))

    assert store.docs == {rel: chapter}
    assert store.transactions == []


def test_revalidate_refuses_an_invalid_chapter_whose_payload_ordinal_mismatches_path(
        tmp_path, monkeypatch):
    invalid = _stage5_chapter(
        text='问题稿路径说第一章，载体却自报第二章。', output_ordinal=2)
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 3,
        'budget_chars': 3,
        'scenes': [{'slot': 's01', 'budget_chars': 3}],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore({inv_rel: invalid})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(write.M, 'overlap_ratio', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    with pytest.raises(ProvenanceRefused, match='提交主体错配'):
        write.run_revalidate(
            SimpleNamespace(novel_dir=tmp_path / 'book'),
            actor='tester', reason='尝试重判错主体')

    assert store.docs == {inv_rel: invalid}
    assert store.promoted is None
    assert store.transactions == []


@pytest.mark.parametrize('slots', (
    ('s01',),
    ('s01', 's99'),
    ('s02', 's01'),
))
def test_revalidate_refuses_an_edited_invalid_with_noncanonical_slots(
        tmp_path, monkeypatch, slots):
    invalid = _stage5_chapter(text='占位正文。').model_copy(update={
        'segments': [
            write.ChapterSegment(slot=slot, text=f'{slot} 正文。')
            for slot in slots
        ],
    })
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': 6,
        'budget_chars': 6,
        'scenes': [
            {'slot': 's01', 'budget_chars': 3},
            {'slot': 's02', 'budget_chars': 3},
        ],
    }
    book = _trim_book(entry)
    inv_rel = 'stage5/ch01.INVALID.json'
    store = _MemoryTrimStore({inv_rel: invalid})
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    with pytest.raises(TerminalState, match='canonical segment 契约不成立'):
        write.run_revalidate(
            SimpleNamespace(novel_dir=tmp_path / 'book'),
            actor='tester', reason='尝试提升被改坏的 INVALID')

    assert store.docs == {inv_rel: invalid}
    assert store.promoted is None
    assert store.transactions == []



def test_current_invalid_carrier_refuses_an_injected_retired_budget():
    """任务预算只来自已验证的当前任务书；INVALID 不能另加一份预算绕过 trim。"""
    from pydantic import ValidationError
    baseline = _stage5_chapter(text='正文保持不变。')
    payload = baseline.model_dump(by_alias=True)
    payload['budget_chars'] = 3000
    with pytest.raises(ValidationError, match='budget_chars'):
        write.Chapter.model_validate(payload)
    assert set(baseline.model_dump(by_alias=True)) == {
        'output_ordinal', 'segments', '_provenance'}


def test_same_text_commit_keeps_the_matching_trim_resolution(monkeypatch):
    from novel.stages.stage5 import trim_resolution

    text = '同一份超长正文不会因为只刷新元数据而重开人工决定。'
    segments = [{'slot': 's01', 'text': text}]
    chapter = _stage5_chapter(text=text, budget_chars=3)
    _notes, audit = write._over_length_audit(
        segments, {'output_ordinal': 1, 'budget_chars': 3})
    assert audit is not None
    resolution = object()
    store = _MemoryTrimStore({
        'stage5/ch01.json': object(),
        'stage5/trim_audit_ch01.json': audit,
        'stage5/trim_resolution_ch01.json': resolution,
    })
    matched = []
    monkeypatch.setattr(
        trim_resolution, 'matches_current_audit',
        lambda got_store, got_audit, got_sha, *, ordinal: (
            matched.append((got_store, got_audit, got_sha)) or True))

    write._commit_canonical_chapter(
        store,
        chapter,
        expected_ordinal=1,
        commit=lambda rel, artifact: store.write_round(rel, artifact),
        entry={'output_ordinal': 1, 'scenes': [{'slot': 's01', 'budget_chars': 3}]},
    )

    assert matched == [(store, audit, 'a' * 64)]
    assert store.docs['stage5/trim_audit_ch01.json'] == audit
    assert store.docs['stage5/trim_resolution_ch01.json'] is resolution
    assert store.archived == []


def test_a_successful_retry_uses_transactional_round_write_for_superseded_files(
        tmp_path, monkeypatch):
    class Store:
        def __init__(self, live):
            self.live = set(live)
            self.archived = []
            self.transactions = []

        def exists(self, rel):
            return rel in self.live

        def archive_round(self, rel, *, keep=False):
            self.archived.append(rel)
            if not keep:
                self.live.remove(rel)

        @contextmanager
        def history_transaction(self, *rels):
            self.transactions.append(rels)
            yield

        def write_round(self, rel, _artifact):
            # Mirror ArtifactStore.write_round closely enough for this producer
            # boundary test: retain the old formal as history, replace live, and
            # retire the INVALID sibling only after the replacement succeeds.
            with self.history_transaction(rel):
                if self.exists(rel):
                    self.archive_round(rel, keep=True)
                self.live.add(rel)
                invalid_rel = rel.replace('.json', '.INVALID.json')
                if self.exists(invalid_rel):
                    self.archive_round(invalid_rel)
            return rel

        def write_invalid(self, rel, _artifact, *, gates_skipped):
            assert gates_skipped == []
            invalid_rel = rel.replace('.json', '.INVALID.json')
            with self.history_transaction(invalid_rel):
                if self.exists(invalid_rel):
                    self.archive_round(invalid_rel)
                self.live.add(invalid_rel)
            return invalid_rel

    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    blocking = []
    monkeypatch.setattr(
        write, '_judge',
        lambda *_a, **_kw: (['judged'], list(blocking), []))
    monkeypatch.setattr(
        write, '_chapter_record',
        lambda entry, segments, *_a, **_kw: SimpleNamespace(
            output_ordinal=entry['output_ordinal'], budget_chars=1,
            segments=[write.ChapterSegment.model_validate(item) for item in segments]))
    monkeypatch.setattr(write, '_over_length_audit', lambda *_a, **_kw: ([], None))

    ctx = SimpleNamespace(
        novel_dir=tmp_path / 'book',
        accounting={}, account=lambda *_a, **_kw: None)
    entry = {'output_ordinal': 5, 'scenes': [{'slot': 's01', 'budget_chars': 1}]}
    kwargs = {
        'segments': [{'slot': 's01', 'text': '合格正文。'}],
        'mat': 'given',
        'account': write.M.NormalizeAccount(),
        'provenance': object(),
    }

    rel = 'stage5/ch05.json'
    invalid = 'stage5/ch05.INVALID.json'
    rejected = 'stage5/ch05.rejected.json'
    audit = 'stage5/trim_audit_ch05.json'
    resolution = 'stage5/trim_resolution_ch05.json'
    store = Store({rel, invalid, rejected, audit, resolution})
    where, errors, findings, trim_notes = write._write_chapter(
        ctx, store, entry, SimpleNamespace(), **kwargs)
    assert (where, errors, findings, trim_notes) == (rel, [], ['judged'], [])
    assert set(store.archived) == {rel, invalid, rejected, audit, resolution}
    assert store.transactions == [
        (rel, rejected),
        (rel, 'stage5/trim_audit_ch05.json', 'stage5/trim_resolution_ch05.json'),
        (rel,),
    ]
    assert rel in store.live and invalid not in store.live

    blocking.append('still invalid')
    store = Store({rel, invalid, rejected, audit, resolution})
    where, errors, findings, trim_notes = write._write_chapter(
        ctx, store, entry, SimpleNamespace(), **kwargs)
    assert (where, errors, findings, trim_notes) == (
        invalid, ['still invalid'], ['judged'], [])
    assert set(store.archived) == {invalid, rejected}
    assert store.transactions == [(rel, rejected), (invalid,)]
    assert rel in store.live, '新问题稿不能销毁上一轮正式稿'
    assert audit in store.live and resolution in store.live, (
        'INVALID 只记录失败候选，不能让旧 canonical 的 trim 决定失效')


def test_revise_regression_gets_exactly_one_refresh_resample(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def ask_json(self, *_args, **kwargs):
            self.calls.append(kwargs)
            text = '第一次退化稿' if len(self.calls) == 1 else '第二次合格稿'
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': text}],
            })

    monkeypatch.setattr(write.prompts, 'build_material',
                        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'revise_user', lambda *_a, **_kw: 'USER')
    monkeypatch.setattr(write.M, 'normalize_chapter',
                        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_over_length_audit',
                        lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write.checks, 'trim_regressions',
        lambda _prev, new, *_a, **_kw: ['丢失结尾'] if '第一次' in new else [])
    client = Client()

    got = write._generate_chapter(
        {'output_ordinal': 1, 'scenes': [{'slot': 's01'}]},
        SimpleNamespace(quote_style='curly', sep_chars=[]),
        client, revise={1: ('旧正式稿', [], 'review-fp')},
        system='SYS', progress=SimpleNamespace())

    assert got['text'] == '第二次合格稿'
    assert len(client.calls) == 2
    assert 'cache_policy' not in client.calls[0]
    assert client.calls[1]['cache_policy'].name == 'refresh-write'


def test_two_regressed_revisions_preserve_formal_and_write_rejected(monkeypatch, tmp_path):
    from contracts.schema.stage5 import RejectedRevision
    from novel.store import ArtifactStore

    rejected = {
        'output_ordinal': 1,
        'why': ['丢失结尾'],
        'fixes': [['rule-a', 'evidence-a']],
        'attempts': [
            {'segments': [{'slot': 's01', 'text': '退化一'}]},
            {'segments': [{'slot': 's01', 'text': '退化二'}]},
        ],
    }
    monkeypatch.setattr(write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(write, '_generate_chapter', lambda *_a, **_kw: {'rejected': rejected})
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: object())
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    root = tmp_path / '修订退化_812'
    try:
        formal = root / 'stage5' / 'ch01.json'
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b'paid-formal-draft')
        ctx = RunContext(
            novel_dir=root, node='stage5.write', paid=False,
            code=prov.code_version(), cache=CachePolicy.default(),
            ledger=CostLedger(), observer=lambda _event: None)
        store = ArtifactStore(ctx)

        for _attempt in range(2):
            with pytest.raises(GateBlocked, match='全部没落成正式稿'):
                write._run_group(
                    ctx, store, SimpleNamespace(), [{'output_ordinal': 1}],
                    SimpleNamespace(concurrency=1),
                    revise={1: ('旧正式稿', [], 'review-fp')})
            assert formal.read_bytes() == b'paid-formal-draft'

        live, _raw = store.read_own(
            RejectedRevision, 'stage5/ch01.rejected.json')
        archived, _raw = store.read_own(
            RejectedRevision,
            'stage5/_rounds/ch01.rejected.r1.json')
        for artifact in (live, archived):
            assert [attempt.text for attempt in artifact.attempts] == [
                '退化一', '退化二']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_resume_after_interruption_only_schedules_missing_chapters(monkeypatch):
    book = SimpleNamespace(chapters={
        1: {'output_ordinal': 1},
        2: {'output_ordinal': 2},
        3: {'output_ordinal': 3},
    })
    store = SimpleNamespace(exists=lambda rel: rel == 'stage5/ch01.json')

    assert [row['output_ordinal'] for row in write._chapters_of(store, book, None)] == [2, 3]


def test_canonical_loader_ignores_legacy_and_forwards_freshness_policy():
    root = Path(tempfile.mkdtemp(prefix='stage5-canonical-loader-'))
    try:
        for rel in (
            'stage5/ch01.json',
            'stage5/ch02.INVALID.json',
            'stage5/ch03.rejected.json',
            'stage5/deep/ch04.json',
            'stage5/shallow/draft.md',
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')

        calls = []
        chapter = SimpleNamespace(
            output_ordinal=1,
            provenance=SimpleNamespace(round=2),
            text='唯一正式正文。',
            model_dump=lambda **_kwargs: {
                'output_ordinal': 1,
                'from_source_ordinals': [1],
                'chars': 7,
            },
        )

        class Store:
            @staticmethod
            def path(rel):
                return root / rel

            @staticmethod
            def read_judged(_model, rel, *, project, require_current):
                calls.append((rel, project, require_current))
                return chapter, 'a' * 10, 'b' * 10

        by_ord, invalids, judged = write.M.load_drafts(
            Store(), require_current=False)

        assert list(by_ord) == [1]
        assert invalids == ['stage5/ch02.INVALID.json']
        assert list(judged) == ['stage5/ch01.json']
        assert [(rel, current) for rel, _project, current in calls] == [
            ('stage5/ch01.json', False),
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_legacy_stage5_discovery_is_explicit_and_canonical_is_not_legacy():
    root = Path(tempfile.mkdtemp(prefix='stage5-legacy-discovery-'))
    try:
        legacy = (
            'stage5/deep/ch01.json',
            'stage5/deep/ch01.rejected.json',
            'stage5/shallow/draft.md',
        )
        for rel in (*legacy, 'stage5/ch01.json'):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        store = SimpleNamespace(path=lambda rel: root / rel)

        assert write.M.legacy_stage5_paths(store) == [
            'stage5/deep', 'stage5/shallow']
        assert all(write.M.is_legacy_stage5_rel(rel) for rel in legacy)
        assert write.M.is_legacy_stage5_rel(r'stage5\deep\draft.md')
        assert not write.M.is_legacy_stage5_rel('stage5/ch01.json')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_legacy_arm_directory_is_still_incompatible():
    root = Path(tempfile.mkdtemp(prefix='stage5-empty-legacy-arm-'))
    try:
        (root / 'stage5' / 'deep').mkdir(parents=True)
        store = SimpleNamespace(path=lambda rel: root / rel)
        assert write.M.legacy_stage5_paths(store) == ['stage5/deep']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_paid_write_refuses_legacy_before_loading_upstream(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix='stage5-legacy-preflight-'))
    try:
        legacy = root / 'stage5' / 'deep' / 'ch01.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{}', encoding='utf-8')
        store = SimpleNamespace(path=lambda rel: root / rel)
        monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)

        loaded = []
        monkeypatch.setattr(
            write.review, 'load_book',
            lambda *_args: loaded.append(True),
        )
        with pytest.raises(TerminalState, match='legacy_incompatible'):
            write.run_write(
                SimpleNamespace(novel_dir=root, executor_profile=None), object())
        assert loaded == [], '旧盘面在终态前不应读取上游，更不能进入付费写作'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_legacy_direct_write_holds_the_shared_book_lock_through_the_wire(
        tmp_path, monkeypatch):
    """无 agent journal 的直 CLI 路必须把输入快照到最终提交整段串行化。"""
    ctx = SimpleNamespace(
        novel_dir=tmp_path / 'book', executor_profile=None)
    store = SimpleNamespace()
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(
        write.stage5_runs, 'refuse_foreign_prepared_before_wire',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.review, 'load_book', lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        write, '_chapters_of', lambda *_args, **_kwargs: [{'output_ordinal': 1}])
    started = threading.Event()
    acquired = threading.Event()
    worker: threading.Thread | None = None
    crossed_wire: list[bool] = []
    result = object()

    def competing_writer() -> None:
        started.set()
        with write.stage5_runs.book_commit_ownership(ctx.novel_dir):
            acquired.set()

    def fake_group(*_args, **_kwargs):
        nonlocal worker
        worker = threading.Thread(target=competing_writer)
        worker.start()
        assert started.wait(timeout=5)
        crossed_wire.append(acquired.wait(timeout=0.1))
        return result

    monkeypatch.setattr(write, '_run_group', fake_group)

    assert write.run_write(ctx, object()) is result
    assert worker is not None
    worker.join(timeout=5)
    assert crossed_wire == [False]
    assert acquired.is_set()


@pytest.mark.parametrize('write_path', [
    'initial', 'revise', 'partial_success', 'response_repair_pause',
])
def test_parallel_write_paths_land_each_successful_canonical_chapter(
        tmp_path, monkeypatch, write_path):
    """单章失败或暂停不得取消同组内其他 canonical 章的成功落盘。"""
    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', lambda *_a, **_kw: None)
    root = tmp_path / '书名_123'
    root.mkdir()
    entries = {
        ordinal: {
            'output_ordinal': ordinal,
            'from_source_ordinals': [ordinal],
            'source_chars': 6,
            'budget_chars': 6,
            'segments': [{'slot': 's01'}],
        }
        for ordinal in (1, 2)
    }
    book = SimpleNamespace(
        chapters=entries, quote_style='curly', sep_chars=[], source_text={})

    def record(entry, segments):
        text = write._segments_text(segments)
        return {
            'output_ordinal': entry['output_ordinal'],
            'from_source_ordinals': [entry['output_ordinal']],
            'chars': write.M.chars_of(text),
            'overlap_excl_verbatim': 0.0,
            'text': text,
        }

    class Store:
        def __init__(self):
            self.by_ord = {}
            self.invalid = None
            self.writes = []

        def path(self, rel):
            return root / rel

        def exists(self, rel):
            if rel.endswith('.INVALID.json'):
                return self.invalid is not None
            if rel.startswith('stage5/ch') and rel.endswith('.json'):
                digits = ''.join(char for char in Path(rel).stem if char.isdigit())
                return bool(digits) and int(digits) in self.by_ord
            return False

        def read_own(self, _model, rel, **_kwargs):
            if rel.endswith('.INVALID.json'):
                assert self.invalid is not None
                return self.invalid, {}
            ordinal = int(''.join(char for char in Path(rel).stem if char.isdigit()))
            row = self.by_ord[ordinal]
            art = SimpleNamespace(
                segments=[SimpleNamespace(
                    model_dump=lambda row=row: {
                        'slot': 's01', 'text': row['text'],
                    })],
                text=row['text'], provenance=object(),
            )
            return art, {'_provenance': {'inputs': {}}}

        def write(self, rel, artifact):
            self.writes.append(rel)
            if rel.startswith('stage5/ch') and not rel.endswith('.INVALID.json'):
                ordinal = int(''.join(char for char in Path(rel).stem if char.isdigit()))
                self.by_ord[ordinal] = artifact
            return rel

        def write_round(self, rel, artifact):
            return self.write(rel, artifact)

        def next_round(self, _rel):
            return 1

        @contextmanager
        def history_transaction(self, *_rels):
            yield

        def promote_invalid(self, rel, _model, **kwargs):
            self.by_ord[int(''.join(char for char in Path(rel).stem if char.isdigit()))] = (
                kwargs['artifact_override'])
            self.invalid = None

    store = Store()
    ctx = SimpleNamespace(
        accounting={},
        account=lambda *_args, **_kwargs: None,
        observe=lambda *_args, **_kwargs: None,
        llm_provenance_for_chapter=lambda *_args, **_kwargs: object(),
        rewrite_provenance=lambda provenance, **_kwargs: provenance,
        free_provenance=lambda *_args, **_kwargs: object(),
    )

    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: book)
    monkeypatch.setattr(
        write.M, 'load_drafts',
        lambda got, *, require_current: (dict(got.by_ord), [], {}))
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(write.prompts, 'write_system', lambda *_args, **_kwargs: 'SYS')
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(
        write, '_chapter_record',
        lambda entry, segments, _book, **_kwargs: record(entry, segments))
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_args, **_kwargs: SimpleNamespace(given='given'))

    def land(_ctx, got_store, entry, _book, *, segments, **_kwargs):
        artifact = record(entry, segments)
        rel = f'stage5/ch{entry["output_ordinal"]:02d}.json'
        got_store.write_round(rel, artifact)
        return rel, [], [], []

    monkeypatch.setattr(write, '_write_chapter', land)

    def generated(entry, *_args, **_kwargs):
        ordinal = entry['output_ordinal']
        if write_path == 'partial_success' and ordinal == 1:
            return {'error': '没有拿到正文'}
        if write_path == 'response_repair_pause' and ordinal == 1:
            return {'response_repair_required': ResponseRepairRequired(
                '等待修复', attempt_id='a' * 16,
                candidate_path='candidate.json', original_path='original.txt',
                hint='response-repair')}
        segments = [{'slot': 's01', 'text': f'第{ordinal}章新正文。'}]
        return {
            'entry': entry, 'ordinal': ordinal,
            'mat': SimpleNamespace(given='given'), 'user': 'USER',
            'segments': segments, 'text': write._segments_text(segments),
            'account': write.M.NormalizeAccount(), 'prev_text': '旧正文。', 'fixes': [],
            'review_fp': 'review-fp', 'accepted_purpose': f'ch{ordinal}',
        }

    monkeypatch.setattr(write, '_generate_chapter', generated)
    client = SimpleNamespace(
        concurrency=1,
        model='test-model',
        accepted_request_fingerprint=lambda purpose: ('a' * 63 + purpose[-1]),
        accepted_model=lambda _purpose: 'test-model',
    )

    if write_path == 'initial':
        write._run_group_parallel(
            ctx, store, book, [entries[1]], client,
            revise=None)
    elif write_path == 'revise':
        store.by_ord[1] = record(entries[1], [{'slot': 's01', 'text': '旧正文。'}])
        write._run_group_parallel(
            ctx, store, book, [entries[1]], client,
            revise={1: ('旧正文。', [], 'review-fp')})
    elif write_path == 'partial_success':
        store.by_ord[1] = record(entries[1], [{'slot': 's01', 'text': '第一章旧正文。'}])
        write._run_group_parallel(
            ctx, store, book, [entries[1], entries[2]], client,
            revise=None)
    elif write_path == 'response_repair_pause':
        store.by_ord[1] = record(entries[1], [{'slot': 's01', 'text': '第一章旧正文。'}])
        with pytest.raises(ResponseRepairRequired):
            write._run_group_parallel(
                ctx, store, book, [entries[1], entries[2]], client,
                revise=None)
        assert 2 in store.by_ord, '一章等待修复不能取消另一章的成功落盘'
    assert sorted(store.by_ord) == ([1] if write_path in {'initial', 'revise'} else [1, 2])
    assert all(rel.startswith('stage5/ch') for rel in store.writes)


def test_parallel_batch_trim_readback_does_not_poison_the_next_chapter(
        tmp_path, monkeypatch):
    """并发候选按章 formalize 时，前章自有 trim 状态不得进入后章 inputs。"""
    seed = factories.stage5_book()
    first = copy.deepcopy(seed.chapters[1])
    first['scenes'][0]['budget_chars'] = 8
    second = copy.deepcopy(seed.chapters[1])
    second['output_ordinal'] = 2
    second['scenes'][0]['budget_chars'] = 200

    novel_dir = tmp_path / '双章来源账_812'
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    clean = novel_dir / 'stage0' / 'clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text('固定测试根。\n', encoding='utf-8', newline='')
    ctx = RunContext(
        novel_dir=novel_dir,
        node='stage5.write',
        paid=True,
        code='test-stage5-trim-source-account',
        cache=CachePolicy.default(),
        ledger=CostLedger(),
        observer=lambda _event: None,
    )
    store = ArtifactStore(ctx)

    writer_context = WriterContext(
        chapters=[first, second],
        _provenance=ctx.free_provenance(CtxProvenance),
    )
    writer_payload = writer_context.model_dump(by_alias=True, mode='json')
    writer_payload['_provenance'] = writer_context.provenance.to_disk()
    writer_bytes = (json.dumps(
        encode_artifact(STAGE4_WRITER_CONTEXT, writer_payload),
        ensure_ascii=False,
        indent=2,
    ) + '\n').encode('utf-8')
    writer_path = store.path(STAGE4_WRITER_CONTEXT)
    writer_path.parent.mkdir(parents=True)
    writer_path.write_bytes(writer_bytes)
    store.read(WriterContext, STAGE4_WRITER_CONTEXT)
    baseline_inputs = ctx.edges
    assert set(baseline_inputs) == {STAGE4_WRITER_CONTEXT}

    rows = [
        row.model_dump(by_alias=True, mode='json')
        for row in writer_context.chapters
    ]
    book = write.M.Book(
        title=seed.title,
        ctx={'chapters': rows},
        measurable=seed.measurable,
        source_text=seed.source_text,
        secrets=seed.secrets,
        characters=seed.characters,
    )
    monkeypatch.setattr(
        write, 'assert_chapter_candidate_current',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write, '_judge', lambda *_args, **_kwargs: ([], [], []))

    def artifact(entry, text):
        ordinal = int(entry['output_ordinal'])
        rel = write._rel(ordinal)
        provenance = ctx.llm_provenance_for_chapter(
            ordinal,
            write.RoundLlmProvenance,
            prompts={'SYS': 'TEST-SYS'},
            model='requested-model',
            adopted_models=['actual-model'],
            round=store.next_round(rel),
            request_fingerprint=str(ordinal) * 64,
        )
        return write._chapter_record(
            entry,
            [{'slot': 's01', 'text': text}],
            provenance=provenance,
        )

    # ``_run_group_parallel`` 的 agent 路在全部并发候选闭合后按章号正式化；
    # 这里从那个确定性提交边界开始，先覆盖超限的第 1 章。
    first_entry = book.chapters[1]
    first_prepared = write.prepare_chapter_candidate(
        first_entry, book, revision=None, system='TEST-SYS')
    first_artifact = artifact(
        first_entry, '第一章超限正文必须原样保留并生成审计。' * 4)
    first_rel = write._rel(1)
    _where, first_trim_notes = write._commit_canonical_chapter(
        store,
        first_artifact,
        expected_ordinal=1,
        commit=lambda rel, candidate: store.write_round(rel, candidate),
        entry=first_entry,
    )
    first_audit_rel = write.artifact_rel('stage5.trim_audit', nn=1)
    assert first_trim_notes and store.exists(first_audit_rel)

    write.verify_chapter_delivery(
        ctx, first_prepared, first_artifact, rel=first_rel)
    assert ctx.edges == baseline_inputs, 'trim readback 污染了 run-global inputs'

    # 同一 live audit 的 crash preflight 与 matching-audit 提交路也必须只读不记边。
    desired, audit_sha, resolution_sha = write._trim_preflight_expectations(
        ctx, store, first_prepared, first_artifact)
    assert desired is not None and audit_sha is not None and resolution_sha is None
    before_audit = store.path(first_audit_rel).read_bytes()
    write._commit_canonical_chapter(
        store,
        first_artifact,
        expected_ordinal=1,
        commit=lambda rel, _candidate: store.path(rel),
        entry=first_entry,
    )
    assert store.path(first_audit_rel).read_bytes() == before_audit
    assert ctx.edges == baseline_inputs, '预存 TrimAudit 被误记成了 Stage5 写作输入'

    second_entry = book.chapters[2]
    second_prepared = write.prepare_chapter_candidate(
        second_entry, book, revision=None, system='TEST-SYS')
    second_artifact = artifact(second_entry, '第二章正常正文。')
    assert set(second_artifact.provenance.inputs) == set(baseline_inputs)
    assert first_audit_rel not in second_artifact.provenance.inputs
    second_rel = write._rel(2)
    write._commit_canonical_chapter(
        store,
        second_artifact,
        expected_ordinal=2,
        commit=lambda rel, candidate: store.write_round(rel, candidate),
        entry=second_entry,
    )
    write.verify_chapter_delivery(
        ctx, second_prepared, second_artifact, rel=second_rel)

    assert not store.exists(write.artifact_rel('stage5.trim_audit', nn=2))
    assert store.freshness(second_rel).state == 'fresh'
    assert ctx.edges == baseline_inputs


def test_one_semantically_failed_chapter_does_not_abort_the_rest(monkeypatch):
    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', lambda *_a, **_kw: None)
    class Client:
        model = 'test-model'

        def __init__(self):
            self.calls = []

        def ask_json(self, *args, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LlmFailed('同配置两采与模型兜底均被拒绝')
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '第二章完整结束。'}],
            })

        @staticmethod
        def accepted_request_fingerprint(_purpose):
            return 'b' * 64

        @staticmethod
        def accepted_model(_purpose):
            return 'test-model'

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        @contextmanager
        def history_transaction(*_rels):
            yield

        @staticmethod
        def write_round(rel, _artifact):
            return rel

    client = Client()
    captured = {}
    events = []
    report = object()
    monkeypatch.setattr(
        write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'build_user', lambda _mat: 'USER')
    monkeypatch.setattr(
        write.M, 'normalize_chapter',
        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write, '_over_length_audit', lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write, '_write_chapter',
        lambda *_a, **_kw: ('chapter.json', [], ['judged'], []))

    def fake_report(*_args, **kwargs):
        captured.update(kwargs)
        return report

    monkeypatch.setattr(write, '_report', fake_report)
    ctx = SimpleNamespace(
        llm_provenance_for_chapter=lambda *_a, **_kw: object(),
        observe=lambda event, **fields: events.append({'event': event, **fields}))
    todo = [
        {'output_ordinal': 1, 'segments': [{'slot': 's01'}]},
        {'output_ordinal': 2, 'segments': [{'slot': 's01'}]},
    ]

    book = SimpleNamespace(quote_style='curly', sep_chars=[])
    assert write._run_group(
        ctx, Store(), book, todo, client, revise=None) is report
    assert len(client.calls) == 2
    assert all(call['model_cls'] is write.ChapterResponse for call in client.calls)
    assert all(callable(call['validator']) for call in client.calls)
    assert [(call['progress'].chapter, call['progress'].index, call['progress'].total)
            for call in client.calls] == [(1, 1, 2), (2, 2, 2)]
    assert [(event['chapter'], event['outcome']) for event in events] == [
        (1, 'failed'), (2, 'ok'),
    ]
    assert captured['written'] and '第 1 章没拿到完整正文' in captured['blocked'][0]


def test_production_chapter_provenance_uses_the_accepted_request(
        monkeypatch):
    """正式章的 request 身份来自 accepted attempt 反查，不按首档参数估算。"""
    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', lambda *_a, **_kw: None)
    accepted_request = 'b' * 64
    captured: dict[str, object] = {}

    class Client:
        concurrency = 1
        model = 'test-model'

        def ask_json(self, system, user, **kwargs):
            captured['wire'] = (system, user, kwargs['purpose'])
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '完整章节。'}],
            })

        def accepted_request_fingerprint(self, purpose):
            captured['accepted_purpose'] = purpose
            return accepted_request

        @staticmethod
        def accepted_model(_purpose):
            return 'actual-model'

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        @contextmanager
        def history_transaction(*_rels):
            yield

        @staticmethod
        def write_round(rel, _artifact):
            return rel

    monkeypatch.setattr(write.prompts, 'write_system', lambda **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a: SimpleNamespace(given='GIVEN'))
    monkeypatch.setattr(
        write.prompts, 'build_user', lambda mat: f'USER:{mat.given}')
    monkeypatch.setattr(
        write.M, 'normalize_chapter',
        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(write, '_over_length_audit', lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    monkeypatch.setattr(
        write, '_write_chapter',
        lambda *_a, **_kw: ('stage5/ch01.json', [], [], []))
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: report)

    def provenance(chapter, cls, /, **kwargs):
        captured['provenance'] = (chapter, cls, kwargs)
        return object()

    ctx = SimpleNamespace(
        llm_provenance_for_chapter=provenance,
        observe=lambda *_a, **_kw: None,
    )
    entry = {'output_ordinal': 1, 'segments': [{'slot': 's01'}]}
    book = SimpleNamespace(quote_style='curly', sep_chars=[])

    assert write._run_group(
        ctx, Store(), book, [entry], Client(), revise=None) is report
    assert captured['wire'] == (
        'SYS', 'USER:GIVEN', 'stage5.write·ch1')
    assert captured['accepted_purpose'] == 'stage5.write·ch1'
    chapter, cls, kwargs = captured['provenance']
    assert chapter == 1 and cls is write.RoundLlmProvenance
    assert kwargs['prompts'] == {'SYS': 'SYS'}
    assert kwargs['request_fingerprint'] == accepted_request
    assert kwargs['model'] == 'test-model'
    assert kwargs['adopted_models'] == ['actual-model']


class _LandingStore:
    """只替换文件系统；producer 的响应、provenance 与 Chapter 载体全走真实现。"""

    def __init__(self):
        self.landed = []

    @staticmethod
    def next_round(_rel):
        return 1

    @staticmethod
    def exists(_rel):
        return False

    @staticmethod
    @contextmanager
    def history_transaction(*_rels):
        yield

    def write_round(self, rel, artifact):
        self.landed.append((rel, artifact))
        return rel

    def read(self, _model, rel):
        return next(artifact for path, artifact in reversed(self.landed) if path == rel)

    @staticmethod
    def prospective_sha256(_rel, artifact):
        return hashlib.sha256(json.dumps(
            artifact.model_dump(by_alias=True, mode='json'),
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def full_sha256_or_none(self, rel):
        found = next((artifact for path, artifact in reversed(self.landed) if path == rel), None)
        return None if found is None else self.prospective_sha256(rel, found)

    @staticmethod
    def write_invalid(*_args, **_kwargs):
        raise AssertionError('合格响应不应落到 INVALID')


class _LandingContext:
    """保留真实 LlmClient → ledger → RoundLlmProvenance 联结的最小 run。"""

    node = 'stage5.write'
    sid = '123'
    code = 'test-code'

    def __init__(self):
        self.novel_dir = Path(tempfile.mkdtemp()) / 'book'
        self.cache = CachePolicy.default()
        from novel.llm.ledger import ledger_path
        self.ledger = CostLedger(path=ledger_path(self.novel_dir.parent))
        self.run_id = self.ledger.run_id
        self.parent_run_id = self.ledger.parent_run_id
        self.edges = {}
        self._accounting = {}
        self.events = []

    @property
    def accounting(self):
        return dict(self._accounting)

    def account(self, key, value):
        self._accounting[key] = value

    @staticmethod
    def require_observed_roots_current():
        return None

    def observe(self, event, **fields):
        self.events.append({'event': event, **fields})

    def llm_provenance_for_chapter(
            self, chapter, cls, /, *, prompts, model, **extra):
        out = cls(
            code=self.code,
            root_clean_sha1='0' * 40,
            inputs={},
            prompts={key: prov.fingerprint(value) for key, value in prompts.items()},
            model=model,
            usage=self.ledger.chapter_usage(chapter),
            **extra,
        )
        self.ledger.commit_chapter(chapter)
        return out


def _production_response(text='完整章节。', *, model=FLASH_MODEL):
    return ChatResponse(
        content=json.dumps(
            {'segments': [{'slot': 's01', 'text': text}]},
            ensure_ascii=False),
        finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 5},
        model=model,
    )


def _production_request(book, entry, *, effort='high', thinking=True):
    material = write.prompts.build_material(entry, book)
    return ChatRequest(
        model=FLASH_MODEL,
        system=write.prompts.write_system(),
        user=write.prompts.build_user(material),
        reasoning_effort=effort,
        thinking=thinking,
        json_mode=JSON_OBJECT_MODE,
        max_tokens=write.MAX_TOKENS,
    )


def _run_real_production_landing(monkeypatch, transport, *, cache=None):
    """只豁免与本回归无关的正文判据；其余写作落盘链走生产实现。"""
    book = factories.stage5_book()
    entry = book.chapters[1]
    ctx = _LandingContext()
    store = _LandingStore()
    client = LlmClient(
        ctx,
        transport,
        cache=cache or ResponseCache(),
        model=FLASH_MODEL,
        max_retries=0,
        concurrency=1,
        warmup=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: report)

    got = write._run_group(
        ctx, store, book, [entry], client, revise=None)
    assert got is report
    [(rel, chapter)] = store.landed
    assert rel == 'stage5/ch01.json'
    # 真正走一次当前 Chapter schema 往返，避免 fake provenance 再掩盖长度不合约。
    reloaded = write.Chapter.model_validate(chapter.model_dump(by_alias=True))
    return ctx, client, reloaded, book, entry


def test_real_production_write_lands_a_full_request_sha256(monkeypatch):
    transport = FakeTransport.always(_production_response())

    ctx, client, chapter, _book, _entry = _run_real_production_landing(
        monkeypatch, transport)

    assert len(transport.calls) == 1
    expected = request_fingerprint(transport.calls[0])
    assert len(expected) == 64
    assert chapter.provenance.request_fingerprint == expected
    assert chapter.provenance.adopted_models == [FLASH_MODEL]
    assert client.accepted_request_fingerprint('stage5.write·ch1') == expected
    assert ctx.ledger.calls_made == 1


def test_production_write_refuses_to_land_without_an_accepted_request_identity(
        monkeypatch):
    monkeypatch.setattr(
        LlmClient, 'accepted_request_fingerprint', lambda *_args: None)
    transport = FakeTransport.always(_production_response())

    with pytest.raises(ProvenanceRefused, match='没有 accepted attempt'):
        _run_real_production_landing(monkeypatch, transport)


def test_real_production_write_reuses_the_existing_32_char_cache_path(monkeypatch):
    cache_dir = Path(tempfile.mkdtemp())
    cache = ResponseCache(cache_dir)
    book = factories.stage5_book()
    entry = book.chapters[1]
    request = _production_request(book, entry)
    old_key = cache_key(request)
    assert len(old_key) == 32
    cache.put(old_key, _production_response('缓存里的完整章节。'))
    assert (cache_dir / f'{old_key}.json').is_file()
    transport = FakeTransport.always(_production_response('不应上线。'))

    ctx, _client, chapter, _book, _entry = _run_real_production_landing(
        monkeypatch, transport, cache=ResponseCache(cache_dir))

    assert transport.calls == []
    assert ctx.ledger.calls_made == 0 and ctx.ledger.calls_cached == 1
    assert chapter.provenance.request_fingerprint == request_fingerprint(request)
    assert len(chapter.provenance.request_fingerprint) == 64
    assert (cache_dir / f'{old_key}.json').is_file(), '兼容缓存不应被迁移或改名'


def test_real_production_write_binds_provenance_to_the_accepted_pro_fallback(
        monkeypatch):
    truncated = ChatResponse(
        content='{"segments":[', finish_reason='length', model=FLASH_MODEL)
    accepted = _production_response(
        'Pro 兜底后的完整章节。', model=PRO_FALLBACK_MODEL)
    transport = FakeTransport(
        responder=lambda request, _index: (
            truncated if request.model == FLASH_MODEL else accepted))

    ctx, client, chapter, _book, _entry = _run_real_production_landing(
        monkeypatch, transport)

    assert [req.model for req in transport.calls] == [
        FLASH_MODEL, PRO_FALLBACK_MODEL]
    assert [(req.reasoning_effort, req.thinking) for req in transport.calls] == [
        DEFAULT_RUNGS[0], DEFAULT_RUNGS[0]]
    rejected_fp = request_fingerprint(transport.calls[0])
    accepted_fp = request_fingerprint(transport.calls[1])
    assert accepted_fp != rejected_fp
    assert chapter.provenance.request_fingerprint == accepted_fp
    assert chapter.provenance.model == FLASH_MODEL
    assert chapter.provenance.adopted_models == [PRO_FALLBACK_MODEL]
    assert client.accepted_request_fingerprint('stage5.write·ch1') == accepted_fp
    assert ctx.ledger.calls_made == 2


@pytest.mark.parametrize(('chapters', 'expected_peak'), [(2, 2), (19, 4), (40, 4)])
def test_stage5_chapters_run_together_up_to_four(
        monkeypatch, chapters, expected_peak):
    monkeypatch.setattr(write, '_record_legacy_chapter_delivery', lambda *_a, **_kw: None)
    all_entered = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    class Client:
        concurrency = 32
        model = 'test-model'

        def ask_json(self, *args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == expected_peak:
                    all_entered.set()
            assert all_entered.wait(2), 'chapter calls were serialized'
            with lock:
                active -= 1
            return write.ChapterResponse.model_validate({
                'segments': [{'slot': 's01', 'text': '完整章节。'}],
            })

        @staticmethod
        def accepted_request_fingerprint(_purpose):
            return 'c' * 64

        @staticmethod
        def accepted_model(_purpose):
            return 'test-model'

    class Store:
        @staticmethod
        def next_round(_rel):
            return 1

        @staticmethod
        def exists(_rel):
            return False

        @staticmethod
        @contextmanager
        def history_transaction(*_rels):
            yield

        @staticmethod
        def write_round(rel, _artifact):
            return rel

    monkeypatch.setattr(write.prompts, 'write_system', lambda *_a, **_kw: 'SYS')
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda *_a, **_kw: SimpleNamespace(given='given'))
    monkeypatch.setattr(write.prompts, 'build_user', lambda _mat: 'USER')
    monkeypatch.setattr(
        write.M, 'normalize_chapter',
        lambda text, **_kw: (text, write.M.NormalizeAccount()))
    monkeypatch.setattr(
        write, '_over_length_audit', lambda *_a, **_kw: ([], None))
    monkeypatch.setattr(
        write, 'CtxChapter', SimpleNamespace(model_validate=lambda value: value))
    monkeypatch.setattr(write, 'assert_prompt_private_absent', lambda *_a, **_kw: None)
    written = []

    def fake_write(_ctx, _store, entry, *_args, **_kwargs):
        written.append(entry['output_ordinal'])
        return 'chapter.json', [], [], []

    monkeypatch.setattr(write, '_write_chapter', fake_write)
    report = object()
    monkeypatch.setattr(write, '_report', lambda *_a, **_kw: report)
    ctx = SimpleNamespace(
        llm_provenance_for_chapter=lambda *_a, **_kw: object(),
        observe=lambda *_a, **_kw: None)
    todo = [
        {'output_ordinal': value, 'segments': [{'slot': 's01'}]}
        for value in range(1, chapters + 1)
    ]
    book = SimpleNamespace(quote_style='curly', sep_chars=[])
    got = write._run_group(
        ctx, Store(), book, todo, Client(), revise=None)
    assert got is report
    assert peak == expected_peak
    assert sorted(written) == list(range(1, chapters + 1))


def test_two_arm_composite_writer_is_gone():
    assert not hasattr(write, 'run_selected')


def test_targeted_stage5_refreshes_cache_and_does_not_print_false_success(
        monkeypatch, capsys):
    cli = importlib.import_module('novel.cli.main')
    from novel.llm import stage5_runs

    ctx = SimpleNamespace(
        node='stage5.write',
        cache=CachePolicy.default(),
    )
    seen = {}
    failed_report = SimpleNamespace(passed=False)

    monkeypatch.setattr(cli, '_llm_client', lambda _ctx: object())
    monkeypatch.setattr(cli, 'render', lambda _report: 'REPORT')
    monkeypatch.setattr(
        stage5_runs, 'refuse_foreign_prepared_before_wire',
        lambda _ctx, _chapters: None,
    )

    def fake_run(run_ctx, _client, chapters):
        seen['cache'] = run_ctx.cache.name
        seen['chapters'] = chapters
        return failed_report

    monkeypatch.setattr(write, 'run_write', fake_run)
    rc = cli._run_stage5(ctx, ['--chapters', '5,5'])

    assert rc == X.CHECK_FAILED
    assert seen == {'cache': 'refresh-write', 'chapters': '5'}
    output = capsys.readouterr().out
    assert '✓ 逐章成稿已落盘' not in output
    assert '仍有阻断稿' in output


def test_targeted_free_recovery_forwards_chapters(
        monkeypatch):
    """`--chapters` 不得被免费重判吞掉。"""
    cli = importlib.import_module('novel.cli.main')
    ctx = SimpleNamespace(node='stage5.write')
    report = SimpleNamespace(passed=True)
    seen = {}
    monkeypatch.setattr(cli, 'render', lambda _report: 'REPORT')
    monkeypatch.setattr(
        write, 'run_recheck',
        lambda _ctx, chapters: seen.update(recheck=chapters) or report)
    monkeypatch.setattr(
        write, 'run_revalidate',
        lambda _ctx, *, actor, reason, chapters: (
            seen.update(revalidate=(actor, reason, chapters)) or report))

    assert cli._run_stage5(ctx, ['--recheck', '--chapters', '2,4']) == X.OK
    assert cli._run_stage5(
        ctx, ['--revalidate', '--chapters', '3', '--actor', 'A', '--reason', 'R']) == X.OK
    assert seen == {'recheck': '2,4', 'revalidate': ('A', 'R', '3')}
