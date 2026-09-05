#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_secret_edit.py —— `stage2.global --apply-edit`：Stage2 typed facts 的 0 成本人工订正。

【它守的是什么】细粒度 patch 先补上已有 secret 字段的修订入口；完整 replace surface
继续接住 patch 无法表达的集合级决定：拆分复合 secret、增删 world rule、重建 address 列表。
两条路都必须有严格 schema、来源账、同一归一/判据、轮次归档和 global-review 刷新。

【四条各对应一次会静默发生的损失】
  · **越界章号**  —— 越界的揭示章让逐章剧透闸门**永不触发**（`R-6.3` 按它开闸），
                    而产物里那一格看着一切正常 ⇒ 落盘之前显式终态；
  · **未知字段**  —— 人手写的 JSON 里一个拼错的键会被静默忽略，
                    于是「我改过了」与「什么都没改」在盘上同形 ⇒ 载体 `extra='forbid'`；
  · **同一判定**  —— 人工稿绕过闸门就是给自己开一个免检写口 ⇒ HARD 走 `.INVALID`，
                    **正式全书底座不被覆盖**；
  · **下游 stale** —— 底座变了而下游还按旧的算，是全链最贵的一类错
                    ⇒ 由字节指纹与字段投影两条边同时保证。

⚠ 本文件**全程合成语料**，不依赖 `NOVEL_OUTPUT`：底座三章来自 `tests/test_stage2.py`。
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    ChapterBlueprints, GlobalEdit, GlobalEditCommit, GlobalEditFailure,
    GlobalGrounding,
)
from novel.errors import (
    GateBlocked, ProvenanceRefused, TerminalState, UnsupportedRunMode,
    UpstreamStale,
)
from novel.orchestrator import (
    execution_permit, operator, queue, state as orchestrator_state, uploads,
)
from novel.orchestrator.state import readonly_ctx
from novel.quality.global_review import decide
from novel.stages.stage2 import global_review as global_review_ops
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.factories import approved_global_review
from tests.test_stage2_generation import (
    _client, _ctx, _ok, _respond_blueprints, _response_blueprints,
    _response_global,
)
from tests.test_stage2 import main_text

BLUEPRINT_EDGE = 'stage2/blueprint_character_projection.json'


#: 通用底座固定在默认 v3.1；契约特有拓扑由 dispatch 测试覆盖。
@pytest.fixture(autouse=True)
def _pin_ir31_arm(monkeypatch):
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')


def _seed(book: Path, *, with_blueprints: bool = False) -> None:
    """把这本书跑到「有正式全书底座」（可选：连逐章蓝图一起）。**一次真调用都不发。**"""
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    client, _transport = _client(global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, client)
    if not with_blueprints:
        return
    by_ordinal = _response_blueprints()

    def respond(req, _index):
        return _respond_blueprints(req, by_ordinal)

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    bp_client, _bp_transport = _client(blueprint_ctx, respond)
    extract.run_blueprint(blueprint_ctx, bp_client)


def _write_edit(book: Path, payload: dict) -> None:
    """把人写的当前版本决策集落到书目录里。"""
    (book / 'stage2').mkdir(parents=True, exist_ok=True)
    (book / extract.GLOBAL_EDIT).write_text(
        json.dumps(encode_artifact(extract.GLOBAL_EDIT, payload),
                   ensure_ascii=False, indent=2),
        encoding='utf-8', newline='\n')


def _locator(chapter: int, locator: str) -> dict:
    """GlobalEdit/响应证据形；正式 SourceSpan 一律由生产归一回取。"""
    return {'chapter_ordinal': chapter, 'locator': locator}


def _fact_locator() -> dict:
    return _locator(1, '信上说父亲已经回城。')


def _role_locator() -> dict:
    return _locator(3, '李宝珠把账册摊在桌上一页页翻。')


def _knowledge_character(char_id: str, chapter: int) -> dict:
    locator = ('她想起了昨夜那封信。' if chapter == 1
               else '其实母亲早就知道那封信的事。')
    return {
        'subject': {'kind': 'character', 'char_id': char_id},
        'known_from_chapter': chapter,
        'evidence': _locator(chapter, locator),
    }


def _knowledge_group(label: str, chapter: int = 2) -> dict:
    return {
        'subject': {'kind': 'group', 'label': label},
        'known_from_chapter': chapter,
        'evidence': _locator(chapter, '其实母亲早就知道那封信的事。'),
    }


def _apply(book: Path):
    return extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))


