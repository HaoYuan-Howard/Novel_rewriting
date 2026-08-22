#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2.py —— 抽取那一层的判据行。源是 `docs/schema/stage2.md` §5.1（**唯一落点**）。

**74 条现役：40 HARD · 16 INFO · 16 度量 · 2 查不了。**
权威恒在本模块的注册表与 `BACKEND_CONTRACT.md` §反向覆盖率；2026-08-23 新增的
`conflict_stage_coverage` HARD 把冲突阶段的逐章覆盖关在 Stage2 产地。

【一张表，两个节点共用】`stage2.global`（全书级抽取）与 `stage2.blueprint`（逐章抽取）
**读同一张表**，谁也不许再抄一份。⚠ `--check-only` 只挂在 `stage2.blueprint` 上
（覆盖测试要求每层恰好一个），
而那一相**两份产物一起读**（判 `canonical_unique` 要 global，判 `present_ids_dangling`
要 registry）⇒「global 的判据没有 0 成本复检」这句话不成立。

【判据 id 一律照抄文档，不加 stage 前缀】它们是与 `wontfix.json` 的键、
与 `_provenance.fixes` 的引用之间的契约。

【本层特有的四条，与前七层都不同】

**① 这是第一个用 `Rule.fix` 的层。** 36 条带机器可读的修复路由：
`blueprint --chapters` 17 · `blueprint --patch` 1 · `blueprint --renormalize` 2 ·
`global --resample` 14 ·
`global --purge-role` / `--renormalize` 各 1。注册期第 ⑧ 条会查节点与 flag 都真实存在——**写歪一个当场炸**，
而写进 `note` 的散文里拼错一个 flag 名不会有任何东西出声（LESSONS 第 11 条）。
⚠ **`fix` 与 `directive` 是两格，在本层完全不重叠**：全表 `repair='NONE'`
（反哺是 stage5 独有的，`contracts/rules.py::_validate` ④ 把它锁在 `owner='stage5'` 上）
⇒ `directive` 全表为空，而 `fix` 有 36 条。

⚠ **徽章结构上永远不可能是 `pass`**：本表有 **2 条 `unver`**，其中
`role_fate_unverifiable` **无条件产出**（见下）⇒ 每本每次都产 ⇒ 最好情况是 `amber`
（`passed=True`、退出码 0）。与 stage5 同形、与 stage4 相反。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage2'

#: 本层两个节点的修复路由。**写成常量而不是每处一个元组字面**：
#: 17 条判据指着同一条 `blueprint --chapters`，散着写就是 17 份会各自漂的拷贝。
FIX_REGEN = ('stage2.blueprint', '--chapters')      # 定向重生成那几章（**付费**）
FIX_REGEN_ARGS = ('--chapters',)                    # 值由本次 finding 的机器证据给
FIX_RENORMALIZE_BP = ('stage2.blueprint', '--renormalize')  # 确定性归一，0 成本
FIX_RENORMALIZE_G = ('stage2.global', '--renormalize')      # 同上，全书那一份
FIX_PURGE_ROLE = ('stage2.global', '--purge-role')  # 语义判官臂重划 role/fate（**付费**）
FIX_RESAMPLE = ('stage2.global', '--resample')  # 绕过问题缓存重采整份 global（**付费**）
FIX_PATCH = ('stage2.blueprint', '--patch')     # 字段级修复（**付费**，ir1 专属，量级=条）


def _hard(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    if kw.get('fix') == FIX_REGEN:
        kw.setdefault('fix_args_from_finding', FIX_REGEN_ARGS)
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=owner, **kw))


def _info(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    if kw.get('fix') == FIX_REGEN:
        kw.setdefault('fix_args_from_finding', FIX_REGEN_ARGS)
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=owner, **kw))


def _metric(rule: str, item: str, requirement: str, **kw) -> Rule:
    """度量：**无条件报出**，`owner=None`（它不是一条结论，没有「根因第一嫌疑层」）。

    本层有一批与告警配对的无条件度量
    （`present_names_*` 五分 · `time_layer_mix` · `narration_voices` · `batch_density` ·
    `span_coverage_per_chapter`）。它们与告警的分界线是
    **有没有前件**：无条件产出的是度量，挂在 `if 违规` 上的是告警（判据 21）。
    """
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


def _unver(rule: str, item: str, requirement: str, *, who_checks: str = 'INFO', **kw) -> Rule:
    """查不了的：**永远输出**，不是「还没写完」。

    ⚠ 本层两条的前件形状**不同**，别合成一个：
      · `role_fate_unverifiable` —— **无条件**产出（这一维根本没有确定性防线）；
      · `secrets_absent` —— **判定前提不成立**时出声（这本书一条秘密都没有）。
    """
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='unver',
                                  who_checks=who_checks,          # type: ignore[arg-type]
                                  requirement=requirement, **kw))


# ═══ 一、产物完整性与章对齐（4 HARD）══════════════════════════════════════
#
# ⚠ 四条全部指向 `R-3.16`。它们答的是同一个问题的四个面：**这份产物与正文章对得上吗**。

_hard('upstream_missing', '两份 stage2 产物齐备', 'R-3.16',
      note='⚠ **缺一份判 FAIL 不判 note**：什么都没查却打 PASS，正是'
           '「一条从不执行的判据与一条不存在的判据在报告上长得一样」那个病。'
           '本层的复检相要两份一起读（判 `canonical_unique` 要 global，'
           '判 `present_ids_dangling` 要 registry）⇒ 缺一份不是「少查几条」，是**什么都没查**。'
           '盘上 0/3')

_hard('section_id_leak', 'blueprint 不含上游的分节键', 'R-3.16',
      note='章的唯一键**全链只有一个**（`chapter_ordinal`）。混进 `section_id` 之后，'
           '下游按哪个键 join 就成了一件要去读代码才知道的事。盘上 0/3。'
           '⚠ 2026-08-22 起 `section_id` 已从 stage1 两份产物里删除（阶段 B：'
           '一份产物只留一个系统主身份）⇒ 上游不再有这个键可供泄漏，本条从此'
           '**只守一件事：模型别自己发明第二个章键**。那不是变成空验证——'
           '恰恰相反，它现在是这条事实在全链的唯一守卫。')

