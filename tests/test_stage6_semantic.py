#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 6 文案语义审判的两相边界、sha1 绑定与 uncertain 不洗绿。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.stage6 import HookSemanticResponse, HookTextClaims, HookTextClaimsResponse
from novel.errors import TerminalState, UpstreamMissing
from novel.llm.transport import ChatResponse
from novel.stages.stage6 import semantic

WIRE = ChatResponse(content='{}')

ARC = ['最终她亲手烧掉了将军府', '真凶其实是她的贴身丫鬟']
TEXT = '她嫁进将军府那晚，就发现枕头下压着一封没有署名的信。\n\n信里只有一句话。'
TEXT_B = '第二条候选：他在城门口等了三年。'
TEXT_C = '第三条候选：说书人说，这故事没人讲完过。'
EVIDENCE = '枕头下压着一封没有署名的信'


def _resp(label: str = 'identity_contrast', *, coverage: str = 'complete',
          evidence: str = EVIDENCE) -> HookTextClaimsResponse:
    return HookTextClaimsResponse.model_validate({
        'label': label,
        'coverage': coverage,
        'uncertainty_reason': '开头有含混指涉' if coverage == 'uncertain' else None,
        'claims': [{'claim_id': 'c1', 'assertion': '她婚房里有一封匿名信',
                    'evidence': evidence}],
    })


def _claims(label: str = 'identity_contrast', *, coverage: str = 'complete',
            text: str = TEXT) -> HookTextClaims:
    base = _resp(label, coverage=coverage).model_dump(mode='json')
    base['text_sha1'] = semantic.text_sha1(text)
    return HookTextClaims.model_validate(base)


def _decision(status: str = 'grounded', *, label: str = 'identity_contrast',
              claim_id: str = 'c1', reason: str = '开篇正文可见此信') -> dict:
    return {'label': label, 'claim_id': claim_id, 'status': status, 'reason': reason}


# ── 第一相：arc/secret-blind 与逐字证据 ─────────────────────────────────────

def test_claim_prompt_is_arc_and_secret_blind():
    """第一相只见一条文案文本：arc、下发面、秘密一个字都不进 user。"""
    user = semantic.build_claim_user('identity_contrast', TEXT)
    payload = json.loads(user.split('\n\n', 1)[1])
    assert payload == {'label': 'identity_contrast', 'text': TEXT}
    for src in ARC:
        assert src not in user
    assert 'secret' not in semantic.CLAIMS_SYSTEM.lower()


def test_claim_prompt_publishes_the_exact_response_shape():
    system = semantic.CLAIMS_SYSTEM
    assert '"label": "identity_contrast"' in system
    assert '"coverage": "complete|uncertain"' in system
    assert '"uncertainty_reason": "str|null"' in system
    assert '"claim_id": "c1"' in system
    align = semantic.ALIGN_SYSTEM
    assert '"decisions": [' in align
    assert '"status": "arc_leak|invented|grounded|unver"' in align


def test_claims_validator_requires_label_echo_and_verbatim_evidence():
    validate = semantic.claims_validator('identity_contrast', TEXT)
    assert validate(_resp(), WIRE) is None
    assert '原样交回' in (validate(_resp('result_flashback'), WIRE) or '')
    assert '连续子串' in (validate(_resp(evidence='这不是文案里的证据'), WIRE) or '')

    dup = HookTextClaimsResponse.model_validate({
        'label': 'identity_contrast', 'coverage': 'complete', 'uncertainty_reason': None,
        'claims': [{'claim_id': 'c1', 'assertion': '甲', 'evidence': EVIDENCE},
                   {'claim_id': 'c1', 'assertion': '乙', 'evidence': '信里只有一句话'}],
    })
    assert '重复' in (validate(dup, WIRE) or '')


def test_claims_validator_recovers_unique_whitespace_difference_to_hook_bytes():
    """空白差异唯一匹配时回取成文案原字节——与 stage5 共用同一个 helper。"""
    squeezed = TEXT.replace('\n\n', '\n')
    validate = semantic.claims_validator('identity_contrast', TEXT)
    resp = _resp(evidence=squeezed)
    assert validate(resp, WIRE) is None
    assert resp.claims[0].evidence == TEXT


def test_uncertainty_reason_alias_is_normalised_and_conflict_rejected():
    """与 stage5 同款收敛的拷贝也要有自己的反例，否则拷贝坏了没人知道。"""
    aliased = HookTextClaimsResponse.model_validate({
        'label': 'identity_contrast', 'coverage': 'uncertain',
        'coverage_reason': '结尾含混', 'claims': [],
    })
    assert aliased.uncertainty_reason == '结尾含混'
    with pytest.raises(ValueError, match='互相冲突'):
        HookTextClaimsResponse.model_validate({
            'label': 'identity_contrast', 'coverage': 'uncertain',
            'uncertainty_reason': '甲', 'coverage_note': '乙', 'claims': [],
        })


