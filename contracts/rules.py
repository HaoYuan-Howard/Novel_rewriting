#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules.py —— 判据注册表。**判据表只有一张**，key 重复当场炸。

【每条判据必须说出自己在满足哪条需求】`requirement` 是必填的。
没有这一列，「关卡的判据必须跟着被它检查的契约一起改版」就无从执行——
契约改了而关卡没改，关卡**不会报错**，它会继续输出一个看起来正常的结论。
原型实测过一次：四条确定性检查在新契约下**三条静默失效、一条全量误报**（结论恒为「需修订」）。
两种故障方向相反、危害相同：**报告不再携带信息**。

【注册期就校验，不等到跑的时候】七条，每条都对应一次已发生的损失，见 `_validate`。

【注册表现在是空的，这是对的】判据随各 stage 移植过来时才注册。
`PORTED_STAGES` 声明「哪些 stage 已经移植完」——**正向覆盖率只对已移植的 stage 生效**，
否则第一天就是满屏红，而一条从一开始就红的测试等于没有测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Literal

from contracts.invariants import OWNER, SEVERITY, WHO_CHECKS_RULE

#: ⚠ 比 `WHO_CHECKS` 少四个（`CARRIER`/`STRUCTURAL`/`DELEGATED`/`N_A`）——
#: **一条判据的存在本身就否定了那四个**。取值面与理由的唯一落点在 `invariants.WHO_CHECKS`。
WhoChecks = Literal['HARD', 'INFO', 'GATE', 'METRIC', 'HUMAN']
RepairBucket = Literal['UNIQUE', 'FREE', 'NONE']

#: `RepairBucket` 的运行时取值面。⚠ `Literal` 在运行时不拦任何东西（同 `WhoChecks`），
#: 而这一格写歪的后果是**一次付费自救的准入被静默放宽**。
#:
#: 【三个取值答的是同一个问题：满足这条判据的路径唯一吗】
#:   · `UNIQUE` = 目标字串被上游钉死（材料 / 称谓表 / 源章原话），或动作就是
#:     「删掉指出来的那个」——写手只能放进去或拿掉，**编不出第三种答案**；
#:   · `FREE`   = 密度 / 比率 / 档位。满足它的路径有无穷多条，而**最省力的那条
#:     往往是删内容或编内容**。实测：某章为满足「对白变少了」（引语 7→11 句）
#:     **凭空编了一句台词**，源章 0 次且语义讲不通，而第二轮全部 4 条判定
#:     **没有一条指向它**，报告还记它「消掉 2 条」；
#:   · `NONE`   = 不进反哺。
#:
#: ⚠⚠ **「进且只进一张桶」在这里是结构上成立的**（`R-8.6`）：原型把它写成
#: `REPAIR_UNIQUE` / `REPAIR_FREE` **两张 dict**，于是要另配一个
#: `assert_repair_admission()` 去查「每条 actionable 判据恰好落进其中一张」。
#: 收成**一个字段的三个取值**之后那道断言没有服务对象了——一条判据不可能同时
#: 在两张表里，也不可能一张都不在。
REPAIR_BUCKETS: Final = ('UNIQUE', 'FREE', 'NONE')

_REQ_RE = re.compile(r'^R-\d+\.\d+[a-z]?$')

