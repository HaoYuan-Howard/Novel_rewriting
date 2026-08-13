#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 47 条机械判据的实现。**行在 `contracts/judges/stage5.py`，这里只负责执行。**

【本层与前四层最大的形状差异：机械表由写作闸门与全书验收共用】
落盘闸门（写作相，逐章）与全书验收相**调同一个 `chapter_findings`**。
原型此前把书级那 8 条住在 `n7_review.RULES6` 里、靠一个 `_merged_rules()` 合起来，
而共享层得反向 lazy-import 才拿得到——**一张表拆成两半的全部成本都在那个环上**。

⚠⚠ **判据吃 `dict` 不吃模型。** 这不是随意：一旦判据跑在已经过了 Pydantic 的对象上，
「必填齐全」这类判据在**结构上构造不出反例**——它必然全绿，于是又变成一盏保证会亮的绿灯。
机械层 7 条 HARD 的反例样本（`tests/test_stage5.py`）正是靠这一条才写得出来。

⚠ **结论一律走 `contracts.schema.report.finding()`，不许手写 dict**：
`sev` / `item` / `owner` / `actionable` 全从判据表取 ⇒ 一条 finding 不可能与表说的不一致。
"""
from __future__ import annotations

import collections
import math
import re
from typing import Any, Iterable

from contracts.invariants import DEEP_OVERLAP_MAX, HINGE_GROUPS, SPEECH_TAG_MAX
from contracts.rules import REGISTRY
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.core.bands import PARA_BANDS, SENT_BANDS, band
from novel.core.facts import NUM_UNIT, num_unit_keys, num_values, secret_keywords
from novel.core.measure import measure
from novel.core.naming import invented_names
from novel.core.spans import lcs
from novel.core.style import (
    DASH_CORPUS_P95, SHORT_PARA_MAX, SHORT_RUN_CORPUS_P95, SHORT_RUN_MIN,
    dash_per_1k, short_run_per_1k,
)
from novel.core.text import TERMINAL_PUNCT, min_hamming, norm, norm_index, quote_count
from novel.core.text import split_by_speech_tag
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage5 import measure as M
from novel.stages.stage5 import prompts

# ─────────────────────────────────────────────────────────────────────────────
# 零、几张只有本层读的表
# ─────────────────────────────────────────────────────────────────────────────

#: 叙述外壳：章节标题 / 编号 / 任务术语。**`meta_leak` 的词表**（`wordlist=True`）。
#:
#: ⚠ **它刻意不进 `contracts/invariants.py`**：那张表收的是「**同时**进 prompt 与判据」的
#: 枚举，而这一份**只有判据一个消费方**。放进去就是给它造第二个家。
#: ⚠⚠ 而两条臂的 system 里那句「不出现任何任务术语（节拍、展开、目标之类的词）」
#: **是手写的一个子集**，不是从这张表插值来的——那是 LESSONS 第 9 条的形状，
#: 但 prompt 那两段要与原型**逐字节相同**，改它是一次 prompt 变更。
#: ⇒ 登记在这里：**下发面说了三个词，判据查的是这一整张表**，两侧不等价是已知的。
META_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'^\s*第[一二三四五六七八九十百零〇\d]+章'), '章节标题'),
    (re.compile(r'^\s*#{1,6}\s'), 'Markdown 小标题'),
    (re.compile(r'本章(?:目标|字数|共|要写|完)'), '任务术语'),
    (re.compile(r'(?:节拍|拍数|beat|Beat)\s*[0-9①-⑥]'), '任务术语'),
    (re.compile(r'【?(?:约\s*\d+\s*字|目标字数|篇幅方式|展开程度)】?'), '任务术语'),
    (re.compile(r'(?:以上|以下)?(?:是|为)本章(?:的)?(?:正文|改写|重构)'), '叙述外壳'),
    (re.compile(r'^\s*(?:正文|全文|改写稿|重构稿)\s*[:：]'), '叙述外壳'),
)

#: 恒等基线在这几条上**不适用**——不是判据坏，是它量的东西本来就与源文无关：
#: `length_*` 比的是「源章字数 vs 计划预算」，而计划预算本来就要偏离源章；
#: `overlap_report` 拿源文与自己比恒等于 100%。
#: 在源文上命中说明的是**计划**，不是判据。
IDENTITY_NA = frozenset({'length_over', 'length_under', 'length_report', 'overlap_report',
                         'deep_overlap_high'})

#: 这几条判的是「成稿有没有守住**从源文切出来的**材料」。源文当然守得住 ⇒
#: 恒等基线上命中 **0 是应该的，非 0 是上游 bug**（锚点切出来的东西回不到原文里去），
#: **不是「判据无判别力」**。分开报，别混进「不可反哺」那一桶。
IDENTITY_UPSTREAM = frozenset({'verbatim_missing', 'verbatim_typo', 'keep_value_missing',
                               'must_appear_missing'})


# ─────────────────────────────────────────────────────────────────────────────
# 一、保真回写 —— 该守的守住没、该编的越界没
# ─────────────────────────────────────────────────────────────────────────────

def fidelity_findings(body: str, entry: dict[str, Any], book: M.Book, *,
                      mode: str, given: str) -> list[Finding]:
    """四组保真材料 + 剧透 + 发明越界。**闭卷写手看不到原文、无法自证，所以只能在代码侧验。**

    `given` = 这一臂**实际看得见**的全部文字（下发面；开卷再加源章原文）。
    ⚠⚠ **参照集必须跟着「这一臂的写手看得见什么」走**：它一度只有下发面，于是
    **开卷臂 4/4 全是假阳**——那几个「五次/千倍/百倍」全都原样在源章原文里，
    而开卷写手手里就有那份原文，**照抄一个数字不是发明，是保真**。

    ⚠ `evidence` 是**给验收相用的机器可比凭据**，不是给人看的摘要：它必须是能在这一章
    成稿或下发材料里**定位回去**的原串，而不是 `detail` 里那个截断到 14/18 字的展示版。
    反哺 prompt 与「两轮之间这条消没消」都按它对齐。
    """
    fs: list[Finding] = []
    beats = entry.get('beats') or []

    # ── ① 【原样写入】必须逐字出现（四组材料里唯一要求逐字的一组）───────────────
    #
    # ⚠ **按原文段落边界拆开逐段查**：原文是「每句一段」排版，一条材料常常跨了 `\n\n`
    #   ——那意味着它本来就是**两句**。`norm()` 会把换行也剥掉，若整条当成一个连续串来要求，
    #   就凭空造出了「这两句之间不许有别的字」这个**原文里并不存在**的约束。
    #   实测：写手把两句都逐字写了，只在中间插了一句「他放下茶盏，声音发紧。」
    #   ——那是 `INVENT_ALLOW` 里明确**鼓励**的动作分解，判它失败是判错了。
    nb, nmap = norm_index(body)
    miss_v: list[str] = []
    typo_v: list[str] = []
    split_v: list[tuple[str, str]] = []
    for be in beats:
        for t in (be.get('verbatim') or []):
            for seg in re.split(r'\n+', t):
                k = norm(seg)
                if not k or k in nb:
                    continue
                # 【被提示语切开算满足】`「…，」他说，「…」` 是中文小说的**标准写法**，
                # 不是改写。放行，但**单独留痕**——「一个字都没动」与「切成两截后逐字出现」
                # 不是同一件事，不能都走 `[OK]`。
                if (gap := split_by_speech_tag(k, body, nb, nmap)) is not None:
                    split_v.append((seg, gap))
                    continue
                # 【一字之差按错别字放行】原文里本来就有错字（实测「这**为**公子伤口溃烂…」），
                # 写手写成了「这**位**公子…」——那是**纠正**，不是改写。
                # 要求它复现原文的错别字是荒谬的，而只差一个字的改写实际上不存在。
                (typo_v if min_hamming(k, nb) <= 1 else miss_v).append(seg)
    if split_v:
        fs.append(finding(
            'verbatim_split',
            detail=f'{len(split_v)} 句【原样写入】被提示语切成两截后逐字出现'
                   f'（本体裁的标准写法，已放行）：'
                   f'{[(t[:14], g[:8]) for t, g in split_v[:3]]}',
            evidence=[t for t, _g in split_v]))
    if miss_v:
        fs.append(finding(
            'verbatim_missing',
            detail=f'{len(miss_v)} 句【原样写入】的台词没出现在成稿里'
                   f'（这组要求逐字，改一个字就废）：{[t[:18] for t in miss_v[:4]]}',
            evidence=miss_v))
    if typo_v:
        fs.append(finding(
            'verbatim_typo',
            detail=f'{len(typo_v)} 句【原样写入】与成稿差一个字'
                   f'（多半是写手纠正了原文的错别字，已放行）：{[t[:18] for t in typo_v[:3]]}',
            evidence=typo_v))

    # ── ② 【数值不得改动】只要求**值**保住、措辞可动 ────────────────────────────
    #
    # ⚠⚠ **比值不比字面**：下发面「30岁」、成稿「三十岁」＝满足。原型只搜阿拉伯字面，
    #   某章因此被**整章拦下**而值一个都没变——而写手做的正是契约让它做的事。
    #   解析不出值的罕见形态**退回字面比对**：不许因为解析器不认就默认放行。
    body_vals = num_values(body)
    miss_n: list[tuple[str, list[str]]] = []
    miss_n_full: list[str] = []
    for be in beats:
        for t in (be.get('keep_value') or []):
            nums = [m.group() for m in NUM_UNIT.finditer(t or '')]
            vals = num_values(t)
            ok = bool(vals & body_vals) if vals else any(n in body for n in nums)
            if nums and not ok:
                miss_n.append((t[:18], nums[:3]))
                miss_n_full.append(t)
    if miss_n:
        fs.append(finding(
            'keep_value_missing',
            detail=f'{len(miss_n)} 处【数值不得改动】的数字在成稿里找不到：{miss_n[:4]}',
            # 凭据存**整条原始材料**，不存截断到 18 字的展示版：反哺时写手要看见完整那句，
            # 才知道那个数字长在哪儿。
            evidence=miss_n_full))

    # ── ③ 【必须在场并起作用，怎么描写随你】⇒ **不能按整串比对**（那是 verbatim 的查法）──
    #
    # 实测吃过一次：材料「羽林卫特制的箭矢带有逆刺」，成稿写「羽林卫的箭，箭簇带逆刺」，
    # 保住了在场与作用（后文「倒刺便在伤口里绞动」），却被整串匹配判成缺失
    # ——**判据与它自己声明的维度不一致**。改看最长公共子串：换说法能过，整个没写才报。
    miss_o: list[str] = []
    miss_o_full: list[str] = []
    for be in beats:
        for t in (be.get('must_appear') or []):
            k = norm(t)
            if k and lcs(k, nb)[1] < max(2, min(3, len(k))):
                miss_o.append(t[:14])
                miss_o_full.append(t)
    if miss_o:
        fs.append(finding(
            'must_appear_missing',
            detail=f'{len(miss_o)} 个【必须在场】的物件在成稿里找不到任何痕迹：{miss_o[:4]}',
            evidence=miss_o_full))
    # ⚠ `must_hold` **故意不查文面**——它要的是命题成立，逐字出现反而是硬塞背景交代。
    #   它由书级那条 `must_hold_unver` 显式说「没查」。

    # ── ④ 本章不该点破的秘密 ────────────────────────────────────────────────
    hid = entry.get('must_not_reveal') or []
    if hid:
        table = {s['secret_id']: s for s in book.secrets}
        leak: list[tuple[str, list[str]]] = []
        unknown: list[str] = []
        leak_spans: list[str] = []
        for sid in hid:
            s = table.get(sid)
            if s is None:
                # 【不静默】`must_not_reveal` 点了一条 secret，而全书 secrets 里没有它——
                # 那不是「查过、没泄漏」，是**根本没得查**。
                unknown.append(sid)
                continue
            # ⚠ **两侧不同口径，而这是原型的现状**：`secret_keywords` 把 fact 里的
            #   中文数字**剥掉**（「一直」→「直」），而成稿这一侧没剥 ⇒ 凡 fact 里带
            #   数字词的片段**永远匹配不上**。它是「19/21 条 secret 连揭示章都命中不了」
            #   这个实测数字的成因之一。**本轮不改**：改它会动全部三本的基线，
            #   而手上没有 ground truth 判断新口径是更准还是更吵（判据 18：调参先量方差）。
            #   ⇒ 登记在这里，连同那条盲区计数一起（`review._outcomes` 打在同一行上）。
            kws = secret_keywords(s.get('fact'))
            hit = [k[:14] for k in kws if norm(k) and norm(k) in nb]
            if hit:
                leak.append((sid, hit[:2]))
                # ⚠⚠ **凭据必须是成稿自己的字节，不是 secret 的字节**：命中的 `k` 来自
                #   `secret.fact`，把它当凭据发回给写手就是**把秘密告诉它**。
                #   回映成成稿里的那一段之后，反哺时引用的是写手自己写过的话。
                for k in kws:
                    j = nb.find(norm(k)) if norm(k) else -1
                    if j >= 0 and j < len(nmap):
                        end = nmap[min(j + len(norm(k)), len(nmap)) - 1] + 1
                        leak_spans.append(body[nmap[j]:end])
        if leak:
            fs.append(finding('secret_leak',
                              detail=f'成稿疑似提前点破了本章不该揭示的秘密：{leak[:4]}',
                              evidence=leak_spans or [sid for sid, _h in leak]))
        if unknown:
            fs.append(finding(
                'secret_unknown',
                detail=f'{len(unknown)} 条 must_not_reveal 在 stage2 的 secrets 里找不到'
                       f'（{unknown[:4]}）——这几条的泄漏检查**没有跑**，不是跑了没问题。'
                       f'多半是 writer_context 与盘上 stage2 不同步，看产物来源行',
                evidence=unknown))

    # ── ⑤ 发明越界的两个探针 ────────────────────────────────────────────────
    #
    # ⚠ **另五项（新事件/新地点/新道具/新因果/新时间跳跃）一个探针都没有**（`R-7.5b` → `B-01`）。
    #   别把这里的 0 条读成「那七条都守住了」。
    keys_body = num_unit_keys(body)
    keys_given = num_unit_keys(given)
    inv = sorted({disp for k, disp in keys_body.items() if k not in keys_given})
    if inv:
        seen = '源章原文与下发面' if mode == 'shallow' else '下发面'
        why = ('开卷写手手里有源章原文，两处都没有的数字就只能是编的' if mode == 'shallow'
               else '闭卷写手写出的事实性数字若不在任何给定材料里，就只能是编的')
        fs.append(finding(
            'invented_number',
            detail=f'⚠ 成稿出现{seen}里都没有的【数字+单位】{inv[:8]}——{why}，逐个核对',
            evidence=inv))
    nm = invented_names(body, given, book.name_stems)
    if nm:
        # 【不过滤「X家Y」】实测 6 条候选里 4 条是这个形状（「苏家姑娘」「赵家小姐」），
        # 都在指已有角色；但把带「家」的整类滤掉，会连「苏家二小姐」这种真发明一起滤掉。
        # **宁可让人多看两眼，不要为了好看的数字砍掉判别力。**
        fs.append(finding(
            'invented_name',
            detail=f'⚠ 成稿出现下发面里没有的【人物称谓】{nm[:8]}——逐个核对指的是不是已有角色。'
                   f'「X家姑娘/X家小姐」这类多半是自然构词（在指已有的人）；'
                   f'真要警惕的是**多出一个人**（如「苏家二小姐」）',
            evidence=nm))
    return fs


# ─────────────────────────────────────────────────────────────────────────────
# 二、声音守恒的六条漂移 + 对白
# ─────────────────────────────────────────────────────────────────────────────

def _dialogue_drift(body: str, book: M.Book) -> list[Finding]:
    """对白维度的声音守恒。**这是画像里最硬的形态特征，比句长段长更刺眼。**"""
    base = book.measurable
    got = measure(body)
    if not got:
        return []
    if not base.get('dialogue_ratio_applicable'):
        # ⚠ **不作为破形**：实测两本书的引号是**入库前被人工删掉的**，原作本有「」。
        #   所以「无引号」是**语料缺陷不是作者选择**，不能拿它去约束写作
        #   ——写手加引号反而更接近原作。措辞是提示不是违规，`owner=corpus`。
        return ([finding('quotes_in_unquoted_corpus',
                         detail=f'注意：本书语料无引号（可能是入库时被删，非作者选择），'
                                f'成稿用了 {list(got.quote_styles)}'
                                f'——若原作确有引号则属正常，不必按语料回改',
                         evidence=list(got.quote_styles))]
                if got.quote_styles else [])
    if not got.dialogue_ratio_applicable:
        return [finding('dialogue_broken',
                        detail='对白破形：原书是引号对白体，成稿一句引号对白都没有',
                        evidence=['0'])]
    b, g = base.get('dialogue_ratio'), got.dialogue_ratio
    if b and (g < b * 0.7 or g > b * 1.4):
        return [finding('dialogue_ratio_drift',
                        detail=f'对白比偏离：原书 {b:.3f} → 成稿 {g:.3f}（{g / b:.2f}×）',
                        evidence=[f'{b:.3f}->{g:.3f}'])]
    return []


def _band_drift(body: str, book: M.Book) -> list[Finding]:
    """成稿的句/段形态是否还落在这本书的档位上（写手有没有把「一句一段」写成大段）。

    ⚠ **走 `core.bands` 的同一张档位表**，保证两边同一把尺子。
    ⚠ 凭据带上是**哪一维**：同一条 rule 的两个实例必须分得开，否则
    「句子形态修好了、段落形态还没」在收敛差集里看起来是同一条。
    """
    got = measure(body)
    if not got:
        return []
    base = book.band_of
    mine = {'句子形态': band(got.avg_sentence_chars, SENT_BANDS),
            '段落形态': band(got.avg_paragraph_chars, PARA_BANDS)}
    return [finding('band_drift',
                    detail=f'形态漂移 {k}：底座「{base[k]}」→ 成稿「{mine[k]}」',
                    evidence=[k, base[k], mine[k]])
            for k in ('句子形态', '段落形态') if base.get(k) and base[k] != mine[k]]


def _para_len_drift(body: str, book: M.Book) -> list[Finding]:
    """段均字数**双向**冲。

    ⚠ 它补的是 `band_drift` 档位太粗那一块：实测某本 19.6 与 11.5 同落「极短段」档、穿档而过。
    """
    base = book.measurable.get('avg_paragraph_chars')
    got = measure(body)
    if not base or not got:
        return []
    r = got.avg_paragraph_chars / base
    if r < 0.6 or r > 1.6:
        which = '电报体（段被剁得太碎）' if r < 0.6 else '段被并得太长'
        return [finding('para_len_drift',
                        detail=f'段长漂移：本书均段 {base} 字 → 本章 '
                               f'{got.avg_paragraph_chars} 字（{r:.2f}×），{which}',
                        evidence=[f'{base}->{got.avg_paragraph_chars}'])]
    return []


_INLINE_CUE = re.compile(r'[：:]\s*[“「『"]')


def _layout_drift(body: str, book: M.Book) -> list[Finding]:
    """提示语排版惯例（冒号收尾 + 换行 + 引语独段）。

    书级基线 ≥0.8 才启用（**不下发无据的指令**）；章级邻接 ≥3 才判（分母太小不判）。
    ⚠ **永不升 HARD**——它是形状偏好不是内容保真。
    """
    paras = [ln.strip() for ln in (body or '').split('\n') if ln.strip()]
    inline = [p for p in paras if _INLINE_CUE.search(p)]
    if len(inline) < 3:
        return []
    cue = book.cue_rate
    if cue is None or cue < 0.8:
        return []
    return [finding('layout_drift',
                    detail=f'对白排版漂移：本书提示语以冒号收尾后 {cue:.0%} 换行独段，'
                           f'本章有 {len(inline)} 处把提示语和引语挤在同一段'
                           f'（同行冒号/句中插入）——这不是这本书的排法',
                    evidence=inline[:6])]


def _density_drift(body: str, book: M.Book) -> list[Finding]:
    """明喻密度 +「心里」密度，各按 `max(2×base, base+1.0)/千字` 判。

    ⚠⚠ 那个 `+1.0` **没有语料级来源**（对照：`dash_drift` 的 `DASH_CORPUS_P95` 由
    `tools/` 的标定脚本从 156 本语料算出、可 0 成本重跑）⇒ **调它之前先按同样办法重标**。
    """
    out: list[Finding] = []
    for rule, key, fn, why in (
            ('simile_drift', 'simile_per_1k', M.simile_per_1k,
             '这本书靠白描不靠比喻，你把它写成了另一本'),
            ('xinli_drift', 'xinli_per_1k', M.xinli_per_1k,
             '这本书表演情绪、不解释情绪')):
        base = book.baseline(key, fn)
        if base is None:
            continue
        got = fn(body)
        if got > max(2 * base, base + 1.0):
            label = '明喻' if rule == 'simile_drift' else '「心里」'
            out.append(finding(rule,
                               detail=f'{label}密度漂移：本书 {base:.2f}/千字 → '
                                      f'本章 {got:.2f}/千字（{why}）',
                               evidence=[f'{base:.2f}->{got:.2f}']))
    return out


_DASH_EG = re.compile(r'[^\n。！？]{0,10}—{2,}[^\n。！？]{0,10}')


def _dash(body: str, entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """破折号：**度量无条件出**，告警按「本书基线 + 体裁上界」双线判。

    ⚠ **为什么是 `max(2×base, base + 语料 p95)`**：两条线各挡一种情况——本书本来就爱用
    破折号的（实测某本源文 3.02/千字，语料前 5），乘法线给它应有的余量；本书基线接近 0 的
    （实测三本源文全书几乎不用），乘法线会退化成「>0 就报」，这时加法线兜底。

    ⚠⚠ **基线取书级而不是同章源章**：同章配对会让**恒等基线退化成恒真**——
    源章 vs 源章恒等于 1.00、永不触发，于是它在 `baselines_identity` 里恒为 0/N，
    被读成「判据干净」，而那是唯一能逐书判定判别力的机制。
    """
    base = book.baseline('dash_per_1k', dash_per_1k)
    if base is None:
        return []
    got = dash_per_1k(body)
    n = len(re.findall(r'—{2,}', body or ''))
    src = M.chapter_source(entry, book.source_text)
    vs_src = f'｜源章 {dash_per_1k(src):.2f}' if src else ''
    out = [finding('dash_report',
                   detail=f'破折号密度：本章 {n} 处 = {got:.2f}/千字{vs_src}｜本书 {base:.2f}'
                          f'｜语料 p95 {DASH_CORPUS_P95:.2f}',
                   evidence=[f'{base:.2f}->{got:.2f}'])]
    if got > max(2 * base, base + DASH_CORPUS_P95):
        eg = [m.group() for m in _DASH_EG.finditer(body or '')][:3]
        out.append(finding(
            'dash_drift',
            detail=f'破折号密度漂移：本书 {base:.2f}/千字 → 本章 {got:.2f}/千字'
                   f'（{n} 处；语料 p95 {DASH_CORPUS_P95:.2f}）'
                   + (f'　例：{" ｜ ".join(eg)}' if eg else ''),
            evidence=[f'{base:.2f}->{got:.2f}']))
    return out


def _short_run(body: str, entry: dict[str, Any], book: M.Book) -> list[Finding]:
    """连续短段成串 —— **只量不判**。

    实测本书内信号很强（成稿中位 5.51/千字 vs 同章源文 2.51，2.2×），而且与
    `para_len_drift` 几乎不重叠。但语料分布说它**落在体裁常态内**（p95 7.68、max 10.83）
    ⇒「这本书不这么排」算不算缺陷是审美裁决，不是结构不变量。
    **别因为它的数字好看就顺手加阈值。**
    """
    base = book.baseline('short_run_per_1k', short_run_per_1k)
    if base is None:
        return []
    src = M.chapter_source(entry, book.source_text)
    vs_src = f'｜源章 {short_run_per_1k(src):.2f}' if src else ''
    return [finding('short_run_report',
                    detail=f'连续短段成串：本章 {short_run_per_1k(body):.2f}/千字{vs_src}'
                           f'｜本书 {base:.2f}｜语料 p95 {SHORT_RUN_CORPUS_P95:.2f}'
                           f'（口径：≤{SHORT_PARA_MAX} 字的段连续 ≥{SHORT_RUN_MIN} 段算一串）',
                    evidence=[f'{base:.2f}->{short_run_per_1k(body):.2f}'])]


def _plan_keeps_content(entry: dict[str, Any]) -> bool:
    """这一章的计划有没有**净压缩**（预算基本等于源字数）。

    ⚠ **为什么不读 `op`**：它曾经就是 `entry['op'] == 'keep'`，而 `op` 已锁定为 `keep`
    ⇒ 那个合取项**恒真**，靠它收窄的窄 HARD 门会当场变成宽门，所有开卷章都进硬拦
    ——闸门不是失效，是**扩张到了它没标定过的范围**。
    改读 `budget_chars/source_chars` 之后判的是同一件事而且更准：`op` 只是个标签，
    这两个数才是计划实际给出的量。
    ⚠ 源字数缺失 ⇒ `False`（判不了就不拦，别拿一把缺刻度的尺子升 HARD）。
    """
    src = entry.get('source_chars') or 0
    return bool(src) and (entry.get('budget_chars') or 0) >= src * 0.90


def _dialogue(body: str, entry: dict[str, Any], book: M.Book, *, mode: str) -> list[Finding]:
    """章级引语数：成稿 M vs 源章 N。**度量无条件出**，告警两级。

    ⚠ **窄 HARD 门的理由**：只在 `shallow + 计划没净压缩 + N≥4` 时拦腰斩（`M < ceil(0.5N)`）
    ——开卷且计划没删内容时**原文就在写手眼前**，腰斩对白没有任何合法理由。
    净压缩的章计划本身已删内容，N 高估了合法目标，强拦只会**逼写手拿对白凑数**
    ——按它修会更差。所以闭卷与净压缩章一律走提示级。

    ⚠⚠ **`dialogue_report` 是无条件的**：原型只有三条**告警**，于是「这本书的对白比是多少」
    只有在漂了的时候才说得出来——而它承载的是本项目**效应量最大**的一条跨书结论
    （闭卷逐章中位 0.80 / 开卷 1.00，6 本书方向一致）。
    """
    n = M.src_quote_count(entry, book.source_text)
    m = quote_count(body)
    out: list[Finding] = [finding(
        'dialogue_report',
        detail=f'直接引语：成稿 {m} 句'
               + (f' ｜ 源章 {n} 句 = {m / n:.2f}×' if n else ' ｜ 源章（取不到）'),
        evidence=[str(m), str(n if n is not None else '')])]
    if n is None:
        return out
    if mode == 'shallow' and _plan_keeps_content(entry) and n >= 4 and m < math.ceil(n / 2):
        out.append(finding(
            'dialogue_halved',
            detail=f'直接引语腰斩：源章 {n} 句 → 成稿 {m} 句（开卷、且计划没有净压缩'
                   f'（预算 {entry.get("budget_chars")} / 源 {entry.get("source_chars")} 字），'
                   f'没有任何合法理由砍对白——台词是承重内容不是可压缩的填充）',
            evidence=[f'{n}->{m}']))
    elif n >= 4 and m < n * 0.7:
        out.append(finding(
            'dialogue_fewer',
            detail=f'直接引语明显少于源章：{n} 句 → {m} 句（{m / n:.2f}×）'
                   f'——查是否把台词压成了转述',
            evidence=[f'{n}->{m}']))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 三、⭐ 逐章判定 —— **落盘闸门与全书验收相调的就是它**
# ─────────────────────────────────────────────────────────────────────────────

def chapter_findings(text: str, entry: dict[str, Any], book: M.Book, *,
                     mode: str, given: str = '',
                     quote_problems: Iterable[str] = ()) -> list[Finding]:
    """一章成稿的全部确定性判定 → `[Finding]`。**两相共用同一个函数。**

    `given` 缺省为空串时会按**最严**的口径判发明越界（不知道是哪一臂就不替它放宽探测器）；
    调用方一律应传 `prompts.build_material(...).given`。

    ⚠ **追加顺序即渲染顺序**：`warns` 是这份列表的字符串投影并原样存进 `chNN.json`，
    顺序一变，验收相逐轮做差集时会把整批警告读成「全换了」。
    """
    fs: list[Finding] = []
    body = (text or '').strip()

    # 【早退是有意的】后面每一条判据都建立在「这是一篇文章」之上，对 12 个字跑它们只会
    # 产出一堆没有意义的数。但验收相要知道这不是「其余都干净」——那条区分由这条 HARD 自己承担。
    if len(body) < 50:
        return [finding('draft_too_short',
                        detail=f'成稿过短或为空（{len(body)} 字），疑似失败',
                        evidence=[body[:30] or '（空）'])]

    if body[-1] not in TERMINAL_PUNCT:
        fs.append(finding('draft_truncated',
                          detail=f'结尾无终止标点（疑似截断）：…{body[-12:]!r}',
                          evidence=[body[-24:]]))
    for pat, label in META_PATTERNS:
        if (m := pat.search(body)) is not None:
            fs.append(finding('meta_leak',
                              detail=f'正文出现{label}「{m.group().strip()}」'
                                     f'——叙述外壳不该进正文',
                              evidence=[m.group().strip()]))

    names = book.names
    if names and not any(n in body for n in names):
        fs.append(finding('names_absent',
                          detail=f'本章相关人物名一个都没出现（是否被改名/换称呼）：'
                                 f'{sorted(names)[:6]}',
                          evidence=sorted(names)))

    # ── 篇幅：度量无条件出，两条告警按 `DRAFT_BAND` 判 ──────────────────────
    src_chars = entry.get('source_chars') or 0
    band_range = M.target_range(entry)
    bud = entry.get('budget_chars')
    flag = ''
    if band_range and not (band_range[0] <= len(body) <= band_range[1]):
        flag = f'  ← 超出目标 {band_range[0]}~{band_range[1]}'
    if src_chars:
        # 【两个比值都给，但主角是 vs 预算那个】写手对齐的是 `budget_chars`（逐拍下发），
        # 只报 vs 原章会让人拿一个**派生量**去判达标——上一轮的外部审阅就是这么读岔的。
        vs_bud = f' / 预算 {bud} 字 = {len(body) / bud:.2f}×' if bud else ''
        fs.append(finding('length_report',
                          detail=f'篇幅：{len(body)} 字{vs_bud} ｜ '
                                 f'原章 {src_chars} 字 = {len(body) / src_chars:.2f}×{flag}',
                          evidence=[str(len(body)), str(bud), str(src_chars)]))
    if band_range:
        lo, hi = band_range
        if len(body) > hi:
            fs.append(finding(
                'length_over',
                detail=f'篇幅超上限：{len(body)} 字 > {hi} 字（目标 {lo}~{hi}，'
                       f'{len(body) / hi:.2f}× 上限）——收缩已跑过并未能压下来，**照样落盘**，'
                       f'由全书验收相在成品上判（由阻断降为提示）',
                evidence=[f'{len(body)}>{hi}']))
        elif len(body) < lo:
            fs.append(finding(
                'length_under',
                detail=f'⚠ 明显低于目标下限（{len(body)} < {lo}）——查是否丢了事件；'
                       f'若本章走过收缩，还要查结尾立意是否被换掉',
                evidence=[f'{len(body)}<{lo}']))

    # ── 重合度：度量无条件出；**闭卷才有上界** ──────────────────────────────
    ov = M.overlap_ratio(body, entry, book.source_text)
    if ov is not None:
        fs.append(finding('overlap_report',
                          detail=f'与源章原文重合度（20字窗口，已扣除逐字要求）：{ov:.1%}',
                          evidence=[f'{ov}']))
        if mode == 'deep' and ov > DEEP_OVERLAP_MAX:
            # ⚠⚠ **它守的是【臂的身份】不是文风**：越线说明「章的分工写反 /
            #   `with_source_text` 被翻转 / 开卷稿落进 deep 目录」，不是「写得像原文」。
            fs.append(finding(
                'deep_overlap_high',
                detail=f'闭卷臂与源章重合 {ov:.1%} > {DEEP_OVERLAP_MAX:.0%}'
                       f'——闭卷写手看不到原文，这个数说明**这一章多半不是闭卷写的**'
                       f'（分工写反 / 开卷稿落进了 deep/ 目录），不是「写得像原文」',
                evidence=[f'{ov:.4f}']))

    fs += fidelity_findings(body, entry, book, mode=mode, given=given or '')
    fs += _dialogue(body, entry, book, mode=mode)
    fs += (_layout_drift(body, book) + _para_len_drift(body, book)
           + _density_drift(body, book) + _band_drift(body, book) + _dialogue_drift(body, book))
    # ⚠ 新探针一律**追加在末尾**（见 docstring 那条「追加顺序即渲染顺序」）。
    fs += _dash(body, entry, book) + _short_run(body, entry, book)
    if (qp := sorted(set(quote_problems))):
        # ⚠⚠ 它原来是 `mech.quote_problems`，**一个记账块里的字段**——而它根本不是记账，
        #   **是一条结论**：「引号配对异常、未归一、留着交人工」。
        fs.append(finding('quote_unbalanced',
                          detail=f'{len(qp)} 处引号配对异常，归一**整段原样返回**未处理'
                                 f'（引号错乱多半是台词归属错位，不猜）：{qp[:3]}',
                          evidence=qp))
    return fs


def blocking(fs: Iterable[Finding]) -> list[str]:
    """落盘闸门：哪几条是**阻断级**。`sev` 一律现查注册表，不在这里抄第二份。"""
    return [f'[{f.rule}] {f.detail}' for f in fs if REGISTRY.get(f.rule).sev == 'hard']


def warnings_of(fs: Iterable[Finding]) -> list[str]:
    """`chNN.json.warns` —— 提示级判定的**字符串投影**。

    ⚠ **恒非空**：4 条 metric（篇幅 / 重合度 / 破折号 / 连续短段）无条件产出
    ⇒「有 warns」不携带任何信息，只有**读它是哪几条**才有。
    """
    return [f'[{f.rule}] {f.detail}' for f in fs if REGISTRY.get(f.rule).sev != 'hard']


# ─────────────────────────────────────────────────────────────────────────────
# 四、验收相的逐章增量 —— 事件落点 · 材料形态 · 恒等基线
# ─────────────────────────────────────────────────────────────────────────────

def _atoms_of_event(text: str, roster: Iterable[str]) -> set[str]:
    """从一条 `event` 里提取**可确定性回查的原子**：数字+单位、以及章级名册里的专名。

    ⚠ **只取这两类是有理由的，不是偷懒**：`event` 是概括句，除了这两样没有别的东西能在
    成稿里做无歧义匹配——中文没有词边界，拿名词去匹配会得到一堆假阳。
    提不出原子的拍**落 `unver`**，不落「干净」。
    """
    atoms: set[str] = set()
    t = text or ''
    # 与 invented_number 共用值域过滤；否则「一百成功」会在事件落点里
    # 生成一个正文永远不可能合理复现的假原子「一百成」。
    atoms.update(num_unit_keys(t).values())
    for nm in roster:
        if nm and nm in t:
            atoms.add(nm)
    return atoms


def _pronoun_named(entry: dict[str, Any]) -> set[str]:
    """本章里**以代词现身**的角色的 `display_name` —— 这些名字不能当落点原子。

    实测抓到的假阳：`event` 写「孟青禾觉得冤枉」，而本章是**第一人称**、叙述者就是她
    ——成稿里她通篇是「我」，名字 0 次。于是「这一拍没落点」判成立，可那一拍明明落了。
    ⚠ **只剔叙述者，不剔 `focalizer`**：视角人物在第三人称叙述里照样有名字。
    """
    return {n['narrator'] for n in (entry.get('narration') or [])
            if n.get('grammatical_person') == 'first' and n.get('narrator')}


def event_landing(entry: dict[str, Any], body: str, src_text: str
                  ) -> tuple[list, list, list, list]:
    """每一拍的事件在成稿里有没有落点 → `(落了, 没落, 提不出原子, 原子提错)`。

    ⚠ **身份基线是这条判据能不能被解读的前提**：同一套原子先拿**源章原文**跑一遍
    ——源章里都落不下的原子，说明是**抽取坏了不是成稿坏了**。
    没有这个对照，「落点率 70%」这个数没有任何可解读性。
    """
    roster = {c.get('display_name') for c in (entry.get('characters') or {}).values()}
    roster = {n for n in roster if n} - _pronoun_named(entry)
    landed: list = []
    missed: list = []
    noatom: list = []
    ident_miss: list = []
    for be in (entry.get('beats') or []):
        text = ' '.join(be.get('events') or [])
        atoms = _atoms_of_event(text, roster)
        if not atoms:
            noatom.append(text[:24])
            continue
        if src_text and not any(a in src_text for a in atoms):
            # 原文里都找不到 → **原子提错了**，这一拍不参与判定（否则是拿坏尺子量）
            ident_miss.append(text[:24])
            continue
        (landed if any(a in body for a in atoms) else missed).append(
            (text[:36], sorted(atoms)[:4]))
    return landed, missed, noatom, ident_miss


def review_chapter_findings(body: str, entry: dict[str, Any], book: M.Book,
                            ) -> tuple[list[Finding], collections.Counter]:
    """验收相相对落盘闸门的**逐章增量**：事件落点 + 下发材料形态。"""
    fs: list[Finding] = []
    src_text = M.chapter_source(entry, book.source_text)
    landed, missed, noatom, ident = event_landing(entry, body, src_text)
    if missed:
        fs.append(finding('event_landing',
                          detail=f'{len(missed)} 拍的事件在成稿里找不到任何落点'
                                 f'（这些拍提得出可回查的原子，但一个都没出现）：'
                                 f'{[m[0] for m in missed[:3]]}',
                          evidence=[m[0] for m in missed]))
    if noatom or ident:
        fs.append(finding('event_landing_unver',
                          detail=f'{len(noatom)} 拍提不出可回查的原子、{len(ident)} 拍的原子'
                                 f'连源章原文里都找不到（原子提错了，不是成稿的账）'
                                 f'—— 这些拍**没有被检查**',
                          evidence=[str(len(noatom)), str(len(ident))]))
    # 材料坏没坏与成稿无关，但它会**同时**污染判定与反哺，所以在这里一起报出来。
    bad = [t for be in (entry.get('beats') or []) for g in HINGE_GROUPS.values()
           for t in (be.get(g) or []) if prompts.clean_material(t) != (t or '').strip()]
    if bad:
        fs.append(finding(
            'material_malformed',
            detail=f'{len(bad)} 条下发材料两端带着标点/引号残渣（多半是 stage2 的锚点切过界）：'
                   f'{[repr(t) for t in bad[:3]]}——拿它判「在不在」会判出假阳，'
                   f'拿它反哺写手照办不了。发给写手时已清掉残渣，**但判定用的还是原样**：'
                   f'证据留着，等 stage2 修',
            evidence=bad))
    land = collections.Counter({'ok': len(landed), 'missed': len(missed),
                                'noatom': len(noatom), 'ident': len(ident)})
    return fs, land


def identity_rules(entry: dict[str, Any], book: M.Book, *, mode: str) -> set[str]:
    """**恒等基线**：把源章原文当成一份「成稿」，跑同一套判据，返回它触发了哪些 rule。

    这是判断「判据对不对」的决定性测法：**原作要是通不过它自己的判据，那就是判据把合法
    情况排除在外了，不是数据错了**。拿这种判据去反哺，写手会被逼着把书改成另一本。
    """
    src = M.chapter_source(entry, book.source_text)
    if not src:
        return set()
    return {f.rule for f in chapter_findings(src, entry, book, mode=mode, given='')}


# ─────────────────────────────────────────────────────────────────────────────
# 五、书级 —— 单章视角看不到的那几条
# ─────────────────────────────────────────────────────────────────────────────

_CTX_CHARS = 2


def _number_context_key(body: str, atom: str) -> tuple[str, str] | None:
    """`(左邻2字, 单位)` —— 跨章数字矛盾的分组键。

    ⚠ 只按单位分组太粗（「两个」「三个」在不同章里各说各的，完全合法）；
    带上左邻两个字之后分的才是「**同一件事**被写成了两个数」。
    """
    i = body.find(atom)
    if i < 0:
        return None
    return body[max(0, i - _CTX_CHARS):i], atom[-1:]


def book_findings(book: M.Book, *, rows: list[dict[str, Any]], bodies: dict[int, str],
                  inv_numbers: list[tuple[int, str, list[str]]],
                  inv_names: list[tuple[int, list[str]]]) -> list[Finding]:
    """单章视角看不到的那几条。**这是全书验收相相对逐章落盘校验的全部增量。**"""
    fs: list[Finding] = []

    # ── ① 秘密的全书分布 —— **这是剧透判据的基线，不是判定** ──────────────────
    if not book.secrets:
        fs.append(finding(
            'no_secrets_in_book',
            detail='stage2 给这本书抽出的 secrets 是空的 —— 逐章剧透闸门与 '
                   '`must_not_reveal` 在它上面**一次都不会执行**。'
                   '本书的「没有提前泄底」不是一个结论，是没得查',
            evidence=['0']))
    for s in book.secrets:
        kws = [k for k in secret_keywords(s.get('fact')) if norm(k)]
        seen = [o for o, b in sorted(bodies.items())
                if any(norm(k) in norm(b) for k in kws)]
        reveal = sorted({e.get('chapter') for e in (s.get('reader_reveal_events') or [])
                         if e.get('chapter')})
        blind = bool(reveal) and not (set(seen) & set(reveal))
        fs.append(finding(
            'secret_timeline',
            detail=f"{s.get('secret_id')}：关键片段 {len(kws)} 条 · 揭示章 {reveal or '（无）'}"
                   f" · 在成稿里出现于 {seen or '（一章都没有）'}"
                   + ('　← 连揭示章自己都没命中：这条 secret 的字面探针在本书上没有判别力'
                      if blind else ''),
            evidence=[str(s.get('secret_id'))]))

    # ── ② 跨章数字矛盾（自造细节自相矛盾）────────────────────────────────────
    groups: dict[tuple[str, str], set[tuple[int, str]]] = collections.defaultdict(set)
    for o, body, atoms in inv_numbers:
        for a in atoms:
            if (k := _number_context_key(body, a)) is not None:
                groups[k].add((o, a))
    for (ctxs, unit), hits in sorted(groups.items()):
        vals = {a for _o, a in hits}
        if len(vals) > 1:
            fs.append(finding(
                'cross_chapter_number',
                detail=f'「{ctxs}…{unit}」在不同章里是不同的数：'
                       f'{sorted(hits)}——两处都不在下发面里（都是写手自己写的），'
                       f'但它们互相打架',
                evidence=[f'{ctxs}|{unit}', *sorted(vals)]))

    # ── ③ 全书篇幅总账（**逐章合规不蕴含全书不胀**）─────────────────────────
    written = [r for r in rows if r.get('relpath')]
    tot = sum(r.get('chars') or 0 for r in written)
    bud = sum(r.get('budget_chars') or 0 for r in written)
    src = sum(r.get('source_chars') or 0 for r in written)
    if tot:
        fs.append(finding(
            'book_length_report',
            detail=(f'已写章合计 {tot} 字 ｜ 预算 {bud} 字 = {tot / bud:.2f}× ｜ '
                    f'源 {src} 字 = {tot / src:.2f}×') if bud and src
                   else f'已写章合计 {tot} 字',
            evidence=[str(tot), str(bud), str(src)]))
        lo, hi = M.book_range(src)
        if src and not (lo <= tot <= hi):
            fs.append(finding(
                'book_length',
                detail=f'全书 {tot / src:.2f}× 源字数（{tot} 字，契约带 {lo}~{hi}）'
                       f'——「重构不改变总长」是契约不是倾向。⚠ 全书篇幅是**预算之和**'
                       f'决定的，写手只能在自己那一章里动，根因在计划',
                evidence=[f'{tot / src:.2f}']))

    # ── ④ 名字面盘点 —— **筛不判，而且无条件出** ────────────────────────────
    #
    # ⚠⚠ 原型这一条写成 `if by_surface or collide:` **只在命中时才产出** ⇒ 盘上 12 份
    #   review **0 次**，于是「全书 0 个自造称谓、0 组撞名」这个**读数**永远说不出来，
    #   而「查过、干净」与「根本没在查」在报告上长得一模一样。对照组就在同一个函数里：
    #   `secret_timeline` 是逐条无条件产出的。⇒ **已改成无条件。**
    by_surface: dict[str, set[int]] = collections.defaultdict(set)
    for o, surfaces in inv_names:
        for s_ in surfaces:
            by_surface[s_].add(o)
    dup: dict[str, list[str]] = collections.defaultdict(list)
    for cid, c in book.characters.items():
        dup[c.get('display_name')].append(cid)
    collide = {k: v for k, v in dup.items() if k and len(v) > 1}
    fs.append(finding(
        'name_surface_survey',
        detail=f'自造称谓 {len(by_surface)} 个'
               + (f'（{dict(sorted((k, sorted(v)) for k, v in by_surface.items())[:6])}）'
                  if by_surface else '')
               + f'；下发名撞名 {len(collide)} 组' + (f'（{collide}）' if collide else '')
               + '　——**只摆料不判对错**：原文若本就管两个人叫同一个名字，'
                 '改写照样叫才是保真的；这一条要人来看',
        evidence=sorted(by_surface) + sorted(collide)))

    # ── ⑤ 风格块夹带情节 ────────────────────────────────────────────────────
    #
    # ⚠ **盘点通道不盘点字段**：它是在某一维上被发现的，但风格块把 voice 八维 + 写作习惯
    #   **整份**发给每一章 ⇒ 要扫的是**它实际发出去的那整段**。
    # ⚠ 检测器用**全书**名表与**全量** secrets——收窄下发面是修复，收窄检测器是削弱。
    # 扫写手真正会看到的每一份逐章投影，不扫一份它不再收到的
    # 书级聚合块。收窄下发面是修复，检测面仍要覆盖全书所有实际投影。
    blob = '\n'.join(
        prompts.style_block(
            book.style,
            source_ordinals=tuple(entry.get('from_source_ordinals') or []))
        for entry in book.chapters.values())
    all_names = {c.get('display_name') for c in book.characters.values()}
    kw_hits = sorted({k for s in book.secrets for k in secret_keywords(s.get('fact'))
                      if norm(k) and norm(k) in norm(blob)})
    nm_hits = sorted({n for n in all_names if n and len(n) >= 2 and n in blob})
    if kw_hits or nm_hits:
        fs.append(finding(
            'style_block_carries_plot',
            detail=f'stage3 的风格块（全量直发每一章）里出现了 secret 片段 {kw_hits[:3]} '
                   f'/ 人物名 {nm_hits[:5]}——风格描述本不该点名情节与人。'
                   f'根因在 stage3 的抽取契约，重写本章修不掉',
            evidence=kw_hits + nm_hits))

    # ── ⑥ `must_hold`：确定性层判不了，**必须显式说「没查」而不是让它消失** ────
    n_hold = sum(len(be.get('must_hold') or []) for c in book.chapters.values()
                 for be in (c.get('beats') or []))
    fs.append(finding(
        'must_hold_unver',
        detail=f'全书 {n_hold} 条【必须成立、不必写在纸面上】的材料 —— '
               f'这一组按定义就查不了文面，确定性层**没有检查它们**。'
               f'要判只能上语义层，在那之前别把「没报违规」读成「守住了」',
        evidence=[str(n_hold)]))
    return fs


# ─────────────────────────────────────────────────────────────────────────────
# 六、返工与收缩的回归护栏 —— `R-7.12` / `R-8.5` 的落点之一
# ─────────────────────────────────────────────────────────────────────────────

def trim_regressions(prev: str, new: str, entry: dict[str, Any], book: M.Book, *,
                     mode: str) -> list[str]:
    """这一轮把上一轮**弄坏了**吗 → 坏在哪的人话列表（空 = 没变坏）。

    ⚠⚠ **四条护栏没有一条在查「其余一字不动」**——它们查的是**没变得更坏**。
    那句叮嘱只写在 prompt 里（`REVISE_TAIL` 第二条），**没有任何守卫**。登记在这里。

    ⚠ 判准是「新稿在这几条上不劣于旧稿」，**不是「新稿全绿」**：旧稿本来就带着问题，
    要求全绿等于把一次修订变成一次必然失败的重写。
    """
    def hits(text: str) -> collections.Counter:
        return collections.Counter(f.rule for f in chapter_findings(
            text, entry, book, mode=mode, given=''))

    a, b = hits(prev), hits(new)
    out: list[str] = []
    for rule, label in (('verbatim_missing', '丢了【原样写入】的原话'),
                        ('keep_value_missing', '丢了【数值不得改动】的数字'),
                        ('draft_truncated', '结尾被削断'),
                        ('dialogue_halved', '把直接引语腰斩了'),
                        ('dialogue_fewer', '把台词压成了转述'),
                        ('must_appear_missing', '丢了【必须在场】的物件')):
        if b[rule] > a[rule]:
            out.append(f'{label}（{rule}：{a[rule]} → {b[rule]}）')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 七、报告编排 —— **一条注册判据恰好一行**
# ─────────────────────────────────────────────────────────────────────────────

#: 落盘闸门那一相**能**产出的判据。逐条手列，**不自动发现**。
#: ⚠ 少列一条，那一条在报告上就变 `skipped`——而 `skipped` 与「跑了没命中」是两种颜色。
_FROM_CHAPTER: tuple[str, ...] = (
    'draft_too_short', 'draft_truncated', 'meta_leak', 'names_absent',
    'verbatim_missing', 'verbatim_typo', 'verbatim_split', 'keep_value_missing',
    'must_appear_missing', 'secret_leak', 'secret_unknown',
    'invented_number', 'invented_name',
    'length_report', 'length_over', 'length_under',
    'overlap_report', 'deep_overlap_high',
    'dialogue_report', 'dialogue_halved', 'dialogue_fewer', 'dialogue_broken',
    'dialogue_ratio_drift', 'quotes_in_unquoted_corpus',
    'layout_drift', 'para_len_drift', 'simile_drift', 'xinli_drift',
    'band_drift', 'dash_report', 'dash_drift', 'short_run_report', 'quote_unbalanced',
)

#: **只有全书验收相**产得出来的：单章视角看不到，或者要跨章比。
_FROM_REVIEW: tuple[str, ...] = (
    'event_landing', 'event_landing_unver', 'material_malformed',
    'draft_missing', 'draft_duplicated',
    'no_secrets_in_book', 'secret_timeline', 'cross_chapter_number',
    'book_length_report', 'book_length', 'name_surface_survey',
    'style_block_carries_plot', 'must_hold_unver', 'convergence_report',
)

#: 付费书级语义审判产出的两条。实现刻意在 `semantic.py`，不混进 0 成本机械检查。
_FROM_SEMANTIC: tuple[str, ...] = (
    'semantic_secret_leak', 'semantic_secret_coverage',
)

#: `unver` 的五条：**必须走 `unverifiable(rule, why)`**（`builder._status_of` 会拒收
#: 一条 `sev='unver'` 却没说出「这次为什么够不着」的判据）。
_UNVER: frozenset[str] = frozenset({
    'must_hold_unver', 'event_landing_unver', 'draft_missing', 'draft_duplicated',
    'no_secrets_in_book'})


def _why_of(rule: str, fs: list[Finding], *, empty: str) -> str:
    """`unver` 那一行的「这次为什么够不着」。没命中时也要说得出话。"""
    return '；'.join(f.detail for f in fs[:3]) if fs else empty


#: 写作相**够不着**的那几条：它们要么是书级的、要么要跨章比。
#: ⚠ 报告里它们落 **`unver` 而不是 `skipped`**——两者是两种颜色：
#: `skipped` 说的是「实现忘了调」（一个 bug），而这里是「**这一相判不了它**」（判据的边界）。
_REVIEW_ONLY_WHY = '写作相判不了它——它是书级/跨章的，由 `stage5.review` 报（0 成本）'


def check(*, chapter_findings_by_rule: dict[str, list[Finding]],
          scanned: dict[str, str], unver_empty: dict[str, str],
          secret_blind: str | None = None, phase: str = 'review') -> list[Outcome]:
    """把逐章 + 书级的全部结论编排成「一条注册判据恰好一行」。

    ⚠ **它遍历 `_FROM_CHAPTER + _FROM_REVIEW`，不遍历命中的那些**——报告不是由实现的
    输出生成的，所以一条判据不可能因为「实现忘了调」而从报告里消失。

    `phase='write'` ⇒ 这是**落盘闸门**那一相：书级/跨章那几条它够不着，落 `unver` 并说清
    「由验收相报」。⚠ **不许让它们落 `skipped`**：`skipped` 的措辞是「实现未被调用」，
    那是一句**假话**——它们的实现在，只是这一相判不了。而一份 47 行全 `skipped` 的报告
    徽章是 `fail`，读起来像「全查过全没过」，实际上什么都没说。

    `secret_blind` 非 `None` ⇒ **这本书的字面探针盲区 100%**，`secret_leak` 落 `unver`
    而不是打一盏绿灯（实测 21 条 secret 里 19 条连它自己的揭示章都命中不了）。
    """
    out: list[Outcome] = []
    for rule in (*_FROM_CHAPTER, *_FROM_REVIEW):
        fs = chapter_findings_by_rule.get(rule, [])
        sc = scanned.get(rule)
        if phase == 'write' and rule in _FROM_REVIEW:
            out.append(unverifiable(rule, _REVIEW_ONLY_WHY, scanned=sc))
            continue
        if rule == 'secret_leak' and secret_blind is not None:
            out.append(unverifiable(rule, secret_blind, scanned=sc))
            continue
        if rule in _UNVER:
            out.append(unverifiable(
                rule, _why_of(rule, fs, empty=unver_empty.get(rule, '这一次没有够不着的地方')),
                scanned=sc))
            continue
        out.append(ran(rule, fs, scanned=sc))
    return out
