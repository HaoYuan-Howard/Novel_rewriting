#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— 九条判据的**实现**。行在 `contracts/judges/stage3.py`（`Q-12`）。

⚠⚠ **本模块吃 `dict` 不吃模型，这是挑过的。**
新契约把 `voice` 八维、`surface_habits`、`hook_style` 全做成了
`Field(min_length=1)` + `extra='forbid'`，于是**一份已经过模型的产物再跑 `required`
必然全绿**——那是判据 31 点名的「保证会亮的绿灯」。
而按本项目自己的分界线（`判据 vs 载体按失败成本划`），`required` **该留在判据侧**：
载体拒收 ⇒ 整份响应作废 ⇒ **再花一次钱**；判据拦下 ⇒ `.INVALID` + 人手改一行 +
`--revalidate` ⇒ **0 成本**。
⇒ 判据跑在**模型之前**的那份 dict 上（付费路的落盘闸门、以及测试里的反例样本），
`--check-only` 复检时喂的是 `model_dump(by_alias=True)`——同一段代码，两个入口。

⚠ 判据 id 一律照抄文档，不加 stage 前缀（它们是与 `wontfix.json` 的键的契约）。
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from contracts.invariants import HOOK_KEYS, VOICE_KEYS
from contracts.schema.report import finding
from contracts.schema.stage3 import Measurable
from novel.core.measure import drift, flatten
from novel.core.text import norm, quotes
from novel.report.findings import Outcome, ran, unverifiable

#: 【不参与文本判据的键】它们不是**模型写的描述**，而是确定性搬进来的元数据。
#:
#: ⚠⚠ **原型这张表漏了 `_provenance`**，于是报告打印「扫过 30 个文本字段」
#: 而被测对象只有 20 个（三本各 10 / 10 / 19 个字符串来自 `_provenance`）。
#: 今天没有假阳（那些值全是 ASCII 指纹／模型名／git sha），但**报出来的基数是错的**
#: （判据 31），而且一旦往 `_provenance` 里写进任何中文值，
#: 它会立刻进入 `name_leak` / `verbatim` 的扫描面。
#:
#: ⚠ 裁掉 `title` / `source_id` 之后这张表**只剩两项**——**排除表越短越好**：
#: 每一项都是「一个不该被当成描述的东西却存在产物里」的记号。
META_KEYS = ('measurable', '_provenance')


