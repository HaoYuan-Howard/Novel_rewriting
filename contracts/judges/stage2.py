#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 共享人物、必要知识与完整剧情蓝图的判据注册表。

生成、免费复检和修订共用这些规则；章级剧情检查由 plot_checks 实现。
机械检查只证明形状、引用与证据闭合，归因、因果和揭示语义由独立范围审阅裁决。
修复路由由注册表绑定当前节点及参数；实际条目和严重度以注册结果为准。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage2'

#: 本层两个节点的修复路由。**写成常量而不是每处一个元组字面**：
#: 16 条判据指着同一条 `blueprint --chapters`，散着写就是 16 份会各自漂的拷贝。
FIX_REGEN = ('stage2.blueprint', '--chapters')      # 定向重生成那几章（**付费**）
FIX_REGEN_ARGS = ('--chapters',)                    # 值由本次 finding 的机器证据给
FIX_RENORMALIZE_G = ('stage2.global', '--renormalize')      # 同上，全书那一份
FIX_CHARACTERS = ('stage2.characters', '--resample')  # 只重采人物 INVALID；typed 人工编辑另走免费恢复入口
FIX_RESAMPLE = ('stage2.global', '--resample')  # 绕过问题缓存重采整份 global（**付费**）


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
    （`time_layer_mix` · `narration_voices` · `span_coverage_per_chapter`）。它们与告警的分界线是
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

_hard('canonical_unique', '一个显示名只挂一个 char_id', 'R-2.1', fix=FIX_CHARACTERS,
      note='重名 ⇒ 下游按名字解析时**静默**取到其中一个，而取到哪一个只取决于遍历顺序。'
           '盘上 0/3（12 / 15 / 48 个显示名）')

_hard('character_unattested', '每个角色至少有一个叫法在原文里搜得到', 'R-2.2', fix=FIX_CHARACTERS,
      note='判的是 `canonical`（剥掉区分词之后）与全部 `surface` 里**至少有一个**能搜到。'
           '⚠ 与 `address_unlocatable` 是**同一条需求的两级**：那一条逐条查、判 INFO；'
           '本条查「这个人在原文里到底有没有出现过」，一个都搜不到＝**这个角色是编的**。'
           '盘上 0/3')

_hard('address_by_unresolved', '「谁在这么叫」解析得出来', 'R-2.4', fix=FIX_CHARACTERS,
      note='取值是 registry 里的 `char_id`，或 `narration` / `public`。'
           '⚠ 载体那边的 `AddressBy` 只管**形状**（`^(char_\\d+|narration|public)$`），'
           '「这个 `char_001` 在不在 registry 里」是**跨产物**的，只能是判据。'
           '⚠ 群体名一律在落盘前压成 `public`'
           '——这个字段的用途是**派发键**，一个解析不出人的串对派发毫无用处。'
           '盘上 0/3（27 / 47 / 82 条）')

_hard('address_from_out_of_range', '「从第几章起可用」在全书章范围内', 'R-2.5',
      fix=FIX_CHARACTERS,
      note='越界 ⇒ 这条称呼**永不下发**，而它在产物里看着一切正常。盘上 0/3')

_hard('address_from_too_early', '「从第几章起可用」不早于该字面在原文里首次出现的章', 'R-2.5',
      fix=FIX_CHARACTERS,
      note='⚠ **单边判定**：早于字面首现一定错（读者还不知道这个叫法）；晚于合法'
           '（作者可以先用别的叫法）。反过来判就会把一大批正当的谨慎标注报成违规。'
           '⚠ 落盘前会**只往后抬** ⇒ 本条在成品上恒 0 是**预期**，'
           '它守的是抬升逻辑本身没坏。'
           '盘上 0/3')

_hard('address_bare_pronoun', '称谓整条不是代词', 'R-2.3', fix=FIX_CHARACTERS,
      note='代词相对说话人，收了它下游必然取到「我妈」这种说法，而它不触发任何终态。'
           '⭐ **盘上 1/3 有真反例**：三岁那本 `char_034` 的 `surface` 整条就是「她」。'
           '词表是 `contracts/invariants.py::BARE_PRONOUNS`（我/你/他/她/它/咱），'
           '**整份插值进 `novel/stages/stage2/characters.py::SYSTEM`，不许手抄子集**')

_hard('address_all_pronoun', '一个角色有据的叫法不得全都含代词', 'R-2.3',
      fix=FIX_CHARACTERS,
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
           '而落盘前的最长子串回取会先确定性修正。盘上 0/3')

# ═══ 三、身份与结局（5 HARD · 1 INFO · 1 度量 · 1 查不了）════════════════════════

_hard('role_missing', '每个角色都有静态身份描述', 'R-2.6', fix=FIX_CHARACTERS,
      note='下游**只下发 `role`，不下发 `fate`** ⇒ 缺了它写手不认识这个人。盘上 0/3')

