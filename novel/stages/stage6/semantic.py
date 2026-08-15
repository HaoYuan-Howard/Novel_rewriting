#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 匿名质量审判：实质矛盾 + 吸引力排序。

审判一次看完全部候选，但只收到 ``T1/T2/...`` 中性标签，避免 approach 名称诱导偏好。
它可以看到原作文案、全书事实/故事地图、开篇成稿和正文风格包：原文用于质量参照，
全书用于矛盾核对，正文用于声线参照。剧透不构成违规；只有改变身份、关系、关键因果
或故事结果才是 material contradiction。结论仍按文本 sha1 绑定，因此 ``--pick`` 不会
使审判过期，``--apply-edit`` 会。
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass

from contracts.schema.common import Finding
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage6 import (
    Hook,
    HookCandidateReview,
    HookCandidateReviewResponse,
    HookSemanticResponse,
    HookSemanticReview,
)
from novel import prov
from novel.core.text import whitespace_equivalent_slice
from novel.errors import TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from novel.report.findings import Outcome, ran, unverifiable
from novel.run import RunContext
from novel.stages.stage6 import hook
from novel.store import ArtifactStore

SEMANTIC_TPL = 'stage6/semantic_{n}.json'
_SEMANTIC_RX = re.compile(r'^semantic_(\d+)\.json$')

#: 固定权重是契约，不让模型自报总分。惩罚项为负权重。
QUALITY_WEIGHTS = {
    'read_on_desire': 0.30,
    'body_voice_match': 0.20,
    'concrete_scene_and_specificity': 0.15,
    'curiosity_and_payoff_promise': 0.20,
    'hook_craft': 0.15,
    'synopsis_penalty': -0.10,
}

SEMANTIC_SYS = with_json_shape("""\
你是负责付费短篇与网文投放的资深主编。匿名评审若干条开篇文案，目标是选出最能让目标读者继续读第一章的一条。

输入会给出：匿名候选、原始文案、重写后的开篇正文、正文实际执行的声音与写作手法，以及全书事实和逐章故事地图。
原始文案不是默认冠军；它只告诉你这本书原本靠什么吸引人。候选标签没有意义，不要猜它来自哪种策略。

逐条评分（0~10，可用小数）：
- read_on_desire：读完是否立刻想继续；
- body_voice_match：是否与重写正文像同一本书、同一个叙述者；
- concrete_scene_and_specificity：是否有具体人物、动作、对白、处境，而非抽象概括；
- curiosity_and_payoff_promise：是否既给出值得期待的兑现，又留下真正想追的缺口；
- hook_craft：起势、升级、节奏、反差、最后一击是否成熟；
- synopsis_penalty：越像剧情摘要、设定清单、秘密罗列或泛泛宣传语，分越高（0 最好，10 最差）。

【实质矛盾口径】
- 剧透本身不加分也不扣分，只判断它增强阅读欲还是把问题彻底说完；
- 文案体裁允许重写台词、压缩时间、倒叙、合并数章和合成场面；“正文没有逐字出现这句台词/这个场面”不等于矛盾；
- 只有候选改变人物身份、人物关系、关键因果或故事结果，或凭空增加会改变读者理解的新设定，才写入 contradictions；
- 每条 contradiction 的 evidence 必须是候选文案里的逐字连续片段，assertion 与 reason 不得大段复述全书真相；
- 若无法确信已完整核对，coverage=uncertain 并说明原因，绝不能用 complete 洗绿。

不要偏爱信息最多、最安全或最规整的一条。空泛反问、抽象抒情和全知式剧情讲解要明确扣分。
ranking 必须恰好覆盖全部匿名标签；所有 complete 且无矛盾的候选必须排在矛盾或 uncertain 候选之前。
只输出 JSON。""", {
    'reviews': [{
        'label': 'T1',
        'coverage': 'complete|uncertain',
        'uncertainty_reason': 'str|null',
        'contradictions': [{
            'assertion': 'str',
            'evidence': 'str',
            'reason': 'str',
        }],
        'quality': {
            'read_on_desire': 'number 0..10',
            'body_voice_match': 'number 0..10',
            'concrete_scene_and_specificity': 'number 0..10',
            'curiosity_and_payoff_promise': 'number 0..10',
            'hook_craft': 'number 0..10',
            'synopsis_penalty': 'number 0..10',
        },
        'concise_reason': 'str',
    }],
    'ranking': ['T1', 'T2', 'T3'],
})


@dataclass(frozen=True)
class CandidateAssessment:
    """视图与批准路共用的当前候选状态。"""

    state: str
    reason: str
    review: HookCandidateReview | None = None
    quality_score: float | None = None
    quality_rank: int | None = None

    @property
    def selectable(self) -> bool:
        return self.state == 'clear'


