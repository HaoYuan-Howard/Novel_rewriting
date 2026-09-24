#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candidates —— STR-06 候选节点、产物与 workflow／epoch 身份登记（F7a-1）。

【为什么在接设施之前先定义身份】
F7 要把隔离链接到正式 Store、ledger、许可与预算设施。若边接边定义身份，候选
节点就会借旧节点名义拿许可、绕过版本检查，或靠改全局注册表打开隔离入口——
三条都是计划明令禁止的。因此本模块在**任何接线之前**把候选面固化成纯数据：
节点表（paid／lifecycle／flags／produces／免费复检／恢复入口）、产物表（家族／
版本／唯一 writer）、workflow 与 execution epoch 身份。F8 激活时直接翻本表，
不为切换再定义第二套。

【三条硬边界（与 families 同源，S-04）】
① 本表不进 `contracts.schema.ARTIFACT_SPECS`、不注册 `NODES`、不进判据注册表；
   生产入口到 F8 仍用现役注册表，候选身份不借用旧节点。
② 本模块只依赖标准库与本子包（contracts 层禁止 import novel，I-03）；
   `code_version` 等运行期事实由调用方显式传入，不在此读盘。
③ 候选节点 id 一律 `str06.` 前缀，结构上不可能与现役 `stageN.<name>` 节点键
   撞命名空间；产物 rel 是三级路径，同样进不了现役两级注册面。

【两种 owner 粒度（I-25 的「唯一 writer」指哪一种）】
`families.py` 的 `writer_owner` 是**语义 owner**（stage2.blueprint…）；本表产物
条的 `writer` 是**执行该 paid/free 节点的候选节点 id**（str06.stage2.source…）。
一个 artifact 只有一个执行 writer。语义 owner 到执行 owner 的收敛（如
body_design 的产出不单独落盘，经 `publish_graph` 与 Hook 合并发布，唯一
publisher 是 hook_design 节点）在本表 `produces` 里显式登记；F8 激活注册表时
合并两表，不留第二份真相。

【版本规则（I-16：每类信息只有一个 authoritative source）】
产物条的 `schema_version` 是该产物**当前盘上形状**的 RC 标签。模型自带
`contract_version` 字段的（Characters v4、Brief、Binding、Plan、Graph、BookIR、
HookContext、ChapterReceipt、NameMap v5），表值必须与模型声明**逐字相等**，
由 `tests/test_str06_candidates.py` 对账；模型未声明的（Blueprints v12 与
自由形态派生记录、正文），表值登记形状定格的批次，F8 激活时必须把声明补进
模型或注册表——这是登记在案的 F8 义务，不是默许的漂移。

【epoch 语义（对齐 `novel/AGENTS.md` 与正式 ExecutionEpoch）】
身份未漂移不开新 epoch；`code_drift`／`contract_drift`／`repair` 是仅有的滚动
理由，且前两者要求对应身份**确实变了**（声明漂移而身份未变 = 拒绝）。epoch
滚动不重置任何累计额度：书级总闸按 sid 持久，跨 epoch 连续（Q-10）。候选链
不续 provider session——`session_run_id` 是本次运行的会话身份，历史采用显式
不继承旧 session（F7a-3）。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final, NamedTuple

from contracts.schema.str06.book_ir import WriterBookIR
from contracts.schema.str06.design import PresentationGraph
from contracts.schema.str06.execution import ExecutionPlan
from contracts.schema.str06.grounding import (
    PreserveCheckpoint,
    ReconstructionBrief,
    SemanticBinding,
)
from contracts.schema.str06.hook import HookContext, HookReceipt
from contracts.schema.str06.naming import NameMapV5
from contracts.schema.str06.receipt import ChapterReceipt
from contracts.schema.str06.source import BlueprintsArtifactV12, CharactersArtifactV4

#: 本子包契约的 RC 版式身份（与 `contracts.schema.str06.STR06_RC_VERSION` 同源；
#: 此处再 import 会形成包内环，故按同一常量字面值登记并由测试对账）。
RC_VERSION: Final = 'str06-rc4'

