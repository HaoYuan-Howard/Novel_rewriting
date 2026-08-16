#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— stage5 的**确定性核心**：归一链 · 重合度 · 篇幅带 · 成稿装载。
**逐字移植自原型（`Stage5/stage5_common.py`），注释一起搬。**

【本模块只放纯函数】它不碰 LLM、不写盘，判据与三条 0 成本路都从这里取数。
⚠ 落盘闸门、`--recheck`、`--revalidate`、`--apply-edit`、全书验收**读同一份实现**——
各自抄一遍，其中一处就会悄悄不再归一（原型正是这样：`--recheck` 把定稿原样搬过去，
于是候选归一了、而真正拼进 `book.txt` 的那一份没有，**报告全绿而交付的书里版式是坏的**）。

⚠⚠ **成稿的装载器住在这里，而不是在读它的那两层**：`stage5/<mode>/chNN.json` 是
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
from typing import Any, Callable

from contracts.invariants import BOOK_BAND, DEPTH_MODES, DRAFT_BAND
from contracts.schema.stage5 import Chapter, DraftConsumed, JudgedDraft
from novel.core.bands import bands
from novel.core.naming import KINSHIP
from novel.core.normalize import (
    dominant_quote_style, normalize_paragraphs, normalize_punctuation, normalize_quotes,
    quote_pair,
)
from novel.core.text import norm, quote_count, window_overlap
from novel.store import ArtifactStore


# ─────────────────────────────────────────────────────────────────────────────
# 一、成稿的装载 —— 走 `read_judged`，绝不走会记 `inputs` 的那条
# ─────────────────────────────────────────────────────────────────────────────

def draft_projection(data: object) -> object:
    """本层（以及下游两层）**真正消费**的成稿投影 —— 喂给 `store.read_judged(project=)`。

    来源元数据、`warns`、预算这些字段变了**不要求任何人重做** ⇒ 只投影章序与正文。

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


def load_drafts(store: ArtifactStore) -> tuple[dict[int, list[dict[str, Any]]], list[str],
                                               dict[str, JudgedDraft]]:
    """全部成稿 → `({output_ordinal: [rec]}, invalids, judged)`。**唯一落点。**

    ⚠⚠ **一律走 `store.read_judged`，绝不走 `store.read`**：`inputs` 的键只取两级，
    `stage5/deep/ch01.json` 会塌成 `deep/ch01.json` ⇒ 反查拼出来的路径根本不存在
    ⇒ 那条边被**静默跳过、永远显示「对得上」**。**一个假的匹配比没有记录更坏。**
    （B1 期已由 `store.read` 的 `PathRefused` 从结构上拦住这条路，这里别绕开它。）

    ⚠ `.INVALID.json` **排除，但要报计数**：「排除了 3 份废稿」与「一份废稿都没有」
    必须分得开。

    ⚠ 同章双份（deep + shallow 都写过）**这里不判死**：两份都留着，由判据
    `draft_duplicated` 去说。**产地的多义性不该在装载器里被一个隐式的选择抹掉。**

    ⚠ 枚举臂时**只走 `DEPTH_MODES`**，于是 `_` 开头的子目录天然不在里面
    ——盘上真有一个人手工放进去的 `stage5/_rounds/`，而界面那一侧曾把它当成第三条臂。
    """
    by_ord: dict[int, list[dict[str, Any]]] = {}
    judged: dict[str, JudgedDraft] = {}
    invalids: list[str] = []
    for mode in DEPTH_MODES:
        directory_rel = artifact_rel('stage5.chapter', mode=mode, nn=1).rsplit('/', 1)[0]
        pattern = os.path.join(store.path(directory_rel), 'ch*.json')
        for p in sorted(glob.glob(pattern)):
            base = os.path.basename(p)
            rel = f'{directory_rel}/{base}'
            if base.endswith('.INVALID.json'):
                invalids.append(rel)
                continue
            chapter, fp, _cfp = store.read_judged(
                Chapter, rel, project=draft_projection, require_current=True)
            rec = chapter.model_dump(by_alias=True)
            rec['text'] = chapter.text  # deterministic in-memory projection, never a disk authority
            rec['_relpath'], rec['_mode'] = rel, mode
            by_ord.setdefault(chapter.output_ordinal, []).append(rec)
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
    base = ((measurable or {}).get('body') or {})
    if not base.get('dialogue_ratio_applicable'):
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

def chapter_source(entry: dict[str, Any], source_text: dict[int, str]) -> str:
    """本章对应的源章原文（按 `from_source_ordinals` 拼）。

    ⚠ **校验器读原文的唯一用途**：算重合度与源章引语数。**它永不进 prompt**——
    闭卷臂看不到原文这件事是由 `prompts.build_material` 保证的，不是由这里。
    """
    return ''.join(source_text.get(s, '') for s in (entry.get('from_source_ordinals') or []))


def mandated_verbatim(entry: dict[str, Any]) -> str:
    """本章**被契约要求逐字复现**的全部句子拼起来——重合度要扣掉的那部分。"""
    return ''.join(v for be in (entry.get('segments') or [])
                   for v in (be.get('verbatim') or []))


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
    budget = int(entry['budget_chars'])
    if budget <= 0:
        raise ValueError('budget_chars 必须是正整数')
    lo, hi = DRAFT_BAND
    return round(budget * lo), round(budget * hi)


def book_range(source_total: int) -> tuple[int, int]:
    """全书篇幅带。⚠ 与逐章**同一个带**（`BOOK_BAND == DRAFT_BAND`），不是两个数。"""
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
    style: dict[str, Any]
    source_text: dict[int, str]
    #: stage2 的**全量** secrets（含一次都没下发过的）。⚠ **收窄下发面是修复，收窄检测器是削弱**：
    #: 从各章 `facts.secrets` 取并集会让一条全书从未下发的 secret 的泄漏检查**永远不执行**，
    #: 而判据照常输出一个「没报违规」。
    secrets: list[dict[str, Any]]
    _cache: dict[str, Any] = field(default_factory=dict)

    @property
    def chapters(self) -> dict[int, dict[str, Any]]:
        """`{output_ordinal: 章条目}`。"""
        if 'chapters' not in self._cache:
            self._cache['chapters'] = {int(c['output_ordinal']): c
                                       for c in (self.ctx.get('chapters') or [])}
        return self._cache['chapters']

    @property
    def characters(self) -> dict[str, dict[str, Any]]:
        """书级人物表。⚠ **下发给写手的是章级那一份**（`entry['characters']`）——
        书级这一份只给确定性代码当名表用。"""
        return (self.ctx.get('world_facts') or {}).get('characters') or {}

    @property
    def measurable(self) -> dict[str, Any]:
        return (self.style.get('measurable') or {}).get('body') or {}

    @property
    def band_of(self) -> dict[str, str]:
        """这本书自己的形态档位（章篇幅 / 句子形态 / 段落形态 …）。

        ⚠ **prompt 里一律给档位词不给数值**——写手要的是「极短段/一句一段」这种可执行的
        形态；给原始数字既没用又会诱导它去凑数。
        ⚠ **不许在 prompt 里硬编码任何一本书的特征**：同样是网文，本语料里章长中位数
        从 425 到 1348 不等，写死「短章体」会把长章书直接压垮。
        """
        if 'bands' not in self._cache:
            from contracts.schema.stage3 import Measurable
            meas = self.style.get('measurable')
            self._cache['bands'] = bands(Measurable.model_validate(meas)) if meas else {}
        return self._cache['bands']

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
                self.style.get('measurable'), ''.join(self.source_text.values()))
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
