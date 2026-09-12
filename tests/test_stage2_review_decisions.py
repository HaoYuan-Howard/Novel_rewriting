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
from tests.test_stage2_global_core_edits import seed as seed_current_core


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


def test_a_book_dir_absolute_decision_path_is_accepted(tmp_path):
    core_ctx, _artifact = seed_current_core(tmp_path)
    book = core_ctx.novel_dir
    items = _current_items(book)
    path = book / 'stage2/global_review_decisions.json'
    path.write_text(_versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), str(path.resolve()))

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_a_same_sid_service_edit_decision_path_is_accepted(tmp_path):
    """Supervisor/API 共用的书外一次性输入必须能穿过真实 producer。"""
    core_ctx, _artifact = seed_current_core(tmp_path)
    root, book = tmp_path, core_ctx.novel_dir
    items = _current_items(book)
    path = root / '_service' / 'edits' / core_ctx.sid / 'global-review.json'
    path.parent.mkdir(parents=True)
    path.write_text(
        _versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), str(path.resolve()))

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'



def test_an_outside_absolute_decision_path_is_still_refused(tmp_path):
    core_ctx, _artifact = seed_current_core(tmp_path)
    book = core_ctx.novel_dir
    outside = tmp_path / 'elsewhere_decisions.json'
    outside.write_text(
        _versioned_decisions(_current_items(book)), encoding='utf-8', newline='\n')

    with pytest.raises(PathRefused, match='复核决策文件必须在当前书或'):
        global_review.apply_file(
            _ctx(book, 'stage2.global', paid=False), str(outside.resolve()))


def test_a_custom_book_relative_decision_path_is_readable(tmp_path):
    """⭐ **`--emit-review-template` 存到自定义书内相对路径后读不回来。**

    模板是本仓自己生成的、**带版本信封**的（`review_template_json` 调
    `encode_artifact`）。而 `store.read(model, rel)` 按 rel 自己查注册表：
    自定义相对路径未注册 ⇒ 信封不解码 ⇒ `_schema_version` 原样留在 payload 里
    ⇒ StrictModel 报「多了一个字段」。**换个文件名就读不回来**，
    而报错说的是 schema 不对，看不出真正的原因是路径没登记。
    """
    core_ctx, _artifact = seed_current_core(tmp_path)
    book = core_ctx.novel_dir
    items = _current_items(book)
    path = book / 'stage2/my_review_decisions.json'
    path.write_text(_versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), 'stage2/my_review_decisions.json')

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_the_emitted_template_round_trips_through_a_custom_relative_path(tmp_path):
    """产地与读回口用同一把尺子，但占位 actor 不能冒充审阅。"""
    core_ctx, _artifact = seed_current_core(tmp_path)
    book = core_ctx.novel_dir
    ctx = _ctx(book, 'stage2.global', paid=False)
    emitted = global_review.review_template_json(ctx)
    (book / 'stage2/emitted_template.json').write_text(
        emitted, encoding='utf-8', newline='\n')

    # 能读回当前 v3 信封；真正 apply 另拒绝占位自报，
    # 防止 Commander 只把 UNVER 翻成 PASS 就整批开闸。
    with pytest.raises(HumanEditProtected, match='actor.*占位'):
        global_review.apply_file(ctx, 'stage2/emitted_template.json')


def test_v2_workpack_sha_is_rejected_without_disk_mutation_then_v6_can_apply(tmp_path):
    """旧阅读凭证不能穿过 v6；重新读新包并绑定新 SHA 后才允许写回。

    legacy payload 只在测试里从当前同一批主体重建，用来表示升级前实际 v2 形状；
    生产应用口仍只现算 v6，不保留第二个旧 workpack builder。
    """
    core_ctx, _artifact = seed_current_core(tmp_path)
    root, book = tmp_path, core_ctx.novel_dir
    ctx = _ctx(book, 'stage2.global', paid=False)
    current_v6 = global_review.review_workpack(ctx)
    current_review = ArtifactStore(ctx).read(GlobalReview, global_review.REL)

    legacy = copy.deepcopy(current_v6)
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
                'claim_path': row['subject_path'],
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
    assert legacy_v2_sha != current_v6['workpack_sha256']

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

    write_decision(str(current_v6['workpack_sha256']))
    updated = global_review.apply_file(ctx)
    assert all(item.state == 'PASS' for item in updated.items
               if review_effect_of(item.kind) == 'blocking')
    assert {item.item_id for item in updated.items} == {
        item.item_id for item in current_review.items}
    assert (rounds / 'global_review.r1.json').read_bytes() == before['review']



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


