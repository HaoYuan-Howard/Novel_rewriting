#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2.py —— 对应 `docs/schema/stage2.md`。抽取。**全链第一个调 LLM 的层。**

【读这份模型之前必须知道的三条】

  ① **这一层每一个字段都是模型写的，而产地只校验顶层键。**
     往下每一层读字段都是 `x.get(k) or []` ⇒ 模型少写一个 `event`、少写整个 `narration`，
     **不会有任何一层出声**。⇒ 本文件里的「必填」几乎全部是**契约要求它必填**，
     不是**结构上保证它在**；「盘上 3/3」在这一层只等于「这三次调用它都写了」。
     `extra='forbid'` + 必填校验第一次把这句话变成真的。

  ② **有一批字段是代码写的**（`beat_id` 归一 · `hinges[].text` 按锚点从原文切 ·
     `present_char_ids` 解析 · `address` 四项回取 · `setup_chapters` 裁剪），
     它们与模型写的那些必须分开读。⚠ **凡是被落盘前改写过的字段，校验器就不能再断言它的值**
     ——那会是一条永远为真的判定。已解决的两类改断言 `_provenance` 里的计数（见 `GlobalProvenance`）。

  ③ **盘上这三本是 FAIL 的**（`模棱语`×2本 · `role 泄结局`×2本 · `address 裸代词`×1本 ·
     `hinges 落在漏抓区`×1本）。读盘上那一源时必须知道：它**不是一份通过了的产物**，
     拿它当「合法取值面」会把已知违规读成合法分支。

【判据 vs 载体的分界线（`stage4.md` §四立的规矩）：按失败成本划】
本层的取值面枚举（`time_layer` / `hinges[].kind` / `address[].context` …）归**载体**：
它们要么是代码写的（拒收＝代码 bug），要么整份响应本来就坏了（prompt 漂了，
而且要在生成下一章之前就停住）。跨产物的那些（`fact_ref` / `fact_coverage`）归 Stage4 判据。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, BLUEPRINT_ARCHITECTURES,
    FACT_ATOM_KINDS, FACT_ATOM_OBLIGATIONS, HINGE_KINDS,
    KNOWLEDGE_SUBJECT_KINDS, RELATION_VISIBILITY_KINDS, SECRET_SETUP_KINDS,
    NAME_DISTINCTIVENESS, NARRATIVE_PERSON, RESPONSE_TIME_LAYERS,
    TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import LlmProvenance, Sha256

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


class ConflictStage(StrictModel):
    """冲突阶段。盘上每本 4~5 段。"""

    #: 长度 5~9。阶段名要**能定位**（「政治清算」），不是对经过的复述——
    #: 复述必含后几章的结果 = 剧透。**无守卫 → `B-08`**。
    #: ⚠ 下发时**只发阶段名与跨度，不发摘要**：阶段摘要描述的是整个阶段的经过，
    #: 必然含本阶段后几章才发生的结果（实测 ch16 拿到的摘要写着 ch20 才发生的事）。
    stage: str = Field(min_length=1)

    #: **逐章显式枚举**，不是区间端点：`[3, 5]` 不暗含第 4 章。
    #: 全部阶段合起来必须让每个阅读序章号**恰好出现一次**；跨字段的不重不漏由
    #: `conflict_stage_coverage` HARD 在产地按 Stage1 正文章集合校验（`R-2.14`）。
    chapters: list[Annotated[int, Field(strict=True)]] = Field(min_length=1)


class ArcTrack(StrictModel):
    """一条人物弧线。盘上 16 条。**仅末章下发。**"""

    #: `char_id` ｜ `"reader"` ｜ `relation_id`。**三种都必须能解析成人话**，否则显式终态。
    #: ⚠ 这条链路**曾经断着**：`relation_id` 被引用却从未被解析，未解析的 `rel_05`
    #: 直接进了写作 prompt。**有确定性消费方 ≠ 那条链路接通了。**
    subject: str = Field(min_length=1)

    #: 长度 16~38。要说清**从什么变到什么**，不能只写「有成长」。**无守卫 → `B-08`**。
    trajectory: str = Field(min_length=1)
    trajectory_evidence: FactEvidence


class Arc(StrictModel):
    """⚠ **这一块是解读层，不是事实层。**

    `entities` / `canon` 记的是原文写出来的事实；`arc` 记的是「高潮在哪」「弧怎么走」
    ——**判断**。它的确定性消费方近乎恒空（逐章只发一个阶段名），几乎全靠 prompt 消费。
    """

    conflict_stages: list[ConflictStage] = Field(min_length=1)

    #: 长度 25~52。**仅末章下发。**
    climax: str = Field(min_length=1)

    #: **仅末章下发。**
    ending: str = Field(min_length=1)

    #: 长度 4~7。**仅末章下发**——非末章一律不发：剥掉 `trajectory` 剩下的对写手是零信息，
    #: 不剥就是结局。两头不讨好，所以整块不发。
    arc_tracks: list[ArcTrack] = Field(min_length=1)


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