_hard('chapter_coverage', '每个正文章恰有一份 blueprint', 'R-3.16', fix=FIX_REGEN,
      note='不缺 / 不多 / 不重，三个方向一条判据。盘上 0/3（14+8+9 章全覆盖）。'
           '⚠ **重号那一半已被载体收走**（`ChapterBlueprints._chapter_ordinals_are_unique`），'
           '这里仍要查它——载体只看本产物内部，而「缺章」「多章」要拿 `main_text` 比')

_hard('chapter_order', 'blueprint 顺序与 main_text 章序一致', 'R-3.16', fix=FIX_REGEN,
      note='顺序错而集合对，是分批产出年代的典型残留：`beat_id` 里的章号由代码按'
           '**位置**归一，顺序一乱，`b03_01` 就会挂到第 5 章上。盘上 0/3')

# ═══ 二、称谓表（7 HARD · 4 INFO）═════════════════════════════════════════

_hard('canonical_unique', '一个显示名只挂一个 char_id', 'R-2.1', fix=FIX_RESAMPLE,
      note='重名 ⇒ 下游按名字解析时**静默**取到其中一个，而取到哪一个只取决于遍历顺序。'
           '盘上 0/3（12 / 15 / 48 个显示名）')

_hard('character_unattested', '每个角色至少有一个叫法在原文里搜得到', 'R-2.2', fix=FIX_RESAMPLE,
      note='判的是 `canonical`（剥掉区分词之后）与全部 `surface` 里**至少有一个**能搜到。'
           '⚠ 与 `address_unlocatable` 是**同一条需求的两级**：那一条逐条查、判 INFO；'
           '本条查「这个人在原文里到底有没有出现过」，一个都搜不到＝**这个角色是编的**。'
           '盘上 0/3')

_hard('address_by_unresolved', '「谁在这么叫」解析得出来', 'R-2.4', fix=FIX_RESAMPLE,
      note='取值是 registry 里的 `char_id`，或 `narration` / `public`。'
           '⚠ 载体那边的 `AddressBy` 只管**形状**（`^(char_\\d+|narration|public)$`），'
           '「这个 `char_001` 在不在 registry 里」是**跨产物**的，只能是判据。'
           '⚠ 群体名一律压成 `public`（落盘前兜底，账记在 `_provenance.address_by_fallback`）'
           '——这个字段的用途是**派发键**，一个解析不出人的串对派发毫无用处。'
           '盘上 0/3（27 / 47 / 82 条）')

_hard('address_from_out_of_range', '「从第几章起可用」在全书章范围内', 'R-2.5',
      fix=FIX_RESAMPLE,
      note='越界 ⇒ 这条称呼**永不下发**，而它在产物里看着一切正常。盘上 0/3')

_hard('address_from_too_early', '「从第几章起可用」不早于该字面在原文里首次出现的章', 'R-2.5',
      fix=FIX_RESAMPLE,
      note='⚠ **单边判定**：早于字面首现一定错（读者还不知道这个叫法）；晚于合法'
           '（作者可以先用别的叫法）。反过来判就会把一大批正当的谨慎标注报成违规。'
           '⚠ 落盘前会**只往后抬**，账记在 `_provenance.address_from_chapter_raised`'
           '（盘上共 7 处）⇒ 本条在成品上恒 0 是**预期**，它守的是抬升逻辑本身没坏。'
           '盘上 0/3')

_hard('address_bare_pronoun', '称谓整条不是代词', 'R-2.3', fix=FIX_RESAMPLE,
      note='代词相对说话人，收了它下游必然取到「我妈」这种说法，而它不触发任何终态。'
           '⭐ **盘上 1/3 有真反例**：三岁那本 `char_034` 的 `surface` 整条就是「她」。'
           '词表是 `contracts/invariants.py::BARE_PRONOUNS`（我/你/他/她/它/咱），'
           '**整份插值进 `GLOBAL_SYS`，不许在这里手抄子集**')

_hard('address_all_pronoun', '一个角色有据的叫法不得全都含代词', 'R-2.3',
      fix=FIX_RESAMPLE,
      note='上一条判「整条是代词」，本条判**这个角色一条干净叫法都没有**——'
           '那种情况下游 `display_name` 只能取到「我妈」。'
           '两条分开是因为**修法不同**：前者删掉那一条即可，后者要补一条新的。盘上 0/3')

_info('address_has_pronoun', '含代词的叫法旁边另有干净叫法', 'R-2.3',
      note='**违约，但下游取不到它**（`display_name` 会挑干净的那条）⇒ 判 INFO。'
           '盘上 **1/3**（三岁 2 条）')

_info('address_selfref', '没有把别人对他的称呼挂成自称', 'R-2.5b',
      note='⚠ **自称本身正当**（本宫 / 哀家 / 朕），不正当的是把别人对他的称呼挂错了人。'
           '判据只能报出「`by` 等于这条称谓所属的角色」这个形状，**哪一半正当要人看**'
           '⇒ INFO。盘上 **1/3**（三岁 1 条）')

_info('address_missing', '每个角色至少有一条称呼', 'R-2.5b',
      note='叙述里总得指称他，一条都没有多半是漏了。'
           '⚠ **载体那边已有 `min_length=1`**（`Character.address`），本条在结构上'
           '与载体形成同一约束的报告投影；判据纯函数直接接收候选 dict，'
           '因此仍能在落盘前给出带规则 id 的失败结论。')

_info('address_unlocatable', '每条叫法都能在原文里原样找到', 'R-2.2',
      note='⚠ **逐条**查，与 `character_unattested`（至少有一个）是同一条需求的两级。'
           '判 INFO 不判 HARD：绰号 / 转述里的叫法确实可能改过一两个字，'
           '而落盘前的最长子串回取已经修掉一批（账在 `_provenance.address_surface_rewrites`，'
           '盘上 0 / 2 / 8 条）。盘上 0/3')

# ═══ 三、身份与结局（5 HARD · 1 度量 · 1 查不了）══════════════════════════

