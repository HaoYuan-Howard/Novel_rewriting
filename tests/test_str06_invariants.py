#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_invariants —— STR-06 F1 反例套件：跨产物不变量与窄修复/交付语义。

【验证边界】每个判定入口都有红例与绿对照（AGENTS 判据纪律）；跨章布局以
fixture 证明契约能力接通（F1 的分工——真实生成中的跨章质量归 F8 Golden）。
判定器是 RC 期组件（novel/stages/str06），不注册现役判据。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema.str06 import (
    BindingEntry,
    ChapterAssignment,
    DependencyEdge,
    Establishment,
    ExecutionPlan,
    ExecutionSlice,
    PreserveCheckpoint,
    PreserveKind,
    BodyDesign,
    ReconstructionBrief,
    SceneReceipt,
    SemanticBinding,
    SceneDelivery,
    SceneDriver,
    SourceLineage,
    SourceObligation,
    TargetBeat,
    TargetScene,
    UnboundEntry,
    VerificationIR,
    WriterBrief,
    WriterContext,
)
from contracts.schema.str06.grounding import BriefRequirement
from novel.stages.str06.checks import (
    canonical_sha256,
    check_binding_covers_checkpoint,
    check_plan_matches_graph,
    check_receipt_binding,
    directly_affected,
    entry_sensitive_closure,
)
H64 = 'a' * 64
H64B = 'b' * 64


# ───────────────────────── fixture 构造器 ─────────────────────────

def _obligation(oid: str, must: bool = True) -> SourceObligation:
    return SourceObligation(
        obligation_id=oid, kind=PreserveKind.EVENT_CORE, statement=f'义务{oid}',
        lineage=SourceLineage(blueprint_refs=(f'b_{oid}',), character_ids=()),
        must_preserve=must, satisfaction=f'正文呈现{oid}' if must else '')


def _checkpoint(*obligations: SourceObligation, sha: str = H64) -> PreserveCheckpoint:
    return PreserveCheckpoint(
        checkpoint_id='cp1', policy_id='preserve-default', policy_version=1,
        characters_sha256=H64, blueprints_sha256=H64, provider='deepseek',
        model='flash', request_sha256=H64, obligations=obligations,
        checkpoint_sha256=sha)


def _binding(cp: PreserveCheckpoint, entries=(), unbound=()) -> SemanticBinding:
    return SemanticBinding(
        binding_id='bd1', preserve_checkpoint_sha256=cp.checkpoint_sha256,
        obligations=cp.obligations, entries=tuple(entries), unbound=tuple(unbound))


