#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retirement —— F6d 现役 Stage6 链退役登记（纯数据；随 RC 包在 F8 删除）。

【只登记，不提前退役】
本表是 F8 契约切换提交的删除清单：节点与编排、产物与版本账、批准绑定、
API/Views/前端、判据、测试与 Stage7 对 Stage6 的现存依赖。F6 只登记并做双向
对账（`tests/test_str06_retirement.py`），**不删除、不改写任何现役面**；
已退役的 `stage6.semantic` 判官不算新增成果，只按「已死残留」登记。

【target 的四种可核对格式（对账测试按前缀分派）】
  1. `路径/文件.ext`              —— 文件存在（delete_file 的对象）。
  2. `路径::符号`                 —— 文件内定义的符号（def/class/模块级赋值名）。
  3. `路径#键`                    —— 文件内任意 dict 字面量中的字符串键。
  4. `路径@"子串"`                —— 文件内出现的字面子串（路由串、token、文档行）。

每条 `take_over` 写明接管者：新链 owner（`stage2.names`／`stage4.hook_design`／
`stage5.hook_writer`／`stage7.naming`／`stage7.assembly`）、「退役无接管」或
迁移批次（如「F5 body_access」）。迁移仍有效的独有反例时先登记接管者，不缩分母。
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class RetirementAction(str, Enum):
    DELETE_FILE = 'delete_file'
    REMOVE_ENTRY = 'remove_entry'
    REWRITE_CONSUMER = 'rewrite_consumer'


class RetirementItem(NamedTuple):
    action: RetirementAction
    target: str
    take_over: str
    note: str = ''


def _items(*rows: tuple[str, str, str, str]) -> tuple[RetirementItem, ...]:
    return tuple(RetirementItem(
        action=RetirementAction(action), target=target, take_over=take_over, note=note)
        for action, target, take_over, note in rows)


