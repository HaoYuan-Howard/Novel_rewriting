#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage6.py —— 开篇文案那一层的判据行。源是 `docs/schema/stage6.md` §四（**唯一落点**）。

**25 条现役：5 HARD · 9 INFO · 3 metric · 8 unver。** 外加 1 条退役而不删。

【判据 id 一律照抄文档，不加 stage 前缀】它们是与 `wontfix.json` 的键、
与 `_provenance.fixes` 的引用之间的契约——加个前缀就等于把存量豁免全部作废。

【落点账已经平过】F1-0 审计：25 条里有 **9 条**原本落不了地——5 条落点写着
`Q-1`/`Q-4`/`Q-5`（`_REQ_RE` 拒收，**import 期当场炸**）、4 条指着写有
「无守卫 → `B-12`」的 `R-9.7`/`R-9.8`（会被判说假话）。已补 `R-9.15`~`R-9.18`
收下它们，`Q-x` 降为下面的 `note`。⇒ 每一条的 `requirement` 都是核过的，不是猜的。

【两条本层特有的裁决，都在这里生效】

**① 全表 `repair='NONE'` ⇒ `actionable` 全 `False`。**
本层一条付费自救都不开（已经有更便宜的路：`--pick N` 换一条候选、`--apply-edit`
改一个字，都是 0 成本）。原型这里标着 `actionable=True` 12 条，那是**另一个意思**的
actionable（「人能 0 成本改掉」而不是「写作层能修」）。裁决是**不放宽载体侧那两条
不变量**，那 12 条的「人怎么改」写进 `note`——理由见 `docs/schema/_report.md` §2.1。

**② 全表不设 `quote`。** `Rule.quote` 答的是「凭据能不能原样引用**给写作节点**」，
而本层根本没有反哺写作层那条路（见①）。且它今天**全仓零消费方**——
给一个没有消费方的语义标上值，与 `actionable` 是同一条病。

⚠⚠ **由此推出一条要写进 README 的结论**：8 条 `unver` 每次都走
`findings.unverifiable(...)`（`builder._status_of` 强制），而 `badge_of` 见
`unver` 就至少 amber ⇒ **stage6 的徽章在结构上永远不可能是 `pass`**，
最好情况是 `amber`（`passed=True`、退出码 0）。**这不是缺陷**，
是本层「能查的都查了，查不了的写在脸上」的可见形态。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, RetiredRule, Rule

STAGE = 'stage6'


def _hard(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=STAGE, **kw))


def _info(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=owner, **kw))


def _metric(rule: str, item: str, requirement: str, **kw) -> Rule:
    """度量：**无条件报出**，`owner=None`（它不是一条结论，没有「根因第一嫌疑层」）。"""
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


def _unver(rule: str, item: str, requirement: str, *, who_checks: str, **kw) -> Rule:
    """查不了的：**永远输出**，不是「还没写完」。

    ⚠ `who_checks` 必须显式给，因为这 8 条分**两族**，而它们指向的需求行记号不同：
      · `HUMAN` —— 真正由人把关的那 4 条（`R-9.18` / `R-9.6` 两行都标着 `人`）；
      · `INFO`  —— 判定**前提**不成立时出声的那 4 条（`R-9.16` / `R-9.17` 标着 `INFO`）。
    合成一个默认值，其中一族就会指着一个说着别的话的需求行。
    """
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='unver',
                                  who_checks=who_checks,          # type: ignore[arg-type]
                                  requirement=requirement, **kw))


# ── 五条阻断级 ──────────────────────────────────────────────────────────────

_hard('hook_empty', '文案不是空的或残的', 'R-9.13',
      note='内容字符 < 20 就早退，**其余判据一条都不跑**——空文案上的判定全是噪声。'
           '盘上 0/9。⚠ 失效模式一次都没发生过，**一个会让它红的样本都没有**')

_hard('hook_truncated', '结尾是完整句', 'R-9.13',
      note='末字 ∈ `core.text.TERMINAL_PUNCT`。盘上 0/9，同样零反例样本')

# ⚠ **它守的是【人】，不是模型。** 判据的基准是原作文案段，而那份按设计**不下发**
# （样例会被模仿，`hook_style` 存在的全部意义就是把「怎么写」抽象出来）⇒ 模型看不见它，
# 就不可能复用它 ⇒ 这条 HARD 对模型**结构上不可能红**。会拿着原作文案去抄的是走
# `--apply-edit` 那条路的人。反过来，模型**看得见**的正文，对应判据只是 info、阈值 0.10
# ——**严格程度与「模型能不能违反」正好反着来**。
# ⚠ 不降级、不拆条：恒等基线整个建立在它上面（原作 vs 原作必然 100%，所以恒等跑里
# 它被单独剔除），拆成两条就要给 `--apply-edit` 另立一套判据，而「三条路共用同一条
# 归一+判定」是本层最值钱的一条纪律。处置只有一条：**`item` 说出主语与基准**（判据 32）。
_hard('hook_reuses_source', '走 --apply-edit 的人没有复用【原作文案段】的句子', 'R-9.11',
      note='与原作文案段的 20 字窗口重合率 ≤ 2%，**无豁免**（文案里一句逐字要求都没有）。'
           '盘上 0/9。⚠ 对**模型**结构上不可能红——它看不见原作文案段')

