#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 书级语义审判：逐章 claims，再与 Stage 2 secret 揭示时点对齐。"""
from __future__ import annotations

from contracts.artifacts import STAGE5_SEMANTIC

import glob
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from contracts.schema.common import Finding
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage5 import (
    JudgedDraft, Review, SemanticAlignmentResponse, SemanticChapterClaims,
    SemanticClaimsResponse, SemanticReview, SemanticSecretAdjudication,
)
from novel.core.text import whitespace_equivalent_slice as _whitespace_equivalent_slice
from novel.errors import TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from novel.observability import ChapterProgressTracker
from novel.report.findings import Outcome, ran, unverifiable
from novel.run import RunContext
from novel.stages.stage5 import measure as M
from novel.stages.stage5 import review
from novel.store import ArtifactStore

SEMANTIC_TPL = STAGE5_SEMANTIC
_SEMANTIC_RX = re.compile(r'^semantic_(\d+)\.json$')

CLAIMS_SYS = """\
你是小说正文的命题抽取器。你只能看到正文，不会也不应猜测任何隐藏设定或待审秘密。

对【当前这一章】抽取正文明确表达或强蕴含的结构化 claims。每条 claim 给简短、自足、可判真假的 assertion，并给该章正文中的【逐字、连续】evidence。

规则：
- 顶层 chapters 必须恰好含当前一章，output_ordinal 原样交回；claim_id 在章内唯一，优先写成 c1、c2 这样的字符串。
- evidence 必须逐字来自对应章，宁可短而精确；不得编造、改写或拼接证据。
- 不把仅有氛围、多义暗示、猜测、反问或角色明确否定的内容当作确定命题。
- 聚合等价表述，关注身份、关系、规则、状态、因果与结果；不要把每个普通动作和每句台词机械枚举成 claim。
- 若无法确信已覆盖该章所有可能影响设定揭示的命题，coverage=uncertain 并说明原因；绝不能用 complete 掩盖遗漏。

只输出 JSON（顶层键 chapters），不要额外文字。"""

CLAIMS_HINT = {
    'chapters': [{
        'output_ordinal': 1,
        'coverage': 'complete|uncertain',
        'uncertainty_reason': 'str|null',
        'claims': [{
            'claim_id': 'c1',
            'assertion': 'str',
            'evidence': 'str',
        }],
    }],
}
CLAIMS_SYSTEM = with_json_shape(CLAIMS_SYS, CLAIMS_HINT)


ALIGN_SYS = """\
你是小说全书级秘密泄露审校器。输入只包含一组已经从正文抽出并验过逐字证据的 claims，以及 Stage 2 定义的 secrets 和合法揭示章；不要补写输入中不存在的 claim。

对【每一条 secret】判断：只要揭示章之前的 claim 已让读者能确定该 fact，status=leak；覆盖完整且没有这种 claim 才能 clear；证据含混、语义蕴含不可靠，或任一揭示前章节 coverage=uncertain 时必须 status=unver，绝不能猜成 clear。

规则：
- evidence_refs 只能引用输入里的 `(output_ordinal, claim_id)`。
- leak 的引用必须全部早于输入给定的 reveal_chapter。
- clear 与 unver 的 evidence_refs 必须是空数组 `[]`。
- reason 简述语义蕴含关系或不确定性，不复述整章或虚构证据。

只输出 JSON（顶层键 secrets），不要额外文字。"""

ALIGN_HINT = {
    'secrets': [{
        'secret_id': 'secret_01',
        'status': 'leak|clear|unver',
        'evidence_refs': [{'output_ordinal': 1, 'claim_id': 'c1'}],
        'reason': 'str',
    }],
}
ALIGN_SYSTEM = with_json_shape(ALIGN_SYS, ALIGN_HINT)


def semantic_seqs(store: ArtifactStore) -> list[int]:
    out: list[int] = []
    for path in glob.glob(os.path.join(store.path('stage5'), 'semantic_*.json')):
        match = _SEMANTIC_RX.match(os.path.basename(path))
        if match is not None:
            out.append(int(match.group(1)))
    return sorted(out)


def latest_semantic(store: ArtifactStore) -> tuple[int, SemanticReview] | None:
    seqs = semantic_seqs(store)
    if not seqs:
        return None
    seq = seqs[-1]
    art, _raw = store.read_own(SemanticReview, SEMANTIC_TPL.format(n=seq))
    return seq, art


