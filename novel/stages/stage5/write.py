#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
write.py —— `stage5.write`：**逐章写作**。全链唯一产出「书」的层，也是唯一带 LLM 返工回路的层。

八条路：
    默认            逐章一次调用，落 `stage5/chNN.json`          **付费**
    `--revise`      把可反哺的结论连同上一轮成稿发回写手           **付费**
    `--chapters`    只写这几章                                      付费路的**参数**
    `--dry-run`     只打印将要发出去的东西                        0 成本
    `--recheck`     按当前判据重判存量成稿并写回指标                0 成本
    `--revalidate`  把合格的 `.INVALID` 提升为正式产物              0 成本
    `--apply-edit`  把人工改好的那一份落成新的一轮                  0 成本
    `--rebuild-draft` 按 canonical 逐章产物重建 `draft.md`                0 成本

⚠⚠ **本层只有闭卷这一条写作路**。源章原文只供确定性校验，
不进 prompt，也没有可以把它加回去的 mode 参数。

逐章写作走 `ask_json` + `ChapterResponse`：producer-owned slot 必须恰好各出现一次，
模型数组顺序不参与组装。机械把关仍在落盘前运行，但响应载体越界会更早在
Pydantic 边界被拒绝，不能形成正常或 `.INVALID` 章产物。

⚠⚠ **收缩臂已整臂下线，本层不再有第二条付费回路。**
这里曾有「同槽结构化候选生成（`TRIM_ROUNDS` 轮） → 确定性 claims 对账 → A/B 双盲语义裁决
→ 采用候选」一整条路，由 `require_enabled('trim_equivalence', …)` 开闸。那个闸从未在任何
宿主上打开过——本仓没有标定过的 evaluator profile，且那套装置已连同 profile 一起删除，
所以**没有任何路径能采用一个收缩候选**。超上限章现在一律零调用落 identity `TrimAudit`
（`_over_length_audit`）转 `verification`，由 `trim_resolution.retain_pretrim` 人工销账，
或由 `stage5.write --apply-edit` 改写成新的一章。

恢复需要一起恢复的东西：`contracts/schema/evaluation.py` 的 `Claim` / `TrimEvidenceSpan` /
`TrimJudgeResponse` 与 `TrimAudit` 的六个字段、`novel.quality.profile`（已删）、
`prompts.trim_system` / `trim_judge_system` / `trim_user` / `trim_judge_user`，
以及本文件的 `_trim_claims` / `_claim_regressions` / `_trim_deterministic_regressions` /
`_trim_profile_binding` / `_judge_trim_candidate`。恢复它等于重新承诺去做那场标定战役。
"""
from __future__ import annotations

from contracts.artifacts import STAGE5_BOOK_DRAFT, artifact_rel

import glob
import hashlib
import os
import re
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Any

from contracts.invariants import STOP_REWRITE_AFTER
from novel.config import MAX_CONCURRENCY
from contracts.rules import REGISTRY
from contracts.schema.common import (
    InputEdge, RoundFreeProvenance, RoundLlmProvenance,
)
from contracts.schema.report import CheckReport
from contracts.schema.evaluation import TrimAudit
from contracts.schema.stage4 import CtxChapter
from contracts.schema.stage5 import (
    Chapter, ChapterResponse, ChapterSegment, RejectedRevision,
)
from novel import paths, prov
from novel.consumption import render_stage5_draft
from novel.errors import (
    ArtifactUnreadable, GateBlocked, ProvenanceRefused, ResponseRepairRequired,
    TerminalState, UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient, LlmFailed
from novel.llm.privacy import assert_prompt_private_absent
from novel.llm.transport import ChatResponse
from novel.observability import ChapterProgressTracker, RequestProgress
from novel.report.builder import build
from novel.report.findings import unverifiable
from novel.run import RunContext
from novel.stages.stage5 import checks
from novel.stages.stage5 import measure as M
from novel.stages.stage5 import prompts, review
from novel.store import ArtifactStore

#: 一次逐章调用的上限。⚠ 它进 `_provenance.params`——换了这个数，产物里说得出来。
MAX_TOKENS = 16000


def _rel(ordinal: int) -> str:
    return artifact_rel('stage5.chapter', nn=ordinal)


def _retire_trim_state(store: ArtifactStore, ordinal: int) -> None:
    """A chapter rewrite makes both the live trim audit and its closure stale."""
    for kind in ('stage5.trim_audit', 'stage5.trim_resolution'):
        rel = artifact_rel(kind, nn=ordinal)
        if store.exists(rel):
            store.archive_round(rel)


def _rejected_rel(ordinal: int) -> str:
    return artifact_rel('stage5.rejected_revision', nn=ordinal)


def require_canonical_layout(store: ArtifactStore) -> None:
    """旧双臂盘面是显式终态；任何付费/写回入口都必须先经过这里。"""
    legacy = M.legacy_stage5_paths(store)
    if legacy:
        raise TerminalState(
            f'legacy_incompatible：发现旧 Stage5 双臂产物 {legacy[:8]}。'
            '旧 base 只作历史证据，不在原地迁移、选臂或静默触发付费重跑；'
            '请在独立 canonical base 从 Stage5 重建')


def _chapters_of(store: ArtifactStore, book: M.Book,
                 spec: str | None) -> list[dict[str, Any]]:
    """这一次要写哪几章。

    ⚠⚠ **不给 `--chapters` 时只补还没有 canonical 成稿的章**，不是全部重写。
    完成度的口径是逐章的（`R-14.2`），默认路必须与它一致——否则一次「续跑」会把已经
    付过钱的章再写一遍。
    ⚠ **`--chapters` 是定向重写的唯一载体**：给了就照给的写（哪怕那一章
    已经有稿——那是人明确要重写）。
    """
    plan = book.chapters
    if spec:
        want = list(dict.fromkeys(int(x) for x in spec.split(',') if x.strip()))
        if (missing := [w for w in want if w not in plan]):
            raise TerminalState(
                f'--chapters 指定的输出章不存在：{missing}（计划共 {len(plan)} 章）')
        return [plan[w] for w in want]
    return [plan[o] for o in sorted(plan) if not store.exists(_rel(o))]


# ─────────────────────────────────────────────────────────────────────────────
# 一、下发面 —— `--dry-run` 与真跑**共用这一个入口**
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt(ctx: RunContext, chapters: str | None = None
                 ) -> tuple[str, str, prompts.Material]:
    """这一次**要发出去的** `(system, user, 下发面)`。

    ⚠ 它与真跑共用同一个构造入口（`R-11.8`）——否则「预览里看到的」与「真发出去的」
    是两件事，而那种分家没有任何东西会报错。
    ⚠ `--dry-run` 只摊**这一次要写的第一章**——摊全部没人读得完，而形状是一样的。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    todo = _chapters_of(store, book, chapters)
    if not todo:
        raise TerminalState(
            '这一次没有要写的章：计划里的每一章都已经有 canonical 成稿了。'
            '⚠ 这**不是**「没问题」——要重写某一章就用 `--chapters N` 明说')
    entry = todo[0]
    mat = prompts.build_material(entry, book)
    system, user = prompts.write_system(), prompts.build_user(mat)
    assert_prompt_private_absent(
        CtxChapter.model_validate(entry), system=system, user=user)
    return system, user, mat