_hard('hook_invented_name', '人物称谓都在下发面里有据', 'R-9.12',
      wordlist=True,
      baseline='盘上 9 候选 0 命中。⚠ **召回有缺口**：用没出现过的姓造人（「钱姑娘」）'
               '抓不到 · 不带称谓后缀的光名字（「小翠」）抓不到 · 地名物名完全不查',
      note='词表是 `core.naming.NAME_TITLE`（十个称谓后缀），左锚是本书自己的名字用字。'
           '⚠ 下发面在剧透上界改版后**变宽了**（secrets 全量下发），名字自动有据，'
           '判别力随之下降——**别把 0 命中读成「没编人名」**。'
           '〔人能 0 成本改掉：`--apply-edit` 把那个称谓改回已有角色〕')

# **唯一一条「绿得有理由」的 HARD**：归一化真的动过手（盘上 9 个候选里 6 个
# `paras_fixed=1`），而它 0 命中 ⇒ 后置条件确实在成立。其余四条 HARD 的 0 命中
# 都只说明「那种失效没发生过」，只有这一条说明「有个机制在起作用」。
_hard('hook_para_sep', '段间分隔档位 == 原作文案段的档位', 'R-9.3',
      note='**后置条件**：`normalize_hook` 已经归一过了，这条亮了说明归一没跑或坏了。'
           '它报绿的信息量全在 `mech.paras_fixed` 里。'
           '⚠ 两边都按**字节**取档位（`core.bands.sep_band_of_text`），'
           '不碰 `paragraph_sep_chars`——那一维有 off-by-one，短文案判反。盘上 0/9')


# ── 九条提示级 ──────────────────────────────────────────────────────────────

_info('hook_transcribes_body', '不整段誊抄开篇正文', 'R-9.11',
      note='与开篇正文的 20 字窗口重合率 ≤ 0.10。'
           '**这一条才是模型那一侧**：文案本来就要复述正文的事，偶有短语重合正常，'
           '整段誊抄才是问题。盘上 0/9')

_info('hook_invented_number', '【数字+单位】都在下发面里有据', 'R-9.12',
      wordlist=True,
      baseline='盘上 9 候选 0 命中。词表是 `core.facts.NUM_UNIT` 的单位表'
               '（年岁月成倍斤里品级重刻鞭板次回遍），裸数字与不定冠词「一」不计',
      note='与 `hook_invented_name` 同一个形状：**词干 + 类别标记**。'
           '〔人能 0 成本改掉：`--apply-edit` 删掉那个数字〕')

_info('hook_arc_literal', '结局/高潮的字面片段没进文案', 'R-9.9',
      note='**改版后唯一还禁的那道**。`secrets[].fact` 全书下发、**允许说破**，'
           '只有 `arc.climax`/`arc.ending`/`arc_tracks[].trajectory` 不许写。'
           '⚠ 它是**字面**探针：改写式说出结局它挡不住，那一半靠 `R-9.6` 的人定稿。'
           '切法与 `hook_downsend_leak` 共用 `core.facts.secret_keywords`。盘上 0/9。'
           '〔人能 0 成本改掉：`--apply-edit` 改掉那一句〕')

_info('hook_length', '篇幅贴合本书自己的文案段', 'R-9.14',
      note='∈ (0.6, 1.6) × 原作文案段字数。'
           '⚠ **必须用产出 `measurable.hook` 的那把尺子来量**——三个口径实测互不相同'
           '（某本 `measure()[chars]`=319 / `len(norm())`=274 / `len(strip())`=347），'
           '拿其中一个去比另一个算出来的基准，错的不是某个数，是那个**跨口径的运算**，'
           '而它不会报错，只会让整条判据系统性偏 15%。盘上 0/9。'
           '〔人能 0 成本改掉：`--pick` 换一条候选〕')

_info('hook_shape', '段落形态贴合本书自己的文案段', 'R-9.14',
      note='段均字数 ∈ (0.5, 2.0) × 原作文案段。盘上 **1/9**（三岁那本 identity_contrast）。'
           '实测动机：三个候选全被写成**一整段**（162/178/185 字/段），'
           '而本书文案段是 16.4 字/段（一句一段）。〔人能 0 成本改掉：`--pick`〕')

