#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nodes.py —— 把 `(节点, 书, flags)` 变成一条命令，并回答关于它的两个问题。

【纯函数，没有一个碰盘、碰网、碰进程】
    validate_run(node, flags)  这条跑法是否属于该节点    手敲 CLI 与编排器共用
    missing_required(node, …)  还差哪几个必填 flag       校验与 preview 占位符共用
    is_paid(node, flags)       这一次会不会花钱          `R-11.7`：贵贱只有一处算
    would_write(node, flags)   这一次会不会往盘上写      本期新补，原型没有这个概念
    step_label(node, flags, …) 这一步叫什么              `R-11.8`：界面与作业记录共用它
    build_argv(node, sid, …)   那条命令                  `R-11.8`：展示与执行共用它
    shell_line(argv)           那条命令的人读形态        `R-11.8`：渲染只许有一个落点
    build_steps(node, sid, …)  这个作业要依次跑哪几条

⚠ **`is_paid` 的位置是被两处注释锁死的**（`contracts/invariants.py` 的 `NodeSpec`
与 `novel/run.py` 的 `RunContext.paid`）。挪走它就要同时改那两处，否则文档说假话。

【为什么纯函数值钱】「排这一批会花多少次调用、会写哪些文件、命令长什么样」
可以 **0 成本**地摊开给人看，也可以 0 成本地单测——而这三个问题的答案
恰恰是人在点「确认」之前唯一能依据的东西。
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from contracts.invariants import (
    CONDITIONAL_REQUIRED_FLAGS, FREE_ENTRIES, NODES, NODE_WRITE_EFFECTS,
    REQUIRED_PLACEHOLDER_PREFIX, VALUED_FLAGS, WRITE_EFFECTS,
)
from contracts.schema.orchestration import Step
from novel import paths as novel_paths
from novel.errors import UnsupportedRunMode

#: 仓根。`build_argv` 里的 `-m novel.cli` 要求 cwd 在这里。
#: ⚠ 装成 editable（`pyproject.toml` 的 `[build-system]`）之后这条依赖就没了，
#: 但 `Step.cwd` 仍然如实记着那一次是在哪儿跑的——记录与依赖是两件事。
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: CLI 的模块名。**与 `novel/cli/__main__.py` 是同一个东西**，不许在别处再写一份字符串。
CLI_MODULE = 'novel.cli'

#: 追跑那条 `--check-only` 的理由。
#:
#: ⚠ **退出码 0 不等于成功**：原型实测 `n2_blueprint` 打印「blueprint 缺章（勿静默）」
#: 之后**没有 `sys.exit`**，于是一份残缺产物退出码 0（F-087）。
#: ⇒ 凡是写盘的付费节点，跑完自动追一次复检，两个结论并排显示。
FOLLOW_CHECK_NOTE = '自动追跑：退出码 0 也可能是残缺产物'


# ─────────────────────────────────────────────────────────────────────────────
# 一、flag 的解析与校验
# ─────────────────────────────────────────────────────────────────────────────

def flag_tokens(flags: Iterable[str]) -> tuple[str, ...]:
    """`['--apply-edit', 'C:/x.json']` → `('--apply-edit',)`。

    带值 flag 的**值**不是 flag。不把它剔掉的话，`is_paid` 会拿一个文件路径
    去查白名单、查不到，于是一个 0 成本的人工编辑被判成付费——
    这正是 `Q-3` 记着的那个「漏登记的后果是反的」，只是换了个成因。
    """
    out: list[str] = []
    skip = False
    for tok in flags:
        if skip:
            skip = False
            continue
        if tok.startswith('-'):
            out.append(tok)
            skip = tok in VALUED_FLAGS
    return tuple(out)


def flag_value(flags: Sequence[str], name: str) -> str | None:
    found = [flags[i + 1] for i, token in enumerate(flags[:-1]) if token == name]
    if len(found) > 1:
        raise UnsupportedRunMode(f'{name} must appear at most once')
    return found[0] if found else None