def _reveal_of(secret: dict) -> int:
    chapters = [int(event['chapter']) for event in secret.get('reader_reveal_events') or []]
    if not chapters:
        raise TerminalState(f'{secret.get("secret_id")}: 没有合法揭示章，语义审判无从对齐')
    return min(chapters)


def _validated_formal_drafts(by_ord: dict[int, list[dict]], planned: set[int]
                             ) -> dict[int, dict]:
    # `.INVALID` 是被落盘闸门拦下的旁路问题稿，不会取代同章已有的
    # `chNN.json` 正式稿。语义审判只绑定当前正式稿；否则一次失败的自救尝试
    # 会让仍然完整可用的全书永久无法受审。
    missing = sorted(planned - set(by_ord))
    unexpected = sorted(set(by_ord) - planned)
    duplicated = sorted(ordinal for ordinal, rows in by_ord.items() if len(rows) != 1)
    if missing or unexpected or duplicated:
        raise TerminalState(
            f'语义审判要求每个计划章恰好一份正式稿；缺章 {missing}，'
            f'越界正式稿 {unexpected}，双份/异常 {duplicated}')
    return {ordinal: by_ord[ordinal][0] for ordinal in sorted(planned)}


def _current_drafts(store: ArtifactStore, book: M.Book
                    ) -> tuple[dict[int, dict], dict[str, JudgedDraft]]:
    by_ord, _invalids, judged = M.load_drafts(store)
    return _validated_formal_drafts(by_ord, set(book.chapters)), judged


def _reveal_chapter(book: M.Book, secret: dict) -> int:
    """Stage 2 的 source chapter 显式投影到 Stage 4 output chapter。

    当前契约是一源章一输出章；仍从 ``from_source_ordinals`` 反查，避免未来一旦调整
    规划映射，语义审判悄悄继续拿源章号当输出章号。
    """
    source = _reveal_of(secret)
    matches = sorted(
        ordinal for ordinal, entry in book.chapters.items()
        if source in [int(value) for value in (entry.get('from_source_ordinals') or [])])
    if len(matches) != 1:
        raise TerminalState(
            f'{secret.get("secret_id")}: Stage 2 揭示源章 {source} 必须映射到恰好一个 '
            f'Stage 4 输出章，实得 {matches}')
    return matches[0]


def _judged_texts(judged: dict[str, JudgedDraft]) -> dict[str, str | None]:
    return {rel: (item.consumed.text_sha1 if item.consumed else None)
            for rel, item in judged.items()}


def _mechanical_is_current(rev: Review, judged: dict[str, JudgedDraft]) -> bool:
    return _judged_texts(rev.judged) == _judged_texts(judged)


def build_claim_user(ordinal: int, draft: dict) -> str:
    """一章一个 secret-blind 请求；失败域、缓存键与正文可见面都收窄到本章。"""
    payload = {
        'chapters': [{
            'output_ordinal': ordinal,
            'text': draft.get('text') or '',
        }],
    }
    return ('以下是当前一章成稿。只抽取这一章的 claims；输入不含秘密，也不要猜秘密。\n\n'
            + json.dumps(payload, ensure_ascii=False, indent=2))


def build_prompts(ctx: RunContext) -> tuple[str, list[tuple[int, str]], int, Review,
                                             M.Book, dict[int, dict],
                                             dict[str, JudgedDraft]]:
    """构造逐章 secret-blind 请求；预览与真跑共用，并拒绝拿旧验收审新稿。"""
    store = ArtifactStore(ctx)
    book = review.load_book(ctx, store)
    if not book.secrets:
        raise TerminalState('这本书没有 Stage 2 secrets，不需要发起付费语义审判')
    got = review.latest_review(store)
    if got is None:
        raise UpstreamMissing('先跑 `stage5.review`（0 成本），再做书级语义审判')
    seq, _ = got
    review_rel = review.REVIEW_TPL.format(n=seq)
    rev = store.read(Review, review_rel)
    drafts, judged = _current_drafts(store, book)
    if not _mechanical_is_current(rev, judged):
        raise TerminalState(
            f'{review_rel} 判的不是盘上当前正文；先重跑 `stage5.review`，不能拿旧结论审新稿')
    if store.freshness(review_rel).state != 'fresh':
        raise TerminalState(f'{review_rel} 的上游已变化；先重跑 0 成本机械验收')

    users = [(ordinal, build_claim_user(ordinal, drafts[ordinal]))
             for ordinal in sorted(book.chapters)]
    return CLAIMS_SYSTEM, users, seq, rev, book, drafts, judged


