#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preview or explicitly execute the private TODO-24 calibration campaign.

There is intentionally no built-in semantic rubric.  Execution needs a
versioned private prompt bundle covering all eight abilities and bound to the
final ground-truth manifest's rubric hash.  The default mode is a read-only
preview.  Real calls require both ``--execute`` and ``--yes``.

The paid path is deliberately narrow: batch size one, one explicit LLM rung,
transport retries zero, no fallback, three independent runs and an empty local
cache per run.  Every request still uses :class:`novel.llm.client.LlmClient`, so
the pinned tokenizer budget check, cache accounting, wire ledger and heartbeat
remain on the normal audited path.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable, Literal, Protocol, Sequence

from pydantic import Field, ValidationError, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.schema.base import StrictModel
from contracts.schema.evaluation import (
    Ability,
    EVALUATOR_ABILITIES,
    EvidenceRef,
    EvaluatorFingerprint,
    GroundTruthCase,
    GroundTruthManifest,
    JudgeDecision,
    SemanticVerdict,
)
from novel import config, prov
from novel.config import Config
from novel.llm.budget import BudgetChecker, enforce_request_budget
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, DeepSeekTransport, Transport
from tools.build_evaluator_profile import (
    CapabilityBinding,
    EvaluationPrediction,
    EvaluationRun,
)
from tools.build_ground_truth_catalog import (
    RenderedSubject,
    canonical_subject_bytes,
    load_stored_recipe,
    render_stored_recipe,
)
from tools.ground_truth_workflow import validate_manifest


RUNNER_VERSION = 1
BATCH_SIZE = 1
RUNS_REQUIRED = 3
TRANSPORT_RETRIES = 0
FALLBACK_RUNGS = 1
EVALUATION_NODE = 'evaluation.calibrate'
USER_WRAPPER_VERSION = 'ground-truth-subject-json-v1'
BLIND_TOKEN_VERSION = 'sha256-dataset-run-slot-case-v1'
ORDERING_VERSION = 'sha256-run-slot-case-v1'
EVIDENCE_DECODER_VERSION = 'panel-unique-quote-v1'
_USER_PREFIX = f'WIRE_SUBJECT_VERSION={USER_WRAPPER_VERSION}\n'
_USER_PAYLOAD_FIELDS = ('blind_token', 'ability', 'question', 'panels')
_PANEL_PAYLOAD_FIELDS = ('panel', 'role', 'text')
_RUN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{2,63}$')
_PRIVATE_LOCATOR_RE = re.compile(
    r'^private://ground-truth/[0-9a-f]{16}/case_[0-9a-f]{24}$')
_JSONL_LOCK = threading.Lock()


def _canonical(value: Any) -> bytes:
    if isinstance(value, StrictModel):
        value = value.model_dump(mode='json')
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _not_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f'{field_name} must not be blank')
    return value


class BlindJudgePrompt(StrictModel):
    identity: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{2,63}$')
    system: str = Field(min_length=1)

    @model_validator(mode='after')
    def _nonblank_system(self) -> 'BlindJudgePrompt':
        _not_blank(self.system, f'{self.identity}.system')
        return self


class AbilityPrompt(StrictModel):
    ability: Ability
    judge_a: BlindJudgePrompt
    judge_b: BlindJudgePrompt

    @model_validator(mode='after')
    def _independent_judges(self) -> 'AbilityPrompt':
        if self.judge_a.identity.casefold() == self.judge_b.identity.casefold():
            raise ValueError(f'{self.ability}: blind judge identities must differ')
        if self.judge_a.system == self.judge_b.system:
            raise ValueError(
                f'{self.ability}: blind judge system surfaces must differ')
        return self


class EvaluatorPromptBundle(StrictModel):
    """Private product definition; prompt bodies never enter a public profile."""

    bundle_version: Literal[1]
    bundle_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{2,95}$')
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    model: str = Field(min_length=1)
    reasoning_effort: Literal['low', 'medium', 'high', 'max']
    thinking: bool
    max_output_tokens: int = Field(ge=1)
    abilities: list[AbilityPrompt] = Field(min_length=8, max_length=8)

    @model_validator(mode='after')
    def _complete_product_definition(self) -> 'EvaluatorPromptBundle':
        _not_blank(self.model, 'model')
        abilities = [row.ability for row in self.abilities]
        if len(set(abilities)) != len(abilities) or set(abilities) != set(EVALUATOR_ABILITIES):
            raise ValueError(
                'private prompt bundle must define every evaluator ability exactly once')
        return self


