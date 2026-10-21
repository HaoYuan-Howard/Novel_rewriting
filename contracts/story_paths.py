"""当前三层产物路径值；旧协议没有读取、换算或采用入口。"""
from __future__ import annotations

SOURCE_STORY = 'stage2/story/source_story.json'
CHAPTER_OBSERVATION = 'stage2/observations/ch{nn}.json'
BOOK_OBSERVATION = 'stage2/observations/book.json'
NAME_OBSERVATION = 'stage2/observations/names.json'
ASSOCIATION = 'stage2/observations/associate.json'
NAME_MAP = 'stage4/naming/name_map.json'
PRESERVATION = 'stage4/story/preservation.json'
TARGET_MODEL = 'stage4/drafts/book.json'
CHAPTER_PLAN = 'stage4/drafts/ch{nn}.json'
TARGET_STORY = 'stage4/story/target_story.json'
ADAPTATION_MAP = 'stage4/story/adaptation_map.json'
CHAPTER_PACKET = 'stage4/packets/ch{nn}.json'
HOOK_PLAN = 'stage4/drafts/hook.json'
HOOK_PACKET = 'stage4/packets/hook.json'
BODY = 'stage5/body/ch{nn}.txt'
BODY_RECEIPT = 'stage5/acceptance/body/ch{nn}.json'
HOOK = 'stage5/hook/hook.txt'
HOOK_RECEIPT = 'stage5/hook/receipt.json'
FINAL = 'stage7/final/{title}_rewriting.txt'
MANIFEST = 'stage7/final/delivery_manifest.json'

STORY_ARTIFACTS = (
    CHAPTER_OBSERVATION, BOOK_OBSERVATION, NAME_OBSERVATION, ASSOCIATION,
    SOURCE_STORY, NAME_MAP, PRESERVATION, TARGET_MODEL, CHAPTER_PLAN,
    TARGET_STORY, ADAPTATION_MAP, CHAPTER_PACKET, HOOK_PLAN, HOOK_PACKET,
    BODY, BODY_RECEIPT, HOOK, HOOK_RECEIPT, FINAL, MANIFEST,
)


def chapter_rel(template: str, ordinal: int) -> str:
    if isinstance(ordinal, bool) or ordinal < 1:
        raise ValueError('章序必须为正整数')
    return template.format(nn=f'{ordinal:02d}')