def build_alignment_prompt(chapters: list[SemanticChapterClaims],
                           secrets: list[dict], book: M.Book | None = None) -> tuple[str, str]:
    """第二相只消费已验真的 claims；原始正文不再重传，避免另起一套证据。"""
    payload = {
        'chapters': [chapter.model_dump(mode='json') for chapter in chapters],
        'secrets': [
            {'secret_id': secret.get('secret_id'), 'fact': secret.get('fact'),
             'reveal_chapter': (_reveal_chapter(book, secret) if book is not None
                                else _reveal_of(secret))}
            for secret in secrets
        ],
    }
    user = ('以下 claims 的 evidence 已通过正文逐字校验。只在这些 claims 中寻找提前泄露，'
            '不得补造命题或证据。\n\n'
            + json.dumps(payload, ensure_ascii=False, indent=2))
    return ALIGN_SYSTEM, user


def claims_validator(drafts: dict[int, dict]):
    """守第一相的章节全集、claim 唯一性与正文逐字证据。"""
    expected_chapters = set(drafts)

    def validate(resp: SemanticClaimsResponse, _wire: ChatResponse) -> str | None:
        chapter_ids = [chapter.output_ordinal for chapter in resp.chapters]
        if len(chapter_ids) != len(set(chapter_ids)) or set(chapter_ids) != expected_chapters:
            return (f'章节集合必须恰好为 {sorted(expected_chapters)}，实得 '
                    f'{sorted(chapter_ids)}')
        for chapter in resp.chapters:
            seen: set[str] = set()
            body = str(drafts[chapter.output_ordinal].get('text') or '')
            for claim in chapter.claims:
                if claim.claim_id in seen:
                    return f'第 {chapter.output_ordinal} 章 claim_id 重复：{claim.claim_id}'
                seen.add(claim.claim_id)
                if claim.evidence not in body:
                    recovered = _whitespace_equivalent_slice(claim.evidence, body)
                    if recovered is None:
                        return (f'第 {chapter.output_ordinal} 章证据不是正文连续子串：'
                                f'{claim.evidence!r}')
                    claim.evidence = recovered
        return None

    return validate


def request_claims(ctx: RunContext, client: LlmClient, system: str,
                   users: list[tuple[int, str]], drafts: dict[int, dict]
                   ) -> list[SemanticChapterClaims]:
    """逐章并发抽 claims；成功章留在缓存，一个坏响应不会推倒全书重发。"""
    if not users:
        raise TerminalState('语义审判没有可抽取的正式章')
    tracker = ChapterProgressTracker(ctx.observe, [ordinal for ordinal, _user in users])
    results: dict[int, SemanticChapterClaims] = {}
    errors: list[Exception] = []

    def one(ordinal: int, user: str) -> SemanticChapterClaims:
        response = client.ask_json(
            system, user, model_cls=SemanticClaimsResponse,
            purpose=f'{ctx.node}.claims.ch{ordinal}',
            validator=claims_validator({ordinal: drafts[ordinal]}),
            progress=tracker.request(ordinal),
        )
        return response.chapters[0]

    with ThreadPoolExecutor(max_workers=min(client.concurrency, len(users))) as pool:
        pending = {pool.submit(one, ordinal, user): ordinal for ordinal, user in users}
        for future in as_completed(pending):
            ordinal = pending[future]
            try:
                results[ordinal] = future.result()
            except Exception as exc:
                errors.append(exc)
                tracker.finish(
                    ordinal, outcome='failed',
                    detail=f'{type(exc).__name__}: {str(exc).splitlines()[0]}')
            else:
                tracker.finish(ordinal, outcome='ok')

    if errors:
        # 所有并发请求都已结算；成功章的响应已经进入缓存，重跑只补失败章。
        raise errors[0]
    return [results[ordinal] for ordinal, _user in users]


