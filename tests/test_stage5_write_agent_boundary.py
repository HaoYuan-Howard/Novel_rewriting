#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 每章 executor 的纯候选/同 session/拒稿边界；全程零真实模型。"""
from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.report import finding
from novel.stages.stage5 import write


class _TypedEntry:
    def __init__(self, value):
        self.value = dict(value)

    def model_dump(self, **_kwargs):
        return dict(self.value)


def _book(*, source='绝不能下发的源章正文'):
    return SimpleNamespace(
        title='测试书',
        ctx={'chapters': []},
        measurable={},
        source_text={1: source, 2: '第二章私有原文'},
        secrets=[{'detail': '未来秘密'}],
        characters={},
        quote_style=None,
        sep_chars=None,
    )


def _entry(ordinal=1):
    return {
        'output_ordinal': ordinal,
        'from_source_ordinals': [ordinal],
        'source_chars': 10,
        'budget_chars': 20,
        'scenes': [{'slot': 's01', 'budget_chars': 20}],
    }


def _patch_prompt_boundary(monkeypatch):
    monkeypatch.setattr(
        write, 'CtxChapter',
        SimpleNamespace(model_validate=lambda value: _TypedEntry(value)))
    monkeypatch.setattr(
        write.prompts, 'build_material',
        lambda entry, _book: SimpleNamespace(
            given=f'只含第{entry["output_ordinal"]}章正式任务'))
    monkeypatch.setattr(
        write.prompts, 'build_user', lambda mat: f'WRITE:{mat.given}')
    monkeypatch.setattr(
        write.prompts, 'revise_user',
        lambda mat, previous, fixes: (
            f'REVISE:{mat.given}\nOLD:{previous}\nFIX:{"|".join(fixes)}'))
    monkeypatch.setattr(
        write.prompts, 'write_system',
        lambda **kwargs: 'REVISE-SYS' if kwargs.get('revise') else 'WRITE-SYS')
    monkeypatch.setattr(
        write, 'assert_prompt_private_absent', lambda *_args, **_kwargs: None)


class _Source:
    model = 'requested-model'
    concurrency = 8

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.feedback = []
        self.attempt = 'attempt-0'
        self.actual_model = 'actual-model'
        self.request_fp = 'f' * 64
        self.queue_results = []
        self.restored_turn = 0

    def ask_json(self, system, user, **kwargs):
        self.calls.append((system, user, dict(kwargs)))
        self.attempt = f'attempt-{len(self.calls)}'
        self.request_fp = str(len(self.calls)) * 64
        return self.responses.pop(0)

    def accepted_attempt(self, _purpose):
        return self.attempt

    def accepted_model(self, _purpose):
        return self.actual_model

    def accepted_models(self, *_purposes):
        return (self.actual_model,)

    def accepted_request_fingerprint(self, _purpose):
        return self.request_fp

    def accepted_checkpoint(self, _purpose):
        return {
            'request_fingerprint': self.request_fp,
            'accepted_turn_index': max(
                1, self.restored_turn + len(self.calls)),
        }

    def restore_checkpoint_acceptance(self, _purpose, record, **_kwargs):
        self.attempt = str(record['attempt_id'])
        self.actual_model = str(record['model'])
        self.request_fp = str(record['request_fingerprint'])
        self.restored_turn = int(record['accepted_turn_index'])

    def queue_code_validation_feedback(self, rows, *, purpose=None):
        self.feedback.append((purpose, list(rows)))
        return self.queue_results.pop(0) if self.queue_results else False

    def queue_restored_code_validation_feedback(self, rows, *, purpose=None):
        self.feedback.append((purpose, list(rows)))
        return self.queue_results.pop(0) if self.queue_results else False

    def purpose_can_resume(self, _purpose):
        return self.restored_turn + len(self.calls) < 4

    def purpose_driver_failed(self, _purpose):
        return False

    def finish_chapter_session(self, _chapter, *, outcome):
        assert outcome in {
            'delivered', 'blocked', 'response_failed',
            'artifact_failed', 'executor_failed',
        }

    def prepare_initial_sessions(self, _chapters):
        return None


