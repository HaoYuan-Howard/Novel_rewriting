#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
canary_chapters.py —— B6-A canary 选章工具（只读、零成本）。

【为什么是机械选章】B6-A 先买三章 canary 再决定是否续跑全书。选哪三章不能
看手感：开篇章（第 1 章）固定入座；中段章取「线程操作数＋跨章读取数」最大
的一章（线程与跨章依赖最密集，写砸了最贵）；后段章取「本章读者确认数＋线程
收束数」最大的一章（收束与揭示最密，提前披露风险最高）。平分取章序较小者，
任一段为空即报错（章数不够就不猜）。

【计分口径】全部从**已封存**的 `stage4/ir/writer_book_ir.json` 机械计数：
  · 跨章读取——声明的入场读取（`entry_state`／`entry_knowledge`）与拍级读取
    （`BeatIR.reads`）里 `established_by` 落在更早章的条数；初态（`init`）
    不计，编译器按参与者补齐的背景（`background_*`）不计；
  · 读者确认——`in_scene` 为真的 pending 确认，按事实地址＋确认位置去重；
  · 线程操作／线程收束——本章拍上的线程操作总数与其中 `op=close` 的条数。

【它不做什么】不发起任何模型调用、不写任何盘面文件；只读数、打印逐章计分、
选中三章与下一条 canary 命令。读口走 `Str06Repository.read_model`（R6 统一
发布权威）：未封存的 IR 直接拒读，不拿诊断字节当选章依据。

用法：
    python tools/canary_chapters.py --base <NOVEL_OUTPUT> --sid <sid>
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel import paths                                        # noqa: E402
from novel.store import Str06Repository                        # noqa: E402

#: 候选链全书静态写作 IR 的家族路径（唯一 writer：`str06.stage4.compile`）。
WRITER_IR_REL = 'stage4/ir/writer_book_ir.json'
#: canary 选章的调用层参数（W1/D2：只作调用元数据，不进任何身份）。
BODY_NODE = 'str06.stage5.body'


@dataclass(frozen=True)
class ChapterCount:
    """一章的 canary 计分（全部从 WriterBookIR 机械计数）。"""

    chapter_ordinal: int
    thread_ops: int
    cross_chapter_reads: int
    reader_confirmations: int
    thread_closes: int

    def middle_score(self) -> int:
        """中段章计分：线程操作＋跨章读取。"""
        return self.thread_ops + self.cross_chapter_reads

    def late_score(self) -> int:
        """后段章计分：本章读者确认＋线程收束。"""
        return self.reader_confirmations + self.thread_closes


def _scene_chapters(writer_ir) -> dict[str, int]:
    """场景 → 所属章序（跨章读取的判定基准）。"""
    return {context.scene_id: chapter.chapter_ordinal
            for chapter in writer_ir.chapters
            for context in chapter.scenes}


def _cross_chapter_reads(chapter, scene_chapters: Mapping[str, int]) -> int:
    """本章声明的读取里 `established_by` 落在更早章的条数。

    只数**声明的**读取（入场视图＋拍级 `reads`）：初态（`init`）是前史不是
    跨章依赖，编译器补齐的背景不是章包身份——两者都不计。锚点不在 IR 场景
    闭集内即坏账，关闭式报错（不按 0 混过去）。
    """
    total = 0
    for context in chapter.scenes:
        ir = context.verification_ir
        declared = list(ir.entry_state) + list(ir.entry_knowledge)
        for beat in ir.beats:
            declared += list(beat.reads)
        for entry in declared:
            anchor = entry.established_by
            if anchor == 'init':
                continue
            scene_id = anchor.split('#', 1)[0]
            if scene_id not in scene_chapters:
                raise ValueError(
                    f'第 {chapter.chapter_ordinal} 章读取锚点 {anchor!r} 不在 '
                    f'IR 场景闭集内；IR 已坏，不能据此选章')
            if scene_chapters[scene_id] < chapter.chapter_ordinal:
                total += 1
    return total


def _reader_confirmations(chapter) -> int:
    """本章 `in_scene` 读者确认数（按事实地址＋确认位置去重）。"""
    seen: set[tuple] = set()
    for context in chapter.scenes:
        for pending in context.verification_ir.pending_reader_confirmations:
            if pending.in_scene:
                seen.add((pending.address.address(), pending.position))
    return len(seen)


