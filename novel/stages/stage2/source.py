#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_compile —— Stage2 新链的确定性 compile 机械臂（F2b；v13 质量改进批次二）。

【职责】compiler：LLM wire（dict）→ 两份 canonical（Characters v4 / Blueprints
v13）。语义判断归抽取 LLM；本模块只做确定性转换、跨产物闭合校验与归一：
  • book wire 分流：characters+initial_state → v4；world_constraints → v13；
    canonical 必须逐字等于该人物某个 surface（称谓闭合的 wire 闸）；
  • 章级 wire → ChapterBlueprint：participants／认知持有者闭集、场景分段生成、
    命题与 loop 的本地标签解析为正式 ID 并闭合引用、登记键作用域绑定、因果边
    闭合验证（相邻顺序不能替代因果）、跨章 from_beat 存在性；
  • `source_fold`：initial_state 起底 + 逐章三类转移折叠 → 事实层（共用场景叠加
    原语，D16）＋认知层（按「命题＋持有者＋模式」的知情 fold）＋读者确认时点；
    每章空转移拍比例（D7）、书末未向读者揭示的 concealed 只读报告；章前文字
    （e00）的两条分流读数改为按持有者与命题核对（QP-07：旧实现只比较共享证据
    段，四条读数均属误报）。

【v13 归一】wire 里的命题／loop 本地标签在 compile 时解析为正式 ID（kNN／loopNN，
compiler 生成——I-21）：**新建**命题在 canonical 盘面保留 statement 文本，**引用**
只带正式 ID（重抄文本必须与登记文本逐字一致，否则拒绝）。跨章引用材料由
`proposition_registry()`／`open_loop_registry()` 渲染进后续章 prompt（D12）。

validator 不修改输入：wire dict 原样进、错误聚合成列表出（I-24/I-36）。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from contracts.schema.source import (
    NON_CHAR_HOLDERS, REGISTERED_STATE_KEYS, RETIRED_STATE_KEYS, Beat,
    BlueprintsArtifactV13, ChapterBlueprint, CharactersCore, SceneSpan,
    WorldConstraint,
)
from novel.stages.common import OverlayChange, StateOverlay
from pydantic import ValidationError

#: 认知模式中构成「确认时点」的模态：读者/人物只有 learns／confirms 算确认。
CONFIRMING_MODES: frozenset[str] = frozenset({'learns', 'confirms'})


def _slug_ok(slug: str) -> bool:
    import re
    return bool(re.fullmatch(r'[a-z0-9_]+', slug))


def _derive_from_chapter(surface: str, source_refs, segments: dict) -> tuple[int, str | None]:
    """称谓 from_chapter 的锚点派生（D12/I-15）：compiler 算，不让 LLM 重复生成。

    规则与实现唯一落在 `novel/stages/stage2/resolver.py::EvidenceResolver`
    （B2 起 Resolver 是 Stage2 全部证据定位的唯一实现，S-03：兼容/共用逻辑
    不散落）；本函数只保留旧 compile_book 的调用形状（(chapter, 问题串)）。
    """
    from novel.stages.stage2.resolver import EvidenceResolver
    chapter, issue = EvidenceResolver(segments).surface_chapter(surface, source_refs)
    return (chapter if chapter is not None else 0), (issue.message if issue else None)


