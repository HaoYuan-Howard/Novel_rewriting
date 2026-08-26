#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 Review 的唯一 currentness 判据及所有消费方的关闭式行为。"""
from __future__ import annotations

import importlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import InputEdge, artifact_spec
from contracts.schema.common import WontFix
from contracts.schema.stage5 import (
    Chapter, ChapterSegment, JudgedDraft, Review, ReviewChapter,
)
from contracts.schema.report import finding
from novel import prov
from novel.errors import BlockingFindingsUnresolved, GateBlocked, TerminalState
from novel.prov import FreshnessReport
from novel.run import RunContext
from novel.stages.stage5 import measure as M, review, write
from tests import factories


class _StatusStore:
    """只替换 I/O；正文消费投影、Review 模型和状态算法都走生产实现。"""

    def __init__(self, root: Path, book: M.Book, chapter: Chapter) -> None:
        self.root = root
        self.book = book
        self.chapters = {'stage5/ch01.json': chapter}
        self.review: Review | None = None
        self.wontfix: WontFix | None = None
        self.ctx = _StatusContext(
            sid='synthetic', title=book.title, novel_dir=root,
            node='stage5.review')
        (root / 'stage5').mkdir(parents=True)
        (root / 'stage5' / 'ch01.json').write_text('{}', encoding='utf-8')
        (root / 'stage5' / 'review_1.json').write_text('{}', encoding='utf-8')

    def path(self, rel: str) -> Path:
        return self.root / rel

    @staticmethod
    def schema_version(rel: str) -> int | None:
        spec = artifact_spec(rel)
        return spec.current_version if spec is not None else None

    def read_own(self, _model, rel: str, **_kwargs):
        if rel in self.chapters:
            chapter = self.chapters[rel]
            return chapter, chapter.model_dump(by_alias=True, exclude_none=True)
        if rel == 'stage5/wontfix.json' and self.wontfix is not None:
            return self.wontfix, self.wontfix.model_dump(by_alias=True)
        assert rel == 'stage5/review_1.json' and self.review is not None
        return self.review, self.review.model_dump(by_alias=True)

    def write(self, rel: str, artifact):
        assert rel == 'stage5/wontfix.json'
        self.wontfix = WontFix.model_validate(artifact)
        self.path(rel).write_text('{}', encoding='utf-8')
        return self.path(rel)

    def read_judged(self, _model, rel: str, *, project, require_current=True):
        del require_current
        chapter = self.chapters[rel]
        raw = chapter.model_dump(by_alias=True, exclude_none=True)
        whole = prov.fingerprint(chapter.model_dump_json(by_alias=True, exclude_none=True))
        consumed = prov.fingerprint(str(project(raw)))
        return chapter, whole, consumed

    @staticmethod
    def freshness(_rel: str) -> FreshnessReport:
        return FreshnessReport('fresh', 'match')

    def exists(self, rel: str) -> bool:
        if rel == 'stage5/wontfix.json':
            return self.wontfix is not None
        return self.path(rel).is_file()

    def read_text(self, rel: str) -> str:
        return self.path(rel).read_text(encoding='utf-8')

    @staticmethod
    def seen_rounds(_rel: str) -> list[int]:
        return [1]


class _StatusContext(SimpleNamespace):
    """状态算法测试的最小上下文；生产的账隔离另有真实 RunContext 回归。"""

    @contextmanager
    def validation_reads(self):
        yield


@dataclass
class _Rig:
    store: _StatusStore
    ctx: object

    def stale_rules(self) -> None:
        assert self.store.review is not None
        provenance = self.store.review.provenance.model_copy(
            update={'prompts': {'RULES': '0' * 10}})
        self.store.review = self.store.review.model_copy(
            update={'provenance': provenance})

    def change_body(self) -> None:
        chapter = self.store.chapters['stage5/ch01.json']
        changed = chapter.text + '\n\n她又补写了一段。'
        self.store.chapters['stage5/ch01.json'] = chapter.model_copy(update={
            'segments': [ChapterSegment(slot='s01', text=changed)],
            'chars': len(changed),
        })

    def add_unreviewed_chapter(self) -> None:
        first = self.store.chapters['stage5/ch01.json']
        second = first.model_copy(update={
            'output_ordinal': 2,
            'from_source_ordinals': [2],
        })
        self.store.chapters['stage5/ch02.json'] = second
        self.store.path('stage5/ch02.json').write_text('{}', encoding='utf-8')

