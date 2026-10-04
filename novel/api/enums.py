#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enums.py —— `GET /api/enums` 的**唯一**构造函数。纯函数：不碰盘、不碰网、不 import fastapi。

【它要解决的病】`contracts/invariants.py` 自称是跨层取值域的唯一落点，而在本期之前
它一个网络出口都没有。于是前端只好自己抄一份：`web/src/constants/vocabulary.ts`
硬编码了 7 个联合类型、`web/src/constants/actions.ts` 手抄了一张 `WRITE_EFFECTS`
并在注释里写着「迟早会漂」。**第 N 个落点必然分家**，那份注释是前端自己写的诊断书。

【三个桶，每个公开常量恰好落一个】
  桶一 · **自动发布**       —— 容器（tuple / dict）。键名 `NAME.lower()`
  桶二 · `_CONSUMED`        —— 被某个合成键吃掉了，不再单独发
  桶三 · `_WITHHELD`        —— 登记为不发，**每条写得出理由**
  桶三的例外 `_PUBLISHED_SCALARS` —— 标量默认不发，除非登记并说明前端拿它做什么

【为什么「发」是自动的而「不发」要手写 —— 这是本模块的全部承重点】
两种烂法的**成本相同，方向相反**：

  · 手写**发布清单** ⇒ **静默地烂**。新加一个常量，前端拿不到，于是前端自己硬编码一份，
    而没有任何东西会出声——那正是 `invariants` 存在的理由要防的事。
  · 手写**不发登记表** ⇒ **响亮地烂**。新加一个常量落不进任何桶，
    `payload()` 当场抛 `ValueDomainRefused`，你必须选一个桶并写出理由。

⚠ **标量为什么默认不发**：一个孤立的数字（`MIN_CHAPTERS` / `BODY_RATIO_MIN` /
`SOURCE_OVERLAP_MAX`…）对前端只有一个用途——**自己算一个结论**，而那就是第二个判官
（`R-11.4`：界面不重算任何已经在校验器里的判据）。容器不一样：取值面、词表、映射表、
区间，前端拿它渲染选项与图例，渲染不产生判定。

输出只由当前 `contracts.invariants` 派生；历史抓包不参与当前 API 契约。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from contracts import invariants as inv
from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
from novel.errors import ValueDomainRefused
from novel.orchestrator.nodes import (
    is_paid,
    missing_required,
    step_label,
    would_write,
    write_effect,
)

# ─────────────────────────────────────────────────────────────────────────────
# 一、三张登记表
# ─────────────────────────────────────────────────────────────────────────────

#: 桶二 —— 被某个**合成键**吃掉的常量。值是「它去了哪儿」。
#:
#: ⚠ **吃掉 = 不再单独发。** 同一取值面在响应里出现两次，两处必然在某次改动后
#: 指向不同的东西——这与本模块要消灭的病是同一个。
#: （旧 Stage5 写作发明边界 `invent` 合成键已随旧链 prompt 常量删除。）
_CONSUMED: dict[str, str] = {
    'NODES': 'job_kinds + actions',
    'NODE_WRITE_EFFECTS': 'actions[].writes',
    'SPOILER_DISCLOSURE': 'spoiler_disclosure',
}