def _patch_agent_run_boundary(monkeypatch):
    checkpoints = []
    monkeypatch.setattr(write.stage5_runs, 'ensure_run', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        write.stage5_runs, 'restore_checkpoint', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write.stage5_runs, 'write_checkpoint',
        lambda *_args, **kwargs: checkpoints.append(dict(kwargs)))
    return checkpoints


def _identity_normalization(monkeypatch):
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))


def test_prepare_is_closed_book_and_has_no_previous_chapter_draft(monkeypatch):
    _patch_prompt_boundary(monkeypatch)
    private_source = '绝不能下发的源章正文'
    prepared = write.prepare_chapter_candidate(
        _entry(2), _book(source=private_source), revision=None)

    assert prepared.purpose == 'stage5.write·ch2'
    assert private_source not in prepared.system + prepared.user
    assert '第一章成稿' not in prepared.user
    assert prepared.user == 'WRITE:只含第2章正式任务'
    assert not any('previous_chapter' in name for name in prepared.__dataclass_fields__)


def test_evaluate_has_no_write_or_settle_and_feedback_does_not_leak_source(
        monkeypatch):
    _patch_prompt_boundary(monkeypatch)
    _identity_normalization(monkeypatch)
    prepared = write.prepare_chapter_candidate(
        _entry(), _book(), revision=None)
    secret = '未下发原文里的完整句子'
    hard = finding(
        'verbatim_missing', detail=f'缺少：{secret}', evidence=[secret])
    monkeypatch.setattr(
        write, '_judge', lambda *_args, **_kwargs: (
            [hard], [f'[verbatim_missing] 缺少：{secret}'], []))
    source = _Source()
    source.attempt = 'attempt-accepted'

    # 若纯评估触盘，这个替身会立即让测试失败。
    monkeypatch.setattr(
        write, 'ArtifactStore',
        lambda _ctx: (_ for _ in ()).throw(AssertionError('evaluate 不得触盘')))
    evaluation = write.evaluate_chapter_candidate(
        source,
        prepared,
        write.ChapterResponse(
            segments=[write.ChapterSegment(slot='s01', text='候选正文。')]),
    )

    assert not evaluation.passed
    encoded = json.dumps(evaluation.feedback_rows(), ensure_ascii=False)
    assert 'verbatim_missing' in encoded
    assert secret not in encoded
    assert 'detail' not in encoded and 'evidence' not in encoded


def test_agent_hard_resumes_same_chapter_purpose_without_refresh(monkeypatch):
    _patch_prompt_boundary(monkeypatch)
    _identity_normalization(monkeypatch)
    checkpoints = _patch_agent_run_boundary(monkeypatch)
    secret = '只在校验器里可见的原文证据'
    hard = finding(
        'verbatim_missing', detail=f'缺少：{secret}', evidence=[secret])

    def judge(text, *_args, **_kwargs):
        return (([hard], ['[verbatim_missing] 缺失'], [])
                if '第一版' in text else ([], [], []))

    monkeypatch.setattr(write, '_judge', judge)
    monkeypatch.setattr(
        write, 'assert_chapter_candidate_current',
        lambda *_args, **_kwargs: None)
    source = _Source([
        write.ChapterResponse(
            segments=[write.ChapterSegment(slot='s01', text='第一版。')]),
        write.ChapterResponse(
            segments=[write.ChapterSegment(slot='s01', text='第二版合格。')]),
    ])
    source.queue_results = [True]

    generated = write._generate_chapter(
        _entry(),
        _book(),
        source,
        revise=None,
        system='WRITE-SYS',
        progress=SimpleNamespace(),
        ctx=SimpleNamespace(node='stage5.write'),
        agent_sessions=True,
    )

    evaluation = generated['evaluation']
    assert evaluation.passed and evaluation.text == '第二版合格。'
    purposes = [call[2]['purpose'] for call in source.calls]
    assert purposes == ['stage5.write·ch1', 'stage5.write·ch1']
    assert all('cache_policy' not in call[2] for call in source.calls)
    assert all(
        call[2]['recovery_policy'] == 'stage5-write-v1'
        for call in source.calls)
    assert [row['state_payload']['outcome'] for row in checkpoints] == [
        'hard', 'passed']
    assert source.feedback[0][0] == 'stage5.write·ch1'
    assert secret not in json.dumps(source.feedback, ensure_ascii=False)


