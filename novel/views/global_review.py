#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)
from contracts.invariants import review_effect_of
from contracts.schema.api import GlobalReviewRow, GlobalReviewView
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import GlobalGrounding
from novel import paths, prov
from novel.errors import NovelError, UpstreamMissing
from novel.orchestrator import state
from novel.quality.global_review import (
    generation_safety, review_effect_counts, review_group_key, review_status,
    subject_sha256,
)
from novel.stages.stage2.global_review import review_identity
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
    grounding_payload = grounding.model_dump(by_alias=True)
    subject, current, approved = review_status(grounding_payload, review)
    safety = generation_safety(grounding_payload, review)
    review_token: str | None = None
    if current:
        # token 只对 current 主体有意义；MainText 缺失/过期时保持 None，
        # 界面据此不提供确认动作，而不是让人对一份不可提交的面签字。
        try:
            main = store.read_own(
                MainText, STAGE1_MAIN_TEXT, require_current=True)[0]
        except NovelError:
            main = None
        if main is not None:
            review_token = review_identity(
                grounding_payload, review,
                subject_sha256(main.model_dump(mode='json', by_alias=True)),
            )['review_token']
    return GlobalReviewView(
        source_sha256=review.source_sha256,
        current_subject_sha256=subject,
        current=current,
        approved=approved,
        generation_safe=safety.safe,
        quarantined_groups=list(safety.quarantined_groups),
        review_token=review_token,
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
