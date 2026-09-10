#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context.py —— `writer_context.json` 的**确定性组装**。

【契约】本模块按显式引用组装写手任务，与 Stage5 从正文计算的机械基线分工。
事实、舞台和材料由 Blueprint 提供；人物只沿本章事实、舞台、叙述或目标引用下发，
叫法仍受源文时点约束。必要知识从独立 KnowledgeLinks 的 typed 决定编译到当前场景。
世界内成立、人物知情与读者披露分别计时；埋点只下发表现，不附秘密答案。

【不能退回的旧做法】逐章比较首次揭示章不足以支持场景重排；setting 的字面子串
过滤曾漏过改写式剧透，不能当作语义通过。本层只核验明确关联及引用，不替 Commander
判断证据是否支持知识。缺失、过期或未决关联由入口拒绝，不能猜一个揭示场景。

【本模块吃 `dict` 不吃模型】理由见 `novel/stages/stage4/__init__.py` ③。
【本模块不 `print`、不 `sys.exit`】可观察信息进入返回账，由节点写入校验报告；
显式终态一律抛 `RefusedByStructure`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel.core.naming import (
    display_names_at, name_collisions, person_naming_surfaces,
    project_character_names, rule_names_a_person, surface_first_chapter,
)
from novel.core.evidence import without_global_evidence
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import beat_index, material_tasks
from novel.stages.stage4.measure import semantic_contract_of

#: `char_id` 的保留形。**按形状分流**：`char_\d+` 解析不出名字就不写，
#: 其余（`narration` / `public` / 「在场士兵」这类无名群体）是自由文本，原样留。
_CHAR_ID = re.compile(r'char_\d+')

@dataclass
class Assembly:
    """组装的**副产物**：那些「必须让人看见、但不该静默改变产物」的账。

    例如「这一章剔了 3 个在场者」与「这一章本来就没人」不能在落盘后同形，
    因此全部收进本结构，由节点交给校验报告。
    """

    ctx: dict[str, Any] = field(default_factory=dict)
    #: `canonical` 是自造标签、已被换成有据叫法的那些（`{原标签: 换成的}`）
    subs: dict[str, str] = field(default_factory=dict)
    #: 书级表上就撞名的（`[(名字, [char_id…], [canonical…])]`）
    collisions: list[tuple[str, list[str], list[Any]]] = field(default_factory=list)
    #: **章级表新增**的撞名（书级表上不存在）
    chapter_collisions: list[tuple[Any, str, list[str]]] = field(default_factory=list)
    #: 本章还叫不出名字、被剔出在场者的（`[(输出章, char_id)]`）
    withheld: list[tuple[Any, str]] = field(default_factory=list)
    #: 未知 kind 被丢弃的 hinge（`[(输出章, kind, 片段)]`）
    dropped_hinges: list[tuple[Any, Any, str]] = field(default_factory=list)
    #: 规则暂不下发（`{(输出章, rule_id, 命中的叫法)}`）
    held_rules: set[tuple[Any, Any, tuple[str, ...]]] = field(default_factory=set)
    #: 当前章首尚未开放的设定，None 不伪装为空字符串。
    withheld_setting: list[tuple[int, str]] = field(default_factory=list)


# ── 一、名字：投影算法唯一落点在 `novel.core.naming` ─────────────────────