_hard('role_missing', '每个角色都有静态身份描述', 'R-2.6', fix=FIX_RESAMPLE,
      note='下游**只下发 `role`，不下发 `fate`** ⇒ 缺了它写手不认识这个人。盘上 0/3')

_hard('role_leaks_fate', '静态身份里没有结局词', 'R-2.6', wordlist=True, fix=FIX_PURGE_ROLE,
      baseline='⭐ **盘上 2/3 命中**（头牌 2 处 · 三岁 5 处）。'
               '⚠⚠ **词表只抓转抄式**：`(最终/后被/而死/下狱/被处死/走向/结局/沦为)` '
               '在头牌上命中 **0/17 而人读真违规 6/17**——`char_009.role` 写着'
               '「…**后成为**李宝珠的下属」，**差一个字避开了 `后被`**。'
               '⇒ 「没报违规」在这一维上不等于「没问题」，那半边由 `role_fate_unverifiable` '
               '常驻说出来',
      note='拆 role/fate 两个字段的**唯一理由**就是只下发 role——写手提前知道谁最后会死，'
           '第 1 章的伏笔就写过明。⇒ 结局漏回 role 就是把这次拆分白做了。'
           '⚠ 修法是**付费**的（`--purge-role` 要再调一次语义判官），'
           '它是本表唯一一条 `is_paid` 为真的 fix')

_hard('role_repeats_name', '静态身份不与显示名完全同字', 'R-2.6', fix=FIX_RESAMPLE,
      note='同字的话写手拿到的下发面是「李宝珠：李宝珠」——一条零信息的介绍。盘上 0/3')

_hard('hedge_words', '身份 / 结局 / 显示名里没有模棱语', 'R-2.6', wordlist=True,
      fix=FIX_RESAMPLE,
      baseline='⭐ **盘上 2/3 命中**（成年人 1 · 头牌 1），⚠ **两处都在 `fate`**——'
               '而 `fate` 一概不下发给写手 ⇒ 这两处的实际危害低于它的严重度，'
               '但**不降级**：同一把尺子量 `role` 时它是真阻断，按命中位置分级'
               '会让这条判据说不清自己在查什么',
      note='模棱语（可能 / 疑似 / 大概 / 或许…）落在这三个字段上意味着**抽取没抽到底**：'
           '这三个字段是「原文写出来的事实」，不确定就不该写进来')

_hard('distinguisher_lonely', '带区分词的显示名真有同名对象', 'R-2.6b',
      fix=FIX_RESAMPLE,
      note='没有重名就没有消歧可言，而**区分词会被写手原样写进正文**'
           '（「李宝珠（丫鬟）掀开帘子」）。⚠ 落盘前会剥区分词，账记在'
           '`_provenance.canonical_distinguishers_stripped`——**盘上 3/3 本恒空**，'
           '而那正是「空也要报」那条形式要求的教科书：空就不打印的话，'
           '这个键从落地到今天一次都不会出现在任何报告里。盘上 0/3')

_unver('role_fate_unverifiable', '改写式结局泄漏有确定性防线', 'R-2.6',
       note='⚠⚠ **本仓改成无条件产出**（同 F4-6 对 `name_surface_survey` 的处置）。'
            '原型把它挂在 `if not leaky` 那一支上——`role_leaks_fate` 命中的那两本'
            '**根本走不到它**，于是「这一维没有确定性防线」这句话'
            '**在最需要说的两本上反而不说**。'
            '⇒ 本条与 `role_leaks_fate` 报绿灯还是红灯无关，**每本每次都产**：'
            '词表只挡转抄式（实测头牌命中 0/17 而人读真违规 6/17），'
            '改写式泄漏归语义层，而语义层只有 `--purge-role` 那条**付费**臂。'
            '⚠ 它是本层徽章到不了 `pass` 的主因，**而那是对的**')

_metric('fate_report', '结局字段的缺失率', 'R-2.7',
        note='⚠ **无条件报出**（`R-2.7` 那一格写的就是 `度量`）：这一格答的是'
             '「结局有没有被单独接住」，不是「结局对不对」。'
             '盘上 12/12 · **9/15** · 48/48 —— 中间那本 6 个角色没有结局，'
             '⚠ 那**不是违约**（配角本来就可以没有归宿），所以它只能是读数')

# ═══ 四、世界规则 · 秘密 · 关系（3 HARD · 3 INFO · 1 度量 · 1 查不了）═════

_hard('world_rule_no_desc', '每条规则至少有一个带正文的版本', 'R-2.11b',
      fix=FIX_RESAMPLE,
      note='`versions[].desc` 是规则正文的**唯一**落点，而下游取正文时'
           '**无兜底、缺失即显式终态**（`or description` 那个兜底就是漏洞本身）。'
           '⚠ 载体那边 `versions` 有 `min_length=1`、`desc` 有 `min_length=1`，'
           '本条在结构上恒不命中——理由同 `address_missing`。盘上 0/3')

_info('world_rule_names_person', '规则正文里出现人名时按叫法时点扣住', 'R-2.11b',
      note='⚠⚠ **这道闸门在盘上什么也没拦住，而判据必须报出自己的基线**：'
           '含人名的规则各 2 / 1 / 1 条，其中**两本的全部叫法在 ch1 就叫得出**'
           '（头牌「夫君」· 三岁「念念」）⇒ 等于全程下发。'
           '不报基线的话，「没报违规」与「闸门一条都没拦住」在报告上**完全同形**。'
           '⚠ 闸门按**叫法**不按**角色**：同一个人从 ch1 就在场（叫「龟公」），'
           '泄底的是「百晓生」这个 ch8 才出现的叫法。盘上 **3/3 都要报基线**')

_hard('secret_setup_order', '埋点章严格早于最早揭示章', 'R-2.9', fix=FIX_RENORMALIZE_G,
      note='⚠⚠ **断言的是 `_provenance.setup_chapters_pruned` 那个计数，不是字段值**'
           '（2026-08-10 裁决）：裁剪跑在落盘前，再断言字段值就是一条**永远为真**的判定。'
           '`==` 也违约——埋点与揭示同章等于没埋。'
           '`setup_chapters_pruned` 是现行来源账的必填键；缺失时载体直接拒收。'
           '⚠ 一条 `sev=hard` 的判据落 `unver` 状态在本仓是合法的：`sev` 与 '
           '`RuleLine.status` 是**两格**。')

