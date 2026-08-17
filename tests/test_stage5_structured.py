from __future__ import annotations

import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema.evaluation import (
    CapabilityMetrics, EvaluatorFingerprint, EvaluatorProfile, EvidenceRef,
    JudgeDecision, SemanticDecision, TrimAudit, TrimJudgeResponse,
)
from contracts.schema.stage4 import CtxChapter, FactRef, PlanGoal
from contracts.schema.stage5 import ChapterResponse
from novel.errors import RefusedByStructure
from novel.llm.privacy import (
    PromptPrivateLeak, assert_prompt_private_absent, private_tokens,
)
from novel.llm.transport import ChatResponse
from novel.stages.stage5 import prompts, write
from novel.stages.stage5 import checks
from novel.stages.stage4 import context as stage4_context
from tests.factories import stage5_book


def _entry() -> tuple[dict, object]:
    book = stage5_book()
    return book.chapters[min(book.chapters)], book


def test_writer_response_slots_are_complete_unique_and_canonicalised():
    entry, _book = _entry()
    second = {**entry['segments'][0], 'slot': 's02'}
    entry = {**entry, 'segments': [entry['segments'][0], second]}
    response = ChapterResponse.model_validate({
        'segments': [
            {'slot': 's02', 'text': '后段。'},
            {'slot': 's01', 'text': '前段。'},
        ],
    })
    assert write._response_problem(
        response, ChatResponse(content='', finish_reason='stop'), entry) is None
    assert write._ordered_segments(response, entry) == [
        {'slot': 's01', 'text': '前段。'}, {'slot': 's02', 'text': '后段。'}]

    missing = ChapterResponse.model_validate({
        'segments': [{'slot': 's01', 'text': '只有一段。'}]})
    assert '恰好' in str(write._response_problem(
        missing, ChatResponse(content='', finish_reason='stop'), entry))
    with pytest.raises(ValidationError, match='duplicate'):
        ChapterResponse.model_validate({'segments': [
            {'slot': 's01', 'text': '一。'}, {'slot': 's01', 'text': '二。'}]})


def test_fidelity_material_must_land_in_its_declared_segment():
    entry, book = _entry()
    required = '这句必须留在第一幕，一个字也不能挪。'
    first = {**entry['segments'][0], 'verbatim': [required]}
    second = {**entry['segments'][0], 'slot': 's02', 'verbatim': []}
    entry = {**entry, 'segments': [first, second]}
    returned = [
        {'slot': 's01', 'text': '第一幕只有别的内容。'},
        {'slot': 's02', 'text': '第二幕误放了：' + required},
    ]
    hits = checks.segment_fidelity_findings(
        returned, entry, book, mode='deep', given='')
    assert any(hit.rule == 'verbatim_missing' and hit.evidence[0] == 's01'
               for hit in hits)
    assert hits and {hit.output_ordinal for hit in hits} == {entry['output_ordinal']}


def test_segment_event_order_is_proved_or_explicitly_unverifiable():
    entry, book = _entry()
    entry = {
        **entry,
        'characters': {
            **entry['characters'],
            'char_002': {'display_name': '周衡', 'role': '账房'},
        },
        'segments': [{
            **entry['segments'][0],
            'events': ['沈青先查看账册。', '周衡随后关门。'],
        }],
    }
    reversed_hits = checks.segment_event_findings(
        [{'slot': 's01', 'text': '周衡随后关门。沈青才查看账册。'}],
        entry, book)
    assert any(hit.rule == 'event_landing' and '顺序' in hit.detail
               for hit in reversed_hits)

    unver_entry = {
        **entry,
        'segments': [{**entry['segments'][0], 'events': ['风声变紧，局势更危险。']}],
    }
    unver_hits = checks.segment_event_findings(
        [{'slot': 's01', 'text': '风更紧了。'}], unver_entry, book)
    assert any(hit.rule == 'event_landing_unver' for hit in unver_hits)


