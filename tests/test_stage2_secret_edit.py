#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_secret_edit.py —— `stage2.global --apply-edit`：Stage2 typed facts 的 0 成本人工订正。

【它守的是什么】细粒度 patch 与完整 replace 接住长期秘密和规则的修订，
两条路都必须有严格 schema、来源账、同一归一/判据、轮次归档和 WAL 提交。
Stage2 语义审阅退役后：``--apply-edit`` 后没有审阅刷新，机械结论由同一判据
重判与下游 freshness 表达；带 ``post_edit_review`` 的旧流程编辑在预检与
apply 入口被显式拒绝，journal 的 global_review 两格只是空壳兼容格。

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

from novel.stages.stage2 import grounding as grounding_io

import copy
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.stage1 import MainText
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage2 import (
    ChapterBlueprints, GlobalEdit, GlobalEditCommit, GlobalEditFailure,
    GlobalGrounding,
)
from novel.errors import (
    ArtifactUnreadable, GateBlocked, ProvenanceRefused, TerminalState, UnsupportedRunMode,
    UpstreamStale,
)
from novel.orchestrator import (
    execution_permit, operator, queue, state as orchestrator_state, uploads,
)
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2_generation import (
    _client, _ctx, _ok, _respond_blueprints, _response_blueprints,
    _response_global, _edit_static_character,
)
from tests.test_stage2 import main_text

BLUEPRINT_EDGE = 'stage2/blueprint_character_projection.json'


#: 通用底座固定在当前完整剧情契约；契约特有拓扑由 dispatch 测试覆盖。
@pytest.fixture(autouse=True)
def _pin_ir31_arm(monkeypatch):
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')


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


def _overlapping_rule(book: Path) -> dict:
    """构造 schema 可解析、现役时间 HARD 会拒绝的两段规则。"""
    store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
    rule = grounding_io.read(store).world_rules[0].model_dump(mode='json')
    second = copy.deepcopy(rule['versions'][0])
    second.update(version_id='v02', effective_from=2, revealed_from=2)
    rule['versions'].append(second)
    return {'rule_id': rule['rule_id'], 'versions': rule['versions']}


def test_formal_base_no_longer_has_a_review_derived_edit_template(tmp_path):
    """语义审阅退役后，正式稿不再派生编辑模板；替代入口是直接提交 GlobalEdit。"""
    root, book = _tmp_book(tmp_path, '模板退役_002c')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    with pytest.raises(TerminalState, match='正式稿不再有审阅面派生的编辑模板'):
        extract.global_edit_template(ctx)


def test_reviewed_edit_is_refused_at_preview_preflight_and_apply(tmp_path):
    """「当前入口拒绝旧流程」：带 post_edit_review 的编辑在任何写盘前被显式关闭。"""
    root, book = _tmp_book(tmp_path, 'reviewed编辑拒绝_002e')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    payload = {
        'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}],
        'post_edit_review': {
            'actor': 'agent:novel-pipeline-operator',
            'expected_source_sha256': 'a' * 64,
            'reviewed_chapter_ordinals': [1, 2, 3],
            'default_state': 'PASS',
            'reason': '退役流程的旧审阅证明不得再落盘',
            'quarantine_groups': [],
        },
    }
    _write_edit(book, payload)
    before = (book / extract.GLOBAL_REL).read_bytes()
    with pytest.raises(TerminalState, match='语义审阅已退役'):
        _apply(book)
    assert (book / extract.GLOBAL_REL).read_bytes() == before
    assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    assert not (book / extract._GLOBAL_INVALID).exists()

    edit = GlobalEdit.model_validate(payload)
    with pytest.raises(TerminalState, match='语义审阅已退役'):
        extract.preview_edit(ctx, edit)
    with pytest.raises(TerminalState, match='语义审阅已退役'):
        extract.preflight_global_edit(ctx, edit)
    assert (book / extract.GLOBAL_REL).read_bytes() == before


