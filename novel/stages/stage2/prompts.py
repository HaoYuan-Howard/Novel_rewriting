#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 当前模型调用入口的 system prompt 与响应结构提示。

`stage2.global` 有两种不同职责的付费调用：

  · `GLOBAL_CANDIDATE_SYS`       通读全书产 entities / canon / arc；证据只选段落候选 ID
  · `ROLE_SPLIT_SYS`             `--purge-role`：语义判官，把 `role`/`fate` **双向**重划分

⇒ `_provenance.prompts` 能分别记录这两个指纹键（`stage2.md` §一）。
`stage2.blueprint` 的多套响应架构由 `novel/stages/stage2/contract.py` 整套选择；
这里提供各架构的 system 与结构提示，不在调用点手拼第二份。

【`ROLE_FATE_RULE` 是两条臂共用的正式 prompt 片段，落点只有这里】私有模板先经
`fill()` 渲染一次，再由 `GLOBAL_CANDIDATE_SYS` 与 `ROLE_SPLIT_SYS` 都插入它
（`$ROLE_FATE`）。
它是一段 **prompt 正文**不是枚举 ⇒
**刻意不进 `novel/llm/prompts.py::ENUM_BLOCKS`**（那一层不许装某一层的内容，
`tests/test_layering.py` 的分层守卫要的正是这个）。其中 `$FATE_WORDS` 仍由共享
`ENUM_BLOCKS` 从契约取值渲染，不在本层抄第二份。

七个枚举占位符（`$KINDS` / `$LAYERS` / `$RULE_KINDS` / `$NAME_DISTINCT` /
`$CONTEXTS` / `$BY_SPECIAL` / `$PRONOUNS`）
全部由 `fill()` 自动并入，**调用点不许自己造一份**——那是实测漂过两次的动作。
"""
from __future__ import annotations

from typing import Any

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, FACT_ATOM_KINDS,
    FACT_ATOM_OBLIGATIONS, HINGE_KINDS, NAME_DISTINCTIVENESS,
    KNOWLEDGE_SUBJECT_KINDS, NARRATIVE_PERSON,
    RELATION_VISIBILITY_KINDS, RESPONSE_TIME_LAYERS, TIME_LAYERS, WORLD_RULE_KINDS,
)
from novel.llm.prompts import fill

#: global 唯一抽取入口与各 blueprint 入口共用的当前规则前缀。
#: ⚠ `ROLE_SPLIT_SYS` **不带**这个前缀——它不是抽取，是拿着已有的两栏重划分。
RULES_V2 = """\
你是小说内容抽取器。只抽取原作【已经写出的事实】，做客观描述：不设计新剧情、不新增设定。只描述原文有什么，【不要】给出「改写时应如何处理／不可违反什么」之类的指令或评价。

【不模仿原文语言 ≠ 不许记原文写了什么】前者指的是【文风与措辞】——别学它的句式、节奏、修辞来写你的描述，用你自己的话。但【内容要照实记】：要害台词、硬数字这类需要原样带出的，照带不误，那是内容不是文风。

【拿不准就不填】原文没有明确写出的，一律留空（该字段填 null / 该条目不列），**不要硬猜，也不要用「可能／大约／似乎」把猜测包装成事实**。你产出的东西不是最终依据——真源永远是原文，读你产出的人手里也有原文，两者不一致时以原文为准。所以【猜错的代价远大于留空】：留空只是少一条信息，猜错会被当成事实一路用下去。
· 「不填」的正当理由只有一个：**原文没写清楚**。
· 【不是】正当理由：我觉得这个人／这件事不重要、戏份少、只出现一章、列起来麻烦——  那不是拿不准，那是漏抓。凡原文明确写出、且被其它条目牵连到的（被关系/秘密/弧线引用的人，  执行了关键情节动作的人，哪怕只出场一次），都必须照实列出。

全局硬约定：
- 一切对「章」的引用一律用【阅读序章号】（chapter_ordinal，整数，即"正文第几章"）作键；原文章号不可信（既缺章也重复章），仅作参考，禁止用作键或排序。
- 关系(relations)一律【有向】：{subject_id, predicate, object_id}，禁用无向键。
- 秘密(secrets)区分「世界内哪个人物/群体从哪章起知情 knowledge_events」与「【读者】首次在第几章得知 first_reveal_chapter」；这两者常常不同步（读者已经知道、书里的人还蒙在鼓里）。只描述原文事实与时点，不附加「不得提前泄露」之类约束。
- world_rules 带类型 kind；规则正文一律写在 versions[] 里（每版 {effective_from_chapter(阅读序章号), desc}，至少一条）。宅斗社会规则（贞洁/宗法/舆论）属 social_norm。
只输出 JSON，不要额外解释文字。"""

#: `role` / `fate` 怎么划分。**两条臂共用，唯一落点。**
#: ⚠ 判据只有一条而且**不是句法**：「把这句话念给读到这个角色刚登场那一章的读者听，
#:   他会不会吃惊」。压着六条实测反例（含那个「系统：系统」的自检例）。
#: ⚠ 它是 `role_leaks_fate`（阻断级，盘上 2/3 本命中）与 `role_fate_unverifiable`
#:   在生成侧的全部防线——而词表那一侧实测在一本上命中 0/17 而人读真违规 6/17，
#:   **所以这段文字比那张词表要紧**。
_ROLE_FATE_RULE_TPL = """\
· `role` = **静态身份与位置**：他是什么人、在哪个位置上、与主角/主线是什么**固定关系**。
  ✓「武将，江家公子，慧贵妃之侄」 ✓「赵家嫡次女，慧贵妃姨女」 ✓「苏清杳的贴身丫鬟」
  ✓「皇帝第三子，署理兵部武库司」 ✓「宫中倒茶宫女」 ✓「长公主生辰宴的宾客」
