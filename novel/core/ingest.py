#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0/stage1 共用的摄入词法。纯函数，不碰盘、不写产物。

最重要的不变量是：非正文标记只有这一份实现。stage0 用它决定删除什么，stage1
用它断言批准后的文本里没有残留；两边各抄一份会让同一行在边界两侧得到两个答案。

章节词法与中文数字解析从原型 n1_partition.py 搬来。阈值不在这里重抄，全部取自
contracts.invariants，包括已由 30 收紧到 20 的番外开头上限。
'''
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass

from contracts.invariants import (
    END_MARKER_WORDS,
    NONBODY_HEAD_MAX_CP,
    NONBODY_LEAD_STRIP,
    NONBODY_MID_MAX_CP,
    NONBODY_TRAIL_STRIP,
)
from novel import prov

TEXT_NORM = 'v1:bom+crlf'

# BOM 必须先于猜测；UTF-32 又必须排在 UTF-16 前，因为其 BOM 以前者开头。
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF32_LE, 'utf-32'),
    (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF16_LE, 'utf-16'),
    (codecs.BOM_UTF16_BE, 'utf-16'),
)
_FALLBACK_ENCODINGS = ('utf-8', 'gb18030')

_CN_NUM = r'[一二三四五六七八九十百千万亿零两]{1,8}'
CHAPTER_RE = re.compile(
    r'^[ \t　]*(?:'
    r'(?P<d>\d{1,4})[、.．)）]?'
    r'|[(（](?P<dp>\d{1,4})[)）]'
    r'|[【\[](?P<bp>\d{1,4})[】\]]'
    r'|[(（](?P<cp>' + _CN_NUM + r')[)）]'
    r'|(?P<c>' + _CN_NUM + r')[、.．)）]?'
    r')[ \t　]*$'
)
END_RE = re.compile(
    r'^[ \t　]*[（(]?\s*(?:' + '|'.join(map(re.escape, END_MARKER_WORDS))
    + r')\s*[)）]?[ \t　]*$'
)
_NB_LEAD = re.compile(NONBODY_LEAD_STRIP)
_NB_TRAIL = re.compile(NONBODY_TRAIL_STRIP)

_CN_DIGITS = {
    '零': 0, '一': 1, '二': 2, '两': 2, '三': 3,
    '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}
_CN_UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000, '亿': 100000000}


class DecodeProblem(ValueError):
    '''原始字节无法按摄入契约无损解释。由 stage0 翻成显式终态。'''


@dataclass(frozen=True)
class DecodedSource:
    text: str
    encoding: str
    source_sha1: str


@dataclass(frozen=True)
class ChapterMarker:
    chapter_no: int
    line_start: int
    content_start: int


@dataclass(frozen=True)
class NonbodyMarker:
    kind: str
    title: str
    core: str
    line_start: int
    content_start: int


@dataclass(frozen=True)
class ScanResult:
    chapters: tuple[ChapterMarker, ...]
    nonbody: tuple[NonbodyMarker, ...]
    end_marker: int | None


def normalize_newlines(text: str) -> str:
    '''归一化只做 BOM/换行两件事；不 NFC、不 strip、不改行尾空白。'''
    if text.startswith('\ufeff'):
        text = text[1:]
    return text.replace('\r\n', '\n').replace('\r', '\n')


def decode_source_bytes(raw: bytes) -> DecodedSource:
    '''原始字节转无损文本、实际编码、归一化文本的 40 位身份。

    BOM 一旦说了话就只信它；命中 BOM 却解不开表示文件损坏，不能再让几乎什么都能
    解的 GB18030 把坏字节成功吞掉。
    '''
    bom_encoding = next((enc for bom, enc in _BOMS if raw.startswith(bom)), None)
    tried = (bom_encoding,) if bom_encoding else _FALLBACK_ENCODINGS
    text: str | None = None
    encoding: str | None = None
    errors: list[str] = []
    for candidate in tried:
        try:
            text = raw.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError as exc:
            errors.append(f'{candidate}: {exc}')
    if text is None or encoding is None:
        suffix = '；BOM 已指定编码，故不再回退猜测' if bom_encoding else ''
        detail = ' | '.join(errors)
        raise DecodeProblem(f'无法无损解码（试过 {list(tried)}{suffix}）：{detail}')

    text = normalize_newlines(text)
    if '\x00' in text:
        raise DecodeProblem(
            f'按 {encoding} 解出的正文含 NUL；典型原因是无 BOM UTF-16 被单字节编码误接收')
    if not text.strip():
        raise DecodeProblem('正文为空')
    return DecodedSource(
        text=text,
        encoding=encoding,
        source_sha1=prov.content_sha1(text.encode('utf-8')),
    )


def marker_core(line: str | None) -> str:
    '''标记整行转剥前导括号/数字与尾部句读后的 core。'''
    stripped = (line or '').strip()
    return _NB_TRAIL.sub('', _NB_LEAD.sub('', stripped)) if stripped else ''


def nonbody_kind(line: str | None) -> str | None:
    '''一行是否为非正文标记；命中时返回闭合 kind。'''
    core = marker_core(line)
    if not core:
        return None
    if core.startswith('番外'):
        return '番外' if len(core) <= NONBODY_HEAD_MAX_CP else None
    if '番外' in core:
        return '番外' if len(core) <= NONBODY_MID_MAX_CP else None
    if core.startswith('作者有话说'):
        return '作者有话说'
    return core if core in ('后记', '尾声') else None


def chinese_number(value: str) -> int:
    '''中文位置数词转整数：十五到 15、二十三到 23、三千零一到 3001。'''
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
            continue
        unit = _CN_UNITS[char]
        if unit >= 10000:
            section = (section + number) * unit
            total += section
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    return total + section + number


def chapter_number(line: str) -> int | None:
    match = CHAPTER_RE.match(line)
    if match is None:
        return None
    for name in ('d', 'dp', 'bp'):
        if match.group(name) is not None:
            return int(match.group(name))
    return chinese_number(match.group('cp') or match.group('c'))


def scan_text(text: str) -> ScanResult:
    '''一次扫描得到章节、非正文与第一个完结标记的码点偏移。

    优先级严格为章节、非正文、完结。裸数字章节行绝不能被另一条规则抢走。
    '''
    chapters: list[ChapterMarker] = []
    nonbody: list[NonbodyMarker] = []
    end_marker: int | None = None
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line_start = offset
        line_end = offset + len(raw_line)
        content = raw_line.rstrip('\r\n')
        number = chapter_number(content)
        if number is not None:
            chapters.append(ChapterMarker(number, line_start, line_end))
        elif (kind := nonbody_kind(content)) is not None:
            nonbody.append(NonbodyMarker(
                kind=kind,
                title=content.strip(),
                core=marker_core(content),
                line_start=line_start,
                content_start=line_end,
            ))
        elif end_marker is None and END_RE.match(content):
            end_marker = line_start
        offset = line_end
    return ScanResult(tuple(chapters), tuple(nonbody), end_marker)


def nonbody_cut(text: str, scan: ScanResult) -> int | None:
    '''第一个非正文切点；两行式番外把紧邻的数字行一并回卷。'''
    if not scan.nonbody:
        return None
    cut = scan.nonbody[0].line_start
    previous = [marker for marker in scan.chapters if marker.line_start < cut]
    if previous and not text[previous[-1].content_start:cut].strip():
        cut = previous[-1].line_start
    return cut


def tail_is_only_end_marker(tail: str) -> bool:
    '''R-1.14：尾段为空，或 strip 后恰好是一条完结标记。'''
    stripped = tail.strip()
    return not stripped or END_RE.fullmatch(stripped) is not None
