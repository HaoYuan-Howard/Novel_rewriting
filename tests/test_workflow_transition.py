#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 manifest 推进走真实 Core、来源子进程、CLI 与文件系统恢复；不调用真实模型。

【F8c narrowed：现役只剩 v5→v6】v4→v5 迁移随旧链退役——它唯一的新产物是
Blueprint 人物称名投影的免费重编译，编译器 `novel.stages.stage2.shared.
character_refs` 已随旧链整层删除，投影本身也是退役产物；本模块不手抄旧编译
器，v4 基础按 legacy_incompatible 只读拒识。原 v4→v5 投影族用例因此按
narrowed 后生产改写：

  * **v4 源显式拒绝**：`_pair`／`preview`／`_prepared` 三层都说「没有登记
    此来源的下一版显式迁移」，且不建任何迁移记录、不改一个字节；
  * **v4 前驱指针不可重放**：带 v4→v5 已完成前驱的 v5 manifest 同样只读
    拒识（普通入口 `workflow_manifest` 也拿不到它）；
  * **v5→v6 主路径**：不重编译任何已验收产物（`WorkflowTransitionPreparedV2`
    钉死 projections 为空），崩溃窗口逐格恢复；
  * **prepared 禁 projections**：V2 校验器拒绝任何非空投影。

来源代码替身仍是「现役字节 + 退役模块 shim」：`_SOURCE_PROOF` 子进程按
``PYTHONPATH=source_code`` import 的 `novel.stages.stage2.global_review` 与
`novel.stages.stage5.closure` 由来源树解析（真实冻结 checkout 自带完整语义，
这里以最小等价检查证明同一历史前置）。代码身份核验由 `transition._code_identity`
替身承担。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from contracts.schema.workflow import (
    WorkflowManifestV4, WorkflowManifestV5, WorkflowManifestV6, WorkflowManifestV7,
    WorkUnitContentTurn, WorkUnitLedgerV2, WorkUnitRegistryV1,
    WorkflowTransitionCompletedV1, WorkflowTransitionPendingV1,
    WorkflowTransitionPreparedV1, WorkflowTransitionProjectionV1,
    WorkflowTransitionProofV1, WorkflowTransitionRefV1,
)
from novel import paths, prov, work_units
from novel.cli import main as cli
from novel.errors import TerminalState
from novel.orchestrator import admission, approvals
from novel.orchestrator import workflow_transition as transition

REPO = Path(__file__).resolve().parents[1]
CODE = 'a' * 40
SID = '2042'

#: v7 起不登记 v6→v7 迁移：旧 v4~v6 根由冻结旧代码读取，v7 只允许 clean 初始化。
#: 生产只保留 5→6 一对显式迁移（``_TRANSITION_PAIRS``）。
_LEGACY_REVIEW_TOKEN = 's2gr1_' + 'a' * 64
_LEGACY_CLOSURE_TOKEN = 's5cr1_' + 'b' * 64

#: 冻结旧来源替身里的退役模块：真实 v5 来源 checkout 自带完整
#: ``require_global_accepted``／``require_stage5_closed`` 语义闸；工作树无法
#: 在仓内复原整份旧代码，这里以最小等价检查证明同一历史前置——服务审批账
#: 存在 stage2.global 的当前 typed 接受（不认旧 gate bool），且 s5 由一份
#: closure 回执打开。除此之外来源树与现役字节逐字相同。
_LEGACY_GLOBAL_REVIEW_SHIM = '''"""冻结旧来源替身：Stage2 语义审阅已退役，仅证明历史迁移前置。"""
import json


def require_global_accepted(ctx) -> None:
    from novel.orchestrator import approvals
    path = approvals._path(ctx.novel_dir.parent, ctx.sid)
    doc = json.loads(path.read_bytes())
    row = (doc.get('stage2_reviews') or {}).get('stage2.global') or {}
    if row.get('decision') != 'accept' or not row.get('review_token'):
        raise ValueError('stage2.global 尚无当前独立 Commander 接受')
'''

_LEGACY_STAGE5_CLOSURE_SHIM = '''"""冻结旧来源替身：Stage5 人工闭合已退役，仅证明历史迁移前置。"""


def require_stage5_closed(store) -> None:
    import json

    from novel.orchestrator import approvals
    path = approvals._path(store.ctx.novel_dir.parent, store.ctx.sid)
    doc = json.loads(path.read_bytes())
    if not doc.get('gate', {}).get('s5') or not (doc.get('closure_history') or []):
        raise ValueError('stage5 尚无当前机械闭合回执')
'''