#: 已经移植完、判据应当齐备的 stage。移植一层加一层。
#: ⚠ 加进来之前先确认那一层的判据真的全注册了：这张表一旦说谎，正向覆盖率就成了摆设。
#:
#: ⚠⚠ **`stage3` 是 2026-08-11（骨架 C 期）加进来的第一层**，加进来那一刻同时发生四件事：
#: `test_registry_starts_empty` 红（已换成两侧夹死的
#: `test_registry_covers_exactly_the_ported_stages`）· 正向覆盖率从 skip 变真 ·
#: `ask_json` 调用点守卫从 skip 变真 · `test_no_rule_claims_a_non_judge_requirement` 变真真。
#: **那是好事**——它们本来就欠着，只是 `REGISTRY` 空着的时候一条都红不了。
#:
#: ⚠ **`stage6` 是 F 期第一个移植进来的层**（2026-08-11）。选它而不是 stage2 的理由
#: 与 C 期选 stage3 当第一条纵切同构：**按验证价值挑，不按管线顺序挑**——
#: 它是七层里最便宜的一层，用来把「加一层」那张 11 步清单走通一遍。
#: ⚠ **`stage7` 是 F 期第二层**（2026-08-12）。它是本仓第一个**一个 stage 三个节点**的层
#: （`stage7.rename` / `stage7.assemble` / `stage7.approve`），于是「一层一个跑法」
#: 那几张按 **stage** 索引的表（`_runners` / `_checkers`）第一次要在函数体里再按
#: `ctx.node` 分一次岔——**那不是新机制，是 F0 定的那张查表的自然形态**。
#: ⚠ **`stage4` 是 F 期第三层**（2026-08-12）。它是本仓第一个**一层两份产物、
#: 两节需求**的层（`R-5` 计划 + `R-6` 组装），于是正向覆盖率第一次要取两节的并集
#: ——`STAGE_SECTIONS` 早就是元组了，那一格终于有了真消费方。
#: ⚠⚠ 它也是第一个**徽章能真的是 `pass`** 的层：判据表里没有一条 `sev='unver'`
#: （stage6/stage7 各有 8/7 条常驻 `unver`，结构上永远最好只有 amber）。
PORTED_STAGES: frozenset[str] = frozenset({'stage3', 'stage4', 'stage6', 'stage7'})


@dataclass(frozen=True)
class Rule:
    """一条判据。字段全部必填的那几个是有理由的，见 `_validate`。"""

    rule: str                       # 判据 id，全局唯一
    stage: str                      # 产出这条结论的 stage
    item: str                       # 人读的一句话
    sev: str                        # SEVERITY 之一
    who_checks: WhoChecks           # 它在需求表「谁在查」那一列里对应什么
    requirement: str                # 指向 R-x.y。**必填**

    owner: str | None = None        # 根因第一嫌疑层；OWNER 之一
    repair: RepairBucket = 'NONE'   # 进不进付费自救。取值面见 REPAIR_BUCKETS
    quote: bool = False             # 凭据能不能原样引用给写作节点
    wordlist: bool = False          # 它是不是词表类判据
    baseline: str | None = None     # 词表类判据的基线命中率。wordlist 时必填
    note: str = ''

    #: **发回写手的那句话**——「这一条要怎么改」。进反哺的判据必填，其余必须留空。
    #:
    #: 【为什么它长在判据表上，而不是在反哺那一侧现写】`_provenance.fixes` 存的是
    #: `[[rule, evidence_key]…]` **引用**，不是渲染后的指令文本（`stage5.md` §5.1）——
    #: 存第二份就会跟着表漂：判据表改了措辞而存量产物里那份没有，
    #: 而**下一轮到底发了哪句话**就再也说不清了。⇒ 指令由本字段生成，唯一落点。
    #:
    #: 【留空是被下面那条不变量逼出来的，而它是对的】写一条**永远不会被发出去**的指令，
    #: 就是「退役的描述会以现役身份被引用」那个形状——下一个人读到它会以为反哺在修这条。
    #: 真有那句话就写进 `note` 当注释（原型 `dash_drift` 就是这么处理的）。
    directive: str = ''


@dataclass(frozen=True)
class RetiredRule:
    """一条**退役而不删**的判据。

    ⚠ 退役的是**判据**，不是**要求**：stage3 的 `cliche` 撤了，而 `STYLE_SYS` 里那句
    「不要用『语言流畅／节奏明快』这类放之四海皆准的话填格」**仍在下发**——
    那张九词表因此没有任何现役消费方，是判据 35 的教科书实例
    （一条只写在 prompt 里的约束，遵守率没人在测）。
    ⇒ 它必须在报告里**常驻一行**，好让「量过、量不准，所以撤了」与「忘了」读起来不一样。

    `why` 必填：写不出理由的退役与「悄悄删掉」在盘上没有区别。
    """

    rule: str
    stage: str
    why: str