def test_exhausted_revision_writes_only_rejected_evidence(monkeypatch):
    _patch_prompt_boundary(monkeypatch)
    _identity_normalization(monkeypatch)
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(
        write.checks, 'trim_regressions',
        lambda *_args, **_kwargs: ['丢了旧稿已经保住的结尾'])
    monkeypatch.setattr(
        write, 'assert_chapter_candidate_current',
        lambda *_args, **_kwargs: None)
    old_text = '旧正式稿。'
    prepared = write.prepare_chapter_candidate(
        _entry(),
        _book(),
        revision=(old_text, [['verbatim_missing', 'ev']], 'review-fp'),
        system='REVISE-SYS',
    )
    source = _Source([
        write.ChapterResponse(
            segments=[write.ChapterSegment(slot='s01', text='退化候选。')]),
    ])
    response = source.ask_json(
        prepared.system, prepared.user, purpose=prepared.purpose)
    evaluation = write.evaluate_chapter_candidate(source, prepared, response)

    class Store:
        def __init__(self):
            self.docs = {'stage5/ch01.json': SimpleNamespace(text=old_text)}
            self.writes = []

        def write_round(self, rel, artifact):
            self.writes.append(rel)
            self.docs[rel] = artifact
            return Path(rel)

        def read_own(self, _model, rel, **_kwargs):
            return self.docs[rel], {}

    store = Store()
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    delivery = write.finalize_chapter_candidate(
        SimpleNamespace(node='stage5.write'), source, prepared, evaluation)

    assert delivery.outcome == 'rejected_revision'
    assert store.writes == ['stage5/ch01.rejected.json']
    assert store.docs['stage5/ch01.json'].text == old_text


def test_dirty_checkpoint_queues_safe_feedback_before_new_candidate(monkeypatch):
    _patch_prompt_boundary(monkeypatch)
    _identity_normalization(monkeypatch)
    monkeypatch.setattr(
        write, 'assert_chapter_candidate_current',
        lambda *_args, **_kwargs: None)
    secret = '只能留在代码校验器里的源文证据'
    hard = finding(
        'verbatim_missing', detail=f'缺少：{secret}', evidence=[secret])

    def judge(text, *_args, **_kwargs):
        return (([hard], ['[verbatim_missing] 缺失'], [])
                if '旧坏候选' in text else ([], [], []))

    monkeypatch.setattr(write, '_judge', judge)
    prepared = write.prepare_chapter_candidate(
        _entry(), _book(), revision=None, system='WRITE-SYS')
    old_response = write.ChapterResponse(
        segments=[write.ChapterSegment(slot='s01', text='旧坏候选。')])
    source = _Source([
        write.ChapterResponse(
            segments=[write.ChapterSegment(slot='s01', text='新合格候选。')]),
    ])
    source.attempt = 'old-attempt'
    source.request_fp = 'a' * 64
    old_evaluation = write.evaluate_chapter_candidate(
        source, prepared, old_response)
    old_state = write._chapter_checkpoint_state(
        prepared, old_evaluation, accepted_turn_index=1)
    restored = SimpleNamespace(
        value=old_response,
        acceptance={
            'attempt_id': 'old-attempt',
            'model': 'actual-model',
            'request_fingerprint': 'a' * 64,
            'accepted_turn_index': 1,
        },
        state_payload=old_state,
    )
    checkpoints = []
    monkeypatch.setattr(
        write.stage5_runs, 'ensure_run', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        write.stage5_runs, 'restore_checkpoint',
        lambda *_args, **_kwargs: restored)
    monkeypatch.setattr(
        write.stage5_runs, 'write_checkpoint',
        lambda *_args, **kwargs: checkpoints.append(dict(kwargs)))
    source.queue_results = [True]

    generated = write._generate_chapter_with_agent(
        SimpleNamespace(node='stage5.write'),
        prepared,
        source,
        progress=SimpleNamespace(),
    )

    evaluation = generated['evaluation']
    assert evaluation.passed
    assert evaluation.text == '新合格候选。'
    assert evaluation.attempts == (
        (len('旧坏候选。'), '旧坏候选。'),
        (len('新合格候选。'), '新合格候选。'),
    )
    assert len(source.calls) == 1
    assert source.calls[0][2]['recovery_policy'] == 'stage5-write-v1'
    assert source.feedback[0][0] == prepared.purpose
    assert secret not in json.dumps(source.feedback, ensure_ascii=False)
    assert checkpoints[0]['state_payload']['accepted_turn_index'] == 2
    assert checkpoints[0]['state_payload']['prior_attempts'] == [
        {'chars': len('旧坏候选。'), 'text': '旧坏候选。'},
    ]