#: 桶三 —— 登记为**不发**。全是标量，且每一条的理由都是同一句的实例：
#: **发过去等于请前端自己判**，而判定只许有一处（`R-11.4`）。
#:
#: ⚠ 这张表**必须逐个列名**，不许写成「所有标量一律不发」那种规则：
#: 那样新加一个标量会被**静默**挡住，而静默正是本模块要防的方向。
_WITHHELD: dict[str, str] = {
    'WORKFLOW_ROLE_CONTRACT': '工作流执行身份；界面只显示后端工作单元读模型',
    'WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS': '预算已由持久工作单元账折叠，前端不自行重算',
    'BOOK_CONTENT_CALL_CEILING_DEFAULT': '整书付费总闸默认值只由后端 reserve 时强制；前端显示工作单元预算投影，不自行判断余量',
    'DETERMINISTIC_REBUILD_NODES': '自动重建白名单是后端调度边界（R-11.27）；前端只消费 operator 给出的具体动作，不自行推导重建范围',
    'WORK_UNIT_MAX_DELIVERY_ATTEMPTS': '网络尝试上限只由工作单元底座执行，前端显示后端预算投影',
    'CASCADE_REWIND_LIMIT': (
        '跨节点回派每根累计上界（R10c 硬化批）是后端级联背压边界；'
        '前端只消费 typed 停止状态，不自行推导回派额度'),
    'SYSTEM_INVALID_REVALIDATE_ACTOR': (
        '批次 C：live .INVALID 由队列自动排入 0-wire --revalidate 的固定审计身份；'
        '不是前端取值面或按钮文案'),
    'SYSTEM_INVALID_REVALIDATE_REASON': (
        '同上的固定复验理由；只写进队列 argv 与审计，不是前端取值面'),
    'STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES': (
        '只随 Commander immutable submission option 与 Stage5 writer 固定 prompt 下发；'
        '当前浏览器没有该 delegated rewrite 表单，单独发布会制造未接线的第二消费面'),
    'LLM_EXECUTOR_NODES': (
        '付费 LLM executor 节点属于 provider/model/effort/thinking 组合能力面，'
        '唯一公开口是动态 `GET /api/agent/capabilities`；'
        '再经 `/api/enums` 发一份会造成两个可漂移的节点清单'),
    'RETENTION_CANDIDATE_KINDS': (
        'retention preview 已随每条候选返回服务端判定出的 kind；它不是维护策略选项，'
        '浏览器不得据此重算是否应清理，另经 `/api/enums` 发布会制造第二个消费面'),
    'OPERATOR_PHASES': (
        'operator telemetry 只由本地 skill/脚本写读，不是 HTTP/UI 取值面'),
    'OPERATOR_PHASE_EVENT_KINDS': '同上，append-only 事件种类不由前端构造',
    'COMMANDER_ADMISSION_EVENT_KINDS': (
        'Commander/run_step 的内部 append-only admission wire；'
        '只由后端生命周期边界构造，不是前端操作选项'),
    'COMMANDER_ADMISSION_TAIL_REPAIR_KINDS': (
        'Commander admission 坏尾的内部两相恢复记录；只由 typed CLI 恢复口构造'),
    'RESUME_VERDICTS': (
        'operator/queue 内部的断点判定域；前端只消费随 OperatorNext '
        '返回的具体结论，不自行构造或重算'),
    'OPERATOR_REDO_STRATEGIES': (
        'AgentRun 对 REDO 的内部关闭式重放策略；前端只消费 OperatorNext '
        '已经判定出的 exact flags 与 execution token，不自行选择或重算策略'),
    'OWNER_REVISION_MAX_ROUNDS': (
        'R3 自动修订轮上限只由付费边界与回派账强制；前端显示后端 issue 记录里'
        '的 rounds_total 投影，不自行判断剩余额度'),
    'OPERATOR_NEXT_STATES': (
        'OperatorNext 响应自身已携严格状态；它是只读调度结果，'
        '不是 /api/enums 供界面构造的动作选项'),
    'EXECUTION_PERMIT_KINDS': (
        'ready/recovery 是本地 operator runner 的内部执行许可来源；'
        '不对 HTTP/UI 发布第二份可执行路由表'),
    'OPERATOR_TELEMETRY_SOURCES': '同上，遥测来源由本地入口固定',
    'OPERATOR_TERMINAL_STATES': '同上，终态由本地 fold 契约解释',
    'OPERATOR_DURATION_SOURCES': '同上，时长来源由本地计时边界决定',
    'LEGACY_STAGE5_MODES': '只用来识别并拒绝旧 deep/shallow 盘面，不是现役选项',
    # ── 分章（stage0 / stage1）的判据阈值与剥壳正则 ──
    'NONBODY_LEAD_STRIP': '正则。发过去等于请前端自己剥壳，而剥出来的 core 是所有长度阈值的输入',
    'NONBODY_TRAIL_STRIP': '同上',
    'MIN_CHAPTERS': '判据阈值。前端拿到就能自己判「章数够不够」，而那条判据在 stage1',
    'BODY_RATIO_MIN': '判据阈值（正文占比下限）',
    'DROP_RATIO_MAX': '判据阈值（剔除占比上限）',
    'NOISY_DUPLICATE_MIN': '判据阈值（重复行算噪声的次数下限）',
    'NONBODY_HEAD_MAX_CP': '判据阈值（篇首非正文标记的最大码点数）',
    'NONBODY_MID_MAX_CP': '判据阈值（篇中同上）',
    'NONBODY_MARKER_WARN_CP': '判据阈值（长标记报警线）',
    'BLUEPRINT_ARCHITECTURE': '`stage2.blueprint` 那次调用的**模型可见边界身份**，'
                              '是产地写进 `_provenance.context.architecture` 的一笔账。'
                              '⚠ 前端要显示的是**这份产物是哪个架构产的**，'
                              '而那个值已经随产物落盘、并由 `context_scope` 那条度量'
                              '**无条件报出**。enums 再发一份「当前是哪一版」，'
                              '前端就能拿它自己判「这份产物过期没有」——'
                              '而那正是判据在做的事（`R-11.4` 的第二个判官）',
    # ── stage4 / stage5 ──
    'SOURCE_OVERLAP_MAX': '判据阈值（canonical 成稿与原文的重合度上限）',
    'STOP_REWRITE_AFTER': '返工护栏的轮次上限。它是**编排**参数，不是给人看的取值面',
    'REVALIDATE_ONLY_NEW_ERRORS': '一个 bool 开关，说的是重算路怎么判。前端不参与那次判定',
    'PIPELINE_DEFAULT_CONCURRENCY': '并发默认由 Agent capability 发布，enums 不另复制',
    'PIPELINE_BOOK_CONCURRENCY': '整书总槽默认由 Agent capability 发布',
    'PIPELINE_PROVIDER_CONCURRENCY': '供应商总槽默认由 Agent capability 发布',
    'PIPELINE_TOTAL_CONCURRENCY_MIN': '总槽配置范围由 Agent capability 发布',
    'PIPELINE_TOTAL_CONCURRENCY_MAX': '总槽配置范围由 Agent capability 发布',
    'PIPELINE_CONCURRENCY_TRIALS': '历史档位元组；现役节点与总槽并发取 capability 发布的整数区间',
    'PIPELINE_PARALLEL_NODES': '并发覆盖节点由 Agent capability 发布',
    'LLM_MAX_OUTPUT_TOKENS': '全部付费节点的统一输出上限（D6）；截断即停由后端执行，'
                             '前端不据此改判或重试',
    # ── 成书分隔 ──
    'BOOK_BLOCK_SEP': '成书的段间分隔（空行）。它是后端写的字节——前端拿它'
                      '只能用来自己拼一份，那就是交付物的第二个落点',
    'REQUIRED_PLACEHOLDER_PREFIX': '**CLI `preview` 的渲染细节，不是取值面。** 它存在的'
                                   '理由是「摊开的那条命令要能整条粘走」，而前端从不粘'
                                   '命令——它按 `actions[]` 发 `POST /api/jobs`。'
                                   '发过去只会诱使前端拿它去**预填一个人工凭据输入框**，'
                                   '而那个框恰恰是要人自己填的（`--actor` 是自报身份）',
    'CONDITIONAL_REQUIRED_FLAGS': '⚠ **容器，但仍然不发。** 它的键是 `(节点, flag)` 二元组，'
                                  '发布器只认字符串键；而更要紧的是**方向**：前端拿到必填表'
                                  '就会自己判「这条跑法能不能提交」，那是 `validate_run` 的'
                                  '判定（`R-11.4` 的第二个判官）。前端提交后由后端当场拒，'
                                  '错在哪由那句拒收文案说，不由前端复述一遍',
}

