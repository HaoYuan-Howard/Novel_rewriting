#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
book_overlap.py —— **成书**与源章原文的 20 字窗口重合率，逐章报读数。

【为什么需要它：交付物那一侧没有这把尺子】
用户读到的是 `stage7/final/<书名>_rewriting.txt`——**换名之后**那一份。
新链的章级机械验（`novel/stages/stage5/body_checks.py`）量篇幅带与泄漏，
不量「成书跟原文有多像」；这个问题此前也没有任何一处给得出读数。
⇒ 本工具把那个读数单独摆出来。

【为什么必须先把人名换回去】
不回映射直接量，人名替换会把 20 字窗口从中间打断 ⇒ 读出来是**假的低**。
实测（无情道 23 章）：逐章中位 **0.128（不回映射）vs 0.223（回映射）**；
最悬殊的 ch12 是 **0.261 vs 0.542**——差了一倍。
⇒ 回映射不是「把判据调严」，是**把一个本来就存在的重合量出来**。

【F8a 换新链坐标后的两处口径变化（如实登记，不与旧读数混批比较）】
① 回映射的表**由新链 compile 现场派生**：`stage7/naming/name_map.json`
   （NameMapV5）＋`stage2/characters/characters.json`（Characters v4）经
   `novel/stages/stage7/naming.py::compile_surface_replacements` 得到替换表
   ——与 assembly 落成书用的是同一条派生路径，工具不自己重拼一份。
   逆向替换走 `novel/stages/common.py::apply_replacements`
   （单遍、长名优先、游标不回头，`R-10.2` 的结构保证）。
② 旧链 `stage5/measure.py::overlap_ratio` 的 `mandated_verbatim` 豁免面
   **在新链没有对应物**（WriterBookIR／ChapterIR 无逐字材料字段，str06
   guard 无 verbatim 机制）⇒ 重合计算用共享实现
   `novel/core/text.py::window_overlap` 且 `exclude=''`：读数是**不扣豁免**
   的全量 20 字窗重合，与旧链 `overlap_excl_verbatim` **不可逐位可比**
   （旧链扣了被要求逐字复现的部分；新链没有那部分，「保真机制做得越好
   读数越难看」的旧警告随之失去对象）。

【章号恒等，不再需要 from_source_ordinals】
`I-40`：输出章号恒等于源章号 ⇒ 源章文本按同一 ordinal 从
`stage1/main_text.json` 直取，不需要旧 writer_context 的章节映射表。

【它不判，也刻意不定阈值】
退出码**恒为 0**。`B-03`（`docs/BACKEND_CONTRACT.md`）在 2026-08-10 裁决过
「闭卷定线、开卷不定」，理由是「0.9%~96.8% 的跨度不是一个可以一刀切的量」——
那条理由今天仍然成立。这里发明一个阈值正是 `Q-9`/判据 15 反对的事。
⇒ 本工具只把读数摆出来，判断留给人（判据 21：度量与告警不共用同一个分支）。

用法：
    python tools/book_overlap.py --base <outputs 目录> --sid <书 sid>
    python tools/book_overlap.py --base <outputs> --sid <sid> --json-out <路径>
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import BOOK_BLOCK_SEP  # noqa: E402
from contracts.schema import decode_artifact  # noqa: E402
from novel.core.text import window_overlap  # noqa: E402
from novel.paths import artifact_path, book_txt_rel, resolve_novel_dir  # noqa: E402
from novel.stages.common import apply_replacements  # noqa: E402
from novel.stages.stage7 import compile_surface_replacements  # noqa: E402

NEWLINE = '\n'

#: 现役成书章标记：「第N章」单独成块（`assembly.py::_chapter_marker`）。
#: 旧链成书用的是裸阿拉伯数字行（已随旧链退役），这里两种形态都认，
#: 让工具对旧根与新根都能切块。
_CHAPTER_MARKER = '第{}章'


def _is_chapter_marker(head: str, nxt: int) -> bool:
    if head == _CHAPTER_MARKER.format(nxt):
        return True
    return head.isdigit() and int(head) == nxt


