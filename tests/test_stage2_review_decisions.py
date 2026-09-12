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

import copy
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from novel.errors import HumanEditProtected, PathRefused
from contracts.artifacts import (
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.invariants import review_effect_of
from contracts.schema import encode_artifact
from contracts.schema.evaluation import EvidenceRef, GlobalReview
from novel import paths
from novel.errors import (
    BlockingFindingsUnresolved, GateBlocked, UpstreamMissing, UpstreamStale,
)
from novel.orchestrator import approvals
from novel.quality.global_review import build_review, decide, subject_sha256
from novel.stages.stage2 import global_review
from novel.stages.stage7 import assemble
from novel.store import ArtifactStore
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _sandbox, _seed


@pytest.fixture(autouse=True)
def _pin_retained_ir31_for_downstream_fixture(monkeypatch):
    """本文件验证下游 gate；底座显式使用当前完整剧情合成 wire。"""
    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')


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


def _accept_global_node(book):
    """合成 Commander 明确核过逐项决定后，提交当前 Global 节点接受。"""
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    ctx = readonly_ctx(book, 'stage2.global', prov.code_version())
    return approvals.approve_stage2_review(book.parent, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=global_review.review_token(ctx),
        decision='accept', actor='fixture-global-commander',
        reason='合成 fixture 已逐项核对当前 Global 与正文'))


def _accept_blueprint_node(book, monkeypatch):
    from tests.stage2_fixtures import accept_and_materialize_links
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    accept_and_materialize_links(ctx)
    return approvals.get(book.parent, ctx.sid, include_spawns=False)


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


def test_a_same_sid_service_edit_decision_path_is_accepted():
    """Supervisor/API 共用的书外一次性输入必须能穿过真实 producer。"""
    root, book = _sandbox('书_123')
    try:
        _seed(book)
        items = _current_items(book)
        path = root / '_service' / 'edits' / '123' / 'global-review.json'
        path.parent.mkdir(parents=True)
        path.write_text(
            _versioned_decisions(items), encoding='utf-8', newline='\n')

        updated = global_review.apply_file(
            _ctx(book, 'stage2.global', paid=False), str(path.resolve()))

        decided = {item.item_id: item.state for item in updated.items}
        assert decided[items[0]['item_id']] == 'PASS'
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
    """产地与读回口用同一把尺子，但占位 actor 不能冒充审阅。"""
    _root, book = _sandbox('书_123')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    emitted = global_review.review_template_json(ctx)
    (book / 'stage2/emitted_template.json').write_text(
        emitted, encoding='utf-8', newline='\n')

    # 能读回当前 v2 信封；真正 apply 另拒绝占位自报，
    # 防止 Commander 只把 UNVER 翻成 PASS 就整批开闸。
    with pytest.raises(HumanEditProtected, match='actor.*占位'):
        global_review.apply_file(ctx, 'stage2/emitted_template.json')


