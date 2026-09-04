from __future__ import annotations

import copy
import hashlib

import pytest

from contracts.schema.evaluation import EvidenceRef, JudgeDecision
from novel.quality.evaluator import double_blind_decision
from novel.quality.global_review import (
    build_review, decide, review_status, subject_sha256,
)
from novel.errors import HumanEditProtected


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
    }


def test_global_review_enumerates_and_only_inherits_unchanged_subjects():
    first = build_review(_global())
    assert first.items
    assert {'canon.world_rules', 'canon.secrets'} <= {
        row.subject_path for row in first.items}
    target = first.items[0]
    approved = decide(
        first, item_id=target.item_id,
        expected_subject_sha256=target.subject_sha256,
        state='PASS', actor='reviewer-1', reason='按口径核对',
        now='2026-08-16T00:00:00Z')
    same = build_review(_global(), approved)
    assert next(row for row in same.items if row.item_id == target.item_id).state == 'PASS'

    changed = _global()
    changed['entities']['characters']['char_001']['name_distinctiveness'] = 'LOW'
    refreshed = build_review(changed, approved)
    path = 'entities.characters.char_001.name_distinctiveness'
    assert next(row for row in refreshed.items if row.subject_path == path).state == 'pending'


def test_empty_stage2_collections_still_require_explicit_completeness_decisions():
    grounding = _global()
    grounding['entities']['characters'] = {}
    grounding['entities']['relations'] = []
    grounding['canon']['world_rules'] = []
    grounding['canon']['secrets'] = []
    review = build_review(grounding)
    collection_items = [
        (row.kind, row.subject_path) for row in review.items
        if row.kind.endswith('_set_complete')
    ]
    assert collection_items == [
        ('character_set_complete', 'entities.characters'),
        ('relation_set_complete', 'entities.relations'),
        ('world_rule_set_complete', 'canon.world_rules'),
        ('secret_set_complete', 'canon.secrets'),
    ]
    assert all(
        row.state == 'pending' for row in review.items
        if row.kind.endswith('_set_complete'))


def _full_global():
    """把每一类风险面都填上的 grounding —— 覆盖面回归的**分母**。

    ⚠ 它比 `_global()` 全，是因为「都枚举到了吗」这个问题**只能拿什么都有的样本问**：
    用一份缺字段的 fixture 去问，答案永远是「枚举到了」。
    """
    return {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '甲', 'name_distinctiveness': 'HIGH',
                    'role': '客栈掌柜', 'fate': '后来远走他乡',
                    'role_evidence': _evidence('客栈掌柜'),
                    'fate_evidence': _evidence('后来远走他乡', 5),
                    'address': [{'context': 'address', 'surface': '阿甲',
                                 'by': 'public', 'from_chapter': 1}],
                },
                'char_002': {
                    'canonical': '乙', 'name_distinctiveness': 'LOW',
                    'role': '账房先生', 'fate': None,
                    'role_evidence': _evidence('账房先生'),
                    'fate_evidence': None,
                    'address': [{'context': 'reference', 'surface': '乙娘',
                                 'by': 'narration', 'from_chapter': 2}],
                },
            },
            'relations': [{
                'relation_id': 'rel_01', 'subject_id': 'char_001',
                'object_id': 'char_002', 'predicate': '雇佣',
                'predicate_evidence': _evidence('雇佣'),
                'visibility': {'kind': 'public', 'from_chapter': 1},
                'valid_from_chapter': 1, 'valid_through_chapter': None,
            }],
        },
        'canon': {
            'setting': {
                '时代背景': '架空王朝', 'era_evidence': _evidence('架空王朝'),
                '世界观': '城镇市井', 'worldview_evidence': _evidence('城镇市井'),
            },
            'world_rules': [{'rule_id': 'wr_01', 'kind': 'social',
                             'versions': [{'effective_from_chapter': 1,
                                           'desc': '宵禁按更鼓算',
                                           'desc_evidence': _evidence('宵禁按更鼓算')}]}],
            'secrets': [
                {'secret_id': 'secret_01', 'fact': '掌柜是前朝旧臣',
                 'fact_evidence': _evidence('掌柜是前朝旧臣'),
                 'knowledge_events': [{
                     'subject': {'kind': 'character', 'char_id': 'char_001'},
                     'known_from_chapter': 1,
                     'evidence': _evidence('掌柜自己知道'),
                 }],
                 'setup': {'kind': 'foreshadowed', 'chapters': [1, 2]},
                 'first_reveal_chapter': 5},
                # 冷揭示：显式声明没有埋点。⚠ 它与「归一裁空后降级成 cold」
                # 在字段值上仍然同形，分得开的只有 `setup_chapters_pruned` 那份账。
                {'secret_id': 'secret_02', 'fact': '账房识得那枚印',
                 'fact_evidence': _evidence('账房识得那枚印', 3),
                 'knowledge_events': [{
                     'subject': {'kind': 'character', 'char_id': 'char_002'},
                     'known_from_chapter': 2,
                     'evidence': _evidence('账房早已识得', 2),
                 }, {
                     'subject': {'kind': 'group', 'label': '在场众人'},
                     'known_from_chapter': 3,
                     'evidence': _evidence('众人当场看见', 3),
                 }],
                 'setup': {'kind': 'cold'},
                 'first_reveal_chapter': 3},
            ],
        },
        'arc': {
            'conflict_stages': [{'stage': '身份败露', 'chapters': [1, 2, 3, 4, 5]}],
            'climax': '第五章对质',
            'ending': '掌柜远走',
            'arc_tracks': [{
                'subject': 'char_001', 'trajectory': '由藏到认',
                'trajectory_evidence': _evidence('由藏到认', 5),
            }],
        },
    }


