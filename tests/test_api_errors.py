#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_errors.py —— 异常 → HTTP 状态码那张表。

【它盯的那一条纪律】`novel/errors.py` 开头写着「**不设默认异常**」——
每一处终态都要选一个具体的类，选不出来说明那个失败还没想清楚。
本文件是它在 HTTP 侧的孪生条：**每一个异常类都要显式选一个状态码**，
而一个默认的 500 会把「这个失败我们还没想清楚」藏成「服务器内部错误」。

⚠ 本文件**不需要 fastapi**（`novel/api/errors.py` 不 import 它，
`novel/api/__init__.py` 一行 import 都没有）。
"""
from __future__ import annotations

import pytest

from novel import errors as E
from novel.api import errors as H
from novel.orchestrator.jobs import JobConflict


def _all_subclasses(cls) -> set[type]:
    """本仓声明的全部异常类。

    ⚠ **按模块过滤，只收 `novel.*` 里定义的。** `__subclasses__()` 是**全局**的：
    别的测试在函数体里临时定义一个子类（本文件的
    `test_status_does_not_climb_the_mro` 就定义了一个），那个类会一直挂在
    父类的子类表上，于是本文件的断言**取决于测试跑的顺序**——
    单独跑绿、全量跑红。实测踩过一次，注释留着。
    """
    out = set()
    for sub in cls.__subclasses__():
        if sub.__module__.split('.')[0] == 'novel':
            out.add(sub)
        out |= _all_subclasses(sub)
    return out


#: 两个抽象基类。**它们刻意没有状态码**——`novel/errors.py` 明写「不直接抛它」，
#: 而给它们一格，就等于给所有子类发了一张默认通行证。
_ABSTRACT = {E.TerminalState, E.IngestTerminal, E.RefusedByStructure}


def _load_the_llm_family() -> None:
    """把 LLM 那一族**加载进来**——`__subclasses__()` 只看已经 import 的类。

    ⚠ 服务层自己**一次都不 import 它们**（那正是
    `test_the_service_never_imports_the_llm_layer_at_all` 在钉的），所以这里要显式拉一把。
    ⚠ 用 `import_module` 而不是两行 `import novel.llm.x`：后者绑定的都是名字 `novel`，
    静态检查会把第二行报成「导入但未使用」——而我们要的恰恰是它的**副作用**
    （那三个类被定义出来）。**把意图写清楚，比压一条警告好。**
    """
    import importlib
    for m in ('novel.llm.client', 'novel.llm.transport'):
        importlib.import_module(m)


def test_every_error_class_lands_in_exactly_one_table():
    """⭐ **加一个异常类而不决定它怎么办，这条当场红。**

    两张表，各答一个不同的问题：
      · `STATUS`               —— 它经 HTTP 冒出来时是几号
      · `UNREACHABLE_OVER_HTTP` —— 它**越不过 HTTP 边界**，以及为什么

    ⚠ 第二张表不是豁免表。它今天只装 LLM 那一族，而理由是结构性的：
    `R-11.3` 规定一切花钱的操作走子进程 ⇒ 那一族只在子进程里抛，
    父进程看到的是一个退出码。给它们配一个状态码等于**声称一件不会发生的事**。
    """
    _load_the_llm_family()
    concrete = _all_subclasses(E.NovelError) - _ABSTRACT
    homeless = sorted(c.__name__ for c in concrete
                      if c not in H.STATUS and c.__name__ not in H.UNREACHABLE_OVER_HTTP)
    assert not homeless, (
        f'这些异常两张表里都没有：{homeless}\n'
        f'→ 要么补进 `STATUS`（想清楚它是**谁的错**：数据说走不下去 4xx / 代码自相矛盾 5xx），'
        f'要么补进 `UNREACHABLE_OVER_HTTP` 并写出它为什么越不过边界。**不许只是不提它**')
    both = sorted(c.__name__ for c in H.STATUS if c.__name__ in H.UNREACHABLE_OVER_HTTP)
    assert not both, f'这些异常同时说「有状态码」和「到不了 HTTP」：{both}'
    # 守卫的守卫：`__subclasses__()` 只看**已经 import 进来的**类。
    # 扫到的数掉下来，说明有一批异常这次根本没被加载 —— 上面那条会静静地绿。
    assert len(concrete) >= 16, (
        f'只扫到 {len(concrete)} 个异常类，期望 ≥16——'
        f'`novel/errors.py` / `novel/orchestrator/jobs.py` / `novel/llm/*` '
        f'多半有一批没被 import 到')


def test_the_unreachable_table_says_why():
    """「越不过边界」的每一条都要写得出理由——**一个空理由等于没登记**。"""
    blank = sorted(k for k, why in H.UNREACHABLE_OVER_HTTP.items() if not (why or '').strip())
    assert not blank, f'这些条目没写理由：{blank}'
    assert H.UNREACHABLE_OVER_HTTP, '表是空的——本文件的对账多半在扫一个空表'


def test_the_unreachable_table_names_real_classes():
    """⭐ **字符串键的代价在这里被补上。**

    `UNREACHABLE_OVER_HTTP` 的键是类名字符串（这样服务层才不用 import `novel.llm`），
    代价是拼错 / 改名不会被解释器发现。⇒ 这一条把幽灵名字报出来，
    而拼错的那个类会被 `test_every_error_class_lands_in_exactly_one_table` 报成「无家可归」
    ——**两个方向各有一条**，才算真的补上了。
    """
    _load_the_llm_family()
    known = {c.__name__ for c in _all_subclasses(E.NovelError)}
    ghosts = sorted(set(H.UNREACHABLE_OVER_HTTP) - known)
    assert not ghosts, (
        f'`UNREACHABLE_OVER_HTTP` 里有对不上任何异常类的名字：{ghosts}\n'
        f'→ 类被改名/删掉了？那一行会一直挂在表里让人以为它管着事')


def test_the_service_never_imports_the_llm_layer_at_all():
    """⭐ `novel/api/errors.py` **一行 `novel.llm` 都不许有**，连函数体里的懒 import 也不行。

    ⚠ 「够得到」与「这次没调」是两件事：一个懒 import 仍然让服务进程**结构上**
    够得到发请求的代码，而 `R-11.3` 的孪生条要求它够不到。
    ⇒ 这正是那张表用**类名字符串**当键的全部理由。
    （分层那一侧由 `tests/test_layering.py::test_the_service_process_cannot_spend_money`
    扫整个 `novel/api/**` 与 `novel/views/**`，本条只是它在这一个文件上的近距离版本。）
    """
    import ast
    src = open(H.__file__, encoding='utf-8').read()
    tree = ast.parse(src)
    leaked = sorted({n.module for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module
                     and n.module.startswith('novel.llm')})
    assert not leaked, f'`novel/api/errors.py` import 了 LLM 层：{leaked}'
    # 守卫的守卫：那三个类名得真的在文件里被提到，否则本文件在查一个空表。
    assert all(k in src for k in H.UNREACHABLE_OVER_HTTP)


def test_the_table_never_names_a_class_that_is_gone():
    """反方向：表里不许有已经不存在的类。**删了类却留着登记**，
    那一行会一直挂在表里让人以为它管着事（同 `test_api_enums` 里的幽灵登记那条）。
    """
    known = _all_subclasses(E.NovelError) | {E.NovelError}
    ghosts = sorted(c.__name__ for c in H.STATUS if c not in known)
    assert not ghosts, f'STATUS 里有已经不存在的异常类：{ghosts}'
    assert E.NovelError not in H.STATUS, \
        '基类不该有状态码——它一旦有了，「每个类都要决定一次」就漏了'


def test_there_is_no_default_status():
    """⭐ 认不出的异常**原样抛出去**，不 fallback 到 500。

    ⚠ 反例用的是两个抽象基类与一个仓外异常：三者都不该有答案。
    """
    for exc in (E.TerminalState('抽象基类'), E.IngestTerminal('抽象基类'),
                E.RefusedByStructure('抽象基类'),
                ValueError('仓外的异常')):
        with pytest.raises(type(exc)):
            H.status_of(exc)


def test_status_does_not_climb_the_mro():
    """**精确匹配，不上溯。** 新的子类必须自己决定一次。

    ⚠ 上溯听起来省事，实际是给新异常发一张**静默通行证**：
    那个类从此有了一个没人决定过的状态码。
    """
    class BrandNewProvenanceProblem(E.ProvenanceRefused):
        pass

    assert H.STATUS[E.ProvenanceRefused] == 500
    with pytest.raises(BrandNewProvenanceProblem):
        H.status_of(BrandNewProvenanceProblem('还没人给我一格'))


# ───────────────────────── 逐条：这一格为什么是这个数 ─────────────────────────

def test_missing_and_unreadable_are_two_different_codes():
    """「上游还没跑」（去跑它）与「上游读不出来」（去看它）是**相反**的指示。

    合成一条，界面会把「先跑 stage2」说成「stage2 的产物损坏了」
    ——`novel/errors.py` 里 `UpstreamMissing` 的 docstring 就是这句。
    """
    assert H.status_of(E.UpstreamMissing('x')) == 404
    assert H.status_of(E.ArtifactUnreadable('x')) == 409


def test_a_second_job_on_the_same_book_is_409():
    """`R-11.10` 的状态码映射；第二个请求的真实 409 行为另有 API 测试覆盖。
    """
    assert H.status_of(JobConflict('这本书已经在跑了')) == 409


def test_ingest_conflicts_and_unprocessable_content_have_different_codes():
    '''已有状态冲突要人选择，小说内容不满足契约则是不可处理的输入。'''
    conflicts = (
        E.SourceConflict('x'),
        E.HumanEditProtected('x'),
        E.PartitionInputMismatch('x'),
    )
    unprocessable = (
        E.SourceDecodeFailed('x'),
        E.SourcePathRejected('x'),
        E.Stage0BodyRejected('x'),
        E.PartitionChapterMismatch('x'),
        E.ResidualNonbody('x'),
        E.HookCopyMissing('x'),
        E.TooFewChapters('x'),
    )
    assert all(H.status_of(exc) == 409 for exc in conflicts)
    assert all(H.status_of(exc) == 422 for exc in unprocessable)


def test_structural_refusals_are_5xx_because_they_are_our_bug():
    """`RefusedByStructure` 那一族是**代码自相矛盾**，不是数据问题 ⇒ 5xx。"""
    for exc in (E.ProvenanceRefused('x'), E.AccountingKeyUnregistered('x'),
                E.ByteFormRefused('x'), E.ValueDomainRefused('x')):
        assert H.status_of(exc) == 500, type(exc).__name__


def test_path_refused_is_the_one_that_differs_from_the_exit_code():
    """⚠⚠ 全表唯一一条**两侧归属不同**的。

    CLI 侧 `PathRefused` 是退出码 3（代码自相矛盾），HTTP 侧它是 500——
    **但「书 id 不合法」那一种的产地是客户端**，那是 400。
    解法不是给异常分子类，是**在边界上先验**（路由第一件事调 `paths.check_sid`）。
    这条测试钉的是「表里那一格仍然是 500」——边界那一半由 HTTP 行为测试查。
    """
    from novel.cli import exit_codes
    assert H.status_of(E.PathRefused('x')) == 500
    assert exit_codes.REFUSED_BY_STRUCTURE == 3
    assert H.BAD_REQUEST == 400 and H.CSRF_REFUSED == 403