@pytest.fixture
def status_rig(tmp_path, monkeypatch) -> _Rig:
    book = factories.stage5_book()
    entry = book.chapters[1]
    text = ('沈青关上门，重新翻到账册最后一页。她逐笔核对墨迹，'
            '确认那串数字已被人换过。' * 4)
    chapter = Chapter.model_validate({
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'source_chars': entry['source_chars'],
        'budget_chars': entry['budget_chars'],
        'chars': len(text),
        'overlap_excl_verbatim': 0.01,
        'segments': [{'slot': 's01', 'text': text}],
        'warns': [],
        '_provenance': {
            'code': 'test-code', 'root_clean_sha1': 'a' * 40,
            'inputs': {}, 'round': 1, 'origin': 'human',
        },
    })
    store = _StatusStore(tmp_path / 'book', book, chapter)
    _by_ord, _invalid, judged = M.load_drafts(store, require_current=False)
    one: JudgedDraft = judged['stage5/ch01.json']
    store.review = Review.model_validate({
        'sources': {
            'writer_context': {'state': 'fresh', 'detail': [], 'code': 'test-code'},
            'drafts': {'stale': [], 'no_prov': []},
        },
        'judged': judged,
        'chapters': [ReviewChapter(
            output_ordinal=1,
            from_source_ordinals=[1],
            budget_chars=entry['budget_chars'],
            source_chars=entry['source_chars'],
            relpath='stage5/ch01.json',
            draft_round=1,
            draft_sha1=one.sha1,
            draft_text_sha1=one.consumed.text_sha1,
            chars=len(text),
            findings=[],
        )],
        'book': [],
        'baselines': {'_fact_landing': '0/0'},
        'baselines_identity': {},
        'counts': {},
        '_provenance': {
            'code': 'test-code', 'root_clean_sha1': 'a' * 40, 'inputs': {},
            'prompts': {'RULES': prov.fingerprint(prov.rules_fingerprint('stage5'))},
        },
    })
    monkeypatch.setattr(review, 'load_book', lambda _ctx, current: current.book)
    return _Rig(store=store, ctx=store.ctx)


def test_review_status_fresh_baseline_binds_the_complete_canonical_subject(status_rig):
    status = review.review_status(status_rig.ctx, status_rig.store)
    assert status.state == 'fresh' and status.reasons == ()
    assert status.review_rel == 'stage5/review_1.json' and status.seq == 1


