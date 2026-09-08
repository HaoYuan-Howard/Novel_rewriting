from __future__ import annotations

import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema.evaluation import TrimAudit
from contracts.schema.stage4 import CtxChapter, FactRef, PlanGoal, WriterContext
import contracts.schema as schema_exports
import contracts.schema.stage5 as stage5_schema
from contracts.schema.stage5 import ChapterResponse, ChapterSegment
from novel.errors import RefusedByStructure
from novel.llm.privacy import (
    PromptPrivateLeak, assert_prompt_private_absent, private_tokens,
)
from novel.llm.transport import ChatResponse
from novel.stages.stage5 import prompts, write
from novel.stages.stage5 import checks
from novel.stages.stage4 import context as stage4_context
from tests.factories import stage5_book


def _entry() -> tuple[dict, object]:
    book = stage5_book()
    return book.chapters[min(book.chapters)], book


def test_writer_response_slots_are_complete_unique_and_canonicalised():
    entry, _book = _entry()
    second = {**entry['scenes'][0], 'slot': 's02'}
    entry = {**entry, 'scenes': [entry['scenes'][0], second]}
    response = ChapterResponse.model_validate({
        'segments': [
            {'slot': 's02', 'text': '后段。'},
            {'slot': 's01', 'text': '前段。'},
        ],
    })
    assert all(isinstance(segment, ChapterSegment) for segment in response.segments)
    assert not hasattr(stage5_schema, 'GeneratedSegment')
    assert not hasattr(schema_exports, 'GeneratedSegment')
    assert write._response_problem(
        response, ChatResponse(content='', finish_reason='stop'), entry) is None
    assert write._ordered_segments(response, entry) == [
        {'slot': 's01', 'text': '前段。'}, {'slot': 's02', 'text': '后段。'}]

    missing = ChapterResponse.model_validate({
        'segments': [{'slot': 's01', 'text': '只有一段。'}]})
    assert '恰好' in str(write._response_problem(
        missing, ChatResponse(content='', finish_reason='stop'), entry))
    with pytest.raises(ValidationError, match='duplicate'):
        ChapterResponse.model_validate({'segments': [
            {'slot': 's01', 'text': '一。'}, {'slot': 's01', 'text': '二。'}]})


def test_writing_gate_requires_fidelity_material_in_its_declared_segment():
    entry, book = _entry()
    required = '这句必须留在第一幕，一个字也不能挪。'
    first = {**entry['scenes'][0], 'verbatim': [required]}
    second = {**entry['scenes'][0], 'slot': 's02', 'verbatim': []}
    entry = {**entry, 'scenes': [first, second]}
    source = dict(book.source_text)
    source_ordinal = int(entry['from_source_ordinals'][0])
    source[source_ordinal] += '\n' + required
    book = type(book)(title=book.title, ctx=book.ctx, measurable=book.measurable,
                      source_text=source, secrets=book.secrets)
    returned = [
        {'slot': 's01', 'text': '第一幕只有别的内容。' * 4},
        {'slot': 's02', 'text': '第二幕误放了：' + required + '其余故事仍在继续。' * 4},
    ]
    # 整章确实含 required；只有真正穿过 ``write._judge -> chapter_findings``
    # 的 producer-slot 判据才会拦住这份稿，不能拿 helper 直调冒充生产链覆盖。
    text = '\n\n'.join(segment['text'] for segment in returned)
    hits, errs, _warns = write._judge(
        text, entry, book, mat='', account=write.M.NormalizeAccount(),
        segments=returned)
    assert any(hit.rule == 'verbatim_missing' and hit.evidence[0] == 's01'
               for hit in hits)
    assert any('[verbatim_missing]' in err for err in errs)
    assert hits and {hit.output_ordinal for hit in hits} == {entry['output_ordinal']}


def test_segment_facts_are_checked_in_their_declared_scene_without_source_order():
    entry, book = _entry()
    entry = {
        **entry,
        'characters': [
            *entry['characters'],
            {'char_id': 'char_002', 'display_name': '周衡', 'role': '账房'},
        ],
        'scenes': [{
            **entry['scenes'][0],
            'required_facts': ['沈青查看账册。', '周衡关门。'],
            'turn_fact': '周衡关门。',
        }],
    }
    reordered_hits = checks.segment_fact_findings(
        [{'slot': 's01', 'text': '周衡随后关门。沈青才查看账册。'}],
        entry, book)
    assert not any(hit.rule == 'fact_landing' for hit in reordered_hits)

    unver_entry = {
        **entry,
        'scenes': [{**entry['scenes'][0],
                    'required_facts': ['风声变紧，局势更危险。'],
                    'turn_fact': '风声变紧，局势更危险。'}],
    }
    unver_hits = checks.segment_fact_findings(
        [{'slot': 's01', 'text': '风更紧了。'}], unver_entry, book)
    assert any(hit.rule == 'fact_landing_unver' for hit in unver_hits)


