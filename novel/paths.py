#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paths.py —— 「这本书在哪」「这份产物在哪」的**唯一落点**。纯路径运算，不碰内容。

【它管两件事，都是踩过坑的】

  ① **书名 ↔ 目录名的双向尺子。** `derive_source_meta`（解析）与 `novel_dirname`（构造）
     必须互为逆，`dirname_roundtrip_errors` 是这一对唯一的正确性保证。
     实测代价（原型 F-088）：`Test_book` 拼成 `Test_book_<id>` 之后，解析那把尺子把
     `Test_` 当平台前缀吃掉、解出标题 `book`——**产地建 `Test_book_<id>/`、
     消费方找 `book_<id>/`，同一本书裂成两个目录，没有任何一层会报错。**

  ② **产物相对路径的形态**：`.INVALID` 的孪生名 · `_rounds/` 的归档名 ·
     来源账那个**两级**的键。三者都必须只有一份实现，理由同上。

⚠ **本模块不做 IO 决策**，只有 `find_novel_dir` 摸盘（它必须摸，那是「找目录」本身）。
  读写一律经 `novel/store.py`——那是唯一会记账的那条路。

【为什么这几个函数从原型逐字搬过来】它们是**确定性核心**：注释里写的是
「为什么不能用那个更直观的写法」。重写等于把踩坑的证据丢掉（见仓根 `README.md`）。
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from novel.errors import PathRefused, UpstreamMissing

# ─────────────────────────────────────────────────────────────────────────────
# 一、书名 ↔ 目录名
# ─────────────────────────────────────────────────────────────────────────────

#: 原始 txt 的扩展名。剥掉它才能拿到 `<platform?>_<title>[_<id>]`。
_SOURCE_EXT = re.compile(r'\.(?:txt|md)$', re.I)

#: Windows 文件名不接受的那几个。**中文标点一个都不动**（`，`/`、`/`。` 都是合法文件名字符），
#: 把它们换成 `_` 等于无谓地改写用户的书名。
_UNSAFE_TITLE = re.compile(r'[\\/:*?"<>|]')
_CTRL = re.compile(r'[\x00-\x1f\x7f]')
MAX_TITLE = 80


def derive_source_meta(path_or_name: str) -> tuple[str, str, str | None]:
    """`<platform?>_<title>[_<id>]` → `(source_id, title, platform)`。

    **两步解析，不用一条带可选组的正则**：`book_123` 在
    `^(?:([A-Za-z]+)_)?(.+?)(?:_(\\d+))?$` 下会被贪心的 platform 组读成
    `(platform='book', title='123')`——可选组的歧义不会报错，它只会在某些名字上悄悄换一个答案。
    先从尾部锚 id，再从头部剥平台前缀，就没有歧义可言。

    **没有 `_<数字>` 尾巴时 `source_id = title`**（全语料 163 本里 13 本这样，
    如 `我的妻子.txt`）：书名本身就是目录键。
    """
    name = _SOURCE_EXT.sub('', os.path.basename(path_or_name))
    m = re.search(r'_(\d+)$', name)
    sid, rest = (m.group(1), name[:m.start()]) if m else (None, name)
    pm = re.match(r'^([A-Za-z]+)_', rest)
    # 平台前缀只在【剥完还剩东西】时才算前缀，否则 `FQ_` 这种名字会被剥成空标题
    if pm and rest[pm.end():]:
        platform, title = pm.group(1), rest[pm.end():]
    else:
        platform, title = None, rest
    return (sid or title), title, platform