#: 已知 RC 标签面（版本规则见模块头）。
RC_LABELS: Final = ('str06-rc2', 'str06-rc3', 'str06-rc4')

#: epoch 滚动理由面。`initial` 只允许首开；漂移类要求身份确实变化；`repair`
#: 是 typed 修复的显式 producer 边界（对齐正式 ExecutionEpochReason 的 continue）。
EPOCH_REASONS: Final = ('initial', 'code_drift', 'contract_drift', 'repair')

_EPOCH_ID_RE = re.compile(r'^[0-9a-f]{64}$')
_RUN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,127}$')


class CandidateLifecycle(NamedTuple):
    """候选节点的执行生命周期。

    unit_kind:              付费内容调用的记账单元：'book' 全书一个单元、
                            'chapter' 逐章单元、'strategy_and_chapter' 整书
                            策略调用 + 逐章调用共存（对齐正式 stage4.design）。
    session_scope:          会话作用域标签（executor:book／chapter／strategy/chapter）；
                            候选链不跨运行续 provider session，仅作身份登记。
    executor_content_limit: 本节点每工作单元的付费内容调用上限；**0 = 结构上
                            不发付费调用**（compile／assemble 这类确定性节点），
                            由付费边界机械拒绝，不靠约定。
    recovery_entries:       免费恢复入口（saved_response／historical_adoption／
                            owner_patch／chapter_prefix_replay／same_context_rewrite／
                            deterministic_replay）。
    """

    unit_kind: str
    session_scope: str
    executor_content_limit: int
    recovery_entries: tuple[str, ...]


class CandidateNode(NamedTuple):
    """一个可跑的候选作业单元（F8 激活时翻为正式 NODES 条目）。

    node:         `str06.<stage>.<owner>`；候选命名空间，不进现役 NODES。
    stage:        stage2／stage4／stage5／stage7（Stage6 已退役）。
    owner:        语义 owner（与 families.writer_owner、依赖登记同源）。
    label:        人读的一句话。
    paid:         默认路径是否调 LLM。免费节点由 executor_content_limit=0 结构性保证。
    produces:     本节点写盘的产物 rel（模式见 CandidateArtifact.rel）。
    free_recheck: 免费复检入口（'--check-only' 或 'deterministic-rerun'）。
    flags:        节点身份 flag（改变成本／生命周期的跑法）；入口级 flag
                  （--through／--wire-dir／--body-chapter-limit）不属节点身份。
    paid_flags:   能把免费节点变付费的 flag；现役候选面无此类 flag（空）。
    lifecycle:    执行生命周期（付费节点与免费节点都有，免费节点 limit=0）。
    """

    node: str
    stage: str
    owner: str
    label: str
    paid: bool
    produces: tuple[str, ...]
    free_recheck: str
    lifecycle: CandidateLifecycle
    flags: tuple[str, ...] = ()
    paid_flags: tuple[str, ...] = ()


class CandidateArtifact(NamedTuple):
    """一份候选产物的身份（F8 激活时翻为 ARTIFACT_SPECS／ARTIFACT_PATHS 条目）。

    rel:            书内相对路径；`{nn}` = 两位章序，`{title}` = 书名。
    family:         `STR06_PATH_FAMILIES` 的家族键（路径准入与来源账键规则）。
    form:           'json'（schema 化记录）或 'text'（正文／交付字节）。
    writer:         唯一执行 writer（候选节点 id，I-25）。
    schema_version: 当前盘上形状的 RC 标签（规则见模块头）。
    model:          json 产物的契约模型；自由形态派生记录与正文为 None。
    """

    rel: str
    family: str
    form: str
    writer: str
    schema_version: str
    model: type | None = None


class CandidateEpoch(NamedTuple):
    """候选 workflow 的一段身份稳定的执行窗口。

    epoch_id 由（契约表身份、code_version、ordinal、理由、task_id、
    session_run_id、前一 epoch_id）确定性派生；ordinal 单调递增，链条可验证。
    """

    epoch_id: str
    ordinal: int
    reason: str
    contract_version: str
    code_version: str
    task_id: str
    session_run_id: str
    created_at: int


