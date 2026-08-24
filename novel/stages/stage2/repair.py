#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repair.py —— IR 系契约的字段级修复（`stage2.blueprint --patch`，5A 最小集）。
适用面由 `contract.py::ContractSpec.supports_field_patch` 声明，**不在本文件里点名**
（ir1.1 上线时正是那份名单漏了它，而漏改不会有任何东西出声）。

【只修数据已证明需要的两类】（重放实测两本书 2+2 条歧义、0 条其他可局部修的失败）
  · `候选选择`（`短语在本拍区间内多处命中`）：代码从 `hinge_ambiguous_pending` 账取回
    原短语、在拍区间内枚举命中位，模型**只挑 candidate_id**；选定后代码把短语向两侧
    **确定性扩展到拍内唯一**，再走全量重编译——代码枚举确定候选、模型只做受限选择。
  · `短语重给`（`短语不在本章原文里`）：原短语是编的、无候选可枚举，模型按
    「该拍 span 原文 + kind」重给一条能在拍内原样唯一找到的短语（validator 当场验）。

【修复 = 改 IR → `compile_v2` 全量重编译 → 全量判据 → 落盘】不原位改 artifact：
`P_INHERIT` 等信息在产物里已坍缩，原位改等于绕开编译器的全部一致性。
未修的章经 `canonical_ir_projection` 精确往返（重放已逐字节证明）。

【拒绝面】以下情况显式终态、不发一次请求：
  · 基线契约的 `supports_field_patch` 是 `False`（v3 的失败面走 `--chapters` 整章路）；
  · 存在整章级根因（drift / 跨章 / span 族——`REPAIR_ROUTE` 判 `chapter_regen`）：
    带着它们重编译会把 drift 的账错记成 void（投影丢短语），先 `--chapters`。

【缓存纪律】首次 patch 调用用**默认策略**（read+write）——中断恢复不再重复付费；
`refresh-write` 的语义是「明确要新采样」，不是「这是修复调用」（审阅 §五.5）。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from contracts.invariants import REPAIR_ROUTE
from contracts.schema.stage2 import (
    BlueprintProvenance, ChapterBlueprints, GlobalGrounding,
    HingePhraseResponse, HingeSelectResponse,
)
from novel import prov
from novel.core.spans import content_index, content_only
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.llm.ledger import combine_usage
from novel.llm.prompts import with_json_shape
from novel.run import RunContext
from novel.store import ArtifactStore

#: 候选上下文窗（前后各取的字符数）。给人/模型认位置用的展示量，不进任何判定。
_CONTEXT_CHARS = 16


@dataclass(frozen=True)
class RepairAction:
    route: str                  # 'candidate_select' | 'phrase_patch'
    chapter: int
    beat_id: str
    hinge_index: int            # 在该拍 hinges 里的下标
    hinge_kind: str
    phrase: str | None          # 候选选择的原短语；短语重给无


def _occurrences(seg: str, phrase: str) -> list[tuple[int, int]]:
    """拍区间内全部命中位（与 `compile_v2._locate_in_span` 同一把梯子：
    原样字节有 ≥1 命中就用字节位，否则退内容空间）。"""
    raw = phrase.strip()
    hits: list[tuple[int, int]] = []
    if raw:
        at = seg.find(raw)
        while at >= 0:
            hits.append((at, at + len(raw)))
            at = seg.find(raw, at + 1)
        if hits:
            return hits
    content, cmap = content_index(seg)
    needle = content_only(phrase)
    if not needle or not cmap:
        return []
    at = content.find(needle)
    while at >= 0:
        hits.append((cmap[at], cmap[at + len(needle) - 1] + 1))
        at = content.find(needle, at + 1)
    return hits


def disambiguate_phrase(seg: str, start: int, end: int) -> str | None:
    """把选中的那一处命中向两侧扩展成**拍内唯一**的字节串。

    确定性：先右后左逐字符交替扩，扩到唯一或触到区间两端为止。
    扩出来的串必然在 `[start', end')` 原样存在 ⇒ 重编译时恰好唯一命中在选中处。
    """
    left, right = start, end
    while True:
        candidate = seg[left:right]
        if candidate and seg.count(candidate) == 1:
            return candidate
        if right < len(seg):
            right += 1
        elif left > 0:
            left -= 1
        else:
            return None                          # 整个区间都不唯一——不可能，防御性出口


