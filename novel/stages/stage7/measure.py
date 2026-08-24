#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— 上游装载 / 投影 / 展开 / 纯读数。**判定一律不在这里。**

唯一的例外是 `build_subs`：它把「哪些进了 `subs`」与「哪些是违规」放在**同一次遍历**里
产出，而那是有理由的——分开写就会长出第二把尺子（一把决定换不换，一把判该不该换），
两把尺子必然在某次改动后分家。它交出的是 `(rule, detail, evidence)` 三元组，
翻成 `Finding` 的那一步在 `checks.py`。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from contracts.invariants import MIN_SURFACE
from contracts.schema.stage7 import HookConsumed
from novel.core.naming import strip_distinguisher
from novel.core.text import norm
from novel.stages.stage5.measure import draft_consumed_of, draft_projection, load_drafts
from novel.stages.stage7.subs import apply_subs, sub_table, unclaimed_residual_keys

__all__ = ['draft_consumed_of', 'draft_projection', 'load_drafts']  # 下游按本模块的名字取

#: `build_subs` / `apply_everything` 交出来的一条待翻译的问题。
Problem = tuple[str, str, list[str]]

#: 一个替换面单位：`(where, text)`。`where` ∈ `ch<N>` / `hook` / `title`。
Unit = tuple[str, str]


# ── `consumed` 投影：本层**真正读到**上游的哪一部分 ──────────────────────────
#
# ⚠ **投影与装载器都住在产地**（`novel/stages/stage5/measure.py`）：成稿是 stage5 的
#   产物，本层与 stage6 都只是下游读者。F4 之前这三样在两层里各有一份**逐字相同**的
#   拷贝——同一件事的第二个落点，而它们只要有一次不同步，
#   「本层读到的上游变了没有」就会在两层给出不同的答案。


def hook_consumed_of(rec: dict | None) -> HookConsumed:
    """stage7 对 `stage6/hook.json` 的真实消费面：**文件在不在 + 人工定稿文本**。

    ⚠ 候选、判据、来源账那些元数据变化**不要求重做替换**——拼进成书的只有定稿那一段。
    """
    from novel import prov
    text = (((rec or {}).get('approved') or {}).get('text') or '')
    return HookConsumed(present=bool(rec), approved_text_sha1=prov.fingerprint(text))


# ── 成稿：走 `read_judged`，绝不走会记 `inputs` 的那条 ──────────────────────

def collect_chapters(by_ord: dict[int, dict], plan: dict,
                     invalids: list[str]
                     ) -> tuple[list[dict], list[int], list[str]]:
    """→ ``(canonical recs 按章排好, missing, invalids)``。

    ⚠⚠ **残缺态一律翻成判据，这里【一个 `sys.exit` 都不许有】。**
    本函数会被复检经 HTTP 工作线程调用，而 `SystemExit` 是 `BaseException`——
    anyio 会把它重新抛进事件循环那个 task，**一次「这本书还没写完」就杀掉整个服务**。
    **这条是付过一次代价的。** 判死在 `assemble.run_assemble`，那里是 CLI 的调用点。

    ⚠ 映射表的产出与界面预览**不受这些约束**——人正要靠界面看清缺的是哪几章。
    受约束的只有 txt：那是交付物，而 txt 里没有地方写「缺章」抬头横幅。
    """
    # canonical 路径按章结构唯一；加载器也会拒绝文件名与 ordinal 不一致，
    # 下游只接收这一个明确投影。
    recs = [rec for _o, rec in sorted(by_ord.items(), key=lambda kv: kv[0] or 0)]
    want = sorted(c['output_ordinal'] for c in plan['output_chapters'])
    missing = [o for o in want if o not in by_ord]
    return recs, missing, sorted(invalids)


# ── 人工编辑面：按 `char_id` 浅合并 ─────────────────────────────────────────

#: 可编辑的五个键 + 定位用的 `char_id`。
#: ⚠⚠ **`old_canonical` 刻意不在里面**：它由 registry 派生，人改它等于改一份
#: 上游的投影而上游一个字没动。写了就是显式终态，不静默丢弃（`R-10.15`）。
_CHARACTER_PATCH_KEYS = frozenset({
    'char_id', 'renamed', 'new_canonical', 'not_renamed_reason', 'aliases', 'reason',
})


