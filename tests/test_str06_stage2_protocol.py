#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_stage2_protocol —— Stage2 五节点观察/关联协议与 Resolver 的 contract 测试（B2）。

【验证边界】确定性形状与闭集判据的正反例：协议显式版本化（I-13）、模型输出面
去机械化（I-20/I-21：拍号/编号/正式 ID/章序不在 wire）、章观察只含章内本地句柄
（D3）、associate 只选已给句柄的形状面（D8）、Resolver 的闭集选择／逐字锚唯一定位
／R-2.10 称谓锚点派生。语义判断（同一性、归因、关联正确性）归付费 semantic unit，
不在机械验证范围，语义保持 unver 披露。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.artifacts import (
    ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS, artifact_rel,
    str06_chapter_observation_rel,
)
from contracts.invariants import NODES, LLM_EXECUTOR_NODES, PIPELINE_PARALLEL_NODES
from contracts.schema import (
    ARTIFACT_SPECS, AssociateObservation, BookObservation, ChapterObservation,
    NamesObservation,
)
from contracts.schema.observation import (
    STAGE2_ASSOCIATE_PROTOCOL, STAGE2_BOOK_PROTOCOL, STAGE2_CHAPTERS_PROTOCOL,
    STAGE2_OBSERVATION_PROTOCOL,
)
from contracts.schema.source import FamilyEntry, NameStructure, PersonNameStructure
from contracts.schema.workflow import STR06_RC_LABELS, STR06_RC_VERSION
from novel.stages.stage2 import EvidenceResolver, ResolverIssue
from novel.stages.stage2.resolver import chapter_of_evidence
from tests.str06_expression_inputs import (
    sample_book_expression,
)

STAGE2_NODES = ('str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
                'str06.stage2.associate', 'str06.stage2.compile')

SEGMENTS = {
    'e00_01': {'chapter': 0, 'text': '章前文字：夫君坦白借种之约。', 'pre_chapter': True},
    'e01_01': {'chapter': 1, 'text': '夫君走进门来。云娘没有回头。'},
    'e01_02': {'chapter': 1, 'text': '云娘坐在窗边。'},
    'e02_01': {'chapter': 2, 'text': '第二夜窗外有响动。'},
}


def _basis(ref: str) -> list[dict]:
    """R10a a1 fixture：逐字 basis（段文本前 6 字做 quote）。"""
    return [{'segment': ref, 'quote': SEGMENTS[ref]['text'][:6]}]


# ───────────────────────── 节点注册（D9 一次性改齐） ─────────────────────────

def test_five_stage2_nodes_registered_in_chain_order():
    keys = [n for n in NODES if n.startswith('str06.stage2.')]
    assert tuple(keys) == STAGE2_NODES
    assert 'str06.stage2.source' not in NODES, '联合抽取节点已退役（D9）'
    assert NODES['str06.stage2.book'].paid and NODES['str06.stage2.chapters'].paid
    assert NODES['str06.stage2.associate'].paid
    assert not NODES['str06.stage2.compile'].paid, 'compile 免费（D9）'


def test_stage2_produces_are_registered_and_single_writer():
    assert NODES['str06.stage2.book'].produces == ('stage2/observations/book.json',)
    assert NODES['str06.stage2.chapters'].produces == ('stage2/observations/ch{nn}.json',)
    assert NODES['str06.stage2.names'].produces == ('stage2/observations/names.json',)
    assert NODES['str06.stage2.associate'].produces == ('stage2/observations/associate.json',)
    assert set(NODES['str06.stage2.compile'].produces) == {
        'stage2/characters/characters.json',
        'stage2/blueprints/chapter_blueprints.json',
        'stage2/acceptance/fold.json'}
    # 双真源唯一 writer（D9）：Characters／Blueprint／fold 只由 compile 产出。
    writers: dict[str, str] = {}
    for node, spec in NODES.items():
        for rel in spec.produces:
            assert rel not in writers, f'{rel} 双 writer：{writers.get(rel)} / {node}'
            writers[rel] = node
    assert writers['stage2/characters/characters.json'] == 'str06.stage2.compile'
    assert writers['stage2/blueprints/chapter_blueprints.json'] == 'str06.stage2.compile'


