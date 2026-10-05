"""CS-F3 关键语义离线标定 harness：请求组装＋评分＋fake transport 运行器。

【定位（测试工具，同 tests/golden.py 一类）】
不新增产品节点、不改 13 节点拓扑、不接 PaidRequestSession——本模块只做离线
标定的免费前置（用户 2026-10-04 裁决：先离线标定，再启用自动语义闸）：
冻结预期标签 → 按三目标组装核验请求 → 经可注入 transport 取回答 → 机械解析
→ 与金标逐案评分并汇总分项指标与启用阻断条件。真实付费执行需要独立授权与
正式接线（未实现）；在此之前 transport 只允许脚本化 fake。

【unver 纪律】
fake transport 绿例不得记成语义核验已通过：报告恒带 `semantic_gate_status:
'unver'`，`gate_ready` 只汇总机械阻断条件，本 harness 不启用任何生产闸。
金标拟用仓外 Golden_Set_v3.0 yaml 属**待用户确认**项——从该来源导入的案件必须
带 `label_source='golden_yaml_pending_confirmation'`，评分时作为阻断条件报出，
确认前不参与任何通过判定。

【三个核验目标（对象与边界见 docs/schema/repair_convergence.md §A/B）】
· stage2_fact：章观察候选事实的高影响选择＋源文对照核验（主体／行为命题／
  事实状态／说话者知情者／时间限定；结果闭集 支持／矛盾／未确定，带具体对象
  与逐字证据；引文存在、人物ID合法、模型自报"已检查"都不构成支持证明）；
· preserve_basis：义务 statement／satisfaction 里肯定与否定限制的源依据核验
  （不得从"读者已知"推"角色不知"，不得把目标设计选择冒充源文要求）；
· design_fragment：正式 Brief 语句＋对应候选片段的局部语义核验（源盲——材料
  不含源章号与原文窗口；无关内容即使引用合法也须否决）。

【身份与成本】
每份报告登记协议字面量、规则修订号、规则摘要与模型名；逐案记录 prompt
字符数（注入 token_counter 时另记 token）——供"先量新增核验调用成本"的免费
预算，未实际购买时成本只记未测。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.schema.observation import ChapterObservation
from contracts.schema.source import Cognition, Effect
from novel.llm.transport import ChatRequest, ChatResponse

#: 判定 wire 的协议字面量（模型回显；I-13 显式版本化，规则变化必须换版）。
CSF3_VERIFY_PROTOCOL = 'csf3-verify-v1'

#: 提示词规则修订号（进报告身份登记；改规则文本必须换号）。
CSF3_RULES_REVISION = 'csf3-rules-r1'

#: 系统段（与现役付费节点同形：短身份，不承载规则正文）。
CALIBRATION_SYSTEM = '语义核验标定'

VERDICTS = ('支持', '矛盾', '未确定')

TARGETS = ('stage2_fact', 'preserve_basis', 'design_fragment')

#: 高影响事实类别闭集（repair_convergence.md §A：改变主线、人物关系或关键
#: 行动依据的断言；高影响性判断本身是语义工作，不由关键词白名单代替）。
STAGE2_CATEGORIES = ('relation', 'betrayal', 'death_survival', 'identity',
                     'key_knowledge', 'key_misunderstanding')
PRESERVE_CATEGORIES = ('affirmative_basis', 'negative_basis')
DESIGN_CATEGORIES = ('fact_preservation', 'restriction_preservation')

#: 标签来源闭集。`golden_yaml_pending_confirmation` 是拟用金标（待用户确认），
#: 评分时作阻断条件；`user_adjudicated` 是已裁决正反例（硬规则对象）。
LABEL_SOURCES = ('user_adjudicated', 'golden_yaml_pending_confirmation',
                 'synthetic_fixture')

#: 评分结局闭集（报告分项指标的字段面）。
OUTCOMES = ('correct_support', 'correct_rejection', 'correct_undecided',
            'correct_skip', 'wrong_support', 'wrong_rejection', 'undecided_miss',
            'missed_high_impact', 'wire_error', 'missing')

#: 源证据段 id 形状（同 `novel/stages/chain.py::build_evidence` 的 e{nn}_{nn}）。
_SEGMENT_ID = re.compile(r'\be\d+_\d+\b')

#: 每个已组装请求的首行标记（fake transport 按它分发；留证用）。
_MARKER = '【标定案件】{case_id}｜{target}'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


# ───────────────────────── 冻结预期标签（金标记录） ─────────────────────────

class GoldAnchor(Strict):
    """金标证据锚：`ref` 是段 id（stage2／preserve）或 Brief 句柄／拍引用
    （design，源盲面没有源段坐标）；`quote` 是支撑标签的逐字文本。"""

    ref: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class CalibrationCase(Strict):
    """一条可核验案件的冻结标签。标签先于模型回答冻结（带时点与来源），
    不能用待测模型自己的回答当标准。"""

    case_id: str = Field(min_length=1)
    target: str = Field(pattern=r'^(' + '|'.join(TARGETS) + r')$')
    category: str = Field(min_length=1)
    #: 请求闭集内的条目句柄（stage2：候选事实句柄；preserve：义务 id；
    #: design：绑定锁／命题句柄）。
    item: str = Field(min_length=1)
    expected: str = Field(pattern=r'^(' + '|'.join(VERDICTS) + r')$')
    #: 证据确实不足（仅 expected=未确定 时可为真；模型必须保持未确定）。
    evidence_insufficient: bool = False
    #: stage2 选择维度的金标：该候选是否高影响（漏选分母）。
    high_impact: bool = False
    gold_evidence: tuple[GoldAnchor, ...] = Field(min_length=1)
    label_source: str = Field(
        default='synthetic_fixture',
        pattern=r'^(' + '|'.join(LABEL_SOURCES) + r')$')
    subset: str = Field(default='dev', pattern=r'^(dev|holdout)$')
    #: 标签分歧未裁决（阻断启用，不擅自改标签）。
    disputed: bool = False
    frozen_at: str = Field(min_length=1,
                           description='ISO-8601 时点；标签冻结先于任何模型回答')
    notes: str = ''

    @model_validator(mode='after')
    def _shape(self) -> 'CalibrationCase':
        pools = {'stage2_fact': STAGE2_CATEGORIES,
                 'preserve_basis': PRESERVE_CATEGORIES,
                 'design_fragment': DESIGN_CATEGORIES}
        if self.category not in pools[self.target]:
            raise ValueError(f'{self.case_id}: 类别 {self.category!r} 不属于目标'
                             f' {self.target} 的闭集 {pools[self.target]}')
        if self.evidence_insufficient and self.expected != '未确定':
            raise ValueError(f'{self.case_id}: evidence_insufficient 只允许配'
                             ' expected=未确定')
        if self.high_impact and self.target != 'stage2_fact':
            raise ValueError(f'{self.case_id}: high_impact 只属于 stage2_fact')
        if self.label_source == 'user_adjudicated' and self.subset != 'dev':
            raise ValueError(f'{self.case_id}: 已裁决正反例属开发集'
                             '（留出集未参与提示词调试）')
        return self


class FrozenLabels(Strict):
    """一份冻结的金标集。"""

    schema_version: Literal[1] = 1
    book: str = Field(min_length=1)
    source_note: str = ''
    cases: tuple[CalibrationCase, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique(self) -> 'FrozenLabels':
        ids = [c.case_id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError(f'case_id 重复：{sorted(ids)}')
        return self


# ───────────────────────── 核验材料（请求组装的输入面） ─────────────────────────

class TextSegment(Strict):
    """源文段：`e{nn}_{nn}` 段 id ＋ 逐字文本。role=preceding 是"必要前文"
    （核验允许复用的上文窗口），不参与本章高影响分母。"""

    segment_id: str = Field(pattern=r'^e\d+_\d+$')
    text: str = Field(min_length=1)
    role: str = Field(default='chapter', pattern=r'^(chapter|preceding)$')


class FactCandidate(Strict):
    """章观察候选事实（stage2 目标的闭集成员）。`line` 是 assembler 从
    Effect／Cognition 机械投影的人读行；`basis` 是观察自带的逐字证据
    （候选声称的依据，供模型对照，不构成支持证明）。"""

    handle: str = Field(min_length=1)
    kind: str = Field(pattern=r'^(effect|cognition)$')
    line: str = Field(min_length=1)
    basis: tuple[str, ...] = Field(default=(), min_length=0)


class Stage2FactMaterial(Strict):
    """stage2_fact 目标的材料：本章段＋必要前文＋候选事实闭集。"""

    segments: tuple[TextSegment, ...] = Field(min_length=1)
    candidates: tuple[FactCandidate, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _ids_unique(self) -> 'Stage2FactMaterial':
        ids = [s.segment_id for s in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError(f'段 id 重复：{ids}')
        handles = [c.handle for c in self.candidates]
        if len(set(handles)) != len(handles):
            raise ValueError(f'候选句柄重复：{handles}')
        return self

    def closed_items(self) -> tuple[str, ...]:
        return tuple(c.handle for c in self.candidates)

    def texts(self) -> tuple[str, ...]:
        return tuple(s.text for s in self.segments)


class ObligationMaterial(Strict):
    """preserve_basis 目标的材料：一条保存义务＋其锚点证据文本。"""

    obligation_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    satisfaction: str = ''
    must_preserve: bool
    anchors: tuple[str, ...] = Field(
        default=(), min_length=0, description='锚点逐字引文（义务声称的依据）')
    segments: tuple[TextSegment, ...] = Field(min_length=1)

    def closed_items(self) -> tuple[str, ...]:
        return (self.obligation_id,)

    def texts(self) -> tuple[str, ...]:
        return tuple(s.text for s in self.segments)


class DesignLock(Strict):
    """候选片段上的一条绑定（源盲面）：`item` 是 Brief 句柄（kp_／wc_／kf_），
    `statement` 是正式 Brief 语句文本。"""

    item: str = Field(min_length=1)
    statement: str = Field(min_length=1)


class DesignMaterial(Strict):
    """design_fragment 目标的材料：绑定清单＋候选片段行。源盲纪律是机械闸：
    行与语句里不得出现源证据段 id（e{nn}_{nn}）——出现即构造期拒绝。"""

    locks: tuple[DesignLock, ...] = Field(min_length=1)
    rows: tuple[str, ...] = Field(
        min_length=1, description='候选片段行（拍引用＋事件／状态／知情）')

    @model_validator(mode='after')
    def _source_blind(self) -> 'DesignMaterial':
        for text in (*self.rows, *(lock.statement for lock in self.locks)):
            if _SEGMENT_ID.search(text):
                raise ValueError(f'设计核验材料含源证据段 id（违反源盲边界）：{text!r}')
        return self

    def closed_items(self) -> tuple[str, ...]:
        return tuple(lock.item for lock in self.locks)

    def texts(self) -> tuple[str, ...]:
        return (*self.rows, *(lock.statement for lock in self.locks))


Material = Stage2FactMaterial | ObligationMaterial | DesignMaterial

_MATERIAL_BY_TARGET: dict[str, type[BaseModel]] = {
    'stage2_fact': Stage2FactMaterial,
    'preserve_basis': ObligationMaterial,
    'design_fragment': DesignMaterial,
}


# ───────────────────────── 候选事实的机械投影（I-20／I-15） ─────────────────────────

_OP_WORDS = {'set': '确立', 'unset': '解除', 'add': '增加', 'remove': '移除',
             'unknown': '维度未知'}
_MODALITY_WORDS = {'actual': '已发生', 'planned': '计划未发生',
                   'expected': '预计未发生', 'conditional': '条件执行未发生'}
_MODE_WORDS = {'learns': '得知', 'suspects': '起疑', 'confirms': '确认',
               'hears_claim': '听闻断言', 'believes': '相信', 'asserts': '主张'}


def _target_words(target) -> str:
    if target.kind == 'character':
        return f'人物 {target.char_id}'
    if target.kind == 'relation':
        return f'关系 {target.subject}↔{target.object}'
    return '世界'


def render_effect_line(effect: Effect) -> str:
    """Effect → 人读行（机械投影；语义判断归模型）。"""
    value = '' if effect.value is None else f'「{effect.value}」'
    return (f'{_target_words(effect.target)}的 {effect.key}：'
            f'{_OP_WORDS.get(effect.op, effect.op)}{value}'
            f'（{_MODALITY_WORDS.get(effect.modality, effect.modality)}）')


def render_cognition_line(cognition: Cognition) -> str:
    """Cognition → 人读行。命题文本优先取 statement（引用既有命题为空时用 ref）。"""
    statement = cognition.proposition.statement or cognition.proposition.ref
    return (f'{cognition.holder}{_MODE_WORDS.get(cognition.mode, cognition.mode)}'
            f'「{statement}」')


def fact_candidates_from_observation(chapter_ordinal: int,
                                     observation: ChapterObservation,
                                     ) -> tuple[FactCandidate, ...]:
    """章观察 → 候选事实闭集。句柄沿用 associate 的位置编码族：
    ``ch{nn}.b{拍}.e{效应序}``／``ch{nn}.b{拍}.k{知情序}``。"""
    out: list[FactCandidate] = []
    for beat_index, beat in enumerate(observation.beats, start=1):
        for effect_index, effect in enumerate(beat.effects, start=1):
            out.append(FactCandidate(
                handle=f'ch{chapter_ordinal:02d}.b{beat_index}.e{effect_index}',
                kind='effect', line=render_effect_line(effect),
                basis=tuple(q.quote for q in effect.basis)))
        for know_index, cognition in enumerate(beat.cognition, start=1):
            out.append(FactCandidate(
                handle=f'ch{chapter_ordinal:02d}.b{beat_index}.k{know_index}',
                kind='cognition', line=render_cognition_line(cognition),
                basis=tuple(q.quote for q in cognition.basis)))
    return tuple(out)


# ───────────────────────── 请求组装（三目标） ─────────────────────────

_JSON_SHAPE = ('输出 JSON（且只输出 JSON）：'
               '{"protocol_version": "%s", "selection": ["句柄", ...], '
               '"verdicts": [{"item": "句柄", "verdict": "支持|矛盾|未确定", '
               '"object": "具体对象（主体／命题／限制）", "evidence": ["逐字引文", ...]}]}。'
               'selection 只在要求选择时给出；verdicts 覆盖你选定的每个条目。'
               % CSF3_VERIFY_PROTOCOL)

_STAGE2_RULES = f"""任务：先从候选事实清单里选出**高影响**候选，再逐项对照原文核验。
高影响＝改变主线、人物关系或关键行动依据的断言：亲密／亲属关系、背叛、死亡／生还、真实身份与身份揭示、决定行动的关键知情与误解；高影响性是语义判断，不按关键词。
核验逐项比较：主体、行为／命题、事实状态、说话者／知情者、时间限定；对代词保留可判别的前后文；明确场景或主体切换必须识别，不能沿用上一主体。
判定只允许 支持／矛盾／未确定：支持＝原文确证该断言（含主体与时态一致）；矛盾＝原文与之冲突或主体被误派；未确定＝证据确实不足。每条带具体对象与逐字证据引文。
引文存在、人物ID合法、你自报"已检查"都不构成支持证明；证据确实不足时必须保持未确定，不补猜。未选中的候选不得冒充已核验。
{_JSON_SHAPE}"""

_PRESERVE_RULES = f"""任务：核验这条保存义务的限制是否有源依据。
statement 与 satisfaction 里的**肯定限制**（必须保留什么）与**否定限制**（不得改变／不得提前什么）都必须能在所给原文段中找到依据。
以下情形判 矛盾（并在 object 里指明具体是哪条限制）：限制在原文中无依据；从"读者已知"推导"角色不知"；把目标设计选择冒充源文要求。
限制确有原文依据判 支持；依据是否充分无法确定判 未确定（证据确实不足必须保持未确定）。每条判定带具体对象与逐字证据引文。
{_JSON_SHAPE}"""

_DESIGN_RULES = f"""任务：核验候选片段是否保留了所绑定的每条正式语句。
你只看到绑定语句与候选片段（源盲）：不得假设任何源文、源章号或原文窗口；判定只基于所给材料。
对每条绑定：候选片段确实保留该事实／限制判 支持；片段内容与之冲突、或把绑定放在无关内容上（即使位置引用合法）判 矛盾；材料不足以判定判 未确定。
每条判定带具体对象（绑定语句在片段中的对应位置或缺失处）与逐字证据引文（从所给材料逐字复制）。
{_JSON_SHAPE}"""

_RULES_BY_TARGET = {'stage2_fact': _STAGE2_RULES,
                    'preserve_basis': _PRESERVE_RULES,
                    'design_fragment': _DESIGN_RULES}


def _render_segments(segments: tuple[TextSegment, ...]) -> list[str]:
    lines = []
    for segment in segments:
        head = '前文' if segment.role == 'preceding' else '本章'
        lines.append(f'[{head} {segment.segment_id}] {segment.text}')
    return lines


def _stage2_prompt(case_id: str, material: Stage2FactMaterial) -> str:
    parts = [_MARKER.format(case_id=case_id, target='stage2_fact'),
             '【原文】（前文只作上下文，高影响分母只算本章）']
    parts.extend(_render_segments(material.segments))
    parts.append('【候选事实清单】（句柄｜观察行｜观察自带的依据引文）')
    parts.extend(f'- {c.handle}｜{c.line}｜依据：{"；".join(c.basis) or "（无）"}'
                 for c in material.candidates)
    parts.append(_STAGE2_RULES)
    return '\n'.join(parts) + '\n'


def _preserve_prompt(case_id: str, material: ObligationMaterial) -> str:
    parts = [_MARKER.format(case_id=case_id, target='preserve_basis'),
             '【原文段】']
    parts.extend(_render_segments(material.segments))
    parts.append('【保存义务】')
    parts.append(f'- 义务 {material.obligation_id}'
                 f'（{"必保" if material.must_preserve else "可综合"}）')
    parts.append(f'  statement：{material.statement}')
    if material.satisfaction:
        parts.append(f'  satisfaction：{material.satisfaction}')
    if material.anchors:
        parts.append('  义务声称的依据引文：' + '；'.join(material.anchors))
    parts.append(_PRESERVE_RULES)
    return '\n'.join(parts) + '\n'


def _design_prompt(case_id: str, material: DesignMaterial) -> str:
    parts = [_MARKER.format(case_id=case_id, target='design_fragment'),
             '【绑定语句】（正式 Brief，源盲面）']
    parts.extend(f'- {lock.item}｜{lock.statement}' for lock in material.locks)
    parts.append('【候选片段】')
    parts.extend(f'- {row}' for row in material.rows)
    parts.append(_DESIGN_RULES)
    return '\n'.join(parts) + '\n'


def build_verification_prompt(case_id: str, target: str, material: Material) -> str:
    """按目标组装核验请求 user 段。纯函数：同材料恒同字节。"""
    if target not in _MATERIAL_BY_TARGET:
        raise ValueError(f'未知核验目标：{target}')
    if not isinstance(material, _MATERIAL_BY_TARGET[target]):
        raise ValueError(f'材料形状与目标 {target} 不符：'
                         f'{type(material).__name__}')
    builder = {'stage2_fact': _stage2_prompt, 'preserve_basis': _preserve_prompt,
               'design_fragment': _design_prompt}[target]
    return builder(case_id, material)


def build_verification_request(case_id: str, target: str, material: Material, *,
                               model: str) -> ChatRequest:
    return ChatRequest(model=model, system=CALIBRATION_SYSTEM,
                       user=build_verification_prompt(case_id, target, material),
                       reasoning_effort='high', thinking=True, json_mode=True,
                       max_tokens=4000)


# ───────────────────────── 判定 wire 的机械解析 ─────────────────────────

class ItemVerdict(Strict):
    item: str = Field(min_length=1)
    verdict: str = Field(pattern=r'^(' + '|'.join(VERDICTS) + r')$')
    object: str = Field(min_length=1)
    evidence: tuple[str, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _evidence_for_assertions(self) -> 'ItemVerdict':
        if self.verdict in ('支持', '矛盾') and not self.evidence:
            raise ValueError(f'{self.item}: {self.verdict} 判定必须带逐字证据')
        return self


class VerdictWire(Strict):
    protocol_version: str = Field(pattern=r'^' + CSF3_VERIFY_PROTOCOL + r'$')
    selection: tuple[str, ...] = Field(default=(), min_length=0)
    verdicts: tuple[ItemVerdict, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _consistent(self) -> 'VerdictWire':
        items = [v.item for v in self.verdicts]
        if len(set(items)) != len(items):
            raise ValueError(f'判定条目重复：{items}')
        if len(set(self.selection)) != len(self.selection):
            raise ValueError('selection 句柄重复')
        if self.selection and set(items) != set(self.selection):
            raise ValueError('selection 与 verdicts 必须一一对应（选中的条目都要判定）')
        return self


def parse_verdict_payload(payload: Any, *, closed_items: tuple[str, ...],
                          texts: tuple[str, ...],
                          require_selection: bool = False,
                          ) -> tuple[VerdictWire | None, list[str]]:
    """原始回答 → 判定 wire。机械闸（必要不充分）：闭集成员与覆盖、判定值域、
    断言带证据、证据引文在所给材料中逐字存在。错误保留条目定位（I-36）。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            return None, [f'回答不是合法 JSON：{exc}']
    if not isinstance(payload, dict):
        return None, ['回答不是 JSON 对象']
    errors: list[str] = []
    try:
        wire = VerdictWire.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError 的条目逐一定位报出
        if hasattr(exc, 'errors'):
            errors.extend(f'{e.get("loc")}: {e.get("msg")}' for e in exc.errors())
        else:
            errors.append(str(exc))
        return None, errors
    closed = set(closed_items)
    unknown = sorted({v.item for v in wire.verdicts} - closed)
    if unknown:
        errors.append(f'判定引用闭集外条目：{unknown}')
    if require_selection:
        if not wire.selection:
            errors.append('该目标要求 selection（高影响选择面为空＝未执行选择）')
    else:
        uncovered = sorted(closed - {v.item for v in wire.verdicts})
        if uncovered:
            errors.append(f'判定未覆盖闭集条目：{uncovered}')
    joined = '\n'.join(texts)
    for verdict in wire.verdicts:
        for quote in verdict.evidence:
            if quote not in joined:
                errors.append(f'{verdict.item}: 证据引文未在材料中逐字命中：{quote!r}')
    if errors:
        return None, errors
    return wire, []