def required_flags(node: str, flags: Sequence[str] = ()) -> tuple[str, ...]:
    """这条跑法必填哪几个 flag。**无条件的那些 + 被本次 flag 触发的那些。**

    ⚠ 顺序稳定（先无条件、后触发序），因为 `preview` 要拿它渲染占位符，
    而一条命令的形状每次都换个顺序会让人以为跑法变了。
    """
    spec = NODES.get(node)
    if spec is None:
        return ()
    out = list(spec.required_flags)
    for tok in flag_tokens(flags):
        for extra in CONDITIONAL_REQUIRED_FLAGS.get((node, tok), ()):
            if extra not in out:
                out.append(extra)
    return tuple(out)


def missing_required(node: str, flags: Sequence[str] = ()) -> tuple[str, ...]:
    """还差哪几个必填 flag。**`validate_run` 与 `preview` 共用这一处**——
    分成两处算，摊开给人看的那条命令与真跑那条的必填口径会慢慢分家。"""
    present = set(flag_tokens(flags))
    return tuple(f for f in required_flags(node, flags) if f not in present)


def placeholder_for(flag: str) -> str:
    """`preview` 给一个必填项渲染的占位符。**不含空格**，好让人整条粘走。

    人话说明不塞进占位符里（那会把它变成多个 shell 词），
    由 `preview` 的注解带着 `VALUED_FLAGS` 那句原文一起打出来。
    """
    return REQUIRED_PLACEHOLDER_PREFIX + flag.lstrip('-')


def validate_run(node: str, flags: Sequence[str] = (),
                 mode: str | None = None, *,
                 allow_placeholders: bool = False) -> tuple[str, ...]:
    """节点认不认识这条跑法。**认不出就当场拒**，不猜。

    ⚠ 它开在 `build_argv` 里而不是 CLI 的 argparse 里，是因为
    **展示与执行共用 `build_argv`**（`R-11.8`）：拒收开在共用的那一处，
    「界面上摆出来的那条命令」就不可能是一条拼错的命令。
    开在 argparse 里等于只拦住手敲的那一半。

    ⚠ `allow_placeholders` **只由 `build_argv(placeholders=True)` 传**，
    也就是只在 `preview` 那一趟为真：那一趟要的是「形状完整的模板」。
    真跑那一趟恒为假 ⇒ 占位符进不了产物。
    """
    if node not in NODES:
        raise UnsupportedRunMode(
            f'未知节点 {node!r}。取值面的唯一落点是 `contracts.invariants.NODES`，'
            f'现有 {len(NODES)} 个：{", ".join(sorted(NODES))}')
    spec = NODES[node]
    tokens = flag_tokens(flags)
    composite_write = (
        node == 'stage5.write'
        and bool({'--deep', '--shallow-rest'} & set(tokens))
    )
    if node == 'stage2.global' and '--resample' in tokens:
        if set(tokens) != {'--resample'}:
            raise UnsupportedRunMode(
                'stage2.global --resample 必须单独执行；它会重新采样整份全局响应')

    if composite_write:
        required = {'--deep', '--shallow-rest'}
        if not required <= set(tokens) or set(tokens) - required:
            raise UnsupportedRunMode(
                'stage5 composite write requires exactly --deep and --shallow-rest')
        if mode is not None:
            raise UnsupportedRunMode(
                'stage5 composite write selects both arms and must not carry --mode')
    elif spec.modes:
        if mode not in spec.modes:
            raise UnsupportedRunMode(
                f'{node} 必须指定 --mode，取值是 {list(spec.modes)}，实得 {mode!r}')
    elif mode is not None:
        raise UnsupportedRunMode(
            f'{node} 没有臂，不接受 --mode={mode!r}。'
            f'⚠ 全链只有 `stage5.write` 有臂（两臂只差 system prompt 与给不给源章原文，'
            f'产物形状逐字节相同，判据表只有一张）')

    bad = [f for f in tokens if f not in spec.flags]
    if bad:
        raise UnsupportedRunMode(
            f'{node} 不接受 {bad}。它接受的是：{list(spec.flags) or "（没有跑法 flag）"}\n'
            f'⚠ 如果这条跑法**现役后端真的在跑**，那就是节点表漏登记了——'
            f'补进 `NODES[{node!r}].flags`，别在这里绕过去')

    # 带值 / 不带值都要当场对上：拼错的那条 argv **会真的被执行**。
    seq = list(flags)
    for i, tok in enumerate(seq):
        if not tok.startswith('-'):
            continue
        has_value = i + 1 < len(seq) and not seq[i + 1].startswith('-')
        if tok in VALUED_FLAGS and not has_value:
            raise UnsupportedRunMode(
                f'{tok} 要一个值（{VALUED_FLAGS[tok]}），而它后面没有')
        if tok not in VALUED_FLAGS and has_value:
            raise UnsupportedRunMode(
                f'{tok} 不带值，而它后面跟着 {seq[i + 1]!r}')
        # preview 的占位符**不是一个值**。放它过去等于把「待填·actor」
        # 当自报身份写进人工凭据里——那条账日后没有任何人能反查。
        if (not allow_placeholders and has_value
                and seq[i + 1].startswith(REQUIRED_PLACEHOLDER_PREFIX)):
            raise UnsupportedRunMode(
                f'{tok} 还是 preview 的占位符 {seq[i + 1]!r}，请换成真值'
                f'（{VALUED_FLAGS.get(tok, "见节点文档")}）')

    # 必填缺项：拒在**展示与执行共用**的这一处（`R-11.8`）。开在节点函数体里
    # 只拦得住真跑那一半，而 preview 会照样摊开一条注定 exit 2 的命令。
    missing = missing_required(node, flags)
    if missing:
        raise UnsupportedRunMode(
            f'{node} 缺必填 {list(missing)}：'
            + '；'.join(f'{f} = {VALUED_FLAGS.get(f, "见节点文档")}' for f in missing))
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# 二、贵贱（`R-11.7` / `Q-10`）
# ─────────────────────────────────────────────────────────────────────────────

