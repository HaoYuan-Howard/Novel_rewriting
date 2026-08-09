#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text.py —— 摄入：字节 → 归一化正文。**这一层的每一条都是「不静默」的执行落点**（需求 Q-1）。

【归一化只做两件事】去 BOM、`\\r\\n|\\r → \\n`。
**不做 NFC/NFKC、不 strip 行尾**——存下来的就是分章要读的字节，
而下游每一个 span 都是对它的**码点偏移**；多做一步就静默平移全部偏移。

⚠ **原样移植自快速原型，连注释一起搬。**
"""
from __future__ import annotations

import codecs
import hashlib

#: 归一化口径的版次。改了这里就必须改这个串，
#: 否则新旧两批产物的 sha1 不可比而**没有任何东西会出声**。
TEXT_NORM = 'v1:bom+crlf'

# BOM 必须先于编码猜测判——否则一份 UTF-16 文本会被 gb18030 **成功**解码成一串夹着 \x00 的乱码，
# 「解码成功」于是不再意味着「解对了」。UTF-32 的 BOM 以 UTF-16 的 BOM 开头，必须排在前面。
_BOMS = (
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF32_LE, 'utf-32'), (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF16_LE, 'utf-16'), (codecs.BOM_UTF16_BE, 'utf-16'),
)
# 无 BOM 时的猜测顺序。gb18030 放最后：它几乎能解码任意字节序列，放前面会把 UTF-8 也「解成功」。
_FALLBACK_ENCODINGS = ('utf-8', 'gb18030')


class IngestError(Exception):
    """摄入的显式终态。

    【为什么是异常不是 `sys.exit`】原型用 `sys.exit` 当显式终态，而 `SystemExit` 是
    `BaseException`、`except Exception` 抓不到——于是把节点 import 进长驻进程跑，
    一次解码失败会**杀掉整个服务**，原型的界面层为此专门写了 `except SystemExit`。
    换成普通异常之后「显式终态」这个语义一点没变（调用方不接就照样终止），
    但它不再能穿透一个 `except Exception`。CLI 那一层负责把它翻译成非零退出码。
    """


def read_source(path: str) -> tuple[str, str, str]:
    """原始文件 → `(归一化正文, 实际编码, sha1)`。解不出来就入显式终态。"""
    with open(path, 'rb') as f:
        raw = f.read()
    bom_enc = next((e for bom, e in _BOMS if raw.startswith(bom)), None)
    # 【BOM 说了话就只信它】BOM 命中却解不开 = 文件损坏/截断，此时**不再回退**到 gb18030：
    # 那条回退恰恰是「解码成功但解错了」的来源，而我们加 BOM 探测就是为了堵住它。
    tried = [bom_enc] if bom_enc else list(_FALLBACK_ENCODINGS)
    text = enc = None
    for cand in tried:
        try:
            text, enc = raw.decode(cand), cand
            break
        except UnicodeDecodeError:
            continue
    if enc is None:
        raise IngestError(
            f'无法解码 {path}——试过 {tried}'
            + ('（BOM 指明了编码，故不再回退猜测：BOM 对不上说明文件已损坏）' if bom_enc else '')
            + '。请先把文件另存为 UTF-8。'
              '**不会用 errors=replace 兜底**：那会静默损坏正文，'
              '而下游所有「原文有据」类检查都建立在正文可信之上')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if '\x00' in text:
        raise IngestError(
            f'按 {enc} 解出来的正文里有 NUL 字符——'
            f'这几乎一定是解码解错了（典型：无 BOM 的 UTF-16 被当成单字节编码）')
    if not text.strip():
        raise IngestError(f'{path} 正文为空')
    return text, enc, hashlib.sha1(text.encode('utf-8')).hexdigest()
