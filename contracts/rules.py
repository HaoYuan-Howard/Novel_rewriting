#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules.py —— 判据注册表。**判据表只有一张**，key 重复当场炸。

【每条判据必须说出自己在满足哪条需求】`requirement` 是必填的。
没有这一列，「关卡的判据必须跟着被它检查的契约一起改版」就无从执行——
契约改了而关卡没改，关卡**不会报错**，它会继续输出一个看起来正常的结论。
原型实测过一次：四条确定性检查在新契约下**三条静默失效、一条全量误报**（结论恒为「需修订」）。
两种故障方向相反、危害相同：**报告不再携带信息**。

【注册期就校验，不等到跑的时候】九条，每条都对应一次已发生的损失，见 `_validate`。

Stage3 已整层退役；注册表必须完整覆盖其余七个现役层。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from contracts.invariants import CHECK_KIND, OWNER, SEVERITY, WHO_CHECKS_RULE

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


@dataclass(frozen=True)
class Rule:
    """一条判据。字段全部必填的那几个是有理由的，见 `_validate`。"""

    rule: str                       # 判据 id，全局唯一
    stage: str                      # 产出这条结论的 stage
    item: str                       # 人读的一句话
    sev: str                        # SEVERITY 之一
    who_checks: WhoChecks           # 它在需求表「谁在查」那一列里对应什么
    requirement: str                # 指向 R-x.y。**必填**

    check_kind: str = 'mechanical'  # mechanical | semantic；与 sev/repair 正交
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

    #: **机器可读的修复路由**——「这一条要拿哪条跑法去修」。`(节点 id, flag…)`，
    #: 如 `('stage2.blueprint', '--renormalize')`。修不了 / 只能人改的留 `None`。
    #:
    #: 【为什么它不能是 `note` 里的一句散文】原型每条判据带的是结构化的
    #: `{'kind': 'stage2.blueprint', 'extra': ['--renormalize']}`：
    #: 「文案里那句『修法：…』是给人读的；**界面要执行它，就必须有一份机器可读的同源表达**，
    #: 否则只能去正则抠文案」。而散文那条路上**拼错一个 flag 名不会有任何东西出声**
    #: （LESSONS 第 11 条）。
    #:
    #: 【它与 `directive` 是两件事，别合并】`directive` 是**发给写手的一句话**
    #: （「你把这段改成…」），只有 `repair != 'NONE'` 的判据有；本字段是**一条要跑的命令**
    #: （「拿 `--renormalize` 重新定位一遍」），与花不花钱、进不进反哺都无关。
    #: stage2 全表 `repair='NONE'` 而 24 条有 `fix`——两格在这一层完全不重叠。
    #:
    #: 【收益是它能过 `is_paid`】界面据此把「0 成本修」与「要再花一次钱」画成两个颜色：
    #: `('stage2.blueprint', '--renormalize')` 免费，`('stage2.global', '--purge-role')` 付费。
    #: 那个判断走的是 `novel/orchestrator/nodes.py::is_paid`，**贵贱只有一处算**（`R-11.7`）。
    fix: tuple[str, ...] | None = None

    #: `fix` 里哪些带值 flag 的实参要由**本次 finding**提供。
    #:
    #: 路由属于判据，值属于某次命中：例如 `hinge_in_hole` 永远走
    #: `stage2.blueprint --chapters`，但章号只能由这次命中的 beat id 得出。
    fix_args_from_finding: tuple[str, ...] = ()

    #: Identifier namespaces that a model must be able to resolve from the
    #: prompt before this rule can fairly reject its response.
    needs_id_spaces: tuple[str, ...] = ()

    #: Registered prompt surface that is responsible for providing those IDs.
    prompt_surface: str | None = None


@dataclass(frozen=True)
class PromptSurface:
    """A named LLM downsend surface and the identifier joins it exposes."""

    surface: str
    provides_id_spaces: tuple[str, ...]


class Registry:
    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}
        self._prompt_surfaces: dict[str, PromptSurface] = {}

    def register_prompt_surface(self, surface: PromptSurface) -> PromptSurface:
        if surface.surface in self._prompt_surfaces:
            raise ValueError(f'prompt surface 重复：{surface.surface!r}')
        values = surface.provides_id_spaces
        if not surface.surface.strip():
            raise ValueError('prompt surface 名不得为空')
        if values != tuple(sorted(set(values))) or any(not value.strip() for value in values):
            raise ValueError(
                f'{surface.surface}: provides_id_spaces 必须非空白、去重并排序')
        self._prompt_surfaces[surface.surface] = surface
        return surface

    def register(self, rule: Rule) -> Rule:
        if rule.rule in self._rules:
            raise ValueError(
                f'判据 id 重复：{rule.rule!r}。判据表只有一张，重名当场炸——'
                f'两条同名判据会让「这一条报了没有」永远说不清'
            )
        _validate(rule, self._prompt_surfaces)
        self._rules[rule.rule] = rule
        return rule

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

    def prompt_surface(self, name: str) -> PromptSurface:
        try:
            return self._prompt_surfaces[name]
        except KeyError:
            raise KeyError(f'prompt surface {name!r} 未注册') from None

    def prompt_surfaces(self) -> tuple[PromptSurface, ...]:
        return tuple(self._prompt_surfaces.values())


