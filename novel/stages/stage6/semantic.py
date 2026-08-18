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

from contracts.artifacts import STAGE6_SEMANTIC

import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass

from contracts.schema.common import Finding
from contracts.schema.evaluation import (
    EvidenceRef,
    JudgeDecision,
    SemanticDecision,
)
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage6 import (
    Hook,
    HookCandidateReview,
    HookCandidateReviewResponse,
    HookQualityScores,
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
from novel.quality.evaluator import double_blind_decision, runtime_fingerprint
from novel.run import RunContext
from novel.stages.stage6 import hook
from novel.store import ArtifactStore

SEMANTIC_TPL = STAGE6_SEMANTIC
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

JUDGE_ROLE_SUFFIX = {
    'A': '\n\n你是盲评实例 A；不得推测或接触另一名 judge 的输出。',
    'B': '\n\n你是盲评实例 B；不得推测或接触另一名 judge 的输出。',
}


def judge_request(system: str, user: str, role: str) -> tuple[str, str]:
    """一个判官这一次**真发出去的** `(system, user)`。**全模块唯一的拼装口。**

    ⚠⚠ **身份标签挂在 user 末尾，不挂在 system 末尾**，而这一位是成本承重：
    供应商按 `<BOS>{system}<｜User｜>{user}…` 的**字节前缀**做上下文缓存
    （编码见 `novel/llm/budget.py::_render_prompt`）。标签原来挂在 system 末尾，
    于是前缀在那里就分叉——B 判官那一份 ~45k token 的材料**全额 miss**，
    而两个判官的材料逐字节相同。实测一本书两次判官调用共 95,972 prompt token
    几乎全额付费；`.llm_cache` 全量统计里 30k+ 档的命中率只有 3.5%（6k–12k 档有 47.6%）。
    挪到 user 末尾之后，`system` + 整包材料成为两次调用的公共前缀。

    ⚠ **双盲语义不由这句话落在哪一侧承担**，而由「两次独立请求」承担：
    两个判官本来就只该差这一句身份标签，材料完全相同。

    ⚠⚠ **它同时是 `_runtime_contract` 的拼装口。** `evaluator_fingerprint` 记的必须是
    **真发出去的字节**；两处各拼一份就会分家，而分家之后没有任何东西说得出「那一次到底
    发的是什么」（同 `novel/stages/stage5/prompts.py::write_system` 那条纪律）。
    ⚠ 认不出的 role **当场炸**，不兜底：兜底会让一次拼错的 role 安静地跑成另一个判官，
    而产物里的 `judge_rankings` 照样写着两个键。
    """
    if role not in JUDGE_ROLE_SUFFIX:
        raise KeyError(f'没有 {role!r} 这个判官。取值面是 {sorted(JUDGE_ROLE_SUFFIX)}')
    return system, user + JUDGE_ROLE_SUFFIX[role]


#: `_provenance.prompts` 的三个键。**两个判官共用一份 SYS**，身份标签单列。
#:
#: ⚠ 原来是 `HOOK_SEMANTIC_SYS_A` / `_B` 两个键，各存一份 `SEMANTIC_SYS + 标签`：
#: 既把同一段 SYS 存了两遍，又在标签已经不在 system 里之后**继续叫自己 SYS**——
#: 那就是来源账在说假话。键名跟着真实的请求形状走。
def judge_prompts(system: str) -> dict[str, str]:
    """进 `_provenance.prompts` 的那一份。**唯一产地**，测试与生产共用。"""
    return {
        'HOOK_SEMANTIC_SYS': system,
        **{f'HOOK_SEMANTIC_ROLE_{role}': suffix
           for role, suffix in JUDGE_ROLE_SUFFIX.items()},
    }
DECODER_IDENTITY = {
    'name': 'stage6-reference-boundary-double-blind-v1',
    'quality_weights': QUALITY_WEIGHTS,
    'evidence': 'candidate-verbatim-span-or-whole-subject',
    'ranking': 'PASS-before-UNVER-before-FAIL,then-average-score,then-label',
}


@dataclass(frozen=True)
class CandidateAssessment:
    """视图与批准路共用的当前候选状态。"""

    state: str
    reason: str
    review: HookCandidateReview | None = None
    decision: SemanticDecision | None = None
    quality_score: float | None = None
    quality_rank: int | None = None
    semantic_review_seq: int | None = None

    #: ⚠ 这里曾有 `selectable`（`state == 'clear'`）与 `historical_fail`。
    #: `clear` 与 `contradiction` 都只由 automated PASS/FAIL 产生，自动放行门下线后
    #: **两个状态都不可达**，`state` 只剩 `unreviewed` / `uncertain`。
    #: ⚠ 同一批里还曾有 `human_resolvable`（`state in {'unreviewed', 'uncertain'}`）。
    #: 两个状态都删光之后它恒真——而一个恒真的「可人工消解」跨过 API 边界，就是在
    #: 界面上画一道**永远开着的闸**，与恒假的 `selectable` 是同一种误导。⇒ 一并删掉。
    #: 要判「这条候选还用不用人看」，读 `state` 本身（两个值都要人看，理由不同）。


def text_sha1(text: str) -> str:
    """一条文案文本的绑定指纹（10 位）。"""
    return prov.fingerprint(text)


def text_sha256(text: str) -> str:
    """人工决定与校准证据的完整内容指纹。"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _runtime_contract(model: str):
    """The exact static request/decoder surface used by both paid judges.

    ⚠ 这里曾是一个二选一：`--advisory` 走无 dataset 的哨兵指纹，默认路走
    `require_enabled('reference_boundary', …)`——那道闸从未在任何宿主上打开过。
    自动放行门下线后只剩一种，flag 也随之删除（恒真的 flag 是误导）。
    """
    return runtime_fingerprint(
        model=model,
        # ⚠ **静态面必须由 `judge_request` 产出**，不在这里重拼一份：它记的是「真发出去的
        #   字节长什么样」，而身份标签现在在 user 侧（见 `judge_request` 的 docstring）。
        #   逐书变的材料不进指纹，所以这里给空 user——留下的正好是两个判官的静态差异面。
        prompts=[part for role in ('A', 'B')
                 for part in judge_request(SEMANTIC_SYS, '', role)],
        schemas=[HookSemanticResponse],
        decoder_identity=DECODER_IDENTITY,
    )


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


def _judge_decision(judge: str, review: HookCandidateReviewResponse, *,
                    real_label: str, text: str) -> JudgeDecision:
    """Turn one blind response into a hash-bound PASS/FAIL/UNVER decision."""
    verdict = ('UNVER' if review.coverage == 'uncertain' else
               'FAIL' if review.contradictions else 'PASS')
    evidence: list[EvidenceRef] = []
    artifact = f'{hook.REL}#candidate/{real_label}'
    subject = text_sha256(text)
    if verdict == 'PASS':
        evidence.append(EvidenceRef(
            artifact=artifact, subject_sha256=subject, start=0, end=len(text)))
    else:
        for contradiction in review.contradictions:
            start = text.find(contradiction.evidence)
            if start >= 0:
                evidence.append(EvidenceRef(
                    artifact=artifact,
                    subject_sha256=subject,
                    start=start,
                    end=start + len(contradiction.evidence),
                ))
    return JudgeDecision(
        judge=judge,
        verdict=verdict,
        evidence=evidence,
        reason=(review.uncertainty_reason
                if verdict == 'UNVER' else review.concise_reason),
    )


def _average_quality(first: HookCandidateReviewResponse,
                     second: HookCandidateReviewResponse) -> HookQualityScores:
    values = {
        key: round((float(getattr(first.quality, key))
                    + float(getattr(second.quality, key))) / 2, 3)
        for key in QUALITY_WEIGHTS
    }
    return HookQualityScores.model_validate(values)


def materialize_double_blind(
    first: HookSemanticResponse,
    second: HookSemanticResponse,
    *,
    aliases: list[tuple[str, str, str]],
) -> tuple[list[HookCandidateReview], list[SemanticDecision], list[str],
           dict[str, list[str]]]:
    """Combine A/B through the one merge contract.

    The combined review retains averaged quality and reported contradictions for
    diagnosis.  Its *state* is nevertheless owned solely by ``decisions``——
    而合并决议恒是带证据的 `UNVER`（`novel/quality/evaluator.py::double_blind_decision`）。
    """
    alias_map = {neutral: (real, text) for neutral, real, text in aliases}
    by_judge = [
        {review.label: review for review in response.reviews}
        for response in (first, second)
    ]
    reviews: list[HookCandidateReview] = []
    decisions: list[SemanticDecision] = []
    real_by_subject: dict[str, str] = {}
    for neutral, real, text in aliases:
        left, right = by_judge[0][neutral], by_judge[1][neutral]
        subject = text_sha256(text)
        decision = double_blind_decision(
            ability='reference_boundary',
            subject_key=subject,
            first=_judge_decision('A', left, real_label=real, text=text),
            second=_judge_decision('B', right, real_label=real, text=text),
        )
        decisions.append(decision)
        real_by_subject[subject] = real

        contradictions = []
        seen: set[tuple[str, str, str]] = set()
        for row in (left, right):
            for contradiction in row.contradictions:
                key = (contradiction.assertion, contradiction.evidence,
                       contradiction.reason)
                if key not in seen:
                    seen.add(key)
                    contradictions.append(contradiction)

        # 合并决议恒 UNVER ⇒ coverage 恒 `uncertain`、矛盾线索无条件保留。
        # ⚠ 别把 `uncertainty_reason` 省成 None：它是人在界面上读到的那一句，
        #   四条理由逐句不同（含「A/B 同判且证据闭合」那一条）。
        reviews.append(HookCandidateReview(
            label=real,
            coverage='uncertain',
            uncertainty_reason=decision.reason,
            contradictions=contradictions,
            quality=_average_quality(left, right),
            concise_reason=decision.reason,
            text_sha1=text_sha1(text),
        ))

    review_by_label = {review.label: review for review in reviews}
    # ⚠ 排序曾是 `(state_order[verdict], -quality, label)` 三段。合并 verdict 恒 UNVER
    #   之后第一段是常量，留着只会让人以为排序里还含着一个判定。
    ranking = sorted(
        review_by_label,
        key=lambda label: (-quality_score(review_by_label[label]), label),
    )
    judge_rankings = {
        judge: [alias_map[label][0] for label in response.ranking]
        for judge, response in (('A', first), ('B', second))
    }
    return reviews, decisions, ranking, judge_rankings


def run_semantic(ctx: RunContext, client: LlmClient
                 ) -> tuple[HookSemanticReview, CheckReport]:
    current = _runtime_contract(client.model)
    system, user, art, up, aliases = build_prompt(ctx)
    # ⚠ 两次调用是**顺序**的，而这正是身份标签挪到 user 末尾之后省钱的前提：
    #   A 跑完把 `system` + 整包材料写进供应商前缀缓存，B 才发出去 ⇒ B 命中那一整段。
    responses = [
        client.ask_json(
            *judge_request(system, user, role),
            model_cls=HookSemanticResponse,
            purpose=f'{ctx.node}.{role}',
            validator=response_validator(aliases),
        )
        for role in ('A', 'B')
    ]
    reviews, decisions, ranking, judge_rankings = materialize_double_blind(
        responses[0], responses[1], aliases=aliases)

    store = ArtifactStore(ctx)
    artifact = HookSemanticReview(
        judged_texts={real: text_sha1(text) for _neutral, real, text in aliases},
        hook_file_sha1=prov.fingerprint_bytes(store.path(hook.REL).read_bytes()),
        reviews=reviews,
        ranking=ranking,
        judge_rankings=judge_rankings,
        decisions=decisions,
        evaluator_fingerprint=current,
        _provenance=ctx.llm_provenance(
            prompts=judge_prompts(system),
            model=client.model,
        ),
    )
    seqs = semantic_seqs(store)
    seq = (seqs[-1] + 1) if seqs else 1
    rel = SEMANTIC_TPL.format(n=seq)
    store.write(rel, artifact)
    outcomes = hook._judge_artifact(art, up, store)
    return artifact, hook._report(ctx, store, outcomes, art, freshness_rel=rel)


# ⚠⚠ 这里曾有 `_calibration_status()`（不发请求地复查 profile 漂移）与
#     `_is_advisory_artifact()`（用 `dataset_sha256` 哨兵辨认「刻意未校准」的那一跑）。
#     自动放行门下线后所有产物都是同一种，没有第二类要辨认，也没有「漂移」可复查——
#     两个函数连同 `calibrated` 这个第三返回值一起删除。
def _current_artifact(
    store: ArtifactStore,
    art: Hook,
) -> tuple[HookSemanticReview | None, str, int | None]:
    """当前那一份语义审判，以及「为什么它不可用」的人读理由。

    ⚠ 三种「没有当前结论」是分开的：没跑过 / 上游变了 / 判的不是这批文本。
    它们都落到 `artifact=None`，但 `why` 逐句不同——那句话会一直传到 inbox 与终审包。
    """
    got = latest_semantic(store)
    if got is None:
        return None, '没有 stage6/semantic_<n>.json；本能力保持人工-only', None
    seq, artifact = got
    rel = SEMANTIC_TPL.format(n=seq)
    if store.freshness(rel).state != 'fresh':
        return None, f'{rel} 的上游已变化；当前结论降为 UNVER', seq

    current_shas = {text_sha1(text) for _label, text in hook._texts_of(art)}
    judged_shas = {review.text_sha1 for review in artifact.reviews}
    if current_shas != judged_shas:
        return None, ('当前文本与最近一次质量审判不是同一个精确集合：'
                      f'未覆盖 {len(current_shas - judged_shas)}，'
                      f'额外旧文本 {len(judged_shas - current_shas)}；当前结论降为 UNVER'), seq
    decision_subjects = {decision.subject_key for decision in artifact.decisions}
    current_subjects = {text_sha256(text) for _label, text in hook._texts_of(art)}
    if current_subjects != decision_subjects:
        return None, '当前文本与合并 SemanticDecision 不是同一个精确集合', seq
    return artifact, '', seq


def current_assessments(store: ArtifactStore, art: Hook) -> dict[str, CandidateAssessment]:
    """按文本 sha1 返回当前评审状态；键可同时服务候选与重复的定稿文本。"""
    artifact, why, seq = _current_artifact(store, art)
    current_texts = {
        text_sha1(text): text
        for _label, text in hook._texts_of(art)
    }
    if artifact is None:
        return {
            sha: CandidateAssessment(state='unreviewed', reason=why)
            for sha in current_texts
        }

    rank_by_label = {label: i for i, label in enumerate(artifact.ranking, 1)}
    decision_by_subject = {decision.subject_key: decision for decision in artifact.decisions}
    by_sha: dict[str, CandidateAssessment] = {}
    for review in artifact.reviews:
        text = next(text for _label, text in hook._texts_of(art)
                    if text_sha1(text) == review.text_sha1)
        decision = decision_by_subject[text_sha256(text)]
        # ⚠ 判过 ⇒ 恒 `uncertain`。这**不是**「查不了」，而是「查了、有证据、但本仓
        #   不替人盖章」——`decision.reason` 逐句说明是哪一种，别把它压成一句套话。
        by_sha[review.text_sha1] = CandidateAssessment(
            state='uncertain',
            reason=decision.reason,
            review=review,
            decision=decision,
            quality_score=quality_score(review),
            quality_rank=rank_by_label.get(review.label),
            semantic_review_seq=seq,
        )
    return by_sha


def assessment_for_text(store: ArtifactStore, art: Hook, text: str) -> CandidateAssessment:
    return current_assessments(store, art).get(
        text_sha1(text),
        CandidateAssessment(state='unreviewed', reason='当前文本没有质量审判记录'),
    )


def require_human_resolution(
    store: ArtifactStore,
    art: Hook,
    text: str,
    label: str,
    *,
    actor: str,
    reason: str,
    expected_subject_sha256: str,
) -> tuple[CandidateAssessment, str]:
    """Validate an explicit approval bound to the exact approved bytes.

    Returns ``(assessment, basis)``.  ⚠ `basis` 恒是 `'human_verification'`——
    它仍作为返回值给出，是为了让**写 resolution 的那一处**只有一个来源；
    `calibrated_pass` 随自动放行门一起被裁决掉了（`HookApprovalResolution.basis`）。

    ⚠⚠ **产品含义**：这里曾有一道「已有校准双盲 FAIL ⇒ 人工也不得绕过」的硬闸。
    自动 FAIL 不再可产，那道闸随之消失 ⇒ **横在「有矛盾的文案」与成书之间的只剩
    人在这里读判官证据这一道**。矛盾线索仍逐条呈现（`hook_candidate_contradiction`），
    但没有任何机制会替人否决一个候选。
    """
    if not actor.strip() or not reason.strip():
        raise TerminalState('定稿必须显式给 --actor 与 --reason（actor 是自报身份）')
    actual = text_sha256(text)
    if expected_subject_sha256 != actual:
        raise TerminalState(
            f'候选 {label} 已变化，不能用旧页面定稿：'
            f'期望 {expected_subject_sha256}，实得 {actual}')
    assessment = assessment_for_text(store, art, text)
    return assessment, 'human_verification'


def require_approved_clear(store: ArtifactStore, art: Hook) -> CandidateAssessment:
    """下游消费闸：**存在定稿，且它的人工消解仍绑着当前字节。**

    ⚠ 这里曾有三条分支：校准 FAIL 拒绝、`clear` 自动放行、以及「原来的校准 PASS 漂了」
    的兜底 raise。三条都由 automated 结论驱动，随自动放行门一起消失。
    留下的这一条不是变松了——`subject_sha256` 那道字节绑定本来就是真正在拦人的那一条：
    改一个字，旧 resolution 立刻失效。
    """
    if art.approved is None:
        raise TerminalState('开篇文案还没定稿——先跑 stage6.approve --pick N')
    resolution = art.approved.resolution
    actual = text_sha256(art.approved.text)
    if resolution.subject_sha256 != actual:
        raise TerminalState('定稿 resolution 绑定的不是当前文本字节')
    return assessment_for_text(store, art, art.approved.text)


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
    artifact, why, _seq = _current_artifact(store, art)
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
        if review.contradictions:
            disposition = 'contradiction 线索（UNVER，不自动阻断）'
            for contradiction in review.contradictions:
                candidate_findings.append(finding(
                    'hook_candidate_contradiction',
                    detail=(f'[{label}] {disposition}：{contradiction.assertion}；'
                            f'候选证据：{contradiction.evidence}；{contradiction.reason}'),
                    evidence=[f'{label}:{contradiction.evidence}'],
                ))
        elif assessment.state == 'uncertain':
            candidate_findings.append(finding(
                'hook_candidate_contradiction',
                detail=f'[{label}] 双盲合并为 UNVER：{assessment.reason}',
                # ⚠⚠ **这一格原来是 `[]`，而那让人工豁免闭环在这条上失效。**
                # 空凭据 ⇒ `evidence_key` 恒是空串 ⇒ 三条候选各自的「核对不完整」
                # **共用同一个收敛键**，一次豁免会把三条一并豁免掉——正是
                # `novel/wontfix.py` 明令禁止的形状（「换了个位置的同类问题」
                # 不是人做的决定）。
                # ⚠ 用 `<候选>:<文本指纹>` 而不是候选名：候选名是**位置**，
                # 换一条文案还是同一个名字；而豁免必须在文本变化后自动失效
                # （2026-08-15 裁决：失效由证据自身保证，不靠版本号）。
                # ⚠ 不放 `uncertainty_reason`：那句话每次重审都可能换措辞，
                # 放进键里等于「重跑一次就失效」——那是被裁掉的那个方案。
                evidence=[f'{label}:{review.text_sha1}'],
            ))

    # ⚠⚠ `hook_semantic_contradiction` 现在是 **`unver` 判据**（`contracts/judges/stage6.py`）。
    #     它曾经是 HARD，命中两条路——定稿文本 `state == 'contradiction'`、或三条候选全
    #     contradiction。那个 state 只由 automated FAIL 产生，自动放行门下线后**不可达**，
    #     于是它一条 Finding 都产不出来了。
    #     ⚠ 继续把它挂成 HARD 会有两个坏结果：`tests/test_hard_counterexamples.py` 是双向
    #     断言，要么逼人伪造一个绕开真实契约的反例，要么给一条刻意双向的守卫开豁免口；
    #     而界面上一个永远绿的 HARD 会被读成「查过了，没矛盾」。降为 `unver` 之后
    #     badge 是 amber、路由仍是 `human`，说的是实话：**矛盾要人自己看**。
    scanned = f'{len(artifact.reviews)} 份去重文本'
    if art.approved is not None:
        chosen = assessments[text_sha1(art.approved.text)]
        subject = f'定稿文本状态为 {chosen.state}：{chosen.reason}'
    else:
        subject = '还没有定稿；三条候选的矛盾线索见 hook_candidate_contradiction'
    hard = unverifiable(
        'hook_semantic_contradiction',
        f'{subject}。本仓没有自动放行门，实质矛盾一律由人读判官证据后裁决',
        scanned=scanned)

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
