#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_paths.py —— 「这本书在哪」「这份产物在哪」的两把尺子。

每条断言各对应一次**已经发生过**的损失：
同一本书裂成两个目录（原型 F-088）· 书名变成数字串发给每一章的写手（F-118）·
归档没有编号于是第二次覆盖第一次 · 三级路径记进两级键、那条边永远显示对得上。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from novel import paths
from novel.errors import PathRefused

# ─────────────────────────────────────────────────────────────────────────────
# 一、书名 ↔ 目录名：两把尺子必须互为逆
# ─────────────────────────────────────────────────────────────────────────────

#: `(文件名, 期望的 (sid, title, platform))`。每一条都写得出它在挡什么。
_META_CASES = [
    ('FQ_成年人不做选择，姐全都要_7581413687886353176.txt',
     ('7581413687886353176', '成年人不做选择，姐全都要', 'FQ')),
    # 没有 `_<数字>` 尾巴 ⇒ **sid = 书名本身**。旧实现在这里返回 (整个文件名, 整个文件名)，
    # 于是目录建成 `我的妻子_我的妻子`——能跑，但键空间里多了一个没人想要的形态。
    ('我的妻子.txt', ('我的妻子', '我的妻子', None)),
    # ⚠ **这一条是两步解析存在的全部理由**：一条带可选组的正则会把 `book` 读成平台前缀，
    #   于是 title 变成 `123`。可选组的歧义不报错，它只是在某些名字上悄悄换一个答案。
    ('book_123.txt', ('123', 'book', None)),
    # 剥完平台前缀还剩东西才算前缀，否则 `FQ_` 会被剥成空标题。
    ('FQ_.txt', ('FQ_', 'FQ_', None)),
]


@pytest.mark.parametrize('name,expect', _META_CASES, ids=[c[0] for c in _META_CASES])
def test_source_meta_is_parsed_in_two_steps(name, expect):
    assert paths.derive_source_meta(name) == expect


def test_dirname_survives_a_title_that_looks_like_a_platform_prefix():
    """⚠ 原型 F-088 的**最小复现**：`Test_book` 会被解析尺子当成平台 `Test` + 标题 `book`。

    产地建 `Test_book_<id>/`、消费方找 `book_<id>/` ⇒ **同一本书裂成两个目录，
    而没有任何一层会报错**。修法是建名时先替解析尺子剥到不动点。
    """
    d = paths.novel_dirname('Test_book', '123')
    assert paths.dirname_roundtrip_errors('Test_book', '123') == [], \
        f'目录名 {d!r} 解析不回去——这正是 F-088 那次分家'
    assert paths.derive_source_meta(d)[0] == '123'


def test_roundtrip_errors_actually_reports_something():
    """守卫的守卫：`dirname_roundtrip_errors` 恒返回空列表时，上面那条会**全绿**。

    ⚠ 拿一个**不可能**过关的 sid 逼它出声：sid 与目录名解析出来的 id 对不上。
    """
    assert paths.dirname_roundtrip_errors('书名', '不是它的id') != []


def test_title_comes_from_the_directory_not_from_the_argument():
    """⚠ F-118：同一本书的书名一度**取决于当时怎么敲的命令**。

    用 id 敲 ⇒ `derive_source_meta(argv)[1]` 返回**数字串**，
    随后被渲染成 `《7581413687886353176》` 发给**每一章**的写手，还流进验收报告抬头。
    ⇒ 书名的唯一权威来源是**目录名**。
    """
    d = '成年人不做选择，姐全都要_7581413687886353176'
    assert paths.title_of(d) == '成年人不做选择，姐全都要'
    assert paths.title_of(f'/x/y/{d}/') == '成年人不做选择，姐全都要'
    # 拿 id 当参数解析出来的那个坏值，长这样：
    assert paths.derive_source_meta('7581413687886353176')[1] == '7581413687886353176'


# ── 全语料：163 本真实文件名跑一遍两把尺子（0 成本，不落盘） ──────────────────

_CORPUS = Path(__file__).resolve().parents[2] / 'Novel_refactoring' / 'Novel_data'


def _corpus_names() -> list[str]:
    root = Path(os.environ.get('NOVEL_DATA') or _CORPUS)
    return sorted(p.name for p in root.rglob('*.txt')) if root.is_dir() else []


