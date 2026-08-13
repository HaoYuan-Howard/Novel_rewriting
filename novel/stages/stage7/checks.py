#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 34 条判据的**实现**。行在 `contracts/judges/stage7.py`（`Q-12`）。

⚠⚠ **唯一的判定落点。** 真跑、`--recheck`、`--apply-edit` 走的都是
`rename_findings()` 这一个函数——三条路各判一遍就会长出三把尺子。

⚠ 判据 id 一律照抄文档，不加 stage 前缀（它们是与 `wontfix.json` 的键的契约）。

【阈值都在 `contracts/invariants.py`，与 stage6 相反】stage6 的四个阈值只有一个消费方
（判据自己），所以留在模块里；本层的 `MIN_SURFACE` / `RESIDUAL_*` 同时被
**替换器**（`subs.py`）与**判据**用，而那两处必须是同一个数——放在一层的模块里，
另一层就要 import 一个 `checks` 模块才能拿到一个常量。
"""
from __future__ import annotations

import re

from contracts.invariants import IDENTITY_BLIND, MIN_SURFACE
from contracts.schema.common import Finding
from contracts.schema.report import finding
from novel.core.naming import (
    NAME_TITLE, is_generic_appellation, rename_semantic_markers, strip_distinguisher,
)
from novel.core.style import (
    DASH_CORPUS_P95, SHORT_RUN_CORPUS_P95, dash_per_1k, short_run_per_1k,
)
from novel.report.findings import Outcome, ran, unverifiable
from novel.stages.stage7.measure import Problem, Unit

#: 称谓/亲属后缀落在**词尾**（`许总` / `李嬷嬷`）。
_TITLE_TAIL = re.compile(NAME_TITLE + r'$')


def _kept_tail(old: str, new: str) -> bool | None:
    """`old` 以某个称谓后缀结尾时，`new` 必须以**同一个**后缀结尾。

    这是「关系与职位不变」这条总原则唯一能确定性验的**一半**：
    「许总」→「林总」合格，「许总」→「林昭」把职位丢了。
    → `None` = 这条不适用（`old` 没有可识别的后缀）；`True/False` = 判定。

    ⚠ 另一半（**排行**）验不了，而它实测漏过：模型把「七郎」改成「六郎」，
    后缀还在、本条全绿，**而人物排行已经被改写**。
    """
    m = _TITLE_TAIL.search(old or '')
    return (new or '').endswith(m.group()) if m else None


def _generic_appellation_was_touched(old: str, new: str, subs: dict[str, str],
                                     surnames: set[str]) -> bool:
    """关系词本身被改了才算违规；「关系标签 + 真名」只换真名不算。

    `is_generic_appellation` 为了拦「姐姐」「女主」这类词会做前缀匹配，
    所以复合别名「女主方衡」也会命中。若同一张替换表能证明它只是把内嵌的
    `方衡→沈凌` 展开成 `女主方衡→女主沈凌`，关系标签逐字未动；除此之外
    一律继续保守地判为 touched。
    """
    if not is_generic_appellation(old, surnames):
        return False
    return not any(
        inner_old != old
        and inner_old in old
        and old.replace(inner_old, inner_new) == new
        for inner_old, inner_new in subs.items()
    )


def rename_findings(*, name_map: dict, subs: dict[str, str], problems: list[Problem],
                    applied: dict, residual: list[dict], baseline: dict[str, dict],
                    registry: dict[str, dict], pre_target: str, source_text: str,
                    residual_keys: set[str], stem_noise: dict[str, int],
                    family_members: dict[str, tuple[str, ...]],
                    dup: list[int], missing: list[int], mixed: list[int],
                    invalids: list[str], post_units: list[Unit],
                    title_line: str) -> list[Finding]:
    """对一份映射表 + 一次替换跑全部确定性判据 → `[Finding]`。**唯一判定落点。**"""
    fs: list[Finding] = []
    chars = name_map.get('characters') or []

    # ── ① 映射表自身的结构 ────────────────────────────────────────
    miss_cid = sorted(set(registry) - {c.get('char_id') for c in chars})
    extra_cid = sorted({c.get('char_id') for c in chars} - set(registry))
    bad_shape = [c.get('char_id') for c in chars
                 if c.get('renamed') and not (c.get('new_canonical') or '').strip()]
    if miss_cid or extra_cid or bad_shape:
        fs.append(finding('rn_llm_shape',
                          detail=f'映射表与人物表对不上：漏 {miss_cid[:6]}、多 {extra_cid[:6]}、'
                                 f'renamed 却没给新名 {bad_shape[:6]}',
                          evidence=[str(x) for x in miss_cid + extra_cid + bad_shape]))
    no_reason = [c.get('char_id') for c in chars
                 if not c.get('renamed') and not (c.get('not_renamed_reason') or '').strip()]
    if no_reason:
        fs.append(finding('rn_no_reason',
                          detail=f'{len(no_reason)} 个角色不改名却没说为什么：{no_reason[:8]}'
                                 f'——「不该改」与「漏了」在产物里长得一样',
                          evidence=[str(x) for x in no_reason]))

    # ── ② `build_subs` 攒下来的冲突（判定与展开在同一次遍历里产出）──
    for rule, detail, ev in problems:
        fs.append(finding(rule, detail=detail, evidence=list(ev)))

    # ── ③ 新名不许已经存在 ────────────────────────────────────────
    #    【只查完整新称呼，不查单字】新**姓字**必然在原文里另有普通用法
    #    （谢/李/王 都是常用字），拿单字判会把全部合法新名排除在外。
    #    单字那一维是 `rn_stem_noise`（metric，供人评估读感），**不是门**。
    #    【为什么拆成 hard + info 两条】**替换目标**里撞上 → 换完就分不清哪个是换来的
    #    ⇒ 真问题；**只在原作正文里**撞上 → 原文不参与组装，什么都不会坏，
    #    只说明这个字在本书语域里常用、读起来会串味 ⇒ 提示。硬拦后者就是把合法情况排除在外。
    hit = sorted({n for n in subs.values() if len(n) >= MIN_SURFACE and n in pre_target})
    if hit:
        fs.append(finding('rn_already_present',
                          detail=f'新称呼 {hit[:6]} 在替换目标（成稿+文案+书名）里已经出现过'
                                 f'——换完就分不清哪个是换来的，也可能撞上别的实体',
                          evidence=hit))
    only_src = sorted({n for n in subs.values()
                       if len(n) >= MIN_SURFACE and n not in pre_target and n in source_text})
    if only_src:
        fs.append(finding('rn_new_in_source',
                          detail=f'新称呼 {only_src[:6]} 在原作正文里出现过（成品里没有）——'
                                 f'不影响替换正确性，但这些字在本书语域里是常用的，读起来会串味',
                          evidence=only_src))

    # ── ④ 关系与职位不变 ─────────────────────────────────────────
    lost = [(o, n) for o, n in sorted(subs.items()) if _kept_tail(o, n) is False]
    if lost:
        fs.append(finding('rn_title_dropped',
                          detail=f'这些映射把称谓/职位后缀丢了：{lost[:6]}——'
                                 f'「关系与职位不变」是本节点的总原则',
                          evidence=[o for o, _n in lost]))
    marker_changes = [
        (old, new, rename_semantic_markers(old), rename_semantic_markers(new))
        for old, new in sorted(subs.items())
        if rename_semantic_markers(old) != rename_semantic_markers(new)
    ]
    if marker_changes:
        fs.append(finding(
            'rn_semantic_marker_changed',
            detail=f'这些映射改写了数字/排行/辈分/关系/官职语义标记：{marker_changes[:6]}',
            evidence=[f'{old}->{new}' for old, new, _before, _after in marker_changes]))
    surs = {s for s in (name_map.get('surname_map') or {}) if s}
    touched = [o for o, n in sorted(subs.items())
               if _generic_appellation_was_touched(o, n, subs, surs)]
    if touched:
        fs.append(finding('rn_kinship_touched',
                          detail=f'通用亲属/敬称被当成名字换掉了：{touched[:8]}'
                                 f'——它们是关系不是名字',
                          evidence=touched))

    # ── ⑤ 同族共姓 ───────────────────────────────────────────────
    #    ⚠ 族群表（`_groups`）已被新契约裁掉（加载回来 0 读者）⇒ 上游装载时**现算**：
    #      必需族群一个不能漏；同一个新姓不许被两个原姓共用；额外映射至少得被一个
    #      已改名角色真实使用。最后一条拦的是实测 `太后`/`太傅` 误组后留下的 `太→宋`：
    #      两个角色都明确不改名，而这条幽灵映射此前能让全部 HARD 打绿。
    smap = name_map.get('surname_map') or {}
    missing_surnames = sorted(set(family_members) - set(smap))
    by_new: dict[str, list[str]] = {}
    for old_s, new_s in sorted(smap.items()):
        if new_s:
            by_new.setdefault(new_s, []).append(old_s)
    split = [(new_s, olds) for new_s, olds in sorted(by_new.items()) if len(olds) > 1]
    by_id = {c.get('char_id'): c for c in chars}
    inconsistent: list[str] = []
    for old_s, cids in sorted(family_members.items()):
        new_s = smap.get(old_s)
        if not new_s:
            continue
        for cid in cids:
            c = by_id.get(cid) or {}
            if not c.get('renamed') or not (c.get('new_canonical') or '').startswith(new_s):
                inconsistent.append(f'{old_s}:{cid}')
    orphan: list[str] = []
    for old_s, new_s in sorted(smap.items()):
        used = any(
            c.get('renamed')
            and strip_distinguisher((c.get('old_canonical') or '')).startswith(old_s)
            and (c.get('new_canonical') or '').startswith(new_s)
            for c in chars)
        if not used:
            orphan.append(f'{old_s}→{new_s}')
    if split or missing_surnames or inconsistent or orphan:
        fs.append(finding('rn_surname_split',
                          detail=(f'姓氏族群账不一致：漏填 {missing_surnames[:4]}、'
                                  f'共用新姓 {split[:4]}、成员未按族群改名 {inconsistent[:6]}、'
                                  f'无人使用的映射 {orphan[:4]}——'
                                  f'同族必须共姓，映射也不能脱离角色决定单独存在'),
                          evidence=(missing_surnames + [n for n, _o in split]
                                    + inconsistent + orphan)))

    # ── ⑥ 替换器的后置条件 ───────────────────────────────────────
    #    红了 = **替换器坏了或产物是旧的，不是数据坏了**。
    #    ⚠ 口径是「落在被认领区间之外」，**不是 `.count()==0`**：某个被映射的称呼是
    #      保留称呼的**子串**时（「阿禾」换、「阿禾姊姊」留），保留下来的那段里必然还有它。
    #      用 `.count()` 判，恒等映射下这条会 100% 误报——那本身就是口径错了的信号。
    left = sorted(residual_keys)
    if left:
        fs.append(finding('rn_residual_key',
                          detail=f'替换后这些键还落在被认领的区间之外：{left[:6]}——'
                                 f'单次扫描不该留下它们，检查替换器或产物是不是旧的',
                          evidence=left))
    # 半改不改：某个保留下来的称呼里夹着一个已经被改掉的名字。
    # ⚠ **只查真的会出现在成品里的那些**：带区分词的 canonical（`书童(陆明洲)`）是
    #   registry 为满足键唯一造出来的，**按构造在正文里出现 0 次**——它含着别人的名字
    #   是事实，但那不可能造成半改不改。不加这道，两本各会报一条**恒定的假阳**。
    half = sorted({f'{k}⊃{o}' for k in (name_map.get('kept') or [])
                   if k in pre_target
                   for o in subs if o != k and o in k})
    if half:
        fs.append(finding('rn_kept_contains_mapped',
                          detail=f'这些称呼被保留，但里面夹着已经改掉的名字：{half[:6]}——'
                                 f'成品里会半改不改（「阿禾」成了「阿舒」而「阿禾姊姊」原样留着）。'
                                 f'把它们的新形一并给出，或说明为什么这里该保留',
                          evidence=half))

    # ── ⑦ 组装的前置条件（红了一律拒写 txt）──────────────────────
    if dup or missing:
        fs.append(finding('rn_book_incomplete',
                          detail=(f'第 {dup} 章在两条臂下各有一份成稿；' if dup else '')
                                 + (f'计划有而成稿无的章：{missing}' if missing else '')
                                 + '——成书要求每一章有且只有一份',
                          evidence=[str(o) for o in (list(dup) + list(missing))]))
    if mixed:
        # 判据用**结构特征**不用时间戳：旧契约成稿没有 `budget_chars`。
        fs.append(finding('rn_mixed_contract',
                          detail=f'第 {list(mixed)} 章是旧契约稿（没有 budget_chars），'
                                 f'与其余各章不是同一套契约下写的——拼成一本不是一本连贯的书',
                          evidence=[str(o) for o in mixed]))
    if invalids:
        # 「排除了 N 份废稿」与「一份废稿都没有」必须分得开。
        # ⚠ 某章只有废稿时，它同时表现为缺章，两条一起响——**那是对的**。
        fs.append(finding('rn_invalid_drafts',
                          detail=f'排除了 {len(invalids)} 份被拦下的废稿：{list(invalids)[:6]}'
                                 f'　※ 它们不进成书；若某章只有废稿，上面的缺章那条会一起响',
                          evidence=list(invalids)))

    # ── ⑧ 漏网（本节点唯一能发现「白名单不够」的通道）──────────────
    #    ⚠ 无论有没有命中，**基线都要输出**——一条没有分母的绿灯不携带信息。
    #      残留 0 时基线由 `check()` 的 `scanned` 带出去。
    if residual:
        hi = [r for r in residual if r['confidence'] == 'high']
        bl = '；'.join(f"{st}：{b['total']}→剩{b['residual']}（覆盖{b['ratio']:.0%}）"
                       for st, b in sorted(baseline.items(),
                                           key=lambda kv: -(kv[1]['ratio'] or 0))
                       if b['total'])
        sug = sorted({(r['unit'], r['suggest']) for r in hi if r['unit'] and r['suggest']})
        fs.append(finding(
            'rn_residual_stem',
            detail=f'{len(residual)} 处旧姓字落在替换面之外（其中 {len(hi)} 处高置信）：'
                   + '；'.join(f"{r['where']}「{r['hit']}」…{r['context']}…"
                               for r in residual[:4])
                   + f'　※ 基线 {bl}'
                   + (('　※ 高置信项的建议替换对：'
                       + '、'.join(f'{o}→{n}' for o, n in sug)) if sug else '')
                   + '　※ **覆盖率就是判别器**：≥90% 的字，残留几乎必然是真漏网；'
                     '<50% 的字，残留基本是普通词（「许久」「谈话」这类固有假阳，'
                     '中文没有词边界，消不掉）。'
                     '⚠ 但**漏掉的形就是该姓主用法时，覆盖率低是由构造决定的**'
                     '（实测「沈」40%、「孙」60%，两个都是真漏网）——'
                     '两个信号打架时以 confidence 为准。'
                     '人从上往下读，勾中的走 `--apply-edit` 补进 `extra_subs`',
            evidence=[r['hit'] for r in residual]))

    # ── ⑨ 三条度量：**无条件产出**，不许挂在「没问题」那一支上 ────
    fs.append(finding(
        'rn_stem_noise',
        detail=f'新姓字在原文里的非人名用法次数：{stem_noise or "（本书没有姓氏映射）"}'
               f'　※ 数字大不等于错，只是新名会带来这么多读感噪声'
               f'　※ 取值面是**姓氏映射的值**——原型取「新名的第一个字」，'
               f'实测三本全破（「东街张媒婆→东街薛媒婆」取到「东」12 次，与任何新姓无关）',
        evidence=sorted(stem_noise)))

    tot = sum(sum((v.get('counts') or {}).values())
              for v in (applied.get('chapters') or {}).values())
    tot += sum(((applied.get('hook') or {}).get('counts') or {}).values())
    before = sum(len(t) for w, t in post_units if w not in ('hook', 'title'))
    fs.append(finding(
        'rn_report',
        detail=f'{len(subs)} 条映射，正文+文案共替换 {tot} 处；'
               f'{len(name_map.get("kept") or [])} 条保留；'
               f'{len(smap)} 个姓氏族群；成书正文 {before} 字'
               f'　※ 篇幅变化不再落盘（替换不改篇幅，实测 3/3 都是 1.000×~1.001×）',
        evidence=[str(len(subs)), str(tot)]))

    fs.append(_style_report(post_units, source_text))

    # ── ⑩ 三条查不了的**也进产物**，而这不是重复 ────────────────────────────
    #    它们的 `detail` **逐本不同**（书名那条点名这本书的书名、口味那条举本书真实的
    #    第一个新名），所以它们是**这一份产物的结论**，不是一段可以写死在报告层的模板。
    #    ⚠ 例子取本书真实的新名，**不写死一个字面量**：写死的那个会随着别的书漂成
    #      一个谁也不认识的名字（原型那份报告曾经举着一个只存在于验证用假映射里的名字）。
    #    ⚠ 盘上 `book[]` 恒 **7 条** = 1 info + 3 metric + **这 3 条**，契约就是这么写的。
    eg = next((c.get('new_canonical') for c in chars if c.get('renamed')), None)
    fs.append(finding(
        'rn_taste_unver',
        detail='新名的时代感 / 性别气质 / 雅俗**没有确定性判据**——'
               + (f'「{eg}」贴不贴这本书的时代与题材' if eg else '「这个名字像不像那个年代的人」')
               + '不是结构不变量。本节点这一维的现役闸门是**人定稿**那一步，不是代码',
        evidence=[eg] if eg else []))
    fs.append(finding(
        'rn_coverage_unver',
        detail='「替换面覆盖了正文里所有指称人物的字面」**验不了**：只能替换人物表里'
               '有的称呼，而**人物表漏收的角色这一层结构上够不着**。'
               '`rn_residual_stem` 只是**候选探针，不是覆盖率证明**'
               '——别把它的 0 条读成「一个都没漏」',
        evidence=[]))
    fs.append(finding('rn_title_unver', detail=title_line, evidence=[]))
    return fs


def _style_report(post_units: list[Unit], source_text: str) -> Finding:
    """成书级的写法密度总账 —— **只量不判**，永远输出。

    【它不是逐章那份的重复，差别在文案】验收相逐章判的是 `stage5/chNN.json`，
    而 `book.txt` = **文案 + 各章正文**。文案那一段不属于任何一章，
    全链至今**没有任何探针量过它**。这里按 `where` 把「正文」与「文案」分开报。

    ⚠⚠ **原型这一条的「原作」列永远不输出**：它读 `data['_source_text']`，
    而装载器从来没设过这个键（实际存在的是 `_orig`，而且是 `str` 不是 `dict`）
    ⇒ `if src:` 恒假，那一列恒空**而没有任何东西出声**。F2 修掉。

    ⚠ **文案那一列的密度不稳，因为分母只有几十到两百字**：实测三本新文案各含 1 处
    破折号（原作文案 5/5 都是 0 处），密度显示成语料 p95 的 2~4 倍，而绝对量就是一处。
    **读这一列看绝对处数，不看倍率** ⇒ 这一条永远不给阈值。
    """
    body = '\n'.join(t for w, t in post_units if w not in ('hook', 'title'))
    hook = '\n'.join(t for w, t in post_units if w == 'hook')

    def _row(label: str, txt: str) -> str:
        if not (txt or '').strip():
            return f'{label} —'
        # 绝对处数与密度一起给：文案的分母只有几十字，光看密度会读成大问题
        return (f'{label} 破折号 {len(re.findall(r"—{2,}", txt))} 处'
                f'（{dash_per_1k(txt):.2f}）／短段成串 {short_run_per_1k(txt):.2f}')

    parts = [_row('正文', body), _row('文案', hook), _row('原作', source_text)]
    return finding(
        'rn_style_report',
        detail='成书写法密度（每千内容字）：' + '　｜　'.join(parts)
               + f'　※ 语料 p95：破折号 {DASH_CORPUS_P95:.2f}／'
                 f'短段成串 {SHORT_RUN_CORPUS_P95:.2f}'
                 '　※ 只量不判；逐章判定在全书验收相，**文案这一列全链只有这里量**',
        evidence=[f'{dash_per_1k(body):.2f}', f'{short_run_per_1k(body):.2f}'])


# ─────────────────────────────────────────────────────────────────────────────
# 一份产物 → 34 个 `Outcome`
# ─────────────────────────────────────────────────────────────────────────────

#: 由 `rename_findings` 产的那些（hard/info/metric），**逐条列出不 glob**：
#: 少列一条，那一条就会变成 `skipped`，而 `skipped` 与「跑了没命中」在报告上
#: 是两种颜色——那正是本骨架要保住的区分。
_FROM_FINDINGS = (
    # 映射表自身的结构
    'rn_llm_shape', 'rn_map_coverage', 'rn_new_collide', 'rn_new_collide_human',
    'rn_ambiguous', 'rn_shared_surface', 'rn_shared_surface_human', 'rn_surface_short',
    'rn_already_present', 'rn_new_in_source', 'rn_title_dropped',
    'rn_semantic_marker_changed', 'rn_kinship_touched',
    'rn_surname_split', 'rn_overlap',
    # 替换器的后置条件
    'rn_residual_key', 'rn_kept_contains_mapped',
    # 组装的前置条件
    'rn_book_incomplete', 'rn_mixed_contract', 'rn_invalid_drafts',
    # 漏网
    'rn_residual_stem', 'rn_no_reason',
    # 三条度量
    'rn_stem_noise', 'rn_report', 'rn_style_report',
)


def check(*, findings: list[Finding], scanned: str, baseline_line: str,
          chapter_scanned: str, txt_state: tuple[bool, str], approved: bool,
          stale: list[str], no_consumed: list[str], hook_stale: bool | None,
          identity: dict | None, title_line: str, body_count: int) -> list[Outcome]:
    """一份 `name_map.json` → **34 个 `Outcome`，一条判据一个**。

    ⚠ `scanned` 一律填：**一个不知道自己扫了多少的绿灯没有判别力**。
    两条判据的分母与其余的不同，各自单给：
      · `rn_residual_stem` —— 姓字覆盖率（「本轮 0 命中」说明不了探针在不在工作）；
      · `rn_book_incomplete` —— **章计划读不回来时，它这一趟根本没有分母**。
    """
    by_rule: dict[str, list[Finding]] = {}
    for f in findings:
        by_rule.setdefault(f.rule, []).append(f)

    _special = {'rn_residual_stem': baseline_line, 'rn_book_incomplete': chapter_scanned}
    out: list[Outcome] = []
    for rule in _FROM_FINDINGS:
        out.append(ran(rule, by_rule.get(rule, []),
                       scanned=_special.get(rule, scanned)))

    # ── 交付物的账：它是纯文本、带不了来源账，指纹记在产物里 ────────────────
    ok, detail = txt_state
    out.append(ran('rn_txt_stale',
                   [] if ok else [finding('rn_txt_stale', detail=detail, evidence=[])],
                   scanned='盘上的 book.txt × 产物记的指纹'))

    # ── 恒等基线（`R-10.7`）──────────────────────────────────────────────────
    out.extend(identity_outcomes(identity))

    # ── 七条查不了的：**永远输出**，不是「还没写完」 ────────────────────────
    # ⚠⚠ 这三条的措辞**从产物里取**，不在报告层重写一遍：它们逐本不同
    #    （书名那条点名这本书的书名、口味那条举本书真实的第一个新名）。
    #    在这里另写一份，「产物说的」与「报告说的」就会在某次改动后分家——
    #    而**复检那条路的全部纪律就是「读产物，不重算」**。
    #    取不到时（产物早于这几条）才回落到一句通用的话，并**说出它是回落的**。
    out.append(unverifiable('rn_taste_unver',
                            _stored_why(by_rule, 'rn_taste_unver',
                                        '新名好不好没有确定性判据，现役闸门是人定稿'),
                            scanned='全部新名'))
    out.append(unverifiable('rn_coverage_unver',
                            _stored_why(by_rule, 'rn_coverage_unver',
                                        '替换覆盖率验不了：人物表够不到的角色，'
                                        '这一层结构上够不着'),
                            scanned='人物表里有的那些称呼'))
    out.append(unverifiable('rn_title_unver',
                            _stored_why(by_rule, 'rn_title_unver', title_line),
                            scanned='书名一行'))

    if approved:
        out.append(unverifiable(
            'rn_not_approved', '已定稿（`origin=human`）。⚠ 它仍然是「查不了」而不是绿灯：'
                               '**定稿说明有人看过，不说明新名好**',
            scanned='产物的 `approved` 键'))
    else:
        out.append(unverifiable(
            'rn_not_approved',
            '映射表在盘上但**没有人拍板**——跑 `stage7.approve`（0 成本）。'
            '新名的时代感与雅俗没有确定性判据，人定稿是这一维唯一的闸门',
            scanned='产物的 `approved` 键'))

    out.append(unverifiable('rn_body_stale',
                            _body_stale_why(stale, no_consumed, hook_stale),
                            scanned=f'{body_count} 份成稿 + 文案'))

    out.append(unverifiable(
        'rn_hook_missing',
        '开篇文案的定稿状态由 `hook_consumed.present` 记着；'
        '**文案未定稿时 `stage7.assemble` 会直接拒写 txt**（那道闸在组装那一步，不在这里）。'
        '⚠ 正因为那道闸挡在前面，「成书里没有文案」这一态**结构上不可达**',
        scanned='`hook_consumed` 一格'))
    return out


def identity_outcomes(identity: dict | None) -> list[Outcome]:
    """只判恒等基线。

    `stage7.rename --identity` 必须能在 `name_map.json` 诞生前运行；那时映射相关判据
    没有被执行，不能借一份空映射把它们伪装成绿灯。这个小出口让预检只产出自己
    真正量过的两行，其余注册规则由报告层诚实标为 `skipped`。
    """
    out: list[Outcome] = []
    if identity is None:
        # **「没跑过」要出声。** 不打印这一行，它与「跑过且无损」在报告上同形。
        out.append(ran('rn_identity_baseline',
                       [finding('rn_identity_baseline',
                                detail='恒等基线**从没跑过**——产物里没有 `identity`。'
                                       '它是本层最该先点的一条：替换器在恒等下不逐字节守恒时，'
                                       '后面每一分钱都白花。跑 `--identity`（0 成本）',
                                evidence=[])],
                       scanned='产物里的 `identity` 键'))
    else:
        bad = list(identity.get('mismatches') or [])
        n = identity.get('chapters') or 0
        out.append(ran(
            'rn_identity_baseline',
            [] if (identity.get('roundtrip_ok') and not bad and n) else
            [finding('rn_identity_baseline',
                     detail=(f'恒等基线不守恒：{bad[:6]}' if bad else
                             f'恒等基线量了 {n} 章——**量 0 章的「无损」是一句空话**'),
                     evidence=[str(x) for x in bad] or ['0'])],
            scanned=f'{n} 章 + 文案 + 书名的占位符双射往返'))

    out.append(unverifiable(
        'rn_identity_blind',
        '恒等基线**验不了**这三样：' + '；'.join(IDENTITY_BLIND)
        + '。⚠ 它们由 pytest 的对抗性合成映射覆盖，**不由这条 CLI**——'
          '别把基线跑绿了读成「替换是安全的」',
        scanned=f'{len(IDENTITY_BLIND)} 条登记在案的盲区'))

    return out


def _stored_why(by_rule: dict[str, list[Finding]], rule: str, fallback: str) -> str:
    """常驻 `unver` 的措辞：**优先用产物里存的那一条**。

    ⚠ 回落时要**说出它是回落的**——「这份产物早于这条判据」与
    「这条判据这次没话可说」是两件事，静默回落会把前者伪装成后者。
    """
    got = by_rule.get(rule)
    if got:
        return got[0].detail
    return f'{fallback}　※ ⚠ 这句是**回落文案**：盘上这份产物里没有这一条的结论，' \
           f'它早于这条判据 —— 跑一次 `--recheck`（0 成本）就有了'


def _body_stale_why(stale: list[str], no_consumed: list[str], hook_stale: bool | None) -> str:
    """`rn_body_stale` 的三态措辞。

    ⚠⚠ **口径与原型不同，而这是被迁移逼出来的**（同 stage6 的 `hook_body_stale`）：
    `judged[].sha1` 是**整文件**指纹，而新契约裁过 stage5 产物的字段
    ⇒ 迁移后它**必然**对不上，照原型退回整文件 sha1 会让盘上大多数本误报红。
    ⇒ `consumed`（字段投影）判过期、`sha1` 只作**审计**；
    **没有 `consumed` ⇒ 说不清**，不是 fresh、也不是 stale。
    """
    parts = []
    if stale:
        parts.append(f'{len(stale)} 份成稿在替换之后**变过**：{stale[:4]}'
                     f'——重跑 `--recheck`（0 成本）')
    if no_consumed:
        parts.append(f'{len(no_consumed)} 份成稿没有 `consumed` 投影 ⇒ **说不清**'
                     f'（整文件 `sha1` 只作审计，拿它判过期会让迁移过的产物必然误报）')
    if hook_stale:
        parts.append('`stage6/hook.json` 的**人工定稿文本**在替换之后变过——'
                     '成书里的文案可能是旧的')
    elif hook_stale is None:
        parts.append('产物里没有文案的消费投影 ⇒ 无从判断成书文案是否仍配对')
    if not parts:
        return ('逐份成稿的 `consumed` 投影都对得上、文案定稿文本也没变过。'
                '⚠ 它仍然登记为「查不了」而不是绿灯：整文件 `sha1` 只作审计，'
                '而投影只覆盖本层真正消费的那几个字段')
    return '；'.join(parts)