def compile_book(
    wire: dict[str, Any], *, evidence_ids: Iterable[str],
) -> tuple[CharactersCore | None, list[Any] | None, list[str]]:
    """book wire → (Characters core view, world_constraints wire, errors)。

    core view（characters + initial_state）形状与 RC2 相同：Preserve/章节编译
    只消费它。发布 Characters 前由 `name_structure.merge_name_structure` 合并
    名称结构（F6）。world_constraints 由调用方并入 Blueprints v13。
    """
    errors: list[str] = []
    if set(wire) - {'characters', 'initial_state', 'world_constraints'}:
        errors.append('book wire 携带未知字段')
    evidence = set(evidence_ids)
    segments = dict(evidence_ids) if hasattr(evidence_ids, 'items') else {}
    raw_chars = wire.get('characters') or {}
    if not isinstance(raw_chars, dict) or not raw_chars:
        return None, None, ['characters 缺失或为空']
    for slug in raw_chars:
        if not _slug_ok(slug):
            errors.append(f'人物 slug 非法：{slug}')
    # 称谓 from_chapter 由证据锚点派生（D12/I-15）：compiler 归一化（不修改调用方
    # 的 wire），锚点段须逐字包含该 surface，章前锚点映射到第 1 章。
    raw_chars = deepcopy(raw_chars)
    for slug, c in raw_chars.items():
        for s in (c or {}).get('surfaces') or []:
            refs = s.get('source_refs') or []
            from_chapter, problem = _derive_from_chapter(s.get('surface'), refs, segments)
            if problem:
                errors.append(problem)
            else:
                s['from_chapter'] = from_chapter
    # canonical 必须是该人物登记过的某个 surface：canonical 是内部主键显示名，
    # 又作为身份名交给 Design／Hook；放一个没登记的字面会让它在替换表里找不到
    # 对应新名（F7e 评估实证）。wire 闸，不过就走 owner patch。
    for slug, c in raw_chars.items():
        surfaces = [s.get('surface') for s in (c or {}).get('surfaces') or []]
        canonical = (c or {}).get('canonical')
        if canonical is not None and canonical not in surfaces:
            errors.append(
                f'{slug} 的 canonical {canonical!r} 不是其 surface 之一'
                f'（canonical 必须逐字等于某个已登记 surface）')
    # 证据闭合（initial_state / world_constraints / surfaces）
    for slug, c in raw_chars.items():
        for s in (c or {}).get('surfaces') or []:
            for ev in s.get('source_refs') or []:
                if ev not in evidence:
                    errors.append(f'{slug} surface {s.get("surface")} source_ref 悬空：{ev}')
    for atom in wire.get('initial_state') or []:
        for ev in atom.get('source_refs') or []:
            if ev not in evidence:
                errors.append(f'initial_state {atom.get("state_id")} source_ref 悬空：{ev}')
        # 登记键闭集（D8）：场景作用域的位置键不进 initial_state——开场所在由
        # 第 1 章第一个 new 拍建立，故事开始前成立的长期居所用 residence。
        if REGISTERED_STATE_KEYS.get(atom.get('key')) == 'scene':
            errors.append(
                f'initial_state {atom.get("state_id")} 的键 {atom.get("key")} 是场景'
                '作用域：开场所在交给第 1 章第一个 new 拍；故事开始前成立的长期'
                '居所登记 residence')
    wcs = wire.get('world_constraints') or []
    for wc in wcs:
        for ev in wc.get('source_refs') or []:
            if ev not in evidence:
                errors.append(f'world_constraint {wc.get("constraint_id")} source_ref 悬空：{ev}')
    if errors:
        return None, None, errors
    try:
        characters = CharactersCore(
            characters=raw_chars,
            initial_state=wire.get('initial_state') or [])
    except ValidationError as exc:
        return None, None, [f'characters core 构造失败：{exc}']
    try:
        for wc in wcs:
            WorldConstraint.model_validate(wc)
    except ValidationError as exc:
        return None, None, [f'世界约束构造失败：{exc}']
    return characters, wcs, []


@dataclass
class _CompileState:
    """compile 的跨章登记表（试解析在副本上进行，整章通过才提交）。"""

    #: 命题登记：ref（wire 本地标签或正式 ID）→ {'id', 'statement', 'chapter'}
    propositions: dict[str, dict] = field(default_factory=dict)
    #: loop 登记：ref → {'id', 'question', 'open', 'chapter'}
    loops: dict[str, dict] = field(default_factory=dict)
    prop_seq: int = 0
    loop_seq: int = 0
    scene_seq: int = 0
    #: 当前开放场景（跨章延续；None＝没有可延续的场景）
    open_scene: str | None = None