def plan_repairs(artifact_raw: dict[str, Any]) \
        -> tuple[list[RepairAction], list[str]]:
    """从 `_provenance` 账重建逐拍失败序列 → 可字段级修的动作 + 整章级剩余清单。

    映射依据：`compile_v2` 逐拍按 hinge 顺序发失败行 ⇒ 该拍里 `source=null` 的
    hinge（按下标序）与该拍的失败行（按落账序）一一对应；歧义行再按序配
    `hinge_ambiguous_pending` 里的原短语。
    """
    provenance = artifact_raw.get('_provenance') or {}
    rows = [row for row in (provenance.get('verbatim_failures') or [])
            if len(row) >= 3]
    pending = list(provenance.get('hinge_ambiguous_pending') or [])
    by_beat: dict[str, list[str]] = {}
    for beat_id, where, cause in rows:
        if where == 'hinge':
            by_beat.setdefault(str(beat_id), []).append(str(cause))
    leftovers = [f'{row[0]}: {row[2]}' for row in rows if row[1] == 'span']

    actions: list[RepairAction] = []
    pending_queue = {  # beat_id -> 该拍歧义短语队列（按落账序）
        }
    for beat_id, kind, phrase in pending:
        pending_queue.setdefault(str(beat_id), []).append((str(kind), str(phrase)))

    for blueprint in artifact_raw.get('blueprints') or []:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        for beat in blueprint.get('beats') or []:
            beat_id = str(beat.get('beat_id') or '')
            causes = by_beat.get(beat_id)
            if not causes:
                continue
            if beat.get('span') is None:
                continue                        # cascade：span 自己坏了，归整章路
            failed = [i for i, h in enumerate(beat.get('hinges') or [])
                      if h.get('source') is None]
            for index, cause in zip(failed, causes):
                route = REPAIR_ROUTE.get(cause)
                kind = str((beat.get('hinges') or [])[index].get('kind') or '')
                if route != 'field_patch':
                    leftovers.append(f'{beat_id}: {cause}')
                    continue
                if cause == '短语在本拍区间内多处命中':
                    queue = pending_queue.get(beat_id) or []
                    phrase = queue.pop(0)[1] if queue else None
                    if phrase is None:
                        leftovers.append(
                            f'{beat_id}: 歧义行没有配对的 pending 短语（旧产物？）')
                        continue
                    actions.append(RepairAction(
                        'candidate_select', ordinal, beat_id, index, kind, phrase))
                else:                            # 短语不在本章原文里
                    actions.append(RepairAction(
                        'phrase_patch', ordinal, beat_id, index, kind, None))
    return actions, leftovers


def _patch_user(action: RepairAction, seg: str,
                candidates: list[dict[str, str]] | None) -> str:
    parts = [
        f'── 这一拍的原文（阅读序第{action.chapter}章 {action.beat_id}）──',
        seg,
        f'── 材料种类 ──\n{action.hinge_kind}',
    ]
    if candidates is not None:
        parts.append(
            '── 候选位置（同一短语在这一拍里出现了不止一次，选这条材料真正压着的那一处）──\n'
            + json.dumps({'原短语': action.phrase, 'candidates': candidates},
                         ensure_ascii=False, indent=1))
        parts.append('只输出 JSON：{"candidate_id": "<候选 id>"}')
    else:
        parts.append(
            '── 任务 ──\n上一版给的短语在这一拍的原文里找不到（是转述或编的）。'
            '从上面的原文里【原样照抄】一段能唯一定位、且这一幕分量真正压在上面的字，'
            '按材料种类的定义选。只输出 JSON：{"phrase": "原样照抄的那几个字"}')
    return '\n\n'.join(parts)


