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

def _corpus_names() -> list[str]:
    """`NOVEL_DATA` 指到的语料目录里的全部 txt 文件名。

    **没有默认路径**：原来兜底的 `../Novel_refactoring/Novel_data`（163 本）已于
    2026-08-16 随原型仓删除。指不到的默认值只会让下面那条 skip 看起来像「环境没配」，
    而实际上是「这台机器上根本没有全语料了」——两者该被区分开。
    """
    raw = os.environ.get('NOVEL_DATA')
    if not raw:
        return []
    root = Path(raw)
    return sorted(p.name for p in root.rglob('*.txt')) if root.is_dir() else []


@pytest.mark.corpus
def test_every_corpus_filename_round_trips():
    """全语料的文件名 → 目录名 → 再解析回来，**一本都不许对不上**。

    「盘上 3 本能跑」不是证据（`platform` 就是这么被推错的：盘上 3/3 是 `'FQ'`，
    当年那份 163 本语料里 120 本是 `null`）。这条测试是**零成本**的：
    只做字符串运算，不碰内容——所以只要手上有语料就该跑。
    """
    names = _corpus_names()
    if not names:
        pytest.skip('没设 NOVEL_DATA 或目录里没有 txt——这一条**没在查**，不是「查过没事」')
    assert len(names) >= 100, f'只扫到 {len(names)} 本，语料目录多半找错了'
    bad = []
    for n in names:
        sid, title, _plat = paths.derive_source_meta(n)
        if errs := paths.dirname_roundtrip_errors(title, sid):
            bad.append((n, errs))
    assert not bad, f'{len(bad)} 本的目录名解析不回去：{bad[:3]}'


# ─────────────────────────────────────────────────────────────────────────────
# 一之二、书 id 的准入 —— 定位这一步不许走出 `<Output>`
# ─────────────────────────────────────────────────────────────────────────────

def _output_with(*dirnames: str) -> Path:
    """建一个临时 `<Output>/`，里面放几个书目录。

    ⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸——一律 `tempfile.mkdtemp()`。
    """
    import tempfile
    base = Path(tempfile.mkdtemp()) / 'Output'
    for n in dirnames:
        (base / n).mkdir(parents=True)
    base.mkdir(parents=True, exist_ok=True)
    return base


@pytest.mark.parametrize('bad', [
    '',            # `os.path.join(base, '')` 是 `<Output>/` 本身 ⇒ 每本书都定位到 Output 根
    '   ',
    '.',           # `<Output>` 自己
    '..',          # `<Output>` 的**父目录**
    'a/b',         # 静默钻进子目录
    'a\\b',
    '../别的书_9',  # 越出去落到另一棵树上
    'C:/x',        # `os.path.join` 遇到带盘符的第二段会**整个丢弃** base
    '/x',
    '\x00x',
])
def test_a_book_id_can_never_climb_out_of_the_output_dir(bad):
    """⭐⭐ 产物相对路径那一侧早有闸（`_check`），而书 id 这一侧一条都没有。

    于是 `<Output>` 后面拼的是一个**完全没查过**的字符串：
    `sid='..'` 定位到 Output 的父目录，`sid=''` 定位到 Output 自己，
    `sid='C:/x'` 直接落到另一个盘——而 `ArtifactStore` 会在那里照常读写。
    接上 API / CLI 之后，这些各自都是一个任意目录读写入口。
    """
    base = _output_with('书名_123')
    with pytest.raises(PathRefused):
        paths.find_novel_dir(base, bad)
    # `resolve_novel_dir` 也不许把它兜成「上游还没跑」——那是另一件事
    with pytest.raises(PathRefused):
        paths.resolve_novel_dir(base, bad)


def test_a_legitimate_id_still_resolves_and_a_missing_one_still_returns_none():
    """加闸不许伤到正路，也不许把「找不到」变成异常。

    ⚠ 「找不到」刻意返回 `None`：原型找不到就 `sys.exit`，
    而对**长驻服务**那是致命的——一次「这本书还没跑 stage1」会杀掉整个队列。
    """
    base = _output_with('书名_123', '我的妻子')
    assert paths.find_novel_dir(base, '123') == base / '书名_123'
    assert paths.find_novel_dir(base, '我的妻子') == base / '我的妻子'   # 无 id 的书，sid = 书名
    assert paths.find_novel_dir(base, '999') is None
    from novel.errors import UpstreamMissing
    with pytest.raises(UpstreamMissing):
        paths.resolve_novel_dir(base, '999')