def test_chapters_node_is_registered_for_concurrency():
    """B1 移交项：`str06.stage2.chapters` 建节点时同步登记并发覆盖表。"""
    assert 'str06.stage2.chapters' in PIPELINE_PARALLEL_NODES
    assert NODES['str06.stage2.chapters'].lifecycle.unit_kind == 'chapter'
    assert 'str06.stage2.chapters' in LLM_EXECUTOR_NODES


def test_retired_derived_inputs_are_refused_paths():
    """联合抽取的派生输入退役（D3/D9）：路径只供识别拒绝，不在现役表。"""
    assert 'stage2/compiled/book_extraction.json' not in ARTIFACT_PATHS
    assert 'stage2/compiled/input_snapshots.json' not in ARTIFACT_PATHS
    assert STR06_ARTIFACT_RC_VERSIONS.get('stage2/compiled/book_extraction.json') is None


# ───────────────────────── 协议显式版本化（I-13） ─────────────────────────

def test_observation_artifacts_are_registered_with_independent_versions():
    # rc15（RC-26/27 证据寻址）：携带段号的三类观察换身份；associate 只有
    # 位置句柄、形状不变，不虚构升版。
    assert STR06_ARTIFACT_RC_VERSIONS['stage2/observations/book.json'] == 'str06-rc15'
    for rel in ('stage2/observations/ch{nn}.json',
                'stage2/observations/names.json'):
        assert rel in ARTIFACT_PATHS and rel in ARTIFACT_SPECS
        assert STR06_ARTIFACT_RC_VERSIONS[rel] == 'str06-rc15'
    # R1：associate 观察独立升版（stage2-associate-v2），形状升 rc9；三类观察
    # 冻结在 rc6（B2 身份）不动。RC 版式头随 R1 升 rc9。
    # rc17（CS-H H4，RC-65）：associate 协议升 v3（维度句柄）——同键配对按构造
    # 不可能、模型只配键不同但同维的维度；观察产物随协议换身份。
    assert ARTIFACT_SPECS['stage2/observations/associate.json']
    assert STR06_ARTIFACT_RC_VERSIONS['stage2/observations/associate.json'] == 'str06-rc17'
    # R2（语义送达）起 RC 版式头升 rc10；associate 冻结在 rc9（R1 身份）不动。
    assert STR06_RC_VERSION == 'str06-rc17'     # rc17：CS-H H3 称谓面＋H4 associate v3
    # rc16 续（CS-F2 Hook 称谓投影）：楔子上下文随绑定表换身份，旧 rc12 拒读。
    # rc17 续（CS-H H3）：hook 上下文增 display_faces/avoid_lines。
    assert STR06_ARTIFACT_RC_VERSIONS['stage4/context/hook/hook.json'] == 'str06-rc17'
    for label in ('str06-rc6', 'str06-rc9', 'str06-rc10', 'str06-rc11'):
        assert label in STR06_RC_LABELS
    assert str06_chapter_observation_rel(3) == 'stage2/observations/ch03.json'
    assert artifact_rel('stage2/observations/ch{nn}.json', nn=12) == \
        'stage2/observations/ch12.json'


def test_protocol_version_is_required_literal():
    book = _book_observation()
    assert book.protocol_version == STAGE2_BOOK_PROTOCOL
    wrong = _book_wire()
    wrong['protocol_version'] = 'stage2-observation-v0'
    with pytest.raises(ValidationError):
        BookObservation.model_validate(wrong)
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate({'beats': [_beat_wire()]})


def _book_wire() -> dict:
    return {**sample_book_expression({
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']},
                {'surface': '他', 'source_refs': ['e01_01']},
            ]},
            'yun_niang': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'source_refs': ['e01_02']},
            ]},
        }),
        'protocol_version': 'stage2-book-v4',
        'characters': {
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']},
                {'surface': '他', 'source_refs': ['e01_01']},
            ]},
            'yun_niang': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'source_refs': ['e01_02']},
            ]},
        },
        'initial_state': [
            {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
             'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
             'knowledge': None, 'source_refs': ['e00_01'], 'basis': _basis('e00_01')},
        ],
        'world_constraints': [
            {'kind': 'setting_constant', 'statement': '故事发生在古代江南',
             'source_refs': ['e01_01'], 'basis': _basis('e01_01')},
        ],
    }


def _book_observation() -> BookObservation:
    return BookObservation.model_validate(_book_wire())


