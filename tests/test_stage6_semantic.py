#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 匿名质量审判：矛盾边界、质量评分、sha1 绑定与批准硬闸。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.stage6 import (
    HookCandidateReview,
    HookSemanticResponse,
)
from novel.errors import TerminalState, UpstreamMissing
from novel.llm.transport import ChatResponse
from novel.stages.stage6 import semantic

WIRE = ChatResponse(content='{}')

TEXT_A = '她嫁进将军府那晚，就发现枕头下压着一封没有署名的信。\n\n信里只有一句话。'
TEXT_B = '他在城门口等了三年，等来的却是她与仇人成亲的喜帖。'
TEXT_C = '说书人拍醒木：这故事没人讲完过，因为讲到最后的人都死了。'


def _quality(**overrides) -> dict:
    data = {
        'read_on_desire': 9,
        'body_voice_match': 8,
        'concrete_scene_and_specificity': 7,
        'curiosity_and_payoff_promise': 9,
        'hook_craft': 8,
        'synopsis_penalty': 2,
    }
    data.update(overrides)
    return data


def _response_review(label: str, *, text: str = TEXT_A, contradiction: bool = False,
                     uncertain: bool = False, evidence: str | None = None) -> dict:
    contradictions = ([{
        'assertion': '她嫁给了仇人',
        'evidence': evidence or ('嫁进将军府' if text == TEXT_A else text[:6]),
        'reason': '全书人物关系与此相反',
    }] if contradiction else [])
    return {
        'label': label,
        'coverage': 'uncertain' if uncertain else 'complete',
        'uncertainty_reason': '指代无法确认' if uncertain else None,
        'contradictions': contradictions,
        'quality': _quality(),
        'concise_reason': '具体场面强，最后一击有效。',
    }


def _stored_review(label: str, text: str, *, contradiction: bool = False,
                   uncertain: bool = False, **score_overrides) -> HookCandidateReview:
    data = _response_review(label, text=text, contradiction=contradiction,
                            uncertain=uncertain)
    data['quality'] = _quality(**score_overrides)
    data['text_sha1'] = semantic.text_sha1(text)
    return HookCandidateReview.model_validate(data)


def _art(*, approved: str | None = None):
    candidates = [
        SimpleNamespace(approach='source_architecture', text=TEXT_A, findings=[]),
        SimpleNamespace(approach='payoff_preview', text=TEXT_B, findings=[]),
        SimpleNamespace(approach='scene_punchline', text=TEXT_C, findings=[]),
    ]
    picked = next((c for c in candidates if c.approach == approved), None)
    approved_obj = (SimpleNamespace(approach=picked.approach, text=picked.text)
                    if picked is not None else None)
    return SimpleNamespace(candidates=candidates, approved=approved_obj)


def _semantic_artifact(reviews: list[HookCandidateReview], ranking: list[str] | None = None):
    return SimpleNamespace(
        reviews=reviews,
        ranking=ranking or [review.label for review in reviews],
    )


class _Store:
    def __init__(self, fresh: bool = True) -> None:
        self._fresh = fresh

    def freshness(self, _rel):
        return SimpleNamespace(state='fresh' if self._fresh else 'stale')


def _all_clean_reviews() -> list[HookCandidateReview]:
    return [
        _stored_review('source_architecture', TEXT_A),
        _stored_review('payoff_preview', TEXT_B),
        _stored_review('scene_punchline', TEXT_C),
    ]


def _install(monkeypatch, artifact, *, seq: int = 1):
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _store: (seq, artifact))


def test_system_publishes_quality_and_material_contradiction_contract():
    system = semantic.SEMANTIC_SYS
    assert '剧透本身不加分也不扣分' in system
    assert '重写台词、压缩时间、倒叙、合并数章和合成场面' in system
    assert '人物身份、人物关系、关键因果或故事结果' in system
    assert '"read_on_desire": "number 0..10"' in system
    assert '"synopsis_penalty": "number 0..10"' in system
    assert '"ranking": [' in system


