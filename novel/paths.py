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

  ③ **服务自己那个目录的形状**：`<base>/_service/` 与它底下按内容地址存的上传原文。
     它跟着 base 走，所以「记下来的绝对路径断了、文件其实还在」要靠这一段认回来
     （`uploaded_source_tail`；验内容再落地在 `novel/core/source.py`）。

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

from contracts.artifacts import artifact_rel
from novel.errors import PathRefused, TerminalState, UpstreamMissing

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
    同理，各层的 `_rounds/` 也是归档目录，不是一本书或一个现役产物分支。

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


#: 终审包的文件名后缀：`<书目录>/<书名>_review.md`。
#:
#: ⚠ **它不是产物**：不在 `ARTIFACT_PATHS` 里、没有 schema、没有 `_provenance`，
#: 代码一行都不读它——它是 agent 代跑之后交给人的那一份汇总（唯一的人工界面）。
#: 所以它**落在书目录根，不在任何 `stageN/` 下**：进 `stageN/` 就变成一份没人管形状的
#: 「产物」，而 `check_rel` 那道两级闸与 `ARTIFACT_SPECS` 都会开始对它有意见。
#:
#: ⚠ 从前它叫 `<base>/final_review_<sid>.md`（落在 base 根）。搬进书目录之后
#: **文件名里不必再带 sid**——书目录名本身已经带了，同名书不会撞。
REVIEW_SUFFIX = '_review.md'


def review_package_rel(novel_dir: str | os.PathLike[str]) -> str:
    """终审包在书目录里的文件名。**唯一落点**——别在提示词或文档里各拼一份。

    书名取 `title_of`（权威源是目录名），因此天然是文件系统安全的：
    它本来就是一个真实目录名里的一段。⚠ 别改成
    `derive_source_meta(命令行参数)[1]`，那是 F-118（用 id 敲命令时书名会变成数字串）。
    """
    return f'{title_of(novel_dir)}{REVIEW_SUFFIX}'


def review_package_path(novel_dir: str | os.PathLike[str]) -> Path:
    """终审包的绝对路径。纯路径运算，不摸盘。"""
    return Path(novel_dir) / review_package_rel(novel_dir)


# ─────────────────────────────────────────────────────────────────────────────
# 二、产物相对路径
# ─────────────────────────────────────────────────────────────────────────────

#: 归档目录名。**归档而不是覆盖**（`Q-8`）：它是花过钱的产物，
#: 而且人从此才有得比较「改了 prompt 之后是变好还是变坏」。
ROUNDS_DIRNAME = '_rounds'