def _beat_wire(**over) -> dict:
    beat = {
        'event': '云娘向夫君试探',
        'participants': ['yun_niang', 'fu_jun'],
        'source_refs': ['e01_01'],
        'scene_boundary': 'new',
        'temporal_mode': 'single',
        'effects': [{'target': {'kind': 'character', 'char_id': 'yun_niang'},
                     'key': 'wariness', 'op': 'set', 'value': '高', 'scope': 'book',
                     'modality': 'actual',
                     'disclosure': {'visibility': 'on_page'},
                     'basis': _basis('e01_01')}],
        'cognition': [{'proposition': {'ref': 'p_secret',
                                       'statement': '借种之约是夫妻共同秘密'},
                       'holder': 'yun_niang', 'mode': 'learns',
                       'basis': _basis('e01_01')}],
        'loops': [{'ref': 'q_night', 'op': 'open', 'question': '夜半响动是谁？'}],
    }
    beat.update(over)
    return beat


# ───────────────────────── 模型输出面去机械化（I-20/I-21） ─────────────────────────

def test_wire_carries_no_mechanical_coordinates():
    """拍号、状态/约束编号、章序、窗口坐标不在协议字段面上（compiler 生成）。"""
    for model, banned in (
        (BookObservation, ('state_id', 'constraint_id')),
        (ChapterObservation, ('chapter', 'beat_id', 'causal_edges', 'scenes')),
    ):
        for field in banned:
            assert field not in model.model_fields, f'{model.__name__}.{field} 不应在 wire'
    surface_model = BookObservation.model_validate(_book_wire()).characters['fu_jun'] \
        .surfaces[0]
    assert not hasattr(surface_model, 'from_chapter'), '称谓窗口坐标由 compiler 派生（R-2.10）'
    beat = ChapterObservation.model_validate(
        {'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
         'beats': [_beat_wire()]}).beats[0]
    assert not hasattr(beat, 'beat_id'), '拍号由 compiler 按列表序派发'


def test_character_slug_is_the_model_given_semantic_handle():
    book = _book_observation()
    assert set(book.characters) == {'fu_jun', 'yun_niang'}
    bad = _book_wire()
    bad['characters']['Fu Jun'] = bad['characters'].pop('fu_jun')
    with pytest.raises(ValidationError, match='slug'):
        BookObservation.model_validate(bad)


# ───────────────────────── 章观察只含章内本地句柄（D3） ─────────────────────────

def test_chapter_observation_rejects_unknown_fields():
    wire = {'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
            'beats': [_beat_wire()], 'causal_edges': [
                {'from_beat': 'c01b01', 'to_beat': 'c01b02', 'kind': 'causes'}]}
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate(wire), '因果边不在章观察面（D3）'


def test_transfer_semantics_carry_over_from_v13():
    """三类转移的形状闸逐字复用 v13 模型：可见性、悬念开闭、登记键作用域兜底。"""
    # concealed 必须声明已知者并带命题锚（R-2.13）
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate({'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
                                           'beats': [_beat_wire(effects=[
                                               _concealed_effect(known_by=[])])]})
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate({'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
                                           'beats': [_beat_wire(effects=[
                                               _concealed_effect(proposition=None)])]})
    # open 必带 question、close 必带 resolution 且不改写问题（R-2.11）
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate({'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
                                           'beats': [_beat_wire(loops=[
                                               {'ref': 'q1', 'op': 'open'}])]})
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate({'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
                                           'beats': [_beat_wire(loops=[
                                               {'ref': 'q1', 'op': 'close',
                                                'question': '改写问题', 'resolution': '是夫君'}])]})
    # 登记键作用域：表内键声明不符在 schema 层兜底拒（R-2.14）
    with pytest.raises(ValidationError):
        ChapterObservation.model_validate({'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
                                           'beats': [_beat_wire(effects=[
                                               _effect('residence', scope='scene')])]})


def _effect(key: str, *, scope: str) -> dict:
    return {'target': {'kind': 'character', 'char_id': 'fu_jun'}, 'key': key,
            'op': 'set', 'value': '城南', 'scope': scope, 'modality': 'actual',
            'disclosure': {'visibility': 'on_page'}, 'basis': _basis('e01_01')}