class ChapterCompiler:
    """逐章 compile 的跨章状态：已登记 beat、命题、loop、场景与开放场景。"""

    def __init__(self, characters: CharactersCore, *, evidence_ids: Iterable[str]):
        self.chars = set(characters.characters)
        self.evidence = frozenset(evidence_ids)
        self.known_beats: set[str] = set()
        self._state = _CompileState()

    # ── 登记面（prompt 的跨章引用材料由此渲染） ──────────────────────────

    def proposition_registry(self) -> dict[str, str]:
        """正式 ID → 命题文本（后续章 prompt 的跨章引用清单）。"""
        return {ref: entry['statement'] for ref, entry in self._state.propositions.items()
                if ref == entry['id']}

    def open_loop_registry(self) -> dict[str, str]:
        """正式 ID → 未收束悬念的问题文本（后续章 prompt 的 loop 引用清单）。"""
        return {ref: entry['question'] for ref, entry in self._state.loops.items()
                if ref == entry['id'] and entry['open']}

    # ── 引用解析（wire 本地标签 → 正式 ID；只在副本上试解析） ─────────────

    def _normalize(self, wire: dict[str, Any], chapter: int,
                   state: _CompileState) -> tuple[dict[str, Any], list[str]]:
        """把 wire 里的命题／loop 本地标签解析为正式 ID（编译器归一，S2-3）。

        返回（归一后的 wire 副本, 错误列表）。原 wire 不被修改；解析过程中的
        登记只落在传入的 `state` 副本上，整章校验失败即丢弃。**新建**命题保留
        statement（canonical 需要它承载语义）；**引用**只留正式 ID，重抄文本必须
        与登记文本逐字一致（v12「同 key 同 statement」规则在此延续）。
        """
        normalized = deepcopy(wire)
        errors: list[str] = []

        def register(prop: dict, where: str) -> None:
            """新建命题：compiler 派正式 ID（I-21），canonical 保留 statement。"""
            ref, stmt = prop['ref'], prop['statement']
            if ref in state.propositions:
                errors.append(f'{where} 命题标签 {ref!r} 与已登记命题或正式 ID 重复')
                return
            state.prop_seq += 1
            formal = f'k{state.prop_seq:02d}'
            state.propositions[ref] = {'id': formal, 'statement': stmt,
                                       'chapter': chapter}
            state.propositions[formal] = state.propositions[ref]
            prop['ref'] = formal

        def reference(prop: dict, where: str) -> None:
            """引用既有命题（正式 ID 或已登记标签）：重抄文本必须逐字一致。"""
            entry = state.propositions.get(prop['ref'])
            if entry is None:
                errors.append(f'{where} 引用未登记命题 {prop["ref"]!r}')
                return
            if prop.get('statement') is not None and prop['statement'] != entry['statement']:
                errors.append(
                    f'{where} 复用命题 {entry["id"]} 时重抄的文本与登记不一致'
                    f'（登记「{entry["statement"]}」）')
                return
            prop['ref'] = entry['id']

        for raw in normalized.get('beats') or []:
            if not isinstance(raw, dict):
                continue
            for eff in raw.get('effects') or []:
                if not isinstance(eff, dict):
                    continue
                # 登记键作用域由 compiler 绑定（I-15：可推导坐标不让 LLM 声明）。
                # 表内键（scene_location／residence／身体状态键）的 scope 是契约
                # 固定事实，模型声明与表不符时归一化而非拒绝——真书实证模型系统性
                # 地把身体状态声明为 scene（v13 首跑 ch04／ch05 各 5 处）。
                registered = REGISTERED_STATE_KEYS.get(eff.get('key'))
                if registered is not None:
                    eff['scope'] = registered
            # 拍内两遍：先登记本拍新建（effect 的 concealed 命题、cognition 新建、
            # loop open），再解析全部引用——同一拍里「证实拍建事实效应并引用同一
            # 命题」（D5）因此合法，而跨拍/跨章的悬空引用仍被闭合闸拦下。
            propositions: list[tuple[dict, str]] = []
            for eff in raw.get('effects') or []:
                if isinstance(eff, dict) and isinstance(eff.get('proposition'), dict):
                    propositions.append((eff['proposition'],
                                         f'beat {raw.get("beat_id")} 的 effect'))
            for cog in raw.get('cognition') or []:
                if isinstance(cog, dict) and isinstance(cog.get('proposition'), dict):
                    propositions.append((cog['proposition'],
                                         f'beat {raw.get("beat_id")} 的 cognition'))
            registered: set[int] = set()
            for prop, where in propositions:
                if prop.get('statement') is not None and prop['ref'] not in state.propositions:
                    register(prop, where)
                    registered.add(id(prop))
            for loop in raw.get('loops') or []:
                if not isinstance(loop, dict):
                    continue
                ref, op = loop.get('ref'), loop.get('op')
                if op == 'open':
                    if ref in state.loops:
                        errors.append(
                            f'loop {ref!r} 重复 open（loop 只能 open 一次；已开放的'
                            '悬念之后只许 close）')
                        continue
                    state.loop_seq += 1
                    formal = f'loop{state.loop_seq:02d}'
                    state.loops[ref] = {'id': formal, 'question': loop.get('question'),
                                        'open': True, 'chapter': chapter}
                    state.loops[formal] = state.loops[ref]
                    loop['ref'] = formal
                else:
                    entry = state.loops.get(ref)
                    if entry is None:
                        errors.append(f'loop close 引用未登记的悬念 {ref!r}')
                        continue
                    if not entry['open']:
                        errors.append(f'loop {entry["id"]} 已 close，不得重复 close')
                        continue
                    entry['open'] = False
                    loop['ref'] = entry['id']
            for prop, where in propositions:
                if id(prop) not in registered:
                    reference(prop, where)
        return normalized, errors

    def validate(self, wire: dict[str, Any], chapter: int) -> list[str]:
        return deepcopy(self).compile(wire, chapter)[1]

    def compile(self, wire: dict[str, Any], chapter: int) -> tuple[ChapterBlueprint | None, list[str]]:
        trial = deepcopy(self._state)
        normalized, errors = self._normalize(wire, chapter, trial)
        if set(normalized) - {'beats', 'causal_edges'}:
            errors.append('chapter wire 携带未知字段')
        beats: list[Beat] = []
        #: 本章场景分段：scene_id → beat_ids（按拍序）；order 保持开场序。
        scenes: dict[str, list[str]] = {}
        scene_order: list[str] = []
        char_toward: list[tuple[str, str]] = []
        rel_pairs: list[tuple[str, str]] = []
        for raw in normalized.get('beats') or []:
            char_toward, rel_pairs = [], []
            for ev in raw.get('source_refs') or []:
                if ev not in self.evidence:
                    errors.append(f'source_ref 悬空：{ev}')
            for p in raw.get('participants') or []:
                if p not in self.chars:
                    errors.append(f'participants 引用未知人物 {p}')
            if not (raw.get('source_refs') or []):
                errors.append(f'beat {raw.get("beat_id")} 缺 source_refs')
            for eff in raw.get('effects') or []:
                t = eff.get('target') or {}
                for name in ('char_id', 'subject', 'object'):
                    if t.get(name) and t[name] not in self.chars:
                        errors.append(f'effect 引用未知人物 {t[name]}')
                if (eff.get('key') or '') in RETIRED_STATE_KEYS:
                    errors.append(
                        f'位置键 {eff.get("key")} 已退役：当前位置用 scene_location'
                        '（场景作用域）、长期居所用 residence（书作用域）、身体状态'
                        '各自一键，不得塞进同一个位置值')
                if t.get('kind') == 'relation':
                    pair = (t.get('subject'), t.get('object'))
                    rel_pairs.append(pair)
                if t.get('kind') == 'character' and '_toward_' in (eff.get('key') or ''):
                    char_toward.append((t.get('char_id'), eff['key']))
                disclosure = eff.get('disclosure') or {}
                for who in disclosure.get('known_by') or []:
                    if who not in self.chars:
                        errors.append(f'effect 可见性 known_by 引用未知人物 {who}')
            for cog in raw.get('cognition') or []:
                holder = cog.get('holder')
                if holder not in self.chars | NON_CHAR_HOLDERS:
                    errors.append(f'cognition holder 引用未知人物 {holder}')
            for loop in raw.get('loops') or []:
                if loop.get('op') not in ('open', 'close'):
                    errors.append(f'loop op 非法：{loop.get("op")}')
            try:
                beat = Beat.model_validate(raw)
            except (ValidationError, KeyError) as exc:
                errors.append(f'beat {raw.get("beat_id")} 构造失败：{exc}')
                continue
            beats.append(beat)
            m = _beat_no(beat.beat_id, chapter)
            if m is None:
                errors.append(f'beat_id {beat.beat_id} 不属于第 {chapter} 章')
            for cid, key in char_toward:
                for subject, obj in rel_pairs:
                    if cid in (subject, obj):
                        errors.append(f'同一语义变化双 owner：{key} 与 relation({subject},{obj}) 并存')
            # 场景分段（D1/D8）：new 开新场景（旧场景 scene 作用域状态失效）；
            # continue 延续当前开放场景（可跨章）。全书第一个拍必须 new。
            if beat.scene_boundary == 'new' or trial.open_scene is None:
                if beat.scene_boundary == 'continue' and trial.open_scene is None:
                    errors.append(
                        f'beat {beat.beat_id} 用 continue 但没有可延续的场景'
                        '（全书第一个拍必须 new）')
                trial.scene_seq += 1
                trial.open_scene = f'sc{trial.scene_seq:02d}'
                scene_order.append(trial.open_scene)
                scenes[trial.open_scene] = []
            elif trial.open_scene not in scenes:
                # 跨章延续：本章首次出现该开放场景。
                scene_order.append(trial.open_scene)
                scenes[trial.open_scene] = []
            scenes[trial.open_scene].append(beat.beat_id)
        # 因果不等于相邻顺序；全部验证并保留，不静默删除。
        edges = []
        for raw in normalized.get('causal_edges') or []:
            if raw.get('to_beat') not in {b.beat_id for b in beats}:
                if raw.get('to_beat') not in self.known_beats:
                    errors.append(f'causal to_beat 未知：{raw.get("to_beat")}')
                    continue
                errors.append(f'causal to_beat 指向历史章（from→to 方向反了）：{raw}')
                continue
            if raw.get('from_beat') not in self.known_beats | {b.beat_id for b in beats}:
                errors.append(f'causal from_beat 未知：{raw.get("from_beat")}')
                continue
            edges.append(raw)
        if errors:
            return None, errors
        try:
            chapter_bp = ChapterBlueprint(
                chapter=chapter, beats=tuple(beats),
                scenes=tuple(SceneSpan(scene_id=sid, beat_ids=tuple(scenes[sid]))
                             for sid in scene_order),
                causal_edges=tuple(_edge(r) for r in edges))
        except (ValidationError, KeyError) as exc:
            return None, [f'章蓝图构造失败：{exc}']
        # 登记跨章状态（整章通过才提交：引用解析的副作用随 trial 一起落账）
        self._state = trial
        for beat in beats:
            self.known_beats.add(beat.beat_id)
        return chapter_bp, []