def test_one_id_matching_two_directories_is_refused_by_name():
    """⭐ **F-088：同一本书裂成两个目录，没有任何一层会报错。**

    原来是 `sorted()` 之后取第一个——于是产地建 `Test_book_<id>/`、
    消费方找 `book_<id>/`，两边都不出声。静默挑一个不是「容错」，
    是把一个人能修的问题变成一个查不出来的问题。⇒ 报出来，并把候选**都**列上。
    """
    base = _output_with('书名_123', '另一个书名_123')
    with pytest.raises(PathRefused) as e:
        paths.find_novel_dir(base, '123')
    assert '书名_123' in str(e.value) and '另一个书名_123' in str(e.value), \
        '不把候选都列出来，人就不知道该去合并哪两个'


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


def test_is_numbered_pattern_separates_three_shapes_of_produces():
    """`produces` 有三种形状，各有各的完成度判据——**分不开就会互相顶替**。

    带编号占位符的那种此前没有任何解析器，于是 `state.required_artifacts`
    整个丢掉它们、续跑判定只能退回 stage 级 `done`：`stage5.review` 与
    `stage5.semantic` 因此恒被「章稿写全了」顶替成 `skip`（实测两本书都没跑）。
    """
    assert paths.is_numbered_pattern('stage5/review_{n}.json')
    assert paths.is_numbered_pattern('stage6/semantic_{n}.json')
    # 目录段也带占位符 ⇒ 逐章类，归 `chapters_done`，不是轮次账
    assert not paths.is_numbered_pattern('stage5/{mode}/ch{nn}.json')
    assert not paths.is_numbered_pattern('stage5/{mode}/draft.md')
    # 没有占位符 ⇒ 一次 is_file() 就答完了
    assert not paths.is_numbered_pattern('stage7/name_map.json')


def test_numbered_rounds_counts_only_live_formal_rounds():
    """只认现役正式稿：`.INVALID` 不算、`_rounds/` 里的归档不算、别的前缀不算。

    ⚠ 与 `archived_rounds` 同一条纪律：逐个文件名比对，不拼 glob 模式
    （书名与 stem 里可能有 `[` / `]`）。
    """
    import tempfile
    root = Path(tempfile.mkdtemp())
    d = root / 'stage5'
    (d / paths.ROUNDS_DIRNAME).mkdir(parents=True)
    for n in ('review_1.json', 'review_2.json', 'review_10.json',
              'review_3.INVALID.json', 'review_x.json', 'semantic_1.json',
              'draft.md'):
        (d / n).write_bytes(b'{}')
    (d / paths.ROUNDS_DIRNAME / 'review_4.json').write_bytes(b'{}')

    assert paths.numbered_rounds(root, 'stage5/review_{n}.json') == [1, 2, 10]
    assert paths.numbered_rounds(root, 'stage5/semantic_{n}.json') == [1]
    assert paths.numbered_rounds(root, 'stage6/semantic_{n}.json') == [], \
        '目录不存在时是空，不是抛'
    with pytest.raises(PathRefused, match='is_numbered_pattern'):
        paths.numbered_rounds(root, 'stage5/{mode}/ch{nn}.json')


# ─────────────────────────────────────────────────────────────────────────────
# 五、`--apply-edit <路径>` → 书内相对路径（`edit_rel`）
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠⚠ 这一节此前**不存在**，而 stage3/4/6/7 各有一份逐字相同的 `_edit_rel`。
# 全仓唯一直接测过它的是 `tests/test_stage7_subs.py` 的两条断言（书内绝对路径 /
# 目录外绝对路径），stage3/4/6 零覆盖、**相对路径与 `..` 穿越零覆盖**。
# 而漏掉的恰好是坏的那一条：四份实现都先 `Path(edit_path).resolve()`（按**进程 cwd**）
# 再拿书目录去卡，报错文案却以书目录为基准 —— 站在仓根敲一个书内相对路径会被
# 报成「落在产物目录之外」。skill 现场记录为此一度只能建议「传绝对路径绕开它」；
# 收敛后的当前说法是 FN-016（逐层语义不同，走这四层就写进书目录内）。
#
# ⇒ 收敛成 `paths.edit_rel` 一份，并按**书目录**解释相对路径；本节按
#   「四层 × 五类路径」钉住，缺一类就等于那一类没在查。