def test_prompt_private_values_are_derived_from_schema_metadata_recursively():
    goal = PlanGoal(
        intent='advance',
        fact_refs=[FactRef(kind='fact_atom', ref='f01_01')],
    )
    assert [(token.path, token.value) for token in private_tokens(goal)] == [
        ('fact_refs[0].ref', 'f01_01'),
    ]
    with pytest.raises(PromptPrivateLeak, match='fact_refs'):
        assert_prompt_private_absent(
            goal, system='系统', user='意外包含：f01_01')


def test_context_schema_rejects_retired_must_not_reveal():
    entry, book = _entry()
    entry = {**entry, 'must_not_reveal': ['secret_never_send_987']}
    with pytest.raises(ValidationError, match='must_not_reveal'):
        CtxChapter.model_validate(entry)


@pytest.mark.parametrize('retired,mutate', [
    ('world_facts', lambda p: p.__setitem__('world_facts', {})),
    ('characters map', lambda p: p['chapters'][0].__setitem__('characters', {})),
    ('secret_id', lambda p: p['chapters'][0]['facts']['secrets'][0].__setitem__(
        'secret_id', 'secret_01')),
    ('rule_id', lambda p: p['chapters'][0]['facts']['world_rules'][0].__setitem__(
        'rule_id', 'wr_01')),
])
def test_writer_context_v4_rejects_retired_duplicate_fields(retired, mutate):
    entry, _book = _entry()
    payload = {
        'chapters': [deepcopy(entry)],
        '_provenance': {
            'code': 'test', 'root_clean_sha1': '1' * 40, 'inputs': {},
        },
    }
    if retired == 'secret_id' and not payload['chapters'][0]['facts']['secrets']:
        payload['chapters'][0]['facts']['secrets'] = [{
            'fact': '秘密事实', 'known_by': [], 'reader_knows_by_now': True,
        }]
    if retired == 'rule_id' and not payload['chapters'][0]['facts']['world_rules']:
        payload['chapters'][0]['facts']['world_rules'] = [{
            'kind': 'legal_rule', 'description': '规则正文',
        }]
    mutate(payload)
    with pytest.raises(ValidationError):
        WriterContext.model_validate(payload)


def test_final_writer_messages_project_character_id_and_human_facts():
    entry, book = _entry()
    entry = deepcopy(entry)
    char_id = 'char_never_send_987'
    entry['characters'] = [{
        'char_id': char_id, 'display_name': '周衡', 'role': '账房',
    }]
    entry['facts']['world_rules'] = [{
        'kind': 'legal_rule',
        'description': '旧城账册必须由掌柜与账房共同封存。',
    }]
    entry['facts']['secrets'] = [{
        'fact': '周衡已经调换账册末页。',
        'known_by': ['周衡'],
        'reader_knows_by_now': True,
    }]
    typed = CtxChapter.model_validate(entry)

    private = {(token.path, token.value) for token in private_tokens(typed)}
    assert ('characters[0].char_id', char_id) in private

    material = prompts.build_material(entry, book)
    system, user = prompts.write_system(), prompts.build_user(material)
    rendered = system + user
    assert char_id not in rendered
    for human_fact in ('周衡', '账房', '共同封存', '调换账册末页'):
        assert human_fact in user
    assert_prompt_private_absent(typed, system=system, user=user)


def test_writer_goal_uses_resolved_facts_and_never_internal_plan_ids():
    entry, book = _entry()
    material = prompts.build_material(entry, book)
    user = prompts.build_user(material)
    assert '本章事实：沈青发现账册末页的墨迹被人换过。' in user
    assert 'f01_01' not in user
    assert 'chapter:1:start' not in user
    assert 'fact_refs' not in user


def test_context_resolves_goal_ids_after_chapter_gates():
    goal = {
        'intent': 'advance',
        'fact_refs': [
            {'kind': 'fact_atom', 'ref': 'f01_01'},
            {'kind': 'character', 'ref': 'char_001'},
        ],
    }
    resolved = stage4_context._resolve_goal(
        goal, ordinal=1,
        atoms={'f01_01': {'claim': '沈青发现账册墨迹被换过。'}},
        disp={'char_001': '沈青'},
        facts={'relations': [], 'world_rules': [], 'secrets': [], 'setting': {}},
        global_g={'entities': {'relations': []}},
    )
    assert resolved == {
        'intent': 'advance',
        'facts': ['本章事实：沈青发现账册墨迹被换过。', '人物：沈青'],
    }
    assert not any(token in json.dumps(resolved, ensure_ascii=False)
                   for token in ('f01_01', 'char_001', 'fact_refs'))