class CandidateWorkflow(NamedTuple):
    """候选根的 workflow 身份记录（落盘 `runtime/candidate_workflow.json`）。

    绑定 epoch、契约表身份与源输入身份；不含路径（记录随根自定位）。
    `source_identity` 在 F7c 改读正式 Stage1 章数组前是源 JSON 的完整字节
    SHA-256，之后是 Stage1 输入的身份摘要——消费形状变化必须换身份，
    不能沿用旧根的采用结论。
    """

    epoch: CandidateEpoch
    contract_identity: str
    source_identity: str
    created_at: int


# ─── 候选节点表 ────────────────────────────────────────────────────────────────
# 付费节点按 F7b owner runner 拆分；免费节点（compile／assemble）以
# executor_content_limit=0 结构性禁付。恢复入口与正式 NodeLifecycleSpec
# 的 recovery_entries 同构，另加候选链特有的 historical_adoption（F7a-3）。

_PAID_RECOVERY: Final = ('saved_response', 'historical_adoption', 'owner_patch')

STR06_CANDIDATE_NODES: Final[dict[str, CandidateNode]] = {
    'str06.stage2.source': CandidateNode(
        node='str06.stage2.source', stage='stage2', owner='stage2.source',
        label='Stage2 源语义抽取（book＋chNN）与确定性 Blueprint 编译',
        paid=True,
        produces=('stage2/blueprints/chapter_blueprints.json',
                  'stage2/compiled/input_snapshots.json',
                  'stage2/acceptance/fold.json'),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='strategy_and_chapter', session_scope='executor:strategy/chapter',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage2.names': CandidateNode(
        node='str06.stage2.names', stage='stage2', owner='stage2.names',
        label='Stage2 源名称结构窄调用并发布 Characters v4',
        paid=True,
        produces=('stage2/characters/characters.json',),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage4.preserve': CandidateNode(
        node='str06.stage4.preserve', stage='stage4', owner='stage4.preserve',
        label='Stage4 Preserve（保存策略义务）与 ReconstructionBrief 投影',
        paid=True,
        produces=('runtime/preserve_checkpoint.json',
                  'stage4/grounding/reconstruction_brief.json'),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage4.body_design': CandidateNode(
        node='str06.stage4.body_design', stage='stage4', owner='stage4.body_design',
        label='Stage4 目标故事与导演说明设计（BodyDesign）',
        paid=True,
        # 产出不单独落盘：与 Hook 经 publish_graph 合并发布为
        # presentation_graph.json（唯一 publisher 是 hook_design 节点）。
        produces=(),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage4.hook_design': CandidateNode(
        node='str06.stage4.hook_design', stage='stage4', owner='stage4.hook_design',
        label='Stage4 Hook 设计并经 publish_graph 唯一发布 PresentationGraph',
        paid=True,
        produces=('stage4/design/presentation_graph.json',),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage4.bind': CandidateNode(
        node='str06.stage4.bind', stage='stage4', owner='stage4.bind',
        label='Stage4 Bind（义务到目标位置绑定，完整 canonical snapshot）',
        paid=True,
        produces=('stage4/grounding/semantic_binding.json',),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage4.compile': CandidateNode(
        node='str06.stage4.compile', stage='stage4', owner='stage4.compile',
        label='Stage4 确定性编译：ExecutionPlan／Guard／WriterBookIR／HookContext',
        paid=False,
        produces=('stage4/compiled/execution_plan.json',
                  'stage4/acceptance/guard/compile.json',
                  'stage4/acceptance/guard/report.json',
                  'stage4/context/hook/hook.json',
                  'stage4/ir/writer_book_ir.json'),
        free_recheck='deterministic-rerun',
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=0, recovery_entries=('deterministic_replay',))),
    'str06.stage5.body': CandidateNode(
        node='str06.stage5.body', stage='stage5', owner='stage5.body',
        label='Stage5 按章正文成文（机械检查＋有限重写＋章级回执）',
        paid=True,
        produces=('stage5/body/ch{nn}.txt', 'stage5/acceptance/body/ch{nn}.json'),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='chapter', session_scope='executor:chapter',
            # 首次 + 再 2 次同输入重写 = 3；留 1 次余量给显式人工追加，
            # 不把重写额度做成永久放大（Q-10 重试不叠加）。
            executor_content_limit=4,
            recovery_entries=('chapter_prefix_replay',) + _PAID_RECOVERY)),
    'str06.stage5.hook': CandidateNode(
        node='str06.stage5.hook', stage='stage5', owner='stage5.hook',
        label='Stage5 Hook 成文（同 Context 有限重写＋机械验）',
        paid=True,
        produces=('stage5/hook/hook.txt', 'stage5/hook/receipt.json'),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4,
            recovery_entries=('same_context_rewrite',) + _PAID_RECOVERY)),
    'str06.stage7.naming': CandidateNode(
        node='str06.stage7.naming', stage='stage7', owner='stage7.naming',
        label='Stage7 Naming（NameMap v5 赋值；Apply 归 assembly）',
        paid=True,
        produces=('stage7/naming/name_map.json',),
        free_recheck='--check-only',
        flags=('--check-only',),
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=4, recovery_entries=_PAID_RECOVERY)),
    'str06.stage7.assemble': CandidateNode(
        node='str06.stage7.assemble', stage='stage7', owner='stage7.assembly',
        label='Stage7 改名替换与成书装配（TXT 先落盘、manifest 最后提交）',
        paid=False,
        produces=('stage7/final/{title}_rewriting.txt',
                  'stage7/final/delivery_manifest.json'),
        free_recheck='deterministic-rerun',
        lifecycle=CandidateLifecycle(
            unit_kind='book', session_scope='executor:book',
            executor_content_limit=0, recovery_entries=('deterministic_replay',))),
}