_hard('role_leaks_fate', '静态身份里没有结局词', 'R-2.6', wordlist=True,
      fix=FIX_CHARACTERS,
      baseline='⭐ **盘上 2/3 命中**（头牌 2 处 · 三岁 5 处）。'
               '⚠⚠ **词表只抓转抄式**：`(最终/后被/而死/下狱/被处死/走向/结局/沦为)` '
               '在头牌上命中 **0/17 而人读真违规 6/17**——`char_009.role` 写着'
               '「…**后成为**李宝珠的下属」，**差一个字避开了 `后被`**。'
               '⇒ 「没报违规」在这一维上不等于「没问题」，那半边由 `role_fate_unverifiable` '
               '常驻说出来',
      note='拆 role/fate 两个字段的**唯一理由**就是只下发 role——写手提前知道谁最后会死，'
           '第 1 章的伏笔就写过明。⇒ 结局漏回 role 就是把这次拆分白做了。'
           '静态人物可由独立 producer 定向返工；人工核对后的新 `role` 另可走 '
           '`stage2.characters --apply-edit <书内json>`（0 LLM）')

_hard('role_repeats_name', '静态身份不与显示名完全同字', 'R-2.6', fix=FIX_CHARACTERS,
      note='同字的话写手拿到的下发面是「李宝珠：李宝珠」——一条零信息的介绍。盘上 0/3')

_hard('hedge_words', '身份 / 显示名里没有模棱语', 'R-2.6', wordlist=True,
      fix=FIX_CHARACTERS,
      baseline='旧裸子串基线 **2/3 命中**，两处都在 `fate`；拆出 fate 后'
               '`role/canonical` 基线为 **0/3**。',
      note='模棱语（可能 / 疑似 / 大概 / 或许…）落在下发字段上意味着'
           '**抽取没抽到底**。⚠ 「不可能」是确定判断，匹配器不再把它当成「可能」；'
           '「不太可能 / 并非不可能」仍是不确定并继续命中')

_info('fate_hedge_words', '结局字段的模棱语筛查', 'R-2.6', wordlist=True,
      baseline='旧裸子串基线 **2/3 命中**（成年人 1 · 头牌 1），两处都在 fate；'
               '新匹配器排除确定的「不可能」后待 current-schema corpus 重算。',
      note='`fate` 不下发 Stage4/5 写手，且其人工复核项的交付效果本就是 '
           '`disclosure`，所以模棱语保留在报告中但不拒绝 global 落盘，也不拦 Stage4。')

_hard('distinguisher_lonely', '带区分词的显示名真有同名对象', 'R-2.6b',
      fix=FIX_CHARACTERS,
      note='没有重名就没有消歧可言，而**区分词会被写手原样写进正文**'
           '（「李宝珠（丫鬟）掀开帘子」）。落盘前会确定性剥除无必要区分词。盘上 0/3')

_unver('role_fate_unverifiable', '改写式结局泄漏有确定性防线', 'R-2.6',
       note='⚠⚠ **本仓改成无条件产出**（同 F4-6 对 `name_surface_survey` 的处置）。'
            '原型把它挂在 `if not leaky` 那一支上——`role_leaks_fate` 命中的那两本'
            '**根本走不到它**，于是「这一维没有确定性防线」这句话'
            '**在最需要说的两本上反而不说**。'
            '⇒ 本条与 `role_leaks_fate` 报绿灯还是红灯无关，**每本每次都产**：'
            '词表只挡转抄式（实测头牌命中 0/17 而人读真违规 6/17），'
            '改写式泄漏仍须人读；人物 producer 返工也不能代替 Global Commander 的语义裁决，'
            '人工核对后的 typed `role` 可用 `stage2.characters --apply-edit` 0 LLM 写回。'
            '⚠ 它是本层徽章到不了 `pass` 的主因，**而那是对的**')

_metric('fate_report', '结局字段的缺失率', 'R-2.7',
        note='⚠ **无条件报出**（`R-2.7` 那一格写的就是 `度量`）：这一格答的是'
             '「结局有没有被单独接住」，不是「结局对不对」。'
             '盘上 12/12 · **9/15** · 48/48 —— 中间那本 6 个角色没有结局，'
             '⚠ 那**不是违约**（配角本来就可以没有归宿），所以它只能是读数')

# ═══ 四、世界规则 · 秘密 · 关系（5 HARD · 3 INFO · 1 度量 · 1 查不了）═════

_hard('world_rule_no_desc', '每条规则至少有一个带正文的版本', 'R-2.11b',
      fix=FIX_RESAMPLE,
      note='`versions[].desc` 是规则正文的**唯一**落点，而下游取正文时'
           '**无兜底、缺失即显式终态**（`or description` 那个兜底就是漏洞本身）。'
           '⚠ 载体那边 `versions` 有 `min_length=1`、`desc` 有 `min_length=1`，'
           '本条在结构上恒不命中——理由同 `address_missing`。盘上 0/3')