def _concealed_effect(*, known_by=(), proposition='p_hidden') -> dict:
    eff = {'target': {'kind': 'character', 'char_id': 'fu_jun'}, 'key': 'birthright',
           'op': 'set', 'value': '养子', 'scope': 'book', 'modality': 'actual',
           'disclosure': {'visibility': 'concealed', 'known_by': list(known_by)},
           'basis': _basis('e01_01')}
    if proposition is not None:
        eff['proposition'] = {'ref': proposition, 'statement': '夫君是养子'}
    return eff


def test_chapter_observation_green_with_local_handles():
    obs = ChapterObservation.model_validate(
        {'protocol_version': STAGE2_CHAPTERS_PROTOCOL, 'beats': [_beat_wire()]})
    assert obs.beats[0].cognition[0].proposition.ref == 'p_secret'
    assert obs.beats[0].loops[0].question == '夜半响动是谁？'


# ───────────────────────── associate 只选已给句柄（D8） ─────────────────────────

def test_associate_protocol_is_independently_versioned():
    """R1：associate 独立升版（CS-H H4 起 stage2-associate-v3，RC-65 维度句柄）；
    三类观察协议字面量逐字不变。

    旧协议字面量对 associate 关闭式拒读（I-13：无运行时转换层）——句柄含义与
    输入材料已变，旧回答不做句柄替换后继续采用（首版 PLAN.md R1，Git 2fc03832；A1.5 出自被它吸收的提案）。
    """
    assert STAGE2_ASSOCIATE_PROTOCOL == 'stage2-associate-v3'
    assert STAGE2_OBSERVATION_PROTOCOL == 'stage2-observation-v1'
    empty = {'same_proposition': [], 'same_state_dimension': [], 'loop_pairing': [],
             'cross_chapter_cause': [], 'scene_continuation': []}
    obs = AssociateObservation.model_validate(
        {'protocol_version': STAGE2_ASSOCIATE_PROTOCOL, **empty})
    assert obs.protocol_version == STAGE2_ASSOCIATE_PROTOCOL
    with pytest.raises(ValidationError):
        AssociateObservation.model_validate(
            {'protocol_version': 'stage2-associate-v2', **empty}), \
        'v2 字面量（状态写入句柄配对）对 v3 关闭式拒读'
    with pytest.raises(ValidationError):
        AssociateObservation.model_validate(
            {'protocol_version': 'stage2-observation-v1', **empty}), \
        '旧协议字面量对 associate 关闭式拒读'


def test_associate_observation_shape_and_closed_handle_charset():
    obs = AssociateObservation.model_validate({
        'protocol_version': STAGE2_ASSOCIATE_PROTOCOL,
        'same_proposition': [{'left': 'c01:p_secret', 'right': 'c03:p2'}],
        'same_state_dimension': [{'left': 'c01:e1', 'right': 'c02:e3'}],
        'loop_pairing': [{'open': 'c01:q_night', 'close': 'c03:q9'}],
        'cross_chapter_cause': [{'from_beat': 'c01:b01', 'to_beat': 'c02:b02',
                                 'kind': 'setup_payoff'}],
        'scene_continuation': [{'left': 'c01:sc02', 'right': 'c02:sc01'}],
    })
    assert obs.same_proposition[0].left == 'c01:p_secret'
    assert obs.cross_chapter_cause[0].kind == 'setup_payoff'
    with pytest.raises(ValidationError):
        AssociateObservation.model_validate({
            'protocol_version': STAGE2_ASSOCIATE_PROTOCOL,
            'cross_chapter_cause': [{'from_beat': 'c01:b01', 'to_beat': 'c01:b01',
                                     'kind': 'causes'}]}), '因果边自指'
    with pytest.raises(ValidationError):
        AssociateObservation.model_validate({
            'protocol_version': STAGE2_ASSOCIATE_PROTOCOL,
            'same_proposition': [{'left': '带 空格', 'right': 'c02:p1'}]})
    with pytest.raises(ValidationError):
        AssociateObservation.model_validate({
            'protocol_version': STAGE2_ASSOCIATE_PROTOCOL,
            'same_proposition': [{'left': 'c01:p1', 'right': 'c02:p1',
                                  'note': '闭集之外的字段不收'}]})


# ───────────────────────── names 观察与初态/世界约束去编号 ─────────────────────────