class GlobalProvenance(LlmProvenance):
    """`global_grounding._provenance` —— 只保留会驱动闸门或恢复的归一账。"""

    #: `(secret_id, 原埋点章, 裁完的)`。**校验器断言的是它，不是字段值。**
    #: 空列表 = 这一趟没裁过；**键不在 = 「不知道」，不发绿灯**。
    #: ⚠ 裁完为 `[]` 的那一条在产物里**降级成 `{"kind": "cold"}`**，与真·冷揭示同形
    #: ——v2 的判别联合消除的是「模型意图」那层歧义，这一层仍然只有这个账说得清。
    setup_chapters_pruned: list[tuple[str, list[int], list[int]]]

    #: `(claim_path, chapter, locator, reason)`；非空时正式证据为 `source=None`，
    #: 由 `global_evidence_unlocated` HARD 拦到可恢复的 `.INVALID`。
    evidence_locator_failures: list[tuple[str, int, str, str]]


class GlobalGrounding(StrictModel):
    """`stage2/global_grounding.json` —— 顶层 4 个键。**全书一次调用。**

    结构化调用点声明的必需顶层键 = `entities` · `canon` · `arc`
    （`_provenance` 是代码写的，不归调用点管）。
    **全链规矩：每个结构化调用点声明自己【全部】必需顶层键。**
    判准不能是「这一块有没有后置兜底判据」——**兜底判据本身是会退役的**。

    ⚠ **已裁撤**：`title` · `source_id`——书名 = 目录名、`source_id` = 目录名后缀，
    都能 0 成本现算，落盘只是抄第二份。
    """

    entities: Entities
    canon: Canon
    arc: Arc

    #: `inputs` 恒一条：`stage1/main_text.json`。
    provenance: GlobalProvenance = Field(alias='_provenance')


class BlueprintCharacterProjectionChapter(StrictModel):
    """某一章真正交给 Blueprint 的已现称名闭集。

    这不是 GlobalGrounding 的摘要：只有最终 prompt/validator 消费的两格能进入这里。
    ``role``、``fate``、关系、秘密、设定与证据一概没有载体位置，因而后续订正它们
    不会靠调用方记得“忽略字段”来避免整书 Blueprint 过期。
    """

    chapter_ordinal: ChapterOrdinal
    past_attested_surfaces: list[str]
    # 与 ``Entities.characters`` 同一键域：这里是成员闭集载体，不新增一条仓内
    # 明说不存在的 char_id 格式守卫。格式异常若需要阻断，必须先成为 Global 判据。
    visible_character_refs: dict[str, list[str]]