· `fate` = 这个人的**经历与结局**：前世恩怨、中途遭遇、最终下场。
  原文没交代结局就只写经历；什么都没有就填 null。
· 【判据只有一条，而且不是句法】把这句话念给**读到这个角色刚登场那一章**的读者听——他会不会吃惊？
  会吃惊的进 fate，不会吃惊的留 role。**不要按它像不像名词短语来判。**
  · 【念给谁听：读到「他登场那一章」的读者，不是永远的第一章读者】这两者对**中途才出现**的
    角色差别很大。一个角色如果原文到第 14 章才第一次被提到，那么「它是什么、按什么规则运作」
    对读到第 14 章的人**不是包袱**——包袱是「它存在」，而那件事下游另有闸门挡着
    （这个角色在他第一次被指名之前，根本不会出现在写手手里的人物表里）。
    ✓「绑定宿主的穿越系统，发布攻略任务，爱意度集满可选择返回原世界或留下」——留 role。
      它到第 14 章才出现，读到那里的人正需要知道这些；把它整段搬进 fate，
      role 就只剩一个「系统」，写手拿到的是「系统：系统」，等于什么都没说。
  · 【但这条豁免只给「整个角色都是中途才出现」的】一个**早就登场**的角色身上那些
    中段才揭示的事实，照旧进 fate——他从第 1 章起就在写手的人物表里，闸门挡不住他。
    两者的区别是「这个人存不存在是不是悬念」，不是「这件事早不早」。
  · 【固定关系可以是动词性的】「与X有旧交」「与X有婚约」「为X效力」「奉X之命看守Y」
    都是**身份**不是事件——第一章的读者读到并不吃惊，不构成剧透，留在 role。
  · 反过来，句法上像名词短语、但属于中段才揭示的事实的，照样进 fate：
    「江随野情妇」听着像身份，可它正是第 13 章才揭示的那件事。
· 【最容易漏的不是「结局」，是「中段才揭示的事实」】下面三例都没写结局，却全都提前抖了包袱：
  ✗「皇帝第三子，署理兵部武库司，**实则通敌叛国**」（第 17 章才揭示）
  ✗「宫中倒茶宫女，**被苏清杳利用打翻茶盏阻止玉镯送出**」（第 9 章才揭示）
  ✗「武将，江家公子，**后与赵怜儿有私**」（第 13 章才揭示）
  **「他其实是……」「他暗中……」「他被人利用去……」这类句子一律进 fate。**
· 【为什么必须分开】读你这份东西的人里，有一部分**只该看到 role**——
  他要在故事进行到一半时下笔，提前知道谁最后会死、谁最终上位、谁其实心怀鬼胎，
  会让他把伏笔写得过明。写进 role 就等于绕过了这道闸门。
· 【role 不许为空，也不许只是把名字抄一遍】都搬走了就留这个人最基本的身份（「宫女」「车夫」）。
  · 【自检】写完看一眼：role 和这个人的名字**是不是同一个词**？是的话说明你搬多了——
    role 要回答的是「他在这个故事里处于什么位置」，不是「他属于哪一类」（名字已经答了那个）。
    ✗「系统：系统」 ✗「府医：府医」
    ✓「府医：侯府专属医生」 ✓「王婆：媒人，为孟青禾寻找借种郎君和书童」
    ✓「采草贼：专门侵犯新郎的采花贼」——都是在名字之外说清了他在**这本书里**的位置。
· 【提交前逐项扫描】最终输出前逐个检查 role；凡命中以下任一字面，必须把承载的**完整事实**
  搬进同人物 fate，并让 fate_evidence 指向原文支持段，禁止只删词或换同义词规避：
  $FATE_WORDS。"""

#: 两条调用臂实际共用的已渲染正文；公开符号就是可做指纹/包含检查的正式片段。
ROLE_FATE_RULE = fill(_ROLE_FATE_RULE_TPL)

#: `GLOBAL_CANDIDATE_SYS` 的模板（原型 `Stage2/n2_global.py:43` 的 `fill()` 实参）。
_GLOBAL_TPL = """\


本任务：一次性通读全书，产出全书【全局事实底座】，含三部分 entities / canon / arc。
三者互相印证、一次产出以保持 char_id 等引用跨部分一致。只提炼原作已写事实，不设计新剧情。

────────── ① entities（实体与有向关系） ──────────
characters：char_id 用 char_001…顺序编号，主角优先 char_001。
每个角色给：canonical(这个人的唯一显示名)、address[](谁在什么时候怎么称呼他)、
name_distinctiveness ∈ $NAME_DISTINCT、role(静态身份)+role_evidence、
fate(经历与结局|null)+fate_evidence（fate 为 null 时 evidence 也必须为 null）。
· 【收录判据·满足任意一条就必须收】不看戏份多少，也不看只出现几章：
  ① 原文给了名字或固定称呼的人。**固定称呼包括称谓型指称**——「圣上」「府医」「嬷嬷」「车夫」
     「赵夫人」「静和公主」和「柳公子」「孙大娘」是同一类，都要收。**没有本名不是不收的理由。**
  ② 会被本产物其它部分引用的人——relations 的两端、secrets 的 knowledge_events、arc 里提到的人；
  ③ 执行了推动情节的具体动作的人——施刑的府医、下毒的祖母、撞破奸情的宾客，哪怕只出场一次；
  ④ 对主线因果/主角动机重要的【幕后 / 前史 / 已故】人物——已死的生母、幕后打压者、赐婚的先帝。