_metric('secret_cold_reveal', '没有埋点的秘密条数', 'R-2.9b',
        note='凭空点破。**据实留空是允许的**（作者确实可以不埋），所以它只能是读数。'
             '⚠⚠ 「冷揭示」与「被裁空」（产地把揭示章号抄进埋点、落盘前被裁）'
             '在**字段值上完全同形** ⇒ 两者只能靠 `setup_chapters_pruned` 那个计数分开，'
             '因此该计数必须常驻来源账。')

_hard('secret_known_by_dangling', '「谁知情」里的保留形都解析得出来', 'R-2.9c',
      fix=FIX_RESAMPLE,
      note='⚠ 按**形状**分流：`char_\\d+` 必须能在 registry 里解析，'
           '自由文本（「在场士兵」）**放行**。⚠⚠ 这与 `Address.by` 的处置'
           '**故意相反**：那边要的是派发键，群体名解析不出人就没用；'
           '这边要的是「谁知情」，**群体本身就是答案**，逼它写 id 只会逼出假 id。'
           '**同一个「无名群体」在两个字段上的正确处置方向相反。**'
           '盘上 0/3（24 / 24 / 25 条，⚠ 73/73 全是 `char_id`——自由文本那一臂零样本）')

_info('reveal_unmarked', '声称点破的那一章里有点破痕迹', 'R-2.9d',
      wordlist=True,
      baseline='⭐ **盘上 2/3 命中**（头牌 3/8 · 三岁 6/10）。'
               '⚠ **它是筛子不是判据**：「演而不说」是合法写法，命中不等于错。'
               '它的价值是把「需要有人回原文核一眼」的清单**从全书压到个位数**',
      note='⚠ 与 stage5 那张「揭示标记」词表的教训相反着读：那一张在 21/22 章命中，'
           '通过分支是**一盏保证会亮的绿灯**；本条反过来，**有判别力的是它的反面**。'
           '⇒ 命中后由人核对原文；它不自动提供修改产物的路由')

_unver('secrets_absent', '这本书有可查的秘密', 'R-2.9b',
       note='**一条 secret 都没有** ⇒ 逐章剧透闸门与「本章不许揭示什么」'
            '在这本书上**恒不执行**，任何「剧透闸门验过了」的说法对它都不成立。'
            '⚠ 它与「查过、没泄」必须分得开。盘上 0/3（三本都有秘密，这一支零样本）')

_info('relation_dangling', '关系两端都是 registry 里的 char_id', 'R-2.8',
      note='⚠ 判 INFO 不判 HARD：地点 / 机构 / 物品**按契约就不入 relations**，'
           '而模型偶尔会给一条「李宝珠 → 苏府」——那条解析不出来只是这一条失效，'
           '不影响别的。⚠ **本条不查 `valid_from/to_chapter` 的范围**：'
           '那一半今天没有判据（`R-2.8` 那一格写的是范围，而范围要拿全书章数比，'
           '本层两份产物里都没有那个数）。盘上 0/3（13 / 15 / 42 条）')

# ═══ 五、冲突阶段章覆盖（1 HARD）═══════════════════════════════════════════

_hard('conflict_stage_coverage', '冲突阶段逐章显式枚举且恰好覆盖全书一次', 'R-2.14',
      fix=FIX_RESAMPLE,
      note='`chapters` 是**显式章号枚举**，不是区间端点：`[3, 5]` 只表示第 3、5 章，'
           '不能暗含第 4 章。每个阅读序章号必须恰好出现一次；缺章会让 Stage4 拿不到'
           '`current_stage`，重章会让归属不唯一。实测 Flash 曾把连续段省写成 '
           '`[3,5]/[8,10]/[13,15]`，旧产地照常落盘，直到 Stage4 才裸抛 exit 70。'
           '本条把错误拦在付费产物的产地；问题稿可人工补全后走 `--revalidate` 0 成本提升，'
           '或显式 `--resample` 重采整份 global。')

# ═══ 六、拍与区间（8 HARD · 1 INFO）═══════════════════════════════════════

_hard('beat_id_unique', '全书 beat_id 不重复', 'R-3.1',
      note='`beat_id` 是下游引用一拍的**唯一**键（`stage4` 的 `beat_ref` · '
           '`stage5` 的材料回查都按它 join）。重号 ⇒ 每一次 join 都可能取错拍。'
           '盘上 0/3（93 / 67 / 91 个）')

_hard('beat_id_format', 'beat_id 形如 b<章:02d>_<序:02d> 且章内序连续', 'R-3.1',
      note='三件事一条判据：格式 · 章内序连续 · **章号与所属章一致**。'
           '第三件与 `chapter_order` 是同一个错误的两个观测面。'
           '⚠ 代码**无条件归一、模型自拟一律覆盖**（`R-3.1`）⇒ 本条守的是归一器没坏。'
           '盘上 0/3')

_hard('span_unlocatable', '每条精确区间与本章原文一致且按序', 'R-3.2', fix=FIX_REGEN,
      note='定位不到 ＝ 这一拍**没有量纲**：它的字数、覆盖率、hinge 归属全部落空。'
           '`SourceSpan` 联合核对 offset/text/normalized，且起点不得退到上一拍终点之前。'
           '⚠ `bounded-fidelity-v3` 起区间由**段号查表**算出，本条因此不再守'
           '「模型抄的锚点找不找得到」，而是守**归一器与段落表没坏**'
           '（越界段号 / 区间倒序 / 起段没越过上一拍的止段，都在这一条里出声）。'
           '盘上 0/3（251/251 全中，v2 口径）')