#: 桶三的**例外** —— 当前没有允许直接下发给前端的标量判据。
_PUBLISHED_SCALARS: dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# 二、序列化 —— 认不出就当场拒
# ─────────────────────────────────────────────────────────────────────────────

def _seq(name: str, value: Sequence[Any]) -> list[Any]:
    """元组 → 列表。**要么全是字符串，要么全是数**，混的一律拒。

    ⚠ 这条「同质」要求不是洁癖，它是 `NamedTuple` 的那道闸：`NodeSpec` 是 tuple 的子类，
    元素是 `(str, str, bool, str, tuple, ...)`——混的。没有这条，它会被静默写成一个
    **位置数组**发给前端，字段名全部丢掉而没有一层出声。
    """
    items = list(value)
    if all(isinstance(x, str) for x in items):
        return items
    # ⚠ `bool` 是 `int` 的子类：不排掉它，`(True, False)` 会被当成一对数发出去。
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in items):
        return items
    raise ValueDomainRefused(
        f'{name} 是个元组，但元素既不全是字符串也不全是数：{items!r}。'
        f'⚠ `NamedTuple` 落到这里就是这个形状——它会被写成一个位置数组，'
        f'字段名全丢。要发它请先给它一个合成键（见 `_CONSUMED`）')


def _jsonable(name: str, value: Any) -> Any:
    """取值面 → 可 JSON 化。**四种形状，认不出抛 `ValueDomainRefused`，不兜底。**"""
    if isinstance(value, tuple):
        return _seq(name, value)
    if isinstance(value, dict):
        bad_keys = [k for k in value if not isinstance(k, str)]
        if bad_keys:
            raise ValueDomainRefused(f'{name} 的键不是字符串：{bad_keys!r}')
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, str):
                out[k] = v
            elif isinstance(v, tuple):
                out[k] = _seq(f'{name}[{k!r}]', v)
            else:
                raise ValueDomainRefused(
                    f'{name}[{k!r}] 的值是 {type(v).__name__}，'
                    f'发布器只认字符串与元组')
        return out
    raise ValueDomainRefused(
        f'{name} 是 {type(value).__name__}，发布器只自动发**容器**（元组 / 字典）。'
        f'⚠ 标量默认不发：一个孤立的数字对前端只有一个用途——自己算一个结论，'
        f'而那是第二个判官（`R-11.4`）。'
        f'确实要发就登记进 `_PUBLISHED_SCALARS` 并说明前端拿它做什么；'
        f'不发就登记进 `_WITHHELD` 并写出理由。**不许只是不提它**')


