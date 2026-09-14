#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exit_codes.py —— 退出码的**唯一落点**。

【为什么在顶层】退出码是**子进程通信协议**（`argv[0] ≡ sys.executable` 的那套），
不是 CLI 的私有物：cli（翻译异常）、orchestrator（jobs/queue/run_step 判成败）、
api（edits 路由判落盘）三方平级消费。放在 `novel/cli/` 之下会迫使后两者
反向 import CLI 层；它是进程间的事实，与哪一层碰巧最先用它无关。

【为什么它要单独一份】退出码是编排层判成败的依据（`R-12.3`），
而「哪个数字表示什么」一旦在两处各写一遍，两处就会在某个分支上给出不同的数
——那时候界面上显示的成败与真实发生的事就分家了，**且没有任何东西会出声**。

【五个数，各自答一个不同的问题】
    0  做完了
    1  做完了且**正式结果已落盘**，但结论不通过
       （`badge == 'fail'` 或 typed negative，如 Stage4B `infeasible`）
    2  **显式终态**：数据/流程说这一步不该继续。**不是 bug**，所以不打栈
    3  **结构性拒绝**：代码自相矛盾。**是 bug**，栈要留全
    70 **未捕获异常**：进程崩了，**结论未知**。**是 bug**，栈要留全

⚠ `1` 与 `2` 必须分开：前者是「跑成了、结果已落盘，结论是不通过」，
后者是「压根没跑成」。结论不通过通常来自 `CheckReport`，但也可以是
一份已正式落盘的 typed negative 结果；不能为后者伪造一份报告。
合成一个数，编排层就分不出「这一层查出问题了」与「这一层没能跑」——
而那正是本项目一路在防的那对（`Q-2`）。

⚠ `1` 与 `70` 也必须分开：此前未捕获异常走 Python 默认退出码 1，
与 `CHECK_FAILED` 同数——**一次真崩溃会被编排层读成「跑完了但不合格」**，
stage5 写了一半崩了与「验收没过」在账本上是同一副面孔。
70 不是新造的数：`orchestrator/jobs.py` 的 worker 早就用它表「编排层自身崩」，
这里只是把那个魔数收编进唯一落点（BSD `EX_SOFTWARE` 同义）。
它故意不取 4——与 0~3 拉开视觉距离，免得被误读成第五个业务态。

⚠ 已知残留：`argparse` 对不合法命令行自带 `sys.exit(2)`，与 `TERMINAL_STATE`
同数。「命令行不合法 = 按设计走不下去」语义可容，登记不修。
"""
from __future__ import annotations

OK = 0
CHECK_FAILED = 1
TERMINAL_STATE = 2
REFUSED_BY_STRUCTURE = 3
CRASH = 70

#: 给报错信息用：数字 → 一句人话。**报错里要出现数字本身**，
#: 否则人拿到一个 `exit 2` 之后还得翻代码。
MEANING: dict[int, str] = {
    OK: '做完了',
    CHECK_FAILED: '跑完且正式结果已落盘，但结论不通过',
    TERMINAL_STATE: '显式终态：这一步按设计走不下去了（不是 bug）',
    REFUSED_BY_STRUCTURE: '结构性拒绝：代码自相矛盾（是 bug，栈在上面）',
    CRASH: '未捕获异常：进程崩了，结论未知（是 bug，栈在上面）',
}

#: 「这一次**落没落盘**」——`0` 与 `1` 都落了，`2` / `3` / `70` 都没有。
#:
#: ⚠⚠ **它必须在这里，不许在消费方各写一遍。** 这个谓词此前只活在
#: `novel/api/routes/edits.py::_completed`（一个私有函数）里，于是同一句话在
#: 编排层根本没有：`orchestrator/jobs.py::fold` 写的是
#: `state = 'ok' if code == 0 else 'failed'` ⇒ **`1` 与 `2` / `70` 同名**。
#: 实测代价：一次 8 分钟的付费批量在 `stage6.hook` exit 1 上整条停下，
#: 而 `hook.json` **好好地落在盘上**（三条候选里只有一条命中 HARD），
#: 队列却把其后三项记成 `not_run`，日志上与「压根没跑成」一个样子。
#: ⚠ 未登记的码一律按**没落盘**算：钱这件事上「不知道」倒向贵的一边（`Q-10`），
#: 落盘这件事上「不知道」倒向「别拿它当成品」。
LANDED: frozenset[int] = frozenset({OK, CHECK_FAILED})


def landed(code: int | None) -> bool:
    """这一次跑**落没落盘**。`None`（还没结束 / 不知道）一律按没落算。"""
    return code in LANDED


def landed_but_red(code: int | None) -> bool:
    """落了盘、但结论不通过。**它不是「没跑成」，也不是「没问题」。**

    多数节点是校验报告有阻断级红行；Stage4B 还可能是已落盘的
    typed `infeasible`。定稿类动作在这个码上照样盖审批章（`edits.py`），
    因为人定稿的语义正是「看过负结论之后拍板」。
    """
    return code == CHECK_FAILED