def test_prompt_private_values_are_derived_from_schema_metadata_recursively():
    goal = PlanGoal(
        intent='advance',
        fact_refs=[FactRef(kind='event', ref='b01_01', chapter=1)],
        rationale='这是只供审计、绝不能下发的自由理由',
    )
    assert [(token.path, token.value) for token in private_tokens(goal)] == [
        ('fact_refs[0].ref', 'b01_01'),
        ('rationale', '这是只供审计、绝不能下发的自由理由'),
    ]
    with pytest.raises(PromptPrivateLeak, match='rationale'):
        assert_prompt_private_absent(
            goal, system='系统', user='意外包含：这是只供审计、绝不能下发的自由理由')


def test_final_writer_messages_do_not_contain_private_secret_ids():
    entry, book = _entry()
    entry = {**entry, 'must_not_reveal': ['secret_never_send_987']}
    typed = CtxChapter.model_validate(entry)
    material = prompts.build_material(entry, book, with_source_text=False)
    system, user = prompts.write_system('deep'), prompts.build_user(material)
    assert 'secret_never_send_987' not in system + user
    assert_prompt_private_absent(typed, system=system, user=user)


def test_final_writer_messages_project_character_rule_and_secret_ids_to_human_facts():
    entry, book = _entry()
    entry = deepcopy(entry)
    char_id = 'char_never_send_987'
    rule_id = 'wr_never_send_987'
    secret_id = 'secret_never_send_987'
    entry['characters'] = {
        char_id: {'display_name': '周衡', 'role': '账房'},
    }
    entry['facts']['world_rules'] = [{
        'rule_id': rule_id,
        'kind': 'legal_rule',
        'description': '旧城账册必须由掌柜与账房共同封存。',
    }]
    entry['facts']['secrets'] = [{
        'secret_id': secret_id,
        'fact': '周衡已经调换账册末页。',
        'known_by': ['周衡'],
        'reader_knows_by_now': True,
    }]
    typed = CtxChapter.model_validate(entry)

    private = {(token.path, token.value) for token in private_tokens(typed)}
    assert ('characters[key]', char_id) in private
    assert ('facts.world_rules[0].rule_id', rule_id) in private
    assert ('facts.secrets[0].secret_id', secret_id) in private

    material = prompts.build_material(entry, book, with_source_text=False)
    system, user = prompts.write_system('deep'), prompts.build_user(material)
    rendered = system + user
    assert all(token not in rendered for token in (char_id, rule_id, secret_id))
    for human_fact in ('周衡', '账房', '共同封存', '调换账册末页'):
        assert human_fact in user
    assert_prompt_private_absent(typed, system=system, user=user)


def test_writer_goal_uses_resolved_facts_and_never_internal_plan_ids():
    entry, book = _entry()
    material = prompts.build_material(entry, book, with_source_text=False)
    user = prompts.build_user(material)
    assert '事件：沈青发现账册末页的墨迹被人换过。' in user
    assert 'b01_01' not in user
    assert 'chapter:1:start' not in user
    assert 'fact_refs' not in user


def test_context_resolves_goal_ids_after_chapter_gates():
    goal = {
        'intent': 'advance',
        'fact_refs': [
            {'kind': 'event', 'ref': 'b01_01', 'chapter': 1},
            {'kind': 'character', 'ref': 'char_001', 'chapter': 1},
        ],
    }
    resolved = stage4_context._resolve_goal(
        goal, ordinal=1,
        bidx={'b01_01': {'event': '沈青发现账册墨迹被换过。'}},
        disp={'char_001': '沈青'},
        facts={'relations': [], 'world_rules': [], 'secrets': [], 'setting': {}},
        global_g={'entities': {'relations': []}},
    )
    assert resolved == {
        'intent': 'advance',
        'facts': ['事件：沈青发现账册墨迹被换过。', '人物：沈青'],
    }
    assert not any(token in json.dumps(resolved, ensure_ascii=False)
                   for token in ('b01_01', 'char_001', 'fact_refs'))