def test_context_refuses_goal_that_would_bypass_a_chapter_gate():
    with pytest.raises(RefusedByStructure, match='无法在本章安全解析'):
        stage4_context._resolve_goal(
            {'intent': 'advance', 'fact_refs': [
                {'kind': 'world_rule', 'ref': 'wr_09'}]},
            ordinal=1, atoms={}, disp={},
            facts={'relations': [], 'world_rules': [], 'secrets': [], 'setting': {}},
            global_g={'entities': {'relations': []}},
        )


def test_hidden_secret_withhold_goal_does_not_disclose_identity_or_fact():
    resolved = stage4_context._resolve_goal(
        {'intent': 'withhold', 'fact_refs': [
            {'kind': 'secret', 'ref': 'secret_09'}]},
        ordinal=1, atoms={}, disp={},
        facts={'relations': [], 'world_rules': [], 'secrets': [], 'setting': {}},
        global_g={'entities': {'relations': []}},
    )
    wire = json.dumps(resolved, ensure_ascii=False)
    assert 'secret_09' not in wire
    assert '尚未公开' in wire


def test_writer_system_requires_json_segments_and_user_names_each_slot():
    entry, book = _entry()
    system = prompts.write_system()
    user = prompts.build_user(prompts.build_material(entry, book))
    assert 'Return ONLY valid JSON' in system
    assert '"segments"' in system
    assert 'slot=s01' in user
    json.dumps(prompts.CHAPTER_RESPONSE_HINT, ensure_ascii=False)


def _trim_case():
    """一章明显超上限的原稿：目标区间按候选长度定，原稿在它后面又堆了一大段。"""
    raw_entry, book = _entry()
    entry = deepcopy(raw_entry)
    candidate = (
        '沈青在茶馆核对账册。随后，她确认墨迹被人换过，却没有仓促下结论。'
        '窗外雨声渐紧，她把账册收好，决定天亮后再查。')
    entry['budget_chars'] = len(candidate)
    entry['scenes'][0]['budget_chars'] = len(candidate)
    original = candidate + '这段解释重复了同一个意思。' * 8
    return entry, book, [{'slot': 's01', 'text': original}]


def test_over_length_lands_an_identity_audit_and_cannot_spend_anything():
    """超上限 ⇒ 落 identity audit 转人工。**结构上花不了钱**，不是「碰巧没调用」。

    ⚠ 第二段断言才是这条守卫的本体：`_over_length_audit` 的签名里**没有 client**。
    「这次没发请求」可以靠 mock 装出来，「拿不到 client」不行。
    """
    import inspect

    entry, _book, original = _trim_case()
    notes, audit = write._over_length_audit(original, entry)
    assert audit is not None
    assert audit.output_ordinal == int(entry['output_ordinal'])
    assert audit.before_sha256 == write._sha256_text(write._segments_text(original))
    assert notes == [audit.reason]
    assert '超上限' in audit.reason and 'verification' in audit.reason

    params = inspect.signature(write._over_length_audit).parameters
    assert 'client' not in params, (
        '收缩臂已整臂下线；这个函数一旦重新拿到 client，就等于把付费回路悄悄接了回来')


def test_within_the_target_band_produces_no_audit_at_all():
    """没超上限 ⇒ 既没有提示也**没有 audit**——空 audit 会在 inbox 里凭空长出待办。"""
    entry, _book, _original = _trim_case()
    short = [{'slot': 's01', 'text': '短短一句。'}]
    assert write._over_length_audit(short, entry) == ([], None)


def test_over_length_audit_stays_in_verification(tmp_path):
    from contracts.artifacts import artifact_rel
    from novel import prov
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger
    from novel.run import RunContext
    from novel.work_units import initialize_workflow
    from novel.store import ArtifactStore
    from novel.views.inbox import _trim_verification_items

    audit = TrimAudit(
        output_ordinal=4, before_sha256='a' * 64,
        reason='超上限 120 字（620/500）；本仓没有自动收缩路径，保留 pretrim，转 verification')
    initialize_workflow(tmp_path)
    root = tmp_path / '收缩审计_42'
    try:
        writer = RunContext(
            novel_dir=root, node='stage5.write', paid=False,
            code=prov.code_version(), cache=CachePolicy.default(), ledger=CostLedger())
        store = ArtifactStore(writer)
        store.write(artifact_rel('stage5.trim_audit', nn=4), audit)
        # Rendering is tested with an explicitly current audit set; discovery
        # itself intentionally rejects an audit that has no matching formal chapter.
        rows = _trim_verification_items(
            root, prov.code_version(), audits={4: (audit, 'c' * 64)})
        assert [(row.rule, row.chapter) for row in rows] == [
            ('trim_audit_ch04', 4)]
        assert '超上限' in rows[0].what and 'pretrim' in rows[0].how
    finally:
        shutil.rmtree(root, ignore_errors=True)