# ───────────────────────── 评分 ─────────────────────────

#: (模型判定, 预期标签) → 结局。证据不足案例被断言（支持／矛盾）也计
#: 错误支持／错误否决——fabrication 违反另在硬规则里按名单报出。
_VERDICT_OUTCOME = {
    ('支持', '支持'): 'correct_support',
    ('矛盾', '支持'): 'wrong_rejection',
    ('未确定', '支持'): 'undecided_miss',
    ('矛盾', '矛盾'): 'correct_rejection',
    ('支持', '矛盾'): 'wrong_support',
    ('未确定', '矛盾'): 'undecided_miss',
    ('未确定', '未确定'): 'correct_undecided',
    ('支持', '未确定'): 'wrong_support',
    ('矛盾', '未确定'): 'wrong_rejection',
}


def score_case(case: CalibrationCase, wire: VerdictWire | None, *,
               error: bool = False) -> dict:
    """一案评分。wire 缺失／解析失败如实计 missing／wire_error，不当通过。"""
    row = {'case_id': case.case_id, 'target': case.target, 'category': case.category,
           'item': case.item, 'expected': case.expected, 'model_verdict': None,
           'label_source': case.label_source, 'subset': case.subset,
           'evidence_insufficient': case.evidence_insufficient}
    if error:
        row['outcome'] = 'wire_error'
        return row
    if wire is None:
        row['outcome'] = 'missing'
        return row
    verdict = next((v.verdict for v in wire.verdicts if v.item == case.item), None)
    if verdict is None:
        row['outcome'] = 'missing'
        return row
    row['model_verdict'] = verdict
    if case.target == 'stage2_fact' and case.item not in wire.selection:
        row['outcome'] = 'missed_high_impact' if case.high_impact else 'correct_skip'
        return row
    row['outcome'] = _VERDICT_OUTCOME[(verdict, case.expected)]
    return row


