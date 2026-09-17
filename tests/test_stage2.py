#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 当前判据与每条 HARD 的反例。

正式底座由 `characters.compile_response` + `compile_global_draft` 唯一纯编译路径
产出，再分别构造 Global、完整剧情、证据、人物许可和状态失效。
旧事实/hinge 的定位、归属与缺料风险迁至 test_stage2_plot_compiler、
plot_constraints、material_dialogue_flow；语义覆盖不由数量阈值或证据比例证明。
"""
from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from contracts.rules import REGISTRY
from novel.stages.stage2 import checks


CH1 = '\n\n'.join([
    '春风吹过老宅的院墙。',
    '李宝珠站在廊下看着落花。',
    '她想起了昨夜那封信。',
    '信上说父亲已经回城。',
    '她把信折好收进袖中。',
    '远处传来更夫的梆子声。',
])
CH2 = '\n\n'.join([
    '第二日清晨天色微亮。',
    '李宝珠推开院门往外走。',
    '街上已经有小贩在吆喝。',
    '她买了两个热腾腾的包子。',
    '回来时看见母亲在门口等她。',
    '其实母亲早就知道那封信的事。',
])
CH3 = '\n\n'.join([
    '午后的日头晒得人发懒。',
    '李宝珠把账册摊在桌上一页页翻。',
    '管家在门外回话说米价又涨了。',
    '她皱着眉让管家去问问别家。',
    '窗外的槐树落了一地碎叶。',
    '她忽然想起父亲从前也这样翻账。',
    '那时她还够不着桌子。',
    '母亲进来给她添了一盏茶。',
    '茶是新沏的还烫着。',
    '她端起来吹了吹没有喝。',
])


def _source(text: str, a: str, b: str | None = None) -> dict[str, Any]:
    start = text.index(a)
    end_probe = b or a
    end = text.index(end_probe, start) + len(end_probe)
    raw = text[start:end]
    return {'text': raw, 'start': start, 'end': end}


def _evidence(chapter: int, a: str, b: str | None = None) -> dict[str, Any]:
    """正式 GlobalGrounding 的证据形：章号 + 可逐字复验的 SourceSpan。"""
    text = (CH1, CH2, CH3)[chapter - 1]
    return {'chapter_ordinal': chapter, 'source': _source(text, a, b)}


def main_text() -> list[dict[str, Any]]:
    return [{'chapter_ordinal': i, 'text': t}
            for i, t in enumerate((CH1, CH2, CH3), start=1)]


def global_grounding() -> dict[str, Any]:
    """合成领域视图；只供 `tests.stage2_fixtures` 显式投影为两份独立正式产物。"""
    return {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '李宝珠',
                    'address': [{'surface': '李宝珠', 'by': 'narration',
                                 'context': 'reference', 'from_chapter': 1}],
                    'name_distinctiveness': 'high',
                    'role': '沈家的独女，替父亲掌管家中账目',
                    'role_evidence': _evidence(3, '李宝珠把账册摊在桌上一页页翻。'),
                    'fate': None,
                    'fate_evidence': None,
                },
                'char_002': {
                    'canonical': '沈母',
                    'address': [{'surface': '母亲', 'by': 'char_001',
                                 'context': 'reference', 'from_chapter': 2}],
                    'name_distinctiveness': 'medium',
                    'role': '李宝珠的娘，管着老宅内院的日常',
                    'role_evidence': _evidence(2, '回来时看见母亲在门口等她。'),
                    'fate': None,
                    'fate_evidence': None,
                },
            },
            'relations': [{'relation_id': 'rel_01', 'subject_id': 'char_001',
                           'object_id': 'char_002', 'predicate': '母女',
                           'predicate_evidence': _evidence(
                               2, '回来时看见母亲在门口等她。'),
                           'visibility': {'kind': 'public', 'from_chapter': 1},
                           'valid_from_chapter': 1, 'valid_through_chapter': None}],
        },
        'canon': {
            'setting': {'时代背景': '架空古代江南的一座小城',
                        'era_evidence': _evidence(1, '春风吹过老宅的院墙。'),
                        '世界观': '寻常人家的宅院生活，没有神怪，规矩由宗族与官府共同维持',
                        'worldview_evidence': _evidence(2, '街上已经有小贩在吆喝。')},
            'world_rules': [{'rule_id': 'wr_01', 'kind': 'social_norm',
                             'versions': [{'effective_from_chapter': 1,
                                            'desc': '城中入夜有宵禁，更夫敲过梆子之后不许出门走动',
                                            'desc_evidence': _evidence(
                                                1, '远处传来更夫的梆子声。')}]}],
            'secrets': [{'secret_id': 'secret_01',
                         'fact': '父亲早已回城却一直不肯露面',
                         'fact_evidence': _evidence(1, '信上说父亲已经回城。'),
                         'knowledge_events': [
                             {'subject': {'kind': 'character', 'char_id': 'char_001'},
                              'known_from_chapter': 1,
                              'evidence': _evidence(1, '她想起了昨夜那封信。')},
                             {'subject': {'kind': 'group', 'label': '知情的沈家人'},
                              'known_from_chapter': 2,
                              'evidence': _evidence(2, '其实母亲早就知道那封信的事。')},
                         ],
                         'setup': {'kind': 'foreshadowed', 'chapters': [1]},
                         'first_reveal_chapter': 2}],
        },
        'source_blurb': {
            'facts': [{'kind': 'supported', 'claim_id': 'hook_001',
                       'claim': '父亲已经回城',
                       'evidence': _evidence(1, '信上说父亲已经回城。')}],
            'opening': {'kind': 'proposed', 'through_chapter': 1,
                        'rationale': '来信引出连续开篇事件',
                        'boundary_evidence': _evidence(1, '远处传来更夫的梆子声。')},
        },
        '_provenance': {
            'setup_chapters_pruned': [],
            'evidence_locator_failures': [],
        },
    }


def _formal() -> tuple[dict[str, Any], dict[str, Any]]:
    """把合成领域视图经真实编译路径铸成两份独立正式产物（不写盘、不调模型）。"""
    from tests.stage2_fixtures import formal_subjects
    registry, knowledge = formal_subjects(view=None)
    return (registry.model_dump(mode='json', by_alias=True),
            knowledge.model_dump(mode='json', by_alias=True))


def character_registry() -> dict[str, Any]:
    """正式 CharacterRegistry：与 knowledge_grounding 同一次编译，共享候选表。"""
    return _formal()[0]


def knowledge_grounding() -> dict[str, Any]:
    """正式 KnowledgeGrounding：四类长期知识 + 共享 evidence_anchors。"""
    return _formal()[1]


def blueprints() -> dict[str, Any]:
    """共享三章当前正式形状；原文与 Global 夹具不变，不生成旧事实适配层。"""
    from tests.stage2_fixtures import plot_blueprints
    return plot_blueprints()


def _hard_hits(gg: dict | None, chars: dict, bp: dict | None,
               mt: list | None) -> set[str]:
    """跑一遍判据 → **命中的 HARD 判据 id**。

    ⚠ 只收 `sev == 'hard'` 且**真有 findings** 的：`unver` 不算命中
    （那是判据的边界，不是数据的问题），度量更不算（它无条件产出）。
    """
    sev = {r.rule: r.sev for r in REGISTRY.all() if r.stage == 'stage2'}
    return {o.rule for o in checks.check(global_g=gg, blueprints=bp, main_text=mt,
                                         characters=chars)
            if sev.get(o.rule) == 'hard' and o.findings and o.unver_why is None}


# ─────────────────────────────────────────────────────────────────────────────
# 二、底座自己
# ─────────────────────────────────────────────────────────────────────────────

def test_the_clean_base_has_no_hard_hits():
    """⭐ **全部故障样本建立在这一条上。**

    一个本来就红的底座上，「改一处就红」什么也证明不了。
    """
    got = _hard_hits(knowledge_grounding(), character_registry(),
                     blueprints(), main_text())
    assert got == set(), f'干净底座上不该有任何 HARD 命中，实得 {sorted(got)}'


def test_real_stage2_report_separates_instance_detail_from_stable_rule_notes():
    from novel.prov import FreshnessReport
    from novel.report import build, render
    bp = blueprints()
    bp['blueprints'][0]['evidence_anchors'][0]['start'] += 1
    report = build(sid='7615069877019300633', node='stage2.blueprint',
        freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=checks.check(
            global_g=knowledge_grounding(), blueprints=bp, main_text=main_text(),
            characters=character_registry()))
    lines = {line.rule: line for line in report.rules}
    anchor, shape = lines['plot_evidence'], lines['plot_shape']
    assert anchor.findings and '坐标与当前段落索引不一致' in anchor.findings[0].detail
    assert '不以覆盖率证明' not in anchor.findings[0].detail
    assert '不以覆盖率证明' in anchor.note
    assert shape.findings and 'beats' in shape.findings[0].detail
    assert 'carry_out' in shape.findings[0].detail
    assert 'same_beat_ended' in shape.findings[0].detail
    assert '不设节点配额' in shape.note
    rendered = render(report)
    assert f'说明：{anchor.note}' in rendered and f'说明：{shape.note}' in rendered
    assert '**' not in anchor.model_dump_json() and '`' not in anchor.model_dump_json()


def test_hedge_words_distinguish_definite_impossibility_and_field_effect():
    """「不可能」是确定判断；「不太可能」与双重否定重新打开可能性。"""
    chars = character_registry()
    chars['characters']['char_001']['role'] = '家中长女，不太可能离开家业。'
    chars['characters']['char_002']['canonical'] = '账房（疑似沈家的）'
    outcomes = {outcome.rule: outcome for outcome in checks.check(
        global_g=knowledge_grounding(), blueprints=blueprints(),
        main_text=main_text(), characters=chars)}
    assert outcomes['hedge_words'].findings

    # 「不可能」不因子串「可能」误报；「或」的并列选择也不是模棱语。
    for text in ('家中长女，不可能离开家业。', '御医建议过继或让父母再生。'):
        chars = character_registry()
        chars['characters']['char_001']['role'] = text
        outcomes = {outcome.rule: outcome for outcome in checks.check(
            global_g=knowledge_grounding(), blueprints=blueprints(),
            main_text=main_text(), characters=chars)}
        assert not outcomes['hedge_words'].findings, text

    # 双重否定把可能性打开，仍然是不确定。
    chars = character_registry()
    chars['characters']['char_001']['role'] = '家中长女，并非不可能离开家业。'
    outcomes = {outcome.rule: outcome for outcome in checks.check(
        global_g=knowledge_grounding(), blueprints=blueprints(),
        main_text=main_text(), characters=chars)}
    assert outcomes['hedge_words'].findings


def test_role_fate_wordlist_remains_hard_because_role_reaches_writer():
    from contracts.invariants import review_effect_of

    chars = character_registry()
    chars['characters']['char_001']['role'] = '沈家账房，最终下狱'
    outcome = next(outcome for outcome in checks.check(
        global_g=knowledge_grounding(), blueprints=blueprints(),
        main_text=main_text(), characters=chars)
        if outcome.rule == 'role_leaks_fate')

    rule = REGISTRY.get('role_leaks_fate')
    assert outcome.findings, '转抄式结局词必须在进入 writer context 前阻断'
    assert rule.sev == 'hard'
    assert rule.fix == ('stage2.characters', '--resample')
    assert 'role_leaks_fate' in _hard_hits(
        knowledge_grounding(), chars, blueprints(), main_text())
    assert review_effect_of('role') == 'blocking', '机械闸与 Stage4 前人审应共同保护 role'


def test_few_complete_beats_do_not_create_a_quota_or_semantic_pass():
    bp = blueprints()
    chapter = _ch(bp, 3)
    complete = '；'.join(row['content'] for row in chapter['beats'])
    evidence = sorted({ref for row in chapter['beats'] for ref in row['evidence_refs']})
    chapter['beats'] = [{**chapter['beats'][0], 'content': complete, 'evidence_refs': evidence}]
    outcomes = {o.rule: o for o in checks.check(
        global_g=knowledge_grounding(), blueprints=bp, main_text=main_text(),
        characters=character_registry())}
    assert outcomes['plot_shape'].findings
    assert REGISTRY.get('plot_shape').sev == 'metric'
    assert REGISTRY.get('plot_shape').fix is None
    assert outcomes['plot_semantics'].unver_why
    assert not _hard_hits(knowledge_grounding(), character_registry(),
                          bp, main_text())


def test_the_clean_base_leaves_no_rule_unimplemented():
    """**注册了而没有实现调用它 ⇒ `skipped`，徽章至少 amber。**

    这一条盯的是 `checks.check()` 恰好覆盖全部现役 `Outcome`——
    报告是由**判据注册表**生成的，不是由实现的输出生成的，
    所以漏实现一条不会让它从报告里消失，只会让它挂一行「实现未被调用」。
    """
    outs = checks.check(global_g=knowledge_grounding(), blueprints=blueprints(),
                        main_text=main_text(), characters=character_registry())
    want = {r.rule for r in REGISTRY.all() if r.stage == 'stage2'}
    assert {o.rule for o in outs} == want
    assert len(outs) == len(want)


@pytest.mark.parametrize('missing_main_text', [None, []])
@pytest.mark.parametrize('recovery_record', ['missing', 'known_clear', 'known_failure'])
def test_missing_main_text_preserves_unver_and_original_recovery_evidence(
        missing_main_text, recovery_record):
    from contracts.schema.stage2 import BlueprintScopeViolation
    from novel.prov import FreshnessReport
    from novel.report import build
    bp = blueprints()
    if recovery_record == 'missing':
        bp['_provenance'].pop('scope_violations')
    elif recovery_record == 'known_failure':
        bp['_provenance']['scope_violations'] = [BlueprintScopeViolation(
            batch=1, requested=[1], produced=[1, 5], discarded_chapters=[5],
            duplicate_target=0).model_dump_json()]
    outcomes = checks.check(global_g=knowledge_grounding(), blueprints=bp,
                            main_text=missing_main_text,
                            characters=character_registry())
    registered = {row.rule for row in REGISTRY.all() if row.stage == 'stage2'}
    assert {row.rule for row in outcomes} == registered and len(outcomes) == len(registered)
    report = build(sid='123', node='stage2.blueprint',
        freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outcomes)
    lines = {line.rule: line for line in report.rules}
    assert all(line.status != 'skipped' for line in lines.values())
    for rule in ('plot_evidence', 'pronoun_surface_unattested', 'present_ids_dangling'):
        assert lines[rule].status == 'unver' and not lines[rule].findings
    assert lines['plot_contract'].status == 'ok'
    assert lines['plot_semantics'].status == 'unver'
    expected = {'missing': 'hit', 'known_clear': 'ok', 'known_failure': 'hit'}
    assert lines['scope_violation'].status == expected[recovery_record]
    if recovery_record == 'known_failure':
        finding = lines['scope_violation'].findings[0]
        assert finding.output_ordinal == 1
        assert 'discarded_chapters' in finding.detail and '5' in finding.detail


def test_global_field_failures_route_to_their_typed_producers():
    routed = {rule.rule for rule in REGISTRY.all()
              if rule.stage == 'stage2' and rule.fix == ('stage2.global', '--resample')}
    assert routed == {'relation_dangling'}
    renormalized = {rule.rule for rule in REGISTRY.all()
                    if rule.stage == 'stage2'
                    and rule.fix == ('stage2.global', '--renormalize')}
    assert renormalized == {
        'canon_chapter_refs_dangling', 'global_core_contract',
        'global_temporal_contract', 'global_evidence_unlocated',
    }
    character_routed = {rule.rule for rule in REGISTRY.all()
                        if rule.stage == 'stage2' and rule.fix == ('stage2.characters', '--resample')}
    assert character_routed == {
        'character_unattested', 'address_unlocatable', 'address_from_out_of_range',
        'address_bare_pronoun', 'role_missing', 'role_repeats_name',
        'role_leaks_fate', 'hedge_words',
    }


def test_the_two_unver_rules_always_speak():
    """两条 `unver` **每本每次都产**，与别的判据报绿还是报红无关。

    ⚠ 语义退役后它们登记于 `novel/admission.py::SEMANTIC_UNREVIEWED_RULES`，
    只披露不阻断；这里守的是「无论数据红绿，披露行都在」。
    """
    mutated = character_registry()
    mutated['characters']['char_001']['role'] = '她最终沦为妾室'
    for chars in (character_registry(), mutated):
        outs = {o.rule: o for o in checks.check(
            global_g=knowledge_grounding(), blueprints=blueprints(),
            main_text=main_text(), characters=chars)}
        assert outs['role_fate_unverifiable'].unver_why
        assert outs['secrets_absent'].unver_why


# ─────────────────────────────────────────────────────────────────────────────
# 三、现役 HARD —— 每条保留会触发它的反例
# ─────────────────────────────────────────────────────────────────────────────

Mutator = Callable[[dict, dict, dict, list], Any]


def _with(fn: Mutator) -> tuple[dict | None, dict, dict | None, list | None]:
    """在底座的**深拷贝**上改一处 → `(gg, registry, bp, mt)`。

    改坏原件会让「改一处」变成「改了前面每一处」，而那种串味只在**测试顺序**变了
    之后才现形——最难查的一类。
    """
    gg, chars = knowledge_grounding(), character_registry()
    bp, mt = blueprints(), main_text()
    got = fn(copy.deepcopy(gg), copy.deepcopy(chars), copy.deepcopy(bp), copy.deepcopy(mt))
    return got if isinstance(got, tuple) else (gg, chars, bp, mt)


def _m(fn: Callable[[dict, dict, dict, list], None]) -> Mutator:
    """把一个「就地改」的函数包成返回四元组的 mutator。"""
    def wrapped(gg: dict, chars: dict, bp: dict, mt: list):
        fn(gg, chars, bp, mt)
        return gg, chars, bp, mt
    return wrapped


def _ch(bp: dict, o: int) -> dict:
    return next(x for x in bp['blueprints'] if x['chapter_ordinal'] == o)


#: `(判据 id, 改哪一处, 为什么这一处会让它红)`；一个规则可有多种独立失败面。
CASES: list[tuple[str, Mutator, str]] = [
    # ── 一、产物完整性与章对齐 ──────────────────────────────────────────
    ('upstream_missing', lambda g, r, b, m: (None, r, b, m),
     'Global 缺一份。⚠ 判 FAIL 不判 note：什么都没查却打 PASS 是把两件事混为一谈'),
    ('section_id_leak', _m(lambda g, r, b, m: _ch(b, 1).__setitem__('section_id', 'sec_0001')),
     '章的唯一键全链只有一个，混进 section_id 之后下游按哪个键 join 要读代码才知道'),
    ('chapter_coverage', _m(lambda g, r, b, m: b['blueprints'].pop()),
     '少一章 blueprint'),
    ('chapter_order', _m(lambda g, r, b, m: b['blueprints'].reverse()),
     '章序倒过来。⚠ 集合对而顺序错，是分批产出年代的典型残留'),
    # ── 二、独立人物登记 ────────────────────────────────────────────────
    ('character_unattested',
     _m(lambda g, r, b, m: r['characters']['char_002'].__setitem__('first_chapter', 3)),
     '首次出场章没有对应锚点 ⇒ 无法证明这个人物真的在原文出现过'),
    ('address_from_out_of_range',
     _m(lambda g, r, b, m: r['characters']['char_002']['aliases'][0]
        .__setitem__('identity_revealed_from', 99)),
     '称谓披露章指到正文集合外 ⇒ 这条称呼永不下发，产物里却看着一切正常'),
    ('address_unlocatable',
     _m(lambda g, r, b, m: r['characters']['char_001']['aliases'][0]
        .__setitem__('surface', '原文没有的叫法')),
     '归属证据里找不到登记称谓 ⇒ 称谓与证据脱钩，下游拿到凭空的叫法'),
    ('role_missing',
     _m(lambda g, r, b, m: r['characters']['char_001'].__setitem__('role', '')),
     '下游只下发 role，缺了写手不认识这个人。'
     '⚠⚠ **这条反例只有在判据吃 dict 时才构造得出来**——载体上它是 `min_length=1`'),
    ('role_repeats_name',
     _m(lambda g, r, b, m: r['characters']['char_001'].__setitem__(
         'role', r['characters']['char_001']['canonical'])),
     'role 与 canonical 完全同字 ⇒ 写手拿到的是「李宝珠：李宝珠」'),
    ('hedge_words',
     _m(lambda g, r, b, m: r['characters']['char_001'].__setitem__(
         'role', '疑似是沈家账房')),
     'role 会下发写手，「疑似」说明静态身份没有抽到底'),
    # ── 三、Global 四类知识与共享证据 ──────────────────────────────────
    ('global_core_contract',
     _m(lambda g, r, b, m: g['arc_secrets'][0].__setitem__('fact', None)),
     '秘密必须恰好选择 fact 或 fact_ref；独立秘密还必须举证，载体校验是唯一拦截点'),
    ('global_temporal_contract',
     _m(lambda g, r, b, m: g['world_rules'][0]['versions'].append(
         dict(g['world_rules'][0]['versions'][0], version_id='v02'))),
     '前一版本未结束而新版本已生效 ⇒ 生效区间重叠'),
    ('canon_chapter_refs_dangling',
     _m(lambda g, r, b, m: g['world_rules'][0]['versions'][0]
        .__setitem__('effective_from', 99)),
     '规则版本指到正文集合外会永不生效；secret 埋点/揭示章同理由专条参数化覆盖'),
    ('relation_dangling',
     _m(lambda g, r, b, m: g['relations'][0].__setitem__('subject_id', 'char_999')),
     '关系端点指向独立登记之外的人物，外键悬空不能靠启发式猜一个主体'),
    ('global_evidence_unlocated',
     _m(lambda g, r, b, m: g['evidence_anchors'][g['relations'][0]['evidence_refs'][0]]
        .__setitem__('text_sha256', '0' * 64)),
     '证据摘要与正文对不上是可读失败态，不能由引用齐全冒充定位正确'),
    # ── 四、完整剧情、共享证据、状态与人物许可 ──
    ('plot_contract', _m(lambda g, r, b, m: _ch(b, 1)['beats'][0].__setitem__('content', '')),
     '完整剧情正文不能缺失；在 Stage2 拒绝，不能付费规划后才发现'),
    ('plot_contract', _m(lambda g, r, b, m: _ch(b, 2)['beats'][0].__setitem__('beat_id', 'b01_01')),
     '正式节点身份不能越章，不能因稳定 ID 取错主体'),
    ('plot_contract', _m(lambda g, r, b, m: _ch(b, 1)['beats'][1].__setitem__('beat_id', 'b01_01')),
     '同章节点 ID 重复必须拒绝'),
    ('plot_contract', _m(lambda g, r, b, m: _ch(b, 1)['beats'][0].__setitem__('evidence_refs', [])),
     '完整节点仍须有证据，不允许用无依据正文填满蓝图'),
    ('plot_contract', _m(lambda g, r, b, m: _ch(b, 1)['beats'][0].__setitem__('evidence_refs', ['e02_01'])),
     '证据属于别章，不能用另一章片段伪造本章根据'),
    ('plot_evidence', _m(lambda g, r, b, m: _ch(b, 1)['evidence_anchors'][0].__setitem__('start', 27)),
     '正式坐标不匹配程序段号，不能由原文范围覆盖冒充定位正确'),
    ('plot_evidence', _m(lambda g, r, b, m: m[0].__setitem__('text', CH1 + '新的原文句子。')),
     '相同节点不得静默复用到另一份源文'),
    ('plot_state_continuity', _m(lambda g, r, b, m: _ch(b, 2).__setitem__('entry_state_refs', ['st01_99'])),
     '章首状态必须由前章当前 carry_out 提供'),
    ('plot_global_refs', _m(lambda g, r, b, m: _ch(b, 1)['states'].append({
         'state_id': 'st01_01', 'content': None, 'global_ref': {'kind': 'secret', 'ref': 'secret_99'},
         'modality': 'world', 'holder': None, 'established_at': {'kind': 'at_beat', 'beat_ref': 'b01_01'},
         'ended_at': None, 'supersedes_state_refs': [], 'evidence_refs': ['e01_01']})),
     '共享状态不得引用当前未提供的秘密主体'),
    ('present_ids_dangling', _m(lambda g, r, b, m: _ch(b, 1)['beats'][0].__setitem__(
         'participants', [{'char_id': 'char_999', 'presentation_ref': None}])),
     '不能引用当前人物闭集之外的人物，参与与在场必须分别建模'),
    ('scope_violation', _m(lambda g, r, b, m: b['_provenance'].__setitem__(
         'scope_violations', ['批 1 被要求产 ch1，实际产了 ch1 与 ch5'])),
     '越章响应是显式失败；键缺失不能当作合法空列表'),
]


@pytest.mark.parametrize('rule,mutate,why', CASES, ids=[c[0] for c in CASES])
def test_a_sample_that_makes_this_hard_rule_red(rule, mutate, why):
    """⭐ **盘上 0 命中的 HARD 各一份会让它红的样本**（用户裁决 2026-08-12）。

    每一条只改底座的一处，然后断言**恰好那一条**红了。
    ⚠ 不断言「只有它红」：有几处改动会连带触发别的判据（改坏章序可能连带状态连续性），
    而**那本来就是对的**——一份坏产物本来就该多报几条。
    要防的是反过来那件事：改了这一处而它**没红**。
    """
    gg, chars, bp, mt = _with(mutate)
    got = _hard_hits(gg, chars, bp, mt)
    assert rule in got, (
        f'改了这一处而 `{rule}` 没红：{why}\n'
        f'→ 这一次红的是 {sorted(got)}')


def test_every_hard_rule_without_a_disk_counterexample_has_a_synthetic_one():
    """⭐⭐ **守卫的守卫：这张清单不许悄悄变短。**

    本文件为未由现役 corpus 样例覆盖的每条 HARD 配一份失败输入。
    加一条 HARD 而不配样本，这条测试当场点名——
    否则那条新判据就又回到「从没见过反例」那一档，而它与一条不存在的判据长得一样。
    """
    #: 两条 Global 规则保留已有 corpus 反例，正文语料验收与默认回归独立。
    on_disk = {'address_bare_pronoun', 'role_leaks_fate'}
    hard = {r.rule for r in REGISTRY.all() if r.stage == 'stage2' and r.sev == 'hard'}
    covered = {c[0] for c in CASES}
    assert covered <= hard, f'CASES 里有不是 HARD 的：{sorted(covered - hard)}'
    assert not (missing := sorted(hard - on_disk - covered)), (
        f'这几条 HARD 既没有盘上反例、也没有合成样本：{missing}\n'
        f'→ **「盘上 0 命中」不是「干净」**，是那种失效模式一次都没发生过。'
        f'一条从没见过反例的判据与一条不存在的判据，在报告上长得一模一样')
    assert not (stale := sorted(covered & on_disk)), (
        f'这几条盘上已有真反例，不必再配合成样本：{stale}')
