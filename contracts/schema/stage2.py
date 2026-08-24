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
而且要在生成下一章之前就停住）。跨产物的那些（`beat_ref` / `beat_coverage`）归判据。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, BLUEPRINT_ARCHITECTURES, CACHE_POLICY, HINGE_KINDS,
    NAME_DISTINCTIVENESS, NARRATIVE_PERSON, PLACE_MODES, RESPONSE_TIME_LAYERS,
    SPAN_RECOVERY, TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import LlmProvenance

#: 「谁在这么叫」。取值是**某个 `char_id`**，或 `ADDRESS_BY` 里那两个字面之一。
#: ⚠ `ADDRESS_BY` 记的是**三类**（`char_id` 是类名不是值），所以这里必须写成模式，
#: 不能写成 `Literal[ADDRESS_BY]`——那会把全部真实的 `char_001` 拒掉。
AddressBy = Annotated[str, StringConstraints(pattern=r'^(char_\d+|narration|public)$')]

assert set(ADDRESS_BY) == {'char_id', 'narration', 'public'}, \
    'ADDRESS_BY 改了取值面而 AddressBy 那个模式没跟着改——两处必须一起看'


# ─────────────────────────────────────────────────────────────────────────────
# 一、`global_grounding.json`
# ─────────────────────────────────────────────────────────────────────────────

class Address(StrictModel):
    """一条称谓。`R-2.2` ~ `R-2.5` 全部落在这里。"""

    #: 长度 1~10。**[HARD] 整条不许是代词**（`R-2.3`）：代词相对说话人，
    #: 收了它下游必然取到「我妈」这种说法。**[INFO] 必须在原文里原样搜得到**（`R-2.2`）。
    #: ⚠ 落盘前会被**最长子串回取**改写，改写清单记进 `_provenance.address_surface_rewrites`。
    surface: str = Field(min_length=1)

    #: **闭合**。盘上 `narration` 86 · `char_id` 52 · `public` 18。
    #:
    #: ⚠ **群体名一律压成 `public`**（「苏府下人」解析不出人），因为 `by` 的消费是
    #: **派发键**——一个解析不出人的串对派发毫无用处。已知代价：丢掉「哪个群体」。
    #: 盘上兜底 0 次，⚠ 这**不是**「没发生过」的证据，它同时也可以是
    #: 「这三本恰好没有群体称呼」。
    #: ⚠ 这与 `Secret.character_known_by` 的处置**故意相反**：那边要的是「谁知情」，
    #: 群体本身就是答案，逼它写 id 只会逼出假 id。
    #: **同一个「无名群体」在两个字段上的正确处置方向相反**——
    #: 改之前先问它在这里是**派发键**还是**答案**。
    by: AddressBy

    #: `address`（当面）｜ `reference`（背后）。盘上 44 / 112。
    context: Literal[ADDRESS_CONTEXT]

    #: ≥1 且在全书章范围内。**[HARD] 不早于该 `surface` 在原文里首次出现的章**——
    #: **单边**（`R-2.5`）。⚠ 落盘前会被**只往后抬**，记进 `_provenance.address_from_chapter_raised`。
    from_chapter: int = Field(ge=1)


class Character(StrictModel):
    """`entities.characters` 的一个人。盘上 75 个。"""

    #: 长度 1~8。**[HARD] 全书唯一**（`R-2.1`）。
    canonical: str = Field(min_length=1)

    #: 长度 1~10，盘上共 156 条。
    address: list[Address] = Field(min_length=1)

    #: **无守卫 → `B-08`**（`R-2.10`）。它**不下发给写作节点**——
    #: 只给确定性代码用（成稿人名检查的左锚 / stage3 专名过滤）。
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]

    #: 静态身份，长度 12~106。**[HARD] 非空 · 不含结局词 · 不含模棱语 ·
    #: 不得与 `canonical` 同字**（`R-2.6`）。
    #:
    #: ⚠ **它是一条已知的、未修的结局旁路**：实测某本的 `孟青兰` 写着
    #: 「后嫁富商老王为妾」——那是这个角色的**归宿**，属【硬禁·结局】要挡的东西，
    #: 而词表只抓「转抄式结局词」（实测一本上命中 0/17 而人读真违规 6/17）。
    #: **它不是恒为 0，只是判别力低**；校验器为此常驻打印一条 `[????] role 不含结局`。
    role: str = Field(min_length=1)

    #: 长度 2~58；**盘上 6/75 为 `null`**（键恒在）。只统计（`R-2.7`）。
    #: ⚠ **`fate` 一概不下发给写手**——写手提前知道谁最后会死，第 1 章的伏笔就写过明。
    fate: str | None