def rename_all(obj: Any, subs: dict[str, str]) -> Any:
    """把【整份下发内容】里的自造标签换成有据称呼（确定性替换，**长的先换**）。

    只修结构化的那几处（`display_name` / `address` / `present`）是不够的：自造标签还会从
    **任何一段 LLM 写的散文**里漏出去——实测三处、分属两层：
    stage2 的 `event`（「苏父蹙眉说…」）与 `role`（「赵家小厮兼车夫」）·
    计划的 `chapter_goal` / `end_hook_goal`。
    **按字段名逐个去堵必然漏**（这已经是第二次了，上次是剧透的五条旁路）——
    标签是**信息的属性**，它会从任何承载它的字段漏出去。所以在这里对整棵树做一遍。

    ⚠ **这样做是安全的，而且是由构造保证的**：替换的键恰好是那些【在原文里出现 0 次】的串
    （有据的 canonical 根本不会进 `subs`），所以在任何位置替换都**不可能破坏一个原文里真有的词**。
    ⚠ **只换值不换键**：dict 的键是 `char_id` / `secret_id` 这类内部引用，动了就断链。
    ⚠ **长的先换**：`赵家小厮(车夫)` 必须排在 `赵家小厮` 之前，否则会换出半截残串。
    """
    if not subs:
        return obj
    if isinstance(obj, dict):
        return {k: rename_all(v, subs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rename_all(v, subs) for v in obj]
    if isinstance(obj, str):
        for form in sorted(subs, key=len, reverse=True):
            obj = obj.replace(form, subs[form])
    return obj


# ── 二、逐章投影：六道闸门 ────────────────────────────────────────────────

def _name_narration(seg: dict[str, Any], disp: dict[str, str], idx: int, total: int) -> dict[str, Any]:
    """把一段叙述视角整理成写作侧能直接用的形式。**两处刻意的改动**：

    · `char_id` → 人物本名。写作侧不该看见 `char_002` 这种内部编号（每一层只说自己的语言）。
    · **段落边界描述 → 位置标签。** stage2 的 `segment` 是给人和工具看的界标，
      里面**常原样引用原文**当分界（「正文主体（直至"……沉重的呼吸声"）」）——
      那对闭卷写手就是原文泄漏，**而且泄的往往正是本章的末句**。
      写手真正需要的只是「本章分几段、各段谁在说、什么顺序」。

    ⚠ `disp` 是**章级**表。叫不出名字就**不写这个键**，不兜底成 `char_id`——
    第一人称书的第 1 章正是这种情况（主角原文通篇写「我」，名字要到后面才出现），
    而写手要的东西 `grammatical_person` + `pronoun_surfaces` 已经给全了：
    **「用第一人称、自称『我』」本来就不需要知道他叫什么。**
    """
    out: dict[str, Any] = {
        'segment': '全章' if total <= 1 else f'第{idx + 1}段（共{total}段，按顺序写）',
        'grammatical_person': seg.get('grammatical_person'),
    }
    if disp.get(seg.get('narrator_char_id')):
        out['narrator'] = disp[seg['narrator_char_id']]
    if (seg.get('focalizer_char_id') != seg.get('narrator_char_id')
            and disp.get(seg.get('focalizer_char_id'))):
        out['focalizer'] = disp[seg['focalizer_char_id']]
    if seg.get('pronoun_surfaces'):
        out['pronoun_surfaces'] = seg['pronoun_surfaces']
    return out


def _resolve_scenes(
    out_ch: dict[str, Any], bidx: dict[str, dict[str, Any]],
    atoms: dict[str, dict[str, Any]], disp: dict[str, str],
) -> tuple[list[dict[str, Any]], list[tuple[Any, str]], list[str]]:
    """把 typed 场景引用编译成人话；writer context 不保留任何内部 id。"""
    materials = {str(hinge.get('hinge_id')): hinge for beat in bidx.values()
                 for hinge in (beat.get('hinges') or [])}
    scenes: list[dict[str, Any]] = []
    dropped: list[tuple[Any, str]] = []
    withheld: list[str] = []

    def claims(refs: list[Any]) -> list[str]:
        return [str(atoms[str(ref)].get('claim')) for ref in refs if str(ref) in atoms]

    for scene in (out_ch.get('scenes') or []):
        stages = [bidx[str(ref)] for ref in (scene.get('stage_refs') or [])
                  if str(ref) in bidx]
        for row in stages:
            withheld += [cid for cid in row.get('present_char_ids') or [] if cid not in disp]
        stage_rows = [{
            'time_layer': row.get('time_layer'), 'place': row.get('place'),
            'present': [disp[cid] for cid in (row.get('present_char_ids') or []) if cid in disp],
        } for row in stages]
        land_refs = list(scene.get('land_fact_refs') or [])
        context_refs = list(scene.get('context_fact_refs') or [])
        w: dict[str, Any] = {
            'slot': scene.get('slot'), 'purpose': scene.get('purpose'),
            'role': scene.get('role'), 'delivery': scene.get('delivery'),
            'transition': scene.get('transition'), 'budget_chars': scene.get('budget_chars'),
            'required_facts': claims(land_refs), 'context_facts': claims(context_refs),
            'turn_fact': str(atoms[str(scene.get('turn_fact_ref'))].get('claim')),
            'entry_state': claims(list(scene.get('entry_state_refs') or [])),
            'exit_state': claims(list(scene.get('exit_state_refs') or [])),
            'stages': stage_rows,
        }
        selected = [materials[str(ref)] for ref in (scene.get('material_refs') or [])
                    if str(ref) in materials]
        groups, unknown, hidden_names = material_tasks(selected, atoms, disp)
        withheld.extend(hidden_names)
        for group, pieces in groups.items():
            if pieces:
                w[group] = pieces
        dropped.extend(unknown)
        scenes.append(w)
    return scenes, dropped, withheld


def _plot_free_rules(canon: dict[str, Any], o: int, naming: dict[str, str], avail: dict[str, int]
                     ) -> tuple[list[dict[str, Any]], list[tuple[Any, tuple[str, ...]]]]:
    """`world_rules` 取【本章生效】的那一版，并把点到人名的那些**按叫法时点扣住**。

    闸门用的是【叫法的时点】，不是【角色的时点】。
    `avail` = `surface_first_chapter(registry)`。规则的放行章 = 它点到的所有叫法里**最晚**的那一个。
    规则正文只读 `versions[].desc`，不接受任何平级文本兜底。

    返回 `(保留的规则, held)`。
    """
    out: list[dict[str, Any]] = []
    held: list[tuple[Any, tuple[str, ...]]] = []
    for r in (canon.get('world_rules') or []):
        vers = sorted([v for v in (r.get('versions') or [])
                       if (v.get('effective_from_chapter') or 1) <= o],
                      key=lambda v: v.get('effective_from_chapter') or 1)
        if not vers:
            continue          # 本章尚未生效；「一条 version 都没有」由落盘前的显式终态拦住
        r2 = {k: v for k, v in r.items() if k != 'versions'}
        r2['description'] = vers[-1].get('desc')
        hit = rule_names_a_person(r2.get('description'), naming)
        if hit:
            gate = max(avail.get(n, 1) for n in hit)
            if gate > o:
                held.append((r2.get('rule_id'), tuple(sorted(hit)[:3])))
                continue
        out.append(r2)
    return out, held






def _project_identity_facts(global_g: dict[str, Any], ordinal: int, disp: dict[str, str]
                            ) -> tuple[dict[str, Any], list[tuple[Any, tuple[str, ...]]]]:
    """身份与叫法时点的机械投影；知识内容只由独立关联及当前场景编译。

    叫法的 who/by 同样走本章表，避免用后续才出现的本名作为表格标签。
    不再把秘密答案连同 reader_knows_by_now=false 送给埋点章写手。
    """
    chars = global_g['entities']['characters']
    address = []
    for cid, character in chars.items():
        if cid not in disp:
            continue
        for item in character['address']:
            if item['from_chapter'] <= ordinal:
                by = item['by']
                by_name = disp.get(by) if _CHAR_ID.fullmatch(str(by or '')) else by
                address.append({'who': disp[cid], 'surface': item['surface'],
                                'by': by_name, 'context': item['context']})
    rules, held = _plot_free_rules(global_g['canon'], ordinal, person_naming_surfaces(chars),
                                   surface_first_chapter(chars))
    return {'address': address, 'world_rules': rules}, held


def _resolve_goal(goal: dict[str, Any], *, ordinal: int,
                  atoms: dict[str, dict[str, Any]], disp: dict[str, str],
                  disclosure_targets: dict[tuple[str, str], str]) -> dict[str, Any]:
    """稳定引用编译为写手目标；知识目标只使用已过场景披露许可的投影。

    不能以目标解析为名回读未开放的全局知识正文，或把内部关联 ID 下发写手。
    无法解析的目标是显式结构终态，交 Plan 订正后免费重建上下文。
    """
    intent = goal.get('intent')

    resolved: list[str] = []
    failures: list[str] = []
    for ref in goal.get('fact_refs') or []:
        kind, key = ref.get('kind'), ref.get('ref')
        value: str | None = disclosure_targets.get((str(kind), str(key)))
        if value is not None:
            pass
        elif kind == 'fact_atom':
            value = (atoms.get(str(key)) or {}).get('claim')
            if value:
                value = f'本章事实：{value}'
        elif kind == 'character':
            if disp.get(key):
                value = f'人物：{disp[key]}'
        elif kind == 'secret' and intent == 'withhold':
            value = '保留本章尚未公开的一项既定秘密，不点破其内容'
        elif kind == 'chapter_boundary':
            suffix = str(key).rsplit(':', 1)[-1]
            where = {'start': '开篇', 'end': '结尾'}.get(suffix, '整体范围')
            value = f'第 {ordinal} 章{where}'

        if not value:
            failures.append(f'{kind}:{key}')
            continue
        # Machine identities must not survive inside a supposedly resolved
        # label.  Human setting keys are intentionally excluded from this test.
        if kind not in {'setting'} and key and str(key) in value:
            failures.append(f'{kind}:{key}（解析结果仍含内部 id）')
            continue
        if value not in resolved:
            resolved.append(value)

    if failures or not resolved:
        raise RefusedByStructure(
            f'输出章 {ordinal} 的 writer goal 有无法在本章安全解析的事实引用：'
            f'{failures or ["empty"]}。内部 id 与未过揭示闸门的全局事实不得下发；'
            f'请修订 plan 的 fact_refs 后 0 成本重建 writer_context。')
    return {'intent': intent, 'facts': resolved}


# ── 三、组装 ──────────────────────────────────────────────────────────────

def build_writer_context(plan: dict[str, Any], global_g: dict[str, Any],
                         blueprints: dict[str, Any], main_text: list[dict[str, Any]], *,
                         knowledge_links: dict[str, Any]) -> Assembly:
    """把【写作节点该看到的东西】确定性地组装成 `stage4/writer_context.json`。

    ⚠ 写手只从计划读取【重构后】的开收目标；历史 Stage2 章摘要与 end_hook 均已裁撤，
    不再保留一份对原章起收方式的重复描述。

    全局证据原文在入口剥除，知识按正式关联及场景边界投影，文学 arc 不进入下发面。
    语义披露质量仍需独立审阅，本层不以机械关联成功冒充质量接受。

    返回 `Assembly` 而不是裸 dict，使撞名、剔除、闸住等信息能够进入报告。
    """
    # SourceSpan 只服务 Stage2 回取/复核，不是写手故事材料。这里在唯一组装入口剥一次，
    # setting 即使整格投影也不会把证据原文送进付费 prompt。
    semantic_global = without_global_evidence(global_g)
    assert isinstance(semantic_global, dict)
    global_g = semantic_global

    bp = {b.get('chapter_ordinal'): b for b in (blueprints.get('blueprints') or [])}
    text = {m.get('chapter_ordinal'): (m.get('text') or '') for m in main_text}
    chars = ((global_g.get('entities') or {}).get('characters') or {})
    name_registry, subs, unattested = project_character_names(
        chars, '\n'.join(text.values()))
    disp = {
        char_id: str(row['display_name'])
        for char_id, row in name_registry.items()
    }
    if unattested:
        # 【显式终态·勿静默】原文里查不到、`address` 里也没有一个有据叫法的角色。
        # 静默下发等于把 registry 的记账标签当人名发给写手，**它没有别的可用就会写进正文**。
        raise RefusedByStructure(
            f'{len(unattested)} 个角色没有任何【原文里有据】的称呼可下发：'
            f'{[(c, r) for c, r in unattested[:8]]}。\n'
            f'canonical 在原文里出现 0 次（是模型自造的记账标签），'
            f'address[] 里也没有一条在原文里出现过的 surface。\n'
            f'写手手里只会有这一个叫法，于是它原样写进正文（实测「苏父蹙眉」就是这么来的）。\n'
            f'这里必须由人判一次，两种情况的修法不同：\n'
            f'  · 原文其实有叫法（爹爹/我妈/老爷…）→ 让 stage2 把它收进 address，这是漏抽；\n'
            f'  · 这个人原文里确实从没被称呼过 → canonical 就得直接写成这本书会写出来的说法')

    # 【显式终态·勿静默】规则正文的唯一落点是 `versions[].desc`，不设第二落点兜底。
    rules_all = ((global_g.get('canon') or {}).get('world_rules')) or []
    ruleless = [r.get('rule_id') for r in rules_all
                if not [v for v in (r.get('versions') or []) if (v.get('desc') or '').strip()]]
    if ruleless:
        raise RefusedByStructure(
            f'world_rules {ruleless} 没有任何带 desc 的 version，规则正文缺失，未落盘。'
            f'⚠ 这里**没有兜底**：`description` 已从 stage2 契约删除，'
            f'而拿一句来路不明的文本充数正是这条终态要防的东西')

    bidx = beat_index(blueprints, main_text)
    materials = {str(hinge['hinge_id']): hinge for beat in bidx.values()
                 for hinge in beat['hinges']}
    atom_index = {str(atom.get('fact_id')): atom
                  for blueprint in (blueprints.get('blueprints') or [])
                  for atom in (blueprint.get('fact_atoms') or [])}
    asm = Assembly(subs=subs, collisions=name_collisions(disp, chars))

    # 【到本章为止的原文】章级名字表的输入。累积一次，循环里按源章号取。
    # 章号保持源章同号，场景内的披露时点另由当前知识关联确定。
    cum: dict[Any, str] = {}
    acc: list[str] = []
    for k in sorted(text):
        acc.append(text[k])
        cum[k] = '\n'.join(acc)

    outs = plan.get('output_chapters') or []
    chapters = []
    for o in outs:
        narration = []
        srcs = o.get('from_source_ordinals') or []
        o_src = max(srcs) if srcs else 0
        disp_o = display_names_at(chars, cum.get(o_src, ''))
        # 身份材料只沿显式事实、舞台、叙述和目标引用进入本章。过去露过一次面
        # 不等于本章仍需把整份人物登记反复发给写手。
        needed = {cid for fact in (bp.get(o_src) or {}).get('fact_atoms') or []
                  for cid in fact.get('participant_char_ids') or []}
        needed.update(cid for scene in o.get('scenes') or []
                      for ref in scene.get('stage_refs') or []
                      for cid in bidx.get(str(ref), {}).get('present_char_ids') or [])
        # 对白对象未必在舞台上，叙述事实的 participant 也不能代替明确说话关系。
        # 材料的两条依赖都闭合，不能因裁剪人物表把一个已知对象降成 unknown。
        for scene in o['scenes']:
            for ref in scene['material_refs']:
                material = materials[str(ref)]
                needed.update(cid for fid in material['source_fact_refs']
                              for cid in atom_index[fid]['participant_char_ids'])
                dialogue = material.get('dialogue')
                if dialogue is not None:
                    parties = [dialogue['speaker'], *(dialogue['addressees'] or [])]
                    needed.update(party['char_id'] for party in parties
                                  if party is not None and party['kind'] == 'character')
        goal_refs = {(ref['kind'], ref['ref'])
                     for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal')
                     for ref in o[key]['fact_refs']}
        needed.update(ref for kind, ref in goal_refs if kind == 'character')
        needed.update(cid for relation in global_g['entities']['relations']
                      if ('relation', relation['relation_id']) in goal_refs
                      for cid in (relation['subject_id'], relation['object_id']))
        needed.update(event['subject']['char_id'] for secret in global_g['canon']['secrets']
                      if ('secret', secret['secret_id']) in goal_refs
                      for event in secret['knowledge_events']
                      if event['subject']['kind'] == 'character')
        needed.update(cid for seg in (bp.get(o_src) or {}).get('narration') or []
                      for cid in (seg.get('narrator_char_id'), seg.get('focalizer_char_id')) if cid)
        disp_o = {cid: name for cid, name in disp_o.items() if cid in needed}
        for s in srcs:
            b = bp.get(s) or {}
            segs = b.get('narration') or []
            narration += [_name_narration(g, disp_o, i, len(segs)) for i, g in enumerate(segs)]
        facts, held = _project_identity_facts(global_g, o_src, disp_o)
        oo = o.get('output_ordinal')
        asm.held_rules |= {(oo,) + hh for hh in held}
        scenes, unk_kinds, withheld = _resolve_scenes(o, bidx, atom_index, disp_o)
        from novel.stages.stage4.disclosure import project as project_disclosure
        disclosure = project_disclosure(o, scenes, facts, global_g, atom_index, disp_o,
                                        knowledge_links, needed_characters=needed,
                                        materials=materials)
        facts, scenes = disclosure.facts, disclosure.scenes
        asm.withheld_setting.extend((oo, key) for key, value in facts['setting'].items()
                                    if value is None)
        asm.withheld += [(oo, c) for c in withheld]
        # ⚠⚠ **章级表会造出书级表没有的撞名，必须出声**：书级表判有据用全书正文，
        #   于是某角色拿到 canonical、不撞名；而在 ch2 原文还没写出那个本名，
        #   退回最常用的有据叫法就是「书童」——与另外两个真·书童撞成一个名字。
        #   这是**原文本身的模糊**，不在这里替它造区分名；但**收窄名字表这个动作
        #   新增了撞名**，静默发生就等于我们自己制造了歧义还不告诉人。
        asm.chapter_collisions += [(oo, n, ids) for n, ids, _c in name_collisions(disp_o, chars)]
        asm.dropped_hinges += [(oo,) + u for u in unk_kinds]
        contract = semantic_contract_of(bp.get(oo) or {})

        def contract_claims(refs: list[Any]) -> list[str]:
            return [str(atom_index[str(ref)].get('claim')) for ref in refs
                    if str(ref) in atom_index]

        semantic_contract = {
            'chapter_function': contract.get('chapter_function'),
            'information_delta': contract_claims(
                list(contract.get('information_delta') or [])),
            'character_state_delta': contract_claims(
                list(contract.get('character_state_delta') or [])),
            'relationship_delta': contract_claims(
                list(contract.get('relationship_delta') or [])),
            'reader_knowledge_delta': contract_claims(
                list(contract.get('reader_knowledge_delta') or [])),
            'must_land_facts': contract_claims(
                list(contract.get('must_land_fact_refs') or [])),
            'context_facts': contract_claims(
                list(contract.get('context_fact_refs') or [])),
            'causal_edges': [{
                'before': contract_claims([edge.get('before')])[0],
                'after': contract_claims([edge.get('after')])[0],
            } for edge in (contract.get('causal_edges') or [])
                if contract_claims([edge.get('before')])
                and contract_claims([edge.get('after')])],
            'entry_state': contract_claims(
                list(contract.get('entry_state_refs') or [])),
            'ending_state': contract_claims(
                list(contract.get('ending_state_refs') or [])),
        }
        chapter_goal = _resolve_goal(
            o.get('chapter_goal') or {}, ordinal=oo, atoms=atom_index, disp=disp_o,
            disclosure_targets=disclosure.goal_targets)
        open_hook_goal = _resolve_goal(
            o.get('open_hook_goal') or {}, ordinal=oo, atoms=atom_index, disp=disp_o,
            disclosure_targets=disclosure.goal_targets)
        end_hook_goal = _resolve_goal(
            o.get('end_hook_goal') or {}, ordinal=oo, atoms=atom_index, disp=disp_o,
            disclosure_targets=disclosure.goal_targets)

        # rule_id / secret_id 只用于上面的确定性 join；写手和持久化上下文都不消费。
        for row in facts.get('world_rules') or []:
            row.pop('rule_id', None)
        for row in facts.get('secrets') or []:
            row.pop('secret_id', None)

        chapters.append({
            'output_ordinal': oo,
            'chapter_goal': chapter_goal,
            'open_hook_goal': open_hook_goal,
            'end_hook_goal': end_hook_goal,
            'semantic_contract': semantic_contract,
            'chapter_rewrite_brief': o.get('chapter_rewrite_brief'),
            'scenes': scenes,
            'narration': narration,
            'characters': [
                {'char_id': cid, 'display_name': nm,
                 'role': (chars.get(cid) or {}).get('role')}
                for cid, nm in sorted(disp_o.items())
            ],
            'facts': facts,
            'disclosure_windows': disclosure.windows,
        })

    # 【自造标签的收口只此一处】对整棵下发树做一遍确定性替换——**按信息收口，不按字段收口**，
    # 否则每加一个含散文的字段就多一条漏网通道。放在最后：此前所有分支
    # （events / role / chapter_goal / world_rules / secrets…）都已汇入这棵树。
    asm.ctx = rename_all({'chapters': chapters}, subs)
    return asm