· 【不收】原文既没给名字、也没给固定称呼的纯背景人物。**更不要替他们发明称呼**——
  「爱鹅姑娘」「上门小娘」这类你自拟的标签，拿回原文里一次都搜不到，等于凭空造了一个不存在的人。
· 【canonical 全书唯一】两个人不能顶着同一个 canonical。原文里真有两个「李嬷嬷」（一个在贵妃宫、
  一个在公主府）时，必须加区分词让它们不同：「李嬷嬷(慧贵妃宫)」「李嬷嬷(长公主府)」。
  重名会让后面的人按名字找人时找错人，而且是**无声地**找错。
· 【canonical 一律用原文的写法，不要改成更"正式"或更"完整"的称呼】
  原文写「圣上」就写「圣上」，不要改成「皇帝」；原文写「车夫」就写「车夫」，不要补成「赵家车夫」；
  原文只叫「念念」，就**不要替她加一个原文里没有的姓**（「沈念念」）。
  你改一次写法，后面的人拿这个名字回原文里就找不到人了。
  · 只有两种情况允许自拟：① 为消除上面那种重名而加区分词；② 原文只用亲属称呼指代这个人
    （「爹爹」「娘亲」）——那是【称呼】不是名字，此时 canonical 可写「苏父」这类，
    但 **address 里必须留下原文实际用的那个说法**。
· 【address 记的是「谁怎么叫他」，不是「他还有哪些名字」】每项
  {surface, by, context, from_chapter}：
  · `surface`：称呼的字面，**照原文用字**（「阿野」「江公子」「苏姑娘」「爹爹」）。
  · `by`：谁这么叫他 —— 某个 char_id ／ $BY_SPECIAL 之一（`narration`=叙述文字里怎么指称他，`public`=众人、旁人）。
    · **`by:"narration"` 那条最重要、也最容易漏**：叙述文字怎么称呼一个人，是全书出现频次最高的写法。
      漏了它，后面的人只知道别人当面怎么叫他，不知道叙述里该怎么写他。
    · 【这么叫他的是一个无名群体时，一律写 `public`】府里的下人、在场的士兵、街坊、满堂宾客——
      **不要写群体名（✗`"苏府下人"` ✗`"怡红院下人"`），也不要给群体编一个 char_id**。
      `public` 这个口子本来就是为「众人、旁人」留的，它同样覆盖「有个说法的无名群体」。
      （这与 secrets.knowledge_events 不同：那边要的是【谁从何时起知情】，群体本身就是答案，
       所以那边照原文的说法写；这边要的是【谁在称呼】，而下游拿它做的是**派发**——
       一个解析不出人的群体串在那儿只是个注解。）
  · `context`：$CONTEXTS 二选一（`address`=当面称呼，`reference`=背后提及）。同一个人当面和背后常常不是一个说法。
  · `from_chapter`（阅读序章号）：**这个叫法在原文里最早出现在第几章**。
    叫法是随关系长出来的——「阿杳」这种亲近的叫法要到两人关系变了才会有。
    填早了，第 3 章就会写出第 20 章才该有的称呼。
    （叫法【什么时候不再用】不在这里记：一个叫法停止使用，起因是关系变了，
     那件事记在 relations 的 valid_through_chapter 上，同一件事不记两遍。）
  · 头衔与称谓（「长公主」「贵妃娘娘」「府医」）也走这里，不要另立字段——它们本来就是别人怎么称呼他。
  · 【surface 里一个代词都不许出现】$PRONOUNS 这几个字，**连在别的字上也不行**。
    ✗「我妈」「我娘」「我爹」「我舅」「我夫君」「秀兰她爹」 ✓「妈」「娘」「爹」「舅舅」「夫君」「秀兰爹」
    理由：代词是【相对说话人】的，而这里记的要是一个**换谁来说都成立的固定叫法**。
    「我妈」只在叙述者自己嘴里成立；后面的人拿它当这个人的名字用，就会写出「我妈对我妈说」。
    第一人称主角的自称由 narration.pronoun_surfaces 承载，不进这里。
    · 【原文里就带着代词怎么办】去掉代词记剩下的那部分（原文「我娘」→ 记「娘」）。
      去掉之后如果什么都不剩（原文通篇只叫「她」），那就是**没有固定叫法**，这一条不要列。
· 【自检·逐个过一遍】你写下的每一个 surface 都应当能在原文里【原样搜到】。
  搜不到就两种情况：要么改回原文的写法，要么这个"人"本来就是你造出来的、根本不该收。