def public_constants() -> dict[str, Any]:
    """`contracts.invariants` 里所有**公开**常量。

    ⚠ `isupper()` 同时挡掉 import 进来的 `Final` / `NamedTuple`、类型 `NodeSpec`
    以及私有实现常量；这些都不是前端取值面。
    """
    return {n: v for n, v in vars(inv).items() if n.isupper() and not n.startswith('_')}


# ─────────────────────────────────────────────────────────────────────────────
# 三、三个合成键
# ─────────────────────────────────────────────────────────────────────────────

def _spoiler_disclosure() -> dict[str, Any]:
    """TODO-25 display-only boundary, derived from its single invariant."""
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in inv.SPOILER_DISCLOSURE.items()
    }


def job_kinds() -> dict[str, dict[str, Any]]:
    """作业单元表。**键是节点 id**（如 `stage5.write`），不是原型那 12 个 `n1..n9`。

    ⚠ **键名保持 `job_kinds` 不改成 `nodes`**：抓包那 14 个键是契约的一部分，
    改键名是一次前端可见的破坏性变更，而 `docs/BACKEND_CONTRACT.md` §一「命名体系」第 5 条说的是
    「形状不变，**只有 `kind` 的取值变**」。里面装的是什么，`docs/schema/_api.md` 说清楚。

    ⚠⚠ **本表的 `paid` 说的是【默认路径】贵不贵，不许拿它做贵贱对账。**
    真正的贵贱由 `(node, flags)` 二元组决定，唯一落点是 `is_paid`（`R-11.7`），
    出口在 `actions[]`。实测：抓包的 `check_blueprints` 是 `paid=false`，
    而它映到的 `stage2.blueprint` 节点 `paid=true`——那不是不一致，
    是原型把复检做成了独立 kind 而两层命名下它是一个 flag。
    """
    return {node: {'label': s.label, 'paid': s.paid, 'stage': s.stage,
                   'section': s.section,
                   'flags': list(s.flags), 'produces': list(s.produces),
                   'lifecycle': {
                       key: ([list(item) if isinstance(item, tuple) else item for item in value]
                             if isinstance(value, tuple) else value)
                       for key, value in s.lifecycle._asdict().items()}}
            for node, s in NODES.items()}


