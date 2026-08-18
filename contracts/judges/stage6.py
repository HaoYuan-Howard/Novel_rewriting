#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 当前判据注册表。

机械检查守可用性、非抄写与形态；付费语义审判给出**带证据的矛盾线索**与吸引力排序。
剧透、时间压缩、合成场面和重写台词本身不构成违规。

⚠⚠ **本仓没有自动放行门。** 双盲 A/B 的合并决议恒是 ``UNVER``，因此语义层不产生任何
自动 ``PASS``/``FAIL``：唯一的收口是 actor / reason / 当前文本完整 SHA-256 绑定的
``human_verification``。这不是「还没做」——`evaluator profile` 那套标定装置已被裁决删除。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

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

    ⚠ `who_checks` 必须显式给，因为这 7 条分**两族**，而它们指向的需求行记号不同：
      · `HUMAN` —— 真正由人把关的那 3 条（`R-9.18` / `R-9.6` / `R-9.19` 都标着 `人`）；
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

# 原作文案现在下发给模型，因此该规则同时守模型与人工编辑：可学习结构，不能复用长句。
# 恒等基线仍需单独剔除它，因为原作与自身比较构造上必然 100%。
_hard('hook_reuses_source', '新文案没有复用【原作文案段】的长句', 'R-9.11',
      note='原作文案现在作为结构金标准下发；允许学信息次序、节拍与最后一击，'
           '但连续 20 个内容字符不得相同（窗口重合率必须为 0）。')

_hard('hook_invented_name', '人物称谓都在下发面里有据', 'R-9.12',
      wordlist=True,
      baseline='盘上 9 候选 0 命中。⚠ **召回有缺口**：用没出现过的姓造人（「钱姑娘」）'
               '抓不到 · 不带称谓后缀的光名字（「小翠」）抓不到 · 地名物名完全不查',
      note='词表是 `core.naming.NAME_TITLE`（十个称谓后缀），左锚是本书自己的名字用字。'
           '完整故事真相面已全量下发，已有角色名自动有据；词表探针仍有召回盲区，'
           '别把 0 命中读成「绝对没编人名」。'
           '〔人能 0 成本改掉：`--apply-edit` 把那个称谓改回已有角色〕')

# **唯一一条「绿得有理由」的 HARD**：归一化真的动过手（盘上 9 个候选里 6 个
# `paras_fixed=1`），而它 0 命中 ⇒ 后置条件确实在成立。其余四条 HARD 的 0 命中
# 都只说明「那种失效没发生过」，只有这一条说明「有个机制在起作用」。
_hard('hook_para_sep', '段间分隔档位 == 原作文案段的档位', 'R-9.3',
      note='**后置条件**：`normalize_hook` 已经归一过了，这条亮了说明归一没跑或坏了。'
           '它报绿的信息量全在 `mech.paras_fixed` 里。'
           '⚠ 两边都按**字节**取档位（`core.bands.sep_band_of_text`），'
           '不碰 `paragraph_sep_chars`——那一维有 off-by-one，短文案判反。盘上 0/9')


# ── 七条机械提示级 ──────────────────────────────────────────────────────────

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

_info('hook_length', '篇幅贴合本书自己的文案段', 'R-9.14',
      note='∈ (0.6, 1.6) × 原作文案段字数。'
           '⚠ **必须用产出 `measurable.hook` 的那把尺子来量**——三个口径实测互不相同'
           '（某本 `measure()[chars]`=319 / `len(norm())`=274 / `len(strip())`=347），'
           '拿其中一个去比另一个算出来的基准，错的不是某个数，是那个**跨口径的运算**，'
           '而它不会报错，只会让整条判据系统性偏 15%。盘上 0/9。'
           '〔人能 0 成本改掉：`--pick` 换一条候选〕')