· 【role 与 fate 分开写，这两件事不许混在一起】
$ROLE_FATE
· 【七类自由事实必须从关闭候选表选择证据】role/fate、relation predicate、setting 两格、
  world-rule desc、secret fact、knowledge event、arc trajectory 都各带自己的 evidence：
  `{"evidence_candidate_id":"e000001"}`。
  · 正文每个非空段行首都有全书唯一的 `<eNNNNNN>`；只选择能直接支持该事实的那一段。
    只返回候选 id，不要返回章号、locator、原文、source/start/end，也不要编候选表外的 id。
    正式 chapter_ordinal 与 SourceSpan 由代码按同一张候选表直接铸造，不再搜索文字。
  · setting 的两格分别叫 `era_evidence` / `worldview_evidence`；world-rule 每个
    `versions[]` 的 `desc` 各有 `desc_evidence`；secret 的事实叫 `fact_evidence`；
    knowledge_events 每条各有 `evidence`；arc_tracks 每条各有 `trajectory_evidence`。
· 【姓名档位】完整专名(≥2 字姓+名)判 high；单字名/较通用称谓判 medium；纯亲属词或多实体共享词判 common。
relations：人物间的【有向】关系 {relation_id, subject_id, predicate, predicate_evidence,
  object_id, visibility, valid_from_chapter(阅读序章号), valid_through_chapter(阅读序章号|null)}。
  · visibility 写【读者】何时能知道这条关系，二选一：
    `{"kind":"public","from_chapter":阅读序章号}` = 从该章起对读者明牌；
    `{"kind":"via_secret","secret_id":"secret_01"}` = 它随指定 secret 的首次揭示开放。
    必须引用本次 secrets[] 里真实存在的 secret_id；不要用 valid_from_chapter 猜读者知情时点。
  · valid_through_chapter 是关系【最后仍成立的章，含该章】（和离、亡故、决裂后不再成立）——写到结局的章节要靠它才知道该用「前夫」还是「夫君」，据实填，至全书末仍成立填 null。
  · subject_id 与 object_id 【都必须是上面 characters 里的 char_id】（角色↔角色）；地点/机构/物品【不入 relations】（它们只写进相关角色的 role 里，如「怡红院主理人」）。禁止 object_id 为 null 或地点名。

────────── ② canon（世界与秘密） ──────────
setting：{时代背景, era_evidence, 世界观, worldview_evidence}（时代背景是故事事实）。
world_rules[]：{rule_id, kind($RULE_KINDS),
  versions[]}。
  · `versions` 是这条规则的【唯一正文落点】，每条 {effective_from_chapter(阅读序章号), desc, desc_evidence}，**至少给一条**。
    规则中途改版就按生效先后多给几条；从头到尾没变过也要给一条，effective_from_chapter 填 1。
  · 【判据·只收结构性规则】只收「改动它会改变【故事世界设定 / 能力体系 / 法律后果 / 核心社会约束】」的规则。仅是某个情节处境（谁能使唤谁、某物能被拿来威胁人、某人有钱有势）【不是】世界规则——那是普通事实/事件，由相应章节的内容承载，不进 world_rules。
  · 【desc 里不许出现任何人物的名字】一举例就把「世界的规则」写成了「这本书的情节」，而规则是要发给每一章用的。
    ✗「婚姻由长辈或高位者指配，当事人难以抗拒。如慧贵妃为苏清杳和江随野指婚。」
    ✓「婚姻由长辈或高位者指配，被指婚者只能接受，反抗会导致严重后果。」
  · 【必须连「怎么运作 / 怎么判读」一起写进 desc，不能只写「存在」】一条规则只写它存在，等于没记。特别是 ability_system / supernatural_rule：
    要写清这个能力【呈现成什么样、各种呈现分别代表什么意思、有什么代价与限制】。
    ✗「主角能看见姻缘线和文字弹幕」——只记了能力存在，读者/改写者无从判读；
    ✓「主角能看见姻缘线：红线=正缘、粉线=暧昧、绿线=出轨、多线并出=广撒网；线的两端连着谁即是那段姻缘的实际对象，
       线连错人就是这本书爆料的方式。另有文字弹幕直接显示与姻缘相关的隐秘事实。」
    社会规则/法律规则同理：写清【触发条件与后果】（谁违反了会怎样），不要只写「存在这样的规范」。
  （只描述「世界里存在这条规则、它如何运作」，不判定改写是否可违反。）