#: F8 一次合入的删除清单。按段落分组登记；顺序不承载语义。
STR06_F6D_RETIREMENT: tuple[RetirementItem, ...] = (

    # ── 节点与编排 ──────────────────────────────────────────────
    *_items(
        ('delete_file', 'novel/stages/stage6/__init__.py',
         'stage5.hook_writer + stage7.assembly', '整模块：stage6 包入口'),
        ('delete_file', 'novel/stages/stage6/checks.py',
         'stage5.hook_writer 机械 validator + stage7.assembly 残留检查',
         '整模块：stage6 机械检查'),
        ('delete_file', 'novel/stages/stage6/hook.py',
         'stage4.hook_design + stage5.hook_writer', '整模块：三候选 hook 生产'),
        ('delete_file', 'novel/stages/stage6/measure.py',
         'stage5.hook_writer 回执 + stage7.assembly', '整模块：字数/预算测量'),
        ('delete_file', 'novel/stages/stage6/prepare.py',
         'stage2.names', '整模块：prepare 生产'),
        ('delete_file', 'novel/stages/stage6/prepare_compile.py',
         'stage2.names', '整模块：prepare 确定性臂'),
        ('delete_file', 'novel/stages/stage6/prompts.py',
         'novel/stages/str06/prompts.py', '整模块：stage6 prompt'),
        ('delete_file', 'novel/stages/stage6/semantic.py',
         '退役无接管（判官已随 2026-09-15 全自动裁决退役）', '整模块：A/B 语义审判残留'),
        ('remove_entry', 'contracts/invariants.py#stage6.prepare',
         'stage2.names + stage4.hook_design + stage5.hook_writer',
         'NODES 键：prepare 节点（R-9）'),
        ('remove_entry', 'contracts/invariants.py#stage6.hook',
         'stage4.hook_design + stage5.hook_writer', 'NODES 键：hook 三候选节点'),
        ('remove_entry', 'contracts/invariants.py#stage6.approve',
         '退役无接管（三候选改选面随之退役）', 'NODES 键：定稿节点，不阻塞下游'),
        ('remove_entry', 'contracts/invariants.py::RETIRED_APPROVAL_BINDINGS',
         '退役无接管', "删 'stage6.prepare' 键（approval:stage6-prepare 历史只读归属）"),
        ('remove_entry', 'contracts/invariants.py::LLM_EXECUTOR_NODES',
         'stage5.hook_writer（新链直连 LLM）', "删 'stage6.prepare'/'stage6.hook' 两项"),
        ('remove_entry', 'contracts/invariants.py::FREE_ENTRIES',
         'stage4.hook_design', 'stage6 相关免费重验入口项'),
        ('remove_entry', 'contracts/invariants.py::WRITE_EFFECTS',
         'stage5.hook_writer', 'stage6.prepare/hook/approve 的写效应条目'),
        ('remove_entry', 'contracts/invariants.py::NODE_WRITE_EFFECTS',
         'stage5.hook_writer', 'stage6 节点特例条目'),
        ('remove_entry', 'contracts/invariants.py::CONDITIONAL_REQUIRED_FLAGS',
         'stage5.hook_writer',
         "('stage6.*', '--*') 各格：--recover-prepared/--revalidate/--identity"),
        ('remove_entry', 'contracts/invariants.py::HOOK_APPROACHES',
         'stage4.hook_design（材料选择归 HookDesign）', 'HOOK_* 常量：写法取值面'),
        ('remove_entry', 'contracts/invariants.py::HOOK_RECIPE',
         'stage4.hook_design', 'HOOK_* 常量：当前配方'),
        ('remove_entry', 'contracts/invariants.py::HOOK_SLOTS',
         '退役无接管（单 Hook 输出）', 'HOOK_* 常量：三候选槽位'),
        ('remove_entry', 'contracts/invariants.py::HOOK_APPROACH_NOTES',
         'stage4.hook_design', 'HOOK_* 常量：写法说明'),
        ('remove_entry', 'contracts/invariants.py::HOOK_CURRENT_APPROACHES',
         'stage4.hook_design', 'HOOK_* 常量：当前写法集'),
        ('remove_entry', 'contracts/invariants.py::HOOK_RETIRED_APPROACHES',
         '退役无接管', 'HOOK_* 常量：退役写法枚举'),
        ('rewrite_consumer', 'novel/orchestrator/nodes.py',
         'stage5.hook_writer', 'stage6 分支（节点注册/描述）'),
        ('rewrite_consumer', 'novel/orchestrator/execution_permit.py',
         'stage5.hook_writer', 'stage6 许可分支'),
        ('rewrite_consumer', 'novel/orchestrator/queue.py',
         'stage7.assembly', 'stage6 队列/等待态分支（含 WAITING_ON_STAGE6_PREPARE_COMMIT）'),
        ('rewrite_consumer', 'novel/orchestrator/approvals.py',
         '退役无接管', "s6 gate 与 stage6 workpack/approve 分支"),
        ('rewrite_consumer', 'novel/orchestrator/inbox_projection.py',
         '退役无接管', 'stage6 收件箱投影分支'),
        ('rewrite_consumer', 'novel/orchestrator/reviewer_legacy.py',
         '退役无接管', '存量 reviewer 的 stage6 解码段'),
        ('rewrite_consumer', 'novel/orchestrator/reviewer_node_landed.py',
         '退役无接管', "s6r4_ token 校验段"),
        ('rewrite_consumer', 'novel/orchestrator/inspection.py',
         '退役无接管', "ARTIFACT_PATHS['stage6.hook_edit'] 巡检项"),
        ('rewrite_consumer', 'novel/orchestrator/workflow_transition.py',
         '退役无接管', "workflow manifest 快照中 stage6_prepare* 四字段"),
        ('rewrite_consumer', 'contracts/schema/workflow.py',
         '退役无接管',
         "WorkflowManifest v7–v9 的 stage6_prepare/_edit/_review/_decision 字段与 "
         "'stage6-prepare-v1' 契约版本"),
        ('delete_file', 'novel/orchestrator/rebuild.py',
         'stage7.assembly（确定性组装）', '整模块：GET stage6/rebuild 的实现'),
        ('rewrite_consumer', 'novel/cli/main.py',
         'tools/str06_workflow.py（隔离入口）',
         "_run_stage6 与 approval_workpack 的 surface='stage6' 选项"),
    ),

    # ── 产物与版本账 ───────────────────────────────────────────
    *_items(
        ('remove_entry', 'contracts/artifacts.py#stage6.prepare',
         'stage2.names', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.prepare_edit',
         'stage2.names', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.prepare_review',
         '退役无接管', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.prepare_review_decisions',
         '退役无接管', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.prepare_commit',
         '退役无接管', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.hook',
         'stage5.hook_writer（stage5/hook/hook.txt）', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.hook_edit',
         '退役无接管', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.wontfix',
         '退役无接管', 'ARTIFACT_PATHS 键'),
        ('remove_entry', 'contracts/artifacts.py#stage6.semantic',
         '退役无接管（2026-09-15 判官退役残留）', '已死残留：无消费者的路径键'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_PREPARE',
         '退役无接管', '已死残留：无调用方 path 助手（8 个一组）'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_PREPARE_EDIT',
         '退役无接管', '已死残留：path 助手'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_PREPARE_REVIEW',
         '退役无接管', '已死残留：path 助手'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_PREPARE_REVIEW_DECISIONS',
         '退役无接管', '已死残留：path 助手'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_PREPARE_COMMIT',
         '退役无接管', '已死残留：path 助手'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_HOOK',
         '退役无接管', '已死残留：path 助手（approvals.py 改写后无消费）'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_HOOK_EDIT',
         '退役无接管', '已死残留：path 助手'),
        ('remove_entry', 'contracts/artifacts.py::STAGE6_WONTFIX',
         '退役无接管', '已死残留：path 助手'),
        ('remove_entry', 'contracts/schema/__init__.py',
         '退役无接管', 'stage6 / stage6_prepare 的 import 与 ARTIFACT_SPECS 挂载八项'),
        ('delete_file', 'contracts/schema/stage6.py',
         'contracts/schema/str06/hook.py', 'Hook/ApprovedHook/HookApprovalResolution 等'),
        ('delete_file', 'contracts/schema/stage6_prepare.py',
         '退役无接管', 'Stage6Prepare/PrepareEdit/Review/Decision 等'),
        ('remove_entry', 'tests/test_artifact_versions.py#stage6/hook.json',
         'contracts/schema/str06（RC 版式账）', '_SHAPE_BREAKS 两条之一'),
        ('remove_entry', 'tests/test_artifact_versions.py#stage6/hook_edit.json',
         '退役无接管', '_SHAPE_BREAKS 两条之一'),
        ('rewrite_consumer', 'tests/factories.py',
         'tests（str06 builder）', 'stage6_hook / stage6_hook_retry 工厂段'),
    ),

    # ── 批准绑定 ───────────────────────────────────────────────
    *_items(
        ('remove_entry', 'novel/orchestrator/approvals.py@"s6"',
         '退役无接管', "gate 's6'：workpack 动作、门序列与 _POST_WRITING_GATES"),
        ('remove_entry', 'contracts/schema/stage6.py::HookApprovalResolution',
         'contracts/schema/str06/hook.py::HookReceipt', '批准绑定模型'),
        ('remove_entry', 'novel/orchestrator/reviewer_node_landed.py@"s6r4_"',
         '退役无接管', 'opaque workpack token'),
        ('remove_entry', 'web/src/constants/gates.ts@"s6"',
         '退役无接管', '前端 gate 词汇（stage6: s6 与 stage7.need）'),
    ),

    # ── API／Views／前端 ───────────────────────────────────────
    *_items(
        ('remove_entry', 'novel/api/routes/edits.py@"/{sid}/stage6/approve"',
         '退役无接管', '定稿路由'),
        ('remove_entry', 'novel/api/routes/edits.py@"/{sid}/stage6/rebuild"',
         '退役无接管', '重建路由'),
        ('remove_entry', 'novel/api/routes/approvals.py@"/stage6/workpack"',
         '退役无接管', '定稿工作包路由'),
        ('remove_entry', 'novel/api/routes/screens.py@"/{sid}/stage6"',
         '退役无接管', 'Stage6 页路由'),
        ('delete_file', 'novel/api/routes/stage6_prepare.py',
         '退役无接管', 'prepare 视图与 workpack 两条路由'),
        ('delete_file', 'novel/views/stage6.py',
         '退役无接管', 'stage6 消费投影（View）'),
        ('delete_file', 'novel/views/stage6_prepare.py',
         '退役无接管', 'prepare 消费投影（View）'),
        ('rewrite_consumer', 'novel/api/routes/screens.py',
         '退役无接管', 'stage6_view import 与响应模型'),
        ('delete_file', 'web/src/screens/stage6/Stage6Screen.tsx',
         '退役无接管', 'Stage6 页'),
        ('delete_file', 'web/src/screens/stage6/Stage6Screen.test.tsx',
         '退役无接管', 'Stage6 页测试'),
        ('delete_file', 'web/src/screens/stage6/PreparePanel.tsx',
         '退役无接管', 'Prepare 面板'),
        ('delete_file', 'web/src/screens/stage6/PreparePanel.test.tsx',
         '退役无接管', 'Prepare 面板测试'),
        ('rewrite_consumer', 'web/src/api/endpoints.ts',
         '退役无接管', 'getStage6/getStage6Prepare/getStage6PrepareWorkpack/'
                       'getStage6Rebuild/approveStage6 五段'),
        ('rewrite_consumer', 'web/src/api/queryKeys.ts',
         '退役无接管', 'stage6 查询键'),
        ('rewrite_consumer', 'web/src/api/mock.ts',
         '退役无接管', 'stage6 mock 与 fixtures 段'),
        ('rewrite_consumer', 'web/src/types/jobs.ts',
         '退役无接管', 'Stage6 改选后的重建状态等类型（jobs.ts 及相关类型文件）'),
        ('rewrite_consumer', 'web/src/screens/run/RunScreen.tsx',
         '退役无接管', '重建面板段'),
        ('rewrite_consumer', 'web/src/components/app/BookLayout.tsx',
         '退役无接管', 'Stage6 改选行与 Rail 入口'),
        ('rewrite_consumer', 'web/src/constants/vocabulary.ts',
         '退役无接管', 'stage6 词汇'),
        ('rewrite_consumer', 'web/src/fixtures',
         '退役无接管', 'stage6 fixture 段'),
    ),

    # ── 判据 ───────────────────────────────────────────────────
    *_items(
        ('delete_file', 'contracts/judges/stage6.py',
         'stage5.hook_writer 机械 validator + stage7.assembly 残留检查',
         '全部规则（含 report accounting）'),
        ('rewrite_consumer', 'contracts/judges/__init__.py',
         '退役无接管', 'stage6 判据模块 import'),
        ('rewrite_consumer', 'tests/hard_cases.py',
         'tests（str06 反例）', 'stage6 对应判例'),
    ),

    # ── Stage7 现役对 Stage6 的依赖 ────────────────────────────
    *_items(
        ('rewrite_consumer', 'novel/stages/stage7/rename.py',
         'stage5.hook_writer 回执 + stage7.assembly body_access',
         '读 stage6/hook.json 与 require_approved_clear'),
        ('rewrite_consumer', 'novel/stages/stage7/assemble.py',
         'stage5.hook_writer 回执 + stage7.assembly body_access',
         '读 stage6/hook.json 与 require_approved_clear'),
        ('rewrite_consumer', 'novel/stages/stage7/measure.py',
         'stage5.hook_writer 回执（候选集指纹改 Hook 回执）',
         '对 stage6/hook.json 的候选集文本指纹'),
    ),

    # ── 测试（先迁移仍有效的独有反例，登记接管者，不缩分母） ──
    *_items(*(
        ('delete_file', f'tests/{name}', '迁移独有反例后删除', '纯 Stage6 测试文件')
        for name in (
            'test_stage6.py', 'test_stage6_auto_approve.py',
            'test_stage6_core_consumption.py', 'test_stage6_hook_content_budget.py',
            'test_stage6_legacy_delivery_recovery.py', 'test_stage6_prepare.py',
            'test_stage6_prepare_http.py', 'test_stage6_prepare_workflow.py',
            'test_stage6_prepared_cli.py', 'test_stage6_prompt_experiments.py',
            'test_stage6_quality_contract.py', 'test_stage6_rebuild.py',
            'test_stage6_retry.py', 'test_views_stage6.py'))),

    # ── 已死残留（登记，F8 清理） ──────────────────────────────
    *_items(
        ('remove_entry', 'docs/schema/_api.md@"POST /api/novels/:sid/stage6/prepare/edit/preview"',
         '退役无接管', '文档记载了不存在的路由（:160–162 三行）'),
        ('remove_entry', 'docs/schema/_api.md@"POST /api/novels/:sid/stage6/prepare/edit"',
         '退役无接管', '文档记载了不存在的路由'),
        ('remove_entry', 'docs/schema/_api.md@"POST /api/novels/:sid/stage6/prepare/review"',
         '退役无接管', '文档记载了不存在的路由'),
        ('remove_entry', 'tests/test_layering.py@"prepare_recovery.py"',
         '退役无接管', '指向不存在的模块的登记行'),
    ),
)
