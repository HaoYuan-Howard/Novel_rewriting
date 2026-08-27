#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW
from contracts.invariants import review_effect_of
from contracts.schema.api import GlobalReviewRow, GlobalReviewView
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage2 import GlobalGrounding
from novel import paths, prov
from novel.errors import UpstreamMissing
from novel.orchestrator import state
from novel.quality.global_review import (
    review_effect_counts, review_group_key, review_status,
)
from novel.store import ArtifactStore


def global_review_view(base: str, sid: str) -> GlobalReviewView:
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(state.readonly_ctx(novel_dir, 'stage2.global', prov.code_version()))
    if not store.exists(STAGE2_GLOBAL_REVIEW):
        raise UpstreamMissing(f'{STAGE2_GLOBAL_REVIEW} 不在盘上')
    if not store.exists(STAGE2_GLOBAL_GROUNDING):
        raise UpstreamMissing(f'{STAGE2_GLOBAL_GROUNDING} 不在盘上')
    review = store.read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)[0]
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    subject, current, approved = review_status(
        grounding.model_dump(by_alias=True), review)
    return GlobalReviewView(
        source_sha256=review.source_sha256,
        current_subject_sha256=subject,
        current=current,
        approved=approved,
        effect_counts=review_effect_counts(review),
        # 两格由服务端派生后随行下发；界面不复制分类表，也不自己解析 subject_path。
        items=[
            GlobalReviewRow(
                **item.model_dump(mode='json'),
                effect=review_effect_of(item.kind),
                group_key=review_group_key(item.subject_path),
            )
            for item in review.items
        ],
    )