def _frozen_legacy_source(root: Path) -> Path:
    """物化 v5 迁移的「来源代码」替身：现役字节 + 两个退役语义闸模块。"""
    source = root / 'legacy-source'
    for package in ('novel', 'contracts'):
        shutil.copytree(REPO / package, source / package,
                        ignore=shutil.ignore_patterns('__pycache__'))
    review = source / 'novel' / 'stages' / 'stage2' / 'global_review.py'
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(_LEGACY_GLOBAL_REVIEW_SHIM, encoding='utf-8')
    closure = source / 'novel' / 'stages' / 'stage5' / 'closure.py'
    closure.parent.mkdir(parents=True, exist_ok=True)
    closure.write_text(_LEGACY_STAGE5_CLOSURE_SHIM, encoding='utf-8')
    return source


def _write_legacy_stage2_acceptance(base: Path, sid: str) -> None:
    """按 v5 时代的服务账格式重建历史凭据，供旧来源替身核验。

    写入的是**历史形状**（`stage2_reviews` 与 closure 回执键），现役入口不读
    也不写这些键；`approvals._read_raw` 拒绝它正好说明「现役读者只认 v9」，
    而迁移来源证明只认冻结旧代码自己的语义。
    """
    path = approvals._path(base, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    decision = {
        'node': 'stage2.global', 'decision': 'accept',
        'review_token': _LEGACY_REVIEW_TOKEN,
        'actor': 'codex', 'reason': '工程核对合成源文后接受 Core',
        'review': {'scope_sha256': 'b' * 64, 'default_decision': 'accept',
                   'exceptions': []},
    }
    closure = {
        'actor': 'system:stage5-auto', 'reason': '机械闭合回执',
        'workpack_sha256': 'c' * 64, 'review_sha256': 'd' * 64,
        'closure_token': _LEGACY_CLOSURE_TOKEN,
        'must_appear': [], 'group_acks': [],
        'decision_sha256': 'e' * 64,
        'decided_at': '2026-09-13T00:00:00+00:00',
    }
    path.write_bytes(work_units._bytes({
        'stage2_reviews': {'stage2.global': decision},
        'stage2_review_history': [dict(decision, decided_at='2026-09-13T00:00:00+00:00')],
        'closure_history': [closure],
        'gate': {gate: gate == 's5' for gate in ('s0', 's1', 's2', 's4', 's5', 's6', 's7')},
    }))


def _blob_digest(directory: Path, unit_id: str, turn_id: str,
                 payload: dict) -> str:
    """按 `WorkUnitBlobV1` 的原字节约定落一份证据，返回其 SHA-256。"""
    raw = work_units._bytes({'schema_version': 1, 'unit_id': unit_id,
                             'turn_id': turn_id, 'payload': payload})
    digest = hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{digest}.json').write_bytes(raw)
    return digest


def _write_legacy_work_units(base: Path, sid: str, task_id: str, *,
                             node: str, scope: str = 'book',
                             with_turn: bool = False) -> None:
    """按旧链契约手写一份工作单元账（节点名是旧链键，轮次可带真实证据）。

    现役 `WorkUnitBudget` 拒绝为退役节点新建模（`executor_content_limit=0`），
    而迁移来源证明要核验的正是这些历史已购回答的归属与字节 ⇒ 这里按原契约
    直接落账：unit_id 仍由同一 `_digest(identity)` 派生，blob 字节可复算，
    登记账逐本累积（多个单元共用一个 registry）。
    """
    identity = {'task_id': task_id, 'sid': sid, 'node': node,
                'role': 'executor', 'scope': scope}
    unit_id = work_units._digest(identity)
    turns: tuple[WorkUnitContentTurn, ...] = ()
    directory = paths.service_runs_dir(base) / sid / 'work_units' / unit_id
    if with_turn:
        request = _blob_digest(directory, unit_id, 'turn-1',
                               {'kind': 'request'})
        response = _blob_digest(directory, unit_id, 'turn-1',
                                {'kind': 'response'})
        turns = (WorkUnitContentTurn(
            turn_id='turn-1', ordinal=1, request_sha256=request,
            request_payload_sha256=request, response_sha256=response,
            run_id='legacy-run-1', state='delivered', created_at=1),)
    registry_path = paths.work_unit_registry_path(base, sid)
    if registry_path.exists():
        registry = work_units._read(base, registry_path, WorkUnitRegistryV1)
        registry = registry.model_copy(
            update={'unit_ids': (*registry.unit_ids, unit_id)})
    else:
        registry = WorkUnitRegistryV1(task_id=task_id, sid=sid,
                                      unit_ids=(unit_id,))
    ledger = WorkUnitLedgerV2(unit_id=unit_id, **identity, turns=turns)
    work_units._write(base, registry_path, registry)
    work_units._write(base, directory / 'ledger.json', ledger)


@pytest.fixture(scope='module')
def legacy_source(tmp_path_factory):
    return _frozen_legacy_source(tmp_path_factory.mktemp('transition-source'))


@pytest.fixture(scope='module')
def frozen_v5(tmp_path_factory, legacy_source):
    """合成一份合法 v5 来源：clean base + 旧链工作单元账 + 存量接受凭据。

    旧联合生产链（analyze→A/B→writer/closure）的测试助手随旧链删除，v5 来源
    在此按**原契约**逐件重建：Stage2 接受三件套、旧链节点的工作单元账、
    stage2.global typed 接受与 s5 机械闭合回执、真实 owner open→close→
    drained，最后只把 manifest 降级成 V5（task_id/created_at 不变）。
    """
    base = tmp_path_factory.mktemp('transition-v5')
    with pytest.MonkeyPatch.context() as identity:
        identity.setattr(prov, 'code_version', lambda: CODE)
        from novel.work_units import initialize_workflow
        initialize_workflow(base)
        original = work_units.require_workflow(base)
        book = base / f'书名_{SID}'
        for rel in ('stage2/characters.json', 'stage2/global_grounding.json',
                    'stage2/global_review.json', 'stage2/blueprint_character_projection.json',
                    'stage4/plan_chapters.json', 'stage4/writer_context.json',
                    'stage5/ch01.json'):
            target = book / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(work_units._bytes({'rel': rel}))
        _write_legacy_work_units(base, SID, original.task_id,
                                 node='stage2.analyze', with_turn=True)
        _write_legacy_work_units(base, SID, original.task_id,
                                 node='stage5.write', scope='chapter:1')
        _write_legacy_stage2_acceptance(base, SID)
        # profiles 位于服务运行资料；迁移必须逐字保留其嵌套内容和费用账。
        profile = paths.service_dir(base) / 'original_profiles.json'
        profile.write_text(json.dumps({'executor': {'driver': 'claude-code-glm',
            'model': 'glm-5.3-flash[1M]', 'reasoning_effort': 'high'},
            'commander': {'driver': 'claude-code-glm', 'model': 'glm-5.3-flash[1M]',
                          'reasoning_effort': 'high'}}, ensure_ascii=False), encoding='utf-8')
        (paths.service_dir(base) / 'ledger.jsonl').write_bytes(b'')
        # 实际 owner open → close → drained，而不是伪造 stopped 快照。
        with admission.owner_lease(base, 'old-parent'):
            admission.open_gate(base, 'old-parent', SID)
            admission.close_gate(base, 'old-parent')
            admission.mark_drained(base, 'old-parent')
        source = WorkflowManifestV5(
            task_id=original.task_id, created_at=original.created_at)
        work_units._write(base, paths.workflow_manifest_path(base), source)
        # 来源证明在冻结替身里真跑一次；失败就在 fixture 层暴露，不静默跳过。
        proof = transition._source_proof(base, legacy_source, CODE, accepted=True)
    return base, SID, source, legacy_source, proof


@pytest.fixture
def setup(tmp_path, monkeypatch, frozen_v5):
    original, sid, source, legacy_source, _proof = frozen_v5
    base = tmp_path / '输出'
    shutil.copytree(original, base)
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    # 工作树有本批并行改动；仅代码 clean checkout 证明替换。来源 Core/生命周期
    # 证明仍由真实子进程读取完整原字节，不伪造接受或停止结论。
    monkeypatch.setattr(transition, '_code_identity', lambda root: CODE)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    return dict(base=base, source_code=legacy_source, service_ports=(port,),
                stopped_pids=(), actor='codex',
                reason='Gate A 完成后进入 Actor 契约', offline_ack=True)


@pytest.fixture
def setup_v4(tmp_path, monkeypatch, frozen_v5):
    """同一本书的 v4 盘面：只把 manifest 降级成 V4（旧基础只读拒识对象）。"""
    original, sid, _source, legacy_source, _proof = frozen_v5
    base = tmp_path / '输出'
    shutil.copytree(original, base)
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    monkeypatch.setattr(transition, '_code_identity', lambda root: CODE)
    current = WorkflowManifestV5.model_validate_json(
        paths.workflow_manifest_path(base).read_bytes())
    legacy = WorkflowManifestV4(
        task_id=current.task_id, created_at=current.created_at)
    work_units._write(base, paths.workflow_manifest_path(base), legacy)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    return dict(base=base, source_code=legacy_source, service_ports=(port,),
                stopped_pids=(), actor='codex',
                reason='旧基础拒识用例', offline_ack=True, legacy=legacy)


def _apply(setup, plan, *, recovery=False):
    return (transition.recover if recovery else transition.apply)(base=setup['base'],
        transition_id=plan['transition_id'], expected_prepared_sha256=plan['prepared_sha256'], yes=True)


def _record(setup, plan, name):
    return paths.workflow_transition_dir(setup['base'], plan['transition_id']) / name


# ────────────────── v4 源显式拒绝（原 v4→v5 投影族的改写） ──────────────────

def test_registry_registers_only_v5_to_v6():
    """迁移登记只剩 5→6：v4 与 v7 来源都说「没有登记下一版显式迁移」。"""
    assert set(transition._TRANSITION_PAIRS) == {5}
    for version in (4, 6, 7, 8, 9):
        model = {4: WorkflowManifestV4, 6: WorkflowManifestV6,
                 7: WorkflowManifestV7}.get(version)
        if model is None:
            continue
        source = model(task_id='a' * 64, created_at=1)
        with pytest.raises(TerminalState, match='没有登记此来源的下一版显式迁移'):
            transition._pair(source)
    v7 = WorkflowManifestV7(task_id='a' * 64, created_at=1)
    assert transition._manifest(work_units._bytes(v7.model_dump(mode='json'))) == v7


def test_v4_source_is_refused_at_pair_preview_and_ordinary_entry(setup_v4):
    """v4 基础三层拒识：配对、preview 与普通 manifest 读取，全部显式失败。"""
    base = setup_v4['base']
    legacy = setup_v4['legacy']
    legacy_raw = work_units._bytes(legacy.model_dump(mode='json'))
    preview_args = {key: value for key, value in setup_v4.items()
                    if key != 'legacy'}

    with pytest.raises(TerminalState, match='没有登记此来源的下一版显式迁移'):
        transition._pair(legacy)
    with pytest.raises(TerminalState, match='没有登记此来源的下一版显式迁移'):
        transition.preview(**preview_args)
    # 拒绝不产生任何迁移副作用：没有 pointer、没有记录目录、manifest 原字节不动。
    assert not paths.workflow_transition_pending_path(base).exists()
    assert not paths.workflow_transition_dir(base, '0' * 64).parent.exists()
    assert paths.workflow_manifest_path(base).read_bytes() == legacy_raw
    # 普通入口同样只读拒识：v4 不在现役 manifest 读取面（legacy_incompatible）。
    with pytest.raises(TerminalState, match='不支持的工作流 manifest'):
        work_units.workflow_manifest(base)


def _write_v4_predecessor(base: Path, source_code: Path, proof) -> str:
    """旧链形状：一个已闭合的 v4→v5 迁移记录 + 指向它的 v5 manifest。

    人物投影面随旧链退役，这里只重建「前驱已闭合」这一指针事实（projections
    为空正是 narrowed 后该记录的 shape），用来证明它不可重放。
    """
    current = WorkflowManifestV5.model_validate_json(
        paths.workflow_manifest_path(base).read_bytes())
    source = WorkflowManifestV4(
        task_id=current.task_id, created_at=current.created_at)
    source_raw = work_units._bytes(source.model_dump(mode='json'))
    old_id = transition._sha(b'closed-predecessor:' + source_raw)
    target = current.model_copy(update={'transition_ref': WorkflowTransitionRefV1(
        transition_id=old_id, source_manifest_sha256=transition._sha(source_raw))})
    target_raw = work_units._bytes(target.model_dump(mode='json'))
    core_proof = WorkflowTransitionProofV1.model_validate({
        **proof.model_dump(mode='json'),
        'accepted_books': {
            sid: {key: value for key, value in row.model_dump(mode='json').items()
                  if key != 'closure_sha256'}
            for sid, row in proof.accepted_books.items()}})
    previous = WorkflowTransitionPreparedV1(
        transition_id=old_id,
        source_manifest_json=source_raw.decode('utf-8'),
        source_manifest_sha256=transition._sha(source_raw),
        target_manifest_json=target_raw.decode('utf-8'),
        target_manifest_sha256=transition._sha(target_raw),
        source_code_directory=str(REPO), source_code_version=CODE,
        target_code_version=CODE,
        preserved_files=transition._inventory(base), projections={},
        source_proof=core_proof, service_ports=(1,), stopped_pids=(),
        actor='codex', reason='给定旧迁移闭合记录，测试旧指针不可重放',
        prepared_at=1)
    record_dir = paths.workflow_transition_dir(base, old_id)
    transition._immutable(base, record_dir / 'prepared.json', previous)
    digest = transition._sha(work_units._bytes(previous.model_dump(mode='json')))
    transition._immutable(base, record_dir / 'completed.json',
                          WorkflowTransitionCompletedV1(
                              transition_id=old_id, prepared_sha256=digest,
                              source_manifest_sha256=previous.source_manifest_sha256,
                              target_manifest_sha256=previous.target_manifest_sha256,
                              completed_at=2))
    work_units._write(base, paths.workflow_manifest_path(base), target)
    transition._immutable(base, paths.workflow_transition_pending_path(base),
                          WorkflowTransitionPendingV1(
                              transition_id=old_id, prepared_sha256=digest))
    return old_id


@pytest.mark.parametrize('fault', ['none', 'old_completed_missing', 'old_pointer_sha',
                                   'source_bytes'])
def test_v4_predecessor_pointer_is_never_replayed(setup, frozen_v5, fault):
    """v4→v5 前驱指针不可重放：旧基础按 legacy_incompatible 只读拒识。"""
    base = setup['base']
    _base, sid, _source, legacy_source, proof = frozen_v5
    previous_id = _write_v4_predecessor(base, legacy_source, proof)
    pointer = paths.workflow_transition_pending_path(base)

    if fault == 'old_completed_missing':
        (paths.workflow_transition_dir(base, previous_id) / 'completed.json').unlink()
    elif fault == 'old_pointer_sha':
        value = json.loads(pointer.read_bytes())
        value['prepared_sha256'] = 'f' * 64
        pointer.write_bytes(work_units._bytes(value))
    elif fault == 'source_bytes':
        path = paths.workflow_manifest_path(base)
        path.write_bytes(path.read_bytes() + b'\n')
    # 用例自己造成的漂移先落定：拒绝必须原样保留这些字节，一个都不修。
    manifest_before = paths.workflow_manifest_path(base).read_bytes()
    pointer_before = pointer.read_bytes()

    with pytest.raises(TerminalState, match='没有登记此来源的下一版显式迁移'):
        transition.preview(**setup)
    with pytest.raises(TerminalState, match='没有登记此来源的下一版显式迁移'):
        transition.require_transition_completed(
            base, WorkflowManifestV5.model_validate_json(manifest_before),
            manifest_before)
    # 普通入口同样拒识：旧书只读，不开新迁移、不改写盘面。
    with pytest.raises(TerminalState):
        work_units.workflow_manifest(base)
    assert paths.workflow_manifest_path(base).read_bytes() == manifest_before
    assert pointer.read_bytes() == pointer_before
    journals = {path.name for path in
                paths.workflow_transition_dir(base, previous_id).parent.iterdir()}
    assert journals == {previous_id}, '拒绝不得另建迁移记录'


def test_prepared_v2_forbids_any_projection(setup):
    """v5→v6 不重编译任何已验收产物：prepared V2 钉死 projections 为空。"""
    plan = transition.preview(**setup)
    prepared, digest = transition._prepared(setup['base'], plan['transition_id'])
    assert prepared.schema_version == 2 and prepared.projections == {}
    assert digest == plan['prepared_sha256']
    tampered = prepared.model_copy(update={'projections': {
        SID: WorkflowTransitionProjectionV1(
            relative_path=f'书名_{SID}/stage2/blueprint_character_projection.json',
            source_json=None, source_sha256=None,
            target_json='{}', target_sha256='f' * 64,
            characters_sha256='a' * 64, main_text_sha256='b' * 64)}})
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match='不允许改写原人物投影'):
        type(prepared).model_validate(tampered.model_dump(mode='json'))