class WireEvidenceQuote(StrictModel):
    """Untrusted model output; artifact/hash/offset are deliberately absent."""

    panel: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=4096)

    @model_validator(mode='after')
    def _quote_not_blank(self) -> 'WireEvidenceQuote':
        _not_blank(self.quote, 'evidence quote')
        return self


class EvaluatorJudgeResponse(StrictModel):
    verdict: SemanticVerdict
    evidence: list[WireEvidenceQuote]
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _wire_shape(self) -> 'EvaluatorJudgeResponse':
        _not_blank(self.reason, 'judge reason')
        if self.verdict == 'UNVER' and self.evidence:
            raise ValueError('UNVER must not claim closed evidence')
        return self


class RunSpec(StrictModel):
    run_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{2,63}$')
    evaluated_at: str = Field(min_length=1)

    @model_validator(mode='after')
    def _values_not_blank(self) -> 'RunSpec':
        _not_blank(self.evaluated_at, 'evaluated_at')
        return self


class CompletedJudge(StrictModel):
    case_id: str = Field(min_length=1)
    decision: JudgeDecision


class PartialEvaluationRun(StrictModel):
    partial_version: Literal[1]
    run_id: str = Field(min_length=1)
    evaluated_at: str = Field(min_length=1)
    bindings: list[CapabilityBinding] = Field(min_length=8, max_length=8)
    completed_judges: list[CompletedJudge]
    completed_predictions: list[EvaluationPrediction]
    error_type: str = Field(min_length=1)
    error: str = Field(min_length=1)


class AbilityPreview(StrictModel):
    ability: Ability
    cases: int = Field(ge=0)
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    declared_output_tokens: int = Field(ge=0)
    fingerprint: EvaluatorFingerprint


class RunPreview(StrictModel):
    run_id: str
    evaluated_at: str
    cases: int = Field(ge=0)
    exact_calls: int = Field(ge=0)
    exact_input_tokens: int = Field(ge=0)
    declared_output_tokens: int = Field(ge=0)
    wire_hard_cap: int = Field(ge=0)
    cache_dir: str
    ledger: str
    history: str
    result: str
    partial: str


class CampaignPreview(StrictModel):
    status: Literal['preview']
    runner_version: Literal[1]
    dataset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    prompt_bundle_id: str
    batch_size: Literal[1]
    transport_retries: Literal[0]
    fallback_rungs: Literal[1]
    runs: list[RunPreview] = Field(min_length=3, max_length=3)
    abilities: list[AbilityPreview] = Field(min_length=8, max_length=8)
    exact_calls: int = Field(ge=0)
    exact_input_tokens: int = Field(ge=0)
    declared_output_tokens: int = Field(ge=0)
    wire_hard_cap: int = Field(ge=0)
    output_declaration: dict[str, Any]
    frozen_execute_argv: list[str]


@dataclass(frozen=True)
class ResolvedCase:
    case: GroundTruthCase
    artifact: str
    subject: RenderedSubject


@dataclass(frozen=True)
class RunPaths:
    cache: Path
    ledger: Path
    history: Path
    result: Path
    partial: Path


@dataclass(frozen=True)
class CampaignPlan:
    manifest: GroundTruthManifest
    bundle: EvaluatorPromptBundle
    runs: tuple[RunSpec, RunSpec, RunSpec]
    cases: tuple[ResolvedCase, ...]
    bindings: tuple[CapabilityBinding, ...]
    paths: dict[str, RunPaths]
    preview: CampaignPreview
    corpus_root: Path
    case_store: Path
    output_dir: Path


