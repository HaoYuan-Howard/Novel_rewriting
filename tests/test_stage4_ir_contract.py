#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STR-03-S4 目标契约反例：steps / delivery_focus / permission transition / 失败三分。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema.stage4_ir import (
    PresentationChapter, PresentationGoal, PresentationScene, PresentationStep,
    PermissionTransition, StructureMoves, WriterChapterIR, WriterCharacterIR,
    WriterHookIR, WriterNarrationIR, WriterSceneIR, WriterStageIR, WriterStep,
)
from contracts.schema.stage4_source import (
    SourceBeat, SourceChapter, SourceCharacter, SourceState, Stage4SourceView,
)
from novel.stages.stage4.compile_ir import (
    assert_binding_matches_design, build_writer_chapter_ir,
    compile_presentation_chapter, mint_delivery_ref, propagate_permissions,
    validate_transitions_against_source,
)
from contracts.schema.stage4_ir import DeliveryEvidenceBinding


pytestmark = pytest.mark.unit_contract


def _goal(beat='b01_01', scene='s01'):
    return PresentationGoal(
        intent='establish', focus_refs=['st01_01'],
        anchor_beat_ref=beat, anchor_scene_ref=scene)


def _chapter_two_deliveries_same_beat(*, focus_second: str | None):
    steps = [
        PresentationStep(delivery_ref='d01_01', beat_ref='b01_01', delivery_focus='setup'),
        PresentationStep(delivery_ref='d01_02', beat_ref='b01_01', delivery_focus=focus_second),
    ]
    return PresentationChapter(
        output_ordinal=1, weight='even',
        structure=StructureMoves(mode='retain', moves=[]),
        goal=_goal(), open_hook=_goal(), end_hook=_goal(beat='b01_01'),
        scenes=[PresentationScene(
            scene_ref='s01', role='setup', delivery='scene', transition='start',
            weight='mid', purpose='双次呈现', turn_beat_ref='b01_01', steps=steps,
        )],
    )


def test_multi_delivery_requires_delivery_focus():
    with pytest.raises(ValidationError):
        _chapter_two_deliveries_same_beat(focus_second=None)
    ch = _chapter_two_deliveries_same_beat(focus_second='payoff')
    assert ch.scenes[0].steps[1].delivery_focus == 'payoff'
    assert ch.scenes[0].steps[0].delivery_ref != ch.scenes[0].steps[1].delivery_ref


def test_delivery_ref_mint_is_snapshot_local():
    assert mint_delivery_ref(5, 7) == 'd05_07'
    assert mint_delivery_ref(5, 7) == mint_delivery_ref(5, 7)


def test_permission_transition_sticky_mid_scene():
    chapter = PresentationChapter(
        output_ordinal=1, weight='even',
        structure=StructureMoves(mode='retain', moves=[]),
        goal=_goal(), open_hook=_goal(), end_hook=_goal(),
        scenes=[PresentationScene(
            scene_ref='s01', role='turn', delivery='scene', transition='start',
            weight='peak', purpose='中途揭晓', turn_beat_ref='b01_03',
            steps=[
                PresentationStep(delivery_ref='d01_01', beat_ref='b01_01'),
                PresentationStep(delivery_ref='d01_02', beat_ref='b01_02'),
                PresentationStep(delivery_ref='d01_03', beat_ref='b01_03'),
            ],
        )],
        permission_transitions=[
            PermissionTransition(
                delivery_ref='d01_03',
                surface_grants={'char_001': ['芸娘']},
                fact_permissions={'st01_05': 'world_fact'},
            ),
        ],
    )
    source = SourceChapter(
        chapter_ordinal=1, chapter_function='揭晓',
        characters=[SourceCharacter(
            char_id='char_001', surfaces=['芸娘'], first_chapter=1,
            identity_revealed_from=1)],
        beats=[
            SourceBeat(beat_ref='b01_01', content='陌生男子', participant_ids=['char_001']),
            SourceBeat(beat_ref='b01_02', content='取出信物', participant_ids=['char_001']),
            SourceBeat(beat_ref='b01_03', content='认出对方', participant_ids=['char_001']),
        ],
        states=[SourceState(state_ref='st01_05', content='他是救命恩人', modality='world')],
        entry_state_refs=[], carry_out_refs=[], constraints=[], global_subjects=[],
    )
    validate_transitions_against_source(chapter, source)
    snap = propagate_permissions(chapter, source)
    assert '芸娘' not in snap['d01_01']['surface_permissions'].get('char_001', [])
    assert snap['d01_03']['surface_permissions']['char_001'] == ['芸娘']
    assert snap['d01_03']['fact_permissions']['st01_05'] == 'world_fact'


def test_transition_cannot_open_before_identity_revealed():
    chapter = PresentationChapter(
        output_ordinal=1, weight='even',
        structure=StructureMoves(mode='retain', moves=[]),
        goal=_goal(), open_hook=_goal(), end_hook=_goal(),
        scenes=[PresentationScene(
            scene_ref='s01', role='setup', delivery='scene', transition='start',
            weight='mid', purpose='过早揭晓', turn_beat_ref='b01_01',
            steps=[PresentationStep(delivery_ref='d01_01', beat_ref='b01_01')],
        )],
        permission_transitions=[PermissionTransition(
            delivery_ref='d01_01', surface_grants={'char_002': ['裴书远']})],
    )
    source = SourceChapter(
        chapter_ordinal=1, chapter_function='x',
        characters=[SourceCharacter(
            char_id='char_002', surfaces=['裴书远'], first_chapter=1,
            identity_revealed_from=5)],
        beats=[SourceBeat(beat_ref='b01_01', content='a', participant_ids=[])],
        states=[], entry_state_refs=[], carry_out_refs=[], constraints=[],
        global_subjects=[],
    )
    with pytest.raises(ValueError, match='identity_revealed_from'):
        validate_transitions_against_source(chapter, source)