def test_service_global_edit_is_staged_canonically_and_survives_transport_cleanup():
    """一次性 UUID 只负责运输；正式 provenance 必须绑定可长期复算的书内输入。"""
    root, book = _sandbox('服务修订_321')
    try:
        _seed(book)
        edit = GlobalEdit.model_validate({
            'arc_secrets': [{
                'secret_id': 'secret_01',
                'revealed_from': 3,
            }],
        })
        service_path = uploads.save_global_edit(root, '321', edit)
        decoded = decode_artifact(
            extract.GLOBAL_EDIT,
            json.loads(service_path.read_bytes()))
        assert GlobalEdit.model_validate(decoded.payload) == edit

        artifact, _report = extract.run_apply_edit(
            _ctx(book, 'stage2.global', paid=False), str(service_path))
        assert artifact.arc_secrets[0].revealed_from == 3
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
    """普通 edit 的人工 INVALID 修订也由 WAL 补尾，且不伪造 retired review 身份。"""
    root, book = _tmp_book(tmp_path, '普通人工提升_326')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(ctx)
    _write_edit(book, {
        'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 1}],
        'world_rules': [_overlapping_rule(book)],
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
    raw['arc_secrets'][0]['revealed_from'] = 3
    raw['world_rules'][0]['versions'][0]['effective_until'] = 1
    invalid_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    assert store.revalidation_audit(
        extract.GLOBAL_REL, KnowledgeGrounding,
        actor='human:reviewer', reason='修正秘密揭示章').edited_by_human
    assert orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '326', code=ctx.code)

    original = extract._reconcile_reviewed_edit_commit
    monkeypatch.setattr(
        extract, '_reconcile_reviewed_edit_commit',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt('模拟 revalidate prepared 后硬杀')))
    with pytest.raises(KeyboardInterrupt, match='prepared 后'):
        extract.run_revalidate(
            ctx, actor='human:reviewer', reason='修正秘密揭示章')

    journal = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert journal.state == 'prepared'
    assert journal.action_kind == 'revalidate'
    assert journal.reviewed_edit is False
    assert journal.expected_source_sha256 is None
    assert journal.global_review_workpack_sha256 is None
    assert journal.baseline.failure_sha256 is not None
    # 空壳兼容格：journal 仍带 review 形状，但不再对应任何盘上文件。
    assert not (book / extract.GLOBAL_REVIEW_REL).exists()

    current = operator.next_action(root, '326')
    assert current.state == 'blocked' and current.action is not None
    assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_REVALIDATE
    assert current.action.flags == [
        '--revalidate', '--actor', 'human:reviewer',
        '--reason', '修正秘密揭示章',
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
        ctx, actor='human:reviewer', reason='修正秘密揭示章')
    assert promoted and report.passed
    assert not invalid_path.exists()
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    committed = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert committed.state == 'committed'
    assert committed.target.global_grounding.provenance.revalidation_audit.actor == (
        'human:reviewer')


@pytest.mark.parametrize('completed_companions', [0, 1])
def test_generic_global_revalidate_prepared_recovers_every_hard_kill_write_point(
        completed_companions, monkeypatch, tmp_path):
    """无 GlobalEdit/formal 身份的首次 Global INVALID 也由同一 WAL 补尾。"""
    sid = f'3268{completed_companions}'
    root, book = _tmp_book(tmp_path, f'首次全局提升_{sid}')
    ctx = _ctx(book, 'stage2.global')
    store = ArtifactStore(ctx)
    store.write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    response = _response_global()
    response['arc_secrets'][0]['revealed_from'] = 99
    client, _transport = _client(ctx, lambda _req, _index: _ok(response))
    with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
        extract.run_global(ctx, client)

    invalid_path = book / extract._GLOBAL_INVALID
    raw = json.loads(invalid_path.read_text(encoding='utf-8'))
    raw['arc_secrets'][0]['revealed_from'] = 3
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
            actor='human:reviewer', reason='修正首次知识抽取的越界章号')

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
        (extract.GLOBAL_REL, grounding_io.for_write(store, journal.target.global_grounding)),
    ]
    for rel, payload in targets[:completed_companions]:
        store.write_round(rel, payload)

    current = operator.next_action(root, sid)
    assert current.state == 'blocked' and current.action is not None
    assert current.action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_REVALIDATE
    assert current.action.flags == [
        '--revalidate', '--actor', 'human:reviewer',
        '--reason', '修正首次知识抽取的越界章号',
    ]
    permit = execution_permit.permit_from_next(
        current, 'stage2.global', current.action.flags)
    assert permit.flags == current.action.flags

    promoted, report = extract.run_revalidate(
        _ctx(book, 'stage2.global', paid=False),
        actor='human:reviewer', reason='修正首次知识抽取的越界章号')
    assert promoted and report.passed
    assert not invalid_path.exists()
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    committed = store.read_own(
        GlobalEditCommit, extract.GLOBAL_EDIT_COMMIT)[0]
    assert committed.state == 'committed'