def run_field_patches(ctx: RunContext, client: LlmClient) -> Any:
    """`stage2.blueprint --patch` 的本体。修完走全量判据，绿则正式落盘。"""
    from novel.stages.stage2 import prompts
    from novel.stages.stage2.contract import CONTRACTS, contract_for
    from novel.stages.stage2.extract import (
        BLUEPRINT_REL, GLOBAL_REL, _INVALID, _Both, _blueprint_metrics,
        _hard_hits, _judge_candidate, _note_adopted, _read_main_text, _report,
        _write_formal, _write_invalid,
    )

    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing('stage1/main_text.json 不在盘上')
    source_rel = _INVALID if store.exists(_INVALID) else BLUEPRINT_REL
    if not store.exists(source_rel):
        raise UpstreamMissing(
            f'{BLUEPRINT_REL} 与 {_INVALID} 都不在盘上——没有可修复的蓝图')
    previous, previous_raw = store.read_own(
        ChapterBlueprints, source_rel, require_current=True)
    old_prov = previous.provenance
    # ⚠ 一律以**基线产物的** architecture 为准，不读 env、不写死名字：判「能不能修」
    #   与选「拿哪套构件去修」必须同源，这是 `contract.py::contract_for` 的原话
    #   「修复必须与基线同构」。
    #
    # ⚠⚠ **这里原来写死 `contract_for('extraction-ir-v1')`，而那个 `spec` 是死变量**
    #   ——赋值之后一次都没用过，编译器与投影函数是模块顶上直接 import 的
    #   `compile_v2` / `canonical_ir_projection` 硬调。所以写死并没有真的发错 prompt
    #   （patch 有自己的 `HINGE_*_SYS`，不发 blueprint 的 system），
    #   但它把「换契约 = 换整套」这条纪律在本文件里**断开了**：IR 系将来出现第二种
    #   编译器时，这里会静默地继续用旧的。⇒ 下面两处改走 `spec.*`，让它真正接上。
    baseline = old_prov.context.architecture
    if not contract_for(baseline).supports_field_patch:
        patchable = sorted(a for a, s in CONTRACTS.items() if s.supports_field_patch)
        raise TerminalState(
            f'--patch 只修 {"/".join(patchable)} 的字段级失败；基线是 '
            f'{baseline!r}——那个契约的失败面走 '
            '`--chapters` 整章重生成。尚未调用 LLM')
    if old_prov.model != client.model:
        raise TerminalState(
            '字段修复不能把不同主模型配置的结果混成一份来源账；'
            f'原稿 model={old_prov.model!r}，本次={client.model!r}。尚未调用 LLM')
    spec = contract_for(baseline)

    actions, leftovers = plan_repairs(previous_raw)
    if leftovers:
        raise TerminalState(
            f'有 {len(leftovers)} 条整章级根因，--patch 修不了（带着它们重编译会把'
            f' drift 的账错记成 void）：{leftovers[:5]}\n'
            f'→ 先跑 `stage2.blueprint --chapters <章>` 重生成那些章，再回来 --patch')
    if not actions:
        raise TerminalState(
            '账上没有可字段级修复的失败（歧义 / 编的短语都为 0）——没有要修的东西')

    global_raw = store.read(GlobalGrounding, GLOBAL_REL).model_dump(by_alias=True)
    text_of = {int(s.get('chapter_ordinal') or 0): str(s.get('text') or '')
               for s in main_text}
    spans_by_beat = {
        str(beat.get('beat_id') or ''):
            (int(bp.get('chapter_ordinal') or 0),
             beat.get('span') or {})
        for bp in previous_raw['blueprints']
        for beat in (bp.get('beats') or [])
    }

    # ── 投影出全书 IR（未动章精确往返），逐条问模型、改 IR ────────────────
    ir_by_ordinal: dict[int, dict[str, Any]] = {}
    summaries: dict[int, str] = {}
    for bp in previous_raw['blueprints']:
        ordinal = int(bp.get('chapter_ordinal') or 0)
        ir_by_ordinal[ordinal] = spec.projection_fn(
            copy.deepcopy(bp), text_of.get(ordinal, ''))
        summaries[ordinal] = str(bp.get('summary') or '')

    repair_rows: list[dict[str, Any]] = []
    prompt_keys: dict[str, str] = {}
    before_metrics = {
        int(bp.get('chapter_ordinal') or 0): _blueprint_metrics(bp)
        for bp in previous_raw['blueprints']}
    beat_index_of = {
        str(beat.get('beat_id') or ''): index
        for bp in previous_raw['blueprints']
        for index, beat in enumerate(bp.get('beats') or [])
    }
    for action in actions:
        ordinal, span = spans_by_beat[action.beat_id]
        seg = text_of.get(ordinal, '')[span.get('start', 0):span.get('end', 0)]
        # 投影保持拍序 1:1 ⇒ 产物里的拍下标就是 IR 里的拍下标。
        ir_beats = ir_by_ordinal[action.chapter]['beats']
        target = beat_index_of[action.beat_id]

        purpose = f'{ctx.node}:patch:{action.beat_id}.h{action.hinge_index}'
        if action.route == 'candidate_select':
            occs = _occurrences(seg, action.phrase or '')
            if len(occs) < 2:
                raise TerminalState(
                    f'{action.beat_id} 的歧义短语现在只剩 {len(occs)} 处命中——'
                    '账与正文对不上（正文被改过？），先 --renormalize 重验')
            candidates = [
                {'candidate_id': f'c{i}',
                 'context': ('…' + seg[max(0, s - _CONTEXT_CHARS):e + _CONTEXT_CHARS]
                             + '…')}
                for i, (s, e) in enumerate(occs)
            ]
            system = with_json_shape(prompts.HINGE_SELECT_SYS,
                                     prompts.HINGE_SELECT_HINT)
            prompt_keys['HINGE_SELECT_SYS'] = system
            valid_ids = {c['candidate_id'] for c in candidates}
            user = _patch_user(action, seg, candidates)
            response = client.ask_json(
                system, user,
                model_cls=HingeSelectResponse,
                purpose=purpose,
                validator=lambda obj, _r, _ids=valid_ids: (
                    None if obj.candidate_id in _ids
                    else f'candidate_id 不在候选里：{obj.candidate_id!r}'),
            )
            chosen = occs[int(response.candidate_id[1:])]
            new_phrase = disambiguate_phrase(seg, *chosen)
            if new_phrase is None:
                raise TerminalState(
                    f'{action.beat_id} 选中处无法扩展成拍内唯一——防御性出口，'
                    '不该发生；先 --chapters 整章重生成')
        else:
            system = with_json_shape(prompts.HINGE_PHRASE_SYS,
                                     prompts.HINGE_PHRASE_HINT)
            prompt_keys['HINGE_PHRASE_SYS'] = system
            user = _patch_user(action, seg, None)
            response = client.ask_json(
                system, user,
                model_cls=HingePhraseResponse,
                purpose=purpose,
                validator=lambda obj, _r, _seg=seg: (
                    None if len(_occurrences(_seg, obj.phrase)) == 1
                    else f'短语在这一拍里命中 '
                         f'{len(_occurrences(_seg, obj.phrase))} 处（要恰好 1 处）'),
            )
            new_phrase = response.phrase

        accepted_model = client.accepted_model(purpose)
        if accepted_model is None:
            raise ProvenanceRefused(
                f'{action.beat_id} 已返回字段修复，但没有 accepted model；'
                '拒绝按主模型配置估算')
        ir_beats[target]['hinges'][action.hinge_index]['phrase'] = new_phrase
        repair_rows.append({
            'chapter_ordinal': action.chapter,
            'failure_items': ['hinge_ambiguous'
                              if action.route == 'candidate_select'
                              else 'hinge_anchor_void'],
            'failure_evidence': prov.projection_fingerprint(
                {'beat_id': action.beat_id, 'hinge_index': action.hinge_index,
                 'route': action.route}),
            'user_prompt': prov.fingerprint(user),
            'output': prov.projection_fingerprint({'phrase': new_phrase}),
            'model': accepted_model,
            'cache_policy': ctx.cache.name,
            'fields': [f'beats[{action.beat_id}]'
                       f'.hinges[{action.hinge_index}].phrase'],
            'before_metrics': before_metrics[action.chapter],
            'after_metrics': before_metrics[action.chapter],   # 结构未变，账在下面重算
        })

    # ── 改完 IR → 全量重编译 → 全量判据 ──────────────────────────────────
    ordinals = sorted(ir_by_ordinal)
    data, accounting = spec.compile_fn(
        {'blueprints': [ir_by_ordinal[o] for o in ordinals]},
        main_text, global_raw['entities']['characters'], summaries=summaries)
    for row in repair_rows:
        row['after_metrics'] = _blueprint_metrics(
            next(bp for bp in data['blueprints']
                 if int(bp.get('chapter_ordinal') or 0) == row['chapter_ordinal']))
    # ⚠ `llm_provenance` 自己给 prompts 值算指纹 ⇒ delta 只喂**新臂的完整字节**，
    #   旧键（已是指纹）在最终合并时并入——把旧指纹再喂回去会被二次指纹。
    delta = ctx.llm_provenance(
        BlueprintProvenance,
        prompts=prompt_keys,
        model=client.model,
        adopted_models=client.accepted_models(),
        scope_violations=list(old_prov.scope_violations),
        batches=[list(batch) for batch in old_prov.batches],
        context=old_prov.context.model_dump(),
        partial_regen_chapters=list(old_prov.partial_regen_chapters),
        repairs=[row.model_dump() for row in old_prov.repairs] + repair_rows,
        **accounting,
    )
    provenance = BlueprintProvenance.model_validate({
        **delta.model_dump(),
        'prompts': {**old_prov.prompts, **delta.prompts},
        'adopted_models': sorted(set(old_prov.adopted_model_ids())
                                 | set(delta.adopted_model_ids())),
        'usage': combine_usage(old_prov.usage, delta.usage),
        'partial_rewrite': old_prov.partial_rewrite,
    })
    artifact = ChapterBlueprints.model_validate({
        'blueprints': data['blueprints'], '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(global_raw, candidate, main_text)
    hits = _hard_hits(outcomes)
    if hits:
        path = _write_invalid(store, BLUEPRINT_REL, artifact)
        raise GateBlocked(
            f'字段级修复后仍命中 {hits}。问题稿已存新一轮 .INVALID，'
            '正式产物没有被覆盖。剩余失败按判据的 fix 路由处理。',
            invalid_path=path)
    _write_formal(store, BLUEPRINT_REL, artifact)
    _note_adopted(
        ctx, client, rel=BLUEPRINT_REL, candidate=candidate,
        purposes={
            f'{ctx.node}:patch:{a.beat_id}.h{a.hinge_index}': a.chapter
            for a in actions})
    both = _Both(store)
    return _report(ctx, store, both, both.judge())