_EDIT_DEFAULTS = {
    'stage3': 'stage3/style_edit.json',
    'stage4': 'stage4/plan_edit.json',
    'stage6': 'stage6/hook_edit.json',
    'stage7': 'stage7/name_map_edit.json',
}


def test_the_edit_defaults_here_are_the_ones_the_stages_actually_use():
    """守卫的守卫：本节的四个缺省值必须**就是**各层 `EDIT` 常量。

    各写一份，下面那五类用例就会在一个**没人用的路径**上全绿。
    """
    from novel.stages.stage3 import style
    from novel.stages.stage4 import plan
    from novel.stages.stage6 import hook
    from novel.stages.stage7 import rename

    assert {'stage3': style.EDIT, 'stage4': plan.EDIT,
            'stage6': hook.EDIT, 'stage7': rename.EDIT} == _EDIT_DEFAULTS


@pytest.fixture
def book_dir(tmp_path: Path) -> Path:
    d = tmp_path / '书名_123'
    (d / 'stage3').mkdir(parents=True)
    return d


@pytest.mark.parametrize('layer,default', sorted(_EDIT_DEFAULTS.items()))
def test_an_empty_path_falls_back_to_this_layers_registered_default(layer, default, book_dir):
    """① 缺省：不给路径 ⇒ 契约里登记的那一份（`ARTIFACTS` 有它的形状）。"""
    assert paths.edit_rel(book_dir, '', default) == default
    assert paths.edit_rel(book_dir, None, default) == default


@pytest.mark.parametrize('layer,default', sorted(_EDIT_DEFAULTS.items()))
def test_a_relative_path_is_read_against_the_book_dir_not_the_cwd(
        layer, default, book_dir, monkeypatch, tmp_path):
    """⭐② **相对路径相对书目录**——本节存在的理由（现场记录 FN-016 的第一条分支）。

    ⚠ 故意把 cwd 挪到别处：旧实现在这里会把路径解释到 cwd 底下，
    然后拿书目录去卡它，报出一句与自己行为矛盾的「落在产物目录之外」。
    """
    monkeypatch.chdir(tmp_path)
    assert paths.edit_rel(book_dir, default, default) == default
    # 反斜杠（Windows 上手敲命令的常见形态）也要归一成 `/`
    assert paths.edit_rel(book_dir, default.replace('/', os.sep), default) == default


@pytest.mark.parametrize('layer,default', sorted(_EDIT_DEFAULTS.items()))
def test_an_absolute_path_inside_the_book_dir_is_still_accepted(layer, default, book_dir):
    """③ 绝对路径照收——这是收敛前唯一那条绕行路，收敛之后它仍然成立（见 FN-016）。"""
    assert paths.edit_rel(book_dir, str(book_dir / default), default) == default


@pytest.mark.parametrize('layer,default', sorted(_EDIT_DEFAULTS.items()))
def test_a_path_outside_the_book_dir_is_a_terminal_state(layer, default, book_dir, tmp_path):
    """④ 目录外 ⇒ 显式终态（`2`），不静默去读。

    编辑文件的**形状是契约**；落在别处就没有任何东西在管它，也记不进来源账。
    """
    from novel.errors import TerminalState

    with pytest.raises(TerminalState, match='产物目录之外'):
        paths.edit_rel(book_dir, str(tmp_path / 'outside.json'), default)


@pytest.mark.parametrize('layer,default', sorted(_EDIT_DEFAULTS.items()))
def test_a_dotdot_traversal_cannot_climb_out(layer, default, book_dir):
    """⑤ `..` 穿越 ⇒ 同样拦下。**此前一条用例都没有。**

    `resolve()` 先把 `..` 算平，算平之后不在书目录里就一样被拦
    ——挡的是「另一本书」而不只是「另一个目录」：来源账的键不含书名，
    真写过去了事后查不出来（`input_key` 的 docstring）。
    """
    from novel.errors import TerminalState

    for bad in ('../别的书/stage3/style_edit.json', '..', '../../etc/passwd'):
        with pytest.raises(TerminalState, match='产物目录之外'):
            paths.edit_rel(book_dir, bad, default)