def is_paid(node: str, flags: Iterable[str] = ()) -> bool:
    """这一次跑法会不会花钱。**唯一落点**——前端一行判贵贱的代码都没有。

    【白名单，不是黑名单】只有逐个核对过分支的 flag 才进 `FREE_ENTRIES`；
    认不出的一律按花钱算。**钱这件事上，「不知道」必须倒向贵的那一边**（`Q-10`）——
    少弹一次确认框的代价是一笔没打算花的账单。

    ⚠ 原型在这里踩过一个具体的坑，注释一起搬：它写的是 `KINDS.get(kind, {})`，
    于是一个**未知 kind** 会被当成「`paid` 缺省为假」的节点——
    那是判据 26 说的「`dict.get(key, 默认值)` 给违约数据发的静默通行证」，
    而这里发的是一张**账单**上的通行证。⇒ 本函数第一件事就是「不认识 ⇒ 付费」。

    ⚠ 本函数比原型**多严一格**：原型对 0 成本节点直接放行、不看它带了什么未知 flag。
    这里认不出的 flag 一律付费——反正 `build_argv` 也会当场拒收它，
    两处答案一致比「一处宽一处严」好。
    """
    spec = NODES.get(node)
    if spec is None:
        return True
    tokens = flag_tokens(flags)
    if any(f not in spec.flags for f in tokens):
        return True
    if not spec.paid:
        # 默认不调 LLM 的节点：只有登记在案的花钱 flag 能把它变贵。
        return any(f in spec.paid_flags for f in tokens)
    return not any(f in FREE_ENTRIES for f in tokens)


# ─────────────────────────────────────────────────────────────────────────────
# 三、写不写盘 —— 本期新补的那一样东西
# ─────────────────────────────────────────────────────────────────────────────