def test_context_refuses_goal_that_would_bypass_a_chapter_gate():
    with pytest.raises(RefusedByStructure, match='无法在本章安全解析'):
        stage4_context._resolve_goal(
            {'intent': 'advance', 'fact_refs': [
                {'kind': 'world_rule', 'ref': 'wr_09', 'chapter': 1}]},
            ordinal=1, bidx={}, disp={},
            facts={'relations': [], 'world_rules': [], 'secrets': [], 'setting': {}},
            global_g={'entities': {'relations': []}},
        )


def test_hidden_secret_withhold_goal_does_not_disclose_identity_or_fact():
    resolved = stage4_context._resolve_goal(
        {'intent': 'withhold', 'fact_refs': [
            {'kind': 'secret', 'ref': 'secret_09', 'chapter': 1}]},
        ordinal=1, bidx={}, disp={},
        facts={'relations': [], 'world_rules': [], 'secrets': [], 'setting': {}},
        global_g={'entities': {'relations': []}},
    )
    wire = json.dumps(resolved, ensure_ascii=False)
    assert 'secret_09' not in wire
    assert '尚未公开' in wire


def test_writer_system_requires_json_segments_and_user_names_each_slot():
    entry, book = _entry()
    system = prompts.write_system('deep')
    user = prompts.build_user(prompts.build_material(entry, book, with_source_text=False))
    assert 'Return ONLY valid JSON' in system
    assert '"segments"' in system
    assert 'slot=s01' in user
    json.dumps(prompts.CHAPTER_RESPONSE_HINT, ensure_ascii=False)


def test_trim_is_fail_closed_without_an_enabled_calibrated_profile(monkeypatch):
    monkeypatch.delenv('NOVEL_EVALUATOR_PROFILE', raising=False)
    monkeypatch.delenv('NOVEL_GROUND_TRUTH_MANIFEST', raising=False)
    entry, book = _entry()
    entry = {**entry, 'budget_chars': 20}
    segments = [{'slot': 's01', 'text': '这是一段明显超过二十字上限的正文。' * 8}]
    kept, notes, audit = write._trim_to_fit(
        SimpleNamespace(model='test-model'), segments, entry, book, mode='deep')
    assert kept == segments
    assert audit is not None and audit.adopted is False and audit.semantic is None
    assert audit.before_sha256 == audit.after_sha256
    assert notes and '未发起收缩调用或语义调用' in notes[0]


_HASH = '1' * 64


def _enabled_trim_binding():
    fingerprint = EvaluatorFingerprint(
        dataset_sha256=_HASH, model='judge-v1', prompt_sha256=_HASH,
        schema_sha256=_HASH, decoder_sha256=_HASH)
    metric = CapabilityMetrics(
        ability='trim_equivalence', fingerprint=fingerprint, n=100,
        pass_precision_wilson95_lower=.91,
        fail_precision_wilson95_lower=.91,
        violation_recall_wilson95_lower=.91,
        evidence_closure=.96, rerun_consistency=.96, enabled=True)
    profile = EvaluatorProfile.model_construct(
        profile_version=1, profile_id='trim-profile-v1',
        calibrated_at='2026-08-16T00:00:00Z', runs=3,
        thresholds_locked_before_evaluation=True, capabilities=[metric])
    return profile, fingerprint


def _trim_case():
    raw_entry, book = _entry()
    entry = deepcopy(raw_entry)
    candidate = (
        '沈青在茶馆核对账册。随后，她确认墨迹被人换过，却没有仓促下结论。'
        '窗外雨声渐紧，她把账册收好，决定天亮后再查。')
    entry['budget_chars'] = len(candidate)
    entry['segments'][0]['budget_chars'] = len(candidate)
    original = candidate + '这段解释重复了同一个意思。' * 8
    return entry, book, [
        {'slot': 's01', 'text': original}], [
        {'slot': 's01', 'text': candidate}]


