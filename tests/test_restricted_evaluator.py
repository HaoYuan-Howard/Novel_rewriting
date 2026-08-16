from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from contracts.schema.evaluation import (
    EVALUATOR_ABILITIES,
    EvidenceRef,
    GroundTruthCase,
    GroundTruthManifest,
)
from novel.config import Config
from novel.llm.budget import BudgetEstimate
from novel.llm.client import LlmFailed
from novel.llm.transport import ChatResponse, FakeTransport, TransportError
from tools.build_evaluator_profile import EvaluationRun
from tools.build_ground_truth_catalog import (
    PrivateExternalRecipe,
    PrivateSubjectLocator,
    RenderedPanel,
    RenderedSubject,
    canonical_subject_bytes,
)
from tools.restricted_evaluator import (
    AbilityPrompt,
    BlindJudgePrompt,
    EvaluatorPromptBundle,
    PartialEvaluationRun,
    RunSpec,
    execute_campaign,
    prepare_campaign,
)


def _canonical(value) -> bytes:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _budget(requests, *, node, purpose):
    assert node == 'evaluation.calibrate'
    assert purpose
    assert len(requests) == 1
    request = requests[0]
    return BudgetEstimate(
        model=request.model,
        input_tokens=len(request.system) + len(request.user),
        max_tokens=request.max_tokens,
        context_limit=10_000_000,
        max_output_tokens=100_000,
        tokenizer_revision='test-only',
    )


def _bundle(*, system_suffix: str = '') -> EvaluatorPromptBundle:
    return EvaluatorPromptBundle(
        bundle_version=1,
        bundle_id='private-rubric-v1',
        rubric_sha256='a' * 64,
        model='deepseek-v4-pro',
        reasoning_effort='high',
        thinking=True,
        max_output_tokens=300,
        abilities=[AbilityPrompt(
            ability=ability,
            judge_a=BlindJudgePrompt(
                identity='judge-a', system=f'{ability} private rubric A{system_suffix}'),
            judge_b=BlindJudgePrompt(
                identity='judge-b', system=f'{ability} private rubric B{system_suffix}'),
        ) for ability in EVALUATOR_ABILITIES],
    )


