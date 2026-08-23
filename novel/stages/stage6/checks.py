#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 19 条**机械**判据的实现。行在 `contracts/judges/stage6.py`（`Q-12`）。
（另 3 条 `check_kind='semantic'` 的产地在 `semantic.outcomes`——付费审判读盘上产物，
与本模块的 0 成本判据在 `hook._judge_artifact` 合并成同一份报告。）

⚠⚠ **唯一的判定落点。** 三个候选、定稿、以及恒等基线（拿原作文案段当输入）
走的都是 `hook_findings()` 这一个函数——**基线要有意义，量的就必须是同一把尺子**。

⚠ 判据 id 一律照抄文档，不加 stage 前缀（它们是与 `wontfix.json` 的键的契约）。

【阈值都在本模块，不进 `contracts/invariants.py`】它们只有一个消费方，
而 `R-11.4` 明说判据阈值不发前端 ⇒ 塞进 invariants 就要再登记一张「不发」表，
给单消费方的东西造第二套机制。`stage6.md` §四 那张阈值表自己写的就是「模块常量」。
"""
from __future__ import annotations

import re

from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.core.bands import ending_shape, sep_band_of_text
from novel.core.facts import locate_secret, num_unit_keys, secret_keywords
from novel.core.measure import measure
from novel.core.naming import invented_names, name_stems
from novel.core.spans import lcs
from novel.core.text import TERMINAL_PUNCT, norm, norm_index, window_overlap
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage6.measure import hidden_secrets, quote_stats
from novel.stages.stage6.prompts import Material

#: 篇幅 / 段落形态的容许带，相对**原作文案段**。
HOOK_BAND = (0.6, 1.6)
SHAPE_BAND = (0.5, 2.0)

#: 与**原作文案段**的 20 内容字窗口重合上限。原作文案现在作为结构金标准下发，
#: 因而这条从「人可能抄」变成模型也可违反的真实门；允许学结构，不允许复用长句。
REUSE_MAX = 0.0

#: 与**开篇正文**的窗口重合上限。
#: ⚠ 原型这里是**行内字面量**（`> 0.10`），而同一族的另一个阈值 `REUSE_MAX` 有落点
#: ——`stage6.md` §四 点名的就是这一处。提成常量的收益不是好看：
#: 它让「这两个阈值为什么差 5 倍」变成一个**看得见**的问题（答案见 `hook_reuses_source`
#: 那条注释：严格程度与「模型能不能违反」正好反着来）。
TRANSCRIBE_MAX = 0.10

#: 文案长度下限（内容字符）。低于它整份早退，**其余判据一条都不跑**。
EMPTY_MIN = 20


def _lcs_detail(candidate: str, reference: str, *, reference_name: str) -> tuple[str, list[str]]:
    """把归一串的 LCS 确定性回映到两侧原始字节，供报告与定向重试直接定位。"""
    cand_norm, cand_index = norm_index(candidate)
    ref_norm, ref_index = norm_index(reference)
    cand_start, length = lcs(cand_norm, ref_norm)
    if length <= 0:
        return '最长公共子串 norm=0（没有可回映片段）', []
    fragment = cand_norm[cand_start:cand_start + length]
    ref_start = ref_norm.find(fragment)
    if ref_start < 0:  # lcs 的后置条件；保留显式说明，绝不伪造原始偏移。
        return f'最长公共子串 norm={length}，但无法回映 {reference_name} 原始字节', []
    cand_raw_start = cand_index[cand_start]
    cand_raw_end = cand_index[cand_start + length - 1] + 1
    ref_raw_start = ref_index[ref_start]
    ref_raw_end = ref_index[ref_start + length - 1] + 1
    cand_raw = candidate[cand_raw_start:cand_raw_end]
    ref_raw = reference[ref_raw_start:ref_raw_end]

    def shown(value: str) -> str:
        return value if len(value) <= 80 else value[:77] + '…'

    return (
        f'最长公共子串 norm={length}；候选 raw[{cand_raw_start}:{cand_raw_end}]='
        f'{shown(cand_raw)!r}；{reference_name} raw[{ref_raw_start}:{ref_raw_end}]='
        f'{shown(ref_raw)!r}',
        [cand_raw[:80]],
    )


#: 结构距离读数并排展示的几档窗口。**不是阈值**，是让方差看得见的刻度。
#: ⚠ 20 那一档与 `REUSE_MAX` 守的是同一件事（`hook_reuses_source`），
#: 放在同一行里报，是为了让「闸报 0 违规」与「结构其实很近」这两个读数**挨着出现**。
SOURCE_DISTANCE_WINDOWS = (4, 8, 20)


def _source_distance_detail(body: str, src_hook: str) -> tuple[str, list[str]]:
    """文案与原作文案段的**呈现结构距离**读数。**度量，不是门。**

    【为什么需要它】`hook_reuses_source` 只看「连续 20 个内容字符是否相同」，
    而 `R-9.11` 的尾注**明写着允许**学结构、节拍、对白密度与最后一击的功能。
    两条合起来留了一个看不见的地带：一条候选可以逐句改写措辞、却完整复用原作的
    信息出场顺序并落在同一句收尾上——闸报 0 违规，而它离原作其实很近。
    实测一本书三条候选：最长逐字重合 10 / 6 / 3 字（闸线 20），4 字窗口重合率
    19.7% / 8.2% / 0.0%，**贴合度差 20 倍，而闸对三条一律报 0 违规**。

    ⚠⚠ **当前不给阈值，这是刻意的。** 本仓的机械探针有过跑间方差极大的实测
    （同条件重跑同一个探针从 4.41 漂到 0.00），而结构贴近**本身不违约**——
    `source_architecture` 这条策略是被 prompt 明确要求继承原作结构的。
    要不要设线，得先有多轮采样量出噪声基线，再由人裁决。同
    `hook_quote_shape` 那条注释的纪律。

    ⚠ 窗口重合率一律走 `core.text.window_overlap`，最长重合走 `core.spans.lcs`
    ——与 `hook_reuses_source` **同一对函数**。这里另写一份"内容字符提取"，
    两处口径就会漂，而漂的表现是其中一处悄悄不再拦。
    """
    rates: list[str] = []
    for window in SOURCE_DISTANCE_WINDOWS:
        rate = window_overlap(body, src_hook, window=window)
        rates.append(f'{window} 字 '
                     + ('量不了' if rate is None else f'{rate:.1%}'))
    _start, longest = lcs(norm(body), norm(src_hook))

    def last_line(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ''

    cand_last, src_last = last_line(body), last_line(src_hook)
    _s, tail_shared = lcs(norm(cand_last), norm(src_last))
    tail = (f'末行与原作末行最长逐字重合 {tail_shared} 字'
            if cand_last and src_last else '末行无法比对')
    return (
        f'与原作文案段的窗口重合率：{" / ".join(rates)}；'
        f'最长逐字重合 {longest} 字（`hook_reuses_source` 的闸线是 20）；{tail}。'
        f'**度量不是门**：结构贴近本身不违约（`R-9.11` 尾注明许学结构），'
        f'阈值要先量跑间方差再谈',
        [f'{longest}/{tail_shared}'],
    )


def _source_distance_finding(body: str, src_hook: str | None) -> Finding:
    """`hook_source_distance` 的唯一构造口；短文本也必须留下读数。"""
    if src_hook:
        detail, evidence = _source_distance_detail(body, src_hook)
    else:
        detail, evidence = '本书没有原作文案段可比，结构距离量不了', []
    return finding('hook_source_distance', detail=detail, evidence=evidence)


def hook_findings(text: str, *, mat: Material, secrets: list[dict], arc: dict | None,
                  characters: dict | None, src_hook: str | None,
                  style_hook: dict | None, quote_problems: list[str]) -> list[Finding]:
    """对**一条文案**跑全部确定性判据 → `[Finding]`。

    返回的是 `Finding` 而不是 `Outcome`：本层一份产物里有**四条文本**
    （3 候选 + 定稿）各自判一遍，而 `Outcome` 是「一条判据这一次跑出来的东西」
    ——四条文本的产出要**并进同一个 `Outcome`**，由 `check()` 去合。
    """
    fs: list[Finding] = []
    body = text or ''
    nb = norm(body)
    lead_text = '\n'.join(r.get('text') or '' for r in mat.lead)

    # ── ① 基本可用性 ────────────────────────────────────────────────────────
    if len(nb) < EMPTY_MIN:
        fs.append(finding('hook_empty',
                          detail=f'文案只有 {len(nb)} 个内容字符——没写出来',
                          evidence=[body[:40]] if body else []))
        # `R-9.23` 要求这一读数无条件报出。短文本虽不能继续跑其余语义噪声探针，
        # 仍然能与原作文案比较；漏掉它会被 metric 聚合洗成“扫描过且 OK”。
        fs.append(_source_distance_finding(body, src_hook))
        return fs                        # 空的就不必往下判了，后面全是噪声

    if body.rstrip()[-1] not in TERMINAL_PUNCT:
        fs.append(finding('hook_truncated',
                          detail=f'文案结尾没有句末标点，疑似被截断：…{body.rstrip()[-24:]!r}',
                          evidence=[body.rstrip()[-24:]]))

    # ── ② 不复用原句 ────────────────────────────────────────────────────────
    #    原作文案是**唯一**的「不许复用」基准；正文那条是 info——
    #    文案本来就要复述正文的事，偶有短语重合正常，整段誊抄才是问题。
    if src_hook:
        ov = window_overlap(body, src_hook)          # 无豁免
        if ov is not None and ov > REUSE_MAX:
            overlap_detail, overlap_evidence = _lcs_detail(
                body, src_hook, reference_name='原作文案')
            fs.append(finding('hook_reuses_source',
                              detail=f'与原作文案段的 20 字窗口重合率 {ov:.1%}'
                                     f'（上限 {REUSE_MAX:.0%}）——这是重写，不是改标点；'
                                     f'{overlap_detail}',
                              evidence=overlap_evidence or [f'{ov:.3f}']))
    ovb = window_overlap(body, lead_text)
    if ovb is not None and ovb > TRANSCRIBE_MAX:
        overlap_detail, overlap_evidence = _lcs_detail(
            body, lead_text, reference_name='开篇正文')
        fs.append(finding('hook_transcribes_body',
                          detail=f'与开篇正文的 20 字窗口重合率 {ovb:.1%}'
                                 f'——文案在誊抄正文，不是在提炼；{overlap_detail}',
                          evidence=overlap_evidence or [f'{ovb:.3f}']))

    # ── ③ 发明越界：人名 / 数字 ─────────────────────────────────────────────
    #
    # 【引语没有确定性判据 —— 这是恒等基线否掉的一条，不是漏写的】
    # 曾有一条 hard：「文案里引号内的句子必须能在正文里找到」。理由听着很硬：
    # 引号是在向读者承诺「书里有人这么说过」。**但恒等基线当场把它判死了**——
    # 拿两本原作文案跑，一本报 1 句、一本报 6 句，全都「找不到」；
    # 进一步查**原作全书**，这 7 条引语逐字命中 **0 章**，最长公共子串只有 8%~29%。
    # ⇒ **原作文案里的台词，原作正文里根本不存在**，那是这个体裁的常规手法。
    # 判据错，不是数据错 ⇒ 降为 `hook_quote_unver`（永远输出）。
    given = mat.given
    nm = invented_names(body, given, name_stems(characters))
    if nm:
        fs.append(finding('hook_invented_name',
                          detail=f'文案出现下发面里没有的【人物称谓】{nm[:6]}'
                                 f'——逐个核对指的是不是已有角色',
                          evidence=nm))
    # 比较归一后的「值+单位」，展示仍用正文原字面：这样 `30岁` 与
    # `三十岁` 是同一事实，同时共享层也能过滤「一百成功」→「一百成」
    # 这类把普通词首字误当单位的假阳。
    body_nums = num_unit_keys(body)
    given_nums = num_unit_keys(given)
    inv = sorted(body_nums[k] for k in body_nums.keys() - given_nums.keys())
    if inv:
        fs.append(finding('hook_invented_number',
                          detail=f'文案出现下发面里没有的【数字+单位】{inv[:6]}——逐个核对',
                          evidence=inv))

    # ── ④ 卖点用了哪几条（度量）──────────────────────────────────────────
    hid, _no_time = hidden_secrets(secrets, mat.upto)
    hid_ids = {s.get('secret_id') for s in hid}
    used: list[tuple[str, str]] = []
    for s in secrets or []:
        for k in secret_keywords(s.get('fact')):
            if (sp := locate_secret(k, body)) is not None:
                # ⚠ 凭据必须回映成**文案自己的字节**——命中的片段来自 `secret.fact`，
                # 直接拿它当凭据展示等于把秘密原文抄进报告。
                used.append((s.get('secret_id'), body[sp[0]:sp[1]]))
                break
    late = [sid for sid, _ in used if sid in hid_ids]
    fs.append(finding(
        'hook_secret_literal',
        detail=(f'与秘密原文**逐字**重合 {len(used)}/{len(secrets or [])} 条'
                + (f'（其中 {len(late)} 条读者到第 {mat.upto} 章还没看到：{late[:3]}）'
                   f'——**说破秘密现在是授权的**，这条只是记账，不是违约' if late else '')
                + (f'：{[(sid, seg[:14]) for sid, seg in used[:3]]}' if used else
                   '——**0 条是常态**，它不代表卖点没用上：文案按体裁在改写，'
                   '字面探针在这个产物类上结构性失效（同 hook_spoiler_unver）')),
        evidence=[seg for _, seg in used]))

    # ── ⑤ 形态：贴合本书自己的**文案段**，不是正文 ─────────────────────────
    #
    # ⚠⚠ **必须用产出 `measurable.hook` 的那把尺子来量。** 三个口径实测互不相同：
    # 某本 `measure()['chars']`=319、`len(norm())`=274、`len(strip())`=347。
    # 拿其中一个去比另一个算出来的基准，错的不是某个数，是那个**跨口径的运算**
    # ——而它不会报错，只会让整条判据系统性偏 15%。
    got = measure(body)
    if style_hook and got:
        base = style_hook.get('chars') or 0
        if base:
            lo, hi = base * HOOK_BAND[0], base * HOOK_BAND[1]
            if not lo <= got.chars <= hi:
                fs.append(finding('hook_length',
                                  detail=f'{got.chars} 字，本书文案段 {base} 字'
                                         f'（提示带 {lo:.0f}~{hi:.0f}）',
                                  evidence=[f'{got.chars}/{base}']))
        pb = style_hook.get('avg_paragraph_chars') or 0
        gp = got.avg_paragraph_chars or 0
        if pb and gp and not pb * SHAPE_BAND[0] <= gp <= pb * SHAPE_BAND[1]:
            fs.append(finding('hook_shape',
                              detail=f'平均每段 {gp:.1f} 字，本书文案段是 {pb:.1f} 字/段'
                                     f'（文案的体裁特征是短句短段）',
                              evidence=[f'{gp:.1f}/{pb:.1f}']))
        qs = style_hook.get('quote_styles') or []
        odd = [q for q in (got.quote_styles or []) if q not in qs]
        if qs and odd:
            fs.append(finding('hook_quote_style',
                              detail=f'文案用了 {odd}，本书是 {qs}', evidence=odd))

    # ── ⑥ 引号配对异常：**留着报，不静默修** ───────────────────────────────
    if quote_problems:
        fs.append(finding('hook_quote_unbalanced',
                          detail=f'引号配对异常，归一化**拒绝猜**并原样留下：{quote_problems[:4]}'
                                 f'——引号错乱多半是台词归属错位，交人工',
                          evidence=list(quote_problems)))

    # ── ⑦ 段间分隔：**后置条件**，归一化已经做过了 ─────────────────────────
    #
    # ⚠ 两边都按**字节**取档位，不碰 `avg_paragraph_chars`（那一维看不见分隔符）
    # 也不碰 `paragraph_sep_chars`（那一维有 off-by-one，短文案判反）。
    if src_hook:
        gb, sb = sep_band_of_text(body), sep_band_of_text(src_hook)
        if gb != sb:
            # ⚠⚠ **两种病因，处方相反，此前共用一句错的诊断。**
            # `normalize_paragraphs` 是 `sep.join(按行切开的非空行)`：它能把
            # 分隔符从 `\n` 换成 `\n\n`，**但造不出段落边界**。模型回一整段密排
            # 正文（一个 `\n` 都没有）时 `lines` 长度为 1，join 出来还是它自己，
            # `changed=False` —— 于是这条 HARD 亮起，还附一句「归一没跑或坏了」，
            # 把人支去查一个**根本没问题**的归一化（判据 32：说错病因等于制造第二个 bug）。
            one_para = '\n' not in body.strip()
            fs.append(finding(
                'hook_para_sep',
                detail=(f'文案是**一整段**（正文里一个换行都没有），而本书原作文案段是'
                        f'【{sb}】——**归一化修不掉这个**：它只换得了分隔符，'
                        f'造不出段落边界。要么重出一版文案，要么人工分段后 `--apply-edit`'
                        if one_para else
                        f'文案是【{gb}】，本书原作文案段是【{sb}】'
                        f'——归一化应该已经修掉它了，这条亮了说明归一没跑或坏了'),
                evidence=[f'{gb}/{sb}' + ('（成稿单段）' if one_para else '')]))

    # ── ⑧ 收尾形态：`hook_style.end_question` 唯一可确定性检测的投影 ────────
    #
    # 【为什么需要这一条】`end_question` 逐书下发给模型，但**没有任何一层在查它**——
    # 实测五本：一本的 `end_question` 写着「用带余波的短句戛然而止，**不展开后续**」，
    # 成品却展开了后续还加了个反问；另一本写着「以**宣言式断言**收尾」，成品是一句反问。
    # 两本都违反，全链一处没出声。
    # ⚠ 更值得记的是那个形状：**合规的那个候选就在同一份产物里**，
    # 人定稿时选了违规的那个——**没有判据，人也没有依据去挑**。
    if src_hook:
        gshape, sshape = ending_shape(body), ending_shape(src_hook)
        if gshape and sshape and gshape != sshape:
            last = [ln for ln in body.split('\n') if ln.strip()][-1].strip()
            fs.append(finding('hook_ending_shape',
                              detail=f'收尾是【{gshape}】，本书原作文案段是【{sshape}】'
                                     f'——收尾句是文案的最后一击，对一遍 hook_style.end_question',
                              evidence=[last]))

    # ── ⑨ 引语形态：**度量，不是门** ───────────────────────────────────────
    gq_n, gq_max = quote_stats(body)
    sq = (f'原作文案 {quote_stats(src_hook)[0]} 条 / 最长 {quote_stats(src_hook)[1]} 字'
          if src_hook else '本书没有原作文案段可比')
    dr = (style_hook or {}).get('dialogue_ratio')
    fs.append(finding(
        'hook_quote_shape',
        detail=(f'引语 {gq_n} 条 / 最长 {gq_max} 字（≥6 字才计）；{sq}'
                + (f'；对白比 {got.dialogue_ratio} vs 原作 {dr}'
                   if got is not None and got.dialogue_ratio is not None and dr is not None
                   else '')
                + '。仍是度量不是门：禁令已撤，但要不要设阈值得先量方差'),
        evidence=[f'{gq_n}/{gq_max}']))

    # ── ⑩ 结构距离：**度量，不是门** ───────────────────────────────────────
    #
    # ⚠ 与 ⑨ 并排放，因为它们回答的是同一类问题（"这条候选像谁"），
    #   而两条都刻意不给阈值。没有原作文案段时**不静默跳过**：
    #   "没得比"与"比过了很远"必须分得开。
    fs.append(_source_distance_finding(body, src_hook))
    return fs


_ATTRIBUTION_VERBS = ('送给', '赠给', '交给', '给了', '送了', '赠了', '给', '送', '赠')


def _character_names(mat: Material) -> list[str]:
    chars = (((mat.story_truth or {}).get('entities') or {}).get('characters') or {})
    names: set[str] = set()
    for character in chars.values():
        if not isinstance(character, dict):
            continue
        if canonical := str(character.get('canonical') or '').strip():
            names.add(canonical)
        for address in character.get('address') or []:
            if isinstance(address, dict) and (surface := str(address.get('surface') or '').strip()):
                names.add(surface)
    return sorted(names, key=lambda item: (-len(item), item))


def _fact_attribution_probe(text: str, mat: Material) -> list[tuple[str, str, str, bool]]:
    """弱字面探针：人物 + 赠与动词 + 对象；只记账，不作真假裁决。"""
    truth_events = [
        str(event)
        for chapter in mat.story_map
        for event in (chapter.get('events') or [])
        if str(event).strip()
    ]
    out: list[tuple[str, str, str, bool]] = []
    seen: set[tuple[str, str, str]] = set()
    verbs = '|'.join(re.escape(verb) for verb in _ATTRIBUTION_VERBS)
    for name in _character_names(mat):
        pattern = re.compile(
            re.escape(name) + rf'[^。！？\n]{{0,8}}?(?:{verbs})'
            r'(?P<object>[^，。！？；：\n]{1,12})')
        for match in pattern.finditer(text):
            obj = match.group('object').strip(' “”—-')
            fragment = match.group(0)
            key = (name, obj, fragment)
            if not obj or key in seen:
                continue
            seen.add(key)
            supported = any(
                name in event and obj in event
                and any(verb in event for verb in _ATTRIBUTION_VERBS)
                for event in truth_events
            )
            out.append((name, obj, fragment, supported))
    return out


def _fact_attribution_metric(
    texts: list[tuple[str, str, list[Finding]]], mat: Material,
) -> Finding:
    rows = [(label, *_row) for label, text, _fs in texts
            for _row in _fact_attribution_probe(text, mat)]
    supported = sum(1 for _label, _name, _obj, _frag, ok in rows if ok)
    samples = [
        f'{label}:{name}→{obj}（{"故事地图有同字面" if ok else "故事地图未同字面命中"}）'
        for label, name, obj, _fragment, ok in rows[:8]
    ]
    detail = (
        f'弱事实归因探针抽到 {len(rows)} 条“人物 + 送/给/赠 + 对象”，'
        f'{supported} 条在 Stage4 故事地图事件中有同人物、同对象和赠与词字面支持'
        + (f'：{samples}' if samples else '。本轮未抽到可比短语')
        + '。这是低召回/低精度度量，不是矛盾门；未命中必须交语义审判或人工核对。'
    )
    return finding('hook_fact_attribution', detail=detail,
                   evidence=[fragment for _label, _name, _obj, fragment, _ok in rows[:8]])


# ─────────────────────────────────────────────────────────────────────────────
# 一份产物 → 机械 `Outcome`（语义三条由 semantic.outcomes 产）
# ─────────────────────────────────────────────────────────────────────────────

#: 逐条文本判的那 14 条（5 hard + 9 info）。
_PER_TEXT_JUDGED = (
    'hook_empty', 'hook_truncated', 'hook_reuses_source', 'hook_invented_name',
    'hook_para_sep', 'hook_transcribes_body', 'hook_invented_number',
    'hook_length', 'hook_shape', 'hook_quote_style', 'hook_quote_unbalanced',
    'hook_ending_shape',
)
#: 逐条文本判、但 sev 是 metric 的那两条。
_PER_TEXT_METRIC = ('hook_secret_literal', 'hook_quote_shape', 'hook_source_distance')


def check(*, texts: list[tuple[str, str, list[Finding]]], mat: Material,
          secrets: list[dict], arc: dict | None,
          lead_upto_ch: int, hook_chars: int | None, approved: bool,
          stale: list[str] | None, missing: list[str] | None) -> list[Outcome]:
    """一份 `hook.json` → **18 个机械 `Outcome`，一条判据一个**。

    ⚠ `texts` 是 `[(标签, 文本, 这条文本的 findings)]`，四份（3 候选 + 定稿）。
    **每条 finding 的 `detail` 由调用方加 `[标签]` 前缀**——
    原型只报定稿那一条，于是盘上三本命中集全空，而
    「合规的那个候选就在同一份产物里」这件事在报告上看不见。
    ⚠ 顺带治一个真病：**原型从来没读过 `approved.findings`**，
    于是某本 `approach='human_edit'` 与任何候选都不匹配 ⇒ 人工稿自己的判定一条都没进报告。

    ⚠ `scanned` 一律填：**一个不知道自己扫了多少的绿灯没有判别力**（判据 31）。
    """
    scanned = f'{len(texts)} 份文本（{", ".join(lbl for lbl, _t, _f in texts)}）'
    by_rule: dict[str, list[Finding]] = {}
    for _lbl, _t, fs in texts:
        for f in fs:
            by_rule.setdefault(f.rule, []).append(f)

    out: list[Outcome] = []
    for rule in _PER_TEXT_JUDGED + _PER_TEXT_METRIC:
        out.append(ran(rule, by_rule.get(rule, []), scanned=scanned))

    # ── 书级度量 ────────────────────────────────────────────────────────────
    #
    # ⚠⚠ **字数一律现算**：`candidates[].chars` 已被新契约裁掉（能 0 成本重算的字段
    # 不落盘）。而且必须走 `measure()` 那把尺子——同一条候选 `measure()['chars']`=253
    # 而 `len(text)`=277，**差 9.5%**。读产物或改用 `len()`，
    # 都会让报告里的字数与 `hook_length` 判据用的字数**不是同一个数**。
    out.append(ran('hook_fact_attribution', [_fact_attribution_metric(texts, mat)],
                   scanned=scanned))
    out.append(ran('hook_report',
                   [finding('hook_report',
                            detail=f'下发范围到第 {lead_upto_ch} 章；'
                                   f'各文本字数 {_chars_of(texts)}；'
                                   f'原作文案段 '
                                   f'{hook_chars if hook_chars is not None else "?"} 字',
                            evidence=[str(lead_upto_ch)])],
                   scanned=scanned))

    # ── 三条工作流/前提状态 ────────────────────────────────────────────────
    out.append(unverifiable(
        'hook_not_approved',
        '这份文案还没有人定稿——先看质量排序，再跑 `stage6.approve --pick N`'
        if not approved else
        '已有人定稿；质量审判负责排序与矛盾门，人负责最终审美选择',
        scanned='approved 一格'))

    out.append(unverifiable(
        'hook_body_missing',
        f'下发范围内第 {list(mat.missing)} 章还没有成稿——'
        f'这份文案是照一份**不完整**的开篇写的'
        if mat.missing else
        f'第 1~{mat.upto} 章全部有成稿。⚠ 「成稿够不够撑起一段文案」不由本层判',
        scanned=f'第 1~{mat.upto} 章'))

    out.append(_body_stale_outcome(stale, missing, mat))
    return out


def _chars_of(texts: list[tuple[str, str, list[Finding]]]) -> str:
    """各文本的字数，**现算，走 `measure()` 那把尺子**。"""
    return ' · '.join(f'{lbl} {(measure(t).chars if measure(t) else 0)}'
                      for lbl, t, _f in texts) or '（无）'


def _body_stale_outcome(stale: list[str] | None, missing: list[str] | None,
                        mat: Material) -> Outcome:
    """`hook_body_stale`：按正文消费投影判断文案绑定的版本。"""
    scanned = f'{len(mat.lead)} 份开篇成稿'
    if missing:
        return unverifiable(
            'hook_body_stale',
            f'文案引用的 {len(missing)} 份成稿已不在盘上（{missing[:3]}），'
            f'无法证明它仍绑定当前正文集合',
            scanned=scanned)
    if stale:
        return unverifiable(
            'hook_body_stale',
            f'{len(stale)} 份成稿在这份文案之后改过（{stale[:3]}）'
            f'——文案判的不是盘上这一版正文，先重跑 `--recheck`',
            scanned=scanned)
    return unverifiable(
        'hook_body_stale',
        f'{len(mat.lead)} 份开篇成稿的 `consumed` 投影与这份文案记的**逐条相同**'
        f'——它判的就是盘上这一版正文',
        scanned=scanned)