def test_char_id_identity_not_merged_by_display_name():
    ir = WriterChapterIR(
        output_ordinal=1, function='同名', goal='establish',
        open_hook=WriterHookIR(intent='open', beat_ref='b01_01'),
        end_hook=WriterHookIR(intent='close', beat_ref='b01_01'),
        narration=WriterNarrationIR(person='third'),
        characters={
            'char_001': WriterCharacterIR(default_surface='芸娘'),
            'char_002': WriterCharacterIR(default_surface='芸娘'),
        },
        scenes=[WriterSceneIR(
            scene_ref='s01', role='setup', delivery='scene', transition='start',
            purpose='二人', target_chars=100,
            stage=WriterStageIR(present=['char_001', 'char_002']),
            steps=[WriterStep(
                delivery_ref='d01_01', beat_ref='b01_01', content='二人同场')],
        )],
    )
    assert set(ir.characters) == {'char_001', 'char_002'}


def test_binding_rejects_stale_delivery_ref_from_other_design():
    design = _chapter_two_deliveries_same_beat(focus_second='echo')
    with pytest.raises(ValueError, match='delivery'):
        assert_binding_matches_design(design, [
            DeliveryEvidenceBinding(delivery_ref='d01_01', evidence_refs=['e01_01']),
            DeliveryEvidenceBinding(delivery_ref='d99_99', evidence_refs=['e01_02']),
        ])


def test_writer_ir_preserves_design_step_order():
    chapter = compile_presentation_chapter(
        output_ordinal=1, weight='even',
        structure={'mode': 'retain', 'moves': []},
        goal={'intent': 'establish', 'focus_refs': ['st01_01'],
              'anchor_beat_ref': 'b01_02', 'anchor_scene_ref': 's01'},
        open_hook={'intent': 'open', 'focus_refs': ['st01_01'],
                   'anchor_beat_ref': 'b01_01', 'anchor_scene_ref': 's01'},
        end_hook={'intent': 'withhold', 'focus_refs': ['st01_01'],
                  'anchor_beat_ref': 'b01_03', 'anchor_scene_ref': 's01'},
        scenes=[{
            'scene_ref': 's01', 'role': 'turn', 'delivery': 'scene',
            'transition': 'start', 'weight': 'peak', 'purpose': '倒叙呈现',
            'turn_beat_ref': 'b01_02',
            'steps': [
                {'beat_ref': 'b01_03'},
                {'beat_ref': 'b01_01'},
                {'beat_ref': 'b01_02'},
            ],
        }],
        alias_to_ref={},
    )
    source = SourceChapter(
        chapter_ordinal=1, chapter_function='倒叙',
        characters=[SourceCharacter(char_id='char_001', surfaces=['我'], first_chapter=1)],
        beats=[
            SourceBeat(beat_ref='b01_01', content='A', participant_ids=['char_001']),
            SourceBeat(beat_ref='b01_02', content='B', participant_ids=['char_001']),
            SourceBeat(beat_ref='b01_03', content='C', participant_ids=['char_001']),
        ],
        states=[], entry_state_refs=[], carry_out_refs=[], constraints=[],
        global_subjects=[],
    )
    bindings = [
        DeliveryEvidenceBinding(delivery_ref=s.delivery_ref, evidence_refs=['e01_01'])
        for s in chapter.scenes[0].steps
    ]
    ir = build_writer_chapter_ir(
        design=chapter, source=source, bindings=bindings,
        target_chars_by_scene={'s01': 500},
    )
    assert [s.beat_ref for s in ir.scenes[0].steps] == ['b01_03', 'b01_01', 'b01_02']


def test_compile_presentation_stable_payload():
    kwargs = dict(
        output_ordinal=2, weight='CH_EVEN',
        structure={'mode': 'reconstruct',
                   'declared_change_kinds': ['presentation_grouping', 'disclosure_order']},
        goal={'intent': 'G_ADVANCE', 'focus_refs': ['st02_01'],
              'anchor_beat_ref': 'b02_01', 'anchor_scene_ref': 's01'},
        open_hook={'intent': 'G_OPEN', 'focus_refs': ['st02_01'],
                   'anchor_beat_ref': 'b02_01', 'anchor_scene_ref': 's01'},
        end_hook={'intent': 'G_CLOSE', 'focus_refs': ['st02_01'],
                  'anchor_beat_ref': 'b02_01', 'anchor_scene_ref': 's01'},
        scenes=[{
            'scene_ref': 's01', 'role': 'setup', 'delivery': 'scene',
            'transition': 'start', 'weight': 'W_MID', 'purpose': '稳',
            'turn_beat_ref': 'b02_01',
            'steps': [{'beat_ref': 'b02_01'}],
        }],
    )
    a = compile_presentation_chapter(**kwargs)
    b = compile_presentation_chapter(**kwargs)
    assert a.model_dump() == b.model_dump()