def test_ordinary_candidate_terminal_closes_input_wal_and_keeps_human_owner(
        monkeypatch, tmp_path):
    """候选 schema 终态先落 sidecar；重启只关 WAL，后续动作由人给出新编辑。"""
    root, book = _tmp_book(tmp_path, '普通候选终态_327')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    _write_edit(book, {
        'arc_secrets': [{
            'secret_id': 'secret_01', 'revealed_from': 3,
        }],
    })
    original_validate = GlobalGrounding.model_validate

    def reject_only_applied_candidate(cls, value, *args, **kwargs):
        provenance = value.get('_provenance') if isinstance(value, dict) else None
        inputs = getattr(provenance, 'inputs', {})
        if extract.GLOBAL_EDIT in inputs:
            broken = copy.deepcopy(value)
            broken.pop('setting', None)
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
    # 终态不再派生队列动作：恢复面已关，唯一的后继是人工给出新编辑。
    assert orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '327', code=ctx.code)
    assert orchestrator_state.stage2_edit_commit_recovery_flags(
        root, '327', code=ctx.code) is None


def test_new_ordinary_edit_explicitly_supersedes_old_terminal_failure(tmp_path):
    """旧 failure 不得抢 planner；新运输 edit canonical 化时明确归档它。"""
    root, book = _tmp_book(tmp_path, '普通新修订_328')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    _write_edit(book, {
        'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 1}],
        'world_rules': [_overlapping_rule(book)],
    })
    with pytest.raises(GateBlocked):
        _apply(book)
    assert (book / extract.GLOBAL_EDIT_FAILURE).is_file()

    corrected_rule = _overlapping_rule(book)
    corrected_rule['versions'][0]['effective_until'] = 1
    replacement = GlobalEdit.model_validate({
        'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 3}],
        'world_rules': [corrected_rule],
    })
    service_path = uploads.save_global_edit(root, '328', replacement)
    assert orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '328', code=ctx.code)

    landed, report = extract.run_apply_edit(ctx, str(service_path))
    assert report.passed
    assert landed.arc_secrets[0].revealed_from == 3
    assert not (book / extract.GLOBAL_EDIT_FAILURE).exists()
    assert not orchestrator_state.stage2_global_edit_terminal_failure_needed(
        root, '328', code=ctx.code)