def split_book(text: str) -> dict[int, str]:
    """成书字节 -> `{输出章号: 正文}`。

    章分隔是一个章标记单独成块（现役「第N章」；旧链为裸阿拉伯数字，两种都认），
    块间是 `BOOK_BLOCK_SEP`。
    第一块是文案（新链 Hook 成文 `stage5/hook/hook.txt` 的装配块），它前面没有
    章号，自然不入表。

    两个踩过的陷阱，都写在这里免得下一个人再踩一遍：

    1. **正文自己也用 `BOOK_BLOCK_SEP` 分段。** 「标记块的下一块就是正文」只会取到
       第一段——首版这么写，量出来每章只有 9~24 字、重合 1.000。
       正确做法是收到**下一个章标记为止**。
    2. **正文里可能有单独成段的纯数字或形似标记的行**（灵网跟帖、楼层号这类）。
       因此只认**恰好等于「上一章号 + 1」** 的标记块，其余一律当正文。
       依据是产品定义：输出章号从 1 连续、恒等于源章号（`AGENTS.md`「架构要点」）。
    """
    out: dict[int, str] = {}
    cur: int | None = None
    buf: list[str] = []
    for b in text.split(BOOK_BLOCK_SEP):
        head = b.strip()
        nxt = 1 if cur is None else cur + 1
        if _is_chapter_marker(head, nxt):
            if cur is not None:
                out[cur] = BOOK_BLOCK_SEP.join(buf).strip()
            cur, buf = nxt, []
        elif cur is not None:
            buf.append(b)
    if cur is not None:
        # ⚠ 末章要 strip：`assemble_txt` 的返回值是 `join(parts) + 换行`，
        #   那个尾随换行会落进最后一块。写入侧每块都 `body.strip()` 过，
        #   这里对称地剥掉——不剥不影响重合读数（`norm` 会去空白），但会让
        #   `chars` 与「正文有多长」差一个字节，而那是要被人读的数。
        out[cur] = BOOK_BLOCK_SEP.join(buf).strip()
    return out


def back_map(table: dict) -> tuple[dict[str, str], tuple[str, ...]]:
    """替换表容器 -> `({新名: 旧名}, kept)`，供**逆向**替换用。

    ⚠⚠ **反转的是 compile 派生的替换表，不是从 NameMap 条目或 Characters
    自己重拼一份。** 新链里那张表的唯一权威是
    `novel/stages/stage7/naming.py::compile_surface_replacements`（与 assembly
    落成书用的是同一条派生路径）。工具侧重拼就会出现「读数用的映射与成书
    实际用的映射不是一张」——首版（旧链）从 `characters` 重拼，漏掉
    `surname_map` 派生项与人工 `extra_subs`，23 章里 ch18 一章读数就对不上
    （0.532 vs stage5 的 0.547）。这正是「同一件事的第二个落点必然分家」。

    ⚠ 反转要求 `新名` 侧无撞车（两个旧名换成同一个新名就不可逆）。撞了当场报，
    不静默取其一——那会让读数悄悄偏低而没有任何人知道。

    `kept` 在新链没有落点：`compile_surface_replacements` 只登记
    「值 != 字面」的键，保留名（`keep_reason` 条目）天然恒等；参数保留是为
    兼容本容器形状的历史用法，主流程恒传 `()`。
    """
    subs = table.get('subs') or {}
    back: dict[str, str] = {}
    for old, new in subs.items():
        if new in back:
            raise SystemExit(
                '{!r} 被两个旧名共用（{!r} / {!r}）=> 映射不可逆，'
                '本工具无法给出诚实读数'.format(new, back[new], old))
        back[new] = old
    return back, tuple(table.get('kept') or ())


def unname(s: str, back: dict[str, str]) -> str:
    """把成书里的新名换回原名。**走新链替换语义的共享实现，不自己 replace。**

    `novel/stages/common.py::apply_replacements` 是单遍扫描、
    长名优先、游标不回头：链式 `str.replace` 的级联（`A->B` 与 `B->C` 一起跑时
    A 变 C）在结构上不可表示（`R-10.2`）。它与 assembly 的 ApplyNames 同源，
    逆向替换因此与正向替换逐位同语义。kept 名不在 `back` 里，天然恒等——
    新链没有旧 `apply_subs` 的 kept 参数（保留名不产生键）。
    """
    out, _claims = apply_replacements(s, back)
    return out


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8'))


