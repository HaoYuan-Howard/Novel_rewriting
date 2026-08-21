#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从产物记下来的那条来源引用，找回**此刻真正能读的那份原文**——唯一落点。

`stage0/removal_report.json::source_ref` 是一个**判别联合**，两支的处置必须相反：

  ① **外部语料**（`external_file`，如 `Test_data/xxx.txt`）：被删被移就是
     **世界真的变了**，答「不在」是正确答案，契约有意接受这个代价——补偿是报告里的
     `head_preview`/`tail_preview` 残片（`contracts/schema/stage0.py`：「丢了就没有
     第二次机会」）。**不许在这里"救"它**：多认一种形状就是把一条真信号洗成好消息。

  ② **界面上传的书**（`managed_upload`）：原文住在
     `<base>/_service/uploads/<blob_sha256>/<filename>`，那是**服务自己的目录**、
     跟着 base 走。⇒ 它压根不记绝对路径，此刻在哪由当前 base 现算，
     Output 根搬家对它没有任何影响。

【2026-08-22 换成联合之前是什么样，以及为什么要换】
此前只有一格 `original_path: str`（绝对路径），这里的做法是**先试记录的那条路径，
断了再猜它是不是 `_service/uploads/<sha256>/<name>` 那种形状**并按当前 base 重锚。
那套回落逻辑本身是对的（实测救回过一本：《夫君与鲛人孰_2024515569832010055》——
记的是旧 base 下的上传目录，文件就在当前 base 的同一个内容地址目录下，
`source_sha1` 逐字节相等，而 `state` 报 unknown/ghost、复跑直接 `UpstreamMissing`），
**但它必须去猜**：同一个字符串既可能是外部语料也可能是本服务的上传，而两者
「找不到」的含义完全相反。⇒ 把「它是哪一种」从推断变成**产地当时就写下来**。

⇒ 换成联合之后，**这里没有"回落"这回事了**：每一支只有一条路。
  「记录的那条路径优先，重锚只是回落」那条顺序纪律随之作废——它守的是
  `_require_stage0_fresh`（写盘之后再问一次 freshness，用来发现「提交这一批的过程中
  源被换掉了」并整批回滚）拿到的是**能反映世界变了**的读数，而不是一份永远自洽的
  答案。现在这条性质由类型保证：`external_file` 本来就只认那一条绝对路径，
  没有任何别的候选可以顶上来；`managed_upload` 的候选必须自证内容地址，
  顶上来的只可能是**同一份字节**。

⚠ **内容地址的复算不许摊到调用方**：摊出去就是四份会各自漂的实现，而它正是
  「永远不可能把另一份文件冒充成原文」的全部依据。校验失败与「压根没有候选」都返回
  `None`：调用方看到的仍然是那条诚实的「原文不在记录位置」，不会凭空多出一份
  来路不明的正文。

⚠ 本模块**碰盘也碰内容**，因此不放 `novel/paths.py`（那边是纯路径运算，
  只有「找目录」那一类摸盘）。这里读的是**原始 txt**、不是产物，所以不经
  `novel/store.py`——那条唯一读路管的是产物（同 `novel/views/ingest.py` 里那条既有注释）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from contracts.schema.stage0 import ExternalFileRef, ManagedUploadRef, SourceRef
from novel import paths


def source_ref_for(path: str | os.PathLike[str]) -> SourceRef:
    """产地那一刻的判定：这条路径是外部语料还是本服务的上传？**唯一落点。**

    ⚠ 判定只在**写产物那一刻**做一次，之后就是产物里写死的事实。这正是换成判别
    联合的全部意义：读取端不必再从一个字符串里推断它是哪一种，
    而推断在 Output 根搬家之后会给出相反的答案。

    ⚠ 认的是**尾巴不是 base**（`paths.uploaded_source_tail`）：上传目录形如
    `<任意 base>/_service/uploads/<内容地址>/<文件名>`，只有这一种形状认得回来，
    其余一律当外部语料——多认一种形状就是把一条真信号洗成好消息。
    """
    tail = paths.uploaded_source_tail(path)
    if tail is None:
        return ExternalFileRef(
            kind='external_file', absolute_path=str(Path(os.fspath(path)).resolve()))
    digest, name = tail
    return ManagedUploadRef(
        kind='managed_upload', blob_sha256=digest, filename=name)


def describe_source(ref: SourceRef) -> str:
    """这条来源引用**读给人听**是什么样。错误消息与界面共用，不各写一份。"""
    if ref.kind == 'external_file':
        return ref.absolute_path
    return f'上传原文 {ref.filename}（内容地址 {ref.blob_sha256[:12]}…）'


def resolve_recorded_source(
    ref: SourceRef,
    base: str | os.PathLike[str] | None,
) -> Path | None:
    """这条来源引用此刻实际能读到的位置；`None` = 它真的不在盘上。

    `base` 给 `None` 表示调用方拿不到当前 Output 根。此时 `managed_upload` 一律
    答「不在」——它的位置**只能**由当前 base 算出来，没有第二种解法，
    而猜一个出来就是本模块整篇在反对的事。

    返回的 Path 就是接下来该读的那一个。调用方照旧自己比对 `source_sha1`——
    本函数只保证「这份文件是它自称的那份」，不代替任何人回答
    「它还是不是产物当初的那份」。
    """
    if ref.kind == 'external_file':
        path = Path(ref.absolute_path)
        return path if path.is_file() else None

    if base is None:
        return None
    candidate = paths.uploads_dir(base) / ref.blob_sha256 / ref.filename
    if not candidate.is_file():
        return None
    try:
        raw = candidate.read_bytes()
    except OSError:
        return None
    # ⚠ 目录名就是 `sha256(原始字节)`（产地唯一：
    #   `novel/orchestrator/uploads.py::save_upload`）。复算相等才采信。
    if hashlib.sha256(raw).hexdigest() != ref.blob_sha256:
        return None
    return candidate
