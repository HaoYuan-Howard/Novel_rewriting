#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_phase3_change —— CS-S Phase 3（rc20）判据套件：closed change keys、
拍级 access、`ev→event`／`state→change` 改名、reader 知情 compile 派生。

【验证边界】docs/REQUIREMENTS.md CS-S Phase 3：
  • change 键入 `CHANGE_KEYS` 闭集（27 个通用维度，rc15 冻结根三本书 184 个
    真实键聚类定稿）——wire／canonical／Characters 三层同批收紧；scene_location
    只由 compiler 从拍级字段合成；
  • wire 字段改名 `ev`→`event`、`state`→`change`；旧字面按 extra 拒收；
  • 拍级 `access` 获知通道闭集（witnessed 默认不写／narrated 变体直陈例外）；
  • **reader 知情由 compile 派生**：wire 禁 reader（schema 层即拒）；蓝图的
    reader cognition 全部是派生物——narrated 拍的 actual 事实与 learns/confirms
    命题→reader learns；hears_claim→reader hears_claim（任何通道）；悬念 close
    的 resolution→reader learns；suspects/believes/asserts 不派生。
全部确定性构造，零模型调用。
"""
from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError

from contracts.schema.observation import (
    STAGE2_CHAPTERS_WIRE_PROTOCOL, ChapterObservationWire,
)
from contracts.schema.source import ACCESS_CHANNELS, CHANGE_KEYS

from tests.test_str06_stage2_publish import (
    CPV, EVIDENCE, _basis, _beat, _book_wire, _observations, _plain_effect,
    _quiet_beat,
)


def _slim_beat(**over) -> dict:
    beat = {'event': '一拍。', 'on_stage': ['fu_jun'], 'start': 'e01_01'}
    beat.update(over)
    return beat


def _wire(beats: list[dict]) -> dict:
    return {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': beats}


def test_wire_renames_ev_and_state_to_event_and_change():
    """v10 字段改名：`event`／`change` 是现役词汇；旧 `ev`／`state` 按 extra 拒。"""
    model = ChapterObservationWire.model_validate(_wire([
        _slim_beat(change=[{'at': 'fu_jun', 'key': 'attitude',
                            'val': '低', 'evidence_refs': ['e01_01']}])]))
    assert model.beats[0].event == '一拍。'
    assert model.beats[0].change[0].key == 'attitude'
    try:
        ChapterObservationWire.model_validate(_wire([
            {'ev': '旧词汇。', 'on_stage': [], 'start': 'e01_01'}]))
        raise AssertionError('旧字段名 ev 应被拒绝')
    except ValidationError as exc:
        assert any('ev' in str(e.get('loc', '')) for e in exc.errors())
    try:
        ChapterObservationWire.model_validate(_wire([
            {'event': '旧词汇。', 'on_stage': [], 'start': 'e01_01',
             'state': []}]))
        raise AssertionError('旧字段名 state 应被拒绝')
    except ValidationError as exc:
        assert any('state' in str(e.get('loc', '')) for e in exc.errors())


def test_change_keys_closed_set_covers_frozen_dimensions():
    """闭集判据：27 键、含聚类高频维度；scene_location 不在模型面（compiler 专用）；
    表外键 wire 即拒。"""
    assert len(CHANGE_KEYS) == 27
    for key in ('intent', 'relationship', 'residence', 'body_state',
                'body_occupancy', 'marital_status', 'pregnancy', 'attitude',
                'kinship', 'identity', 'secret', 'emotional_state', 'health'):
        assert key in CHANGE_KEYS, key
    assert 'scene_location' not in CHANGE_KEYS
    try:
        ChapterObservationWire.model_validate(_wire([
            _slim_beat(change=[{'at': 'fu_jun', 'key': 'plan_for_sheng_er',
                                'val': 'x', 'evidence_refs': ['e01_01']}])]))
        raise AssertionError('书内自造键应被拒绝（长尾键收进通用维度）')
    except ValidationError as exc:
        assert any('plan_for_sheng_er' in str(e) for e in exc.errors())


def test_access_channel_enum_is_witnessed_default_and_narrated_exception():
    """获知通道闭集：witnessed／narrated 两值（真实分布冻结——told 语义由
    knows 的 hears_claim 模式逐条承载，不重复拍级标注）；表外值拒。"""
    assert ACCESS_CHANNELS == ('witnessed', 'narrated')
    model = ChapterObservationWire.model_validate(_wire([
        _slim_beat(access='narrated')]))
    assert model.beats[0].access == 'narrated'
    assert ChapterObservationWire.model_validate(
        _wire([_slim_beat()])).beats[0].access == 'witnessed', '缺省＝witnessed'
    try:
        ChapterObservationWire.model_validate(_wire([_slim_beat(access='told')]))
        raise AssertionError('told 不是获知通道值（说法由 hears_claim 承载）')
    except ValidationError:
        pass


def _complete_associate(book, chapters):
    """构造恰答当前全部候选的答案；不从 compiler 的成功结论反推 fixture。"""
    from contracts.schema.observation import AssociateObservation
    from novel.stages.stage2 import (
        project_association_space, project_cause_candidates, project_identity_candidates,
    )
    space = project_association_space(book, chapters)
    candidates = project_identity_candidates(space, book)
    observation = AssociateObservation.model_validate({
        'protocol_version': 'stage2-associate-v5',
        'same_proposition': [
            {'left': c.left, 'right': c.right, 'verdict': 'distinct'}
            for c in candidates.same_proposition],
        'loop_pairing': [
            {'open': c.open, 'close': c.close, 'verdict': 'distinct'}
            for c in candidates.loop_pairing],
        'scene_continuation': [
            {'left': c.left, 'right': c.right, 'verdict': 'distinct'}
            for c in candidates.scene_continuation],
    })
    causes = project_cause_candidates(space, chapters, book, observation)
    return AssociateObservation.model_validate({
        **observation.model_dump(mode='json'),
        'cross_chapter_cause': [
            {'from_beat': c.from_beat, 'to_beat': c.to_beat, 'kind': 'none'}
            for c in causes],
    })


def _compiled_with(beat_over, loops=(), extra_chapters=()):
    """三章观察（ch01 带覆写拍）→ compile_source_truth 的蓝图与 fold。"""
    from novel.stages.stage2 import compile_source_truth
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [_beat('一拍', ('e01_01',),
                                                     participants=('fu_jun',),
                                                     **beat_over)]}
    chapters = {1: ch01}
    chapters.update({n: {'protocol_version': CPV,
                         'beats': [_quiet_beat(f'第{n}章', f'e{n:02d}_01')]}
                     for n in extra_chapters or (2, 3)})
    book_obs, chapter_obs = _observations(book, chapters)
    from tests.test_str06_stage2_publish import _names_observation
    return compile_source_truth(
        book=book_obs, chapters=chapter_obs,
        names=_names_observation(book_obs), evidence=EVIDENCE,
        associate=_complete_associate(book_obs, chapter_obs))


def _reader_entries(blueprint, chapter=1):
    return [(c.holder, c.mode, c.proposition.statement)
            for beat in blueprint.chapters[chapter - 1].beats
            for c in beat.cognition if c.holder == 'reader']


def test_reader_learned_from_narrated_facts_and_confirmations():
    """narrated 拍派生：actual set-op 字符串事实→reader learns（statement＝val）；
    learns/confirms 命题→reader learns 镜像（同一命题与证据）。"""
    outcome = _compiled_with({
        'access': 'narrated',
        'effects': [_plain_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                  'intent', '求娶芸娘为报恩')],
        'cognition': [{'proposition': {'ref': 'p_x', 'statement': '夫君幼时落水'},
                       'holder': 'yun_niang', 'mode': 'confirms',
                       'basis': _basis('e01_01')}],
    })
    assert outcome.errors == [] and not outcome.undecided, outcome.errors
    entries = _reader_entries(outcome.blueprint)
    assert ('reader', 'learns', '求娶芸娘为报恩') in entries, entries
    assert ('reader', 'learns', '夫君幼时落水') in entries, entries


def test_reader_hears_claim_mirrored_at_witnessed_and_suspects_not_derived():
    """hears_claim 在任何通道镜像为 reader hears_claim；suspects／believes／
    asserts 不派生（角色私下立场不是读者确认的机械信号）。"""
    outcome = _compiled_with({
        'cognition': [
            {'proposition': {'ref': 'p_claim', 'statement': '据说夫君将远行'},
             'holder': 'yun_niang', 'mode': 'hears_claim',
             'basis': _basis('e01_01')},
            {'proposition': {'ref': 'p_susp', 'statement': '云娘起疑'},
             'holder': 'yun_niang', 'mode': 'suspects',
             'basis': _basis('e01_01')},
        ]})
    assert outcome.errors == [] and not outcome.undecided, outcome.errors
    entries = _reader_entries(outcome.blueprint)
    assert ('reader', 'hears_claim', '据说夫君将远行') in entries, entries
    assert all(mode != 'learns' or statement != '云娘起疑'
               for _h, mode, statement in entries), entries


def test_reader_learned_from_loop_close_resolution():
    """悬念 close 的 resolution→reader learns（收束即叙述向读者确认的 payoff）；
    wire 自写 reader 在 schema 层即拒。"""
    from contracts.schema.observation import AssociateObservation
    from novel.stages.stage2 import compile_source_truth
    from tests.test_str06_stage2_publish import _names_observation
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('悬念开启', ('e01_01',),
              loops=[{'ref': 'q_id', 'op': 'open', 'question': '床底的人是谁？'}]),
        _beat('悬念收束', ('e01_02',), scene_boundary='continue',
              loops=[{'ref': 'q_id', 'op': 'close', 'resolution': '床底的人是夫君'}]),
    ]}
    ch02 = {'protocol_version': CPV, 'beats': [_quiet_beat('二', 'e02_01')]}
    ch03 = {'protocol_version': CPV, 'beats': [_quiet_beat('三', 'e03_01')]}
    book_obs, chapter_obs = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    outcome = compile_source_truth(
        book=book_obs, chapters=chapter_obs, names=_names_observation(book_obs),
        evidence=EVIDENCE,
        associate=_complete_associate(book_obs, chapter_obs))
    assert outcome.errors == [] and not outcome.undecided, outcome.errors
    entries = _reader_entries(outcome.blueprint)
    assert ('reader', 'learns', '床底的人是夫君') in entries, entries
    # （fold 的 knowledge_timeline 只按书级 facts 键跟踪——章内命题与派生条目
    # 不进时间线，T-5 只读读数面不变。）
    # wire 禁 reader（SlimCognition 模型层）。
    try:
        ChapterObservationWire.model_validate(_wire([
            _slim_beat(know=[{'who': 'reader', 'how': 'learns',
                              'what': 'x', 'evidence_refs': ['e01_01']}])]))
        raise AssertionError('wire 自写 reader 应被拒绝')
    except ValidationError as exc:
        assert any('禁 reader' in str(e) for e in exc.errors())


def test_observation_side_reader_is_refused_by_compile_defense():
    """canonical 直通面带 reader（观察面自写）→ compile 防御按条目拒发。"""
    from contracts.schema.observation import AssociateObservation
    from novel.stages.stage2 import compile_source_truth
    from tests.test_str06_stage2_publish import _names_observation
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_r', 'statement': '读者条目'},
                          'holder': 'reader', 'mode': 'learns',
                          'basis': _basis('e01_01')}]),
        _quiet_beat('收尾', 'e01_02')]}
    ch02 = {'protocol_version': CPV, 'beats': [_quiet_beat('二', 'e02_01')]}
    ch03 = {'protocol_version': CPV, 'beats': [_quiet_beat('三', 'e03_01')]}
    book_obs, chapter_obs = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    outcome = compile_source_truth(
        book=book_obs, chapters=chapter_obs, names=_names_observation(book_obs),
        evidence=EVIDENCE,
        associate=_complete_associate(book_obs, chapter_obs))
    assert any('禁 reader' in e for e in outcome.errors), outcome.errors
    assert not outcome.publishable


def test_reader_keeps_each_payoff_when_one_beat_closes_multiple_loops():
    """同拍收束两个悬念，两条 resolution 各派生正式命题，且真实 Preserve
    投影同时看到两条；不能共用拍级句柄而把第二条 payoff 静默去重。
    """
    outcome = _compiled_with({'loops': [
        {'ref': 'q_person', 'op': 'open', 'question': '床底是谁？'},
        {'ref': 'q_noise', 'op': 'open', 'question': '为何床板响？'},
        {'ref': 'q_person', 'op': 'close', 'resolution': '床底的人是夫君'},
        {'ref': 'q_noise', 'op': 'close', 'resolution': '夫君翻身撞响床板'},
    ]})
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    reader = [c for c in outcome.blueprint.chapters[0].beats[0].cognition
              if c.holder == 'reader']
    assert {c.proposition.statement for c in reader} == {
        '床底的人是夫君', '夫君翻身撞响床板'}
    assert len({c.proposition.ref for c in reader}) == 2
    from novel.stages.stage4.grounding import project_preserve_view
    view = project_preserve_view(outcome.characters.core_view(), outcome.blueprint)
    assert all(f'认知 reader learns 命题 {c.proposition.ref}「{c.proposition.statement}」'
               in view for c in reader)


def test_reader_generated_propositions_cannot_collide_with_observation_refs():
    """canonical 直通的本地 ref 没有保留 rd 前缀，派生事实/收束不能占用它。
    原命题镜像仍复用原正式 ID，两条新派生命题保持各自的文本和正式 ID。
    """
    for ref in ('rd01e01', 'rd01l', 'rd01l02'):
        outcome = _compiled_with({
            'access': 'narrated',
            'effects': [_plain_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                      'intent', '求娶芸娘为报恩')],
            'cognition': [{'proposition': {'ref': ref, 'statement': '夫君幼时落水'},
                           'holder': 'yun_niang', 'mode': 'confirms',
                           'basis': _basis('e01_01')}],
            'loops': [
                {'ref': 'q_person', 'op': 'open', 'question': '床底是谁？'},
                {'ref': 'q_person', 'op': 'close', 'resolution': '床底的人是夫君'},
            ],
        })
        assert outcome.publishable, (ref, outcome.errors, outcome.undecided)
        cognition = outcome.blueprint.chapters[0].beats[0].cognition
        source_prop = next(c.proposition for c in cognition if c.holder == 'yun_niang')
        reader = {c.proposition.statement: c.proposition.ref
                  for c in cognition if c.holder == 'reader'}
        assert set(reader) == {'夫君幼时落水', '求娶芸娘为报恩', '床底的人是夫君'}, (ref, reader)
        assert reader['夫君幼时落水'] == source_prop.ref
        assert len(set(reader.values())) == 3


def test_chapter_prompt_renders_closed_keys_and_access():
    """提示词随闭集渲染：chapters 与 book 的 prompt 都带【变化键闭集】清单与
    access 指引；wire 的协议字面量 v10。"""
    from novel.stages.common import CHAPTER_PROMPT
    prompt = CHAPTER_PROMPT
    assert '【变化键闭集】' in prompt and 'body_occupancy' in prompt
    assert 'access' in prompt and 'narrated' in prompt and 'witnessed' in prompt
    assert '"protocol_version":"stage2-chapters-v10"' in prompt
    assert '不写 reader' in prompt, 'know.who 禁 reader 要写进指引'