def write_effect(node: str, flags: Iterable[str] = ()) -> str:
    """这一次会往盘上写什么。**空串 = 不写盘。**

    【它为什么必须与 `is_paid` 分开】两个维度**正交**，而原型只实现了前者：
    后者只活在注释里，且一字不差地出现了 **8 次**——「`FREE_FLAGS` 只管钱不管写」。
    于是界面上一个写着「0 成本」的按钮可能归档旧轮、覆盖正式产物、重拼成书 txt，
    **而用户看到的只有「0 成本」三个字**。

    【默认是「写」，flag 只能把它变成「不写」】每个节点的默认路径都产一份产物
    ——那是「节点」这个词的定义。方向与 `is_paid` 同构（`FREE_ENTRIES` 让付费变免费）。
    ⇒ **没登记的 flag 一律按会写算**：`Q-10` 那条「不知道要倒向贵的那一边」，
    在这里是倒向危险的那一边。

    ⚠ 返回一句话而不是 `bool`，与记账那边「`recorded=False` ⇒ `[????] 未记录`」
    是同一个纪律：**「不写」与「没人说」必须分得开**。
    """
    spec = NODES.get(node)
    tokens = flag_tokens(flags)
    # 「只看不动」的跑法是互斥的：带上任意一个，这一趟就不落盘。
    if any(WRITE_EFFECTS.get(f, '未登记') == '' for f in tokens):
        return ''
    # 同一 flag 在不同节点可以写不同文件。节点维度的覆盖优先：
    # stage4 `--identity` 实际写的是基线专用文件，不是本节点
    # `produces` 里的正式计划。「只看不动」仍在它之前，与 CLI 分支
    # 优先级一致：`--identity --dry-run` 只预览，不写盘。
    overrides = [NODE_WRITE_EFFECTS[(node, f)] for f in tokens
                 if (node, f) in NODE_WRITE_EFFECTS]
    if overrides:
        return ' · '.join(overrides)
    said = [WRITE_EFFECTS[f] for f in tokens if WRITE_EFFECTS.get(f)]
    if said:
        return ' · '.join(said)
    # 默认路径：**说出那几份产物的路径**，不说「本层产物」。
    # 人是拿这句话去决定要不要点确认的，而「会覆盖 stage3/style_grounding.json」
    # 与「会写点什么」在那一刻不是同一条信息。
    return ' · '.join(spec.produces) if spec and spec.produces else f'{node} 的产物'


def would_write(node: str, flags: Iterable[str] = ()) -> bool:
    """`write_effect` 的布尔投影。**判定只有一处算**，这里不重写一遍规则。"""
    return bool(write_effect(node, flags))


def check_nodes(stage: str) -> tuple[str, ...]:
    """这一层里**接受 `--check-only`** 的那些节点。从 `NODES` 派生，不手抄清单。

    【它给谁用】校验报告的路由 `GET /api/novels/:sid/check/:kind`——
    `:kind` 是**层**（八个，取值面 = `STAGES`），而复检是**节点**上的一条跑法。
    这个函数就是那两轴之间唯一的那座桥。

    F7 已把 stage0 的旧地雷拆掉：只有 `stage0.remove` 接受 `--check-only`，
    因此层路由仍然恰好落到一个复检节点。首次 `--source` 摄入尚无 `sid` 时，
    `build_steps` 会先由原文身份派生它，再给后续复检命令使用。
    `tests/test_orchestrator_nodes.py` 的守卫要求每个已移植层恰好有一个复检节点。
    """
    return tuple(n for n, s in NODES.items()
                 if s.stage == stage and '--check-only' in s.flags)