# ─────────────────────────────────────────────────────────────────────────────
# 二、⭐ 落盘 —— 归一 → 判据 → 闸门。**唯一写成稿的地方。**
# ─────────────────────────────────────────────────────────────────────────────

def _judge(text: str, entry: dict[str, Any], book: M.Book, *, mat: str,
           account: M.NormalizeAccount,
           segments: list[dict[str, str]] | None = None) -> tuple[list, list[str], list[str]]:
    """归一之后的字节 → `(findings, errs, warns)`。**落盘闸门与验收相共用同一份判据。**"""
    fs = checks.chapter_findings(text, entry, book, given=mat,
                                 quote_problems=account.quote_problems,
                                 segments=segments)
    return fs, checks.blocking(fs), checks.warnings_of(fs)


def _chapter_record(entry: dict[str, Any], segments: list[dict[str, str]], book: M.Book, *,
                    warns: list[str], errs: list[str] | None,
                    provenance: Any) -> Chapter:
    """一份成稿。**正式稿与 `.INVALID` 共用这一份章契约**——状态由文件名区分。"""
    text = _segments_text(segments)
    return Chapter(
        output_ordinal=int(entry['output_ordinal']),
        from_source_ordinals=list(entry['from_source_ordinals']),
        source_chars=int(entry['source_chars']),
        budget_chars=int(entry['budget_chars']),
        chars=M.chars_of(text),
        overlap_excl_verbatim=M.overlap_ratio(text, entry, book.source_text),
        segments=[ChapterSegment.model_validate(segment) for segment in segments],
        warns=warns,
        errs=errs,
        _provenance=provenance,
    )


def _write_chapter(ctx: RunContext, store: ArtifactStore, entry: dict[str, Any],
                   book: M.Book, *, segments: list[dict[str, str]], mat: str,
                   account: M.NormalizeAccount, provenance: Any
                   ) -> tuple[str, list[str], list]:
    """归一 → 判据 → 闸门 → 落盘。返回 `(落到哪, errs)`。

    ⚠⚠ **归一必须跑在收缩【之前】**（调用方保证），而**再归一一遍**在这里：链子是
    「归一 → 收缩(LLM) → 再归一 → 校验」，三道门与落盘看的是**同一份最终字节**。
    两遍的账**相加不覆盖**——只留后一份会让「首稿修了 9 处引号」凭空消失。

    ⚠ 有 `errs` ⇒ **不覆盖已有成稿**，问题稿存 `.INVALID`，**一章失败不中断整批**。
    """
    segments2, acc2 = _normalize_segments(segments, book)
    text2 = _segments_text(segments2)
    account = account + acc2
    for k, v in account.counters().items():
        ctx.account(k, int(ctx.accounting.get(k) or 0) + v)
    fs, errs, warns = _judge(
        text2, entry, book, mat=mat, account=account,
        segments=segments2)
    rel = _rel(entry['output_ordinal'])
    rejected_rel = _rejected_rel(int(entry['output_ordinal']))
    art = _chapter_record(entry, segments2, book, warns=warns,
                          errs=errs or None, provenance=provenance)
    with store.history_transaction(rel, rejected_rel):
        if store.exists(rejected_rel):
            store.archive_round(rejected_rel)
        if errs:
            # ⚠ `gates_skipped` 是**空列表也要写**的那一格：
            # 「一条都没跳过」与「没人说」是两件事。
            p = store.write_invalid(rel, art, gates_skipped=[])
            return str(p), errs, fs
        return str(store.write_round(rel, art)), [], fs


# ─────────────────────────────────────────────────────────────────────────────
# 三、付费路
# ─────────────────────────────────────────────────────────────────────────────

def _reject_length_capped(_text: str, response: ChatResponse) -> str | None:
    """`finish_reason=length` 是协议层明确说「答案被截断」，非空也不能当成正文。"""
    if response.finish_reason == 'length':
        return 'finish_reason=length（答案被 token 上限截断）'
    return None


def _expected_slots(entry: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(segment.get('slot') or '') for segment in (entry.get('segments') or []))


def _response_problem(
    response: ChapterResponse,
    wire: ChatResponse,
    entry: dict[str, Any],
) -> str | None:
    if wire.finish_reason == 'length':
        return 'finish_reason=length（答案被 token 上限截断）'
    expected = _expected_slots(entry)
    actual = tuple(segment.slot for segment in response.segments)
    if len(actual) != len(expected) or set(actual) != set(expected):
        return f'slot 必须恰好为 {list(expected)}，实际为 {list(actual)}'
    return None