def text_sha1(text: str) -> str:
    """一条文案文本的绑定指纹（10 位）。"""
    return prov.fingerprint(text)


def quality_score(review: HookCandidateReview | HookCandidateReviewResponse) -> float:
    """固定权重总分，裁到 0~10 并保留两位。"""
    scores = review.quality
    raw = sum(float(getattr(scores, key)) * weight for key, weight in QUALITY_WEIGHTS.items())
    return round(max(0.0, min(10.0, raw)), 2)


def semantic_seqs(store: ArtifactStore) -> list[int]:
    out: list[int] = []
    for path in glob.glob(os.path.join(store.path('stage6'), 'semantic_*.json')):
        match = _SEMANTIC_RX.match(os.path.basename(path))
        if match is not None:
            out.append(int(match.group(1)))
    return sorted(out)


def latest_semantic(store: ArtifactStore) -> tuple[int, HookSemanticReview] | None:
    seqs = semantic_seqs(store)
    if not seqs:
        return None
    seq = seqs[-1]
    art, _raw = store.read_own(HookSemanticReview, SEMANTIC_TPL.format(n=seq))
    return seq, art


def _unique_texts(art: Hook) -> list[tuple[str, str]]:
    """按文本 sha1 去重；定稿若只是候选拷贝，不重复付费审阅。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, text in hook._texts_of(art):
        sha = text_sha1(text)
        if sha not in seen:
            seen.add(sha)
            out.append((label, text))
    return out


def build_prompt(ctx: RunContext) -> tuple[str, str, Hook, hook._Upstreams,
                                               list[tuple[str, str, str]]]:
    """构造一次匿名审判请求；返回 ``(neutral, real, text)`` 映射供落盘解匿名。"""
    store = ArtifactStore(ctx)
    if not store.exists(hook.REL):
        raise UpstreamMissing(f'{hook.REL} 不在盘上——先跑 stage6.hook')
    if store.freshness(hook.REL).state != 'fresh':
        raise TerminalState(
            'stage6/hook.json 的生成上游已经变化；不要付费审一份过期文案，先重跑 stage6.hook')
    art, _raw = store.read_own(Hook, hook.REL)
    up = hook._Upstreams(ctx, store)

    aliases = [
        (f'T{i}', real_label, text)
        for i, (real_label, text) in enumerate(_unique_texts(art), 1)
    ]
    payload = {
        'texts': [{'label': neutral, 'text': text} for neutral, _real, text in aliases],
        'source_reference': up.src_hook,
        'rewritten_opening': [
            {'chapter': row.get('output_ordinal'), 'text': row.get('text') or ''}
            for row in up.mat.lead
        ],
        'story_truth': up.mat.story_truth,
        'whole_story_map': list(up.mat.story_map),
        'style': up.mat.style_packet,
    }
    user = ('匿名评审以下候选。先核对实质矛盾，再按留存能力评分与排序。\n\n'
            + json.dumps(payload, ensure_ascii=False, indent=2))
    return SEMANTIC_SYS, user, art, up, aliases


def response_validator(aliases: list[tuple[str, str, str]]):
    """守标签覆盖、排序覆盖、逐字证据与「坏候选不压过干净候选」。"""
    bodies = {neutral: text for neutral, _real, text in aliases}
    expected = set(bodies)

    def validate(resp: HookSemanticResponse, _wire: ChatResponse) -> str | None:
        got = [review.label for review in resp.reviews]
        if len(got) != len(set(got)) or set(got) != expected:
            return f'reviews 必须恰好覆盖 {sorted(expected)}，实得 {got}'
        if len(resp.ranking) != len(set(resp.ranking)) or set(resp.ranking) != expected:
            return f'ranking 必须恰好覆盖 {sorted(expected)}，实得 {resp.ranking}'

        by_label = {review.label: review for review in resp.reviews}
        for review in resp.reviews:
            body = bodies[review.label]
            for contradiction in review.contradictions:
                if contradiction.evidence in body:
                    continue
                recovered = whitespace_equivalent_slice(contradiction.evidence, body)
                if recovered is None:
                    return (f'{review.label} 的 contradiction evidence 不是候选文案连续子串：'
                            f'{contradiction.evidence!r}')
                contradiction.evidence = recovered

        clean = {label for label, review in by_label.items()
                 if review.coverage == 'complete' and not review.contradictions}
        bad = expected - clean
        if clean and bad:
            positions = {label: i for i, label in enumerate(resp.ranking)}
            if max(positions[label] for label in clean) > min(positions[label] for label in bad):
                return 'ranking 必须把 complete 且无矛盾的候选全部排在矛盾/uncertain 候选之前'
        return None

    return validate


def run_semantic(ctx: RunContext, client: LlmClient) -> tuple[HookSemanticReview, CheckReport]:
    system, user, art, up, aliases = build_prompt(ctx)
    response = client.ask_json(
        system,
        user,
        model_cls=HookSemanticResponse,
        purpose=ctx.node,
        validator=response_validator(aliases),
    )

    alias_map = {neutral: (real, text) for neutral, real, text in aliases}
    reviews: list[HookCandidateReview] = []
    for review in response.reviews:
        data = review.model_dump(mode='python')
        data['label'] = alias_map[review.label][0]
        reviews.append(HookCandidateReview(
            **data,
            text_sha1=text_sha1(alias_map[review.label][1]),
        ))
    ranking = [alias_map[label][0] for label in response.ranking]

    store = ArtifactStore(ctx)
    artifact = HookSemanticReview(
        judged_texts={real: text_sha1(text) for _neutral, real, text in aliases},
        hook_file_sha1=prov.fingerprint_bytes(store.path(hook.REL).read_bytes()),
        reviews=reviews,
        ranking=ranking,
        texts=[],
        decisions=[],
        _provenance=ctx.llm_provenance(
            prompts={'HOOK_SEMANTIC_SYS': system},
            model=client.model,
        ),
    )
    seqs = semantic_seqs(store)
    seq = (seqs[-1] + 1) if seqs else 1
    rel = SEMANTIC_TPL.format(n=seq)
    store.write(rel, artifact)
    outcomes = hook._judge_artifact(art, up, store)
    return artifact, hook._report(ctx, store, outcomes, art, freshness_rel=rel)


def _current_artifact(store: ArtifactStore, art: Hook) -> tuple[HookSemanticReview | None, str]:
    got = latest_semantic(store)
    if got is None:
        return None, '没有 stage6/semantic_<n>.json；先运行 stage6.semantic'
    seq, artifact = got
    rel = SEMANTIC_TPL.format(n=seq)
    if not artifact.reviews:
        return None, f'{rel} 是旧版剧透审判，不含质量评分与实质矛盾结论；请重跑 stage6.semantic'
    if store.freshness(rel).state != 'fresh':
        return None, f'{rel} 的上游已变化；请重跑 stage6.semantic'

    current_shas = {text_sha1(text) for _label, text in hook._texts_of(art)}
    judged_shas = {review.text_sha1 for review in artifact.reviews}
    missing = current_shas - judged_shas
    if missing:
        return None, f'当前有 {len(missing)} 份文本未被最近一次质量审判覆盖；请重跑 stage6.semantic'
    return artifact, ''


def current_assessments(store: ArtifactStore, art: Hook) -> dict[str, CandidateAssessment]:
    """按文本 sha1 返回当前评审状态；键可同时服务候选与重复的定稿文本。"""
    artifact, why = _current_artifact(store, art)
    current_shas = {text_sha1(text) for _label, text in hook._texts_of(art)}
    if artifact is None:
        return {
            sha: CandidateAssessment(state='unreviewed', reason=why)
            for sha in current_shas
        }

    rank_by_label = {label: i for i, label in enumerate(artifact.ranking, 1)}
    by_sha: dict[str, CandidateAssessment] = {}
    for review in artifact.reviews:
        if review.contradictions:
            state = 'contradiction'
            reason = review.concise_reason
        elif review.coverage == 'uncertain':
            state = 'uncertain'
            reason = review.uncertainty_reason or review.concise_reason
        else:
            state = 'clear'
            reason = review.concise_reason
        by_sha[review.text_sha1] = CandidateAssessment(
            state=state,
            reason=reason,
            review=review,
            quality_score=quality_score(review),
            quality_rank=rank_by_label.get(review.label),
        )
    return by_sha


def assessment_for_text(store: ArtifactStore, art: Hook, text: str) -> CandidateAssessment:
    return current_assessments(store, art).get(
        text_sha1(text),
        CandidateAssessment(state='unreviewed', reason='当前文本没有质量审判记录'),
    )


def require_selectable(store: ArtifactStore, art: Hook, text: str, label: str) -> CandidateAssessment:
    """批准路的代码硬闸：缺审、矛盾和 uncertain 都不能被人误选。"""
    assessment = assessment_for_text(store, art, text)
    if assessment.state == 'clear':
        return assessment
    if assessment.state == 'contradiction' and assessment.review is not None:
        evidence = [item.evidence for item in assessment.review.contradictions]
        raise TerminalState(
            f'候选 {label} 有实质矛盾，不能定稿：{evidence[:3]}。'
            f'请改选 clear 候选，或 --apply-edit 后重跑 stage6.semantic')
    raise TerminalState(
        f'候选 {label} 当前状态是 {assessment.state}，不能定稿：{assessment.reason}。'
        f'先运行/重跑 stage6.semantic')


def require_approved_clear(store: ArtifactStore, art: Hook) -> CandidateAssessment:
    """下游消费闸：人工编辑定稿后也必须重新获得当前 clear 结论。"""
    if art.approved is None:
        raise TerminalState('开篇文案还没定稿——先跑 stage6.approve --pick N')
    return require_selectable(store, art, art.approved.text, '定稿')


def unavailable_outcomes(why: str) -> list[Outcome]:
    return [
        unverifiable('hook_semantic_contradiction', why, scanned='0 份当前质量审判'),
        unverifiable('hook_candidate_contradiction', why, scanned='0 份当前质量审判'),
        unverifiable('hook_quality_rank', why, scanned='0 份当前质量审判'),
    ]


def outcomes(store: ArtifactStore, art: Hook | None) -> list[Outcome]:
    """当前质量产物 → 矛盾门、候选提示与质量排序三条判据。"""
    if art is None:
        return unavailable_outcomes('stage6/hook.json 不可读，质量结论无从绑定')
    artifact, why = _current_artifact(store, art)
    if artifact is None:
        return unavailable_outcomes(why)

    assessments = current_assessments(store, art)
    labels_and_texts = hook._texts_of(art)
    candidate_findings: list[Finding] = []
    for label, text in labels_and_texts:
        assessment = assessments[text_sha1(text)]
        review = assessment.review
        if review is None:
            continue
        for contradiction in review.contradictions:
            candidate_findings.append(finding(
                'hook_candidate_contradiction',
                detail=(f'[{label}] 实质矛盾：{contradiction.assertion}；'
                        f'候选证据：{contradiction.evidence}；{contradiction.reason}'),
                evidence=[f'{label}:{contradiction.evidence}'],
            ))
        if review.coverage == 'uncertain':
            candidate_findings.append(finding(
                'hook_candidate_contradiction',
                detail=f'[{label}] 语义核对不完整：{review.uncertainty_reason}',
                evidence=[],
            ))

    hard_findings: list[Finding] = []
    hard_unver: str | None = None
    if art.approved is not None:
        chosen = assessments[text_sha1(art.approved.text)]
        if chosen.state == 'contradiction' and chosen.review is not None:
            hard_findings.append(finding(
                'hook_semantic_contradiction',
                detail='定稿文本含实质矛盾；该选择不能进入成书',
                evidence=[item.evidence for item in chosen.review.contradictions],
            ))
        elif chosen.state != 'clear':
            hard_unver = f'定稿文本状态为 {chosen.state}：{chosen.reason}'
    else:
        candidate_states = [assessments[text_sha1(candidate.text)] for candidate in art.candidates]
        if not any(item.state == 'clear' for item in candidate_states):
            if any(item.state in ('uncertain', 'unreviewed') for item in candidate_states):
                hard_unver = '三条候选里没有已确认 clear 的文本，且至少一条审判不确定'
            else:
                hard_findings.append(finding(
                    'hook_semantic_contradiction',
                    detail='三条候选全部存在实质矛盾，没有可定稿文本',
                    evidence=[
                        contradiction.evidence
                        for item in candidate_states if item.review is not None
                        for contradiction in item.review.contradictions
                    ],
                ))

    scanned = f'{len(artifact.reviews)} 份去重文本'
    hard = (unverifiable('hook_semantic_contradiction', hard_unver, scanned=scanned)
            if hard_unver else
            ran('hook_semantic_contradiction', hard_findings, scanned=scanned))

    metric_findings = [finding(
        'hook_quality_rank',
        detail=(
            f'匿名排序 {artifact.ranking}；' + ' · '.join(
                f'{review.label} 总分 {quality_score(review):.2f}'
                f'（阅读欲 {review.quality.read_on_desire:g} / 声线 {review.quality.body_voice_match:g} / '
                f'场面 {review.quality.concrete_scene_and_specificity:g} / '
                f'期待 {review.quality.curiosity_and_payoff_promise:g} / '
                f'技法 {review.quality.hook_craft:g} / 摘要罚 {review.quality.synopsis_penalty:g}）'
                for review in artifact.reviews
            )
        ),
        evidence=[f'{review.label}:{quality_score(review):.2f}' for review in artifact.reviews],
    )]
    return [
        hard,
        ran('hook_candidate_contradiction', candidate_findings, scanned=scanned),
        ran('hook_quality_rank', metric_findings, scanned=scanned),
    ]
