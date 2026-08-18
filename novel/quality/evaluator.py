#!/usr/bin/env python3
"""双盲语义判官的合并器与运行时身份。

⚠⚠ **本仓没有自动放行门。** 这里曾有一整套「用标定过的 `EvaluatorProfile` 把两名判官的
一致结论升级成 automated `PASS`/`FAIL`」的装置（`wilson_lower` / `profile_matches` /
`capability_enabled` / `Confusion` / `confusion`，以及整份 `novel.quality.profile`（已删）：
`NOVEL_EVALUATOR_PROFILE` + `NOVEL_GROUND_TRUTH_MANIFEST` 两个裸 env、`require_enabled`
那道总闸）。它从未被标定过，因此那条分支一次都没有走通过。

现在合并决议在**类型上**就只能是 `UNVER`（`SemanticDecision.verdict: Literal['UNVER']`）。
留下来的是判官不是闸门：证据、逐判官 verdict 与人读理由全部保留，收口只走 hash-bound 的
`human_verification`。恢复整套装置见 git 与 `docs/schema/evaluation.md` 顶部那段。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel

from contracts.schema.evaluation import (
    Ability,
    EvaluatorFingerprint,
    EvidenceRef,
    JudgeDecision,
    SemanticDecision,
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def runtime_fingerprint(
    *,
    model: str,
    prompts: Iterable[str],
    schemas: Iterable[type[BaseModel]],
    decoder_identity: Any,
) -> EvaluatorFingerprint:
    """把这一次判官调用绑到它**确切的请求面**上。

    它回答的是「这份结论当时看的是哪一版 prompt / 响应 schema / 解码面」——产物新鲜度
    与人工复算都要它。它**不**回答「这份结论够不够格自动放行」：那个问题在本仓没有答案。
    """
    return EvaluatorFingerprint(
        model=model,
        prompt_sha256=canonical_sha256(list(prompts)),
        schema_sha256=canonical_sha256([
            schema.model_json_schema(mode='validation') for schema in schemas]),
        decoder_sha256=canonical_sha256(decoder_identity),
    )


def _closed_evidence(decision: JudgeDecision) -> bool:
    """A non-UNVER verdict must point to at least one well-formed subject span."""
    return decision.verdict == 'UNVER' or bool(decision.evidence)


def double_blind_decision(
    *,
    ability: Ability,
    subject_key: str,
    first: JudgeDecision,
    second: JudgeDecision,
) -> SemanticDecision:
    """合并两名互不可见的判官。结论恒 `UNVER`，但**理由逐句不同**。

    ⚠ 这不是一个壳。它做两件真活：
      1. 证据并集去重——「带证据的 UNVER」里「证据」那一半；
      2. 算出四选一的人读 `reason`。第四条（A/B 同判且各自证据闭合）是信息量最大的一条，
         它必须说出两名判官实际说了什么，而不是笼统一句「未校准」——那会把唯一一次
         判官一致的读数丢掉，让人以为「没有结论」。
    """
    judges = [first, second]
    evidence: list[EvidenceRef] = []
    seen: set[tuple] = set()
    for row in judges:
        for ref in row.evidence:
            key = (ref.artifact, ref.subject_sha256, ref.start, ref.end)
            if key not in seen:
                seen.add(key)
                evidence.append(ref)

    if first.verdict == 'UNVER' or second.verdict == 'UNVER':
        reason = '至少一名盲评 judge 明确判为 UNVER'
    elif first.verdict != second.verdict:
        reason = (
            f'两名盲评 judge 结论不一致（A={first.verdict}，B={second.verdict}）')
    elif not all(_closed_evidence(row) for row in judges):
        reason = 'judge 结论缺少可闭合证据'
    else:
        reason = (
            f'A/B 同判 {first.verdict} 且各自证据闭合；'
            f'本仓没有标定过的自动放行门 ⇒ 结论保持 UNVER，'
            f'交人工按 actor / reason / 完整 subject SHA-256 核验')
    return SemanticDecision(
        ability=ability, subject_key=subject_key, verdict='UNVER',
        evidence=evidence, reason=reason, judges=judges)