def actions() -> list[dict[str, Any]]:
    """界面上摆得出来的每一个按钮 = `节点 × (默认路径 + 每个独立 flag)`。

    **字段形状照抄抓包，只做加法**（§一「命名体系」第 5 条：形状不变、只有 `kind` 的取值变）：
    `kind` / `extra` / `label` / `paid` / `free_flag` 五格原样，
    新增 `writes` / `writes_disk` / `needs_value` 三格。

    ⚠ 每一格都是**派生**的，一个字都不手写：
      · `label`       ← `nodes.step_label`  —— 与作业记录里那一步共用（`R-11.8`）
      · `paid`        ← `nodes.is_paid`     —— 贵贱只有一处算（`R-11.7`）
      · `writes`      ← `nodes.write_effect`—— 空串 = 不写盘
      · `writes_disk` ← `nodes.would_write` —— `web/AGENTS.md` U-08 的写盘分类来源；
        `web/src/constants/actions.ts` 仅为缺该字段的历史 mock 保留兜底，真实响应不另造判定。

    ⚠ **flag 的那一句人话不放进本表。** `free_entries` / `paid_flags` / `valued_flags` /
    `write_effects` 四张表已经整份下发了，前端按 flag 去查即可——
    同一份数据放两处就是第二个落点（与 `CheckReport` 砍掉四个 sev 桶是同一条裁决）。

    ⚠ `extra` 里的带值 flag **不带值**（`--apply-edit` 而不是 `--apply-edit <路径>`）：
    那个值是人在点下去的那一刻才给的。`needs_value` 就是用来说这件事的。
    """
    # 这些 flag 是审计凭据/提交锁，不是可独立运行的动作。它们仍留在
    # ``job_kinds[].flags``，并经下方 ``required_inputs`` 挂到真正需要它们的动作。
    credential_flags = {
        '--actor', '--reason', '--subject-sha256',
        '--review-token',
        '--current-chapter-sha256', '--preview-subject-sha256',
        '--edit-confirmation',
        '--source-journal-sha256', '--source-commit-sha256',
        '--reviewer-rewrite-request-sha256',
        '--reviewer-rewrite-recovery-target-run-id',
        '--reviewer-rewrite-recovery-journal-sha256',
        # Supervisor 的内部 hash-bound action 不能由普通作业表单发起；
        # HTTP job 入口也明确拒绝，不能把它们发布成不可执行的按钮。
        # （--request-reviewer-rewrite 已随批次 B3 trim 三选一退役删除。）
        '--reviewer-rewrite',
        '--recover-reviewer-rewrite-prepared',
        '--retire-reviewer-rewrite-request',
    }
    # 批次 C：人工修复写面退役——内部 0-wire revalidate / draft repair 仍可由
    # 队列与 CLI 恢复路径复用，但 enums.actions 不再向 Stage 页发布这些用户入口。
    internal_recovery_flags = {
        '--revalidate',
        '--repair-analyze-draft',
        '--emit-analyze-draft-template',
        '--response-repair',
    }

    def required_inputs(node: str, flags: Sequence[str]) -> list[dict[str, str]]:
        """调用方在执行前必须收齐的缺失值。

        这里刻意复用 ``validate_run`` 与 preview placeholder 共用的
        ``missing_required``。把描述挂到具体动作上不是给浏览器再造一张必填 flag 表，
        只是在渲染动作时说明该收哪些人工输入；提交时仍由服务端作唯一权威校验。
        """
        return [
            {'flag': flag, 'description': VALUED_FLAGS[flag]}
            for flag in missing_required(node, flags)
        ]

    out: list[dict[str, Any]] = []
    for node, spec in NODES.items():
        independent = [
            f for f in spec.flags
            if f not in credential_flags and f not in internal_recovery_flags
        ]
        for flags in [()] + [(f,) for f in independent]:
            out.append({
                    'kind': node,
                    'label': step_label(node, flags),
                    # 抓包里默认路径是 `null`、带 flag 的是一个数组。原样保持。
                    'extra': list(flags) or None,
                    'paid': is_paid(node, flags),
                    'free_flag': next((f for f in flags if f in FREE_ENTRIES), None),
                    'writes': write_effect(node, flags),
                    'writes_disk': would_write(node, flags),
                    'needs_value': next((VALUED_FLAGS[f] for f in flags
                                         if f in VALUED_FLAGS), None),
                    'required_inputs': required_inputs(node, flags),
                })
    return out