# ⚠⚠ **这里曾经有一条 `anchor_not_verbatim`（INFO，断言 `_provenance.verbatim_recovered`
#   的计数）**，随 `bounded-fidelity-v3` 一并裁撤：beat 的区间改由段号算出、
#   hinge 那一侧的 LCS 兜底本来就是禁的 ⇒「按最长公共子串救回来的锚点」**结构性不存在**。
#   一条不可能红的判据不是保险，是一盏没有判别力的绿灯——留着只会让读的人以为
#   「这次没救回过什么」是一个观测结果，而它其实是一个恒等式。
_hard('verbatim_failed', '落盘前的原文回取没有锚不到的', 'R-3.18', fix=FIX_REGEN,
      note='✚ **原型没有这条**——它那边只有一次 `sys.exit`，**账不落盘**，'
           '而「显式终态」在多命令流水线里只等于「这一步红了」，'
           '**不等于「链路被挡住了」**。⇒ 本仓断言 `_provenance.verbatim_failures` '
           '非空即失败态，根因**分开报，修法各不相同**：'
           '`span` 一族是段号本身坏了（越界 / 倒序 / 与上一拍重叠），'
           '`hinge` 一族是材料锚不到（取值面见 '
           '`contracts/invariants.py::HINGE_FAILURE_ROUTE`）。'
           '⚠⚠ 它替掉了此前那条「靠 `hinges[].text` 的**缺席**去反推失败态」；'
           '现在局部写 `source:null`，汇总账写入 failures，失败态有**正面**落点。'
           '⚠ 盘上一份 v3 产物都还没有 ⇒ 键不在时落 `unver` 状态')

_hard('edge_hole', '章首章尾被第一 / 最后一拍覆盖', 'R-3.8', fix=FIX_REGEN,
      note='判的是 ≥30 字的未覆盖原文**不得落在第一拍之前或最后一拍之后**'
           '（中段的空隙合法——过渡句、环境交代）。'
           '⚠⚠ **prompt 侧已经明说了这一条**（`novel/stages/stage2/prompts.py::BLUEPRINT_SYS` '
           '里「但空隙只能在中间，两头不许留」，原型 2026-08-08 补的，正为那 5 处章首漏抓）'
           '**而那 5 处是在它明说之后发生的** ⇒ 「写进 prompt」没有遵守率保证，'
           '守它的只能是本条。**别照旧措辞再往 prompt 里补一句。**盘上 0/3')

_hard('beats_collapsed', '≥300 字的章不止一拍', 'R-3.9', fix=FIX_REGEN,
      note='⚠ 阈值用的是**契约自己的反例**（一章 ≤1 拍），**不跨书比**：'
           '拍数是同一个 prompt 都能改动 ±44% 的自由变量。盘上 0/3')

_hard('beats_coarse', '章级 字/拍 不超本书中位数的 3 倍', 'R-3.9', fix=FIX_REGEN,
      note='⚠⚠ **同一本书内部才可比**——分母是**本书自己的中位数**，不是跨书常量。'
           '理由同上：拍数是自由变量，拿别的书的中位数当尺子量到的是两本书的差异。'
           '盘上 0/3（中位 143 / 169 / 138 字每拍）')

# ═══ 七、保真材料（6 HARD · 1 INFO）═══════════════════════════════════════
#
# ⚠ `hinges` 锚不到的**四分流**（原型旧口径一律并成一条，给三类发的修法都是
#   旧定位器重跑——对正式精确区间是空转）：
#     · 短语不在本章原文        → `hinge_anchor_void`   → 重生成
#     · 引的是别的章的原文      → `hinge_cross_chapter` → 重生成
#     · 落在任何 span 都没覆盖的空档 → `hinge_in_hole`  → 重生成（漏抓的下游后果）
#     · 本 beat 的 span 自己没定位成功 → `hinge_cascade` → **修上游那条就好，别重复计数**
#
# ⚠⚠ **产地说哪一句话 → 落到哪一条判据，这张对应表在
#   `contracts/invariants.py::HINGE_FAILURE_ROUTE`。** 判据侧**按相等查表**，
#   不再按中文子串猜（`'空档' in cause`）——那是一条没有任何东西在守的隐式契约，
#   产地改一句措辞就能让 `hinge_anchor_void` 静默降级成 `hinge_drift` 而报告照样绿。
# ⚠ `hinge_cross_chapter` **不在那张表里**：产地手里只有本章正文，说不出「这是别章的」，
#   那一档由判据侧现场回扫全书判定。

_hard('hinge_cross_chapter', '保真材料引的是本章的原文', 'R-3.5', fix=FIX_REGEN,
      note='**逐字无误但归错了章**——这一条最像对的：文字确实是原文，只是不在这一章。'
           '它进不了本章的下发面（下发按章切），于是这条材料等于凭空消失。盘上 0/3')

_hard('hinge_in_hole', '保真材料的锚点落在某一拍覆盖到的区间里', 'R-3.5', fix=FIX_REGEN,
      note='⭐ **盘上 1/3 有真反例**：三岁 `b07_05` 的「要害台词」——锚点在本章原文里，'
           '却落在任何 span 都没覆盖到的空档。'
           '⚠ **它是漏抓的下游后果，不是独立故障**：修法是把那一章重抽一遍'
           '（把空档补进某条 span），不是去改这条 hinge。'
           '⚠ `bounded-fidelity-v3` 起「空档」是**段级**的：hinge 指的那一段不属于任何一拍。'
           '同时消失的是「在多个 beat 里都命中、归属不唯一」那一档——段区间由游标单调'
           '保证互不重叠 ⇒ 归属恒唯一，`normalize` 直接按段号把材料改判给正确的拍'
           '（留痕在 `_provenance.verbatim_rewrites` 的 `hinge.owner` 行）')

_hard('hinge_anchor_void', '保真材料的锚点在本章原文里找得到', 'R-3.5', fix=FIX_REGEN,
      note='**是转述或编的**——四分流里唯一一条根因在模型身上的。'
           '⚠ `bounded-fidelity-v3` 起它收两种根因：短语整章都找不到（编的），'
           '以及段号指向一个本章不存在的段（位置也是编的）。'
           '⚠ **「短语在本章、只是不在它指的那一段」不归这里**，归 `hinge_drift`：'
           '那是段号指错，不是材料编的，说错了会让人去查一句原文里明明有的话。盘上 0/3')