def test_every_risk_bearing_stage2_subject_is_enumerated_for_review():
    """`R-2.17`：没被枚举出来的字段等于没人审。

    ⭐ 这一条守的是 `R-2.16` 那道硬闸的**分母**。闸本身只会说「每一项都 PASS」，
    而「项」有多少是这里决定的——少枚举一类，闸照样开，人却从没看过它。
    历史上正是这样漏掉四处已知风险面：`role` 的结局旁路（词表实测 0/17 而人读
    真违规 6/17）、`setting.世界观` 的泄底（逐章字面闸门实测 0/8）、relations
    这条第二剧透通道，以及两道下发闸门共同的前件**埋点**。
    """
    review = build_review(_full_global())
    by_kind: dict[str, list[str]] = {}
    for row in review.items:
        by_kind.setdefault(row.kind, []).append(row.subject_path)

    # 事实逐条：项数必须**随样本规模走**。断言成「有就行」会让这条守卫在
    # 只枚举了第一个角色的实现上照样绿。
    assert sorted(by_kind['role']) == [
        'entities.characters.char_001.role', 'entities.characters.char_002.role']
    assert sorted(by_kind['fate']) == [
        'entities.characters.char_001.fate', 'entities.characters.char_002.fate']
    assert by_kind['relation'] == ['entities.relations.rel_01']
    assert sorted(by_kind['setting_fact']) == [
        'canon.setting.世界观', 'canon.setting.时代背景']
    assert sorted(by_kind['secret_setup']) == [
        'canon.secrets.secret_01.setup',
        'canon.secrets.secret_02.setup']
    assert sorted(by_kind['knowledge_event']) == [
        'canon.secrets.secret_01.knowledge_events.0',
        'canon.secrets.secret_02.knowledge_events.0',
        'canon.secrets.secret_02.knowledge_events.1',
    ]

    # 解读层分组：整份一项，不逐条拆——单拆一条判不出对错。
    assert by_kind['conflict_partition'] == ['arc.conflict_stages']
    assert by_kind['arc_analysis'] == ['arc']


