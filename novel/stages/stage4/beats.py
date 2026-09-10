#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 对 Stage2 证据材料的确定性索引。

Stage2 beat 只保留为原文 span、舞台条件与保真片段的证据容器；它不再被切分、合并或
展开成写作骨架。Stage4 的写作顺序只来自场景编译器生成的 `scenes[]`。
"""
from __future__ import annotations

import re
from typing import Any

from contracts.invariants import HINGE_GROUPS
from novel.core.spans import locate_spans

#: 一条 hinge 拆成「点」时的段落边界。⚠ **按段落拆，不按句子拆**：见 `verbatim_pieces`。
_PARA_SPLIT = re.compile(r'\n\s*\n')


def _hinge_text(hinge: dict[str, Any]) -> str:
    """一条 hinge 的原文字节。**入参恒是 stage2 产物形态的 hinge**（`{kind, source}`）。

    ⚠⚠ **这里只认 `source`，而这是本模块唯一允许的 hinge 形态**——别再加
    `else hinge.get('text')` 那样的第二条读法。真实发生过的事：
    `beat_index` 曾把 hinge 归一成 `{'kind','text'}` 再交给 `verbatim_pieces`，
    于是本函数需要双分支才能同时读两种形态；而那个 `text` 键**与已废弃的旧 stage2
    契约（hinge 直接带顶层 `text`）长得一模一样**，一次「清理遗留兼容」的重构就把它
    连带删掉了——归一形态从此读不出字节，`verbatim_pieces` 恒返回空，
    **闭卷写手拿原文的唯一通道整整断了一批书**（盘上 5 本共 898 条 hinge 下发 0 条），
    而上下游判据的分母同时归零，一路报绿。
    ⇒ 根治办法不是把那条分支加回来，是**不再造第二种形态**（见 `beat_index`）。
    """
    source = hinge.get('source')
    return str(source.get('text') if isinstance(source, dict) else '')


def beat_index(blueprints: dict[str, Any],
               main_text: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`beat_id` → 写作侧要的原文材料，**按引用取值**。

    · `source_chars` 由 `beat.span` **确定性算出**（不是模型填的）；
      算不出给 `None`，**绝不退回估算**——估算的方向会整个反过来。
    · `hinges` 是 stage2 落盘时**已回取成原文字节**的片段，
      所以这里搬运的一定是原文，不是模型打出来的串。
      ⚠⚠ **原样搬运，不许在这里重建成别的形状**（只按「有没有原文字节」过滤）。
      曾经这里归一成 `{'kind','text'}`，理由是「写作侧只要这两样」——它当时确实是
      恒等的（那一版 stage2 的 hinge 就长这样）。后来 stage2 换成 `{'kind','source'}`，
      这个中间形态就成了**第三种形态**，且它的 `text` 键与已废弃的旧契约同名；
      一次「清理遗留兼容」把 `_hinge_text` 里读 `text` 的那条分支删掉，
      下发通道当场断掉且无人出声（详见 `_hinge_text` 的注释）。
      ⇒ 形态只留两种：**stage2 产物形态**（本函数搬运的）与**下发的点**
      （`verbatim_pieces` 的产出）。中间不再有第三种。
    · 舞台三样是事实 claim 之外仍需单独提供的执行条件：
      一段转述若不标出来会被写成当场发生的戏；地点与在场者同理，闭卷时无从推断。
    """
    text = {m.get('chapter_ordinal'): (m.get('text') or '') for m in main_text}
    bps = blueprints.get('blueprints') or []
    spans, _problems = locate_spans(bps, text)
    size = {bid: e - s for chs in spans.values() for bid, s, e in chs}
    idx: dict[str, dict[str, Any]] = {}
    for b in bps:
        o = b.get('chapter_ordinal')
        for be in (b.get('beats') or []):
            idx[be.get('beat_id')] = {
                'ord': o,
                'source_chars': size.get(be.get('beat_id')),
                'hinges': [h for h in (be.get('hinges') or []) if _hinge_text(h).strip()],
                'time_layer': be.get('time_layer'),
                'place': be.get('place'),
                'present_char_ids': be.get('present_char_ids') or [],
            }
    return idx


