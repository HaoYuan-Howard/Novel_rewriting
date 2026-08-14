#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 文案语义审判：逐文本抽 claims，再与 arc 三项/下发面对齐（`R-9.19`/`R-9.20`）。

形态照 `novel/stages/stage5/semantic.py`（两相 + 逐字证据回查 + uncertain 不洗绿），
差别只有三处，而每处都是产物类逼出来的：
  · 审判单位是**一条文案文本**（3 候选＋定稿）不是一章，第一相 arc-blind **且** secret-blind；
  · 第二相是**单表裁决**（一条 claim 一行：arc_leak / invented / grounded / unver），
    不是按 secret 聚合——文案的 ground truth 是 arc 三项与下发面，不是揭示时间线；
  · 结论按**文本内容 sha1** 绑定（`judged_texts`），不绑 hook.json 整文件——
    `--pick` 只改 `approved` 键、不换文本字节，整文件指纹会让定稿动作把语义结论
    误判过期（`R-9.17` 教训的同型）。
"""
from __future__ import annotations

import glob
import json
import os
import re

from contracts.schema.common import Finding
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage6 import (
    Hook, HookSemanticResponse, HookSemanticReview, HookTextClaims,
    HookTextClaimsResponse,
)
from novel import prov
from novel.core.text import norm, whitespace_equivalent_slice
from novel.errors import TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from novel.report.findings import Outcome, ran, unverifiable
from novel.run import RunContext
from novel.stages.stage6 import checks, hook
from novel.store import ArtifactStore

SEMANTIC_TPL = 'stage6/semantic_{n}.json'
_SEMANTIC_RX = re.compile(r'^semantic_(\d+)\.json$')

#: `reason` 里禁止出现的 arc 原文连续片段长度。8 个内容字符已足以复述一句收场——
#: 更短会误伤普通措辞，更长等于允许整句抄进报告。
_ARC_ECHO_MIN = 8

CLAIMS_SYS = """\
你是开篇文案的命题抽取器。你只能看到一条文案文本，不知道也不应猜测本书的结局、高潮或任何隐藏设定。

对【当前这一条文案】抽取其中明确表达或强蕴含的事实性 claims（人物、身份、关系、事件、结果、数字等）。每条 claim 给简短、自足、可判真假的 assertion，并给该文案文本中的【逐字、连续】evidence。

规则：
- label 原样交回；claim_id 在本条文案内唯一，优先写成 c1、c2 这样的字符串。
- evidence 必须逐字来自这条文案，宁可短而精确；不得编造、改写或拼接证据。
- 修辞、悬念式反问、氛围渲染不算命题；只抽读者会当作事实接收的陈述。
- 若无法确信已覆盖这条文案的全部事实性命题，coverage=uncertain 并说明原因；绝不能用 complete 掩盖遗漏。

只输出 JSON，不要额外文字。"""

CLAIMS_HINT = {
    'label': 'identity_contrast',
    'coverage': 'complete|uncertain',
    'uncertainty_reason': 'str|null',
    'claims': [{
        'claim_id': 'c1',
        'assertion': 'str',
        'evidence': 'str',
    }],
}
CLAIMS_SYSTEM = with_json_shape(CLAIMS_SYS, CLAIMS_HINT)


ALIGN_SYS = """\
你是开篇文案的语义审校器。输入只包含：若干条已验过逐字证据的文案 claims、本书的结局/高潮取值面（arc）、写手当时看得见的下发面（given）与开篇正文（lead）。不要补写输入中不存在的 claim。

对【每一条 claim】给出恰好一个裁决：
- arc_leak：claim 以任何改写形式让读者提前确定了 arc 里的结局/高潮/人物终局（语义等价即算，不要求字面重合）。
- invented：claim 陈述的事实在 given 与 lead 里都找不到依据（口径：不许发明事实，必须发明文字）。
- grounded：claim 在 given 或 lead 里有据，且不泄露 arc。
- unver：证据含混、语义蕴含不可靠时必须 unver，绝不能猜成 grounded。