class Relation(StrictModel):
    """一条有向关系。盘上 70 条。"""

    relation_id: str = Field(pattern=r'^rel_\d{2}$')

    #: **必须是 `characters` 里的 `char_id`**（地点/机构/物品不入 relations）。
    #: **[INFO] 两端可解析**——不是 HARD。
    subject_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)

    predicate: str = Field(min_length=1)

    #: 关系**世内**成立的章。**[HARD] 在章范围内**（`R-2.8`）。
    #:
    #: ⚠⚠ **它是【世内生效时点】，不是【读者知情时点】。** 秘密类关系上两者系统性错位，
    #: 于是 relations 是 secrets 之外的**第二条剧透通道**。
    #: **不加字段**（同一语义两个落点必然漂移），在**下发侧**解决：两端人名都在某条
    #: secret 的 `fact` 里、且 `valid_from` 落在隐瞒窗口内的关系，改按 `reader_reveal_events` 闸门。
    #: ⚠ 那条闸门的前件是 `setup_chapters`（冷揭示的 secret 没有隐瞒窗口，不参与）。
    #: ⚠⚠ **旧注释写的「盘上 20/21 是空数组 ⇒ 等于没开」是已删语料时代的读数，已作废**
    #: （2026-08-21 复核）。当前盘上是 **6/22 冷揭示**，且**逐模型分布很不一样**：
    #: Pro 那本 **0/4**（埋点全填了，闸门全程有效），Flash 三本 1/7 · 1/4 · **4/7**。
    #: ⇒ 闸门在 Pro 产的 global 上是开着的；Flash 会掉一部分，那是**生成侧换模型**
    #: 要盯的读数，不是这条闸门本身失效。
    valid_from_chapter: int = Field(ge=1)

    #: 失效点（和离/亡故/决裂），没失效填 `null`（键恒在）。盘上 22/70 非 `null`。
    valid_to_chapter: int | None


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

    #: 长度 33~69。
    #: ⚠ **逐章闸门只挡转抄式泄底**（字面子串包含），实测在唯一有 secrets 的那本上
    #: 命中 **0/8**，包括一条已知真泄漏。命中 0 时打常驻 `[????]`，**不打 `[OK]`**。
    worldview: str = Field(alias='世界观', min_length=1)


class WorldRuleVersion(StrictModel):
    """一条规则的一个版本。**规则正文的唯一落点。**"""

    #: 从头没变过的规则也要给一条，填 `1`。
    effective_from_chapter: int = Field(ge=1)

    #: 长度 23~94。**必须连「怎么运作 / 怎么判读」一起写**——只写「存在」等于没记。
    #: **无守卫 → `B-08`**。
    #: ⚠ 下游取正文时**无兜底，缺失即显式终态**（`or description` 那个兜底就是漏洞本身）。
    desc: str = Field(min_length=1)


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


class ReaderRevealEvent(StrictModel):
    """读者第几章被揭示——**逐章剧透闸门的依据**（`secrets` 与 `relations` 两条通道都按它闸）。"""

    chapter: int = Field(ge=1)


class Secret(StrictModel):
    """一条秘密。盘上 21 条。"""

    secret_id: str = Field(pattern=r'^secret_\d{2}$')

    #: 长度 17~48。写**被瞒住的真相**，不是揭示那章的情节。
    fact: str = Field(min_length=1)

    #: 长度 1~11，盘上共 73 项。**[HARD] 按【形状】分流**：`char_\\d+` 必须能解析；
    #: 其余是自由文本（「在场士兵」），放行。
    #: ⚠ **盘上 73/73 全是 `char_id`——自由文本那一臂零样本。**
    character_known_by: list[str] = Field(min_length=1)

    #: 埋点章。**[HARD] 每个都严格 `<` 最早揭示章**（`R-2.9`）——
    #: ⚠ **判据断言的是 `_provenance.setup_chapters_pruned` 那个计数，不是本字段的值**：
    #: 裁剪跑在落盘前，再断言字段值就永远为真。
    #:
    #: ⚠ **「冷揭示」与「被裁空」在字段值上完全同形**（盘上 **6/22 条**是空数组，
    #: 2026-08-21 复核——旧注释的「20/21」是已删语料时代的读数，已作废）：
    #: 前者合法；后者说明产地在把揭示章号抄进埋点。两者只能靠那个计数分开。
    #: ⚠ 分布逐模型差很多：Pro 那本 **0/4**，Flash 三本 1/7 · 1/4 · **4/7**。
    setup_chapters: list[int]

    #: 长度 1~2。分几次揭示记几条。
    reader_reveal_events: list[ReaderRevealEvent] = Field(min_length=1)


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
    chapters: list[int] = Field(min_length=1)


class ArcTrack(StrictModel):
    """一条人物弧线。盘上 16 条。**仅末章下发。**"""

    #: `char_id` ｜ `"reader"` ｜ `relation_id`。**三种都必须能解析成人话**，否则显式终态。
    #: ⚠ 这条链路**曾经断着**：`relation_id` 被引用却从未被解析，未解析的 `rel_05`
    #: 直接进了写作 prompt。**有确定性消费方 ≠ 那条链路接通了。**
    subject: str = Field(min_length=1)

    #: **自由短词**（处境／权力／立场／认知／关系亲疏…）。盘上 11 个互不相同。
    dimension: str = Field(min_length=1)

    #: ⚠⚠ **开放枚举，故意不做成 `Literal`。**
    #: `stage2.md` §1.6 写的是 `stable｜development｜revelation｜deterioration｜reversal｜…`
    #: ——**末尾那个 `…` 是语义的一部分**。把它闭合会在下一本书上拒收一份合法产物。
    #: ⚠ 与 `WORLD_RULE_KINDS`（闭合 7 值）恰好相反，两者不许互相类比。
    #: **无守卫 → `B-08`**；盘上只见 3 个。
    change_type: str = Field(min_length=1)

    #: 长度 16~38。要说清**从什么变到什么**，不能只写「有成长」。**无守卫 → `B-08`**。
    #: ⚠ `dimension` / `change_type` **只在与它捆在一起时才携带信息**——
    #: 剥掉 `trajectory` 之后剩下的 `{char_001, 道德底线, deterioration}` 对写手是零信息。
    #: **别把它们当成三个独立的信号。**
    trajectory: str = Field(min_length=1)


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