def test_review_kind_vocabulary_matches_what_enumeration_produces():
    """取值面与产出面必须对齐，两个方向都要。

    ⚠ 「枚举得出而取值面收不下」是**落盘那一刻才炸**的一对：那时这一轮 stage2
    已经跑完（付费）。反方向「声明了却没人产」则是一条永远不会出现的分支，
    读的人会以为那个主体有人审。
    """
    from typing import get_args

    from contracts.schema.evaluation import GlobalReviewItem

    declared = set(get_args(GlobalReviewItem.model_fields['kind'].annotation))
    produced = {row.kind for row in build_review(_full_global()).items}
    assert produced == declared - {'known_by'}, (
        f'除兼容旧 review v1 的 known_by 外只声明未产出：'
        f'{sorted(declared - produced - {"known_by"})}；'
        f'只产出未声明：{sorted(produced - declared)}')

    # GlobalReview 本身仍是 v1；旧盘上的 known_by 行必须继续能严格解码，
    # 但当前 producer 不再从无时间维的 character_known_by 造这种行。
    legacy = GlobalReviewItem.model_validate({
        'item_id': 'known_by:canon.secrets.secret_01.character_known_by:abc',
        'kind': 'known_by',
        'subject_path': 'canon.secrets.secret_01.character_known_by',
        'subject_sha256': 'a' * 64,
        'state': 'pending',
        'evidence': [],
    })
    assert legacy.kind == 'known_by'


@pytest.mark.parametrize(('trail', 'subject_path'), [
    (('entities', 'characters', 'char_001', 'role_evidence'),
     'entities.characters.char_001.role'),
    (('entities', 'characters', 'char_001', 'fate_evidence'),
     'entities.characters.char_001.fate'),
    (('entities', 'relations', 0, 'predicate_evidence'),
     'entities.relations.rel_01'),
    (('canon', 'setting', 'era_evidence'), 'canon.setting.时代背景'),
    (('canon', 'setting', 'worldview_evidence'), 'canon.setting.世界观'),
    (('canon', 'world_rules', 0, 'versions', 0, 'desc_evidence'),
     'canon.world_rules.wr_01.versions.0'),
    (('canon', 'secrets', 0, 'fact_evidence'), 'canon.secrets.secret_01.fact'),
    (('canon', 'secrets', 0, 'knowledge_events', 0, 'evidence'),
     'canon.secrets.secret_01.knowledge_events.0'),
    (('arc', 'arc_tracks', 0, 'trajectory_evidence'), 'arc'),
])
def test_each_free_fact_hash_binds_its_formal_evidence(trail, subject_path):
    grounding = _full_global()
    before = next(
        row.subject_sha256 for row in build_review(grounding).items
        if row.subject_path == subject_path)
    changed = copy.deepcopy(grounding)
    cursor = changed
    for segment in trail[:-1]:
        cursor = cursor[segment]
    cursor[trail[-1]] = _evidence('换了一段正式证据', 2)
    after = next(
        row.subject_sha256 for row in build_review(changed).items
        if row.subject_path == subject_path)
    assert after != before


def test_free_fact_hash_uses_claim_evidence_pair_and_completeness_strips_evidence():
    grounding = _full_global()
    review = build_review(grounding)
    role = next(row for row in review.items
                if row.subject_path == 'entities.characters.char_001.role')
    character = grounding['entities']['characters']['char_001']
    assert role.subject_sha256 == subject_sha256({
        'claim': character['role'], 'evidence': character['role_evidence'],
    })

    changed = copy.deepcopy(grounding)
    changed['entities']['characters']['char_001']['role_evidence'] = \
        _evidence('人物换证据', 2)
    changed['entities']['relations'][0]['predicate_evidence'] = \
        _evidence('关系换证据', 2)
    changed['canon']['world_rules'][0]['versions'][0]['desc_evidence'] = \
        _evidence('规则换证据', 2)
    changed['canon']['secrets'][0]['fact_evidence'] = _evidence('秘密换证据', 2)
    before = {row.kind: row.subject_sha256 for row in review.items
              if row.kind.endswith('_set_complete')}
    after = {row.kind: row.subject_sha256 for row in build_review(changed).items
             if row.kind.endswith('_set_complete')}
    assert after == before

    approved = review
    for item in [row for row in review.items if row.kind.endswith('_set_complete')]:
        approved = decide(
            approved,
            item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='PASS', actor='reviewer-1', reason='通读全书核过集合完整性',
            now='2026-08-24T00:00:00Z')
    refreshed = build_review(changed, approved)
    assert all(row.state == 'PASS' for row in refreshed.items
               if row.kind.endswith('_set_complete'))