def _edge(raw: dict[str, Any]):
    from contracts.schema.source import CausalEdge
    return CausalEdge.model_validate(raw)


def _beat_no(beat_id: str, chapter: int) -> int | None:
    import re
    m = re.fullmatch(rf'b{chapter:02d}_(\d+)', beat_id)
    return int(m.group(1)) if m else None


def assemble_blueprint(
    world_constraints: list[dict[str, Any]], chapters: Iterable[ChapterBlueprint],
) -> BlueprintsArtifactV13:
    from contracts.schema.source import WorldConstraint
    return BlueprintsArtifactV13(
        world_constraints=tuple(
            WorldConstraint(**wc) for wc in world_constraints),
        chapters=tuple(chapters))


# ───────────────────────── source fold（单一实现，F3 与逐章 prompt 共用） ─────────────────────────

def _target_dict(target) -> dict[str, Any]:
    d = target.model_dump() if hasattr(target, 'model_dump') else dict(target)
    return {k: v for k, v in d.items() if v is not None}


def _tkey(target: dict[str, Any]) -> tuple:
    kind = target.get('kind')
    if kind == 'character':
        return ('char', target.get('char_id'))
    if kind == 'relation':
        return ('rel', target.get('subject'), target.get('object'))
    return ('world',)


def _state_key(target: dict[str, Any], key: str) -> tuple:
    """事实层地址：领域＋作用对象＋键（与目标侧 StateAddress 同构的两段式）。"""
    return (_tkey(target), key)