class Registry:
    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}
        self._retired: dict[str, RetiredRule] = {}

    def register(self, rule: Rule) -> Rule:
        if rule.rule in self._rules:
            raise ValueError(
                f'判据 id 重复：{rule.rule!r}。判据表只有一张，重名当场炸——'
                f'两条同名判据会让「这一条报了没有」永远说不清'
            )
        if rule.rule in self._retired:
            raise ValueError(
                f'判据 {rule.rule!r} 已登记为退役，不许再注册成现役。'
                f'要复活它就先把那条退役登记撤掉——**复活是一次裁决，该被看见**'
            )
        _validate(rule)
        self._rules[rule.rule] = rule
        return rule

    def retire(self, retired: RetiredRule) -> RetiredRule:
        """登记一条退役判据。**一个 id 不许同时又活着又退役。**

        那种状态下报告会既列一行判定又列一行「已退役」，而人只会信其中一行。
        """
        if not retired.why.strip():
            raise ValueError(f'{retired.rule}: 退役必须写出理由——没有理由的退役等于悄悄删掉')
        if retired.rule in self._rules:
            raise ValueError(
                f'{retired.rule}: 它同时是现役判据和退役判据。'
                f'报告会既列一行判定又列一行「已退役」，而人只会信其中一行'
            )
        if retired.rule in self._retired:
            raise ValueError(f'{retired.rule}: 退役判据重复登记')
        self._retired[retired.rule] = retired
        return retired

    def retired_of_stage(self, stage: str) -> tuple[RetiredRule, ...]:
        return tuple(r for r in self._retired.values() if r.stage == stage)

    def __contains__(self, rid: str) -> bool:
        return rid in self._rules

    def __len__(self) -> int:
        return len(self._rules)

    def get(self, rid: str) -> Rule:
        """按 id 取一条。**取不到当场炸，不返回 `None`。**

        ⚠ 这是 `finding()` 工厂唯一的入口。返回 `None` 会让「造一条没有判据表撑腰的
        finding」变成一次静默的 `AttributeError`（或更坏：一条 `sev=None` 的结论），
        而本表存在的全部意义就是**一条 finding 不可能与判据表说的不一致**。
        """
        try:
            return self._rules[rid]
        except KeyError:
            raise KeyError(
                f'判据 {rid!r} 不在注册表里。判据表是唯一落点——'
                f'要么这个 id 拼错了，要么这条判据的【行】还没写'
                f'（判据的行在 `contracts/judges/stageN.py`，与它守的契约同层）'
            ) from None

    def all(self) -> tuple[Rule, ...]:
        return tuple(self._rules.values())

    def of_stage(self, stage: str) -> tuple[Rule, ...]:
        return tuple(r for r in self._rules.values() if r.stage == stage)

    def requirements_covered(self) -> set[str]:
        return {r.requirement for r in self._rules.values()}