def safe_title(title: str) -> str:
    """书名 → 文件系统安全的目录名片段。清洗到**不动点**，写盘前由 round-trip 断言把关。"""
    t = _CTRL.sub('', title or '')
    t = _UNSAFE_TITLE.sub('_', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = t.rstrip(' .')                       # Windows 不接受以点/空格结尾的目录名
    return t[:MAX_TITLE].rstrip(' .')


def novel_dirname(title: str, sid: str) -> str:
    """`(标题, id)` → per-novel 目录名。无 id 的书（`sid == title`）目录就叫书名本身。

    **必须先把标题剥到不动点**：`Test_book` 拼成 `Test_book_<id>` 之后，
    `derive_source_meta` 会把 `Test_` 当平台前缀吃掉、解析出标题 `book`——
    于是产地建的是 `Test_book_<id>/`、消费方找的是 `book_<id>/`（原型 F-088）。
    所以这里**先替那把尺子剥一遍**，让目录建在它待会儿要读的地方。
    """
    t = safe_title(title)
    for _ in range(8):
        cand = t if sid == t else f'{t}_{sid}'
        _sid2, t2, plat = derive_source_meta(cand)
        if plat is None and t2 == t:
            return cand
        if not t2 or t2 == t:
            break                            # 不收敛，交给 round-trip 断言拦下
        t = t2
    return t if sid == t else f'{t}_{sid}'


def dirname_roundtrip_errors(title: str, sid: str) -> list[str]:
    """写盘【之前】检查目录名能不能被 `derive_source_meta` 原样解析回来。空列表 = 通过。

    **这条断言是本模块唯一的正确性保证**：清洗与剥前缀再周全也是启发式，
    而「解析不回去」的后果是产物散进两个目录、且没有任何一层会报错。

    ⚠ 它**报告**而不是终止——校验器也要用它，不能被上游缺失打断。
    要终止是调用方的决定（原型把这两件事捆在一起，于是长驻进程也被拖着一起退出）。
    """
    d = novel_dirname(title, sid)
    got_sid, got_title, got_plat = derive_source_meta(d)
    errs: list[str] = []
    if got_sid != sid or got_plat is not None:
        errs.append(f'目录名 {d!r} 解析回 (id={got_sid!r}, 标题={got_title!r}, 平台={got_plat!r})，'
                    f'期望 id={sid!r} 且无平台前缀——stage2+ 的所有节点会找不到这本书')
    elif novel_dirname(got_title, got_sid) != d:
        errs.append(f'目录名 {d!r} 不是不动点（再解析再建名得到 '
                    f'{novel_dirname(got_title, got_sid)!r}）——同一本书会裂成两个目录')
    return errs


def check_sid(sid: str) -> str:
    """书 id 的准入。**它是目录名的一整段，不是一条路径**——五条各挡一种越界。

    ⚠⚠ 产物相对路径那一侧早就有闸（`_check`），而书 id 这一侧一条都没有，
    于是 `<base>` 后面拼的是一个**完全没查过**的字符串。实测四种走法：

      · `sid=''`       ⇒ `os.path.join(base, '')` 是 `<base>/` 本身
                         ⇒ **每一本书都定位到 Output 根**，`ArtifactStore` 在那里读写；
      · `sid='..'`     ⇒ 定位到 Output 的**父目录**；
      · `sid='a/b'`    ⇒ 静默钻进子目录，`..` 混在中间还能一路往上；
      · `sid='C:/x'`   ⇒ `os.path.join` 遇到带盘符的第二段会**整个丢弃 `base`**，
                         落点与 Output 再无关系。
    接上 API / CLI 之后，这四条各自都是一个任意目录读写入口。

    ⚠ 它与 `_check` **不合成一个**：那一份查的是「相对书目录的多级路径」，
    这一份查的是「目录名里的一段」，形状不同——硬合成一个会让两边的规则互相污染。
    """
    key = str(sid)
    if not key or key != key.strip():
        raise PathRefused(
            f'书 id 为空或带首尾空白：{sid!r}。空 id 会让 `os.path.join(base, "")` 返回 '
            f'`<Output>` 本身 ⇒ 每一本书都定位到 Output 根，而读写照常发生')
    if _CTRL.search(key):
        raise PathRefused(f'书 id 里有控制字符：{sid!r}')
    if '/' in key or '\\' in key:
        raise PathRefused(
            f'书 id 里有路径分隔符：{sid!r}。id 是目录名的**一整段**，不是一条路径——'
            f'带分隔符就能静默钻出 `<Output>`')
    if key in ('.', '..'):
        raise PathRefused(f'书 id 是 {sid!r}，它定位到的是 `<Output>` 自己或它的父目录')
    if re.match(r'^[A-Za-z]:', key):
        raise PathRefused(
            f'书 id 带盘符：{sid!r}。`os.path.join` 遇到带盘符的第二段会**整个丢弃** base，'
            f'落点与 `<Output>` 再无关系')
    return key


def _is_under(base: str, cand: str) -> bool:
    """`cand` 真的在 `base` 底下吗。**不用 `commonpath`**——它在 Windows 上跨盘抛 `ValueError`。"""
    b = os.path.normcase(os.path.abspath(base)).rstrip('\\/') + os.sep
    c = os.path.normcase(os.path.abspath(cand))
    return c.startswith(b)


def find_novel_dir(base: str | os.PathLike[str], sid: str) -> Path | None:
    """按不可变 id 定位 per-novel 目录：`<base>/<id>/` 或 `<base>/<*_id>/`。**找不到返回 `None`。**

    `glob.escape`：sid 现在可能是书名（无 id 的书），而书名里的 `[` / `]` 在 glob 里是字符类。

    ⚠ **它刻意不抛「找不到」。** 原型的对应函数找不到就 `sys.exit`，那对一个节点是对的
    （上游没跑就该停），对**长驻服务**却是致命的：一次「这本书还没跑 stage1」
    会杀掉整个队列。于是 Web 层自己写了第二份匹配逻辑，两份立刻分了家
    （原型给这里加的 `glob.escape` 那边没有）。
    ⇒ **规则留一份，「找不到怎么办」交给调用方**（要终止的用 `resolve_novel_dir`）。

    ⚠⚠ **「找不到」与另外两件事不是一回事，那两件必须抛**：
      ① **id 本身不合法**（`check_sid`）——它不会因为等一会儿就变合法；
      ② **一个 id 匹配到多个目录**——这正是本模块开头那条 F-088
         「同一本书裂成两个目录，**没有任何一层会报错**」。原来是 `sorted()` 之后取第一个，
         于是产地写进 A、消费方读到 B，两边都不出声。
         静默挑一个不是「容错」，是把一个人能修的问题变成一个查不出来的问题。
    """
    base = os.fspath(base)
    key = check_sid(sid)

    exact = os.path.join(base, key)
    cands = ([exact] if os.path.isdir(exact) else []) \
        + sorted(c for c in glob.glob(os.path.join(glob.escape(base), f'*_{glob.escape(key)}'))
                 if os.path.isdir(c))
    # 兜底断言：`check_sid` 之后越界应当已不可能，留着是因为这一处出事**不会报错**，
    # 只会安静地读写到别处去。
    cands = [c for c in cands if _is_under(base, c)]

    if not cands:
        return None
    if len(cands) > 1:
        raise PathRefused(
            f'id={key} 在 {base} 下匹配到 {len(cands)} 个目录：{cands}。'
            f'⚠ 这正是 F-088：**同一本书裂成两个目录，没有任何一层会报错**——'
            f'产地建了一个、消费方找到另一个。请先把它们合并/改名（只有人能决定留哪个），'
            f'别让定位这一步替你猜')
    return Path(cands[0])


def iter_novel_dirs(base: str | os.PathLike[str]) -> list[Path]:
    """`<base>` 底下有哪几本书。**按目录名排序**，`base` 不存在时返回空列表。

    ⚠ **跳过 `_` 开头的目录**：`Output/_service/`（作业记录与子进程日志）不是一本书。
    这与 `state.stage5_modes` 跳过 `_rounds/` 是同一条规则的两处应用——
    实测抓包里第三本的 `stage5_modes` 是 `["_rounds","deep","shallow"]`，
    原型把归档目录当成了第三条臂，而界面上那多出来的一格**不报错，只是永远是空的**。

    ⚠ **一个 sid 匹配到两个目录时不在这里报**：那是 `find_novel_dir` 的职责，
    而本函数是**列目录**，它连 sid 都还没解析。两件事分开的具体好处是
    ——语料库那一屏仍然列得出全部书，而点进那本重复的书才会拿到那条说得清的错
    （F-088：同一本书裂成两个目录，没有任何一层会报错）。
    ⇒ 列表页不会因为**某一本**坏掉而整屏打不开。

    ⚠ 它摸盘，和 `find_novel_dir` 一样——「找目录」本身就是 IO，
    这是本模块 docstring 里那条「不做 IO 决策」的既有例外。
    """
    d = Path(os.fspath(base))
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_dir() and not p.name.startswith('_')),
                  key=lambda p: p.name)