def test_formal_journal_preflight_covers_every_transaction_path(monkeypatch):
    prepared = SimpleNamespace(ordinal=4)
    store = SimpleNamespace()
    monkeypatch.setattr(
        write, '_journal_landed_primary',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        write, '_trim_preflight_expectations',
        lambda *_args, **_kwargs: (None, None, None))
    calls = []

    def target_state(_ctx, chapter, _record, rel, **kwargs):
        calls.append((chapter, rel, dict(kwargs)))
        return 'baseline'

    monkeypatch.setattr(write.stage5_runs, 'target_state', target_state)
    write._preflight_formal_commit_targets(
        SimpleNamespace(validation_reads=nullcontext),
        store, prepared, SimpleNamespace(), {'state': 'prepared'})

    formal = write._rel(4)
    assert calls == [
        (4, formal, {'expected_file_sha256': None}),
        (4, write.paths.invalid_of(formal), {'expected_absent': True}),
        (4, write._rejected_rel(4), {'expected_absent': True}),
        (4, write.artifact_rel('stage5.trim_audit', nn=4), {
            'expected_file_sha256': None, 'expected_absent': True}),
        (4, write.artifact_rel('stage5.trim_resolution', nn=4), {
            'expected_file_sha256': None, 'expected_absent': True}),
    ]


def test_batch_waits_for_session_isolation_before_any_formal_write(monkeypatch):
    prepared = {
        chapter: object.__new__(write.ChapterCandidateContext)
        for chapter in (1, 2)
    }
    evaluations = {
        chapter: object.__new__(write.ChapterCandidateEvaluation)
        for chapter in (1, 2)
    }

    class Pool:
        model = 'requested-model'
        concurrency = 2
        isolation_failed = True

        def __init__(self):
            self.finished = []

        def prepare_initial_sessions(self, chapters):
            assert tuple(chapters) == (1, 2)

        def finish_chapter_session(self, chapter, *, outcome,
                                   delivery_attempts=None,
                                   same_run_recovered=None,
                                   first_failure_phase=None,
                                   first_failure_detail=None):
            self.finished.append(
                (chapter, outcome, delivery_attempts, same_run_recovered,
                 first_failure_phase, first_failure_detail))

    pool = Pool()
    monkeypatch.setattr(
        write, '_generate_chapter',
        lambda entry, *_args, **_kwargs: {
            'prepared': prepared[int(entry['output_ordinal'])],
            'evaluation': evaluations[int(entry['output_ordinal'])],
        },
    )
    monkeypatch.setattr(
        write, 'finalize_chapter_candidate',
        lambda *_args, **_kwargs: pytest.fail(
            '全批 provider/session 身份闭合前不得写任何正式章'),
    )
    monkeypatch.setattr(
        write, '_report', lambda *_args, **_kwargs: SimpleNamespace())
    store = SimpleNamespace(path=lambda rel: Path(rel))
    ctx = SimpleNamespace(
        node='stage5.write',
        executor_profile=object(),
        observe=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(write.GateBlocked, match='全部没落成正式稿'):
        write._run_group_parallel(
            ctx,
            store,
            SimpleNamespace(),
            [{'output_ordinal': 1}, {'output_ordinal': 2}],
            pool,
            revise=None,
        )

    assert sorted(pool.finished) == [
        (1, 'artifact_failed', None, None, None, None),
        (2, 'artifact_failed', None, None, None, None)]