_info('hook_quote_style', '引号体与本书一致', 'R-9.14',
      note='盘上 0/9。归一化通常已经修掉了，剩下的是原作文案段本身缺引号的情形。'
           '〔人能 0 成本改掉：`--apply-edit`〕')

_info('hook_quote_unbalanced', '引号配对异常，留着报不静默修', 'R-9.3b',
      note='与 stage5 的 `quote_unbalanced` 同型。**归一化拒绝猜**：配对失败时整段原样返回，'
           '问题落到这一条上。⚠ 盘上 **0/9 且恒缺格**——它晚于那三份产物，'
           '`baselines_identity` 因此只有 12 格而不是 13（见 `stage6.md` §四）。'
           '〔人能 0 成本改掉：`--apply-edit` 补上那半个引号〕')

_info('hook_ending_shape', '收尾形态与原作文案段同类', 'R-9.4',
      note='∈ `invariants.ENDING_SHAPES` 四类。这是 `hook_style.end_question`'
           '（一条**自由文本**描述，逐书下发）**唯一可确定性检测的投影**。'
           '⚠ 实测两本的 `end_question` 与成品直接违反，而全链一处没出声（判据 35）；'
           '更值得记的是：**合规的那个候选就在同一份产物里**，人定稿时选了违规的那个'
           '——没有判据，人也没有依据去挑。盘上 **2/9**（头牌那本两条候选）。'
           '〔人能 0 成本改掉：`--pick` 换那条合规的〕')

# ⚠ owner 是 **stage3**，不是本层：混进来的结局字面来自它下发的画像。
# 判据 27：一份「已经在检查」的守卫，要问它开在**哪扇门**上——这一条开在下发面上，
# 是 `_voice_for_hook` 那两处收窄之外的兜底。
_info('hook_downsend_leak', '下发面自己没有混进结局', 'R-9.10', owner='stage3',
      note='回扫拼好的 prompt。取值面与 `hook_arc_literal` 共用 `_arc_sources`，'
           '因此**判别力同样有限**：命中就是真漏，不命中不代表干净。盘上 0/3 本')


# ── 三条度量：**无条件报出**，不被 HARD 抑制 ────────────────────────────────
#
# ⚠⚠ 原型的复检把 `sev='metric'` **整族丢掉了**（`elif f['sev'] == 'info'`）：
# 它们既不作为命中出现、也不作为 `[OK]` 出现——**在那份报告里等于不存在**，
# 而 `hook_quote_shape` 正是 `R-9.5` 的唯一落点。
# 更坏的是它只丢在一条路上：界面的 stage6 页走另一条路、**照常显示** metric
# ⇒ 同一个数字在一个界面上有、在另一个界面上没有，取决于人点了哪个 tab。
# ⇒ 新骨架的 `build()` 遍历**注册表**而不是遍历产出，这条**结构上不可能再发生**。

_metric('hook_report', '下发范围 · 各候选字数 · 原作文案段字数', 'R-9.15',
        note='⚠ `candidates[].chars` 已被新契约裁掉（能 0 成本现算的字段不落盘），'
             '所以这三个数**一律现算**，不许读产物')

_metric('hook_quote_shape', '引语条数 · 最长一条字数 · 对白比，与原作并排', 'R-9.5',
        note='**度量不是门，本轮不给阈值。** 实测（五本 × 三候选 + 七本原作文案）：'
             '平均最长引语 **原作 22.6 字 → 候选 6.8 字**，条数 2.71 → 1.20，'
             '**12/12 同向**。根因不在模型：`HOOK_SYS` 的两条硬禁**联合**把引号台词掐死了'
             '——「只引用正文真实出现过的句子」（判据已退役成 `hook_quote_unver`，'
             '而 prompt 那句没撤）＋「不许复用正文里读到的句子」（HARD，在执行）。'
             '模型只能遵守被执行的那一半，于是少引、短引。'
             '⇒ 报出两边的数字，把**因**写在结论里，等 prompt 改完再谈门（判据 29）')

_metric('hook_secret_literal', '与秘密原文的逐字重合条数（按读者时点分已揭示/未揭示）', 'R-9.15',
        note='盘上 9/9 候选恒 **0 条**。⚠ **0 条是常态，不是「卖点没用上」**：'
             '`secret_keywords` 切的是整个分句，而文案按体裁在改写，'
             '字面探针在这个产物类上**结构性失效**（同 `hook_spoiler_unver`）。'
             '⚠ 非 0 条也**不是违约**：说破秘密现在是授权的（`R-9.9`），这条只是记账。'
             '⚠ 凭据必须经 `core.text.locate_norm` 回映成**文案自己的字节**——'
             '命中的片段来自 `secret.fact`，直接拿它当凭据展示等于把秘密原文抄进报告')


