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
`DEEP_OVERLAP_MAX`…）对前端只有一个用途——**自己算一个结论**，而那就是第二个判官
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
#: ⚠ **吃掉 = 不再单独发。** 同时发 `invent` 和 `invent_allow`，前端就有两处可取，
#: 而两处必然在某次改动后指向不同的东西——这与本模块要消灭的病是同一个。
_CONSUMED: dict[str, str] = {
    'INVENT_ALLOW': 'invent.allow',
    'INVENT_FORBID': 'invent.forbid',
    'INVENT_PRINCIPLE': 'invent.principle',
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
    # ── stage4 篇幅的两级档位（批 4）──
    'CHAPTER_WEIGHT_RATIO': '⚠ **容器，但仍然不发。** 它是档位 → 倍率的**分配系数**，'
                            '而分配本身由 `measure.allocate_budgets` 做完并落盘'
                            '（`PlanChapter.weight` 是决策、各拍 `budget_chars` 是结果，'
                            '两样都在产物里）。发这张表，前端就能拿它自己算一遍'
                            '「这一章该有多少字」并与产物比对——**那正是第二个判官**'
                            '（`R-11.4`），而真正的那一个是 `budget_band` / `plan_book_band`。'
                            '⚠ 前端要渲染的取值面是 `CHAPTER_WEIGHTS`（它照常发）',
    'UNIT_WEIGHT_FACTOR': '同上，单元级那一张。⚠ 它还有一层：这些系数经过归一化与'
                          '逐字下限重分配之后**不等于**最终比例，发过去会让前端算出一个'
                          '与产物对不上的数，然后把差异当成 bug 报出来',
    # ── stage2（全书事实底座 / 逐章骨架）──
    'CHANGE_TYPES_ENUM': '⚠ **它是一个【开放】枚举，末尾那个 `…` 是语义的一部分**'
                         '（`stage2.md` §1.6）。发过去的后果是方向反的：前端会把它当成'
                         '闭合取值面——渲染成下拉框、或拿它校验一个合法但不在表里的值，'
                         '而「还可以有别的」正是这一格要表达的东西。'
                         '⚠ 它也不是给人看的取值面，而是**发给模型的那句话**：'
                         '唯一消费方是 `novel/stages/stage2/prompts.py` 的 `$CHANGE_TYPES`。'
                         '前端要显示某本书**实际用到**哪几种，那份读数在产物的 `arc_tracks` 里',
    # ── 事件拍 ──
    'BLUEPRINT_ARCHITECTURE': '`stage2.blueprint` 那次调用的**模型可见边界身份**，'
                              '是产地写进 `_provenance.context.architecture` 的一笔账。'
                              '⚠ 前端要显示的是**这份产物是哪个架构产的**，'
                              '而那个值已经随产物落盘、并由 `context_scope` 那条度量'
                              '**无条件报出**。enums 再发一份「当前是哪一版」，'
                              '前端就能拿它自己判「这份产物过期没有」——'
                              '而那正是判据在做的事（`R-11.4` 的第二个判官）',
    'SPAN_RECOVERY': '⚠ **容器，但仍然不发。** 它是 `SourceSpan.recovery` 的取值面，'
                     '而那个字段的**每一条实例值**都随产物落盘了。'
                     '前端渲染一条 span 时读的是实例值，不需要取值面；'
                     '拿到取值面只会诱使它自己给「哪几档算可信」下结论',
    'SPAN_FAILURE_CAUSES': '⚠ **容器，但仍然不发。** 落盘前回取的失败根因取值面。'
                           '它是**产地与判据之间**的词表，前端读到的是'
                           '`verbatim_failed` 那条判据已经分好类的读数',
    'HINGE_FAILURE_ROUTE': '⚠ **容器，但仍然不发。** 「产地说哪句话 → 落到哪条判据」'
                           '的分流表。发过去等于把四分流的**全部**判定逻辑交给前端，'
                           '它可以自己重算一遍 hinge 该算哪种病（`R-11.4` 的第二个判官）',
    'SPEECH_TAG_MAX': '判据阈值（说话标签最大长度）',
    'BEAT_MIN_CHARS': '分预算参数。⚠ 它与下面那条报警线是**故意的两个数**，'
                      '发出去一个、前端用它当另一个，就把那次刻意的区分抹平了',
    'BEAT_NATURAL_MAX': '分预算参数的上界，与 `BEAT_MIN_CHARS` 是同一对。'
                        '⚠ **它与 `BEAT_ALERT_MAX` 差得很远（150 vs 300），而那是故意的**：'
                        '一个指导规划器分配、一个是「离谱到该有人看一眼」的线。'
                        '发出去一个、前端拿它当另一个用，就把那次刻意的区分抹平了',
    'BEAT_ALERT_MIN': '判据阈值（碎拍报警下限）',
    'BEAT_ALERT_MAX': '判据阈值（长拍报警上限）',
    'GROUPING_VERSION': '分组算法的版本串。它是**产地记进来源账的一笔**，'
                        '而那笔账已经随 `_provenance.params` 落在产物里、'
                        '并由 `plan_params` 那条度量报出来。enums 再发一份，'
                        '前端就能拿它自己判「这份计划是不是旧版本切的」——'
                        '而那正是 `params_drift` 那条判据在做的事（`R-11.4` 的第二个判官）',
    # ── stage4 / stage5 ──
    'VERBATIM_BUDGET_FACTOR': '判据阈值（一拍预算至少是逐字原话的几倍）。'
                              '⚠ 它**没有独立标定过**，只由分布长尾支持——'
                              '把一个没校准的数发给前端，等于请它拿这个数去下结论。'
                              '前端要显示的是分布本身，而那份读数由 `verbatim_share` '
                              '那条度量**无条件报出**（`R-5.14`），已经在报告里了',
    'PLAN_BUDGET_BAND': 'Stage4 判据阈值。前端不自行重算预算是否出带',
    'PLAN_RECONSTRUCT_MIN_UNITS': 'Stage4 判据阈值（一章大到几个源内容单元时要做过结构决策）。'
                                  '前端拿到就能自己判「这一章该不该重构」，'
                                  '而那条判定是 `structure_changed`（`R-11.4` 的第二个判官）',
    'ANCHOR_COVERAGE_WARN': 'Stage5 对齐覆盖率的判据阈值。响应已经给出 threshold/state，'
                            '前端只展示后端结论，不重判',
    'DEEP_OVERLAP_MAX': '判据阈值（闭卷臂与原文的重合度上限）',
    'STOP_REWRITE_AFTER': '返工护栏的轮次上限。它是**编排**参数，不是给人看的取值面',
    'REVALIDATE_ONLY_NEW_ERRORS': '一个 bool 开关，说的是重算路怎么判。前端不参与那次判定',
    # ── stage7（人名替换与成书）──
    'MIN_SURFACE': '判据阈值（能进替换面的称呼最短几个字）。前端拿到就能自己判'
                   '「这条映射合不合法」，而那条判据在 stage7',
    'RESIDUAL_HIGH_STEMS': '⚠ **容器，但仍然不发。** `confidence` 就是「命中处第二个字'
                           '在不在这五个字里」——发过去等于把那条判定的**全部**内容'
                           '交给前端，它可以自己重算一遍 `confidence`（`R-11.4` 的第二个判官）。'
                           '前端渲染要的是取值面本身，而 `RESIDUAL_CONFIDENCE` 已经发了',
    'RESIDUAL_INTERRUPT_MARKS': '⚠ **容器，但仍然不发——理由与上面那格逐字相同。** '
                                '它是 `confidence` 的第三个输入（旧姓后紧跟成串的中断号 '
                                '⇒ 一句被打断的称呼）。发过去，前端就凑齐了自己重算 '
                                '`confidence` 的全部材料，而那是第二个判官（`R-11.4`）',
    'RESIDUAL_INTERRUPT_RUN': '中断号成串的最短长度。判据阈值，与上面那格是同一条判定的两半，'
                              '**要发就得一起发**——而那条判定不该出现在前端',
    'RESIDUAL_HIT_CHARS': '探针报出的命中片段长度。它是产地口径，不是取值面',
    'RESIDUAL_CONTEXT_CHARS': '上下文半径。**只影响可读性**，而那份可读文本已经'
                              '随 `residual[].context` 落在产物里了——发这个数字'
                              '只会诱使前端自己去原文里再截一遍',
    'BOOK_CHAPTER_SEP': '成书的章分隔形态。它是**产品形态不是可调参数**，'
                        '而成书是后端写的字节——前端拿它只能用来自己拼一份，'
                        '那就是交付物的第二个落点',
    'BOOK_BLOCK_SEP': '同上',
    'IDENTITY_PLACEHOLDER': '恒等基线的占位符模板。它只在后端跑基线的那几十毫秒里存在，'
                            '**产物里一个字节都不留**（留了就说明基线漏掉了一处）',
    'IDENTITY_BLIND': '⚠ **容器，但仍然不发。** `R-10.7b` 要的是这三句**与基线的结论'
                      '印在一起**，而结论在产物的 `identity.blind` 里（那是当次跑基线时'
                      '的措辞快照）。enums 再发一份就是第二个落点，且那一份**没有'
                      '「与结论同在」这个约束**——前端很容易把它单独摆成一段常驻说明，'
                      '而那正好丢掉这条需求要的东西',
    # ── 必填 flag（2026-08-16 复盘 §六.1）──
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

#: 桶三的**例外** —— 标量里被显式放行的那几个。值说的是「前端拿它做什么」。
#:
#: ⚠ 这张表存在的理由：标量默认不发，而 `UNCOVERED_MIN_CHARS` 前端确实要拿去插值文案
#: ⇒ 需要一个**显式登记过的**例外，而不是把「标量不发」那条规则悄悄放宽。
#: （曾写作「超集测试当场红」，而那条抓包超集测试全仓已不存在——
#: 现在逼这张表被写出来的是 `public_constants()` 的三桶归类，不是它。）
_PUBLISHED_SCALARS: dict[str, str] = {
    'UNCOVERED_MIN_CHARS': '抓包里有它。前端把它插值进一句说明文案'
                           '（「未覆盖 ≥30 字的段」），**不做判定**',
}


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

def _invent() -> dict[str, Any]:
    """三个常量合成一个键。**形状照抄抓包**（`allow` / `forbid` / `principle`）。"""
    return {'allow': list(inv.INVENT_ALLOW),
            'forbid': list(inv.INVENT_FORBID),
            'principle': inv.INVENT_PRINCIPLE}


def _spoiler_disclosure() -> dict[str, Any]:
    """TODO-25 display-only boundary, derived from its single invariant."""
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in inv.SPOILER_DISCLOSURE.items()
    }


