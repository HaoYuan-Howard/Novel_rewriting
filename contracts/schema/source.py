#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source —— Characters v7／Blueprints v18 两份 Source Truth canonical（rc27）。

【职责与发布边界】Characters 保存人物条目（canonical＋surfaces 时间窗）、
人物/关系 initial_state 与名称结构；Blueprints 保存逐章拍、三类转移、场景、
跨章因果、世界约束及源表达。book／chapters／names／associate 观察经唯一
writer `novel/stages/stage2/compile.py` 编译发布，两份产物是下游真相源（S2-5）。

【拍模型】每一拍是一次状态转移：event 给人读，需延续的变化进入三类转移——
  • effects（事实）：叙述确立的事实变化，modality 区分 actual／planned／
    expected／conditional；变化键入闭集，作用域读时按 REGISTERED_STATE_KEYS 派生；
  • cognition（认知）：set 登记带极性的认知，unset 撤回当前接受；证据性质与
    读者可见性独立表达，reader 条目由 compiler 派生，观察面禁自写；
  • loops（悬念）：open 带问题，close 引用本拍已确认且读者可见的答案原子，
    resolution 仅由 compiler 派生。
拍另有 scene_boundary（new／continue）、temporal_mode（single／iterative）与
access（witnessed／narrated）。场景、命题、loop 等正式 ID 由 compiler 生成
（I-21）；章观察使用本地命题句柄或书级 facts 编号，跨章关联经正式观察协议。

【机械不变量】surface 时间窗 from≤until，until 只在有失效证据时给出；关系
目标禁自环，初态只引用人物闭集，op／mode／kind／变化键闭集且转移带逐字 basis。
scene_location 从拍级字段合成为 world 效应（scene 作用域），其余变化键为 book
作用域；effect 不再携带 disclosure／proposition／scope。证据闭合、命题/loop
引用与人物闭集由 owner 验收及最终 compile 防御复核，validator 不猜缺失语义。

【名称结构】`NameStructure` 显式登记姓氏家族、名、化名、词面同一性判定与构词
模板；人物同一性归 book，词面解析归 names。surface_forms 由 compiler 从部件
字面派生（歧义只接受 wire 的 form_overrides）；模板须逐字渲染源 surface，纯称谓
（form=None）不含本人姓名字面。`name_projection()` 提供专名与家族字面消费视图。
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

Slug = str  # 其它人物引用沿用原字符规则；人物表的保留字约束见 CharacterKey。


def _character_key_is_not_reader(key: str) -> str:
    """reader 是认知持有者保留字，不得注册成故事人物；其它字符规则保持原口径。"""
    if key == 'reader':
        raise ValueError('人物键禁 reader——reader 是读者认知持有者保留字，不是人物')
    return key


# 三种人物载体共用同一保留字闸；dict key 的错误仍定位到 characters.<key>，
# 不用顶层 model_validator 把付费 wire 的一个条目错误扩大为整份返修。
CharacterKey = Annotated[str, AfterValidator(_character_key_is_not_reader)]

# 命题的极性与证据性质分属不同轴：否认首先也是说法，不自动成为负命题确知。
Polarity = Literal['positive', 'negative']
EvidenceSupport = Literal['confirmed', 'claim', 'inference']

# ───────────────────── 变化键闭集与固定作用域（D8；CS-S Phase 3）─────────────────────
#
# 位置分轴：`scene_location`（当前所在，场景作用域，换场即失效）与 `residence`
# （长期居所，书作用域，换场不清除）。作用域由 compiler 绑定（I-15，可推导坐标
# 不让 LLM 声明）。
#
# 变化键闭集（CS-S Phase 3，2026-10-08）：从 rc15 冻结根三本书（5 个观察集、
# 844 拍、184 个真实键）聚类定稿——184 键中 98 个是只出现一次的书内自造键
# （`plan_for_sheng_er`、`attitude_toward_xie_jingzhi` 一类），全部归并到下列
# 通用维度，语义细节由 value 承载。模型只从闭集选键（提示词渲染清单，wire 与
# compile 双闸拒绝表外键）；`scene_location` 只由 compiler 从拍级字段合成，不进
# 模型面。键即 canonical（`same_state_dimension` 关联面随之消失）。扩展本表是
# 契约变化，随形状升版登记（I-13）。
CHANGE_KEYS: Final[tuple[str, ...]] = (
    'intent',               # 意图/计划/决心/追求（吸收 plan、*_intent、contingency_plan）
    'relationship',         # 关系事实与亲疏（含恩义、邻里、旧谊；吸收 relation）
    'residence',            # 长期居所与住居安排（借宿/暂住/迁居是本键的值替换）
    'body_state',           # 身体与外观（伤、衣饰遮蔽、束缚、外貌）
    'body_occupancy',       # 身体占用/换身（意识归属；吸收 body_consciousness 族）
    'marital_status',       # 婚姻状态（已婚/和离/形婚性质/婚约安排/入赘/禁离令）
    'pregnancy',            # 孕产（受孕、胎动、孕期照护）
    'attitude',             # 态度与性格取向（对特定对象的好恶、顺从、宠溺）
    'kinship',              # 亲缘（家族、血亲、家世）
    'identity',             # 身份事实（公开身份、身世、学生/供养等身份）
    'children',             # 子女（有无、增减）
    'occupation',           # 职业与差事
    'engagement',           # 婚约状态（含订婚进展与立场）
    'health',               # 健康与病症
    'emotional_state',      # 情绪（愧、惧、悲、忧等当下情绪状态）
    'agreement',            # 约定/承诺/盟约/约束
    'paternity',            # 生父（生母）身份认定
    'reputation',           # 名声与舆论
    'fertility',            # 生育力及其公开说法
    'secret',               # 秘密与隐瞒状态
    'conflict',             # 交恶与冲突状态
    'social_standing',      # 地位/头衔/处境
    'wealth',               # 财产与产业
    'age',                  # 年龄
    'possession',           # 持有物
    'address_term',         # 称呼方式事实
    'daily_training',       # 日常修习
)

