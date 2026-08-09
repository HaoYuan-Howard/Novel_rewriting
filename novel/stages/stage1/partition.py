#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
partition.py —— stage1：把一份小说原始文档【摄入 + 分章】成文案段与正文。

⚠ **原样移植自快速原型 `Stage1/n1_partition.py`，连注释一起搬。**
判据对全 161 本语料实跑标定过（命中 27 本 / 34 条真标记 / **0 假阳**，切点 75.9%~99.3%），
每一条分支背后都有一条实测。验收方式是**新旧两版跑全语料逐字节 diff**，
所以下面报告的**键顺序、warnings 与 notes 的文案一个字都不能动**。

关键不变量（需求 R-1）：
  * 【对原文只读】从不改写正文（连明显错别字也不改）；错别字是语义判断，归下游。
  * 【摄入不静默】见 `novel.core.text`。
  * 【番外等非正文一律不进正文】正文 = `[0, 第一个非正文标记)`。
    番外体在盘上**不留副本**，只把标记行/字数/首尾预览记进 `dropped[]`
    ——那是人核对「切对了没有」的唯一入口，没有它一次假阳会静默吃掉半本书。
  * 【章边界锚在独立成行的数字枚举行】半/全角数字、中文数字，可带括号或分隔符。
  * `chapter_no` 是原文标号（原样保留，含缺号/重号）；`chapter_ordinal` 才是修正后的阅读序。
  * `source_span` 是对**归一化后全文**的码点半开区间 `[start, end)`。
  * `warnings` 是可信度层（不同于静态 `notes`）：分章解析成功了、但结果**可疑到该有人瞄一眼**。