# ── 八条查不了的：**永远输出**，不是「还没写完」 ────────────────────────────
#
# 这 8 条与 5 条 HARD 加在一起才是本层真实的防线图：**能查的都查了，查不了的写在脸上。**
# 省略掉，读报告的人会默认它们守住了。

_unver('hook_spoiler_unver', '「不说破任何答案」被有效检查过', 'R-9.18', who_checks='HUMAN',
       note='⚠ 它不是「还没来得及做」，是**已实测判别力为零**：字面探针抓不到改写式泄底。'
            '`R-9.7` 那一行写着「无守卫 → `B-12`」，说的正是同一件事')

_unver('hook_quote_unver', '「引语在正文里有着落」被检查过', 'R-9.18', who_checks='HUMAN',
       note='⚠ **这是恒等基线否掉的一条判据，不是漏写的。** 曾有一条 hard：'
            '「文案里引号内的句子必须能在正文里找到」。拿两本原作文案跑基线：'
            '一本报 1 句、一本报 6 句，全都「找不到」；进一步查**原作全书**，'
            '这 7 条引语逐字命中 **0 章**，最长公共子串只有 8%~29%。'
            '⇒ **原作文案里的台词，原作正文里根本不存在**——那是文案作者浓缩、改写、'
            '乃至重编的，是这个体裁的常规手法。按它去修数据会让产物变差（判据 20）'
            '⇒ **判据错，不是数据错。**')

_unver('hook_relations_unver', '「没歪曲人物关系」被检查过', 'R-9.18', who_checks='HUMAN',
       note='`R-9.8` 写着「无守卫 → `B-12`」。⚠ 这条要求曾与同一份 prompt 的另一条硬禁'
            '互相打架（「不复用原句」，后者有阻断级在执行），实测代价是'
            '最长引语 22.6 → 6.8 字、12/12 同向。打架的那句已删，口径改成'
            '「不许发明事实，必须发明文字」。⇒ **一条要求在被判定为「无守卫」之前，'
            '先确认它本身是对的**')

_unver('hook_not_approved', '有人给这份文案定过稿', 'R-9.6', who_checks='HUMAN',
       note='`R-9.6` 那一格是 `人`——**定稿是现役的唯一闸门**，文案好坏没有确定性判据。'
            '本条由复检产出，不落产物。盘上三本都已定稿 ⇒ 0 命中')

_unver('hook_no_secrets', '这本书有可查的秘密', 'R-9.16', who_checks='INFO',
       note='stage2 一条 secret 都没抽到 ⇒ **下发面里没有卖点可给**，'
            '这本书的文案只能靠开篇那几章自己立钩子，先天比原作干瘪一截。'
            '**这是判定前提不成立，不是「查过没问题」**')

_unver('hook_secret_no_time', '每条秘密都说得出自己什么时候揭示', 'R-9.16', who_checks='INFO',
       note='缺 `reader_reveal_events` ⇒ 说不出它什么时候对读者揭示。'
            '⚠ 这**不再影响下发**（秘密现在全量下发），只影响 `hook_secret_literal` '
            '把它算进「已揭示」还是「未揭示」')

_unver('hook_body_missing', '开篇那几章都有成稿', 'R-9.16', who_checks='INFO',
       note='下发范围内某几章还没写 ⇒ 文案是照一份**不完整**的开篇写的。'
            '前提不成立要出声，不许静默按「有几章算几章」跑完')

_unver('hook_body_stale', '判的是照盘上这份正文写的那一版文案', 'R-9.17', who_checks='INFO',
       note='⚠⚠ **口径与原型不同，而这是被迁移逼出来的**：`judged[].sha1` 是**整文件**指纹，'
            '新契约裁过 stage5 产物的字段 ⇒ 迁移后它**必然**对不上，'
            '照原型退回整文件 sha1 会让盘上 2/3 本误报红。'
            '⇒ `consumed`（字段投影）判过期、`sha1` 只作**审计**；'
            '**没有 `consumed` ⇒ `unver`**（说不清），不是 fresh、也不是 stale。'
            '盘上：成年人/头牌无 `consumed` ⇒ `unver`；三岁 2/2 条有 ⇒ 判得动')


# ── 一条退役而不删（报告里常驻列出） ────────────────────────────────────────

REGISTRY.retire(RetiredRule(
    rule='hook_no_source', stage=STAGE,
    why='「这本书有原作文案段可比」。缺 `hook_copy.json` 已改**显式终态**'
        '（`R-1.12` 把无文案段的书挡在 stage1，本层不可能遇到），'
        '而它留着会造出 **6 盏假绿灯**：0 成本实测把原作文案段与 `style_hook` '
        '一起置空重跑，13 条 hard+info 打了 13 个 `[OK]`，其中 6 条**根本没跑**'
        '——它们全都挂在「有原作文案段」这个前提上。判据 31'))