# ───────────────────── v5→v6 主路径与崩溃恢复 ─────────────────────

@pytest.mark.parametrize('window', ['none', 'before_cas', 'after_cas',
                                    'before_completed', 'after_completed'])
def test_v5_to_v6_retains_all_stage4_stage5_sources_and_recovers_exactly(
        setup, frozen_v5, monkeypatch, window):
    base = setup['base']
    before = transition._inventory(base)
    plan = transition.preview(**setup)
    prepared, digest = transition._prepared(base, plan['transition_id'])
    assert prepared.schema_version == 2 and prepared.projections == {}
    assert prepared.source_proof.accepted_books[frozen_v5[1]].closure_sha256
    assert prepared.source_proof.counts['admissions'] == 1
    assert set(prepared.source_proof.accepted_books) == {frozen_v5[1]}
    assert any('/stage4/' in key for key in before)
    assert any('/stage5/' in key for key in before)
    if window != 'none':
        with monkeypatch.context() as crash:
            if window.endswith('cas'):
                original = transition._cas_manifest
                def interrupted(base, value):
                    if window == 'after_cas': original(base, value)
                    raise OSError('模拟迁移中断')
                crash.setattr(transition, '_cas_manifest', interrupted)
            else:
                original = transition._immutable
                def interrupted(base, path, value):
                    if path.name != 'completed.json' or window == 'after_completed':
                        original(base, path, value)
                    if path.name == 'completed.json': raise OSError('模拟迁移中断')
                crash.setattr(transition, '_immutable', interrupted)
            with pytest.raises(OSError, match='模拟'):
                _apply(setup, plan)
    result = _apply(setup, plan, recovery=True)
    assert result['state'] == 'completed'
    target_raw = paths.workflow_manifest_path(base).read_bytes()
    assert target_raw == prepared.target_manifest_json.encode('utf-8')
    # 闭合后的 v6 载体由迁移器自己的消费者入口核验；普通 manifest 读取面只认
    # v7/v8/v9（v6 是历史契约），这正是 narrowed 后「迁移完成 ≠ 现役可写」。
    target = transition._manifest(target_raw)
    assert isinstance(target, WorkflowManifestV6)
    assert (target.task_id, target.created_at) == (frozen_v5[2].task_id,
                                                   frozen_v5[2].created_at)
    transition.require_transition_completed(base, target, target_raw)
    with pytest.raises(TerminalState, match='不支持的工作流 manifest'):
        work_units.require_workflow(base)
    assert transition._inventory(base) == before
    assert _apply(setup, plan) == result
    # 旧预算、profiles、决定和付费回答一个字节也不改。
    registry = work_units._read(base, paths.work_unit_registry_path(base, frozen_v5[1]),
                                work_units.WorkUnitRegistryV1)
    assert registry.unit_ids
    spent = 0
    for unit_id in registry.unit_ids:
        ledger = WorkUnitLedgerV2.model_validate_json(
            paths.work_unit_dir(base, frozen_v5[1], unit_id).joinpath(
                'ledger.json').read_bytes())
        assert ledger.task_id == frozen_v5[2].task_id
        assert ledger.base_content_limit == 4
        assert ledger.content_limit - len(ledger.turns) <= 4
        if ledger.turns:
            assert ledger.content_limit - len(ledger.turns) < 4, (
                '用过额度的工作单元必须仍带着已用轮次，迁移不得重置预算')
            spent += len(ledger.turns)
    assert spent == 1, '合成来源里恰有一轮已购回答，迁移后原样保留'