def _aggregate(rows: list[dict]) -> dict:
    counts = {outcome: 0 for outcome in OUTCOMES}
    for row in rows:
        counts[row['outcome']] += 1
    return {'denominator': len(rows), **counts}


def score_labels(labels: FrozenLabels,
                 wires: Mapping[str, VerdictWire | None],
                 *, wire_errors: frozenset[str] = frozenset()) -> dict:
    """金标 × 判定 → 分项报告＋硬规则违反＋启用阻断条件（不启用任何闸）。"""
    rows = [score_case(case, wires.get(case.case_id),
                       error=case.case_id in wire_errors)
            for case in labels.cases]
    by_target = {target: _aggregate([r for r in rows if r['target'] == target])
                 for target in TARGETS}
    by_category: dict[str, dict] = {}
    for category in (*STAGE2_CATEGORIES, *PRESERVE_CATEGORIES, *DESIGN_CATEGORIES):
        selected = [r for r in rows if r['category'] == category]
        if selected:
            by_category[category] = _aggregate(selected)
    by_subset = {subset: _aggregate([r for r in rows if r['subset'] == subset])
                 for subset in ('dev', 'holdout')}

    adjudicated_violations = sorted(
        r['case_id'] for r in rows
        if r['label_source'] == 'user_adjudicated'
        and r['outcome'] in ('wrong_support', 'wrong_rejection'))
    fabrication_violations = sorted(
        r['case_id'] for r in rows
        if r['evidence_insufficient'] and r['model_verdict'] not in (None, '未确定'))
    incomplete = sorted(r['case_id'] for r in rows
                        if r['outcome'] in ('missing', 'wire_error'))
    disputed = sorted(c.case_id for c in labels.cases if c.disputed)
    pending_labels = sorted(c.case_id for c in labels.cases
                            if c.label_source == 'golden_yaml_pending_confirmation')
    holdout_denominator = by_subset['holdout']['denominator']

    blockers: list[str] = []
    if adjudicated_violations:
        blockers.append(f'已裁决正反例出现错误支持／错误否决：{adjudicated_violations}')
    if fabrication_violations:
        blockers.append(f'证据不足案例未保持未确定：{fabrication_violations}')
    if incomplete:
        blockers.append(f'存在缺失或解析失败案件：{incomplete}')
    if disputed:
        blockers.append(f'标签分歧未裁决：{disputed}')
    if pending_labels:
        blockers.append(f'金标来源待用户确认：{pending_labels}')
    if not holdout_denominator:
        blockers.append('留出集未建立（未参与提示词调试的样本分母为 0）')
    return {'overall': _aggregate(rows), 'by_target': by_target,
            'by_category': by_category, 'by_subset': by_subset,
            'hard_rules': {'adjudicated_violations': adjudicated_violations,
                           'fabrication_violations': fabrication_violations,
                           'incomplete': incomplete},
            'cases': rows,
            'semantic_gate_status': 'unver',
            'gate_ready': not blockers,
            'gate_blockers': blockers,
            'note': '本报告只汇总标定数据，不启用任何生产闸；启用须另行验收登记'
                    '模型、提示词／协议身份、各类实测结果与适用范围。'}