def merge_mapping_edit(rec: dict | None, edit: dict) -> tuple[dict, list[dict]]:
    """人工编辑文件 → `(model_out, extra_subs)`。

    ⭐ **`character_patches` 是全链唯一把「定向返工只许改判据点名的部分」（`R-4.10`）
    做成数据结构的地方**——别处都只是 prompt 里的一句叮嘱。不在 patch 里的角色
    **在结构上改不到**。

    它的由来值得记：旧接口只有 `characters` 完整决策集，实测 48 人的映射表里
    唯一那条 HARD 只涉及一个角色的一条重复 alias，却要人复制其余 47 人的决定
    ——**既易误覆盖，也让「0 成本人工出路」名存实亡**。
    """
    from novel.errors import TerminalState

    if not isinstance(edit, dict):
        raise TerminalState('编辑文件顶层必须是一个 JSON object')
    if 'characters' in edit:
        characters = [dict(c) for c in (edit.get('characters') or [])]
    elif rec:
        characters = [dict(c) for c in (rec.get('characters') or [])]
    else:
        characters = []
    if not characters:
        raise TerminalState(
            '盘上没有旧映射时，编辑文件必须给出非空的 `characters` 完整决策集'
            '——patch 是「在已有决定上改一两个」，没有已有决定就无从 patch')

    by_id: dict[str, int] = {}
    for i, c in enumerate(characters):
        cid = c.get('char_id')
        if not cid or cid in by_id:
            raise TerminalState(f'`characters` 里 char_id 缺失或重复：{cid!r}')
        by_id[cid] = i

    seen: set[str] = set()
    for p in (edit.get('character_patches') or []):
        if not isinstance(p, dict):
            raise TerminalState('`character_patches` 每一项都必须是 object')
        cid = p.get('char_id')
        if cid in seen:
            raise TerminalState(f'`character_patches` 重复修订 {cid!r}')
        if cid not in by_id:
            raise TerminalState(f'`character_patches` 指向未知 char_id：{cid!r}')
        unknown = sorted(set(p) - _CHARACTER_PATCH_KEYS)
        if unknown:
            raise TerminalState(
                f'{cid} 的 patch 含不可编辑字段 {unknown}——'
                f'`old_canonical` 由 registry 派生，改它等于改一份上游的投影'
                f'而上游一个字没动')
        seen.add(cid)
        i = by_id[cid]
        characters[i] = {**characters[i], **{k: v for k, v in p.items() if k != 'char_id'}}

    surname_map = (edit.get('surname_map') if 'surname_map' in edit
                   else (rec or {}).get('surname_map')) or {}
    extra_subs = (edit.get('extra_subs') if 'extra_subs' in edit
                  else (rec or {}).get('extra_subs')) or []
    return {'surname_map': surname_map, 'characters': characters}, list(extra_subs)


# ── 展开：判定与展开在同一次遍历 ───────────────────────────────────────────