def _private_dataset(tmp_path: Path, *, case_count: int = 2):
    corpus = tmp_path / 'private-corpus'
    store = tmp_path / 'case-store'
    corpus.mkdir(parents=True)
    store.mkdir(parents=True)
    dataset_id = 'held-out-private-v1'
    dataset_token = hashlib.sha256(dataset_id.encode()).hexdigest()[:16]
    cases: list[GroundTruthCase] = []
    subject_paths: list[Path] = []
    for index in range(case_count):
        subject = RenderedSubject(
            ability='event_order',
            question='按私有冻结口径判断。',
            panels=[
                RenderedPanel(role='来源', text=f'前缀唯一证据后缀-{index}'),
                RenderedPanel(role='候选', text=f'候选内容-{index}'),
            ],
        )
        artifact = f'subjects/subject-{index}.json'
        subject_path = corpus / artifact
        subject_path.parent.mkdir(exist_ok=True)
        subject_path.write_text(
            json.dumps(subject.model_dump(mode='json'), ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        raw_sha = hashlib.sha256(subject_path.read_bytes()).hexdigest()
        subject_sha = hashlib.sha256(canonical_subject_bytes(subject)).hexdigest()
        record_id = f'case_{index + 1:024x}'
        recipe = PrivateExternalRecipe(
            recipe_version=1,
            recipe_kind='external',
            record_id=record_id,
            dataset_id=dataset_id,
            ability='event_order',
            genre='题材甲',
            family_id=f'family-{index:04d}',
            variant_id=f'v_{index:04d}',
            subject=PrivateSubjectLocator(
                artifact=artifact,
                artifact_sha256=raw_sha,
                subject_sha256=subject_sha,
            ),
        )
        (store / f'{record_id}.json').write_text(
            json.dumps(recipe.model_dump(mode='json'), ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        locator = f'private://ground-truth/{dataset_token}/{record_id}'
        cases.append(GroundTruthCase(
            case_id=f'event.case-{index:04d}',
            ability='event_order',
            genre='题材甲',
            expected='PASS' if index % 2 == 0 else 'FAIL',
            subject_sha256=subject_sha,
            evidence=[EvidenceRef(artifact=locator, subject_sha256=subject_sha)],
        ))
        subject_paths.append(subject_path)
    dataset_payload = {
        'dataset_id': dataset_id,
        'rubric_sha256': 'a' * 64,
        'cases': [case.model_dump(mode='json') for case in cases],
    }
    # The production loader validates the >=8x100 floor.  Lower-level unit tests
    # deliberately construct a tiny, otherwise hash-valid manifest so fake
    # transports do not need 4,800 synthetic responses.
    manifest = GroundTruthManifest.model_construct(
        manifest_version=1,
        dataset_id=dataset_id,
        dataset_sha256=_sha(dataset_payload),
        rubric_sha256='a' * 64,
        created_at='2026-08-16T00:00:00Z',
        annotators=2,
        adjudicator='third-human',
        agreement=0.9,
        cases=cases,
    )
    return manifest, corpus, store, subject_paths


def _runs():
    return (
        RunSpec(run_id='eval-run-a', evaluated_at='2026-08-16T01:00:00Z'),
        RunSpec(run_id='eval-run-b', evaluated_at='2026-08-16T02:00:00Z'),
        RunSpec(run_id='eval-run-c', evaluated_at='2026-08-16T03:00:00Z'),
    )


def _plan(tmp_path: Path, *, case_count: int = 2, bundle=None):
    manifest, corpus, store, subject_paths = _private_dataset(
        tmp_path, case_count=case_count)
    plan = prepare_campaign(
        manifest,
        bundle or _bundle(),
        corpus_root=corpus,
        case_store=store,
        output_dir=tmp_path / 'eval-output',
        runs=_runs(),
        raw_argv=['--manifest', 'private-manifest.json'],
        budget_checker=_budget,
    )
    return plan, subject_paths


def _config() -> Config:
    return Config(
        api_key='not-used-by-fake',
        model='ignored-and-repinned',
        max_retries=3,
        concurrency=4,
    )


def _ok_response() -> ChatResponse:
    return ChatResponse(
        content=json.dumps({
            'verdict': 'PASS',
            'evidence': [{'panel': 0, 'quote': '唯一证据'}],
            'reason': '私有口径下证据闭合',
        }, ensure_ascii=False),
        finish_reason='stop',
        model='deepseek-v4-pro',
        usage={
            'prompt_tokens': 100,
            'prompt_cache_hit_tokens': 20,
            'prompt_cache_miss_tokens': 80,
            'completion_tokens': 10,
        },
    )


def test_preview_is_exact_text_free_and_binds_every_runtime_surface(tmp_path: Path):
    plan, _ = _plan(tmp_path)
    preview = plan.preview
    assert preview.exact_calls == 2 * 2 * 3
    assert preview.wire_hard_cap == preview.exact_calls
    assert preview.declared_output_tokens == preview.exact_calls * 300
    assert preview.exact_input_tokens == sum(
        row.exact_input_tokens for row in preview.runs)
    assert preview.batch_size == 1
    assert preview.transport_retries == 0
    assert preview.fallback_rungs == 1
    assert len(preview.abilities) == 8
    assert {row.ability for row in preview.abilities} == set(EVALUATOR_ABILITIES)
    assert all(row.fingerprint.dataset_sha256 == plan.manifest.dataset_sha256
               for row in preview.abilities)
    assert '--execute' in preview.frozen_execute_argv
    assert '--yes' in preview.frozen_execute_argv
    assert not (tmp_path / 'eval-output').exists(), 'preview must not create outputs/caches'


def test_three_fake_runs_use_audited_single_rung_and_checked_evidence(tmp_path: Path):
    plan, _ = _plan(tmp_path)
    transports: dict[str, FakeTransport] = {}

    def factory(_cfg, run):
        transport = FakeTransport.always(_ok_response())
        transports[run.run_id] = transport
        return transport

    results = execute_campaign(
        plan,
        transport_factory=factory,
        config_factory=_config,
        code_identity=lambda: 'test-code',
        budget_checker=_budget,
    )
    assert len(results) == 3
    for result, run in zip(results, plan.runs):
        loaded = EvaluationRun.model_validate_json(
            plan.paths[run.run_id].result.read_text(encoding='utf-8'))
        assert loaded == result
        assert loaded.bindings == list(plan.bindings)
        assert len(loaded.predictions) == 2
        assert all(len(row.judges) == 2 for row in loaded.predictions)
        assert all(row.judges[0].judge == 'judge-a' for row in loaded.predictions)
        assert all(row.judges[1].judge == 'judge-b' for row in loaded.predictions)
        for prediction in loaded.predictions:
            for decision in prediction.judges:
                assert len(decision.evidence) == 1
                evidence = decision.evidence[0]
                assert evidence.artifact.endswith('#panels/0/text')
                assert (evidence.start, evidence.end) == (2, 6)

        calls = transports[run.run_id].calls
        assert len(calls) == 4
        assert all(call.reasoning_effort == 'high' and call.thinking for call in calls)
        assert all(call.max_tokens == 300 and call.json_mode for call in calls)
        assert all('"expected"' not in call.user for call in calls)
        assert all(not any(case.case_id in call.user for case in plan.manifest.cases)
                   for call in calls)
        assert len({call.user for call in calls}) == 4, \
            'opaque per-run/per-judge case tokens must prevent local cache reuse'

        ledger = [json.loads(line) for line in
                  plan.paths[run.run_id].ledger.read_text(encoding='utf-8').splitlines()]
        assert len(ledger) == 4
        assert all(row['node'] == 'evaluation.calibrate' for row in ledger)
        assert all(row['attempt'] == 1 and row['max_attempts'] == 1 for row in ledger)
        assert all(row['cached'] is False for row in ledger)
        assert all(row['prompt_cache_hit_tokens'] == 20 for row in ledger)
        assert all(row['prompt_cache_miss_tokens'] == 80 for row in ledger)
        assert len(list(plan.paths[run.run_id].cache.glob('*.json'))) == 4
        history = [json.loads(line) for line in
                   plan.paths[run.run_id].history.read_text(encoding='utf-8').splitlines()]
        started = next(row for row in history if row['kind'] == 'run_started')
        assert started['order_sha256']['A'] != started['order_sha256']['B']


def test_invalid_or_ambiguous_evidence_stops_once_and_preserves_partial(tmp_path: Path):
    plan, _ = _plan(tmp_path, case_count=1)
    bad = ChatResponse(
        content=json.dumps({
            'verdict': 'FAIL',
            'evidence': [{'panel': 0, 'quote': '不存在的证据'}],
            'reason': 'bad fake response',
        }, ensure_ascii=False),
        finish_reason='stop',
        usage={'prompt_tokens': 7, 'completion_tokens': 3},
        model='deepseek-v4-pro',
    )
    created: list[FakeTransport] = []

    def factory(_cfg, _run):
        transport = FakeTransport.always(bad)
        created.append(transport)
        return transport

    with pytest.raises(LlmFailed):
        execute_campaign(
            plan,
            transport_factory=factory,
            config_factory=_config,
            code_identity=lambda: 'test-code',
            budget_checker=_budget,
        )
    assert len(created) == 1
    assert len(created[0].calls) == 1, 'invalid JSON semantics must not trigger a fallback call'
    first = plan.paths[plan.runs[0].run_id]
    assert not first.result.exists()
    partial = PartialEvaluationRun.model_validate_json(
        first.partial.read_text(encoding='utf-8'))
    assert partial.completed_judges == []
    assert partial.completed_predictions == []
    assert len(first.ledger.read_text(encoding='utf-8').splitlines()) == 1
    assert not list(first.cache.glob('*.json')), 'rejected response must be evicted'
    assert not plan.paths[plan.runs[1].run_id].ledger.exists(), \
        'campaign must stop before starting the next run'

    with pytest.raises(FileExistsError):
        execute_campaign(
            plan,
            transport_factory=factory,
            config_factory=_config,
            code_identity=lambda: 'test-code',
            budget_checker=_budget,
        )
    assert len(created) == 1, 'existing partial/history must block before transport'


def test_retryable_transport_error_has_one_wire_attempt_and_no_next_run(tmp_path: Path):
    plan, _ = _plan(tmp_path, case_count=1)
    error = TransportError('synthetic 429', retryable=True, status=429)
    created: list[FakeTransport] = []

    def factory(_cfg, _run):
        transport = FakeTransport(responder=lambda _req, _index: error)
        created.append(transport)
        return transport

    with pytest.raises(TransportError):
        execute_campaign(
            plan,
            transport_factory=factory,
            config_factory=_config,
            code_identity=lambda: 'test-code',
            budget_checker=_budget,
        )
    assert len(created) == 1 and len(created[0].calls) == 1
    ledger_path = plan.paths[plan.runs[0].run_id].ledger
    row = json.loads(ledger_path.read_text(encoding='utf-8').splitlines()[0])
    assert row['outcome'] == 'error'
    assert row['max_attempts'] == 1
    assert row['will_retry'] is False
    assert row['error_kind'] == 'rate_limit'


def test_prompt_product_definition_and_source_double_hash_fail_closed(tmp_path: Path):
    with pytest.raises(ValueError, match='identities must differ'):
        AbilityPrompt(
            ability='event_order',
            judge_a=BlindJudgePrompt(identity='same', system='rubric A'),
            judge_b=BlindJudgePrompt(identity='same', system='rubric B'),
        )
    with pytest.raises(ValueError, match='system surfaces must differ'):
        AbilityPrompt(
            ability='event_order',
            judge_a=BlindJudgePrompt(identity='judge-a', system='same rubric'),
            judge_b=BlindJudgePrompt(identity='judge-b', system='same rubric'),
        )
    manifest, corpus, store, subject_paths = _private_dataset(tmp_path, case_count=1)
    wrong_rubric = _bundle().model_copy(update={'rubric_sha256': 'b' * 64})
    with pytest.raises(ValueError, match='rubric_sha256'):
        prepare_campaign(
            manifest, wrong_rubric,
            corpus_root=corpus, case_store=store,
            output_dir=tmp_path / 'out', runs=_runs(), budget_checker=_budget)

    subject_paths[0].write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='artifact hash mismatch'):
        prepare_campaign(
            manifest, _bundle(),
            corpus_root=corpus, case_store=store,
            output_dir=tmp_path / 'out', runs=_runs(), budget_checker=_budget)


def test_prompt_change_changes_binding_before_any_run(tmp_path: Path):
    first, _ = _plan(tmp_path / 'first', bundle=_bundle(system_suffix=' v1'))
    second, _ = _plan(tmp_path / 'second', bundle=_bundle(system_suffix=' v2'))
    first_map = {row.ability: row.fingerprint for row in first.bindings}
    second_map = {row.ability: row.fingerprint for row in second.bindings}
    assert first_map['event_order'].prompt_sha256 != second_map['event_order'].prompt_sha256
    assert first_map['event_order'].schema_sha256 == second_map['event_order'].schema_sha256
    assert first_map['event_order'].decoder_sha256 == second_map['event_order'].decoder_sha256
