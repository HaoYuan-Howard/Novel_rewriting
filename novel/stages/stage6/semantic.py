#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 匿名质量审判：实质矛盾 + 吸引力排序。

审判一次看完全部候选，但只收到 ``T1/T2/...`` 中性标签，避免 approach 名称诱导偏好。
它可以看到原作文案、全书事实/故事地图和开篇成稿：原文用于质量参照，
全书用于矛盾核对，开篇成稿用于声线参照。剧透不构成违规；只有改变身份、关系、关键因果
或故事结果才是 material contradiction。结论仍按文本 sha1 绑定，因此 ``--pick`` 不会
使审判过期，``--apply-edit`` 会。
"""
from __future__ import annotations

from contracts.artifacts import STAGE6_SEMANTIC

import glob
import hashlib
import json
import time
import os
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

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
from novel.errors import (
    ArtifactUnreadable,
    ProvenanceRefused,
    TerminalState,
    UpstreamMissing,
)
from novel.llm.client import LlmClient
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from novel.report.findings import Outcome, ran, unverifiable
from novel.quality.evaluator import double_blind_decision, runtime_fingerprint
from novel.run import RunContext
from novel.stages.stage6 import hook, prompts as stage6_prompts
from novel.store import ArtifactStore

SEMANTIC_TPL = STAGE6_SEMANTIC
_SEMANTIC_RX = re.compile(r'^semantic_(\d+)\.json$')
SEMANTIC_MAX_TOKENS = 64_000


class _SemanticResponseSource(Protocol):
    """legacy client 与双判官 session pool 共用的最窄 producer 面。"""

    model: str

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any: ...

    def accepted_attempt(self, purpose: str) -> str | None: ...

    def accepted_models(self, *purposes: str) -> tuple[str, ...]: ...

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]: ...

    def restore_checkpoint_acceptance(
            self, purpose: str, record: Mapping[str, Any], **kwargs: Any,
    ) -> None: ...

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

输入会给出：匿名候选、原始文案、重写后的开篇正文，以及全书事实和逐章故事地图。
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


def judge_prefix_observation(
        system: str, user: str, *, driver: str,
        prompt_json_layout: str = stage6_prompts.PRETTY_JSON_LAYOUT,
        judge_stagger_ms: int = 0,
) -> dict[str, Any]:
    """返回双盲首轮真实 stdin 的无正文公共前缀证据。

    这只证明 A/B **具备**逐字节公共前缀，不声称供应商已经命中缓存；命中与
    未命中仍只认 ledger/session usage。两个请求继续由独立 session 真并发执行。
    """
    from novel.agents import render_structured_agent_stdin_bytes

    payloads = {
        role: render_structured_agent_stdin_bytes(
            *judge_request(system, user, role), driver=driver)
        for role in ('A', 'B')
    }
    first, second = payloads['A'], payloads['B']
    common = next(
        (index for index, pair in enumerate(zip(first, second))
         if pair[0] != pair[1]),
        min(len(first), len(second)),
    )
    prefix = first[:common]
    return {
        'protocol': 'stage6-semantic-independent-prefix/v1',
        'driver': driver,
        'prompt_json_layout': prompt_json_layout,
        'judge_stagger_ms': judge_stagger_ms,
        'request_bytes': {role: len(payload) for role, payload in payloads.items()},
        'request_sha256': {
            role: hashlib.sha256(payload).hexdigest()
            for role, payload in payloads.items()
        },
        'common_prefix_bytes': common,
        'common_prefix_sha256': hashlib.sha256(prefix).hexdigest(),
        'cache_hit_guaranteed': False,
        # 真实命中/未命中仍只认每条独立 wire 的 ledger usage。
        'provider_cache_usage_source': '_service/ledger.jsonl',
        'session_policy': (
            'independent-concurrent-stagger/v1' if judge_stagger_ms else
            'independent-parallel'),
    }


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
    # ⚠ v1 → v2：匿名标签的落位口径从「产物顺序」改成「文本 sha1 排序」
    #   （`_anonymisation_order`）。它改变真发出去的字节 ⇒ 必须在指纹里说出来，
    #   否则事后没有任何东西分得清一份结论是在哪套匿名口径下判的。
    'name': 'stage6-reference-boundary-double-blind-v2',
    'anonymisation': 'neutral-labels-assigned-by-candidate-text-sha1-order',
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


@dataclass(frozen=True)
class SemanticCandidateContext:
    """一次双盲审判的只读输入快照；不含响应，也没有任何落盘能力。"""

    system: str
    user: str
    judge_users: tuple[tuple[str, str], ...]
    art: Hook
    up: hook._Upstreams
    aliases: tuple[tuple[str, str, str], ...]
    judged: Mapping[str, Any]
    input_sha256: str
    hook_file_sha1: str
    prompt_json_layout: str = stage6_prompts.PRETTY_JSON_LAYOUT
    judge_stagger_ms: int = 0

    def user_for(self, role: str) -> str:
        try:
            return dict(self.judge_users)[role]
        except KeyError as exc:
            raise KeyError(f'prepared semantic 没有 judge role={role!r}') from exc


@dataclass(frozen=True)
class SemanticCandidateEvaluation:
    """无落盘 provisional 结果；终态只能交给 ``finalize_semantic_candidate``。"""

    candidate: HookSemanticReview
    requested_model: str
    adopted_models: tuple[str, ...]
    accepted_attempts: tuple[tuple[str, str], ...]
    input_sha256: str
    _responses: tuple[tuple[str, HookSemanticResponse], ...]
    _candidate_sha256: str


def text_sha1(text: str) -> str:
    """一条文案文本的绑定指纹（10 位）。"""
    return prov.fingerprint(text)


def text_sha256(text: str) -> str:
    """人工决定与校准证据的完整内容指纹。"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _runtime_contract(
        model: str, *, prompt_json_layout: str | None = None,
        judge_stagger_ms: int | None = None,
):
    """The exact static request/decoder surface used by both paid judges.

    ⚠ 这里曾是一个二选一：`--advisory` 走无 dataset 的哨兵指纹，默认路走
    `require_enabled('reference_boundary', …)`——那道闸从未在任何宿主上打开过。
    自动放行门下线后只剩一种，flag 也随之删除（恒真的 flag 是误导）。
    """
    layout = (stage6_prompts.prompt_json_layout()
              if prompt_json_layout is None else prompt_json_layout)
    stagger_ms = (stage6_prompts.semantic_stagger_ms()
                  if judge_stagger_ms is None else judge_stagger_ms)
    # 默认 pretty-v1 沿用既有 decoder identity，避免零字节变化的默认路平白 stale；
    # compact 实验臂改变真实 user bytes，必须把 arm 明写进 evaluator fingerprint。
    decoder_identity = DECODER_IDENTITY
    if layout != stage6_prompts.PRETTY_JSON_LAYOUT or stagger_ms:
        decoder_identity = {
            **DECODER_IDENTITY,
            'prompt_json_layout': layout,
            'judge_stagger_ms': stagger_ms,
            'session_policy': (
                'independent-concurrent-stagger/v1'
                if stagger_ms else 'independent-parallel'),
        }
    return runtime_fingerprint(
        model=model,
        # ⚠ **静态面必须由 `judge_request` 产出**，不在这里重拼一份：它记的是「真发出去的
        #   字节长什么样」，而身份标签现在在 user 侧（见 `judge_request` 的 docstring）。
        #   逐书变的材料不进指纹，所以这里给空 user——留下的正好是两个判官的静态差异面。
        prompts=[part for role in ('A', 'B')
                 for part in judge_request(SEMANTIC_SYS, '', role)],
        schemas=[HookSemanticResponse],
        decoder_identity=decoder_identity,
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


def _anonymisation_order(art: Hook) -> list[tuple[str, str]]:
    """匿名标签 `T1/T2/…` 落在哪条候选上。**按文本指纹排，不按产物里的先后。**

    ⚠⚠ **这一位是判据承重的。** 原来直接用 `_unique_texts` 的返回顺序，而那个顺序
    就是候选在产物里的顺序、也就是 `HOOK_APPROACHES` 的顺序 ⇒ `T1` 恒是第一种策略、
    `T3` 恒是第三种。**位置与策略身份完全混淆**：本仓已记录过判官"先按位置选答案、
    再编一套相反理由"的形状（`reason` 因此不能当证据），在那个前提下，
    "哪条策略的文案更好"这个问题**根本无法从质量排序里读出来**——排序里混着一个
    与策略同步变化的位置项，而两者的贡献分不开。

    ⚠ 用**文本 sha1 排序**而不是随机打乱：随机会让同一份产物两次重审得到不同请求，
    既毁掉可复现性，也让缓存恒 miss（本仓禁止不留账的不确定性）。按内容排则是
    确定性的，且与策略枚举的顺序无关——换一批文案，位置就换。

    ⚠ 这**不改变任何落盘字段**：产物里记的是解匿名后的真实标签。变的只有请求字节，
    因此存量 `semantic_<n>.json` 仍可读、仍绑得住它自己的文本，只是重审必然 miss 缓存。
    """
    return sorted(_unique_texts(art), key=lambda row: text_sha1(row[1]))


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _prompt_judged(up: hook._Upstreams) -> dict[str, Any]:
    """只绑定真正进入 ``rewritten_opening`` 的 canonical Stage5 章稿。

    ``lead_drafts`` 为复用 Stage6 hook 的判定面会返回全书 ``judged``；而语义判官
    实际只收到 ``mat.lead``。后半书改字仍会按既有业务门先让全书 Stage5 Review
    暂不可用；但零成本重跑 Review 后，若 opening 与其他 prompt 输入没变，这份审判
    应恢复 current，不能再被一条从未下发的正文永久判 stale。筛选必须只在这里做，
    prepared、artifact、commit 与 currentness 全部消费同一结果。
    """
    lead_ordinals = {
        int(row.get('output_ordinal'))
        for row in up.mat.lead
        if isinstance(row, Mapping)
        and isinstance(row.get('output_ordinal'), int)
        and not isinstance(row.get('output_ordinal'), bool)
    }
    return {
        rel: row
        for rel, row in sorted(up.judged.items())
        if row.output_ordinal <= up.upto and row.output_ordinal in lead_ordinals
    }


def _judged_text_projection(rows: Mapping[str, Any]) -> dict[str, dict[str, object]]:
    """Stage5 reference currentness 只看路径、章序与实际消费的正文 SHA-1。"""
    out: dict[str, dict[str, object]] = {}
    for rel, raw in sorted(rows.items()):
        row = (raw.model_dump(by_alias=True, mode='json')
               if hasattr(raw, 'model_dump') else dict(raw))
        consumed = row.get('consumed')
        if not isinstance(consumed, Mapping):
            raise ProvenanceRefused(f'{rel}: semantic judged 缺 consumed 投影')
        ordinal = row.get('output_ordinal')
        text_fp = consumed.get('text_sha1')
        if (not isinstance(ordinal, int) or isinstance(ordinal, bool)
                or ordinal < 1 or not isinstance(text_fp, str)):
            raise ProvenanceRefused(
                f'{rel}: semantic judged 缺合法 output_ordinal/consumed.text_sha1')
        out[rel] = {'output_ordinal': ordinal, 'text_sha1': text_fp}
    return out


def _prompt_payload(
        art: Hook, up: hook._Upstreams,
        aliases: tuple[tuple[str, str, str], ...],
) -> dict[str, object]:
    """真正进入两个 judge user 的动态 allowlist；input hash 与 prompt 共用。"""
    return {
        'texts': [
            {'label': neutral, 'text': text}
            for neutral, _real, text in aliases
        ],
        'source_reference': up.src_hook,
        'rewritten_opening': [
            {'chapter': row.get('output_ordinal'), 'text': row.get('text') or ''}
            for row in up.mat.lead
        ],
        'story_truth': up.mat.story_truth,
        'whole_story_map': list(up.mat.story_map),
    }


def _semantic_input_payload(
        art: Hook, up: hook._Upstreams,
        aliases: tuple[tuple[str, str, str], ...],
) -> dict[str, object]:
    prompt = _prompt_payload(art, up, aliases)
    return {
        # run/checkpoint 保守绑定 producer 真正读到的 typed hook；普通 freshness 仍由
        # ``stage6_semantic_hook`` 的文本投影保证 --pick 不会误判历史审判 stale。
        'hook': art.model_dump(by_alias=True, mode='json'),
        'references': {
            key: prompt[key]
            for key in (
                'source_reference', 'rewritten_opening',
                'story_truth', 'whole_story_map',
            )
        },
        'aliases': [
            {
                'neutral': neutral,
                'real': real,
                'text_sha256': text_sha256(text),
            }
            for neutral, real, text in aliases
        ],
    }


def _read_hook_as_input(store: ArtifactStore) -> Hook:
    """正式运行必须走 ``read`` 记上游边；旧 unit fake 只实现 ``read_own``。"""
    reader = getattr(store, 'read', None)
    if callable(reader):
        return reader(Hook, hook.REL)
    # 这一支只保持既有的无盘单元替身；生产 ``ArtifactStore`` 必有 ``read``。
    art, _raw = store.read_own(Hook, hook.REL)
    return art


def build_prompt(ctx: RunContext) -> tuple[str, str, Hook, hook._Upstreams,
                                               list[tuple[str, str, str]]]:
    """构造一次匿名审判请求；返回 ``(neutral, real, text)`` 映射供落盘解匿名。"""
    store = ArtifactStore(ctx)
    if not store.exists(hook.REL):
        raise UpstreamMissing(f'{hook.REL} 不在盘上——先跑 stage6.hook')
    if store.freshness(hook.REL).state != 'fresh':
        raise TerminalState(
            'stage6/hook.json 的生成上游已经变化；不要付费审一份过期文案，先重跑 stage6.hook')
    art = _read_hook_as_input(store)
    up = hook._Upstreams(ctx, store)

    aliases = tuple(
        (f'T{i}', real_label, text)
        for i, (real_label, text) in enumerate(_anonymisation_order(art), 1)
    )
    payload = _prompt_payload(art, up, aliases)
    user = ('匿名评审以下候选。先核对实质矛盾，再按留存能力评分与排序。\n\n'
            + stage6_prompts.dump_prompt_json(payload))
    return SEMANTIC_SYS, user, art, up, list(aliases)


def _hook_file_sha1(store: ArtifactStore) -> str:
    try:
        return prov.fingerprint_bytes(store.path(hook.REL).read_bytes())
    except OSError as exc:
        raise ArtifactUnreadable(f'无法读取 {hook.REL} 的审计指纹：{exc}') from exc


def _context_from_inputs(ctx: RunContext) -> SemanticCandidateContext:
    system, user, art, up, raw_aliases = build_prompt(ctx)
    aliases = tuple(raw_aliases)
    judge_users: list[tuple[str, str]] = []
    for role in ('A', 'B'):
        role_system, role_user = judge_request(system, user, role)
        if role_system != system:
            raise ProvenanceRefused(
                f'stage6.semantic judge {role} 改写了共享 system；双盲缓存面已分叉')
        judge_users.append((role, role_user))
    judged = _prompt_judged(up)
    return SemanticCandidateContext(
        system=system,
        user=user,
        judge_users=tuple(judge_users),
        art=art,
        up=up,
        aliases=aliases,
        judged=judged,
        input_sha256=_canonical_sha256(
            _semantic_input_payload(art, up, aliases)),
        hook_file_sha1=_hook_file_sha1(ArtifactStore(ctx)),
        prompt_json_layout=stage6_prompts.prompt_json_layout(),
        judge_stagger_ms=stage6_prompts.semantic_stagger_ms(),
    )


def _uses_agent_runs(ctx: RunContext) -> bool:
    return getattr(ctx, 'executor_profile', None) is not None


def _assert_prepared_memory(prepared: SemanticCandidateContext) -> None:
    current_input = _canonical_sha256(_semantic_input_payload(
        prepared.art, prepared.up, prepared.aliases))
    if current_input != prepared.input_sha256:
        raise ProvenanceRefused(
            'stage6.semantic prepared context 在内存中发生漂移；拒绝继续请求或落盘')
    if _judged_text_projection(_prompt_judged(prepared.up)) \
            != _judged_text_projection(prepared.judged):
        raise ProvenanceRefused(
            'stage6.semantic prepared Stage5 reference 在内存中发生漂移')
    expected_users = tuple(
        (role, judge_request(prepared.system, prepared.user, role)[1])
        for role in ('A', 'B')
    )
    if prepared.judge_users != expected_users:
        raise ProvenanceRefused(
            'stage6.semantic prepared judge request 字节发生漂移')
    if prepared.prompt_json_layout not in stage6_prompts.PROMPT_JSON_LAYOUTS:
        raise ProvenanceRefused('stage6.semantic prepared JSON layout 未登记')
    if not 0 <= prepared.judge_stagger_ms <= stage6_prompts.MAX_SEMANTIC_STAGGER_MS:
        raise ProvenanceRefused('stage6.semantic prepared stagger 超出登记上限')


def _assert_prepared_current(
        ctx: RunContext, prepared: SemanticCandidateContext,
) -> SemanticCandidateContext:
    """付费前/提交前重建 exact allowlist；校验读取不污染正式 provenance。"""
    _assert_prepared_memory(prepared)
    current_code = prov.code_version()
    if current_code != ctx.code:
        raise ProvenanceRefused(
            f'stage6.semantic executor sessions 期间代码版本发生漂移：'
            f'start={ctx.code} current={current_code}')
    with ctx.validation_reads():
        current = _context_from_inputs(ctx)
    if (current.input_sha256 != prepared.input_sha256
            or current.system != prepared.system
            or current.judge_users != prepared.judge_users
            or current.prompt_json_layout != prepared.prompt_json_layout
            or current.judge_stagger_ms != prepared.judge_stagger_ms
            or current.hook_file_sha1 != prepared.hook_file_sha1
            or _judged_text_projection(current.judged)
            != _judged_text_projection(prepared.judged)):
        raise ProvenanceRefused(
            'stage6.semantic 运行期间 hook/Stage5 reference/prompt 输入发生漂移；'
            '必须新建双判官 sessions')
    ctx.require_observed_roots_current()
    return current


def prepare_semantic_candidate(ctx: RunContext) -> SemanticCandidateContext:
    """读取并冻结双判官共同主体；不发模型请求、不写 semantic 正式产物。"""
    if ctx.node != 'stage6.semantic':
        raise ValueError(
            f'prepare_semantic_candidate 只接受 stage6.semantic，实得 {ctx.node!r}')
    prepared = _context_from_inputs(ctx)
    _assert_prepared_memory(prepared)
    if prepared.judge_stagger_ms and not _uses_agent_runs(ctx):
        raise ProvenanceRefused(
            'Stage6 semantic stagger 实验只支持独立 executor sessions；'
            'legacy 顺序客户端不能冒充前缀缓存实验')
    if _uses_agent_runs(ctx):
        from novel.llm import stage6_runs

        stage6_runs.ensure_run(
            ctx,
            input_sha256=prepared.input_sha256,
            system=prepared.system,
            users=dict(prepared.judge_users),
            response_model=HookSemanticResponse,
            max_tokens=SEMANTIC_MAX_TOKENS,
            experiment_arm=stage6_runs.semantic_experiment_arm(
                judge_stagger_ms=prepared.judge_stagger_ms),
        )
    return prepared


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


def _judge_purpose(ctx: RunContext, role: str) -> str:
    return f'{ctx.node}.{role}'


def _request_semantic_role(
        ctx: RunContext,
        source: _SemanticResponseSource,
        prepared: SemanticCandidateContext,
        *,
        role: str,
        validator: Callable[[HookSemanticResponse, ChatResponse], str | None],
) -> HookSemanticResponse:
    """一个独立判官的 wire→checkpoint 窄边界；供 agent 双 session 并发复用。"""
    from novel.llm import stage6_runs

    purpose = _judge_purpose(ctx, role)
    response = source.ask_json(
        prepared.system,
        prepared.user_for(role),
        model_cls=HookSemanticResponse,
        purpose=purpose,
        max_tokens=SEMANTIC_MAX_TOKENS,
        validator=validator,
        recovery_policy=stage6_runs.RECOVERY_POLICY,
    )
    stage6_runs.write_checkpoint(
        ctx,
        role=role,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user_for(role),
        response_model=HookSemanticResponse,
        max_tokens=SEMANTIC_MAX_TOKENS,
        response_payload=response.model_dump(by_alias=True, mode='json'),
        acceptance=source.accepted_checkpoint(purpose),
    )
    return response


def _request_semantic_role_after_stagger(
        ctx: RunContext, source: _SemanticResponseSource,
        prepared: SemanticCandidateContext, *, role: str,
        validator: Callable[[HookSemanticResponse, ChatResponse], str | None],
        stagger_b: bool = True,
) -> HookSemanticResponse:
    """两个 worker 同时提交；只让 B 在自己的 worker 内做有界 opt-in 错峰。"""
    if stagger_b and role == 'B' and prepared.judge_stagger_ms:
        time.sleep(prepared.judge_stagger_ms / 1000)
    return _request_semantic_role(
        ctx, source, prepared, role=role, validator=validator)


def request_semantic_candidates(
        ctx: RunContext,
        source: _SemanticResponseSource,
        prepared: SemanticCandidateContext,
) -> dict[str, HookSemanticResponse]:
    """取得 A/B；legacy 保持顺序，agent 全量验 checkpoint 后并发缺失 session。"""
    _assert_prepared_current(ctx, prepared)
    validate = response_validator(list(prepared.aliases))
    restored: dict[str, tuple[HookSemanticResponse, dict[str, Any]]] = {}
    responses: dict[str, HookSemanticResponse] = {}

    if _uses_agent_runs(ctx):
        required = (
            'accepted_checkpoint', 'restore_checkpoint_acceptance',
            'accepted_attempt', 'accepted_models',
        )
        missing = [name for name in required if not callable(getattr(source, name, None))]
        if missing:
            raise ProvenanceRefused(
                f'stage6.semantic agent source 缺 checkpoint bridge {missing}；'
                '已在首个 wire 前停止')
        from novel.llm import stage6_runs

        # 两份都先验完：B 的损坏不能在 A 已经新付费之后才被发现。
        for role in stage6_runs.JUDGE_ROLES:
            got = stage6_runs.restore_checkpoint(
                ctx,
                role=role,
                input_sha256=prepared.input_sha256,
                system=prepared.system,
                user=prepared.user_for(role),
                response_model=HookSemanticResponse,
                max_tokens=SEMANTIC_MAX_TOKENS,
                validator=validate,
            )
            if got is not None:
                restored[role] = got
        for role, (response, acceptance) in restored.items():
            source.restore_checkpoint_acceptance(
                _judge_purpose(ctx, role),
                acceptance,
                system=prepared.system,
                user=prepared.user_for(role),
                max_tokens=SEMANTIC_MAX_TOKENS,
                recovery_policy=stage6_runs.RECOVERY_POLICY,
            )
            responses[role] = response

        missing_roles = tuple(
            role for role in stage6_runs.JUDGE_ROLES if role not in responses)
        if missing_roles:
            # 双判官是两个独立 provider session。共享 RunContext 的来源账不是线程局部，
            # 所以 exact input 只在启动前统一复核；线程里只碰各自 session/checkpoint。
            _assert_prepared_current(ctx, prepared)
            # 错峰的实验对象是两条同时新发的独立 session。若 A 已从 checkpoint
            # 恢复、只剩 B，一条 worker 没有可错开的同批 peer，sleep 只会白增墙钟。
            stagger_b = set(missing_roles) == set(stage6_runs.JUDGE_ROLES)
            with ThreadPoolExecutor(max_workers=len(missing_roles)) as executor:
                futures = {
                    role: executor.submit(
                        _request_semantic_role_after_stagger,
                        ctx,
                        source,
                        prepared,
                        role=role,
                        validator=validate,
                        stagger_b=stagger_b,
                    )
                    for role in missing_roles
                }
                # 取值顺序固定为 A/B；即使 A 失败，context manager 仍等 B 收口，
                # B 已接受的响应会先写 checkpoint，重启不重复付费。
                for role in missing_roles:
                    responses[role] = futures[role].result()
        return responses

    # legacy LlmClient 的双请求、purpose 与顺序逐字保留；不把旧 acceptance
    # 伪装成 node-executor/v1，也不创建 stage6 run sidecar。
    for role in ('A', 'B'):
        responses[role] = source.ask_json(
            prepared.system,
            prepared.user_for(role),
            model_cls=HookSemanticResponse,
            purpose=_judge_purpose(ctx, role),
            validator=validate,
        )
    return responses


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


def _validated_responses(
        prepared: SemanticCandidateContext,
        responses: Mapping[str, HookSemanticResponse],
) -> dict[str, HookSemanticResponse]:
    if set(responses) != {'A', 'B'}:
        raise ProvenanceRefused(
            f'stage6.semantic responses 必须恰有 A/B，实得 {sorted(responses)}')
    validate = response_validator(list(prepared.aliases))
    out: dict[str, HookSemanticResponse] = {}
    for role in ('A', 'B'):
        response = HookSemanticResponse.model_validate(responses[role])
        why = validate(
            response,
            ChatResponse(content=response.model_dump_json(by_alias=True)),
        )
        if why is not None:
            raise ProvenanceRefused(
                f'stage6.semantic judge {role} 候选绕过响应闸：{why}')
        out[role] = response
    return out


def _accepted_attempts(
        ctx: RunContext, source: _SemanticResponseSource,
) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for role in ('A', 'B'):
        purpose = _judge_purpose(ctx, role)
        attempt_id = source.accepted_attempt(purpose)
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ProvenanceRefused(
                f'{purpose}: 双盲响应已进入候选，但缺 accepted attempt')
        rows.append((role, attempt_id))
    if len({attempt for _role, attempt in rows}) != 2:
        raise ProvenanceRefused('stage6.semantic A/B 不能共用同一 accepted attempt')
    return tuple(rows)


def _adopted_models(
        ctx: RunContext, source: _SemanticResponseSource,
) -> tuple[str, ...]:
    purposes = tuple(_judge_purpose(ctx, role) for role in ('A', 'B'))
    models = tuple(sorted(set(source.accepted_models(*purposes))))
    if not models or any(not isinstance(model, str) or not model for model in models):
        raise ProvenanceRefused(
            'stage6.semantic 双盲候选缺实际 accepted model，不能按 requested model 猜')
    return models


def _materialize_semantic_candidate(
        ctx: RunContext,
        source: _SemanticResponseSource,
        prepared: SemanticCandidateContext,
        responses: Mapping[str, HookSemanticResponse],
        *,
        settle: bool,
) -> HookSemanticReview:
    checked = _validated_responses(prepared, responses)
    reviews, decisions, ranking, judge_rankings = materialize_double_blind(
        checked['A'], checked['B'], aliases=list(prepared.aliases))
    adopted_models = _adopted_models(ctx, source)
    return HookSemanticReview(
        judged_texts={
            real: text_sha1(text)
            for _neutral, real, text in prepared.aliases
        },
        hook_file_sha1=prepared.hook_file_sha1,
        reviews=reviews,
        ranking=ranking,
        judge_rankings=judge_rankings,
        decisions=decisions,
        evaluator_fingerprint=_runtime_contract(
            source.model,
            prompt_json_layout=prepared.prompt_json_layout,
            judge_stagger_ms=prepared.judge_stagger_ms,
        ),
        judged=dict(prepared.judged),
        _provenance=ctx.llm_provenance(
            prompts=judge_prompts(prepared.system),
            model=source.model,
            adopted_models=adopted_models,
            settle=settle,
        ),
    )


def evaluate_semantic_candidate(
        ctx: RunContext,
        source: _SemanticResponseSource,
        prepared: SemanticCandidateContext,
        responses: Mapping[str, HookSemanticResponse],
) -> SemanticCandidateEvaluation:
    """合并 A/B 并铸造 provisional typed artifact；不选序号、不写盘、不结算。"""
    if ctx.node != 'stage6.semantic':
        raise ValueError(
            f'evaluate_semantic_candidate 只接受 stage6.semantic，实得 {ctx.node!r}')
    _assert_prepared_current(ctx, prepared)
    checked = _validated_responses(prepared, responses)
    attempts = _accepted_attempts(ctx, source)
    adopted_models = _adopted_models(ctx, source)
    candidate = _materialize_semantic_candidate(
        ctx, source, prepared, checked, settle=False)
    payload = candidate.model_dump(by_alias=True, mode='json')
    return SemanticCandidateEvaluation(
        candidate=candidate,
        requested_model=source.model,
        adopted_models=adopted_models,
        accepted_attempts=attempts,
        input_sha256=prepared.input_sha256,
        _responses=tuple((role, checked[role]) for role in ('A', 'B')),
        _candidate_sha256=_canonical_sha256(payload),
    )


def _semantic_finalization_payload(
        *, rel: str, seq: int, prepared: SemanticCandidateContext,
        artifact: HookSemanticReview,
) -> dict[str, object]:
    payload = artifact.model_dump(by_alias=True, mode='json')
    provenance = payload.get('_provenance')
    if not isinstance(provenance, Mapping):
        raise ProvenanceRefused('stage6.semantic candidate 缺 typed provenance')
    return {
        'artifact': rel,
        'seq': seq,
        'input_sha256': prepared.input_sha256,
        'judged': payload.get('judged'),
        'judged_texts': payload.get('judged_texts'),
        'hook_file_sha1': payload.get('hook_file_sha1'),
        'inputs': provenance.get('inputs'),
    }


def _journal_rel(record: Mapping[str, Any]) -> tuple[str, int]:
    rel = record.get('artifact')
    if not isinstance(rel, str):
        raise ProvenanceRefused('stage6.semantic commit journal 缺 artifact 路径')
    match = _SEMANTIC_RX.fullmatch(os.path.basename(rel))
    if (match is None or os.path.dirname(rel).replace('\\', '/') != 'stage6'
            or rel != SEMANTIC_TPL.format(n=int(match.group(1)))):
        raise ProvenanceRefused(
            f'stage6.semantic commit journal 指向非法 artifact={rel!r}')
    return rel, int(match.group(1))


def _note_semantic_adoptions(
        ctx: RunContext, *, rel: str, candidate: HookSemanticReview,
        adoptions: tuple[tuple[str, str], ...],
) -> None:
    fingerprint = prov.projection_fingerprint(
        candidate.model_dump(by_alias=True, mode='json'))
    if len(adoptions) != 2 or {purpose for purpose, _attempt in adoptions} != {
            _judge_purpose(ctx, 'A'), _judge_purpose(ctx, 'B')}:
        raise ProvenanceRefused(
            f'stage6.semantic 正式产物必须恰有 A/B 两份 adoption，实得 {adoptions}')
    for purpose, attempt_id in adoptions:
        ctx.ledger.note_adoption(
            sid=ctx.sid,
            node=ctx.node,
            purpose=purpose,
            attempt_id=attempt_id,
            artifact=rel,
            artifact_fingerprint=fingerprint,
            chapter=None,
        )


def verify_semantic_delivery(
        ctx: RunContext,
        prepared: SemanticCandidateContext,
        expected: HookSemanticReview,
        *,
        rel: str,
) -> tuple[HookSemanticReview, CheckReport]:
    """typed readback + exact equality + freshness/latest + Stage5 reference 终验。"""
    if ctx.node != 'stage6.semantic':
        raise ValueError(
            f'verify_semantic_delivery 只接受 stage6.semantic，实得 {ctx.node!r}')
    current = _assert_prepared_current(ctx, prepared)
    store = ArtifactStore(ctx)
    match = _SEMANTIC_RX.fullmatch(os.path.basename(rel))
    if match is None or rel != SEMANTIC_TPL.format(n=int(match.group(1))):
        raise ProvenanceRefused(f'stage6.semantic 正式路径非法：{rel!r}')
    seq = int(match.group(1))
    seqs = semantic_seqs(store)
    if not seqs or seqs[-1] != seq:
        raise ProvenanceRefused(
            f'stage6.semantic readback 不是当前 latest：target={seq}, disk={seqs}')

    with ctx.validation_reads():
        landed, _raw = store.read_own(
            HookSemanticReview, rel, require_current=True)
        freshness = store.freshness(rel)
    expected_payload = expected.model_dump(by_alias=True, mode='json')
    landed_payload = landed.model_dump(by_alias=True, mode='json')
    if (expected_payload != landed_payload
            or _canonical_sha256(expected_payload)
            != _canonical_sha256(landed_payload)):
        raise ProvenanceRefused(
            'stage6.semantic 正式 readback 与本次终态候选不完全相等')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage6.semantic 正式 readback freshness='
            f'{freshness.state}/{freshness.reason}；拒绝 delivered')
    if landed.hook_file_sha1 != current.hook_file_sha1:
        raise ProvenanceRefused(
            'stage6.semantic 正式 readback 的 hook_file_sha1 已不是当前输入字节')
    if (_judged_text_projection(landed.judged)
            != _judged_text_projection(current.judged)):
        raise ProvenanceRefused(
            'stage6.semantic 正式 readback 绑定的 Stage5 开篇正文已变化')

    with ctx.validation_reads():
        outcomes = hook._judge_artifact(current.art, current.up, store)
        report = hook._report(
            ctx, store, outcomes, current.art, freshness_rel=rel)
    return landed, report