def test_review_view_refetches_wontfix_without_rewriting_review(
        status_rig, monkeypatch):
    """wontfix 只写决定账；同一 Review 的真实 view refetch 必须立刻叠加它。"""
    from novel.views import stage5 as stage5_views

    hit = finding(
        'invented_name',
        detail='成稿里出现了下发面之外的称谓「周管事」',
        evidence=['周管事'],
        converge='unresolved_1',
        unresolved_rounds=1,
    ).model_copy(update={'output_ordinal': 1})
    book_hit = finding(
        'book_length',
        detail='全书篇幅没有落在契约带内',
        evidence=['1000 not in 9000~11000'],
    )
    assert status_rig.store.review is not None
    row = status_rig.store.review.chapters[0].model_copy(
        update={'findings': [hit]})
    status_rig.store.review = status_rig.store.review.model_copy(
        update={'chapters': [row], 'book': [book_hit]})

    monkeypatch.setattr(
        stage5_views.paths, 'resolve_novel_dir',
        lambda _base, _sid: status_rig.store.root)
    monkeypatch.setattr(
        stage5_views.paths, 'derive_source_meta',
        lambda _name: ('synthetic', '合成书', None))
    monkeypatch.setattr(stage5_views.paths, 'title_of', lambda _root: '合成书')
    monkeypatch.setattr(stage5_views, 'readonly_ctx', lambda *_args: status_rig.ctx)
    monkeypatch.setattr(
        stage5_views, 'ArtifactStore', lambda _ctx: status_rig.store)
    monkeypatch.setattr(review, 'ArtifactStore', lambda _ctx: status_rig.store)

    before = stage5_views.review_view('unused', 'synthetic', 1)
    assert before.chapter.findings[0].wontfix is None
    assert before.book[0].wontfix is None
    assert before.index[0].unresolved == 1
    review_bytes = status_rig.store.path(
        'stage5/review_1.json').read_bytes()

    review.mark_wontfix(
        status_rig.ctx,
        f'1|invented_name|{hit.evidence_key}=逐字核对后接受这个称谓',
    )
    review.mark_wontfix(
        status_rig.ctx,
        f'book|book_length|{book_hit.evidence_key}=人工确认保留当前全书篇幅',
    )

    assert status_rig.store.path('stage5/review_1.json').read_bytes() == review_bytes
    assert review.review_seqs(status_rig.store) == [1], 'wontfix 不得偷产一轮 Review'
    after = stage5_views.review_view('unused', 'synthetic', 1)
    marked = after.chapter.findings[0]
    assert marked.wontfix is not None
    assert marked.wontfix.why == '逐字核对后接受这个称谓'
    assert marked.actionable is False
    assert after.index[0].unresolved == 0
    assert after.book[0].wontfix is not None
    assert after.book[0].wontfix.why == '人工确认保留当前全书篇幅'
    assert after.book[0].actionable is False
    assert sum(after.finding_counts.values()) == 2


def test_fresh_review_guard_does_not_leak_probe_reads_into_downstream_sources(
        tmp_path, monkeypatch):
    """统一 Review 闸必须使用 RunContext 的生产隔离口，而不是靠下游清理假边。"""
    book = tmp_path / '隔离校验_123'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('测试根。\n', encoding='utf-8')
    ctx = RunContext.open(
        tmp_path, '123', 'stage6.hook', paid=False, write_ledger=False)
    kept = InputEdge(file='1' * 10, consumed='2' * 10)
    ctx.record_edge('stage1/hook_copy.json', kept)

    expected = review.ReviewStatus('fresh', ())

    def probe(actual_ctx, _store):
        actual_ctx.record_edge(
            'stage1/main_text.json', InputEdge(file='3' * 10, consumed=None))
        actual_ctx.record_edge(
            'stage5/ch98.json', InputEdge(file='4' * 10, consumed=None))
        actual_ctx.record_judged('stage5/ch99.json', '5' * 10)
        return expected

    monkeypatch.setattr(review, 'review_status', probe)
    assert review.require_fresh_review(ctx, object()) is expected
    assert ctx.edges == {'stage1/hook_copy.json': kept}
    assert ctx.judged == {}


def test_accepted_review_is_stricter_than_fresh_and_rejects_current_hard(
        status_rig, monkeypatch):
    """fresh 只回答“结论是否当前”；正式下游还必须回答“当前结论是否可接受”。"""
    hit = finding(
        'paragraph_budget_missed',
        detail='自然段数量未命中明确目标：2 段，不在 31~46 段内',
        evidence=['2 not in 31~46'],
    ).model_copy(update={'output_ordinal': 1})
    assert status_rig.store.review is not None
    row = status_rig.store.review.chapters[0].model_copy(update={'findings': [hit]})
    status_rig.store.review = status_rig.store.review.model_copy(
        update={'chapters': [row]})

    assert review.require_fresh_review(status_rig.ctx, status_rig.store).fresh
    with pytest.raises(BlockingFindingsUnresolved) as caught:
        review.require_accepted_review(status_rig.ctx, status_rig.store)
    assert caught.value.rules == ('paragraph_budget_missed',)

    from novel.orchestrator import approvals
    monkeypatch.setattr(approvals, '_require_stage5_complete', lambda *_args: None)
    monkeypatch.setattr(approvals, '_planned', lambda *_args: [1])
    monkeypatch.setattr(approvals, '_store_for', lambda _novel_dir: status_rig.store)
    monkeypatch.setattr(approvals.paths, 'resolve_novel_dir',
                        lambda *_args: status_rig.store.root)
    with pytest.raises(BlockingFindingsUnresolved):
        approvals._stage5_subject_rels(
            status_rig.store.root.parent, 'test-sid',
            approvals._fresh_record('test-sid'))