def test_a_noop_service_global_edit_is_rejected_before_canonical_staging():
    root, book = _sandbox('空修订_322')
    try:
        _seed(book)
        edit = GlobalEdit.model_validate({
            'arc_secrets': [{
                'secret_id': 'secret_01',
                'revealed_from': 2,
            }],
        })
        service_path = uploads.save_global_edit(root, '322', edit)

        with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
            extract.run_apply_edit(
                _ctx(book, 'stage2.global', paid=False), str(service_path))
        assert 'candidate_noop:1' in caught.value.safe_summary
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
            'arc_secrets': [{
                'secret_id': 'secret_01',
                'revealed_from': 3,
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
        assert artifact.arc_secrets[0].revealed_from == 3
        assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    finally:
        shutil.rmtree(root)


def test_new_wal_is_not_swallowed_by_previous_committed_formal_journal(
        monkeypatch, tmp_path):
    """J0/E0 是新 WAL 的 baseline，不得被误认成 WAL1 已成功。"""
    root, book = _tmp_book(tmp_path, '旧提交新WAL_324')
    try:
        _seed(book)
        _write_edit(book, {'arc_secrets': [{
            'secret_id': 'secret_01', 'revealed_from': 3,
        }]})
        first, _report = _apply(book)
        assert first.arc_secrets[0].revealed_from == 3
        old_canonical = (book / extract.GLOBAL_EDIT).read_bytes()
        old_commit = (book / extract.GLOBAL_EDIT_COMMIT).read_bytes()

        edit = GlobalEdit.model_validate({
            'arc_secrets': [{
                'secret_id': 'secret_01', 'revealed_from': 2,
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
        assert landed.arc_secrets[0].revealed_from == 2
        assert (book / extract.GLOBAL_EDIT).read_bytes() != old_canonical
        assert not (book / extract.GLOBAL_EDIT_INPUT_COMMIT).exists()
    finally:
        shutil.rmtree(root)


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 主样本：错误揭示章 → 人工修正 → 重判 → 下游 stale
# ─────────────────────────────────────────────────────────────────────────────

def test_a_wrong_reveal_chapter_is_corrected_and_the_downstream_turns_stale():
    """揭示修订真实落盘并重判；当前 Plot 的 Global 依赖失效后可免费复验。

    人物称名投影没有变化，不能把它误判为失效。Plot 还消费必要 Global，
    揭示章修订必须使真实消费边过期；免费重新编译只重绑来源，不生成语义接受。
    """
    root, book = _sandbox('揭示章订正_001')
    try:
        _seed(book, with_blueprints=True)
        bp_ctx = _ctx(book, 'stage2.blueprint', paid=False)
        assert ArtifactStore(bp_ctx).freshness(extract.BLUEPRINT_REL).state == 'fresh', \
            '底座还没动，下游就已经 stale？那这条测试后面什么也证明不了'

        from novel.stages.stage2.blueprint.inputs import read_input_digests
        inputs_before = read_input_digests(ArtifactStore(bp_ctx))
        blueprint_before = ArtifactStore(bp_ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
        ledger_before = bp_ctx.ledger.path.read_bytes()
        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01', 'revealed_from': 3}]})
        artifact, report = _apply(book)

        # ① 改真的落进产物
        secret = artifact.arc_secrets[0]
        assert secret.revealed_from == 3
        reloaded = grounding_io.read(ArtifactStore(_ctx(book, 'stage2.global', paid=False)))
        assert reloaded.arc_secrets[0].revealed_from == 3

        # ② 重走归一：埋点章 1 < 新揭示章 3 ⇒ 不该被裁掉
        assert secret.setup_chapters == [1]

        # ③ 重判真的跑过（不是把旧报告抄一遍）
        assert report.node == 'stage2.global' and report.rules
        assert {r.rule for r in report.rules if r.status == 'skipped'} == set(), \
            '有判据没排上——那一行会绿着而什么都没查'

        # ④ 人物投影仍相同；真实 Global 消费改变不能沿用旧来源身份。
        fresh = ArtifactStore(bp_ctx).freshness(extract.BLUEPRINT_REL)
        edge = next(e for e in fresh.edges if e.key == BLUEPRINT_EDGE)
        assert edge.recorded_file == edge.now_file
        assert edge.recorded_consumed == edge.now_consumed
        assert edge.state == 'fresh'
        global_edge = next(e for e in fresh.edges if e.key == extract.GLOBAL_REL)
        assert (fresh.state, global_edge.state) == ('stale', 'stale')
        assert global_edge.recorded_consumed != global_edge.now_consumed
        inputs_after = read_input_digests(ArtifactStore(bp_ctx))
        assert inputs_before['1'] == inputs_after['1']
        assert inputs_before['2'] != inputs_after['2']
        # 揭示章本身是写作输入的一部分（plot-global-scope-v2 携带 revealed_from），
        # 因此第 3 章的身份也随调度变化；蓝图主体不嵌入该字段，重绑后逐字节不变。
        assert inputs_before['3'] != inputs_after['3']

        assert extract.run_renormalize(bp_ctx).passed
        rebuilt = ArtifactStore(bp_ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL,
                                                require_current=True)[0]
        assert rebuilt.blueprints == blueprint_before.blueprints
        assert bp_ctx.ledger.path.read_bytes() == ledger_before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_correcting_the_reveal_chapter_backwards_preserves_same_chapter_setup():
    """同章铺垫合法且免费归一不裁剪；晚于揭示的铺垫由明确 schema 拒绝。"""
    root, book = _sandbox('揭示章前移_002')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        ledger = ctx.ledger.path.read_bytes()
        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01', 'revealed_from': 1}]})
        artifact, report = _apply(book)
        assert report.passed
        assert artifact.arc_secrets[0].setup_chapters == [1]
        assert artifact.arc_secrets[0].revealed_from == 1
        assert extract.run_renormalize(ctx).passed
        stored = grounding_io.read(ArtifactStore(ctx))
        assert stored.arc_secrets[0].setup_chapters == [1]
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01', 'setup_chapters': [2]}]})
        with pytest.raises(extract.GlobalEditCandidateRejected):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert ctx.ledger.path.read_bytes() == ledger
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize('events', [
    [_knowledge_character('char_001', 1)],
    [_knowledge_group('知情的沈家人', 2)],
])
def test_global_rejects_retired_typed_knowledge_timeline(events):
    """人物及群体知情转归 KnowledgeLinks，Global 不能静默接回旧知情矩阵。"""
    root, book = _sandbox('知情人订正_003')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'arc_secrets': [{
            'secret_id': 'secret_01',
            'fact': '父亲早已回城，只是被债主扣在城南客栈里',
            'fact_ref': None, 'evidence_refs': [_fact_locator()],
            'knowledge_events': events,
        }]})
        with pytest.raises(ArtifactUnreadable, match='knowledge_events'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_role_correction_rebinds_knowledge_without_llm_and_stays_mechanically_current():
    """独立人物订正免费重绑知识；机械复验后 freshness 恢复，全程零模型。"""
    root, book = _sandbox('人物身份订正_003a')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        corrected = '沈家账房，负责核对往来账目'
        ledger = ctx.ledger.path.read_bytes()
        from contracts.schema.characters import CharactersEdit
        from novel.stages.stage2 import characters
        character_ctx = _ctx(book, 'stage2.characters', paid=False)
        template = characters.edit_template(character_ctx)
        row = next(op for op in template['operations'] if op['char_id'] == 'char_001')
        row['character'].update(role=corrected, role_evidence_refs=[_role_locator()])
        characters.apply_edit(character_ctx, CharactersEdit.model_validate(
            {**template, 'operations': [row]}), actor='test:human', reason='核对首次公开身份')
        assert store.freshness(extract.GLOBAL_REL).state == 'stale'
        report = extract.run_renormalize(_ctx(book, 'stage2.global', paid=False))
        assert report.node == 'stage2.global' and report.passed
        reloaded = store.read_own(characters.CharacterRegistry, characters.REL)[0]
        assert reloaded.characters['char_001'].role == corrected
        assert ctx.ledger.path.read_bytes() == ledger
        fresh = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        assert fresh.freshness(extract.GLOBAL_REL).state == 'fresh'
        assert fresh.freshness(characters.REL).state == 'fresh'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_leaky_role_invalid_is_repaired_by_the_typed_zero_llm_path(tmp_path):
    """人物 owner 留可读 INVALID；正确证据修复后归档原正式稿与失败稿。"""
    from contracts.schema.characters import CharacterRegistry, CharactersEdit
    from novel.stages.stage2 import characters
    root, book = _tmp_book(tmp_path, '身份泄漏恢复_003aa')
    _seed(book)
    ctx = _ctx(book, 'stage2.characters', paid=False)
    store = ArtifactStore(ctx)
    formal = store.path(characters.REL)
    invalid = store.path(characters.INVALID)
    before = formal.read_bytes()
    knowledge = store.path(extract.GLOBAL_REL).read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    template = characters.edit_template(ctx)
    row = next(op for op in template['operations'] if op['char_id'] == 'char_001')
    row['character'].update(
        role='沈家账房，最终下狱')
    with pytest.raises(GateBlocked, match='role_leaks_fate'):
        characters.apply_edit(
            ctx, CharactersEdit.model_validate({**template, 'operations': [row]}),
            actor='test:human', reason='核对静态身份与证据')
    assert formal.read_bytes() == before
    invalid_bytes = invalid.read_bytes()
    failed = store.read_own(CharacterRegistry, characters.INVALID)[0]
    assert failed.characters['char_001'].role_evidence_refs
    assert all(ref in failed.evidence_anchors for ref in failed.characters['char_001'].role_evidence_refs)
    repair = characters.edit_template(_ctx(book, 'stage2.characters', paid=False))
    fixed = next(op for op in repair['operations'] if op['char_id'] == 'char_001')
    fixed['character'].update(
        role='沈家账房，负责核对往来账目', role_evidence_refs=[_role_locator()])
    edit = CharactersEdit.model_validate({**repair, 'operations': [fixed]})
    main = store.path(extract.MAIN_TEXT)
    main_bytes = main.read_bytes()
    changed_main = json.loads(main_bytes)
    changed_main['payload'][0]['text'] += '正文发生变化。'
    main.write_text(json.dumps(changed_main, ensure_ascii=False), encoding='utf-8')
    with pytest.raises((UpstreamStale, ProvenanceRefused)):
        characters.apply_edit(
            _ctx(book, 'stage2.characters', paid=False), edit,
            actor='test:human', reason='核对静态身份与证据')
    assert invalid.read_bytes() == invalid_bytes and formal.read_bytes() == before
    main.write_bytes(main_bytes)
    corrected = characters.apply_edit(
        _ctx(book, 'stage2.characters', paid=False), edit,
        actor='test:human', reason='核对静态身份与证据')
    assert all(ref in corrected.evidence_anchors for ref in corrected.characters['char_001'].role_evidence_refs)
    assert not invalid.exists()
    rounds = book / 'stage2/_rounds'
    assert any(p.read_bytes() == invalid_bytes for p in rounds.glob('characters.INVALID.r*.json'))
    assert any(p.read_bytes() == before for p in rounds.glob('characters.r*.json'))
    assert store.path(extract.GLOBAL_REL).read_bytes() == knowledge
    assert ctx.ledger.path.read_bytes() == ledger


def test_typed_edit_requires_explicit_chapter_correction_and_keeps_audit_compact():
    """人工定位符只在声明章唯一匹配；错章拒绝后显式修正，全程不购买模型。"""
    root, book = _sandbox('人工证据错章恢复_003ab')
    try:
        _seed(book)
        locator = '李宝珠把账册摊在桌上一页页翻。'
        ctx = _ctx(book, 'stage2.global', paid=False)
        before = (book / extract.GLOBAL_REL).read_bytes()
        ledger = ctx.ledger.path.read_bytes()
        patch = {'arc_secrets': [{
            'secret_id': 'secret_01', 'fact': '账册已被摊开逐页核对',
            'fact_ref': None, 'evidence_refs': [_locator(1, locator)],
        }]}
        _write_edit(book, patch)
        with pytest.raises(extract.GlobalEditCandidateRejected):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before
        assert not (book / extract._GLOBAL_INVALID).exists()
        patch['arc_secrets'][0]['evidence_refs'] = [_locator(3, locator)]
        _write_edit(book, patch)
        artifact, report = _apply(book)
        assert report.passed
        anchor = artifact.evidence_anchors[artifact.arc_secrets[0].evidence_refs[0]]
        assert anchor.chapter_ordinal == 3
        assert ctx.ledger.path.read_bytes() == ledger
        assert not [row for row in ctx.ledger.audit_events if row['kind'] == 'evidence_locator_rewrite']
        assert locator not in json.dumps(ctx.ledger.audit_events, ensure_ascii=False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_complete_typed_surfaces_reach_disk_and_stay_mechanically_current():
    """拆分复合秘密并完整替换规则，经真实知识写口落盘并免费复验通过。"""
    root, book = _sandbox('集合订正_003b')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)

        _write_edit(book, {
            'arc_secrets_replace': [{
                'secret_id': 'secret_01',
                'fact': '父亲已经回城',
                'fact_ref': None, 'evidence_refs': [_fact_locator()],
                'setup_chapters': [1],
                'revealed_from': 3,
            }, {
                'secret_id': 'secret_02',
                'fact': '母亲早就知道那封信的事',
                'fact_ref': None,
                'evidence_refs': [_locator(2, '其实母亲早就知道那封信的事。')],
                'setup_chapters': [],
                'revealed_from': 2,
            }],
            'world_rules_replace': [{
                'rule_id': 'wr_02',
                'kind': 'legal_rule',
                'versions': [{
                    'version_id': 'v01', 'effective_from': 1,
                    'effective_until': None, 'revealed_from': 1,
                    'desc': '城中夜间梆子响过以后实行宵禁，行人须留在住所直到天明',
                    'evidence_refs': [_locator(1, '远处传来更夫的梆子声。')],
                }],
            }],
        })
        artifact, report = _apply(book)

        assert report.node == 'stage2.global'
        assert [row.secret_id for row in artifact.arc_secrets] == [
            'secret_01', 'secret_02']
        assert [row.rule_id for row in artifact.world_rules] == ['wr_02']
        store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        assert store.freshness(extract.GLOBAL_REL).state == 'fresh'
        # 审阅文件已退役：编辑后没有 review 产物，也没有 live INVALID。
        assert not store.exists(extract.GLOBAL_REVIEW_REL)
        assert not (book / extract._GLOBAL_INVALID).exists()
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
        before = grounding_io.read(store).provenance

        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01', 'fact': '父亲早已回城，只是不肯认这笔账',
             'fact_ref': None, 'evidence_refs': [_fact_locator()]}]})
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
        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01',
             'revealed_from': 3}]})
        _apply(book)
        archived = sorted(p.name for p in (book / 'stage2' / '_rounds').iterdir())
        assert {
            'global_grounding.r1.json', 'global_edit_input_commit.r1.json',
        }.issubset(archived)
        assert (book / 'stage2' / '_rounds' / 'global_grounding.r1.json').read_bytes() == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_partial_write_is_resumed_from_prepared_bundle(
        monkeypatch):
    """普通异常也保留 WAL：partial formal 只能按冻结 target 继续收敛。"""
    root, book = _sandbox('组合事务_005b')
    try:
        _seed(book)
        _write_edit(book, {'arc_secrets': [{
            'secret_id': 'secret_01',
            'revealed_from': 3,
        }]})
        formal_before = (book / extract.GLOBAL_REL).read_bytes()
        original_write = ArtifactStore.write

        def fail_formal(self, rel, artifact):
            if rel == extract.GLOBAL_REL:
                raise OSError('模拟 formal 落盘中断')
            return original_write(self, rel, artifact)

        monkeypatch.setattr(ArtifactStore, 'write', fail_formal)
        with pytest.raises(OSError, match='formal 落盘中断'):
            _apply(book)

        assert (book / extract.GLOBAL_REL).read_bytes() == formal_before
        assert (book / extract.GLOBAL_EDIT_INPUT_COMMIT).is_file()
        prepared_raw = json.loads(
            (book / extract.GLOBAL_EDIT_COMMIT).read_bytes())
        assert decode_artifact(
            extract.GLOBAL_EDIT_COMMIT, prepared_raw).payload['state'] == 'prepared'

        monkeypatch.setattr(ArtifactStore, 'write', original_write)
        artifact, report = _apply(book)

        assert report.passed
        assert artifact.arc_secrets[0].revealed_from == 3
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
        GlobalEdit.model_validate({'arc_secrets': [
            {'secret_id': 'secret_01', 'reveal_chapter': 3}]})           # 正确的是 revealed_from
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secret': [{'secret_id': 'secret_01', 'fact': 'x'}]})


