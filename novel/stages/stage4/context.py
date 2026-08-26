#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context.py —— `writer_context.json` 的**确定性组装**。

【契约】写作节点只吃 stage3（风格）+ 本文件这一份。本模块是**按引用取值 + 按章投影**，
不是「把计划给的东西转抄一遍」——四件事都由代码做：

  · 每个新场景按 `fact_refs` 去 stage2 取事实原子，再由原子的 `source_beat_ref` 回取
    【原文字节】与【舞台条件】；场景计划只存编排决策，不复制第二份事实源；
  · 下发的人名一律换成【原文里有据】的叫法（`canonical` 可能是模型自造的记账标签）；
  · 全书事实按「本章为止」裁剪（未揭示的 secret / 结局 / 人物弧终点不下发）；
  · 叙述视角原样透传，`char_id` 换成本名，段界描述换成**位置标签**。

⚠⚠ **本层是全链的收口点：前三层所有逐章闸门最后都汇到这一份产物上。**
六道闸一条都不许漏（`docs/schema/stage4.md` 事实②）：

| 闸门 | 挡什么 | 落点 |
|---|---|---|
| 叫法时点 | 本章原文里还叫不出名字的人 → 整个人不下发 | `display_names_at` |
| 秘密揭示章 | 未揭示且非埋点章的 secret → 整条不下发 | `_project_facts` |
| 规则放行章 | 规则点到的人本章还没被指名 → 暂不下发 | `_plot_free_rules` |
| 关系剧透闸门 | relation 的显式 `visibility` 决定读者可见章 | `relation_visibility_gates` |
| 弧线终点 | `arc_tracks` / `climax` / `ending` **只在末章下发** | `_project_facts` |
| setting 逐章闸门 | 转抄式说出未揭示 secret 的句子 → 剔除 | `_gate_setting` |

⇒ **改任何一条闸门，唯一能看到效果的地方是这份产物。**

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
from novel.core.facts import (
    first_reveal_chapter, relation_visibility_gates, secret_contains, secret_keywords,
    setup_chapters_of,
)
from novel.core.evidence import without_global_evidence
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import beat_index, verbatim_pieces
from novel.stages.stage4.measure import semantic_contract_of

#: `char_id` 的保留形。**按形状分流**：`char_\d+` 解析不出名字就不写，
#: 其余（`narration` / `public` / 「在场士兵」这类无名群体）是自由文本，原样留。
_CHAR_ID = re.compile(r'char_\d+')

#: `setting` 的句界。⚠ 含 `；`：那一层是「一句一段」排版，分号后面常常就是另一件事。
_SENT_SPLIT = re.compile(r'(?<=[。！？；])|\n+')


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
    #: setting 逐章闸门剔掉的句子（`[(输出章, 键, 句子)]`）
    setting_cut: list[tuple[Any, str, str]] = field(default_factory=list)
    #: setting 被整个剔空的章
    setting_emptied: list[Any] = field(default_factory=list)


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
        groups, unknown = verbatim_pieces(selected)
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


def _relation_labels(ents: dict[str, Any], disp: dict[str, str]) -> dict[str, str]:
    """`relation_id` → 人话标签（「苏清杳与江随野的关系（未婚夫妻）」）。

    `arc_tracks[].subject` 的取值域是 `char_id | "reader" | relation_id` 三选一，
    而这里过去只解析了 `char_id` —— `rel_05` 就这么原样进了写手手里。
    **下发给下游的名字必须是人话，不能是记账用的内部编号**。

    ⚠ 顺带说明为什么 `relation_id` 不是死字段：零消费审计看不见它，是因为它的消费方
    （这个 join）**从来没被实现过**——消费方存在但链路没接通，字段是坏的，不是没用。
    """
    out: dict[str, str] = {}
    for r in (ents.get('relations') or []):
        rid = r.get('relation_id')
        s, t = disp.get(r.get('subject_id')), disp.get(r.get('object_id'))
        if rid and s and t:
            pred = (r.get('predicate') or '').strip()
            out[rid] = f'{s}与{t}的关系' + (f'（{pred}）' if pred else '')
    return out


def _arc_subject(subj: Any, disp: dict[str, str], rel_labels: dict[str, str]) -> str | None:
    """把一条轨迹的 `subject` 说成人话；说不成返回 `None`（由调用方入显式终态）。"""
    if subj == 'reader':
        return '读者'                      # 本来就是人话
    return disp.get(subj) or rel_labels.get(subj)