@dataclass
class EvaluationContext:
    """The minimal audited context consumed by ``LlmClient``.

    Calibration is not a pipeline job, so it must not pretend to be a Stage 5
    or Stage 6 node and must not weaken ``RunContext``'s ``NODES`` invariant.
    """

    sid: str
    code: str
    cache: CachePolicy
    ledger: CostLedger
    observer: Callable[[dict[str, object]], None]
    node: str = field(default=EVALUATION_NODE, init=False)

    def observe(self, event: str, **fields: object) -> None:
        self.observer({
            'event': event, 'sid': self.sid, 'node': self.node, **fields,
        })

    def require_observed_roots_current(self) -> None:
        """Evaluator inputs are resolver/hash-bound, not Stage0-rooted artifacts.

        ``LlmClient`` invokes this hook before budget/cache/transport for normal
        ``RunContext`` instances.  Calibration has no pipeline root and verifies
        every private subject against the frozen resolver immediately before the
        campaign, so the equivalent hook is intentionally a no-op here.
        """
        return None


class TransportFactory(Protocol):
    def __call__(self, cfg: Config, run: RunSpec) -> Transport: ...


def _ability_prompts(bundle: EvaluatorPromptBundle) -> dict[Ability, AbilityPrompt]:
    return {row.ability: row for row in bundle.abilities}


_COMMON_SYSTEM = """You are one blind semantic evaluator. You do not know the human label.
Judge only the supplied subject under the private frozen rubric above. Return exactly one JSON
object shaped as {"verdict":"PASS|FAIL|UNVER","evidence":[{"panel":0,
"quote":"exact contiguous quote"}],"reason":"non-empty reason"}. Evidence may only use a
zero-based panel number and an exact non-empty quote copied contiguously from that panel's text.
Do not return artifact paths, hashes, offsets, markdown, or any field outside the schema. If the
evidence cannot be closed from the supplied subject, return verdict UNVER with an empty evidence
list."""


def _system_prompt(prompt: AbilityPrompt, slot: Literal['A', 'B']) -> str:
    judge = prompt.judge_a if slot == 'A' else prompt.judge_b
    return (
        f'BLIND_JUDGE_IDENTITY={judge.identity}\n'
        f'ABILITY={prompt.ability}\n'
        f'{judge.system.rstrip()}\n\n{_COMMON_SYSTEM}'
    )


def _blind_token(
    dataset_sha256: str,
    run_id: str,
    slot: Literal['A', 'B'],
    case_id: str,
) -> str:
    raw = f'{dataset_sha256}\0{run_id}\0{slot}\0{case_id}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def _user_prompt(
    resolved: ResolvedCase,
    *,
    dataset_sha256: str,
    run_id: str,
    slot: Literal['A', 'B'],
) -> str:
    # The human expected label and canonical case_id are intentionally absent.
    subject = resolved.subject.model_dump(mode='json')
    panels = [
        {'panel': index, 'role': panel['role'], 'text': panel['text']}
        for index, panel in enumerate(subject['panels'])
    ]
    payload = {
        'blind_token': _blind_token(dataset_sha256, run_id, slot, resolved.case.case_id),
        'ability': subject['ability'],
        'question': subject['question'],
        'panels': panels,
    }
    return (
        _USER_PREFIX
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )


def _schema_sha256() -> str:
    return _sha256(EvaluatorJudgeResponse.model_json_schema(mode='validation'))


def _decoder_sha256(bundle: EvaluatorPromptBundle) -> str:
    return _sha256({
        'runner_version': RUNNER_VERSION,
        'batch_size': BATCH_SIZE,
        'transport_retries': TRANSPORT_RETRIES,
        'fallback_rungs': FALLBACK_RUNGS,
        'reasoning_effort': bundle.reasoning_effort,
        'thinking': bundle.thinking,
        'json_mode': True,
        'temperature': None,
        'max_output_tokens': bundle.max_output_tokens,
        'user_wrapper_version': USER_WRAPPER_VERSION,
        'blind_token_version': BLIND_TOKEN_VERSION,
        'ordering_version': ORDERING_VERSION,
        'evidence_decoder_version': EVIDENCE_DECODER_VERSION,
    })