def _validate(r: Rule) -> None:
    """注册期校验。七条，每条对应一次已发生的损失。"""

    # ① 取值域闭合。`dict.get(key, 默认值)` 用在契约枚举上，就是给违约数据发一张静默通行证。
    if r.sev not in SEVERITY:
        raise ValueError(f'{r.rule}: sev={r.sev!r} 不在 {SEVERITY}')
    if r.owner is not None and r.owner not in OWNER:
        raise ValueError(f'{r.rule}: owner={r.owner!r} 不在 {OWNER}')
    # `Literal` 在运行时不拦任何东西——它只是给类型检查器看的。取值面要真闭合就得在这里查
    # （同判据 26：枚举上的兜底就是给违约数据发一张静默通行证）。
    if r.who_checks not in WHO_CHECKS_RULE:
        raise ValueError(
            f'{r.rule}: who_checks={r.who_checks!r} 不在 {WHO_CHECKS_RULE}。'
            f'`CARRIER`/`STRUCTURAL`/`DELEGATED`/`N_A` 那四个不许出现在判据这一侧——'
            f'一条判据的存在本身就否定了「载体在守 / 结构在守 / 转授 / 不适用」'
        )
    # `metric` 与 `METRIC` 必须成对：一条无条件产出的度量写成 `INFO`，
    # 就是把度量重新并回告警那一支（判据 21）。
    if (r.sev == 'metric') != (r.who_checks == 'METRIC'):
        raise ValueError(
            f'{r.rule}: sev={r.sev!r} 与 who_checks={r.who_checks!r} 对不上。'
            f'度量在两侧必须同时是度量——否则「无条件报出的读数」与「一条会命中的结论」混掉'
        )

    # ② 每条判据必须说出它在满足哪条需求。
    if not _REQ_RE.match(r.requirement):
        raise ValueError(
            f'{r.rule}: requirement={r.requirement!r} 形如 R-8.2 才对。'
            f'一条不指向任何需求的判据，改版时没有任何东西会提醒你它该跟着改'
        )

    # ③ 自救准入的取值面要真闭合（`R-8.6`）。`Literal` 只给类型检查器看，
    #    而这一格写歪的后果是**一次付费自救的准入被静默放宽**：`repair='unique'`
    #    （小写）今天会原样存进去，然后 `!= 'NONE'` 判真——它会花钱。
    if r.repair not in REPAIR_BUCKETS:
        raise ValueError(
            f'{r.rule}: repair={r.repair!r} 不在 {REPAIR_BUCKETS}。'
            f'这一格答的是「满足这条判据的路径唯一吗」，写歪一个值就是给一条'
            f'路径不唯一的判据发一张付费自救的通行证'
        )

    # ④ 「可反哺」蕴含「根因在写作层」—— 根因不在写手身上的条目，重写一百遍也修不好。
    if r.repair != 'NONE' and r.owner != 'stage5':
        raise ValueError(
            f'{r.rule}: repair={r.repair} 但 owner={r.owner!r}。'
            f'根因不在 stage5 的条目进反哺 = 花钱让写手去修一个它修不了的东西'
        )

    # ⑤ 「查不了」不许进反哺，也不许假装自己是别的。
    if r.sev == 'unver' and r.repair != 'NONE':
        raise ValueError(f'{r.rule}: sev=unver 的判据不许进反哺——它连有没有问题都不知道')

    # ⑥ 词表类判据必须报出自己的基线命中率。
    #    没有基线数字的词表判据，等于不知道自己在说什么：实测一张「揭示标记」词表
    #    在 21/22 章命中，与那一章有没有点破什么无关——它的「通过」分支是
    #    **一盏保证会亮的绿灯**，报告照常输出、只是不再携带信息。
    if r.wordlist and not r.baseline:
        raise ValueError(
            f'{r.rule}: 词表类判据必须填 baseline（它在多少章/多少条上命中）。'
            f'有判别力的往往只有它的反面——一个标记都没有＝反常'
        )

    # ⑦ 指令与自救准入**双向**对齐（原型同款断言 `bool(directive) == actionable`）。
    #    两个方向各消灭一种故障：
    #      · 进反哺却没有指令 ⇒ 那一轮会把一条**空指令**发给写手（花了钱、没说要改什么）；
    #      · 不进反哺却写着指令 ⇒ 一句**永远发不出去**的话留在表里，
    #        下一个人读到它会以为反哺在修这一条。
    if bool(r.directive) != (r.repair != 'NONE'):
        raise ValueError(
            f'{r.rule}: repair={r.repair!r} 而 directive='
            f'{"有" if r.directive else "空"}。进反哺的必须写出「这一条要怎么改」，'
            f'不进的必须留空——一句永远发不出去的指令会被下一个人当成现役的'
        )


#: 全局注册表。各 stage 的判据在移植时 import 它并 `register(...)`。
REGISTRY = Registry()