def iter_strings(obj: Any, path: str = '') -> Iterator[tuple[str, str]]:
    """遍历产物里**模型写的**字符串值（元数据键见 `META_KEYS`，跳过）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in META_KEYS:
                continue
            yield from iter_strings(v, f'{path}.{k}' if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f'{path}[{i}]')
    elif isinstance(obj, str):
        yield path, obj


# ── ⑤a 的辅助：什么算「具体词例」 ────────────────────────────────────────────

def _is_format_placeholder(q: str) -> bool:
    """纯结构模板不是词例：`X`、`+`、多槽模板，或很短的单内槽语法壳。

    ⚠ 返工的首个失败样本证明「引号内一律禁」**过宽**：`说话者+说：`、`一……便……`
    是**结构模板**，删掉会让产物更差（判据 20）。所以只豁免明确标出可替换插槽的模板。
    """
    s = q or ''
    n = norm(s)
    if n and re.fullmatch(r'[Xx]+', n):
        return True
    if '+' in s:
        return True
    slots = list(re.finditer(r'(?:…{2,}|\.{3,}|[Xx]+)', s))
    if len(slots) >= 2:
        return True
    if len(slots) == 1:
        slot = slots[0]
        fixed = norm(s[:slot.start()] + s[slot.end():])
        # 「可不是……么」这类短语法壳只有一个内槽，但没有可独立照抄的长句干；
        # 「我又沾了……的福」固定部分更长、已携带本书措辞，**仍阻断**。
        return slot.start() > 0 and slot.end() < len(s) and len(fixed) <= 4
    return False


def habit_literals(data: dict) -> list[str]:
    """`surface_habits` 里【引号内的具体词例】；**按字段形状判，不靠长度/逐书词表**。

    这一条单独拎出来阻断，是因为它的下游后果和别处不同：写作侧把 `surface_habits`
    当**写作习惯清单**读，里面举的原句会被当成这本书的口头禅整句复用
    （实测一稿内「我气笑了」「我心里咯噔一下」「空气安静了一瞬」各复用 3~6 次，像复读机）。

    ⚠ 旧判据要求「命中原文且 ≥4 字」，于是两本真机产物里的「福气」「好日子」与
    「看吧」「谁怕谁」**全部从 2~3 字门槛下钻过去，自动报告却 PASS**。
    这里的契约本来就比「是不是原文」更严格：**只许写类别，不许列任何具体词。**
    """
    return sorted({q for h in (data.get('surface_habits') or []) if isinstance(h, str)
                   for q in quotes(h)
                   if norm(q) and not _is_format_placeholder(q)})


# ── 九条判据 ────────────────────────────────────────────────────────────────

def check(data: dict, *, corpus: str, names: frozenset[str] | None = None,
          spoiler_names: frozenset[str] = frozenset(),
          measured_now: Measurable | None = None) -> list[Outcome]:
    """契约 + 越界 + 自洽。返回**每条判据一个 `Outcome`**（没排上的不给，那就是 `skipped`）。

    `measured_now` 给 `None` ⇒ **不排** `measurable_drift`——落盘那一刻它必然相等
    （`measurable` 刚由代码算出来），而**一条永远不会红的检查不该打 `[OK]`**（判据 31）。
    ⚠ 它随后会在报告里变成一行 `skipped`，而 `measurable_drift` 是 HARD ⇒ 徽章 `fail`。
    那是对的：**你确实没跑那道闸，就不该拿到一个绿灯。** `--check-only` 恒会算它。
    """
    out: list[Outcome] = []
    fields = list(iter_strings(data))
    n_fields = len(fields)

    # ① 必填齐全（阻断）
    voice = data.get('voice') or {}
    req: list[str] = []
    miss = [k for k in VOICE_KEYS if not str(voice.get(k) or '').strip()]
    if miss:
        req.append(f'voice 维度缺失/为空: {miss}')
    extra = [k for k in voice if k not in VOICE_KEYS]
    if extra:
        req.append(f'voice 出现契约外字段: {extra}')
    if not (data.get('surface_habits') or []):
        req.append('surface_habits 为空（这本书一个写作小动作都没有？多半是没抽到）')
    hook = data.get('hook_style')
    if not isinstance(hook, dict):
        # `R-1.12` 已把无文案段的书挡在 stage1 ⇒ 本层**必然**有 hook_style。
        req.append('hook_style 缺失——而 `R-1.12` 保证走到本层的书都有文案段')
    else:
        hmiss = [k for k in HOOK_KEYS if not str(hook.get(k) or '').strip()]
        if hmiss:
            req.append(f'hook_style 字段缺失/为空: {hmiss}')
    n_voice = len([k for k in VOICE_KEYS if str(voice.get(k) or '').strip()])
    out.append(ran(
        'required', [finding('required', detail=d, evidence=[d]) for d in req],
        # 判据 31：绿灯要报出自己的分母，否则「查过干净」与「根本没在查」分不开。
        scanned=(f'voice {n_voice}/{len(VOICE_KEYS)} 维 · '
                 f'surface_habits {len(data.get("surface_habits") or [])} 条 · '
                 f'hook_style {"有" if isinstance(hook, dict) else "null"}')))

    # ③ 越界：剧情/结构引用。**只报第一处**——同一份产物里这类引用往往成串，
    #    报一处足以让人去看，报二十处会把报告淹掉。
    struct = next(((path, m.group()) for path, v in fields
                   for m in [re.search(r'b\d{2}_\d{2}|sec_\d{4}|第\s*\d+\s*章', v)] if m), None)
    out.append(ran('struct_ref', [finding(
        'struct_ref',
        detail=f'{struct[0]} 出现结构/章节引用「{struct[1]}」——在复述情节而非描述写法',
        evidence=[struct[0], struct[1]])] if struct else [], scanned=f'{n_fields} 个文本字段'))

    # ④ 疑似摘抄原句（阻断）。标点/空白全归一后比对。
    verbatim = []
    for path, v in fields:
        s = norm(v)
        hit = next((s[i:i + 20] for i in range(len(s) - 19) if s[i:i + 20] in corpus), None)
        if hit:
            verbatim.append(finding(
                'verbatim', detail=f'{path} 含 ≥20 字原文连续片段「{hit}」——摘抄原句',
                evidence=[path, hit]))
    out.append(ran('verbatim', verbatim,
                   scanned=f'{n_fields} 个文本字段 × {len(corpus)} 字归一语料'))

    # ⑤a surface_habits 举具体词例（阻断）
    hl = habit_literals(data)
    out.append(ran('habit_quote', [finding(
        'habit_quote',
        detail=f'surface_habits 在引号内列了 {len(hl)} 处具体词例「{"、".join(hl[:5])}」'
               f'——不论是否命中原文、也不论长短，都只写类别 + 位置 + 效果；'
               f'带 X/+、多插槽或短单内槽的纯结构模板除外',
        evidence=['surface_habits', *hl])] if hl else [],
        scanned=f'surface_habits {len(data.get("surface_habits") or [])} 条'))

    # ⑤b 其余位置的原文微引用（举例性质）：不阻断，但**必须可见**——闭卷写作节点的样例泄漏面
    micro = [q for path, v in fields if not path.startswith('surface_habits')
             for q in quotes(v) if len(norm(q)) >= 6 and norm(q) in corpus]
    out.append(ran('micro_quote', [finding(
        'micro_quote',
        detail=f'含原文微引用 {len(micro)} 处（举例性质，非阻断；闭卷试写后复核是否被整句复制）: '
               + '、'.join(micro[:4]),
        evidence=sorted(set(micro)))] if micro else [],
        scanned=f'{n_fields} 个文本字段（surface_habits 除外，它归 habit_quote）'))

    # ⑥ 实测数字被抄进描述文本（阻断：数字只该有 measurable 一个落点，否则重算即漂移）
    dup = [(path, m.group()) for path, v in fields
           for m in [re.search(r'\d+(?:\.\d+)?\s*(?:字符|字|%|％)', v)] if m]
    out.append(ran('number_dup', [finding(
        'number_dup',
        detail='描述文本里复述了实测数字（应只留形态词，数字另有落点）: '
               + str([f'{p}「{g}」' for p, g in dup[:4]]),
        evidence=[f'{p}|{g}' for p, g in dup])] if dup else [],
        scanned=f'{n_fields} 个文本字段'))

    # ⑦ 专名泄漏（可迁移性）。**三态**，见下。
    out.append(_name_leak(fields, names, spoiler_names, n_fields))

    # ⑨ 内部节点名泄漏
    joined = '\n'.join(v for _, v in fields)
    leak = re.search(r'\bN[1-9]\b|gate\s*\d?|blueprint|stage\s*[1-9]|QC', joined, re.I)
    out.append(ran('node_leak', [finding(
        'node_leak', detail=f'产物出现内部工作流名「{leak.group()}」',
        evidence=[leak.group()])] if leak else [], scanned=f'{n_fields} 个文本字段'))

    # ★ measurable 漂移（**只在复检时**；理由见 `contracts/judges/stage3.py`）
    if measured_now is not None:
        out.append(_measurable_drift(data, measured_now))
    return out


def _name_leak(fields: list[tuple[str, str]], names: frozenset[str] | None,
               spoiler_names: frozenset[str], n_fields: int) -> Outcome:
    """⚠ **三态，不是两态**（`stage3.md` §六 设计要点①）。

    没有名表 ⇒ `unver`（这条规则**本次没有跑**）；
    **名表过滤后为空也是 `unver`**——拿一张空表去扫，0 命中是必然的，
    那是「保证会亮的绿灯」（判据 31）。只有拿到非空名表扫过，才配打 `[OK]`。
    """
    if names is None:
        return unverifiable('name_leak',
                            '缺 stage2/global_grounding.json（还没跑 stage2.global），'
                            '没有人物名表可比对——这条规则本次【没有跑】，不是跑过没事')
    if not names:
        return unverifiable('name_leak',
                            'stage2 的人物名表过滤后是空的（全部角色 name_distinctiveness=common，'
                            '或叫法全是亲属称谓）——拿空表去扫，0 命中是必然的，不构成结论')
    scanned = f'拿 {len(names)} 个专名扫过 {n_fields} 个文本字段'
    hit = sorted({n for _p, v in fields for n in names if n in v})
    if not hit:
        return ran('name_leak', [], scanned=scanned)
    # 同时是未揭示秘密主体的那些，**单独点出来**——两种泄漏的严重程度差得很远，
    # 而报告只说「出现人物专名 ['系统']」时，人看不出该不该立刻重跑。
    spoil = sorted(set(hit) & set(spoiler_names or ()))
    return ran('name_leak', [finding(
        'name_leak',
        detail=f'出现人物专名 {hit[:6]}——描述应可迁移到新故事，改用角色功能（主角/施害方/旁观者）'
               + (f'。⚠ 其中 {spoil} **同时是读者尚未知情的秘密主体**：'
                  f'这份产物会整份进写手 prompt，等于把底提前告诉写第 1 章的写手' if spoil else ''),
        evidence=hit)], scanned=scanned)


def _measurable_drift(data: dict, measured_now: Measurable) -> Outcome:
    """存的 `measurable` vs 照盘上 stage1 现算的，**取并集逐项比**。"""
    stored_raw = data.get('measurable') or {}
    stored = Measurable.model_validate(stored_raw) if stored_raw else None
    fs = []
    for scope in ('body', 'hook'):
        s = getattr(stored, scope, None)
        n = getattr(measured_now, scope, None)
        if (s is None) != (n is None):
            fs.append(finding('measurable_drift',
                              detail=f'measurable.{scope}: 产物里{"有" if s else "无"}、'
                                     f'照现在的 stage1 重算{"有" if n else "无"}',
                              evidence=[f'measurable.{scope}', 'presence']))
            continue
        diff = drift(s, n) if s is not None else []
        if diff:
            fs.append(finding(
                'measurable_drift',
                detail=f'measurable.{scope} 有 {len(diff)} 项与照盘上 stage1 重算的结果不一致（'
                       + '；'.join(f'{k} {a}→{b}' for k, a, b in diff[:6])
                       + f'{"…" if len(diff) > 6 else ""}）——下游 stage5 的段落目标正拿着这份'
                         '旧数字在算（F-054）。跑 `--remeasure` 重算写回（0 成本）',
                evidence=[f'measurable.{scope}'] + [k for k, _a, _b in diff]))
    return ran('measurable_drift', fs, scanned=' · '.join(
        f'{scope} {len(flatten(getattr(measured_now, scope, None)))} 项'
        for scope in ('body', 'hook')))
