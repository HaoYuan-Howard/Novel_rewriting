#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 18 条**机械**判据的实现。行在 `contracts/judges/stage6.py`（`Q-12`）。
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

from contracts.invariants import ENDING_SHAPES
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.core.bands import ending_shape, sep_band_of_text
from novel.core.facts import locate_secret, num_unit_keys, secret_keywords
from novel.core.measure import measure
from novel.core.naming import invented_names, name_stems
from novel.core.text import TERMINAL_PUNCT, norm, window_overlap
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
            fs.append(finding('hook_reuses_source',
                              detail=f'与原作文案段的 20 字窗口重合率 {ov:.1%}'
                                     f'（上限 {REUSE_MAX:.0%}）——这是重写，不是改标点',
                              evidence=[f'{ov:.3f}']))
    ovb = window_overlap(body, lead_text)
    if ovb is not None and ovb > TRANSCRIBE_MAX:
        fs.append(finding('hook_transcribes_body',
                          detail=f'与开篇正文的 20 字窗口重合率 {ovb:.1%}'
                                 f'——文案在誊抄正文，不是在提炼',
                          evidence=[f'{ovb:.3f}']))

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
    return fs


# ─────────────────────────────────────────────────────────────────────────────
# 一份产物 → 18 个机械 `Outcome`（语义三条由 semantic.outcomes 产）
# ─────────────────────────────────────────────────────────────────────────────

#: 逐条文本判的那 14 条（5 hard + 9 info）。
_PER_TEXT_JUDGED = (
    'hook_empty', 'hook_truncated', 'hook_reuses_source', 'hook_invented_name',
    'hook_para_sep', 'hook_transcribes_body', 'hook_invented_number',
    'hook_length', 'hook_shape', 'hook_quote_style', 'hook_quote_unbalanced',
    'hook_ending_shape',
)
#: 逐条文本判、但 sev 是 metric 的那两条。
_PER_TEXT_METRIC = ('hook_secret_literal', 'hook_quote_shape')


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
