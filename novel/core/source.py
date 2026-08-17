#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从产物记下来的那条源路径，找回**此刻真正能读的那份原文**——唯一落点。

`stage0/removal_report.json::original_path` 记的是一条**书目录之外的绝对路径**
（`docs/schema/stage0.md`：绝对路径；文件被移走时是「不知道」，不是「不匹配」）。
它有两种来源，处置**必须相反**：

  ① **外部语料**（`Test_data/xxx.txt` 之类）：被删被移就是**世界真的变了**，
     `ghost` 是正确答案，契约有意接受这个代价——补偿是报告里的
     `head_preview`/`tail_preview` 残片（`contracts/schema/stage0.py`：「丢了就没有
     第二次机会」）。**不许在这里"救"它**：多认一种形状就是把一条真信号洗成好消息。

  ② **界面上传的书**：原文住在 `<base>/_service/uploads/<sha256>/<name>`，那是
     **服务自己的目录**，跟着 base 走。Output 根一搬家，记下来的绝对路径就断，
     **而文件还在、字节一个没变**。这时报 `ghost` 是**假阴性**：变的不是世界，
     是我们自己给自己起的目录名。实测过一次——《夫君与鲛人孰_2024515569832010055》
     记的是 `<旧 base>/_service/uploads/cc7a2ac…/…txt`，文件就在当前 base 的**同一个
     内容地址目录**下，`source_sha1` 逐字节相等（`e807e63c7ff8…`，12441 码点），
     而 `state` 报 unknown/ghost、不带 `--source` 复跑直接 `UpstreamMissing`。

⇒ 只对 ② 重锚，且**必须自证内容地址**：那个目录名就是 `sha256(原始字节)`
  （产地唯一：`novel/orchestrator/uploads.py::save_upload`）。重锚拿到的候选必须
  `sha256(raw) == 目录名` 才采信，否则宁可继续答「不在」。**这一条不许摊到调用方**——
  摊出去就是四份会各自漂的实现，而它正是「回落永远不可能把另一份文件冒充成原文」
  的全部依据。校验失败与「压根没有候选」都返回 `None`：调用方看到的仍然是那条
  诚实的「原文不在记录位置」，不会凭空多出一份来路不明的正文。

⚠⚠ **记录的那条路径优先，重锚只是回落。顺序不许反。**
  `novel/stages/stage0/removal.py::_require_stage0_fresh` 在**写盘之后**再问一次
  freshness，用来发现「提交这一批的过程中源被换掉了」并整批回滚。记录的那条路径
  还在时，它才是那个能反映「世界变了」的读数——先看重锚会让那道闸拿到一份
  永远自洽的答案，于是「你批准的这份剔除，它的源已经不是刚才那份了」变成一句
  无人知晓的事。

⚠ 本模块**碰盘也碰内容**，因此不放 `novel/paths.py`（那边是纯路径运算，
  只有「找目录」那一类摸盘）。路径怎么拆仍然归它：`paths.uploaded_source_tail`。
  这里读的是**原始 txt**、不是产物，所以不经 `novel/store.py`——
  那条唯一读路管的是产物（同 `novel/views/ingest.py` 里那条既有注释）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from novel import paths


def resolve_recorded_source(
    recorded: str | os.PathLike[str],
    base: str | os.PathLike[str] | None,
) -> Path | None:
    """记录的那条源路径，此刻实际能读到的位置；`None` = 它真的不在盘上。

    `base` 给 `None` 表示调用方拿不到当前 Output 根，此时只问记录的那条路径本身
    （**不猜**）。返回的 Path 就是接下来该读的那一个，调用方照旧自己比对
    `source_sha1`——本函数只保证"这份文件是它自称的那份"，
    不代替任何人回答"它还是不是产物当初的那份"。
    """
    path = Path(os.fspath(recorded))
    if path.is_file():
        return path
    if base is None:
        return None

    tail = paths.uploaded_source_tail(path)
    if tail is None:
        return None
    digest, name = tail
    candidate = paths.uploads_dir(base) / digest / name
    if not candidate.is_file():
        return None
    try:
        raw = candidate.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != digest:
        return None
    return candidate