def build_bindings(
    manifest: GroundTruthManifest,
    bundle: EvaluatorPromptBundle,
) -> tuple[CapabilityBinding, ...]:
    schema_sha = _schema_sha256()
    decoder_sha = _decoder_sha256(bundle)
    prompts = _ability_prompts(bundle)
    rows: list[CapabilityBinding] = []
    for ability in EVALUATOR_ABILITIES:
        prompt = prompts[ability]
        prompt_sha = _sha256({
            'ability': ability,
            'judge_a': {
                'identity': prompt.judge_a.identity,
                'system': _system_prompt(prompt, 'A'),
            },
            'judge_b': {
                'identity': prompt.judge_b.identity,
                'system': _system_prompt(prompt, 'B'),
            },
            'user_prefix': _USER_PREFIX,
            'user_payload_fields': _USER_PAYLOAD_FIELDS,
            'panel_payload_fields': _PANEL_PAYLOAD_FIELDS,
            'blind_token_version': BLIND_TOKEN_VERSION,
        })
        rows.append(CapabilityBinding(
            ability=ability,
            fingerprint=EvaluatorFingerprint(
                dataset_sha256=manifest.dataset_sha256,
                model=bundle.model,
                prompt_sha256=prompt_sha,
                schema_sha256=schema_sha,
                decoder_sha256=decoder_sha,
            ),
        ))
    return tuple(rows)


def _resolver_artifact(case: GroundTruthCase) -> str:
    matches = [
        row.artifact for row in case.evidence
        if _PRIVATE_LOCATOR_RE.fullmatch(row.artifact)
        and row.subject_sha256 == case.subject_sha256
        and row.start is None and row.end is None
    ]
    if len(matches) != 1:
        raise ValueError(
            f'{case.case_id}: expected exactly one unsliced private resolver locator; '
            f'found {len(matches)}')
    return matches[0]


def resolve_cases(
    manifest: GroundTruthManifest,
    *,
    corpus_root: Path,
    case_store: Path,
) -> tuple[ResolvedCase, ...]:
    resolved: list[ResolvedCase] = []
    for case in sorted(manifest.cases, key=lambda row: row.case_id):
        artifact = _resolver_artifact(case)
        recipe = load_stored_recipe(artifact, case_store=case_store)
        if recipe.dataset_id != manifest.dataset_id:
            raise ValueError(f'{case.case_id}: resolver belongs to another dataset')
        if recipe.ability != case.ability:
            raise ValueError(f'{case.case_id}: resolver ability mismatch')
        subject = render_stored_recipe(recipe, corpus_root)
        actual = _sha256_bytes(canonical_subject_bytes(subject))
        if actual != recipe.subject_sha256 or actual != case.subject_sha256:
            raise ValueError(
                f'{case.case_id}: reconstructed subject hash mismatch; '
                f'manifest={case.subject_sha256} recipe={recipe.subject_sha256} actual={actual}')
        resolved.append(ResolvedCase(case=case, artifact=artifact, subject=subject))
    return tuple(resolved)


def _run_paths(output_dir: Path, run_id: str) -> RunPaths:
    return RunPaths(
        cache=output_dir / 'cache' / run_id,
        ledger=output_dir / f'{run_id}.ledger.jsonl',
        history=output_dir / f'{run_id}.history.jsonl',
        result=output_dir / f'{run_id}.json',
        partial=output_dir / f'{run_id}.partial.json',
    )


def _request(
    resolved: ResolvedCase,
    prompt: AbilityPrompt,
    bundle: EvaluatorPromptBundle,
    manifest: GroundTruthManifest,
    run: RunSpec,
    slot: Literal['A', 'B'],
) -> ChatRequest:
    return ChatRequest(
        model=bundle.model,
        system=_system_prompt(prompt, slot),
        user=_user_prompt(
            resolved, dataset_sha256=manifest.dataset_sha256,
            run_id=run.run_id, slot=slot),
        reasoning_effort=bundle.reasoning_effort,
        thinking=bundle.thinking,
        json_mode=True,
        max_tokens=bundle.max_output_tokens,
    )


def _parse_runs(values: Sequence[str]) -> tuple[RunSpec, RunSpec, RunSpec]:
    if len(values) != RUNS_REQUIRED:
        raise ValueError(f'exactly {RUNS_REQUIRED} --run ID=EVALUATED_AT values are required')
    rows: list[RunSpec] = []
    for value in values:
        run_id, separator, evaluated_at = value.partition('=')
        if not separator:
            raise ValueError('--run must use ID=EVALUATED_AT')
        rows.append(RunSpec(run_id=run_id, evaluated_at=evaluated_at))
    folded = [row.run_id.casefold() for row in rows]
    if len(set(folded)) != RUNS_REQUIRED:
        raise ValueError('the three run identities must be distinct (case-insensitive)')
    return rows[0], rows[1], rows[2]