# ─── 候选产物表 ────────────────────────────────────────────────────────────────
# 只登记**书产物**（F8 将翻入 ARTIFACT_SPECS／ARTIFACT_PATHS 的正式产物）。
# runtime 下的付费证据（request_*／validation_*／admission／adopt_*／
# *_attempts／workflow_status）是逐请求动态文件，由付费边界与 runner 各自的
# 契约治理（行内自带请求身份、协议与校验版本、ledger code），不进本版本表；
# `runtime/preserve_checkpoint.json` 是已购 canonical 证据，例外登记在册。

STR06_CANDIDATE_ARTIFACTS: Final[dict[str, CandidateArtifact]] = {
    'stage2/blueprints/chapter_blueprints.json': CandidateArtifact(
        rel='stage2/blueprints/chapter_blueprints.json',
        family='str06.stage2.blueprints', form='json',
        writer='str06.stage2.source', schema_version='str06-rc2',
        model=BlueprintsArtifactV12),
    'stage2/compiled/input_snapshots.json': CandidateArtifact(
        rel='stage2/compiled/input_snapshots.json',
        family='str06.stage2.compiled', form='json',
        writer='str06.stage2.source', schema_version='str06-rc2'),
    'stage2/acceptance/fold.json': CandidateArtifact(
        rel='stage2/acceptance/fold.json',
        family='str06.stage2.acceptance', form='json',
        writer='str06.stage2.source', schema_version='str06-rc2'),
    'stage2/characters/characters.json': CandidateArtifact(
        rel='stage2/characters/characters.json',
        family='str06.stage2.characters', form='json',
        writer='str06.stage2.names', schema_version='str06-rc3',
        model=CharactersArtifactV4),
    'runtime/preserve_checkpoint.json': CandidateArtifact(
        rel='runtime/preserve_checkpoint.json',
        family='str06.runtime', form='json',
        writer='str06.stage4.preserve', schema_version='str06-rc3',
        model=PreserveCheckpoint),
    'stage4/grounding/reconstruction_brief.json': CandidateArtifact(
        rel='stage4/grounding/reconstruction_brief.json',
        family='str06.stage4.grounding', form='json',
        writer='str06.stage4.preserve', schema_version='str06-rc3',
        model=ReconstructionBrief),
    'stage4/grounding/semantic_binding.json': CandidateArtifact(
        rel='stage4/grounding/semantic_binding.json',
        family='str06.stage4.grounding', form='json',
        writer='str06.stage4.bind', schema_version='str06-rc3',
        model=SemanticBinding),
    'stage4/compiled/execution_plan.json': CandidateArtifact(
        rel='stage4/compiled/execution_plan.json',
        family='str06.stage4.compiled', form='json',
        writer='str06.stage4.compile', schema_version='str06-rc3',
        model=ExecutionPlan),
    'stage4/acceptance/guard/compile.json': CandidateArtifact(
        rel='stage4/acceptance/guard/compile.json',
        family='str06.stage4.acceptance.guard', form='json',
        writer='str06.stage4.compile', schema_version='str06-rc3'),
    'stage4/acceptance/guard/report.json': CandidateArtifact(
        rel='stage4/acceptance/guard/report.json',
        family='str06.stage4.acceptance.guard', form='json',
        writer='str06.stage4.compile', schema_version='str06-rc4'),
    'stage4/design/presentation_graph.json': CandidateArtifact(
        rel='stage4/design/presentation_graph.json',
        family='str06.stage4.design', form='json',
        writer='str06.stage4.hook_design', schema_version='str06-rc4',
        model=PresentationGraph),
    'stage4/context/hook/hook.json': CandidateArtifact(
        rel='stage4/context/hook/hook.json',
        family='str06.stage4.context', form='json',
        writer='str06.stage4.compile', schema_version='str06-rc3',
        model=HookContext),
    'stage4/ir/writer_book_ir.json': CandidateArtifact(
        rel='stage4/ir/writer_book_ir.json',
        family='str06.stage4.ir', form='json',
        writer='str06.stage4.compile', schema_version='str06-rc4',
        model=WriterBookIR),
    'stage5/hook/hook.txt': CandidateArtifact(
        rel='stage5/hook/hook.txt', family='str06.stage5.hook', form='text',
        writer='str06.stage5.hook', schema_version='str06-rc3'),
    'stage5/hook/receipt.json': CandidateArtifact(
        rel='stage5/hook/receipt.json', family='str06.stage5.hook', form='json',
        writer='str06.stage5.hook', schema_version='str06-rc3',
        model=HookReceipt),
    'stage5/body/ch{nn}.txt': CandidateArtifact(
        rel='stage5/body/ch{nn}.txt', family='str06.stage5.body', form='text',
        writer='str06.stage5.body', schema_version='str06-rc4'),
    'stage5/acceptance/body/ch{nn}.json': CandidateArtifact(
        rel='stage5/acceptance/body/ch{nn}.json',
        family='str06.stage5.acceptance.body', form='json',
        writer='str06.stage5.body', schema_version='str06-rc4',
        model=ChapterReceipt),
    'stage7/naming/name_map.json': CandidateArtifact(
        rel='stage7/naming/name_map.json', family='str06.stage7.naming',
        form='json', writer='str06.stage7.naming', schema_version='str06-rc3',
        model=NameMapV5),
    'stage7/final/{title}_rewriting.txt': CandidateArtifact(
        rel='stage7/final/{title}_rewriting.txt', family='str06.stage7.final',
        form='text', writer='str06.stage7.assemble', schema_version='str06-rc4'),
    'stage7/final/delivery_manifest.json': CandidateArtifact(
        rel='stage7/final/delivery_manifest.json', family='str06.stage7.final',
        form='json', writer='str06.stage7.assemble', schema_version='str06-rc4'),
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False)
                          .encode('utf-8')).hexdigest()