def build_subs(characters: list[dict], extra_subs: list[dict] | tuple = (),
               owners: dict[str, frozenset[str]] | None = None,
               universe: set[str] | frozenset[str] = frozenset()
               ) -> tuple[dict[str, str], set[str], list[Problem]]:
    """模型的决定 + 人工补充 → `(subs, kept, problems)`。

    · `subs` = 真要换的 `{old: new}`；
    · `kept` = **明确保留**的称呼集合——它们不改字节，但**必须进 alternation**
      （事实② 第一道防线，见 `subs.sub_table`）；
    · `problems` = `[(rule, detail, evidence)]`，由 `checks.rename_findings` 翻成 `Finding`。

    **判定与展开在同一处发生**：「哪些进了 subs」与「哪些是违规」是同一次遍历的两个输出。

    ⚠ `owners` 抓「这个称呼同时挂着好几个人」；`universe` 是下发面上的全部称呼，
    用来判**模型有没有漏报**。
    """
    problems: list[Problem] = []
    # old -> {new: [来源标签]}；用它同时抓「一 old 两 new」与「谁贡献的」
    fwd: defaultdict[str, defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list))
    new_of_char: dict[str, str] = {}
    kept: set[str] = set()
    spoken: set[str] = set()

    for c in (characters or []):
        cid = c.get('char_id')
        raw_old = (c.get('old_canonical') or '').strip()
        stripped = strip_distinguisher(raw_old)
        alias_pairs = [((a.get('old') or '').strip(), (a.get('new') or '').strip())
                       for a in (c.get('aliases') or [])]
        # canonical 的去向由 `new_canonical` 那一栏表态，不必再在 aliases 里列一遍
        spoken |= {o for o, _ in alias_pairs if o} | {x for x in (raw_old, stripped) if x}
        if not c.get('renamed'):
            # 不改名的角色：它的每一个称呼都是**明确保留**，全部进屏蔽面
            kept |= {o for o, _ in alias_pairs if o} | {x for x in (raw_old, stripped) if x}
            continue
        new_of_char[cid] = (c.get('new_canonical') or '').strip()
        pairs = [(raw_old, new_of_char[cid])]
        # 带区分词的 canonical：剥后那一形也要能换（长的先换，同 `sub_table` 的理由）
        if stripped and stripped != raw_old:
            pairs.append((stripped, new_of_char[cid]))
        pairs += alias_pairs
        for old, new in pairs:
            if not old:
                continue
            if not new or old == new:
                kept.add(old)            # old==new 就是「这条不换」，**是决定不是错误**
                continue
            fwd[old][new].append(cid)

    human_declared: set[str] = set()     # 人明写在 `extra_subs` 里的替换面
    for e in (extra_subs or ()):
        old, new = (e.get('old') or '').strip(), (e.get('new') or '').strip()
        if old and new and old != new:
            fwd[old][new].append(e.get('origin') or 'human')
            spoken.add(old)
            human_declared.add(old)

    # 【漏报 = HARD，不是静默保留】模型少列一个称呼，与它明说「这个不改」
    # 在产物里长得一模一样，而前者是漏、后者是决定。两者必须分得开。
    unspoken = sorted(set(universe or ()) - spoken - set(fwd))
    if unspoken:
        problems.append(('rn_map_coverage',
                         f'下发了但映射表里没有去向的称呼 {unspoken[:8]}'
                         f'（共 {len(unspoken)} 条）——「漏了」与「决定不改」必须分得开，'
                         f'这里按【保留】处理但不静默', unspoken))
        kept |= set(unspoken)

    # 新名撞车：两个**不同角色**换成同一个名字。
    # ⚠ 取值面**只有模型写的 `new_canonical`**——人手补的 `extra_subs` 不在里面，
    #   那是有意的（见下面 `rn_new_collide_human`）。
    by_new: defaultdict[str, set[str]] = defaultdict(set)
    for cid, n in new_of_char.items():
        if n:
            by_new[n].add(cid)
    for n, cids in sorted(by_new.items()):
        if len(cids) > 1:
            problems.append(('rn_new_collide',
                             f'{sorted(cids)} 都被换成了「{n}」', [n]))

    # ⭐ **人手补的映射撞上别人的新名**（F2 新增，`stage7.md` 事实⑤）。
    # 上面那条 HARD 结构上够不到这扇门，而 `extra_subs` 恰恰是**唯一一条人手打新名、
    # 且没有模型全局视野**的通道。盘上（書1）`祈颜→叶翎`（模型）与 `祈辕→叶翎`（人工）
    # 同时在 `subs` 里，而那条 HARD 打的是 `[OK]`。
    # ⚠ **不扩 HARD 的取值面**：那一次撞名**恰好是对的**（原作对同一个人有两种写法），
    #   硬拦它的唯一出路是撤销一个对的决定。⇒ 照 `rn_shared_surface_human` 的先例报一条提示。
    for old in sorted(human_declared):
        for new in sorted(fwd.get(old) or ()):
            others = sorted(cid for cid, n in new_of_char.items() if n == new)
            if others:
                problems.append((
                    'rn_new_collide_human',
                    f'人手补的「{old}→{new}」撞上了 {others} 的新名——'
                    f'按人的裁决照样执行（原作对同一个人有两种写法时这是对的），'
                    f'但它会让这几个字面在成品里指向同一个名字', [old, new]))

    # 人工补充之间的跨界重叠：`a` 的真后缀 == `b` 的真前缀时，单次扫描会切出半截残串。
    # ⚠ registry 来的键不会有这个形状（都是完整称呼），所以**只查人工补充那一侧**。
    ek = sorted({(e.get('old') or '').strip() for e in (extra_subs or ()) if e.get('old')})
    for a in ek:
        for b in fwd:
            if a == b:
                continue
            for k in range(1, min(len(a), len(b))):
                if a[-k:] == b[:k] and a + b[k:] != a:
                    problems.append(('rn_overlap',
                                     f'人工补充的「{a}」与「{b}」首尾重叠 {a[-k:]!r}——'
                                     f'单次扫描会切出半截残串', [a, b]))
                    break

    subs: dict[str, str] = {}
    for old, news in fwd.items():
        if len(news) > 1:
            # **不猜。** 同一个字面要被换成两个不同的名字，哪个对无从判断。
            problems.append(('rn_ambiguous',
                             f'「{old}」被要求换成 {sorted(news)} 两个不同的名字'
                             f'（来自 {sorted({s for v in news.values() for s in v})}）',
                             [old]))
            kept.add(old)
            continue
        new = next(iter(news))
        if len(old) < MIN_SURFACE:
            # 单字进替换面**必然**误伤（中文没有词边界）。实测「姐」是某角色的**唯一**
            # 称呼，而它单主、多主那条抓不到——只剩一道词表防线，那不够。
            problems.append(('rn_surface_short',
                             f'「{old}」只有 {len(old)} 个字，进替换面必然误伤别的词'
                             f'（中文没有词边界）——这个角色只能靠更长的称呼来换', [old]))
            kept.add(old)
            continue
        if owners and len(owners.get(norm(old)) or ()) > 1:
            others = sorted(owners[norm(old)])
            if old in human_declared:
                problems.append(('rn_shared_surface_human',
                                 f'「{old}」同时挂着 {others}，按**人工裁决**照样替换'
                                 f'（这类书里多主是情节本身：顶名/替身/女扮男装）'
                                 f'——它会把这几个角色的这个叫法一起换成「{new}」', [old]))
            else:
                problems.append(('rn_shared_surface',
                                 f'「{old}」在人物表里同时挂着 {others}'
                                 f'——换掉它会一起改到别人。'
                                 f'确属顶名/替身/女扮男装（原文里本来就该是同一个名字）时，'
                                 f'把它写进 `extra_subs` 走 `--apply-edit`，'
                                 f'即按人的裁决执行', [old]))
                kept.add(old)
                continue
        subs[old] = new
    kept -= set(subs)                    # 同一个字面既被换又被留时，**换赢**（上面已各自报过）
    return subs, kept, problems


