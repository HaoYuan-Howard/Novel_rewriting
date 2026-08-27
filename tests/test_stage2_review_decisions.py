#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_review_decisions.py —— `stage2.global --review-decisions` 的决策文件路径闸。

【它守的是什么】报错文案承诺「复核决策文件必须在**当前书**或 `_service/edits/<sid>` 内」，
但 `apply_file` 此前只卡第二个根：落在书目录内的绝对路径被误判 `PathRefused`（exit 3），
与文案、与 `paths.edit_rel` 的既有口径（「绝对路径仍然收，只要落在书目录内」）都矛盾。
这里钉死两个根都收、两个根之外都拒。

绝对路径、书内相对路径与 API 一次性文件都吃注册表版本化形状；路径写法不再改变契约。
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from novel.errors import PathRefused
from contracts.artifacts import (
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema import encode_artifact
from contracts.schema.evaluation import EvidenceRef, GlobalReview
from novel import paths
from novel.errors import (
    BlockingFindingsUnresolved, GateBlocked, UpstreamMissing, UpstreamStale,
)
from novel.orchestrator import approvals
from novel.quality.global_review import build_review, decide
from novel.stages.stage2 import global_review
from novel.stages.stage7 import assemble
from novel.store import ArtifactStore
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _sandbox, _seed


def _current_items(book):
    raw = json.loads((book / 'stage2/global_review.json').read_text(encoding='utf-8'))
    return raw['payload']['items'] if 'payload' in raw else raw['items']


def _versioned_decisions(items) -> str:
    payload = {
        'actor': 'tester',
        'decisions': [{
            'item_id': items[0]['item_id'],
            'expected_subject_sha256': items[0]['subject_sha256'],
            'state': 'PASS',
            'reason': '逐字核对原文后人工 PASS',
            'evidence': [],
        }],
    }
    return json.dumps(
        encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, payload), ensure_ascii=False)


def _approve_all_review(book) -> None:
    review_ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(review_ctx)
    review = store.read(GlobalReview, global_review.REL)
    for item in tuple(review.items):
        review = decide(
            review,
            item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='PASS',
            actor='joint-gate-reviewer',
            reason='逐项对照当前正文与全局事实后确认',
            now='2026-08-27T00:00:00+00:00',
        )
    store.write_round(global_review.REL, review)


def test_a_book_dir_absolute_decision_path_is_accepted():
    _root, book = _sandbox('书_123')
    _seed(book)
    items = _current_items(book)
    path = book / 'stage2/global_review_decisions.json'
    path.write_text(_versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), str(path.resolve()))

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_an_outside_absolute_decision_path_is_still_refused(tmp_path):
    _root, book = _sandbox('书_123')
    _seed(book)
    outside = tmp_path / 'elsewhere_decisions.json'
    outside.write_text(
        _versioned_decisions(_current_items(book)), encoding='utf-8', newline='\n')

    with pytest.raises(PathRefused, match='复核决策文件必须在当前书或'):
        global_review.apply_file(
            _ctx(book, 'stage2.global', paid=False), str(outside.resolve()))


def test_a_custom_book_relative_decision_path_is_readable():
    """⭐ **`--emit-review-template` 存到自定义书内相对路径后读不回来。**

    模板是本仓自己生成的、**带版本信封**的（`review_template_json` 调
    `encode_artifact`）。而 `store.read(model, rel)` 按 rel 自己查注册表：
    自定义相对路径未注册 ⇒ 信封不解码 ⇒ `_schema_version` 原样留在 payload 里
    ⇒ StrictModel 报「多了一个字段」。**换个文件名就读不回来**，
    而报错说的是 schema 不对，看不出真正的原因是路径没登记。
    """
    _root, book = _sandbox('书_123')
    _seed(book)
    items = _current_items(book)
    path = book / 'stage2/my_review_decisions.json'
    path.write_text(_versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), 'stage2/my_review_decisions.json')

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_the_emitted_template_round_trips_through_a_custom_relative_path():
    """产地与读回口用的是同一把尺子：emit 出来的字节原样存下就该能提交。"""
    _root, book = _sandbox('书_123')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    emitted = global_review.review_template_json(ctx)
    (book / 'stage2/emitted_template.json').write_text(
        emitted, encoding='utf-8', newline='\n')

    # 模板自己就是一份合法（全 pending）的决策集，读得回来即证明信封解得开。
    updated = global_review.apply_file(ctx, 'stage2/emitted_template.json')
    assert updated.items