def resolve_novel_dir(base: str | os.PathLike[str], sid: str) -> Path:
    """`find_novel_dir` + 显式终态。节点用这个，长驻进程用 `find_novel_dir`。"""
    d = find_novel_dir(base, sid)
    if d is None:
        raise UpstreamMissing(
            f'找不到 id={sid} 的书目录（{base} 下既没有 {sid}/ 也没有 *_{sid}/）——先跑 stage1')
    return d


def title_of(novel_dir: str | os.PathLike[str]) -> str:
    """这本书的书名。**唯一权威来源 = per-novel 目录名**，与命令行怎么敲无关。

    ⚠⚠ **别用 `derive_source_meta(命令行参数)[1]`。** 那个函数是对的，
    但它拿到什么取决于**调用方式**：
      · `... 成年人不做选择，姐全都要_7581413687886353176` → title = 书名 ✓
      · `... 7581413687886353176`                        → title = **数字串** ✗
    两种敲法都能跑、都定位得到同一个目录，于是同一本书的书名**取决于当时怎么敲的命令**。
    实测后果（原型 F-118）：盘上两本的 stage2/stage3 产物里 title 是书名，
    而 stage4 的两份里是 `7581413687886353176`——因为那两次是用 id 敲的。
    坏值随后被渲染成 `《7581413687886353176》` 发给**每一章**的写手，还流进验收报告抬头。
    它不报错，只是安静地把数字串当书名用。
    """
    return derive_source_meta(os.path.basename(str(novel_dir).rstrip('\\/')))[1]