def test_v2_workpack_sha_is_rejected_without_disk_mutation_then_v3_can_apply():
    """旧阅读凭证不能穿过 v3；重新读新包并绑定新 SHA 后才允许写回。

    legacy payload 只在测试里从当前同一批主体重建，用来表示升级前实际 v2 形状；
    生产应用口仍只现算 v3，不保留第二个旧 workpack builder。
    """
    root, book = _sandbox('工作包升级_123')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        current_v3 = global_review.review_workpack(ctx)
        current_review = ArtifactStore(ctx).read(GlobalReview, global_review.REL)

        legacy = copy.deepcopy(current_v3)
        legacy.pop('workpack_sha256')
        legacy['kind'] = 'stage2-global-review-workpack-v2'
        legacy.pop('legend')
        legacy_by_id = {row['item_id']: row for row in legacy['items']}
        legacy['items'] = [
            legacy_by_id[item.item_id] for item in current_review.items
            if item.item_id in legacy_by_id
        ]
        for row in legacy['items']:
            reading_scope = row.pop('reading_scope')
            evidence_mode = row.pop('evidence_mode')
            row.pop('effect')
            row.pop('group_key')
            row.pop('fate_companion', None)
            if row['formal_evidence']:
                row['exact_literal_hits'] = [{
                    'chapter_ordinal': evidence['chapter_ordinal'],
                    'literal': evidence['literal'],
                    'start': evidence['start'],
                    'end': evidence['end'],
                    'context': evidence['context'],
                    'origin': 'formal_evidence',
                    'claim_path': evidence['claim_path'],
                } for evidence in row['formal_evidence']]
            base_note = (
                '集合完整性项必须通读全书核对；0 个精确命中不等于 PASS。'
                if reading_scope == 'whole_book' else
                '这些命中只缩小人工阅读范围，不产生 PASS/FAIL/UNVER。')
            if evidence_mode == 'unresolved_formal_evidence':
                row['review_note'] = (
                    '正式 evidence 含 source=None 或无效坐标；必须维持 UNVER，'
                    '不能用 claim 字面命中代替证据。' + base_note)
            elif evidence_mode == 'formal_evidence':
                row['review_note'] = (
                    '已优先把正式 evidence 转为 hash-bound EvidenceRef 与正文上下文；'
                    '它仍不自动产生裁决。' + base_note)
            else:
                row['review_note'] = base_note
        legacy_v2_sha = subject_sha256(legacy)
        assert legacy_v2_sha != current_v3['workpack_sha256']

        template = global_review.review_template(ctx)
        assert template.blocking_scope is not None

        def decision_bound_to(workpack_sha256: str):
            return template.model_copy(update={
                'actor': 'scope-reviewer',
                'blocking_scope': template.blocking_scope.model_copy(update={
                    'expected_workpack_sha256': workpack_sha256,
                    'state': 'PASS',
                    'reason': '已通读当前工作包并逐卡核对全部 blocking 项',
                }),
            })

        decision_path = book / STAGE2_GLOBAL_REVIEW_DECISIONS

        def write_decision(workpack_sha256: str) -> None:
            supplied = decision_bound_to(workpack_sha256)
            decision_path.write_text(json.dumps(encode_artifact(
                STAGE2_GLOBAL_REVIEW_DECISIONS,
                supplied.model_dump(mode='json', by_alias=True),
            ), ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')

        rounds = book / 'stage2/_rounds'

        def protected_bytes():
            return {
                'formal': (book / STAGE2_GLOBAL_GROUNDING).read_bytes(),
                'review': (book / global_review.REL).read_bytes(),
                'rounds': ({
                    path.relative_to(rounds).as_posix(): path.read_bytes()
                    for path in rounds.rglob('*') if path.is_file()
                } if rounds.exists() else {}),
            }

        write_decision(legacy_v2_sha)
        before = protected_bytes()
        with pytest.raises(HumanEditProtected, match='workpack changed'):
            global_review.apply_file(ctx)
        assert protected_bytes() == before, \
            '旧 v2 SHA 拒绝必须发生在 formal/review/_rounds 的首个写盘动作前'

        write_decision(str(current_v3['workpack_sha256']))
        updated = global_review.apply_file(ctx)
        assert all(item.state == 'PASS' for item in updated.items
                   if review_effect_of(item.kind) == 'blocking')
        assert {item.item_id for item in updated.items} == {
            item.item_id for item in current_review.items}
        assert (rounds / 'global_review.r1.json').read_bytes() == before['review']
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_downstream_gate_requires_current_blueprint_and_joint_check(tmp_path, monkeypatch):
    """全 PASS review 不能替缺失 blueprint 或联合 HARD 背书。"""
    book = tmp_path / '联合收口_123'
    book.mkdir()
    _seed(book, with_blueprints=True)

    _approve_all_review(book)
    _accept_global_node(book)
    _accept_blueprint_node(book, monkeypatch)

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
    _accept_global_node(book)

    record = approvals._fresh_record('126')
    gates = dict(record.gate)
    gates.update({'s0': True, 's1': True})
    record = record.model_copy(update={
        'gate': gates,
        'seen': {'global': True, 'read': True},
    })
    monkeypatch.setattr(
        approvals, '_read_state', lambda _base, _sid: (record, record))

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
        monkeypatch, tmp_path):
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
                    'fate': '后来成为客栈东家', 'fate_evidence': valid,
                    'address': [],
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
        'source_blurb': {'facts': [], 'opening': {'kind': 'proposed', 'through_chapter': 1, 'rationale': '开篇交代掌柜', 'boundary_evidence': valid}},
    }
    review = build_review(payload)
    grounding = SimpleNamespace(model_dump=lambda **_kwargs: payload)
    main = SimpleNamespace(
        root=[SimpleNamespace(chapter_ordinal=1, text=chapter_text)],
        model_dump=lambda **_kwargs: [{
            'chapter_ordinal': 1,
            'text': chapter_text,
        }],
    )

    class FakeStore:
        def read_own(self, _model, rel, **_kwargs):
            if rel == global_review.REL:
                return review, None
            if rel == global_review.STAGE1_HOOK_COPY:
                return SimpleNamespace(text='掌柜面临新的经营挑战。'), None
            if rel == global_review.STAGE1_MAIN_TEXT:
                return main, None
            return grounding, None

        def read(self, _model, _rel):
            return main

    monkeypatch.setattr(global_review, 'ArtifactStore', lambda _ctx: FakeStore())
    monkeypatch.setattr(global_review.grounding_io, 'read_own', lambda *_args, **_kwargs: (grounding, None))
    from novel.orchestrator import approvals
    monkeypatch.setattr(approvals, 'stage2_review_history', lambda *_args: [])
    workpack = global_review.review_workpack(SimpleNamespace(sid='synthetic-book', novel_dir=tmp_path / '书_synthetic-book'))
    by_path = {row['subject_path']: row for row in workpack['items']}
    full_workpack = global_review.review_workpack(
        SimpleNamespace(sid='synthetic-book', novel_dir=tmp_path / '书_synthetic-book'), scope='all')
    full_by_path = {row['subject_path']: row for row in full_workpack['items']}

    role = by_path['entities.characters.char_001.role']
    assert len(role['formal_evidence']) == 1
    assert role['exact_literal_hits'] == [], \
        'formal evidence 已含坐标与上下文，不得再复制成 literal hit'
    assert role['reading_scope'] == 'localized'
    assert role['evidence_mode'] == 'formal_evidence'
    ref = EvidenceRef.model_validate(role['formal_evidence'][0]['evidence_ref'])
    assert ref.artifact == 'stage1/main_text.json#chapter/1'
    assert ref.subject_sha256 == hashlib.sha256(
        chapter_text.encode('utf-8')).hexdigest()

    relation = by_path['entities.relations.rel_01']
    assert 'predicate_evidence' not in relation['subject']
    assert relation['exact_literal_hits'] == []
    assert relation['evidence_mode'] == 'formal_evidence'

    worldview = by_path['canon.setting.世界观']
    assert worldview['formal_evidence'] == []
    assert worldview['exact_literal_hits'] == [], \
        'source=None 不得拿恰好出现的 claim 字面制造 evidence'
    assert worldview['unresolved_formal_evidence'][0]['reason'].endswith('source=None')
    assert worldview['evidence_mode'] == 'unresolved_formal_evidence'

    opening = by_path['source_blurb.opening']
    assert opening['reading_scope'] == 'whole_book'
    assert opening['evidence_mode'] == 'formal_evidence', \
        '整书阅读与 evidence 模式是正交轴，不能互相覆盖'

    companion = role['fate_companion']
    fate_item = next(item for item in review.items
                     if item.subject_path == 'entities.characters.char_001.fate')
    assert set(companion) == {
        'read_only', 'kind', 'item_id', 'subject_path', 'subject_sha256',
        'current_state', 'current_reason', 'generation_disposition',
        'effect', 'generation_impact', 'subject', 'formal_evidence',
        'unresolved_formal_evidence',
    }
    assert companion['read_only'] is True
    assert companion['kind'] == 'fate'
    assert companion['item_id'] == fate_item.item_id
    assert companion['subject_path'] == fate_item.subject_path
    assert companion['subject_sha256'] == fate_item.subject_sha256
    assert companion['current_state'] == 'pending'
    assert companion['current_reason'] is None
    assert companion['generation_disposition'] is None
    assert companion['effect'] == 'disclosure'
    assert companion['generation_impact'] == 'disclose'
    assert companion['subject'] == '后来成为客栈东家'
    assert companion['unresolved_formal_evidence'] == []
    assert len(companion['formal_evidence']) == 1
    empty_companion = by_path['entities.characters.char_002.role']['fate_companion']
    assert empty_companion['subject'] is None
    assert empty_companion['formal_evidence'] == []
    assert empty_companion['unresolved_formal_evidence'] == [], \
        'fate=null 是合法主体，不得伪报成证据回取失败'

    name = full_by_path['entities.characters.char_001.name_distinctiveness']
    assert name['evidence_mode'] == 'claim_literal'
    assert name['exact_literal_hits']
    complete = by_path['source_blurb.facts']
    assert complete['reading_scope'] == 'whole_book'
    assert complete['evidence_mode'] == 'none'
    assert 'fate_companion' not in full_by_path['entities.characters.char_001.role'], \
        'all scope 已有同组 fate 正式行，不得再复制一份 companion'