def _ordered_segments(
    response: ChapterResponse,
    entry: dict[str, Any],
) -> list[dict[str, str]]:
    by_slot = {segment.slot: segment.text.strip() for segment in response.segments}
    return [{'slot': slot, 'text': by_slot[slot]} for slot in _expected_slots(entry)]


def _segments_text(segments: list[dict[str, str]] | list[ChapterSegment]) -> str:
    def text_of(item: dict[str, str] | ChapterSegment) -> str:
        return item.text if isinstance(item, ChapterSegment) else str(item.get('text') or '')
    return '\n\n'.join(text_of(item).strip() for item in segments).strip()


def _normalize_segments(
    segments: list[dict[str, str]],
    book: M.Book,
) -> tuple[list[dict[str, str]], M.NormalizeAccount]:
    out: list[dict[str, str]] = []
    account = M.NormalizeAccount()
    for segment in segments:
        text, one = M.normalize_chapter(
            segment['text'], quote_style=book.quote_style, sep_chars=book.sep_chars)
        if not text.strip():
            raise TerminalState(f"slot {segment['slot']} 归一后为空，拒绝拼成缺段章节")
        out.append({'slot': segment['slot'], 'text': text})
        account = account + one
    return out, account


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _over_length_audit(segments: list[dict[str, str]],
                       entry: dict[str, Any]) -> tuple[list[str], TrimAudit | None]:
    """超上限 ⇒ **零调用**落一份 identity `TrimAudit`，正文原样保留。

    ⚠ 签名里刻意没有 `client`：本层不再有第二条付费回路，这个函数在结构上就花不了钱。
    这里曾经会退回去做「只删不改」的收缩，再由校准双盲裁决要不要采用候选；那条路的
    开闸条件（`require_enabled('trim_equivalence', …)`）在本仓永远不成立，现已连同
    evaluator profile 整批删除（模块头记了删的是什么、怎么恢复）。

    ⇒ 超上限的唯一出路是**人看**，两条都 0 成本：
      · 接受当前原稿 ⇒ `stage5.review --retain-pretrim`（写 hash-bound resolution）；
      · 确需改写     ⇒ `stage5.write --apply-edit`（落成新的一轮）。

    ⚠ `length_over` 是提示级判据，压不下来**照样落盘**——所以这份 audit 才是那道门：
    它把「这一章超了，且还没有人处理过」变成 `verification` 里一条会赖着不走的待办，
    而不是一行读完就忘的提示。
    """
    text = _segments_text(segments)
    chars, hi = M.chars_of(text), M.target_range(entry)[1]
    if chars <= hi:
        return [], None
    reason = (
        f'超上限 {chars - hi} 字（{chars}/{hi}）；本仓没有自动收缩路径，'
        f'保留 pretrim，转 verification')
    return [reason], TrimAudit(
        output_ordinal=int(entry['output_ordinal']),
        before_sha256=_sha256_text(text),
        reason=reason,
    )


def ctx_purpose(entry: dict[str, Any]) -> str:
    return f"stage5.write·ch{entry.get('output_ordinal')}"