def _frozen_execute_argv(raw_argv: Sequence[str]) -> list[str]:
    out = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve())]
    out.extend(value for value in raw_argv if value not in ('--execute', '--yes'))
    out.extend(['--execute', '--yes'])
    return out


def prepare_campaign(
    manifest: GroundTruthManifest,
    bundle: EvaluatorPromptBundle,
    *,
    corpus_root: Path,
    case_store: Path,
    output_dir: Path,
    runs: tuple[RunSpec, RunSpec, RunSpec],
    raw_argv: Sequence[str] = (),
    budget_checker: BudgetChecker = enforce_request_budget,
) -> CampaignPlan:
    """Resolve every case and budget every request before any paid side effect."""
    validate_manifest(manifest)
    if bundle.rubric_sha256 != manifest.rubric_sha256:
        raise ValueError(
            'private prompt bundle rubric_sha256 does not match final manifest')
    cases = resolve_cases(
        manifest, corpus_root=corpus_root, case_store=case_store)
    bindings = build_bindings(manifest, bundle)
    prompts = _ability_prompts(bundle)
    paths = {run.run_id: _run_paths(output_dir, run.run_id) for run in runs}

    blind_tokens = {
        _blind_token(manifest.dataset_sha256, run.run_id, slot, resolved.case.case_id)
        for run in runs
        for slot in ('A', 'B')
        for resolved in cases
    }
    expected_tokens = len(runs) * 2 * len(cases)
    if len(blind_tokens) != expected_tokens:
        raise ValueError('opaque blind-token collision; change run identities')

    per_run: list[RunPreview] = []
    ability_totals: dict[Ability, dict[str, int]] = {
        ability: {'cases': 0, 'calls': 0, 'input': 0, 'output': 0}
        for ability in EVALUATOR_ABILITIES
    }
    for run in runs:
        input_tokens = 0
        calls = 0
        for resolved in cases:
            for slot in ('A', 'B'):
                req = _request(
                    resolved, prompts[resolved.case.ability], bundle, manifest, run, slot)
                estimate = budget_checker(
                    (req,), node=EVALUATION_NODE,
                    purpose=f'{resolved.case.ability}/{resolved.case.case_id}/{slot}')
                input_tokens += estimate.input_tokens
                calls += 1
                totals = ability_totals[resolved.case.ability]
                totals['calls'] += 1
                totals['input'] += estimate.input_tokens
                totals['output'] += bundle.max_output_tokens
        for ability in EVALUATOR_ABILITIES:
            ability_totals[ability]['cases'] = sum(
                row.case.ability == ability for row in cases)
        run_paths = paths[run.run_id]
        per_run.append(RunPreview(
            run_id=run.run_id,
            evaluated_at=run.evaluated_at,
            cases=len(cases),
            exact_calls=calls,
            exact_input_tokens=input_tokens,
            declared_output_tokens=calls * bundle.max_output_tokens,
            wire_hard_cap=calls,
            cache_dir=str(run_paths.cache),
            ledger=str(run_paths.ledger),
            history=str(run_paths.history),
            result=str(run_paths.result),
            partial=str(run_paths.partial),
        ))

    binding_map = {row.ability: row.fingerprint for row in bindings}
    ability_preview = [AbilityPreview(
        ability=ability,
        cases=ability_totals[ability]['cases'],
        calls=ability_totals[ability]['calls'],
        input_tokens=ability_totals[ability]['input'],
        declared_output_tokens=ability_totals[ability]['output'],
        fingerprint=binding_map[ability],
    ) for ability in EVALUATOR_ABILITIES]
    preview = CampaignPreview(
        status='preview',
        runner_version=RUNNER_VERSION,
        dataset_sha256=manifest.dataset_sha256,
        prompt_bundle_id=bundle.bundle_id,
        batch_size=BATCH_SIZE,
        transport_retries=TRANSPORT_RETRIES,
        fallback_rungs=FALLBACK_RUNGS,
        runs=per_run,
        abilities=ability_preview,
        exact_calls=sum(row.exact_calls for row in per_run),
        exact_input_tokens=sum(row.exact_input_tokens for row in per_run),
        declared_output_tokens=sum(row.declared_output_tokens for row in per_run),
        wire_hard_cap=sum(row.wire_hard_cap for row in per_run),
        output_declaration={
            'response_schema_sha256': _schema_sha256(),
            'required_fields': [
                'verdict', 'evidence[].panel', 'evidence[].quote', 'reason'],
            'max_output_tokens_per_call': bundle.max_output_tokens,
            'verdicts': ['PASS', 'FAIL', 'UNVER'],
        },
        frozen_execute_argv=_frozen_execute_argv(raw_argv),
    )
    return CampaignPlan(
        manifest=manifest, bundle=bundle, runs=runs, cases=cases,
        bindings=bindings, paths=paths, preview=preview,
        corpus_root=corpus_root, case_store=case_store, output_dir=output_dir,
    )