def test_every_corpus_filename_round_trips():
    """全语料 163 本的文件名 → 目录名 → 再解析回来，**一本都不许对不上**。

    「盘上 3 本能跑」不是证据（`platform` 就是这么被推错的：盘上 3/3 是 `'FQ'`，
    全语料 120/163 是 `null`）。这条测试是**零成本**的：只做字符串运算，不碰内容。
    """
    names = _corpus_names()
    if not names:
        pytest.skip('找不到原始语料目录——这一条**没在查**，不是「查过没事」')
    assert len(names) >= 100, f'只扫到 {len(names)} 本，语料目录多半找错了'
    bad = []
    for n in names:
        sid, title, _plat = paths.derive_source_meta(n)
        if errs := paths.dirname_roundtrip_errors(title, sid):
            bad.append((n, errs))
    assert not bad, f'{len(bad)} 本的目录名解析不回去：{bad[:3]}'


# ─────────────────────────────────────────────────────────────────────────────
# 二、产物相对路径的准入
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', [
    '/stage3/style_grounding.json',      # 绝对路径：拼在书目录后面会静默落在别处
    'C:/stage3/x.json',                  # 盘符，同上
    '../stage3/x.json',                  # 越出书目录 = 写到另一本书上
    'stage3/../../x.json',
    'stage3//x.json',
    '  stage3/x.json',
])
def test_relative_paths_that_would_escape_are_refused(bad):
    with pytest.raises(PathRefused):
        paths.check_rel_deep(bad)


def test_input_key_refuses_three_levels():
    """⚠⚠ **来源账的键是两级的**（`_common.md` §3.1）。

    `stage5/deep/ch01.json` 记进 `inputs` 会塌成一个**反查不到文件**的键
    ⇒ 指纹取不到 ⇒ 那条边**永远显示对得上**。**一个假的匹配比没有记录更坏。**
    ⇒ 成稿走 `store.read_judged`（存完整相对路径）。
    """
    assert paths.input_key('stage3/style_grounding.json') == 'stage3/style_grounding.json'
    with pytest.raises(PathRefused, match='两级|2 级'):
        paths.input_key('stage5/deep/ch01.json')
    # 但**写**成稿是合法的——那条路不产生 `inputs` 的边
    assert paths.check_rel_deep('stage5/deep/ch01.json') == 'stage5/deep/ch01.json'


def test_invalid_twin_is_same_dir_same_stem():
    assert paths.invalid_of('stage3/style_grounding.json') == 'stage3/style_grounding.INVALID.json'
    assert paths.is_invalid('stage3/style_grounding.INVALID.json')
    assert not paths.is_invalid('stage3/style_grounding.json')
    with pytest.raises(PathRefused):
        paths.invalid_of('stage3/style_grounding.INVALID.json')     # 不许套两层


def test_rounds_live_next_to_the_artifact_and_carry_a_number():
    """归档路径与盘上实测形态一致，**并且带轮次号**。

    ⚠ 原型给 `.INVALID` 的归档起的名字是 `chNN.INVALID.superseded.json`——**没有编号**，
    于是第二次被拦下的稿会**静默覆盖**第一次那份。
    归档是「出事之后唯一能回去的那一份」，把它做成全系统最容易丢的东西，方向是反的。
    """
    assert paths.round_rel('stage5/deep/ch01.json', 2) == 'stage5/deep/_rounds/ch01.r2.json'
    assert paths.round_rel('stage6/hook.json', 1) == 'stage6/_rounds/hook.r1.json'
    # `.INVALID` 留在 stem 里：它与正式产物是**两条历史**，各编各的号
    assert (paths.round_rel('stage3/style_grounding.INVALID.json', 1)
            == 'stage3/_rounds/style_grounding.INVALID.r1.json')
    with pytest.raises(PathRefused):
        paths.round_rel('stage6/hook.json', 0)


def test_archived_rounds_anchors_the_stem_exactly():
    """扫 `hook` 的轮次时，`hook.INVALID.r1.json` **不许**被算进去。

    合成一条历史会重号，而重号会静默覆盖一份花过钱的稿。

    ⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，pytest 的 `tmp_path` 一用就炸，
    全仓一律 `tempfile.mkdtemp()`。
    """
    import tempfile
    d = Path(tempfile.mkdtemp())
    for n in ('hook.r1.json', 'hook.r3.json', 'hook.INVALID.r2.json', 'hook.json', 'other.r9.json'):
        (d / n).write_bytes(b'{}')
    assert paths.archived_rounds(d, 'hook') == {1, 3}
    assert paths.archived_rounds(d, 'hook.INVALID') == {2}
    assert paths.archived_rounds(d / '不存在', 'hook') == set()