# ───────────────────────── fake transport 运行器 ─────────────────────────

class ScriptedTransport:
    """按标定案件标记分发的脚本化 transport（只用于 fake 跑通；不接付费面）。

    `responses`：case_id → JSON 可序列化回答对象；未脚本化的案件拒绝静默通过。
    """

    def __init__(self, responses: Mapping[str, Any]):
        self.responses = dict(responses)
        self.calls: list[str] = []

    def send(self, request: ChatRequest) -> ChatResponse:
        first_line = request.user.split('\n', 1)[0]
        match = re.match(r'【标定案件】(?P<case_id>.+?)｜(?P<target>.+)', first_line)
        if match is None:
            raise ValueError(f'请求缺少标定案件标记：{first_line!r}')
        case_id = match.group('case_id')
        if case_id not in self.responses:
            raise ValueError(f'未脚本化的标定案件：{case_id}')
        self.calls.append(case_id)
        payload = self.responses[case_id]
        return ChatResponse(
            content=payload if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False),
            finish_reason='stop',
            usage={'prompt_tokens': 0, 'completion_tokens': 0}, model='fake')


def run_calibration(labels: FrozenLabels, materials: Mapping[str, Material],
                    transport: ScriptedTransport, *, model: str = 'fake',
                    token_counter: Callable[[str], int] | None = None,
                    ) -> dict:
    """逐案组装请求 → fake transport → 解析 → 评分。零付费、零外发。"""
    wires: dict[str, VerdictWire | None] = {}
    wire_errors: set[str] = set()
    requests: list[dict] = []
    for case in labels.cases:
        material = materials.get(case.case_id)
        if material is None or not isinstance(
                material, _MATERIAL_BY_TARGET[case.target]):
            wires[case.case_id] = None
            reason = '标定材料缺失' if material is None else '材料形状与目标不符'
            requests.append({'case_id': case.case_id, 'prompt_chars': 0,
                             'parse_errors': [reason]})
            continue
        request = build_verification_request(
            case.case_id, case.target, material, model=model)
        response = transport.send(request)
        wire, errors = parse_verdict_payload(
            response.content, closed_items=material.closed_items(),
            texts=material.texts(),
            require_selection=case.target == 'stage2_fact')
        wires[case.case_id] = wire
        if wire is None:
            wire_errors.add(case.case_id)
        entry = {'case_id': case.case_id, 'prompt_chars': len(request.user),
                 'parse_errors': errors}
        if token_counter is not None:
            entry['prompt_tokens'] = token_counter(request.user)
        requests.append(entry)
    report = score_labels(labels, wires, wire_errors=frozenset(wire_errors))
    report['identity'] = {
        'protocol': CSF3_VERIFY_PROTOCOL,
        'rules_revision': CSF3_RULES_REVISION,
        'rules_sha256': hashlib.sha256(
            (_STAGE2_RULES + _PRESERVE_RULES + _DESIGN_RULES).encode('utf-8')
        ).hexdigest(),
        'model': model,
        'transport': 'scripted_fake',
        'paid': False,
    }
    report['requests'] = requests
    report['cost_note'] = ('fake transport 零付费；token 计数未注入时只报字符数，'
                           '真实调用与净变化对照须授权后实测（未测不作零）。')
    report['gate_blockers'] = list(report['gate_blockers'])
    report['gate_blockers'].append('非真实付费执行（scripted fake）')
    report['gate_ready'] = False
    return report


