"""Characters v3 兼容再导出。

正式定义已并入 ``contracts.schema.characters``；本模块保留历史 import 路径。
"""
from __future__ import annotations

from contracts.schema.characters import (  # noqa: F401
    BOOK_LEVEL_SURFACE_KINDS,
    NAME_LICENSE_KINDS,
    BookExtractResponse,
    BookResolveResponse,
    CandidateSource,
    ChapterSurfaceDiscover,
    CharacterId,
    CharacterIdentityStatus,
    CharacterRegistry,
    CharacterRegistryV3,
    CharacterSeed,
    LocalCharacterRef,
    MentionCandidate,
    MentionDisposition,
    MentionId,
    MentionKind,
    MentionRecord,
    RegisteredCharacter,
    SurfaceBinding,
    SurfaceBindingScope,
    SurfaceRecord,
)

__all__ = [
    'BOOK_LEVEL_SURFACE_KINDS',
    'NAME_LICENSE_KINDS',
    'BookExtractResponse',
    'BookResolveResponse',
    'CandidateSource',
    'ChapterSurfaceDiscover',
    'CharacterId',
    'CharacterIdentityStatus',
    'CharacterRegistry',
    'CharacterRegistryV3',
    'CharacterSeed',
    'LocalCharacterRef',
    'MentionCandidate',
    'MentionDisposition',
    'MentionId',
    'MentionKind',
    'MentionRecord',
    'RegisteredCharacter',
    'SurfaceBinding',
    'SurfaceBindingScope',
    'SurfaceRecord',
]