def _assert_evaluation_identity(
        ctx: RunContext,
        source: _SemanticResponseSource,
        prepared: SemanticCandidateContext,
        evaluation: SemanticCandidateEvaluation,
) -> None:
    if evaluation.input_sha256 != prepared.input_sha256:
        raise ProvenanceRefused(
            'stage6.semantic evaluation 不属于当前 prepared input')
    if source.model != evaluation.requested_model:
        raise ProvenanceRefused(
            'stage6.semantic 评估后 requested model 漂移')
    if _accepted_attempts(ctx, source) != evaluation.accepted_attempts:
        raise ProvenanceRefused(
            'stage6.semantic 评估后 accepted attempts 漂移')
    if _adopted_models(ctx, source) != evaluation.adopted_models:
        raise ProvenanceRefused(
            'stage6.semantic 评估后 adopted models 漂移')
    if _canonical_sha256(evaluation.candidate.model_dump(
            by_alias=True, mode='json')) != evaluation._candidate_sha256:
        raise ProvenanceRefused(
            'stage6.semantic provisional candidate 在评估后被修改')


def finalize_semantic_candidate(
        ctx: RunContext,
        source: _SemanticResponseSource,
        prepared: SemanticCandidateContext,
        evaluation: SemanticCandidateEvaluation,
) -> tuple[HookSemanticReview, CheckReport]:
    """终态只提交一次；agent 走 checkpoint→journal→readback→双 adoption。"""
    if ctx.node != 'stage6.semantic':
        raise ValueError(
            f'finalize_semantic_candidate 只接受 stage6.semantic，实得 {ctx.node!r}')
    _assert_evaluation_identity(ctx, source, prepared, evaluation)
    _assert_prepared_current(ctx, prepared)
    responses = dict(evaluation._responses)

    from novel.llm import stage6_runs

    store = ArtifactStore(ctx)
    with stage6_runs.commit_ownership(ctx.novel_dir):
        _assert_prepared_current(ctx, prepared)
        journal: Mapping[str, Any] | None = None
        if _uses_agent_runs(ctx):
            journal = stage6_runs.committed(ctx)
            if journal is None:
                journal = stage6_runs.prepared(ctx)
        if journal is None:
            seqs = semantic_seqs(store)
            seq = seqs[-1] + 1 if seqs else 1
            rel = SEMANTIC_TPL.format(n=seq)
        else:
            rel, seq = _journal_rel(journal)
            later = [item for item in semantic_seqs(store) if item > seq]
            if later:
                raise ProvenanceRefused(
                    f'run={ctx.run_id} 的 semantic commit={seq} 已不是 latest；'
                    f'盘上还有后续审判 {later}')

        # legacy 与 agent 共用序号空间。即使当前分支不造 journal，也不能复用
        # 另一 agent run 已 prepared、但尚未正式写出的 crash 恢复位。
        stage6_runs.refuse_foreign_commit_reservations(ctx, rel)
        # 所有不依赖最终 provenance 的序号/恢复闸都已通过，才推进 usage settle。
        # 否则 foreign prepared 等静态阻断会在没有正式产物时吞掉本 context 的结算窗。
        artifact = _materialize_semantic_candidate(
            ctx, source, prepared, responses, settle=True)
        candidate = artifact.model_dump(by_alias=True, mode='json')
        if (_canonical_sha256(candidate) != evaluation._candidate_sha256
                or candidate != evaluation.candidate.model_dump(
                    by_alias=True, mode='json')):
            raise ProvenanceRefused(
                'stage6.semantic provisional/final typed candidate 不一致；'
                '拒绝在非确定合并器上写盘')

        finalization = _semantic_finalization_payload(
            rel=rel, seq=seq, prepared=prepared, artifact=artifact)
        role_attempts = dict(evaluation.accepted_attempts)
        if _uses_agent_runs(ctx):
            journal = stage6_runs.prepare_commit(
                ctx,
                artifact=rel,
                artifact_payload=candidate,
                finalization_payload=finalization,
                adoptions=role_attempts,
            )
            if (not stage6_runs.verify_commit_artifact(journal, candidate)
                    or not stage6_runs.verify_commit_finalization(
                        journal, finalization)):
                raise ProvenanceRefused(
                    'stage6.semantic commit journal 与最终候选/输入/序号不闭合')

        if store.exists(rel):
            existing, _raw = store.read_own(
                HookSemanticReview, rel, require_current=True)
            if existing.model_dump(by_alias=True, mode='json') != candidate:
                raise ProvenanceRefused(
                    f'{rel} 已存在另一份 semantic 候选；拒绝覆盖历史审判')
        else:
            store.write(rel, artifact)

        landed, report = verify_semantic_delivery(
            ctx, prepared, artifact, rel=rel)
        landed_payload = landed.model_dump(by_alias=True, mode='json')
        committed_finalization = _semantic_finalization_payload(
            rel=rel, seq=seq, prepared=prepared, artifact=landed)
        if _uses_agent_runs(ctx):
            assert journal is not None
            # adoption 是正式交付的一部分；在它之前再关一次代码/输入漂移窗，避免
            # 已知过期的产物仍被记成「本 run 采纳」。
            _assert_prepared_current(ctx, prepared)
            restored = stage6_runs.restore_commit_adoptions(ctx, journal)
            _note_semantic_adoptions(
                ctx, rel=rel, candidate=landed, adoptions=restored)
            _assert_prepared_current(ctx, prepared)
            stage6_runs.mark_committed(
                ctx,
                artifact_payload=landed_payload,
                finalization_payload=committed_finalization,
            )
        else:
            _assert_prepared_current(ctx, prepared)
            _note_semantic_adoptions(
                ctx,
                rel=rel,
                candidate=landed,
                adoptions=tuple(
                    (_judge_purpose(ctx, role), attempt_id)
                    for role, attempt_id in evaluation.accepted_attempts
                ),
            )
        return landed, report


