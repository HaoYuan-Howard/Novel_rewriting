"""story-v3 每条 HARD 的生产红绿对照，输入均为明确的合成样例。

check 从合法底座只改变目标条件，真实调用 schema/compiler/validator/repository。
结论来自生产异常或 issue，不能用规则名或扫描测试源码代替执行证据。
细分分支与阅读质量分别由 stage/golden 套件承担。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from novel.errors import TerminalState


@dataclass(frozen=True)
class Case:
    stage: str
    check: Callable[[bool, Path], list[str]]
    expect: str


def _findings(action: Callable[[], object]) -> list[str]:
    try:
        result = action()
    except (ValueError, TerminalState) as exc:
        issues = getattr(exc, 'issues', None)
        return [f"{issue.code}: {getattr(issue, 'detail', getattr(issue, 'message', ''))}"
                for issue in issues] if issues else [str(exc)]
    if isinstance(result, (tuple, list)):
        return [f"{issue.code}: {getattr(issue, 'detail', getattr(issue, 'message', ''))}"
                for issue in result if hasattr(issue, 'code')]
    return []


def _source():
    from tests.test_source_story import _compiled
    return _compiled()


def _source_schema(valid, _path, *, change=None):
    from contracts.schema.source_story import SourceStory
    story = _source()[3].model_dump(mode='json')
    if not valid:
        (change or (lambda value: value.pop('contract_version')))(story)
    return _findings(lambda: SourceStory.model_validate(story))


def _source_identity(valid, _path):
    from novel.stages.stage2.story import validate_source_story
    index, _, _, story = _source()
    if not valid:
        story.source_identity = '0' * 64
    return _findings(lambda: validate_source_story(story, index=index))


def _source_refs(valid, path):
    def change(value):
        value['knowledge_updates'][0]['holder'] = 'unregistered-person'
    return _source_schema(valid, path, change=change)


def _source_state(valid, path):
    def change(value):
        slots = list(value['state_slots'].values())
        slots[1].update(subjects=slots[0]['subjects'], aspect=slots[0]['aspect'], focus=slots[0]['focus'])
    return _source_schema(valid, path, change=change)


def _source_threads(valid, path):
    def change(value):
        thread = next(iter(value['threads'].values()))
        thread['updates'] = thread['updates'][1:]
    return _source_schema(valid, path, change=change)


def _source_names(valid, _path):
    from novel.stages.stage2.story import compile_source_story
    from tests.test_source_story import _names
    index, chapters, book, _ = _source()
    names = _names(book)
    if not valid:
        names.people.pop(next(iter(names.people)))
    return _findings(lambda: compile_source_story(index=index, chapters=chapters,
                                                 book=book, names=names))


def _target_model(valid, _path, *, change=None):
    from tests.story_facilities import target_wires
    from novel.stages.stage4.story import compile_naming, compile_preserve, compile_target_model
    source, naming, preservation, model, *_ = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preservation, source, names)
    if not valid:
        change(model)
    return _findings(lambda: compile_target_model(model, contract, source_chapter_count=2,
                          total_length_budget=2001, name_map=names))


def _target_chapters(valid, path):
    return _target_model(valid, path, change=lambda value: value['chapters'].pop())


def _target_realization(valid, _path):
    from tests.story_facilities import compiled_case
    from novel.stages.stage4.story import compile_target_story
    _, names, preservation, model, story, *_ = compiled_case()
    if not valid:
        model.events[1].realizes = ()
    return _findings(lambda: compile_target_story(model, story.chapter_plans, preservation, names))


def _target_knowledge(valid, path):
    def change(value):
        value['propositions'][0]['status'] = 'refuted'
    return _target_model(valid, path, change=change)


def _packet(valid, _path):
    from tests.story_facilities import compiled_case
    from novel.stages.stage4.story import compile_hook, project_hook_packet
    *_, story, _adaptation, hook_wire = compiled_case()
    if not valid:
        hook_wire['allowed_knowledge'] = [{'holder': 'person_a', 'proposition_no': 1, 'mode': 'known'}]
    return _findings(lambda: project_hook_packet(story, compile_hook(hook_wire, story, length_budget=300)))


def _seal(valid, path):
    from novel.llm.ledger import CostLedger
    from novel.store import Str06Repository
    from tests.story_facilities import make_context
    from tests.test_story_prose import packet
    from contracts.story_paths import CHAPTER_PACKET
    rel = CHAPTER_PACKET.format(nn='01')
    repository = Str06Repository(path, make_context(path, ledger=CostLedger(run_id='synthetic'),
                                                   node='str06.stage4.compile'))
    repository.write(rel, packet())
    if not valid:
        actual = repository.path(rel)
        actual.write_bytes(actual.read_bytes() + b' ')
    return _findings(lambda: repository.inspect_sealed_model(rel))


def _target_names(valid, _path):
    from tests.story_facilities import target_wires
    from novel.stages.stage4.story import compile_naming
    source, naming, *_ = target_wires()
    if not valid:
        naming['entries'].pop()
    return _findings(lambda: compile_naming(naming, source))


def _prose(valid, _path):
    from tests.test_story_prose import packet
    from novel.stages.stage5.story import build_body_request, validate_prose_answer
    return _findings(lambda: validate_prose_answer(
        {'kind': 'prose', 'text': '沈青推门进屋。' if valid else ''}, build_body_request(packet())))


def _receipt(valid, _path):
    from tests.test_story_prose import packet, accepted
    from novel.stages.stage5.story import verify_body
    body = accepted('沈青推门进屋。', packet())
    if not valid:
        body = body.model_copy(update={'text': body.text + '未经回执授权的改写。'})
    return _findings(lambda: verify_body(body, packet()))


def _patch(valid, _path):
    from tests.test_story_prose import packet, accepted, patch_answer
    from novel.stages.stage5.story import opening_patch_request, validate_opening_patch_answer
    body = accepted('\n\n'.join(f'第{n}个自然段。' for n in range(1, 11)), packet())
    request = opening_patch_request(body, packet(), reason='修复入场承接')
    patch = patch_answer(request, replacement='沈青停在门边。').model_dump(mode='json')
    if not valid:
        patch['paragraphs'][-1]['handle'] = 'ninth-unauthorized'
    return _findings(lambda: validate_opening_patch_answer(patch, request, body, packet()))


def _hook(valid, _path):
    from tests.test_story_prose import hook_packet
    from novel.stages.stage5.story import accept_hook, build_hook_request, verify_hook
    packet = hook_packet()
    body = accept_hook('沈青等着拆信。', build_hook_request(packet),
                       response_id='synthetic-hook', provider='synthetic', model='synthetic')
    if not valid:
        body = body.model_copy(update={'receipt': body.receipt.model_copy(update={'packet_sha256': '0' * 64})})
    return _findings(lambda: verify_hook(body, packet))


def _delivery_inputs():
    from tests.test_story_prose import packet, accepted, hook_packet
    from novel.stages.stage5.story import accept_hook, build_hook_request
    packets = (packet(), packet(2))
    first = accepted('沈青走到门前。', packets[0])
    second = accepted('姐姐推开门，向她招手。', packets[1], prior=first.text)
    hook = hook_packet()
    return dict(title='合成样书', source_chapter_count=2, packets=packets,
        bodies=(first, second), hook_packet=hook,
        hook=accept_hook('沈青有一件心事。', build_hook_request(hook),
                        response_id='synthetic-hook', provider='synthetic', model='synthetic'))


def _delivery_chapters(valid, _path):
    from novel.stages.stage7.story import assemble_story
    inputs = _delivery_inputs()
    if not valid:
        inputs['bodies'] = inputs['bodies'][:1]
    return _findings(lambda: assemble_story(**inputs))


def _delivery_bytes(valid, _path):
    from novel.stages.stage7.story import assemble_story, verify_final_story
    inputs = _delivery_inputs()
    final = assemble_story(**inputs)
    if not valid:
        final = final.model_copy(update={'text': final.text + '未登记的结尾。'})
    return _findings(lambda: verify_final_story(final, **inputs))


CASES = {
    'story_source_contract': Case('stage2', _source_schema, 'contract_version'),
    'story_source_identity_evidence': Case('stage2', _source_identity, 'source_identity'),
    'story_source_references': Case('stage2', _source_refs, 'holder'),
    'story_source_state_address': Case('stage2', _source_state, '具体状态槽'),
    'story_source_threads': Case('stage2', _source_threads, '孤立'),
    'story_names_structure': Case('stage2', _source_names, 'name_coverage'),
    'story_target_chapters': Case('stage4', _target_chapters, '章节数'),
    'story_target_realization': Case('stage4', _target_realization, '没有目标承载'),
    'story_target_knowledge': Case('stage4', _target_knowledge, '确知'),
    'story_packet_projection': Case('stage4', _packet, '知情授权'),
    'story_artifact_seal': Case('stage4', _seal, '封存'),
    'story_target_naming': Case('stage4', _target_names, '覆盖'),
    'story_prose_complete': Case('stage5', _prose, 'answer_shape'),
    'story_prose_receipt': Case('stage5', _receipt, 'body'),
    'story_opening_patch_scope': Case('stage5', _patch, 'scope'),
    'story_hook_receipt': Case('stage5', _hook, 'packet'),
    'story_delivery_chapters': Case('stage7', _delivery_chapters, 'chapter_set'),
    'story_delivery_bytes': Case('stage7', _delivery_bytes, 'final_identity'),
}
