#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
book_overlap.py —— **成书**与源章原文的 20 字窗口重合率，逐章报读数。

【为什么需要它：stage5 那把尺子量不到交付物】
`stage5/chNN.json` 里的 `overlap_excl_verbatim` 算在**换名之前**，
而用户读到的是 `stage7/<书名>_rewriting.txt` ——**换名之后**那一份。
两者之间隔着 `stage7.rename`，而 stage7 侧唯一带 overlap 字样的读数
（`rn_overlap`）量的是**人名映射的字面重叠**，与文本相似度无关。
⇒ 「成书跟原文有多像」这个问题，全链此前没有任何一处给得出读数。

【为什么必须先把人名换回去】
不回映射直接量，人名替换会把 20 字窗口从中间打断 ⇒ 读出来是**假的低**。
实测（无情道 23 章）：逐章中位 **0.128（不回映射）vs 0.223（回映射）**；
最悬殊的 ch12 是 **0.261 vs 0.542**——差了一倍。
⇒ 回映射不是「把判据调严」，是**把一个本来就存在的重合量出来**。
⚠ 反过来说，本工具**不能**用来证明 `stage7.rename` 做得好不好：
它刻意把改名这一维消掉，好让剩下的那一维（文本本身像不像）单独可读。

【口径与 stage5 逐位可比，因为用的就是它那一份实现】
`novel/stages/stage5/measure.py::overlap_ratio` —— 连同它的两条口径一起继承：
  · 在 `core.text.norm` 空间里算（**标点与空白早已去掉**，所以标点归一不影响读数）；
  · **扣掉 `writer_context` 里被要求逐字复现的那部分**（`mandated_verbatim`）——
    不扣的话，保真机制做得越好这个数越难看。
自测：无情道 ch04 本工具算出 0.511，与该章 `overlap_excl_verbatim` 逐位相同。

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

from contracts.invariants import BOOK_BLOCK_SEP, BOOK_CHAPTER_SEP  # noqa: E402
from novel.stages.stage5.measure import overlap_ratio  # noqa: E402


def split_book(text: str) -> dict[int, str]:
    """成书字节 -> `{输出章号: 正文}`。

    切法从 `assemble_txt` 的两个常量**派生**，不另写一份正则：章分隔是一个裸阿拉伯
    数字单独成块（`BOOK_CHAPTER_SEP`），块间是 `BOOK_BLOCK_SEP`。
    第一块是文案（`stage6.hook`），它前面没有章号，自然不入表。

    两个踩过的陷阱，都写在这里免得下一个人再踩一遍：

    1. **正文自己也用 `BOOK_BLOCK_SEP` 分段。** 「数字块的下一块就是正文」只会取到
       第一段——首版这么写，量出来每章只有 9~24 字、重合 1.000。
       正确做法是收到**下一个章号为止**。
    2. **正文里可能有单独成段的纯数字**（灵网跟帖、楼层号这类）。因此只认
       **恰好等于「上一章号 + 1」** 的数字块，其余数字块一律当正文。
       依据是产品定义：输出章号从 1 连续、恒等于源章号（`AGENTS.md`「架构要点」）。
    """
    assert BOOK_CHAPTER_SEP.format(7) == '7', 'BOOK_CHAPTER_SEP 变形了，切法要跟着改'
    out: dict[int, str] = {}
    cur: int | None = None
    buf: list[str] = []
    for b in text.split(BOOK_BLOCK_SEP):
        head = b.strip()
        nxt = 1 if cur is None else cur + 1
        if head.isdigit() and int(head) == nxt:
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


def back_map(name_map: dict) -> tuple[dict[str, str], tuple[str, ...]]:
    """`stage7/name_map.json` -> `({新名: 旧名}, kept)`，供**逆向**替换用。

    ⚠⚠ **反转的是 `subs`，不是自己从 `characters` 重拼一份。**
    `subs` 才是 `assemble_txt` 真正用的那张表——它已经把 `surname_map` 派生的
    「X家/X府」与人工补的 `extra_subs` 合并进去了。首版从 `characters` 拼，
    漏掉那两处，23 章里 ch18 一章读数就对不上（0.532 vs stage5 的 0.547）。
    这正是「同一件事的第二个落点必然分家」。

    ⚠ 反转要求 `new` 侧无撞车（两个旧名换成同一个新名就不可逆）。撞了当场报，
    不静默取其一——那会让读数悄悄偏低而没有任何人知道。
    """
    subs = name_map.get('subs') or {}
    back: dict[str, str] = {}
    for old, new in subs.items():
        if new in back:
            raise SystemExit(
                '{!r} 被两个旧名共用（{!r} / {!r}）=> 映射不可逆，'
                '本工具无法给出诚实读数'.format(new, back[new], old))
        back[new] = old
    return back, tuple(name_map.get('kept') or ())


def unname(s: str, back: dict[str, str], kept: tuple[str, ...]) -> str:
    """把成书里的新名换回原名。**走生产落点 `apply_subs`，不自己 replace。**

    ⚠ 链式 `str.replace` 会级联（`A->B` 与 `B->C` 一起跑时 A 变 C），
    `novel/stages/stage7/subs.py::apply_subs` 是单次扫描、游标不回头，
    级联在结构上不可表示（`R-10.2`）。逆向同样需要这条保证，
    而且它顺带带来最长优先（`sub_table` 按长度倒序进 alternation）。
    """
    from novel.stages.stage7.subs import apply_subs
    out, _counts, _claimed = apply_subs(s, back, kept=kept)
    return out


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8'))


def measure(base: Path, sid: str) -> dict:
    """逐章读数。**每一处读不到的都说出来，不静默跳过。**"""
    from novel.paths import artifact_path, book_txt_rel, resolve_novel_dir

    nd = resolve_novel_dir(base, sid)
    book_p = nd / book_txt_rel(nd)
    if not book_p.exists():
        raise SystemExit('没有成书：' + str(book_p) + NEWLINE
                         + '⇒ 这本书还没跑到 stage7，本工具没有可量的对象（不是「重合 0」）')

    ctx = _read(artifact_path(nd, 'stage4/writer_context.json'))
    main_text = _read(artifact_path(nd, 'stage1/main_text.json'))['payload']
    source_text = {c['chapter_ordinal']: c['text'] for c in main_text}
    entries = {c['output_ordinal']: c for c in ctx['chapters']}

    nm_p = artifact_path(nd, 'stage7/name_map.json')
    back, kept = back_map(_read(nm_p)) if nm_p.exists() else ({}, ())

    chapters = split_book(book_p.read_text(encoding='utf-8'))
    rows = []
    for o in sorted(chapters):
        entry = entries.get(o)
        if entry is None:
            rows.append({'output_ordinal': o, 'overlap': None,
                         'why': 'writer_context 里没有这一章'})
            continue
        body = unname(chapters[o], back, kept)
        rows.append({'output_ordinal': o,
                     'overlap': overlap_ratio(body, entry, source_text),
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

    if a.json_out:
        Path(a.json_out).write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'留档：{a.json_out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