#: 被落盘闸门拦下的那一份的中缀。`stage4/plan_chapters.json` 的孪生是
#: `stage4/plan_chapters.INVALID.json`——**同目录同 stem**，人一眼看得见它俩是一对。
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
    """同 `check_rel`，但允许更深的归档/历史路径（如 `stage5/_rounds/ch01.r1.json`）。

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
    """书目录 + 相对路径 → 绝对路径。允许多级（含历史目录与 ``_rounds/``）。

    ⚠⚠ **未填的占位符当场拒。** `ARTIFACT_PATHS` 里有三类带 `{}` 的模板
    （逐章 · 轮次编号 · 书级参数化），而真实文件名不可能含 `{` ⇒ 这一条不误伤。
    不拦的后果**全是静默的**：`store.exists()` 对一个字面叫 `{title}_rewriting.txt`
    的路径老实答 `False`（读起来就像「还没跑」），`store.write_text()` 则真的把那个
    文件建出来——两侧都不报错。这就是 `docs/LESSONS.md` 第 23 条那个形状。
    JSON 那一侧的同款闸在 `contracts/schema/__init__.py::_refuse_unfilled_template`，
    但它长在 `decode/encode_artifact` 上，**字节形态产物（成书）够不着它**
    ⇒ 闸要装在这个漏斗上，而不是要求每个调用点各自记得先填占位符。
    """
    norm = check_rel_deep(rel)
    if '{' in norm:
        raise PathRefused(
            f'{rel!r} 是路径**模板**不是路径（占位符还没填）。'
            f'→ 逐章/轮次用 `contracts/artifacts.py::artifact_rel` 填出具体路径；'
            f'成书用 `novel/paths.py::book_txt_rel(novel_dir)`')
    return Path(novel_dir).joinpath(*norm.split('/'))


def edit_rel(novel_dir: str | os.PathLike[str], edit_path: str | None,
             default_rel: str) -> str:
    """`--apply-edit <路径>` → **书内相对路径**。stage2/4/6/7 共用这一份。

    【相对谁】**相对当前书目录**，不是相对进程 cwd。
    ⚠⚠ 这一位是本函数存在的理由。四层此前各有一份 `_edit_rel`，都写成
    `Path(edit_path).resolve()` 再 `.relative_to(书目录)` —— 先按 **cwd** 解释，
    再拿书目录去卡。于是站在仓根敲 `--apply-edit stage4/plan_edit.json`
    会被报成「落在这本书的产物目录之外」，而报错文案说的正是书目录
    ——**代码按一个口径解释，报错按另一个口径解释**，两边都言之凿凿。
    这条曾把 skill 现场记录逼出一条「一律传绝对路径绕开它」的处置建议。
    ⇒ 现在按报错文案说的那个口径来；skill 侧收敛后的当前说法见现场记录 FN-016。

    【绝对路径仍然收】给了绝对路径就按绝对路径算，只要它落在这本书目录内。

    ⚠ 落在书目录**之外** ⇒ 显式终态（`2`），不静默去读：编辑文件的形状是契约的一部分
    （`contracts/schema/__init__.py::ARTIFACTS` 里登记着），放在别处就没有任何东西
    在管它的形状，也记不进来源账。`..` 穿越同理——`resolve()` 先把它算平，
    算平之后不在书目录里就一样被拦。

    ⚠⚠ **`stage5.write` 与 `stage0.edit` 刻意不走这里。** 它们的路径语义是「任意路径」，
    而 `novel/api/routes/edits.py` **依赖**这一点：服务进程把书目录**之外**的
    `<base>/_service/edits/` 绝对路径喂给 `stage5.write --apply-edit`
    （`R-11.3` 要求服务进程不写产物盘）。把它们并进来会打断那条现役 HTTP 改稿路。
    """
    if not edit_path:
        return default_rel
    root = Path(novel_dir).resolve()
    p = Path(edit_path)
    target = (p if p.is_absolute() else root / p).resolve()
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        raise TerminalState(
            f'`--apply-edit {edit_path}` 落在这本书的产物目录之外'
            f'（书目录 {root}，算出来是 {target}）。'
            f'编辑文件的**形状是契约**（`ARTIFACTS` 里登记着 {default_rel}）——'
            f'放在别处就没有任何东西在管它，也记不进来源账。'
            f'⚠ 相对路径按**书目录**解释，不按当前工作目录') from None


def invalid_of(rel: str) -> str:
    """`stage4/plan_chapters.json` → `stage4/plan_chapters.INVALID.json`。"""
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
    """`stage5/ch01.json` → `ch01`；`stage4/x.INVALID.json` → `x.INVALID`。

    ⚠ **`.INVALID` 留在 stem 里是有意的**：它与正式产物是**两条历史**，
    归档时各自编各自的号。合成一条会重号，而重号会静默覆盖一份花过钱的稿。
    """
    return os.path.basename(check_rel_deep(rel)).rpartition('.')[0]


def rounds_dir_of(rel: str) -> str:
    """`stage5/ch01.json` → `stage5/_rounds`（**与产物同目录下的 `_rounds/`**）。

    现役形态是：`stage5/_rounds/ch01.r1.json` · `stage6/_rounds/hook.r1.json` ·
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


def book_txt_rel(novel_dir: str | os.PathLike[str]) -> str:
    """**这本书**的成书在书内的相对路径：`stage7/<书名>_rewriting.txt`。

    【为什么必须有这个函数】`ARTIFACT_PATHS['stage7.book']` 是**书级参数化模板**
    （`contracts/artifacts.py::BOOK_SCOPED_PATHS`），`STAGE7_BOOK` 因此只是模板而不是
    路径。交付物按书名命名，而书名的唯一权威源是**目录名**
    （`title_of`；别用 `derive_source_meta(命令行参数)[1]`，那是 F-118）。
    ⇒ 「这本书的成书叫什么」只有这一处答得出来，消费方一律调它，不要各拼一份。

    ⚠ 书名天然是文件系统安全的：它本来就是一个真实目录名里的一段（已过 `safe_title`）。
    ⚠ 后缀里那个 `_rewriting` 让 stem 永不等于 Windows 保留设备名（`CON` / `NUL` …），
      那一格 `safe_title` 至今不管——是这个后缀顺手挡住的，不是它被处理过了。

    ⚠⚠ **它把书名放进同一条路径两次**（`<书名>_<sid>/stage7/<书名>_rewriting.txt`），
    而 `novel/store.py` 的两段注释记着两次真实的 MAX_PATH 事故正是「重复长 stem」。
    2026-08-19 实测（默认 base `…/Novel_rewriting/outputs`，41 字符）：
      · 现役那本 `把夫君做成丧尸后`：整条 100 字符，普通路径（无 `\\\\?\\` 前缀）读得回来；
      · 最坏情况（书名吃满 `MAX_TITLE=80` 码点 + 19 位 sid）：**244 字符，余量只有 16**。
    ⇒ base 再深一点就会撞墙。根治办法是系统级 `LongPathsEnabled`（管理员一次性），
      不是把书名截短——截短会让「文件名就是书名」这句话变成有时成立。
    ⚠ 这与 `tests/conftest.py` 那道 basetemp 预检**不是同一件事**：那条算的是测试临时
      目录里最长的 Stage0 内容寻址归档（basetemp 之后约 204 字符，仍长于本条的 202），
      且测试套件里的书名都很短 ⇒ 那条预算不因本函数而变，别去动它。
    """
    return artifact_rel('stage7.book', title=title_of(novel_dir))