def _node_identity(node: CandidateNode) -> dict:
    return {
        'stage': node.stage, 'owner': node.owner, 'paid': node.paid,
        'produces': list(node.produces), 'free_recheck': node.free_recheck,
        'flags': list(node.flags), 'paid_flags': list(node.paid_flags),
        'lifecycle': {'unit_kind': node.lifecycle.unit_kind,
                      'session_scope': node.lifecycle.session_scope,
                      'executor_content_limit': node.lifecycle.executor_content_limit,
                      'recovery_entries': list(node.lifecycle.recovery_entries)},
    }


def _artifact_identity(artifact: CandidateArtifact) -> dict:
    return {
        'family': artifact.family, 'form': artifact.form,
        'writer': artifact.writer, 'schema_version': artifact.schema_version,
        'model': getattr(artifact.model, '__name__', None),
    }


def candidate_contract_identity() -> str:
    """候选契约表的确定性身份：节点表 + 产物表 + RC 版式的 SHA-256。

    表内任何一行变化都会改变它——候选根初始化时记档，之后复校；
    漂移即拒绝消费旧根的采用结论（F7a-3 的阻断条件之一）。
    """
    return _digest({
        'rc': RC_VERSION,
        'nodes': {node: _node_identity(spec)
                  for node, spec in sorted(STR06_CANDIDATE_NODES.items())},
        'artifacts': {rel: _artifact_identity(spec)
                      for rel, spec in sorted(STR06_CANDIDATE_ARTIFACTS.items())},
    })