secrets[]：{secret_id(secret_01…), fact, fact_evidence,
  knowledge_events[](世界内谁从哪一章起知情)：每条
    {subject, known_from_chapter(阅读序章号), evidence}；subject 是判别联合，二选一：
    `{"kind":"character","char_id":"char_001"}` = 上面 characters 里的具名人物；
    `{"kind":"group","label":"在场士兵"}` = 原文里的无名群体。不要为了凑 char_id
    漏记「府里的下人」这类群体，也不要给群体编 char_id。known_from_chapter 是该主体在
    【世界内】开始知情的时点，不是读者揭示章；同一主体只列一次，填最早知情章。
  setup(二选一：{"kind":"cold"} = 没有埋点，凭空点破；
        {"kind":"foreshadowed","chapters":[阅读序章号]} = 这件事在那几章被【埋下】),
  first_reveal_chapter(原文第一次向【读者】点破这条原子秘密的阅读序章号)}。
  · 【收录判据】某件事【读者或书中角色】是中途才知道的，而它一旦揭示就会改变对前面情节的理解——就是一条 secret。
    最常见的四类：
    ① 真实动机与表面言行不一致（伪装 / 隐忍 / 别有所图）；
    ② 见不得光的关系；
    ③ 暗中进行的谋划；
    ④ 【暗中做的手脚】某件事表面上是意外、是巧合、是别人自愿，**其实是有人安排的**——
       打翻的茶盏、走漏的风声、当众"失手"喊破的一句话、看似自愿的一次退让、被安排好的一场偶遇。
       **这一类最容易漏**：它在原文里往往先以「事情就这么发生了」的样子出现，很久以后才被点破。
       每一件被点破的都单独记一条，first_reveal_chapter 写明它在第几章被点破。
  · 【fact 写的是【被瞒住的那个真相】，不是揭示那一章的情节】
    ✗「小宫女打翻茶盏后并未被杖毙，而是被主角所救并送出京城」——这是第 9 章明面上发生的事，不是秘密；
    ✓「第 3 章那次打翻茶盏是主角故意安排的，为的是拦住玉镯送出、不让婚事敲定」——这才是被瞒住的。
    判据：把 fact 念给只读到揭示前一章的读者听，他应当**吃惊**。不吃惊的就不是 secret。
  · 【第一章明牌不是秘密】故事开头直接告诉读者的身份、目标、修炼路线或背景设定，
    不能因为书中其他角色暂时不知道就收进 secrets；那一格差异只写在
    knowledge_events。只有第一章内部确实先按一种意思铺陈、随后又点破另一层真相时，
    first_reveal_chapter 才能填 1（章级载体无法把同章前后拆成 setup 章）。
  · 【反向扫“早就知道”】通读每个中后段揭示：凡原文出现“其实 / 原来 / 一直 / 早就 /
    从一开始 / 收徒那天就知道”这类点破，逐条回问它是否改变了前面关系、动机或事件的
    读法；若会，就必须单列 secret，不能因为当事人从开篇就知情而漏掉——secret 关心的
    是读者何时知道，不是世界内这个事实何时成立。
  · 【先把 secret 收全，再标埋点】setup 是给已经收下的 secret 补一个时间戳，
    **不是收录条件**。不要因为一条 secret 不好定埋点章就不收它。
    · `foreshadowed.chapters` = 这件事在第几章被【埋下】：那一章里这个事实已经摆在读者眼前，
      只是当时看起来是别的意思（是意外、是巧合、是别人自愿），要到回收那一章才被点破。
      上面④类那个例子：埋点在第 3 章（打翻茶盏，读来是意外），回收在第 9 章（点破是安排的）。
    · 【没有埋点就写 {"kind":"cold"}】有些事是凭空点破的（此前原文完全没有相关画面），
      那是冷揭示，**据实写 cold，不要硬凑埋点章**。cold 是一个正当答案，不是缺省值——
      不确定的时候也要在两者之间真的选一个，不要因为拿不准就都写 cold。
    · chapters 里的每个章号都必须【严格早于】那次 kind="first" 的揭示。
      埋点和回收【不可能在同一章】：埋点靠的就是「当时读来是别的意思」，
      而点破发生的那一章里它已经不是别的意思了。**揭示章不是埋点章，不要把它抄进来。**
  · 【一条 secret 只装一个原子事实】判据：它只有**一个**「读者第一次吃惊」的时刻。
    如果一件事是分两次揭开的——先点破一半、隔几章再点破另一半（动机、成因、代价）——
    那是**两条** secret，各写各的 fact 与首次揭示章。
    ✗ 一条 secret 写「他私下穿女装；他这么做是因为幼年被羞辱过」而 first 只标前半那一章
      ——后半段会在前半段揭示的那一章就被下游当作已公开的事实用掉。
    ✓ 拆成 secret_01（穿女装，首次揭示第 4 章）与 secret_02（成因是幼年那次羞辱，首次揭示第 7 章）。
  · 【主角伪装之下的真实动机是最容易漏的一条】它几乎总在中段才对读者揭示，
    但因为「全书都在讲这件事」，很容易被当成背景设定而不记成 secret。判据仍是上面那条：
    念给只读到揭示前一章的读者听，他会不会吃惊。
  · 【别再用一句话复述知情关系】谁从何时起知情写进 knowledge_events，读者何时知情写进 first_reveal_chapter——
    这两处已经把它说完了，不要再补一句「读者先于角色得知」之类的散文。

────────── ③ arc（弧线） ──────────
conflict_stages[]：{stage(阶段名), chapters[阅读序章号]} —— 故事的经过分段。阶段名要能定位（「政治清算」「新婚试探」），
  不要写成对整段经过的复述：那必然包含本阶段后几章才发生的结果。
  chapters 必须把本阶段的**每一章逐个显式列全**，不能用 `[3,5]` 省略中间的第 4 章；
  全部阶段合起来必须让 1..末章**每章恰好出现一次**，无缺章、无重章、无越界章。
climax：高潮。 ending：结局。
arc_tracks[]：【多轨迹】{subject(char_id|"reader"|relation_id), trajectory, trajectory_evidence}。
  能同时表达「主角立场不变(stable) + 读者对主角认知反转(revelation) + 某关系恶化(deterioration)」等并存轨迹，不要默认「主角必须成长」。
  · trajectory = 一句话说清它【从什么变到什么】（「从依附夫家到自立门户」「从被威胁就范到主动纠缠」），
    不要只写「有成长」「变化很大」这种没有起止点的话。