def substitution_table(novel_dir: Path) -> dict:
    """NameMap v5 + Characters v4 -> 反向替换表容器（compile 现场派生）。

    与 `str06.stage7.assemble` 落成书之前完全相同的一步：读两份 canonical
    产物、走 `compile_surface_replacements`。compile 侧报出的错误（同一源字面
    多个值 / 链式替换风险）当场停止——那是产线自己的拒绝理由，不是读数问题。
    """
    name_map = decode_artifact(
        'stage7/naming/name_map.json',
        _read(artifact_path(novel_dir, 'stage7/naming/name_map.json')))
    characters = decode_artifact(
        'stage2/characters/characters.json',
        _read(artifact_path(novel_dir, 'stage2/characters/characters.json')))
    table, errors = compile_surface_replacements(
        characters.payload, name_map.payload)
    if errors:
        raise SystemExit('替换表派生失败（与 assembly 同一判据）：'
                         + NEWLINE + NEWLINE.join(errors))
    return {'subs': table, 'kept': ()}


def measure(base: Path, sid: str) -> dict:
    """逐章读数。**每一处读不到的都说出来，不静默跳过。**"""
    nd = resolve_novel_dir(base, sid)
    book_p = nd / book_txt_rel(nd)
    if not book_p.exists():
        raise SystemExit('没有成书：' + str(book_p) + NEWLINE
                         + '⇒ 这本书还没跑到 str06.stage7.assemble，本工具没有可量的对象'
                           '（不是「重合 0」）')

    main_rel = 'stage1/main_text.json'
    main = decode_artifact(main_rel, _read(nd / main_rel))
    source_text = {c.chapter_ordinal: c.text for c in main.payload.root}

    nm_p = artifact_path(nd, 'stage7/naming/name_map.json')
    back, kept = back_map(substitution_table(nd)) if nm_p.exists() else ({}, ())

    chapters = split_book(book_p.read_text(encoding='utf-8'))
    rows = []
    for o in sorted(chapters):
        source = source_text.get(o)
        if source is None:
            rows.append({'output_ordinal': o, 'overlap': None,
                         'why': '源文（stage1/main_text.json）里没有这一章'})
            continue
        body = unname(chapters[o], back)
        rows.append({'output_ordinal': o,
                     'overlap': window_overlap(body, source),
                     'chars': len(body.strip()),
                     'renamed_back': body != chapters[o]})
    return {'sid': sid, 'novel_dir': str(nd), 'book': str(book_p),
            'renames_applied': len(back), 'chapters': rows}


def main() -> int:
    ap = argparse.ArgumentParser(description='成书 vs 源章的 20 字窗重合率（只读、只报读数）')
    ap.add_argument('--base', required=True, help='含各书目录的那一层')
    ap.add_argument('--sid', required=True)
    ap.add_argument('--json-out', help='把读数留档到这个路径')
    a = ap.parse_args()

    res = measure(Path(a.base), a.sid)
    rows = res['chapters']
    print(f"成书：{res['book']}")
    print(f"人名回映射：{res['renames_applied']} 组"
          + ('' if res['renames_applied'] else '（没有 name_map，读数是【未回映射】口径）'))
    print()
    print(f"{'ch':>4}{'重合':>9}{'字数':>8}  说明")
    for r in rows:
        ov = r.get('overlap')
        shown = '量不了' if ov is None else f'{ov:.3f}'
        print(f"{r['output_ordinal']:>4}{shown:>9}{r.get('chars', 0):>8}  {r.get('why', '')}")

    got = [r['overlap'] for r in rows if r.get('overlap') is not None]
    print()
    if got:
        print(f'逐章：中位 {statistics.median(got):.3f} · '
              f'最高 {max(got):.3f} · 最低 {min(got):.3f} · n={len(got)}')
    unmeasured = [r['output_ordinal'] for r in rows if r.get('overlap') is None]
    # ⚠ 「量不了」要单独说出来，不许并进分母（判据 7：那不是「重合 0」）。
    print(f'量不了：{unmeasured or "无"}')
    print()
    print('⚠ 这是**度量不是判据**：没有阈值，退出码恒 0。'
          '`B-03` 2026-08-10 裁决「开卷不定线」，那条理由至今成立。')
    print('⚠ 口径：`core.text.window_overlap` 全量 20 字窗重合（新链无逐字豁免面，'
          '不与旧链 `overlap_excl_verbatim` 逐位可比）。')

    if a.json_out:
        Path(a.json_out).write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'留档：{a.json_out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