def test_completed_replay_does_not_reopen_or_delete_new_downstream(setup):
    plan = transition.preview(**setup)
    result = _apply(setup, plan)
    book = next(iter(paths.iter_novel_dirs(setup['base'])))
    target = book / 'stage4' / 'blind_candidate.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'new downstream after completed')
    after = transition._inventory(setup['base'])
    assert _apply(setup, plan, recovery=True) == result
    assert transition._inventory(setup['base']) == after


@pytest.mark.parametrize('drift', ['manifest', 'ledger', 'profile', 'prepared',
                                   'wrong_sha', 'pending', 'missing_manifest'])
def test_apply_refuses_any_third_state_without_overwriting_sources(setup, drift):
    base = setup['base']
    plan = transition.preview(**setup)
    if drift == 'manifest':
        path = paths.workflow_manifest_path(base)
        path.write_bytes(path.read_bytes() + b' ')
    elif drift == 'missing_manifest':
        paths.workflow_manifest_path(base).unlink()
    elif drift in {'ledger', 'profile'}:
        path = paths.service_dir(base) / ('ledger.jsonl' if drift == 'ledger'
                                          else 'original_profiles.json')
        path.write_bytes(path.read_bytes() + b' ')
    elif drift == 'prepared':
        _record(setup, plan, 'prepared.json').write_bytes(b'{}')
    elif drift == 'pending':
        path = paths.workflow_transition_pending_path(base)
        raw = json.loads(path.read_bytes())
        raw['transition_id'] = 'f' * 64
        path.write_bytes(work_units._bytes(raw))
    else:
        plan['prepared_sha256'] = 'f' * 64
    before = {str(p.relative_to(base)): p.read_bytes()
              for p in base.rglob('*') if p.is_file()}
    with pytest.raises(TerminalState):
        _apply(setup, plan)
    assert {str(p.relative_to(base)): p.read_bytes()
            for p in base.rglob('*') if p.is_file()} == before
    assert not _record(setup, plan, 'completed.json').exists()