#: 键作用域（R-2.14 读时派生）：`scene_location`＝scene（compiler 合成）、其余
#: 一律 book。键集＝CHANGE_KEYS＋`scene_location`（合成键）。
REGISTERED_STATE_KEYS: Final[dict[str, str]] = {
    **{key: 'book' for key in CHANGE_KEYS},
    'scene_location': 'scene',
}


#: 状态阅读标签：复用现役键的既定含义，不扩展维度；未知标签不得猜补。
STATE_KEY_LABELS: Final[dict[str, str]] = {
    'intent': '意图与计划',
    'relationship': '关系与亲疏',
    'residence': '长期居所',
    'body_state': '身体与外观',
    'body_occupancy': '身体与意识归属',
    'marital_status': '婚姻状态',
    'pregnancy': '孕产状态',
    'attitude': '态度',
    'kinship': '亲缘',
    'identity': '身份',
    'children': '子女状况',
    'occupation': '职业与差事',
    'engagement': '婚约状态',
    'health': '健康状态',
    'emotional_state': '情绪',
    'agreement': '约定',
    'paternity': '生父母身份',
    'reputation': '名声与舆论',
    'fertility': '生育力',
    'secret': '秘密与隐瞒状态',
    'conflict': '交恶与冲突状态',
    'social_standing': '地位与处境',
    'wealth': '财产与产业',
    'age': '年龄',
    'possession': '持有物',
    'address_term': '称呼方式',
    'daily_training': '日常修习',
    'scene_location': '当前所在',
}

#: 变化键的 schema 模式（含 compiler 合成的 `scene_location`——合成效应也走
#: `Effect` 模型）。
STATE_KEY_PATTERN: Final = '^(?:' + '|'.join(REGISTERED_STATE_KEYS) + r')$'

#: 模型面的变化键模式（不含 scene_location——那是 compiler 专用合成键）。
CHANGE_KEY_PATTERN: Final = '^(?:' + '|'.join(CHANGE_KEYS) + r')$'

#: 认知模式闭集（D2）：believes＝持有者以为为真（不代表世界为真）；asserts＝公开主张。
COGNITION_MODES: Final[tuple[str, ...]] = (
    'learns', 'suspects', 'confirms', 'hears_claim', 'believes', 'asserts')

#: 拍级获知通道闭集（CS-S Phase 3，2026-10-08，按 rc15 冻结根 844 拍真实分布
#: 冻结）：`witnessed`＝当场上演（默认，不写——对话/动作/转述，读者旁观）；
#: `narrated`＝叙述者直陈（例外显式标注——回溯、背景交代、概括、「叙述者点明」
#: 一类不经角色展演的内容；启发式实测约 3%，其中承载读者揭示的回溯/概括拍
#: 是 reader 派生的输入）。told 语义不单设值——「听到说法」由 know 的
#: hears_claim/asserts 模式逐条承载，不重复拍级标注。
ACCESS_CHANNELS: Final[tuple[str, ...]] = ('witnessed', 'narrated')

#: 拍级获知通道的 schema 模式。
ACCESS_PATTERN: Final = '^(?:' + '|'.join(ACCESS_CHANNELS) + ')$'


class SurfaceWindow(StrictModel):
    """称呼的时间窗：从 from_chapter 起可解析到该人物；until 只在文本给出明确
    失效依据时给出——「最后一次采到」不等于「从此不能用」（试点教训）。"""

    surface: str = Field(min_length=1)
    from_chapter: int = Field(ge=1)
    until_chapter: int | None = Field(default=None)
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _window_ordered(self) -> 'SurfaceWindow':
        if self.until_chapter is not None and self.until_chapter < self.from_chapter:
            raise ValueError(f'surface 窗口非法：from={self.from_chapter} until={self.until_chapter}')
        return self


class IdentityBinding(StrictModel):
    """语境指称与人物的同一性证据；不是通用别名或姓名释放许可。"""

    description: str = Field(min_length=1)
    appearance_refs: tuple[str, ...] = Field(min_length=1)
    identity_refs: tuple[str, ...] = Field(min_length=1)