#: `produces` 模板里的占位符（`review_{n}.json` 的 `{n}`）。
_PLACEHOLDER_RX = re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')

#: **轮次编号**那一类的占位符名。收成一个闭集而不是「任何一个占位符」是刻意的，
#: 理由见 `is_numbered_pattern`。
_NUMBERED_PLACEHOLDER = '{n}'


def is_numbered_pattern(pattern_rel: str) -> bool:
    """这条 `produces` 是不是「同目录下一个**编号**占位符」的形状。

    真：`stage5/review_{n}.json` · `stage6/semantic_{n}.json`
    假：`stage5/ch{nn}.json`（使用逐章占位符 `{nn}`，那是逐章类，
        完成度挂在「计划里的章写了没有」上，不是「盘上有几号文件」）
    假：`stage7/{title}_rewriting.txt`（**书级参数化**，见 `BOOK_SCOPED_PATHS`）
    假：`stage7/name_map.json`（没有占位符，一次 `is_file()` 就答完了）

    ⚠⚠ **只认 `{n}`，不认「tail 里恰好一个占位符」。** 后者是本函数的原实现，
    而它会把书级那条判成 `True`（`stage7/{title}_rewriting.txt`：head 无占位符、
    tail 恰好一个）⇒ 成书的完成度改去问 `rounds_done`，而 `numbered_rounds` 拿
    `title` 走 `isdigit()` 只会得到空集 ⇒ **成书在 `artifacts[]` 与徽章里静默消失**。
    那正是下面 `numbered_rounds` docstring 记着的事故形状的镜像
    （那次是把没跑过的层记成 `done`，这次是把跑过的层记成没有）。
    """
    head, _, tail = pattern_rel.rpartition('/')
    return _PLACEHOLDER_RX.search(head) is None and \
        _PLACEHOLDER_RX.findall(tail) == [_NUMBERED_PLACEHOLDER]


def numbered_rounds(novel_dir: str | os.PathLike[str], pattern_rel: str) -> list[int]:
    """`stage5/review_{n}.json` → 盘上真实存在的那几个编号（升序）。

    【为什么必须有这个函数】`produces` 带 `{}` 的产物**做不了一次 `is_file()`**，
    而 `state.required_artifacts` 因此把它们整个丢掉 ⇒ `StageState.artifacts` 里
    没有它们的行。续跑判定于是只剩 stage 级的 `done` 可问，而那个 `done` 对 stage5
    说的是**章稿写全了没有**——拿它去答「全书验收跑过没有」，
    答案永远是「跑过了」，**而盘上一份 `review_*.json` 都没有**。
    实测后果：批量队列把 `stage5.review`（0 成本）与当时的 `stage5.semantic`（付费，
    已于 2026-08-17 下线）双双记成 `done` 而一次都没执行，
    整条链上唯一的书级质量闸门静默缺席。

    ⚠ **全等匹配、只认十进制编号**，与 `archived_rounds` 同一条纪律：
    `review_1.INVALID.json` 匹配不上（它不是一份现役产物），
    `_rounds/` 是子目录、`is_file()` 就滤掉了（归档不是现役）。
    ⚠ 书名里可能有 `[` / `]`，所以逐个文件名比对、不拼 glob 模式。
    """
    if not is_numbered_pattern(pattern_rel):
        raise PathRefused(
            f'{pattern_rel!r} 不是「同目录下一个编号占位符」的形状，'
            f'不能按轮次编号解析——先用 `is_numbered_pattern` 问一句')
    head, _, tail = pattern_rel.rpartition('/')
    prefix, _, suffix = tail.partition(_PLACEHOLDER_RX.search(tail).group(0))
    d = Path(novel_dir) / head
    out: list[int] = []
    if not d.is_dir():
        return out
    for p in d.iterdir():
        name = p.name
        if not p.is_file() or not name.startswith(prefix) or not name.endswith(suffix):
            continue
        digits = name[len(prefix):len(name) - len(suffix)] if suffix else name[len(prefix):]
        if digits.isdigit():
            out.append(int(digits))
    return sorted(out)