def test_names_observation_wraps_name_structure():
    obs = NamesObservation.model_validate({
        'protocol_version': 'stage2-names-v3',
        'structure': {
            'families': {'f_lu': {'literal': '陆', 'forms': []}},
            'people': {'fu_jun': {'family_id': 'f_lu', 'given': '夫君',
                                  'aliases': [], 'surface_forms': {'夫君': None}}},
        }})
    assert obs.structure.families['f_lu'].literal == '陆'


def test_initial_fact_and_world_rule_carry_no_ids():
    book = _book_observation()
    atom = book.initial_state[0]
    assert not hasattr(atom, 'state_id'), '初态编号由 compiler 生成（I-21）'
    rule = book.world_constraints[0]
    assert not hasattr(rule, 'constraint_id'), '约束编号由 compiler 生成（I-21）'


# ───────────────────────── Resolver：闭集与逐字锚 ─────────────────────────

def test_resolver_closed_set_selects_only_known_segments():
    resolver = EvidenceResolver(SEGMENTS)
    assert resolver.segment_ids == frozenset(SEGMENTS)
    issues = resolver.check_closed_set(['e01_01', 'e09_09'])
    assert [i.code for i in issues] == ['unknown_segment']
    assert 'e09_09' in issues[0].message


def test_resolver_locates_verbatim_anchor_uniquely():
    resolver = EvidenceResolver(SEGMENTS)
    hit, issues = resolver.locate('云娘坐在窗边', ['e01_02'])
    assert issues == [] and hit is not None
    assert (hit.segment_id, hit.chapter, hit.offset) == ('e01_02', 1, 0)
    # 不存在：不猜笔误，precise issue
    hit, issues = resolver.locate('芸娘坐在窗边', ['e01_02'])  # '芸'≠'云'
    assert hit is None and issues[0].code == 'missing_literal'
    # 重复引文：同一字面在所引句段出现两次 ⇒ 无法唯一定位
    segments = {'e01_01': {'chapter': 1, 'text': '夫君走进门来。夫君没有说话。'}}
    hit, issues = EvidenceResolver(segments).locate('夫君', ['e01_01'])
    assert hit is None and issues[0].code == 'ambiguous_literal'
    assert len(issues[0].context['hits']) == 2


def test_resolver_refuses_to_locate_outside_the_closed_set():
    hit, issues = EvidenceResolver(SEGMENTS).locate('夫君', ['e07_01'])
    assert hit is None and [i.code for i in issues] == ['unknown_segment']


def test_resolver_surface_chapter_follows_r_2_10():
    resolver = EvidenceResolver(SEGMENTS)
    # 最早逐字命中章：夫君在 e00（章前）与 e01 都出现 ⇒ 取最早段并映射第 1 章
    chapter, issue = resolver.surface_chapter('夫君', ['e00_01', 'e01_01'])
    assert issue is None and chapter == 1
    # 只引第 2 章而字面不在 ⇒ 拒（QP-04 根因，消息与旧实现逐字一致）
    chapter, issue = resolver.surface_chapter('夫君', ['e02_01'])
    assert chapter is None and issue is not None
    assert issue.message == ("surface '夫君' 的证据锚点不含该字面"
                             '（source_refs=[\'e02_01\']）')
    assert issue.message == _legacy_message('夫君', ['e02_01'])
    # 合成证据面（只给 id 不给文本）：按 id 前缀派生，仍是既有行为
    chapter, issue = EvidenceResolver({}).surface_chapter('夫君', ['e03_02'])
    assert issue is None and chapter == 3


def _legacy_message(surface: str, refs) -> str:
    """退役前 `_derive_from_chapter` 的原始消息（委托后必须逐字保持）。"""
    return (f"surface {surface!r} 的证据锚点不含该字面"
            f'（source_refs={list(refs)}）')


# ⚠ B5（S-05）：`test_compile_book_delegates_to_the_resolver` 随旧联合抽取引擎
# 的委托 shim（`source.py::_derive_from_chapter`）删除；R-2.10 唯一实现的
# 消息一致性已由上方 resolver 用例（`_legacy_message` 逐字对账）钉住。


def test_evidence_ref_chapter_mapping():
    assert chapter_of_evidence('e00_03') == 0
    assert chapter_of_evidence('e12_07') == 12
    with pytest.raises(ValueError, match='形状非法'):
        chapter_of_evidence('x01_01')
