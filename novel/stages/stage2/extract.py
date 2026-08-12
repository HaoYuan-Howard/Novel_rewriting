#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract.py —— `stage2.global` 与 `stage2.blueprint` 的节点函数。

【本层的两个节点、九条路】

    `stage2.global`     `--migrate`      0 成本：重跑确定性归一（称谓回取 / 章号抬升 /
                                          区分词剥离 / 埋点裁剪），并把账写进 `_provenance`
                        `--drop`         0 成本：人工裁掉某条 secret / 规则，**理由必填**
                        `--purge-role`   **付费**：语义判官双向重划分 `role`/`fate`
                        （默认）          **付费**：一次调用产全书事实底座

    `stage2.blueprint`  `--check-only`   0 成本复检（**两份产物一起读**）
                        `--prompt-only`  0 成本：摊开这一次要发出去的两段
                        `--migrate`      0 成本：重跑 `present_names` → `present_char_ids`
                                          的解析与锚点回取
                        `--apply-edit`   0 成本：把人改过的那一份收回来、重判
                        `--chapters`     定向重生成那几章（**付费**，配 `--replay-cache`）
                        （默认）          **付费**：一次调用产一章

⚠⚠ **`--check-only` 只挂在 `stage2.blueprint` 上**（`test_every_ported_stage_has_exactly_one_check_node`
要求每层恰好一个），而那一相**两份产物一起读**：判 `canonical_unique` 要 global，
判 `present_ids_dangling` 要 registry ⇒「global 的判据没有 0 成本复检」这句话不成立。

⚠⚠ **判定的唯一落点是 `checks.check()`。** 落盘闸门 / 复检 / `--migrate` 之后的重判
走的都是它——三条路各判一遍就会长出三把尺子。原型这一层正是这么坏的：
`n2_global` 与 `n2_blueprint` 各带一段自己的校验，而权威那份在 `check_blueprints.py` 里，
**三处对 `address.by` 的判定互不相同**。

⚠⚠ **付费的【产出】那几条今天落显式终态**（`RunModeNotImplemented`）。
F5 这一期移植的是**契约、prompt、校验器**三样：判据表 75 条已经在跑真数据，
prompt 三条臂逐字节对过，而**生成侧**（`ask_json` 的响应模型 · 落盘前的称谓归一与
verbatim 回取 · 逐章批次与定向重生成）是另一批代码，一行都还没写。
**说「还没接」比给一条半成品的付费路要诚实**——后者会在真花掉一次钱之后才发现产不对。
⇒ 报错里逐条写出缺的是哪几件（见 `_NOT_WIRED`）。
"""
from __future__ import annotations

from typing import Any

from contracts.schema.report import CheckReport
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from novel import paths
from novel.errors import RunModeNotImplemented, UpstreamMissing
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2 import checks
from novel.store import ArtifactStore

GLOBAL_REL = 'stage2/global_grounding.json'
BLUEPRINT_REL = 'stage2/chapter_blueprints.json'
MAIN_TEXT = 'stage1/main_text.json'

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(BLUEPRINT_REL)

#: 付费产出那几条路今天缺的是什么。**逐条写出来**，而不是一句「没实现」——
#: 下一个人要据此判断「接它」是多大一件事。
_NOT_WIRED = (
    '① `ask_json` 的响应模型（`GlobalGrounding` / `ChapterBlueprints` 减掉 `_provenance`）；'
    '② 落盘前的确定性归一：`address` 四项回取（`by` 兜底 / `surface` 最长子串回取 / '
    '`from_chapter` 只往后抬 / `canonical` 剥区分词）与 `setup_chapters` 裁剪，'
    '**它们各自的账要同批落进 `_provenance`**——判据断言的是那些计数，不是字段值；'
    '③ `present_names` → `present_char_ids` 的解析（`novel/core/naming.py` 已备齐纯函数）；'
    '④ verbatim 回取的三笔账（`verbatim_rewrites` / `verbatim_recovered` / `verbatim_failures`，'
    '契约与 `novel/core/spans.py::_find_or_recover` 都已就位，缺的是调用它的落盘路）'
)


def _paid_not_wired(what: str) -> RunModeNotImplemented:
    return RunModeNotImplemented(
        f'{what} 是**付费产出**路径，本仓还没有接它的线。\n'
        f'⚠ 本层已经移植的是**契约、prompt、校验器**三样：'
        f'75 条判据在跑、三条臂的 system 与原型逐字节对过、'
        f'`--check-only` / `--migrate` / `--apply-edit` / `--drop` 这几条 0 成本路都通。\n'
        f'→ 缺的是：{_NOT_WIRED}\n'
        f'⚠ **不给一条半成品的付费路**：那会在真花掉一次钱之后才发现产不对')


# ─────────────────────────────────────────────────────────────────────────────
# 一、上游与判定 —— **唯一落点**
# ─────────────────────────────────────────────────────────────────────────────

class _Both:
    """两份 stage2 产物 + 正文。**装载与判定分开**，几条路共用这一份。

    ⚠ 三份都**允许缺**，缺了不抛：`upstream_missing` 那一条 HARD 判据就是为此存在的，
    而 `checks.check()` 会让依赖那一份的判据落 `unver`。
    **在这里抛掉，报告就永远说不出「缺了哪一份」**——那正是原型
    「什么都没查却打 PASS」的反面错误。
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.global_g = _read_raw(store, GlobalGrounding, GLOBAL_REL)
        self.blueprints = _read_raw(store, ChapterBlueprints, BLUEPRINT_REL)
        self.main_text = _read_main_text(store)

    def judge(self) -> list[Outcome]:
        """跑全部 75 条。**唯一判定落点**——落盘闸门与复检共用它。"""
        return checks.check(global_g=self.global_g, blueprints=self.blueprints,
                            main_text=self.main_text)