# ─────────────────────────────────────────────────────────────────────────────
# 三、服务自己的目录：`<base>/_service/`
# ─────────────────────────────────────────────────────────────────────────────

#: 服务自己的账（`ledger.jsonl` / `agent_progress.jsonl` / 作业记录）与自己的输入住这里。
#: **它不是一本书**——`iter_novel_dirs` 靠 `_` 前缀把它跳过。
SERVICE_DIRNAME = '_service'

#: 可恢复项目删除的唯一落点。回收站不参加 `iter_novel_dirs()`，也不冒充小说产物。
TRASH_DIRNAME = 'trash'

#: 界面上传的原文长期保留在 `<base>/_service/uploads/<sha256(原始字节)>/<原文件名>`。
#: 目录名**就是内容地址**，产地只有一个：`novel/orchestrator/uploads.py::save_upload`。
UPLOADS_DIRNAME = 'uploads'

#: 内容地址的形状。认死 64 位小写十六进制——**认不出就不是上传目录**，
#: 与其猜一个不如答「不知道」（`novel/core/source.py` 的重锚以此为前提）。
_CONTENT_ADDRESS = re.compile(r'[0-9a-f]{64}')

#: 记下来的路径可能是另一台机器/另一个 OS 写的，两种分隔符都要认。
_ANY_SEP = re.compile(r'[\\/]+')


def service_dir(base: str | os.PathLike[str]) -> Path:
    """`<base>/_service/`。服务自己的东西的**唯一落点**，产物一律不进这里。"""
    return Path(os.fspath(base)) / SERVICE_DIRNAME


def trash_dir(base: str | os.PathLike[str]) -> Path:
    """项目回收站：`<base>/_service/trash/`。"""
    return service_dir(base) / TRASH_DIRNAME


def uploads_dir(base: str | os.PathLike[str]) -> Path:
    """`<base>/_service/uploads/`。界面上传的原文长期留在这儿供 stage0 审校屏取回。"""
    return service_dir(base) / UPLOADS_DIRNAME


def uploaded_source_tail(recorded: str | os.PathLike[str]) -> tuple[str, str] | None:
    """把一条**记下来的**源路径认成「界面上传的那一份」，拆出 `(内容地址, 文件名)`。

    `None` = 它不是上传路径（比如外部语料 `Test_data/xxx.txt`）。

    ⚠⚠ **认的是尾巴，不是 base。** 存在的理由正是「记下来时的 base 已经不是现在这个」：
    上传的原文住在 `<base>/_service/uploads/<内容地址>/<文件名>`，这一段**跟着
    base 走、一个字都没变**，而记下来时的 base 可能已经不是现在这个。
    ⇒ 产地在写产物那一刻用它判「这条路径是不是上传」，据此写出
    `ManagedUploadRef` 而不是 `ExternalFileRef`
    （`novel/core/source.py::source_ref_for`，唯一调用方）。

    ⚠ **只认这一种形状，其余一律 `None`。** 外部语料被删就是**世界变了**，
    `ghost` 是正确答案、契约有意接受（`docs/schema/stage0.md`：文件被移走时是
    「不知道」，不是「不匹配」）。在这里多认一种形状 = 把一条真信号洗成好消息。
    """
    parts = [p for p in _ANY_SEP.split(os.fspath(recorded)) if p]
    if len(parts) < 4:
        return None
    service, uploads, digest, name = parts[-4:]
    if service != SERVICE_DIRNAME or uploads != UPLOADS_DIRNAME:
        return None
    if not _CONTENT_ADDRESS.fullmatch(digest):
        return None
    return digest, name


# ─────────────────────────────────────────────────────────────────────────────
# 四、诊断留证：`<书目录>/_answer_lost/`
# ─────────────────────────────────────────────────────────────────────────────