def _gate_setting(setting: dict[str, Any], secrets: list[dict[str, Any]], hidden_ids: list[Any]
                  ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """`setting` 的逐章闸门：剔掉说出了【本章还不该揭示的 secret】的那些句子。

    【为什么 setting 需要一道闸门】它曾是**书级一份、无任何投影**地进写手 prompt。
    实测某本的 `世界观` 写着「主角穿越到…」「系统绑定宿主，发布攻略任务，目标爱意度达百分百」
    ——那**就是** `secret_03` 的全部内容，而它对读者的揭示在 ch14，写 ch1 的写手照样读到。
    上一次按信息盘通道时，`setting` 由 global 直发、**从没进过那份名单**——
    每次漏掉的那层都带着同一句直觉「这个块里不会有情节」。

    【这不是新加一层闸门，是把唯一的例外收编】`relations`/`secrets`/`world_rules`/`arc`/`address`
    全都已经在 `chapters[].facts` 里逐章投影，`setting` 是唯一漏在外面的那个。

    【这不是词表】切出来的片段来自 secret 自己的 `fact`（共用 `secret_keywords`），
    不是人手写的词表；换一本书自动跟着变。

    ⚠⚠ **实测基线：盘上唯一有 secrets 的那本书上，它命中 0 次。**
    四条 secret × 两个 setting 键 = 8 组比对，全部无命中，**包括那条已知的真泄漏**：
    `fact` =「许言是穿越者，绑定系统，任务是完成剧情攻略男主们」（揭示 ch14），
    `世界观` =「主角穿越到…系统绑定宿主，发布攻略任务…」——同一件事，**措辞完全不重叠**。
    这是**改写式**泄底，而本函数的判据是纯字面子串包含，**它只挡得住转抄式**。

    ⇒ **别把这道闸门读成「setting 的剧透面已经关掉了」。它没有。**
    它的价值只有两条：① 万一 setting 真的抄了 secret 的措辞，这一条能挡；
    ② 把 `setting` 收进逐章 `facts`，让将来真有判据时有地方接。
    ⚠ **命中 0 时调用方要打一条常驻「查不了」，不打「通过」**。

    ⚠ 真根因在**产地**：`canon.setting` 装着情节，与 `world_rules[].desc` 混进人名是同一类病。
    """
    if not setting or not hidden_ids:
        return dict(setting or {}), []
    hid = set(hidden_ids)
    kws = [k for s in secrets if s.get('secret_id') in hid
           for k in secret_keywords(s.get('fact'))]
    if not kws:
        return dict(setting), []
    out: dict[str, Any] = {}
    cut: list[tuple[str, str]] = []
    for key, val in setting.items():
        if not isinstance(val, str):
            out[key] = val
            continue
        keep = []
        for sent in _SENT_SPLIT.split(val):
            if sent and sent.strip():
                if any(secret_contains(k, sent) for k in kws):
                    cut.append((key, sent.strip()[:40]))
                else:
                    keep.append(sent)
        # ⚠ **整条被剔光不静默**：`setting` 是写手判断「这是个什么世界」的唯一依据，
        #   空掉它比多给一句更危险。留空值 + 由调用方报出来，不假装它本来就没有。
        out[key] = ''.join(keep).strip()
    return out, cut


def _known_by_at(secret: dict[str, Any], ordinal: int, disp: dict[str, str]) -> list[str]:
    """把本章已经知情的 typed subject 投影成 `known_by` 的人话列表。

    `knowledge_events` 的时间判断是包含式：`known_from_chapter <= ordinal`。
    CharacterRef 走本章名字表，叫不出名字就略去；GroupRef 的自由文本 label 原样保留。
    evidence 只用于上游回取与审计，不进入写手上下文。

    本仓没有旧 artifact 迁移/双形状读取；缺整格 `knowledge_events` 与显式空数组都不
    猜知情主体。GlobalGrounding v2 会先被注册版本拒掉，不能在下发层悄悄复活旧语义。
    """
    values = []
    for event in (secret.get('knowledge_events') or []):
        if not isinstance(event, dict):
            continue
        known_from = event.get('known_from_chapter')
        if type(known_from) is not int or known_from > ordinal:
            continue
        subject = event.get('subject')
        if not isinstance(subject, dict):
            continue
        if subject.get('kind') == 'character':
            values.append(disp.get(subject.get('char_id')))
        elif subject.get('kind') == 'group':
            label = subject.get('label')
            values.append(label.strip() if isinstance(label, str) else None)

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _project_facts(global_g: dict[str, Any], o: int, is_last: bool, disp: dict[str, str]
                   ) -> tuple[dict[str, Any], list[tuple[Any, tuple[str, ...]]],
                              list[tuple[str, str]]]:
    """把全书事实裁剪到【本章为止】——闭卷防的是**文风**泄漏，全量下发泄的是**剧情**。

    【`disp` 是唯一的名字表】= 本章可下发的名字（章级）。凡是要写进下发树的名字都用它；
    **查不到就不写那一项**——写手拿到一个本章还不存在的名字，就是剧透。

    实测（裁剪前）：写 ch1 的写手能看到**全部 7 条尚未揭示的 secret** 以及
    `arc.climax`「第 20 章…赐死…」、`arc.ending`「第 23 章…密牢折磨」。
    **写手一旦知道结局，笔下的伏笔就会写得过明**——对一本靠揭示节奏吃饭的书这是致命的。

    ⚠ **裁剪的边界**：**点名了某个后续事件或结局的**才裁（未揭示的 secret、climax/ending、
    人物弧的终点）；框架性的**不裁**（setting / world_rules / characters / 当前阶段），
    因为**错删事实的代价（写手改口、编名字）比多给严重**。

    `secrets` 的闸门是两条【或】关系，两条都带时间维：
      ① 读者到本章已被**首次**揭示（`first_reveal_chapter(s) <= o`）→ 可以写在纸面上；
      ② 本章是这条 secret 的【埋点章】→ 事实要摆在读者眼前，
         **但当时读来是别的意思**，此时给 `reader_knows_by_now=false`，意思是「要埋，不要点破」。
         没有②，写 ch3 那只茶盏时不知道它是主角安排的，ch9 的回收就没有铺垫。

    ⚠ 一条 secret 只承载一个原子事实，并直接保存唯一的 `first_reveal_chapter`。
    分步点破的复合真相必须拆成多条 secret，各自按自己的首次揭示章下发；不另存确认
    事件或分组标签，因为它们不参与任何下游判定。

    ⚠⚠ `knowledge_events` 有时间维，但它只回答**世内谁到本章已经知道**，用于裁剪
    `known_by`；它仍然不能替代读者的揭示时点来放行 secret。第一人称主角先知道真相，
    不等于读者已经知道；秘密本身的两臂闸门仍只认首次揭示章与埋点章。
    """
    ents = global_g.get('entities') or {}
    canon = global_g.get('canon') or {}
    arc = global_g.get('arc') or {}

    # 【投影出去的一律用人名，不留 char_id】写作侧不该看见内部编号——它得拿着人物表反查，
    # 而「每层只说自己的语言」正是为了避免这种事。
    secrets: list[dict[str, Any]] = []
    hidden: list[Any] = []
    for s in (canon.get('secrets') or []):
        first = first_reveal_chapter(s)
        past = first is not None and first <= o
        is_setup = o in setup_chapters_of(s)
        if not past and not is_setup:
            hidden.append(s.get('secret_id'))
            continue
        # 【只留 `reader_knows_by_now` 这一个标志】它已经把两种写法说完了。
        # 曾经并排的 `is_setup_chapter` 落盘之后**全仓无人读**，而且它和这个标志
        # 在揭示章上会同时为真——**一对自相矛盾的标志**。
        secrets.append({'secret_id': s.get('secret_id'), 'fact': s.get('fact'),
                        'known_by': _known_by_at(s, o, disp),
                        'reader_knows_by_now': past})

    # `relations` 是**第二条剧透通道**。`valid_from_chapter` 只记世内生效时点；
    # 显式 `visibility` 才是读者可见时点。两种 visibility 的 max / secret join
    # 与 typed fact refs 共用 core 那一个 gate，旧人名启发式只留给 INFO 对账。
    gates = relation_visibility_gates(ents, canon.get('secrets') or [])
    # ⚠ **两端都得叫得出名字，否则整条不发**：一条边只有一端有名字，写手拿到的是
    #   「许言 · 情人 · char_003」——裸编号进 prompt；而把它解析成全书通用名，
    #   正是这一整轮要堵的剧透（那个人本章还没登场）。
    #   两害相权：**这一章先不告诉写手这条关系**——关系还在，下一章他有名字了就会出现。
    rels = [{'subject': disp[r.get('subject_id')],
             'predicate': r.get('predicate'),
             'object': disp[r.get('object_id')]}
            for r in (ents.get('relations') or [])
            if r.get('subject_id') in disp and r.get('object_id') in disp
            and gates.get(r.get('relation_id')) is not None
            and gates[r.get('relation_id')] <= o
            and (r.get('valid_through_chapter') is None
                 or r.get('valid_through_chapter') >= o)]

    # `arc_tracks` 的 `trajectory` 写的是「从什么变到什么」——**终点就是结局**。
    # 去掉终点后只剩主体，是零信息；trajectory 本身终点即结局，截断不了。
    # **两头不讨好，所以非末章一律不发 `arc_tracks`。**
    a: dict[str, Any] = {}
    stages = arc.get('conflict_stages') or []
    stage = next((st for st in stages if o in (st.get('chapters') or [])), None)
    if stage is None:
        # 【阶段章列表会把连续段的中间章省掉】`conflict_stages[].chapters` 是显式枚举，
        # 而覆盖全部章这条性质**没有任何判据在查**（`ConflictStage.chapters` 的注释自己承认）。
        # 实测 flash 产出 [3,5]/[8,10]/[13,15]，4/9/14 三章落在段内空隙——旧代码只认
        # 精确成员，此时组出**空的 arc**，`CtxArc.current_stage` 是必填字段，
        # `WriterContext.model_validate` 在落盘事务里裸抛 exit 70（结论未知）。
        # ⇒ 按 min≤o≤max 的**区间包含**解释：唯一可解时照常下发，读出的是写段时的
        # 本意（连续段省写了中间章），不是发明归属；`chapters` 仍按记录原样透传。
        inside = []
        for st in stages:
            chs = [c for c in (st.get('chapters') or []) if isinstance(c, int)]
            if chs and min(chs) <= o <= max(chs):
                inside.append(st)
        if len(inside) != 1:
            # 跨阶段的真空隙（0 个包含）与双包含（≥2 个）都说不清唯一归属：
            # current_stage 是写手必拿的章级事实，省略与猜一个都是静默造假。
            raise RefusedByStructure(
                f'第 {o} 章不在任何冲突阶段的章列表里，区间解释也定不出唯一归属'
                f'（包含它的阶段数 = {len(inside)}）。请重跑 stage2.global（付费）'
                f'让 conflict_stages 覆盖全部章；本层不省略也不猜测章级阶段事实')
        stage = inside[0]
    if stage:
        # ⚠ **只给阶段名与跨度，不给 `summary`**：摘要描述的是【整个阶段】的经过，
        # 里面必然含本阶段后几章才发生的结果——实测 ch16 拿到的阶段摘要写着 ch20 的事。
        a['current_stage'] = {k: stage.get(k) for k in ('stage', 'chapters')}
    if is_last:
        rl = _relation_labels(ents, disp)
        a['arc_tracks'] = [
            {'subject': _arc_subject(t.get('subject'), disp, rl),
             'trajectory': t.get('trajectory')}
            for t in (arc.get('arc_tracks') or [])]
        a['climax'], a['ending'] = arc.get('climax'), arc.get('ending')

    # 称谓表按起点投影：把全书的叫法一股脑给写手，第 3 章就会写出第 20 章才该有的称呼
    # （实测某个亲密称呼要到 ch21 两人关系变了才出现）。
    # 【没有终点这一维】曾有过 `to_chapter`，但模型 48/48 全填 null，而确定性派生
    # 噪声压倒信号——17 个候选里最亲密的那个反被判成 ch21 退役，**方向正好相反**。
    # 叫法停用的【成因】是关系变了，那件事记在 `relations.valid_through_chapter` 上。
    # ⚠⚠ **`who` 也要走章级表**：这一处是本函数**自己内部**漏掉的那条——
    #   闸门筛的是「列哪些叫法」，而挂在它上面的 `who` 标签用的仍是全书表，
    #   于是称谓表本身合规、标签却把那个人的全书通用名说了出来。
    #   **这一次的旁路就在同一个 return 语句里。**
    chars = ents.get('characters') or {}
    address = []
    for cid, v in chars.items():
        if cid not in disp:
            continue                      # 本章还叫不出他的名字，整个人先不列
        for ad in (v.get('address') or []):
            if (ad.get('from_chapter') or 1) <= o:
                by = ad.get('by')
                by_name = disp.get(by) if _CHAR_ID.fullmatch(str(by or '')) else by
                address.append({'who': disp[cid], 'surface': ad.get('surface'),
                                'by': by_name, 'context': ad.get('context')})

    rules, held = _plot_free_rules(canon, o, person_naming_surfaces(chars),
                                   surface_first_chapter(chars))
    setting, set_cut = _gate_setting(canon.get('setting') or {},
                                     canon.get('secrets') or [], hidden)
    return ({'setting': setting, 'relations': rels, 'secrets': secrets, 'world_rules': rules,
             'arc': a, 'address': address}, held, set_cut)


def _resolve_goal(goal: dict[str, Any], *, ordinal: int,
                  atoms: dict[str, dict[str, Any]], disp: dict[str, str],
                  facts: dict[str, Any], global_g: dict[str, Any]) -> dict[str, Any]:
    """Project a durable ``PlanGoal`` into a writer-safe, human-readable goal.

    The plan keeps stable IDs because they make every decision recomputable.  A
    writer cannot act on ``b03_02`` or ``secret_04`` and must never see those
    join keys.  Resolution happens here, after all chapter/reveal gates have
    run, so a reference cannot bypass the same projection used by ``facts``.
    An inaccessible non-secret reference is a broken plan/context edge and is
    therefore an explicit terminal state, not an invitation to leak the global
    record or invent a label.
    """
    intent = goal.get('intent')
    entities = global_g.get('entities') or {}
    relations = {
        row.get('relation_id'): row
        for row in (entities.get('relations') or []) if row.get('relation_id')
    }
    projected_relations = facts.get('relations') or []
    projected_rules = {
        row.get('rule_id'): row
        for row in (facts.get('world_rules') or []) if row.get('rule_id')
    }
    projected_secrets = {
        row.get('secret_id'): row
        for row in (facts.get('secrets') or []) if row.get('secret_id')
    }

    resolved: list[str] = []
    failures: list[str] = []
    for ref in goal.get('fact_refs') or []:
        kind, key = ref.get('kind'), ref.get('ref')
        value: str | None = None
        if kind == 'fact_atom':
            value = (atoms.get(str(key)) or {}).get('claim')
            if value:
                value = f'本章事实：{value}'
        elif kind == 'character':
            if disp.get(key):
                value = f'人物：{disp[key]}'
        elif kind == 'world_rule':
            row = projected_rules.get(key) or {}
            if row.get('description'):
                value = f'规则：{row["description"]}'
        elif kind == 'secret':
            row = projected_secrets.get(key)
            if row and row.get('fact'):
                prefix = ('只作伏笔，不点破：' if not row.get('reader_knows_by_now')
                          else '已揭示事实：')
                value = prefix + str(row['fact'])
            elif intent == 'withhold':
                # A hidden secret's content is deliberately absent from this
                # chapter projection.  The generic directive is sufficient:
                # events carry what to write, while the gate prevents the
                # writer from learning what those events will later mean.
                value = '保留本章尚未公开的一项既定秘密，不点破其内容'
        elif kind == 'relation':
            row = relations.get(key) or {}
            subject = disp.get(row.get('subject_id'))
            obj = disp.get(row.get('object_id'))
            predicate = row.get('predicate')
            projected = {'subject': subject, 'predicate': predicate, 'object': obj}
            if subject and obj and predicate and projected in projected_relations:
                value = f'关系：{subject}与{obj}（{predicate}）'
        elif kind == 'setting':
            setting = (facts.get('setting') or {}).get(key)
            if isinstance(setting, str) and setting.strip():
                value = f'设定：{setting.strip()}'
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
                         blueprints: dict[str, Any], main_text: list[dict[str, Any]]) -> Assembly:
    """把【写作节点该看到的东西】确定性地组装成 `stage4/writer_context.json`。

    ⚠ 写手只从计划读取【重构后】的开收目标；历史 Stage2 章摘要与 end_hook 均已裁撤，
    不再保留一份对原章起收方式的重复描述。

    【剧透面已全部关闭】曾经有四条旁路把未揭示事实泄给写手，逐条堵掉：
    `characters[].identity` 写着结局 → 契约拆成 role/fate，只发 role ·
    `world_rules` 里混着情节与结局 → 按叫法时点扣住 ·
    `core_conflict`（「…最终走向自由」）→ 该字段随后从 stage2 契约整个删除 ·
    `arc_tracks` 剥掉 trajectory 后是零信息、不剥就是结局 → 非末章不发。

    返回 `Assembly` 而不是裸 dict，使撞名、剔除、闸住等信息能够进入报告。
    """
    # SourceSpan 只服务 Stage2 回取/复核，不是写手故事材料。这里在唯一组装入口剥一次，
    # setting 与末章 arc_tracks 即使整格投影也不会把证据原文送进付费 prompt。
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

    # 【显式终态·勿静默】`arc_tracks[].subject` 三种取值都必须能说成人话（末章会下发给写手）。
    # 实测某本 ch23 有 **7 处原样发出去了**：`char_001`×3 / `char_002` / `char_003` /
    # `rel_05` / `rel_09`。
    _arc = (global_g.get('arc') or {})
    _rl = _relation_labels(global_g.get('entities') or {}, disp)
    unresolved = [t.get('subject') for t in (_arc.get('arc_tracks') or [])
                  if not _arc_subject(t.get('subject'), disp, _rl)]
    if unresolved:
        raise RefusedByStructure(
            f'arc_tracks 的 subject 解析不出人话：{unresolved}，未落盘。'
            f'取值域是 char_id | "reader" | relation_id；'
            f'relation_id 要两端都能解析成人名才有标签可发')

    bidx = beat_index(blueprints, main_text)
    atom_index = {str(atom.get('fact_id')): atom
                  for blueprint in (blueprints.get('blueprints') or [])
                  for atom in (blueprint.get('fact_atoms') or [])}
    asm = Assembly(subs=subs, collisions=name_collisions(disp, chars))

    # 【到本章为止的原文】章级名字表的输入。累积一次，循环里按源章号取。
    # ⚠ 章号取 `max(srcs)` —— 与 `_project_facts` 的闸门**同一个口径**。
    cum: dict[Any, str] = {}
    acc: list[str] = []
    for k in sorted(text):
        acc.append(text[k])
        cum[k] = '\n'.join(acc)

    outs = plan.get('output_chapters') or []
    chapters = []
    for oi, o in enumerate(outs):
        n_chars, narration = 0, []
        srcs = o.get('from_source_ordinals') or []
        o_src = max(srcs) if srcs else 0
        disp_o = display_names_at(chars, cum.get(o_src, ''))
        for s in srcs:
            b = bp.get(s) or {}
            segs = b.get('narration') or []
            n_chars += len(text.get(s, ''))
            narration += [_name_narration(g, disp_o, i, len(segs)) for i, g in enumerate(segs)]
        facts, held, set_cut = _project_facts(global_g, o_src, oi == len(outs) - 1, disp_o)
        oo = o.get('output_ordinal')
        asm.setting_cut += [(oo,) + c for c in set_cut]
        if not any((facts.get('setting') or {}).values()) and (global_g.get('canon') or {}).get('setting'):
            asm.setting_emptied.append(oo)
        asm.held_rules |= {(oo,) + hh for hh in held}
        scenes, unk_kinds, withheld = _resolve_scenes(o, bidx, atom_index, disp_o)
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
            facts=facts, global_g=global_g)
        open_hook_goal = _resolve_goal(
            o.get('open_hook_goal') or {}, ordinal=oo, atoms=atom_index, disp=disp_o,
            facts=facts, global_g=global_g)
        end_hook_goal = _resolve_goal(
            o.get('end_hook_goal') or {}, ordinal=oo, atoms=atom_index, disp=disp_o,
            facts=facts, global_g=global_g)

        # rule_id / secret_id 只用于上面的确定性 join；写手和持久化上下文都不消费。
        for row in facts.get('world_rules') or []:
            row.pop('rule_id', None)
        for row in facts.get('secrets') or []:
            row.pop('secret_id', None)

        chapters.append({
            'output_ordinal': oo,
            'from_source_ordinals': srcs,
            'chapter_goal': chapter_goal,
            'open_hook_goal': open_hook_goal,
            'end_hook_goal': end_hook_goal,
            'semantic_contract': semantic_contract,
            'chapter_rewrite_brief': o.get('chapter_rewrite_brief'),
            'budget_chars': sum(scene['budget_chars'] for scene in scenes),
            'scenes': scenes,
            'narration': narration,
            'source_chars': n_chars,
            'characters': [
                {'char_id': cid, 'display_name': nm,
                 'role': (chars.get(cid) or {}).get('role')}
                for cid, nm in sorted(disp_o.items())
            ],
            'facts': facts,
        })

    # 【自造标签的收口只此一处】对整棵下发树做一遍确定性替换——**按信息收口，不按字段收口**，
    # 否则每加一个含散文的字段就多一条漏网通道。放在最后：此前所有分支
    # （events / role / chapter_goal / world_rules / secrets…）都已汇入这棵树。
    asm.ctx = rename_all({'chapters': chapters}, subs)
    return asm