def test_accepted_review_rejects_an_unresolved_invalid_even_when_review_is_fresh(
        status_rig):
    status_rig.store.path('stage5/ch02.INVALID.json').write_text('{}', encoding='utf-8')
    assert review.require_fresh_review(status_rig.ctx, status_rig.store).fresh
    with pytest.raises(GateBlocked) as caught:
        review.require_accepted_review(status_rig.ctx, status_rig.store)
    assert caught.value.invalid_path == str(status_rig.store.path('stage5'))


@pytest.mark.parametrize('consumer', ['stage6', 'stage7'])
def test_formal_downstreams_call_the_accepted_review_guard_before_loading_inputs(
        status_rig, monkeypatch, consumer):
    """Stage6/7 不能各自退化成只看“有章稿”；共同 accepted 闸必须是首个动作。"""
    class GuardReached(Exception):
        pass

    monkeypatch.setattr(
        review, 'require_accepted_review',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(GuardReached()))
    if consumer == 'stage6':
        from novel.stages.stage6 import hook
        constructor = hook._Upstreams
    else:
        from novel.stages.stage7 import rename
        constructor = rename._Upstreams
    with pytest.raises(GuardReached):
        constructor(status_rig.ctx, status_rig.store)


@pytest.mark.parametrize(('mutation', 'reason'), [
    ('stale_rules', 'RULES 已变'),
    ('change_body', '正文投影已变'),
    ('add_unreviewed_chapter', 'judged 主体集合已变'),
])
def test_review_status_closes_on_every_current_subject_drift(status_rig, mutation, reason):
    getattr(status_rig, mutation)()
    status = review.review_status(status_rig.ctx, status_rig.store)
    assert status.state == 'stale'
    assert any(reason in detail for detail in status.reasons), status.reasons


def test_review_status_classifies_old_two_arm_layout_as_legacy(status_rig):
    legacy = status_rig.store.path('stage5/deep/ch01.json')
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{}', encoding='utf-8')
    status = review.review_status(status_rig.ctx, status_rig.store)
    assert status.state == 'legacy_incompatible'
    assert any('旧双臂路径:stage5/deep' in reason for reason in status.reasons)


def test_old_review_is_not_a_convergence_baseline_but_keeps_the_next_sequence(tmp_path):
    """v1 报告不得混入 v2 收敛，也不得堵死免费 producer 的下一轮。"""
    root = tmp_path / 'book'
    (root / 'stage5').mkdir(parents=True)
    (root / 'stage5/review_7.json').write_text(
        '{"_schema_version":1}', encoding='utf-8')

    class OldReviewStore:
        @staticmethod
        def path(rel: str) -> Path:
            return root / rel

        @staticmethod
        def schema_version(_rel: str) -> int:
            return 1

        @staticmethod
        def read_own(*_args, **_kwargs):
            raise AssertionError('低版本 Review 不得进入 v2 模型读取')

    store = OldReviewStore()
    assert review.review_seqs(store) == [7]
    assert review.latest_review(store) is None
    assert review.review_seqs(store)[-1] + 1 == 8