# ─────────────────────────────────────────────────────────────────────────────
# 二、产物相对路径
# ─────────────────────────────────────────────────────────────────────────────

#: 归档目录名。**归档而不是覆盖**（`Q-8`）：它是花过钱的产物，
#: 而且人从此才有得比较「改了 prompt 之后是变好还是变坏」。
ROUNDS_DIRNAME = '_rounds'

#: 被落盘闸门拦下的那一份的中缀。`stage3/style_grounding.json` 的孪生是
#: `stage3/style_grounding.INVALID.json`——**同目录同 stem**，人一眼看得见它俩是一对。
INVALID_INFIX = '.INVALID'

#: `_rounds/<stem>.r<N>.json`。⚠ 匹配时**锚死 stem**：
#: `style_grounding.INVALID.r1.json` 不许被 `style_grounding` 的扫描认领
#: （它们是两条不同的历史，混在一起数轮次会重号）。
_ROUND_SUFFIX = re.compile(r'\.r(\d+)\.json$')


def check_rel(rel: str) -> str:
    """产物相对路径的准入。返回归一成 `/` 的形态；不合法抛 `PathRefused`。

    三条，各挡一种已发生或结构上必然发生的错：
      ① **绝对路径 / 盘符**：`store` 会把它拼在书目录后面，拼出来的东西静默落在别处；
      ② **`..`**：越出书目录 = 写到另一本书上，而来源账的键不含书名，事后查不出来；
      ③ **层数必须是 2**（`stageN/<文件名>`）。这一条见 `PathRefused` 的 docstring：
         来源账的键是两级的，三级路径记进 `inputs` 会塌成一个反查不到文件的键
         ⇒ 那条边**永远显示对得上**。成稿走 `read_judged`（`_common.md` §3.2）。
         ⚠ **例外由调用方显式声明**（`check_rel_deep`），不由本函数猜。
    """
    return _check(rel, depth=2)


def check_rel_deep(rel: str) -> str:
    """同 `check_rel`，但允许更深的层（`stage5/deep/ch01.json`）。

    ⚠ **只给 `read_judged` / `read_text` / `write_text` 用**——它们不产生 `inputs` 的边，
    所以那个两级键的陷阱够不着它们。产生边的路径一律走 `check_rel`。
    """
    return _check(rel, depth=None)


def _check(rel: str, *, depth: int | None) -> str:
    norm = str(rel).replace('\\', '/')
    if not norm or norm != norm.strip():
        raise PathRefused(f'产物相对路径为空或带首尾空白：{rel!r}')
    if norm.startswith('/') or re.match(r'^[A-Za-z]:', norm):
        raise PathRefused(f'产物路径必须是相对书目录的，拿到绝对路径：{rel!r}')
    parts = norm.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise PathRefused(f'产物路径含 `..` 或空段，会越出书目录：{rel!r}')
    if depth is not None and len(parts) != depth:
        raise PathRefused(
            f'产物路径 {rel!r} 有 {len(parts)} 级，这里要求恰好 {depth} 级（`stageN/<文件名>`）。'
            f'来源账的键是两级的——更深的路径记进 `inputs` 会塌成一个反查不到文件的键，'
            f'于是那条边永远显示对得上。成稿请走 `store.read_judged`（`_common.md` §3.2）')
    return norm