def run_semantic(ctx: RunContext, client: LlmClient
                 ) -> tuple[HookSemanticReview, CheckReport]:
    """legacy 单轮包装；agent executor 复用相同四段，但启用 checkpoint/journal。"""
    prepared = prepare_semantic_candidate(ctx)
    responses = request_semantic_candidates(ctx, client, prepared)
    evaluation = evaluate_semantic_candidate(
        ctx, client, prepared, responses)
    return finalize_semantic_candidate(ctx, client, prepared, evaluation)


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

    # `inputs` freshness 管普通上游；Stage5 正文是 judged 主体，必须另用真正下发
    # 给 A/B 的开篇 allowlist 闭合。全书 Stage5 Review 若失效，`_Upstreams` 仍按既有
    # 业务门让 semantic 暂不可用；免费重跑 Review 后，后半书不会继续残留成假 stale。
    if isinstance(artifact, HookSemanticReview):
        try:
            ctx = store.ctx
            with ctx.validation_reads():
                current_up = hook._Upstreams(ctx, store)
                current_judged = _prompt_judged(current_up)
        except TerminalState as exc:
            return None, (
                f'{rel} 无法重验 Stage5 开篇正文：{exc}；当前结论降为 UNVER'), seq
        if (_judged_text_projection(artifact.judged)
                != _judged_text_projection(current_judged)):
            return None, (
                f'{rel} 判过的 Stage5 开篇正文已变化或缺失；当前结论降为 UNVER'), seq

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