class _TrimClient:
    model = 'judge-v1'

    def __init__(self, candidate, verdicts=('PASS', 'PASS')):
        self.candidate = candidate
        self.verdicts = list(verdicts)
        self.calls = []

    def ask_json(self, _system, _user, *, model_cls, **kwargs):
        self.calls.append((model_cls, kwargs))
        if model_cls is ChapterResponse:
            return ChapterResponse.model_validate({'segments': self.candidate})
        verdict = self.verdicts.pop(0)
        evidence = ([] if verdict == 'UNVER' else [
            {'side': 'before', 'start': 0, 'end': 2},
            {'side': 'after', 'start': 0, 'end': 2},
        ])
        if verdict == 'FAIL':
            evidence = [evidence[-1]]
        return model_cls.model_validate({
            'verdict': verdict, 'evidence': evidence,
            'reason': f'judge says {verdict}',
        })


def test_trim_profile_missing_makes_zero_client_calls(monkeypatch):
    monkeypatch.delenv('NOVEL_EVALUATOR_PROFILE', raising=False)
    monkeypatch.delenv('NOVEL_GROUND_TRUTH_MANIFEST', raising=False)
    entry, book, original, candidate = _trim_case()
    client = _TrimClient(candidate)
    kept, _notes, audit = write._trim_to_fit(
        client, original, entry, book, mode='deep')
    assert kept == original
    assert client.calls == []
    assert audit is not None and audit.deterministic_passed


def test_trim_profile_binding_covers_exact_runtime_surfaces(monkeypatch):
    binding = _enabled_trim_binding()
    seen = {}

    def fake_require(ability, **kwargs):
        seen['ability'] = ability
        seen.update(kwargs)
        return binding

    monkeypatch.setattr(write, 'require_enabled', fake_require)
    client = SimpleNamespace(
        model='judge-v1', _rungs=(('medium', True), ('low', False)))
    assert write._trim_profile_binding(client) == binding
    assert seen['ability'] == 'trim_equivalence'
    assert seen['model'] == 'judge-v1'
    assert seen['prompts'] == [
        prompts.trim_system(), prompts.trim_judge_system('A'),
        prompts.trim_judge_system('B')]
    assert seen['schemas'] == [ChapterResponse, write.TrimJudgeResponse]
    assert seen['decoder_identity']['judge_rungs'] == [
        ['medium', True], ['low', False]]
    assert seen['decoder_identity']['generator_rungs'] == [['low', False]]


def test_trim_judge_evidence_must_close_on_both_exact_subjects():
    with pytest.raises(ValidationError, match='before and after'):
        TrimJudgeResponse.model_validate({
            'verdict': 'PASS',
            'evidence': [{'side': 'after', 'start': 0, 'end': 1}],
            'reason': '只指了候选，不能闭合等价',
        })
    response = TrimJudgeResponse.model_validate({
        'verdict': 'PASS',
        'evidence': [
            {'side': 'before', 'start': 0, 'end': 1},
            {'side': 'after', 'start': 0, 'end': 99},
        ],
        'reason': '模型自报了越界 span',
    })
    assert '超出正文长度' in str(write._trim_judge_problem(
        response, ChatResponse(content='', finish_reason='stop'), '甲', '乙'))


def test_trim_mechanical_failure_never_calls_semantic_judges(monkeypatch):
    monkeypatch.setattr(write, '_trim_profile_binding',
                        lambda _client: _enabled_trim_binding())
    entry, book, original, _candidate = _trim_case()
    client = _TrimClient(original)  # unchanged response: not shorter / still over
    kept, _notes, audit = write._trim_to_fit(
        client, original, entry, book, mode='deep')
    assert kept == original
    assert audit is not None and audit.deterministic_passed is False
    assert audit.semantic is None
    assert client.calls
    assert all(model_cls is ChapterResponse for model_cls, _kwargs in client.calls)
    assert all(kwargs['rungs'] == write.TRIM_RUNGS for _model, kwargs in client.calls)
    assert client.calls[-1][1]['cache_policy'].name == 'refresh-write'


