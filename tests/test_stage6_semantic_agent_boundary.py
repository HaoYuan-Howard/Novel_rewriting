from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from contracts.schema.stage5 import DraftConsumed, JudgedDraft
from contracts.schema.stage6 import HookSemanticResponse
from novel.errors import ProvenanceRefused
from novel.llm import stage6_runs
from novel.stages.stage6 import semantic


def _judged(ordinal: int, text_sha1: str) -> JudgedDraft:
    return JudgedDraft(
        output_ordinal=ordinal,
        round=1,
        sha1=str(ordinal) * 10,
        consumed=DraftConsumed(
            output_ordinal=ordinal,
            text_sha1=text_sha1,
        ),
    )


def _response() -> HookSemanticResponse:
    return HookSemanticResponse.model_validate({
        'reviews': [{
            'label': 'T1',
            'coverage': 'complete',
            'uncertainty_reason': None,
            'contradictions': [],
            'quality': {
                'read_on_desire': 8,
                'body_voice_match': 8,
                'concrete_scene_and_specificity': 8,
                'curiosity_and_payoff_promise': 8,
                'hook_craft': 8,
                'synopsis_penalty': 1,
            },
            'concise_reason': '具体场面与最后一击都成立。',
        }],
        'ranking': ['T1'],
    })


def _prepared() -> semantic.SemanticCandidateContext:
    return semantic.SemanticCandidateContext(
        system='system',
        user='shared-user',
        judge_users=(('A', 'user-A'), ('B', 'user-B')),
        art=SimpleNamespace(),
        up=SimpleNamespace(),
        aliases=(('T1', 'c1', '候选正文'),),
        judged={},
        input_sha256='a' * 64,
        hook_file_sha1='b' * 10,
    )


def test_prompt_judged_only_binds_drafts_actually_sent_to_judges():
    up = SimpleNamespace(
        upto=2,
        mat=SimpleNamespace(lead=[{
            'output_ordinal': 1,
            'text': '第一章',
        }]),
        judged={
            'stage5/ch01.json': _judged(1, 'a' * 10),
            # 在 upto 内但正文缺席，不能冒充已经下发。
            'stage5/ch02.json': _judged(2, 'b' * 10),
            # canonical loader 会看见后半书，但 judge 没有看见。
            'stage5/ch03.json': _judged(3, 'c' * 10),
        },
    )

    assert semantic._prompt_judged(up) == {
        'stage5/ch01.json': up.judged['stage5/ch01.json'],
    }


def test_build_prompt_records_hook_as_a_formal_input(monkeypatch):
    art = SimpleNamespace(
        candidates=[SimpleNamespace(text='候选正文')],
        approved=None,
    )
    calls: list[tuple[str, str]] = []

    class Store:
        def exists(self, _rel):
            return True

        def freshness(self, _rel):
            return SimpleNamespace(state='fresh')

        def read(self, _model, rel):
            calls.append(('read', rel))
            return art

        def read_own(self, *_args, **_kwargs):
            raise AssertionError('producer 不能把 hook 当成本节点自己的产物读取')

    upstream = SimpleNamespace(
        src_hook='原作文案',
        mat=SimpleNamespace(
            lead=[{'output_ordinal': 1, 'text': '第一章'}],
            story_truth={'prepare': {'facts': [], 'opening': {'kind': 'proposed', 'through_chapter': 1}}},
            story_map=[],
        ),
    )
    store = Store()
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(semantic.hook, '_Upstreams', lambda _ctx, _store: upstream)

    semantic.build_prompt(SimpleNamespace())

    assert calls == [('read', semantic.hook.REL)]


def test_prepare_freezes_both_exact_judge_requests_before_wire(monkeypatch):
    prepared = _prepared()
    captured = {}
    ctx = SimpleNamespace(
        node='stage6.semantic',
        executor_profile=SimpleNamespace(provider='codex'),
    )
    monkeypatch.setattr(semantic, '_context_from_inputs', lambda _ctx: prepared)
    monkeypatch.setattr(semantic, '_assert_prepared_memory', lambda _prepared: None)
    monkeypatch.setattr(
        stage6_runs,
        'ensure_run',
        lambda _ctx, **kwargs: captured.update(kwargs),
    )

    assert semantic.prepare_semantic_candidate(ctx) is prepared
    assert captured == {
        'input_sha256': prepared.input_sha256,
        'system': prepared.system,
        'users': {'A': 'user-A', 'B': 'user-B'},
        'response_model': HookSemanticResponse,
        'max_tokens': semantic.SEMANTIC_MAX_TOKENS,
        'experiment_arm': {
            'session_policy': 'independent-parallel',
            'judge_stagger_ms': 0,
        },
    }