_hard('hinge_ambiguous', '保真材料的短语在所属拍的区间内唯一命中', 'R-3.5', fix=FIX_PATCH,
      note='`extraction-ir-v1` 专属：hinge 不再交段号，短语在**所属拍区间内**定位；'
           '多处命中 ⇒ 这条材料压在哪几个字上定不下来。'
           '⚠ **它是新契约信息不足的预期产物，不是模型编的**：交接实测 189 条反投影后'
           '187 条唯一、2 条双命中（`第一课` / `纸鸢`，约 1.06%）。'
           '⚠ 修法是**候选选择**（代码从 `hinge_ambiguous_pending` 账取回短语并枚举'
           '命中位，模型只挑 candidate_id——`novel/stages/stage2/repair.py`；'
           '`contracts/invariants.py::REPAIR_ROUTE` 同判 `field_patch`），'
           '量级是**条**不是章，别拿 `--chapters` 整章重付。'
           '`bounded-fidelity-v3` 的产地不发这个根因 ⇒ 存量产物上恒空，不是绿灯是不适用')

_info('hinge_cascade', '锚不到不是因为本拍的 span 自己就没定位成功', 'R-3.5',
      note='⚠ **不是独立故障，别重复计数**：`span_unlocatable` 已经报过那条 span 了，'
           '这里再报一次会让同一个病在报告上出现两行、而修法只有一个。'
           '⇒ 判 INFO 且**不给 fix**：修上游那条 span 就好。盘上 0/3')

_hard('hinge_drift', '保真材料的正文落在本拍 span 的原文范围内', 'R-3.5', fix=FIX_REGEN,
      note='正式产物联合核对 hinge `SourceSpan` 的 offset/text/normalized，并要求其区间完整'
           '落在所属 beat.span 内；因此能区分「原文字节不一致」与「材料归错拍」。'
           '不满足时说明材料字节或归属不可信。'
           '盘上 0/3（113 / 66 / 103 条全中）')

_hard('chapter_no_hinge', '整章不得一条保真材料都没有', 'R-3.6b', fix=FIX_REGEN,
      note='保真材料是闭卷写作**唯一**的通道 ⇒ 整章为 0 等于这一章的原文材料'
           '一条都没往下游走。凡有对白、或交代了因果/身份/利害关系的章'
           '几乎不可能一条都没有。⚠ **单拍为空是正常的**（盘上 63/251 拍为空），'
           '判的是章级。盘上 0/3')

# ═══ 八、在场与叙述（8 HARD · 6 INFO）═════════════════════════════════════

_hard('time_layer_no_present', '全书不得 0 条「现场」层', 'R-3.6c', fix=FIX_REGEN,
      note='⚠⚠ **枚举合法 ≠ 分布合理**——取值面那一半已被载体收走'
           '（`Beat.time_layer` 是 `Literal[TIME_LAYERS]`），而**每一条都合法、'
           '合起来不可能**这件事载体表达不了。'
           '实测某本 **0/70**：模型按**叙述时态**判层，而这个字段判的是**叙述层次**'
           '（回顾式叙述的主线本身就是现场）。前件 `n >= 10` 才判——'
           '章数太少时全是回忆是可能的。盘上 0/3（present 95% / 100% / 92%）')

_hard('present_ids_dangling', '在场人物 id 都在 registry 里', 'R-3.7', fix=FIX_RENORMALIZE_BP,
      note='⚠⚠ **这是解析器的后置条件，不是数据判据**：`present_char_ids` 是'
           '**代码从 `present_names` 解析出来的**，模型不写它 ⇒ '
           '违反它 ＝ **代码坏了，不是数据坏了**。'
           '⇒ 修法是 `--renormalize`（重跑一遍确定性解析，0 成本），不是重生成。'
           '盘上 0/3（130 / 148 / 173 个）')

_hard('present_names_invented', '在场者的称呼在全书原文里找得到', 'R-3.7b', fix=FIX_REGEN,
      note='找不到 ＝ 模型**凭空造了一个人**，没有合法情况 ⇒ 五分里唯一一条判 HARD 的。'
           '⚠ 另四种去向（唯一命中 / 歧义 / 名册漏收 / 代词）**处置完全不同，'
           '绝不合成一个「无据」**——它们各有一条度量。'
           '盘上 0/3（177 / 176 / 216 个，`invented` 恒 0）')

_info('present_continuity', '相邻拍同地同层时在场集连续', 'R-3.7c',
      note='⚠⚠ **不判阻断**：按它去修数据会让另一半更差——「内心剖析某人」时'
           '逼着把他列进在场，就是造一个不存在的在场者。'
           '⚠ 它**依赖 `place` 字面相等**（自由文本、粒度不稳：街上/街边），'
           '**系统性漏检**——0 条不等于没有漏标。盘上 **2/3**（5 / 7 / 0 处）')

_info('present_ungrounded', '（章, 在场者）对在本章原文里有字面依据', 'R-3.7c',
      note='⚠⚠ **它不是幻觉检测器**：三本 128 对里只报 1 条（成年人 `ch10 char_009(祈颜)`），'
           '逐条回原文核过，**幻觉在场者 0 例**。它实际抓的是两件别的事：'
           '① `address[]` 漏收了原文在用的叫法（绰号 / 无归属台词）'
           '② **后见之明标注**（身份本章尚未揭示，而 `present_char_ids` 是逐拍下发给写手的'
           '⇒ 那是一条剧透旁路）。也确有合法反例（整段用代词 / `recall`/`hearsay` 的'
           '在场者本章不留字面）⇒ **判 INFO 不判 HARD**。'
           '⚠⚠ **名字表不许复用 `present_broad` 那一份**——那份带 `len>=2` 过滤，'
           '搬过来会把「唯一 surface 是『姐』」的角色报成找不到依据，'
           '那是**判据自己造的假阳**。'
           '⚠ **叙述者必须豁免且单独计数**。盘上 **1/3**（1/36 · 0/43 · 0/49）')

_info('present_early_named', '在场者不早于该角色在原文里被指名的章', 'R-3.7d',
      note='后见之明的第一条通道。⚠ **第一人称叙述者是系统性合法反例**'
           '（通篇写「我」，名字晚出现是常态）⇒ 必须豁免且单独计数。盘上 0/3')