# ───────────────────────── CLI（只支持 fake 模式） ─────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m tests.semantic_calibration',
        description='CS-F3 离线标定 harness（免费前置：请求组装＋评分＋fake transport）。'
                    '真实付费执行须独立授权与正式接线，本入口不提供。')
    parser.add_argument('--labels', required=True,
                        help='冻结标签 JSON（FrozenLabels 形状）')
    parser.add_argument('--materials', required=True,
                        help='案件材料 JSON：{case_id: 材料对象}（按 target 分形状）')
    parser.add_argument('--responses', required=True,
                        help='脚本化回答 JSON：{case_id: 回答对象}（fake）')
    parser.add_argument('--out', required=True, help='报告输出 JSON 路径')
    args = parser.parse_args(argv)

    labels = FrozenLabels.model_validate(
        json.loads(Path(args.labels).read_text(encoding='utf-8')))
    raw_materials = json.loads(Path(args.materials).read_text(encoding='utf-8'))
    materials: dict[str, Material] = {}
    for case in labels.cases:
        if case.case_id in raw_materials:
            materials[case.case_id] = _MATERIAL_BY_TARGET[case.target].model_validate(
                raw_materials[case.case_id])
    responses = json.loads(Path(args.responses).read_text(encoding='utf-8'))
    report = run_calibration(labels, materials, ScriptedTransport(responses))
    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    blockers = report['gate_blockers']
    print(f'标定完成：{report["overall"]["denominator"]} 案；'
          f"gate_ready={report['gate_ready']}（阻断 {len(blockers)} 项）；报告 {out}")
    for blocker in blockers:
        print(f'  - {blocker}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