def apply_everything(recs: list[dict], hook_old: str, title: str,
                     subs: dict[str, str], kept: set[str] | frozenset[str]
                     ) -> tuple[dict, list[Unit], list[Unit], str, str, set[str]]:
    """把一份 `subs` 应用到**全部替换面**
    → `(applied, pre_units, post_units, title_new, hook_new, residual_keys)`。

    **替换面只有三处：正文、文案、书名。** 中间产物一律不动（`R-10.1`：只读上游、绝不回写）。
    `pre_units` / `post_units` 给残留探针用——分母量在 pre、残留量在 post。

    `residual_keys` = 落在**被认领区间之外**的映射键。
    ⚠⚠ 口径**不是 `.count() == 0`**：某个被映射的称呼是**保留称呼的子串**时
    （「阿禾」换、「阿禾姊姊」留），保留下来的那段里必然还有它，那**不是残留**。
    用 `.count()` 判，恒等映射下这条会 **100% 误报**——那本身就是口径错了的信号。
    """
    pat, _table = sub_table(subs, kept)
    applied: dict[str, Any] = {'chapters': {}, 'hook': {'counts': {}}}
    pre_units: list[Unit] = []
    post_units: list[Unit] = []
    residual_keys: set[str] = set()

    def _one(where: str, before: str) -> tuple[str, Counter]:
        after, cnt, claimed = apply_subs(before, subs, pat, kept)
        pre_units.append((where, before))
        post_units.append((where, after))
        residual_keys.update(unclaimed_residual_keys(after, subs, claimed))
        return after, cnt

    for r in recs:
        o = r.get('output_ordinal')
        _after, cnt = _one(f'ch{o}', r.get('text') or '')
        applied['chapters'][str(o)] = {'counts': dict(cnt)}
    hook_new, hcnt = _one('hook', hook_old) if hook_old else ('', Counter())
    applied['hook'] = {'counts': dict(hcnt)}
    # ⚠ 书名那一块**不落盘**（`applied.title` 已裁）：它的唯一读者是一条 metric，
    #   而 `title_new` 现算 0 成本、盘上 3/3 与原书名逐字节相同。
    title_new, _tcnt = _one('title', title)
    return applied, pre_units, post_units, title_new, hook_new, residual_keys