def _read_raw(store: ArtifactStore, model: type, rel: str) -> dict[str, Any] | None:
    """读一份产物的**原始 dict**（顺带过一遍载体）。

    ⚠⚠ **返回 dict 不返回模型**：判据吃 dict（`checks.py` 文件头那条），
    一旦跑在已过 Pydantic 的对象上，「必填齐全」那一类在结构上构造不出会红的样本。
    ⚠ 载体拒收时**照常抛**：那份产物存在的意义就是提醒人这条路坏了，
    不许静默降级成一份「PASS」。
    """
    if not store.exists(rel):
        return None
    _art, raw = store.read_own(model, rel)
    return raw


def _read_main_text(store: ArtifactStore) -> list[dict[str, Any]] | None:
    if not store.exists(MAIN_TEXT):
        return None
    art = store.read(MainText, MAIN_TEXT)
    return [s.model_dump(by_alias=True) for s in art.root]


#: 两份产物的记账键 → 报告的记账节。⚠ **只放产物里真有的键**：
#: 键不在 ⇒ 报告打「未记录」，那与「记了 0 次」不是一回事（`Q-2` 的三态）。
_GLOBAL_KEYS = ('address_by_fallback', 'address_surface_rewrites',
                'address_from_chapter_raised', 'canonical_distinguishers_stripped',
                'setup_chapters_pruned', 'human_adjudicated')
_BLUEPRINT_KEYS = ('verbatim_rewrites', 'verbatim_recovered', 'verbatim_failures',
                   'partial_regen_chapters', 'repairs')


def _accounting_of(both: _Both) -> dict[str, Any]:
    """⚠ **本层的记账横跨两份产物**，而报告只有一节 —— 这是全链唯一一处。
    理由与「判据表只有一张」同源：两个节点是**同一层**，把账拆成两节，
    「这一层落盘前改写了多少」就要读两屏才拼得出来。
    """
    got: dict[str, Any] = {}
    for src, keys in (((both.global_g or {}).get('_provenance') or {}, _GLOBAL_KEYS),
                      ((both.blueprints or {}).get('_provenance') or {}, _BLUEPRINT_KEYS)):
        got.update({k: src[k] for k in keys if k in src})
    return got