def run_write(ctx: RunContext, client: LlmClient,
              chapters: str | None = None) -> CheckReport:
    """默认付费路：逐章一次调用。**一章失败不中断整批。**

    ⚠ 连贯性靠**上一章的计划**传递，**不给上一章的成稿**——否则写手会顺着自己刚写的续，
    偏差沿章累积，章与章也变成串行依赖（`prompts.prev_context`）。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    todo = _chapters_of(store, book, chapters)
    if not todo:
        raise TerminalState(
            '这一次没有要写的章：计划里的每一章都已经有 canonical 成稿了。'
            '⚠ 这**不是**「没问题」——要重写某一章就用 `--chapters N` 明说')
    return _run_group(ctx, store, book, todo, client, revise=None)


def _generate_chapter(
    entry: dict[str, Any],
    book: M.Book,
    client: LlmClient,
    *,
    revise: dict[int, tuple[str, list[list[str]], str]] | None,
    system: str,
    progress: RequestProgress,
) -> dict[str, Any]:
    ordinal = int(entry['output_ordinal'])
    mat = prompts.build_material(entry, book)
    if revise is not None:
        prev_text, fixes, review_fp = revise[ordinal]
        user = prompts.revise_user(mat, prev_text, _fix_lines(fixes))
    else:
        prev_text, fixes, review_fp = '', [], ''
        user = prompts.build_user(mat)
    typed_entry = CtxChapter.model_validate(entry)
    assert_prompt_private_absent(typed_entry, system=system, user=user)
    accepted_purpose = ctx_purpose(entry)
    try:
        got = client.ask_json(
            system, user, model_cls=ChapterResponse,
            purpose=accepted_purpose, max_tokens=MAX_TOKENS,
            validator=lambda obj, wire: _response_problem(obj, wire, entry),
            progress=progress)
    except ResponseRepairRequired as exc:
        # 这是本章在**下一采之前**的恢复暂停，不是全批网络故障。把终态带回
        # 汇总线程，让其他已起跑/待起跑章节照常完成并落下自己的成功结果。
        return {'response_repair_required': exc}
    except LlmFailed as exc:
        return {'error': str(exc).splitlines()[0]}
    segments = _ordered_segments(got, entry)
    segments, account = _normalize_segments(segments, book)
    trim_notes, trim_audit = _over_length_audit(segments, entry)
    text = _segments_text(segments)
    if revise is not None:
        attempts = [{'chars': M.chars_of(text), 'text': text}]
        regressions = checks.trim_regressions(prev_text, text, entry, book)
        if regressions:
            accepted_purpose = f'{ctx_purpose(entry)}·退化重采样'
            try:
                got = client.ask_json(
                    system, user, model_cls=ChapterResponse,
                    purpose=accepted_purpose,
                    max_tokens=MAX_TOKENS,
                    validator=lambda obj, wire: _response_problem(obj, wire, entry),
                    progress=progress, cache_policy=CachePolicy.refresh())
            except ResponseRepairRequired as exc:
                return {'response_repair_required': exc}
            except LlmFailed as exc:
                return {
                    'rejected': {
                        'output_ordinal': ordinal,
                        'why': [*regressions,
                                '唯一一次绕缓存重采样没有拿到完整正文：'
                                + str(exc).splitlines()[0]],
                        'fixes': fixes,
                        'attempts': attempts,
                    },
                }
            retry_segments = _ordered_segments(got, entry)
            retry_segments, retry_account = _normalize_segments(retry_segments, book)
            retry_trim_notes, retry_trim_audit = _over_length_audit(
                retry_segments, entry)
            retry_text = _segments_text(retry_segments)
            attempts.append({'chars': M.chars_of(retry_text), 'text': retry_text})
            retry_regressions = checks.trim_regressions(
                prev_text, retry_text, entry, book)
            if retry_regressions:
                return {
                    'rejected': {
                        'output_ordinal': ordinal,
                        'why': retry_regressions,
                        'fixes': fixes,
                        'attempts': attempts,
                    },
                }
            segments, text, account = retry_segments, retry_text, retry_account
            trim_notes = [*trim_notes, *retry_trim_notes]
            trim_audit = retry_trim_audit
    return {
        'entry': entry, 'ordinal': ordinal, 'mat': mat, 'user': user,
        'segments': segments, 'text': text, 'account': account,
        'trim_notes': trim_notes, 'trim_audit': trim_audit,
        'prev_text': prev_text, 'fixes': fixes, 'review_fp': review_fp,
        'accepted_purpose': accepted_purpose,
    }


def _run_group_parallel(
    ctx: RunContext,
    store: ArtifactStore,
    book: M.Book,
    todo: list[dict[str, Any]],
    client: LlmClient,
    *,
    revise: dict[int, tuple[str, list[list[str]], str]] | None,
) -> CheckReport:
    system = prompts.write_system(revise=revise is not None)
    judged: dict[int, list] = {}
    written_by: dict[int, str] = {}
    blocked_by: dict[int, str] = {}
    ordinals = [int(entry['output_ordinal']) for entry in todo]
    tracker = ChapterProgressTracker(ctx.observe, ordinals)
    workers = min(
        MAX_CONCURRENCY,
        max(1, int(getattr(client, 'concurrency', MAX_CONCURRENCY))),
        len(todo),
    )
    fatal: Exception | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _generate_chapter,
                entry,
                book,
                client,
                revise=revise,
                system=system,
                progress=tracker.request(int(entry['output_ordinal'])),
            ): int(entry['output_ordinal'])
            for entry in todo
        }
        for future in as_completed(futures):
            ordinal = futures[future]
            try:
                generated = future.result()
            except CancelledError:
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章因同批网络故障在发请求前取消')
                tracker.finish(
                    ordinal, outcome='failed', detail='同批网络故障，未发请求')
                continue
            except Exception as exc:
                if fatal is None:
                    fatal = exc
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章网络级失败：{type(exc).__name__}: '
                    f'{str(exc).splitlines()[0]}')
                tracker.finish(
                    ordinal, outcome='failed', detail=str(exc).splitlines()[0])
                continue

            if error := generated.get('error'):
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章没拿到正文' if error == '没有拿到正文'
                    else f'第 {ordinal} 章没拿到完整正文：{error}')
                tracker.finish(ordinal, outcome='failed', detail=error)
                continue

            if repair_pause := generated.get('response_repair_required'):
                if fatal is None:
                    fatal = repair_pause
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章已停在下一采之前，等待受托响应修复：'
                    f'{str(repair_pause).splitlines()[0]}')
                tracker.finish(
                    ordinal, outcome='failed',
                    detail='等待受托响应修复；同批其他章节继续')
                continue

            if rejected := generated.get('rejected'):
                rel = _rejected_rel(ordinal)
                artifact = RejectedRevision.model_validate(rejected)
                where = store.write_round(rel, artifact)
                line = (
                    f'第 {ordinal} 章两次修订候选都相对旧稿退化，旧正式稿未覆盖；'
                    f'证据 → {where}（{"；".join(artifact.why)}）')
                blocked_by[ordinal] = line
                tracker.finish(ordinal, outcome='blocked', detail=artifact.why[0])
                continue

            entry = generated['entry']
            round_number = store.next_round(_rel(ordinal))
            extra: dict[str, Any] = {'round': round_number}
            if revise is not None:
                extra.update(
                    revision_of=prov.fingerprint(generated['prev_text']),
                    review=generated['review_fp'],
                    fixes=[list(item) for item in generated['fixes']],
                )
            request_fingerprint = client.accepted_request_fingerprint(
                generated['accepted_purpose'])
            if request_fingerprint is None:
                raise ProvenanceRefused(
                    f'第 {ordinal} 章已经返回正文，但 purpose='
                    f'{generated["accepted_purpose"]!r} 没有 accepted attempt；'
                    '拒绝估算或截断 request fingerprint')
            accepted_model = client.accepted_model(generated['accepted_purpose'])
            if accepted_model is None:
                raise ProvenanceRefused(
                    f'第 {ordinal} 章已经返回正文，但 purpose='
                    f'{generated["accepted_purpose"]!r} 没有 accepted model；'
                    '拒绝按主模型配置估算')
            extra['request_fingerprint'] = request_fingerprint
            prompt_record = {'SYS': system}
            provenance = ctx.llm_provenance_for_chapter(
                ordinal,
                RoundLlmProvenance,
                prompts=(
                    {**prompt_record, 'REVISE': prompts.REVISE_TAIL}
                    if revise is not None else prompt_record),
                model=client.model,
                adopted_models=[accepted_model],
                **extra,
            )
            trim_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
            trim_resolution_rel = artifact_rel('stage5.trim_resolution', nn=ordinal)
            chapter_rel = _rel(ordinal)
            # A later in-band rewrite resolves the live verification row; a
            # later trim attempt supersedes it.  In both cases retain the old
            # audit under `_rounds` instead of silently overwriting history.
            with store.history_transaction(
                    chapter_rel, trim_rel, trim_resolution_rel):
                _retire_trim_state(store, ordinal)
                if generated['trim_audit'] is not None:
                    store.write(trim_rel, generated['trim_audit'])
                where, errors, findings = _write_chapter(
                    ctx, store, entry, book,
                    segments=generated['segments'], mat=generated['mat'].given,
                    account=generated['account'], provenance=provenance)
            judged[ordinal] = findings
            trim_notes = generated['trim_notes']
            line = (
                f'第 {ordinal} 章 → {where}'
                + (f'（{len(errors)} 条阻断级）' if errors else '')
                + (f'　⚠ {"；".join(trim_notes)}' if trim_notes else '')
            )
            (blocked_by if errors else written_by)[ordinal] = line
            tracker.finish(
                ordinal,
                outcome='blocked' if errors else 'ok',
                detail=(str(errors[0]) if errors
                        else (trim_notes[0] if trim_notes else None)),
            )

    written = [written_by[key] for key in sorted(written_by)]
    blocked = [blocked_by[key] for key in sorted(blocked_by)]
    # 即使同批部分失败，只要有 canonical 章发生变化，draft 也必须
    # 是当前集合的纯投影；不能留一份「看起来像上次成功」的旧拼稿。
    assemble_book(ctx, store, book)
    if fatal is not None:
        raise fatal
    report = _report(
        ctx, store, written=written, blocked=blocked, book=book, judged=judged)
    if blocked and not written:
        raise GateBlocked(
            f'这一批 {len(todo)} 章全部没落成正式稿：' + '；'.join(blocked)
            + '。人修好对应 `.INVALID` 后跑 `stage5.write '
              '--revalidate --actor <id> --reason <why>`'
              '（0 LLM、同轮重判并留不可变审计）',
            invalid_path=str(store.path('stage5')))
    return report


def _run_group(ctx: RunContext, store: ArtifactStore, book: M.Book,
               todo: list[dict[str, Any]], client: LlmClient, *,
               revise: dict[int, tuple[str, list[list[str]], str]] | None
               ) -> CheckReport:
    return _run_group_parallel(ctx, store, book, todo, client, revise=revise)


def _fix_lines(fixes: list[list[str]]) -> list[str]:
    """`[[rule, evidence_key], …]` → 发给写手的指令行。

    ⚠⚠ **指令的唯一落点是 `Rule.directive`**：`_provenance.fixes` 只存**引用**，
    存第二份渲染后的文本就会跟着判据表漂，而**下一轮到底发了哪句话**再也说不清。
    """
    out: list[str] = []
    for rule, ev in fixes:
        r = REGISTRY.get(rule)
        out.append(f'· {r.directive}' + (f'　〔{ev}〕' if ev else ''))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 四、`--revise` —— 全链唯一的 LLM 返工回路
# ─────────────────────────────────────────────────────────────────────────────

def revise_targets(store: ArtifactStore
                   ) -> tuple[dict[int, list[list[str]]], list[str], str]:
    """最近一份验收 → 这一轮要发回写手的条目。返回 `(按章的 fixes, 跳过的理由, 报告指纹)`。

    **四道筛子**，一条都不能省：
      ① `actionable`（根因在写作层——根因不在写手身上的条目重写一百遍也修不好）；
      ② **`repair == 'UNIQUE'`**：满足这条判据的路径唯一。密度 / 比率 / 档位类一律不进
         ——满足它们的路径有无穷多条，而**最省力的那条是删内容或编内容**。
         实测代价：可反哺条目 **36 → 9（转人工 27 条，75%）**，其中一本是 10 → 0；
      ③ `unresolved_rounds < STOP_REWRITE_AFTER`（`R-8.5`）：**连续 2 轮未消 ⇒ 判「根因
         不在写手」，主动跳过**。无收敛证据的循环重写是在烧钱抽奖。

    ⚠ **跳过的两类都要报出来**（`stalled` / 非 `UNIQUE`）：「不进反哺」不等于「不管」，
    它们照常列在报告与界面上等人看。
    """
    got = review.latest_review(store)
    if got is None:
        raise TerminalState(
            '盘上一份验收报告都没有——`--revise` 要拿它挑目标。先跑 `stage5.review`（0 成本）')
    seq, rev = got
    review_bytes = store.path(review.REVIEW_TPL.format(n=seq)).read_bytes()
    picked: dict[int, list[list[str]]] = {}
    skipped: list[str] = []
    for row in rev.chapters:
        if not row.relpath:
            continue
        for f in row.findings:
            if not f.actionable:
                continue
            if REGISTRY.get(f.rule).repair != 'UNIQUE':
                skipped.append(f'第 {row.output_ordinal} 章 {f.rule}：不进付费自救'
                               f'（满足路径不唯一）⇒ 转人工')
                continue
            if f.unresolved_rounds >= STOP_REWRITE_AFTER:
                skipped.append(f'第 {row.output_ordinal} 章 {f.rule}：连续 '
                               f'{f.unresolved_rounds} 轮未消 ⇒ 判「根因不在写手」，跳过')
                continue
            picked.setdefault(row.output_ordinal, []).append([f.rule, f.evidence_key])
    # ⚠ 这里曾并入 `stage5.semantic` 的可反哺结论（`semantic.revision_targets`）：
    #   语义判据的 repair 是 `UNIQUE`，泄底证据可以只把正文片段发回写手。
    #   该节点于 2026-08-17 下线（用户裁决）⇒ 反哺面收敛成「机械验收里的
    #   `UNIQUE` 结论」这一侧。指纹随之只由验收字节决定。
    fp = prov.content_sha1(review_bytes)
    return picked, skipped, fp


def run_revise(ctx: RunContext, client: LlmClient,
               chapters: str | None = None) -> CheckReport:
    """`--revise`：把可反哺的结论连同上一轮成稿发回写手。**付费。**"""
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    # 付费反哺前必须先证明 Review 的上游、RULES、章集与
    # judged 主体都是当前的。旧规则/旧正文报告绝不能驱动一次付费重写。
    review.require_fresh_review(ctx, store)
    picked, skipped, fp = revise_targets(store)
    if (spec := chapters):
        want = {int(x) for x in spec.split(',') if x.strip()}
        picked = {o: v for o, v in picked.items() if o in want}
    if not picked:
        # **显式终态并明说「这不是『没问题』」。**
        raise TerminalState(
            '这一轮没有可自救的条目。⚠ **这不是「没问题」**：'
            + (f'有 {len(skipped)} 条被跳过——' + '；'.join(skipped[:6]) if skipped
               else '要么全部转人工，要么连续未消已到上限') +
            '\n它们照常列在验收报告与界面上等人看。')
    todo: list[dict[str, Any]] = []
    revise: dict[int, tuple[str, list[list[str]], str]] = {}
    for o, fixes in sorted(picked.items()):
        entry = book.chapters.get(o)
        if entry is None:
            continue
        prev_art, _raw = store.read_own(
            Chapter, _rel(o), require_current=True)
        todo.append(entry)
        revise[o] = (prev_art.text, fixes, fp)
    for s in skipped:
        print(f'    · 跳过：{s}', flush=True)
    return _run_group(ctx, store, book, todo, client, revise=revise)


# ─────────────────────────────────────────────────────────────────────────────
# 五、三条 0 成本路
# ─────────────────────────────────────────────────────────────────────────────

def run_recheck(ctx: RunContext, chapters: str | None = None) -> CheckReport:
    """`--recheck`：按当前判据重判存量成稿并写回指标，并重拼 `draft.md`。

    ⚠⚠ **它会改写正文字节而不动来源账**，这是一处已知的潜伏故障（盘上 0 样本）：
    它逐章重跑归一并把结果写回产物，刷新 `chars` / `overlap` / `warns`，
    **而 `_provenance` 一个字不动**。两个后果：① 一份付费产物的正文字节被一次 0 成本操作
    改掉，而来源账仍宣称是那次调用产出的；② `draft_text_sha1` 随之改变 ⇒ 下一份验收会把它
    读成「写手又落笔了」——**而 `draft_text_sha1` 的存在正是为了防这件事**。
    ⇒ 这里的处置：**改写字节时出声**（报告里逐章点名），并**用 `rewrite_provenance` 标
    `partial_rewrite`**，让「这份产物是混版的」在盘上说得出来。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    touched: list[str] = []
    judged: dict[int, list] = {}
    ordinals = ([int(entry['output_ordinal'])
                 for entry in _chapters_of(store, book, chapters)]
                if chapters else sorted(book.chapters))
    for o in ordinals:
        rel = _rel(o)
        if not store.exists(rel):
            continue
        art, _raw = store.read_own(Chapter, rel)
        entry = book.chapters[o]
        segments, acc = _normalize_segments(
            [segment.model_dump() for segment in art.segments], book)
        text = _segments_text(segments)
        mat = prompts.build_material(entry, book).given
        fs, errs, warns = _judge(
            text, entry, book, mat=mat, account=acc,
            segments=segments)
        judged[o] = fs
        for k, v in acc.counters().items():
            ctx.account(k, int(ctx.accounting.get(k) or 0) + v)
        changed = text != art.text
        if changed:
            touched.append(f'第 {o} 章正文字节被归一改写了（{M.chars_of(art.text)} → '
                           f'{M.chars_of(text)} 字）——**这是一次 0 成本操作改付费产物**')
        new_prov = ctx.rewrite_provenance(
            art.provenance, by='--recheck') if changed else art.provenance
        updated = _chapter_record(
            entry, segments, book, warns=warns,
            errs=errs or None, provenance=new_prov)
        if changed:
            trim_audit_rel = artifact_rel('stage5.trim_audit', nn=o)
            trim_resolution_rel = artifact_rel('stage5.trim_resolution', nn=o)
            with store.history_transaction(rel, trim_audit_rel, trim_resolution_rel):
                _retire_trim_state(store, o)
                store.write(rel, updated)
        else:
            store.write(rel, updated)
    assemble_book(ctx, store, book)
    return _report(ctx, store, written=[], blocked=touched, book=book, judged=judged)


