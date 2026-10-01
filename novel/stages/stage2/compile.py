#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile —— `str06.stage2.compile` 的确定性编译 owner 引擎（B2；D8/D9）。

【职责】观察 → canonical 的唯一发布点（S2-5）：book／chapters／names／associate
四份观察 + 证据句段 → Characters（`stage2/characters/characters.json`）＋
Blueprint（`stage2/blueprints/chapter_blueprints.json`）＋ fold 读数。
  • 解析证据：人物称谓窗口走 Resolver 唯一实现（`R-2.10`）；participants／认知
    holder／effect target／known_by 对书级人物闭集核对（`R-2.3` 归 compile）；
  • 生成 ID：拍号（``b{章:02d}_{序}``）、命题（kNN）与悬念（loopNN）正式 ID、
    场景（scNN）、初态（s0_*）与世界约束（wc_*）编号——编号归 compiler（I-21），
    人物 slug 仍由模型给出；
  • 执行关联（D8）：命题/悬念的跨章同一性按配对授予同一正式 ID（并查集，链式
    配对合法）；状态近义键按同维配对归一到同一地址（canonical key＝最早确立
    者）；**未决项拒绝发布并按 item 报告**——悬空的跨章命题引用、未配对的跨章
    悬念 close、无任何 statement 的命题类、时序倒转、作用对象不一致、重复
    close、续场配对越位。不默认合并，也不默认不同；
  • 源 fold（`R-2.17`）：只消费 canonical transitions——事实层（共用场景叠加
    原语，D16）＋认知层（按「命题＋持有者＋模式」的知情 fold）＋读者确认时点；
    未揭示 concealed、每章空转移拍比例、章前文字两条分流读数全部是**度量，
    不是门**。

【边界】validator 不修改输入（观察 wire 只读）；本模块的全部改写都发生在
compiler 自己的副本上（归一是 compile 的职责本身，I-24 不拦 compiler）。
语义判断归付费 semantic unit；配得对不对保持 unver 披露，机械检查不冒充证明。
`chapter_input_snapshot`（逐章输入快照）不随迁——D3 起章观察互不依赖，快照
退役；旧引擎 `source.py` 已随 B5 整包删除（S-05），新链没有这个产物面。
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.observation import (
    AssociateObservation, BookObservation, ChapterObservation, NamesObservation,
)
from contracts.schema.source import (
    NON_CHAR_HOLDERS, REGISTERED_STATE_KEYS, RETIRED_STATE_KEYS,
    Beat, BlueprintsArtifactV15, ChapterBlueprint, CharactersArtifactV4,
    CharactersCore, CausalEdge, Effect, InitialStateAtom, SceneSpan,
    WorldConstraint,
)
from novel.stages.stage2.resolver import EvidenceResolver, verify_bases
from novel.stages.common import OverlayChange, StateOverlay
from novel.stages.stage2.associate import (
    AssociationSpace, book_state_handle, chapter_state_handle,
    loop_pairing_issues, project_association_space, serialize_target, target_label,
)
from novel.stages.stage2.book import character_core_view
from novel.stages.stage2.expression import compile_source_expression

#: 认知模式中构成「确认时点」的模态：读者/人物只有 learns／confirms 算确认。
CONFIRMING_MODES: frozenset[str] = frozenset({'learns', 'confirms'})

_BEAT_HANDLE = re.compile(r'ch(\d+)\.b(\d+)')
_SCENE_HANDLE = re.compile(r'ch(\d+)\.sc(\d+)')


# ───────────────────────── 并查集（配对等价类） ─────────────────────────

