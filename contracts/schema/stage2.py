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
    ADDRESS_BY, ADDRESS_CONTEXT, CACHE_POLICY, HINGE_KINDS, NAME_DISTINCTIVENESS,
    NARRATIVE_PERSON, TIME_LAYERS, WORLD_RULE_KINDS,
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
    #: ⚠ 那条闸门的前件是 `setup_chapters`，而盘上 20/21 是空数组 ⇒ **等于没开**。
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
    #: ⚠ **「冷揭示」与「被裁空」在字段值上完全同形**（盘上 20/21 条是空数组）：
    #: 前者合法；后者说明产地在把揭示章号抄进埋点。两者只能靠那个计数分开。
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

    #: 长度 1~5。**盘上 3/3 本恰好覆盖全部章、无重无缺**——
    #: ⚠ 但**没有任何判据在查这件事**。
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
    """`global_grounding._provenance` —— 五个记账键 + 一个人工裁决记录。

    ⚠ **记账不是终点，报出来才是**（`Q-1`）：这五个键各有**指定出口**
    （断言计数 / 逐条列出），细则见 `stage2.md` §3.3。三条形式要求：
    **空也要报**（报成 `0 处`）· 一行常驻非空才展开 · 出口在校验报告不在 stdout。

    ⚠ **已裁撤**：`changed`。它是一个**进程内**的开关（「什么都没改时不落盘」），
    确定性迁移那条路径 `pop` 掉它、正常产出那条路径忘了 pop，于是它落了盘——
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

    #: 人工裁掉某条 secret / 规则时记裁决理由。**盘上零样本。**
    #: 出口：**逐条列出裁决理由**——人删掉过东西这件事必须留在报告里。
    #:
    #: ⚠ **契约从没定过它的子字段形状**（`stage2.md` §3.1 只写了「记裁决理由」）。
    #: 这里用 `dict[str, str]` 是**登记这个空缺**，不是定义它——
    #: stage2 移植之前要把形状补齐，否则 `extra='forbid'` 在这一格上是失效的。
    human_adjudicated: list[dict[str, str]] | None = None


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


# ─────────────────────────────────────────────────────────────────────────────
# 二、`chapter_blueprints.json`
# ─────────────────────────────────────────────────────────────────────────────

class BeatSpan(StrictModel):
    """一拍在原文里的两个锚点。

    **契约要的是「原样照抄 8~30 字」，而那是【生成期指导】不是产物不变量**——
    落盘前的回取会把两头改长度。盘上 **66/502 个锚点内容字符 < 6**（最短 1），
    校验器**明说不判违约**，所以这里也不加长度下界。

    ⚠ **产地按 8~30 要求、校验侧承认做不到，中间那段差额会被模型用编造填上**：
    实测一条 `ends_with` 写成 `我"嗯"。`（原文只有 `"嗯。"`），补出来的「我」原文里不存在，
    整条 beat 因锚点定位不到而作废，而且**扛过了一次带证据的定向重生成**。
    ⇒ prompt 里必须有那条逃生口：「锚点本来就短时，照抄它有多少字，不要补字凑长度」。
    """

    #: 长度 2~43。
    starts_with: str = Field(min_length=1)

    #: 长度 1~25。
    ends_with: str = Field(min_length=1)


class Hinge(StrictModel):
    """一条保真点。**闭卷写作唯一的保真通道**，所以它的字节必须是原文的字节。

    盘上 282 条：`要害台词` 167 · `硬数字` 61 · `物件` 30 · `因果前提` 24。

    ⚠ **两个锚点允许重叠、甚至完全重合**：128 条实测里 **75 条（59%）** 的内容字符
    比两个锚点加起来还短——「硬数字」「物件」天生就是短的（`丝绢`、`二十两银子`）。
    按「`ends_with` 必须在 `starts_with` 之后开始」实现的话，这类一律锚不到（实测 97/128 失败）。
    ⚠ **不加单调游标**：`span` 明写「按原文顺序前进」，`hinges` **没有顺序约定**
    ——它是「这一幕靠什么成立」的清单，模型按重要性列很正常。强加游标会误伤 7/128。
    """

    #: **[HARD] 闭合 4 值**（`R-3.6`）。
    #: ⚠ 每类各守各的维度（`HINGE_GROUPS`），**那张表不许用 `dict.get(kind, 默认值)` 取**：
    #: 把写歪的类型兜底成「原样写入，一个字都不能改」，会让一段叙述文变成逐字要求，
    #: 而全链没有一层出声。
    kind: Literal[HINGE_KINDS]

    #: 长度 2~24 / 1~24。**模型写的决策**：这段材料在原文的哪一段。
    starts_with: str = Field(min_length=1)
    ends_with: str = Field(min_length=1)

    #: 长度 2~81。**代码按锚点从原文切出来的，模型不写它。**
    #:
    #: ⚠⚠ **缺失 = 失败态标记，不是一个合法的可选字段。**
    #: 锚不到时的设计是**原样留着、不猜不删**（删了这一次会响亮报错，下一次跑就没得报了），
    #: 于是产物里留下一条没有 `text` 的 hinge。**盘上实测 281/282**：
    #: 三岁 `b07_05` 的「要害台词」锚不到——它是那份产物**唯一**能看出
    #: 「这次跑是失败态」的痕迹（`_provenance` 里没有记 failures）。
    #:
    #: **两件事都要写在这儿**：只写「可选」会被读成「有时候没有很正常」；
    #: 只写「必填」则盘上那份产物不合法。
    #: ⚠ 锚不到有**三种根因、修法各不相同**——锚点不在本章原文（重生成）/ 本 beat 的
    #: `span` 自己就没定位成功（级联，修上游那条就好）/ 锚点在本章但落在**没被任何 beat
    #: 覆盖的空档**（漏抓的下游后果，重生成）。盘上那一条属于第三种。
    text: str | None = None


class Beat(StrictModel):
    """一拍 = **一件独立发生的事**。盘上 251 条。

    ⚠ 比它大的那一层（连续区间 + 恒定 place + 恒定在场集）叫**场景**，
    是**推导出来的、不存字段**。
    """

    #: **`b<章:02d>_<序:02d>`**，章内连续。**代码无条件归一，模型自拟一律覆盖**（`R-3.1`）。
    #: **[HARD] 全书唯一 + 格式 + 连续。**
    beat_id: str = Field(pattern=r'^b\d{2}_\d{2}$')

    #: **[HARD] 按序定位**（`R-3.2`）：第 n 条的 `starts_with` 必须出现在第 n−1 条的
    #: `ends_with` 之后。**两条之间允许有空隙**（过渡句、环境交代），**但两头不许留**——
    #: **[HARD] 章首章尾必须被第一/最后一拍覆盖**（`R-3.8`）。
    #: 盘上三本合计覆盖率 97~98%，未覆盖区间全在章的中段。
    span: BeatSpan

    #: **[HARD] 取值** · **[HARD] 全书 `present` 不得为 0**（枚举合法 ≠ 分布合理）。
    #: 盘上 `present` 239 (95%) · `recall` 7 · `hearsay` 5。
    #: ⚠ **一拍内恒定**（分组函数的 HARD 后置条件）——跨层并拍会把只在回忆里出场的人
    #: 放进现世街头。
    time_layer: Literal[TIME_LAYERS]

    #: 长度 1~11，**盘上 40/251 为 `null`**（键恒在）。[INFO] 只做转场计数。
    #:
    #: ⚠ **自由文本、粒度不稳**（街上/街边、公寓/公寓卫生间门口）：
    #: 作为**下发内容**合格，作为**相等判断的键**不合格——
    #: 凡按它相等做的推导（转场计数、场景派生、在场连续性）都在量措辞。
    place: str | None

    #: **模型写的决策**：这一拍在场的人，各用原文里称呼他的那个字面。
    #: **[HARD] 有据**——全书原文都找不到 = 自造（`R-3.7`，盘上 0 例）。
    #: 长度 0~7，共 515 项；21/251 拍为空。
    #:
    #: ⚠ **取值面是「原文里的叫法 ∪ 代词」，而代词永远不会在 registry 里**——
    #: 这两句话必须一起写，少一句就会有人去「修」其中一边。
    #: 第一人称书里模型会照原文写「我」，而 `address[].surface` **按设计永远不会收代词**
    #: ⇒ 「我」解析不出来**不是 address 漏收，也不是自造**，是两条契约在这里对不上。
    #: 盘上：书1 有 15 项代词（5 项定人成功、10 项定不了）· 另两本 0 项。
    present_names: list[str]

    #: **代码从 `present_names` 解析出来的，模型不写它。**
    #: **[HARD] 都在 registry**——这是解析器的**后置条件**：
    #: 违反它 = **代码坏了，不是数据坏了**。
    #: 长度 0~7，共 451 项；29/251 拍为空。
    present_char_ids: list[str]

    #: 长度 13~175。**按引用逐字搬进下游，中间层不转抄**（`Q-7`）。
    #: **[HARD] 存在且非空**（`R-3.13`）——⚠ 这条判据判在**本层**而不是 stage4：
    #: 留在 stage4 意味着要先付掉一次规划调用才发现 stage2 产坏了。
    event: str = Field(min_length=1)

    #: 长度 0~7，共 282 条；**63/251 拍为空（25%）**。
    #: **[HARD] 整章一条都没有 = FAIL**（单拍为空正常）。
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
    #: ⚠ 「限知 vs 全知」这一轴**刻意不在本层立字段**：它已经有落点
    #: （stage3 的 `narrative_distance` / `interiority`，写得比二值枚举好得多）。
    #: 已知代价（stage3 是全书一份，表达不了章间差异）归 `B-09`，**不假装它被覆盖了**。
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


class BlueprintContext(StrictModel):
    """这一趟是怎么分批、吃到哪儿的。新产物恒 `{1, true}`。

    **必须常驻打印**——不打印它，读的人会把「这次没报跨章引用」当成模型学乖了，
    而其实只是没抽到。
    """

    batch_size: int = Field(ge=1)
    context_upto: bool


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
    #: ⚠ **产地无条件写这个键**（没越界写空列表），否则「键不在 = 没越界」
    #: 会给所有旧产物发绿灯。
    scope_violations: list[str]

    #: 新产物恒 `[[1],[2],…]`；**旧产物记的是别的值**。
    #: 校验器靠它区分「逐章」「分了 N 批」「不知道」三种产物。
    batches: list[list[int]]

    context: BlueprintContext
    char_refs: CharRefs

    # ── ⭐ verbatim 回取的三笔账（F5-1 立，`stage2.md` §3.2）───────────────────
    #
    # 【它们把事实② 那张三档表里**唯一的「未解决」**那一格填上】
    # 原文：verbatim 回取「改写不留任何产物痕迹，而它对应的判据在改写之后**恒真**」，
    # 且 §2.4 记着「这份产物是不是失败态**目前只能靠遍历全部 hinge 去反推**」。
    # 修法照 F4 给 `address_*` 立账的先例：**生产侧把「改了多少、改了什么」交出来，
    # 校验器断言那份账而不再断言字段值。**
    #
    # 【为什么是三个键不是一个】判据 14：一个量纲一个口径。三者的**修法各不相同**，
    # 合并之后报告就说不出该去修哪一个。
    #
    # ⚠ 三个键**盘上 3/3 本一份都没有**（原型根本没有它们）⇒ 已登记进
    # `tests/corpus.py::ADDED['stage2/chapter_blueprints.json']`，否则那三本加载不了。
    # 证据列因此是 `契·码` 而不是 `三源`、也不是裸 `契`：「码」指的是本仓 F5-5 的产地。

    #: `(beat_id, 位置, 原值, 回取值)`。**精确回取**：锚点归一后命中，字节被换成原文的。
    #: 出口：**逐条列出**（形态照 `address_surface_rewrites`）。
    verbatim_rewrites: list[tuple[str, str, str, str]]

    #: `(beat_id, 位置, 原锚点, 救回的片段, 重合度)`。**按最长公共子串救回来的**
    #: （`novel/core/spans.py::_find_or_recover`，双门槛：绝对 ≥4 字 + 相对 ≥`HINGE_LCS_MIN`）。
    #:
    #: ⚠⚠ **它必须与上一个键分开**，与 F4-2 把 `split_by_speech_tag` 的放行单独留痕
    #: 是同一条：「一个字都没动」与「救回来的」**不是同一件事**——
    #: 后者**锚点本来就不是原文字节**。合成一个键，报告上那两种就同形了。
    #: 出口：**断言计数 + 逐条列出**——判据 `anchor_not_verbatim` 断言的就是它。
    verbatim_recovered: list[tuple[str, str, str, str, float]]

    #: `(beat_id, 位置, 三种根因之一)`。**锚不到。[HARD] 非空即失败态。**
    #:
    #: ⚠ 它替掉今天那条「靠 `hinges[].text` 的**缺席**去反推失败态」——
    #: 失败态从此有**正面**的落点。三种根因（锚点不在本章 / 本 beat 的 span 自己
    #: 没定位成功 / 落在漏抓区）**修法各不相同**，所以要分开报。
    #: ⚠ 非空时产地照旧入显式终态（原型 `report_verbatim_failures` 就是这么做的），
    #: 但**账要先落盘**——原型那条 `sys.exit` 之后什么都没留下，而「显式终态」
    #: 在多命令流水线里只等于「这一步红了」，**不等于「链路被挡住了」**（§2.4 已记）。
    #: 出口：**断言计数 + 逐条列出**，按三种根因分开。
    verbatim_failures: list[tuple[str, str, str]]

    #: 定向重生成过哪几章（累积）。盘上 2/3 本有。
    #: 出口：合并成一行「哪几章是定向重生成过的」。
    partial_regen_chapters: list[int] | None = None

    #: 定向重生成的历史。出口：上一行展开「各修了什么」。
    repairs: list[Repair] | None = None


class ChapterBlueprints(StrictModel):
    """`stage2/chapter_blueprints.json` —— 顶层 2 个键。
    **一次调用产一章，只吃到该章为止的正文，没有旋钮。**

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