def test_an_empty_patch_is_refused():
    """一个字段都没给的补丁 ⇒ 拒收。空补丁什么都不改，
    而它在盘上与「人看过并改了」长得一样。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'arc_secrets': [{'secret_id': 'secret_01'}]})
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'arc_secrets': []})


def test_two_patches_for_one_secret_are_refused():
    """同一条 secret 给两份补丁 ⇒ 拒收：哪一份生效取决于遍历顺序，
    而那意味着**人的决定被实现细节改写**。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'arc_secrets': [
            {'secret_id': 'secret_01', 'fact': 'A', 'fact_ref': None, 'evidence_refs': [_fact_locator()]},
            {'secret_id': 'secret_01', 'fact': 'B', 'fact_ref': None, 'evidence_refs': [_fact_locator()]}]})


@pytest.mark.parametrize('payload,why', [
    ({'arc_secrets': [{'secret_id': 'secret_01', 'revealed_from': 9}]},
     '揭示章越界'),
    ({'arc_secrets': [{'secret_id': 'secret_01', 'setup_chapters': [7]}]},
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
        with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
            _apply(book)
        assert 'invalid_chapter_reference:1' in caught.value.safe_summary
        assert (book / extract.GLOBAL_REL).read_bytes() == before, f'{why}：正式底座被动过了'
        assert not (book / extract._GLOBAL_INVALID).exists(), \
            f'{why}：决策集自己错了，不该给它建一份 `.INVALID` 归档'
        assert not (book / 'stage2' / '_rounds').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unknown_secret_id_is_refused_without_echoing_model_payload():
    """指向盘上没有的 secret ⇒ 显式终态，但持久拒因不得回显模型 ID。"""
    root, book = _sandbox('未知secret_007')
    try:
        _seed(book)
        _write_edit(book, {'arc_secrets': [{
            'secret_id': 'secret_09', 'fact': '凭空一条',
            'fact_ref': None, 'evidence_refs': [_fact_locator()]}]})
        with pytest.raises(extract.GlobalEditCandidateRejected) as caught:
            _apply(book)
        assert 'missing_patch_subject:1' in caught.value.safe_summary
        assert 'secret_09' not in caught.value.safe_summary
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_dangling_known_by_reference_is_rejected_before_any_artifact_write():
    """退休的 Global 知情槽在读入时拒绝；真实 KnowledgeLinks 外键由 Gate B 验证。"""
    root, book = _sandbox('闸门拦下_008')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01',
             'knowledge_events': [_knowledge_character('char_777', 1)]}]})
        with pytest.raises(ArtifactUnreadable, match='knowledge_events'):
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
        _write_edit(book2, {'arc_secrets': [{
            'secret_id': 'secret_01', 'fact': 'x',
            'fact_ref': None, 'evidence_refs': [_fact_locator()]}]})
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


