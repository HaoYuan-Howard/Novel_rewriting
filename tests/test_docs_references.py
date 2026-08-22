#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_docs_references.py —— 文档里的**引用**不许悬空。

【为什么要有】2026-08-10 的 stage1 审阅抓到五处问题，其中**两处是同一个形状**：
  · `stage1.md` §六 指着 `tests/fixtures_stage1.py`，而盘上叫 `fixtures_ingest.py`
  · `R-0.3` 拆成 `R-0.3a`/`R-0.3b` 之后，还有一处引用留在旧编号上

两处都**不会报错**——读的人会去找一个不存在的文件、或去查一条不存在的需求，
然后按自己的猜测把它读通。这正是判据 23 的文档版：
**读着已改名东西的消费方不会报错，它只是安静地什么也不做。**

本文件把这一类变成会红的测试。它查两件事：
  ① 文档里出现的每个 `R-x.y` / `Q-x` / `B-xx` 编号，在冻结后端契约里真的存在
  ② 文档里出现的每个**仓内路径**，盘上真的存在
"""
from __future__ import annotations

import os
import re

import pytest

from contracts import requirements as reqs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = reqs.load()

#: 扫哪些文档。产品 U/S 规格已并入冻结契约；本正则只识别后端 R/Q/B 编号。
#:
#: ⚠ `docs/LESSONS.md` 在这张表里，而它**多数条目都点名代码落点**（「唯一落点是 X」）
#: ——那正是本文件第 ② 条要查的东西。一份「经验」文档指着一个已经改名的模块，
#: 比没有这条经验更坏：读的人会去找它，然后按自己的猜测把它读通。
#: ⚠ `docs/AUDIT_*.md` 是**临时销账清单**（跑完一次全链之后逐条修，修完删文件），
#: 但它恰恰是引用密度最高的一类文档——每条都点名 `<模块>:<行>` 的根因与改法。
#: 一份指着已改名模块的修复清单，比没有这份清单更坏：照着改的人会先去找那个文件。
#: 通配符而不是逐份登记：文件删掉之后这条 glob 自己就空了，不会留下死配置。
#: ⚠ `AGENTS.md` 是 agent 的入口摘要（`CLAUDE.md` 是指向它的符号链接，glob 只 match 实体
#: 那一份，不会重复扫）。它必须在这张表里，因为**它是本仓引用密度最高的文档**：
#: 一整张「单一落点」表逐格点名模块，而读它的人（或模型）会拿这些路径当第一手线索去改代码。
#: 实测过这个形状的代价：README 那句「双击 `tools\start.cmd`」指着一个不存在的路径活了很久，
#: 而它没被抓住只是因为写的是**反斜杠**——本文件的 `_PATH` 只认正斜杠。
#: ⇒ 在这几份文档里写仓内路径时一律用正斜杠，否则等于自愿退出这道守卫。
#: ⚠ skill 文档也在这张表里。`.claude/skills/*/scripts/validate_skill.py` 查的是
#: markdown **链接**（`[文字](相对路径)`）的目标存在性与渐进披露闭合；反引号里的
#: **仓内路径**和 R/Q/B 编号一直无人查，两边的域不重叠、不是重复。
DOC_GLOBS = (
    'docs/schema/*.md',
    'docs/AUDIT_*.md',
    'docs/BACKEND_CONTRACT.md',
    'docs/REQUIREMENTS.md',
    'docs/LESSONS.md',
    'README.md',
    'AGENTS.md',
    '.claude/skills/*/SKILL.md',
    '.claude/skills/*/references/*.md',
    '.claude/skills/*/assets/*.md',
)

#: 只有这些前缀才当「仓内路径」看。`stage0/clean_text.txt` 这类是**产物路径**，不在仓里。
REPO_PREFIXES = ('tests/', 'contracts/', 'tools/', 'novel/', 'docs/', '.claude/')

#: **文档明说「它不在仓里」的那几条路径**，逐条登记、逐条写理由。
#:
#: 【为什么是手写登记表，而不是「凡被 .gitignore 忽略就放行」】
#: 两种烂法成本相同、方向相反（同 `novel/api/enums.py` 的三张登记表）：
#: 自动放行**静默地烂**——一整类路径被挪出扫描面，从此这里写错谁都不知道；
#: 手写登记**响亮地烂**——文档新引一条不存在的路径当场红，必须二选一：
#: 补出这个文件，或者登记进来并写出「为什么它本来就不该在仓里」。
#: ⚠ 这不是「已知豁免」，是一份账：每条理由必须说得出**文档那句话在说什么**。
#: ⚠ 只放行**逐字全等**的路径，不做前缀匹配——放行 `.claude/settings.local.json`
#: 不该顺带把 `.claude/settings.local.json.bak` 一起放行。
_DECLARED_ABSENT: dict[str, str] = {
    '.claude/settings.local.json': (
        '被 `.gitignore` 忽略、只存在于各人自己的工作区。README 与 `docs/REQUIREMENTS.md` '
        '里引用它的那两句话说的**正是它不存在**（「根 `.env`、`outputs/`、`Test_data/`…'
        '不存在」那一段）⇒ 要求它在盘上等于要求文档自我否定，任何新 clone / worktree '
        '都会红，而红的是宿主环境不是文档'),
}

_ID = re.compile(r'`(R-\d+\.\d+[a-z]?|Q-\d+|B-\d+)`')
#: ⚠ 允许尾随 `::符号` 或 `['键']`：`novel/paths.py::edit_rel` 这种写法在
#: skill 经验库里是标准形态，只取路径那一段，符号那一段由 skill 校验器自己查。
_PATH = re.compile(
    r'`((?:tests|contracts|tools|novel|docs|\.claude)/[A-Za-z0-9_./-]+)'
    r'(?:::[A-Za-z0-9_]+(?:\[[^\]]*\])?)?`')


def _docs() -> list[str]:
    import glob
    out: list[str] = []
    for pat in DOC_GLOBS:
        out += sorted(glob.glob(os.path.join(ROOT, pat)))
    return out


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_referenced_requirement_id_exists(doc):
    """引用一个不存在的需求编号 = 需求改过而文档没跟上。"""
    text = open(doc, encoding='utf-8').read()
    known = set(DOC.requirements)
    missing = sorted({m for m in _ID.findall(text) if m not in known})
    assert not missing, (
        f'{_rel(doc)} 引用了不存在的编号：{missing}\n'
        f'→ 需求改过（拆分/改名/删除）而这里没跟上。'
        f'改编号时要 grep 全部文档，不只是校验器'
    )


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_referenced_repo_path_exists(doc):
    """引用一个不存在的文件 = 读的人会去找它，然后按自己的猜测把文档读通。"""
    text = open(doc, encoding='utf-8').read()
    bad = []
    for path in sorted(set(_PATH.findall(text))):
        if path in _DECLARED_ABSENT:      # 文档明说它不在仓里，见上表
            continue
        # 目录形（以 / 结尾）与文件形都接受
        full = os.path.join(ROOT, path)
        if not (os.path.exists(full) or os.path.exists(full.rstrip('/'))):
            bad.append(path)
    assert not bad, (
        f'{_rel(doc)} 引用了盘上不存在的路径：{bad}\n'
        f'→ 文件改名/搬家了而文档没跟上。这类引用不会报错，只会静默误导。'
        f'若它本来就不该在仓里（被 .gitignore 忽略等），'
        f'登记进 `_DECLARED_ABSENT` 并写出理由，别把它从扫描面上摘掉'
    )


def test_declared_absent_entries_are_still_live_and_still_absent():
    """登记表自己的守卫：一条**死登记**比没有登记更坏。

    两侧都查，因为两侧都会烂：
      · 没有任何文档还在引用它 ⇒ 这条登记已经是死配置，删掉；
      · 它现在**真的在盘上**了 ⇒ 放行的前提没了，这条必须撤销，
        否则它从此把一条真能查的路径静默挪出扫描面（那正是本表拒绝
        「凡 gitignore 就放行」的同一个理由）。
    """
    referenced = set()
    for doc in _docs():
        referenced |= set(_PATH.findall(open(doc, encoding='utf-8').read()))
    for path, why in _DECLARED_ABSENT.items():
        assert why.strip(), f'{path} 登记了却没写理由'
        assert path in referenced, (
            f'{_DECLARED_ABSENT} 里的 {path} 已经没有任何文档引用 ⇒ 死配置，删掉这一条')
        full = os.path.join(ROOT, path)
        assert not (os.path.exists(full) or os.path.exists(full.rstrip('/'))), (
            f'{path} 现在真的在盘上了 ⇒ 放行的前提没了，'
            f'把这条从 `_DECLARED_ABSENT` 撤销，让它回到扫描面上')


def test_the_scan_actually_finds_something():
    """守卫自己的守卫：正则失配时上面两条会**全绿**，而那正是最坏的情况。

    「没报违规」与「根本没在查」必须能区分——这条测试自己也不例外。
    """
    ids = paths = 0
    for doc in _docs():
        text = open(doc, encoding='utf-8').read()
        ids += len(_ID.findall(text))
        paths += len(_PATH.findall(text))
    assert ids > 100, f'只扫到 {ids} 个编号引用——正则多半失配了'
    assert paths > 10, f'只扫到 {paths} 个路径引用——正则多半失配了'
