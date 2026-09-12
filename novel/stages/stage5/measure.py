#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— stage5 的**确定性核心**：归一链 · 重合度 · 篇幅带 · 成稿装载。
**逐字移植自原型（`Stage5/stage5_common.py`），注释一起搬。**

【本模块只放纯函数】它不碰 LLM、不写盘，判据与三条 0 成本路都从这里取数。
⚠ 落盘闸门、`--recheck`、`--revalidate`、`--apply-edit`、全书验收**读同一份实现**——
各自抄一遍，其中一处就会悄悄不再归一（原型正是这样：`--recheck` 把定稿原样搬过去，
于是候选归一了、而真正拼进成书的那一份没有，**报告全绿而交付的书里版式是坏的**）。

⚠⚠ **成稿的装载器住在这里，而不是在读它的那两层**：`stage5/chNN.json` 是
本层的产物，`stage6`（开篇文案要看前几章）与 `stage7`（成书要看全部）都是**下游读者**。
F4 之前这份枚举在 stage6 / stage7 里各有一份**逐字相同**的拷贝（连
`draft_projection` 的函数体都一样）——第三份写下去就是判据 25 的教科书。
"""
from __future__ import annotations

from contracts.artifacts import artifact_rel

import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from contracts.invariants import BOOK_BAND, DRAFT_BAND
from contracts.schema.stage5 import Chapter, DraftConsumed, JudgedDraft
from novel.core.bands import body_bands
from novel.core.naming import KINSHIP
from novel.core.normalize import (
    dominant_quote_style, normalize_paragraphs, normalize_punctuation, normalize_quotes,
    quote_pair,
)
from novel.core.text import norm, quote_count, window_overlap
from novel.errors import ArtifactUnreadable
from novel.store import ArtifactStore


# ─────────────────────────────────────────────────────────────────────────────
# 一、成稿的装载 —— 走 `read_judged`，绝不走会记 `inputs` 的那条
# ─────────────────────────────────────────────────────────────────────────────

def draft_projection(data: object) -> object:
    """本层（以及下游两层）**真正消费**的成稿投影 —— 喂给 `store.read_judged(project=)`。

    来源审计元数据变了**不要求任何人重做** ⇒ 只投影章序与正文。

    ⚠⚠ 这条投影是「判过期」的**全部依据**：整文件 `sha1` 只作审计。
    不参与消费的元数据变化可以改变整文件 `sha1`，但不能让正文投影误报过期。

    ⚠ 它吃**盘上那份原始 dict**（不是模型）：判过期时要对一份
    **可能已经不合当前 schema** 的上游重算同一个投影。
    """
    if not isinstance(data, dict):
        return None
    segments = data.get('segments') or []
    text = '\n\n'.join(
        str(item.get('text') or '').strip()
        for item in segments if isinstance(item, dict)
    ).strip()
    return {'output_ordinal': data.get('output_ordinal'), 'text': text}


def draft_consumed_of(rec: dict[str, Any]) -> DraftConsumed:
    """成稿 → `judged[].consumed` 那一格（契约里现成的模型，不另造一份）。"""
    from novel import prov
    return DraftConsumed(
        output_ordinal=int(rec.get('output_ordinal') or 0),
        text_sha1=prov.fingerprint(str(rec.get('text') or '')),
    )


def is_legacy_stage5_rel(rel: str) -> bool:
    """Whether a provenance/path key belongs to the retired two-arm Stage5 layout."""
    normalized = str(rel).replace('\\', '/').lstrip('./')
    return normalized.startswith(('stage5/deep/', 'stage5/shallow/'))


def legacy_stage5_paths(source: ArtifactStore | Path) -> list[str]:
    """列出旧 `stage5/deep|shallow` 布局的根，不枚举内部文件。

    这是 review/state/API 共用的纯发现面：旧产物必须显式说
    `legacy_incompatible`，不能因 canonical 装载器看不见它们而被说成 missing。
    消费方只需要知道「哪一条旧臂存在」；空目录仍是旧布局证据。
    """
    path_of: Callable[[str], Path]
    if isinstance(source, Path):
        path_of = lambda rel: source / rel
    else:
        path_of = source.path
    return [
        f'stage5/{old_arm}'
        for old_arm in ('deep', 'shallow')
        if path_of(f'stage5/{old_arm}').is_dir()
    ]


def load_drafts(store: ArtifactStore, *, require_current: bool = True
                ) -> tuple[dict[int, dict[str, Any]], list[str],
                           dict[str, JudgedDraft]]:
    """全部 canonical 成稿 → `({output_ordinal: rec}, invalids, judged)`。**唯一落点。**

    ⚠⚠ **一律走 `store.read_judged`，绝不走 `store.read`**：canonical 路径只有
    两级，可以被消费指纹无损反查。

    ⚠ `.INVALID.json` **排除，但要报计数**：「排除了 3 份废稿」与「一份废稿都没有」
    必须分得开。

    ⚠ 旧双臂目录由 `legacy_stage5_paths` 单独发现；本函数不读它们、
    不替人选一份，更不把旧字节伪装成 current v3。
    """
    by_ord: dict[int, dict[str, Any]] = {}
    judged: dict[str, JudgedDraft] = {}
    invalids: list[str] = []
    directory_rel = 'stage5'
    pattern = os.path.join(store.path(directory_rel), 'ch*.json')
    for p in sorted(glob.glob(pattern)):
        base = os.path.basename(p)
        rel = f'{directory_rel}/{base}'
        if re.fullmatch(r'ch\d+\.INVALID\.json', base):
            invalids.append(rel)
            continue
        if not re.fullmatch(r'ch\d+\.json', base):
            # `.rejected.json` 与其他证据不是正式成稿。
            continue
        chapter, fp, _cfp = store.read_judged(
            Chapter, rel, project=draft_projection, require_current=require_current)
        canonical = artifact_rel('stage5.chapter', nn=chapter.output_ordinal)
        if rel != canonical:
            raise ArtifactUnreadable(
                f'{rel} 声明 output_ordinal={chapter.output_ordinal}，应落在 {canonical}')
        if chapter.output_ordinal in by_ord:
            raise ArtifactUnreadable(
                f'第 {chapter.output_ordinal} 章出现多份 canonical 成稿，不猜哪份是成品')
        rec = chapter.model_dump(by_alias=True)
        rec['text'] = chapter.text  # deterministic in-memory projection, never a disk authority
        rec['_relpath'] = rel
        by_ord[chapter.output_ordinal] = rec
        judged[rel] = JudgedDraft(
            output_ordinal=chapter.output_ordinal,
            round=getattr(chapter.provenance, 'round', 1) or 1,
            sha1=fp, consumed=draft_consumed_of(rec))
    return by_ord, sorted(invalids), judged


# ─────────────────────────────────────────────────────────────────────────────
# 二、落盘前那条确定性改写链
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NormalizeAccount:
    """归一链这一次改了多少。**它是「一次运行的读数」，不是产物的属性。**

    ⚠⚠ 这就是被裁掉的那个 `mech` 块**换的落点**：原型把它落进每一份成稿，
    而全仓 **0 个读者、0 个报告出口**，盘上 31/31 四项全 0。
    ⇒ 三个计数器进**校验报告的一行**（`Q-1`：空也要报）；
    `quote_problems` 不在这里边——**它不是记账，是一条结论**，已升成判据
    `quote_unbalanced`（「引号配对异常、未归一、留着交人工」）。
    把一条结论塞进记账块，正是它既没有出口、也没有守卫的原因。

    ⚠ `punct_fixed` / `quotes_fixed` 数的是**字符**，`paras_fixed` 是 0/1
    （段落归一是全章一次的事，没有「改了几处」可言）。
    """

    punct_fixed: int = 0
    quotes_fixed: int = 0
    paras_fixed: int = 0
    quote_problems: list[str] = field(default_factory=list)

    def counters(self) -> dict[str, int]:
        """进 `ctx.accounting` 的那三格。**`quote_problems` 不在里面。**"""
        return {'punct_fixed': self.punct_fixed, 'quotes_fixed': self.quotes_fixed,
                'paras_fixed': self.paras_fixed}

    def __add__(self, other: NormalizeAccount) -> NormalizeAccount:
        """两遍归一的账**相加不覆盖**。

        ⚠ 链子是「归一 → 收缩(LLM) → 再归一 → 校验」，只留后一份会让
        「首稿修了 9 处引号」凭空消失——而那一份账正是「模型这一轮吐出来的东西
        有多脏」的唯一读数。
        """
        return NormalizeAccount(
            punct_fixed=self.punct_fixed + other.punct_fixed,
            quotes_fixed=self.quotes_fixed + other.quotes_fixed,
            paras_fixed=self.paras_fixed + other.paras_fixed,
            quote_problems=[*self.quote_problems, *other.quote_problems],
        )


def target_quote_style(measurable: dict[str, Any] | None,
                       source_text: str) -> tuple[str, str] | None:
    """本书成稿应归一到哪种引号体 → `(主, 副)`；判不了返回 `None`（**不猜**）。

    主样式在**全书源文**上按各体实测频次取最高者（确定性、原文有据）。

    ⚠ **`dialogue_ratio_applicable=False` 的书直接返回 `None`**：那几本的引号是
    **入库前被人工删掉的**（语料缺陷，不是作者选择）⇒ 没有「本书的引号体」可言，
    硬归一等于按一个不存在的基准去改写正文。
    """
    if not (measurable or {}).get('dialogue_ratio_applicable'):
        return None
    return quote_pair(dominant_quote_style(source_text))


def normalize_chapter(text: str, *, quote_style: tuple[str, str] | None,
                      sep_chars: float | None) -> tuple[str, NormalizeAccount]:
    """成稿的确定性后处理链：标点 → 引号体 → 段落分隔。返回 `(新文本, 账)`。

    **算法在 `novel/core/normalize.py`，这里只决定「拿谁当基准」**——
    stage6 的同位物 `normalize_hook` 基准是原作文案段，本层是全书正文。

    ⚠⚠ **三步都必须跑在收缩【之前】**，这一条是实测出来的：
    `normalize_paragraphs` 会把单换行改成空行分段，**它往文本里加字符**，
    而 `budget_chars` 的口径含空白。曾有一章归一前 1408 字（未触发收缩，正确），
    段落归一 +101 个换行 → 1509 字 → 被落盘闸门阻断——**它是被「修版式」这个动作
    推过线的**，而唯一能救它的收缩已经跑完并正确地拒绝了。写手无从改，重跑也白搭。

    ⚠ 对保真是安全的，**而且是由构造保证的**：四套保真校验与重合率全部工作在
    【内容字符空间】（`core.text.norm()` 把标点整体剥掉），所以动标点不可能影响其中任何一条。
    """
    text, punct = normalize_punctuation(text)
    if quote_style:
        text, quotes_fixed, problems = normalize_quotes(text, *quote_style)
    else:
        quotes_fixed, problems = 0, []
    text, changed = normalize_paragraphs(text, sep_chars)
    return text, NormalizeAccount(punct_fixed=punct, quotes_fixed=quotes_fixed,
                                  paras_fixed=int(changed), quote_problems=problems)


# ─────────────────────────────────────────────────────────────────────────────
# 三、篇幅与重合度 —— **口径在这里，算法在 core**
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChapterBasis:
    """当前写作任务的唯一运行时基准。

    ``source_ordinal`` 只认 ``output_ordinal``；``source_chars`` 只认当前
    Stage1 源章正文；``budget_chars`` 只认场景预算之和。WriterContext v7 与
    Chapter v3 都不保存这三份副本；API 只在读取时投影。

    源字数是含空白的整章长度，规划预算仍受各源 span 合计约束；两者口径不同。
    不因删除展示副本而把预算基准改为整章长度，否则会误拒合法计划。

    ``source_text is None`` 只服务 ``target_range`` 这类只需预算的纯函数；
    真实 Stage5 Book 一定传源章表，并由 ``require_chapter_basis`` 对账。
    """

    source_ordinal: int | None
    source_text: str | None
    source_chars: int | None
    budget_chars: int | None


def chapter_basis(
    entry: dict[str, Any],
    source_text: dict[int, str] | None = None,
) -> ChapterBasis:
    """从章序、Stage1 正文与场景预算现算 Stage5 执行基准。"""
    raw_ordinal = entry.get('output_ordinal')
    source_ordinal = int(raw_ordinal) if raw_ordinal is not None else None

    scenes = entry.get('scenes')
    if isinstance(scenes, list) and scenes:
        scene_budgets = [
            scene.get('budget_chars') if isinstance(scene, dict) else None
            for scene in scenes
        ]
        budget_chars = (
            sum(int(value) for value in scene_budgets)
            if all(value is not None for value in scene_budgets) else None
        )
    elif entry.get('budget_chars') is not None:
        # 保留 target_range 对「只给一个预算」的纯函数输入的旧契约。
        # WriterContext v7 要求 scenes 非空，真实路径不会走这条。
        budget_chars = int(entry['budget_chars'])
    else:
        # 部分机械 helper 的反例夹具只构造自己所需的字段；源章派生不应
        # 因无关的预算缺失而失败。真实 Book 在 load_book 对账时仍关闭式拒绝。
        budget_chars = None

    if source_text is None:
        return ChapterBasis(
            source_ordinal=source_ordinal,
            source_text=None,
            source_chars=None,
            budget_chars=budget_chars,
        )
    if source_ordinal is None:
        raise ValueError('output_ordinal 缺失，无法派生源章')
    if source_ordinal not in source_text:
        raise ValueError(f'Stage1 缺少第 {source_ordinal} 章，无法派生 Stage5 基准')
    source = source_text[source_ordinal]
    if not isinstance(source, str):
        raise ValueError(f'Stage1 第 {source_ordinal} 章正文不是字符串')
    return ChapterBasis(
        source_ordinal=source_ordinal,
        source_text=source,
        source_chars=len(source),
        budget_chars=budget_chars,
    )


def require_chapter_basis(
    entry: dict[str, Any], source_text: dict[int, str],
) -> ChapterBasis:
    """真实执行要求当前源章与正数场景预算齐全，未知读数不能进入写作与审阅。"""
    basis = chapter_basis(entry, source_text)
    if (basis.source_ordinal is None or basis.source_chars is None
            or basis.budget_chars is None or basis.budget_chars <= 0):
        raise ValueError('写作基准必须提供完整 Stage1 源章表与正数场景预算')
    return basis


def chapter_source(entry: dict[str, Any], source_text: dict[int, str]) -> str:
    """本章对应的源章原文（源章序由 `output_ordinal` 唯一派生）。

    ⚠ **校验器读原文的唯一用途**：算重合度与源章引语数。**它永不进 prompt**——
    闭卷臂看不到原文这件事是由 `prompts.build_material` 保证的，不是由这里。
    """
    source = chapter_basis(entry, source_text).source_text
    assert source is not None
    return source


def mandated_verbatim(entry: dict[str, Any]) -> str:
    """按已登记场景端点取得字面义务，与写作保真判据共用约束投影。"""
    from novel.stages.stage5.plot_checks import fidelity_scenes
    return ''.join(dict.fromkeys(value for scene in fidelity_scenes(entry)
                                  for value in scene['verbatim']))


def overlap_ratio(text: str, entry: dict[str, Any],
                  source_text: dict[int, str]) -> float | None:
    """成稿与源章原文的 20 字窗口重合率，**扣除被要求逐字复现的那部分**。

    ⚠⚠ **为什么必须扣**：这个指标是在保真材料还到不了写手的时代定的，那时任何重合
    都意味着誊写。现在 `verbatim` 是**要求**写手逐字写进去的，两者混在一起就分不清
    「写手在抄原文」（坏）与「写手正确复现了指定台词」（好）——原型实测某章重合 19.9%，
    而 52 个重合窗口里属于非 `verbatim` 的是 **0** 个：它不是誊写，
    **恰恰是保真机制在正常工作**。不扣的话，保真做得越好这个指标越难看。

    ⚠ 返回 `None` 是「量不了」（正文短于一个窗口），**不是「重合 0」**。
    """
    return window_overlap(text, chapter_source(entry, source_text),
                          exclude=mandated_verbatim(entry))


def target_range(entry: dict[str, Any]) -> tuple[int, int]:
    """本章的篇幅带 `(下限, 上限)`。

    **`DRAFT_BAND` 就是唯一执行带，没有第二层宽限。**
    """
    budget = chapter_basis(entry).budget_chars
    if budget is None:
        # 保持这个纯函数原有的错误契约：缺格是 KeyError，显式 null 是 TypeError。
        budget = int(entry['budget_chars'])
    if budget <= 0:
        raise ValueError('budget_chars 必须是正整数')
    lo, hi = DRAFT_BAND
    return round(budget * lo), round(budget * hi)


#: 段落分隔符字面量：reflow 与槽位计段共用（避免在行内写转义）。
NL = '\n'


def reflow_shadow(text: str, *, target: int) -> str | None:
    """whitespace-only reflow 的 shadow 计算；**只算不采**（P1-1 观测面）。

    把段列表向目标段数调整：段多时合并相邻段、段少时在句末边界拆分。
    三条硬约束：
      - **非空白字符序列逐字不变**（只增删换行）——函数内自检，不满足即返回 None；
      - **不跨对白合并**：任一待并段含引号字符就不并；
      - **只在句末标点后拆分**，不在词中间或引号内断开。

    返回 None 表示这一章没有可行的 whitespace-only 重排；shadow 消费方必须
    把 None 与「可行」分开记录，不得静默折叠。在语料标定完成前，reflow 的
    结果**不写任何产物、不改变任何验收结论**。
    """
    import re as _re

    _QUOTES = '「」『』“”"'''  # noqa: P103 —— 对白边界字符集
    _SENT_END = _re.compile(r'[。！？…!?]["」』”]?\s*$')

    paragraphs = [line for line in str(text).split(NL) if line.strip()]

    def non_ws_sha(value: str) -> str:
        import hashlib
        return hashlib.sha256(
            ''.join(value.split()).encode('utf-8')).hexdigest()

    before = non_ws_sha(text)
    out = list(paragraphs)

    def has_quote(line: str) -> bool:
        return any(char in _QUOTES for char in line)

    # 段太多 → 从最短的非对白相邻对开始合并，直到达标或无可并。
    while len(out) > target:
        candidates = [
            index for index in range(len(out) - 1)
            if not has_quote(out[index]) and not has_quote(out[index + 1])]
        if not candidates:
            return None
        index = min(candidates, key=lambda i: (len(out[i]) + len(out[i + 1]), i))
        out[index:index + 2] = [out[index] + out[index + 1]]

    # 段太少 → 在非对白段的句末边界拆分；先拆最长段、切点取最接近段心的，
    # 让 shadow 结果尽量均匀。
    _END_PUNCT = '。！？…!?'
    _CLOSE_QUOTE = '」』”'
    while len(out) < target:
        candidates = []
        for index, line in enumerate(out):
            if has_quote(line) or len(line) < 8:
                continue
            for pos, char in enumerate(line[:-1], start=0):
                if char not in _END_PUNCT:
                    continue
                cut = pos + 1
                if cut < len(line) and line[cut] in _CLOSE_QUOTE:
                    cut += 1
                if cut < len(line):
                    candidates.append((len(line), index, cut))
        if not candidates:
            return None
        length, index, cut = max(
            candidates,
            key=lambda item: (item[0], -abs(item[2] - item[0] / 2), -item[1]))
        out[index:index + 1] = [out[index][:cut], out[index][cut:]]

    result = '\n'.join(out)
    if non_ws_sha(result) != before:
        return None  # 自检失败宁可放弃，不产出会改字的 shadow
    return result


def paragraph_slot_plan(entry: dict[str, Any], book: Book) -> list[int]:
    """确定性逐 slot 段落计划；下发面与判据/反馈共同消费的唯一落点。

    - 章节目标 = ``round(budget_chars / paragraph_cost)``，并 clamp 进
      :func:`target_paragraphs` 的合法区间；
    - 每个非空 slot 至少 1 段；剩余段数按场景字数预算做 largest-remainder
      分配，slot 顺序是唯一的平局规则；
    - 分配结果总和精确等于章节目标；
    - slot 数已超过合法段数上限（或目标）⇒ :class:`ParagraphPlanInfeasible`：
      任务在任何 wire 之前就是不可能完成的，不得发给写手。
    """
    from novel.errors import ParagraphPlanInfeasible

    lo, hi = target_paragraphs(entry, book)
    avg_paragraph_chars = book.measurable.get('avg_paragraph_chars') or 20
    paragraph_cost = float(avg_paragraph_chars) + book.sep_chars
    budget_chars = float(entry.get('budget_chars') or 0)
    if budget_chars <= 0:
        budget_chars = (lo + hi) / 2 * paragraph_cost
    target = min(max(int(round(budget_chars / paragraph_cost)), lo), hi)
    scenes = [scene for scene in (entry.get('scenes') or []) if scene]
    if not scenes:
        return [target]
    slots = len(scenes)
    if slots > hi or slots > target:
        raise ParagraphPlanInfeasible(
            f'paragraph_plan_infeasible：本章 {slots} 个场景至少要 {slots} 段，'
            f'而合法段落区间是 {lo}~{hi}（目标 {target} 段）；'
            '必须在 Stage4 计划里合并场景或调整章预算，不得把必败任务发给写手')
    allocation = [1] * slots
    remaining = target - slots
    weights = [max(float(scene.get('budget_chars') or 0), 0.0) for scene in scenes]
    total_weight = sum(weights)
    if remaining > 0 and total_weight > 0:
        remainders: list[tuple[float, int, int]] = []
        handed_out = 0
        for index, weight in enumerate(weights):
            exact = remaining * weight / total_weight
            give = int(exact)
            allocation[index] += give
            handed_out += give
            remainders.append((exact - give, -index, index))
        for _frac, _neg_index, index in sorted(
                remainders, reverse=True)[:remaining - handed_out]:
            allocation[index] += 1
    elif remaining > 0:
        for index in range(remaining):
            allocation[index % slots] += 1
    assert sum(allocation) == target, 'slot 段数分配必须精确等于章节目标'
    return allocation


def target_paragraphs(entry: dict[str, Any], book: Book) -> tuple[int, int]:
    """把篇幅带换算成段落数带，供下发面与落盘判据共同消费。

    `avg_paragraph_chars` 是段内字符，而篇幅预算含段落分隔符；分母必须把两者相加，
    否则目标段数会被系统性抬高。下限至少 3 段、上限至少 4 段，与既有下发契约一致。
    """
    lo, hi = target_range(entry)
    avg_paragraph_chars = book.measurable.get('avg_paragraph_chars') or 20
    paragraph_cost = float(avg_paragraph_chars) + book.sep_chars
    if paragraph_cost <= 0:
        raise ValueError('段均字数与段落分隔符开销之和必须为正数')
    return (max(round(lo / paragraph_cost), 3),
            max(round(hi / paragraph_cost), 4))


def book_range(source_total: int) -> tuple[int, int]:
    """全书篇幅带。

    ⚠⚠ **这里曾写着「与逐章同一个带（`BOOK_BAND == DRAFT_BAND`），不是两个数」，
    那句话自 2026-08-17 用户裁决起就不成立**（0.90~1.10 vs 0.80~1.20），而它一直挂在
    这个函数头上——一条注释宣称的恒等式，代码早就不再满足。两条带答的是两个问题：
    逐章带答「单章允许偏离预算多少」（节奏本来就有胖有瘦），书级带答「全书总量允许偏离
    源书多少」（那是产品承诺，必须紧）。⚠ 逐章放宽而全书收紧是**自洽**的：
    逐章的正负偏差在书级相互抵消。

    ⚠ 2026-08-22 起 `BOOK_BAND` 有了**第二个消费方**：stage4 的 `plan_book_band`
    （`R-5.3c`，全书预算之和 / 全书 span 合计）。**分母不是同一个数**——本函数的
    `source_total` 是整章字数那一支。两个读数各自对带，不许拿来做相等断言。
    """
    lo, hi = BOOK_BAND
    return round(source_total * lo), round(source_total * hi)


def chars_of(text: str | None) -> int:
    """成稿字数：`len(text.strip())`，**含空白**。

    ⚠ 与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径——
    跨口径运算要先对齐。本层的预算 `budget_chars` 也是含空白那一支，两边对得上。
    """
    return len((text or '').strip())


# ─────────────────────────────────────────────────────────────────────────────
# 四、写法密度 —— **只有一个消费方，所以不进 core**
# ─────────────────────────────────────────────────────────────────────────────

#: 明喻标记。⚠ 它的判定线 `+1.0` **没有语料级来源**（对照：`DASH_CORPUS_P95`
#: 由 `calibrate_style_corpus.py` 标定、可 0 成本重跑）⇒ 这一条的阈值是拍的，
#: 而它盘上命中 7/31、恒等基线 5/31——**判别力本来就薄**，调它之前先重标。
_SIMILE_RX = re.compile(r'像|好似|如同|仿佛|似的|一样')

#: 「心里…」这种**解释情绪**而不是**表演情绪**的写法。
_XINLI_RX = re.compile(r'心里|心中|心底')


def simile_per_1k(body: str | None) -> float:
    """明喻标记每千**内容**字。分母口径 = `len(core.text.norm())`，与破折号那条一致。"""
    n = len(norm(body)) or 1
    return len(_SIMILE_RX.findall(body or '')) / n * 1000


def xinli_per_1k(body: str | None) -> float:
    """「心里/心中/心底」每千内容字。"""
    n = len(norm(body)) or 1
    return len(_XINLI_RX.findall(body or '')) / n * 1000


def src_quote_count(entry: dict[str, Any], source_text: dict[int, str]) -> int | None:
    """本章**源章原文**的直接引语条数；源文取不到返回 `None`。

    ⚠ 这个**标量**是可以进 prompt 的（「本章原文有 N 句直接引语」），
    而源章**正文**不行——先例是 `source_chars` 与段落数。标量不泄露文本。
    """
    src = chapter_source(entry, source_text)
    return quote_count(src) if src else None


# ─────────────────────────────────────────────────────────────────────────────
# 五、一本书的上游打包 —— **下发面与判据读同一份**
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Book:
    """四份上游 + 从它们算出来的**书级基线**。下发面（`prompts`）与判据（`checks`）共用。

    ⚠⚠ **书级基线只算一次，缓存在这里**（`_cache`）。这不是性能优化，是判据 25：
    「这本书平均一段多少字 / 破折号多密」在下发面与判据两侧必须是**同一个数**——
    写手按 A 写、校验按 B 判，谁也不会出声。

    ⚠ **基线一律取【书级】而不是【同章源章】**：同章配对会让恒等基线**退化成恒真**
    ——`identity` 那一路把源章原文当成一份成稿跑同一套判据，若判据是「与自己的源章比」，
    源章 vs 源章恒等于 1.00、永不触发，于是它在 `baselines_identity` 里恒为 0/N，
    被读成「判据干净」——**一盏保证会亮的绿灯**，而那正是唯一能逐书判定判别力的机制。
    """

    title: str
    ctx: dict[str, Any]
    #: Stage1 正文即时计算的九叶机械投影；Book 结构上没有定性画像或文案实测入口。
    measurable: dict[str, Any]
    source_text: dict[int, str]
    #: stage2 的**全量** secrets（含一次都没下发过的）。⚠ **收窄下发面是修复，收窄检测器是削弱**：
    #: 从各章 `facts.secrets` 取并集会让一条全书从未下发的 secret 的泄漏检查**永远不执行**，
    #: 而判据照常输出一个「没报违规」。
    secrets: list[dict[str, Any]]
    #: 从当前 Stage2 人物表与 Stage1 全书正文按 Stage4 同一名字投影现场派生。
    characters: dict[str, dict[str, Any]] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict)

    @property
    def chapters(self) -> dict[int, dict[str, Any]]:
        """`{output_ordinal: 章条目}`。"""
        if 'chapters' not in self._cache:
            self._cache['chapters'] = {int(c['output_ordinal']): c
                                       for c in (self.ctx.get('chapters') or [])}
        return self._cache['chapters']

    @property
    def band_of(self) -> dict[str, str]:
        """这本书自己的形态档位（章篇幅 / 句子形态 / 段落形态 …）。

        ⚠ **prompt 里一律给档位词不给数值**——写手要的是「极短段/一句一段」这种可执行的
        形态；给原始数字既没用又会诱导它去凑数。
        ⚠ **不许在 prompt 里硬编码任何一本书的特征**：同样是网文，本语料里章长中位数
        从 425 到 1348 不等，写死「短章体」会把长章书直接压垮。
        """
        if 'bands' not in self._cache:
            self._cache['bands'] = body_bands(self.measurable) if self.measurable else {}
        return self._cache['bands']

    @property
    def first_person_surfaces(self) -> frozenset[str]:
        """**这本书自己**的第一人称自称字面池（`narration_person_*` 的右锚）。

        取自 `writer_context.chapters[].narration[].pronoun_surfaces`，只收
        `grammatical_person == 'first'` 那些段声明的字面 ⇒ 它是**上游给的**，
        不是本模块发明的词表。

        ⚠⚠ **绝不能写死一张「我/咱/本宫/朕」**：那就是关键词表，换一本书就漂。
        stage2 的 prompt 明写这一格要「照原文用字」（`novel/stages/stage2/prompts.py`
        那一行举的正是「我」「咱」「本宫」「朕」「哀家」「老子」）⇒ 取值面由书决定。

        ⚠⚠ **书级并集，不取同章那一份**：判「声明 `third` 的章里混进第一人称」时，
        那一章自己的 `pronoun_surfaces` 恰好是 `null`——第三人称交空数组是合法的
        （`stage2/checks.py::pronoun_surface_unattested` 的 `scanned` 就是这么写的）。
        拿同章那一份当右锚，**被测的那一类永远没有锚可用、判据恒不执行**。
        与 `band_of` 那条「基线一律取书级、同章配对会退化成恒真」是同一个理由的两个实例。

        ⚠ 空集是合法结果（全书没有一段第一人称）⇒ 调用方必须把「池是空的」与
        「池非空但没命中」分开报，别把前者说成「查过、干净」。
        """
        if 'fp_surfaces' not in self._cache:
            self._cache['fp_surfaces'] = frozenset(
                s for entry in self.chapters.values()
                for seg in (entry.get('narration') or [])
                if (seg or {}).get('grammatical_person') == 'first'
                for s in ((seg or {}).get('pronoun_surfaces') or []) if s)
        return self._cache['fp_surfaces']

    @property
    def sep_chars(self) -> float:
        """段落分隔符的平均字符开销（段落归一的基准）。

        ⚠ 它是 `measurable.body.paragraph_sep_chars` 那个**平均值**，而
        `core/bands.py::sep_band_of_text` 明写「别走这个平均值，它段数越少偏得越低」。
        本层照旧走它，理由是**口径必须与存量成稿一致**：盘上 31 份成稿是拿这个数归一过的，
        换一把尺子会让 `--recheck` 把 31 章全部改写一遍，而那是一次 0 成本操作改付费产物的字节。
        ⚠ 那条 off-by-one 的触发条件是「段数 ≤ 3」，而正文章节段数都在几十以上 ⇒ 本层不咬人；
        咬人的是**文案**（本来就短），所以 stage6 才必须绕开它。
        """
        return float(self.measurable['paragraph_sep_chars'])

    @property
    def quote_style(self) -> tuple[str, str] | None:
        """本书成稿应归一到哪种引号体；判不了返回 `None`（**不猜**）。"""
        if 'quote_style' not in self._cache:
            self._cache['quote_style'] = target_quote_style(
                self.measurable, ''.join(self.source_text.values()))
        return self._cache['quote_style']

    @property
    def names(self) -> frozenset[str]:
        """全书主要人物的 `display_name`（`names_absent` 用）。

        ⚠ 用 `display_name` 不用 `canonical`：后者为消重名带了区分词
        （`小宫女(打翻茶盏)`），那是内部唯一键、**从不下发**；写手手里只有 `display_name`。
        """
        if 'names' not in self._cache:
            self._cache['names'] = frozenset(
                n for c in self.characters.values()
                if (n := c.get('display_name')) and c.get('name_distinctiveness') != 'common')
        return self._cache['names']

    @property
    def name_stems(self) -> frozenset[str]:
        """本书的**名字用字池**——`invented_name` 的左锚。

        ⚠ 姓氏池得从书里长出来，**不能写死**：写死一张百家姓就是「关键词表」，
        换一本书（现代言情 / 西幻）立刻失效。
        """
        if 'stems' not in self._cache:
            self._cache['stems'] = frozenset(
                n[0] for c in self.characters.values()
                for n in [c.get('display_name') or '']
                if 2 <= len(n) <= 3 and c.get('name_distinctiveness') in ('high', 'medium')
                and not KINSHIP.match(n))
        return self._cache['stems']

    @property
    def cue_rate(self) -> float | None:
        """全书源文里「冒号收尾段 → 下一段以引号开头」的占比（提示语排版惯例）。"""
        if 'cue' not in self._cache:
            tot = hit = 0
            for s in self.source_text.values():
                paras = [ln.strip() for ln in (s or '').split('\n\n') if ln.strip()]
                for i, p in enumerate(paras):
                    if p.rstrip().endswith(('：', ':')):
                        tot += 1
                        if i + 1 < len(paras) and paras[i + 1][:1] in ('“', '「', '『', '"'):
                            hit += 1
            self._cache['cue'] = (hit / tot) if tot else None
        return self._cache['cue']

    def baseline(self, key: str, fn: Callable[[str], float]) -> float | None:
        """书级基线 = 把**全书源文**当成一篇跑 `fn`。源文缺失 ⇒ `None`（判不了，不猜）。

        **所有「贴着本书」类探针的唯一基线落点。**
        """
        if not self.source_text:
            return None
        if key not in self._cache:
            self._cache[key] = fn(''.join(self.source_text.values()))
        return self._cache[key]