def test_the_blueprint_node_has_a_distinct_typed_plot_edit_entry():
    """Blueprint 现役 ``--apply-edit`` 按章修订完整剧情及显式身份操作。

    Global 同名 flag 仍只吃 ``GlobalEdit``；两条路由各自的注册载体、
    producer 和上游边分开，不再靠“一个 flag 全仓只能有一个语义”的旧断言。
    """
    from contracts.artifacts import STAGE2_BLUEPRINT_EDIT
    from contracts.invariants import NODES
    from contracts.schema import ARTIFACTS
    from contracts.schema.plot_edit import PlotEdit

    assert {'--emit-edit-template', '--validate-edit', '--apply-edit'} <= set(
        NODES['stage2.blueprint'].flags)
    assert ARTIFACTS[STAGE2_BLUEPRINT_EDIT] is PlotEdit

    # 同名 flag 没有让 Global producer 窃取 Blueprint 语义。
    root, book = _sandbox('走错节点_011')
    try:
        _seed(book)
        _write_edit(book, {'arc_secrets': [{
            'secret_id': 'secret_01', 'fact': 'x',
            'fact_ref': None, 'evidence_refs': [_fact_locator()]}]})
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
        _write_edit(book, {'arc_secrets': [{
            'secret_id': 'secret_01', 'fact': '改一个字',
            'fact_ref': None, 'evidence_refs': [_fact_locator()]}]})
        artifact, _report = _apply(book)
        assert extract.GLOBAL_EDIT in artifact.provenance.inputs, \
            f'来源账里没有 {extract.GLOBAL_EDIT} 这条边：{sorted(artifact.provenance.inputs)}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stale_blueprints_are_displayable_but_not_consumable_after_base_edit():
    """称名投影真实变化后，旧蓝图可供只读诊断，但不得再进生产链或付费请求。

    不靠增删展示字段制造假 stale：把 ``char_001`` 首个登记称谓的
    ``identity_revealed_from`` 从第 1 章移到第 2 章，它会真正改变第 1 章
    下发的 ``char_id -> surface`` 候选，因此旧蓝图必须 stale。
    """
    root, book = _sandbox('下游可读_013')
    try:
        _seed(book, with_blueprints=True)
        from contracts.schema.characters import CharacterRegistry
        from novel.stages.stage2 import characters as characters_module
        char_store = ArtifactStore(_ctx(book, 'stage2.characters', paid=False))
        registry = char_store.read(CharacterRegistry, characters_module.REL)
        aliases = [row.model_dump(mode='json')
                   for row in registry.characters['char_001'].aliases]
        assert aliases and aliases[0]['identity_revealed_from'] == 1
        aliases[0]['identity_revealed_from'] = 2
        _edit_static_character(book, 'char_001', aliases=aliases)
        extract.run_renormalize(_ctx(book, 'stage2.global', paid=False))

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
        _artifact_before, before = grounding_io.read_own(store, extract.GLOBAL_REL)
        _write_edit(book, {'arc_secrets': [
            {'secret_id': 'secret_01', 'revealed_from': 3}]})
        _apply(book)
        _artifact_after, after = grounding_io.read_own(ArtifactStore(
            _ctx(book, 'stage2.global', paid=False)), extract.GLOBAL_REL)

        expected = copy.deepcopy(before)
        # ⚠ 决策集里没写 `kind`，落盘那一格仍然要带上它（载体默认 `first`）：
        #   这里跟着写全，才是「这一格整格被替换」而不是「少写一个键也算没溢出」。
        expected['arc_secrets'][0]['revealed_from'] = 3
        expected.pop('_provenance')
        got = copy.deepcopy(after)
        got.pop('_provenance')
        assert got == expected, '改动溢出了点名的那一格'
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
