#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_corpus_fixtures.py —— 拿盘上 3 本书的真实产物给新 schema 做**零成本体检**。

**这是骨架阶段最强的一次验收，且一分钱不花**：约 90 份真实产物（含 `_rounds/` 归档），
覆盖 stage1~stage7 全部七层。stage0 盘上零产物——**那一格是空的，不是绿的**。

四条断言分别钉住四件不同的事：

| # | 断言 | 它防的那种失败 |
|---|---|---|
| ① | 原样加载失败，且 `extra_forbidden` **恰好等于** `CUT` | 「我们裁了 N 个字段」从散文变成会红的测试。**多裁一个红、漏裁一个也红** |
| ② | 裁完之后缺的必填键**恰好等于** `ADDED` | 「我们给八层加了哪些键」同样会红。**偷偷加一个必填键会当场被抓** |
| ③ | 按清单迁移之后**全部加载成功** | 新 schema 在真实数据上真的立得住 |
| ④ | 没有 `_provenance` 的产物**恰好等于**登记的那一份 | `stage4.md` 事实⑤ 从散文变成会红的测试 |

⚠ **① 与 ② 是两条，不能合成一条**：裁撤与新增的**性质不同**
（一个是「这份信息有第二个落点」，一个是「这份信息以前根本没记」）。
合成一条之后，「少裁了一个」与「多加了一个必填键」在测试输出上会长得一样。

⚠ 全部断言**只在内存里跑**，不落任何盘——迁移后的语料**永远不写成文件**：
那是第二个落点，且会与 `tests/corpus.py` 的三张表分家。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests import corpus
from tests.corpus import ADDED, CUT, MISSING_PROVENANCE, Artifact, corpus_root, norm_loc

#: 收集期就把清单拿到手——`pytest.mark.parametrize` 要它。
ARTIFACTS: list[Artifact] = list(corpus.iter_artifacts()) if corpus_root() else []

_NO_CORPUS = pytest.mark.skipif(
    not ARTIFACTS,
    reason=(
        f'找不到语料目录（`{corpus.CORPUS_ENV}` 或本仓的兄弟目录 '
        f'`../Novel_refactoring/Output`）——**这四条现在没在查，不是「查过没事」**。'
    ),
)


def _ids(items: list[Artifact]) -> list[str]:
    return [f'{a.source_id[-4:]}/{a.rel}' for a in items]


def _locs(err: ValidationError, model: type, kind: str) -> set[str]:
    """把某一类错误的 `loc` 归一成 `tests/corpus.py` 的路径写法。"""
    return {norm_loc(model, e['loc']) for e in err.errors() if e['type'] == kind}


# ───────────────────── 守卫的守卫 ─────────────────────

@_NO_CORPUS
def test_the_scan_actually_finds_the_whole_corpus():
    """扫错目录时下面三条会**全绿**，而那正是最坏的情况。

    照 `test_docs_references.py::test_the_scan_actually_finds_something` 的形态。
    """
    assert len(ARTIFACTS) > 60, f'只扫到 {len(ARTIFACTS)} 份产物——路径多半错了'
    seen = {a.pattern for a in ARTIFACTS}
    assert len(seen) >= 12, f'只覆盖了 {len(seen)} 种产物：{sorted(seen)}'
    assert len({a.source_id for a in ARTIFACTS}) == 3, '语料应当是 3 本书'
    # 归档必须在里面：`residual[].confidence = 'high'` 只在 `_rounds/` 出现过
    # （正式产物 19/19 全是 `low`，归档 8 份里有 35 条 `high`）。
    assert any('_rounds/' in a.rel for a in ARTIFACTS), \
        '一份 `_rounds/` 归档都没扫到——**只反推正式产物会定出一个把 `high` 当零样本的 schema**'


@_NO_CORPUS
def test_every_input_edge_really_gets_reshaped():
    """`RESHAPED` 那张表**不能只是散文**——这一条让它的第一项变成会红的断言。

    盘上 **79 份**产物带 `_provenance`、**297 条** `inputs` 边，
    **全部是扁平的 10 位字符串**；新契约一条边是 `{file, consumed}`。
    ⚠ 数字写死是有意的（守卫的守卫）：迁移函数悄悄漏掉一支时，
    「边全都合法」与「一条边都没走到」在类型校验上长得一模一样。

    ⚠ `consumed` **一律迁成 `null`**，含义是「本节点没有声明自己读了哪些字段」
    ⇒ 该边的新鲜度是 `unknown`，**不是 `fresh`**。**建槽位，不填槽位。**
    """
    flat, edges, carriers = 0, 0, 0
    for a in ARTIFACTS:
        raw = corpus.load_raw(a)
        if not (isinstance(raw, dict) and isinstance(raw.get('_provenance'), dict)):
            continue
        carriers += 1
        flat += sum(1 for v in (raw['_provenance'].get('inputs') or {}).values()
                    if isinstance(v, str))
        for edge in corpus.load_cut(a)['_provenance']['inputs'].values():
            edges += 1
            assert set(edge) == {'file', 'consumed'} and edge['consumed'] is None, edge
    assert (carriers, flat, edges) == (79, 297, 297), (
        f'带来源账的产物 {carriers} 份（应为 79）· 盘上扁平边 {flat} 条 · '
        f'迁移后 {edges} 条（应各为 297）'
    )


# ───────────────────── ① 裁撤清单 ─────────────────────