def test_character_set_fail_emits_a_complete_typed_recovery_instead_of_crashing():
    root, book = _sandbox('人物集合恢复_002c')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        grounding = store.read(GlobalGrounding, extract.GLOBAL_REL)
        review = approved_global_review(grounding)
        target = next(item for item in review.items
                      if item.kind == 'character_set_complete')
        review = decide(
            review,
            item_id=target.item_id,
            expected_subject_sha256=target.subject_sha256,
            state='FAIL', actor='test-reviewer',
            reason='原文还有一名参与主线的人物未抽取',
            now='2026-09-02T00:00:00+00:00')
        store.write(extract.GLOBAL_REVIEW_REL, review)

        template = extract.global_edit_template(ctx)

        [collection_edit] = template.collection_edits
        assert collection_edit.collection == 'characters'
        assert collection_edit.operations == []
        assert len(collection_edit.expected_collection_sha256) == 64
        assert template.characters_replace is None
        assert template.characters == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_second_round_reviewed_edit_commits_global_and_closed_review_together():
    root, book = _sandbox('两轮闭合_002d')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        grounding = store.read(GlobalGrounding, extract.GLOBAL_REL)
        review = approved_global_review(grounding)
        target = next(
            item for item in review.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        review = decide(
            review,
            item_id=target.item_id,
            expected_subject_sha256=target.subject_sha256,
            state='FAIL', actor='test-reviewer',
            reason='人物身份混入了错误事实',
            now='2026-09-02T00:00:00+00:00')
        store.write(extract.GLOBAL_REVIEW_REL, review)
        _write_edit(book, {
            'characters': [{
                'char_id': 'char_001',
                'role': '沈家账房，负责核对往来账目',
                'role_evidence': _role_locator(),
            }],
            'post_edit_review': {
                'actor': 'agent:novel-pipeline-operator',
                'expected_source_sha256': review.source_sha256,
                'reviewed_chapter_ordinals': [1, 2, 3],
                'default_state': 'PASS',
                'reason': '第二轮已复核精确修改后的完整 blocking 主体',
                'quarantine_groups': [],
            },
        })

        artifact, report = _apply(book)

        assert report.passed
        closed = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        corrected = next(
            item for item in closed.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        assert corrected.state == 'PASS'
        assert corrected.actor == 'agent:novel-pipeline-operator'
        assert corrected.subject_sha256 != target.subject_sha256
        assert artifact.entities.characters['char_001'].role.startswith('沈家账房')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reviewed_correction_reapplies_to_formal_after_first_edit_left_invalid():
    """第二次 Commander 纠错不叠加 INVALID，并可闭合首轮 HARD。"""
    root, book = _sandbox('两轮闸门纠错_002e')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        grounding = store.read(GlobalGrounding, extract.GLOBAL_REL)
        review = approved_global_review(grounding)
        target = next(
            item for item in review.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        review = decide(
            review,
            item_id=target.item_id,
            expected_subject_sha256=target.subject_sha256,
            state='FAIL', actor='test-reviewer',
            reason='人物身份需要返工',
            now='2026-09-02T00:00:00+00:00')
        store.write(extract.GLOBAL_REVIEW_REL, review)
        review_workpack = global_review_ops.review_workpack(
            ctx, scope='blocking', replaced_input_keys=(extract.GLOBAL_EDIT,))
        attestation = {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': review.source_sha256,
            'reviewed_chapter_ordinals': [1, 2, 3],
            'default_state': 'PASS',
            'reason': '已复核纠正后的完整 blocking 主体',
            'quarantine_groups': [],
            'expected_workpack_sha256': review_workpack['workpack_sha256'],
            'review_scope': [{
                'item_id': target.item_id,
                'expected_subject_sha256': target.subject_sha256,
            }],
            'decisions': [],
        }

        _write_edit(book, {
            'characters': [
                {
                    'char_id': 'char_001',
                    'role': '李宝珠',
                    'role_evidence': _role_locator(),
                },
                {
                    # 模拟旧 full-template assembler：显式写回 formal 原值。
                    'char_id': 'char_002',
                    'name_distinctiveness': grounding.entities.characters[
                        'char_002'].name_distinctiveness,
                },
            ],
            'collection_edits': [{
                # 旧 wire 还会把 template scaffold 的空 operations 当提交行。
                'collection': 'relations',
                'expected_collection_sha256': extract.subject_sha256(
                    grounding.model_dump(mode='json', by_alias=True)[
                        'entities']['relations']),
                'operations': [],
            }],
            'post_edit_review': attestation,
        })
        formal_before = (book / extract.GLOBAL_REL).read_bytes()
        with pytest.raises(GateBlocked, match='role_repeats_name'):
            extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))
        assert (book / extract.GLOBAL_REL).read_bytes() == formal_before
        assert (book / extract._GLOBAL_INVALID).is_file()
        workpack = extract.global_edit_correction_workpack(
            _ctx(book, 'stage2.global', paid=False))
        assert workpack['commander_budget_max'] == 3
        assert workpack['incremental_correction'] is True
        assert workpack['recovery_fact_kind'] == 'registered_sidecar'
        assert workpack['failure_kind'] == 'hard_gate'
        assert isinstance(workpack['failure_sha256'], str)
        assert workpack['hard_hits'] == ['role_repeats_name']
        assert workpack['hard_outcomes'][0]['findings'][0]['evidence'] == [
            "char_001:'李宝珠'"]
        failed_edit = decode_artifact(
            extract.GLOBAL_EDIT, workpack['failed_edit']).payload
        assert failed_edit['characters'][0]['role'] == '李宝珠'
        assert workpack['global_review_workpack']['binding'][
            'expected_source_sha256'] == review.source_sha256
        ov = orchestrator_state.overview(root, '002e')
        assert orchestrator_state.stage2_reviewed_edit_correction_needed(
            root, '002e', ov)

        # reviewed INVALID 若已被人直接修改，不能让 Commander 的 formal sparse
        # correction 覆盖这份人工候选；planner 应改暴露 manual successor。
        invalid_path = book / extract._GLOBAL_INVALID
        invalid_before = invalid_path.read_bytes()
        human = json.loads(invalid_before)
        human['entities']['characters']['char_001']['role'] = '人工候选身份'
        invalid_path.write_text(
            json.dumps(human, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')
        human_ov = orchestrator_state.overview(root, '002e')
        assert not orchestrator_state.stage2_reviewed_edit_correction_needed(
            root, '002e', human_ov)
        human_next = operator.next_action(root, book.name)
        assert human_next.action is not None
        assert human_next.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_TERMINAL
        invalid_path.write_bytes(invalid_before)

        # sidecar 引入前已落稳的 exact edit + INVALID 仍能恢复；不能因为升级
        # current schema 就把旧 Commander 动作解释成“没发生”。
        (book / extract.GLOBAL_EDIT_FAILURE).unlink()
        legacy_workpack = extract.global_edit_correction_workpack(
            _ctx(book, 'stage2.global', paid=False))
        assert legacy_workpack['recovery_fact_kind'] == 'legacy_invalid'
        assert legacy_workpack['failure_sha256'] is None
        assert legacy_workpack['hard_hits'] == ['role_repeats_name']
        legacy_failed = decode_artifact(
            extract.GLOBAL_EDIT, legacy_workpack['failed_edit']).payload
        assert [row['char_id'] for row in legacy_failed['characters']] == [
            'char_001']
        assert 'collection_edits' not in legacy_failed
        assert legacy_workpack['failed_edit_sha256'] == (
            ArtifactStore(ctx).full_sha256_or_none(extract.GLOBAL_EDIT))
        legacy_overview = orchestrator_state.overview(root, '002e')
        assert orchestrator_state.stage2_reviewed_edit_correction_needed(
            root, '002e', legacy_overview)

        _write_edit(book, {
            'characters': [{
                'char_id': 'char_001',
                'role': '沈家账房，负责核对往来账目',
                'role_evidence': _role_locator(),
            }],
            'post_edit_review': attestation,
        })
        artifact, report = extract.run_apply_edit(
            _ctx(book, 'stage2.global', paid=False))

        assert report.passed
        assert artifact.entities.characters['char_001'].role == (
            '沈家账房，负责核对往来账目')
        assert not (book / extract._GLOBAL_INVALID).exists()
        closed_overview = orchestrator_state.overview(root, '002e')
        assert not orchestrator_state.stage2_reviewed_edit_correction_needed(
            root, '002e', closed_overview)
        closed = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        corrected = next(
            item for item in closed.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        assert corrected.state == 'PASS'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_post_review_failure_builds_correction_and_only_new_decision_can_land(
        tmp_path):
    """漏掉 surviving FAIL 不伪装 HARD；补 decision 时保留原业务 delta。"""
    root, book = _tmp_book(tmp_path, '修后复核纠正_002f')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        grounding = store.read(GlobalGrounding, extract.GLOBAL_REL)
        review = approved_global_review(grounding)
        target = next(
            item for item in review.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        review = decide(
            review,
            item_id=target.item_id,
            expected_subject_sha256=target.subject_sha256,
            state='FAIL', actor='test-reviewer',
            reason='这条未改主体必须在修后复核中重新裁决',
            now='2026-09-05T00:00:00+00:00')
        store.write(extract.GLOBAL_REVIEW_REL, review)
        review_workpack = global_review_ops.review_workpack(
            ctx, scope='blocking', replaced_input_keys=(extract.GLOBAL_EDIT,))
        attestation = {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': review.source_sha256,
            'reviewed_chapter_ordinals': [1, 2, 3],
            'default_state': 'PASS',
            'reason': '已复核本次 secret 改动打开的全部 blocking 主体',
            'quarantine_groups': [],
            'expected_workpack_sha256': review_workpack['workpack_sha256'],
            'review_scope': [{
                'item_id': target.item_id,
                'expected_subject_sha256': target.subject_sha256,
            }],
            'decisions': [],
        }
        business = {
            'secrets': [{
                'secret_id': 'secret_01', 'first_reveal_chapter': 3,
            }],
        }
        _write_edit(book, {**business, 'post_edit_review': attestation})

        with pytest.raises(TerminalState, match='blocking'):
            _apply(book)

        assert not (book / extract._GLOBAL_INVALID).exists()
        pack = extract.global_edit_correction_workpack(
            _ctx(book, 'stage2.global', paid=False))
        assert pack['failure_kind'] == 'post_edit_review'
        assert pack['hard_hits'] == []
        assert pack['invalid_sha256'] is None
        assert pack['failure_code'] == 'blocking_remaining'
        failed = GlobalEdit.model_validate(
            decode_artifact(extract.GLOBAL_EDIT, pack['failed_edit']).payload)
        assert failed.secrets[0].first_reveal_chapter == 3
        ov = orchestrator_state.overview(root, '002f')
        assert orchestrator_state.stage2_reviewed_edit_correction_needed(
            root, '002f', ov)

        terminal_failure = store.read_own(
            GlobalEditFailure, extract.GLOBAL_EDIT_FAILURE)[0]
        pending = extract._build_global_edit_failure(
            store,
            failure_kind='canonical_pending',
            failed_edit_sha256=terminal_failure.failed_edit_sha256,
            formal_sha256=terminal_failure.formal_sha256,
            expected_source_sha256=terminal_failure.expected_source_sha256,
            global_review_workpack_sha256=(
                terminal_failure.global_review_workpack_sha256),
            reason_code='canonical_input_persisted',
            reason_summary='canonical edit 已落稳，等待 deterministic replay',
        )
        store.write_round(extract.GLOBAL_EDIT_FAILURE, pending)
        assert orchestrator_state.stage2_reviewed_edit_commit_recovery_needed(
            root, '002f', code=ctx.code)
        pending_ov = orchestrator_state.overview(root, '002f')
        assert not orchestrator_state.stage2_reviewed_edit_correction_needed(
            root, '002f', pending_ov)
        store.write_round(extract.GLOBAL_EDIT_FAILURE, terminal_failure)

        fixed_attestation = copy.deepcopy(attestation)
        fixed_attestation['decisions'] = [{
            'item_id': target.item_id,
            'expected_subject_sha256': target.subject_sha256,
            'state': 'PASS',
            'reason': '已重新核对该未变化主体，正文证据支持当前描述',
            'evidence': [],
            'generation_disposition': None,
        }]
        _write_edit(book, {
            **business,
            'post_edit_review': fixed_attestation,
        })
        landed, report = _apply(book)

        assert report.passed
        assert landed.canon.secrets[0].first_reveal_chapter == 3
        closed = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        assert next(item for item in closed.items
                    if item.item_id == target.item_id).state == 'PASS'
        assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _sandbox(name: str) -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp())
    book = root / name
    book.mkdir()
    return root, book


def _tmp_book(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """新增恢复测试统一留在 pytest 管理的短根下。"""
    root = tmp_path / 'output'
    book = root / name
    book.mkdir(parents=True)
    return root, book


def test_service_global_edit_is_staged_canonically_and_survives_transport_cleanup():
    """一次性 UUID 只负责运输；正式 provenance 必须绑定可长期复算的书内输入。"""
    root, book = _sandbox('服务修订_321')
    try:
        _seed(book)
        edit = GlobalEdit.model_validate({
            'secrets': [{
                'secret_id': 'secret_01',
                'first_reveal_chapter': 3,
            }],
        })
        service_path = uploads.save_global_edit(root, '321', edit)
        decoded = decode_artifact(
            extract.GLOBAL_EDIT,
            json.loads(service_path.read_bytes()))
        assert GlobalEdit.model_validate(decoded.payload) == edit

        artifact, _report = extract.run_apply_edit(
            _ctx(book, 'stage2.global', paid=False), str(service_path))
        assert artifact.canon.secrets[0].first_reveal_chapter == 3
        assert extract.GLOBAL_EDIT in artifact.provenance.inputs
        assert (book / extract.GLOBAL_EDIT).is_file()

        uploads.discard_edit_plan(root, service_path)
        assert not service_path.exists()
        fresh = ArtifactStore(
            _ctx(book, 'stage2.global', paid=False)).freshness(
                extract.GLOBAL_REL)
        assert fresh.state == 'fresh', fresh
    finally:
        shutil.rmtree(root)


def test_ordinary_hard_human_revalidate_prepared_replays_exact_audit_identity(
        monkeypatch, tmp_path):
    """普通 edit 的人工 INVALID 修订也由 WAL 补尾，且不伪造 reviewed 身份。"""
    root, book = _tmp_book(tmp_path, '普通人工提升_326')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    _write_edit(book, {
        'characters': [{
            'char_id': 'char_001',
            'role': '李宝珠',
            'role_evidence': _role_locator(),
        }],
    })
    original_retire = extract._retire_nonreviewed_global_edit_input_commit
    monkeypatch.setattr(
        extract, '_retire_nonreviewed_global_edit_input_commit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('模拟 HARD terminal 后、WAL 退休前硬杀')))
    with pytest.raises(KeyboardInterrupt, match='WAL 退休前'):
        _apply(book)
    assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()
    monkeypatch.setattr(
        extract, '_retire_nonreviewed_global_edit_input_commit', original_retire)
    interrupted = operator.next_action(root, '326')
    assert interrupted.action is not None
    assert interrupted.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_COMMIT
    execution_permit.permit_from_next(
        interrupted, 'stage2.global', interrupted.action.flags)
    with pytest.raises(GateBlocked, match='HARD terminal'):
        _apply(book)
    assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    terminal = store.read_own(
        GlobalEditFailure, extract.GLOBAL_EDIT_FAILURE)[0]
    assert terminal.failure_kind == 'ordinary_hard_gate'
    assert orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '326', code=ctx.code)

    invalid_path = book / extract._GLOBAL_INVALID
    raw = json.loads(invalid_path.read_text(encoding='utf-8'))
    raw['entities']['characters']['char_001']['role'] = '沈家账房，负责核对往来账目'
    invalid_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    assert store.revalidation_audit(
        extract.GLOBAL_REL, GlobalGrounding,
        actor='human:reviewer', reason='修正人物身份').edited_by_human
    assert orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '326', code=ctx.code)

    original = extract._reconcile_reviewed_edit_commit
    monkeypatch.setattr(
        extract, '_reconcile_reviewed_edit_commit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('模拟 revalidate prepared 后硬杀')))
    with pytest.raises(KeyboardInterrupt, match='prepared 后'):
        extract.run_revalidate(
            ctx, actor='human:reviewer', reason='修正人物身份')

    journal = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert journal.state == 'prepared'
    assert journal.action_kind == 'revalidate'
    assert journal.reviewed_edit is False
    assert journal.expected_source_sha256 is None
    assert journal.global_review_workpack_sha256 is None
    assert journal.baseline.failure_sha256 is not None

    current = operator.next_action(root, '326')
    assert current.state == 'blocked' and current.action is not None
    assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_REVALIDATE
    assert current.action.flags == [
        '--revalidate', '--actor', 'human:reviewer',
        '--reason', '修正人物身份',
    ]
    permit = execution_permit.permit_from_next(
        current, 'stage2.global', current.action.flags)
    assert permit.flags == current.action.flags
    with pytest.raises(UnsupportedRunMode, match='exact actor/reason'):
        execution_permit.permit_from_next(
            current, 'stage2.global', [
                '--revalidate', '--actor', 'system:wrong',
                '--reason', 'wrong',
            ])

    monkeypatch.setattr(
        extract, '_reconcile_reviewed_edit_commit', original)
    promoted, report = extract.run_revalidate(
        ctx, actor='human:reviewer', reason='修正人物身份')
    assert promoted and report.passed
    assert not invalid_path.exists()
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    committed = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert committed.state == 'committed'
    assert committed.target.global_grounding.provenance.revalidation_audit.actor == (
        'human:reviewer')


@pytest.mark.parametrize('completed_companions', [0, 1, 2, 3])
def test_generic_global_revalidate_prepared_recovers_every_hard_kill_write_point(
        completed_companions, monkeypatch, tmp_path):
    """无 GlobalEdit/formal 身份的首次 Global INVALID 也由同一 WAL 补尾。"""
    sid = f'3268{completed_companions}'
    root, book = _tmp_book(tmp_path, f'首次全局提升_{sid}')
    ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    response = _response_global()
    original_name = response['entities']['characters']['char_002']['canonical']
    response['entities']['characters']['char_002']['canonical'] = (
        response['entities']['characters']['char_001']['canonical'])
    client, _transport = _client(ctx, lambda _req, _index: _ok(response))
    with pytest.raises(GateBlocked, match='canonical_unique'):
        extract.run_global(ctx, client)

    invalid_path = book / extract._GLOBAL_INVALID
    raw = json.loads(invalid_path.read_text(encoding='utf-8'))
    raw['entities']['characters']['char_002']['canonical'] = original_name
    invalid_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    assert not (book / extract.GLOBAL_REL).exists()
    assert not (book / extract.GLOBAL_EDIT).exists()
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()

    original_reconcile = extract._reconcile_reviewed_edit_commit
    monkeypatch.setattr(
        extract, '_reconcile_reviewed_edit_commit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('模拟 generic revalidate prepared 后硬杀')))
    with pytest.raises(KeyboardInterrupt, match='prepared 后'):
        extract.run_revalidate(
            _ctx(book, 'stage2.global', paid=False),
            actor='human:reviewer', reason='修正首次抽取的重复姓名')

    journal = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert journal.state == 'prepared'
    assert journal.action_kind == 'revalidate'
    assert journal.reviewed_edit is False
    assert journal.failed_edit_sha256 is None
    assert journal.formal_sha256 is None
    assert journal.baseline.global_sha256 is None
    assert journal.baseline.failure_sha256 is None

    monkeypatch.setattr(
        extract, '_reconcile_reviewed_edit_commit', original_reconcile)
    targets = [
        (extract.GLOBAL_REL, journal.target.global_grounding),
        (extract.GLOBAL_REVIEW_REL, journal.target.global_review),
        (BLUEPRINT_EDGE, journal.target.blueprint_character_projection),
    ]
    for rel, payload in targets[:completed_companions]:
        store.write_round(rel, payload)

    current = operator.next_action(root, sid)
    assert current.state == 'blocked' and current.action is not None
    assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_REVALIDATE
    assert current.action.flags == [
        '--revalidate', '--actor', 'human:reviewer',
        '--reason', '修正首次抽取的重复姓名',
    ]
    permit = execution_permit.permit_from_next(
        current, 'stage2.global', current.action.flags)
    assert permit.flags == current.action.flags

    promoted, report = extract.run_revalidate(
        _ctx(book, 'stage2.global', paid=False),
        actor='human:reviewer', reason='修正首次抽取的重复姓名')
    assert promoted and report.passed
    assert not invalid_path.exists()
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    committed = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert committed.state == 'committed'


def test_ordinary_candidate_terminal_closes_input_wal_after_hard_kill(
        monkeypatch, tmp_path):
    """候选 schema 终态先落 sidecar；重启只关 WAL，不在新底座重施 edit。"""
    root, book = _tmp_book(tmp_path, '普通候选终态_327')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    _write_edit(book, {
        'secrets': [{
            'secret_id': 'secret_01', 'first_reveal_chapter': 3,
        }],
    })
    original_validate = GlobalGrounding.model_validate

    def reject_only_applied_candidate(cls, value, *args, **kwargs):
        provenance = value.get('_provenance') if isinstance(value, dict) else None
        inputs = getattr(provenance, 'inputs', {})
        if extract.GLOBAL_EDIT in inputs:
            broken = copy.deepcopy(value)
            broken.pop('entities', None)
            return original_validate(broken, *args, **kwargs)
        return original_validate(value, *args, **kwargs)

    original_retire = extract._retire_nonreviewed_global_edit_input_commit
    monkeypatch.setattr(
        GlobalGrounding, 'model_validate', classmethod(reject_only_applied_candidate))
    monkeypatch.setattr(
        extract, '_retire_nonreviewed_global_edit_input_commit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('模拟 candidate terminal 后、WAL 退休前硬杀')))
    with pytest.raises(KeyboardInterrupt, match='WAL 退休前'):
        _apply(book)

    store = ArtifactStore(ctx)
    failure = store.read_own(
        GlobalEditFailure, extract.GLOBAL_EDIT_FAILURE)[0]
    assert failure.failure_kind == 'ordinary_candidate_validation'
    assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()
    current = operator.next_action(root, '327')
    assert current.action is not None
    assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_COMMIT

    monkeypatch.setattr(GlobalGrounding, 'model_validate', original_validate)
    monkeypatch.setattr(
        extract, '_retire_nonreviewed_global_edit_input_commit', original_retire)
    with pytest.raises(TerminalState, match='候选不符合'):
        _apply(book)
    assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    terminal = operator.next_action(root, '327')
    assert terminal.action is not None
    assert terminal.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_TERMINAL
    assert terminal.action.flags == []


def test_new_ordinary_edit_explicitly_supersedes_old_terminal_failure(tmp_path):
    """旧 failure 不得抢 planner；新运输 edit canonical 化时明确归档它。"""
    root, book = _tmp_book(tmp_path, '普通新修订_328')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    _write_edit(book, {
        'characters': [{
            'char_id': 'char_001', 'role': '李宝珠',
            'role_evidence': _role_locator(),
        }],
    })
    with pytest.raises(GateBlocked):
        _apply(book)
    assert (book / extract.GLOBAL_EDIT_FAILURE).is_file()

    replacement = GlobalEdit.model_validate({
        'characters': [{
            'char_id': 'char_001',
            'role': '沈家账房，负责核对往来账目',
            'role_evidence': _role_locator(),
        }],
    })
    service_path = uploads.save_global_edit(root, '328', replacement)
    terminal = operator.next_action(root, '328')
    assert terminal.action is not None
    assert terminal.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_TERMINAL
    execution_permit.permit_from_next(
        terminal, 'stage2.global', ['--apply-edit', str(service_path)])

    landed, report = extract.run_apply_edit(ctx, str(service_path))
    assert report.passed
    assert landed.entities.characters['char_001'].role.startswith('沈家账房')
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    assert not orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '328', code=ctx.code)


def test_a_noop_service_global_edit_is_rejected_before_canonical_staging():
    root, book = _sandbox('空修订_322')
    try:
        _seed(book)
        edit = GlobalEdit.model_validate({
            'secrets': [{
                'secret_id': 'secret_01',
                'first_reveal_chapter': 2,
            }],
        })
        service_path = uploads.save_global_edit(root, '322', edit)

        with pytest.raises(TerminalState, match='没有改变任何业务字节'):
            extract.run_apply_edit(
                _ctx(book, 'stage2.global', paid=False), str(service_path))
        assert not (book / extract.GLOBAL_EDIT).exists()
    finally:
        shutil.rmtree(root)


def test_wal_only_first_service_edit_is_reachable_via_canonical_recovery(
        monkeypatch, tmp_path):
    """WAL 已落、canonical 尚缺时，operator/permit/producer 仍能闭合。"""
    root, book = _tmp_book(tmp_path, '首次WAL恢复_323')
    try:
        _seed(book)
        edit = GlobalEdit.model_validate({
            'secrets': [{
                'secret_id': 'secret_01',
                'first_reveal_chapter': 3,
            }],
        })
        service_path = uploads.save_global_edit(root, '323', edit)
        original = extract._reconcile_global_edit_input_commit

        def hard_kill_before_canonical(_store, _journal):
            raise KeyboardInterrupt('模拟 WAL 首写后硬杀')

        monkeypatch.setattr(
            extract, '_reconcile_global_edit_input_commit',
            hard_kill_before_canonical)
        with pytest.raises(KeyboardInterrupt, match='WAL 首写后'):
            extract.run_apply_edit(
                _ctx(book, 'stage2.global', paid=False), str(service_path))
        assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()
        assert not (book / extract.GLOBAL_EDIT).exists()

        monkeypatch.setattr(
            extract, '_reconcile_global_edit_input_commit', original)
        current = operator.next_action(root, '323')
        assert current.state == 'blocked'
        assert current.action is not None
        assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_COMMIT
        assert current.action.flags == ['--apply-edit', extract.GLOBAL_EDIT]
        permit = execution_permit.permit_from_next(
            current, 'stage2.global', tuple(current.action.flags))
        assert permit.external_inputs == []

        artifact, report = _apply(book)
        assert report.passed
        assert artifact.canon.secrets[0].first_reveal_chapter == 3
        assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    finally:
        shutil.rmtree(root)


def test_new_wal_is_not_swallowed_by_previous_committed_formal_journal(
        monkeypatch, tmp_path):
    """J0/E0 是新 WAL 的 baseline，不得被误认成 WAL1 已成功。"""
    root, book = _tmp_book(tmp_path, '旧提交新WAL_324')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01', 'first_reveal_chapter': 3,
        }]})
        first, _report = _apply(book)
        assert first.canon.secrets[0].first_reveal_chapter == 3
        old_canonical = (book / extract.GLOBAL_EDIT).read_bytes()
        old_commit = (book / extract.GLOBAL_EDIT_COMMIT).read_bytes()

        edit = GlobalEdit.model_validate({
            'secrets': [{
                'secret_id': 'secret_01', 'first_reveal_chapter': 2,
            }],
        })
        service_path = uploads.save_global_edit(root, '324', edit)
        original = extract._reconcile_global_edit_input_commit
        monkeypatch.setattr(
            extract, '_reconcile_global_edit_input_commit',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt('模拟 WAL1 后硬杀')))
        with pytest.raises(KeyboardInterrupt, match='WAL1'):
            extract.run_apply_edit(
                _ctx(book, 'stage2.global', paid=False), str(service_path))
        assert (book / extract.GLOBAL_EDIT).read_bytes() == old_canonical
        assert (book / extract.GLOBAL_EDIT_COMMIT).read_bytes() == old_commit

        monkeypatch.setattr(
            extract, '_reconcile_global_edit_input_commit', original)
        landed, report = _apply(book)
        assert report.passed
        assert landed.canon.secrets[0].first_reveal_chapter == 2
        assert (book / extract.GLOBAL_EDIT).read_bytes() != old_canonical
        assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    finally:
        shutil.rmtree(root)


@pytest.mark.parametrize('completed_companions', [0, 1, 2, 3])
def test_reviewed_prepared_bundle_recovers_every_hard_kill_write_point(
        completed_companions, monkeypatch, tmp_path):
    """prepared 后 0/1/2/3 个正式 companion 已写都走同一 operator 恢复。"""
    sid = f'325{completed_companions}'
    root, book = _tmp_book(tmp_path, f'reviewed提交断点_{sid}')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        grounding = store.read(GlobalGrounding, extract.GLOBAL_REL)
        review = approved_global_review(grounding)
        target_item = next(
            item for item in review.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        review = decide(
            review,
            item_id=target_item.item_id,
            expected_subject_sha256=target_item.subject_sha256,
            state='FAIL', actor='test-reviewer',
            reason='人物身份需要精确修订',
            now='2026-09-05T00:00:00+00:00')
        store.write(extract.GLOBAL_REVIEW_REL, review)
        workpack = global_review_ops.review_workpack(
            ctx, scope='blocking', replaced_input_keys=(extract.GLOBAL_EDIT,))
        _write_edit(book, {
            'characters': [{
                'char_id': 'char_001',
                'role': '沈家账房，负责核对往来账目',
                'role_evidence': _role_locator(),
            }],
            'post_edit_review': {
                'actor': 'agent:novel-pipeline-operator',
                'expected_source_sha256': review.source_sha256,
                'reviewed_chapter_ordinals': [1, 2, 3],
                'default_state': 'PASS',
                'reason': '已复核精确修改后的完整 blocking 主体',
                'quarantine_groups': [],
                'expected_workpack_sha256': workpack['workpack_sha256'],
                'review_scope': [{
                    'item_id': target_item.item_id,
                    'expected_subject_sha256': target_item.subject_sha256,
                }],
                'decisions': [],
            },
        })
        original = extract._reconcile_reviewed_edit_commit
        monkeypatch.setattr(
            extract, '_reconcile_reviewed_edit_commit',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt('模拟 prepared 后硬杀')))
        with pytest.raises(KeyboardInterrupt, match='prepared 后'):
            _apply(book)
        journal = ArtifactStore(ctx).read_own(
            GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
        assert journal.state == 'prepared'
        assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()

        monkeypatch.setattr(
            extract, '_reconcile_reviewed_edit_commit', original)
        replay_store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        targets = [
            (extract.GLOBAL_REL, journal.target.global_grounding),
            (extract.GLOBAL_REVIEW_REL, journal.target.global_review),
            (BLUEPRINT_EDGE, journal.target.blueprint_character_projection),
        ]
        for rel, payload in targets[:completed_companions]:
            replay_store.write_round(rel, payload)

        current = operator.next_action(root, sid)
        assert current.state == 'blocked'
        assert current.action is not None
        assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_COMMIT
        permit = execution_permit.permit_from_next(
            current, 'stage2.global', tuple(current.action.flags))
        assert permit.flags == ['--apply-edit', extract.GLOBAL_EDIT]

        landed, report = _apply(book)
        assert report.passed
        assert landed.entities.characters['char_001'].role.startswith('沈家账房')
        assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
        assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
        committed = ArtifactStore(ctx).read_own(
            GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
        assert committed.state == 'committed'
    finally:
        shutil.rmtree(root)


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 主样本：错误揭示章 → 人工修正 → 重判 → 下游 stale
# ─────────────────────────────────────────────────────────────────────────────

def test_a_wrong_reveal_chapter_is_corrected_and_the_downstream_turns_stale():
    """⭐⭐ TODO-05 的验收样本，四段一次跑完。

    合成底座里 `secret_01` 的读者揭示章是第 2 章。假设那是抽错的（真相其实在第 3 章
    才点破）⇒ 人写一份决策集把它改到 3，跑 `--apply-edit`：

      ① **改真的落进产物**（不是只改了报告）；
      ② **重走归一**：埋点章 `[1]` 仍严格早于新揭示章 ⇒ 留着；
      ③ **重判**：闸门跑过、报告出得来；
      ④ **下游按各自消费什么分开判**：逐章蓝图只绑定物化称名投影；
         secret 编辑不改变投影字节——blueprint 从 global 只取已现称名表，
         改 secret 不该让全链最贵的付费节点重跑一遍。
         ⚠ 这一段 2026-08-23 改过判向。此前它断言投影也失配，理由写着
         「逐章剧透闸门正是消费它的那一个」——**那句话是错的**：剧透闸门在
         `stage4.context`，blueprint 不消费 secrets。是过宽的整份投影
         （`artifact_payload`）让那句错理由一直绿着。
         ⚠ 「改 secrets 会让谁 stale」并没有因此没人查：真正消费它的那一层由
         `tests/test_store.py::test_stage4_grounding_edits_invalidate_real_store_freshness`
         的 `known_by` 那一格钉着。**底座改了而下游还按旧的算**仍是全链最贵的
         一类错，只是「下游」要指对层。
    """
    root, book = _sandbox('揭示章订正_001')
    try:
        _seed(book, with_blueprints=True)
        bp_ctx = _ctx(book, 'stage2.blueprint', paid=False)
        assert ArtifactStore(bp_ctx).freshness(extract.BLUEPRINT_REL).state == 'fresh', \
            '底座还没动，下游就已经 stale？那这条测试后面什么也证明不了'

        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'first_reveal_chapter': 3}]})
        artifact, report = _apply(book)

        # ① 改真的落进产物
        secret = artifact.canon.secrets[0]
        assert secret.first_reveal_chapter == 3
        reloaded = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(
            GlobalGrounding, extract.GLOBAL_REL)
        assert reloaded.canon.secrets[0].first_reveal_chapter == 3

        # ② 重走归一：埋点章 1 < 新揭示章 3 ⇒ 不该被裁掉
        assert secret.setup.chapters == [1]
        assert reloaded.provenance.setup_chapters_pruned == []

        # ③ 重判真的跑过（不是把旧报告抄一遍）
        assert report.node == 'stage2.global' and report.rules
        assert {r.rule for r in report.rules if r.status == 'skipped'} == set(), \
            '有判据没排上——那一行会绿着而什么都没查'

        # ④ Blueprint 不伪记 global 边；非称名修订保持投影字节与消费指纹不变。
        fresh = ArtifactStore(bp_ctx).freshness(extract.BLUEPRINT_REL)
        edge = next(e for e in fresh.edges if e.key == BLUEPRINT_EDGE)
        assert edge.recorded_file == edge.now_file, \
            '非称名编辑不应重写投影字节，否则仍会制造一次付费 Blueprint stale'
        assert edge.recorded_consumed == edge.now_consumed, \
            'blueprint 只取已现称名投影 ⇒ 改 secret 不该让它重跑'
        assert (fresh.state, edge.state) == ('fresh', 'fresh'), \
            'consumed 对得上就该是 fresh——判定顺序是 consumed 优先于 file，不是反过来'
        assert extract.GLOBAL_REL not in {e.key for e in fresh.edges}, \
            'Blueprint 来源账不得伪造一条没有读取的 global 输入边'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_correcting_the_reveal_chapter_backwards_reprunes_and_then_hits_the_same_gate():
    """⭐ 人把揭示章往**前**改时，走的是与生成路**一模一样**的那三步。

    埋点章 `[1]` 在揭示章改到第 1 章之后不再严格早于它（`R-2.9`）⇒
      ① `_prune_setups` 把它裁掉，并把裁剪账记进 `_provenance.setup_chapters_pruned`
         （「冷揭示」与「被裁空」在字段值上完全同形，只能靠那个计数分开）；
      ② `secret_setup_order`（HARD）**断言的正是那个计数**⇒ 命中 ⇒ 问题稿进 `.INVALID`，
         正式底座不动。⚠ **这不是人工路的特例**：`run_global` 在同一份数据上
         也是这个下场（见 `test_stage2_global_renormalize_promotes_deterministically_pruned_invalid`）
         ——「裁过一刀」这件事必须让人看见，不许静默应用；
      ③ 0 成本出路是 `--renormalize`：再归一时已经没什么可裁，计数归零、提升为正式稿。

    ⚠ 本条是「人工稿要重走 normalize、当前判据和落盘闸门」这句话的完整形态：
    只测到 ① 会让人以为人工路可以绕过 ②。
    """
    root, book = _sandbox('揭示章前移_002')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'first_reveal_chapter': 1}]})

        with pytest.raises(GateBlocked, match='secret_setup_order'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before, '正式底座被覆盖了'

        invalid = json.loads((book / extract._GLOBAL_INVALID).read_text(encoding='utf-8'))
        assert invalid['canon']['secrets'][0]['setup'] == {'kind': 'cold'}
        assert invalid['_provenance']['setup_chapters_pruned'] == [['secret_01', [1], []]]

        report = extract.run_renormalize(_ctx(book, 'stage2.global', paid=False))
        assert report.passed
        stored = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(
            GlobalGrounding, extract.GLOBAL_REL)
        assert stored.canon.secrets[0].first_reveal_chapter == 1
        assert stored.canon.secrets[0].setup.kind == 'cold'
        assert stored.provenance.setup_chapters_pruned == []
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fact_and_typed_knowledge_timeline_land_together():
    """secret 事实与 CharacterRef/GroupRef 知情时间线同样能走 typed 写回。"""
    root, book = _sandbox('知情人订正_003')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01',
            'fact': '父亲早已回城，只是被债主扣在城南客栈里',
            'fact_evidence': _fact_locator(),
                'knowledge_events': [
                    _knowledge_character('char_001', 1),
                    _knowledge_group('知情的沈家人', 2),
                ],
        }]})
        artifact, _report = _apply(book)
        secret = artifact.canon.secrets[0]
        assert secret.fact.endswith('客栈里')
        assert [event.subject.kind for event in secret.knowledge_events] == [
            'character', 'group']
        assert [event.known_from_chapter for event in secret.knowledge_events] == [1, 2]
        # 没给的字段一个都不许动
        assert secret.first_reveal_chapter == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_reviewed_role_can_be_corrected_without_an_llm_and_reopens_only_its_review():
    """批 1 的 role 复核必须接得上批 2 的 typed 写回面。

    人先对旧 role 作出 PASS 决策，再用 ``GlobalEdit.characters[].role`` 修正它；
    写回要走正式落盘口，而旧 subject hash 上的决定不得跟到新事实上。
    """
    root, book = _sandbox('人物身份订正_003a')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        old_item = next(
            item for item in review.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        stable_item = next(
            item for item in review.items
            if item.kind == 'role'
            and item.subject_path != old_item.subject_path)
        review = decide(
            review,
            item_id=old_item.item_id,
            expected_subject_sha256=old_item.subject_sha256,
            state='PASS',
            actor='test-reviewer',
            reason='旧身份已按原文复核',
            now='2026-08-24T00:00:00+00:00',
        )
        review = decide(
            review,
            item_id=stable_item.item_id,
            expected_subject_sha256=stable_item.subject_sha256,
            state='PASS',
            actor='test-reviewer',
            reason='另一个未改身份已按原文复核',
            now='2026-08-24T00:00:01+00:00',
        )
        store.write(extract.GLOBAL_REVIEW_REL, review)

        corrected = '沈家账房，负责核对往来账目'
        _write_edit(book, {'characters': [{
            'char_id': 'char_001',
            'role': corrected,
            'role_evidence': _role_locator(),
        }]})
        artifact, report = _apply(book)

        assert report.node == 'stage2.global' and report.passed
        assert artifact.entities.characters['char_001'].role == corrected
        reloaded = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
        assert reloaded.entities.characters['char_001'].role == corrected
        assert reloaded.provenance.partial_rewrite == '--apply-edit'

        rebuilt = ArtifactStore(ctx).read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        new_item = next(
            item for item in rebuilt.items
            if item.kind == 'role'
            and item.subject_path == 'entities.characters.char_001.role')
        assert new_item.subject_sha256 != old_item.subject_sha256
        assert new_item.item_id != old_item.item_id
        assert new_item.state == 'pending'
        kept_item = next(
            item for item in rebuilt.items
            if item.subject_path == stable_item.subject_path)
        assert kept_item.item_id == stable_item.item_id
        assert kept_item.subject_sha256 == stable_item.subject_sha256
        assert kept_item.state == 'PASS'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_leaky_role_invalid_is_repaired_by_the_typed_zero_llm_path():
    """role/evidence HARD 先留可读问题稿，再从 `.INVALID` 走 typed 恢复。

    第一次人工稿既含结局词，locator 也零命中；归一必须把证据收成 `source=null`，
    仍能通过 GlobalGrounding 载体落 `.INVALID`。第二次同交 role + 正确 locator，
    从 live INVALID 继续修，0 LLM 回取正式 SourceSpan；旧正式稿与问题稿都归档。
    """
    root, book = _sandbox('身份泄漏恢复_003aa')
    try:
        _seed(book)
        formal_path = book / extract.GLOBAL_REL
        invalid_path = book / extract._GLOBAL_INVALID
        formal_before = formal_path.read_bytes()

        _write_edit(book, {'characters': [{
            'char_id': 'char_001',
            'role': '沈家账房，最终下狱',
            'role_evidence': _locator(3, '不存在的身份原文'),
        }]})
        blocked_ctx = _ctx(book, 'stage2.global', paid=False)
        with pytest.raises(GateBlocked, match='role_leaks_fate') as blocked:
            extract.run_apply_edit(blocked_ctx)
        assert 'global_evidence_unlocated' in str(blocked.value)

        assert blocked_ctx.ledger.usage() is None, '0 LLM 人工路不该产生调用账'
        assert formal_path.read_bytes() == formal_before, '问题稿覆盖了正式底座'
        invalid_before = invalid_path.read_bytes()
        invalid = json.loads(invalid_before)
        assert invalid['entities']['characters']['char_001']['role'].endswith('最终下狱')
        evidence = invalid['entities']['characters']['char_001']['role_evidence']
        assert evidence['source'] is None
        failures = invalid['_provenance']['evidence_locator_failures']
        assert failures and failures[0][0] == 'entities.characters.char_001.role'
        decoded = decode_artifact(extract._GLOBAL_INVALID, invalid)
        assert GlobalGrounding.model_validate(decoded.payload), \
            'source=null 必须仍是可读的失败态'

        corrected = '沈家账房，负责核对往来账目'
        _write_edit(book, {'characters': [{
            'char_id': 'char_001',
            'role': corrected,
            'role_evidence': _role_locator(),
        }]})

        # 只豁免「正被替换的决策集」那条 stale 边；正文变化仍须关闭式拒绝。
        main_path = book / extract.MAIN_TEXT
        main_before = main_path.read_bytes()
        changed_main = json.loads(main_before)
        changed_main['payload'][0]['text'] += '正文发生变化。'
        main_path.write_text(
            json.dumps(changed_main, ensure_ascii=False, indent=2), encoding='utf-8')
        with pytest.raises(UpstreamStale, match='stage1/main_text.json'):
            extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))
        assert invalid_path.read_bytes() == invalid_before
        assert formal_path.read_bytes() == formal_before
        main_path.write_bytes(main_before)

        repair_ctx = _ctx(book, 'stage2.global', paid=False)
        artifact, report = extract.run_apply_edit(repair_ctx)

        assert repair_ctx.ledger.usage() is None, '恢复路不该调用模型或命中模型缓存'
        assert report.passed
        assert artifact.entities.characters['char_001'].role == corrected
        assert artifact.entities.characters['char_001'].role_evidence.source is not None
        assert artifact.provenance.evidence_locator_failures == []
        assert not invalid_path.exists(), '通过后的 live INVALID 没有退役'
        rounds = book / 'stage2' / '_rounds'
        assert any(path.read_bytes() == invalid_before for path in rounds.glob(
            'global_grounding.INVALID.r*.json')), '原问题稿没有进入轮次归档'
        assert any(path.read_bytes() == formal_before for path in rounds.glob(
            'global_grounding.r*.json')), '原正式底座没有进入轮次归档'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_typed_edit_uses_cross_chapter_unique_recovery_and_hash_only_audit():
    root, book = _sandbox('人工证据错章恢复_003ab')
    try:
        _seed(book)
        locator = '李宝珠把账册摊在桌上一页页翻。'
        _write_edit(book, {'characters': [{
            'char_id': 'char_001',
            'role': '沈家账房，负责核对往来账目',
            'role_evidence': _locator(1, locator),
        }]})
        ctx = _ctx(book, 'stage2.global', paid=False)

        artifact, report = extract.run_apply_edit(ctx)

        assert report.passed
        evidence = artifact.entities.characters['char_001'].role_evidence
        assert evidence.chapter_ordinal == 3 and evidence.source is not None
        event, = [row for row in ctx.ledger.audit_events
                  if row['kind'] == 'evidence_locator_rewrite']
        assert event['claim_path'] == 'entities.characters.char_001.role'
        assert (event['original_chapter_ordinal'],
                event['resolved_chapter_ordinal']) == (1, 3)
        assert event['artifact'] == extract.GLOBAL_REL
        assert event['artifact_sha256'] == ArtifactStore(
            ctx).full_sha256_or_none(extract.GLOBAL_REL)
        assert locator not in json.dumps(event, ensure_ascii=False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_complete_typed_surfaces_reach_disk_and_rebuild_the_human_review():
    """A compound secret can be split without bypassing the real consumer.

    ``Stage4`` exposes a secret's whole fact after the earliest reveal event;
    adding a second event to one compound fact therefore cannot express two
    independently revealed facts.  The complete-set edit splits that row,
    also replaces an omitted world-rule set and a character's complete address
    surface, then ``run_apply_edit`` rebuilds ``global_review`` from the landed
    artifact.  Old decisions bind to the old hashes and must not follow it.
    """
    root, book = _sandbox('集合订正_003b')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        old_review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        old_secret_rows = [
            item for item in old_review.items
            if item.kind in {'secret_set_complete', 'secret', 'known_by', 'reveal'}]
        assert old_secret_rows
        for item in old_secret_rows:
            old_review = decide(
                old_review,
                item_id=item.item_id,
                expected_subject_sha256=item.subject_sha256,
                state='PASS',
                actor='test-reviewer',
                reason='synthetic source evidence checked before replacement',
                now='2026-08-17T00:00:00+00:00',
            )
        store.write(extract.GLOBAL_REVIEW_REL, old_review)
        old_item_ids = {item.item_id for item in old_secret_rows}

        _write_edit(book, {
            'secrets_replace': [{
                'secret_id': 'secret_01',
                'fact': '父亲已经回城',
                'fact_evidence': _fact_locator(),
                'knowledge_events': [_knowledge_character('char_002', 2)],
                'setup': {'kind': 'foreshadowed', 'chapters': [1]},
                'first_reveal_chapter': 3,
            }, {
                'secret_id': 'secret_02',
                'fact': '母亲早就知道那封信的事',
                'fact_evidence': _locator(2, '其实母亲早就知道那封信的事。'),
                'knowledge_events': [_knowledge_character('char_001', 1)],
                'setup': {'kind': 'cold'},
                'first_reveal_chapter': 2,
            }],
            'world_rules_replace': [{
                'rule_id': 'wr_02',
                'kind': 'legal_rule',
                'versions': [{
                    'effective_from_chapter': 1,
                    'desc': '城中夜间梆子响过以后实行宵禁，行人须留在住所直到天明',
                    'desc_evidence': _locator(1, '远处传来更夫的梆子声。'),
                }],
            }],
            'addresses_replace': [{
                'char_id': 'char_002',
                'address': [{
                    'surface': '母亲', 'by': 'public', 'context': 'address',
                    'from_chapter': 2,
                }, {
                    'surface': '母亲', 'by': 'char_001', 'context': 'reference',
                    'from_chapter': 2,
                }],
            }],
        })
        artifact, report = _apply(book)

        assert report.node == 'stage2.global'
        assert [row.secret_id for row in artifact.canon.secrets] == [
            'secret_01', 'secret_02']
        assert [row.rule_id for row in artifact.canon.world_rules] == ['wr_02']
        assert [(row.surface, row.by, row.context, row.from_chapter)
                for row in artifact.entities.characters['char_002'].address] == [
            ('母亲', 'public', 'address', 2),
            ('母亲', 'char_001', 'reference', 2),
        ]

        rebuilt = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(
            GlobalReview, extract.GLOBAL_REVIEW_REL)
        secret_rows = [
            item for item in rebuilt.items
            if item.kind in {'secret_set_complete', 'secret', 'knowledge_event', 'reveal'}]
        assert len(secret_rows) == 7  # set completeness + 2 × fact/knowledge-event/reveal
        assert all(item.state == 'pending' for item in secret_rows)
        assert old_item_ids.isdisjoint({item.item_id for item in secret_rows})
        assert {item.subject_path for item in secret_rows if item.kind == 'secret'} == {
            'canon.secrets.secret_01.fact', 'canon.secrets.secret_02.fact'}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_conflict_partition_replace_uses_the_full_apply_edit_commit_path():
    """整组重画必须刷新审阅 hash、归档旧轮并保留人工来源账，不能旁写 Global。"""
    root, book = _sandbox('冲突阶段重画_003c')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        old_review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        old_item = next(
            item for item in old_review.items
            if item.kind == 'conflict_partition')
        old_review = decide(
            old_review,
            item_id=old_item.item_id,
            expected_subject_sha256=old_item.subject_sha256,
            state='PASS',
            actor='test-reviewer',
            reason='synthetic old partition reviewed before replacement',
            now='2026-08-29T00:00:00+00:00',
        )
        store.write(extract.GLOBAL_REVIEW_REL, old_review)
        formal_before = (book / extract.GLOBAL_REL).read_bytes()

        _write_edit(book, {'conflict_stages_replace': [
            {'stage': '疑点累积', 'chapters': [1]},
            {'stage': '关系对质', 'chapters': [2, 3]},
        ]})
        artifact, report = _apply(book)

        assert report.passed
        assert [row.stage for row in artifact.arc.conflict_stages] == [
            '疑点累积', '关系对质']
        assert artifact.provenance.partial_rewrite == '--apply-edit'
        assert extract.GLOBAL_EDIT in artifact.provenance.inputs
        reloaded = store.read(GlobalGrounding, extract.GLOBAL_REL)
        assert reloaded.arc.conflict_stages == artifact.arc.conflict_stages

        new_review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        new_item = next(
            item for item in new_review.items
            if item.kind == 'conflict_partition')
        assert new_item.subject_sha256 != old_item.subject_sha256
        assert new_item.state == 'pending'
        assert new_item.actor is None and new_item.reason is None
        assert (book / 'stage2' / '_rounds' /
                'global_grounding.r1.json').read_bytes() == formal_before
        assert (book / 'stage2' / '_rounds' / 'global_review.r1.json').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('replacement,detail', [
    ([{'stage': '缺章', 'chapters': [1, 3]}], '缺章'),
    ([{'stage': '重复甲', 'chapters': [1, 2]},
      {'stage': '重复乙', 'chapters': [2, 3]}], '重复'),
    ([{'stage': '越界', 'chapters': [1, 2, 3, 4]}], '越界'),
])
def test_bad_conflict_partition_replacement_changes_no_artifact_bytes(
    replacement, detail,
):
    """坏人工分段在内存副本关闭；正式底座不动，也不产生误导性的 INVALID。"""
    root, book = _sandbox(f'冲突阶段{detail}_003d')
    try:
        _seed(book)
        formal_before = (book / extract.GLOBAL_REL).read_bytes()
        review_before = (book / extract.GLOBAL_REVIEW_REL).read_bytes()
        _write_edit(book, {'conflict_stages_replace': replacement})
        bytes_before = {
            path.relative_to(book): path.read_bytes()
            for path in book.rglob('*') if path.is_file()
        }

        with pytest.raises(TerminalState, match=detail):
            _apply(book)

        bytes_after = {
            path.relative_to(book): path.read_bytes()
            for path in book.rglob('*') if path.is_file()
        }
        assert bytes_after == bytes_before
        assert (book / extract.GLOBAL_REL).read_bytes() == formal_before
        assert (book / extract.GLOBAL_REVIEW_REL).read_bytes() == review_before
        assert not (book / 'stage2' / 'global_grounding.INVALID.json').exists()
        assert not (book / 'stage2' / '_rounds').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unrelated_edit_on_bad_conflict_invalid_still_reaches_the_shared_hard_gate():
    """没有 replacement 时不把既有坏 INVALID 误报成“坏决策集”。"""
    root, book = _sandbox('冲突阶段渐进修复_003e')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        formal_before = (book / extract.GLOBAL_REL).read_bytes()
        current = store.read(GlobalGrounding, extract.GLOBAL_REL)
        raw = current.model_dump(by_alias=True)
        raw['arc']['conflict_stages'] = [
            {'stage': '既有问题分段', 'chapters': [1, 3]},
        ]
        store.write_invalid(
            extract.GLOBAL_REL, GlobalGrounding.model_validate(raw),
            gates_skipped=[])
        _write_edit(book, {'characters': [{
            'char_id': 'char_001', 'name_distinctiveness': 'medium',
        }]})

        with pytest.raises(GateBlocked, match='conflict_stage_coverage'):
            _apply(book)

        assert (book / extract.GLOBAL_REL).read_bytes() == formal_before
        invalid = ArtifactStore(
            _ctx(book, 'stage2.global', paid=False)).read(
                GlobalGrounding, 'stage2/global_grounding.INVALID.json')
        assert invalid.entities.characters['char_001'].name_distinctiveness == 'medium'
        assert invalid.arc.conflict_stages[0].chapters == [1, 3]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 二、来源账：这一份此后是混版的，而账要说得出口
# ─────────────────────────────────────────────────────────────────────────────

def test_the_human_round_inherits_the_paid_ledger_and_marks_the_partial_rewrite():
    """⚠ **`prompts` / `model` 一个字不动，`partial_rewrite` 标上。**

    这一份此后是**混版的**：`entities` / `arc` 出自当初那次付费产出，
    被改的 secret 是现在由人给的。三个记法各错一半——沿用旧账说不清「照哪份原文算的」·
    整份覆盖等于宣称本次调过模型（伪造）· 借 `revalidated` 说的是状态变更而内容没动。
    """
    root, book = _sandbox('来源账_004')
    try:
        _seed(book)
        store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        before = store.read(GlobalGrounding, extract.GLOBAL_REL).provenance

        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'fact': '父亲早已回城，只是不肯认这笔账',
             'fact_evidence': _fact_locator()}]})
        artifact, _report = _apply(book)

        after = artifact.provenance
        assert after.prompts == before.prompts, '重盖了 prompt 指纹 —— 那是伪造'
        assert after.model == before.model
        assert after.partial_rewrite == '--apply-edit'
        assert after.gates_skipped is None
        assert after.usage is not None and after.usage.calls_made == before.usage.calls_made, \
            '0 成本路把调用数改了？那本账就不是账了'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_previous_round_is_archived_before_the_human_round_lands():
    """覆盖前有处可回：旧的正式底座进 `_rounds/`。"""
    root, book = _sandbox('轮次归档_005')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01',
             'knowledge_events': [_knowledge_character('char_002', 2)]}]})
        _apply(book)
        archived = sorted(p.name for p in (book / 'stage2' / '_rounds').iterdir())
        assert {
            'global_grounding.r1.json', 'global_review.r1.json',
            'global_edit_input_commit.r1.json',
        }.issubset(archived)
        assert (book / 'stage2' / '_rounds' / 'global_grounding.r1.json').read_bytes() == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_and_review_partial_write_is_resumed_from_prepared_bundle(
        monkeypatch):
    """普通异常也保留 WAL：partial formal 只能按冻结 target 继续收敛。"""
    root, book = _sandbox('组合事务_005b')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01',
            'knowledge_events': [
                _knowledge_character('char_001', 1),
                _knowledge_group('知情的沈家长辈', 2),
            ],
        }]})
        formal_before = (book / extract.GLOBAL_REL).read_bytes()
        review_before = (book / extract.GLOBAL_REVIEW_REL).read_bytes()
        original_write = ArtifactStore.write

        def fail_review(self, rel, artifact):
            if rel == extract.GLOBAL_REVIEW_REL:
                raise OSError('模拟 global review 刷新失败')
            return original_write(self, rel, artifact)

        monkeypatch.setattr(ArtifactStore, 'write', fail_review)
        with pytest.raises(OSError, match='review 刷新失败'):
            _apply(book)

        assert (book / extract.GLOBAL_REL).read_bytes() != formal_before
        assert (book / extract.GLOBAL_REVIEW_REL).read_bytes() == review_before
        assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()
        prepared_raw = json.loads(
            (book / extract.GLOBAL_EDIT_COMMIT).read_bytes())
        assert decode_artifact(
            extract.GLOBAL_EDIT_COMMIT, prepared_raw).payload['state'] == 'prepared'

        monkeypatch.setattr(ArtifactStore, 'write', original_write)
        artifact, report = _apply(book)

        assert report.passed
        assert artifact.canon.secrets[0].knowledge_events[1].subject.kind == 'group'
        assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
        committed_raw = json.loads(
            (book / extract.GLOBAL_EDIT_COMMIT).read_bytes())
        assert decode_artifact(
            extract.GLOBAL_EDIT_COMMIT, committed_raw).payload['state'] == 'committed'
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 三、反例：四条各挡一种会静默发生的错
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unknown_field_is_refused_by_the_carrier():
    """未知字段 ⇒ 载体当场拒收。**拼错一个键不许被静默忽略**——
    那会让「我改过了」与「什么都没改」在盘上同形。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': [
            {'secret_id': 'secret_01', 'reveal_chapter': 3}]})           # 正确的是 first_reveal_chapter
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secret': [{'secret_id': 'secret_01', 'fact': 'x'}]})


def test_an_empty_patch_is_refused():
    """一个字段都没给的补丁 ⇒ 拒收。空补丁什么都不改，
    而它在盘上与「人看过并改了」长得一样。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': [{'secret_id': 'secret_01'}]})
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': []})