_info('hook_shape', '段落形态贴合本书自己的文案段', 'R-9.14',
      note='段均字数 ∈ (0.5, 2.0) × 原作文案段。历史盘面曾有 **1/9** 命中。'
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

_metric('hook_fact_attribution', '人物赠与类事实的弱字面归因读数', 'R-9.15',
        note='抽取“人物 + 送/给/赠 + 对象”，与 Stage4 故事地图事件做同人物、同对象、'
             '同赠与词的字面并排。**低召回/低精度，只是度量，不是矛盾门**；'
             '未同字面命中不能自动判错，须交语义审判或人工核对。')

_metric('hook_quote_shape', '引语条数 · 最长一条字数 · 对白比，与原作并排', 'R-9.5',
        note='**度量不是门，当前不给阈值。** 报告把候选与原作文案的引语形态并排展示，'
             '供人工定稿时判断；不得把它提升成引语必须逐字来自正文的机械门。')

_metric('hook_secret_literal', '与秘密原文的逐字重合条数（按读者时点分已揭示/未揭示）', 'R-9.15',
        note='盘上 9/9 候选恒 **0 条**。⚠ **0 条是常态，不是「卖点没用上」**：'
             '`secret_keywords` 切的是整个分句，而文案按体裁在改写，'
             '字面探针在这个产物类上**结构性失效**，这里只保留为创作材料使用读数。'
             '⚠ 非 0 条也**不是违约**：说破秘密现在是授权的（`R-9.9`），这条只是记账。'
             '⚠ 凭据必须经 `core.text.locate_norm` 回映成**文案自己的字节**——'
             '命中的片段来自 `secret.fact`，直接拿它当凭据展示等于把秘密原文抄进报告')


# ── 三条语义判据：矛盾交人裁决，候选问题是提示，吸引力是读数 ──────────────

_unver('hook_semantic_contradiction',
       '定稿候选不改变人物身份、关系、关键因果或故事结果', 'R-9.19',
       who_checks='HUMAN', check_kind='semantic',
       note='⚠ **它曾经是 HARD**。命中 HARD 的两条路都要求候选处于 `contradiction`，'
            '而那个状态只由 automated FAIL 产生——自动放行门随 evaluator profile 整批'
            '下线后不可达，一条 Finding 都产不出来。继续挂 HARD 就只剩两条路：伪造一个'
            '绕开真实契约的反例，或给 `tests/test_hard_counterexamples.py` 那条双向守卫'
            '开豁免口，两条本仓都禁止。降为 `unver` 说的是实话——**矛盾要人自己看**：'
            'badge 是 amber（不是绿），修复路由仍是 `human`，收口只认 actor / reason / '
            '完整 subject SHA-256 绑定的 `human_verification`。'
            '剧透、时间压缩、合成场面和为真实关系重写台词不算矛盾。')

_info('hook_candidate_contradiction',
      '逐候选列出实质矛盾或审判不确定项，供定稿时避开', 'R-9.20',
      check_kind='semantic',
      note='证据必须是候选文案的连续原字节；只报告候选自己的问题，不因另一条干净候选'
           '存在而隐藏。')

_metric('hook_quality_rank',
        '候选吸引力六维评分、固定权重总分与匿名排序', 'R-9.21',
        check_kind='semantic',
        note='总分由代码计算：阅读欲 30% + 正文声线 20% + 具体场面 15% + '
             '期待承诺 20% + 文案技法 15% - 摘要感 10%；模型不能自报总分。'
             '剧透本身不加分也不扣分。')


# ── 三条工作流/前提状态：常驻输出 ──────────────────────────────────────────
#
# 与 HARD 一起构成本层真实防线图：能查的都查，工作流状态写在脸上。
# 省略掉，读报告的人会默认它们守住了。

_unver('hook_not_approved', '有人给这份文案定过稿', 'R-9.6', who_checks='HUMAN',
       note='定稿仍由人负责，但人现在会看到匿名质量排序，且批准路会拒绝矛盾、'
            '不确定或未经审判的候选。')

_unver('hook_body_missing', '开篇那几章都有成稿', 'R-9.16', who_checks='INFO',
       note='下发范围内某几章还没写 ⇒ 文案是照一份**不完整**的开篇写的。'
            '前提不成立要出声，不许静默按「有几章算几章」跑完')

_unver('hook_body_stale', '判的是照盘上这份正文写的那一版文案', 'R-9.17', who_checks='INFO',
       note='`consumed`（字段投影）判过期，`sha1` 只作审计。'
            '当前 `JudgedDraft` 必须携带 `consumed`，逐条正文投影不一致即 stale。')