#: 「答案流丢失」那一份的留证目录（`novel/llm/client.py` 模块 docstring ①：正文空、
#: 非截断、整段答案写进了 `reasoning_content`）。**一次一份文件**，文件名就是
#: `attempt_id` ⇒ 与成本账那一行、采纳审计账那一行靠同一个 id 联结。
#:
#: ⚠⚠ **它刻意【不】进 `<base>/_service/`。** 那份推理里装的是**这本书的正文**
#: （答案本来就该写进 `content`，只是走错了通道）⇒ 跟书走：删书即删证据，
#: 而跨书的服务目录里至今一个字正文都没有——这条不该由一份诊断来打破。
#: `_` 前缀与 `_rounds` / `_revalidation` 同类：它不是产物，完成度与阶段视图不看它。
#:
#: 对自由文本 `ask_text`，留证仍只作诊断而不从 reasoning 抠正文；对结构化
#: `ask_json`，整段 reasoning 只有重新通过与 content 完全相同的 parse、schema、
#: dynamic validator 与章节 scope 闸后才可采用，并由 checkpoint 继续绑定原始字节。
#: 因而它既回答「那条通道里到底装了什么」，也为一份已完整验证的结构化救回稿留来源证据。
ANSWER_LOST_DIRNAME = '_answer_lost'

#: 被结构化内容闸拒绝的供应商响应与受托修复决定。它与 `_answer_lost` 同样跟书走，
#: 但不是只写诊断：`--response-repair agent` 会把这里当成一个显式、可恢复的状态机。
#: 因此目录名仍用 `_` 隔离于正式产物，路径构造也必须拒绝外部传入的穿越片段。
REJECTED_RESPONSE_DIRNAME = '_llm_rejected'

#: Stage2 逐章响应在正式整书产物提交前的验证后检查点。它不是正式 artifact、
#: 不进完成度与来源账，也不是会被普通请求直接命中的 `.llm_cache`。目录跟书走，
#: 因为其中封存了模型响应正文；`run_id` 只负责把一次首轮、人工修复与唯一 resume
#: 绑成同一条恢复链。
STAGE2_RUNS_DIRNAME = '_stage2_runs'


def answer_lost_rel(attempt_id: str) -> str:
    """留证文件相对**书目录**的路径。审计账里存的就是它——绝对路径换台机器就断。"""
    return f'{ANSWER_LOST_DIRNAME}/{attempt_id}.json'


def answer_lost_path(novel_dir: str | os.PathLike[str], attempt_id: str) -> Path:
    """`<书目录>/_answer_lost/<attempt_id>.json`。形状只有 `answer_lost_rel` 一处。"""
    return Path(os.fspath(novel_dir)) / answer_lost_rel(attempt_id)


def rejected_response_rel(chain_id: str, attempt_id: str) -> str:
    """一次拒绝的隔离目录，相对**书目录**；两段均为完整小写十六进制身份。"""
    if not re.fullmatch(r'[0-9a-f]{64}', chain_id):
        raise PathRefused(f'LLM 修复链 id 必须是 64 位小写 SHA-256，实得 {chain_id!r}')
    if not re.fullmatch(r'[0-9a-f]{16}', attempt_id):
        raise PathRefused(f'LLM attempt_id 必须是 16 位小写十六进制，实得 {attempt_id!r}')
    return f'{REJECTED_RESPONSE_DIRNAME}/{chain_id}/{attempt_id}'


def rejected_response_path(novel_dir: str | os.PathLike[str], chain_id: str,
                           attempt_id: str) -> Path:
    """`<书目录>/_llm_rejected/<chain>/<attempt>/`；不是缓存或正式产物。"""
    return Path(os.fspath(novel_dir)) / rejected_response_rel(chain_id, attempt_id)


def stage2_blueprint_run_path(
        novel_dir: str | os.PathLike[str], run_id: str,
) -> Path:
    """`<书目录>/_stage2_runs/<run_id>/blueprint/`；run id 先过唯一身份闸。"""
    from novel.run_identity import check_run_id

    return (Path(os.fspath(novel_dir)) / STAGE2_RUNS_DIRNAME
            / check_run_id(run_id) / 'blueprint')


def stage2_blueprint_checkpoint_path(
        novel_dir: str | os.PathLike[str], run_id: str,
        phase: str, chapter: int,
) -> Path:
    """一章一相的检查点路径；phase/章号不允许成为路径注入面。"""
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', phase):
        raise PathRefused(f'Stage2 checkpoint phase 不合法：{phase!r}')
    if isinstance(chapter, bool) or int(chapter) < 1:
        raise PathRefused(f'Stage2 checkpoint 章号必须是正整数，实得 {chapter!r}')
    return (stage2_blueprint_run_path(novel_dir, run_id) / 'checkpoints'
            / f'{phase}-ch{int(chapter):04d}.json')