@pytest.mark.parametrize('relative', [
    # v5 来源合法持有 Stage2/4/5 已购回答与历史 ⇒ 不阻断。
    'stage2/chapter_blueprints.json', 'stage2/chapter_blueprints.INVALID.json',
    'stage2/_rounds/chapter_blueprints.r1.json', 'stage2/knowledge_links.json',
    'stage4/empty/', '_stage4_runs/old/checkpoints/1.json',
    '_service/runs/2042/stage2/old/blueprint/checkpoints/1.json',
    '_service/runs/2042/stage2/old/blueprint/response.txt',
    # 更晚层（stage6/7 及退役运行目录）一个也不认 ⇒ 阻断。
    'stage6/hook.json', 'stage6/prepare.INVALID.json',
    'stage6/rounds/0001.json', '_stage6_runs/old/prepare/run.json',
    'stage7/name_map.json', 'stage7/name_map.INVALID.json',
    '_service/runs/2042/stage7/old/response.txt',
])
def test_v5_retains_stage2_4_5_sources_and_refuses_only_later_downstream(
        setup, relative):
    base = setup['base']
    book = next(iter(paths.iter_novel_dirs(base)))
    path = (base if relative.startswith('_service/') else book) / relative
    if relative.endswith('/'):
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{}')
    blocked = relative.startswith(('stage6', 'stage7', '_stage6_runs', '_stage7_runs')) \
        or '/stage7/' in relative or '/stage6/' in relative
    before = paths.workflow_manifest_path(base).read_bytes()
    if blocked:
        with pytest.raises(TerminalState, match='下游'):
            transition.preview(**setup)
        assert paths.workflow_manifest_path(base).read_bytes() == before
        assert not paths.workflow_transition_pending_path(base).exists()
    else:
        assert transition.preview(**setup)['state'] == 'prepared'