@pytest.mark.parametrize('evidence_failure', ['missing', 'digest_mismatch'])
def test_core_material_rows_prefers_anchors_and_never_fabricates_missing_evidence(evidence_failure):
    """空 setting 与坏 anchor 不混淆；坏证据即使命中原文也不能退回字面证明。

    旧 SourceSpan/source=None 的证据职责由本反例承接。Opening 的连续开篇及
    Hook 独立支持属于 Gate C；这里不复活 source_blurb 或动态 fate companion。
    """
    from novel.stages.stage2.core_evidence import evidence_id
    chapter_text = 'exact开头。两人以掌柜与账房相称。世界观事实就在这里。结尾。'
    literal = '两人以掌柜与账房相称'
    start = chapter_text.index(literal)
    anchor = dict(chapter_ordinal=1, start=start, end=start + len(literal),
                  text_sha256=hashlib.sha256(literal.encode()).hexdigest())
    ref = evidence_id(anchor)
    unresolved = 'ev_' + 'f' * 24
    anchors = {ref: anchor}
    if evidence_failure == 'digest_mismatch':
        context_start = chapter_text.index('世界观事实就在这里')
        bad_anchor = dict(chapter_ordinal=1, start=context_start,
                          end=context_start + len('世界观事实就在这里'),
                          text_sha256='0' * 64)
        unresolved = evidence_id(bad_anchor)
        anchors[unresolved] = bad_anchor
    registry = dict(characters={}, evidence_anchors={ref: anchor})
    for index, surface in enumerate(('掌柜', '账房'), 1):
        registry['characters'][f'char_{index:03d}'] = dict(canonical=surface,
            aliases=[dict(surface=surface, identity_revealed_from=1, evidence_refs=[ref])],
            first_chapter=1, first_appearance_evidence_refs=[ref], role='客栈' + surface,
            role_evidence_refs=[ref], name_distinctiveness='high')
    payload = dict(characters_reference_sha256='a' * 64,
        setting=dict(era=None, era_evidence_refs=[], social_context='世界观事实就在这里',
                     social_context_evidence_refs=[unresolved]),
        relations=[dict(relation_id='rel_01', subject_id='char_001', object_id='char_002',
                        predicate='雇佣', effective_from=1, effective_until=None,
                        revealed_from=1, evidence_refs=[ref])],
        world_rules=[], arc_secrets=[], evidence_anchors=anchors)
    review = build_review(payload, characters=registry)
    main = SimpleNamespace(root=[SimpleNamespace(chapter_ordinal=1, text=chapter_text)])
    rows = global_review._material_rows(payload, review, main, registry, 'all')
    by_path = {row['subject_path']: row for row in rows}
    role = by_path['characters.characters.char_001.role']
    assert len(role['formal_evidence']) == 1 and role['exact_literal_hits'] == []
    assert role['reading_scope'] == 'localized' and role['evidence_mode'] == 'formal_evidence'
    evidence = EvidenceRef.model_validate(role['formal_evidence'][0]['evidence_ref'])
    assert evidence.artifact == 'stage1/main_text.json#chapter/1'
    assert evidence.subject_sha256 == hashlib.sha256(chapter_text.encode()).hexdigest()
    assert (evidence.start, evidence.end) == (start, start + len(literal))
    relation = by_path['global.relations.rel_01']
    assert 'evidence_refs' not in relation['subject']
    assert relation['exact_literal_hits'] == [] and relation['evidence_mode'] == 'formal_evidence'
    context = by_path['global.setting.social_context']
    assert context['formal_evidence'] == [] and context['exact_literal_hits'] == []
    assert context['evidence_mode'] == 'unresolved_formal_evidence'
    assert 'anchor' in context['unresolved_formal_evidence'][0]['reason']
    absent = by_path['global.setting.era']
    assert absent['subject'] is None
    assert absent['formal_evidence'] == absent['unresolved_formal_evidence'] == []
    assert absent['exact_literal_hits'] == [] and absent['evidence_mode'] == 'none'
    assert absent['current_state'] == context['current_state'] == 'pending'
    assert absent['effect'] == context['effect'] == 'blocking'
    # 字面搜索可辅助名字判别，不能替代已有但缺失的正式 anchor。
    name = by_path['characters.characters.char_001.name_distinctiveness']
    assert name['evidence_mode'] == 'claim_literal' and name['exact_literal_hits']
    complete = by_path['global.arc_secrets']
    assert complete['reading_scope'] == 'whole_book' and complete['evidence_mode'] == 'none'
    assert all('fate_companion' not in row for row in rows)
    blocking = global_review._material_rows(payload, review, main, registry, 'blocking')
    assert [row['item_id'] for row in blocking] == [row['item_id'] for row in rows]