class _ConcurrentSource:
    model = 'actual-model'

    def __init__(self, events: list[str], barrier: threading.Barrier) -> None:
        self.events = events
        self.barrier = barrier

    def ask_json(self, _system, _user, **kwargs):
        purpose = kwargs['purpose']
        self.events.append(f'ask-enter:{purpose}')
        self.barrier.wait(timeout=10)
        self.events.append(f'ask-exit:{purpose}')
        return _response()

    def accepted_checkpoint(self, purpose):
        return {'purpose': purpose}

    def restore_checkpoint_acceptance(self, purpose, _record, **_kwargs):
        self.events.append(f'acceptance:{purpose}')

    def accepted_attempt(self, _purpose):
        return None

    def accepted_models(self, *_purposes):
        return (self.model,)


def test_agent_preflights_both_checkpoints_then_requests_missing_roles_concurrently(
        monkeypatch,
):
    prepared = _prepared()
    ctx = SimpleNamespace(
        node='stage6.semantic',
        executor_profile=SimpleNamespace(provider='codex'),
    )
    events: list[str] = []
    source = _ConcurrentSource(events, threading.Barrier(2))
    monkeypatch.setattr(
        semantic,
        '_assert_prepared_current',
        lambda _ctx, _prepared: events.append('current'),
    )

    def restore(_ctx, *, role, **_kwargs):
        events.append(f'restore:{role}')
        return None

    def write(_ctx, *, role, acceptance, **_kwargs):
        events.append(f'checkpoint:{role}:{acceptance["purpose"]}')

    monkeypatch.setattr(stage6_runs, 'restore_checkpoint', restore)
    monkeypatch.setattr(stage6_runs, 'write_checkpoint', write)

    got = semantic.request_semantic_candidates(ctx, source, prepared)

    assert set(got) == {'A', 'B'}
    first_ask = min(i for i, event in enumerate(events) if event.startswith('ask-enter:'))
    assert events.index('restore:A') < first_ask
    assert events.index('restore:B') < first_ask
    assert {'checkpoint:A:stage6.semantic.A',
            'checkpoint:B:stage6.semantic.B'} <= set(events)


def test_agent_checkpoint_corruption_stops_before_either_wire(monkeypatch):
    prepared = _prepared()
    ctx = SimpleNamespace(
        node='stage6.semantic',
        executor_profile=SimpleNamespace(provider='claude'),
    )
    calls: list[str] = []
    source = _ConcurrentSource(calls, threading.Barrier(2))
    monkeypatch.setattr(
        semantic, '_assert_prepared_current', lambda _ctx, _prepared: None)

    def restore(_ctx, *, role, **_kwargs):
        calls.append(f'restore:{role}')
        if role == 'B':
            raise ProvenanceRefused('checkpoint B 损坏')
        return None

    monkeypatch.setattr(stage6_runs, 'restore_checkpoint', restore)

    with pytest.raises(ProvenanceRefused, match='checkpoint B'):
        semantic.request_semantic_candidates(ctx, source, prepared)

    assert calls == ['restore:A', 'restore:B']


def test_legacy_client_submits_independent_a_b_concurrently(monkeypatch):
    prepared = _prepared()
    ctx = SimpleNamespace(node='stage6.semantic', executor_profile=None)
    calls: list[str] = []
    barrier = threading.Barrier(2)

    class LegacySource:
        model = 'legacy-model'

        def ask_json(self, _system, _user, **kwargs):
            calls.append(kwargs['purpose'])
            assert 'recovery_policy' not in kwargs
            assert kwargs['max_tokens'] == semantic.SEMANTIC_MAX_TOKENS
            barrier.wait(timeout=10)
            return _response()

    monkeypatch.setattr(
        semantic, '_assert_prepared_current', lambda _ctx, _prepared: None)

    got = semantic.request_semantic_candidates(ctx, LegacySource(), prepared)

    assert set(got) == {'A', 'B'}
    assert set(calls) == {'stage6.semantic.A', 'stage6.semantic.B'}