def test_downstream_gate_requires_current_blueprint_and_joint_check(tmp_path):
    """全 PASS review 不能替缺失 blueprint 或联合 HARD 背书。"""
    book = tmp_path / '联合收口_123'
    book.mkdir()
    _seed(book, with_blueprints=True)

    _approve_all_review(book)

    # 完整的 current global + current blueprint + 联合 HARD + all-PASS review
    # 才是可供 Stage4 消费的那一态。
    global_review.require_approved(
        _ctx(book, 'stage4.design', paid=False))

    (book / 'stage2/chapter_blueprints.json').unlink()

    # API/queue 的早反馈调用同一入口；没有 blueprint 时不得先创建下游作业。
    with pytest.raises(UpstreamMissing, match='chapter_blueprints'):
        approvals.require_stage2_review(tmp_path, '123', 'stage4.design')
    # 已存在的成书/下载路径也不能只凭旧 review 穿过后来缺失的 blueprint。
    with pytest.raises(UpstreamMissing, match='chapter_blueprints'):
        assemble.read_current_book(
            _ctx(book, 'stage7.assemble', paid=False))


def test_downstream_gate_refuses_a_live_global_invalid_even_with_old_passes(tmp_path):
    """旧 formal/review 都还在，也不能越过一份更新的 global 问题稿。"""
    book = tmp_path / '全局问题稿_124'
    book.mkdir()
    _seed(book, with_blueprints=True)
    _approve_all_review(book)

    formal = book / STAGE2_GLOBAL_GROUNDING
    invalid = book / paths.invalid_of(STAGE2_GLOBAL_GROUNDING)
    invalid.write_bytes(formal.read_bytes())

    with pytest.raises(GateBlocked, match='Stage2 global'):
        global_review.require_approved(
            _ctx(book, 'stage4.design', paid=False))


def test_downstream_gate_requires_the_formal_global_to_be_current(tmp_path):
    """review 只绑定 global 字节；它不能替 stale global 对当前正文背书。"""
    book = tmp_path / '全局过期_125'
    book.mkdir()
    _seed(book, with_blueprints=True)
    _approve_all_review(book)

    clean = book / 'stage0/clean_text.txt'
    clean.write_text(
        clean.read_text(encoding='utf-8') + '根正文已经变化\n',
        encoding='utf-8', newline='')

    with pytest.raises(UpstreamStale, match='global_grounding'):
        global_review.require_approved(
            _ctx(book, 'stage4.design', paid=False))


def test_s2_service_approval_cannot_outvote_a_joint_hard_failure(
        tmp_path, monkeypatch):
    """公开 s2 盖章必须复用联合判官，不能只看 review 全 PASS。"""
    book = tmp_path / '联合红灯_126'
    book.mkdir()
    _seed(book, with_blueprints=True)
    _approve_all_review(book)

    record = approvals._fresh_record('126')
    gates = dict(record.gate)
    gates.update({'s0': True, 's1': True})
    record = record.model_copy(update={
        'gate': gates,
        'seen': {'global': True, 'read': True},
    })
    monkeypatch.setattr(
        approvals, '_read_current', lambda _base, _sid: record)

    from novel.stages.stage2 import extract
    monkeypatch.setattr(extract, 'run_check_only', lambda _ctx_: SimpleNamespace(
        provenance=SimpleNamespace(
            invalid=False, freshness='fresh', reason='synthetic current'),
        passed=False,
        rules=[SimpleNamespace(
            rule='synthetic_joint_hard', sev='hard', status='hit')],
    ))

    with pytest.raises(
            BlockingFindingsUnresolved, match='联合 HARD'):
        approvals.approve_gate(tmp_path, '126', 's2')