class _Union:
    """句柄等价类：只建立「同一」断言的传递闭包，不猜、不补边。

    代表取成员里的最小序（`_rank`：书级在前、章序与出现序升序）——canonical
    代表是确定性的，与配对书写顺序无关。
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, tuple] = {}

    def add(self, item: str, rank: tuple | None = None) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = rank if rank is not None else (99, 0, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # 路径压缩
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, left_rank: tuple,
              right: str, right_rank: tuple) -> None:
        self.add(left, left_rank)
        self.add(right, right_rank)
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            keep, drop = sorted((left_root, right_root), key=lambda x: self._rank[x])
            self._parent[drop] = keep

    def __contains__(self, item: str) -> bool:
        return item in self._parent

    def members(self, root: str) -> list[str]:
        return [item for item in self._parent if self.find(item) == root]


def _state_rank(handle: str, space: AssociationSpace) -> tuple:
    """状态句柄的确定性序（E-5 同步面）：book（chapter=0）在前，其后按
    （章，拍，效应序）。

    序取自**唯一关联登记表**的 typed 字段（`StateHandle`），不做第二处字符串
    解析——句柄形状改版时本函数不依赖正则，不会静默退化成 fallback rank。
    不在登记表的句柄（陈旧 wire 句柄）排最末，永不成为 canonical。
    """
    entry = space.states.get(handle)
    if entry is None:
        return (99, 0, 0, handle)
    return (entry.chapter, entry.beat_ordinal, entry.effect_ordinal, handle)


# ───────────────────────── 编译结果 ─────────────────────────

@dataclass
class CompileOutcome:
    """compile_source_truth 的结构化结果：可发布时三份产物齐全，否则未决/错误。"""

    characters: CharactersArtifactV4 | None = None
    blueprint: BlueprintsArtifactV15 | None = None
    fold: dict[str, Any] = field(default_factory=dict)
    #: D8 未决项（按 item 定位）：存在即拒绝发布，恢复走 owner patch 或 associate
    #: 定向修订——compile 不替 semantic unit 拿主意。
    undecided: list[str] = field(default_factory=list)
    #: 机械缺陷（形状/闭集/闭合）：确定性缺陷修代码或 owner patch，不重购。
    errors: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return not self.undecided and not self.errors


def _state_address(target: Any, key: str) -> tuple:
    """事实层地址：领域＋作用对象＋键（与目标侧 StateAddress 同构的两段式）。"""
    data = target.model_dump() if hasattr(target, 'model_dump') else dict(target)
    data = {k: v for k, v in data.items() if v is not None}
    kind = data.get('kind')
    if kind == 'character':
        head: tuple = ('char', data.get('char_id'))
    elif kind == 'relation':
        head = ('rel', data.get('subject'), data.get('object'))
    else:
        head = ('world',)
    return (head, key)


class _StateClasses:
    """状态维度等价类（同维合并，I-15）：canonical key＝类内最早确立者的键。

    作用对象一致性在登记时核对：同类成员目标不同即未决冲突——不同对象的
    「同一维度」不成立，不默认合并（含反向关系：主客体方向区分对象）。
    确定性序查**唯一关联登记表**（`space`；R1 v2 位置编码）。
    """

    def __init__(self, associate: AssociateObservation,
                 space: AssociationSpace) -> None:
        self._space = space
        self._union = _Union()
        for pair in associate.same_state_dimension:
            self._union.union(pair.left, _state_rank(pair.left, space),
                              pair.right, _state_rank(pair.right, space))
        self._targets: dict[str, dict[str, Any]] = {}
        self._canonical: dict[str, str] = {}
        self._rank_of: dict[str, str] = {}

    def register(self, handle: str, target: Mapping[str, Any], key: str) -> None:
        root = self._union.find(handle)
        self._targets.setdefault(root, dict(target))
        # canonical key＝类内最早确立者（rank 序）；键本身的字典序不作依据。
        current = self._canonical.get(root)
        if current is None or _state_rank(handle, self._space) < _state_rank(
                self._rank_of[root], self._space):
            self._canonical[root] = key
            self._rank_of[root] = handle

    def canonical_key(self, handle: str, target: Mapping[str, Any], key: str,
                      where: str, undecided: list[str]) -> str:
        """同维合并：返回该句柄的 canonical key；目标不同即未决冲突。"""
        if handle not in self._union:
            return key
        root = self._union.find(handle)
        expected = self._targets.get(root)
        if expected is not None and expected and dict(target) != expected:
            undecided.append(
                f'{where} 的同维配对作用对象不一致（类内已有 '
                f'{target_label(expected)}，此处 {target_label(target)}）——'
                '不同对象不是同一维度，保持未决')
            return key
        self.register(handle, target, key)
        return self._canonical.get(root, key)


class _BookCompile:
    """跨章编译状态：命题/悬念正式 ID、等价类、续场与跨章因果。

    R1 v2：持有**唯一关联登记表**（`space`）——状态句柄定位与确定性序都查表，
    不在本模块重写编码（E-5：`_state_rank`／初态句柄／章效应句柄同批对齐）。
    """

    def __init__(self, *, slugs: set[str], associate: AssociateObservation,
                 space: AssociationSpace) -> None:
        self.slugs = slugs
        self.space = space
        self.prop_seq = 0
        self.loop_seq = 0
        self.scene_seq = 0
        self.open_scene: str | None = None
        #: 命题：限定句柄（ch{k}.{ref}）→ {'id', 'chapter', 'statement'}
        self.propositions: dict[str, dict[str, Any]] = {}
        #: 等价类代表 → 正式 ID／canonical statement／确立章。
        self.class_formal: dict[str, str] = {}
        self.class_statement: dict[str, str] = {}
        self.formal_chapter: dict[str, int] = {}
        self.prop_union = _Union()
        for pair in associate.same_proposition:
            self.prop_union.union(pair.left, (0, 0, pair.left),
                                  pair.right, (0, 0, pair.right))
        #: 悬念：限定句柄 → formal id；formal id → 'open'｜'closed'；配对映射。
        self.loop_ids: dict[str, str] = {}
        self.loop_state: dict[str, str] = {}
        self.loop_pairing: dict[str, str] = {}
        for pair in associate.loop_pairing:
            self.loop_pairing[pair.close] = pair.open
        #: 续场配对（右 → 左）与占用核对；跨章因果原样暂存。
        #: 右端重复不静默丢弃：留到应用期按 item 报未决（一个场景不能续两个前场景）。
        self.scene_pairing: dict[str, str] = {}
        self.duplicate_continuation_rights: list[tuple[str, str]] = []
        self._seen_continuation_rights: set[str] = set()
        for pair in associate.scene_continuation:
            if pair.right in self.scene_pairing:
                self.duplicate_continuation_rights.append((pair.left, pair.right))
                continue
            self.scene_pairing[pair.right] = pair.left
        self.causes = list(associate.cross_chapter_cause)
        #: 悬空的跨章命题引用：解析推迟到全部章建立之后（可判定时序倒转）。
        self.pending_props: list[tuple[dict, str, str]] = []
        #: 状态维度等价类。
        self.state_classes = _StateClasses(associate, space)

    # ── 命题登记与引用解析 ────────────────────────────────────────────────

    def establish(self, handle: str, statement: str, chapter: int) -> str:
        """新建命题：授正式 ID，或并入已建立的等价类（同义命题合并，D8）。

        合并不改写文本：canonical statement＝最早确立者；后来者的转述文本随
        配对被吸收（模型的同一性断言在先，compiler 只做确定性选择，I-15/I-21）。
        """
        root = self.prop_union.find(handle)
        formal = self.class_formal.get(root)
        if formal is None:
            self.prop_seq += 1
            formal = f'k{self.prop_seq:02d}'
            self.class_formal[root] = formal
            self.class_statement[root] = statement
            self.formal_chapter[formal] = chapter
        self.propositions[handle] = {'id': formal, 'chapter': chapter,
                                     'statement': self.class_statement[root]}
        return formal

    def restatement(self, handle: str, statement: str, where: str,
                    errors: list[str]) -> str | None:
        """同章句柄重抄 statement：必须与登记逐字一致（文本只建立一次）。"""
        entry = self.propositions.get(handle)
        if entry is None:
            return None
        if statement != entry['statement']:
            errors.append(
                f'{where} 重抄命题 {handle!r} 的文本与登记不一致'
                f'（登记「{entry["statement"]}」）——同一命题的文本只建立一次')
        return entry['id']

    def defer(self, prop: dict, handle: str, where: str) -> None:
        """悬空引用推迟到全章建立后解析（届时才能区分缺项与时序倒转）。"""
        self.pending_props.append((prop, handle, where))

    def resolve_pending(self, undecided: list[str]) -> None:
        """解析全部悬空引用：配对到已建立等价类 → 同 ID；否则按 item 未决。"""
        for prop, handle, where in self.pending_props:
            # 成员资格必须在 find 之前问：find 会把句柄补进并查集（防自指），
            # 「先 find 再 in」永远为真，缺项与时序两种未决就分不开了。
            paired = handle in self.prop_union
            root = self.prop_union.find(handle)
            formal = self.class_formal.get(root)
            if formal is None:
                if paired:
                    undecided.append(
                        f'{where} 的命题引用 {handle!r} 的等价类没有任何已登记 '
                        'statement（无 canonical 文本，不猜）')
                else:
                    undecided.append(
                        f'{where} 的命题引用 {handle!r} 是悬空的跨章引用且没有配对'
                        '（缺项保持未决：恢复走 owner patch 或 associate 定向修订）')
                continue
            established = self.formal_chapter.get(formal)
            reference_chapter = int(handle[2:handle.index('.')]) if handle.startswith('ch') else 0
            if established is not None and established > reference_chapter:
                undecided.append(
                    f'{where} 的命题引用 {handle!r} 指向第 {established} 章才确立的'
                    '命题（时序倒转，不默认前移）')
                continue
            self.propositions[handle] = {
                'id': formal, 'chapter': reference_chapter,
                'statement': self.class_statement[root]}
            prop['ref'] = formal


# ───────────────────────── 主编译 ─────────────────────────


def first_appearance_anchors(characters) -> dict[str, int | None]:
    """从同一 character_core_view／canonical 称谓面派生最早证据锚点。"""
    return {cid: min((s.from_chapter for s in entry.surfaces), default=None)
            for cid, entry in characters.characters.items()}


def first_appearance_issues(
    earliest_anchor: Mapping[str, int | None], *, chapter: int, beats,
) -> list[str]:
    """同一道首现闸用于单章接受及最终 compile；无锚点保持未判。"""
    issues: list[str] = []
    for beat in beats:
        refs = set(beat.participants) | {c.holder for c in beat.cognition if c.holder}
        for char_id in sorted(refs):
            anchor = earliest_anchor.get(char_id)
            if anchor is not None and chapter < anchor:
                issues.append(
                    f'participant_before_first_appearance[{char_id}@ch{chapter:02d}]：'
                    f'{char_id} 在第 {chapter} 章作为参与者/认知持有者'
                    f'出场，但其最早称谓锚点在第 {anchor} 章——首现早于任何'
                    '称谓证据（QP-01 同类：观察把既有人物误绑到晚出场角色）；'
                    '恢复走该章观察重问或 owner patch')
    return issues



def _evidence_windows(
    evidence: Mapping[str, Mapping[str, Any]], cited: Iterable[str],
) -> dict[str, str]:
    """R10a a1：被引段 → 证据窗口（段全文＋同章前后各一段全文，机械拼接）。

    窗口只收被任一 basis／source_refs 引用的段（I-16 派生物；精确支持引用
    在条目 basis 里，邻句送达≠邻句为证据）。同章邻段按段号排序取前后各一；
    段不在证据面（书级引用已由 owner 闸与防御复核拦截）跳过——防御在前，
    此处不重复报错。
    """
    import re as _re
    by_chapter: dict[int, list[tuple[int, str]]] = {}
    for ref, seg in evidence.items():
        m = _re.fullmatch(r'e(\d+)_(\d+)', ref)
        if m is None:
            continue
        by_chapter.setdefault(int(m.group(1)), []).append((int(m.group(2)), ref))
    for refs in by_chapter.values():
        refs.sort()
    windows: dict[str, str] = {}
    for ref in sorted(cited):
        seg = evidence.get(ref)
        if seg is None:
            continue
        m = _re.fullmatch(r'e(\d+)_(\d+)', ref)
        same = by_chapter.get(int(m.group(1)), []) if m else []
        idx = next((i for i, (_no, r) in enumerate(same) if r == ref), None)
        parts: list[str] = []
        if idx is None:
            parts.append(seg.get('text') or '')
        else:
            for j in (idx - 1, idx, idx + 1):
                if 0 <= j < len(same):
                    parts.append(evidence[same[j][1]].get('text') or '')
        windows[ref] = '\n'.join(part for part in parts if part)
    return windows


def compile_source_truth(
    *, book: BookObservation, chapters: Mapping[int, ChapterObservation],
    names: NamesObservation, associate: AssociateObservation,
    evidence: Mapping[str, Mapping[str, Any]],
    source_texts: tuple[str, ...] | None = None,
) -> CompileOutcome:
    """四份观察 → (CharactersArtifactV4, BlueprintsArtifactV15, fold 读数)。

    未决项不发布（D8）；机械缺陷不发布（I-34）；两份真源由同一结果一次成形，
    落盘仍由 runner 经 repository 的原子写与来源账完成（I-25 唯一 writer）。
    """
    outcome = CompileOutcome()
    errors = outcome.errors
    undecided = outcome.undecided
    ordinals = sorted(chapters)
    if ordinals != list(range(1, len(ordinals) + 1)):
        errors.append(f'章观察必须从 1 连续，实得 {ordinals}')
        return outcome

    # ⓪ R10a a1 防御复核：全书 basis 引文（owner 验收已拦；发布前 fail fast
    # 再核一遍，I-34）。同时收集被引段集合，供 ⑥ 的证据窗口派生。
    defense: list[tuple[str, str, str, tuple[str, ...]]] = []
    cited: set[str] = set()

    def _cite(refs: Iterable[str]) -> None:
        cited.update(refs)

    for atom in book.initial_state:
        _cite(atom.source_refs)
        for basis in atom.basis:
            defense.append((f'initial_state {atom.key}', basis.segment,
                            basis.quote, atom.source_refs))
            cited.add(basis.segment)
    for rule in book.world_constraints:
        _cite(rule.source_refs)
        for basis in rule.basis:
            defense.append((f'world_constraint {rule.statement[:16]}',
                            basis.segment, basis.quote, rule.source_refs))
            cited.add(basis.segment)
    for n, obs in chapters.items():
        for ordinal, beat in enumerate(obs.beats, 1):
            _cite(beat.source_refs)
            for kind in ('effects', 'cognition'):
                for index, transfer in enumerate(getattr(beat, kind), 1):
                    for basis in transfer.basis:
                        defense.append((f'第{n}章拍{ordinal} {kind}[{index}]',
                                        basis.segment, basis.quote,
                                        beat.source_refs))
                        cited.add(basis.segment)
    for where, issue in verify_bases(EvidenceResolver(evidence), defense):
        errors.append(f'{where}（compile 防御复核）：{issue.message}')
    if errors:
        return outcome

    # ① Characters core（称谓窗口走 Resolver 唯一实现；书级闸已在 book owner 聚合）。
    core, core_errors = character_core_view(book, evidence=evidence)
    errors.extend(core_errors)
    if core is None:
        return outcome
    # 唯一关联登记表（R1 v2）：与 run_associate 的 prompt／闭集核对同一投影——
    # 状态句柄定位与确定性序都查表，不在 compile 重写编码。
    space = project_association_space(book, chapters)
    undecided.extend(loop_pairing_issues(associate, space))
    if undecided:
        return outcome
    state = _BookCompile(slugs=set(core.characters), associate=associate, space=space)

    # ② 初态：生成 s0_* 编号；同维配对可把初态键归一到类内 canonical key。
    initial_atoms: list[InitialStateAtom] = []
    for index, atom in enumerate(book.initial_state, 1):
        target = serialize_target(atom.target, where=f'initial_state {atom.key}')
        handle = book_state_handle(index, atom.key)
        if handle not in space.states:
            errors.append(
                f'初态句柄 {handle!r} 不在关联登记表内（投影与编译位置编码漂移）')
            key = atom.key
        else:
            key = state.state_classes.canonical_key(
                handle, target, atom.key, f'initial_state {atom.key}', undecided)
        initial_atoms.append(InitialStateAtom(
            state_id=f's0_{index:02d}', target=atom.target, key=key, value=atom.value,
            persistence=atom.persistence, knowledge=atom.knowledge,
            source_refs=atom.source_refs, basis=atom.basis))

    # ③ 逐章编译：拍号派发、命题/悬念解析、同维合并、场景分段、人物闭集。
    compiled: list[tuple[int, list[dict[str, Any]], dict[str, list[str]], list[str]]] = []
    for chapter in ordinals:
        compiled_chapter = _compile_chapter(chapter, chapters[chapter], state,
                                            undecided, errors)
        if compiled_chapter is not None:
            compiled.append(compiled_chapter)
    if not compiled:
        return outcome
    # 悬空引用的全书解析必须先于 Beat 模型构造（placeholder 落模型就锁死了）。
    state.resolve_pending(undecided)
    chapter_blueprints: list[ChapterBlueprint] = []
    for chapter, beat_dicts, scenes, scene_order in compiled:
        try:
            chapter_blueprints.append(ChapterBlueprint(
                chapter=chapter,
                beats=tuple(Beat.model_validate(beat) for beat in beat_dicts),
                scenes=tuple(SceneSpan(scene_id=sid, beat_ids=tuple(scenes[sid]))
                             for sid in scene_order),
                causal_edges=()))
        except ValidationError as exc:
            errors.append(f'第{chapter}章蓝图构造失败：{exc}')
    if not chapter_blueprints:
        return outcome

    # ④ 跨章因果（D8）：边落到 to 拍所在章；方向倒转按 item 未决。
    chapter_blueprints = _apply_cross_chapter_causes(
        chapter_blueprints, state, undecided)

    # ⑤ 续场合并（D8）：右端场景并入左端类（改 scene_id，拍序与分章不动）。
    chapter_blueprints = _apply_scene_continuations(chapter_blueprints, state,
                                                    undecided)

    # ⑥ 世界约束编号 + 双真源成形。
    world_constraints = [
        WorldConstraint(constraint_id=f'wc_{index:02d}', kind=rule.kind,
                        statement=rule.statement, source_refs=rule.source_refs,
                        basis=rule.basis)
        for index, rule in enumerate(book.world_constraints, 1)]
    try:
        blueprint = BlueprintsArtifactV15(
            source_expression=compile_source_expression(book.expression, texts=source_texts),
            world_constraints=world_constraints, chapters=tuple(chapter_blueprints),
            evidence=_evidence_windows(evidence, cited))
    except ValidationError as exc:
        errors.append(f'blueprint 构造失败：{exc}')
        return outcome
    merged, merge_errors = _merge_characters(core, initial_atoms, names)
    errors.extend(merge_errors)
    if merged is None:
        return outcome

    # ⑦ 源 fold（R-2.17：只消费 canonical transitions；度量不是门）。
    statements: dict[str, str] = {}
    for entry in state.propositions.values():
        statements.setdefault(entry['id'], entry['statement'])
    fold = source_fold(
        merged.core_view(), blueprint,
        pre_chapter_evidence=frozenset(
            ref for ref, seg in evidence.items() if seg.get('pre_chapter')),
        proposition_registry=statements)
    anchors = first_appearance_anchors(merged)
    for chapter in blueprint.chapters:
        errors.extend(first_appearance_issues(anchors, chapter=chapter.chapter, beats=chapter.beats))
    outcome.characters = merged
    outcome.blueprint = blueprint
    outcome.fold = {
        'snapshots': fold.snapshots,
        'events': fold.events,
        'chapters': {str(chapter): stats
                     for chapter, stats in fold.chapter_stats.items()},
        'concealed_unrevealed': fold.concealed_unrevealed,
        'evidence': evidence_metrics(blueprint, merged.core_view()),
    }
    return outcome


def _merge_characters(
    core: CharactersCore, initial_atoms: list[InitialStateAtom],
    names: NamesObservation,
) -> tuple[CharactersArtifactV4 | None, list[str]]:
    """core + 初态 + 名称结构 → CharactersArtifactV4（发布前唯一合并点）。"""
    from novel.stages.stage2.names import merge_name_structure
    payload = {'characters': {slug: entry.model_dump()
                              for slug, entry in core.characters.items()},
               'initial_state': [atom.model_dump() for atom in initial_atoms],
               'name_structure': names.structure.model_dump()}
    try:
        return CharactersArtifactV4.model_validate(payload), []
    except ValidationError as exc:
        return None, [f'characters v4 合并名称结构失败：{exc}']


def _compile_chapter(
    chapter: int, observation: ChapterObservation, state: _BookCompile,
    undecided: list[str], errors: list[str],
) -> tuple[int, list[dict[str, Any]], dict[str, list[str]], list[str]] | None:
    """单章观察 → (章号, 拍 dict 列表, 场景表, 场景开场序)。

    拍保持 dict 形态返回：跨章悬空引用的全书解析（`resolve_pending`）发生在
    Beat 模型构造**之前**——模型一旦成形，placeholder 就锁死在 canonical 里。
    拍号派发、命题/悬念解析、场景分段与人物闭集在本函数完成。
    """
    beat_dicts: list[dict[str, Any]] = []
    scenes: dict[str, list[str]] = {}
    scene_order: list[str] = []
    chapter_loops: dict[str, str] = {}  # 本章已 open 的本地 ref → formal id
    for ordinal, raw_beat in enumerate(observation.beats, 1):
        beat = deepcopy(raw_beat.model_dump())
        # 拍号由 compiler 按列表序派发（I-21）；零投影两位与历史证据样式一致。
        beat['beat_id'] = f'b{chapter:02d}_{ordinal:02d}'
        where = f'第{chapter}章拍{ordinal}'
        # —— 人物闭集（R-2.3 归 compile：participants/target/known_by/holder）——
        for person in beat.get('participants') or []:
            if person not in state.slugs:
                errors.append(f'{where} participants 引用未知人物 {person}')
        for effect in beat.get('effects') or []:
            target = effect.get('target') or {}
            for field_name in ('char_id', 'subject', 'object'):
                if target.get(field_name) and target[field_name] not in state.slugs:
                    errors.append(
                        f'{where} effect 引用未知人物 {target[field_name]}')
            if (effect.get('key') or '') in RETIRED_STATE_KEYS:
                errors.append(f'{where} 位置键 {effect.get("key")} 已退役（观察闸漏拦）')
            for who in (effect.get('disclosure') or {}).get('known_by') or []:
                if who not in state.slugs:
                    errors.append(
                        f'{where} effect 可见性 known_by 引用未知人物 {who}')
        for cog in beat.get('cognition') or []:
            holder = cog.get('holder')
            if holder not in state.slugs | NON_CHAR_HOLDERS:
                errors.append(f'{where} cognition holder 引用未知人物 {holder}')
        # —— 状态同维合并（I-15：canonical key 按类归一；改在副本上）——
        # 句柄查唯一关联登记表（R1 v2 位置编码 ch{nn}.b{拍}.e{效应序}.{key}）。
        for effect_ordinal, effect in enumerate(beat.get('effects') or [], 1):
            target = serialize_target(effect.get('target') or {}, where=where)
            handle = chapter_state_handle(chapter, ordinal, effect_ordinal,
                                          effect.get('key'))
            if handle not in state.space.states:
                errors.append(
                    f'{where} 的状态句柄 {handle!r} 不在关联登记表内'
                    '（投影与编译位置编码漂移）')
            else:
                effect['key'] = state.state_classes.canonical_key(
                    handle, target, effect.get('key'), where, undecided)
            # 归一后的键若是登记键，作用域随 canonical key 重新绑定（R-2.14）。
            registered_scope = REGISTERED_STATE_KEYS.get(effect.get('key'))
            if registered_scope is not None:
                effect['scope'] = registered_scope
        # —— 命题两遍：先登记/合并本拍新建，再解析其余引用（D5 同拍合法）——
        propositions: list[tuple[dict, str]] = []
        for effect in beat.get('effects') or []:
            if isinstance(effect.get('proposition'), dict):
                propositions.append((effect['proposition'], f'{where} effect 命题'))
        for cog in beat.get('cognition') or []:
            if isinstance(cog.get('proposition'), dict):
                propositions.append((cog['proposition'], f'{where} cognition 命题'))
        resolved: set[int] = set()
        for prop, where_prop in propositions:
            ref = prop.get('ref')
            handle = f'ch{chapter:02d}.{ref}'
            statement = prop.get('statement')
            if statement is not None:
                formal = state.restatement(handle, statement, where_prop, errors)
                if formal is not None:
                    prop['ref'] = formal
                    resolved.add(id(prop))
                    continue
                formal = state.establish(handle, statement, chapter)
                prop['ref'] = formal
                resolved.add(id(prop))
        for prop, where_prop in propositions:
            if id(prop) in resolved:
                continue
            handle = f'ch{chapter:02d}.{prop.get("ref")}'
            entry = state.propositions.get(handle)
            if entry is not None:  # 章内（含同拍先建）已登记 → 同 ID
                prop['ref'] = entry['id']
                continue
            state.defer(prop, handle, where_prop)
        # —— 悬念（R-2.11：open 一次、close 带收束；跨章 close 走配对）——
        for loop in beat.get('loops') or []:
            ref, op = loop.get('ref'), loop.get('op')
            handle = f'ch{chapter:02d}.{ref}'
            if op == 'open':
                if handle in state.loop_ids:
                    errors.append(f'{where} loop {ref!r} 重复 open（悬念只能 open 一次）')
                    continue
                state.loop_seq += 1
                formal = f'loop{state.loop_seq:02d}'
                state.loop_ids[handle] = formal
                state.loop_state[formal] = 'open'
                chapter_loops[ref] = formal
                loop['ref'] = formal
                continue
            formal = chapter_loops.get(ref) or state.loop_ids.get(handle)
            if formal is None:
                open_handle = state.loop_pairing.get(handle)
                if open_handle is None:
                    undecided.append(
                        f'{where} 的悬念 close {handle!r} 本章未 open 且没有开合配对'
                        '（缺项保持未决，不默认丢弃）')
                    continue
                formal = state.loop_ids.get(open_handle)
                if formal is None:
                    undecided.append(
                        f'{where} 的悬念 close {handle!r} 配对的 open '
                        f'{open_handle!r} 尚未建立（时序倒转）')
                    continue
            if state.loop_state.get(formal) != 'open':
                undecided.append(
                    f'{where} 的悬念 {formal} 已 close，不得重复 close（R-2.11）')
                continue
            state.loop_state[formal] = 'closed'
            loop['ref'] = formal
        # —— 场景分段（new 开场；continue 延续当前开放场景，可跨章）——
        boundary = beat.get('scene_boundary')
        if boundary == 'new' or state.open_scene is None:
            if boundary == 'continue' and state.open_scene is None:
                errors.append(
                    f'{where} 用 continue 但没有可延续的场景（全书第一个拍必须 new）')
            state.scene_seq += 1
            state.open_scene = f'sc{state.scene_seq:02d}'
        if state.open_scene not in scenes:
            # 跨章延续：上一章末尾的开放场景在本章首次出现（scene_id 不变）。
            scene_order.append(state.open_scene)
            scenes[state.open_scene] = []
        scenes[state.open_scene].append(beat['beat_id'])
        beat_dicts.append(beat)
    if not beat_dicts:
        errors.append(f'第{chapter}章观察没有可编译的拍')
        return None
    return chapter, beat_dicts, scenes, scene_order


def _apply_cross_chapter_causes(
    chapter_blueprints: list[ChapterBlueprint], state: _BookCompile,
    undecided: list[str],
) -> list[ChapterBlueprint]:
    """跨章因果（D8）：边落到 to 拍所在章；from 章须早于 to 章，否则按 item 未决。"""
    if not state.causes:
        return chapter_blueprints
    by_chapter = {bp.chapter: bp for bp in chapter_blueprints}
    edges: dict[int, list[CausalEdge]] = {}
    for edge in state.causes:
        from_match = _BEAT_HANDLE.fullmatch(edge.from_beat)
        to_match = _BEAT_HANDLE.fullmatch(edge.to_beat)
        if from_match is None or to_match is None:
            undecided.append(f'因果边句柄形状非法：{edge.from_beat}→{edge.to_beat}')
            continue
        from_chapter, from_ordinal = int(from_match.group(1)), int(from_match.group(2))
        to_chapter, to_ordinal = int(to_match.group(1)), int(to_match.group(2))
        if from_chapter >= to_chapter:
            undecided.append(
                f'因果边 {edge.from_beat}→{edge.to_beat} 时序倒转或同章'
                '（章内因果由拍序承载，跨章因果不得回指）')
            continue
        target_bp = by_chapter.get(to_chapter)
        if target_bp is None or to_ordinal > len(target_bp.beats):
            undecided.append(f'因果边 {edge.from_beat}→{edge.to_beat} 的 to 拍不存在')
            continue
        edges.setdefault(to_chapter, []).append(CausalEdge(
            from_beat=f'b{from_chapter:02d}_{from_ordinal:02d}',
            to_beat=f'b{to_chapter:02d}_{to_ordinal:02d}',
            kind=edge.kind))
    if not edges:
        return chapter_blueprints
    updated: list[ChapterBlueprint] = []
    for bp in chapter_blueprints:
        extra = edges.get(bp.chapter)
        if extra:
            bp = bp.model_copy(update={
                'causal_edges': tuple(bp.causal_edges) + tuple(extra)})
        updated.append(bp)
    return updated


def _apply_scene_continuations(
    chapter_blueprints: list[ChapterBlueprint], state: _BookCompile,
    undecided: list[str],
) -> list[ChapterBlueprint]:
    """续场配对（D8）：右端场景并入左端类（改 scene_id，拍序与分章不动）。

    布局核对按 item 报告：左端须是该章最后一个场景（seam 处开放）、右端须是该章
    第一个场景、左章严格早于右章、右端只能被配对一次。合法跨章布局（continue
    边界）不经配对已成立；配对是 associate 对「该章自称 new」的同一性否决。
    """
    if not state.scene_pairing:
        return chapter_blueprints
    scenes_of = {bp.chapter: list(bp.scenes) for bp in chapter_blueprints}
    union = _Union()
    for left_handle, right_handle in state.duplicate_continuation_rights:
        undecided.append(
            f'续场配对 {left_handle}→{right_handle} 的右端已被另一条配对占用'
            '（一个场景不能同时续两个前场景）')
    for right_handle, left_handle in sorted(state.scene_pairing.items()):
        where = f'续场配对 {left_handle}→{right_handle}'
        left_match, right_match = (_SCENE_HANDLE.fullmatch(left_handle),
                                   _SCENE_HANDLE.fullmatch(right_handle))
        if left_match is None or right_match is None:
            undecided.append(f'{where} 句柄形状非法')
            continue
        left_chapter, left_ordinal = int(left_match.group(1)), int(left_match.group(2))
        right_chapter, right_ordinal = int(right_match.group(1)), int(right_match.group(2))
        left_list, right_list = scenes_of.get(left_chapter), scenes_of.get(right_chapter)
        if left_list is None or right_list is None:
            undecided.append(f'{where} 的章蓝图不存在')
            continue
        if left_chapter >= right_chapter:
            undecided.append(f'{where} 时序倒转（左章不早于右章）')
            continue
        if right_chapter != left_chapter + 1:
            undecided.append(
                f'{where} 只定义在相邻章的 seam 上（{left_chapter}→{right_chapter} '
                '中间隔着别的场景，合并会让场景段交错）')
            continue
        if left_ordinal != len(left_list):
            undecided.append(
                f'{where} 左端不是第 {left_chapter} 章最后一个场景（seam 处才可能续场）')
            continue
        if right_ordinal != 1:
            undecided.append(f'{where} 右端不是第 {right_chapter} 章第一个场景')
            continue
        if right_handle in state._seen_continuation_rights:
            undecided.append(
                f'{where} 右端已被另一条配对占用（一个场景不能同时续两个前场景）')
            continue
        state._seen_continuation_rights.add(right_handle)
        union.union(left_list[left_ordinal - 1].scene_id, (left_chapter, left_ordinal, left_handle),
                    right_list[right_ordinal - 1].scene_id, (right_chapter, right_ordinal, right_handle))
    updated: list[ChapterBlueprint] = []
    for bp in chapter_blueprints:
        spans = tuple(
            SceneSpan(scene_id=(union.find(span.scene_id)
                                if span.scene_id in union else span.scene_id),
                      beat_ids=span.beat_ids)
            for span in bp.scenes)
        updated.append(bp.model_copy(update={'scenes': spans}))
    return updated


# ───────────────────────── 源 fold（R-2.17：只读度量） ─────────────────────────

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
    head = _state_address(atom.target, atom.key)[0]
    label = f'{head[0]}:{",".join(map(str, head[1:]))}.{atom.key}'
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
            address = _state_address(atom.target, atom.key)
            self.initial_values[address] = deepcopy(atom.value)
            if atom.persistence == 'stable':
                self.stable.add(address)

    def seed(self, characters: CharactersCore) -> None:
        for atom in characters.initial_state:
            scope = REGISTERED_STATE_KEYS.get(atom.key, 'book')
            self.overlay.apply('__init__', [OverlayChange(
                address=_state_address(atom.target, atom.key),
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
                address = _state_address(eff.target, eff.key)
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

    def _apply_effect(self, eff: Effect, beat: Beat, scene_id: str,
                      chapter: int) -> None:
        address = _state_address(eff.target, eff.key)
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


#: 判断限定词（与 CHAPTER_PROMPT 的引文要求同一集合；只进 fold 读数，不进门——
#: P3「原以为／后来证实」正例要求判据不得按关键词拦）。
JUDGEMENT_HEDGES: tuple[str, ...] = ('以为', '觉得', '像是', '预计', '约', '几天后')


def evidence_metrics(
    blueprint: BlueprintsArtifactV15, characters: CharactersCore,
) -> dict[str, Any]:
    """R10a a1 证据读数（度量不是门，§四诊断表）：引文长度分布、引文等于
    称谓字面（只摘人名凑数的信号）、命题含判断限定词比例、modality 计数。

    只读 canonical（I-24 不改输入）；供 fold 落盘与离线核读，不参与任何
    接受判定。
    """
    surfaces = {window.surface
                for entry in characters.characters.values()
                for window in entry.surfaces}
    quote_lengths: list[int] = []
    quotes_equal_surface = 0
    quote_total = 0
    modality_counts: dict[str, int] = {}
    cognition_total = 0
    cognition_hedged = 0
    for atom in characters.initial_state:
        for basis in atom.basis:
            quote_total += 1
            quote_lengths.append(len(basis.quote))
            quotes_equal_surface += basis.quote in surfaces
    for rule in blueprint.world_constraints:
        for basis in rule.basis:
            quote_total += 1
            quote_lengths.append(len(basis.quote))
            quotes_equal_surface += basis.quote in surfaces
    for chapter_bp in blueprint.chapters:
        for beat in chapter_bp.beats:
            for effect in beat.effects:
                modality_counts[effect.modality] = \
                    modality_counts.get(effect.modality, 0) + 1
                for basis in effect.basis:
                    quote_total += 1
                    quote_lengths.append(len(basis.quote))
                    quotes_equal_surface += basis.quote in surfaces
            for cognition in beat.cognition:
                cognition_total += 1
                statement = cognition.proposition.statement or ''
                cognition_hedged += any(h in statement
                                        for h in JUDGEMENT_HEDGES)
                for basis in cognition.basis:
                    quote_total += 1
                    quote_lengths.append(len(basis.quote))
                    quotes_equal_surface += basis.quote in surfaces
    ordered = sorted(quote_lengths)
    return {
        'quote_total': quote_total,
        'quote_length_min': ordered[0] if ordered else 0,
        'quote_length_median': (
            ordered[len(ordered) // 2] if len(ordered) % 2
            else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
        ) if ordered else 0,
        'quote_length_max': ordered[-1] if ordered else 0,
        'quotes_equal_surface': quotes_equal_surface,
        'cognition_total': cognition_total,
        'cognition_hedged': cognition_hedged,
        'cognition_hedged_ratio': (
            round(cognition_hedged / cognition_total, 3) if cognition_total else 0.0),
        'effect_modality_counts': dict(sorted(modality_counts.items())),
    }


def source_fold(
    characters: CharactersCore, blueprint: BlueprintsArtifactV15,
    *, pre_chapter_evidence: frozenset[str] = frozenset(),
    proposition_registry: dict[str, str] | None = None,
) -> FoldResult:
    """确定性折叠：事实层＋认知层＋读者确认时点，返回结构化只读读数。

    只消费 canonical（CharactersCore＋BlueprintsV13 的 transitions；`R-2.17`），
    **不修改输入**（I-24）。章前文字（e00）两条分流读数按持有者与命题核对
    （QP-07），只报告、不阻断、不猜。
    """
    folder = _Folder(characters)
    folder.seed(characters)
    pre = frozenset(pre_chapter_evidence or ())
    initial_known = [atom for atom in characters.initial_state
                     if atom.knowledge is not None]
    snapshots: dict[int, list[str]] = {0: initial_snapshot(characters)}
    chapter_stats: dict[int, dict[str, Any]] = {}
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        scene_of = {bid: span.scene_id for span in chapter_bp.scenes
                    for bid in span.beat_ids}
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
                                   or (cog.holder == 'reader'
                                       and atom.knowledge.reader_knows))
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