class CharacterEntry(StrictModel):
    canonical: str = Field(min_length=1, description='全书最稳定称呼（内部主键显示名）')
    surfaces: tuple[SurfaceWindow, ...] = Field(min_length=1)
    identity_bindings: tuple[IdentityBinding, ...]

    @model_validator(mode='after')
    def _unique_surfaces(self) -> 'CharacterEntry':
        names = [s.surface for s in self.surfaces]
        if len(set(names)) != len(names):
            raise ValueError(f'{self.canonical} 的 surface 重复登记（复现应并入同一窗口）')
        return self


class StateTarget(StrictModel):
    """initial_state 的作用对象：人物或关系。"""

    kind: str = Field(pattern=r'^(character|relation)$')
    char_id: str | None = None
    subject: str | None = None
    object: str | None = None

    @model_validator(mode='after')
    def _shape_matches_kind(self) -> 'StateTarget':
        if self.kind == 'character' and not self.char_id:
            raise ValueError('character 目标缺 char_id')
        if self.kind == 'relation':
            if not (self.subject and self.object):
                raise ValueError('relation 目标缺 subject/object')
            if self.subject == self.object:
                raise ValueError('relation 目标自环')
        return self


class InitialStateAtom(StrictModel):
    """第 1 章第一拍之前已经成立的地基事实（书级初始事实的 Characters 部分）。

    CS-S 1b（2026-10-07）删 `knowledge`：初始知情不再挂在初态原子上（使用率
    0–8/13，「故事开始那一刻谁知道」在含引子的书里指不清）——Phase 2 由 Book
    facts 的 `known_at_start` 承载该语义，读者确认时点由认知转移落账。
    """

    state_id: str = Field(pattern=r'^s0_[A-Za-z0-9_-]+$')
    target: StateTarget
    #: CS-S Phase 3：键入闭集 `CHANGE_KEYS`（schema 层兜底直接构造的表外键）。
    key: str = Field(pattern=CHANGE_KEY_PATTERN)
    value: Any
    persistence: str = Field(pattern=r'^(stable|mutable)$')
    source_refs: tuple[str, ...] = Field(min_length=1)
    #: R10a a1：逐字证据（必填 ≥1，段属自身 source_refs）。
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)


class InitialKnowledgeAtom(StrictModel):
    """开场前已经确认的知识；来源为 Book 的有据 known_at_start 声明。

    不表示读者已经获知，也不制造一次剧情中的 learns 转移。正负命题共用
    同一正式命题身份，证据章节不决定故事中的确认时间。
    """

    knowledge_id: str = Field(pattern=r'^k0_\d{2,}$')
    holder: CharacterKey
    proposition: PropositionRef
    polarity: Polarity
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _has_statement(self) -> 'InitialKnowledgeAtom':
        if not self.proposition.statement or not self.proposition.statement.strip():
            raise ValueError('开场知识必须包含已闭合的正式命题文本')
        return self


class WorldConstraint(StrictModel):
    """写手若不知道就可能写错世界观的规则；人物判断/单次教训不收。"""

    constraint_id: str = Field(pattern=r'^wc_[A-Za-z0-9_-]+$')
    kind: str = Field(pattern=r'^(hard_rule|social_norm|institutional_fact|setting_constant)$')
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    #: R10a a1：逐字证据（必填 ≥1，段属自身 source_refs）。
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)


class CharactersCore(StrictModel):
    """RC2 形状的人物账 core view：人物条目 + 书级初始事实。

    Preserve/Brief/章节编译只消费这份 core view——`characters_sha256` 绑定它，
    名称结构修订不会连带使已购结果失效（F6 裁决）。"""
    characters: dict[CharacterKey, CharacterEntry] = Field(min_length=1)
    initial_state: tuple[InitialStateAtom, ...] = Field(default=(), min_length=0)
    initial_knowledge: tuple[InitialKnowledgeAtom, ...]

    @model_validator(mode='after')
    def _refs_close(self) -> 'CharactersCore':
        ids = [a.state_id for a in self.initial_state]
        if len(set(ids)) != len(ids):
            raise ValueError('state_id 重复')
        slugs = set(self.characters)
        knowledge_ids = [atom.knowledge_id for atom in self.initial_knowledge]
        if len(set(knowledge_ids)) != len(knowledge_ids):
            raise ValueError('knowledge_id 重复')
        knowledge = set()
        for atom in self.initial_knowledge:
            if atom.holder not in slugs:
                raise ValueError(f'initial_knowledge {atom.knowledge_id} 引用未知人物 {atom.holder}')
            identity = (atom.holder, atom.proposition.ref)
            if identity in knowledge:
                raise ValueError('开场知识的持有者/命题不得重复或同时接受正反结论')
            knowledge.add(identity)
        for atom in self.initial_state:
            t = atom.target
            refs = [t.char_id] if t.kind == 'character' else [t.subject, t.object]
            for ref in refs:
                if ref not in slugs:
                    raise ValueError(f'initial_state {atom.state_id} 引用未知人物 {ref}')
        return self