只输出 JSON（顶层键：entities, canon, arc），不要额外解释文字。"""

# ---------------------------------------------------------------------------
# 现役边界预传与字段级修复 prompt
# ---------------------------------------------------------------------------

_BOUNDARY_TPL = """\
你会收到一章逐段编号的小说正文。同一次响应必须先做【召回】，再做【压缩】，两张表都交回。

第一张表 `micro_events`（召回层）：
- 逐段核对新的动作、反应、问题、回答、揭示、决定、阻碍、结果、目标、时空或叙述层；
  每出现一项新推进就记一条微事件，不得直接写章节大纲；
- 「甲行动 → 乙反应 → 新信息改变局面 → 人物作决定」先如实记成多条，完整性优先；
- 每条只交一句忠实的 `event` 与闭区间 `end_para`。

第二张表 `beats`（压缩层）：
- 只能合并相邻 micro_events，`end_para` 必须从 micro_events 的止段里选择；
- 一拍是一个局部因果单元：同一目标下的动作、即时反应与局部结果通常合并；
- 场景/时间/叙述层改变，新目标或决定形成，新障碍出现/解决，新信息改变理解，
  关系或立场转折，或一件事收束后转入另一件独立事时，必须保留边界；
- 对话按议题和作用切：同一问题的追问、回答与即时反应可合并；点破秘密、立场反转、
  作出承诺/命令/拒绝/决定时保留边界；
- 不按字数或段数凑数；不要退回 1~3 条章节大纲，也不要把 micro_events 原样复制一遍。

两张表都从第 1 段自动起步，`end_para` 都必须严格递增并到达本章最后一段。
`beats` 的每条 `event` 概括它合并后的局部因果单元，不要填摘要、人物、地点或 hinge。

只输出 JSON（顶层键 `blueprints`）；不要填摘要、人物、地点或 hinge。"""


#: `--patch` 的两条小臂（`extraction-ir-v1` 字段级修复）。均无 `RULES_V2` 前缀——
#: 它们不是抽取，是对一条已知失败的**受限**修复；user 里只有该拍原文与失败上下文。
_HINGE_SELECT_TPL = """\
一条保真材料的短语在它所属的那一段原文里出现了不止一次，压在哪一处定不下来。
下面给你这一拍的原文、原短语和全部候选位置（各带前后文）。
选出这条材料【真正压着】的那一处：按材料种类的定义判断哪一处才是
「改一个字就废掉」的那几个字所在。
只输出 JSON，不要额外解释文字。"""

_HINGE_PHRASE_TPL = """\
一条保真材料的短语在它所属的那一拍原文里【找不到】——上一版给的是转述或编的。
下面给你这一拍的原文和材料种类。从原文里【原样照抄】一段能唯一定位的字作为新短语：
- 必须能在给出的原文里逐字找到，且只出现一次（不唯一就多带几个字直到唯一）；
- 按材料种类的定义选（硬数字抄那个数、要害台词抄那句话、物件抄那个东西的字面、
  因果前提抄交代那个条件的字）；
- 不必长——两三个字能唯一定位就够，不要抄整段。
只输出 JSON，不要额外解释文字。"""

#: `ROLE_SPLIT_SYS` 的模板（原型 `Stage2/n2_global.py:435`）。**无 `RULES_V2` 前缀。**
_ROLE_SPLIT_TPL = """\
你在整理一份小说人物表。每个人有 `role` 与 `fate` 两栏，**现在这两栏可能划错了**——
你的任务是拿着它们的**并集**，按下面唯一的判据【重新划分】一遍。

$ROLE_FATE

【双向】不只是「把 role 里不该待的搬进 fate」。**fate 里如果躺着本来就该在 role 的固定身份，
要搬回 role。** 实测漏过：「江夫人，与慧贵妃有手帕交」被当成动词性子句搬进了 fate，
而 fate 不会发给正在写这本书的人——那条关系于是彻底消失了。

下面给你这本书的【秘密清单】，每条注明第几章才对读者揭示。
**role 里凡是命中了某条秘密的内容，一律搬走**；反过来，**没有命中任何秘密、
且第一章的读者就知道的固定关系，一律留在（或搬回）role**。