#: 合成键 → 它的构造函数。桶二那几个常量的去处就是这里。
_COMPOSITE: dict[str, Any] = {
    'spoiler_disclosure': _spoiler_disclosure,
    'job_kinds': job_kinds,
    'actions': actions,
}


# ─────────────────────────────────────────────────────────────────────────────
# 四、整份
# ─────────────────────────────────────────────────────────────────────────────

def payload() -> dict[str, Any]:
    """`GET /api/enums` 的全部内容。**纯函数**，每次从当前 `NODES` 现算。

    ⚠ **它刻意没有 Pydantic 响应模型。** 它的键集就是它的内容；写成一个
    五十来个字段的 `StrictModel`，等于把「自动发布」重新变回一份手写清单，
    而那正是本模块开头论证过要消灭的东西。它的守卫是超集测试，不是类型。
    这一条也写在 `docs/schema/_api.md` 里，免得下一个人来「补齐」它。
    """
    out: dict[str, Any] = {}
    for name, value in public_constants().items():
        buckets = [t for t, table in (('_CONSUMED', _CONSUMED),
                                      ('_WITHHELD', _WITHHELD),
                                      ('_PUBLISHED_SCALARS', _PUBLISHED_SCALARS))
                   if name in table]
        if len(buckets) > 1:
            # ⚠ 三张表重叠时**谁赢取决于本函数的 if 顺序**——那正是「同一件事两个落点」
            # 的最坏形态：改一次顺序就换一个答案，而没有任何东西会出声。
            raise ValueDomainRefused(f'{name} 同时登记在 {buckets} 里，两张表必须互斥')
        if buckets == ['_CONSUMED'] or buckets == ['_WITHHELD']:
            continue
        if buckets == ['_PUBLISHED_SCALARS']:
            if isinstance(value, (tuple, dict)):
                raise ValueDomainRefused(
                    f'{name} 是容器却登记在 `_PUBLISHED_SCALARS` 里。'
                    f'那张表是**标量的例外表**——容器本来就自动发，'
                    f'放进去只会让人以为它需要例外')
            out[name.lower()] = value
            continue
        out[name.lower()] = _jsonable(name, value)
    for key, build in _COMPOSITE.items():
        if key in out:
            raise ValueDomainRefused(
                f'合成键 {key!r} 与一个自动发布的常量撞了——'
                f'后写的那份会静默覆盖前一份，而两份说的不是同一件事')
        out[key] = build()
    return out