def job_kinds() -> dict[str, dict[str, Any]]:
    """作业单元表。**键是节点 id**（`stage3.style`），不是原型那 12 个 `n1..n9`。

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
                   'section': s.section, 'modes': list(s.modes),
                   'flags': list(s.flags), 'produces': list(s.produces)}
            for node, s in NODES.items()}


def actions() -> list[dict[str, Any]]:
    """界面上摆得出来的每一个按钮 = `节点 × (默认路径 + 每个 flag) × 每条臂`。

    **字段形状照抄抓包，只做加法**（§一「命名体系」第 5 条：形状不变、只有 `kind` 的取值变）：
    `kind` / `extra` / `label` / `paid` / `free_flag` 五格原样，
    新增 `mode` / `writes` / `writes_disk` / `needs_value` 四格。

    ⚠ 每一格都是**派生**的，一个字都不手写：
      · `label`       ← `nodes.step_label`  —— 与作业记录里那一步共用（`R-11.8`）
      · `paid`        ← `nodes.is_paid`     —— 贵贱只有一处算（`R-11.7`）
      · `writes`      ← `nodes.write_effect`—— 空串 = 不写盘
      · `writes_disk` ← `nodes.would_write` —— 前端点名要的那一格（`web/DELIVERY.md`）：
        它今天在 `web/src/constants/actions.ts` 手抄成了一张表，而那份注释自己写着
        「`/api/enums` 目前没有 `writes_disk` 字段…所以这个分类只能在前端做一张表」。

    ⚠ **flag 的那一句人话不放进本表。** `free_entries` / `paid_flags` / `valued_flags` /
    `write_effects` 四张表已经整份下发了，前端按 flag 去查即可——
    同一份数据放两处就是第二个落点（与 `CheckReport` 砍掉四个 sev 桶是同一条裁决）。

    ⚠ `extra` 里的带值 flag **不带值**（`--apply-edit` 而不是 `--apply-edit <路径>`）：
    那个值是人在点下去的那一刻才给的。`needs_value` 就是用来说这件事的。
    """
    # These flags are audit credentials/locks, never a runnable operation by
    # themselves.  They remain in ``job_kinds[].flags`` and are exposed on the
    # operation that requires them through ``required_inputs`` below.
    credential_flags = {'--actor', '--reason', '--subject-sha256'}

    def required_inputs(node: str, flags: Sequence[str]) -> list[dict[str, str]]:
        """Missing values the caller must collect before *execution*.

        This is deliberately derived from the same ``missing_required`` helper
        used by ``validate_run`` and preview placeholders.  Publishing the
        descriptors on each concrete action does not give the browser a second
        required-flag table: it only tells the action renderer which human
        inputs to collect; the server remains authoritative at submit time.
        """
        return [
            {'flag': flag, 'description': VALUED_FLAGS[flag]}
            for flag in missing_required(node, flags)
        ]

    out: list[dict[str, Any]] = []
    for node, spec in NODES.items():
        for mode in (spec.modes or (None,)):
            independent = [f for f in spec.flags
                           if f not in {'--deep', '--shallow-rest'}
                           and f not in credential_flags]
            for flags in [()] + [(f,) for f in independent]:
                out.append({
                    'kind': node,
                    'mode': mode,
                    'label': step_label(node, flags, mode=mode),
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
    composite = ('--deep', '1', '--shallow-rest')
    out.append({
        'kind': 'stage5.write',
        'mode': None,
        'label': step_label('stage5.write', composite),
        'extra': ['--deep', '--shallow-rest'],
        'paid': is_paid('stage5.write', composite),
        'free_flag': None,
        'writes': write_effect('stage5.write', composite),
        'writes_disk': would_write('stage5.write', composite),
        'needs_value': VALUED_FLAGS['--deep'],
        'required_inputs': required_inputs('stage5.write', composite),
    })
    return out


#: 合成键 → 它的构造函数。桶二那几个常量的去处就是这里。
_COMPOSITE: dict[str, Any] = {
    'invent': _invent,
    'spoiler_disclosure': _spoiler_disclosure,
    'job_kinds': job_kinds,
    'actions': actions,
}


# ─────────────────────────────────────────────────────────────────────────────
# 四、整份
# ─────────────────────────────────────────────────────────────────────────────

def payload() -> dict[str, Any]:
    """`GET /api/enums` 的全部内容。**纯函数**，每次现算（57 条 action，成本可忽略）。

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