def alignment_validator(chapters: list[SemanticChapterClaims], secrets: list[dict],
                        book: M.Book | None = None):
    """守第二相的 secret 全集、引用闭合、揭示边界与不确定性单调性。"""
    expected_secrets = {str(secret.get('secret_id')):
                        (_reveal_chapter(book, secret) if book is not None else _reveal_of(secret))
                        for secret in secrets}
    claims = {(chapter.output_ordinal, claim.claim_id): claim.evidence
              for chapter in chapters for claim in chapter.claims}
    coverage = {chapter.output_ordinal: chapter.coverage for chapter in chapters}

    def validate(resp: SemanticAlignmentResponse, _wire: ChatResponse) -> str | None:
        secret_ids = [secret.secret_id for secret in resp.secrets]
        if len(secret_ids) != len(set(secret_ids)) or set(secret_ids) != set(expected_secrets):
            return (f'secret 集合必须恰好为 {sorted(expected_secrets)}，实得 '
                    f'{sorted(secret_ids)}')

        for secret in resp.secrets:
            reveal = expected_secrets[secret.secret_id]
            refs = [(ref.output_ordinal, ref.claim_id) for ref in secret.evidence_refs]
            missing_refs = [ref for ref in refs if ref not in claims]
            if missing_refs:
                return f'{secret.secret_id} 引用了不存在的 claims：{missing_refs}'
            if any(ordinal >= reveal for ordinal, _claim_id in refs):
                return f'{secret.secret_id} 的泄漏证据必须全部早于第 {reveal} 章'
            if secret.status == 'clear':
                uncertain = [ordinal for ordinal in sorted(coverage)
                             if ordinal < reveal and coverage.get(ordinal) != 'complete']
                if uncertain:
                    return (f'{secret.secret_id} 在揭示前章节 {uncertain} 覆盖不完整，'
                            '只能判 unver，不能 clear')
        return None

    return validate


def materialize_alignment(response: SemanticAlignmentResponse, secrets: list[dict],
                          book: M.Book | None = None
                          ) -> list[SemanticSecretAdjudication]:
    """把模型判断与确定性 Stage 2/4 时间线合成正式审判结果。"""
    reveal_of = {
        str(secret.get('secret_id')):
        (_reveal_chapter(book, secret) if book is not None else _reveal_of(secret))
        for secret in secrets
    }
    return [SemanticSecretAdjudication(
        secret_id=decision.secret_id,
        reveal_chapter=reveal_of[decision.secret_id],
        status=decision.status,
        evidence_refs=decision.evidence_refs,
        earliest_chapter=(min(ref.output_ordinal for ref in decision.evidence_refs)
                          if decision.status == 'leak' else None),
        reason=decision.reason,
    ) for decision in response.secrets]


def run_semantic(ctx: RunContext, client: LlmClient) -> tuple[SemanticReview, CheckReport]:
    claims_system, claim_users, mechanical_seq, rev, book, drafts, judged = build_prompts(ctx)
    chapters = request_claims(ctx, client, claims_system, claim_users, drafts)
    align_system, align_user = build_alignment_prompt(
        chapters, book.secrets, book)
    align_response = client.ask_json(
        align_system, align_user, model_cls=SemanticAlignmentResponse,
        purpose=f'{ctx.node}.alignment',
        validator=alignment_validator(chapters, book.secrets, book))
    adjudications = materialize_alignment(align_response, book.secrets, book)
    artifact = SemanticReview(
        mechanical_review_seq=mechanical_seq,
        judged=judged,
        chapters=chapters,
        secrets=adjudications,
        _provenance=ctx.llm_provenance(
            prompts={'CLAIMS_SYS': claims_system, 'ALIGN_SYS': align_system},
            model=client.model),
    )
    store = ArtifactStore(ctx)
    seq = (semantic_seqs(store)[-1] + 1) if semantic_seqs(store) else 1
    rel = SEMANTIC_TPL.format(n=seq)
    store.write(rel, artifact)
    # 局部导入避免 review ↔ semantic 的模块环。
    return artifact, review._report_of_review(
        ctx, store, rev, book, freshness_rel=rel)


def unavailable_outcomes(why: str) -> list[Outcome]:
    return [
        unverifiable('semantic_secret_leak', why, scanned='0 份当前语义审判'),
        unverifiable('semantic_secret_coverage', why, scanned='0 份当前语义审判'),
    ]


def _current_artifact(store: ArtifactStore, rev_seq: int,
                      current_judged: dict[str, JudgedDraft]
                      ) -> tuple[int, SemanticReview] | None:
    got = latest_semantic(store)
    if got is None:
        return None
    seq, artifact = got
    rel = SEMANTIC_TPL.format(n=seq)
    if artifact.mechanical_review_seq != rev_seq:
        return None
    if _judged_texts(artifact.judged) != _judged_texts(current_judged):
        return None
    if store.freshness(rel).state != 'fresh':
        return None
    return seq, artifact