class Entities(StrictModel):
    #: 键即 `char_id`。盘上 75 个，全部形如 `char_\\d{3}`，主角恒 `char_001`。
    #: ⚠ **键的格式没有任何判据在查**，下游只做成员检查（`x in registry`）——
    #: 所以这里**故意不加 pattern**：加一个就是替一个不存在的守卫声称了存在。
    #: 形状真正承重的地方在别处（`Secret.character_known_by` 的 `char_\\d+` 分流）。
    characters: dict[str, Character]

    relations: list[Relation]


class GlobalProvenance(LlmProvenance):
    """`global_grounding._provenance` —— 五个确定性归一记账键。

    ⚠ **记账不是终点，报出来才是**（`Q-1`）：这五个键各有**指定出口**
    （断言计数 / 逐条列出），细则见 `stage2.md` §3.3。三条形式要求：
    **空也要报**（报成 `0 处`）· 一行常驻非空才展开 · 出口在校验报告不在 stdout。

    ⚠ **已裁撤**：`changed`。它是一个**进程内**的开关（「什么都没改时不落盘」），
    确定性重归一那条路径 `pop` 掉它、正常产出那条路径忘了 pop，于是它落了盘——
    盘上 3/3 恒 `True`，**没有任何人读它**。进程内那个开关照旧。
    """

    #: `by` 兜底成 `public` 的次数。**校验器断言这个计数**——
    #: 兜底跑在落盘前，再断言字段值就是一条永远为真的判定。
    address_by_fallback: int = Field(ge=0)

    #: `(char_id, 原值, 回取值)`。盘上 0 / 2 / 8 条。出口：**逐条列出给人扫**。
    address_surface_rewrites: list[tuple[str, str, str]]

    #: `(char_id, surface, 原章, 抬到的章)`。盘上共 7 条。出口：**逐条列出**。
    address_from_chapter_raised: list[tuple[str, str, int, int]]

    #: `(char_id, 原值, 剥完的值)`。**盘上 3/3 本为空**。
    #: ⚠ 正因为恒空，它是「**空也要报**」那条形式要求的教科书：如果空就不打印，
    #: 这个键从落地到今天**一次都不会出现在任何报告里**，与不存在毫无区别。
    canonical_distinguishers_stripped: list[tuple[str, str, str]]

    #: `(secret_id, 原 setup_chapters, 裁完的)`。**校验器断言的是它，不是字段值。**
    #: 空列表 = 这一趟没裁过；**键不在 = 「不知道」，不发绿灯**。
    setup_chapters_pruned: list[tuple[str, list[int], list[int]]]

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


class SecretPatch(StrictModel):
    """一条 secret 的人工订正。**按 `secret_id` 定位；给了哪个字段就整字段替换那个。**

    ⚠ **字段级替换而不是组内合并**：`character_known_by` 少写一个人与「这一项没给」
    在合并语义下同形，而它们是两个决定。`None` = 这一格人没动过。
    ⚠ **`setup_chapters` 的 `[]` 是有语义的**（「冷揭示」，盘上 6/22 条如此），
    所以它必须是 `list[int] | None` 而不是靠空列表表示未给——两者一旦同形，
    「人把埋点清空了」就再也说不出口。
    """

    secret_id: str = Field(pattern=r'^secret_\d{2}$')

    fact: str | None = Field(default=None, min_length=1)
    character_known_by: list[str] | None = Field(default=None, min_length=1)
    setup_chapters: list[int] | None = None
    reader_reveal_events: list[ReaderRevealEvent] | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _at_least_one_field(self) -> SecretPatch:
        given = (self.fact, self.character_known_by,
                 self.setup_chapters, self.reader_reveal_events)
        if all(value is None for value in given):
            raise ValueError(
                f'{self.secret_id}: 一个字段都没给——空补丁什么都不改，'
                f'而它在盘上与「人看过并改了」长得一样')
        return self


class WorldRulePatch(StrictModel):
    """Typed correction for an existing world rule; never creates/deletes one."""

    rule_id: str = Field(pattern=r'^wr_\d{2}$')
    kind: Literal[WORLD_RULE_KINDS] | None = None
    versions: list[WorldRuleVersion] | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _has_change(self) -> 'WorldRulePatch':
        if self.kind is None and self.versions is None:
            raise ValueError(f'{self.rule_id}: empty world-rule patch')
        return self


class CharacterEnumPatch(StrictModel):
    """Typed correction for the derived name distinctiveness enum."""

    char_id: str = Field(pattern=r'^char_\d{3}$')
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]


class AddressContextPatch(StrictModel):
    """Correct one existing address enum by its audited list position."""

    char_id: str = Field(pattern=r'^char_\d{3}$')
    address_index: int = Field(ge=0)
    context: Literal[ADDRESS_CONTEXT]


class CharacterAddressReplacement(StrictModel):
    """Replace one character's complete address surface.

    A positional patch cannot add an omitted address, remove an invented one,
    or correct ``surface``/``by``/``from_chapter``.  The replacement is keyed
    by the stable character id and deliberately carries the whole typed list.
    """

    char_id: str = Field(pattern=r'^char_\d{3}$')
    address: list[Address] = Field(min_length=1)