@_NO_CORPUS
@pytest.mark.parametrize('a', ARTIFACTS, ids=_ids(ARTIFACTS))
def test_ondisk_fails_with_exactly_the_documented_cuts(a: Artifact):
    """原样加载，`extra_forbidden` 的 loc 集合**恰好等于** `CUT[pattern]`。

    ⚠ **不是「包含」是「相等」**：
      · 集合里多一个 ⇒ 我们裁了一个文档没说要裁的字段；
      · 少一个 ⇒ 那个字段其实还在模型里，而 `CUT` 说它已经没了。
    两种方向的错都会让「我们裁了 N 个字段」这句话变成一句**没人验证过**的话。
    """
    want = corpus.cuts_of(a)
    try:
        a.model.model_validate(corpus.load_cut(a, apply_cuts=False, fill_added=False))
    except ValidationError as err:
        got = _locs(err, a.model, 'extra_forbidden')
    else:
        got = set()
    assert got == want, (
        f'{a.rel} 的越界键与 `CUT` 对不上\n'
        f'  盘上有而 CUT 没登记：{sorted(got - want)}\n'
        f'  CUT 登记了而盘上没有：{sorted(want - got)}'
    )


# ───────────────────── ② 新增的必填键 ─────────────────────

@_NO_CORPUS
@pytest.mark.parametrize('a', ARTIFACTS, ids=_ids(ARTIFACTS))
def test_exactly_the_documented_keys_are_missing_after_the_cuts(a: Artifact):
    """裁完、改完形状之后，缺的必填键**恰好等于** `ADDED[pattern]`。

    ⚠ 这一条问的是 `ADDED` 那张表**唯一**能被验证的问题。拿**原始**产物去问它会得到
    一堆噪声（`inputs` 还是扁平的 ⇒ 轮次账两条分支都匹配不上 ⇒ 联合类型把两边的错一起报）。

    ⚠ 它同时是一条**反向**守卫：偷偷给某个模型加一个必填字段而不登记，这条当场红——
    而那正是「新契约悄悄要求了一样盘上没有的东西」的形状。
    """
    want = set(ADDED.get(a.pattern, {}))
    try:
        a.model.model_validate(corpus.load_cut(a, fill_added=False))
    except ValidationError as err:
        got = _locs(err, a.model, 'missing')
    else:
        got = set()
    if (a.source_id, a.rel) in MISSING_PROVENANCE:
        # ⚠ 它**不是**「新契约加的一个键」，是那份产物**丢了**来源账——
        # 两件事挂在同一个表里，「我们新加了什么」就再也读不出来了。
        # 它的意义由 `test_the_missing_provenance_is_the_documented_one` 给出。
        got -= {'_provenance'}
    assert got == want, (
        f'{a.rel} 缺的必填键与 `ADDED` 对不上\n'
        f'  盘上缺而 ADDED 没登记：{sorted(got - want)}\n'
        f'  ADDED 登记了而盘上并不缺：{sorted(want - got)}'
    )


# ───────────────────── ③ 迁移之后必须加载成功 ─────────────────────

@_NO_CORPUS
@pytest.mark.parametrize('a', ARTIFACTS, ids=_ids(ARTIFACTS))
def test_ondisk_loads_after_the_documented_migration(a: Artifact):
    """**全计划性价比最高的一条**：约 90 份真实产物给新 schema 免费体检。

    它查的是**其余全部字段**——类型、取值面、必填、后置条件、嵌套结构。
    ⚠ 但要连它的边界一起读：`ADDED` 填进去的是**声明过的占位符，不是实测值**；
    那几格上这条断言只证明「形状对得上」，**不证明取值面**。
    """
    if (a.source_id, a.rel) in MISSING_PROVENANCE:
        pytest.skip(
            '这一份**在新契约下加载不了，而那正是它的价值**：它根本没有 `_provenance`'
            '（`stage4.md` 事实⑤ 的字节级铁证）。'
            '⚠ **不许靠伪造一份来源账把它变绿**——那样这份产物就再也不会提醒任何人'
            '「提升路会把来源账整个丢掉」。它由 '
            '`test_the_missing_provenance_is_the_documented_one` 单独钉住。'
        )
    a.model.model_validate(corpus.load_cut(a))


# ───────────────────── ④ 没有来源账的那一份 ─────────────────────

@_NO_CORPUS
def test_the_missing_provenance_is_the_documented_one():
    """「没有 `_provenance` 的产物」**恰好等于** `MISSING_PROVENANCE`。

    ⚠⚠ 盘上那唯一一条是 `stage4.md` 事实⑤ 的**字节级铁证**——
    `.INVALID` 提升路把来源账整个丢掉，而**没有一行报告会说出来**。
    它必须**留在表里**，不是被当成 bug 修掉：这条测试的价值就是让它一直被看见。

    ⚠ 只数「模型里有 `_provenance` 字段、而盘上那份没有」的。
    契约本来就没有来源账的那几份（`main_text` / `hook_copy` / `partition_report` /
    `name_map_edit`）**由构造排除**——把它们算进来会让这张表里塞满噪声，
    而噪声会让人不看这张表。
    """
    got = {
        (a.source_id, a.rel)
        for a in ARTIFACTS
        if 'provenance' in a.model.model_fields
        and '_provenance' not in (corpus.load_raw(a) or {})
    }
    assert got == MISSING_PROVENANCE, (
        f'没有来源账的产物集合变了\n'
        f'  盘上有而没登记：{sorted(got - MISSING_PROVENANCE)}\n'
        f'  登记了而盘上已经有了：{sorted(MISSING_PROVENANCE - got)}'
    )