规则：
- decisions 必须恰好覆盖输入里的每一个 (label, claim_id)，一条一行。
- reason 简述蕴含关系或不确定性，不得复述 arc 原文的任何片段。

只输出 JSON（顶层键 decisions），不要额外文字。"""

ALIGN_HINT = {
    'decisions': [{
        'label': 'identity_contrast',
        'claim_id': 'c1',
        'status': 'arc_leak|invented|grounded|unver',
        'reason': 'str',
    }],
}
ALIGN_SYSTEM = with_json_shape(ALIGN_SYS, ALIGN_HINT)


def text_sha1(text: str) -> str:
    """一条文案文本的绑定指纹（10 位）。**唯一落点**——判与查两侧共用。"""
    return prov.fingerprint(text)


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


def build_claim_user(label: str, text: str) -> str:
    """一条文本一个 arc/secret-blind 请求；失败域与缓存键收窄到本条文案。"""
    payload = {'label': label, 'text': text}
    return ('以下是一条开篇文案文本。只抽取这一条文案的事实性 claims；'
            '输入不含结局与秘密，也不要猜。\n\n'
            + json.dumps(payload, ensure_ascii=False, indent=2))


def build_prompts(ctx: RunContext) -> tuple[str, list[tuple[str, str, str]], Hook,
                                            'hook._Upstreams']:
    """构造逐文本请求；预览与真跑共用，并拒绝拿过期文案发起付费审判。

    返回的 `users` 已按文本内容 sha1 **去重**（定稿通常与某候选逐字相同，省一次调用）；
    去重只影响调用次数，`judged_texts` 仍记全部标签。
    """
    store = ArtifactStore(ctx)
    if not store.exists(hook.REL):
        raise UpstreamMissing(f'{hook.REL} 不在盘上——先跑 stage6.hook')
    if store.freshness(hook.REL).state != 'fresh':
        raise TerminalState(
            f'{hook.REL} 的上游已变化；先跑 `stage6.hook --recheck`（0 成本），'
            f'不能拿旧文案发起付费语义审判')
    art = store.read(Hook, hook.REL)
    up = hook._Upstreams(ctx, store)

    users: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for label, text in hook._texts_of(art):
        sha = text_sha1(text)
        if sha in seen:
            continue
        seen.add(sha)
        users.append((label, build_claim_user(label, text), text))
    return CLAIMS_SYSTEM, users, art, up


def claims_validator(label: str, body: str):
    """守第一相的 label 回传、claim 唯一性与文案逐字证据。"""

    def validate(resp: HookTextClaimsResponse, _wire: ChatResponse) -> str | None:
        if resp.label != label:
            return f'label 必须原样交回 {label!r}，实得 {resp.label!r}'
        seen: set[str] = set()
        for claim in resp.claims:
            if claim.claim_id in seen:
                return f'{label} 的 claim_id 重复：{claim.claim_id}'
            seen.add(claim.claim_id)
            if claim.evidence not in body:
                recovered = whitespace_equivalent_slice(claim.evidence, body)
                if recovered is None:
                    return f'{label} 的证据不是文案连续子串：{claim.evidence!r}'
                claim.evidence = recovered
        return None

    return validate


def request_claims(ctx: RunContext, client: LlmClient, system: str,
                   users: list[tuple[str, str, str]]) -> list[HookTextClaims]:
    """逐文本抽 claims。**串行即可**——最多 4 条、每条几百字，不值得开线程池。"""
    if not users:
        raise TerminalState('文案语义审判没有可抽取的文本')
    out: list[HookTextClaims] = []
    for label, user, text in users:
        resp = client.ask_json(
            system, user, model_cls=HookTextClaimsResponse,
            purpose=f'{ctx.node}.claims.{label}',
            validator=claims_validator(label, text))
        out.append(HookTextClaims(
            label=resp.label, coverage=resp.coverage,
            uncertainty_reason=resp.uncertainty_reason, claims=resp.claims,
            text_sha1=text_sha1(text)))
    return out


def build_alignment_prompt(texts: list[HookTextClaims],
                           up: 'hook._Upstreams') -> tuple[str, str]:
    """第二相只消费已验真的 claims + arc 三项 + 写手看得见的东西。

    ⚠ `given` 按构造不含 arc（`R-9.9` 不下发 + `hook_downsend_leak` 兜底），
    所以 arc 在这份 payload 里**只出现在 `arc` 键下**——审判模型必须知道收场
    才能判泄底，但它的输出被 `alignment_validator` 的回显闸拦着，抄不回来。
    """
    payload = {
        'texts': [{
            'label': t.label,
            'claims': [{'claim_id': c.claim_id, 'assertion': c.assertion,
                        'evidence': c.evidence} for c in t.claims],
        } for t in texts],
        'arc': checks.arc_sources(up.arc),
        'given': up.mat.given,
        'lead': '\n'.join(r.get('text') or '' for r in up.mat.lead),
    }
    user = ('以下 claims 的 evidence 已通过文案逐字校验。只对这些 claims 逐条裁决，'
            '不得补造命题或证据。\n\n'
            + json.dumps(payload, ensure_ascii=False, indent=2))
    return ALIGN_SYSTEM, user


def _arc_echo(reason: str, arc_srcs: list[str]) -> str | None:
    """`reason` 里有没有复述 arc 原文的连续片段（norm 空间 ≥8 内容字符）。"""
    nr = norm(reason)
    if not nr:
        return None
    for src in arc_srcs:
        ns = norm(src)
        for i in range(len(ns) - _ARC_ECHO_MIN + 1):
            gram = ns[i:i + _ARC_ECHO_MIN]
            if gram in nr:
                return gram
    return None


def alignment_validator(texts: list[HookTextClaims], arc_srcs: list[str]):
    """守第二相的裁决覆盖、取值前提与「不把结局抄进报告」的代码硬闸。"""
    expected = {(t.label, c.claim_id) for t in texts for c in t.claims}

    def validate(resp: HookSemanticResponse, _wire: ChatResponse) -> str | None:
        got = [(d.label, d.claim_id) for d in resp.decisions]
        if len(got) != len(set(got)):
            return '同一条 claim 出现了两份裁决'
        if set(got) != expected:
            return (f'decisions 必须恰好覆盖 {sorted(expected)}，实得 {sorted(set(got))}')
        for d in resp.decisions:
            if d.status == 'arc_leak' and not arc_srcs:
                return (f'{d.label}/{d.claim_id} 判了 arc_leak，而这本书的 arc 取值面为空'
                        f'——没有 ground truth 的泄底裁决不收')
            echoed = _arc_echo(d.reason, arc_srcs)
            if echoed:
                return (f'{d.label}/{d.claim_id} 的 reason 复述了结局原文片段（…{echoed}…）'
                        f'——审判报告只许引用文案自身字节，不许把 arc 抄进来')
        return None

    return validate


def run_semantic(ctx: RunContext, client: LlmClient) -> tuple[HookSemanticReview, CheckReport]:
    claims_system, users, art, up = build_prompts(ctx)
    texts = request_claims(ctx, client, claims_system, users)
    arc_srcs = checks.arc_sources(up.arc)
    align_system, align_user = build_alignment_prompt(texts, up)
    align_resp = client.ask_json(
        align_system, align_user, model_cls=HookSemanticResponse,
        purpose=f'{ctx.node}.alignment',
        validator=alignment_validator(texts, arc_srcs))

    store = ArtifactStore(ctx)
    artifact = HookSemanticReview(
        judged_texts={label: text_sha1(text) for label, text in hook._texts_of(art)},
        hook_file_sha1=prov.fingerprint_bytes(store.path(hook.REL).read_bytes()),
        texts=texts,
        decisions=align_resp.decisions,
        _provenance=ctx.llm_provenance(
            prompts={'HOOK_SEMANTIC_CLAIMS_SYS': claims_system,
                     'HOOK_SEMANTIC_ALIGN_SYS': align_system},
            model=client.model),
    )
    seq = (semantic_seqs(store)[-1] + 1) if semantic_seqs(store) else 1
    rel = SEMANTIC_TPL.format(n=seq)
    store.write(rel, artifact)
    # 报告即 stage6 的合并报告：`_judge_artifact` 会经 `semantic.outcomes`
    # 读回刚落盘的这一份；第一节新鲜度说的是本次写下的 semantic 产物。
    outcomes = hook._judge_artifact(art, up, store)
    return artifact, hook._report(ctx, store, outcomes, art, freshness_rel=rel)


def unavailable_outcomes(why: str) -> list[Outcome]:
    return [
        unverifiable('hook_semantic_spoiler', why, scanned='0 份当前语义审判'),
        unverifiable('hook_semantic_grounding', why, scanned='0 份当前语义审判'),
    ]


def outcomes(store: ArtifactStore, art: Hook | None) -> list[Outcome]:
    """当前 semantic artifact → 两条语义判据；缺失/过期/文本未审/不确定一律 UNVER。"""
    if art is None:
        return unavailable_outcomes('stage6/hook.json 不可读，语义结论无从绑定')
    current = {label: text_sha1(text) for label, text in hook._texts_of(art)}
    got = latest_semantic(store)
    if got is None:
        return unavailable_outcomes(
            '没有 stage6/semantic_<n>.json；这不是通过，需运行 `stage6.semantic` 或人工审阅')
    seq, artifact = got
    rel = SEMANTIC_TPL.format(n=seq)
    judged = set(artifact.judged_texts.values())
    unjudged = sorted(label for label, sha in current.items() if sha not in judged)
    if unjudged:
        return unavailable_outcomes(
            f'{unjudged} 的当前文本没有被最近一次语义审判覆盖（文本改过或新增）；'
            f'先重跑 `stage6.semantic`')
    if store.freshness(rel).state != 'fresh':
        return unavailable_outcomes(
            f'{rel} 的上游已变化（arc/下发面可能已不同）；先重跑 `stage6.semantic`')

    claims = {(t.label, c.claim_id): c for t in artifact.texts for c in t.claims}
    spoiler_fs: list[Finding] = []
    grounding_fs: list[Finding] = []
    for d in artifact.decisions:
        claim = claims[(d.label, d.claim_id)]
        if d.status == 'arc_leak':
            spoiler_fs.append(finding(
                'hook_semantic_spoiler',
                detail=(f'[{d.label}] 以改写形式说破了结局/高潮：{claim.assertion}；'
                        f'证据：{claim.evidence}'),
                evidence=[f'{d.label}:{claim.evidence}']))
        elif d.status == 'invented':
            grounding_fs.append(finding(
                'hook_semantic_grounding',
                detail=(f'[{d.label}] 陈述了下发面与开篇正文里都无据的事实：'
                        f'{claim.assertion}；证据：{claim.evidence}'),
                evidence=[f'{d.label}:{claim.evidence}']))

    uncertain_texts = [t.label for t in artifact.texts if t.coverage == 'uncertain']
    unver_claims = [f'{d.label}/{d.claim_id}' for d in artifact.decisions
                    if d.status == 'unver']
    scanned = (f'{len(artifact.texts)} 份文本 × '
               f'{sum(len(t.claims) for t in artifact.texts)} 条 claim')
    why = (f'模型未能完整判定：coverage=uncertain 的文本 {uncertain_texts or "无"}；'
           f'unver 的 claim {unver_claims or "无"}。按契约交人工，不洗成通过')

    def outcome_of(rule: str, fs: list[Finding]) -> Outcome:
        # 真命中永远报出来（藏一条已发现的泄底比误报更坏）；
        # 「干净」这个结论只有在覆盖完整时才成立，否则是 unver。
        if fs:
            return ran(rule, fs, scanned=scanned)
        if uncertain_texts or unver_claims:
            return unverifiable(rule, why, scanned=scanned)
        return ran(rule, scanned=scanned)

    return [outcome_of('hook_semantic_spoiler', spoiler_fs),
            outcome_of('hook_semantic_grounding', grounding_fs)]
