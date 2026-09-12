#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 当前正式载体：共享主体、知识与完整剧情蓝图。

Global 的语义属性由模型提出，证据由编译器回取。Blueprint 的完整正文、稀疏状态、
约束和段级证据使用 plot.py 契约；本模块保存章集合与来源、编辑身份历史。
载体检查引用与封闭形状，跨产物检查来源，语义正确性仍由正式审阅裁决。
"""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import Field, StringConstraints, field_validator, model_validator

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, BLUEPRINT_ARCHITECTURES,
    KNOWLEDGE_SUBJECT_KINDS, RELATION_VISIBILITY_KINDS, SECRET_SETUP_KINDS,
    NAME_DISTINCTIVENESS,
    TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import LlmProvenance, Sha256
from contracts.schema.evaluation import GlobalReview, GlobalReviewDecision

#: 「谁在这么叫」。取值是**某个 `char_id`**，或 `ADDRESS_BY` 里那两个字面之一。
#: ⚠ `ADDRESS_BY` 记的是**三类**（`char_id` 是类名不是值），所以这里必须写成模式，
#: 不能写成 `Literal[ADDRESS_BY]`——那会把全部真实的 `char_001` 拒掉。
AddressBy = Annotated[str, StringConstraints(pattern=r'^(char_\d+|narration|public)$')]
ChapterOrdinal = Annotated[int, Field(strict=True, ge=1)]

assert set(ADDRESS_BY) == {'char_id', 'narration', 'public'}, \
    'ADDRESS_BY 改了取值面而 AddressBy 那个模式没跟着改——两处必须一起看'


class SourceSpan(StrictModel):
    """正式制品里的原文引用；只保存当前可验证的原文字节与坐标。"""

    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode='after')
    def _end_is_after_start(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError('source span end must be greater than start')
        return self


class EvidenceLocator(StrictModel):
    """模型/人工入口交的短定位；正式产物里不保留 ``locator`` 字节。"""

    chapter_ordinal: ChapterOrdinal
    locator: str = Field(min_length=1, max_length=160)


class FactEvidence(StrictModel):
    """一条自由事实的正式原文证据。

    ``source=None`` 是可读、可归档的失败态：归一找不到或发现多处命中时必须先
    落 `.INVALID`，再让 typed edit / `--revalidate` 有东西可修，不能在 Pydantic
    构造前崩掉而永久锁死一次有效付费响应。
    """

    chapter_ordinal: ChapterOrdinal
    source: SourceSpan | None


class RelationVisibilityPublic(StrictModel):
    kind: Literal['public']
    from_chapter: ChapterOrdinal


class RelationVisibilityViaSecret(StrictModel):
    kind: Literal['via_secret']
    secret_id: str = Field(pattern=r'^secret_\d{2}$')


RelationVisibility = Annotated[
    RelationVisibilityPublic | RelationVisibilityViaSecret,
    Field(discriminator='kind'),
]


class CharacterRef(StrictModel):
    kind: Literal['character']
    char_id: str = Field(pattern=r'^char_\d{3}$')


class GroupRef(StrictModel):
    kind: Literal['group']
    label: str = Field(min_length=1)

    @model_validator(mode='after')
    def _label_has_visible_text(self) -> GroupRef:
        if not self.label.strip():
            raise ValueError('group label must contain visible text')
        return self


KnowledgeSubject = Annotated[CharacterRef | GroupRef, Field(discriminator='kind')]


class KnowledgeEvent(StrictModel):
    """世界内某个主体从哪一章起知道这条 secret。"""

    subject: KnowledgeSubject
    known_from_chapter: ChapterOrdinal
    evidence: FactEvidence


assert set(RELATION_VISIBILITY_KINDS) == {'public', 'via_secret'}, \
    'RelationVisibility 判别联合与契约取值面分家'
assert set(KNOWLEDGE_SUBJECT_KINDS) == {'character', 'group'}, \
    'KnowledgeSubject 判别联合与契约取值面分家'


# ─────────────────────────────────────────────────────────────────────────────
# 一、`global_grounding.json`
# ─────────────────────────────────────────────────────────────────────────────

class Address(StrictModel):
    """一条称谓。`R-2.2` ~ `R-2.5` 全部落在这里。"""

    #: 长度 1~10。**[HARD] 整条不许是代词**（`R-2.3`）：代词相对说话人，
    #: 收了它下游必然取到「我妈」这种说法。**[INFO] 必须在原文里原样搜得到**（`R-2.2`）。
    #: ⚠ 落盘前会被**最长子串回取**改写；成功改写不重复持久化展示账。
    surface: str = Field(min_length=1)

    #: **闭合**。盘上 `narration` 86 · `char_id` 52 · `public` 18。
    #:
    #: ⚠ **群体名一律压成 `public`**（「苏府下人」解析不出人），因为 `by` 的消费是
    #: **派发键**——一个解析不出人的串对派发毫无用处。已知代价：丢掉「哪个群体」。
    #: 盘上兜底 0 次，⚠ 这**不是**「没发生过」的证据，它同时也可以是
    #: 「这三本恰好没有群体称呼」。
    #: ⚠ 这与 `Secret.knowledge_events[].subject` 的 GroupRef 处置**故意相反**：
    #: 那边要的是「谁知情」，群体本身就是答案，逼它写 id 只会逼出假 id。
    #: **同一个「无名群体」在两个字段上的正确处置方向相反**——
    #: 改之前先问它在这里是**派发键**还是**答案**。
    by: AddressBy

    #: `address`（当面）｜ `reference`（背后）。盘上 44 / 112。
    context: Literal[ADDRESS_CONTEXT]

    #: ≥1 且在全书章范围内。**[HARD] 不早于该 `surface` 在原文里首次出现的章**——
    #: **单边**（`R-2.5`）。⚠ 落盘前会被**只往后抬**。
    from_chapter: ChapterOrdinal


class Character(StrictModel):
    """`entities.characters` 的一个人。盘上 75 个。"""

    #: 长度 1~8。**[HARD] 全书唯一**（`R-2.1`）。
    canonical: str = Field(min_length=1)

    #: 长度 1~10，盘上共 156 条。
    address: list[Address] = Field(min_length=1)

    #: **无守卫 → `B-08`**（`R-2.10`）。它**不下发给写作节点**——
    #: 只给确定性代码用（Stage5 成稿人名检查与专名过滤 / Stage7 残留探针）。
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]

    #: 静态身份，长度 12~106。**[HARD] 非空 · 不含结局词 · 不含模棱语 ·
    #: 不得与 `canonical` 同字**（`R-2.6`）。
    #:
    #: ⚠ **它是一条已知的、未修的结局旁路**：实测某本的 `孟青兰` 写着
    #: 「后嫁富商老王为妾」——那是这个角色的**归宿**，属【硬禁·结局】要挡的东西，
    #: 而词表只抓「转抄式结局词」（实测一本上命中 0/17 而人读真违规 6/17）。
    #: **它不是恒为 0，只是判别力低**；校验器为此常驻打印一条 `[????] role 不含结局`。
    role: str = Field(min_length=1)
    role_evidence: FactEvidence

    #: 长度 2~58；**盘上 6/75 为 `null`**（键恒在）。只统计（`R-2.7`）。
    #: ⚠ **`fate` 一概不下发给写手**——写手提前知道谁最后会死，第 1 章的伏笔就写过明。
    fate: str | None
    fate_evidence: FactEvidence | None

    @model_validator(mode='after')
    def _fate_and_evidence_agree(self) -> Character:
        if (self.fate is None) != (self.fate_evidence is None):
            raise ValueError('fate 与 fate_evidence 必须同时为空或同时非空')
        return self


class Relation(StrictModel):
    """一条有向关系。盘上 70 条。"""

    relation_id: str = Field(pattern=r'^rel_\d{2}$')

    #: **必须是 `characters` 里的 `char_id`**（地点/机构/物品不入 relations）。
    #: **[INFO] 两端可解析**——不是 HARD。
    subject_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)

    predicate: str = Field(min_length=1)
    predicate_evidence: FactEvidence

    #: **读者**何时能知道这条关系；与下方世内成立窗口正交。
    visibility: RelationVisibility

    #: 关系**世内**成立的章。**[HARD] 在章范围内**（`R-2.8`）。
    #:
    #: ⚠⚠ **它是【世内生效时点】，不是【读者知情时点】。** 秘密类关系上两者系统性错位，
    #: 于是 relations 是 secrets 之外的**第二条剧透通道**。
    #: **不加字段**（同一语义两个落点必然漂移），在**下发侧**解决：两端人名都在某条
    #: secret 的 `fact` 里、且 `valid_from` 落在隐瞒窗口内的关系，改按
    #: `first_reveal_chapter` 闸门。
    #: ⚠ 那条闸门的前件是 `setup_chapters`（冷揭示的 secret 没有隐瞒窗口，不参与）。
    #: ⚠⚠ **旧注释写的「盘上 20/21 是空数组 ⇒ 等于没开」是已删语料时代的读数，已作废**
    #: （2026-08-21 复核）。当前盘上是 **6/22 冷揭示**，且**逐模型分布很不一样**：
    #: Pro 那本 **0/4**（埋点全填了，闸门全程有效），Flash 三本 1/7 · 1/4 · **4/7**。
    #: ⇒ 闸门在 Pro 产的 global 上是开着的；Flash 会掉一部分，那是**生成侧换模型**
    #: 要盯的读数，不是这条闸门本身失效。
    valid_from_chapter: ChapterOrdinal

    #: 截止仍成立的最后一章（**含该章**）；没有截止章填 `null`（键恒在）。
    #: 旧名 `valid_to_chapter` 没说清边界是否包含，消费者实际一直按 ``>= 当前章``
    #: 判定，故随 GlobalGrounding v3 同批改名为 `valid_through_chapter`。盘上 22/70 非 `null`。
    valid_through_chapter: ChapterOrdinal | None

    @model_validator(mode='after')
    def _valid_window_is_ordered(self) -> Relation:
        if (self.valid_through_chapter is not None
                and self.valid_through_chapter < self.valid_from_chapter):
            raise ValueError(
                'valid_through_chapter 不能早于 valid_from_chapter')
        return self


class Setting(StrictModel):
    """`canon.setting` —— 全链**仅有的三处固定中文 JSON key** 里的两处。

    ⚠ 盘上 12 类产物 1269 条路径逐个扫过，固定的中文键只有：这两个 + stage7
    `name_map._groups[].{原姓,成员}`（另一大批中文键是 `name_map` 按人名做的映射键
    ——那是**数据不是 schema**，别混着数）。

    ⚠ 它们还有一处**第二落点**：`stage4/writer_context.json` 的
    `chapters[].facts.setting.*` 原样透传（31/31 章）⇒ **改 key 要跟着动那一层**。
    ⚠ **不要顺手改成拼音/英文**，要改就单独当一次裁决做。
    """

    #: 长度 12~42。故事事实，**不是文体判断**。
    era: str = Field(alias='时代背景', min_length=1)
    era_evidence: FactEvidence

    #: 长度 33~69。
    #: ⚠ **逐章闸门只挡转抄式泄底**（字面子串包含），实测在唯一有 secrets 的那本上
    #: 命中 **0/8**，包括一条已知真泄漏。命中 0 时打常驻 `[????]`，**不打 `[OK]`**。
    worldview: str = Field(alias='世界观', min_length=1)
    worldview_evidence: FactEvidence


class WorldRuleVersion(StrictModel):
    """一条规则的一个版本。**规则正文的唯一落点。**"""

    #: 从头没变过的规则也要给一条，填 `1`。
    effective_from_chapter: ChapterOrdinal

    #: 长度 23~94。**必须连「怎么运作 / 怎么判读」一起写**——只写「存在」等于没记。
    #: **无守卫 → `B-08`**。
    #: ⚠ 下游取正文时**无兜底，缺失即显式终态**（`or description` 那个兜底就是漏洞本身）。
    desc: str = Field(min_length=1)
    desc_evidence: FactEvidence


class WorldRule(StrictModel):
    """一条世界规则。盘上 11 条。

    ⚠ 「`desc` 不许出现人物名字」**没有产地判据**，现役的是一条按**叫法时点**扣住的
    下发闸门：规则里用到的所有叫法各自 `from_chapter` 取**最大值**，那一章之前不下发。
    **闸门按【叫法】不按【角色】**——同一个人从 ch1 就在场（叫「龟公」），
    泄底的是「百晓生」这个 ch8 才出现的叫法。
    ⚠ **盘上这道闸门什么也没拦住**：三本各报 2/1/1 条含人名的规则，其中两本的全部叫法
    在 ch1 就叫得出 ⇒ 等于全程下发。**报出这个基线才知道它有没有判别力。**
    """

    rule_id: str = Field(pattern=r'^wr_\d{2}$')

    #: **7 值闭合**，⚠ 盘上只见 4 个，**另三个零样本**。
    kind: Literal[WORLD_RULE_KINDS]

    #: **至少一条**。盘上 10 条长度 1 · 1 条长度 2。
    versions: list[WorldRuleVersion] = Field(min_length=1)

    @model_validator(mode='after')
    def _version_starts_are_unique(self) -> WorldRule:
        starts = [row.effective_from_chapter for row in self.versions]
        if len(starts) != len(set(starts)):
            raise ValueError(f'{self.rule_id}: duplicate effective_from_chapter')
        return self


class SecretSetupCold(StrictModel):
    """冷揭示：这条秘密**没有埋点**，读者在揭示那一章才第一次接触到它。"""

    kind: Literal[SECRET_SETUP_KINDS[0]]


class SecretSetupForeshadowed(StrictModel):
    """有埋点：那几章把事实摆在读者眼前了，**但当时读来是别的意思**。"""

    kind: Literal[SECRET_SETUP_KINDS[1]]

    #: **[HARD] 每个都严格 `<` 首次揭示章**（`R-2.9`）。
    #: ⚠ 判据断言的是 `_provenance.setup_chapters_pruned` 那个计数，不是本字段的值：
    #: 裁剪跑在落盘前，再断言字段值就永远为真。
    chapters: list[ChapterOrdinal] = Field(min_length=1)


#: 埋点的两种形态。**判别联合而不是可空列表**（2026-08-23 v2）。
#:
#: 【它修的是什么】v1 里这一格是 `setup_chapters: list[int]`，于是 `[]` 同时表示
#: 「模型声明这是冷揭示」与「模型压根没想过这件事」——读的人无从分辨，
#: 而这一格恰恰是两道下发闸门（关系闸门的隐瞒窗口、secret 闸门第②臂）的前件。
#: 判别联合逼模型**显式选一边**。
#:
#: ⚠ **它没有、也不可能消除第三种歧义**：归一裁剪（`_prune_setups`）跑在落盘前，
#: 把不早于首次揭示章的埋点裁掉；全裁光的那条会**降级成 `cold`**，字段值上与
#: 真·冷揭示同形。那一种仍然只能靠 `_provenance.setup_chapters_pruned` 分开，
#: 与 v1 一样。别把 v2 的收益说成「冷揭示不再有歧义」——它消除的是**模型意图**
#: 那一层的歧义，不是**归一改写**那一层的。
SecretSetup = Annotated[
    SecretSetupCold | SecretSetupForeshadowed, Field(discriminator='kind')]


class Secret(StrictModel):
    """一条秘密。**一条只承载一个原子事实。**

    首次揭示时点是单值，因此一条秘密不能借事件数组混装多个揭示阶段；`fact`
    是否仍塞了多个原子事实由人工复核判断。
    """

    secret_id: str = Field(pattern=r'^secret_\d{2}$')

    #: 长度 17~48。写**被瞒住的真相**，不是揭示那章的情节。
    fact: str = Field(min_length=1)
    fact_evidence: FactEvidence

    #: 世界内知情人时间线。具名人物是 registry 外键；无名群体保留自由文本 label。
    #: 旧 `character_known_by` 没有时间维，不能回答“第几章起知道”，已随 v3 退役。
    knowledge_events: list[KnowledgeEvent] = Field(min_length=1)

    #: 埋点。取值面与「为什么是判别联合」见 `SecretSetup`。
    setup: SecretSetup

    #: 这条原子秘密第一次对读者点破的阅读序章号；逐章剧透闸门只消费这一格。
    first_reveal_chapter: ChapterOrdinal

    @model_validator(mode='after')
    def _knowledge_subjects_are_unique(self) -> Secret:
        """同一个世界内主体只保留最早知情时点。"""
        subjects = [
            (event.subject.kind,
             getattr(event.subject, 'char_id', None),
             getattr(event.subject, 'label', None))
            for event in self.knowledge_events
        ]
        if len(subjects) != len(set(subjects)):
            raise ValueError(f'{self.secret_id}: duplicate knowledge subject')
        return self


EvidenceT = TypeVar('EvidenceT')
HookEvidenceT = TypeVar('HookEvidenceT')


class HookEvidenceLocator(StrictModel):
    """源文案内的短定位；不伪造正文章号。"""

    locator: str = Field(min_length=1, max_length=160)


class HookFactEvidence(StrictModel):
    """HookCopy 的精确码点证据；失败定位保留供免费修订/复验。"""

    source: SourceSpan | None
    unresolved_locator: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _located_evidence_has_no_failed_locator(self) -> HookFactEvidence:
        if self.source is not None and self.unresolved_locator is not None:
            raise ValueError('已定位文案证据不得同时保留失败 locator')
        return self


class SupportedBlurbFact(StrictModel, Generic[EvidenceT]):
    """源文案的一条事实主张及正文支持；语义支持仍交 Global Commander。"""

    kind: Literal['supported']
    claim_id: str = Field(pattern=r'^hook_\d{3}$')
    claim: str = Field(min_length=1)
    evidence: EvidenceT


class HookSupportedBlurbFact(StrictModel, Generic[HookEvidenceT]):
    """原作者在钩子中独立给出的信息；不产生正文章补写义务。"""

    kind: Literal['hook_supported']
    claim_id: str = Field(pattern=r'^hook_\d{3}$')
    claim: str = Field(min_length=1)
    hook_evidence: HookEvidenceT


class UnresolvedBlurbFact(StrictModel):
    kind: Literal['unresolved']
    claim_id: str = Field(pattern=r'^hook_\d{3}$')
    claim: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ProposedOpeningScope(StrictModel, Generic[EvidenceT]):
    """从首章到 through_chapter 的连续材料范围；接受由既有 Global 审阅负责。"""

    kind: Literal['proposed']
    through_chapter: ChapterOrdinal
    rationale: str = Field(min_length=1)
    boundary_evidence: EvidenceT


class UnresolvedOpeningScope(StrictModel):
    kind: Literal['unresolved']
    reason: str = Field(min_length=1)


class SourceBlurbGrounding(StrictModel, Generic[EvidenceT, HookEvidenceT]):
    """正式消费 stage1/hook_copy 的事实与开篇范围，不保存文学分析或正文副本。"""

    facts: list[Annotated[
        SupportedBlurbFact[EvidenceT] | HookSupportedBlurbFact[HookEvidenceT] | UnresolvedBlurbFact,
        Field(discriminator='kind'),
    ]]
    opening: Annotated[
        ProposedOpeningScope[EvidenceT] | UnresolvedOpeningScope,
        Field(discriminator='kind'),
    ]

    @model_validator(mode='after')
    def _unique_claim_ids(self) -> SourceBlurbGrounding:
        ids = [fact.claim_id for fact in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError('源文案 claim_id 不能重复')
        return self


class Canon(StrictModel):
    setting: Setting
    world_rules: list[WorldRule]
    secrets: list[Secret]

    @model_validator(mode='after')
    def _stable_ids_are_unique(self) -> Canon:
        for label, values in (
            ('world rule', [row.rule_id for row in self.world_rules]),
            ('secret', [row.secret_id for row in self.secrets]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f'duplicate {label} ids')
        return self


class Entities(StrictModel):
    #: 键即 `char_id`。盘上 75 个，全部形如 `char_\\d{3}`，主角恒 `char_001`。
    #: ⚠ **键的格式没有任何判据在查**，下游只做成员检查（`x in registry`）——
    #: 所以这里**故意不加 pattern**：加一个就是替一个不存在的守卫声称了存在。
    #: 形状真正承重的地方在别处（`KnowledgeSubject` 的判别联合）。
    characters: dict[str, Character]

    relations: list[Relation]

    @model_validator(mode='after')
    def _relation_ids_are_unique(self) -> Entities:
        values = [row.relation_id for row in self.relations]
        if len(values) != len(set(values)):
            raise ValueError('duplicate relation ids')
        return self


from contracts.schema.knowledge import (
    KnowledgeGrounding as GlobalGrounding, GlobalProvenance,
)


class BlueprintCharacterBinding(StrictModel):
    surface: str = Field(min_length=1)
    identity_revealed_from: ChapterOrdinal


class BlueprintCharacterProjectionChapter(StrictModel):
    """某一章真正交给 Blueprint 的已现称名闭集。

    只保存 Characters 中真实 prompt/validator 消费的称谓及身份映射时点。
    role、关系、秘密、设定与证据不进入本投影；审阅另按实际支持材料建立依赖。
    """

    chapter_ordinal: ChapterOrdinal
    past_attested_surfaces: list[str]
    # 歧义实体仍保留候选；实际 ActorRef 编译使用 Characters 的持久 ID 文法。
    visible_character_refs: dict[str, list[str]]
    character_bindings: dict[str, list[BlueprintCharacterBinding]]

    @model_validator(mode='after')
    def _binding_inventory(self) -> BlueprintCharacterProjectionChapter:
        if self.character_bindings.keys() != self.visible_character_refs.keys():
            raise ValueError('呈现候选必须覆盖全部已现人物，歧义人物不能丢弃')
        for cid, rows in self.character_bindings.items():
            if [row.surface for row in rows] != self.visible_character_refs[cid]:
                raise ValueError('身份映射候选必须与实际下发称谓逐项相同')
        return self


class BlueprintCharacterProjection(StrictModel):
    """``characters + main_text`` 的确定性、版本化 Blueprint 称名投影。

    本产物没有通用 ``_provenance``：它的全部业务字节就是确定性投影本身，freshness
    由当前最新 schema-valid Characters（formal 或 INVALID）与 MainText 重算后
    逐值对账。加入输入文件指纹反而会让一次 role-only 修订改动本产物字节，重新制造
    本产物要消除的无效 stale。
    """

    chapters: list[BlueprintCharacterProjectionChapter] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_are_unique_and_ordered(self) -> BlueprintCharacterProjection:
        ordinals = [row.chapter_ordinal for row in self.chapters]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError('blueprint character projection chapter_ordinal 重号')
        if ordinals != sorted(ordinals):
            raise ValueError('blueprint character projection 必须按阅读序排列')
        return self


# ── LLM response models：只收【模型该写的字段】──────────────────────────────
#
# 最终产物模型不能直接拿来校验响应：Address.by 允许模型先写人名、再由代码回解；
# _provenance 更是代码在请求完成后才铸造。若为了省一组模型而把这些字段设成可选，
# 最终产物的严格契约也会被一起放宽。响应边界因此单独建模，二者各守各的责任。

class CharacterResponse(StrictModel):
    """人工完整替换 ``entities.characters`` 时的一条人物记录。

    正式 ``Character`` 已把证据归一成 ``FactEvidence``；人工恢复入口仍收
    locator，随后与其他 GlobalEdit 字段一起由 ``normalize_global`` 回取
    SourceSpan。``char_id`` 仍是外层映射键，避免同一身份抄两份。
    """

    canonical: str = Field(min_length=1)
    address: list[Address] = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]
    role: str = Field(min_length=1)
    role_evidence: EvidenceLocator
    fate: str | None
    fate_evidence: EvidenceLocator | None

    @model_validator(mode='after')
    def _fate_and_evidence_agree(self) -> CharacterResponse:
        if (self.fate is None) != (self.fate_evidence is None):
            raise ValueError('fate 与 fate_evidence 必须同时为空或同时非空')
        return self


class RelationResponse(StrictModel):
    relation_id: str = Field(pattern=r'^rel_\d{2}$')
    subject_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    predicate_evidence: EvidenceLocator
    visibility: RelationVisibility
    valid_from_chapter: ChapterOrdinal
    valid_through_chapter: ChapterOrdinal | None

    @model_validator(mode='after')
    def _valid_window_is_ordered(self) -> RelationResponse:
        if (self.valid_through_chapter is not None
                and self.valid_through_chapter < self.valid_from_chapter):
            raise ValueError('relation 有效截止章早于起章')
        return self


class WorldRuleVersionResponse(StrictModel):
    effective_from_chapter: ChapterOrdinal
    desc: str = Field(min_length=1)
    desc_evidence: EvidenceLocator


class WorldRuleResponse(StrictModel):
    rule_id: str = Field(pattern=r'^wr_\d{2}$')
    kind: Literal[WORLD_RULE_KINDS]
    versions: list[WorldRuleVersionResponse] = Field(min_length=1)

    @model_validator(mode='after')
    def _version_starts_are_unique(self) -> WorldRuleResponse:
        starts = [row.effective_from_chapter for row in self.versions]
        if len(starts) != len(set(starts)):
            raise ValueError(f'{self.rule_id}: duplicate effective_from_chapter')
        return self


class KnowledgeEventResponse(StrictModel):
    subject: KnowledgeSubject
    known_from_chapter: ChapterOrdinal
    evidence: EvidenceLocator


class SecretResponse(StrictModel):
    secret_id: str = Field(pattern=r'^secret_\d{2}$')
    fact: str = Field(min_length=1)
    fact_evidence: EvidenceLocator
    knowledge_events: list[KnowledgeEventResponse] = Field(min_length=1)
    setup: SecretSetup
    first_reveal_chapter: ChapterOrdinal

    @model_validator(mode='after')
    def _knowledge_subjects_are_unique(self) -> SecretResponse:
        subjects = [
            (event.subject.kind,
             getattr(event.subject, 'char_id', None),
             getattr(event.subject, 'label', None))
            for event in self.knowledge_events
        ]
        if len(subjects) != len(set(subjects)):
            raise ValueError(f'{self.secret_id}: duplicate knowledge subject')
        return self


class EvidenceCandidateRef(StrictModel):
    """本次 prompt 已下发证据候选表中的一个不透明引用。"""

    evidence_candidate_id: str = Field(pattern=r'^e[0-9]{6}$')


class AddressCandidateResponse(StrictModel):
    surface: str = Field(min_length=1)
    by: str = Field(min_length=1)
    context: Literal[ADDRESS_CONTEXT]
    from_chapter: ChapterOrdinal


class RelationCandidateResponse(StrictModel):
    relation_id: str = Field(pattern=r'^rel_\d{2}$')
    subject_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    predicate_evidence: EvidenceCandidateRef
    visibility: RelationVisibility
    valid_from_chapter: ChapterOrdinal
    valid_through_chapter: ChapterOrdinal | None

    @model_validator(mode='after')
    def _valid_window_is_ordered(self) -> RelationCandidateResponse:
        if (self.valid_through_chapter is not None
                and self.valid_through_chapter < self.valid_from_chapter):
            raise ValueError('relation 有效截止章早于起章')
        return self


class SettingCandidateResponse(StrictModel):
    era: str = Field(alias='时代背景', min_length=1)
    era_evidence: EvidenceCandidateRef
    worldview: str = Field(alias='世界观', min_length=1)
    worldview_evidence: EvidenceCandidateRef


class WorldRuleVersionCandidateResponse(StrictModel):
    effective_from_chapter: ChapterOrdinal
    desc: str = Field(min_length=1)
    desc_evidence: EvidenceCandidateRef


class WorldRuleCandidateResponse(StrictModel):
    rule_id: str = Field(pattern=r'^wr_\d{2}$')
    kind: Literal[WORLD_RULE_KINDS]
    versions: list[WorldRuleVersionCandidateResponse] = Field(min_length=1)

    @model_validator(mode='after')
    def _version_starts_are_unique(self) -> WorldRuleCandidateResponse:
        starts = [row.effective_from_chapter for row in self.versions]
        if len(starts) != len(set(starts)):
            raise ValueError(f'{self.rule_id}: duplicate effective_from_chapter')
        return self


class KnowledgeEventCandidateResponse(StrictModel):
    subject: KnowledgeSubject
    known_from_chapter: ChapterOrdinal
    evidence: EvidenceCandidateRef


class SecretCandidateResponse(StrictModel):
    secret_id: str = Field(pattern=r'^secret_\d{2}$')
    fact: str = Field(min_length=1)
    fact_evidence: EvidenceCandidateRef
    knowledge_events: list[KnowledgeEventCandidateResponse] = Field(min_length=1)
    setup: SecretSetup
    first_reveal_chapter: ChapterOrdinal

    @model_validator(mode='after')
    def _knowledge_subjects_are_unique(self) -> SecretCandidateResponse:
        subjects = [
            (event.subject.kind,
             getattr(event.subject, 'char_id', None),
             getattr(event.subject, 'label', None))
            for event in self.knowledge_events
        ]
        if len(subjects) != len(set(subjects)):
            raise ValueError(f'{self.secret_id}: duplicate knowledge subject')
        return self


class CanonCandidateResponse(StrictModel):
    setting: SettingCandidateResponse
    world_rules: list[WorldRuleCandidateResponse]
    secrets: list[SecretCandidateResponse]

    @model_validator(mode='after')
    def _stable_ids_are_unique(self) -> CanonCandidateResponse:
        for label, values in (
            ('world rule', [row.rule_id for row in self.world_rules]),
            ('secret', [row.secret_id for row in self.secrets]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f'duplicate {label} ids')
        return self



from contracts.schema.core_evidence import CoreEvidenceLocator, EvidenceId
from contracts.schema.knowledge import (
    GlobalArcSecret, GlobalFactRef, GlobalRelation, GlobalSetting,
    GlobalWorldRule, GlobalWorldRuleVersion,
)

ManualGlobalEvidence = EvidenceId | CoreEvidenceLocator
class RelationResponse(GlobalRelation[ManualGlobalEvidence]):
    """编辑 wire 允许本批新增人物句柄；正式 GlobalRelation 仍只接受持久 ID。"""

    subject_id: str = Field(pattern=r'^(char_\d{3,}|c[1-9]\d*)$')
    object_id: str = Field(pattern=r'^(char_\d{3,}|c[1-9]\d*)$')


WorldRuleVersionResponse = GlobalWorldRuleVersion[ManualGlobalEvidence]
WorldRuleResponse = GlobalWorldRule[ManualGlobalEvidence]
SecretResponse = GlobalArcSecret[ManualGlobalEvidence]


class SecretPatch(StrictModel):
    secret_id: str = Field(pattern=r'^secret_\d{2,}$')
    fact: str | None = Field(default=None, min_length=1)
    fact_ref: GlobalFactRef | None = None
    setup_chapters: list[ChapterOrdinal] | None = None
    revealed_from: ChapterOrdinal | None = None
    evidence_refs: list[ManualGlobalEvidence] | None = None

    @model_validator(mode='after')
    def _valid_patch(self):
        changed = self.model_fields_set - {'secret_id'}
        if not changed:
            raise ValueError('秘密 patch 不能为空')
        if {'fact', 'fact_ref'} & changed and not {'fact', 'fact_ref', 'evidence_refs'} <= changed:
            raise ValueError('修改秘密事实须同时提交 fact、fact_ref 与 evidence_refs')
        if any(getattr(self, key) is None for key in changed - {'fact', 'fact_ref'}):
            raise ValueError('秘密非可空字段不得置 null')
        return self


class WorldRulePatch(StrictModel):
    rule_id: str = Field(pattern=r'^wr_\d{2,}$')
    kind: Literal[WORLD_RULE_KINDS] | None = None
    versions: list[WorldRuleVersionResponse] | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _valid_patch(self):
        changed = self.model_fields_set - {'rule_id'}
        if not changed or any(getattr(self, key) is None for key in changed):
            raise ValueError('规则 patch 必须修改非空字段')
        if self.versions is not None:
            identities = [version.version_id for version in self.versions]
            if len(identities) != len(set(identities)):
                raise ValueError('同一规则的 version_id 不得重复')
        return self


class RelationPatch(StrictModel):
    relation_id: str = Field(pattern=r'^rel_\d{2,}$')
    subject_id: str | None = Field(default=None, min_length=1)
    object_id: str | None = Field(default=None, min_length=1)
    predicate: str | None = Field(default=None, min_length=1)
    effective_from: ChapterOrdinal | None = None
    effective_until: ChapterOrdinal | None = None
    revealed_from: ChapterOrdinal | None = None
    evidence_refs: list[ManualGlobalEvidence] | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _valid_patch(self):
        changed = self.model_fields_set - {'relation_id'}
        if not changed or any(getattr(self, key) is None for key in changed - {'effective_until'}):
            raise ValueError('关系 patch 只能将 effective_until 显式置 null')
        if {'predicate', 'subject_id', 'object_id'} & changed and 'evidence_refs' not in changed:
            raise ValueError('修改关系事实必须同时提交 evidence_refs')
        return self


class SettingPatch(StrictModel):
    era: str | None = Field(default=None, min_length=1)
    social_context: str | None = Field(default=None, min_length=1)
    era_evidence_refs: list[ManualGlobalEvidence] | None = None
    social_context_evidence_refs: list[ManualGlobalEvidence] | None = None

    @model_validator(mode='after')
    def _valid_patch(self):
        changed = self.model_fields_set
        if not changed or any(getattr(self, key) is None for key in changed - {'era', 'social_context'}):
            raise ValueError('setting patch 必须修改非空字段')
        for claim in ('era', 'social_context'):
            if claim in changed and claim + '_evidence_refs' not in changed:
                raise ValueError('修改背景事实必须同时提交证据')
        return self


class GlobalEditQuarantineGroup(StrictModel):
    """第二轮自检仍无法证实、但可从写作视图整组删除的事实。"""

    group_key: str = Field(pattern=r'^secret:.+$')
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _reason_has_visible_text(self) -> GlobalEditQuarantineGroup:
        if not self.reason.strip():
            raise ValueError('quarantine group reason must not be blank')
        return self


class GlobalEditReviewTarget(StrictModel):
    """修前 workpack 中允许复判的一条旧 blocking identity。"""

    item_id: str = Field(min_length=1)
    expected_subject_sha256: Sha256


class GlobalEditReviewAttestation(StrictModel):
    """Commander 增量轮对自己提交的精确 GlobalEdit 所作的闭合证明。

    producer 在 mutation lock 内先把 edit 应用到内存候选，再将本证明展开到
    **该候选新产生的 pending blocking 项**；仍保持同一 subject hash 的旧项
    只能由 ``review_scope + decisions`` 逐项复判。因此这不是把任意旧红项
    批量改绿。
    """

    actor: str = Field(min_length=1)
    expected_source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewed_chapter_ordinals: list[int] = Field(min_length=1)
    default_state: Literal['PASS']
    reason: str = Field(min_length=1)
    quarantine_groups: list[GlobalEditQuarantineGroup] = Field(default_factory=list)
    # v3 的兼容加法：公开人工 edit 可继续省略；Supervisor 生成的 reviewed edit
    # 必须三者同时在场。它们让一次动作既能修字段，又能对仍存活的旧 FAIL/UNVER
    # 作 hash-bound 复判，不再只能证明“本 edit 新打开的 pending”。
    expected_workpack_sha256: Sha256 | None = None
    review_scope: list[GlobalEditReviewTarget] = Field(default_factory=list)
    decisions: list[GlobalReviewDecision] = Field(default_factory=list)

    @model_validator(mode='after')
    def _complete_and_unique(self) -> GlobalEditReviewAttestation:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('post-edit review actor/reason must not be blank')
        chapters = self.reviewed_chapter_ordinals
        if chapters != sorted(set(chapters)) or any(chapter < 1 for chapter in chapters):
            raise ValueError(
                'post-edit reviewed_chapter_ordinals 必须是去重升序的正整数全集')
        groups = [row.group_key for row in self.quarantine_groups]
        if len(groups) != len(set(groups)):
            raise ValueError('post-edit quarantine group 不得重复')
        scope = [
            (row.item_id, row.expected_subject_sha256)
            for row in self.review_scope
        ]
        if len(scope) != len(set(scope)):
            raise ValueError('post-edit review_scope 不得重复')
        decision_ids = [row.item_id for row in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError('post-edit decisions 不得重复')
        scoped = set(scope)
        outside = [
            row.item_id for row in self.decisions
            if (row.item_id, row.expected_subject_sha256) not in scoped
        ]
        if outside:
            raise ValueError(f'post-edit decisions 越出 review_scope：{outside}')
        extended = (
            self.expected_workpack_sha256 is not None
            or bool(self.review_scope) or bool(self.decisions))
        if extended and (
                self.expected_workpack_sha256 is None or not self.review_scope):
            raise ValueError(
                'reviewed GlobalEdit 的 workpack SHA 与 review_scope 必须同时在场')
        return self


class CharacterCollectionItem(CharacterResponse):
    """collection patch wire 上把人物映射键提升为显式稳定 id。"""

    char_id: str = Field(min_length=1)


GlobalCollectionItem = (
    RelationResponse | SecretResponse | WorldRuleResponse
)


class GlobalCollectionAdd(StrictModel):
    op: Literal['add']
    item: GlobalCollectionItem


class GlobalCollectionDelete(StrictModel):
    op: Literal['delete']
    subject_id: str = Field(min_length=1)


class GlobalCollectionSplit(StrictModel):
    op: Literal['split']
    subject_id: str = Field(min_length=1)
    items: list[GlobalCollectionItem] = Field(min_length=2)


GlobalCollectionOperation = Annotated[
    GlobalCollectionAdd | GlobalCollectionDelete | GlobalCollectionSplit,
    Field(discriminator='op'),
]


class GlobalCollectionEdit(StrictModel):
    """集合完整性修订 DSL；只传 delta，不复制整组既有成员。"""

    collection: Literal['relations', 'arc_secrets', 'world_rules']
    expected_collection_sha256: Sha256
    # 空数组只供 producer 生成 schema-valid no-op 模板；apply 仍会拒绝无业务变化。
    operations: list[GlobalCollectionOperation] = Field(default_factory=list)

    @model_validator(mode='after')
    def _operations_match_collection(self) -> GlobalCollectionEdit:
        expected_type = {
            'relations': RelationResponse,
            'arc_secrets': SecretResponse,
            'world_rules': WorldRuleResponse,
        }[self.collection]

        def item_id(item: GlobalCollectionItem) -> str:
            if isinstance(item, RelationResponse):
                return item.relation_id
            if isinstance(item, SecretResponse):
                return item.secret_id
            return item.rule_id

        touched: list[str] = []
        outputs: list[str] = []
        for operation in self.operations:
            if isinstance(operation, GlobalCollectionAdd):
                if not isinstance(operation.item, expected_type):
                    raise ValueError(
                        f'{self.collection} add 携带了错误成员类型')
                outputs.append(item_id(operation.item))
            elif isinstance(operation, GlobalCollectionDelete):
                touched.append(operation.subject_id)
            else:
                touched.append(operation.subject_id)
                for item in operation.items:
                    if not isinstance(item, expected_type):
                        raise ValueError(
                            f'{self.collection} split 携带了错误成员类型')
                    output = item_id(item)
                    if output == operation.subject_id:
                        raise ValueError('split 输出不得复用被拆分 subject id')
                    outputs.append(output)
        if len(touched) != len(set(touched)):
            raise ValueError('一个集合 subject 不能被 delete/split 多次')
        if len(outputs) != len(set(outputs)):
            raise ValueError('集合 add/split 输出 id 不得重复')
        if set(touched) & set(outputs):
            raise ValueError('集合操作的输入 id 与输出 id 不得重叠')
        return self


class GlobalEdit(StrictModel):
    """Global v7 的免费四类事实编辑；不能承载人物或文案。"""

    arc_secrets: list[SecretPatch] = Field(default_factory=list)
    world_rules: list[WorldRulePatch] = Field(default_factory=list)
    relations: list[RelationPatch] = Field(default_factory=list)
    setting: SettingPatch | None = None
    arc_secrets_replace: list[SecretResponse] | None = None
    world_rules_replace: list[WorldRuleResponse] | None = None
    relations_replace: list[RelationResponse] | None = None
    collection_edits: list[GlobalCollectionEdit] = Field(default_factory=list)
    post_edit_review: GlobalEditReviewAttestation | None = None

    @model_validator(mode='after')
    def _closed_edits(self):
        if not (self.arc_secrets or self.world_rules or self.relations
                or self.setting is not None or self.arc_secrets_replace is not None
                or self.world_rules_replace is not None or self.relations_replace is not None
                or self.collection_edits):
            raise ValueError('global edit must contain at least one typed patch')
        collections = [row.collection for row in self.collection_edits]
        if len(collections) != len(set(collections)):
            raise ValueError('每个集合最多一条 collection edit')
        for name, key in (('arc_secrets', 'secret_id'), ('world_rules', 'rule_id'),
                          ('relations', 'relation_id')):
            patches = getattr(self, name)
            replacement = getattr(self, name + '_replace')
            ids = [getattr(row, key) for row in patches]
            if len(ids) != len(set(ids)):
                raise ValueError('同一主体不能多次 patch')
            if replacement is not None:
                if patches or name in collections:
                    raise ValueError('完整替换不能与同集合 patch/delta 混用')
                values = [getattr(row, key) for row in replacement]
                if len(values) != len(set(values)):
                    raise ValueError('替换集合 ID 不得重复')
            for edit in self.collection_edits:
                if edit.collection != name:
                    continue
                removed = {row.subject_id for row in edit.operations
                           if isinstance(row, (GlobalCollectionDelete, GlobalCollectionSplit))}
                if set(ids) & removed:
                    raise ValueError('直接 patch 不得同时 delete/split 同一主体')
        return self


class GlobalEditFailure(StrictModel):
    """canonical GlobalEdit 尚未提交 formal bundle 的持久失败/恢复事实。

    reviewed 失败另绑 source/workpack，普通 edit terminal 明确禁止伪造这两格。
    ``post_edit_review`` 与候选载体失败都没有 live ``.INVALID``；把两者伪装成
    HARD 会让状态机误读失败性质。反向约束也钉死：真正 HARD 必须绑定那份 exact
    INVALID 及非空规则集，其他失败则二者都不得出现。
    """

    failure_kind: Literal[
        'canonical_pending', 'hard_gate', 'candidate_validation',
        'post_edit_review', 'ordinary_hard_gate',
        'ordinary_candidate_validation']
    failed_edit_sha256: Sha256
    formal_sha256: Sha256
    expected_source_sha256: Sha256 | None = None
    global_review_workpack_sha256: Sha256 | None = None
    candidate_business_sha256: Sha256 | None = None
    invalid_sha256: Sha256 | None = None
    hard_hits: list[str] = Field(default_factory=list)
    reason_code: str = Field(min_length=1, max_length=80,
                             pattern=r'^[a-z0-9_]+$')
    reason_summary: str = Field(min_length=1, max_length=512)
    code_version: str = Field(min_length=1)

    @model_validator(mode='after')
    def _failure_shape_matches_kind(self) -> GlobalEditFailure:
        if self.failure_kind in {'hard_gate', 'ordinary_hard_gate'}:
            if self.invalid_sha256 is None or not self.hard_hits:
                raise ValueError(
                    'HARD failure 必须绑定 exact INVALID 与非空 hard_hits')
        elif self.invalid_sha256 is not None or self.hard_hits:
            raise ValueError(
                '非 HARD reviewed-edit failure 不得伪造 INVALID/hard_hits')
        reviewed = self.failure_kind in {
            'canonical_pending', 'hard_gate', 'candidate_validation',
            'post_edit_review',
        }
        if reviewed:
            if (self.expected_source_sha256 is None
                    or self.global_review_workpack_sha256 is None):
                raise ValueError(
                    'reviewed-edit failure 必须绑定 source/workpack')
        elif (self.expected_source_sha256 is not None
              or self.global_review_workpack_sha256 is not None):
            raise ValueError(
                '普通 GlobalEdit failure 不得伪造 reviewed source/workpack')
        if self.failure_kind == 'canonical_pending':
            if self.candidate_business_sha256 is not None:
                raise ValueError(
                    'canonical_pending 尚无已验证候选，不得声称 candidate identity')
        elif self.candidate_business_sha256 is None:
            raise ValueError(
                'reviewed-edit 终态失败必须绑定候选业务 identity')
        if len(self.hard_hits) != len(set(self.hard_hits)):
            raise ValueError('reviewed-edit failure 的 hard_hits 不得重复')
        if not self.reason_summary.strip() or not self.code_version.strip():
            raise ValueError(
                'reviewed-edit failure reason_summary/code_version 不得为空白')
        return self


class GlobalEditInputCommit(StrictModel):
    """canonical edit 与 pending/superseded failure 的 action-target WAL。"""

    submitted_transport_identity: str = Field(min_length=1, max_length=300)
    reviewed_edit: bool
    formal_sha256: Sha256
    expected_source_sha256: Sha256 | None = None
    global_review_workpack_sha256: Sha256 | None = None
    baseline_edit_sha256: Sha256 | None
    baseline_failure_sha256: Sha256 | None
    baseline_formal_commit_sha256: Sha256 | None
    # GlobalEdit 的 model_fields_set 本身就是业务语义；嵌套 BaseModel 做普通
    # model_dump 会把未提交字段展开成显式 null，所以这里保存 sparse JSON object，
    # validator 仍用唯一 GlobalEdit 模型逐字段收口。
    target_edit: dict[str, object]
    target_edit_sha256: Sha256
    target_failure: GlobalEditFailure | None = None
    target_failure_sha256: Sha256 | None = None
    code_version: str = Field(min_length=1)

    @model_validator(mode='after')
    def _canonical_target_is_complete(self) -> GlobalEditInputCommit:
        target_edit = GlobalEdit.model_validate(self.target_edit)
        if self.reviewed_edit:
            if (self.expected_source_sha256 is None
                    or self.global_review_workpack_sha256 is None
                    or self.target_failure is None
                    or self.target_failure_sha256 is None):
                raise ValueError(
                    'reviewed canonical WAL 必须携 source/workpack/pending failure')
            if target_edit.post_edit_review is None:
                raise ValueError('reviewed canonical WAL 的 target edit 缺 attestation')
            if self.target_failure.failure_kind != 'canonical_pending':
                raise ValueError('canonical WAL target failure 必须是 canonical_pending')
        elif any(value is not None for value in (
                self.expected_source_sha256,
                self.global_review_workpack_sha256,
                self.target_failure,
                self.target_failure_sha256,
        )) or target_edit.post_edit_review is not None:
            raise ValueError(
                '普通 GlobalEdit canonical WAL 不得伪造 reviewed failure 身份')
        if not self.code_version.strip():
            raise ValueError('canonical WAL code_version 不得为空白')
        return self


class GlobalEditCommitBaseline(StrictModel):
    """prepared 前冻结的知识、审阅和两份问题产物；None 表示当时缺失。"""

    global_sha256: Sha256 | None
    review_sha256: Sha256 | None
    invalid_sha256: Sha256 | None
    failure_sha256: Sha256 | None


class GlobalEditCommitTarget(StrictModel):
    """可逐字重放的知识/审阅目标；人物只作冻结的审阅上下文，不拥有写权。

    global_grounding 是只读组装视图；写口须与当前人物登记逐字段互验后，
    只渲染 KnowledgeGrounding。称名投影由人物 producer 独占，不在此提交中。
    """

    global_grounding: GlobalGrounding
    global_review: GlobalReview
    global_sha256: Sha256
    review_sha256: Sha256


class GlobalEditCommit(StrictModel):
    """Stage2 Global 的 prepared/committed 可恢复正式 bundle 提交记录。

    reviewed 与普通人工 edit 共用同一知识/审阅提交面；区别只在前者额外绑定
    attestation/workpack 与 pending failure。它不是 Commander failure，也不进入
    节点 ``produces``。prepared 在任何 live companion mutation 前落稳；恢复只
    接受每个路径仍为 baseline 或已为 target。首次生成或 renormalize 留下
    的 generic INVALID 可没有归属于本动作的 GlobalEdit 身份，也可能没有既有正式
    Global；这两格只由 ``action_kind=revalidate`` 的 ``None`` 显式表达。
    """

    state: Literal['prepared', 'committed']
    action_kind: Literal['apply_edit', 'revalidate'] = 'apply_edit'
    reviewed_edit: bool = True
    submitted_transport_identity: str = Field(min_length=1, max_length=300)
    # generic Global INVALID（首次生成/renormalize HARD）没有归属于本动作的
    # GlobalEdit identity，也可能还没有正式 Global；``None`` 只在 revalidate 表示
    # 没有可绑定身份。apply-edit 与 reviewed/ordinary terminal revalidate 仍绑 SHA。
    failed_edit_sha256: Sha256 | None
    formal_sha256: Sha256 | None
    expected_source_sha256: Sha256 | None = None
    global_review_workpack_sha256: Sha256 | None = None
    main_text_sha256: Sha256
    baseline: GlobalEditCommitBaseline
    target: GlobalEditCommitTarget
    code_version: str = Field(min_length=1)

    @model_validator(mode='after')
    def _identity_matches_baseline(self) -> GlobalEditCommit:
        if (self.reviewed_edit
                and self.formal_sha256 != self.baseline.global_sha256):
            raise ValueError('commit formal_sha256 必须等于 baseline Global SHA')
        if self.action_kind == 'revalidate':
            if self.baseline.invalid_sha256 is None:
                raise ValueError(
                    'revalidate commit 必须绑定 live INVALID baseline')
            if self.formal_sha256 != self.baseline.global_sha256:
                raise ValueError(
                    'revalidate commit formal_sha256 必须等于 baseline Global SHA')
            if self.reviewed_edit:
                if (self.failed_edit_sha256 is None
                        or self.expected_source_sha256 is None
                        or self.global_review_workpack_sha256 is None):
                    raise ValueError(
                        'reviewed revalidate commit 必须绑定 edit/source/workpack')
            elif (self.expected_source_sha256 is not None
                  or self.global_review_workpack_sha256 is not None):
                raise ValueError(
                    'non-reviewed revalidate commit 不得伪造 reviewed source/workpack')
            if (not self.reviewed_edit
                    and self.baseline.failure_sha256 is not None
                    and self.failed_edit_sha256 is None):
                raise ValueError(
                    'ordinary terminal revalidate 必须同时绑定 edit 与 failure')
        elif self.reviewed_edit:
            if (self.failed_edit_sha256 is None
                    or self.formal_sha256 is None
                    or self.expected_source_sha256 is None
                    or self.global_review_workpack_sha256 is None
                    or self.baseline.failure_sha256 is None):
                raise ValueError(
                    'reviewed commit 必须绑定 source/workpack/pending failure')
        elif (self.failed_edit_sha256 is None
              or self.formal_sha256 is None
              or self.expected_source_sha256 is not None
              or self.global_review_workpack_sha256 is not None
              or self.baseline.failure_sha256 is not None):
            raise ValueError(
                '普通 GlobalEdit commit 不得伪造 reviewed failure 身份')
        if not self.code_version.strip():
            raise ValueError('commit code_version 不得为空白')
        return self


class BlueprintContext(StrictModel):
    """逐章原文与共享主体投影的契约身份，具体材料由来源账绑定。"""
    architecture: Literal[BLUEPRINT_ARCHITECTURES]
    source_format: Literal['numbered-paragraphs-v1']
    global_projection_contract: Literal['plot-global-scope-v2']


class BlueprintScopeViolation(StrictModel):
    """``scope_violations[]`` 字符串载体的唯一结构化读写契约。"""

    batch: int
    requested: list[int]
    produced: list[int]
    discarded_chapters: list[int]
    duplicate_target: int


from contracts.schema.plot_edit import PlotRevisionRecord


class BlueprintProvenance(LlmProvenance):
    """V10 只记录实际响应范围与调用身份；段号/引用失败由 durable 响应恢复。"""
    scope_violations: list[str]
    context: BlueprintContext
    # 编辑身份账随同一份蓝图原子提交；审阅/写作投影不下发 provenance。
    plot_revisions: list[PlotRevisionRecord] = Field(default_factory=list)

from contracts.schema.plot import PlotBlueprint


class ChapterBlueprints(StrictModel):
    """V10 完整剧情蓝图：每章一个逻辑抽取请求，齐章后原子提交。

    当前章原文、必要 Global 投影及持续状态是实际输入；后章只携带仍成立的状态，
    不累计全部历史动作。身份编辑账保存在 provenance，不下发给规划或写作模型。
    """

    #: 长度等于源正文章数；跨产物检查逐章对应。
    blueprints: list[PlotBlueprint] = Field(min_length=1)

    #: 实际消费的正文、人物与已接受 Global 投影由来源及请求账绑定。
    provenance: BlueprintProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _chapter_ordinals_are_unique(self) -> ChapterBlueprints:
        """`chapter_ordinal` 是全链的章唯一键——重号会让下游按它做的每一次 join 取错章。

        ⚠ 这里**只查唯一性，不查连续性**：章覆盖与节点引用是跨产物判据
        （跨产物、要拿 `main_text` 比），归 stage2 的判据表，不归载体。
        """
        got = [b.chapter_ordinal for b in self.blueprints]
        if len(set(got)) != len(got):
            dup = sorted({o for o in got if got.count(o) > 1})
            raise ValueError(f'chapter_ordinal 重号：{dup}')
        retired, known, requests = set(), set(), set()
        for revision in self.provenance.plot_revisions:
            if revision.request_sha256 in requests:
                raise ValueError('同一编辑请求不能重复记入身份历史')
            requests.add(revision.request_sha256)
            for change in revision.changes:
                if retired.intersection(change.old_refs):
                    raise ValueError('不能再次修改已退役主体')
                if change.kind != 'preserve' and known.intersection(change.new_refs):
                    raise ValueError('新主体不能复用历史 ID')
            known.update(ref for row in revision.changes for ref in [*row.old_refs, *row.new_refs])
            retired.update(row.node_id for row in revision.tombstones)
        live = {ref for chapter in self.blueprints for ref in (
            *[row.beat_id for row in chapter.beats],
            *[row.state_id for row in chapter.states],
            *[row.constraint_id for row in chapter.constraints],
            *[row.presentation_id for row in chapter.actor_bindings])}
        if retired.intersection(live):
            raise ValueError('当前蓝图不能复活退役主体')
        return self
