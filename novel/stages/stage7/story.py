"""story-v3 最终装配：只接受完整当前章包、正文、Hook 及其机械来源证明。

本模块不读取文件、不调用模型、不改正文；持久化和交付路径属于 repository。
文学质量保持 unver，章目录严格保留源章数及阅读序。
"""
from __future__ import annotations

from collections.abc import Sequence

from contracts.schema.source_story import STORY_VERSION
from contracts.schema.story_prose import (
    ChapterBody, FinalChapterEntry, FinalManifest, FinalStory, HookBody, ProseIssue,
)
from contracts.schema.target_story import ChapterPacket, HookPacket
from novel.core.story_prose import (
    ProseRejected, sha256_text, stable_sha256, verify_body, verify_hook,
)


def assemble_story(*, title: str, source_chapter_count: int,
                   packets: Sequence[ChapterPacket], bodies: Sequence[ChapterBody],
                   hook: HookBody, hook_packet: HookPacket) -> FinalStory:
    """核对真实最终状态；重放相同输入得到相同 TXT 和 manifest。"""
    issues = []

    def issue(code: str, detail: str, chapter: int | None = None):
        issues.append(ProseIssue(code=code, owner='stage7', chapter_ordinal=chapter, detail=detail))

    if not title.strip() or any(char in title for char in '\r\n\x00'):
        issue('invalid_title', '最终书名必须是单行非空文本')
    if type(source_chapter_count) is not int or source_chapter_count < 1:
        issue('source_chapter_count', '源章数必须为正整数')
    expected = tuple(range(1, source_chapter_count + 1)) if type(source_chapter_count) is int else ()
    if tuple(packet.chapter_ordinal for packet in packets) != expected:
        issue('packet_chapter_set', '当前章包必须按阅读序完整覆盖源章号，不能合并、拆分、删除或重排')
    if tuple(body.chapter_ordinal for body in bodies) != expected:
        issue('body_chapter_set', '当前正文必须按阅读序完整覆盖源章号，不能用文件存在代替完成')
    if any(packet.source_chapter_count != source_chapter_count for packet in packets):
        issue('packet_source_count', '章包记录的源章数不一致')
    if issues:
        raise ProseRejected(tuple(issues))
    entries = []
    previous = None
    for packet, body in zip(packets, bodies):
        issues.extend(verify_body(body, packet, prior_body=previous))
        entries.append(FinalChapterEntry(chapter_ordinal=body.chapter_ordinal,
                                         packet_sha256=stable_sha256(packet),
                                         prior_tail_sha256=body.receipt.prior_tail_sha256,
                                         body_sha256=sha256_text(body.text),
                                         receipt_sha256=stable_sha256(body.receipt),
                                         request_id=body.receipt.request_id))
        previous = body.text
    issues.extend(verify_hook(hook, hook_packet))
    if issues:
        raise ProseRejected(tuple(issues))
    parts = [title, '\n\n', hook.text]
    for body in bodies:
        parts.extend((f'\n\n第{body.chapter_ordinal}章\n\n', body.text))
    text = ''.join(parts)
    manifest = FinalManifest(contract_version=STORY_VERSION, title=title, source_chapter_count=source_chapter_count,
                             chapters=tuple(entries), hook_packet_sha256=stable_sha256(hook_packet),
                             hook_body_sha256=sha256_text(hook.text),
                             hook_receipt_sha256=stable_sha256(hook.receipt),
                             final_text_sha256=sha256_text(text), mechanical='passed', semantic='unver')
    return FinalStory(contract_version=STORY_VERSION, text=text, manifest=manifest)


def verify_final_story(final: FinalStory, *, title: str, source_chapter_count: int,
                       packets: Sequence[ChapterPacket], bodies: Sequence[ChapterBody],
                       hook: HookBody, hook_packet: HookPacket) -> tuple[ProseIssue, ...]:
    """下载/交付读面免费复验当前来源和最终 TXT；文件存在不能代替证明。"""
    try:
        expected = assemble_story(title=title, source_chapter_count=source_chapter_count,
                                  packets=packets, bodies=bodies, hook=hook, hook_packet=hook_packet)
    except ProseRejected as exc:
        return exc.issues
    if final != expected:
        return (ProseIssue(code='final_identity', owner='stage7', chapter_ordinal=None,
                           detail='最终TXT或manifest与当前有效正文、Hook、章包不匹配'),)
    return ()