def outcomes(store: ArtifactStore, book: M.Book | None) -> list[Outcome]:
    """当前 semantic artifact → 两条语义判据；缺失/过期/不确定一律 UNVER。"""
    if book is not None and not book.secrets:
        scanned = '0 条 secret（无需付费语义调用）'
        return [ran('semantic_secret_leak', scanned=scanned),
                ran('semantic_secret_coverage', scanned=scanned)]
    if book is None:
        return unavailable_outcomes('Stage 2 secrets 不可读，语义审判无 ground truth')
    mech = review.latest_review(store)
    if mech is None:
        return unavailable_outcomes('没有机械验收，无法确认语义审判对应哪一版成稿')
    rev_seq, _rev = mech
    try:
        _drafts, judged = _current_drafts(store, book)
    except TerminalState as exc:
        return unavailable_outcomes(str(exc))
    current = _current_artifact(store, rev_seq, judged)
    if current is None:
        return unavailable_outcomes(
            '没有与最新机械验收及当前正文匹配的 semantic_<n>.json；'
            '这不是通过，需运行 `stage5.semantic` 或人工审阅')
    _seq, artifact = current

    claims = {(chapter.output_ordinal, claim.claim_id): claim
              for chapter in artifact.chapters for claim in chapter.claims}
    leaks: list[Finding] = []
    for adjudication in artifact.secrets:
        if adjudication.status != 'leak':
            continue
        for ref in adjudication.evidence_refs:
            claim = claims[(ref.output_ordinal, ref.claim_id)]
            excerpt = claim.evidence
            leaks.append(finding(
                'semantic_secret_leak',
                detail=(f'第 {ref.output_ordinal} 章在合法揭示章 '
                        f'{adjudication.reveal_chapter} 前表达了 '
                        f'{adjudication.secret_id}；证据：{excerpt}'),
                evidence=[f'ch{ref.output_ordinal}:{excerpt}']))
    scanned = f'{len(artifact.chapters)} 章 × {len(artifact.secrets)} 条 secret'
    leak_outcome = ran('semantic_secret_leak', leaks, scanned=scanned)
    uncertain_chapters = [chapter.output_ordinal for chapter in artifact.chapters
                          if chapter.coverage == 'uncertain']
    uncertain_secrets = [secret.secret_id for secret in artifact.secrets
                         if secret.status == 'unver']
    if uncertain_chapters or uncertain_secrets:
        coverage = unverifiable(
            'semantic_secret_coverage',
            f'模型未能完整判定：章节 {uncertain_chapters or "无"}；'
            f'secrets {uncertain_secrets or "无"}。按契约交人工，不降成 PASS',
            scanned=scanned)
    else:
        coverage = ran('semantic_secret_coverage', scanned=scanned)
    return [leak_outcome, coverage]


def revision_targets(store: ArtifactStore, mode: str
                     ) -> tuple[dict[int, list[list[str]]], bytes | None]:
    """只回传泄漏正文证据；绝不把 secret fact/reason 泄给早期章写手。"""
    mech = review.latest_review(store)
    if mech is None:
        return {}, None
    rev_seq, rev = mech
    by_ord, _invalids, judged = M.load_drafts(store)
    try:
        _validated_formal_drafts(
            by_ord, {row.output_ordinal for row in rev.chapters})
    except TerminalState:
        # 自动修订必须与语义审判共用同一道正式稿完整性闸门；否则语义结果
        # 虽已过期或稿件集合已畸变，仍可能被回灌给写手。
        return {}, None
    current = _current_artifact(store, rev_seq, judged)
    if current is None:
        return {}, None
    seq, artifact = current
    mode_of = {row.output_ordinal: row.mode for row in rev.chapters}
    claims = {(chapter.output_ordinal, claim.claim_id): claim
              for chapter in artifact.chapters for claim in chapter.claims}
    targets: dict[int, list[list[str]]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    for secret in artifact.secrets:
        if secret.status != 'leak':
            continue
        for ref in secret.evidence_refs:
            if mode_of.get(ref.output_ordinal) != mode:
                continue
            excerpt = claims[(ref.output_ordinal, ref.claim_id)].evidence
            evidence_key = f'ch{ref.output_ordinal}:{excerpt}'
            key = (ref.output_ordinal, evidence_key)
            if key not in seen:
                targets[ref.output_ordinal].append(['semantic_secret_leak', evidence_key])
                seen.add(key)
    rel = SEMANTIC_TPL.format(n=seq)
    return dict(targets), store.path(rel).read_bytes()