def _render_entry(address: tuple, change: OverlayChange) -> str:
    """一条事实的渲染（D11：逐条带来源）。unset/unknown 显式表达，不与未建立混淆；
    scene 作用域标注换场失效。"""
    label = f'{address[0][0]}:{",".join(map(str, address[0][1:]))}.{address[1]}'
    suffix = change.source
    if change.scope == 'scene':
        suffix += '，换场失效'
    if change.op == 'unset':
        return f'{label} = unset（{suffix} 解除）'
    if change.op == 'unknown':
        return f'{label} = unknown（{suffix} 显式未知）'
    return f'{label} = {change.value!r}（{suffix} 建立）'


def _render_initial(atom) -> str:
    target = _target_dict(atom.target)
    label = f'{_tkey(target)[0]}:{",".join(map(str, _tkey(target)[1:]))}.{atom.key}'
    suffix = f'initial_state {atom.state_id} 建立'
    if atom.knowledge is not None:
        known = list(atom.knowledge.known_by) + (['reader'] if atom.knowledge.reader_knows else [])
        if known:
            suffix += f'；初始已知: {",".join(known)}'
    return f'{label} = {atom.value!r}（{suffix}）'


@dataclass
class FoldResult:
    """source_fold 的结构化输出（快照＋只读读数；不改输入）。"""

    snapshots: dict[int, list[str]]
    events: list[str]
    #: 每章读数：beats／空转移拍数／比例（D7）
    chapter_stats: dict[int, dict[str, Any]]
    #: 书末仍未向读者确认的 concealed 命题（只读报告）
    concealed_unrevealed: list[dict[str, Any]]