_hard('canon_chapter_refs_dangling', '规则版本与 secret 时间线章号都在正文集合内', 'R-2.21',
      fix=FIX_RESAMPLE,
      note='覆盖 world_rules[].versions[].effective_from_chapter、secret setup.chapters 与 '
           'first_reveal_chapter。它们都直接决定逐章下发；越界值会让一条事实'
           '永不生效/永不揭示。relation、knowledge、address、evidence 与 conflict stage '
           '各由自己的专门规则守，不在这里重报。人工可用 GlobalEdit v3 0 LLM 修复')

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

_hard('secret_known_by_dangling', '知情事件的 typed 主体与起章都有效', 'R-2.9c',
      fix=FIX_RESAMPLE,
      note='CharacterRef 必须能在 registry 里解析，GroupRef 的自由文本 label '
           '（「在场士兵」）放行；`known_from_chapter` 必须落在正文章集合。'
           '这与 `Address.by` 故意相反：那边要派发键，这边群体本身就是答案。'
           '人工可用 typed `knowledge_events` 经 `--apply-edit` 0 LLM 修复')

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

_hard('relation_dangling', '关系两端、世内窗口与读者 visibility 都关闭式有效', 'R-2.8',
      fix=FIX_RESAMPLE,
      note='两端必须是 registry char_id；valid_from/valid_through 与 public.from_chapter '
           '必须在正文章集合且窗口有序；via_secret 必须引用现有 secret。'
           '这些值直接决定 Stage4 是否下发，悬空时不能退回启发式猜。人工可用 relation '
           'typed `--apply-edit` 0 LLM 修复')

_info('relation_visibility_mismatch', '显式 relation visibility 与旧启发式交叉对账', 'R-2.19',
      note='显式 visibility 是生产权威；旧的人名 + 隐瞒窗口启发式保留为独立 INFO 探针。'
           '不一致必须出声，但 INFO 不能改生产 gate')

_hard('global_evidence_unlocated', '自由事实证据能唯一回取为精确 SourceSpan', 'R-2.20',
      note='模型只交章号 + 短 locator；合法指定章先按原样/内容字符归一唯一回取，'
           '仅零命中时允许全书逐章扫描且全书唯一才纠正章号。章内多命中、全书零/多命中、'
           '非法章与正式 span 漂移仍落 source=null + 正向失败账。agent 首轮只命中本条时'
           '走同会话 hash-bound evidence patch；终态后由 typed GlobalEdit / `--revalidate` '
           '0 LLM 收口，显式 `--resample` 只作最后手段，不再作为报告默认修复路由')

# ═══ 五、连续开篇范围（1 HARD）═══════════════════════════════════════════

_hard('opening_scope_valid', '连续开篇范围具有合法终点及该章边界证据', 'R-2.14',
      note='只验证章域与坐标，不推断语义范围；unresolved 保持 UNVER，由 Global Commander '
           '通过 source_blurb_replace typed 编辑提出范围，再审阅接受。没有第 1 章 fallback；'
           '错误提案可由 --apply-edit 或 .INVALID + --revalidate 零模型修复。')

# ═══ 六、拍与区间（6 HARD · 1 INFO）═══════════════════════════════════════

_hard('plot_contract', '完整剧情节点、稀疏状态与约束引用闭合', 'R-3.13', fix=FIX_REGEN,
      note='验证当前 V9 typed 结构；不从结构合法推导剧情完整或正确归因。')
_hard('plot_evidence', '共享证据锚点绑定当前源章与段落索引', 'R-3.2', fix=FIX_REGEN,
      note='源 SHA、段范围、字符坐标及必要字面一致；不以覆盖率证明语义覆盖。')
_hard('plot_state_continuity', '章首状态有来源，解除与取代后不再携出', 'R-3.13', fix=FIX_REGEN,
      note='跨章状态引用前章携出集合，不复制或永久累计历史动作。')
_hard('plot_global_refs', '状态的共享主体引用属于本章实际提供的 Global 投影', 'R-3.13',
      fix=FIX_REGEN)
_hard('present_ids_dangling', '参与者、在场者及叙述身份均来自有据人物闭集', 'R-3.7b',
      fix=FIX_REGEN)
_hard('scope_violation', '每次逻辑抽取只返回请求章节', 'R-3.16b',
      note='scope_violations 必须显式记录，缺失不能当作空列表。')
_info('pronoun_surface_unattested', '叙述者自称字面有本章原文依据', 'R-3.14b')
_metric('plot_shape', '完整节点、状态、约束与共享证据数量', 'R-3.13',
        note='只报告实际规模，不设节点配额，不把辅助结构变回事实数据库。')
_metric('context_scope', '实际逐章抽取契约与正文边界', 'R-3.16b')
_unver('plot_semantics', '完整剧情、归因、因果与揭示经语义审阅', 'R-3.13',
       note='普通 schema/坐标检查不能证明语义完整；由章审和跨章复核明确裁决。')