def verbatim_pieces(hinges: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[tuple[Any, str]]]:
    """将非对白材料投影到登记的字符串通道；普通对白另走 typed 语义任务。

    ⚠⚠ **唯一一份实现**：`context._resolve_beats`（真正下发给写手的字节）与
    `measure.source_chars_table`（告诉规划器这一拍有多少字压不掉）**都调它**。
    两边各写一份拆分逻辑，报给规划者的数就会与写手实际收到的字节对不上
    ——一个量纲只能有一个口径，**而错的往往不是某个数，是那个跨口径的运算**。

    【分组守的是不同维度】全塞进一个叫 `verbatim` 的数组，
    写手会把「因果前提」也当成必须逐字出现的句子，硬塞背景交代——而它要的只是命题成立。
    ⚠ 映射表就是 `contracts.invariants.HINGE_GROUPS`，**这里不抄第二份**；
    「它的 key 必须与 `HINGE_KINDS` 逐字一致」由 `tests/test_invariants.py` 钉着
    （原型是模块加载时 `assert`，改成测试更好：它不会在 import 期炸掉整条命令行）。

    普通对白不在这里下发源句，由说话关系、语义与事实引用表达。剧情字面例外
    保留完整字符串，密码或识别序列的换行与符号不能拆散。其他材料按段落拆成独立点，
    避免把跨段原文包装成必须整体复制的任务。过去曾出现整章跨段台词全部丢失，
    因此不能把普通对白重新塞回逐字通道，也不能用拆段豁免真正的字面义务。

    【未知 kind 不下发，也不静默】曾经这里是 `HINGE_GROUPS.get(kind, 'verbatim')`——
    模型自造的 kind（实测「行动描述」）于是被**升级**成「原样写入，一个字都不能改」，
    而那是一句叙述文，正是「把段当点发」那条禁令挡的东西。
    ⇒ 现在丢弃并把它交给调用方报出来。
    ⚠ **不在这里入终态**：一条旧数据不该把 `stage4.context` 这条 0 成本重算路永久堵死。
    """
    out: dict[str, list[str]] = {}
    unknown: list[tuple[Any, str]] = []
    for h in hinges:
        kind = h.get('kind')
        g = HINGE_GROUPS.get(kind)
        if g is None:
            unknown.append((kind, _hinge_text(h)[:20]))
            continue
        if g == 'dialogue':
            # 普通对白的源句只作后台证据，不能经字符串材料通道泄给闭卷写手。
            continue
        if g == 'verbatim':
            # 密码、谜面或识别序列的换行与符号属于义务；不能拆开后把改变序列判为满足。
            piece = _hinge_text(h)
            if piece.strip():
                out.setdefault(g, []).append(piece)
            continue
        for piece in _PARA_SPLIT.split(_hinge_text(h)):
            piece = piece.strip()
            if piece:
                out.setdefault(g, []).append(piece)
    return out, unknown


def material_scene_candidates(hinges: list[dict[str, Any]], atoms: dict[str, dict[str, Any]],
                              scenes: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    """B 请求与 Plan 校验共用实际事实归属，不按材料所在的源拍猜目标场景。

    有 land 义务时只取实际落场，后续 context 重提不制造重复候选。
    全是 context 时取显式使用它的场景；没有候选仍保留 unplaceable 歧义，
    不能当成没有材料。多个相关 land 分处多场时由 B 显式选择。
    """
    result: dict[str, tuple[str, ...]] = {}
    for hinge in hinges:
        refs = set(hinge['source_fact_refs'])
        if not refs or not refs <= atoms.keys():
            raise ValueError(f'{hinge["hinge_id"]} 的 source_fact_refs 缺失或引用本章外事实')
        land = {ref for ref in refs if atoms[ref]['obligation'] == 'land'}
        key = 'land_fact_refs' if land else 'context_fact_refs'
        result[str(hinge['hinge_id'])] = tuple(sorted(
            str(scene['slot']) for scene in scenes if (land or refs) & set(scene[key])))
    return result


def material_tasks(hinges: list[dict[str, Any]], atoms: dict[str, dict[str, Any]],
                   names: dict[str, str]
                   ) -> tuple[dict[str, list[Any]], list[tuple[Any, str]], list[str]]:
    """Context 与校验共用的材料投影；事实关系保留引用，原句仅字面例外下发。"""
    text_groups, unknown = verbatim_pieces(hinges)
    groups: dict[str, list[Any]] = dict(text_groups)
    withheld: list[str] = []

    def party(value: dict[str, Any] | None) -> str | None:
        if value is None:
            return None
        if value['kind'] == 'group':
            return str(value['label'])
        cid = value['char_id']
        if cid not in names:
            withheld.append(cid)
        return names.get(cid)

    for hinge in hinges:
        if hinge['kind'] != '要害台词':
            continue
        row = hinge['dialogue']
        addressees = row['addressees']
        resolved = [party(value) for value in addressees] if addressees is not None else None
        # 对象中有任何未解名称，就明确保持未知，不能删掉这个对象冒充完整列表。
        groups.setdefault('dialogue', []).append({
            'speaker': party(row['speaker']),
            'addressees': resolved if resolved is not None and None not in resolved else None,
            'meaning': row['meaning'], 'speech_act': row['speech_act'],
            'required_facts': [atoms[ref]['claim'] for ref in hinge['source_fact_refs']],
        })
    return groups, unknown, withheld