def test_response_validator_closes_review_and_ranking_coverage():
    aliases = [('T1', 'source_architecture', TEXT_A),
               ('T2', 'payoff_preview', TEXT_B),
               ('T3', 'scene_punchline', TEXT_C)]
    validate = semantic.response_validator(aliases)
    good = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1'),
                    _response_review('T2', text=TEXT_B),
                    _response_review('T3', text=TEXT_C)],
        'ranking': ['T1', 'T2', 'T3'],
    })
    assert validate(good, WIRE) is None

    missing = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1')],
        'ranking': ['T1'],
    })
    assert 'reviews 必须恰好覆盖' in (validate(missing, WIRE) or '')

    duplicate_rank = good.model_copy(update={'ranking': ['T1', 'T1', 'T3']})
    assert 'ranking 必须恰好覆盖' in (validate(duplicate_rank, WIRE) or '')


def test_validator_requires_verbatim_contradiction_evidence_and_recovers_whitespace():
    aliases = [('T1', 'source_architecture', TEXT_A)]
    validate = semantic.response_validator(aliases)

    bad = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1', contradiction=True, evidence='候选里根本没有这句话')],
        'ranking': ['T1'],
    })
    assert '连续子串' in (validate(bad, WIRE) or '')

    spaced = TEXT_A.replace('\n\n', '\n')
    recovered = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1', contradiction=True, evidence=spaced)],
        'ranking': ['T1'],
    })
    assert validate(recovered, WIRE) is None
    assert recovered.reviews[0].contradictions[0].evidence == TEXT_A


def test_validator_forces_clean_candidates_ahead_of_bad_candidates():
    aliases = [('T1', 'source_architecture', TEXT_A), ('T2', 'payoff_preview', TEXT_B)]
    validate = semantic.response_validator(aliases)
    response = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1'),
                    _response_review('T2', text=TEXT_B, contradiction=True)],
        'ranking': ['T2', 'T1'],
    })
    assert '全部排在' in (validate(response, WIRE) or '')


def test_quality_total_is_code_owned_and_fixed_weighted():
    review = _stored_review('source_architecture', TEXT_A)
    assert semantic.quality_score(review) == 8.15
    with pytest.raises(ValueError):
        HookCandidateReview.model_validate({
            **review.model_dump(mode='python'),
            'total_score': 10,
        })


def test_build_prompt_is_anonymous_and_carries_all_quality_references(monkeypatch):
    art = _art()
    store = SimpleNamespace(
        exists=lambda rel: rel == 'stage6/hook.json',
        freshness=lambda _rel: SimpleNamespace(state='fresh'),
        read_own=lambda _model, _rel: (art, {}),
    )
    mat = SimpleNamespace(
        lead=({'output_ordinal': 1, 'text': '这是重写后的第一章。'},),
        story_truth={'entities': {'characters': {'c1': {'canonical': '她'}}},
                     'canon': {'secrets': []}, 'arc': {'ending': '真结局'}},
        story_map=({'chapter': 1, 'events': ['她收到匿名信']},),
        style_packet={'book_surface_habits': ['短句']},
    )
    upstream = SimpleNamespace(src_hook='原作文案金句。', mat=mat)
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(semantic.hook, '_Upstreams', lambda _ctx, _store: upstream)

    system, user, got_art, _up, aliases = semantic.build_prompt(SimpleNamespace())
    assert system == semantic.SEMANTIC_SYS and got_art is art
    assert [item[0] for item in aliases] == ['T1', 'T2', 'T3']
    assert all(real not in user for _neutral, real, _text in aliases), 'approach 名泄露给匿名主编'
    payload = json.loads(user.split('\n\n', 1)[1])
    assert payload['source_reference'] == '原作文案金句。'
    assert payload['rewritten_opening'][0]['text'] == '这是重写后的第一章。'
    assert payload['story_truth']['arc']['ending'] == '真结局'
    assert payload['whole_story_map'][0]['events'] == ['她收到匿名信']
    assert payload['style']['book_surface_habits'] == ['短句']


def test_outcomes_are_unver_when_quality_review_is_missing_or_stale(monkeypatch):
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _store: None)
    got = semantic.outcomes(_Store(), _art())
    assert len(got) == 3 and all(out.unver_why for out in got)
    assert {out.rule for out in got} == {
        'hook_semantic_contradiction', 'hook_candidate_contradiction', 'hook_quality_rank'}

    current = _semantic_artifact(_all_clean_reviews())
    _install(monkeypatch, current)
    got = semantic.outcomes(_Store(fresh=False), _art())
    assert all(out.unver_why and '上游已变化' in out.unver_why for out in got)