def candidate_node(node_id: str) -> CandidateNode:
    """按 id 取候选节点；未登记关闭式上抛，不返回 None 让调用方猜。"""
    try:
        return STR06_CANDIDATE_NODES[node_id]
    except KeyError:
        raise ValueError(f'未登记的候选节点：{node_id}') from None


def _rel_pattern(rel: str) -> re.Pattern[str]:
    parts = re.split(r'(\{nn\}|\{title\})', rel)
    regex = ''.join(r'\d{2}' if p == '{nn}' else r'[^/]+' if p == '{title}'
                    else re.escape(p) for p in parts)
    return re.compile(f'^{regex}$')


def candidate_artifact(rel: str) -> CandidateArtifact | None:
    """按路径取候选产物：先精确匹配，再按 `{nn}`／`{title}` 模式匹配。

    未登记返回 None（调用方拒绝，而不是猜一个家族落盘）。
    """
    spec = STR06_CANDIDATE_ARTIFACTS.get(rel)
    if spec is not None:
        return spec
    norm = str(rel).replace('\\', '/')
    for candidate in STR06_CANDIDATE_ARTIFACTS.values():
        if '{' in candidate.rel and _rel_pattern(candidate.rel).match(norm):
            return candidate
    return None


def _validate_epoch(epoch: CandidateEpoch) -> None:
    if not _EPOCH_ID_RE.match(epoch.epoch_id):
        raise ValueError('epoch_id 必须是 64 位十六进制')
    if epoch.ordinal < 1:
        raise ValueError('epoch ordinal 必须 >= 1')
    if epoch.reason not in EPOCH_REASONS:
        raise ValueError(f'epoch 理由不在面内：{epoch.reason}')
    if epoch.contract_version not in RC_LABELS:
        raise ValueError(f'epoch 契约版本不在面内：{epoch.contract_version}')
    if not epoch.code_version:
        raise ValueError('epoch 必须绑定 code_version')
    if not _RUN_ID_RE.match(epoch.task_id) and not re.fullmatch(r'[0-9a-f]{64}', epoch.task_id):
        raise ValueError('task_id 必须是 run id 或 64 位十六进制')
    if not _RUN_ID_RE.match(epoch.session_run_id):
        raise ValueError('session_run_id 必须是 run id 形状')
    if epoch.created_at <= 0:
        raise ValueError('created_at 必须是正 unix 秒')