def test_a_null_fate_still_requires_an_explicit_human_decision():
    """据实留空是允许的，但「确实没有」这句话得有人说。

    `fate` 与 `role` 拆成两个字段的全部意义是它**不下发给写手**；空着不复核，
    就分不出「这个人真没结局」与「抽的时候漏了」——而后者会让写手认不全这个人。
    """
    review = build_review(_full_global())
    row = next(r for r in review.items
               if r.subject_path == 'entities.characters.char_002.fate')
    assert row.state == 'pending'

    # 空值同样有确定的主体摘要 ⇒ 决定能被 hash 绑住，改成非空即失效。
    changed = _full_global()
    changed['entities']['characters']['char_002']['fate'] = '后来接手了客栈'
    changed['entities']['characters']['char_002']['fate_evidence'] = \
        _evidence('后来接手了客栈', 5)
    refreshed = next(r for r in build_review(changed).items
                     if r.subject_path == 'entities.characters.char_002.fate')
    assert refreshed.subject_sha256 != row.subject_sha256


def test_widening_the_review_surface_keeps_unrelated_decisions():
    """补齐覆盖面不许把已有的人工决定冲掉。

    本次补齐的存量代价恰好是零（盘上那本书当时 65 项全 pending），但继承逻辑
    本身必须扛得住「面变大了」——否则下一次加主体会静默作废一批已裁决的项，
    而那种作废在产物里看不出来：项还在，只是回到了 `pending`。
    """
    grounding = _full_global()
    review = build_review(grounding)
    target = next(r for r in review.items if r.kind == 'secret')
    approved = decide(
        review, item_id=target.item_id,
        expected_subject_sha256=target.subject_sha256,
        state='PASS', actor='reviewer-1', reason='回原文核过',
        now='2026-08-23T00:00:00Z')

    # 只动一处**不相干**的主体：那条被批准的 secret 必须原样带着 PASS 过来。
    grounding['arc']['climax'] = '改在第四章对质'
    refreshed = build_review(grounding, approved)
    carried = next(r for r in refreshed.items if r.item_id == target.item_id)
    assert (carried.state, carried.actor) == ('PASS', 'reviewer-1')
    assert next(r for r in refreshed.items
                if r.kind == 'arc_analysis').state == 'pending'


def test_review_approval_recomputes_complete_current_subject_and_required_surface():
    grounding = _global()
    review = build_review(grounding)
    for item in list(review.items):
        review = decide(
            review, item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='PASS', actor='reviewer-1', reason='逐项核对',
            now='2026-08-16T00:00:00Z')
    current_sha, current, approved = review_status(grounding, review)
    assert current_sha == review.source_sha256
    assert current and approved

    # Copying the full-subject hash cannot hide a newly required or omitted row.
    forged = review.model_copy(update={'items': review.items[1:]})
    assert review_status(grounding, forged)[1:] == (False, False)

    # `address[].surface` 没有独立语义复核项（有的是 address 的 `context`）。集合完整性
    # 只绑定成员 ID；若把任意子字段变化都并进集合 hash，改一个已成功主体会重开整张
    # 全书阅读卡，Commander 的增量审阅就会退化为全量重审。
    edited = _global()
    edited['entities']['characters']['char_001']['address'][0]['surface'] = '甲郎'
    assert review_status(edited, review)[1:] == (False, False)

    # 重建 current surface 后，所有真实语义主体 identity 都没变，故已有决定完整继承；
    # source_sha256 的变化仍保证旧 review 本身不能被直接冒充 current。
    refreshed = build_review(edited, review)
    reopened = [item for item in refreshed.items if item.state == 'pending']
    assert reopened == []
    assert review_status(edited, refreshed)[1:] == (True, True)


def test_global_review_rejects_stale_and_unaudited_decisions():
    review = build_review(_global())
    target = review.items[0]
    with pytest.raises(HumanEditProtected, match='stale'):
        decide(
            review, item_id=target.item_id, expected_subject_sha256='0' * 64,
            state='PASS', actor='reviewer', reason='ok')
    with pytest.raises(ValueError, match='actor'):
        decide(
            review, item_id=target.item_id,
            expected_subject_sha256=target.subject_sha256,
            state='PASS', actor=' ', reason='ok')