# ───────────────────────── 源名称结构（F6） ─────────────────────────
#
# 【为什么是独立一段而不是猜测】RC2 Characters 只有 canonical + surfaces，现役
# `name_distinctiveness` 靠「首字猜姓」——复姓被截断、纯称谓被当姓。名称结构把
# 姓氏家族、名、化名与表层称谓的构词关系显式登记（owner=Stage2 names 窄调用），
# 让 Naming／Hook 投影消费结构事实而不是猜。字段是 RC，经反例定版。

_FORM_VAR = re.compile(r'\{([a-z_]+(?::[A-Za-z0-9_-]+)?)\}')


def render_form(form: str, *, family: str | None, given: str | None,
                aliases: dict[str, str]) -> str:
    """用源部件渲染模板串。部件缺失或变量未知即抛错——不猜。

    变量闭集：{family}、{given}、{alias:<alias_id>}。模板串本身由 Stage2
    names wire 登记，渲染等式（结果逐字等于 surface）由 schema 校验。
    """
    def _sub(match: re.Match) -> str:
        var = match.group(1)
        if var == 'family':
            if not family:
                raise ValueError(f'模板 {form!r} 需要 family，但该人物未登记姓')
            return family
        if var == 'given':
            if not given:
                raise ValueError(f'模板 {form!r} 需要 given，但该人物未登记名')
            return given
        if var.startswith('alias:'):
            alias_id = var.split(':', 1)[1]
            literal = aliases.get(alias_id)
            if literal is None:
                raise ValueError(f'模板 {form!r} 引用未登记化名 {alias_id}')
            return literal
        raise ValueError(f'模板 {form!r} 携带未知变量 {{{var}}}')
    return _FORM_VAR.sub(_sub, form)


class FamilyForm(StrictModel):
    """家族级称谓的构词登记：如「陆家」= form '{family}家'（挂在家族下，不属于个人）。"""

    literal: str = Field(min_length=1)
    form: str = Field(min_length=1, description='模板串，只允许 {family} 变量')
    source_refs: tuple[str, ...] = Field(min_length=1)