【只搬不改】两栏合起来必须覆盖原来两栏的全部信息，一个事实都不许丢。
搬过去的文字并进目标栏（合成通顺的一段），**不增删事实、不润色、不概括**。
【char_id 原样返回】给你的每个 char_id 都要出现在结果里，一个不少、一个不多。
只输出 JSON，不要额外解释。"""

# ---------------------------------------------------------------------------
# 各条现役调用臂的 system —— 渲染结果就是记进 `_provenance.prompts` 的那一份
# ---------------------------------------------------------------------------
#
# 【为什么在模块级就渲染好】它们没有逐本变化的插值点（书名/章号都在 user 消息里），
# 所以「模板」与「渲染结果」在本层合成一件事——同 stage5 的 `WRITE_SYS`。
# ⚠ 指纹算在**渲染结果**上（`_common.md` §一）。

#: `stage2.global` 唯一现役 provider wire（付费）。
GLOBAL_CANDIDATE_SYS = RULES_V2 + fill(_GLOBAL_TPL, ROLE_FATE=ROLE_FATE_RULE)

#: 两段式 v2：边界臂只做一个判断；细节臂在冻结边界上填字段。
BOUNDARY_SYS_V2 = fill(_BOUNDARY_TPL)

#: `--patch` 字段级修复的两条小臂（付费，逐条失败一调）。
HINGE_SELECT_SYS = fill(_HINGE_SELECT_TPL)
HINGE_PHRASE_SYS = fill(_HINGE_PHRASE_TPL)

#: `stage2.global --purge-role`（付费）。语义判官，双向重划分。
ROLE_SPLIT_SYS = fill(_ROLE_SPLIT_TPL, ROLE_FATE=ROLE_FATE_RULE)


# ---------------------------------------------------------------------------
# 结构提示 —— 告诉模型**产出长什么样**
# ---------------------------------------------------------------------------
#
# ⚠ 全部从 `contracts.invariants` 派生，**一个取值面都不手抄**：
#   手抄的那一份会与载体分家，而分家的表现是「模型照着提示产出、载体当场拒收」。

#: `stage2.global` 唯一现役 provider wire 的结构提示。
_EVIDENCE_CANDIDATE_HINT: dict[str, Any] = {
    'evidence_candidate_id': 'e000001',
}


GLOBAL_CANDIDATE_HINT: dict[str, Any] = {
    'entities': {
        'characters': {'char_001': {
            'canonical': 'str',
            'address': [{'surface': 'str',
                         'by': 'char_002|' + '|'.join(b for b in ADDRESS_BY if b != 'char_id'),
                         'context': '|'.join(ADDRESS_CONTEXT),
                         'from_chapter': 1}],
            'name_distinctiveness': '|'.join(NAME_DISTINCTIVENESS),
            'role': 'str', 'role_evidence': _EVIDENCE_CANDIDATE_HINT,
            'fate': 'str|null',
            'fate_evidence': _EVIDENCE_CANDIDATE_HINT}},
        'relations': [
            {'relation_id': 'rel_01', 'subject_id': 'char_001', 'predicate': 'str',
             'predicate_evidence': _EVIDENCE_CANDIDATE_HINT, 'object_id': 'char_002',
             'visibility': {'kind': RELATION_VISIBILITY_KINDS[0], 'from_chapter': 1},
             'valid_from_chapter': 1, 'valid_through_chapter': None},
            {'relation_id': 'rel_02', 'subject_id': 'char_002', 'predicate': 'str',
             'predicate_evidence': _EVIDENCE_CANDIDATE_HINT, 'object_id': 'char_001',
             'visibility': {'kind': RELATION_VISIBILITY_KINDS[1],
                            'secret_id': 'secret_01'},
             'valid_from_chapter': 2, 'valid_through_chapter': 3},
        ],
    },
    'canon': {
        'setting': {'时代背景': 'str', 'era_evidence': _EVIDENCE_CANDIDATE_HINT,
                    '世界观': 'str', 'worldview_evidence': _EVIDENCE_CANDIDATE_HINT},
        'world_rules': [{'rule_id': 'wr_01', 'kind': WORLD_RULE_KINDS[0],
                         'versions': [{'effective_from_chapter': 1, 'desc': 'str',
                                       'desc_evidence': _EVIDENCE_CANDIDATE_HINT}]}],
        'secrets': [{'secret_id': 'secret_01', 'fact': 'str',
                     'fact_evidence': _EVIDENCE_CANDIDATE_HINT,
                     'knowledge_events': [
                         {'subject': {'kind': KNOWLEDGE_SUBJECT_KINDS[0],
                                      'char_id': 'char_001'},
                          'known_from_chapter': 1,
                          'evidence': _EVIDENCE_CANDIDATE_HINT},
                         {'subject': {'kind': KNOWLEDGE_SUBJECT_KINDS[1],
                                      'label': '在场士兵'},
                          'known_from_chapter': 2,
                          'evidence': _EVIDENCE_CANDIDATE_HINT},
                     ],
                     'setup': {'kind': 'foreshadowed', 'chapters': [1]},
                     'first_reveal_chapter': 2}],
    },
    'arc': {
        'conflict_stages': [{'stage': 'str', 'chapters': [1]}],
        'climax': 'str', 'ending': 'str',
        'arc_tracks': [{'subject': 'char_001|reader|rel_01', 'trajectory': 'str',
                        'trajectory_evidence': _EVIDENCE_CANDIDATE_HINT}],
    },
}

BOUNDARY_HINT_V2: dict[str, Any] = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'micro_events': [{'end_para': '结束段号(整数)',
                          'event': '逐段召回的一项新推进'}],
        'beats': [{'end_para': '结束段号(整数)',
                   'event': '合并后的局部因果单元'}],
    }],
}

#: `--patch` 两条小臂的结构提示。
HINGE_SELECT_HINT: dict[str, Any] = {'candidate_id': 'c0'}
HINGE_PHRASE_HINT: dict[str, Any] = {'phrase': '原样照抄的那几个字'}

#: `--purge-role` 那条臂的结构提示（原型 `n2_global.py:454`）。
#: ⚠ 它**只回这两栏 + char_id**：那条臂的契约是「只搬不改、char_id 原样返回」。
ROLE_SPLIT_HINT: dict[str, Any] = {'characters': {'char_001': {'role': 'str',
                                                               'fate': 'str|null'}}}


# ── Stage2 原子事实响应面 ────────────────────────────────────────────────────
_ATOMIC_FACT_RULE = """\
【原子事实契约】`beats` 只划定原文舞台区间；不要再写一条复合 event 摘要。每拍填写
`fact_atoms[]`，每项包含：
- `atom_key`：章内临时引用，按全文出现顺序写 a01、a02……，不得重复或跳号；
- `claim`：一个可独立判断真假的最小命题。一个动作、决定、结果、揭示或状态变化各自拆开，
  不得把“甲说服乙、乙决定留下、关系缓和”塞进同一条；