def check_node(stage: str) -> str:
    """这一层那**唯一**一座复检桥。`check_nodes` 的单数投影。

    【为什么单开一个函数】「每层恰好一座」是 `tests/test_orchestrator_nodes.py::
    test_every_stage_has_exactly_one_check_node` 钉住的不变量，而消费方**全都**只要那一个。
    各自解一次单例的下场是各自定一套降级策略：实测同时存在过三种——
    解构当场炸、`nodes[0]` 抛 IndexError、以及最坏的 `nodes[0] if nodes else f'{stage}.review'`
    ⇒ **编出一个不存在的节点 id** 一路带进 `readonly_ctx`，
    把「这层没有复检桥」变成「这层有一座谁都找不到的桥」。
    ⇒ 收成一处：不变量破了就在这里、以同一句话、对所有消费方当场出声。
    """
    nodes = check_nodes(stage)
    if len(nodes) != 1:
        raise KeyError(
            f'{stage} 有 {len(nodes)} 个节点接受 `--check-only`：{nodes}；'
            f'每层必须恰好一座复检桥。'
            f'→ 0 个：忘了给它登记跑法（`contracts/invariants.py::NODES` 的 flags）；'
            f'→ 2 个以上：这一层的复检入口不知道该调哪一个')
    return nodes[0]


# ─────────────────────────────────────────────────────────────────────────────
# 四、命令（`R-11.8` / `R-12.3`）
# ─────────────────────────────────────────────────────────────────────────────

def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """子进程环境。**这三个变量是必需项**（`R-12.3`），不是保险。

    ⚠ 病因是实测出来的，不是理论：父进程的 `locale.getpreferredencoding()` 是 `cp936`；
    子进程的 stdout 接到**控制台**时是 UTF-8，接到**管道**时退回 cp936——
    而报告里满篇 `✓ ✗ ↻ ⚠`。最要命的形态是**先落盘（钱已经花了）、再打印 `✓`**：
    一次**成功的付费跑会被报成失败**；而失败分支打印 `'✗'*20`，
    **报告失败这件事本身就是崩溃触发器**。

    ⚠ 用 `utf-8` 而不是 `utf-8:replace`：`:replace` 会把编不出来的字悄悄换掉，
    那是「静默糟蹋输出」，**比崩溃更坏**（也比崩溃更难发现）。
    """
    return {**(dict(env) if env is not None else os.environ),
            'PYTHONUTF8': '1',
            'PYTHONIOENCODING': 'utf-8',
            'PYTHONUNBUFFERED': '1'}


def build_argv(node: str, sid: str | None, flags: Sequence[str] = (), *,
               base: str | os.PathLike[str], mode: str | None = None,
               placeholders: bool = False) -> list[str]:
    """那条命令。**preview 与真跑共用这一个函数**（`R-11.8`）。

    确认对话框若自己拼一个展示串，那就是同一条命令的第二个落点：
    显示的与执行的会慢慢分家，**而没有任何东西会出声**。

    ⚠ `argv[0]` 恒是 `sys.executable`，**绝不硬编码解释器路径**——
    换一个 venv 就会指到另一份依赖上，而那份依赖里的 `contracts` 可能是旧的。
    ⚠ `--base` 用绝对路径，好让这条命令与 cwd 无关。
    ⚠ argv 里的 `-X utf8 -u` 与 `child_env()` 是**两道**，不是一道：
    前者管「人在终端直接粘这条命令」，后者管「服务把它跑成子进程」。

    ⚠ `placeholders=True` 只给 `preview` 用：把缺的必填项补成显式占位符，
    好让摊开的那条命令**形状完整**。占位符本身仍被 `validate_run` 拒收，
    所以它是「照着改」的模板，不是一条能直接跑通的命令。
    """
    if placeholders:
        filled = list(flags)
        for flag in missing_required(node, flags):
            filled += [flag, placeholder_for(flag)]
        flags = filled
    tokens = validate_run(node, flags, mode, allow_placeholders=placeholders)
    if sid is None:
        if node != 'stage0.remove' or '--source' not in tokens:
            raise UnsupportedRunMode(
                '只有 stage0.remove --source <原始小说> 能在目录产生前不带 sid')
        if '--check-only' in tokens:
            raise UnsupportedRunMode(
                'stage0 --check-only 要定位已有书目录，必须带 sid；新书先完成摄入')
    argv = [sys.executable, '-X', 'utf8', '-u', '-m', CLI_MODULE, 'run', node]
    if sid is not None:
        argv.append(sid)
    if mode is not None:
        argv += ['--mode', mode]
    argv += list(flags)
    argv += ['--base', os.path.abspath(os.fspath(base))]
    if is_paid(node, flags):
        # ⚠ **付费跑法在命令行上必须带 `--yes`**（`Q-10`：花钱前必须人确认）。
        # 它由这里补上而不是由调用方传：到了构造 argv 这一步，确认已经在
        # 界面/队列那一层发生过了（`R-11.8` 的二次确认 · `R-13.3` 的一次性预先确认）。
        # ⇒ 摊开显示的那条命令**就是会跑的那条**，粘进终端也不会再问一遍。
        # ⚠ 它不进 `NodeSpec.flags`：那张表管的是**跑法**（改变贵贱/写不写），
        # 而 `--yes` 一样都不改，它只是一枚确认凭据。同 `--base` / `--mode`。
        argv.append('--yes')
    return argv