def test_workpack_prefers_formal_evidence_and_never_fabricates_for_source_none(
        monkeypatch):
    """正式 SourceSpan 转 EvidenceRef；失败态不能退回 claim 字面搜索。

    反例刻意让 ``世界观`` claim 原样出现在正文里：若 source=None 后仍递归搜 claim，
    就会制造一条看似可用的证据，掩掉抽取阶段明确留下的失败态。
    """
    # 把 SourceSpan.recovery 的字面 ``exact`` 也放进正文；旧递归会把它误当
    # 第二条 claim 搜出来，正式 evidence 路只能产下面那一条原文坐标。
    chapter_text = 'exact开头。两人以掌柜与账房相称。世界观事实就在这里。结尾。'
    literal = '两人以掌柜与账房相称'
    start = chapter_text.index(literal)
    source = {
        'text': literal,
        'start': start,
        'end': start + len(literal),
        'normalized': literal,
        'recovery': 'exact',
    }
    valid = {'chapter_ordinal': 1, 'source': source}
    unresolved = {'chapter_ordinal': 1, 'source': None}
    payload = {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '掌柜', 'name_distinctiveness': 'high',
                    'role': '客栈掌柜', 'role_evidence': valid,
                    'fate': None, 'fate_evidence': None, 'address': [],
                },
                'char_002': {
                    'canonical': '账房', 'name_distinctiveness': 'high',
                    'role': '客栈账房', 'role_evidence': valid,
                    'fate': None, 'fate_evidence': None, 'address': [],
                },
            },
            'relations': [{
                'relation_id': 'rel_01', 'subject_id': 'char_001',
                'object_id': 'char_002', 'predicate': '雇佣',
                'predicate_evidence': valid,
                'visibility': {'kind': 'public', 'from_chapter': 1},
                'valid_from_chapter': 1, 'valid_through_chapter': None,
            }],
        },
        'canon': {
            'setting': {
                '时代背景': '架空年代', 'era_evidence': valid,
                '世界观': '世界观事实就在这里', 'worldview_evidence': unresolved,
            },
            'world_rules': [],
            'secrets': [],
        },
        'arc': {
            'conflict_stages': [], 'climax': '对质', 'ending': '和解',
            'arc_tracks': [],
        },
    }
    review = build_review(payload)
    grounding = SimpleNamespace(model_dump=lambda **_kwargs: payload)
    main = SimpleNamespace(root=[SimpleNamespace(
        chapter_ordinal=1, text=chapter_text)])

    class FakeStore:
        def read_own(self, _model, rel):
            return (review if rel == global_review.REL else grounding, None)

        def read(self, _model, _rel):
            return main

    monkeypatch.setattr(global_review, 'ArtifactStore', lambda _ctx: FakeStore())
    workpack = global_review.review_workpack(SimpleNamespace(sid='synthetic-book'))
    by_path = {row['subject_path']: row for row in workpack['items']}

    role = by_path['entities.characters.char_001.role']
    assert len(role['formal_evidence']) == 1
    assert role['exact_literal_hits'] == [{
        'chapter_ordinal': 1,
        'literal': literal,
        'start': start,
        'end': start + len(literal),
        'context': chapter_text,
        'origin': 'formal_evidence',
        'claim_path': 'entities.characters.char_001.role',
    }]
    ref = EvidenceRef.model_validate(role['formal_evidence'][0]['evidence_ref'])
    assert ref.artifact == 'stage1/main_text.json#chapter/1'
    assert ref.subject_sha256 == hashlib.sha256(
        chapter_text.encode('utf-8')).hexdigest()

    relation = by_path['entities.relations.rel_01']
    assert 'predicate_evidence' not in relation['subject']
    assert all(hit['literal'] == literal for hit in relation['exact_literal_hits'])

    worldview = by_path['canon.setting.世界观']
    assert worldview['formal_evidence'] == []
    assert worldview['exact_literal_hits'] == [], \
        'source=None 不得拿恰好出现的 claim 字面制造 evidence'
    assert worldview['unresolved_formal_evidence'][0]['reason'].endswith('source=None')
    assert '维持 UNVER' in worldview['review_note']