def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str, chapters: str | None = None,
) -> CheckReport:
    """`--revalidate`：按**当前**判据重判被闸门拦下的 `.INVALID`，合格即提升为正式产物。

    ⚠⚠ **来源账继承，一个字不改**——`store.promote_invalid` 的签名里**没有**
    `prompts` / `model` / `inputs`，**没有参数就没有可以传错的东西**。
    原型这条路**伪造**来源账：它盖上当前 SYS 指纹 + 当前 model + 当前 inputs、`round` 还 +1，
    而**没有任何一次新的落笔发生过**。三件事同时错：记了一个没产出它的 prompt、
    记了一个没写它的模型、把「重判提升」记成了「第 N 轮写作」。
    ⚠ **刻意不宣称 `origin=human`**：「判据改了」与「人手改过」在节点这一侧分不开。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    promoted: list[str] = []
    still: list[str] = []
    judged: dict[int, list] = {}
    ordinals = ([int(entry['output_ordinal'])
                 for entry in _chapters_of(store, book, chapters)]
                if chapters else sorted(book.chapters))
    for o in ordinals:
        rel = _rel(o)
        inv = paths.invalid_of(rel)
        if not store.exists(inv):
            continue
        art, _raw = store.read_own(Chapter, inv)
        entry = book.chapters[o]
        mat = prompts.build_material(entry, book).given
        segments, account = _normalize_segments(
            [segment.model_dump() for segment in art.segments], book)
        text = _segments_text(segments)
        fs, errs, warns = _judge(
            text, entry, book, mat=mat,
            account=account, segments=segments)
        judged[o] = fs
        if errs:
            still.append(f'第 {o} 章仍有 {len(errs)} 条阻断级：{errs[0]}')
            continue
        rebuilt = _chapter_record(
            entry, segments, book, warns=warns,
            errs=None, provenance=art.provenance)
        trim_audit_rel = artifact_rel('stage5.trim_audit', nn=o)
        trim_resolution_rel = artifact_rel('stage5.trim_resolution', nn=o)
        with store.history_transaction(rel, trim_audit_rel, trim_resolution_rel):
            store.promote_invalid(
                rel, Chapter, actor=actor, reason=reason,
                artifact_override=rebuilt)
            _retire_trim_state(store, o)
        promoted.append(f'第 {o} 章 → {store.path(rel)}')
    if not promoted and not still:
        raise TerminalState('盘上一份 `.INVALID` 都没有——没有可提升的稿')
    assemble_book(ctx, store, book)
    return _report(ctx, store, written=promoted, blocked=still, book=book,
                   judged=judged)


def run_apply_edit(ctx: RunContext, edit_path: str) -> CheckReport:
    """`--apply-edit`：把人工改好的那一份落成新的一轮。

    ⚠⚠ **这是「怎么记来源账」的教科书，连注释一起搬**：
      · **继承上一轮的 `inputs`**（改的是同一份上游下的稿）；
      · **`pop` 掉 `prompts` 与 `model`**——这一稿不是任何 prompt / 模型产的，
        记了就是**假账**（`RoundFreeProvenance` 在结构上没有那两格）；
      · 记 `origin=human`；
      · 上一轮 `inputs` 为空时**记下这次真实读过的那份并打上 `input_basis`**，
        明说它不是对历史的追认——**不知道不能记成匹配**。

    ⚠ **人工稿仍有 `errs` 时只报不拦**（用户裁决：人手动改往往正是为了机器修不动的地方）。
    判定落进 `errs`/`warns`，界面照常红着。
    ⚠⚠ 而**下游没有第二道**：stage7 只排除 `.INVALID.json`，**不看正式稿里的 `errs`**
    ⇒ 一份带阻断级判定的人工稿会一路进成书，没有一行报告会说。
    这是裁决的后果不是 bug——**但要写进契约**（`stage5.md` §七）。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    p = os.fspath(edit_path)
    if not p or not os.path.isfile(p):
        raise TerminalState(f'--apply-edit 要一份人工稿的路径（这一次是 {edit_path!r}）')
    raw_edit = open(p, encoding='utf-8').read().strip()
    o = _ordinal_of_edit(p, raw_edit)
    entry = book.chapters.get(o)
    if entry is None:
        raise TerminalState(f'人工稿说自己是第 {o} 章，而计划里没有这一章')
    rel = _rel(o)
    prev_inputs: dict[str, Any] = {}
    if store.exists(rel):
        _prev, raw = store.read_own(Chapter, rel)
        prev_inputs = ((raw or {}).get('_provenance') or {}).get('inputs') or {}
    try:
        edited = ChapterResponse.model_validate_json(raw_edit)
    except Exception as exc:
        raise TerminalState(
            '--apply-edit 的 schema v2 文件必须是 {"segments":[{"slot":"s01",'
            '"text":"..."},...]}，不再接受无法恢复 segment 边界的整章纯文本'
        ) from exc
    problem = _response_problem(
        edited,
        ChatResponse(content='', finish_reason='stop'),
        entry,
    )
    if problem:
        raise TerminalState(f'人工稿 segment 契约不成立：{problem}')
    segments, acc = _normalize_segments(_ordered_segments(edited, entry), book)
    text = _segments_text(segments)
    rnd = store.next_round(rel)
    extra: dict[str, Any] = {'round': rnd, 'origin': 'human'}
    if not prev_inputs:
        # **「这份 inputs 是本次重判时读的，不是对旧问题稿历史来源的追认」**——
        # 删了这一格，两种来源在盘上同形，而它记的是一件**不可复算**的事。
        extra['input_basis'] = 'apply_edit_current_validation'
    provenance = ctx.free_provenance(RoundFreeProvenance, **extra)
    if prev_inputs:
        # ⚠ **转成 `InputEdge` 再放回去**：塞裸 dict 会让 pydantic 在序列化时降级
        #   （`PydanticSerializationUnexpectedValue`），而那条警告只在 stderr 上出现一次，
        #   下一次就没人看见了——真正的风险是它有一天开始**静默丢字段**。
        provenance = provenance.model_copy(update={
            'inputs': {k: InputEdge.model_validate(v) for k, v in prev_inputs.items()}})
    mat = prompts.build_material(entry, book).given
    fs, errs, warns = _judge(
        text, entry, book, mat=mat, account=acc,
        segments=segments)
    art = _chapter_record(entry, segments, book, warns=warns, errs=errs, # 空也写
                          provenance=provenance)
    trim_audit_rel = artifact_rel('stage5.trim_audit', nn=o)
    trim_resolution_rel = artifact_rel('stage5.trim_resolution', nn=o)
    with store.history_transaction(rel, trim_audit_rel, trim_resolution_rel):
        if store.exists(rel):
            _retire_trim_state(store, o)
        where = store.write_round(rel, art)
    note = ([f'⚠ 人工稿仍有 {len(errs)} 条阻断级判定，**只报不拦**：{errs[0]}'
             f'　⚠⚠ 下游没有第二道——stage7 只排除 `.INVALID`，这一份会一路进成书']
            if errs else [])
    assemble_book(ctx, store, book)
    return _report(ctx, store, written=[f'第 {o} 章 → {where}'], blocked=note,
                   book=book, judged={o: fs})