#: 不需要引号的那些字节。**没有反斜杠**——Windows 路径因此一定会被引起来，
#: 而那正是要的：`--base E:\out` 在 POSIX shell 里会把 `\o` 吃掉。
#: ⚠ 写成显式 ASCII 字符类而不是 `\w`：Python 的 `\w` 认 Unicode、JS 的不认，
#: 用 `\w` 会让本函数与前端 `quoteArg` 在中文取值上分家（多引一对引号无害，
#: 但「两处逐字节一致」这句话一旦不成立，就没人知道还有哪儿不一致）。
_BARE = re.compile(r'^[A-Za-z0-9_./:=@,+-]+$')


def shell_line(argv: Sequence[str]) -> str:
    """一条 argv → 一行**可逐字粘进 shell** 的命令。**渲染只许有一个落点。**

    【它补的是什么】`build_argv` 产的是**词元序列**，而摊开给人看的一直是
    `' '.join(argv)` —— 于是 `--reason "fix present_names_invented"` 打出来是三个裸词，
    逐字复制到任何 shell 里都会把后两个词解析成位置参数。`build_argv` 上方那条注释
    写着「摊开显示的那条命令**就是会跑的那条**，粘进终端也不会再问一遍」，
    而在含空格的取值上它此前不成立。

    ⚠ 引法与前端 `web/src/api/endpoints.ts::quoteArg` **必须逐字节一致**
    （单引号包裹、内部单引号成对翻倍）：跨语言没法共用一个函数，能做的只有钉死规则。
    改这里就要同时改那里。
    ⚠ 选单引号而不是双引号：PowerShell 与 POSIX sh 对单引号都按**字面**读，
    `$`、反引号、反斜杠一律不展开。cmd.exe 不在支持面内（本仓文档不用它）。
    ⚠⚠ **取值内部含单引号时两种 shell 无法同时满足**：转义写法 PowerShell 是 `''`、
    POSIX 是 `'\\''`，不存在同时正确的编码。这里按 **PowerShell** 写
    （本仓文档、launcher 与前端 `quoteArg` 都是这一侧），POSIX shell 下这一种取值
    要自己改写。其余全部形状——含空格、反斜杠路径、中文、`&`/`|`/`;`——两边都对。
    """
    return ' '.join(
        token if _BARE.match(token) else "'" + token.replace("'", "''") + "'"
        for token in argv
    )