"""
from __future__ import annotations

import re

from contracts.invariants import (BODY_RATIO_MIN, NONBODY_HEAD_MAX_CP,
                                  NONBODY_KINDS, NONBODY_MID_MAX_CP)
from novel.core.text import TEXT_NORM

# 章节标记：整行只有一个数字枚举，半/全角数字或中文数字，可带括号或分隔符后缀。
# 语料标定过的形态（161 本全扫）：
#   1   1、  1.  1)      (01) （01）      (一) （一）      一  一、  一.      【2】  [2]
# Python3 str 模式下的 \d 已经能匹配全角 ０-９。
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
# 完结标记：（全文完）/ 全文完 / (正文完) / (完)。
# `正文完` 实测出现在 3 本里，且都紧挨着番外之前——它标的正是正文的终点，理应算数。
END_RE = re.compile(r'^[ \t　]*[（(]?\s*(?:全文完|正文完|完)\s*[)）]?[ \t　]*$')

# ── 非正文标记 ──────────────────────────────────────────────────────────────────
# 判据是对全 161 本语料实跑标定的，不是拍脑袋（命中 27 本、31 条真标记、0 假阳）：
#
#  · 实际出现的形态远多于「番外」「番外一」：`番外:`（半角冒号后什么都没有）· `(番外)` ·
#    `13 番外` · `15番外:药人` · `18.各种番外` · `12 婚后番外。` · `公主番外：` ·
#    `周京时番外` · `陆无咎番外。` · `番外温珩。` · `【番外：两年后】` · `番外一见琳琅误终身`。
#    所以：**先剥掉前导括号与数字前缀、尾部括号与句读，再看剩下的 core**，
#    并且允许「番外」出现在 core 的开头 / 中间 / 结尾。
#  · 「番外」这一支的长度上限是**假阳的唯一防线**。实测（core 的**码点**数，不是字节数）：
#    真标记 34 条 —— 开头分支最长 17（`番外：李恒日记（口述版，李福安代笔）`）、中/尾分支最长 6
#    （`贺家兄弟番外`）；含「番外」却是正文的只有 1 条，77 字（角色请作者「随便写个番外」的对白）。
#    17 与 77 之间空得很开，故 30 / 12 两条线各留了约 2× 余量。
#    ⚠ 这些数字**必须按码点数**：番外 一个词就是 6 字节，拿字节数当字数会把上限放大三倍。
#  · 后记/尾声 用**全等**而不是包含：散文里有「节目的尾声」（5 字）、「前厅的宴席似乎已经接近尾声」、
#    「孩子出生后记在你的名下」——它们比真标记还短，**长度上限救不了，全等才救得了**。
_NB_LEAD = re.compile(r'^[【\[(（]*(?:\d{1,3}[.、．:：]?[ 　]?)?')
_NB_TRAIL = re.compile(r'[】\])）。:：\s]*$')

LONG_MARKER_THRESHOLD = 20   # 超过实测上限（17）就出声；照样切，但要人确认一眼

# 以下三条是 stage1 局部的**可信度**阈值（不是正确性判据）：分章本身自洽，
# 但结果可疑到该有人瞄一眼。取值来自 161 本实扫里真实出现过的形态。
NOISY_DUPLICATE_THRESHOLD = 3        # 同一个 chapter_no 重复 3+ 次 = 某个固定行被误判成章号
LONG_UNSPLIT_BODY_THRESHOLD = 3000   # 只认出 ≤1 章却这么长 = 多半有第 5 种没覆盖的标记写法
MAX_DROP_RATIO = 0.35                # 丢弃占比上限（切点对了、但番外真有那么长吗）

# ⚠ **切点太靠前的告警线与「正文占比」那条 HARD 是同一个数**，故共用 `BODY_RATIO_MIN`。
# 原型里它们是两个各自写死的 `0.60`（一个在产地叫 `EARLY_CUT_RATIO`、一个在校验器里），
# 同一条线的第二个落点——改一个忘另一个不会报错，只会让产地放行而校验器判死。

_CN_DIGITS = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9}
_CN_UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000, '亿': 100000000}


def nonbody_kind(line: str) -> str | None:
    """一行是不是非正文标记？是则返回它的 kind（`NONBODY_KINDS` 之一），否则 None。"""
    s = (line or '').strip()
    if not s:
        return None
    core = _NB_TRAIL.sub('', _NB_LEAD.sub('', s))
    if not core:
        return None
    if core.startswith('番外'):
        return '番外' if len(core) <= NONBODY_HEAD_MAX_CP else None
    if '番外' in core:
        return '番外' if len(core) <= NONBODY_MID_MAX_CP else None
    if core.startswith('作者有话说'):
        return '作者有话说'
    return core if core in ('后记', '尾声') else None


def _cn_num_to_int(s: str) -> int:
    """中文数字的标准位值解析（十五→15，二十三→23，三千→3000）。"""
    total = section = num = 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        else:
            unit = _CN_UNITS[ch]
            if unit >= 10000:
                section = (section + num) * unit
                total += section
                section = 0
            else:
                section += (num or 1) * unit
            num = 0
    return total + section + num


def find_marker_lines(text: str):
    """→ `(chapter_markers, nonbody_markers, end_marker)`，全部是码点偏移。

    两个 marker 列表里 `line_start`/`content_start` 的口径相同：
    `content_start` 是标记行换行符之后的位置（正文从那里开始）。

    **优先级是严格的 if/elif 链**：章节标记 → 非正文标记 → 完结标记。
    章节标记优先是刻意的——`1` 这样的裸数字行不该被任何一条非正文规则抢走。
    """
    chapter_markers: list[dict] = []
    nonbody_markers: list[dict] = []
    end_marker = None
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line_start = offset
        line_end = offset + len(raw_line)
        content = raw_line.rstrip('\r\n')
        cm = CHAPTER_RE.match(content)
        if cm:
            if cm.group('d') is not None:
                chapter_no = int(cm.group('d'))
            elif cm.group('dp') is not None:
                chapter_no = int(cm.group('dp'))
            elif cm.group('bp') is not None:
                chapter_no = int(cm.group('bp'))
            elif cm.group('cp') is not None:
                chapter_no = _cn_num_to_int(cm.group('cp'))
            else:
                chapter_no = _cn_num_to_int(cm.group('c'))
            chapter_markers.append({
                'chapter_no': chapter_no,
                'line_start': line_start,
                'content_start': line_end,   # 正文从整个标记行之后开始
            })
        elif (kind := nonbody_kind(content)):
            nonbody_markers.append({
                'kind': kind,
                'title': content.strip(),
                'line_start': line_start,
                'content_start': line_end,
            })
        elif end_marker is None and END_RE.match(content):
            end_marker = line_start
        offset = line_end
    return chapter_markers, nonbody_markers, end_marker


def body_cut(text: str, chapter_markers: list[dict], nonbody_markers: list[dict]) -> int | None:
    """正文的终点：第一个非正文标记处；没有则 None。

    **要往回退一行**：语料里有 5 本把番外写成【数字行 + 番外标题行】两行式
    （如 行539=`14` / 行541=`番外-宋温瑶`）。那个数字是**番外自己的编号**，不是正文章。
    不退这一行的后果不是报错，是多出一个空章：`add()` 会把它当空白跳过，
    而 `num_chapters` 仍然把它算进去——报告与产物对不上，且没有任何一层会出声。
    """
    if not nonbody_markers:
        return None
    cut = nonbody_markers[0]['line_start']
    prev = [m for m in chapter_markers if m['line_start'] < cut]
    # 紧邻（中间只有空白）的章节标记行属于番外，一起切掉
    if prev and not text[prev[-1]['content_start']:cut].strip():
        cut = prev[-1]['line_start']
    return cut


def _preview(s: str, n: int) -> str:
    return re.sub(r'\s+', ' ', s).strip()[:n]


def build_sections(text: str, source_id: str):
    """切分。→ `(sections, main_markers, end_marker, body_limit, dropped)`。

    `sections` 只含 `hook_copy` 与 `main_chapter` 两种——**非正文不产 section**，
    它在盘上不留任何副本（用户裁决：彻底抛弃，不是「纳入别处」）。
    """
    markers, nonbody_markers, end_marker = find_marker_lines(text)
    cut = body_cut(text, markers, nonbody_markers)
    body_limit = min(x for x in (end_marker, cut, len(text)) if x is not None)
    sections: list[dict] = []

    # 正文章节标记只数到切点为止——番外内部的数字重启（`01`/`02`）不是新的正文章
    main_markers = [m for m in markers if m['line_start'] < body_limit]

    def add(kind, chapter_no, start, end, title=None, chapter_ordinal=None):
        raw = text[start:end]
        if raw.strip() == '':
            return  # 空隙跳过
        sections.append({
            'source_id': source_id,
            'section_id': f'sec_{len(sections) + 1:04d}',
            'section_kind': kind,
            'chapter_no': chapter_no,
            'chapter_ordinal': chapter_ordinal,   # 只在 main_chapter 之间从 1 数起的阅读序；
                                                  # 与 chapter_no（原文标号，缺号/重号原样保留）
                                                  # 不同，它恒无缺号无重号——下游一律 key 在它上面
            'title': title,
            'source_span': [start, end],          # 对归一化全文的码点半开区间
            'raw_text': raw,
            'suspected_typos': [],                # 有意留给下游（语义判断）
        })

    # 1) 第一个正文章标记之前那段无标题内容 → 开篇文案段
    first_content = main_markers[0]['line_start'] if main_markers else body_limit
    if first_content > 0:
        add('hook_copy', None, 0, first_content)

    # 2) 每一章从它的标记行之后跑到下一个标记（或正文终点）
    for i, mk in enumerate(main_markers):
        start = mk['content_start']
        end = main_markers[i + 1]['line_start'] if i + 1 < len(main_markers) else body_limit
        add('main_chapter', mk['chapter_no'], start, end, chapter_ordinal=i + 1)

    # 3) 被丢弃的非正文——**只记账，不落盘**。逐个标记切成一段，让人能逐条核对切得对不对。
    dropped = []
    if cut is not None:
        for i, hm in enumerate(nonbody_markers):
            # 第一段从 cut 起（可能比标记行更靠前一行，见 body_cut 的往回退）
            start = cut if i == 0 else hm['line_start']
            end = nonbody_markers[i + 1]['line_start'] if i + 1 < len(nonbody_markers) else len(text)
            body = text[start:end]
            dropped.append({
                'kind': hm['kind'],
                'marker_line': hm['title'],
                'line_no': text.count('\n', 0, hm['line_start']) + 1,
                'span': [start, end],
                'code_points': len(body),
                'head_preview': _preview(text[hm['content_start']:end], 80),
                'tail_preview': _preview(body[-120:], 40),
            })

    return sections, main_markers, end_marker, body_limit, dropped


def build_report(source_id, title, platform, text, sections, markers, end_marker,
                 body_limit, dropped, ingest) -> dict:
    """⚠ **键的插入顺序是承重的**（逐字节验收）。别为了「更有条理」重排。"""
    chapter_nos = [m['chapter_no'] for m in markers]
    gaps: list[int] = []
    duplicates: list[int] = []
    if chapter_nos:
        seen = set()
        for n in chapter_nos:
            if n in seen:
                duplicates.append(n)
            seen.add(n)
        lo, hi = min(chapter_nos), max(chapter_nos)
        gaps = [n for n in range(lo, hi + 1) if n not in seen]
    strictly_increasing = all(
        chapter_nos[i] < chapter_nos[i + 1] for i in range(len(chapter_nos) - 1)
    )
    hook = next((s for s in sections if s['section_kind'] == 'hook_copy'), None)
    total = len(text)
    dropped_cp = sum(d['code_points'] for d in dropped)
    body_ratio = round(body_limit / total, 4) if total else 0.0

    warnings: list[str] = []
    # 1) 同一个 chapter_no 重复 N+ 次，不是普通的缺章/重章笔误 —— 是某个反复出现的固定行
    #    （比如字面的 "1."）被当成了章节标记。
    dup_counts: dict[int, int] = {}
    for n in duplicates:
        dup_counts[n] = dup_counts.get(n, 0) + 1
    for n, extra_hits in sorted(dup_counts.items()):
        total_hits = extra_hits + 1
        if total_hits >= NOISY_DUPLICATE_THRESHOLD:
            warnings.append(
                f'chapter_no={n} 出现了 {total_hits} 次（≥{NOISY_DUPLICATE_THRESHOLD}），疑似某个反复出现'
                f'的固定标记被误判为章节号（而非普通缺章/重章），建议人工核对原文的标记规律。'
            )
    # 2) 正文很长却只认出 ≤1 章 —— 多半有一种标记写法没被覆盖。
    if len(markers) <= 1 and total > LONG_UNSPLIT_BODY_THRESHOLD:
        warnings.append(
            f'仅识别到 {len(markers)} 个正文章节，但全文 {total} 字（>{LONG_UNSPLIT_BODY_THRESHOLD}）— '
            f'可能存在 CHAPTER_RE/非正文标记未覆盖的写法，也可能确实是未分章的短篇，建议人工核对。'
        )
    # 3) 切点太靠前 / 丢得太多 —— 剔除是**破坏性**的，一次假阳会静默吃掉半本书。
    #    实测 27 本命中书的切点全在 76.0%~99.3%，落到 60% 以下几乎一定是切错了。
    if dropped:
        d0 = dropped[0]
        if body_ratio < BODY_RATIO_MIN:
            warnings.append(
                f'非正文切点落在全文 {body_ratio:.1%} 处（<{BODY_RATIO_MIN:.0%}），'
                f'标记行是 {d0["marker_line"]!r}（第 {d0["line_no"]} 行）——'
                f'实测真标记都在 76% 之后，这条极可能是误判，正文会因此少掉一大块，请人工核对。'
            )
        if total and dropped_cp / total > MAX_DROP_RATIO:
            warnings.append(
                f'非正文占了全文的 {dropped_cp / total:.1%}（>{MAX_DROP_RATIO:.0%}，共 {dropped_cp} 字）'
                f'——番外通常远短于正文，请人工核对切点。'
            )
        for d in dropped:
            core = _NB_TRAIL.sub('', _NB_LEAD.sub('', d['marker_line']))
            if len(core) > LONG_MARKER_THRESHOLD:
                warnings.append(
                    f'非正文标记行长达 {len(core)} 字（>{LONG_MARKER_THRESHOLD}，实测真标记上限 17）：'
                    f'{d["marker_line"]!r}（第 {d["line_no"]} 行）——已按标记处理，但请人工确认它不是正文句子。'
                )
    # 4) 完结标记之后还有内容，却既不在正文里也没被当成非正文 —— 那是真的丢了。
    if end_marker is not None:
        marker_line_end = text.find('\n', end_marker)
        trailing_start = marker_line_end + 1 if marker_line_end != -1 else total
        drop_start = dropped[0]['span'][0] if dropped else total
        lost = text[trailing_start:max(trailing_start, drop_start)]
        if lost.strip():
            warnings.append(
                f'"完结"标记之后还有 {len(lost.strip())} 字内容，当前会被静默丢弃（既不进正文，'
                f'也不是可识别的非正文标记）——需要人工确认是否要调整 NONBODY_KINDS 的判据或原文格式。'
            )

    return {
        'source_id': source_id,
        'title': title,
        'platform': platform,
        # ── 摄入记账（需求 Q-4：产物必须记住自己是从哪份上游来的） ──
        'original_filename': ingest['original_filename'],
        'original_path': ingest['original_path'],
        'source_sha1': ingest['sha1'],
        'source_encoding': ingest['encoding'],
        'text_norm': TEXT_NORM,
        # ── 分章 ──
        'total_code_points': total,
        'body_code_points': body_limit,        # [0, body_limit) = 文案 + 正文，即保留下来的部分
        'body_ratio': body_ratio,
        'num_sections': len(sections),
        'num_chapters': len(markers),
        'chapter_nos': chapter_nos,
        'chapter_no_gaps': gaps,               # 例 [5] → 第 5 章缺失
        'chapter_no_duplicates': duplicates,
        'chapter_nos_strictly_increasing': strictly_increasing,
        'has_end_marker': end_marker is not None,
        'hook_copy_present': hook is not None,
        'hook_copy_code_points': (hook['source_span'][1] - hook['source_span'][0]) if hook else 0,
        # ── 被丢弃的非正文：盘上唯一的记录 ──
        'dropped': dropped,
        'dropped_code_points': dropped_cp,
        'warnings': warnings,                  # 可信度层 → 该有人瞄一眼
        'notes': [
            'N1 is read-only; typo unification (e.g. 徐喻朗/徐瑜朗) is deferred to N2 entity resolution.',
            'chapter_no is the raw source label (kept as-is, gaps/dups included) — chapter_ordinal '
            'on each main_chapter section is the corrected, gap-free/duplicate-free reading-order '
            'index; downstream nodes should key off chapter_ordinal, not chapter_no.',
            '正文 = [0, 第一个非正文标记)。番外/后记/尾声/作者有话说 的正文体**不落盘**，'
            '只在 dropped[] 里留标记行、字数与首尾预览供人核对；chapter_no_gaps/duplicates '
            '只在正文章上计算。',
        ],
    }


def partition(text: str, source_id: str, title: str, platform: str | None, ingest: dict):
    """纯函数：归一化正文 → `(main_text, hook_copy, report)`。**不碰盘。**

    【为什么切成纯函数】原型里这一段与「写盘 / 覆盖保护 / 打印」缠在一个 `process()` 里，
    于是想验证切分对不对就必须落盘。纯函数化之后，新旧两版逐字节对拍不需要写任何文件。
    """
    sections, markers, end_marker, body_limit, dropped = build_sections(text, source_id)
    report = build_report(source_id, title, platform, text, sections, markers, end_marker,
                          body_limit, dropped, ingest)

    mains = sorted((s for s in sections if s.get('section_kind') == 'main_chapter'),
                   key=lambda s: s.get('chapter_ordinal') or 0)
    main_text = [{
        'section_id': s['section_id'],
        'chapter_ordinal': s['chapter_ordinal'],
        'source_chapter_label': (str(s['chapter_no']) if s.get('chapter_no') is not None else None),
        'text': s['raw_text'],
    } for s in mains]
    hook = next((s for s in sections if s.get('section_kind') == 'hook_copy'), None)
    hook_copy = {'section_id': hook['section_id'], 'text': hook['raw_text']} if hook else None
    return main_text, hook_copy, report