def test_request_claims_round_trips_label_and_stamps_text_sha1():
    calls: list[str] = []

    class Client:
        def ask_json(self, _system, user, *, model_cls, purpose, validator):
            payload = json.loads(user.split('\n\n', 1)[1])
            calls.append(payload['label'])
            resp = model_cls.model_validate({
                'label': payload['label'], 'coverage': 'complete',
                'uncertainty_reason': None, 'claims': [],
            })
            assert validator(resp, WIRE) is None
            assert purpose.endswith(payload['label'])
            return resp

    ctx = SimpleNamespace(node='stage6.semantic')
    users = [('identity_contrast', semantic.build_claim_user('identity_contrast', TEXT), TEXT)]
    out = semantic.request_claims(ctx, Client(), semantic.CLAIMS_SYSTEM, users)
    assert calls == ['identity_contrast']
    assert out[0].text_sha1 == semantic.text_sha1(TEXT)


# ── 第二相：裁决覆盖、取值前提与「不把结局抄进报告」──────────────────────────

def _up(arc_first: str = ARC[0], arc_second: str = ARC[1]) -> SimpleNamespace:
    return SimpleNamespace(
        arc={'climax': arc_first, 'ending': arc_second, 'arc_tracks': []},
        mat=SimpleNamespace(given='下发面：人物甲、人物乙、秘密丙。',
                            lead=({'text': '开篇正文第一章。'},)))


def test_alignment_prompt_carries_arc_given_and_lead_but_claims_stay_verbatim():
    system, user = semantic.build_alignment_prompt([_claims()], _up())
    assert system == semantic.ALIGN_SYSTEM
    assert ARC[0] in user and ARC[1] in user
    assert '下发面：人物甲' in user and '开篇正文第一章。' in user
    assert EVIDENCE in user
    assert '不得补造命题或证据' in user


def test_alignment_validator_closes_decision_coverage():
    validate = semantic.alignment_validator([_claims()], ARC)
    ok = HookSemanticResponse.model_validate({'decisions': [_decision()]})
    assert validate(ok, WIRE) is None

    missing = HookSemanticResponse.model_validate({'decisions': []})
    assert '恰好覆盖' in (validate(missing, WIRE) or '')

    stray = HookSemanticResponse.model_validate(
        {'decisions': [_decision(), _decision(label='result_flashback')]})
    assert '恰好覆盖' in (validate(stray, WIRE) or '')

    doubled = HookSemanticResponse.model_validate(
        {'decisions': [_decision(), _decision(status='unver', reason='又judged一次')]})
    assert '两份裁决' in (validate(doubled, WIRE) or '')


def test_alignment_reason_may_not_echo_arc_literal_bytes():
    """审判报告只许引用文案自身字节——reason 复述结局原文 ≥8 内容字符即拒收。"""
    validate = semantic.alignment_validator([_claims()], ARC)
    bad = HookSemanticResponse.model_validate({'decisions': [_decision(
        status='arc_leak', reason='此句让读者提前知道她亲手烧掉了将军府这一收场')]})
    assert '复述了结局原文片段' in (validate(bad, WIRE) or '')

    clean = HookSemanticResponse.model_validate({'decisions': [_decision(
        status='arc_leak', reason='该命题与结局的收场动作语义等价，读者可提前确定结果')]})
    assert validate(clean, WIRE) is None


def test_arc_leak_requires_a_nonempty_arc_surface():
    validate = semantic.alignment_validator([_claims()], [])
    leak = HookSemanticResponse.model_validate(
        {'decisions': [_decision(status='arc_leak', reason='凭空判泄底')]})
    assert 'ground truth' in (validate(leak, WIRE) or '')


# ── outcomes：sha1 绑定、缺失/过期、uncertain 不洗绿 ─────────────────────────

def _art(*, approved: bool = False):
    cands = [SimpleNamespace(approach='identity_contrast', text=TEXT),
             SimpleNamespace(approach='result_flashback', text=TEXT_B),
             SimpleNamespace(approach='scene_cut', text=TEXT_C)]
    picked = (SimpleNamespace(approach='identity_contrast', text=TEXT)
              if approved else None)
    return SimpleNamespace(candidates=cands, approved=picked)


def _semantic_artifact(decisions: list[dict], *, coverage: str = 'complete',
                       judged_texts: dict | None = None) -> SimpleNamespace:
    texts = [_claims(coverage=coverage),
             HookTextClaims.model_validate({'label': 'result_flashback',
                                            'coverage': 'complete',
                                            'uncertainty_reason': None, 'claims': [],
                                            'text_sha1': semantic.text_sha1(TEXT_B)}),
             HookTextClaims.model_validate({'label': 'scene_cut',
                                            'coverage': 'complete',
                                            'uncertainty_reason': None, 'claims': [],
                                            'text_sha1': semantic.text_sha1(TEXT_C)})]
    return SimpleNamespace(
        judged_texts=judged_texts if judged_texts is not None else {
            'identity_contrast': semantic.text_sha1(TEXT),
            'result_flashback': semantic.text_sha1(TEXT_B),
            'scene_cut': semantic.text_sha1(TEXT_C),
        },
        texts=texts,
        decisions=[SimpleNamespace(**d) for d in decisions])