def test_check_only_is_terminal_exit_2_and_never_renders_old_ok(
        status_rig, monkeypatch, capsys):
    status_rig.stale_rules()
    monkeypatch.setattr(review, 'ArtifactStore', lambda _ctx: status_rig.store)
    monkeypatch.setattr(
        review, '_report_of_review',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('stale Review 不得进入报告渲染')))

    cli = importlib.import_module('novel.cli.main')
    monkeypatch.setattr(
        cli, '_parser',
        lambda: SimpleNamespace(parse_args=lambda _argv: SimpleNamespace(cmd='run')))
    monkeypatch.setitem(
        cli._CMDS, 'run', lambda _args: review.run_check_only(status_rig.ctx))
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert '显式终态' in captured.err and 'RULES' in captured.err
    assert '[OK]' not in captured.out + captured.err


def test_wontfix_revise_and_s5_approval_proof_all_refuse_stale_review(
        status_rig, monkeypatch):
    status_rig.stale_rules()
    monkeypatch.setattr(review, 'ArtifactStore', lambda _ctx: status_rig.store)
    with pytest.raises(TerminalState, match='Review 当前状态是 stale'):
        review.mark_wontfix(status_rig.ctx, '1|draft_too_short=旧结论不得豁免')

    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: status_rig.store)
    with pytest.raises(TerminalState, match='Review 当前状态是 stale'):
        write.run_revise(status_rig.ctx, object())

    from novel.orchestrator import approvals
    monkeypatch.setattr(approvals, '_require_stage5_complete', lambda *_args: None)
    monkeypatch.setattr(approvals, '_planned', lambda *_args: [1])
    monkeypatch.setattr(approvals, '_store_for', lambda _novel_dir: status_rig.store)
    monkeypatch.setattr(approvals.paths, 'resolve_novel_dir',
                        lambda *_args: status_rig.store.root)
    with pytest.raises(TerminalState, match='Review 当前状态是 stale'):
        approvals._stage5_subject_rels(
            status_rig.store.root.parent, 'test-sid',
            approvals._fresh_record('test-sid'))


def test_state_view_and_inbox_forward_the_same_status_and_reasons(
        status_rig, monkeypatch):
    status_rig.stale_rules()
    status = review.review_status(status_rig.ctx, status_rig.store)
    monkeypatch.setattr(review, 'review_status', lambda *_args: status)

    from novel.orchestrator import state
    monkeypatch.setattr(state, 'required_artifacts', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(state, 'numbered_produces', lambda _stage: {})
    monkeypatch.setattr(state, 'chapters_done', lambda _novel_dir: [1])
    monkeypatch.setattr(state, 'planned_chapters', lambda _store: [1])
    monkeypatch.setattr(state, 'stage5_legacy_paths', lambda _novel_dir: [])
    stage = state.stage_state(status_rig.store, 'stage5')
    assert stage.review_state == status.state
    assert stage.review_reasons == list(status.reasons)

    from novel.views import inbox as inbox_module
    items = inbox_module._stage5_stalled(status_rig.store)
    assert len(items) == 1 and items[0].rule == 'review_status'
    assert status.state in items[0].what
    assert all(reason in items[0].what for reason in status.reasons[:5])

    from novel.views import stage5 as stage5_module
    monkeypatch.setattr(stage5_module, 'ArtifactStore', lambda _ctx: status_rig.store)
    monkeypatch.setattr(stage5_module, 'readonly_ctx', lambda *_args: status_rig.ctx)
    monkeypatch.setattr(stage5_module.paths, 'resolve_novel_dir',
                        lambda *_args: status_rig.store.root)
    monkeypatch.setattr(stage5_module.paths, 'derive_source_meta',
                        lambda _name: ('test-sid', 'book'))
    monkeypatch.setattr(stage5_module.paths, 'title_of', lambda _path: '测试书')
    view = stage5_module.review_view(status_rig.store.root.parent, 'test-sid', 1)
    assert view.review_state == status.state
    assert view.review_reasons == list(status.reasons)
