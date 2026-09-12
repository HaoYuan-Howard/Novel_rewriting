"""证据自洽的三节点小章，供正式编译、判据及拒绝路径使用；不宣称模型验收。"""
from __future__ import annotations

from contracts.schema.knowledge_links import KnowledgeLinks
from contracts.schema.plot import PlotChapterResponse
from novel.stages.stage2.plot_compile import compile_chapter

TEXT = '甲收到入住邀请。\n\n乙在门口拒绝甲。\n\n甲决定留下。'
MAIN = [{'chapter_ordinal': 1, 'text': TEXT}]
CHARACTERS = {
    cid: {'canonical': name, 'role': role, 'name_distinctiveness': 'high', 'first_chapter': 1,
          'aliases': [{'surface': name, 'identity_revealed_from': 1, 'evidence_refs': []}],
          'first_appearance_evidence_refs': [], 'role_evidence_refs': []}
    for cid, name, role in [('char_001', '甲', '受邀住客'), ('char_002', '乙', '门口的住客')]
}
REGISTRY = {'characters': CHARACTERS, 'evidence_anchors': {}}
GLOBAL = {'relations': [], 'arc_secrets': [], 'world_rules': [], 'evidence_anchors': {},
          'setting': {'era': None, 'social_context': None,
                      'era_evidence_refs': [], 'social_context_evidence_refs': []}}


def blueprint():
    def evidence(n):
        return [{'start_para': n, 'end_para': n}]
    response = {
        'chapter_ordinal': 1, 'chapter_function': '建立入住冲突并决定留下',
        'beats': [
            {'beat_key': f'b{n}', 'content': content, 'participants': [{'char_id': cid} for cid in participants],
             'evidence': evidence(n), 'source_contexts': [{
                 'evidence': evidence(n), 'time_layer': 'present',
                 'place': '门外' if n == 1 else '门口', 'present': [{'char_id': cid} for cid in participants]}]}
            for n, content, participants in [
                (1, '甲收到入住邀请。', ['char_001']),
                (2, '乙在门口拒绝甲入住。', ['char_001', 'char_002']),
                (3, '甲仍决定留下。', ['char_001']),
            ]],
        'states': [{'state_key': 'st1', 'content': '甲已决定留下。',
                    'global_ref': None, 'modality': 'world', 'holder': None,
                    'established_at': {'kind': 'at_beat', 'beat_ref': 'b3'}, 'ended_at': None,
                    'supersedes_state_refs': [], 'evidence': evidence(3)}],
        'inherited_state_endings': [], 'entry_state_refs': [], 'carry_out_refs': ['st1'],
        'constraints': [
            {'constraint_key': 'c1', 'kind': 'response_to',
             'observer': None,
             'before_beat_ref': 'b2', 'after_beat_ref': 'b3', 'evidence': evidence(2) + evidence(3)},
            {'constraint_key': 'c2', 'kind': 'object', 'target_beat_ref': 'b1',
             'value': '邀请', 'evidence': evidence(1)},
        ],
        'narration': [{'grammatical_person': 'third', 'narrator': None,
                       'focalizer': {'char_id': 'char_001'}, 'pronoun_surfaces': [],
                       'beat_refs': [], 'evidence': evidence(1)}],
    }
    return {'blueprints': [compile_chapter(PlotChapterResponse.model_validate(response), TEXT,
        allowed_char_ids=frozenset(CHARACTERS)).model_dump(mode='json')]}


def links():
    # 本样本无需要传播管理的独立知识项；正式 typed 合法空集与缺失不同。
    return KnowledgeLinks.model_validate({
        'scope': {'contract': 'stage2-knowledge-links-v3', 'scope_sha256': '1' * 64,
                  'main_text_sha256': '2' * 64, 'knowledge_sha256': '3' * 64,
                  'blueprint_sha256': '4' * 64, 'obligations': []},
        'links': [], '_provenance': {'code': 'fixture', 'root_clean_sha1': '0' * 40,
                                    'inputs': {}, 'decision_sha256': '5' * 64},
    }).model_dump(mode='json', by_alias=True)


def plan():
    from novel.stages.stage4.measure import identity_plan
    return identity_plan(blueprint(), MAIN)


def context(candidate=None):
    from novel.stages.stage4.plot_context import build_writer_context
    return build_writer_context(plan() if candidate is None else candidate, GLOBAL,
                                blueprint(), MAIN, knowledge_links=links(), characters=REGISTRY).ctx
