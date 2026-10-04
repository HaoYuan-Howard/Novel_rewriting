"""摄入分章的确定性转换；只消费文本与契约，不读写文件、不推进业务状态。"""
from __future__ import annotations

from dataclasses import dataclass
from contracts.invariants import MIN_CHAPTERS
from contracts.schema.stage1 import HookCopy, MainText, MainTextSection
from novel.core.ingest_audit import scan_partition
from novel.errors import HookCopyMissing, PartitionChapterMismatch, ResidualNonbody, TooFewChapters


@dataclass(frozen=True)
class PartitionResult:
    main_text: MainText
    hook_copy: HookCopy
    tail: str


def partition_text(clean_text: str) -> PartitionResult:
    """分章纯函数；显式终态发生在模型构造和写盘之前。"""
    partition = scan_partition(clean_text)
    if partition.scan.nonbody:
        marker = partition.scan.nonbody[0]
        line_no = clean_text.count('\n', 0, marker.line_start) + 1
        raise ResidualNonbody(
            f'批准后的 clean_text 第 {line_no} 行仍命中非正文标记「{marker.title}」；'
            'stage1 只断言、不代替 stage0 静默删除')

    hook_text = clean_text[:partition.first_marker]
    if not hook_text.strip():
        raise HookCopyMissing(
            '第一个正文章标记之前没有非空开篇文案；开篇文案段是必填输入（R-1.12）')
    if len(partition.markers) < MIN_CHAPTERS:
        raise TooFewChapters(
            f'只识别到 {len(partition.markers)} 个正文章节，少于最低要求 {MIN_CHAPTERS}')

    sections: list[MainTextSection] = []
    for index, (marker, (start, end)) in enumerate(
        zip(partition.markers, partition.chapter_spans), start=1,
    ):
        raw = clean_text[start:end]
        if not raw.strip():
            raise PartitionChapterMismatch(
                f'第 {index} 个章节标记（原章号 {marker.chapter_no}）之后没有正文；'
                f'独立计数会得到 {len(partition.markers)} 章，但章表无法一一落盘')
        sections.append(MainTextSection(chapter_ordinal=index, text=raw))

    return PartitionResult(
        main_text=MainText.model_validate(sections),
        hook_copy=HookCopy(text=hook_text),
        tail=partition.tail,
    )
