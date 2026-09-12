#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import encode_artifact
from contracts.schema.api import StructureApprovalRequest
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import BlueprintProvenance, ChapterBlueprints
from contracts.schema.stage4 import (
    CtxProvenance, EvidenceBinding, EvidenceBindingHumanProvenance, PlanChapters, PlanProvenance,
    WriterContext,
)
from contracts.artifacts import (
    STAGE2_CHARACTERS, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_KNOWLEDGE_LINKS, STAGE4_EVIDENCE_BINDING, STAGE4_WRITER_CONTEXT,
)
from novel.orchestrator import approvals
from novel.orchestrator import queue as Q
from novel.run import RunContext
from novel.store import ArtifactStore
from novel.stages.stage4.plot_design import candidate_subject_sha256
from tests.test_design_review import FACT, PERSON, _subjects


SID = '123'
MAIN_REL = 'stage1/main_text.json'
PLAN_REL = 'stage4/plan_chapters.json'


def _disk_json(book: Path, rel: str, value: object) -> None:
    path = book / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(encode_artifact(rel, value), ensure_ascii=False),
        encoding='utf-8', newline='',
    )


def _plan(ctx: RunContext) -> PlanChapters:
    """身份反例使用完整两章，不能用缺章计划绕过正式章集。"""
    chapters = []
    for ordinal in (1, 2):
        beat = f'b{ordinal:02d}_01'
        def goal(intent):
            return {'intent': intent, 'subject_refs': [{'kind': 'beat', 'ref': beat}],
                    'anchor_beat_ref': beat, 'anchor_scene_ref': 's01'}
        chapters.append({
            'output_ordinal': ordinal, 'from_source_ordinals': [ordinal],
            'chapter_goal': goal('advance'), 'open_hook_goal': goal('open'),
            'end_hook_goal': goal('close'),
            'structure_decision': {'mode': 'retain', 'reason': '只核对审批身份。',
                                   'declared_change_kinds': []},
            'chapter_rewrite_brief': '一场完整呈现本章剧情。',
            'scenes': [{'slot': 's01', 'budget_chars': 20, 'purpose': '呈现本章',
                'role': 'setup', 'delivery': 'scene', 'transition': 'start',
                'context_beat_refs': [], 'turn_beat_ref': beat}],
            'beat_placements': [{'beat_ref': beat, 'deliveries': [
                {'scene_ref': 's01', 'presentation_instruction': None,
                 'evidence_refs': [f'e{ordinal:02d}_01'], 'source_context_refs': []}]}],
            'anchor_bindings': [],
        })
    return PlanChapters(output_chapters=chapters, _provenance=ctx.free_provenance(
        PlanProvenance, origin='human', blind_candidate_sha256=None, evidence_binding_sha256=None))


def _binding(ctx: RunContext) -> EvidenceBinding:
    """合成绑定仍保留完整两章身份和真实上游读取边。"""
    from tests.test_design_review import _mixed_repair_subjects
    subjects = _mixed_repair_subjects()
    candidate = {
        'alias_algorithm': subjects.strategy.basis.alias_algorithm,
        'truth_sha256': approvals._decision_sha256([
            row.blind_truth.model_dump(mode='json') for row in subjects.chapters]),
        'subject_aliases': {FACT: {'kind': 'beat', 'ref': 'b01_01'},
                            PERSON: {'kind': 'character', 'ref': 'char_001',
                                     'actor': {'char_id': 'char_001', 'presentation_ref': None}}},
        'endpoint_aliases': {}, 'participant_aliases': {
            PERSON: {'char_id': 'char_001', 'presentation_ref': None}},
        'reconstruction_strategy': subjects.strategy.reconstruction_strategy,
        'output_chapters': [row.chapter.model_dump(mode='json') for row in subjects.chapters],
    }
    candidate_sha = candidate_subject_sha256(candidate)
    return EvidenceBinding(status='bound', candidate_sha256=candidate_sha,
        chapters=[{'output_ordinal': n, 'scenes': [{
            'candidate_scene_id': f'c{n:02d}s01', 'deliveries': [{
                'beat_ref': f'b{n:02d}_01', 'evidence_refs': [f'e{n:02d}_01'],
                'source_context_refs': []}]}]} for n in (1, 2)], binding_concerns=[],
        _provenance=ctx.free_provenance(EvidenceBindingHumanProvenance, candidate_sha256=candidate_sha))