def _report(ctx: RunContext, store: ArtifactStore, both: _Both,
            outcomes: list[Outcome]) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是正式产物：
    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（正式稿本来还不存在），
    此时拿正式路径去判新鲜度会抛「不在盘上」——于是 0 成本的提升路
    **在最需要它的那一刻**跑不动。（这条 bug 是 stage7 上撞出来的，本层从一开始照着写。）
    """
    judged = BLUEPRINT_REL if store.exists(BLUEPRINT_REL) else _INVALID
    prov = (both.blueprints or {}).get('_provenance') or {}
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged),
                 outcomes=outcomes, accounting=_accounting_of(both),
                 invalid=store.exists(_INVALID),
                 revalidated=prov.get('revalidated'), origin=prov.get('origin'))


# ─────────────────────────────────────────────────────────────────────────────
# 二、0 成本的那几条
# ─────────────────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """`--check-only`：**读产物不重算**，两份一起读。

    ⚠ 正因为不重算，报告第一节报的新鲜度就是全部的来源交代：
    产物过期时它报出来的一切都是**旧结论**。
    ⚠ **一份都没有时照常出报告**：`upstream_missing` 会红，其余落 `unver`。
    抛掉的话「什么都没查」这件事就只剩一段栈，而它本该是报告上最显眼的一行。
    """
    store = ArtifactStore(ctx)
    both = _Both(store)
    if both.global_g is None and both.blueprints is None:
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {BLUEPRINT_REL} **两份都不在盘上** —— '
            f'先跑 stage2。⚠ 与「缺其中一份」分开抛：缺一份时照常出报告'
            f'（`upstream_missing` 红、其余落 `unver`），而两份都没有时'
            f'连「这本书跑到哪一步了」都无从谈起')
    return _report(ctx, store, both, both.judge())


def build_prompt(ctx: RunContext, *, chapters: tuple[int, ...] = ()) -> tuple[str, str]:
    """`--prompt-only` / `--dry-run`：把这次**要发出去的** `(system, user)` 原样交出来。

    ⚠ 它与付费路**共用同一个构造入口**（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    ⚠ 本层的 `user` 里带着**全书原文**（逐章那一相带到本章为止），
    所以它 0 成本但不便宜：打印出来是几万字。
    """
    from novel.stages.stage2 import prompts

    store = ArtifactStore(ctx)
    mt = _read_main_text(store)
    if mt is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——先跑 stage1')

    if ctx.node == 'stage2.global':
        return prompts.GLOBAL_SYS, build_global_user(ctx.title, ctx.sid, mt)
    targets = tuple(chapters) or tuple(s.get('chapter_ordinal') for s in mt)
    return prompts.BLUEPRINT_SYS, build_blueprint_user(ctx.title, ctx.sid, mt, targets)


def _chapter_index(main_text: list[dict[str, Any]]) -> str:
    """章节索引 —— **一切对章的引用用阅读序章号**。"""
    return '\n'.join(f'第{s.get("chapter_ordinal")}章（原文标号 {s.get("source_chapter_label")}）'
                     f' {len(s.get("text") or "")} 字' for s in main_text)


def _book_text(main_text: list[dict[str, Any]], upto: int | None = None) -> str:
    """正文块 —— **不可变原文，按阅读序章号分块**。

    `upto` 非 `None` 时只切到该章为止。⚠⚠ **逐章收窄是本层的默认且没有旋钮**：
    它是「`event` 面的跨章引用」那条**唯一**的解法（两种通用检测都已证伪，
    见 `docs/schema/stage2.md` §六）。
    """
    return '\n\n'.join(f'【第{s.get("chapter_ordinal")}章】\n{s.get("text") or ""}'
                       for s in main_text
                       if upto is None or (s.get('chapter_ordinal') or 0) <= upto)


def build_global_user(title: str, sid: str, main_text: list[dict[str, Any]]) -> str:
    """全书那一相的 user。**吃全书原文。**"""
    return '\n\n'.join([
        f'《{title}》(source_id={sid})',
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 全书正文（不可变原文，按阅读序章号分块）──\n' + _book_text(main_text),
    ])


def build_blueprint_user(title: str, sid: str, main_text: list[dict[str, Any]],
                         targets: tuple[int, ...]) -> str:
    """逐章那一相的 user。

    ⚠⚠ **这里没有人物表**：`char_id` 反转之后抽取器不引用任何全局编号，
    所以 global 整份不下发。这不只是省输入——**人物表本身带着未来**
    （`char_001.address` 里那个名字可能要到 ch4 才在原文首现，而这份表被喂给了抽 ch1 的那次调用）
    ⇒ 只收窄正文修不掉 `event_early_name`，「收窄上下文」与「去掉 global 依赖」
    是同一个根因的两条腿。
    """
    scope = '、'.join(f'阅读序第{o}章' for o in targets)
    return '\n\n'.join([
        f'《{title}》(source_id={sid})',
        '── 章节索引 ──\n' + _chapter_index(main_text),
        '── 正文（不可变原文，按阅读序章号分块）──\n'
        + _book_text(main_text, upto=max(targets) if targets else None),
        '── 本次任务范围 ──\n只产出以下章的 blueprint'
        '（按此顺序，范围外的章一律不要产出）：\n' + scope,
    ])


def run_apply_edit(ctx: RunContext) -> CheckReport:
    """`--apply-edit`：人改过盘上那一份之后**重判一遍**。0 成本。

    ⚠ 它与 `--check-only` **不是同一条路**，虽然今天两条的动作一样：
    前者的语义是「我改过了，再看一眼」，后者是「什么都没动，复述一遍结论」。
    两条合并之后，报告上就说不出**这一份是不是人改过的**——而那正是
    `_provenance.origin` 那一格要回答的问题。
    ⚠ 本函数今天**不写盘**：写回那一步要连 `origin` 与轮次归档一起做，
    与付费产出共用同一个落盘口，而那个口还没建（见 `_NOT_WIRED` ②）。
    """
    store = ArtifactStore(ctx)
    both = _Both(store)
    if both.blueprints is None:
        raise UpstreamMissing(f'{BLUEPRINT_REL} 不在盘上——没有可以「改过之后重判」的东西')
    return _report(ctx, store, both, both.judge())


# ─────────────────────────────────────────────────────────────────────────────
# 三、还没接线的那几条 —— **说清楚缺什么，不给半成品**
# ─────────────────────────────────────────────────────────────────────────────

def run_global(ctx: RunContext) -> CheckReport:
    raise _paid_not_wired('`stage2.global` 的默认路径（一次调用产全书事实底座）')


def run_purge_role(ctx: RunContext) -> CheckReport:
    raise _paid_not_wired('`stage2.global --purge-role`（语义判官双向重划分 role/fate）')


def run_blueprint(ctx: RunContext, *, chapters: tuple[int, ...] = ()) -> CheckReport:
    what = ('`stage2.blueprint --chapters`（定向重生成那几章）' if chapters
            else '`stage2.blueprint` 的默认路径（逐章抽取）')
    raise _paid_not_wired(what)


def run_migrate(ctx: RunContext) -> CheckReport:
    """`--migrate`：0 成本重跑确定性归一并把账写回。

    ⚠ 它今天**跑不动，而理由与付费那几条不同**：归一本身是 0 成本的纯函数，
    但**它要写盘**，而写回去的那一份必须同时带上归一的账
    （`address_*` 四项 / `setup_chapters_pruned` / verbatim 三笔）——
    **那些账正是判据断言的东西**。少写一笔，判据就会去断言一个不存在的键、落 `unver`，
    于是「0 成本修好了」与「什么都没改」在报告上同形。
    ⇒ 与落盘口同批接（`_NOT_WIRED` ②④），不单独放行。
    """
    raise RunModeNotImplemented(
        f'`{ctx.node} --migrate` 的**归一逻辑**是 0 成本纯函数，而它要写盘，'
        f'写回去的那一份必须同时带上归一的账——**判据断言的就是那些账**。\n'
        f'→ 缺的是：{_NOT_WIRED}\n'
        f'⚠ 少写一笔账，「0 成本修好了」与「什么都没改」在报告上就同形')


def run_drop(ctx: RunContext, *, target: str, reason: str) -> CheckReport:
    """`--drop <id> --reason ...`：人工裁掉某条 secret / 规则。0 成本。

    ⚠ **`--reason` 是值的一部分不是独立 flag**（形态照 `--wontfix`）：
    一次删除没有理由，与悄悄删掉没有区别 —— 而 `_provenance.human_adjudicated`
    那一格存在的全部意义就是「人删掉过东西这件事必须留在报告里」。
    """
    raise RunModeNotImplemented(
        f'`{ctx.node} --drop {target}` 要把裁决理由写进 '
        f'`_provenance.human_adjudicated` 再落盘，而那个落盘口与付费产出共用，'
        f'今天还没接。\n→ 缺的是：{_NOT_WIRED}\n'
        f'⚠ 契约**从没定过 `human_adjudicated` 的子字段形状**'
        f'（`contracts/schema/stage2.py` 里那一格用 `dict[str, str]` **登记这个空缺**）'
        f'——接它之前要先把形状补齐，否则 `extra=\'forbid\'` 在这一格上是失效的')