def test_two_patches_for_one_secret_are_refused():
    """同一条 secret 给两份补丁 ⇒ 拒收：哪一份生效取决于遍历顺序，
    而那意味着**人的决定被实现细节改写**。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': [
            {'secret_id': 'secret_01', 'fact': 'A', 'fact_evidence': _fact_locator()},
            {'secret_id': 'secret_01', 'fact': 'B', 'fact_evidence': _fact_locator()}]})


@pytest.mark.parametrize('payload,why', [
    ({'secrets': [{'secret_id': 'secret_01', 'first_reveal_chapter': 9}]},
     '揭示章越界'),
    ({'secrets': [{'secret_id': 'secret_01', 'setup': {'kind': 'foreshadowed', 'chapters': [7]}}]},
     '埋点章越界'),
])
def test_an_out_of_range_chapter_is_refused_before_anything_is_written(payload, why):
    """⭐ 越界章号 ⇒ **显式终态，且一个字节都没落盘。**

    ⚠ 它是 `TerminalState` **不是** `GateBlocked`：后者说「这份**产物**过不了闸」
    （问题稿留 `.INVALID`，人接着改），而这里错的是**决策集本身**——
    把一份写着第 9 章的编辑文件落成 `.INVALID`，等于给一个打不开的东西建一份归档。
    ⚠ 越界之所以危险，是因为它**不报错**：越界的揭示章让逐章剧透闸门永不触发，
    而产物里那一格看着一切正常。
    """
    root, book = _sandbox('越界章号_006')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, payload)
        with pytest.raises(TerminalState, match='无效引用/章号'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before, f'{why}：正式底座被动过了'
        assert not (book / extract._GLOBAL_INVALID).exists(), \
            f'{why}：决策集自己错了，不该给它建一份 `.INVALID` 归档'
        assert not (book / 'stage2' / '_rounds').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unknown_secret_id_is_refused_with_the_current_set_spelled_out():
    """指向盘上没有的 secret ⇒ 显式终态，并把现有集合写出来（人才知道该改什么）。"""
    root, book = _sandbox('未知secret_007')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_09', 'fact': '凭空一条',
            'fact_evidence': _fact_locator()}]})
        with pytest.raises(TerminalState, match='secret_01'):
            _apply(book)
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_dangling_known_by_reference_is_rejected_before_any_artifact_write():
    """A typed registry reference is an invalid decision, not a bad candidate.

    Waiting for ``secret_known_by_dangling`` would first mutate/normalize the
    candidate and then create an INVALID round.  Target and reference validity
    are instead checked atomically before any artifact or archive write.
    """
    root, book = _sandbox('闸门拦下_008')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01',
             'knowledge_events': [_knowledge_character('char_777', 1)]}]})
        with pytest.raises(TerminalState, match='char_777'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert not (book / 'stage2' / '_rounds').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_missing_edit_file_and_a_missing_base_are_two_different_terminal_states():
    """两种「没得改」要分开说：**缺决策集**与**缺前稿**指向的下一步完全不同。"""
    root, book = _sandbox('缺件_009')
    try:
        _seed(book)
        with pytest.raises(TerminalState, match='global_edit.json'):
            _apply(book)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    root2, book2 = _sandbox('缺前稿_010')
    try:
        (book2 / 'stage2').mkdir(parents=True)
        ArtifactStore(_ctx(book2, 'stage2.global')).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        _write_edit(book2, {'secrets': [{
            'secret_id': 'secret_01', 'fact': 'x',
            'fact_evidence': _fact_locator()}]})
        with pytest.raises(Exception) as caught:
            _apply(book2)
        assert 'stage2.global' in str(caught.value), \
            '缺前稿时要说「先跑一次 stage2.global」，不是抱怨编辑文件'
    finally:
        shutil.rmtree(root2, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 四、契约登记：这条路要在三张表上同时存在
# ─────────────────────────────────────────────────────────────────────────────

def test_the_entry_is_registered_everywhere_it_has_to_be():
    """⚠ 少登记一张表的后果各不相同，而**没有一个会报错**：
    不在 `NODES[...].flags` ⇒ `build_argv` 当场拒收（这条路敲不出来）·
    不在 `ARTIFACTS` ⇒ 编辑文件的形状没有任何东西在管 ·
    不在 `FREE_ENTRIES` ⇒ 界面把一次 0 成本编辑画成付费动作并弹二次确认。
    """
    from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
    from contracts.schema import ARTIFACTS

    assert '--apply-edit' in NODES['stage2.global'].flags
    assert ARTIFACTS[extract.GLOBAL_EDIT] is GlobalEdit
    assert '--apply-edit' in FREE_ENTRIES and '--apply-edit' in VALUED_FLAGS


def test_the_blueprint_node_has_a_distinct_typed_relation_edit_entry():
    """Blueprint 现役 ``--apply-edit`` 只替换指定章的完整关系集。

    Global 同名 flag 仍只吃 ``GlobalEdit``；两条路由各自的注册载体、
    producer 和上游边分开，不再靠“一个 flag 全仓只能有一个语义”的旧断言。
    """
    from contracts.artifacts import STAGE2_BLUEPRINT_RELATION_EDIT
    from contracts.invariants import NODES
    from contracts.schema import ARTIFACTS
    from contracts.schema.stage2 import BlueprintRelationEdit

    assert {'--emit-edit-template', '--validate-edit', '--apply-edit'} <= set(
        NODES['stage2.blueprint'].flags)
    assert ARTIFACTS[STAGE2_BLUEPRINT_RELATION_EDIT] is BlueprintRelationEdit

    # 同名 flag 没有让 Global producer 窃取 Blueprint 语义。
    root, book = _sandbox('走错节点_011')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01', 'fact': 'x',
            'fact_evidence': _fact_locator()}]})
        with pytest.raises(TerminalState, match='stage2.global'):
            extract.run_apply_edit(_ctx(book, 'stage2.blueprint', paid=False))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_edit_file_is_recorded_as_an_input_edge():
    """决策集**进来源账**：它落在产物目录里、被 `store.read` 读走 ⇒ 那一读要留一条边。

    没有这条边，「这一版底座是照哪份人工决策改的」事后说不出口。
    """
    root, book = _sandbox('来源边_012')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01', 'fact': '改一个字',
            'fact_evidence': _fact_locator()}]})
        artifact, _report = _apply(book)
        assert extract.GLOBAL_EDIT in artifact.provenance.inputs, \
            f'来源账里没有 {extract.GLOBAL_EDIT} 这条边：{sorted(artifact.provenance.inputs)}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stale_blueprints_are_displayable_but_not_consumable_after_base_edit():
    """人工订正后，旧蓝图可供只读诊断，但不得再进生产链或付费请求。

    这里不靠增删展示字段制造假 stale：先让两个 owner 共享正文里的
    「母亲」，但把 ``char_001`` 的该称名登记为第 3 章才生效；旧蓝图
    生成后，只把这个歧义称名的 ``from_chapter`` 提前到第 2 章。
    它会真正改变第 2 章下发的 ``char_id -> surface`` 候选，因此旧蓝图
    必须 stale；若仅改 ``by/context`` 或唯一称名的无效时点，则应保持 fresh。
    """
    root, book = _sandbox('下游可读_013')
    try:
        _seed(book)
        initial_addresses = [{
            'char_id': 'char_001',
            'address': [{
                'surface': '李宝珠', 'by': 'narration', 'context': 'reference',
                'from_chapter': 1,
            }, {
                'surface': '母亲', 'by': 'public', 'context': 'reference',
                'from_chapter': 3,
            }],
        }, {
            'char_id': 'char_002',
            'address': [{
                'surface': '母亲', 'by': 'char_001', 'context': 'reference',
                'from_chapter': 2,
            }],
        }]
        _write_edit(book, {'addresses_replace': initial_addresses})
        _apply(book)

        by_ordinal = _response_blueprints()
        blueprint_ctx = _ctx(book, 'stage2.blueprint')
        bp_client, _bp_transport = _client(
            blueprint_ctx,
            lambda req, _index: _respond_blueprints(req, by_ordinal),
        )
        extract.run_blueprint(blueprint_ctx, bp_client)

        updated_addresses = copy.deepcopy(initial_addresses)
        updated_addresses[0]['address'][1]['from_chapter'] = 2
        _write_edit(book, {'addresses_replace': updated_addresses})
        artifact, _report = _apply(book)
        shared = artifact.entities.characters['char_001'].address[1]
        assert (shared.surface, shared.from_chapter) == ('母亲', 2)

        production_store = ArtifactStore(_ctx(book, 'stage2.blueprint', paid=False))
        with pytest.raises(UpstreamStale, match='已过期'):
            production_store.read(ChapterBlueprints, extract.BLUEPRINT_REL)

        display_store = ArtifactStore(readonly_ctx(
            book, 'stage2.blueprint', 'test:stale-blueprint-display'))
        stored = display_store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_patch_does_not_touch_anything_outside_the_named_secret():
    """只动点名的那条 secret 的那几格；其余全书事实**逐字节不变**。"""
    root, book = _sandbox('最小改动_014')
    try:
        _seed(book)
        store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        _artifact_before, before = store.read_own(GlobalGrounding, extract.GLOBAL_REL)
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'first_reveal_chapter': 3}]})
        _apply(book)
        _artifact_after, after = ArtifactStore(
            _ctx(book, 'stage2.global', paid=False)).read_own(
                GlobalGrounding, extract.GLOBAL_REL)

        expected = copy.deepcopy(before)
        # ⚠ 决策集里没写 `kind`，落盘那一格仍然要带上它（载体默认 `first`）：
        #   这里跟着写全，才是「这一格整格被替换」而不是「少写一个键也算没溢出」。
        expected['canon']['secrets'][0]['first_reveal_chapter'] = 3
        expected.pop('_provenance')
        got = copy.deepcopy(after)
        got.pop('_provenance')
        assert got == expected, '改动溢出了点名的那一格'
    finally:
        shutil.rmtree(root, ignore_errors=True)