_EDIT_ORDINAL = re.compile(r'ch(\d+)')


def _ordinal_of_edit(path: str, text: str) -> int:
    """人工稿是第几章。**从文件名认**（`chNN...`），认不出**当场炸**。

    ⚠ 不从正文猜：猜错会把一章的人工修改**落到另一章头上**，而两边都不出声。
    """
    m = _EDIT_ORDINAL.search(os.path.basename(path))
    if m is None:
        raise TerminalState(
            f'从文件名认不出这是第几章：{os.path.basename(path)!r}。'
            f'⚠ 不从正文猜——猜错会把一章的人工修改落到另一章头上，而两边都不出声。'
            f'把结构化人工稿命名成 `chNN.json`')
    del text
    return int(m.group(1))


# ─────────────────────────────────────────────────────────────────────────────
# 六、`draft.md` —— canonical 章集合的纯投影
# ─────────────────────────────────────────────────────────────────────────────

def render_book_draft(book: M.Book, by_ord: dict[int, dict[str, Any]]) -> str:
    """Pure canonical chapter projection.  This is the sole byte ruler for `draft.md`."""
    return render_stage5_draft(book.chapters, by_ord)


def expected_book_draft(store: ArtifactStore, book: M.Book) -> str:
    """从当前 canonical 章集合纯重算 `draft.md` 应有的字节。"""
    # draft 是 formal 章字节的恢复投影，不是 freshness 判官。上游刚变、只定向
    # 重写了部分章时，旧章仍须如实进入投影；fresh/stale 由统一 review_status 判断。
    by_ord, _invalids, _judged = M.load_drafts(store, require_current=False)
    return render_book_draft(book, by_ord)