def chapter_counts(writer_ir) -> tuple[ChapterCount, ...]:
    """逐章计分（IR 计数与选章分开：本函数只读数，不做任何选择）。"""
    scene_chapters = _scene_chapters(writer_ir)
    counts = []
    for chapter in writer_ir.chapters:
        ops = [op for context in chapter.scenes
               for op in context.verification_ir.thread_ops]
        counts.append(ChapterCount(
            chapter_ordinal=chapter.chapter_ordinal,
            thread_ops=len(ops),
            cross_chapter_reads=_cross_chapter_reads(chapter, scene_chapters),
            reader_confirmations=_reader_confirmations(chapter),
            thread_closes=sum(1 for op in ops if op.op == 'close')))
    return tuple(counts)


def _best(counts: Sequence[ChapterCount], lo: int, hi: int, score) -> int:
    """区间内计分最大的一章；平分取章序较小者。区间为空即报错。"""
    candidates = [c for c in counts if lo <= c.chapter_ordinal <= hi]
    if not candidates:
        raise ValueError(
            f'选章区间 {lo}..{hi} 为空（章数不足，不做canary 选章）')
    return min(candidates, key=lambda c: (-score(c), c.chapter_ordinal)).chapter_ordinal


def select_canary_chapters(counts: Sequence[ChapterCount]) -> tuple[int, ...]:
    """机械选章（纯函数）：开篇章＋中段章＋后段章，按章序升序返回。

    中段章区间 `⌊N/3⌋+1 .. ⌈2N/3⌉` 按「线程操作＋跨章读取」取最大；后段章
    区间 `⌈2N/3⌉+1 .. N` 按「读者确认＋线程收束」取最大；平分取章序较小者。
    """
    total = len(counts)
    if total == 0:
        raise ValueError('IR 没有章，无从选章')
    middle_high = -(-2 * total // 3)                  # ⌈2N/3⌉
    opening = min(c.chapter_ordinal for c in counts)  # 开篇章固定第 1 章
    middle = _best(counts, total // 3 + 1, middle_high,
                   ChapterCount.middle_score)
    late = _best(counts, middle_high + 1, total, ChapterCount.late_score)
    return tuple(sorted({opening, middle, late}))


def load_writer_book_ir(run_dir: Path):
    """读**已封存**的 WriterBookIR（`read_model`：R6 发布校验＋模型校验）。

    未封存的 IR 直接拒读（AUD-05）——不拿诊断字节当选章依据；`_provenance`
    由 `read_model` 剥掉，来源账不进计分。
    """
    return Str06Repository(Path(run_dir)).read_model(WRITER_IR_REL)


def main() -> int:
    ap = argparse.ArgumentParser(
        description='B6-A canary 选章（只读、零成本）：逐章计分＋选中三章＋下一条命令')
    ap.add_argument('--base', required=True, help='<Output> 根目录')
    ap.add_argument('--sid', required=True, help='书 id（候选根＝书目录）')
    args = ap.parse_args()

    run_dir = paths.resolve_novel_dir(Path(args.base), args.sid)
    if not (run_dir / WRITER_IR_REL).is_file():
        print(f'没查：{run_dir} 下没有 {WRITER_IR_REL}；先跑完 Stage4 再选章',
              file=sys.stderr)
        return 2
    writer_ir = load_writer_book_ir(run_dir)
    counts = chapter_counts(writer_ir)
    selected = select_canary_chapters(counts)

    print(f'候选根：{run_dir}')
    print(f'章数：{len(counts)}')
    print()
    print('逐章计分（线程操作 / 跨章读取 / 读者确认 / 线程收束）：')
    for count in counts:
        mark = ' ←' if count.chapter_ordinal in selected else ''
        print(f'  第{count.chapter_ordinal:>2}章  '
              f'线程操作 {count.thread_ops:>2}  跨章读取 {count.cross_chapter_reads:>2}  '
              f'读者确认 {count.reader_confirmations:>2}  线程收束 {count.thread_closes:>2}{mark}')
    opening, middle, late = selected
    by_ordinal = {c.chapter_ordinal: c for c in counts}
    print()
    print('选中三章（机械规则）：')
    print(f'  开篇章：第 {opening} 章（固定）')
    print(f'  中段章：第 {middle} 章（线程操作＋跨章读取＝{by_ordinal[middle].middle_score()}）')
    print(f'  后段章：第 {late} 章（读者确认＋线程收束＝{by_ordinal[late].late_score()}）')
    print()
    print('下一条命令（付费：显式 --yes 与正文外发授权；选章只作调用元数据）：')
    chapters = ','.join(str(n) for n in selected)
    print(f'  python -m novel.cli run {BODY_NODE} {args.sid} --base {args.base} '
          f'--yes --allow-egress --body-chapters {chapters}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