class _Store:
    def __init__(self, fresh: bool = True) -> None:
        self._fresh = fresh

    def freshness(self, _rel):
        return SimpleNamespace(state='fresh' if self._fresh else 'stale')


def _all_unver(outcomes, needle: str) -> bool:
    return (len(outcomes) == 2
            and all(o.unver_why is not None and needle in o.unver_why for o in outcomes)
            and not any(o.findings for o in outcomes))


def test_outcomes_are_unver_when_semantic_is_missing_stale_or_texts_unmatched(monkeypatch):
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: None)
    assert _all_unver(semantic.outcomes(_Store(), _art()), '没有 stage6/semantic_<n>.json')

    art = _semantic_artifact([_decision()])
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, art))
    assert _all_unver(semantic.outcomes(_Store(fresh=False), _art()), '上游已变化')

    partial = _semantic_artifact(
        [_decision()],
        judged_texts={'identity_contrast': semantic.text_sha1(TEXT)})
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, partial))
    got = semantic.outcomes(_Store(), _art())
    assert _all_unver(got, '没有被最近一次语义审判覆盖')
    assert 'result_flashback' in got[0].unver_why


def test_outcomes_bind_by_text_sha1_so_picking_a_candidate_does_not_stale_them(monkeypatch):
    """`--pick` 后多出「定稿·X」标签，但文本字节没变 ⇒ 语义结论仍然有效。"""
    art = _semantic_artifact([_decision()])
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, art))
    got = semantic.outcomes(_Store(), _art(approved=True))
    assert all(o.unver_why is None and not o.findings for o in got)


def test_arc_leak_materializes_as_hard_finding_with_hook_side_evidence_only(monkeypatch):
    art = _semantic_artifact([_decision(status='arc_leak', reason='语义等价于收场')])
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, art))
    spoiler, grounding = semantic.outcomes(_Store(), _art())
    assert spoiler.rule == 'hook_semantic_spoiler'
    assert len(spoiler.findings) == 1
    f = spoiler.findings[0]
    assert '[identity_contrast]' in f.detail and EVIDENCE in f.detail
    assert list(f.evidence) == [f'identity_contrast:{EVIDENCE}']
    for src in ARC:
        assert src not in f.detail, '审判结论把结局原文抄进了报告'
    assert grounding.unver_why is None and not grounding.findings


def test_invented_fact_materializes_on_the_info_rule(monkeypatch):
    art = _semantic_artifact([_decision(status='invented', reason='下发面与开篇均无此说法')])
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, art))
    spoiler, grounding = semantic.outcomes(_Store(), _art())
    assert not spoiler.findings and spoiler.unver_why is None
    assert grounding.rule == 'hook_semantic_grounding'
    assert len(grounding.findings) == 1
    assert '无据的事实' in grounding.findings[0].detail


def test_uncertain_text_cannot_be_laundered_into_clear(monkeypatch):
    """coverage=uncertain 时「干净」不成立（unver）；但**真命中永远报出来**。"""
    quiet = _semantic_artifact([_decision()], coverage='uncertain')
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, quiet))
    assert _all_unver(semantic.outcomes(_Store(), _art()), '不洗成通过')

    loud = _semantic_artifact(
        [_decision(status='arc_leak', reason='语义等价于收场')], coverage='uncertain')
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, loud))
    spoiler, grounding = semantic.outcomes(_Store(), _art())
    assert spoiler.findings, '已发现的泄底不许被 uncertain 藏起来'
    assert grounding.unver_why is not None


def test_unver_decision_blocks_clean_but_not_hits(monkeypatch):
    art = _semantic_artifact([_decision(status='unver', reason='蕴含关系不可靠')])
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _s: (1, art))
    got = semantic.outcomes(_Store(), _art())
    assert _all_unver(got, 'identity_contrast/c1')


# ── 前置闸：缺稿 / 过期不发起付费审判 ───────────────────────────────────────

def test_build_prompts_refuses_missing_or_stale_hook(monkeypatch):
    ctx = SimpleNamespace()
    monkeypatch.setattr(semantic, 'ArtifactStore',
                        lambda _ctx: SimpleNamespace(exists=lambda _rel: False))
    with pytest.raises(UpstreamMissing, match='先跑 stage6.hook'):
        semantic.build_prompts(ctx)

    stale = SimpleNamespace(exists=lambda _rel: True,
                            freshness=lambda _rel: SimpleNamespace(state='stale'))
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: stale)
    with pytest.raises(TerminalState, match='付费语义审判'):
        semantic.build_prompts(ctx)