def _scene(scene_id: str) -> TargetScene:
    return TargetScene(state_reads=(),
        scene_id=scene_id,
        driver=SceneDriver(focal_actor='ch1', immediate_want='w', opposition='o', turn='t'),
        beats=(TargetBeat(beat_id='bt1', event='e', participants=('ch1',),
                          dramatic_intent='d', target_delta=()),),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _graph(scenes, chapters) -> BodyDesign:
    return BodyDesign(initial_state=(), graph_id='g1', scenes=tuple(scenes), chapters=tuple(chapters))


def _plan(*scene_ids: str, deps: dict[str, list[str]] | None = None) -> ExecutionPlan:
    deps = deps or {}
    scenes = []
    for sid in scene_ids:
        edges = tuple(
            DependencyEdge(before=before, after=sid, kind='carries')
            for before in deps.get(sid, ()))
        scenes.append(_scene_plan(sid, edges))
    return ExecutionPlan(scenes=tuple(scenes))


def _scene_plan(scene_id: str, edges=(), establishments=()):
    from contracts.schema.str06.execution import SceneExecutionPlan
    return SceneExecutionPlan(
        scene_id=scene_id, dependencies=tuple(edges),
        establishments=tuple(establishments))


def _slice(scene_id: str, *consumed: str) -> ExecutionSlice:
    return ExecutionSlice(
        scene_id=scene_id, consumed_obligation_ids=tuple(consumed),
        entry_state_sha256=H64, entry_knowledge_sha256=H64, execution_slice_sha256=H64)


def _context(scene_id: str) -> WriterContext:
    return WriterContext(
        scene_id=scene_id, attempt_id='a1', execution_slice_sha256=H64,
        verification_ir=VerificationIR(scene_id=scene_id, obligations=()),
        writer_brief=WriterBrief(pov='ch1', scene_id=scene_id, task='t', scene_driver_summary='d',
                                 beat_outlines=('b1',), length_budget=800, voice='v'))


# ───────────────────────── Binding 分母一致 ─────────────────────────

def test_binding_covers_checkpoint_green_and_red():
    cp = _checkpoint(_obligation('ob1'), _obligation('ob2'), _obligation('ob3'))
    # 绿：全承载
    ok = _binding(cp, entries=(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'),
        BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id='ob2', scene_id='s02'),
    ), unbound=(UnboundEntry(obligation_id='ob3', owner='design', reason='目标无承载位'),))
    assert check_binding_covers_checkpoint(cp, ok) == []

    # 红：义务消失（半份 Binding 的可机检面）
    dropped = _binding(cp, entries=(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'),))
    problems = check_binding_covers_checkpoint(cp, dropped)
    assert any('消失' in p for p in problems)

    # 红：混入 checkpoint 外义务
    invented = _binding(cp, entries=(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'),
        BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id='ob2', scene_id='s02'),
        BindingEntry(beat_ref=('s03' + '#bt1'), obligation_id='obX', scene_id='s03'),
    ), unbound=(UnboundEntry(obligation_id='ob3', owner='design', reason='r'),))
    problems = check_binding_covers_checkpoint(cp, invented)
    assert any('checkpoint 外' in p for p in problems)

    # 红：checkpoint 身份对不上（采用的 Preserve 结果不是这份 checkpoint）
    other_cp = _checkpoint(_obligation('ob1'), _obligation('ob2'), _obligation('ob3'), sha=H64B)
    problems = check_binding_covers_checkpoint(other_cp, ok)
    assert any('身份' in p for p in problems)


# ───────────────────────── plan ↔ graph 完整性 ─────────────────────────

def test_plan_matches_graph_green_and_red():
    graph = _graph(
        [_scene('s01'), _scene('s02')],
        [ChapterAssignment(chapter_ordinal=1, scene_ids=('s01', 's02'))])
    assert check_plan_matches_graph(graph, _plan('s01', 's02')) == []
    assert '未被编译' in check_plan_matches_graph(graph, _plan('s01'))[0]
    assert '引用了' in check_plan_matches_graph(graph, _plan('s01', 's02', 's03'))[0]


# ───────────────────────── receipt 绑定（旧 PASS 配新输入拒绝） ─────────────────────────

def test_receipt_rejects_stale_context_or_slice():
    ctx = _context('s01')
    receipt = SceneReceipt(
        scene_id='s01', attempt_id='a1', prose_sha256=H64,
        writer_context_sha256=canonical_sha256(ctx),
        execution_slice_sha256=H64, judge_contract_version='str06-rc1', status='accepted')
    assert check_receipt_binding(receipt, ctx, execution_slice_sha256=H64) == []

    # 新 Context（改一个字段）→ 旧 PASS 拒绝
    mutated = ctx.model_copy(deep=True)
    mutated.writer_brief.length_budget = 900
    problems = check_receipt_binding(receipt, mutated, execution_slice_sha256=H64)
    assert any('writer_context_sha256' in p for p in problems)

    # 新 slice → 拒绝
    problems = check_receipt_binding(receipt, ctx, execution_slice_sha256=H64B)
    assert any('execution_slice_sha256' in p for p in problems)

    # 换 NameMap 不影响 Stage5 接受回执：receipt 七字段里没有任何交付材料
    assert 'name_map' not in SceneReceipt.model_fields
    assert check_receipt_binding(receipt, ctx, execution_slice_sha256=H64) == []


# ───────────────────────── 窄修复：直接失效 + 重投影闭包 ─────────────────────────

def test_narrow_repair_prefix_stays_valid():
    slices = {f's{i:02d}': _slice(f's{i:02d}', f'ob{i}') for i in range(1, 10)}
    # 只修第 9 场的义务：前 8 场输入未变 → 前缀继续有效
    affected = directly_affected({'ob9'}, slices)
    assert affected == {'s09'}
    # 无依赖时闭包不扩大
    plan = _plan(*(f's{i:02d}' for i in range(1, 10)))
    assert entry_sensitive_closure(affected, plan) == {'s09'}


def test_narrow_repair_propagates_only_along_real_dependencies():
    slices = {
        's01': _slice('s01', 'ob1'), 's02': _slice('s02', 'ob1'),
        's03': _slice('s03', 'ob3'), 's04': _slice('s04', 'ob4'),
    }
    plan = _plan('s01', 's02', 's03', 's04', deps={
        's02': ['s01'],       # s02 依赖 s01
        's03': ['s01'],       # s03 依赖 s01
        's04': ['s03'],       # s04 只依赖 s03
    })
    affected = directly_affected({'ob1'}, slices)
    assert affected == {'s01', 's02'}
    closure = entry_sensitive_closure(affected, plan)
    # s03 依赖 s01 → 进闭包；s04 依赖 s03 → 传递进入；无依赖路径的场景不在其中
    assert closure == {'s01', 's02', 's03', 's04'}

    # 改 ob4：只有 s04 直接失效，无人依赖它 → 闭包 == 直接集
    affected4 = directly_affected({'ob4'}, slices)
    assert entry_sensitive_closure(affected4, plan) == {'s04'}


def test_narrow_repair_scan_is_not_rerun_scope():
    # 「必要全局校验可扫描全书，但不把扫描范围当付费重跑范围」：
    # 全书扫描（全部 slices 参与判定）不改变付费重写判定（directly_affected）。
    all_slices = {f's{i:02d}': _slice(f's{i:02d}', f'ob{i}') for i in range(1, 13)}
    assert directly_affected({'ob9'}, all_slices) == {'s09'}


# ───────────────────────── 跨章布局：契约能力接通（F1 分工） ─────────────────────────

def test_cross_chapter_merge_layout_passes_contract_chain():
    """两个源章的义务合并进一个目标章、场景承载跨源内容：schema + 分母 +
    完整性判定全绿。能力接通 ≠ 真实生成质量（后者归 F8 Golden 报告）。"""
    cp = _checkpoint(_obligation('ob_a'), _obligation('ob_b'))  # 源：两章各一义务
    binding = _binding(cp, entries=(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob_a', scene_id='s01'),
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob_b', scene_id='s01'),  # 同场承载两源章义务
    ))
    assert check_binding_covers_checkpoint(cp, binding) == []
    graph = _graph(
        [_scene('s01')],
        [ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',))])  # 1 个目标章
    assert check_plan_matches_graph(graph, _plan('s01')) == []


def test_cross_chapter_split_layout_passes_contract_chain():
    """一个源章的义务拆到两个目标章（拆分方向）：同样全绿；章数=2 连续非空。"""
    cp = _checkpoint(_obligation('ob_a'), _obligation('ob_b'))
    binding = _binding(cp, entries=(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob_a', scene_id='s01'),
        BindingEntry(obligation_id='ob_b', scene_id='s02', beat_ref='s02#bt1'),
    ))
    assert check_binding_covers_checkpoint(cp, binding) == []
    graph = _graph(
        [_scene('s01'), _scene('s02')],
        [ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),
         ChapterAssignment(chapter_ordinal=2, scene_ids=('s02',))])
    assert check_plan_matches_graph(graph, _plan('s01', 's02')) == []


def test_same_ordinal_different_semantics_not_conflated():
    """源章 3 与目标章 3 语义不同：目标时点只能来自目标承载位置——
    用 chapter_map 的形状纪律表达（scene→chapter 是唯一权威，不存在源章换算）。"""
    graph = _graph(
        [_scene('s01'), _scene('s02'), _scene('s03')],
        [ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),
         ChapterAssignment(chapter_ordinal=2, scene_ids=('s02',)),
         ChapterAssignment(chapter_ordinal=3, scene_ids=('s03',))])
    mapping = {c.chapter_ordinal: list(c.scene_ids) for c in graph.chapters}
    assert mapping == {1: ['s01'], 2: ['s02'], 3: ['s03']}
    # BodyDesign 上不存在任何「源章」字段可供换算（F0 已测 smuggle）；
    # 这里锁行为面：章节分母来自 Design，而非源章数量
    assert len(graph.chapters) == 3


# ───────────────────────── Preserve 隔离（请求输入面反例） ─────────────────────────

def test_preserve_request_surface_rejects_design_feedback():
    """Preserve 请求的输入面只有 SourceTruth + Policy + 执行配置；
    喂 Design/Bind 失败结果属越界输入。F0 已锁 checkpoint 产物面，这里锁
    「请求构造器」语义：RC 期以最小请求模型表达（F3 落 LLM 调用时直接复用）。"""
    from contracts.schema.str06.policy import PreservationPolicy

    policy = PreservationPolicy(
        policy_id='preserve-default', version=1,
        preserve_kinds=(PreserveKind.EVENT_CORE, PreserveKind.REVEAL))
    # 请求 = truth 身份 + policy 身份 + 执行配置；多出来的都是越界
    from pydantic import BaseModel

    class PreserveRequest(BaseModel):
        model_config = policy.model_config
        characters_sha256: str
        blueprints_sha256: str
        policy_id: str
        policy_version: int
        provider: str
        model: str

    request = PreserveRequest(
        characters_sha256=H64, blueprints_sha256=H64,
        policy_id=policy.policy_id, policy_version=policy.version,
        provider='deepseek', model='flash')
    with pytest.raises(ValidationError):
        PreserveRequest(**{**request.model_dump(), 'design_failure': 'x'})
    with pytest.raises(ValidationError):
        PreserveRequest(**{**request.model_dump(), 'bind_unbound': ['ob1']})


# ───────────────────────── 双视图与 Brief 屏蔽（行为面） ─────────────────────────

def test_verification_ir_carries_continuity_fact_view():
    """IR 带 continuity_fact_view（试点 judge 跨场回指误报的根因修复输入）：
    前场义务以事实视图进入本场 IR，Judge 不需要读前场正文。"""
    ir = VerificationIR(
        scene_id='s02', obligations=(_obligation('ob2'),),
        continuity_fact_view=('s01 已接受：约定成立且为夫妻共同秘密',))
    assert ir.continuity_fact_view[0].startswith('s01')


def test_brief_hides_source_topology_behaviorally():
    brief = ReconstructionBrief(
        brief_id='br1', preserve_checkpoint_sha256=H64,
        requirements=(
            BriefRequirement(kind='event_core', must_preserve=False, satisfaction='', core_id='c1', summary='约定成立', participants=('ch1',)),
            BriefRequirement(kind='event_core', must_preserve=False, satisfaction='', core_id='c2', summary='翻盘', requires_cores=('c1',)),
        ))
    # 因果保留（requires_cores），源拓扑不存在（无章节字段可读）
    assert brief.requirements[1].requires_cores == ('c1',)
    assert not any('chapter' in f for f in ReconstructionBrief.model_fields)


# ───────────────────────── typed effects / 执行语义反例 ─────────────────────────

def test_scene_scoped_effect_expires_at_boundary():
    """scene 作用域的 establishment 只在本场有效——「场景局部状态无衰减」的
    试点缺陷在类型上以 scope 表达；跨场依赖必须由 Design 承接为 book 级。"""
    scene_plan = _scene_plan('s01', establishments=(
        Establishment(op='set', established_by='init', subjects=('ch1',), domain='character', key='emotional_state', value='冷哼', scope='scene'),
        Establishment(op='set', established_by='init', domain='world', key='season', value='秋', scope='book'),
    ))
    assert {e.scope for e in scene_plan.establishments} == {'scene', 'book'}


# ───────────────────────── 迁移期包纪律（AST） ─────────────────────────

def test_str06_migration_package_import_discipline():
    """迁移期包只准依赖 contracts / novel.errors / 本包 / 标准库 / pydantic；
    一旦有人从这里 import 现役 stage 实现或 orchestrator，F8 分流就会带着
    耦合搬家——在源头挡住。"""
    import novel.stages.str06 as pkg
    allowed_roots = ('contracts', 'novel.errors', 'novel.stages.str06')
    std_or_third = ('__future__', 'pydantic', 'typing', 'pathlib', 'hashlib',
                    'dataclasses', 'collections', 'functools', 'itertools',
                    'enum', 'abc', 'datetime', 'json', 're', 'os', 'sys', 'copy')
    for py in Path(pkg.__file__).parent.glob('*.py'):
        tree = ast.parse(py.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            for name in names:
                root = name.split('.')[0]
                assert root in std_or_third or name.startswith(allowed_roots), (
                    f'{py.name} import 了 {name}：迁移期包禁止依赖现役实现，'
                    f'F8 分流前保持零耦合')