def test_a_dotdot_that_stays_inside_is_fine():
    """⚠ 反面：`..` 本身不是罪名，**越出去**才是。只拦形态会把合法路径也拦掉。"""
    import tempfile

    root = Path(tempfile.mkdtemp())
    try:
        book = root / '书名_123'
        (book / 'stage3').mkdir(parents=True)
        got = paths.edit_rel(book, 'stage3/../stage3/style_edit.json',
                             'stage3/style_edit.json')
        assert got == 'stage3/style_edit.json'
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 五、服务自己的目录：记下来的那条源路径怎么认回来
# ─────────────────────────────────────────────────────────────────────────────

_DIGEST = 'cc7a2ac151d17b87cbbfa7a55d5440ecbfb53d9bf77c32a9803185c14fa2fe22'

#: `(记下来的路径, 期望的尾巴)`。`None` = **不是上传路径，不许猜**。
_TAIL_CASES = [
    # 旧根 + 反斜杠：实测那本断了的书记的就是这个形状
    (rf'E:\out\e2e_20260817\_service\uploads\{_DIGEST}\书名_123.txt',
     (_DIGEST, '书名_123.txt')),
    # 正斜杠、相对路径：同一条尾巴，认法必须一致
    (f'outputs/_service/uploads/{_DIGEST}/书名_123.txt', (_DIGEST, '书名_123.txt')),
    # ⚠ 外部语料：被删就是**世界真的变了**，`ghost` 是正确答案，不许在这里"救"
    (r'E:\practice_group\Novel_rewriting\Test_data\书名_123.txt', None),
    # ⚠ 目录名不是内容地址 ⇒ 认不出。**认不出就答「不知道」，不许猜一个**
    (r'E:\out\_service\uploads\随手起的名字\书名_123.txt', None),
    (rf'E:\out\_service\uploads\{_DIGEST.upper()}\书名_123.txt', None),
    (rf'E:\out\_service\uploads\{_DIGEST[:-1]}\书名_123.txt', None),
    # ⚠ `_service` 底下的别的东西不是上传原文（一次性编辑计划、作业日志…）
    (rf'E:\out\_service\edits\{_DIGEST}\书名_123.txt', None),
    (rf'E:\out\uploads\{_DIGEST}\书名_123.txt', None),
    # 段数不够
    (f'{_DIGEST}/书名_123.txt', None),
]


@pytest.mark.parametrize('recorded,expected', _TAIL_CASES)
def test_only_an_uploaded_source_tail_can_be_reanchored(recorded, expected):
    """`<任意 base>/_service/uploads/<内容地址>/<文件名>` **只有这一种形状**认得回来。

    存在的理由：`stage0/removal_report.json::original_path` 记的是绝对路径，
    Output 根一搬家就断，而界面上传的原文跟着 base 走、一个字节没变
    ⇒ 拿这条尾巴 + 当前 base 就能重新锚回去（`novel/core/source.py`）。

    ⚠⚠ **多认一种形状 = 把一条真信号洗成好消息。** 外部语料没了就是没了，
      那是契约有意接受的代价（`docs/schema/stage0.md`：文件被移走时是「不知道」，
      不是「不匹配」）。所以这里认死内容地址的形状，认不出一律 `None`。
    """
    assert paths.uploaded_source_tail(recorded) == expected


def test_the_uploads_layout_has_exactly_one_spelling():
    """上传目录的形状只有一份实现——**产地与回落必须共用它**。

    `novel/orchestrator/uploads.py::save_upload` 用 `paths.uploads_dir` 落盘，
    `novel/core/source.py` 用同一个函数找回来。两处各拼一份 `_service/uploads`
    就会在下一次改布局时分家：写在 A、找在 B，而**没有任何一层会报错**
    （同 F-088 的形状）。
    """
    assert paths.uploads_dir('X') == paths.service_dir('X') / paths.UPLOADS_DIRNAME
    assert paths.service_dir('X') == Path('X') / paths.SERVICE_DIRNAME
    tail = paths.uploaded_source_tail(paths.uploads_dir('X') / _DIGEST / '书名_123.txt')
    assert tail == (_DIGEST, '书名_123.txt')