def open_candidate_epoch(*, code_version: str, task_id: str, session_run_id: str,
                         created_at: int, reason: str = 'initial',
                         contract_version: str = RC_VERSION) -> CandidateEpoch:
    """首开候选 epoch；只允许 reason='initial'。"""
    if reason != 'initial':
        raise ValueError('首开 epoch 只能是 initial；漂移与修复走 roll_candidate_epoch')
    epoch = CandidateEpoch(
        epoch_id='', ordinal=1, reason=reason, contract_version=contract_version,
        code_version=code_version, task_id=task_id, session_run_id=session_run_id,
        created_at=created_at)
    return epoch._replace(epoch_id=_digest({
        'contract': candidate_contract_identity(), 'code': code_version,
        'ordinal': 1, 'reason': reason, 'task_id': task_id,
        'session': session_run_id, 'prev': ''}))


def roll_candidate_epoch(prev: CandidateEpoch, *, reason: str, code_version: str,
                         task_id: str, session_run_id: str, created_at: int,
                         contract_version: str = RC_VERSION) -> CandidateEpoch:
    """滚动到新 epoch：身份未漂移不得新开；漂移理由要求身份确实变化。

    `code_drift`／`contract_drift` 在对应身份与 prev 相同时拒绝（声明漂移而
    身份未变是假漂移）；`repair` 允许同身份续开（typed 修复的显式 producer
    边界，对齐正式 ExecutionEpochReason 的 continue）。累计额度不随滚动重置。
    """
    _validate_epoch(prev)
    if reason not in ('code_drift', 'contract_drift', 'repair'):
        raise ValueError(f'滚动理由不在面内：{reason}')
    if reason == 'code_drift' and code_version == prev.code_version:
        raise ValueError('声明代码漂移但 code_version 未变：拒绝假漂移')
    if reason == 'contract_drift' and contract_version == prev.contract_version:
        raise ValueError('声明契约漂移但 contract_version 未变：拒绝假漂移')
    ordinal = prev.ordinal + 1
    epoch = CandidateEpoch(
        epoch_id='', ordinal=ordinal, reason=reason,
        contract_version=contract_version, code_version=code_version,
        task_id=task_id, session_run_id=session_run_id, created_at=created_at)
    return epoch._replace(epoch_id=_digest({
        'contract': candidate_contract_identity(), 'code': code_version,
        'ordinal': ordinal, 'reason': reason, 'task_id': task_id,
        'session': session_run_id, 'prev': prev.epoch_id}))


def open_candidate_workflow(*, epoch: CandidateEpoch, source_identity: str,
                            created_at: int) -> CandidateWorkflow:
    """在候选根初始化 workflow 身份记录（调用方负责落盘与路径）。"""
    _validate_epoch(epoch)
    if not re.fullmatch(r'[0-9a-f]{64}', source_identity):
        raise ValueError('source_identity 必须是 64 位十六进制（完整字节 SHA-256）')
    return CandidateWorkflow(epoch=epoch,
                             contract_identity=candidate_contract_identity(),
                             source_identity=source_identity, created_at=created_at)


def check_candidate_workflow(record: CandidateWorkflow, *,
                             code_version: str) -> list[str]:
    """复校候选根的 workflow 身份；返回问题清单（空 = 可继续消费）。

    契约表漂移、代码漂移、epoch 形状损坏、源身份缺失都是显式问题——
    调用方必须阻断或先滚动 epoch，不得静默沿用旧根结论。
    """
    problems: list[str] = []
    try:
        _validate_epoch(record.epoch)
    except ValueError as exc:
        problems.append(f'epoch 形状损坏：{exc}')
    current = candidate_contract_identity()
    if record.contract_identity != current:
        problems.append('候选契约表已漂移：根初始化时的表身份与当前不一致，'
                        '需按 contract_drift 滚动 epoch 并重新对账')
    if record.epoch.code_version != code_version:
        problems.append('代码身份漂移：需按 code_drift 滚动 epoch 后再续跑')
    if not re.fullmatch(r'[0-9a-f]{64}', record.source_identity):
        problems.append('源输入身份缺失或损坏：拒绝采用旧根结果')
    return problems
