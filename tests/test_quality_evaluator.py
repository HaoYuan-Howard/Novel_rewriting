from __future__ import annotations

import copy
import hashlib

import pytest

from contracts.schema.evaluation import EvidenceRef, JudgeDecision
from novel.quality.evaluator import double_blind_decision


_H = hashlib.sha256(b'x').hexdigest()


def _judge(name='a', verdict='PASS', evidence=True):
    refs = [EvidenceRef(artifact='stage5/deep/ch01.json', subject_sha256=_H,
                        start=0, end=2)] if evidence else []
    return JudgeDecision(judge=name, verdict=verdict, evidence=refs, reason='fixed rubric')


def _evidence(text: str = '原文证据', chapter: int = 1) -> dict:
    return {
        'chapter_ordinal': chapter,
        'source': {
            'text': text, 'start': 0, 'end': len(text),
        },
    }


@pytest.mark.parametrize(('first', 'second', 'why'), [
    (_judge(), _judge('b'), 'A/B 同判 PASS'),
    (_judge(verdict='FAIL'), _judge('b', 'FAIL'), 'A/B 同判 FAIL'),
    (_judge(), _judge('b', 'FAIL'), '结论不一致'),
    (_judge(verdict='UNVER', evidence=False), _judge('b'), '明确判为 UNVER'),
    (_judge(evidence=False), _judge('b'), '缺少可闭合证据'),
])
def test_the_merged_verdict_is_always_unver_and_the_reason_is_never_the_same(
        first, second, why):
    """本仓没有自动放行门 ⇒ 合并决议**恒** UNVER。

    ⚠ 这条守卫的重点在第二个断言：五种情形的 `reason` **必须逐句不同**。
    如果它们被压成一句套话，人在 inbox 里就分不出「两名判官都说没问题」与
    「判官压根没给证据」——而那两件事的处置完全相反。
    """
    decision = double_blind_decision(
        ability='reference_boundary', subject_key='case-1',
        first=first, second=second)
    assert decision.verdict == 'UNVER'
    assert why in decision.reason, decision.reason


def test_merged_evidence_is_the_deduplicated_union_of_both_judges():
    decision = double_blind_decision(
        ability='reference_boundary', subject_key='case-1',
        first=_judge(), second=_judge('b'))
    # 两名判官指的是同一段字节 ⇒ 并集去重后只剩一条，而不是重复两条。
    assert len(decision.evidence) == 1
    assert [judge.verdict for judge in decision.judges] == ['PASS', 'PASS']


def test_an_automated_pass_is_not_expressible_at_all():
    """⚠ 类型上就踩不进去：`SemanticDecision.verdict` 是 `Literal['UNVER']`。

    这比「有一条校验器拦着」强——一条永远不可能被触发的校验器会绿一辈子，
    而没人知道它到底还在不在。
    """
    from pydantic import ValidationError

    from contracts.schema.evaluation import SemanticDecision

    with pytest.raises(ValidationError):
        SemanticDecision(
            ability='reference_boundary', subject_key='case-1', verdict='PASS',
            evidence=[], reason='两名判官都说没问题',
            judges=[_judge(), _judge('b')])


def _global():
    return {
        'entities': {'characters': {
            'char_001': {
                'name_distinctiveness': 'HIGH', 'role': '静态身份',
                'role_evidence': _evidence('静态身份'),
                'fate': None, 'fate_evidence': None,
                'address': [{'context': 'address', 'surface': '阿甲'}],
            },
        }},
        'canon': {
            'setting': {
                '时代背景': '架空王朝', 'era_evidence': _evidence('架空王朝'),
                '世界观': '城镇市井', 'worldview_evidence': _evidence('城镇市井'),
            },
            'world_rules': [{'rule_id': 'wr_01', 'kind': 'social',
                             'versions': [{'effective_from_chapter': 1,
                                           'desc': '规矩',
                                           'desc_evidence': _evidence('规矩')}]}],
            'secrets': [{'secret_id': 'secret_01', 'fact': '真相',
                         'fact_evidence': _evidence('真相'),
                         'knowledge_events': [{
                             'subject': {'kind': 'character', 'char_id': 'char_001'},
                             'known_from_chapter': 1,
                             'evidence': _evidence('知情'),
                         }],
                         'setup': {'kind': 'cold'},
                         'first_reveal_chapter': 2}],
        },
        'source_blurb': {'facts': [], 'opening': {
            'kind': 'proposed', 'through_chapter': 1, 'rationale': '首章交代起点',
            'boundary_evidence': _evidence('开篇事件')}},
    }