def _seed_core(base: Path, main: MainText):
    """合成有据人物及合法空知识；独立正式写入，不伪造模型或语义接受。"""
    from contracts.schema.characters import CharacterRegistry, CharactersProvenance
    from contracts.schema.knowledge import KnowledgeGrounding, GlobalProvenance
    from novel.stages.stage2.grounding import knowledge_reference_sha256
    from tests.plot_fixtures import GLOBAL

    character_ctx = RunContext.open(base, SID, 'stage2.characters', paid=True, write_ledger=False)
    character_store = ArtifactStore(character_ctx)
    character_store.read(MainText, MAIN_REL)
    text = main.root[0].text
    evidence = 'ev_' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]
    registry = CharacterRegistry.model_validate({'characters': {'char_001': {
        'canonical': '核账人', 'aliases': [{'surface': '核账人', 'identity_revealed_from': 1,
            'evidence_refs': [evidence]}], 'first_chapter': 1,
        'first_appearance_evidence_refs': [evidence], 'role': '核对计划的人',
        'role_evidence_refs': [evidence], 'name_distinctiveness': 'common'}},
        'evidence_anchors': {evidence: {'chapter_ordinal': 1, 'start': 0, 'end': len(text),
            'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}},
        '_provenance': character_ctx.llm_provenance(CharactersProvenance,
            model='fixture-model', adopted_models=('fixture-model',),
            prompts={'FIXTURE_ONLY': '合成有据人物；未发模型'})})
    character_store.write(STAGE2_CHARACTERS, registry)
    global_ctx = RunContext.open(base, SID, 'stage2.global', paid=True, write_ledger=False)
    global_store = ArtifactStore(global_ctx)
    global_store.read(MainText, MAIN_REL)
    global_store.read(CharacterRegistry, STAGE2_CHARACTERS)
    grounding = KnowledgeGrounding.model_validate({**GLOBAL,
        'characters_reference_sha256': knowledge_reference_sha256(registry),
        '_provenance': global_ctx.llm_provenance(GlobalProvenance,
            model='fixture-model', adopted_models=('fixture-model',),
            prompts={'FIXTURE_ONLY': '合成空长期知识；未发模型'}, evidence_locator_failures=[])})
    global_store.write(STAGE2_GLOBAL_GROUNDING, grounding)
    return registry, grounding


@pytest.fixture
def current_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    book = tmp_path / f'审批证明_{SID}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n核账人核对了当前计划。\n', encoding='utf-8', newline='')
    main = MainText.model_validate([
        {'chapter_ordinal': 1, 'text': '核账人核对了当前计划。'},
        {'chapter_ordinal': 2, 'text': '另一章保持不变。'},
    ])
    _disk_json(book, MAIN_REL, main.model_dump(mode='json'))
    registry, grounding = _seed_core(tmp_path, main)

    # 结构 Commander 实际读取的证据必须是真实 typed 产物；未发模型，来源明确标测试。
    from tests.test_stage2 import blueprints
    blueprint_ctx = RunContext.open(
        tmp_path, SID, 'stage2.blueprint', paid=True, write_ledger=False)
    blueprint_store = ArtifactStore(blueprint_ctx)
    blueprint_store.read(MainText, MAIN_REL)
    from contracts.schema.plot import PlotChapterResponse
    from novel.stages.stage2.plot_compile import compile_chapter
    chapters = []
    for chapter in main.root:
        ordinal, text = chapter.chapter_ordinal, chapter.text
        response = PlotChapterResponse.model_validate({
            'chapter_ordinal': ordinal, 'chapter_function': text,
            'beats': [{'beat_key': 'b1', 'content': text,
                'participants': [{'char_id': 'char_001'}] if ordinal == 1 else [],
                'evidence': [{'start_para': 1, 'end_para': 1}], 'source_contexts': []}],
            'states': [], 'inherited_state_endings': [], 'constraints': [],
            'entry_state_refs': [], 'carry_out_refs': [],
            'narration': [{'grammatical_person': 'third', 'narrator': None,
                          'focalizer': None, 'pronoun_surfaces': [], 'beat_refs': [],
                          'evidence': [{'start_para': 1, 'end_para': 1}]}]})
        chapters.append(compile_chapter(response, text, allowed_char_ids=frozenset(registry.characters),
            character_bindings={cid: [row.model_dump(mode='json') for row in character.aliases]
                                for cid, character in registry.characters.items()}).model_dump(mode='json'))
    blueprint_artifact = ChapterBlueprints(
        blueprints=chapters,
        _provenance=blueprint_ctx.llm_provenance(
            BlueprintProvenance, model='fixture-model',
            prompts={'BLUEPRINT_SYS': '合成审批夹具，无真实模型请求'},
            adopted_models=('fixture-model',), **blueprints()['_provenance']),
    )
    blueprint_store.write(STAGE2_CHAPTER_BLUEPRINTS, blueprint_artifact)

    # 本组只测审批来源身份；旁路的是既有 Core/KL 语义审阅前置，仍读取真实当前 typed 文件。
    from novel.stages.stage2 import global_review, grounding as grounding_io, knowledge_links
    from tests.plot_fixtures import links
    from contracts.schema.knowledge_links import KnowledgeLinks, KnowledgeLinksProvenance
    link_ctx = RunContext.open(tmp_path, SID, 'stage2.blueprint', paid=False, write_ledger=False)
    link_store = ArtifactStore(link_ctx)
    link_store.read(MainText, MAIN_REL)
    link_store.read(ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
    association = KnowledgeLinks.model_validate({**links(), '_provenance': link_ctx.free_provenance(
        KnowledgeLinksProvenance, decision_sha256='5' * 64)})
    link_store.write(STAGE2_KNOWLEDGE_LINKS, association)
    monkeypatch.setattr(global_review, 'generation_grounding', grounding_io.read)
    monkeypatch.setattr(knowledge_links, 'require_current',
        lambda _ctx: ArtifactStore(_ctx).read(KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS))

    plan_ctx = RunContext.open(
        tmp_path, SID, 'stage4.plan', paid=True, write_ledger=False)
    plan_store = ArtifactStore(plan_ctx)
    plan_store.read(MainText, MAIN_REL)
    plan_artifact = _plan(plan_ctx)
    plan_store.write(PLAN_REL, plan_artifact)
    # B 是结构审批的真实 typed 输入；不能用裸 {} 配合 concerns 替身绕过它。
    plan_store.write(STAGE4_EVIDENCE_BINDING, _binding(plan_ctx))
    binding, _raw = plan_store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    assert binding.provenance.inputs
    assert plan_store.freshness(STAGE4_EVIDENCE_BINDING).state == 'fresh'
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.stages.stage4.plot_context import build_writer_context
    context_ctx = RunContext.open(tmp_path, SID, 'stage4.context', paid=False, write_ledger=False)
    context_store = ArtifactStore(context_ctx)
    for model, rel in ((MainText, MAIN_REL), (CharacterRegistry, STAGE2_CHARACTERS),
                       (KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING),
                       (ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS),
                       (KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS), (PlanChapters, PLAN_REL)):
        context_store.read(model, rel)
    payload = build_writer_context(plan_artifact.model_dump(mode='json'),
        grounding.model_dump(mode='json'), blueprint_artifact.model_dump(mode='json'),
        main.model_dump(mode='json'), knowledge_links=association.model_dump(mode='json'),
        characters=registry.model_dump(mode='json')).ctx
    context_store.write(STAGE4_WRITER_CONTEXT, WriterContext.model_validate({**payload,
        '_provenance': context_ctx.free_provenance(CtxProvenance)}))

    monkeypatch.setitem(approvals._GATE_REQUIREMENTS, 's4', ())
    monkeypatch.setattr(approvals.prov, 'code_version', lambda: 'fixture-readonly-code')
    monkeypatch.setattr(approvals, '_spawns', lambda _base, _sid: {})
    require_fresh_json = approvals._require_fresh_json

    def require_synthetic_stage1(store, rel):
        if rel == MAIN_REL:
            return hashlib.sha256(store.path(rel).read_bytes()).hexdigest()
        return require_fresh_json(store, rel)

    monkeypatch.setattr(approvals, '_require_fresh_json', require_synthetic_stage1)
    return tmp_path, book


def _approve_writing(base: Path) -> None:
    template = approvals.structure_template(
        base,
        SID,
        actor='freshness-reviewer',
        reason='核对当前结构主体与精确章集',
    )
    approvals.approve_structure(
        base,
        SID,
        StructureApprovalRequest(
            actor=template.actor,
            reason=template.reason,
            review_token=template.review_token,
            chapters=[{
                'output_ordinal': row.output_ordinal,
                'decision': 'accept',
                'reason': f'第 {row.output_ordinal} 章结构已逐项核对',
            } for row in template.chapters],
        ),
    )
    approvals.begin_writing(base, SID)


def _queue_stage5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Q, 'nodes_up_to', lambda _until: ['stage5.write'])
    monkeypatch.setattr(
        Q.st, 'overview', lambda _base, _sid: SimpleNamespace(stages=[]))
    monkeypatch.setattr(
        Q.st, 'resume_verdict',
        lambda _overview, _node: (Q.st.RESUME_RUN, 'stage5 drafts are missing'))


def test_current_writing_proof_binds_stage1_plan_and_context_without_s3(
    current_book, monkeypatch,
):
    base, _book = current_book
    _approve_writing(base)

    raw = json.loads(approvals._path(base, SID).read_text(encoding='utf-8'))
    public = approvals.get(base, SID)
    assert raw['v'] == 8 and public.v == 8
    assert set(raw['gate']) == {'s0', 's1', 's2', 's4', 's5', 's6', 's7'}
    assert set(raw['writing_proof']['artifacts']) == {
        MAIN_REL, PLAN_REL, STAGE4_WRITER_CONTEXT}
    assert raw['writing_proof']['decision_sha256']
    assert public.writing and approvals.has_record(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert blocked == [] and len(todo) == 1
    assert todo[0].node == 'stage5.write' and todo[0].flags == []
    assert 'mode' not in todo[0].model_dump()


def test_retired_v6_with_current_typed_histories_is_not_migrated(
    current_book,
):
    """已有可对证的写作与结构历史，也不能跳过版本边界重新签入当前工作流。"""
    base, _book = current_book
    _approve_writing(base)
    request = approvals.writing_template(
        base,
        SID,
        actor='legacy-writer-reviewer',
        reason='旧 v6 已核对写作主体',
    )
    approvals.approve_writing(base, SID, request)

    before = approvals._read_raw(base, SID)
    assert before.structure_decision is not None
    assert before.writing_decision is not None
    payload = before.model_dump(
        mode='json',
        exclude={'closure_history', 'closure_decision', 'seen_subjects',
                 'stage2_review_history', 'stage2_reviews',
                 'stage2_rework_completed', 'stage2_patch_receipts', 'stage2_blueprint_scope'})
    payload['v'] = 6
    path = approvals._path(base, SID)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    before_bytes = path.read_bytes()
    with pytest.raises(approvals.HumanEditProtected, match='均不迁移'):
        approvals.backfill_from_disk(base, SID)
    assert path.read_bytes() == before_bytes
    with pytest.raises(approvals.HumanEditProtected, match='均不迁移'):
        approvals.get(base, SID)


@pytest.mark.parametrize('mutation', ['root', 'main', 'plan', 'blueprint', 'context', 'invalid'])
def test_current_subject_changes_revoke_writing(current_book, monkeypatch, mutation):
    base, book = current_book
    _approve_writing(base)
    original: bytes | None = None

    if mutation == 'root':
        changed = book / 'stage0' / 'clean_text.txt'
        original = changed.read_bytes()
        changed.write_text(
            '另一版正文根。', encoding='utf-8', newline='')
    elif mutation == 'main':
        changed = book / MAIN_REL
        original = changed.read_bytes()
        _disk_json(book, MAIN_REL, [
            {'chapter_ordinal': 1, 'text': '上游正文已经变化。'},
            {'chapter_ordinal': 2, 'text': '另一章保持不变。'},
        ])
    elif mutation == 'plan':
        changed = book / PLAN_REL
        original = changed.read_bytes()
        data = json.loads(changed.read_text(encoding='utf-8'))
        data['output_chapters'][0]['chapter_rewrite_brief'] = '当前计划字节已由人改动。'
        changed.write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8', newline='')
    elif mutation == 'blueprint':
        changed = book / STAGE2_CHAPTER_BLUEPRINTS
        original = changed.read_bytes()
        data = json.loads(changed.read_text(encoding='utf-8'))
        data['blueprints'][0]['beats'][0]['content'] = '她发现当前计划有一处矛盾。'
        changed.write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8', newline='')
    elif mutation == 'context':
        changed = book / STAGE4_WRITER_CONTEXT
        original = changed.read_bytes()
        changed.write_text(
            '{"changed":true}', encoding='utf-8')
    else:
        changed = book / 'stage4' / 'plan_chapters.INVALID.json'
        changed.write_bytes((book / PLAN_REL).read_bytes())

    assert not approvals.get(base, SID).writing
    assert not approvals.has_record(base, SID)
    raw = approvals._read_raw(base, SID)
    assert raw.writing and raw.writing_proof is not None
    assert raw.gate['s4'] and raw.gate_proofs['s4'] is not None

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL
    assert blocked[0].steps == []

    if original is None:
        changed.unlink()
    else:
        changed.write_bytes(original)
    assert approvals.get(base, SID).writing
    assert approvals.has_record(base, SID)


def test_writing_requires_current_typed_s4_only(current_book, monkeypatch):
    base, _book = current_book
    with pytest.raises(
            approvals.ApprovalRequired,
            match=r'stage4\.context|final structure'):
        approvals.begin_writing(base, SID)

    _queue_stage5(monkeypatch)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].waiting_on == Q.WAITING_ON_WRITING_APPROVAL
    assert blocked[0].steps == []


def test_measurement_contract_change_revokes_writing(current_book, monkeypatch):
    base, _book = current_book
    _approve_writing(base)
    original = approvals.STYLE_MEASUREMENT_CONTRACT
    monkeypatch.setattr(
        approvals, 'STYLE_MEASUREMENT_CONTRACT', 'stage3-measurable-v999')
    assert not approvals.get(base, SID).writing
    raw = approvals._read_raw(base, SID)
    assert raw.writing and raw.writing_proof is not None

    monkeypatch.setattr(approvals, 'STYLE_MEASUREMENT_CONTRACT', original)
    assert approvals.get(base, SID).writing


def test_retired_stage3_bytes_do_not_participate_in_current_v5(current_book):
    base, book = current_book
    legacy = book / 'stage3' / 'style_grounding.json'
    legacy.parent.mkdir()
    legacy.write_text('{"historical":true}', encoding='utf-8')
    _approve_writing(base)

    legacy.write_text('{"historical":"changed"}', encoding='utf-8')
    assert approvals.get(base, SID).writing
    legacy.unlink()
    assert approvals.get(base, SID).writing


def test_backfill_never_manufactures_typed_s4_and_invalid_revokes_explicit_decision(
    current_book, monkeypatch,
):
    base, book = current_book
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s4',))

    first = approvals.backfill_from_disk(base, SID)
    assert first.backfilled == []
    assert not first.approvals.gate['s4']

    _approve_writing(base)
    assert approvals.has_record(base, SID)
    (book / 'stage4' / 'plan_chapters.INVALID.json').write_bytes(
        (book / PLAN_REL).read_bytes())

    assert not approvals.has_record(base, SID)
    second = approvals.backfill_from_disk(base, SID)
    assert second.backfilled == []
    assert not second.approvals.gate['s4']


def test_backfill_rejects_legacy_arm_layout_and_does_not_stamp_stage5(
    current_book, monkeypatch,
):
    base, book = current_book
    _approve_writing(base)
    for mode in ('deep', 'shallow'):
        path = book / 'stage5' / mode / 'ch01.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
    (book / 'stage5' / 'draft.md').write_text('正文。', encoding='utf-8')
    monkeypatch.setattr(approvals, '_GATE_ORDER', ('s5',))

    result = approvals.backfill_from_disk(base, SID)
    assert result.backfilled == []
    assert not result.approvals.gate['s5']


def test_v1_or_corrupt_service_record_never_authorizes_queue(
    current_book, monkeypatch,
):
    base, _book = current_book
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'v': 1, 'sid': SID,
        'gate': {f's{i}': True for i in range(8)},
        'seen': {'read': True, 'global': True},
        'deep': [1], 'writing': True, 'spawns': {},
    }), encoding='utf-8')

    assert not approvals.has_record(base, SID)
    with pytest.raises(approvals.HumanEditProtected, match='当前 v8'):
        approvals.get(base, SID)
    with pytest.raises(approvals.HumanEditProtected, match='均不迁移'):
        approvals.backfill_from_disk(base, SID)

    _queue_stage5(monkeypatch)
    original = path.read_bytes()
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].steps == [] and '审批记录损坏' in blocked[0].why
    assert path.read_bytes() == original

    path.write_text('{broken', encoding='utf-8')
    assert not approvals.has_record(base, SID)
    todo, blocked = Q.plan_book(base, SID, until_stage='stage5')
    assert todo == [] and blocked[0].waiting_on == Q.WAITING_ON_INBOX
    assert blocked[0].steps == [] and path.read_text(encoding='utf-8') == '{broken'