def test_source_reader_rejects_missing_paid_registry_even_without_formal_downstream(setup):
    base = setup['base']
    orphan = paths.service_runs_dir(base) / 'unknown' / 'work_units' / ('f' * 64) / 'response.txt'
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b'paid response without owner registry')
    with pytest.raises(TerminalState, match='来源代码不能证明'):
        transition.preview(**setup)


def test_downstream_work_unit_node_blocks_without_any_downstream_artifact(setup):
    base = setup['base']
    path = next(paths.work_units_dir(base, SID).glob('*/ledger.json'))
    raw = json.loads(path.read_bytes())
    raw['node'] = 'stage6.hook'
    path.write_bytes(work_units._bytes(raw))
    with pytest.raises(TerminalState, match='下游'):
        transition.preview(**setup)


def test_preview_interrupted_before_pointer_recovers_same_immutable_prepared(
        setup, monkeypatch):
    original = transition._immutable
    with monkeypatch.context() as crash:
        def fail(base, path, value):
            if path == paths.workflow_transition_pending_path(base):
                raise OSError('模拟 pointer 尚未发布')
            original(base, path, value)
        crash.setattr(transition, '_immutable', fail)
        with pytest.raises(OSError, match='模拟'):
            transition.preview(**setup)
    path = next(paths.workflow_transition_dir(setup['base'], '0' * 64).parent.glob(
        '*/prepared.json'))
    value, digest = transition._prepared(setup['base'], path.parent.name)
    assert not paths.workflow_transition_pending_path(setup['base']).exists()
    with pytest.raises(TerminalState, match='不能另建迁移'):
        transition.preview(**setup)
    plan = {'transition_id': value.transition_id, 'prepared_sha256': digest}
    assert _apply(setup, plan, recovery=True)['state'] == 'completed'