class BlueprintCharacterProjection(StrictModel):
    """``global + main_text`` 的确定性、版本化 Blueprint 称名投影。

    本产物没有通用 ``_provenance``：它的全部业务字节就是确定性投影本身，freshness
    由当前最新 schema-valid GlobalGrounding（formal 或 INVALID）与 MainText 重算后
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

class AddressResponse(StrictModel):
    surface: str = Field(min_length=1)
    by: str = Field(min_length=1)
    context: Literal[ADDRESS_CONTEXT]
    from_chapter: ChapterOrdinal


class CharacterResponse(StrictModel):
    canonical: str = Field(min_length=1)
    address: list[AddressResponse] = Field(min_length=1)
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


class SettingResponse(StrictModel):
    era: str = Field(alias='时代背景', min_length=1)
    era_evidence: EvidenceLocator
    worldview: str = Field(alias='世界观', min_length=1)
    worldview_evidence: EvidenceLocator


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


class CanonResponse(StrictModel):
    setting: SettingResponse
    world_rules: list[WorldRuleResponse]
    secrets: list[SecretResponse]

    @model_validator(mode='after')
    def _stable_ids_are_unique(self) -> CanonResponse:
        for label, values in (
            ('world rule', [row.rule_id for row in self.world_rules]),
            ('secret', [row.secret_id for row in self.secrets]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f'duplicate {label} ids')
        return self


class ArcTrackResponse(StrictModel):
    subject: str = Field(min_length=1)
    trajectory: str = Field(min_length=1)
    trajectory_evidence: EvidenceLocator


class ArcResponse(StrictModel):
    conflict_stages: list[ConflictStage] = Field(min_length=1)
    climax: str = Field(min_length=1)
    ending: str = Field(min_length=1)
    arc_tracks: list[ArcTrackResponse] = Field(min_length=1)


class EntitiesResponse(StrictModel):
    characters: dict[str, CharacterResponse]
    relations: list[RelationResponse]

    @model_validator(mode='after')
    def _relation_ids_are_unique(self) -> EntitiesResponse:
        values = [row.relation_id for row in self.relations]
        if len(values) != len(set(values)):
            raise ValueError('duplicate relation ids')
        return self


class GlobalGroundingResponse(StrictModel):
    """stage2.global 的不可信响应；不含任何代码产字段。"""

    entities: EntitiesResponse
    canon: CanonResponse
    arc: ArcResponse


class GlobalEvidenceLocatorPatch(StrictModel):
    """一格全局事实证据的 agent 私有修订；路径只作关闭式槽位标识。"""

    claim_path: str = Field(min_length=1, max_length=240)
    evidence: EvidenceLocator


class GlobalFullCandidateTurn(StrictModel):
    """stage2.global agent 首轮或非局部 HARD 的完整候选分支。"""

    kind: Literal['full_candidate']
    candidate: GlobalGroundingResponse


class GlobalEvidencePatchTurn(StrictModel):
    """只允许改当前候选已失败 evidence 槽的 hash-bound 修订分支。"""

    kind: Literal['evidence_patch']
    candidate_sha256: Sha256
    changes: list[GlobalEvidenceLocatorPatch] = Field(min_length=1)

    @model_validator(mode='after')
    def _claim_paths_are_unique(self) -> GlobalEvidencePatchTurn:
        paths = [change.claim_path for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError('evidence patch claim_path 不能重复')
        return self


GlobalAgentPayload = Annotated[
    GlobalFullCandidateTurn | GlobalEvidencePatchTurn,
    Field(discriminator='kind'),
]


class GlobalAgentTurnResponse(StrictModel):
    """stage2.global agent 全 session 固定信封；修复轮不得切换 wire schema。"""

    payload: GlobalAgentPayload


class RoleFateResponse(StrictModel):
    role: str = Field(min_length=1)
    fate: str | None


class RoleSplitResponse(StrictModel):
    """purge-role 只允许交回 role/fate；char_id 是映射键。"""

    characters: dict[str, RoleFateResponse]


class SecretPatch(StrictModel):
    """一条 secret 的 typed 人工订正；claim 改动必须同交新 locator。"""

    secret_id: str = Field(pattern=r'^secret_\d{2}$')
    fact: str | None = Field(default=None, min_length=1)
    fact_evidence: EvidenceLocator | None = None
    knowledge_events: list[KnowledgeEventResponse] | None = Field(
        default=None, min_length=1)
    setup: SecretSetup | None = None
    first_reveal_chapter: ChapterOrdinal | None = None

    @model_validator(mode='after')
    def _valid_patch(self) -> SecretPatch:
        changed = self.model_fields_set - {'secret_id'}
        if not changed:
            raise ValueError(f'{self.secret_id}: empty secret patch')
        for field in ('fact', 'knowledge_events', 'setup', 'first_reveal_chapter'):
            if field in changed and getattr(self, field) is None:
                raise ValueError(f'{self.secret_id}: {field} 不能显式置空')
        if 'fact' in changed and 'fact_evidence' not in changed:
            raise ValueError(f'{self.secret_id}: 修改 fact 必须同交 fact_evidence locator')
        if 'fact_evidence' in changed and self.fact_evidence is None:
            raise ValueError(f'{self.secret_id}: fact_evidence 不能显式置空')
        if self.knowledge_events is not None:
            subjects = [
                (event.subject.kind,
                 getattr(event.subject, 'char_id', None),
                 getattr(event.subject, 'label', None))
                for event in self.knowledge_events
            ]
            if len(subjects) != len(set(subjects)):
                raise ValueError(f'{self.secret_id}: duplicate knowledge subject')
        return self


class WorldRulePatch(StrictModel):
    rule_id: str = Field(pattern=r'^wr_\d{2}$')
    kind: Literal[WORLD_RULE_KINDS] | None = None
    versions: list[WorldRuleVersionResponse] | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _has_change(self) -> WorldRulePatch:
        changed = self.model_fields_set - {'rule_id'}
        if not changed:
            raise ValueError(f'{self.rule_id}: empty world-rule patch')
        for field in changed:
            if getattr(self, field) is None:
                raise ValueError(f'{self.rule_id}: {field} 不能显式置空')
        if self.versions is not None:
            starts = [row.effective_from_chapter for row in self.versions]
            if len(starts) != len(set(starts)):
                raise ValueError(f'{self.rule_id}: duplicate effective_from_chapter')
        return self


class GlobalCharacterPatch(StrictModel):
    """按人物稳定 ID 订正 enum、role/fate 与其 locator。"""

    char_id: str = Field(pattern=r'^char_\d{3}$')
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS] | None = None
    role: str | None = Field(default=None, min_length=1)
    role_evidence: EvidenceLocator | None = None
    fate: str | None = None
    fate_evidence: EvidenceLocator | None = None

    @model_validator(mode='after')
    def _has_change(self) -> GlobalCharacterPatch:
        changed = self.model_fields_set - {'char_id'}
        if not changed:
            raise ValueError(f'{self.char_id}: empty character patch')
        if 'name_distinctiveness' in changed and self.name_distinctiveness is None:
            raise ValueError(f'{self.char_id}: name_distinctiveness 不能显式置空')
        if 'role' in changed and self.role is None:
            raise ValueError(f'{self.char_id}: role 不能显式置空')
        if 'role' in changed and 'role_evidence' not in changed:
            raise ValueError(f'{self.char_id}: 修改 role 必须同交 role_evidence locator')
        if 'role_evidence' in changed and self.role_evidence is None:
            raise ValueError(f'{self.char_id}: role_evidence 不能显式置空')
        if 'fate' in changed and self.fate is not None and 'fate_evidence' not in changed:
            raise ValueError(f'{self.char_id}: 写入 fate 必须同交 fate_evidence locator')
        if 'fate' in changed and self.fate is None and self.fate_evidence is not None:
            raise ValueError(f'{self.char_id}: fate=null 时不能携带非空 fate_evidence')
        if 'fate_evidence' in changed and self.fate_evidence is None \
                and not ('fate' in changed and self.fate is None):
            raise ValueError(f'{self.char_id}: fate_evidence 只能随 fate=null 清空')
        return self


class RelationPatch(StrictModel):
    relation_id: str = Field(pattern=r'^rel_\d{2}$')
    subject_id: str | None = Field(default=None, min_length=1)
    object_id: str | None = Field(default=None, min_length=1)
    predicate: str | None = Field(default=None, min_length=1)
    predicate_evidence: EvidenceLocator | None = None
    visibility: RelationVisibility | None = None
    valid_from_chapter: ChapterOrdinal | None = None
    valid_through_chapter: ChapterOrdinal | None = None

    @model_validator(mode='after')
    def _valid_patch(self) -> RelationPatch:
        changed = self.model_fields_set - {'relation_id'}
        if not changed:
            raise ValueError(f'{self.relation_id}: empty relation patch')
        for field in ('subject_id', 'object_id', 'predicate', 'visibility',
                      'valid_from_chapter'):
            if field in changed and getattr(self, field) is None:
                raise ValueError(f'{self.relation_id}: {field} 不能显式置空')
        if 'predicate' in changed and 'predicate_evidence' not in changed:
            raise ValueError(
                f'{self.relation_id}: 修改 predicate 必须同交 predicate_evidence locator')
        if 'predicate_evidence' in changed and self.predicate_evidence is None:
            raise ValueError(f'{self.relation_id}: predicate_evidence 不能显式置空')
        return self


class SettingPatch(StrictModel):
    era: str | None = Field(default=None, alias='时代背景', min_length=1)
    era_evidence: EvidenceLocator | None = None
    worldview: str | None = Field(default=None, alias='世界观', min_length=1)
    worldview_evidence: EvidenceLocator | None = None

    @model_validator(mode='after')
    def _valid_patch(self) -> SettingPatch:
        changed = self.model_fields_set
        if not changed:
            raise ValueError('empty setting patch')
        for claim, evidence in (
            ('era', 'era_evidence'), ('worldview', 'worldview_evidence'),
        ):
            if claim in changed and getattr(self, claim) is None:
                raise ValueError(f'setting.{claim} 不能显式置空')
            if claim in changed and evidence not in changed:
                raise ValueError(f'修改 setting.{claim} 必须同交 {evidence} locator')
            if evidence in changed and getattr(self, evidence) is None:
                raise ValueError(f'{evidence} 不能显式置空')
        return self


class ArcTrackPatch(StrictModel):
    track_index: int = Field(ge=0)
    subject: str | None = Field(default=None, min_length=1)
    trajectory: str | None = Field(default=None, min_length=1)
    trajectory_evidence: EvidenceLocator | None = None

    @model_validator(mode='after')
    def _valid_patch(self) -> ArcTrackPatch:
        changed = self.model_fields_set - {'track_index'}
        if not changed:
            raise ValueError(f'arc track {self.track_index}: empty patch')
        for field in ('subject', 'trajectory'):
            if field in changed and getattr(self, field) is None:
                raise ValueError(f'arc track {self.track_index}: {field} 不能显式置空')
        if 'trajectory' in changed and 'trajectory_evidence' not in changed:
            raise ValueError('修改 trajectory 必须同交 trajectory_evidence locator')
        if 'trajectory_evidence' in changed and self.trajectory_evidence is None:
            raise ValueError('trajectory_evidence 不能显式置空')
        return self


class AddressContextPatch(StrictModel):
    char_id: str = Field(pattern=r'^char_\d{3}$')
    address_index: int = Field(ge=0)
    context: Literal[ADDRESS_CONTEXT]


class CharacterAddressReplacement(StrictModel):
    char_id: str = Field(pattern=r'^char_\d{3}$')
    address: list[Address] = Field(min_length=1)


class GlobalEdit(StrictModel):
    """`stage2/global_edit.json` —— 全局事实的 0 LLM typed 恢复面。"""

    secrets: list[SecretPatch] = Field(default_factory=list)
    world_rules: list[WorldRulePatch] = Field(default_factory=list)
    characters: list[GlobalCharacterPatch] = Field(default_factory=list)
    relations: list[RelationPatch] = Field(default_factory=list)
    setting: SettingPatch | None = None
    arc_tracks: list[ArcTrackPatch] = Field(default_factory=list)
    addresses: list[AddressContextPatch] = Field(default_factory=list)
    secrets_replace: list[SecretResponse] | None = None
    world_rules_replace: list[WorldRuleResponse] | None = None
    relations_replace: list[RelationResponse] | None = None
    conflict_stages_replace: list[ConflictStage] | None = Field(
        default=None, min_length=1)
    arc_tracks_replace: list[ArcTrackResponse] | None = Field(default=None, min_length=1)
    addresses_replace: list[CharacterAddressReplacement] = Field(default_factory=list)

    @model_validator(mode='after')
    def _one_patch_per_subject(self) -> GlobalEdit:
        if not (
            self.secrets or self.world_rules or self.characters or self.relations
            or self.setting is not None or self.arc_tracks or self.addresses
            or self.secrets_replace is not None or self.world_rules_replace is not None
            or self.relations_replace is not None
            or self.conflict_stages_replace is not None
            or self.arc_tracks_replace is not None
            or self.addresses_replace
        ):
            raise ValueError('global edit must contain at least one typed patch')
        for label, patch, replacement in (
            ('canon.secrets', self.secrets, self.secrets_replace),
            ('canon.world_rules', self.world_rules, self.world_rules_replace),
            ('entities.relations', self.relations, self.relations_replace),
            ('arc.arc_tracks', self.arc_tracks, self.arc_tracks_replace),
        ):
            if replacement is not None and patch:
                raise ValueError(f'{label} cannot be patched and completely replaced together')
        for label, keys in (
            ('secret', [p.secret_id for p in self.secrets]),
            ('world rule', [p.rule_id for p in self.world_rules]),
            ('character', [p.char_id for p in self.characters]),
            ('relation', [p.relation_id for p in self.relations]),
            ('arc track', [p.track_index for p in self.arc_tracks]),
            ('address', [(p.char_id, p.address_index) for p in self.addresses]),
        ):
            if len(keys) != len(set(keys)):
                raise ValueError(f'duplicate {label} patches are order-dependent')
        replacement_characters = [row.char_id for row in self.addresses_replace]
        if len(replacement_characters) != len(set(replacement_characters)):
            raise ValueError('duplicate complete address replacements are order-dependent')
        if set(replacement_characters) & {row.char_id for row in self.addresses}:
            raise ValueError('character address cannot be patched and replaced together')
        for label, rows, key in (
            ('secret', self.secrets_replace, 'secret_id'),
            ('world rule', self.world_rules_replace, 'rule_id'),
            ('relation', self.relations_replace, 'relation_id'),
        ):
            if rows is None:
                continue
            ids = [str(getattr(row, key)) for row in rows]
            if len(ids) != len(set(ids)):
                raise ValueError(f'duplicate {label} ids in complete replacement')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二、`chapter_blueprints.json`
# ─────────────────────────────────────────────────────────────────────────────



FACT_ID_PATTERN = r'^f\d{2,}_\d{3,}$'
HINGE_ID_PATTERN = r'^h\d{2,}_\d{3,}$'
ATOM_KEY_PATTERN = r'^a\d{2,}$'


class Hinge(StrictModel):
    """一条有稳定引用的保真材料；`source=None` 是明确的 INVALID 状态。"""

    hinge_id: str = Field(pattern=HINGE_ID_PATTERN)
    kind: Literal[HINGE_KINDS]
    source: SourceSpan | None


#: 源 beat 编号的形状。这里只用于证据定位；Stage4 场景响应不再引用源 beat 编号。
BEAT_ID_PATTERN = r'^b\d{2}_\d{2}$'


class Beat(StrictModel):
    """源章的舞台区间；它不再兼任写作事实清单。"""

    beat_id: str = Field(pattern=BEAT_ID_PATTERN)
    span: SourceSpan | None
    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present_char_ids: list[str]
    hinges: list[Hinge]


class FactAtom(StrictModel):
    """章级保真的最小单位；claim 与证据只有这一份正式落点。"""

    fact_id: str = Field(pattern=FACT_ID_PATTERN)
    claim: str = Field(min_length=1)
    kind: Literal[FACT_ATOM_KINDS]
    obligation: Literal[FACT_ATOM_OBLIGATIONS]
    source_beat_ref: str = Field(pattern=BEAT_ID_PATTERN)
    source: SourceSpan | None
    participant_char_ids: list[str]


class CausalEdge(StrictModel):
    """同章事实之间不可颠倒的故事因果；不是 Stage4 的披露顺序。"""

    before: str = Field(pattern=FACT_ID_PATTERN)
    after: str = Field(pattern=FACT_ID_PATTERN)


class Narration(StrictModel):
    """一段叙述。**盘上 31 章全是单段，所以多段分支整段是零样本。**

    ⚠ **`narration` 保持数组** + 一条绊线：`len(narration) > 1` 出声，
    **`INFO` 不是 `HARD`**（`R-3.14`）。数组是「本章有几个叙述声音」唯一的表达方式。
    报的**不是「这本小说有问题」**（章内接一段他人视角 / 书信是合法写法，
    判 `HARD` 就是判据把合法情况排除在外），报的是**我们这一层的能力边界**：
    「本章有 N 个叙述声音，而本层不记段界」。

    ⚠ **已裁撤**：`last_beat_id`（存量产物里还有，恒 `null`）。它只表达章内段界，
    零消费 + 零样本——「一章内换叙述者」在全语料 163 本 + 盘上 31 章里一个正样本都没有；
    真实发生的是**章之间**换人称，那由 `grammatical_person` 逐章存承担。
    """

    #: **闭合 3 值，只管人称这一个轴。** 盘上恒 `first`（31/31），另两个值在全语料上各有真样本。
    #:
    #: ⚠ **逐章存是实测辩护过的，不是保守**：`FQ_皇帝亲自替我宫斗` ch1 第一人称 →
    #: ch2~ch19 第三人称——**人称在章之间会变**，一个全书标量表达不了它。
    #: ⚠ 「限知 vs 全知」这一轴**刻意不在本层立字段**：Stage3 v2 已删除没有稳定
    #: 下游收益的定性文风抽取，不能在 Stage2 换个名字恢复一份尚未证明有消费价值的字段。
    #: 视点贴近程度与语法人称仍可能被混淆，这个已知能力缺口归 `B-09`。
    grammatical_person: Literal[NARRATIVE_PERSON]

    #: 模型从本章可见人物闭集里选择；`null` 表示原文没有足够信息。
    narrator_char_id: str | None
    focalizer_char_id: str | None

    #: 该段叙述者**自称的字面**，照原文用字（`我`/`咱`/`本宫`/`朕`）。第三人称填空数组。
    #: 盘上 31/31 长度恒 1，全是 `我`。
    #:
    #: ⚠⚠ **它存在的理由是「别拿代词密度当判据」**：
    #: `FQ_群臣逼朕送镇国大将军去青楼` 的「我」只有 0.1/千字，看着像第三人称，
    #: 实际是第一人称、自称是「朕」。
    pronoun_surfaces: list[str]


class Blueprint(StrictModel):
    """一章的蓝图。盘上 31 章。"""

    #: 全链的章唯一键。**[HARD] 覆盖度 / 顺序 / 不得出现 `section_id`。**
    chapter_ordinal: int = Field(ge=1)

    chapter_function: str = Field(min_length=1)

    #: 长度 5~16，盘上共 251 条。
    beats: list[Beat] = Field(min_length=1)

    #: 章级语义真相。`land` 原子必须在 Stage4 恰落一个场景，`context` 原子可复用。
    fact_atoms: list[FactAtom] = Field(min_length=1)
    causal_edges: list[CausalEdge]
    entry_state_refs: list[str]
    ending_state_refs: list[str]

    #: **盘上恒长度 1**（31/31）。
    narration: list[Narration] = Field(min_length=1)

    @model_validator(mode='after')
    def _semantic_refs_are_closed(self) -> Blueprint:
        fact_ids = [fact.fact_id for fact in self.fact_atoms]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError('fact_atom.fact_id 必须在章内唯一')
        beat_ids = {beat.beat_id for beat in self.beats}
        bad_beats = [fact.fact_id for fact in self.fact_atoms
                     if fact.source_beat_ref not in beat_ids]
        if bad_beats:
            raise ValueError(f'fact_atom 引用了本章外或不存在的 beat：{bad_beats}')
        refs = set(fact_ids)
        used = [ref for edge in self.causal_edges for ref in (edge.before, edge.after)]
        used += list(self.entry_state_refs) + list(self.ending_state_refs)
        missing = sorted(set(used) - refs)
        if missing:
            raise ValueError(f'章级语义引用不存在：{missing}')
        if any(edge.before == edge.after for edge in self.causal_edges):
            raise ValueError('causal_edge 不能自环')
        entry = set(self.entry_state_refs)
        ending = set(self.ending_state_refs)
        obligation = {fact.fact_id: fact.obligation for fact in self.fact_atoms}
        if any(obligation[ref] != 'context' for ref in entry):
            raise ValueError('entry_state_refs 只能引用 context 原子')
        if any(obligation[ref] != 'land' for ref in ending):
            raise ValueError('ending_state_refs 只能引用 land 原子')
        return self


class FactAtomResponse(StrictModel):
    """模型交局部引用与证据短语；正式 fact_id/source 由编译器产生。"""

    atom_key: str = Field(pattern=ATOM_KEY_PATTERN)
    claim: str = Field(min_length=1)
    kind: Literal[FACT_ATOM_KINDS]
    obligation: Literal[FACT_ATOM_OBLIGATIONS]
    evidence_phrase: str = Field(min_length=1)
    participant_char_ids: list[str]


class CausalEdgeResponse(StrictModel):
    before: str = Field(pattern=ATOM_KEY_PATTERN)
    after: str = Field(pattern=ATOM_KEY_PATTERN)


class HingeResponseV2(StrictModel):
    """一条保真材料：**只有种类和那几个字**，没有段号。

    短语必须能在**所属拍的区间内**原样找到且唯一；不唯一时 compiler 记
    `短语在本拍区间内多处命中`（修法是候选选择，不是重抄）。
    """

    kind: Literal[HINGE_KINDS]
    phrase: str = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二·c、`extraction-ir-v2` 的两段式响应契约
#
# Flash 的同书 A/B 显示：在一份响应里同时决定事件边界、地点、在场者与 hinge 时，
# 拍数比 Pro 少约三成；把 effort 拉高反而更粗。这里把「在哪里切」单独收成第一段，
# 第二段只在代码冻结的边界上填事实。最终产物形状不变，仍由编译器落成 `Beat`。
# ─────────────────────────────────────────────────────────────────────────────


class BoundaryBeatResponse(StrictModel):
    """事件切分预传：用一句微事件草图迫使模型实际枚举，再交闭区间止段。"""

    end_para: int = Field(ge=1)
    event: str = Field(min_length=1)


class BoundaryBlueprintResponse(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    micro_events: list[BoundaryBeatResponse] = Field(min_length=1)
    beats: list[BoundaryBeatResponse] = Field(min_length=1)


class ChapterBoundariesResponse(StrictModel):
    blueprints: list[BoundaryBlueprintResponse] = Field(min_length=1)


class BeatResponseV2Refs(StrictModel):
    """冻结边界后的细节响应。

    `present_char_ids` 只能从 user 消息下发的本章可见集合里选；动态集合由调用点
    validator 关闭式校验。`place` 恢复成 v3 已验证过的 nullable 字面：有据就填，
    确实无从判断才为 null，避免三态把 `P_UNKNOWN` 变成等权捷径。
    """

    end_para: int = Field(ge=1)
    time_layer: Literal[tuple(RESPONSE_TIME_LAYERS)]
    place: str | None
    present_char_ids: list[str]
    fact_atoms: list[FactAtomResponse] = Field(min_length=1)
    hinges: list[HingeResponseV2]


class NarrationResponseRefs(StrictModel):
    """叙述身份从本章可见人物闭集选择。"""

    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator_char_id: str | None
    focalizer_char_id: str | None
    pronoun_surfaces: list[str]


class BlueprintResponseV2Refs(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    beats: list[BeatResponseV2Refs] = Field(min_length=1)
    causal_edges: list[CausalEdgeResponse]
    entry_state_refs: list[str]
    ending_state_refs: list[str]
    narration: list[NarrationResponseRefs] = Field(min_length=1)


class ChapterBlueprintsResponseV2Refs(StrictModel):
    blueprints: list[BlueprintResponseV2Refs] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二·d、`extraction-ir-v3.1` 的单调用召回脚手架
#
# v2 的边界相与细节相都要求模型重新读同一章、重新建立一遍任务状态。v3.1 把两相合回
# 一次响应，但**不撤召回脚手架**：模型先列细粒度 cue，再只从这些止段里选最终拍。
# `micro_events` 在编译前即丢弃；叙述者走关闭式人物引用，hinge 优先选择代码
# 枚举的原文候选。它只是响应边界，最终仍编译成 `ChapterBlueprints`。
# ─────────────────────────────────────────────────────────────────────────────


class MicroEventResponse(StrictModel):
    """召回层的一条短提示；`cue` 不是第二份正式事件描述。"""

    end_para: int = Field(ge=1)
    cue: str = Field(min_length=1)


class HingeCandidateResponse(StrictModel):
    """从 user 下发的确定性原文候选中选择；位置由代码掌握，模型只交 id。"""

    kind: Literal[HINGE_KINDS]
    candidate_id: str = Field(pattern=r'^h\d{3}$')


# 不加 `mode`：`candidate_id` 与 `phrase` 两种严格模型本身已互斥，额外判别字段只会
# 增加模型输出。provider 不做 constrained decoding，本地 Pydantic 仍会关闭式拒收
# “两键都给 / 两键都不给 / 多余键”。
HingeResponseV31 = HingeCandidateResponse | HingeResponseV2


class BeatResponseV31Refs(StrictModel):
    end_para: int = Field(ge=1)
    time_layer: Literal[tuple(RESPONSE_TIME_LAYERS)]
    place: str | None
    present_char_ids: list[str]
    fact_atoms: list[FactAtomResponse] = Field(min_length=1)
    hinges: list[HingeResponseV31]


class ScaffoldedBlueprintResponseV31(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    micro_events: list[MicroEventResponse] = Field(min_length=1)
    beats: list[BeatResponseV31Refs] = Field(min_length=1)
    causal_edges: list[CausalEdgeResponse]
    entry_state_refs: list[str]
    ending_state_refs: list[str]
    narration: list[NarrationResponseRefs] = Field(min_length=1)


class ChapterBlueprintsResponseV31(StrictModel):
    blueprints: list[ScaffoldedBlueprintResponseV31] = Field(min_length=1)


class HingeSelectResponse(StrictModel):
    """候选选择（`--patch` 修 `hinge_ambiguous`）：代码枚举命中位，模型**只挑 id**。

    比「让模型多带几个字重抄一遍」稳定——代码枚举确定候选、模型只做受限选择，
    是整个 IR 的第一性原理在修复面上的形状。
    """

    candidate_id: str = Field(min_length=1)


class HingePhraseResponse(StrictModel):
    """短语重给（`--patch` 修 `hinge_anchor_void`）：原短语是编的，按该拍 span
    原文重给一条；「拍内原样唯一」由 validator 当场验，不唯一就换样本。"""

    phrase: str = Field(min_length=1)




class BlueprintContext(StrictModel):
    """蓝图调用的模型可见边界；只接受登记过的有界上下文架构。

    ⚠ `architecture` 曾**故意是单值**（v2→v3 时旧产物语义已坏，放宽等于把
    该重跑的旧蓝图读进来当成还能用）。2026-08-20 起逐步放宽为登记取值面：
    `extraction-ir-v1` 的产物语义与 v3 **逐字段等价**（区间仍是段号查表出的
    精确区间、下游消费面一个不动），后续契约同样只改响应边界而不改
    正式产物。并存不欠迁移——收窄/放宽的裁决依据始终是
    「旧字节语义还成不成立」，全案记录在
    `contracts/invariants.py::BLUEPRINT_ARCHITECTURES` 的注释里。
    """

    architecture: Literal[BLUEPRINT_ARCHITECTURES]
    stable_prefix: Fp10
    past_neighbor_chars_max: int = Field(ge=0)
    continuity_chars_max: int = Field(ge=0)
    past_names_chars_max: int = Field(ge=0)


class BlueprintProvenance(LlmProvenance):
    """`chapter_blueprints._provenance`。"""

    #: 某一批没按「本次任务范围」产出。**[HARD] 非空即失败**；
    #: **键不在 = 「不知道」，不发绿灯**。
    #: 产地无条件写这个键（没越界写空列表）。
    scope_violations: list[str]

    context: BlueprintContext

    # ── verbatim 回取的两笔独立账 ─────────────────────────────────────────────
    # 两个量纲的修复方式不同，因此分别记录，报告可准确指出需要处理的环节。
    #
    # ⚠⚠ **这里曾经是三笔**，第三笔 `verbatim_recovered`（按最长公共子串救回来的锚点）
    # 随 `bounded-fidelity-v3` 一并删除：beat 的区间现在由**段号**算出、不存在「锚点被
    # 模型改掉一两个字」这回事，而 hinge 那一侧的 LCS 兜底本来就是禁的
    # （`novel/core/spans.py` 模块头：救回来的「玉镯」是一个**语义已经变了**的片段）。
    # ⇒ 那个键在新架构下**结构性恒空**，留着就是一盏永远不亮的灯；
    # 它唯一的消费方 `anchor_not_verbatim` 同批裁撤。

    #: `(beat_id, 位置, 根因)`。**锚不到。[HARD] 非空即失败态。**
    #:
    #: ⚠ 它替掉早先那条「靠 `hinges[].text` 的**缺席**去反推失败态」——
    #: 失败态从此有**正面**的落点。根因**修法各不相同**，所以要分开报：
    #: · `位置 == 'span'`  —— 段号越界 / 段号区间倒序 / 段号不是整数 /
    #:   起段没越过上一拍的止段（区间重叠）
    #: · `位置 == 'hinge'` —— 段号不在本 beat 的段区间内 / 短语在那一段里找不到
    #: ⚠⚠ **`'hinge'` 那几条根因的中文字面是与 `novel/stages/stage2/checks.py` 的
    #: 隐式契约**（四分流按子串匹配）。改措辞而不同批改分流，会让 `hinge_anchor_void`
    #: 静默降级成 `hinge_drift`——守卫在 `tests/test_stage2_generation.py`。
    #: ⚠ 非空时产地照旧入显式终态，但**账要先落盘**——「显式终态」在多命令流水线里
    #: 只等于「这一步红了」，**不等于「链路被挡住了」**（§2.4 已记）。
    #: 出口：**断言计数 + 逐条列出**，按根因分开。
    verbatim_failures: list[tuple[str, str, str]]

    #: `(fact_id, source_beat_ref, 根因)`。事实 claim 仍保留，但证据为空并使产物 INVALID；
    #: 不允许 Stage4 把无证据的模型转述当作章级真相继续编译。
    fact_atom_failures: list[tuple[str, str, str]]

    #: `(beat_id, kind, phrase)`（`extraction-ir-v1` 专属账）：短语在所属拍区间内
    #: **多处命中**、被打回失败态的那几条 hinge 的原始短语。
    #: ⚠ 失败态的 hinge 只剩 `{kind, source: null}`——**短语本身不在产物里**，
    #: 而候选选择修复（代码按短语在拍区间内枚举命中位、模型只挑 candidate_id）
    #: 的输入正是这个短语。不落这笔账，字段级修复就只剩「回 LLM cache 捞原始响应」
    #: 一条路，而缓存是可驱逐的。候选**位置**不落盘：由短语+区间确定性重算，零成本。
    hinge_ambiguous_pending: list[tuple[str, str, str]] = Field(default_factory=list)

class ChapterBlueprints(StrictModel):
    """`stage2/chapter_blueprints.json` —— 顶层 2 个键。
    **逐章产出：当前章全文、未来章不可见，历史正文只走有界窗口。**

    一相契约每章一次调用；`extraction-ir-v2` 每章先取边界、再在冻结边界上取细节。

    结构化调用点声明的必需顶层键 = `blueprints`。

    ⚠ **已裁撤**：`source_id`。它**有一个读者**——把这个内部数字 id 原样插进规划节点的
    **prompt 载荷**，而那正是删它的理由（`B-05`：prompt 不许泄漏内部结构名/编号）。
    **移植时要连投影一起改**：只删字段不删投影，那处会安静地发一个 `None` 过去。
    """

    #: 长度 = 正文章数（盘上 8~14）。
    blueprints: list[Blueprint] = Field(min_length=1)

    #: `inputs` 恒两条：`stage1/main_text.json` ·
    #: `stage2/blueprint_character_projection.json`。Blueprint 不读 global 其他字段。
    provenance: BlueprintProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _chapter_ordinals_are_unique(self) -> ChapterBlueprints:
        """`chapter_ordinal` 是全链的章唯一键——重号会让下游按它做的每一次 join 取错章。

        ⚠ 这里**只查唯一性，不查连续性**：章覆盖与事实覆盖是跨产物判据
        （跨产物、要拿 `main_text` 比），归 stage2 的判据表，不归载体。
        """
        got = [b.chapter_ordinal for b in self.blueprints]
        if len(set(got)) != len(got):
            dup = sorted({o for o in got if got.count(o) > 1})
            raise ValueError(f'chapter_ordinal 重号：{dup}')
        return self