def input_key(rel: str) -> str:
    """来源账里那条边的键：**两级，不含书名**（`_common.md` §3.1）。

    ⚠ **不含书名不是笔误——它是原型被迫用子进程的直接原因。**
    原型的来源账是**进程级**的（一个模块全局的 dict），键只有两级，
    于是同一个进程里读过 A 书再写 B 书的产物，会落下一条**看不出来的假匹配边**。
    ⇒ 新骨架把来源账做成 **run 级的实例字段**（`novel/run.py::RunContext`），
    一本书一个节点一次跑 = 一个实例，串扰**在结构上不存在**。
    键保持两级即可，因为作用域已经由实例边界给出。
    """
    return check_rel(rel)


def artifact_path(novel_dir: str | os.PathLike[str], rel: str) -> Path:
    """书目录 + 相对路径 → 绝对路径。允许多级（字节形态产物在 `stage5/<mode>/` 下）。"""
    return Path(novel_dir).joinpath(*check_rel_deep(rel).split('/'))


def invalid_of(rel: str) -> str:
    """`stage3/style_grounding.json` → `stage3/style_grounding.INVALID.json`。"""
    norm = check_rel_deep(rel)
    stem, dot, ext = norm.rpartition('.')
    if not dot:
        raise PathRefused(f'{rel!r} 没有扩展名，取不到 `.INVALID` 的孪生名')
    if stem.endswith(INVALID_INFIX):
        raise PathRefused(f'{rel!r} 已经是 `.INVALID` 形态了，别再套一层')
    return f'{stem}{INVALID_INFIX}.{ext}'


def is_invalid(rel: str) -> bool:
    return INVALID_INFIX + '.' in os.path.basename(check_rel_deep(rel))


def stem_of(rel: str) -> str:
    """`stage5/deep/ch01.json` → `ch01`；`stage3/x.INVALID.json` → `x.INVALID`。

    ⚠ **`.INVALID` 留在 stem 里是有意的**：它与正式产物是**两条历史**，
    归档时各自编各自的号。合成一条会重号，而重号会静默覆盖一份花过钱的稿。
    """
    return os.path.basename(check_rel_deep(rel)).rpartition('.')[0]


def rounds_dir_of(rel: str) -> str:
    """`stage5/deep/ch01.json` → `stage5/deep/_rounds`（**与产物同目录下的 `_rounds/`**）。

    盘上实测就是这个形态：`stage5/deep/_rounds/ch01.r1.json` · `stage6/_rounds/hook.r1.json` ·
    `stage5/_rounds/review_4.*.json`。
    """
    return os.path.dirname(check_rel_deep(rel)).replace('\\', '/') + '/' + ROUNDS_DIRNAME


def round_rel(rel: str, round_no: int) -> str:
    """归档路径：`<同目录>/_rounds/<stem>.r<N>.json`。

    ⚠ **原型给 `.INVALID` 的归档起的名字是 `chNN.INVALID.superseded.json`——没有编号**，
    于是第二次被拦下的稿会**静默覆盖**第一次那份。归档恰恰是「出事之后唯一能回去的
    那一份」，把它做成全系统最容易丢的东西，方向是反的。⇒ 统一带轮次号。
    """
    if round_no < 1:
        raise PathRefused(f'轮次号必须 ≥ 1，拿到 {round_no}')
    return f'{rounds_dir_of(rel)}/{stem_of(rel)}.r{round_no}.json'


def round_no_of(archived_name: str) -> int | None:
    """`ch01.r3.json` → `3`；不是归档名返回 `None`。"""
    m = _ROUND_SUFFIX.search(os.path.basename(archived_name))
    return int(m.group(1)) if m else None


def archived_rounds(rounds_dir: str | os.PathLike[str], stem: str) -> set[int]:
    """`<rounds_dir>/<stem>.r<N>.json` 里出现过的所有 N。

    ⚠ **stem 全等匹配，而且是逐个文件名比对、不用 glob 模式**：
    书名与 stem 里可能有 `[` / `]`（glob 的字符类），拿它们拼 pattern 是一条
    「看起来能跑、在某些书上悄悄少数一个」的路。扫描面小，全等比对更便宜也更准。
    """
    out: set[int] = set()
    d = Path(rounds_dir)
    if not d.is_dir():
        return out
    for p in d.iterdir():
        n = round_no_of(p.name)
        if n is not None and p.name == f'{stem}.r{n}.json':
            out.add(n)
    return out