_info('event_early_name', '事件文本里不写出本章尚未指名的角色名', 'R-3.7d', fix=FIX_REGEN,
      note='后见之明的第二条通道，⚠ **下游堵不住**：在下游做文本替换会摧毁'
           '人名替换那条构造性安全论证 ⇒ 只能从这一层修。'
           '⚠ **只查 `canonical`，排除通名**（「掌柜」「丫鬟」不算指名）。盘上 0/3')

_info('pronoun_surface_unattested', '叙述者自称的字面照原文用字', 'R-3.14b',
      note='第三人称交空数组合法。⚠ 这个字段存在的理由是**别拿代词密度当判据**：'
           '`FQ_群臣逼朕送镇国大将军去青楼` 的「我」只有 0.1/千字，看着像第三人称，'
           '实际是第一人称、自称是「朕」。'
           '⚠ 凡是契约要求逐字照抄的就该有一条确定性检查盯着，而不是只在 prompt 里叮嘱'
           '——实测 262 个照抄锚点 30 个对不上，**prompt 劝说压不住**。'
           '盘上 0/3（14 / 8 / 9 个全有据）')

_info('narration_multi', '一章内只有一个叙述声音', 'R-3.14',
      note='⚠⚠ 报的**不是「这本小说有问题」**（章内接一段他人视角 / 书信是合法写法，'
           '判 HARD 就是判据把合法情况排除在外），报的是**本层的能力边界**：'
           '「本章有 N 个叙述声音，而本层不记段界」——写作节点只知道分几段、不知道界在哪。'
           '⚠ 番外由 `R-0` 剔除，但**剔除词表不含「他人视角」「书信」** ⇒ '
           '这条不是不可能发生，它只是从没被观测到过。'
           '⚠⚠ **全语料 163 本 + 盘上 31 章零样本** ⇒ 合成反例必须跟上')

_hard('scope_violation', '没有某一批越出「本次任务范围」产出', 'R-3.16b',
      note='断言 `_provenance.scope_violations` 非空即失败。'
           '**产地无条件写这个键**（没越界写空列表）；本条同时守住逐章调用的范围。')

_hard('event_empty', '每一拍都有非空事件文本', 'R-3.13', fix=FIX_REGEN,
      note='✚ **原型没有这条**——`R-3.13` 索要它。'
           '⚠⚠ **判在本层不判 stage4**：留在 stage4 意味着要**先付掉一次规划调用**'
           '才发现这一层产坏了。这一层每个字段都是 LLM 写的，而产地只校验顶层键，'
           '少写一个 `event` 下游一路 `or \'\'` 不出声。盘上 0/3（251/251）')

_hard('summary_empty', '每一章都有非空摘要', 'R-3.13', fix=FIX_REGEN,
      note='✚ 同上。⚠ `summary` **0 确定性消费方**，唯一消费方是计划节点的 prompt'
           '——而规划节点手里**有全书原文** ⇒ 留着它是因为「帮它快速理解本章、无害」，'
           '**不是因为不可替代**。判它非空是因为**契约说了它必填**，'
           '而这一层没有任何东西在执行那句话。盘上 0/3（31/31）')

_hard('repair_cache_policy', '定向重生成配的是「不读缓存」', 'R-3.11',
      note='✚ **原型没有这条**——`R-3.11` 索要它，而它是本层最反直觉的一条：'
           '定向重生成的 prompt 与整跑时该章那一批**逐字相同** ⇒ **缓存必然命中** ⇒ '
           '**一个字都没重新生成，而它照样打印「已重生成」**。'
           '⇒ `repairs[]` 里 `fields == ["*"]`（整章重生成）的行 `cache_policy` '
           '必须是 `refresh-write`。'
           '⚠ **字段级修复（`--patch`，fields 记具体路径）豁免**：它发的是新 prompt，'
           '缓存里没有坏响应可读，默认策略反而是「中断恢复不重复付费」的正确语义'
           '（refresh-write = 明确要新采样，不是「这是修复调用」）。'
           '⚠ **不给 fix**：修法是下一次跑的时候带对 flag，不是拿一条命令去改这份产物。'
           '盘上 0/3（3 条 `repairs` 恒 `refresh-write`）')

# ═══ 九、度量（16 条，无条件报出，`owner=None`）════════════════════════════
#
# ⚠⚠ 这一节整节的判准是原型注释里那句「**判据 21：一出问题就不许知道现状**」——
#   把一个读数挂在 `if 违规` 上，就等于「出问题的时候反而看不见基线」。
#   ⇒ 本节 15 条**全部无条件产出**，一条前件都没有。

_metric('narration_voices', '多叙述声音的章数', 'R-3.14',
        note='它是 `narration_multi` 那一整维的**样本量**：没有这个数，'
             '「没报多声音」与「这一维根本没在查」同形。盘上 0/14 · 0/8 · 0/9')

_metric('time_layer_mix', '叙述层分布', 'R-3.6c',
        note='`time_layer_no_present` 的输入。盘上 present/recall/hearsay = '
             '88/5/0 · 67/0/0 · 84/2/5')

_metric('place_moves', '转场点数', 'R-3.17',
        note='⚠⚠ **系统性高估**：`place` 是自由文本、粒度不稳（街上/街边、'
             '公寓/公寓卫生间门口），按它相等推导出来的转场数在量措辞。'
             '⚠ 它作为**下发给写手的舞台信息**是对的，不改；'
             '但**凡引用这个数的地方都要带上这一句**。盘上 23 / 22 / 39 处')

_info('place_inherit_without_base', '「延续上一拍地点」有可继承的基', 'R-3.17',
      note='`extraction-ir-v1` 专属：模型交 `P_INHERIT` 而链上没有基'
           '（首拍、或上一相邻拍不同 time_layer）⇒ compiler 落 `place=null` 并记'
           '`_provenance.place_inherit_without_base`。'
           '⚠ **产物侧的 `place=null` 分不出「无从知道」与「指错了继承」**，'
           '这笔账是两者唯一的分界——无声转换就是把模型的错吞掉。'
           '判 INFO 不判 HARD：链头 INHERIT 语义上等于 UNKNOWN，产物无害，'
           '报出来是给「模型在多大程度上乱用 INHERIT」一个读数。'
           '`bounded-fidelity-v3` 产物无此账 ⇒ 报「不适用」空行，不是绿灯')