class FamilyEntry(StrictModel):
    """一个姓氏家族：literal 可为多字（复姓）。同姓亲属共享 family_id；
    同姓但无亲属关系的不同人各用各的 family_id。"""
    literal: str = Field(min_length=1, description='姓字面（可复姓，如「皇甫」）')
    forms: tuple[FamilyForm, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _family_forms_render(self) -> 'FamilyEntry':
        for item in self.forms:
            try:
                rendered = render_form(item.form, family=self.literal, given=None, aliases={})
            except ValueError as exc:
                raise ValueError(f'家族 {self.literal} 称谓 {item.literal!r} 构词非法：{exc}')
            if rendered != item.literal:
                raise ValueError(
                    f'家族 {self.literal} 称谓 {item.literal!r} 与构词渲染不一致：'
                    f'{item.form!r} 渲染为 {rendered!r}')
        return self


class AliasEntry(StrictModel):
    """独立化名身份：不由新姓或新全名机械猜出，改名须显式赋值。"""
    alias_id: str = Field(pattern=r'^al_[A-Za-z0-9_-]+$')
    literal: str = Field(min_length=1)


class PersonNameStructure(StrictModel):
    """一个人物的名称结构：姓氏家族归属、名、化名与每个表层称谓的构词。

    `surface_forms` 的键必须恰好覆盖该人物在 Characters core 里的全部 surface；
    form=None 表示纯称谓（不含姓名部件）。"""
    family_id: str | None = None
    given: str | None = None
    aliases: tuple[AliasEntry, ...] = ()
    surface_forms: dict[str, str | None]

    def parts(self, families: dict[str, 'FamilyEntry']) -> dict[str, str]:
        """渲染模板用的源部件：family / given / 各化名字面。"""
        aliases = {a.alias_id: a.literal for a in self.aliases}
        family = families[self.family_id].literal if self.family_id else None
        parts: dict[str, str] = {'family': family} if family else {}
        if self.given:
            parts['given'] = self.given
        parts.update(aliases)
        return parts

    def _alias_map(self) -> dict[str, str]:
        return {a.alias_id: a.literal for a in self.aliases}

    def render_check(self, surface: str, form: str | None,
                     families: dict[str, 'FamilyEntry']) -> str | None:
        """返回校验错误；None 表示 surface 由 form 渲染逐字可得（或 form=None 合法）。"""
        if form is None:
            for literal in self.identity_literals(families):
                if literal and literal in surface:
                    return f'纯称谓 {surface!r} 含本人姓名字面 {literal!r}（防漏标：应登记构词）'
            return None
        try:
            rendered = render_form(form, family=(
                families[self.family_id].literal if self.family_id else None),
                given=self.given, aliases=self._alias_map())
        except ValueError as exc:
            return f'surface {surface!r} 构词 {form!r} 渲染失败：{exc}'
        if rendered != surface:
            return f'surface {surface!r} 与构词渲染不一致：{form!r} 渲染为 {rendered!r}'
        return None

    def identity_literals(self, families: dict[str, 'FamilyEntry']) -> tuple[str, ...]:
        family = families[self.family_id].literal if self.family_id else None
        return tuple(x for x in (family, self.given, *(a.literal for a in self.aliases)) if x)


class EvidenceQuote(StrictModel):
    """R10a a1：关键判断的逐字证据引文（wire 与 canonical 同形，I-11）。

    `segment` 是证据句段 id；`quote` 是**逐字**引用——resolver 判定 quote
    逐字存在于所引句段内、且段属于该条目自身的 source_refs（不归一化、
    不模糊匹配）。引文须含决定判断的限定（以为／觉得／像是／预计／约／
    几天后）——该要求属 prompt 面；机械面只管逐字命中与段归属。
    """

    segment: str = Field(pattern=r'^e\d+_\d+$')
    quote: str = Field(min_length=1)


class SourceNameResolution(StrictModel):
    """Stage2语义判断的局部姓名同一性；证据原字面不改，未决不得归一。

    CS-S 1c（2026-10-07）增 `not_name`：候选字面经窄调用判定为「不是人名」
    （笔误假设否决、亦非同一人物）——只作完整性证明，不参与归一（归一只
    消费 same），也不阻断发布（阻断只对 unresolved）；naming 投影不读
    resolutions，改名面不受影响。
    """
    literal: str = Field(min_length=1)
    status: Literal['same', 'unresolved', 'not_name']
    char_id: str | None
    surface: str | None
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def resolution_shape(self):
        if self.status == 'same' and (not self.char_id or not self.surface):
            raise ValueError('same必须指向人物与已登记称谓')
        if self.status != 'same' and (self.char_id is not None or self.surface is not None):
            raise ValueError('非same不得暗含已接受的名称映射')
        return self


class NameStructure(StrictModel):
    """整书源名称结构（Characters 的独立 RC 段；owner=Stage2 names 窄调用）。"""
    families: dict[str, FamilyEntry] = Field(min_length=1)
    people: dict[Slug, PersonNameStructure]
    resolutions: tuple[SourceNameResolution, ...] = ()

    def name_projection(self) -> 'NameProjection':
        """专名字面集与家族字面集（接管旧 `name_distinctiveness` 的有效职责：
        纯称谓不成姓、复姓不被截成首字）。"""
        by_person: list[NameProjectionEntry] = []
        proper: set[str] = set()
        family_literals: set[str] = set()
        for slug in self.people:
            person = self.people[slug]
            family = self.families[person.family_id].literal if person.family_id else None
            identity = person.identity_literals(self.families)
            bearing = tuple(sorted(
                s for s, form in person.surface_forms.items() if form is not None))
            by_person.append(NameProjectionEntry(
                char_id=slug, display='', family_literal=family,
                given=person.given,
                alias_literals=tuple(a.literal for a in person.aliases),
                proper_literals=tuple(sorted(set(identity))),
                name_bearing_surfaces=bearing))
            # 姓/名部件仅在 by_person 中供构词和候选召回；不能把“白”在
            # “明白”中的出现机械宣布成人物指称。
            proper.update(bearing)
            if family:
                family_literals.add(family)
        return NameProjection(
            proper_literals=tuple(sorted(proper)),
            family_literals=tuple(sorted(family_literals)),
            by_person=tuple(by_person))


class NameProjectionEntry(StrictModel):
    """单人物的名称投影：display 供 HookContext 渲染，其余供 Hook/Naming 消费。"""
    char_id: str
    display: str = Field(min_length=0, description='稳定显示称呼（core canonical）')
    family_literal: str | None
    given: str | None
    alias_literals: tuple[str, ...] = ()
    proper_literals: tuple[str, ...] = Field(min_length=0)
    name_bearing_surfaces: tuple[str, ...] = ()


class NameProjection(StrictModel):
    """名称结构的确定性投影：专名字面、家族字面与逐人物视图。"""
    proper_literals: tuple[str, ...]
    family_literals: tuple[str, ...]
    by_person: tuple[NameProjectionEntry, ...]

    def person(self, char_id: str) -> NameProjectionEntry | None:
        return next((e for e in self.by_person if e.char_id == char_id), None)


class CharactersArtifactV7(CharactersCore):
    """`stage2/characters/characters.json` 目标 v7（rc27）。

    `name_structure` 是 RC 字段，经反例定版；core view（characters +
    initial_state）形状与 RC2 相同。v5：initial_state 原子删 `knowledge`
    （初始知情由 Phase 2 的 facts known_at_start 承载）。v6：initial_state 键入
    `CHANGE_KEYS` 闭集（schema 层兜底直接构造的表外键）；rc20 起旧根按
    rc 关闭式拒读。v7：显式登记匿名或歧义指称的 identity_bindings；实体出场
    不再由姓名首次出现推断。旧根只读，新根重建。
    """
    contract_version: Literal['str06-rc27'] = 'str06-rc27'
    name_structure: NameStructure | None = None
    #: 来源账（F7a）：盘上键 `_provenance`。`exclude=True` 让内存态 dump 不带
    #: 它——prompt／请求字节与 F5e 已购身份逐字不变；落盘由 repository 注入。
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    def name_projection(self) -> NameProjection:
        """整书名称投影（display 取 core canonical）；未登记结构时是编程错误。"""
        if self.name_structure is None:
            raise ValueError('Characters 未登记名称结构；先完成 Stage2 names 窄调用')
        projection = self.name_structure.name_projection()
        filled = tuple(
            entry.model_copy(update={'display': self.characters[entry.char_id].canonical})
            for entry in projection.by_person)
        return projection.model_copy(update={'by_person': filled})

    def core_view(self) -> CharactersCore:
        """core view（characters + initial_state）：Preserve/章节编译的唯一消费面。"""
        return CharactersCore.model_validate(
            {'characters': {k: v.model_dump() for k, v in self.characters.items()},
             'initial_state': [a.model_dump() for a in self.initial_state],
             'initial_knowledge': [a.model_dump() for a in self.initial_knowledge]})

    @model_validator(mode='after')
    def _structure_closes(self) -> 'CharactersArtifactV7':
        ns = self.name_structure
        if ns is None:
            return self
        slugs = set(self.characters)
        unknown = set(ns.people) - slugs
        if unknown:
            raise ValueError(f'名称结构引用了 Characters 之外的人物：{sorted(unknown)}')
        missing = slugs - set(ns.people)
        if missing:
            raise ValueError(f'名称结构缺人物条目：{sorted(missing)}')
        for slug, person in ns.people.items():
            surfaces = {s.surface for s in self.characters[slug].surfaces}
            registered = set(person.surface_forms)
            if registered != surfaces:
                raise ValueError(
                    f'{slug} 的 surface_forms 未恰好覆盖全部 surface：'
                    f'缺 {sorted(surfaces - registered)} 多 {sorted(registered - surfaces)}')
            if person.family_id is not None and person.family_id not in ns.families:
                raise ValueError(f'{slug} 的 family_id {person.family_id} 未登记家族')
            for surface, form in person.surface_forms.items():
                problem = person.render_check(surface, form, ns.families)
                if problem:
                    raise ValueError(f'{slug}：{problem}')
        return self


class EffectTarget(StrictModel):
    """事实转移的作用对象：人物、关系或世界。loop 不是世界状态——v13 起悬念走
    独立的 loops 数组（D1/D10），不再挂成 effect target。"""

    kind: str = Field(pattern=r'^(character|relation|world)$')
    char_id: str | None = None
    subject: str | None = None
    object: str | None = None

    @model_validator(mode='after')
    def _shape_matches_kind(self) -> 'EffectTarget':
        pairs = {
            'character': ('char_id',), 'relation': ('subject', 'object'),
            'world': (),
        }
        needed = pairs[self.kind]
        given = [f for f in ('char_id', 'subject', 'object') if getattr(self, f)]
        if self.kind == 'relation' and self.subject == self.object:
            raise ValueError('relation 目标自环')
        if set(given) != set(needed):
            raise ValueError(f'effect target kind={self.kind} 与字段 {given} 不匹配（应为 {list(needed)}）')
        return self


class PropositionRef(StrictModel):
    """命题引用（D2/D12）：同章用 wire 本地标签，跨章用 prompt 列出的正式 ID。

    新建（本 wire 首次出现）必须带 statement；引用既有命题不得带 statement——
    「同一命题的文本只建立一次」由形状保证，不靠模型自觉（v12 允许复用 key 时
    重抄 statement，实测被改写）。标签与正式 ID 的分辨归 compile 层。
    """

    ref: str = Field(min_length=1)
    statement: str | None = None


class Effect(StrictModel):
    """事实转移（D1/D2）：叙述确立的事实变化。只记叙述确立的事实（含 unknown）；
    「谁认为、谁声称什么」只进 cognition，不在这里设 fact/belief/claim 之分。

    CS-S 1b（2026-10-07）删 `disclosure`／`proposition`／`scope`：404 效应实证
    concealed 0、effect 命题 0；作用域改**读时按登记表派生**
    （`REGISTERED_STATE_KEYS`：scene_location＝scene，其余＝book，R-2.14）——
    字段不再进 canonical。命题锚只在 cognition 上（揭示链由认知转移承载）。
    """

    target: EffectTarget
    #: CS-S Phase 3：键入闭集（含 compiler 合成的 `scene_location`；模型面闭集
    #: 见 `CHANGE_KEYS`，wire 闸拒绝表外键）。
    key: str = Field(pattern=STATE_KEY_PATTERN)
    op: str = Field(pattern=r'^(set|unset|add|remove|unknown)$')
    #: R10a a1（C2）：事实的时态身份——计划／预计／条件执行不是已发生；
    #: 「约半个月便回」是 expected，不是 actual 的既成时长。
    modality: str = Field(pattern=r'^(actual|planned|expected|conditional)$')
    reader_visible: bool
    value: Any = None
    #: R10a a1：逐字证据（必填 ≥1）——条目声称的判断必须能在所引句段内
    #: 逐字定位；缺引文的判断不被接受。
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _op_shape(self) -> 'Effect':
        if self.op == 'set' and self.value is None:
            raise ValueError('set 需要显式值（空字符串是合法空值）')
        if self.op in ('unset', 'unknown') and self.value is not None:
            raise ValueError('unset/unknown 不得伪造值')
        if self.op in ('add', 'remove') and self.value is None:
            raise ValueError('add/remove 需要显式值')
        return self


class Cognition(StrictModel):
    """人物对命题的接受或撤回；接受携带模式、极性与证据性质。

    unset 只撤回当前接受，不删除认知历史或证明世界命题为假。wire 的 holder
    禁 reader；正式 reader 条目由 compiler 从明确可见的原子派生，access
    仅表示呈现通道，不能代替真实性或可见性判定。
    """

    proposition: PropositionRef
    holder: str = Field(min_length=1)
    op: Literal['set', 'unset']
    mode: str | None = Field(pattern=r'^(' + '|'.join(COGNITION_MODES) + r')$')
    polarity: Polarity | None
    support: EvidenceSupport
    reader_visible: bool
    #: R10a a1（C1）：逐字证据（必填 ≥1）——「觉得像是看穿了」的怀疑必须
    #: 引到含限定的原句，防把猜测升格为确知后无法回溯。
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _op_shape(self) -> 'Cognition':
        if self.op == 'unset':
            if self.mode is not None or self.polarity is not None:
                raise ValueError('认知 unset 的 mode／polarity 必须为 null')
        elif self.mode is None or self.polarity is None:
            raise ValueError('认知 set 必须显式给出 mode／polarity')
        if self.mode in ('learns', 'confirms') and self.support != 'confirmed':
            raise ValueError('learns／confirms 只接受 confirmed 证据，推测和说法不得升级确知')
        return self


class LoopAction(StrictModel):
    """悬念转移：open 带问题；close 引用本拍答案原子，文本由代码派生。

    loop 只能 open 一次；问题文本只出现在 open 处，之后不可改写——v12 把 loop
    挂成 effect（op open/close）时，模型反复用 set 改写问题文本（F9 与本轮实证），
    独立数组＋形状约束从此结构性禁绝。
    """

    ref: str = Field(min_length=1)
    op: str = Field(pattern=r'^(open|close)$')
    question: str | None = None
    resolution: str | None = None
    answer_refs: tuple[Annotated[str, Field(
        pattern=r'^(?:b\d+_\d+\.)?[ec][1-9]\d*$')], ...]

    @model_validator(mode='after')
    def _op_shape(self) -> 'LoopAction':
        if self.op == 'open':
            if not (isinstance(self.question, str) and self.question.strip()):
                raise ValueError('open 必须带悬念的问题内容（question 非空）')
            if self.resolution is not None:
                raise ValueError('open 不得带 resolution')
            if self.answer_refs:
                raise ValueError('open 不得带答案引用')
        else:
            if not self.answer_refs:
                raise ValueError('close 必须引用本拍答案原子')
            if len(set(self.answer_refs)) != len(self.answer_refs):
                raise ValueError('close 答案原子引用重复')
            if self.resolution is not None and not self.resolution.strip():
                raise ValueError('派生 resolution 不得为空白')
            if self.question is not None:
                raise ValueError('close 不得改写问题文本')
        return self


class Beat(StrictModel):
    """一拍＝一次状态转移（D1）：event 给人读；effects／cognition／loops 承载延续。

    会改变后续人物选择、关系、位置、目标、知情或未决问题的内容，必须同时进入三类
    转移——只留在 event 里该拍就不完整（D7；完整性由 fold 的空转移拍比例读数配合
    离线核读证明，不加声明字段）。
    """

    beat_id: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    event: str = Field(min_length=1)
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    source_refs: tuple[str, ...] = Field(min_length=1)
    #: 场景边界（D1）：new＝本拍开新场景（旧场景的 scene 作用域状态就此失效）；
    #: continue＝延续当前场景（可跨章，见 SceneSpan）。
    scene_boundary: str = Field(pattern=r'^(new|continue)$')
    #: 时间模式（D1）：single＝单次行为；iterative＝重复行为（如「连续三夜」）。
    temporal_mode: str = Field(pattern=r'^(single|iterative)$')
    #: 获知通道（CS-S Phase 3）：witnessed＝当场上演（默认）；narrated＝叙述者
    #: 直陈（回溯/背景/概括——reader 派生的输入之一）。
    access: str = Field(default='witnessed', pattern=ACCESS_PATTERN)
    effects: tuple[Effect, ...] = Field(default=(), min_length=0)
    cognition: tuple[Cognition, ...] = Field(default=(), min_length=0)
    loops: tuple[LoopAction, ...] = Field(default=(), min_length=0)


class CausalEdge(StrictModel):
    from_beat: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    to_beat: str = Field(pattern=r'^b[0-9A-Za-z_-]+$')
    kind: str = Field(pattern=r'^(causes|motivates|enables|setup_payoff)$')

    @model_validator(mode='after')
    def _not_self(self) -> 'CausalEdge':
        if self.from_beat == self.to_beat:
            raise ValueError('因果边自指')
        return self


class SceneSpan(StrictModel):
    """compiler 生成的场景分段（D1/D8）：scene_id 全书唯一（按开场序编）。

    跨章场景在其续章的蓝图里同样出现（beat_ids 只收该章的拍）——fold 据此保留
    局部状态跨章延续，直到下一处 new 边界才失效。
    """

    scene_id: str = Field(pattern=r'^sc[0-9]+$')
    beat_ids: tuple[str, ...] = Field(min_length=1)


class ChapterBlueprint(StrictModel):
    chapter: int = Field(ge=1)
    beats: tuple[Beat, ...] = Field(min_length=1)
    #: compiler 生成：本章 beats 的场景分段（与拍序一致、恰好覆盖全部拍）。
    scenes: tuple[SceneSpan, ...] = Field(min_length=1)
    causal_edges: tuple[CausalEdge, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _unique_beats(self) -> 'ChapterBlueprint':
        ids = [b.beat_id for b in self.beats]
        if len(set(ids)) != len(ids):
            raise ValueError(f'章 {self.chapter} 内 beat_id 重复')
        known = set(ids)
        for edge in self.causal_edges:
            if edge.to_beat not in known:
                raise ValueError(f'章 {self.chapter} 因果边 to_beat 不在本章：{edge.to_beat}')
        spanned = [bid for scene in self.scenes for bid in scene.beat_ids]
        if sorted(spanned) != sorted(ids):
            raise ValueError(
                f'章 {self.chapter} 的场景分段未恰好覆盖全部拍：'
                f'缺 {sorted(known - set(spanned))} 多 {sorted(set(spanned) - known)}')
        return self


from contracts.schema.expression import SourceExpression


class BlueprintsArtifactV18(StrictModel):
    """`stage2/blueprints/chapter_blueprints.json` 目标 v18（rc27）。

    v13（质量改进批次二）：拍模型从 effects/reveals 改为 effects／cognition／loops
    三类转移，拍上带场景边界与时间模式，章蓝图带 compiler 生成的场景分段。跨章
    from_beat 引用的存在性归 compile（需要全书 beat 集合）；命题与 loop 的跨章
    引用闭合归 compile（正式 ID 登记表）。
    v14（R9）：增加带证据的 source_expression；编号与阅读统计由 compiler 派发。
    v15（R10a a1）：转移带逐字 basis（Effect 另有 modality 闭集）；compiler 派生
    `evidence` 证据窗口（被引句段全文＋同章前后各一句，机械合并；精确支持引用
    在条目 basis 里，邻句送达≠邻句为证据）。
    v16（CS-S 1b，2026-10-07）：Effect 删 `disclosure`／`proposition`／`scope`
    （作用域读时按登记表派生）；作用域语义见 `REGISTERED_STATE_KEYS`。
    v17（CS-S Phase 3，2026-10-08）：Beat 增 `access`（获知通道闭集）；效果键入
    `CHANGE_KEYS` 闭集；cognition 的 reader 条目＝compile 按拍级 access 派生的
    读者知情（wire 禁 reader——蓝图里的 reader 全部是派生物）。
    v18（rc27）：认知增加接受／撤回、极性、证据性质和读者可见性；效应显式
    标明可见性；悬念答案引用本拍确定成立且可见的原子，resolution 由代码派生。
    读者确知不再由通道或自由文本收束推断，旧版只读、新根重建。
    """

    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    source_expression: SourceExpression
    world_constraints: tuple[WorldConstraint, ...] = Field(default=(), min_length=0)
    chapters: tuple[ChapterBlueprint, ...] = Field(min_length=1)
    #: R10a a1：compiler 派生的证据窗口（段 id → 该段全文＋同章前后各一句，
    #: 重叠机械合并）。只收被任一 basis／source_refs 引用的段（I-16 派生物）。
    evidence: dict[str, str] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_contiguous(self) -> 'BlueprintsArtifactV18':
        ordinals = [c.chapter for c in self.chapters]
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError(f'章号必须从 1 连续，实得 {sorted(ordinals)}')
        for chapter in self.chapters:
            for beat in chapter.beats:
                atoms = {
                    **{f'{beat.beat_id}.e{index}': atom
                       for index, atom in enumerate(beat.effects, 1)},
                    **{f'{beat.beat_id}.c{index}': atom
                       for index, atom in enumerate(beat.cognition, 1)},
                }
                for action in beat.loops:
                    if action.op != 'close':
                        continue
                    if not action.resolution or not action.resolution.strip():
                        raise ValueError(f'{beat.beat_id} 悬念 close 缺少代码派生 resolution')
                    for ref in action.answer_refs:
                        atom = atoms.get(ref)
                        if atom is None:
                            raise ValueError(f'{beat.beat_id} 悬念答案必须引用本拍正式原子：{ref}')
                        if not atom.reader_visible:
                            raise ValueError(f'{beat.beat_id} 悬念答案未向读者展示：{ref}')
                        if isinstance(atom, Effect):
                            if atom.modality != 'actual' or atom.op != 'set':
                                raise ValueError(f'{beat.beat_id} 悬念答案不是已成立事实：{ref}')
                        elif (atom.op != 'set' or atom.mode not in ('learns', 'confirms')
                              or atom.support != 'confirmed'):
                            raise ValueError(f'{beat.beat_id} 悬念答案不是已确认认知：{ref}')
        return self