def test_evaluate_is_provisional_and_never_constructs_a_store(monkeypatch):
    prepared = _prepared()
    ctx = SimpleNamespace(node='stage6.semantic')
    source = SimpleNamespace(model='requested-model')
    response = _response()
    candidate = SimpleNamespace(
        model_dump=lambda **_kwargs: {'typed': 'candidate'},
    )
    settles: list[bool] = []
    monkeypatch.setattr(
        semantic, '_assert_prepared_current', lambda _ctx, _prepared: None)
    monkeypatch.setattr(
        semantic, '_validated_responses', lambda _prepared, rows: dict(rows))
    monkeypatch.setattr(
        semantic,
        '_accepted_attempts',
        lambda _ctx, _source: (('A', 'attempt-a'), ('B', 'attempt-b')),
    )
    monkeypatch.setattr(
        semantic, '_adopted_models', lambda _ctx, _source: ('actual-model',))
    monkeypatch.setattr(
        semantic,
        '_materialize_semantic_candidate',
        lambda _ctx, _source, _prepared, _responses, *, settle:
            settles.append(settle) or candidate,
    )
    monkeypatch.setattr(
        semantic,
        'ArtifactStore',
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError('evaluate 不得接触正式存储')),
    )

    evaluation = semantic.evaluate_semantic_candidate(
        ctx, source, prepared, {'A': response, 'B': response})

    assert evaluation.candidate is candidate
    assert evaluation.adopted_models == ('actual-model',)
    assert settles == [False]