def test_role_hidden_identity_change_reopens_acceptance_without_fate_companion():
    """原首次公开 role 的接受不能覆盖新增泄底描述；不是声称代码自动判断文学泄底。"""
    from contracts.schema.stage1 import MainText
    from novel.stages.stage2.core_evidence import evidence_id

    texts = ['掌柜照管这间客栈。', '掌柜其实是失踪多年的东家。']
    anchors = []
    for ordinal, text in enumerate(texts, 1):
        anchor = dict(chapter_ordinal=ordinal, start=0, end=len(text),
                      text_sha256=hashlib.sha256(text.encode()).hexdigest())
        anchors.append((evidence_id(anchor), anchor))
    role_ref, role_anchor = anchors[0]
    secret_ref, secret_anchor = anchors[1]
    registry = dict(characters={'char_001': dict(
        canonical='掌柜', aliases=[dict(surface='掌柜', identity_revealed_from=1,
                                       evidence_refs=[role_ref])],
        first_chapter=1, first_appearance_evidence_refs=[role_ref],
        role='照管客栈的掌柜', role_evidence_refs=[role_ref], name_distinctiveness='common')},
        evidence_anchors={role_ref: role_anchor})
    payload = dict(characters_reference_sha256='a' * 64,
        setting=dict(era=None, era_evidence_refs=[], social_context=None,
                     social_context_evidence_refs=[]), relations=[], world_rules=[],
        arc_secrets=[dict(secret_id='secret_01', fact='掌柜是失踪多年的东家',
                         fact_ref=None, setup_chapters=[], revealed_from=2,
                         evidence_refs=[secret_ref])],
        evidence_anchors={secret_ref: secret_anchor})
    accepted = build_review(payload, characters=registry)
    for item in tuple(accepted.items):
        accepted = decide(accepted, item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256, state='PASS', actor='fixture-commander',
            reason='分别核对首次身份和后来揭示的真相', now='2026-09-12T00:00:00Z')
    role_path = 'characters.characters.char_001.role'
    original_role = next(item for item in accepted.items if item.subject_path == role_path)
    changed = copy.deepcopy(registry)
    changed['characters']['char_001']['role'] = '照管客栈、实为失踪多年东家的掌柜'
    changed['characters']['char_001']['role_evidence_refs'].append(secret_ref)
    changed['evidence_anchors'][secret_ref] = secret_anchor
    before = copy.deepcopy((changed, payload, accepted))

    current = build_review(payload, accepted, characters=changed)

    role = next(item for item in current.items if item.subject_path == role_path)
    assert original_role.state == 'PASS' and role.state == 'pending'
    assert role.subject_sha256 != original_role.subject_sha256
    assert role.actor is None and role.reason is None and role.evidence == []
    assert all(item.state == 'PASS' for item in current.items
               if item.subject_path.startswith('global.'))
    assert not global_review.generation_safety(payload, current, characters=changed).safe
    main = MainText.model_validate([
        dict(chapter_ordinal=ordinal, text=text) for ordinal, text in enumerate(texts, 1)])
    rows = global_review._material_rows(payload, current, main, changed, 'blocking')
    by_path = {row['subject_path']: row for row in rows}
    assert by_path[role_path]['subject'] == changed['characters']['char_001']['role']
    secret = by_path['global.arc_secrets.secret_01']
    assert secret['subject']['fact'] == '掌柜是失踪多年的东家'
    assert secret['current_state'] == 'PASS' and secret['effect'] == 'blocking'
    assert all('fate_companion' not in row and '.fate' not in row['subject_path'] for row in rows)
    assert (changed, payload, accepted) == before