def step_label(node: str, flags: Sequence[str] = (), *, mode: str | None = None) -> str:
    """这一步的人话。**`_step()` 与 `/api/enums` 的 `actions[]` 共用这一个函数。**

    ⚠ 与 `build_argv` 同一条纪律（`R-11.8`）：**界面上那个按钮的名字，与作业记录里
    那一步的名字，必须是同一个函数产的**。分成两处的具体后果是人点了
    「stage3 · 只跑确定性实测」而作业记录里写着别的——两边都不出声。

    ⚠ **一个字都不许手写。** 原型那 36 条 action 的 label 是手抄的，于是
    `n1_partition` 的 label 写着「stage1 摄入+分章」，而「摄入」早已搬到 stage0
    ——**手写的 label 会说谎，派生的不会**（`NODES` 那段裁决记录的原话）。

    ⚠ 用 `flag_tokens` 而不是 `f.startswith('-')`：带值 flag 的**值**不是 flag，
    而一个恰好以 `-` 开头的路径会被后者当成 flag 印进 label 里。
    贵贱（`is_paid`）与写不写（`write_effect`）都走这一个解析器，label 没有理由用第二个。
    """
    spec = NODES.get(node)
    if spec is None:
        raise UnsupportedRunMode(
            f'未知节点 {node!r}。取值面的唯一落点是 `contracts.invariants.NODES`')
    label = spec.label if mode is None else f'{spec.label}（{mode}）'
    tokens = flag_tokens(flags)
    return f'{label} {" ".join(tokens)}' if tokens else label


def _step(node: str, sid: str | None, flags: Sequence[str], *, base: str | os.PathLike[str],
          mode: str | None, note: str | None = None,
          placeholders: bool = False) -> Step:
    # ⚠ **一个 Step 的四格必须出自同一份 flags。** argv 补了占位符而 `paid`/`writes`/
    #   `label` 还按原 flags 算，就是同一条命令的第二个落点（`R-11.8` 要防的那件事）
    #   ——今天两边答案恰好一样，而「恰好一样」不是不变量。
    missing = missing_required(node, flags) if placeholders else ()
    filled = list(flags)
    for flag in missing:
        filled += [flag, placeholder_for(flag)]
    if missing:
        said = '；'.join(f'{placeholder_for(f)} = {VALUED_FLAGS.get(f, "见节点文档")}'
                         for f in missing)
        said = f'必填项已渲染成占位符，执行前逐个替换：{said}'
        note = f'{note}\n    ⚠ {said}' if note else said
    return Step(
        argv=build_argv(node, sid, filled, base=base, mode=mode,
                        placeholders=placeholders),
        cwd=str(REPO_ROOT),
        label=step_label(node, filled, mode=mode),
        paid=is_paid(node, filled),
        writes=write_effect(node, filled),
        note=note,
    )


def build_steps(node: str, sid: str | None, flags: Sequence[str] = (), *,
                base: str | os.PathLike[str], mode: str | None = None,
                follow_check: bool = True,
                placeholders: bool = False) -> list[Step]:
    """一个作业要依次跑哪几条命令。**纯函数、无副作用。**

    追跑规则：**任何会写盘的跑法后面自动追一条 `--check-only`**，理由见 `FOLLOW_CHECK_NOTE`。

    ⚠⚠ **判准是「写不写盘」，不是「花不花钱」，而这一格是实测改过的。**
    第一版写的是「付费且写盘」，于是 `--revalidate` / `--apply-edit`
    这几条**免费但会写盘**的路一条都不追——而它们改的是同一份正式产物。

    ⚠ 只在那个节点**真的接受** `--check-only` 时才追（stage2.global 就不接受）——
    追一条它拒收的命令，作业会在第二步无缘无故失败，而第一步可能已经花过钱了。
    """
    first = _step(node, sid, flags, base=base, mode=mode,
                  placeholders=placeholders)
    steps = [first]
    if (follow_check and first.writes
            and '--check-only' in NODES[node].flags
            and '--check-only' not in flag_tokens(flags)):
        check_sid = sid
        if check_sid is None:
            source = flags[list(flags).index('--source') + 1]
            check_sid = novel_paths.derive_source_meta(source)[0]
        steps.append(_step(node, check_sid, ['--check-only'], base=base, mode=mode,
                           note=FOLLOW_CHECK_NOTE))
    return steps