class GlobalEdit(StrictModel):
    """`stage2/global_edit.json` —— typed Stage2 human corrections.

    【它解决的是什么】细粒度 patch 适合订正已有事实的一格，却无法补漏、删除幻觉项，
    也无法把拥有不同读者揭示时点的复合 secret 拆成独立记录。本载体因此同时提供
    existing-row patches 与 typed complete replacements；后者允许有证据的新增/删除/拆分，
    并由运行时在任何写盘前校验全部外键与章号。

    ⚠ 同一 subject 的 patch 与 replacement 严格互斥。否则 replacement 会重定义集合或
    address 下标，而最后生效的是哪份人工决定将取决于实现遍历顺序。
    ⚠ 编辑文件落在书目录里、进 `ARTIFACTS`、被 `store.read` 当上游边记账，
    **形状即契约**（照 stage3/4/6/7 四份 `*_edit.json` 的先例）。
    """

    secrets: list[SecretPatch] = Field(default_factory=list)
    world_rules: list[WorldRulePatch] = Field(default_factory=list)
    characters: list[CharacterEnumPatch] = Field(default_factory=list)
    addresses: list[AddressContextPatch] = Field(default_factory=list)

    #: Complete-set replacements are nullable rather than defaulting to ``[]``:
    #: an explicit empty list means "delete every row", while ``null`` means
    #: that this subject was not edited.  That distinction is required for
    #: omission repair and cannot be recovered after parsing.
    secrets_replace: list[Secret] | None = None
    world_rules_replace: list[WorldRule] | None = None
    addresses_replace: list[CharacterAddressReplacement] = Field(
        default_factory=list)

    @model_validator(mode='after')
    def _one_patch_per_subject(self) -> GlobalEdit:
        if not (
            self.secrets or self.world_rules or self.characters or self.addresses
            or self.secrets_replace is not None
            or self.world_rules_replace is not None
            or self.addresses_replace
        ):
            raise ValueError('global edit must contain at least one typed patch')
        if self.secrets_replace is not None and self.secrets:
            raise ValueError(
                'canon.secrets cannot be patched and completely replaced in one edit')
        if self.world_rules_replace is not None and self.world_rules:
            raise ValueError(
                'canon.world_rules cannot be patched and completely replaced in one edit')
        got = [patch.secret_id for patch in self.secrets]
        if len(set(got)) != len(got):
            dup = sorted({sid for sid in got if got.count(sid) > 1})
            raise ValueError(
                f'同一条 secret 给了多份补丁：{dup}。'
                f'哪一份生效取决于遍历顺序 ⇒ 人的决定会被实现细节改写')
        for label, keys in (
            ('world rule', [patch.rule_id for patch in self.world_rules]),
            ('character', [patch.char_id for patch in self.characters]),
            ('address', [(patch.char_id, patch.address_index) for patch in self.addresses]),
        ):
            if len(keys) != len(set(keys)):
                raise ValueError(f'duplicate {label} patches are order-dependent')

        replacement_characters = [row.char_id for row in self.addresses_replace]
        if len(replacement_characters) != len(set(replacement_characters)):
            raise ValueError('duplicate complete address replacements are order-dependent')
        patched_address_characters = {row.char_id for row in self.addresses}
        conflicts = sorted(set(replacement_characters) & patched_address_characters)
        if conflicts:
            raise ValueError(
                f'character address cannot be patched and completely replaced together: '
                f'{conflicts}')

        for label, rows, key in (
            ('secret', self.secrets_replace, 'secret_id'),
            ('world rule', self.world_rules_replace, 'rule_id'),
        ):
            if rows is None:
                continue
            ids = [str(getattr(row, key)) for row in rows]
            if len(ids) != len(set(ids)):
                duplicate = sorted({item for item in ids if ids.count(item) > 1})
                raise ValueError(
                    f'duplicate {label} ids in complete replacement: {duplicate}')
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
    from_chapter: int = Field(ge=1)


class CharacterResponse(StrictModel):
    canonical: str = Field(min_length=1)
    address: list[AddressResponse] = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]
    role: str = Field(min_length=1)
    fate: str | None


class EntitiesResponse(StrictModel):
    characters: dict[str, CharacterResponse]
    relations: list[Relation]


class GlobalGroundingResponse(StrictModel):
    """stage2.global 的不可信响应；不含任何代码产字段。"""

    entities: EntitiesResponse
    canon: Canon
    arc: Arc


class RoleFateResponse(StrictModel):
    role: str = Field(min_length=1)
    fate: str | None


class RoleSplitResponse(StrictModel):
    """purge-role 只允许交回 role/fate；char_id 是映射键。"""

    characters: dict[str, RoleFateResponse]


# ─────────────────────────────────────────────────────────────────────────────
# 二、`chapter_blueprints.json`
# ─────────────────────────────────────────────────────────────────────────────