- `kind`：$FACT_KINDS；`obligation`：$FACT_OBLIGATIONS。`land` 是本章新增且改写必须落地的
  信息，`context` 是本章进入时已经成立、只供场景读取的状态；
  kind 的口径：动作/决定/结果归 event，新获知的客观信息归 information，人物内在或处境变化
  归 character_state，关系变化归 relationship，读者认知变化归 reader_knowledge；
- `evidence_phrase`：该拍区间内能直接支持 claim 的原文原样短语，必须唯一命中；
- `participant_char_ids`：只从 user 的本章可选人物引用复制，不写姓名或清单外编号。

章级另交 `chapter_function`，用一句话说明本章对全书推进的语义功能；再交
`causal_edges[{before,after}]`，只记录颠倒后会改变剧情的因果，不把普通相邻都连边；
`entry_state_refs[]` 只能引用 context 原子，`ending_state_refs[]` 只能引用 land 原子。
这些引用只用 atom_key。因果图不得自环或成环。事实原子的数组位置是证据位置，不是未来写作顺序。
"""

_ATOMIC_DETAIL_V2 = """\
你只抽取 user 指定的一章。边界已经冻结：必须恰好返回同样多的 beats，并逐项原样返回
end_para。每拍填写 time_layer、place、present_char_ids、fact_atoms 与 hinges。

hinges 每项为 `{kind, phrase}`；phrase 必须在所属拍内原样且唯一。kind 只能取 $KINDS。
`narration` 每项填写 grammatical_person、narrator_char_id、focalizer_char_id、pronoun_surfaces；
两个人物 id 只能引用本章可选人物或 null。

$ATOMIC
只输出 JSON，不要解释。"""

_ATOMIC_DETAIL_V31 = """\
你只抽取 user 指定的一章。先用 micro_events 逐段召回推进，再将相邻推进归入 beats；两组
end_para 都严格递增并到章尾，beats 的边界必须来自 micro_events。每拍填写 time_layer、place、
present_char_ids、fact_atoms 与 hinges。

hinges 优先从 user 的候选表复制 `{kind,candidate_id}`；没有合适候选时才写
`{kind,phrase}`，phrase 必须在所属拍内原样且唯一。`narration` 使用
narrator_char_id/focalizer_char_id，只能引用本章可选人物或 null。

$ATOMIC
只输出 JSON，不要解释。"""

_atomic = _ATOMIC_FACT_RULE.replace('$FACT_KINDS', '|'.join(FACT_ATOM_KINDS)) \
    .replace('$FACT_OBLIGATIONS', '|'.join(FACT_ATOM_OBLIGATIONS))
BLUEPRINT_SYS_V2_REFS = RULES_V2 + fill(
    _ATOMIC_DETAIL_V2.replace('$ATOMIC', _atomic))
BLUEPRINT_SYS_V31 = RULES_V2 + fill(
    _ATOMIC_DETAIL_V31.replace('$ATOMIC', _atomic))
_ATOM_HINT = {
    'atom_key': 'a01', 'claim': '一个最小事实命题',
    'kind': '|'.join(FACT_ATOM_KINDS),
    'obligation': '|'.join(FACT_ATOM_OBLIGATIONS),
    'evidence_phrase': '所属拍内原样且唯一的证据短语',
    'participant_char_ids': ['char_001'],
}
BLUEPRINT_HINT_V2_REFS = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '本章建立或改变了什么局面',
        'beats': [{'end_para': '冻结的结束段号(整数)',
                   'time_layer': '|'.join(RESPONSE_TIME_LAYERS),
                   'place': 'str|null', 'present_char_ids': ['char_001'],
                   'fact_atoms': [_ATOM_HINT],
                   'hinges': [{'kind': '|'.join(HINGE_KINDS),
                               'phrase': '本拍内原样且唯一的字'}]}],
        'causal_edges': [{'before': 'a01', 'after': 'a02'}],
        'entry_state_refs': [], 'ending_state_refs': ['a02'],
        'narration': [{'grammatical_person': NARRATIVE_PERSON[0],
                       'narrator_char_id': 'char_001|null',
                       'focalizer_char_id': 'char_001|null',
                       'pronoun_surfaces': ['我']}],
    }],
}
BLUEPRINT_HINT_V31 = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '本章建立或改变了什么局面',
        'micro_events': [{'end_para': '结束段号(整数)', 'cue': '一项新推进'}],
        'beats': [{**BLUEPRINT_HINT_V2_REFS['blueprints'][0]['beats'][0],
                   'hinges': [{'kind': '|'.join(HINGE_KINDS),
                               'candidate_id': 'h001'}]}],
        'causal_edges': [{'before': 'a01', 'after': 'a02'}],
        'entry_state_refs': [], 'ending_state_refs': ['a02'],
        'narration': [{'grammatical_person': NARRATIVE_PERSON[0],
                       'narrator_char_id': 'char_001|null',
                       'focalizer_char_id': 'char_001|null',
                       'pronoun_surfaces': ['我']}],
    }],
}