class _Folder:
    """三类转移的确定性折叠：事实层（共用叠加原语）＋认知层＋读者确认时点。"""

    def __init__(self, characters: CharactersCore):
        self.overlay = StateOverlay()
        #: 认知层：(命题正式 ID, 持有者) → {'mode', 'at'}
        self.knowledge: dict[tuple[str, str], dict[str, str]] = {}
        #: 读者确认时点：命题正式 ID → 首次确认的 beat（on_page 效应即确认）
        self.reader_confirmed: dict[str, str] = {}
        #: concealed 命题：正式 ID → {'established', 'known_by'}
        self.concealed: dict[str, dict[str, Any]] = {}
        #: 开放悬念：正式 ID → question
        self.open_loops: dict[str, str] = {}
        self.events: list[str] = []
        self.initial_values: dict[tuple, Any] = {}
        #: 当前值为 stable（initial_state 的 persistence=stable）的地址
        self.stable: set[tuple] = set()
        self.open_scene: str | None = None
        for atom in characters.initial_state:
            address = _state_key(_target_dict(atom.target), atom.key)
            self.initial_values[address] = deepcopy(atom.value)
            if atom.persistence == 'stable':
                self.stable.add(address)

    def seed(self, characters: CharactersCore) -> None:
        for atom in characters.initial_state:
            scope = REGISTERED_STATE_KEYS.get(atom.key, 'book')
            self.overlay.apply('__init__', [OverlayChange(
                address=_state_key(_target_dict(atom.target), atom.key),
                op='set', value=deepcopy(atom.value), scope=scope,
                source=f'initial_state {atom.state_id}')])

    def apply_beat(self, beat: Beat, scene_id: str, chapter: int,
                   pre: frozenset[str]) -> bool:
        """折叠一拍；返回该拍是否三类转移全空（D7 读数）。"""
        cites_pre = bool(pre) and any(ref in pre for ref in beat.source_refs)
        if cites_pre:
            # 读数一（与 stable 改写并存、互不排斥）：开场拍对初态同键 set 同值。
            for eff in beat.effects:
                if eff.op != 'set':
                    continue
                address = _state_key(_target_dict(eff.target), eff.key)
                if (address in self.initial_values
                        and self.initial_values[address] == deepcopy(eff.value)):
                    self.events.append(
                        f'[fold] 第{chapter}章开场拍 {beat.beat_id} 对初始状态同键 set 同值 '
                        f'{address}（同一事实两处 canonical 化？）')
        for eff in beat.effects:
            self._apply_effect(eff, beat, scene_id, chapter)
        for cog in beat.cognition:
            self._apply_cognition(cog, beat)
        for loop in beat.loops:
            self._apply_loop(loop, beat)
        return not (beat.effects or beat.cognition or beat.loops)

    def _apply_effect(self, eff, beat: Beat, scene_id: str, chapter: int) -> None:
        address = _state_key(_target_dict(eff.target), eff.key)
        scope = REGISTERED_STATE_KEYS.get(eff.key, eff.scope)
        if eff.op == 'set' and address in self.stable:
            self.events.append(f'[fold] 第{chapter}章 stable 事实被改写 {address}')
            self.stable.discard(address)
        value = deepcopy(eff.value)
        if eff.op in ('add', 'remove'):
            current = self.overlay.visible().get(address)
            if current is None or current.value is None:
                lst: list = []
            elif isinstance(current.value, list):
                lst = list(current.value)
            else:
                lst = [current.value]
            if eff.op == 'add' and eff.value not in lst:
                lst.append(eff.value)
            if eff.op == 'remove' and eff.value in lst:
                lst.remove(eff.value)
            value = lst
        self.overlay.apply(scene_id, [OverlayChange(
            address=address, op='set' if eff.op in ('add', 'remove') else eff.op,
            value=value, scope=scope, source=beat.beat_id)])
        if eff.proposition is not None:
            prop = eff.proposition.ref
            if eff.disclosure.visibility == 'concealed':
                # 隐藏事实：等后续 cognition（经同一命题）记录揭示；读者确认时点
                # 由 reader 持有者的 learns/confirms 落账。
                self.concealed.setdefault(prop, {
                    'established': beat.beat_id,
                    'known_by': list(eff.disclosure.known_by)})
            else:
                # 叙述明示的事实：读者在该拍即确认，无需等待 cognition。
                self.reader_confirmed.setdefault(prop, beat.beat_id)

    def _apply_cognition(self, cog, beat: Beat) -> None:
        prop = cog.proposition.ref
        self.knowledge[(prop, cog.holder)] = {'mode': cog.mode, 'at': beat.beat_id}
        if cog.holder == 'reader' and cog.mode in CONFIRMING_MODES:
            self.reader_confirmed.setdefault(prop, beat.beat_id)

    def _apply_loop(self, loop, beat: Beat) -> None:
        if loop.op == 'open':
            self.open_loops[loop.ref] = loop.question or ''
        else:
            self.open_loops.pop(loop.ref, None)

    def render(self) -> list[str]:
        lines = [_render_entry(address, change)
                 for address, change in sorted(self.overlay.visible().items(), key=str)]
        lines.extend(f'loop {ref} = {question!r}（open）'
                     for ref, question in sorted(self.open_loops.items()))
        for (prop, holder), entry in sorted(self.knowledge.items()):
            note = ('' if prop not in self.concealed or prop in self.reader_confirmed
                    else '（concealed 未向读者确认）')
            lines.append(f'prop {prop} 知情: {holder}={entry["mode"]}({entry["at"]}){note}')
        return lines