@pytest.mark.parametrize('fault', ['uncommitted_tail', 'unclosed_admission',
                                   'orphan_event', 'pending_review', 'bool_only'])
def test_source_proof_never_treats_unknown_or_old_gate_bool_as_accepted_and_drained(
        setup, fault):
    base = setup['base']
    if fault == 'uncommitted_tail':
        with admission.admission_path(base).open('ab') as stream:
            stream.write(b'{}')
    elif fault == 'unclosed_admission':
        admission.open_gate(base, 'unclosed-owner', SID)
    elif fault == 'orphan_event':
        (paths.service_dir(base) / 'jobs.jsonl').write_bytes(b'{}\n')
    else:
        path = approvals._path(base, SID)
        raw = json.loads(path.read_bytes())
        if fault == 'pending_review':
            raw['stage2_reviews']['stage2.global']['decision'] = 'uncertain'
            raw['stage2_review_history'][-1]['decision'] = 'uncertain'
        else:
            raw['stage2_reviews'] = {}
            raw['stage2_review_history'] = []
            raw['gate']['s2'] = True
        path.write_bytes(work_units._bytes(raw))
    with pytest.raises(TerminalState):
        transition.preview(**setup)
    assert not paths.workflow_transition_pending_path(base).exists()


def test_live_owner_is_rejected_by_real_cross_process_lock(setup):
    script = ('from novel.orchestrator.admission import owner_lease\nimport sys\n'
              'with owner_lease(sys.argv[1], "old-parent"):\n'
              ' print("locked", flush=True)\n sys.stdin.readline()\n')
    process = subprocess.Popen([sys.executable, '-B', '-c', script, str(setup['base'])],
        cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(TerminalState, match='锁仍被持有'):
            transition.preview(**setup)
    finally:
        process.communicate('\n', timeout=15)
    assert process.returncode == 0
    assert transition.preview(**setup)['state'] == 'prepared'


def test_live_service_pid_and_port_refuse_before_prepared(setup):
    with pytest.raises(TerminalState, match='仍存活'):
        transition.preview(**{**setup, 'stopped_pids': (os.getpid(),)})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', setup['service_ports'][0]))
        sock.listen()
        with pytest.raises(TerminalState, match='端口'):
            transition.preview(**setup)
    assert not paths.workflow_transition_pending_path(setup['base']).exists()


def test_prepared_or_target_without_completed_fences_all_ordinary_writes(setup):
    base = setup['base']
    plan = transition.preview(**setup)
    prepared, _ = transition._prepared(base, plan['transition_id'])
    transition._cas_manifest(base, prepared)
    # CAS 后 manifest 是 v6 目标：普通入口连版本都不认，更不可能在未闭合的
    # 迁移上继续写（「尚未完成」由迁移围栏表达，版本闸先一步显式拒绝）。
    for operation in (work_units.require_workflow, work_units.check_workflow_write,
                      work_units.initialize_workflow):
        with pytest.raises(TerminalState):
            operation(base)
    _record(setup, plan, 'completed.json').write_bytes(b'{"schema_version":99}')
    with pytest.raises(TerminalState):
        work_units.workflow_manifest(base)


def test_cli_preview_apply_and_completed_recover_are_reachable(setup, capsys):
    base = setup['base']
    assert cli.main(['workflow', 'transition', 'preview', '--base', str(base),
        '--source-code', str(setup['source_code']), '--port', str(setup['service_ports'][0]),
        '--offline-ack',
        '--actor', 'codex', '--reason', '明确推进合成 Gate B']) == 0
    plan = json.loads(capsys.readouterr().out)
    args = ['--base', str(base), '--transition-id', plan['transition_id'],
            '--prepared-sha256', plan['prepared_sha256']]
    assert cli.main(['workflow', 'transition', 'apply', *args]) == 2
    capsys.readouterr()
    assert cli.main(['workflow', 'transition', 'apply', *args, '--yes']) == 0
    applied = json.loads(capsys.readouterr().out)
    assert cli.main(['workflow', 'transition', 'recover', *args, '--yes']) == 0
    assert json.loads(capsys.readouterr().out) == applied


def test_ordinary_workflow_rejects_v4_and_initializes_only_clean_v9(setup, tmp_path):
    raw = paths.workflow_manifest_path(setup['base']).read_bytes()
    with pytest.raises(TerminalState):
        work_units.require_workflow(setup['base'])
    assert paths.workflow_manifest_path(setup['base']).read_bytes() == raw
    current = work_units.initialize_workflow(tmp_path / 'clean')
    assert current.schema_version == 9 and current.transition_ref is None
    assert current.contract_version == 'stage4-presentation-ir-v1'
    assert current.artifact_versions.characters == 3
    assert current.artifact_versions.chapter_blueprints == 11
    assert current.artifact_versions.writer_context == 10
    assert current.artifact_versions.stage6_prepare == 1


def test_unreadable_subtree_and_hardlink_never_become_complete_inventory(
        setup, monkeypatch):
    original = os.scandir
    blocked = paths.service_dir(setup['base'])
    with monkeypatch.context() as denied:
        def fail(path):
            if Path(path) == blocked:
                raise PermissionError('模拟子目录读取失败')
            return original(path)
        denied.setattr(transition.os, 'scandir', fail)
        with pytest.raises(TerminalState, match='无法完整列举'):
            transition.preview(**setup)
    source = paths.service_dir(setup['base']) / 'ledger.jsonl'
    target = setup['base'] / 'ledger-alias'
    os.link(source, target)
    with pytest.raises(TerminalState, match='硬链接'):
        transition.preview(**setup)