_info('place_explicit_unlocatable', '`P_EXPLICIT` 的地点名在截止本章的正文里找得到',
      'R-3.17',
      note='`extraction-ir-v1` 系专属，且**是 ir1.1 的配套探针不是违约判定**：'
           'ir1.1 把 `P_EXPLICIT` 从「三选一」提回「首选档」以救回地点填充率'
           '（ir1 相对 v3 掉 −59%，§十一 实测两个模型上都掉 ⇒ 契约的责任不是模型的）。'
           '加压时模型会找最省力的出路，而 place 是**单字段**、没有别的字段可牺牲 ⇒ '
           '**唯一的规避路径是编一个地点**，这条就是那条路径上的探针：'
           '**填充率涨了而这条同步涨，涨的就是幻觉不是覆盖**。'
           '⚠⚠ **匹配面是「截止本章正文」不是「本拍区间」**——三个面在盘上'
           '（Pro×v3 无情道 132 个有地点的拍）实测：本拍区间 **78%** 找不到 · '
           '本章全文 42% · 截止本章正文 **13%**。地点本来就常写在上文'
           '（那正是 `P_INHERIT` 存在的理由），「合欢宗」「擂台」「灵网」这类场景名'
           '更不会在某一拍里再出现一次 ⇒ 按本拍区间判等于让噪声淹死信号。'
           '取的是与 `present_names_invented` 同源的面；用「截止本章」而不是「全书」'
           '还有一层语义：只在后文出现的说法本身就是后见之明。'
           '⚠⚠ **判 INFO 且不该升 HARD**：就算在这个面上，那 13% 也**不是幻觉**——'
           '盘上 17 条逐条看全是**用原文成分组合**出来的（「武场外」「黎玄的洞府」'
           '「前往秘境的路上」「合欢宗门口」），一条凭空编的都没有。'
           '⇒ 读法是**相对基线的变化**，不是「命中即违规」；基线记在实验手册。'
           '⚠ 多处命中算找得到（地点名重复出现正常，它不像 hinge 需要唯一）。'
           '`bounded-fidelity-v3` 产物无此账 ⇒ 报「不适用」空行，不是绿灯')

_metric('present_names_ambiguous', '称呼在名册里挂着不止一个人', 'R-3.7b',
        note='⚠ **原文自己的歧义，重跑一百次也一样** ⇒ **不猜、不判阻断**：'
             '三条候选消歧规则实测 **0/9 有效**，判 HARD 只会得到一盏永远红着的灯。'
             '盘上 **1** / 0 / 0')

_metric('present_names_unlisted', '原文里真有、只是名册没收它', 'R-3.7b',
        note='⚠ **这是全书级抽取该修的，不是本层违约** —— 所以它是读数不是告警。'
             '盘上 **14 / 14 / 24**（五分里最大的一格）')

_metric('present_names_pronoun', '在场者写成了代词', 'R-3.7b',
        note='⚠⚠ **代词按设计永远不在名册里**（`address[].surface` 按设计不收代词）'
             '⇒ 它**既不是自造也不是漏收**，是两条契约在这里对不上。'
             '第一人称书里模型会照原文写「我」——那是**对的**。'
             '词表同 `address_bare_pronoun`（`invariants.BARE_PRONOUNS`）。'
             '盘上 **15**（5 项定人成功 / 10 项定不了）/ 0 / 0')

_metric('present_names_late', '称呼要到本章之后才出现 → 已拦下', 'R-3.7d',
        note='⚠ **这是收益不是违约**：拦下来的正是后见之明标注那条剧透旁路。'
             '报它是为了让「这道闸拦了几条」可见。盘上 0 / 0 / 0')

_metric('present_broad', '事件文本点名了某角色而本拍未列它', 'R-3.7c',
        note='宽筛。⚠ **精度约 1/4**，且**分子分母不同量纲**（拍×角色 vs 拍）'
             '⇒ 只能是读数。⚠ 它带 `len>=2` 的名字过滤，'
             '**`present_ungrounded` 不许复用这一份**。盘上 45 / 74 / 69 处')

_metric('batch_density', '按一次调用分组的抽取量', 'R-3.16b',
        note='⚠ **必须常驻报出**：逐章产物上它说明的是「批与章是同一个东西」'
             '（`_provenance.batches` 恒 `[[1],[2],…]`），'
             '并让调用次数与章覆盖可以直接核对。')

_metric('context_scope', '逐章抽取采用哪版上下文、边界与窗口', 'R-3.16b',
        note='⚠⚠ **必须常驻打印**：不打印它，读的人会把「这次没报跨章引用」'
             '当成模型学乖了，而其实只是没抽到。'
             '报告 architecture、稳定前缀指纹、当前章全文、未来可见性及三个过去窗口。')

_metric('span_coverage', '区间定位成功条数 / 覆盖率 / 每条字数分位', 'R-3.2',
        note='`span_unlocatable` 那一维的样本量与基线。盘上 98% / 98% / 97%')

_metric('span_coverage_per_chapter', '逐章覆盖率', 'R-3.2',
        note='⚠ **全书一个数看不出哪一章塌了**：三本的全书覆盖率都是 97~98%，'
             '而逐章最低分别是 ch5 95% · ch4 96% · **ch7 90%**——'
             '最后那一章正是 `hinge_in_hole` 那条真反例所在的章')

_metric('uncovered_holes', '未覆盖原文区间', 'R-3.8',
        note='按**位置 × 长度**两维切分（章首 / 中段 / 章尾 × ≥30 字 / 更短），'
             '⚠ **分到哪一类交人工**——`edge_hole` 只吃「两头 + ≥30 字」那一格，'
             '其余格子是合法的空隙。盘上 2% / 2% / 3%')

_metric('hinge_density', '全书保真材料密度', 'R-3.6b',
        note='⚠⚠ **按原文字数归一，不按拍数**：拍数是同一个 prompt 都能改动 ±44% 的'
             '自由变量，拿它做分母量到的是**切分粗细**不是材料多少。'
             '盘上 9.3 / 6.2 / 7.8 条每千字')