def initial_snapshot(characters: CharactersCore) -> list[str]:
    """第 1 章起点快照 = initial_state 渲染（source_fold 的章 0 部分）。"""
    return [_render_initial(atom) for atom in characters.initial_state]


def source_fold(
    characters: CharactersCore, blueprint: BlueprintsArtifactV13,
    *, pre_chapter_evidence: frozenset[str] = frozenset(),
    proposition_registry: dict[str, str] | None = None,
) -> FoldResult:
    """确定性折叠：事实层＋认知层＋读者确认时点，返回结构化只读读数。

    章内快照 = initial_state + 此前各章三类转移的累计结果（book 底值＋开放场景
    的 scene 覆盖，跨章场景延续到下一处 new 边界才失效）。**不修改输入**（I-24）。

    【章前文字（e00）的两条只读读数】`pre_chapter_evidence` 给出章前证据段 id
    （源引子切分）。引子按信息职责分流（开场前已成立 → initial_state；开场行动 →
    第 1 章 beats）有两条可机械侧写的面，**只报告、不阻断、不猜**：
      · 开场拍对初态同键 set 了同值（疑似同一事实两处 canonical 化）；
      · 初态已知者又在引用同一 e00 证据段的拍里被告知——按**持有者与命题**核对
        （QP-07：旧实现只比较共享证据段，不核对知情者与命题，四条读数均属误报）；
        报告同时给出 initial_state 条与命题 ID，是否同一事实留人工核读。
    """
    folder = _Folder(characters)
    folder.seed(characters)
    pre = frozenset(pre_chapter_evidence or ())
    initial_known = [atom for atom in characters.initial_state if atom.knowledge is not None]
    snapshots: dict[int, list[str]] = {0: initial_snapshot(characters)}
    chapter_stats: dict[int, dict[str, Any]] = {}
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        scene_of = {bid: span.scene_id for span in chapter_bp.scenes for bid in span.beat_ids}
        empty = 0
        for beat in chapter_bp.beats:
            scene_id = scene_of[beat.beat_id]
            if scene_id != folder.open_scene:
                # 新场景开场：旧场景的 scene 作用域状态失效（D16 原语）。
                if folder.open_scene is not None:
                    folder.overlay.expire_scene_scope(folder.open_scene)
                folder.open_scene = scene_id
            cites_pre = bool(pre) and any(ref in pre for ref in beat.source_refs)
            if cites_pre:
                for cog in beat.cognition:
                    if cog.mode not in CONFIRMING_MODES:
                        continue
                    for atom in initial_known:
                        shared = sorted(set(atom.source_refs) & set(beat.source_refs) & pre)
                        if not shared:
                            continue
                        already = (cog.holder in atom.knowledge.known_by
                                   or (cog.holder == 'reader' and atom.knowledge.reader_knows))
                        if not already:
                            continue
                        folder.events.append(
                            f'[fold] 第{chapter_bp.chapter}章开场拍 {beat.beat_id} 又把初始'
                            f'已知 {atom.state_id}（known_by 含 {cog.holder}）告知，命题 '
                            f'{cog.proposition.ref} 模式 {cog.mode}（证据段 {shared}；'
                            '是否同一事实待人工核读）')
            if folder.apply_beat(beat, scene_id, chapter_bp.chapter, pre):
                empty += 1
        snapshots[chapter_bp.chapter] = folder.render()
        total = len(chapter_bp.beats)
        chapter_stats[chapter_bp.chapter] = {
            'beats': total, 'empty_transition_beats': empty,
            'empty_ratio': round(empty / total, 3) if total else 0.0}
    registry = proposition_registry or {}
    concealed_unrevealed = [
        {'proposition': prop, 'statement': registry.get(prop),
         'established': entry['established'], 'known_by': entry['known_by']}
        for prop, entry in sorted(folder.concealed.items())
        if prop not in folder.reader_confirmed]
    return FoldResult(snapshots=snapshots, events=folder.events,
                      chapter_stats=chapter_stats,
                      concealed_unrevealed=concealed_unrevealed)


def chapter_input_snapshot(characters: CharactersCore,
                           blueprint: BlueprintsArtifactV13, next_chapter: int) -> list[str]:
    """严格消费连续前缀，禁止取不到章快照时退回初始态。"""
    ordinals = [c.chapter for c in blueprint.chapters]
    if ordinals != list(range(1, next_chapter)):
        raise ValueError(f'第 {next_chapter} 章输入需要连续的 1..{next_chapter - 1} 章前缀')
    return source_fold(characters, blueprint).snapshots[next_chapter - 1]