class SourceSpan(StrictModel):
    """正式制品里的原文引用；模型 locator 只存在于响应归一边界。"""

    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    normalized: str = Field(min_length=1)

    #: 取值面与「为什么只剩两档」的落点：`contracts/invariants.py::SPAN_RECOVERY`。
    recovery: Literal[SPAN_RECOVERY]

    @model_validator(mode='after')
    def _end_is_after_start(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError('source span end must be greater than start')
        return self




class Hinge(StrictModel):
    """一条保真材料；source 为空只表示需要重生成的 INVALID 状态。"""

    kind: Literal[HINGE_KINDS]
    source: SourceSpan | None


#: 源 beat 编号的形状。**这里是唯一一份**——Stage4 的响应侧 `PlanBeatResponse.spine`
#: 收的也是一个 `beat_id`（2026-08-22 起：主干从「第几件事」改成「哪一件事」），
#: 两处各写一遍正则，改宽度时就会有一处不跟着改。
BEAT_ID_PATTERN = r'^b\d{2}_\d{2}$'


class Beat(StrictModel):
    """一拍及其经过原文一致性验证的精确覆盖区间。"""

    beat_id: str = Field(pattern=BEAT_ID_PATTERN)
    span: SourceSpan | None
    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present_names: list[str]
    present_char_ids: list[str]
    event: str = Field(min_length=1)
    hinges: list[Hinge]


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

    #: **模型写的决策**，与 `present_names` 同一条规矩（用原文里的叫法，叫不出填 `null`）。
    #: 盘上各 4/31 为 `null`（键恒在）。
    narrator_name: str | None
    focalizer_name: str | None

    #: **代码解析出来的。[HARD] 在 registry 或 `null`。**
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

    #: 长度 81~329。**[HARD] 存在且非空**（`R-3.13`）。
    #: ⚠ **0 确定性消费方**，唯一消费方是计划节点的 prompt——而规划节点手里**有全书原文**，
    #: 所以它是一层**摘要**而不是唯一的章级线索：留着是因为「帮它快速理解本章、无害」，
    #: **不是因为不可替代**。
    summary: str = Field(min_length=1)

    #: 长度 5~16，盘上共 251 条。
    beats: list[Beat] = Field(min_length=1)

    #: **盘上恒长度 1**（31/31）。
    narration: list[Narration] = Field(min_length=1)


class AnchorLocatorResponse(StrictModel):
    """模型只交**段号区间**；正式 source span 由代码按段落表回取并验证。

    ⚠⚠ **这里曾是 `starts_with` / `ends_with` 两个逐字原文片段**（2026-08-19 改）。
    换掉的理由不是形状不好看，是成本：那种形状要模型每章做约 38 次
    「原样照抄、必须能在本章原文一字不差找到」的转写核对，而本节点的 reasoning
    占它自己 completion 的 88.0%、占全书生成侧的 45.5%。段号是模型**读得出**、
    不需要转写的东西 ⇒ 同一件事不再需要深想。
    ⚠ 段号是**闭区间、1-based、逐章独立**，口径的唯一落点是
    `novel/stages/stage2/paragraphs.py`——那里同时负责下发时的编号，两边不许各切一次。
    """

    start_para: int = Field(ge=1)
    end_para: int = Field(ge=1)

    @model_validator(mode='after')
    def _end_para_is_not_before_start(self) -> AnchorLocatorResponse:
        if self.end_para < self.start_para:
            raise ValueError('end_para must not be before start_para')
        return self


class HingeResponse(StrictModel):
    """一条保真材料的定位：**哪一段 + 段内的哪几个字**。

    ⚠ **不做成纯段号**：hinge 指的是「一句要害台词 / 一个硬数字」，而本仓语料均段
    只有约 21 字——退成整段会把「我今年九岁」这种两三个字的硬数字连着整段吞下去，
    下游 `stage5` 的 `must_appear` 材料跟着变粗。
    ⚠ **也不做成段区间**：短语只需在**那一段之内**可定位，搜索面就从整章缩到约 21 字，
    转写核对的成本基本归零，而精度一点没丢。
    """

    kind: Literal[HINGE_KINDS]
    para: int = Field(ge=1)
    phrase: str = Field(min_length=1)


class BeatResponse(StrictModel):
    """模型的拍；beat_id 会归一，char_id 不允许模型猜。"""

    beat_id: str = Field(min_length=1)
    span: AnchorLocatorResponse
    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present_names: list[str]
    event: str = Field(min_length=1)
    hinges: list[HingeResponse]


class NarrationResponse(StrictModel):
    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator_name: str | None
    focalizer_name: str | None
    pronoun_surfaces: list[str]


class BlueprintResponse(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    summary: str = Field(min_length=1)
    beats: list[BeatResponse] = Field(min_length=1)
    narration: list[NarrationResponse] = Field(min_length=1)


class ChapterBlueprintsResponse(StrictModel):
    """一次调用原则上只产一章；越界章仍需先接住并记账。"""

    blueprints: list[BlueprintResponse] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二·b、`extraction-ir-v1` 的响应契约（`Stage2_improvement.md` 改造）
#
# 与 v3 的差别只有四族，全部朝同一个方向：**模型只做不可避免的局部语义判断，
# 定位/编号/一致性归代码**（compiler：`novel/stages/stage2/compile_v2.py`）。
#   ① beat 只交 `end_para`（起点=上一拍止段+1，首拍起 1，末拍必须到底）
#      ⇒ 重叠/倒序/漏头尾在结构上不可产生；
#   ② hinge 去掉 `para`（交接实测 10 个 verbatim_failed 里 9 个短语对、段号错），
#      改在**所属拍区间内**唯一匹配，多处命中走候选选择修复；
#   ③ place 从自由文本改成判别三态（`P_EXPLICIT`/`P_INHERIT`/`P_UNKNOWN`）；
#   ④ summary 不再由抽取响应携带（书级摘要批独立生成，events 是唯一语义源）。
# 产物模型一个不动——这里只是响应边界，编译后仍落现有 `ChapterBlueprints`。
# ─────────────────────────────────────────────────────────────────────────────


class HingeResponseV2(StrictModel):
    """一条保真材料：**只有种类和那几个字**，没有段号。

    短语必须能在**所属拍的区间内**原样找到且唯一；不唯一时 compiler 记
    `短语在本拍区间内多处命中`（修法是候选选择，不是重抄）。
    """

    kind: Literal[HINGE_KINDS]
    phrase: str = Field(min_length=1)


class ExplicitPlace(StrictModel):
    """原文明写（或延续中可辨认）的地点，必带 `name`。"""

    mode: Literal['P_EXPLICIT']
    name: str = Field(min_length=1)


class InheritPlace(StrictModel):
    """延续上一相邻拍的地点。**继承由代码做**（仅同 `time_layer` 且相邻才许）。"""

    mode: Literal['P_INHERIT']


class UnknownPlace(StrictModel):
    """确实无从判断 ⇒ 产物侧 `place=null`。不确定性是受约束的状态，不是自由文本。"""

    mode: Literal['P_UNKNOWN']


#: 判别联合：非法组合（`P_EXPLICIT` 无名、`P_UNKNOWN` 带名）**从形状上不可表达**，
#: 不靠跨字段校验兜。discriminator 取值面在 `contracts/invariants.py::PLACE_MODES`。
PlaceRef = Annotated[ExplicitPlace | InheritPlace | UnknownPlace,
                     Field(discriminator='mode')]

assert set(PLACE_MODES) == {'P_EXPLICIT', 'P_INHERIT', 'P_UNKNOWN'}, \
    'PLACE_MODES 改了取值面而 PlaceRef 三个成员没跟着改——两处必须一起看'


class BeatResponseV2(StrictModel):
    """`extraction-ir-v1` 的拍：**没有 `beat_id`（代码编号）、没有起点（代码推导）**。

    `time_layer` 用响应侧前缀枚举（`T_*`），compiler 经
    `contracts/invariants.py::RESPONSE_TIME_LAYERS` 映回产物值。
    """

    end_para: int = Field(ge=1)
    time_layer: Literal[tuple(RESPONSE_TIME_LAYERS)]
    place: PlaceRef
    present_names: list[str]
    event: str = Field(min_length=1)
    hinges: list[HingeResponseV2]


class BlueprintResponseV2(StrictModel):
    """一章的最小抽取 IR。**没有 `summary`**——它是 events 的派生视图，
    由书级摘要批独立生成（`SummaryBatchResponse`），不再是第二份语义真相。"""

    chapter_ordinal: int = Field(ge=1)
    beats: list[BeatResponseV2] = Field(min_length=1)
    narration: list[NarrationResponse] = Field(min_length=1)


class ChapterBlueprintsResponseV2(StrictModel):
    """一次调用原则上只产一章；越界章仍需先接住并记账（与 v3 同规）。"""

    blueprints: list[BlueprintResponseV2] = Field(min_length=1)


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
    event: str = Field(min_length=1)
    hinges: list[HingeResponseV2]


class BlueprintResponseV2Refs(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    beats: list[BeatResponseV2Refs] = Field(min_length=1)
    narration: list[NarrationResponse] = Field(min_length=1)


class ChapterBlueprintsResponseV2Refs(StrictModel):
    blueprints: list[BlueprintResponseV2Refs] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二·d、`extraction-ir-v3 / v3.1` 的单调用召回脚手架
#
# v2 的边界相与细节相都要求模型重新读同一章、重新建立一遍任务状态。v3 把两相合回
# 一次响应，但**不撤召回脚手架**：模型先列细粒度 cue，再只从这些止段里选最终拍。
# `micro_events` 在编译前即丢弃；正式产物仍只有 `beats[].event` 一份语义真相。
# v3.1 再把叙述者收成关闭式人物引用，并允许 hinge 优先选择代码枚举的原文候选。
# 两套都只是响应边界，最终仍编译成既有 `ChapterBlueprints`。
# ─────────────────────────────────────────────────────────────────────────────


class MicroEventResponse(StrictModel):
    """召回层的一条短提示；`cue` 不是第二份正式事件描述。"""

    end_para: int = Field(ge=1)
    cue: str = Field(min_length=1)


class ScaffoldedBlueprintResponseV3(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    micro_events: list[MicroEventResponse] = Field(min_length=1)
    beats: list[BeatResponseV2Refs] = Field(min_length=1)
    narration: list[NarrationResponse] = Field(min_length=1)


class ChapterBlueprintsResponseV3(StrictModel):
    blueprints: list[ScaffoldedBlueprintResponseV3] = Field(min_length=1)


class HingeCandidateResponse(StrictModel):
    """从 user 下发的确定性原文候选中选择；位置由代码掌握，模型只交 id。"""

    kind: Literal[HINGE_KINDS]
    candidate_id: str = Field(pattern=r'^h\d{3}$')


# 不加 `mode`：`candidate_id` 与 `phrase` 两种严格模型本身已互斥，额外判别字段只会
# 增加模型输出。provider 不做 constrained decoding，本地 Pydantic 仍会关闭式拒收
# “两键都给 / 两键都不给 / 多余键”。
HingeResponseV31 = HingeCandidateResponse | HingeResponseV2


class NarrationResponseRefs(StrictModel):
    """叙述身份也走本章可见人物引用；名字由代码投影回正式产物。"""

    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator_char_id: str | None
    focalizer_char_id: str | None
    pronoun_surfaces: list[str]


class BeatResponseV31Refs(StrictModel):
    end_para: int = Field(ge=1)
    time_layer: Literal[tuple(RESPONSE_TIME_LAYERS)]
    place: str | None
    present_char_ids: list[str]
    event: str = Field(min_length=1)
    hinges: list[HingeResponseV31]


class ScaffoldedBlueprintResponseV31(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    micro_events: list[MicroEventResponse] = Field(min_length=1)
    beats: list[BeatResponseV31Refs] = Field(min_length=1)
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


class SummaryChapterResponse(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    summary: str = Field(min_length=1)


class SummaryBatchResponse(StrictModel):
    """书级摘要批：全部章 blueprint 完成后**一次调用**按 events 产全书摘要。

    输入已是压缩事实（各章 beat events），不重读原章。validator 硬校验
    目标章集恰好覆盖与长度界；「不引入 events 外新事实」查不了文面，
    归付费冒烟的人工抽查。⚠ 直接拼接 events 当 summary 被否决：
    实测拼接体积是人写摘要的 2.38 倍，会反向放大 stage4.plan 的输入。
    """

    chapters: list[SummaryChapterResponse] = Field(min_length=1)




class BlueprintContext(StrictModel):
    """蓝图调用的模型可见边界；只接受登记过的有界上下文架构。

    ⚠ `architecture` 曾**故意是单值**（v2→v3 时旧产物语义已坏，放宽等于把
    该重跑的旧蓝图读进来当成还能用）。2026-08-20 起逐步放宽为登记取值面：
    `extraction-ir-v1` 的产物语义与 v3 **逐字段等价**（区间仍是段号查表出的
    精确区间、下游消费面一个不动），后续 v1.1/v2 同样只改响应边界而不改
    正式产物。并存不欠迁移——收窄/放宽的裁决依据始终是
    「旧字节语义还成不成立」，全案记录在
    `contracts/invariants.py::BLUEPRINT_ARCHITECTURES` 的注释里。
    """

    architecture: Literal[BLUEPRINT_ARCHITECTURES]
    batch_size: Literal[1]
    stable_prefix: Fp10
    current_chapter_full: Literal[True]
    future_visible: Literal[False]
    past_neighbor_chars_max: int = Field(ge=0)
    continuity_chars_max: int = Field(ge=0)
    past_names_chars_max: int = Field(ge=0)


class CharRefs(StrictModel):
    """称呼解析的八个计数。

    **解析是可重跑的确定性步骤**，不记这几个数，「这份产物解析时有几条没解出来」
    在下一次重算之后就**永远失去答案**。

    ⚠ 五分的**处置完全不同，绝不合成一个「无据」**（分母 569 = 515 个 `present_names`
    + 54 个非 `null` 的 narrator/focalizer 名）：
    唯一命中 501 · 代词 15（5 成功 / 10 定不了）· 歧义 1 · 后见之明 0 · 漏收 52 · 自造 0。
    ⚠ **歧义不判 HARD**：那是**原文自己的歧义**，重跑一百次也一样，
    判 HARD 只会得到一盏永远红着的灯。三条候选消歧规则实测 **0/9 有效**，
    所以**没有**消歧逻辑。
    """

    resolved: int = Field(ge=0)
    pronoun_ok: int = Field(ge=0)
    ambiguous: int = Field(ge=0)
    late: int = Field(ge=0)
    unknown: int = Field(ge=0)
    invented: int = Field(ge=0)
    no_names: int = Field(ge=0)
    pronoun: int = Field(ge=0)


class RepairMetrics(StrictModel):
    """定向重生成前后的三个计数。"""

    beats: int = Field(ge=0)
    hinges: int = Field(ge=0)
    present_names: int = Field(ge=0)


class Repair(StrictModel):
    """一次定向重生成。盘上 2/3 本、共 3 条。

    ⚠ `cache_policy` 盘上恒 `refresh-write` —— **定向重生成必须配「不读缓存」**（`R-3.11`）：
    它的 prompt 与整跑时该章那一批**逐字相同**，缓存必然命中，
    于是**一个字都没重新生成而它照样打印「已重生成」**。
    """

    chapter_ordinal: int = Field(ge=1)
    failure_items: list[str] = Field(min_length=1)
    failure_evidence: Fp10
    user_prompt: Fp10
    output: Fp10
    model: str = Field(min_length=1)
    cache_policy: Literal[CACHE_POLICY]

    #: 重生成了哪几个字段。盘上恒 `['*']`（整章重产）。
    fields: list[str] = Field(min_length=1)

    before_metrics: RepairMetrics
    after_metrics: RepairMetrics


class BlueprintProvenance(LlmProvenance):
    """`chapter_blueprints._provenance`。"""

    #: 某一批没按「本次任务范围」产出。**[HARD] 非空即失败**；
    #: **键不在 = 「不知道」，不发绿灯**。
    #: 产地无条件写这个键（没越界写空列表）。
    scope_violations: list[str]

    #: 恒 `[[1],[2],…]`，记录每次逐章调用的范围。
    batches: list[list[int]]

    context: BlueprintContext
    char_refs: CharRefs

    # ── verbatim 回取的两笔独立账 ─────────────────────────────────────────────
    # 两个量纲的修复方式不同，因此分别记录，报告可准确指出需要处理的环节。
    #
    # ⚠⚠ **这里曾经是三笔**，第三笔 `verbatim_recovered`（按最长公共子串救回来的锚点）
    # 随 `bounded-fidelity-v3` 一并删除：beat 的区间现在由**段号**算出、不存在「锚点被
    # 模型改掉一两个字」这回事，而 hinge 那一侧的 LCS 兜底本来就是禁的
    # （`novel/core/spans.py` 模块头：救回来的「玉镯」是一个**语义已经变了**的片段）。
    # ⇒ 那个键在新架构下**结构性恒空**，留着就是一盏永远不亮的灯；
    # 它唯一的消费方 `anchor_not_verbatim` 同批裁撤。

    #: `(beat_id, 位置, 原值, 回取值)`。**精确回取**：模型给的字节与原文有出入，
    #: 按它指的位置换成原文的那一份。
    #: ⚠ 新架构下只剩 `hinge.phrase` 一个来源——beat 的区间是段号算出来的，
    #: 模型根本没写过那段字节，也就无从「改写」。
    #: 出口：**逐条列出**（形态照 `address_surface_rewrites`）。
    verbatim_rewrites: list[tuple[str, str, str, str]]

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

    #: 定向重生成过哪几章（累积）。
    #: 出口：合并成一行「哪几章是定向重生成过的」。
    partial_regen_chapters: list[int]

    #: 定向重生成的历史。出口：上一行展开「各修了什么」。
    repairs: list[Repair]

    #: 链头 `P_INHERIT` 的拍（`extraction-ir-v1` 专属账）：模型说「延续上一拍地点」
    #: 而链上没有可继承的基（首拍、或上一相邻拍不同 `time_layer`）⇒ 产物 `place=null`。
    #: ⚠ **不许无声吞掉**：`place=null` 本身分不出「模型判无从知道」与「模型指错了继承」，
    #: 这笔账是两者唯一的分界。出口：INFO 判据 `place_inherit_without_base` 逐条列出。
    #: 默认空列表：`bounded-fidelity-v3` 的产地没有这个概念，旧字节缺键照常读入。
    place_inherit_without_base: list[str] = Field(default_factory=list)

    #: `(beat_id, name)`（`extraction-ir-v1` 系专属账）：模型按 `P_EXPLICIT` 写了地点，
    #: 而那个说法在**截止本章的正文**里一个字节都找不到。
    #: ⚠ **它是 `extraction-ir-v1.1` 的配套探针，不是违约判定。** ir1.1 把 `P_EXPLICIT`
    #: 从「三选一」提回「首选档」以救回地点填充率（ir1 相对 v3 掉 −59%，且
    #: `docs/stage2_contract_experiment.md` §十一 实测两个模型上都掉 ⇒ 是契约的责任）。
    #: 给模型加压时它会找最省力的出路，而 place 是单字段、没有别的字段可牺牲，
    #: **唯一的规避路径就是编一个地点**——这笔账就是那条路径上的探针：
    #: 填充率涨了而这笔账同步涨，涨的是幻觉不是覆盖。
    #: ⚠⚠ **匹配面是「截止本章正文」**：三个面在盘上（Pro×v3 无情道 132 个有地点的拍）
    #: 实测为 本拍区间 **78%** / 本章全文 42% / 截止本章正文 **13%** 找不到
    #: ——按本拍区间判等于让噪声淹死信号（地点常写在上文，那正是 `P_INHERIT` 的理由）。
    #: 取与 `present_names_invented` 同源的面；「截止本章」而非「全书」是因为
    #: 只在后文出现的说法本身就是后见之明。
    #: ⚠⚠ **判 INFO 且不该升 HARD**：那 13% 逐条看全是**用原文成分组合**出来的
    #: （「武场外」「黎玄的洞府」），一条凭空编的都没有 ⇒ 读法是**相对基线的变化**。
    #: 出口：INFO 判据 `place_explicit_unlocatable` 逐条列出。
    place_explicit_unlocatable: list[tuple[str, str]] = Field(default_factory=list)

    #: `(beat_id, kind, phrase)`（`extraction-ir-v1` 专属账）：短语在所属拍区间内
    #: **多处命中**、被打回失败态的那几条 hinge 的原始短语。
    #: ⚠ 失败态的 hinge 只剩 `{kind, source: null}`——**短语本身不在产物里**，
    #: 而候选选择修复（代码按短语在拍区间内枚举命中位、模型只挑 candidate_id）
    #: 的输入正是这个短语。不落这笔账，字段级修复就只剩「回 LLM cache 捞原始响应」
    #: 一条路，而缓存是可驱逐的。候选**位置**不落盘：由短语+区间确定性重算，零成本。
    hinge_ambiguous_pending: list[tuple[str, str, str]] = Field(default_factory=list)

    #: `(beat_id, 原止段, 新止段, 根因)`（`extraction-ir-v1` 专属账）：
    #: 层0 确定性边界外扩改过哪几拍的止段。
    #: ⚠⚠ **这是编译器改写了模型的分段主张**——盘上那份 `span` 不是模型给的那个数。
    #: 不落这笔账，「模型切得准」与「模型差 1 段、代码补上了」在产物里长得一模一样，
    #: 而 A/B 要回答的恰恰是前者。护栏与帽子见
    #: `novel/stages/stage2/compile_v2.py::BOUNDARY_NUDGE_MAX_PARAS`。
    #: 出口：断言计数 + 逐条列出（`novel/report/accounting.py`）。
    boundary_nudges: list[tuple[str, int, int, str]] = Field(default_factory=list)


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

    #: `inputs` 恒两条：`stage1/main_text.json` · `stage2/global_grounding.json`。
    provenance: BlueprintProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _chapter_ordinals_are_unique(self) -> ChapterBlueprints:
        """`chapter_ordinal` 是全链的章唯一键——重号会让下游按它做的每一次 join 取错章。

        ⚠ 这里**只查唯一性，不查连续性**：`beat_coverage` / 覆盖度那几条是判据
        （跨产物、要拿 `main_text` 比），归 stage2 的判据表，不归载体。
        """
        got = [b.chapter_ordinal for b in self.blueprints]
        if len(set(got)) != len(got):
            dup = sorted({o for o in got if got.count(o) > 1})
            raise ValueError(f'chapter_ordinal 重号：{dup}')
        return self