def test_picking_duplicate_text_does_not_stale_sha_bound_review(monkeypatch):
    artifact = _semantic_artifact(_all_clean_reviews())
    _install(monkeypatch, artifact)
    outcomes = semantic.outcomes(_Store(), _art(approved='source_architecture'))
    assert all(out.unver_why is None for out in outcomes)
    assert not outcomes[0].findings


def test_one_bad_candidate_is_visible_but_does_not_fail_the_candidate_set(monkeypatch):
    reviews = _all_clean_reviews()
    reviews[1] = _stored_review('payoff_preview', TEXT_B, contradiction=True)
    artifact = _semantic_artifact(
        reviews, ['source_architecture', 'scene_punchline', 'payoff_preview'])
    _install(monkeypatch, artifact)
    hard, info, metric = semantic.outcomes(_Store(), _art())
    assert not hard.findings and hard.unver_why is None
    assert info.rule == 'hook_candidate_contradiction' and len(info.findings) == 1
    assert TEXT_B[:6] in info.findings[0].detail
    assert metric.rule == 'hook_quality_rank' and metric.findings


def test_all_bad_or_approved_bad_materializes_the_hard_rule(monkeypatch):
    all_bad = [
        _stored_review('source_architecture', TEXT_A, contradiction=True),
        _stored_review('payoff_preview', TEXT_B, contradiction=True),
        _stored_review('scene_punchline', TEXT_C, contradiction=True),
    ]
    _install(monkeypatch, _semantic_artifact(all_bad))
    hard = semantic.outcomes(_Store(), _art())[0]
    assert hard.findings and '全部存在实质矛盾' in hard.findings[0].detail

    one_bad = _all_clean_reviews()
    one_bad[1] = _stored_review('payoff_preview', TEXT_B, contradiction=True)
    _install(monkeypatch, _semantic_artifact(one_bad))
    hard = semantic.outcomes(_Store(), _art(approved='payoff_preview'))[0]
    assert hard.findings and '定稿文本含实质矛盾' in hard.findings[0].detail


def test_uncertain_is_not_clear_and_cannot_be_selected(monkeypatch):
    reviews = _all_clean_reviews()
    reviews[0] = _stored_review('source_architecture', TEXT_A, uncertain=True)
    artifact = _semantic_artifact(reviews)
    _install(monkeypatch, artifact)
    assessment = semantic.assessment_for_text(_Store(), _art(), TEXT_A)
    assert assessment.state == 'uncertain' and not assessment.selectable
    with pytest.raises(TerminalState, match='不能定稿'):
        semantic.require_selectable(_Store(), _art(), TEXT_A, '1')
    assert semantic.require_selectable(_Store(), _art(), TEXT_B, '2').selectable


def test_contradiction_cannot_be_selected_even_when_human_picks_it(monkeypatch):
    reviews = _all_clean_reviews()
    reviews[2] = _stored_review('scene_punchline', TEXT_C, contradiction=True)
    _install(monkeypatch, _semantic_artifact(reviews))
    with pytest.raises(TerminalState, match='实质矛盾'):
        semantic.require_selectable(_Store(), _art(), TEXT_C, '3')


def test_downstream_requires_the_current_approved_bytes_to_remain_clear(monkeypatch):
    _install(monkeypatch, _semantic_artifact(_all_clean_reviews()))
    with pytest.raises(TerminalState, match='还没定稿'):
        semantic.require_approved_clear(_Store(), _art())
    assert semantic.require_approved_clear(
        _Store(), _art(approved='source_architecture')).selectable

    reviews = _all_clean_reviews()
    reviews[0] = _stored_review('source_architecture', TEXT_A, contradiction=True)
    _install(monkeypatch, _semantic_artifact(reviews))
    with pytest.raises(TerminalState, match='实质矛盾'):
        semantic.require_approved_clear(
            _Store(), _art(approved='source_architecture'))


def test_build_prompt_refuses_missing_or_stale_hook(monkeypatch):
    monkeypatch.setattr(
        semantic, 'ArtifactStore',
        lambda _ctx: SimpleNamespace(exists=lambda _rel: False),
    )
    with pytest.raises(UpstreamMissing, match='先跑 stage6.hook'):
        semantic.build_prompt(SimpleNamespace())

    stale = SimpleNamespace(
        exists=lambda _rel: True,
        freshness=lambda _rel: SimpleNamespace(state='stale'),
    )
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: stale)
    with pytest.raises(TerminalState, match='不要付费审'):
        semantic.build_prompt(SimpleNamespace())