def _write_legacy_v2(base: Path, *, deep: list[int]) -> None:
    proof = {
        'root_clean_sha1': '0' * 40,
        'artifacts': {PLAN_REL: 'a' * 64},
        'decision_sha256': 'b' * 64,
    }
    gates = {f's{i}': False for i in range(8)}
    gates.update({'s3': True, 's4': True})
    gate_proofs = {f's{i}': None for i in range(8)}
    gate_proofs.update({'s3': proof, 's4': proof})
    path = approvals._path(base, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'v': 2, 'sid': SID, 'gate': gates,
        'seen': {'read': False, 'global': False},
        'deep': deep, 'writing': True,
        'gate_proofs': gate_proofs,
        'seen_proofs': {'read': None, 'global': None},
        'deep_proof': proof,
        'writing_proof': proof,
    }, ensure_ascii=False), encoding='utf-8')


@pytest.mark.parametrize('deep', ([1], []), ids=['all-deep', 'has-shallow'])
def test_retired_v2_keeps_old_stage3_and_writing_proofs_unmodified(
    current_book, deep,
):
    base, _book = current_book
    _write_legacy_v2(base, deep=deep)

    path = approvals._path(base, SID)
    before = path.read_bytes()
    with pytest.raises(approvals.HumanEditProtected, match='均不迁移'):
        approvals.backfill_from_disk(base, SID)
    assert path.read_bytes() == before
    assert not approvals.has_record(base, SID)