def _validate(r: Rule, prompt_surfaces: dict[str, PromptSurface]) -> None:
    """注册期校验。七条，每条对应一次已发生的损失。"""

    # ① 取值域闭合。`dict.get(key, 默认值)` 用在契约枚举上，就是给违约数据发一张静默通行证。
    if r.sev not in SEVERITY:
        raise ValueError(f'{r.rule}: sev={r.sev!r} 不在 {SEVERITY}')
    if r.check_kind not in CHECK_KIND:
        raise ValueError(f'{r.rule}: check_kind={r.check_kind!r} 不在 {CHECK_KIND}')
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

    # A reference-shaped model rule is satisfiable only if its actual prompt
    # declares every identifier namespace the rule later expects.  This is a
    # registry invariant, not a one-off Stage4 regression.
    needs = r.needs_id_spaces
    if needs != tuple(sorted(set(needs))) or any(not value.strip() for value in needs):
        raise ValueError(
            f'{r.rule}: needs_id_spaces 必须非空白、去重并排序')
    if needs:
        if not r.prompt_surface:
            raise ValueError(
                f'{r.rule}: 声明 needs_id_spaces 却没有 prompt_surface')
        surface = prompt_surfaces.get(r.prompt_surface)
        if surface is None:
            raise ValueError(
                f'{r.rule}: prompt_surface={r.prompt_surface!r} 尚未注册')
        missing = sorted(set(needs) - set(surface.provides_id_spaces))
        if missing:
            raise ValueError(
                f'{r.rule}: prompt {surface.surface!r} 缺少 ID spaces {missing}；'
                '一条模型按下发面无法满足的判据不得注册为现役门')
    elif r.prompt_surface is not None:
        raise ValueError(
            f'{r.rule}: prompt_surface 有值但 needs_id_spaces 为空；空声明不携带信息')

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

    # ⑧ `fix` 指的必须是**真跑得起来的那条命令**。四道，各消灭一种故障：
    #      · 节点名拼错   ⇒ 界面上那个「去修」按钮点下去是 404，而表里看不出来；
    #      · flag 不在该节点的 `flags` 里 ⇒ `build_argv` 当场拒收，
    #        于是「这一条能 0 成本修」是一句**永远兑现不了**的承诺；
    #      · 指到别的层   ⇒ 报告把修复动作指到一个不产这份产物的节点上。
    #    ⚠ 这一条正是 `fix` 存在的**全部理由**：写进 `note` 的散文没有任何东西在查它。
    if r.fix is not None:
        from contracts.invariants import NODES, VALUED_FLAGS  # 局部导入：避免反向依赖
        if not r.fix:
            raise ValueError(f'{r.rule}: fix 不能为空 tuple')
        node, *flags = r.fix
        spec = NODES.get(node)
        if spec is None:
            raise ValueError(
                f'{r.rule}: fix 指的节点 {node!r} 不在 `NODES` 里。'
                f'界面上那个「去修」按钮会指到一个跑不起来的地址'
            )
        if not flags and spec.paid:
            raise ValueError(
                f'{r.rule}: fix={r.fix!r} 指向付费节点默认路径，至少要有一个显式 flag。'
                f'默认重跑只有在节点本身 paid=False 时才是一条可声明的确定性修复路'
            )
        flag_tokens: list[str] = []
        missing_values: list[str] = []
        i = 0
        while i < len(flags):
            flag = flags[i]
            if not flag.startswith('--'):
                raise ValueError(
                    f'{r.rule}: fix={r.fix!r} 的 {flag!r} 不是 flag，也不是前一个带值 flag 的值')
            flag_tokens.append(flag)
            has_value = i + 1 < len(flags) and not flags[i + 1].startswith('--')
            if flag in VALUED_FLAGS:
                if has_value:
                    value = flags[i + 1]
                    if not value.strip():
                        raise ValueError(
                            f'{r.rule}: fix={r.fix!r} 里的带值 flag {flag} '
                            f'拿到的是空白值 {value!r}；空白不构成可执行实参')
                    if flag in r.fix_args_from_finding:
                        raise ValueError(
                            f'{r.rule}: {flag} 已在 fix 里写死值，又声明由 finding 提供；'
                            f'同一个实参不许有两个来源')
                    i += 2
                    continue
                missing_values.append(flag)
            elif has_value:
                raise ValueError(f'{r.rule}: {flag} 不带值，而 fix 里跟着 {flags[i + 1]!r}')
            i += 1

        unknown = [f for f in flag_tokens if f not in spec.flags]
        if unknown:
            raise ValueError(
                f'{r.rule}: fix={r.fix!r} 里的 {unknown} 不在 `NODES[{node!r}].flags` 里。'
                f'`build_argv` 会当场拒收它 ⇒ 这条修复路**永远兑现不了**，'
                f'而表里只看得见一句「修法：…」'
            )
        declared = set(r.fix_args_from_finding)
        undeclared = set(missing_values) - declared
        if undeclared:
            flag = sorted(undeclared)[0]
            raise ValueError(
                f'{r.rule}: fix={r.fix!r} 里的带值 flag {flag} 没有值，'
                f'也没有声明由 finding 提供实参。这样的路由到 preview 才会因缺值失败')
        stray = declared - set(missing_values)
        if stray:
            raise ValueError(
                f'{r.rule}: fix_args_from_finding={sorted(stray)!r} 没有对应的缺值 flag。'
                f'实参来源声明必须与 fix 逐项对上')
        if spec.stage != r.stage:
            raise ValueError(
                f'{r.rule}: 判据产自 {r.stage}，而 fix 指向 {node!r}（属 {spec.stage}）。'
                f'一条判据不许把修法指到别的层——那是让报告去改一个不产这份产物的节点'
            )
    elif r.fix_args_from_finding:
        raise ValueError(
            f'{r.rule}: 没有 fix，却声明 fix_args_from_finding={r.fix_args_from_finding!r}')


#: 全局注册表。各 stage 的判据在移植时 import 它并 `register(...)`。
REGISTRY = Registry()