@pytest.mark.parametrize(('verdicts', 'expected'), [
    (('PASS', 'FAIL'), 'UNVER'),
    (('UNVER', 'PASS'), 'UNVER'),
    (('FAIL', 'FAIL'), 'FAIL'),
])
def test_trim_split_unver_or_fail_rolls_back(monkeypatch, verdicts, expected):
    monkeypatch.setattr(write, '_trim_profile_binding',
                        lambda _client: _enabled_trim_binding())
    entry, book, original, candidate = _trim_case()
    client = _TrimClient(candidate, verdicts=verdicts)
    kept, _notes, audit = write._trim_to_fit(
        client, original, entry, book, mode='deep')
    assert kept == original
    assert audit is not None and audit.deterministic_passed
    assert audit.semantic is not None and audit.semantic.verdict == expected
    assert audit.adopted is False


def test_trim_calibrated_double_pass_adopts_candidate(monkeypatch):
    monkeypatch.setattr(write, '_trim_profile_binding',
                        lambda _client: _enabled_trim_binding())
    entry, book, original, candidate = _trim_case()
    client = _TrimClient(candidate)
    kept, _notes, audit = write._trim_to_fit(
        client, original, entry, book, mode='deep')
    assert kept == candidate
    assert audit is not None and audit.adopted
    assert audit.deterministic_passed
    assert audit.semantic is not None
    assert (audit.semantic.verdict, audit.semantic.automated) == ('PASS', True)
    assert audit.before_sha256 == write._sha256_text(write._segments_text(original))
    assert audit.after_sha256 == write._sha256_text(write._segments_text(candidate))


def test_trim_private_token_is_blocked_before_any_client_call(monkeypatch):
    monkeypatch.setattr(write, '_trim_profile_binding',
                        lambda _client: _enabled_trim_binding())
    entry, book, original, candidate = _trim_case()
    private = 'secret_never_send_987'
    entry['must_not_reveal'] = [private]
    original[0]['text'] += private
    client = _TrimClient(candidate)
    with pytest.raises(PromptPrivateLeak, match='must_not_reveal'):
        write._trim_to_fit(client, original, entry, book, mode='deep')
    assert client.calls == []


def test_rejected_trim_semantic_fail_stays_in_verification():
    from contracts.artifacts import artifact_rel
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore
    from novel.views.inbox import _trim_verification_items

    candidate_sha = 'b' * 64
    evidence = [EvidenceRef(
        artifact='stage5/trim_audit_ch04.json#after',
        subject_sha256=candidate_sha, start=0, end=1)]
    judges = [JudgeDecision(
        judge=f'trim-{role}', verdict='FAIL', evidence=evidence,
        reason='删掉了结尾因果') for role in ('A', 'B')]
    decision = SemanticDecision(
        ability='trim_equivalence', subject_key=candidate_sha,
        verdict='FAIL', automated=True, evidence=evidence,
        reason='已校准双盲 judge 同结论且证据闭合',
        profile_id='trim-profile-v1', judges=judges)
    audit = TrimAudit(
        output_ordinal=4, mode='deep', before_sha256='a' * 64,
        after_sha256=candidate_sha, before_claims=[], after_claims=[],
        deterministic_passed=True, evaluator_fingerprint=_enabled_trim_binding()[1],
        semantic=decision, adopted=False,
        reason='语义 FAIL，保留 pretrim')
    root = Path(tempfile.mkdtemp(prefix='trim-inbox-'))
    try:
        store = ArtifactStore(readonly_ctx(
            root, 'stage5.write', prov.code_version()))
        store.write(artifact_rel('stage5.trim_audit', nn=4), audit)
        # Rendering is tested with an explicitly current audit set; discovery
        # itself intentionally rejects an audit that has no matching formal chapter.
        rows = _trim_verification_items(
            root, prov.code_version(), audits={4: (audit, 'c' * 64)})
        assert [(row.rule, row.chapter) for row in rows] == [
            ('trim_audit_ch04', 4)]
        assert 'FAIL' in rows[0].what and 'pretrim' in rows[0].how
    finally:
        shutil.rmtree(root, ignore_errors=True)