def test_finalize_keeps_write_readback_adoptions_and_commit_under_one_lock(
        monkeypatch,
):
    state = {'locked': False}
    events: list[str] = []
    payload = {
        'judged': {},
        'judged_texts': {'c1': 'a' * 10},
        'hook_file_sha1': 'b' * 10,
        '_provenance': {'inputs': {}},
    }

    class Artifact:
        def __init__(self, name: str) -> None:
            self.name = name

        def model_dump(self, **_kwargs):
            return dict(payload)

    provisional = Artifact('provisional')
    artifact = Artifact('artifact')
    landed = Artifact('landed')
    prepared = _prepared()
    evaluation = semantic.SemanticCandidateEvaluation(
        candidate=provisional,
        requested_model='model',
        adopted_models=('actual-model',),
        accepted_attempts=(('A', 'attempt-a'), ('B', 'attempt-b')),
        input_sha256=prepared.input_sha256,
        _responses=(),
        _candidate_sha256=semantic._canonical_sha256(payload),
    )
    ctx = SimpleNamespace(
        node='stage6.semantic',
        novel_dir=SimpleNamespace(),
        executor_profile=SimpleNamespace(provider='codex'),
    )
    source = SimpleNamespace(model='model')

    @contextmanager
    def ownership(_novel_dir):
        assert not state['locked']
        state['locked'] = True
        events.append('lock-enter')
        try:
            yield
        finally:
            events.append('lock-exit')
            state['locked'] = False

    class Store:
        def exists(self, _rel):
            assert state['locked']
            return False

        def write(self, rel, value):
            assert state['locked'] and value is artifact
            events.append(f'write:{rel}')

    monkeypatch.setattr(semantic, '_assert_evaluation_identity', lambda *_args: None)
    monkeypatch.setattr(
        semantic,
        '_assert_prepared_current',
        lambda *_args: events.append(
            'current-locked' if state['locked'] else 'current-before-lock'),
    )
    monkeypatch.setattr(
        semantic,
        '_materialize_semantic_candidate',
        lambda *_args, **_kwargs: artifact,
    )
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(
        semantic,
        'semantic_seqs',
        lambda _store: events.append('seq') or [],
    )
    monkeypatch.setattr(
        semantic,
        '_semantic_finalization_payload',
        lambda *, rel, seq, prepared, artifact: {
            'rel': rel, 'seq': seq, 'from': artifact.name,
        },
    )
    monkeypatch.setattr(
        semantic,
        'verify_semantic_delivery',
        lambda *_args, **_kwargs: (
            events.append('readback') or landed,
            SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(
        semantic,
        '_note_semantic_adoptions',
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(AssertionError('adoption 已跑出 ownership'))
            if not state['locked'] else events.append('adoptions')),
    )
    monkeypatch.setattr(stage6_runs, 'commit_ownership', ownership)
    monkeypatch.setattr(stage6_runs, 'committed', lambda _ctx: None)
    monkeypatch.setattr(stage6_runs, 'prepared', lambda _ctx: None)
    monkeypatch.setattr(
        stage6_runs,
        'refuse_foreign_commit_reservations',
        lambda _ctx, rel: (
            (_ for _ in ()).throw(AssertionError('reservation guard 已跑出 ownership'))
            if not state['locked'] else events.append(f'reservation:{rel}')),
    )
    monkeypatch.setattr(
        stage6_runs,
        'prepare_commit',
        lambda _ctx, **_kwargs: {
            'artifact': 'stage6/semantic_1.json',
        },
    )
    monkeypatch.setattr(stage6_runs, 'verify_commit_artifact', lambda *_args: True)
    monkeypatch.setattr(stage6_runs, 'verify_commit_finalization', lambda *_args: True)
    monkeypatch.setattr(
        stage6_runs,
        'restore_commit_adoptions',
        lambda _ctx, _record: (
            ('stage6.semantic.A', 'attempt-a'),
            ('stage6.semantic.B', 'attempt-b'),
        ),
    )
    marked = {}

    def mark(_ctx, **kwargs):
        assert state['locked']
        marked.update(kwargs)
        events.append('committed')

    monkeypatch.setattr(stage6_runs, 'mark_committed', mark)

    got, _report = semantic.finalize_semantic_candidate(
        ctx, source, prepared, evaluation)

    assert got is landed
    assert marked == {
        'artifact_payload': payload,
        'finalization_payload': {
            'rel': 'stage6/semantic_1.json',
            'seq': 1,
            'from': 'landed',
        },
    }
    assert events.index('seq') > events.index('lock-enter')
    assert events.index('seq') < events.index('reservation:stage6/semantic_1.json')
    assert (events.index('reservation:stage6/semantic_1.json')
            < events.index('write:stage6/semantic_1.json'))
    assert events.index('write:stage6/semantic_1.json') < events.index('readback')
    assert events.index('readback') < events.index('adoptions')
    assert events.index('adoptions') < events.index('committed') < events.index('lock-exit')


def test_legacy_finalize_cannot_steal_a_foreign_prepared_agent_seq(monkeypatch):
    payload = {'typed': 'candidate'}

    class Artifact:
        def model_dump(self, **_kwargs):
            return dict(payload)

    artifact = Artifact()
    prepared = _prepared()
    evaluation = semantic.SemanticCandidateEvaluation(
        candidate=artifact,
        requested_model='legacy-model',
        adopted_models=('legacy-model',),
        accepted_attempts=(('A', 'attempt-a'), ('B', 'attempt-b')),
        input_sha256=prepared.input_sha256,
        _responses=(),
        _candidate_sha256=semantic._canonical_sha256(payload),
    )
    ctx = SimpleNamespace(
        node='stage6.semantic', novel_dir=SimpleNamespace(), executor_profile=None)
    source = SimpleNamespace(model='legacy-model')
    calls: list[str] = []

    @contextmanager
    def ownership(_novel_dir):
        calls.append('lock')
        yield

    class Store:
        def write(self, *_args, **_kwargs):
            calls.append('write')
            raise AssertionError('foreign prepared 阻断后不得写 formal artifact')

    monkeypatch.setattr(semantic, '_assert_evaluation_identity', lambda *_args: None)
    monkeypatch.setattr(semantic, '_assert_prepared_current', lambda *_args: None)
    monkeypatch.setattr(
        semantic, '_materialize_semantic_candidate', lambda *_args, **_kwargs: artifact)
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: Store())
    monkeypatch.setattr(semantic, 'semantic_seqs', lambda _store: [])
    monkeypatch.setattr(stage6_runs, 'commit_ownership', ownership)

    def refuse(_ctx, rel):
        calls.append(f'reservation:{rel}')
        raise ProvenanceRefused('foreign run 仍持有 prepared semantic seq')

    monkeypatch.setattr(
        stage6_runs, 'refuse_foreign_commit_reservations', refuse)

    with pytest.raises(ProvenanceRefused, match='foreign run'):
        semantic.finalize_semantic_candidate(ctx, source, prepared, evaluation)

    assert calls == ['lock', 'reservation:stage6/semantic_1.json']


def test_current_artifact_rejects_changed_stage5_opening_reference(monkeypatch):
    class SemanticArtifact:
        def __init__(self) -> None:
            self.judged = {'stage5/ch01.json': _judged(1, 'a' * 10)}

    artifact = SemanticArtifact()
    current_up = SimpleNamespace(
        upto=1,
        mat=SimpleNamespace(lead=[{'output_ordinal': 1, 'text': '已改正文'}]),
        judged={'stage5/ch01.json': _judged(1, 'b' * 10)},
    )

    class Ctx:
        @contextmanager
        def validation_reads(self):
            yield

    store = SimpleNamespace(
        ctx=Ctx(),
        freshness=lambda _rel: SimpleNamespace(state='fresh'),
    )
    monkeypatch.setattr(semantic, 'HookSemanticReview', SemanticArtifact)
    monkeypatch.setattr(
        semantic, 'latest_semantic', lambda _store: (1, artifact))
    monkeypatch.setattr(
        semantic.hook, '_Upstreams', lambda _ctx, _store: current_up)

    got, why, seq = semantic._current_artifact(store, SimpleNamespace())

    assert got is None and seq == 1
    assert 'Stage5 开篇正文已变化' in why