def draft_is_current(store: ArtifactStore, book: M.Book) -> bool:
    """True only when on-disk draft bytes equal the canonical pure projection."""
    if not store.exists(STAGE5_BOOK_DRAFT):
        return False
    try:
        current = store.read_text(STAGE5_BOOK_DRAFT)
        expected = expected_book_draft(store, book)
    except (ArtifactUnreadable, UpstreamMissing):
        return False
    # `read_text` 按原始 UTF-8 字节解码且拒绝 CRLF，因而字符相等就是
    # 本契约下的字节相等；不在这里另写一条文件读取口。
    return current == expected


def assemble_book(ctx: RunContext, store: ArtifactStore, book: M.Book) -> str:
    """原子写回 canonical 章集合的纯投影；缺章横幅仍显式保留。"""
    store.write_text(STAGE5_BOOK_DRAFT, expected_book_draft(store, book))
    del ctx
    return STAGE5_BOOK_DRAFT


def run_rebuild_draft(ctx: RunContext) -> str:
    """`--rebuild-draft`：零成本恢复 canonical 投影，不改任何章产物。"""
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    return assemble_book(ctx, store, book)


# ─────────────────────────────────────────────────────────────────────────────
# 七、报告
# ─────────────────────────────────────────────────────────────────────────────

def _report(ctx: RunContext, store: ArtifactStore, *,
            written: list[str], blocked: list[str], book: M.Book,
            judged: dict[int, list] | None = None) -> CheckReport:
    """写作相的报告：**这一次碰过的那几章**逐条报出判据。

    ⚠⚠ **书级/跨章那 14 条落 `unver` 而不是 `skipped`**（`checks.check(phase='write')`）。
    两者是两种颜色：`skipped` 的措辞是「实现未被调用」，用在这里是**一句假话**——
    它们的实现在，只是这一相判不了。而一份 49 行全 `skipped` 的报告徽章是 `fail`，
    读起来像「全查过全没过」，实际上什么都没说。**要看全书 49 行，跑 `stage5.review`。**

    ⚠ 这一次一章都没碰（比如 `--revalidate` 一份 `.INVALID` 都没有）⇒ 逐章那几条
    也落 `unver`：「这一次没有任何一章被判过」与「判过、干净」必须分得开。
    """
    for line in (*written, *blocked):
        print(f'    · {line}', flush=True)
    rel = 'stage5'
    by_rule: dict[str, list] = {}
    n = len(judged or {})
    for fs in (judged or {}).values():
        for f in fs:
            by_rule.setdefault(f.rule, []).append(f)
    empty = '这一次一章都没碰过——**没有任何一章被判过**，不是判过没问题'
    scanned = {r: f'{n} 章（这一次碰过的）' for r in checks._FROM_CHAPTER}
    outcomes = checks.check(
        chapter_findings_by_rule=by_rule,
        scanned=scanned, blind={},
        unver_empty={}, phase='write') if n else [
        unverifiable(r, empty, scanned='0 章')
        for r in (*checks._FROM_CHAPTER, *checks._FROM_REVIEW)]
    # ⚠ 这里曾追加 `stage5.semantic` 的两条 UNVER 占位（写作相判不了书级语义）。
    #   该节点于 2026-08-17 下线，两条判据一并注销 ⇒ 不再追加占位行。
    any_rel = next(iter(sorted(
        p for p in glob.glob(os.path.join(store.path(rel), 'ch*.json'))
        if re.fullmatch(r'ch\d+\.json', os.path.basename(p)))), None)
    fresh_rel = (f'{rel}/{os.path.basename(any_rel)}' if any_rel
                 else review.WRITER_CONTEXT)
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(fresh_rel),
                 outcomes=outcomes, accounting=ctx.accounting,
                  invalid=bool(glob.glob(os.path.join(store.path(rel), 'ch*.INVALID.json'))))


def run_check_only(ctx: RunContext) -> CheckReport:
    """写作节点没有 `--check-only`（判据表由 `stage5.review` 报）。**说出来，不静默。**"""
    raise UpstreamMissing(
        '`stage5.write` 不接 `--check-only`——判据表由 `stage5.review` 报（两相共用一张表）')