def current_judge_rankings(
    store: ArtifactStore, art: Hook,
) -> dict[str, list[str]] | None:
    """当前可用语义审判的 A/B 原始排序；不可用时明确为 ``None``。"""
    artifact, _why, _seq = _current_artifact(store, art)
    if artifact is None:
        return None
    return {judge: list(ranking)
            for judge, ranking in artifact.judge_rankings.items()}


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
    """下游消费闸：定稿同时绑定当前字节与当前代码/规则/证据工作包。

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
    # 这是下游消费闸：重算工作包与取语义证据所读的 Hook 上游只决定
    # 「能不能继续」，没有进入调用方的 prompt/产物。隔离在 helper 的最低边界，
    # 避免 Stage7 rename、assemble 或未来新调用方漏包后污染自己的 provenance。
    with store.ctx.validation_reads():
        pack, _report = hook._approval_workpack_snapshot(store, art)
        if (resolution.workpack_sha256 is None
                or resolution.workpack_sha256 != pack.workpack_sha256
                or pack.disposition != 'already_current'):
            raise TerminalState(
                '开篇文案旧批准没有绑定当前代码/规则/机械与 A/B 证据工作包——'
                '请刷新 Stage6 approval workpack 后重跑 stage6.approve（0 成本）')
        return assessment_for_text(store, art, art.approved.text)


def unavailable_outcomes(why: str) -> list[Outcome]:
    return [
        unverifiable('hook_semantic_contradiction', why, scanned='0 份当前质量审判'),
        unverifiable('hook_candidate_contradiction', why, scanned='0 份当前质量审判'),
        unverifiable('hook_quality_rank', why, scanned='0 份当前质量审判'),
    ]


def judge_voices(decision: SemanticDecision | None) -> str:
    """两名盲评判官**各自原样说了什么**，拼成报告里那半句。

    ⚠⚠ **这不是装饰，它补的是一处契约未兑现。** `R-9.19` 写着「报告必须原样呈现
    两名判官实际说了什么，不能压成一句『未校准』」，而 `double_blind_decision`
    算出的四选一 `reason` 在**最常见的第四种情形**（A/B 同判且各自证据闭合）里
    **逐字与候选无关**——三条候选拿到同一句治理模板。实测一本书的终审包 §5 因此
    是四条一模一样的话，而人恰恰要在那一面挑一条定稿。

    判官真正有区分度的判断一直在 `decision.judges[].reason` 里（A 判某条「提前剧透
    结局」、判另一条「更像剧情摘要缺具体场景」），在此之前**全仓没有任何读者**。
    这里只是把它接回人做决定的那一面——**不新增任何 LLM 调用，数据早就在产物里**。

    ⚠ 按 `judge` 名排序输出，不按 `judges[]` 的入列顺序：那个顺序是 A/B 合流时的
    实参位置，读的人要的是「A 说了什么、B 说了什么」这个稳定口径。
    """
    if decision is None:
        return ''
    return '；' + ' ｜ '.join(
        f'判官 {row.judge}（{row.verdict}）：{row.reason}'
        for row in sorted(decision.judges, key=lambda row: row.judge))


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
                    # ⚠ 判官原话跟着**每一条**结论走，不另立一条 finding 汇总：
                    #   finding 是被逐条消费的（inbox 一行、wontfix 一个证据键、
                    #   报告一行），汇总条会让「有人看过并决定不修」落在错的粒度上。
                    detail=(f'[{label}] {disposition}：{contradiction.assertion}；'
                            f'候选证据：{contradiction.evidence}；{contradiction.reason}'
                            f'{judge_voices(assessment.decision)}'),
                    evidence=[f'{label}:{contradiction.evidence}'],
                ))
        elif assessment.state == 'uncertain':
            candidate_findings.append(finding(
                'hook_candidate_contradiction',
                detail=(f'[{label}] 双盲合并为 UNVER：{assessment.reason}'
                        f'{judge_voices(assessment.decision)}'),
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