def _case_order(
    plan: CampaignPlan,
    run: RunSpec,
    slot: Literal['A', 'B'],
) -> list[ResolvedCase]:
    rows = sorted(plan.cases, key=lambda row: hashlib.sha256(
        f'{plan.manifest.dataset_sha256}\0{run.run_id}\0{slot}\0'
        f'{row.case.case_id}'.encode('utf-8')).digest())
    if slot == 'B' and len(rows) > 1:
        order_a = [row.case.case_id for row in _case_order(plan, run, 'A')]
        if [row.case.case_id for row in rows] == order_a:
            rows.reverse()
    return rows


def _order_sha(rows: Sequence[ResolvedCase]) -> str:
    return _sha256([row.case.case_id for row in rows])


def _evidence_refs(
    response: EvaluatorJudgeResponse,
    resolved: ResolvedCase,
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    seen: set[tuple[int, int, int]] = set()
    for row in response.evidence:
        if row.panel >= len(resolved.subject.panels):
            raise ValueError(
                f'evidence panel {row.panel} outside 0..{len(resolved.subject.panels) - 1}')
        text = resolved.subject.panels[row.panel].text
        start = text.find(row.quote)
        if start < 0:
            raise ValueError('evidence quote is not a contiguous span of the declared panel')
        if text.find(row.quote, start + 1) >= 0:
            raise ValueError('evidence quote is ambiguous inside the declared panel')
        end = start + len(row.quote)
        key = (row.panel, start, end)
        if key in seen:
            raise ValueError('duplicate evidence span')
        seen.add(key)
        refs.append(EvidenceRef(
            artifact=f'{resolved.artifact}#panels/{row.panel}/text',
            subject_sha256=resolved.case.subject_sha256,
            start=start,
            end=end,
        ))
    return refs


def _decision(
    response: EvaluatorJudgeResponse,
    resolved: ResolvedCase,
    prompt: AbilityPrompt,
    slot: Literal['A', 'B'],
) -> JudgeDecision:
    identity = prompt.judge_a.identity if slot == 'A' else prompt.judge_b.identity
    return JudgeDecision(
        judge=identity,
        verdict=response.verdict,
        evidence=_evidence_refs(response, resolved),
        reason=response.reason,
    )


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
    with _JSONL_LOCK:
        with open(path, 'ab') as stream:
            stream.write(rendered.encode('utf-8'))


def _write_once(path: Path, value: StrictModel) -> None:
    if path.exists():
        raise FileExistsError(f'refusing to overwrite private evaluation output: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        value.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True,
    ) + '\n'
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(rendered, encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight_execution_paths(plan: CampaignPlan) -> None:
    all_paths: list[Path] = []
    cache_paths: list[Path] = []
    for run in plan.runs:
        paths = plan.paths[run.run_id]
        all_paths.extend((paths.ledger, paths.history, paths.result, paths.partial))
        cache_paths.append(paths.cache)
    normalized = [str(path.resolve()).casefold() for path in [*all_paths, *cache_paths]]
    if len(normalized) != len(set(normalized)):
        raise ValueError('run output/cache paths must be distinct')
    existing = [str(path) for path in all_paths if path.exists()]
    if existing:
        raise FileExistsError(
            f'refusing to overwrite prior result/partial/history/ledger: {existing}')
    for path in cache_paths:
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(f'run cache must be an independent empty directory: {path}')


def _observer(history: Path) -> Callable[[dict[str, object]], None]:
    def emit(row: dict[str, object]) -> None:
        payload = dict(row)
        payload.setdefault(
            'at', datetime.now(timezone.utc).isoformat(timespec='seconds'))
        _append_jsonl(history, {'kind': 'observation', **payload})
    return emit


def _default_transport(cfg: Config, _run: RunSpec) -> Transport:
    return DeepSeekTransport(cfg)


def _run_one(
    plan: CampaignPlan,
    run: RunSpec,
    *,
    transport_factory: TransportFactory,
    config_factory: Callable[[], Config],
    code_identity: Callable[[], str],
    budget_checker: BudgetChecker,
) -> EvaluationRun:
    paths = plan.paths[run.run_id]
    paths.cache.mkdir(parents=True, exist_ok=True)
    history = paths.history
    ctx = EvaluationContext(
        sid=run.run_id,
        code=code_identity(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=paths.ledger),
        observer=_observer(history),
    )
    loaded = config_factory()
    cfg = replace(
        loaded,
        model=plan.bundle.model,
        max_retries=TRANSPORT_RETRIES,
        concurrency=1,
    )
    client = LlmClient(
        ctx, transport_factory(cfg, run),
        cache=ResponseCache(paths.cache),
        model=plan.bundle.model,
        thinking_mode='on' if plan.bundle.thinking else 'off',
        max_retries=TRANSPORT_RETRIES,
        concurrency=1,
        budget_checker=budget_checker,
    )
    prompts = _ability_prompts(plan.bundle)
    completed: dict[str, dict[Literal['A', 'B'], JudgeDecision]] = {}

    orders = {slot: _case_order(plan, run, slot) for slot in ('A', 'B')}
    _append_jsonl(history, {
        'kind': 'run_started',
        'run_id': run.run_id,
        'dataset_sha256': plan.manifest.dataset_sha256,
        'batch_size': BATCH_SIZE,
        'transport_retries': TRANSPORT_RETRIES,
        'fallback_rungs': FALLBACK_RUNGS,
        'order_sha256': {slot: _order_sha(rows) for slot, rows in orders.items()},
    })
    try:
        for slot in ('A', 'B'):
            for resolved in orders[slot]:
                ability_prompt = prompts[resolved.case.ability]

                def validate(
                    response: EvaluatorJudgeResponse,
                    _wire_response,
                    *,
                    subject: ResolvedCase = resolved,
                    private_prompt: AbilityPrompt = ability_prompt,
                    judge_slot: Literal['A', 'B'] = slot,
                ) -> str | None:
                    try:
                        _decision(response, subject, private_prompt, judge_slot)
                    except ValueError as exc:
                        return str(exc)
                    return None

                response = client.ask_json(
                    _system_prompt(ability_prompt, slot),
                    _user_prompt(
                        resolved,
                        dataset_sha256=plan.manifest.dataset_sha256,
                        run_id=run.run_id,
                        slot=slot,
                    ),
                    model_cls=EvaluatorJudgeResponse,
                    purpose=(
                        f'calibrate/{resolved.case.ability}/'
                        f'{resolved.case.case_id}/{slot}'),
                    max_tokens=plan.bundle.max_output_tokens,
                    rungs=((plan.bundle.reasoning_effort, plan.bundle.thinking),),
                    validator=validate,
                    cache_policy=CachePolicy.default(),
                )
                decision = _decision(response, resolved, ability_prompt, slot)
                completed.setdefault(resolved.case.case_id, {})[slot] = decision
                _append_jsonl(history, {
                    'kind': 'judge_completed',
                    'run_id': run.run_id,
                    'case_id': resolved.case.case_id,
                    'slot': slot,
                    'decision': decision.model_dump(mode='json'),
                })

        predictions = [EvaluationPrediction(
            case_id=resolved.case.case_id,
            judges=[
                completed[resolved.case.case_id]['A'],
                completed[resolved.case.case_id]['B'],
            ],
        ) for resolved in sorted(plan.cases, key=lambda row: row.case.case_id)]
        result = EvaluationRun(
            run_version=1,
            run_id=run.run_id,
            evaluated_at=run.evaluated_at,
            bindings=list(plan.bindings),
            predictions=predictions,
        )
        _write_once(paths.result, result)
        _append_jsonl(history, {
            'kind': 'run_completed', 'run_id': run.run_id,
            'predictions': len(predictions),
            'calls_made': ctx.ledger.calls_made,
            'calls_cached': ctx.ledger.calls_cached,
        })
        return result
    except BaseException as exc:
        predictions = [EvaluationPrediction(
            case_id=case_id,
            judges=[slots['A'], slots['B']],
        ) for case_id, slots in sorted(completed.items()) if set(slots) == {'A', 'B'}]
        judges = [CompletedJudge(case_id=case_id, decision=decision)
                  for case_id, slots in sorted(completed.items())
                  for _slot, decision in sorted(slots.items())]
        partial = PartialEvaluationRun(
            partial_version=1,
            run_id=run.run_id,
            evaluated_at=run.evaluated_at,
            bindings=list(plan.bindings),
            completed_judges=judges,
            completed_predictions=predictions,
            error_type=type(exc).__name__,
            error=str(exc) or type(exc).__name__,
        )
        _write_once(paths.partial, partial)
        _append_jsonl(history, {
            'kind': 'run_failed', 'run_id': run.run_id,
            'error_type': type(exc).__name__, 'error': str(exc),
            'calls_made': ctx.ledger.calls_made,
            'calls_cached': ctx.ledger.calls_cached,
        })
        raise


def execute_campaign(
    plan: CampaignPlan,
    *,
    transport_factory: TransportFactory = _default_transport,
    config_factory: Callable[[], Config] = config.load_config,
    code_identity: Callable[[], str] = prov.code_version,
    budget_checker: BudgetChecker = enforce_request_budget,
) -> list[EvaluationRun]:
    """Execute exactly three runs, stopping at the first failed request."""
    # Rebuild the whole read-only plan immediately before creating a cache,
    # ledger, history, partial or result.  This closes the preview/execute gap:
    # a changed source artifact, recipe, prompt, decoder setting or token budget
    # cannot ride on the earlier preview's confirmation.
    verified = prepare_campaign(
        plan.manifest,
        plan.bundle,
        corpus_root=plan.corpus_root,
        case_store=plan.case_store,
        output_dir=plan.output_dir,
        runs=plan.runs,
        raw_argv=plan.preview.frozen_execute_argv,
        budget_checker=budget_checker,
    )
    frozen_exclude = {'frozen_execute_argv'}
    if (
        verified.cases != plan.cases
        or verified.bindings != plan.bindings
        or verified.paths != plan.paths
        or verified.preview.model_dump(exclude=frozen_exclude)
        != plan.preview.model_dump(exclude=frozen_exclude)
    ):
        raise ValueError(
            'evaluation plan drifted after preview; generate and confirm a new preview')
    plan = verified
    _preflight_execution_paths(plan)
    results: list[EvaluationRun] = []
    for run in plan.runs:
        results.append(_run_one(
            plan, run,
            transport_factory=transport_factory,
            config_factory=config_factory,
            code_identity=code_identity,
            budget_checker=budget_checker,
        ))
    return results


def _load(path: Path, model: type[StrictModel]) -> Any:
    return model.model_validate_json(path.read_text(encoding='utf-8'))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--corpus-root', required=True, type=Path)
    parser.add_argument('--case-store', required=True, type=Path)
    parser.add_argument('--prompt-bundle', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument(
        '--run', required=True, action='append',
        help='repeat exactly three times as ID=EVALUATED_AT')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--yes', action='store_true')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw_argv)
    try:
        if args.execute != args.yes:
            raise ValueError('real evaluation requires --execute and --yes together')
        manifest = _load(args.manifest, GroundTruthManifest)
        bundle = _load(args.prompt_bundle, EvaluatorPromptBundle)
        plan = prepare_campaign(
            manifest,
            bundle,
            corpus_root=args.corpus_root,
            case_store=args.case_store,
            output_dir=args.output_dir,
            runs=_parse_runs(args.run),
            raw_argv=raw_argv,
        )
        print(plan.preview.model_dump_json(indent=2))
        if not args.execute:
            return 0
        results = execute_campaign(plan)
        print(json.dumps({
            'status': 'complete',
            'runs': [row.run_id for row in results],
            'results': [str(plan.paths[row.run_id].result) for row in results],
            'wire_hard_cap': plan.preview.wire_hard_cap,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI renders every terminal failure uniformly
        print(json.dumps({
            'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
