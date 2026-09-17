#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 直连逐章候选、拒稿与提交边界；全程零真实模型。"""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.report import finding
from novel.errors import ProvenanceRefused
from novel.llm.client import LlmClient
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

def _identity_normalization(monkeypatch):
    monkeypatch.setattr(
        write, '_normalize_segments',
        lambda segments, _book: (segments, write.M.NormalizeAccount()))


def test_paid_entry_refuses_agent_profile_before_wire(tmp_path):
    ctx = SimpleNamespace(
        novel_dir=tmp_path / 'book', run_id='run-profile',
        executor_profile=object())

    with pytest.raises(ProvenanceRefused, match='不能接管 agent profile'):
        write._require_direct_llm_entry(ctx, object.__new__(LlmClient))


@pytest.mark.parametrize('legacy', [False, True])
def test_paid_entry_refuses_current_run_agent_directory(tmp_path, legacy):
    book = tmp_path / 'book'
    root = (
        write.paths.legacy_stage_runs_dir(book, 'stage5')
        if legacy else write.paths.service_stage_runs_dir(book, 'stage5')
    )
    (root / 'run-agent' / 'ch01').mkdir(parents=True)
    ctx = SimpleNamespace(
        novel_dir=book, run_id='run-agent', executor_profile=None)

    with pytest.raises(ProvenanceRefused, match='已有 agent 状态'):
        write._require_direct_llm_entry(ctx, object.__new__(LlmClient))


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


def test_verbatim_feedback_only_points_to_exact_material_already_given(monkeypatch):
    _patch_prompt_boundary(monkeypatch)
    _identity_normalization(monkeypatch)
    given = '本章已下发的原话甲。\n本章已下发的原话乙。'
    monkeypatch.setattr(write.prompts, 'build_material',
                        lambda *_args: SimpleNamespace(given=given))
    prepared = write.prepare_chapter_candidate(_entry(), _book(), revision=None)
    private = '未下发原文里的完整句子'
    hard = finding('verbatim_missing', detail=f'私有诊断：{private}', evidence=[
        '本章已下发的原话甲。', private, '本章已下发的原话乙。',
        '本章已下发的原话甲。', '本章 已下发的原话甲。',
    ])
    monkeypatch.setattr(write, '_judge', lambda *_args, **_kwargs: ([hard], ['缺原话'], []))
    evaluation = write.evaluate_chapter_candidate(
        _Source(), prepared, write.ChapterResponse(
            segments=[write.ChapterSegment(slot='s01', text='候选正文。')]))
    rows = evaluation.feedback_rows()
    assert rows[0]['required_materials'] == [
        '本章已下发的原话甲。', '本章已下发的原话乙。']
    assert all(item in prepared.user for item in rows[0]['required_materials'])
    assert private not in json.dumps(rows, ensure_ascii=False)
    assert '私有诊断' not in json.dumps(rows, ensure_ascii=False)
    assert '本章 已下发的原话甲。' not in json.dumps(rows, ensure_ascii=False)


def test_material_feedback_does_not_open_other_rules_or_missing_given():
    hard = finding('verbatim_missing', detail='不应发送', evidence=['原话'])
    assert 'required_materials' not in write._chapter_feedback([hard], ())[0].to_dict()
    other = finding('secret_leak', detail='不应发送', evidence=['原话'])
    assert 'required_materials' not in write._chapter_feedback(
        [other], (), given='原话')[0].to_dict()


def test_exhausted_revision_writes_only_rejected_evidence(
        monkeypatch, tmp_path):
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

        def exists(self, rel):
            return rel in self.docs

        def write_round(self, rel, artifact):
            self.writes.append(rel)
            self.docs[rel] = artifact
            return Path(rel)

        def read_own(self, _model, rel, **_kwargs):
            return self.docs[rel], {}

    store = Store()
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: store)
    delivery = write.finalize_chapter_candidate(
        SimpleNamespace(node='stage5.write', novel_dir=tmp_path / 'book'),
        source, prepared, evaluation)

    assert delivery.outcome == 'rejected_revision'
    assert store.writes == ['stage5/ch01.rejected.json']
    assert store.docs['stage5/ch01.json'].text == old_text


def test_formal_journal_preflight_covers_every_transaction_path(monkeypatch):
    prepared = SimpleNamespace(ordinal=4, mode='write')
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
        (4, formal, {'mode': 'write', 'expected_file_sha256': None}),
        (4, write.paths.invalid_of(formal), {'mode': 'write', 'expected_absent': True}),
        (4, write._rejected_rel(4), {'mode': 'write', 'expected_absent': True}),
        (4, write.artifact_rel('stage5.trim_audit', nn=4), {
            'mode': 'write', 'expected_file_sha256': None, 'expected_absent': True}),
        (4, write.artifact_rel('stage5.trim_resolution', nn=4), {
            'mode': 'write', 'expected_file_sha256': None, 'expected_absent': True}),
    ]
