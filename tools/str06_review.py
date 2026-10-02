#!/usr/bin/env python3
"""STR-06 只读研发审阅：实际写作视图、义务承载及同身份已购补丁预检。

默认只打印，不写运行根；--save 将详细材料保存到本书 runtime/review/，不进入仓库。
补丁预检捕获生产 runner 的实际请求与接受函数，发送或写正式产物均被关闭式拒绝。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel import paths
from novel.config import load_config
from novel.llm.paid_requests import digest, purchased_baseline, semantic_owner_unit
from novel.store import Str06Repository
from novel.stages.chain import (
    ChainContext, ExecutionProfile, NODE_RUNNERS, SYS, BODY_WRITER_SYSTEM, HOOK_WRITER_SYSTEM,
    _chapter_evidence, build_evidence, chapter_acceptance, derive_total_length_budget,
    load_stage1_chapters, str06_chat_request,
)
from novel.stages.stage2 import (
    character_core_view, first_appearance_anchors, chapter_observation_prompt,
)
from novel.stages.stage5 import validate_chapter_prose, validate_hook_prose
from novel.stages.common import (
    render_chapter_packet, render_hook_context, project_chapter_packet,
    validate_writer_materials, validate_hook_materials, material_identities,
)
from novel.stages.revisions import preview_owner_patch, validate_prose_wire
from novel.stages.review import dry_run_patch, character_literals
from contracts.schema.hook import HookConstraints
from contracts.schema.workflow import STR06_RUNTIME_CONFIG


class _Capture(BaseException):
    def __init__(self, prompt, validate, system, json_mode, temperature):
        self.prompt, self.validate = prompt, validate
        self.system, self.json_mode, self.temperature = system, json_mode, temperature


class _ReadOnlyRepository(Str06Repository):
    def write(self, *args, **kwargs):
        raise RuntimeError('只读预检不得写运行产物')

    publish = write
    write_text = write
    write_invalid = write
    write_evidence_bytes = write


def request_preview(repository, owner, source, profile):
    """捕获生产请求面；逐章入口绕过并发资源占用，只调用同一接受闸。"""
    budget = derive_total_length_budget(source.chapters)
    constraints = HookConstraints(target_chars=STR06_RUNTIME_CONFIG.hook_target_chars,
                                  hard_max_chars=STR06_RUNTIME_CONFIG.hook_hard_max_chars)
    evidence = build_evidence(source.chapters, source.hook_copy)
    if owner.startswith('ch'):
        n = int(owner[2:])
        book = repository.read_model('stage2/observations/book.json')
        core, errors = character_core_view(book, evidence=evidence)
        if errors:
            raise ValueError(errors)
        local = _chapter_evidence(evidence, n)
        prompt = chapter_observation_prompt(chapter=n, book=book, chapters=source.chapters, evidence=local)
        validate = lambda w: chapter_acceptance(w, chapter=n, evidence_ids=frozenset(local),
                                               character_slugs=frozenset(book.characters),
                                               anchors=first_appearance_anchors(core))[1]
        capture = _Capture(prompt, validate, SYS, True, 0.3)
    elif owner.startswith('body_ch'):
        packet = project_chapter_packet(repository.read_model('stage4/ir/writer_book_ir.json'), int(owner[7:]))
        contract = packet.realization_contract
        validate = lambda w: validate_prose_wire(w, lambda text: validate_chapter_prose(
            text, withheld_literals=packet.chapter.withheld_literals,
            released_surfaces=packet.chapter.released_surfaces, target_chars=contract.target_chars,
            band_low=contract.band_low, band_high=contract.band_high))
        capture = _Capture(render_chapter_packet(packet), validate, BODY_WRITER_SYSTEM, False, 0.7)
    else:
        def ask(_owner, prompt, validate):
            raise _Capture(prompt, validate, SYS, True, 0.3)
        def ask_text(_owner, prompt):
            context = repository.read_model('stage4/context/hook/hook.json')
            raise _Capture(prompt, lambda w: validate_prose_wire(w, lambda text: validate_hook_prose(text, context)),
                           HOOK_WRITER_SYSTEM, False, 0.7)
        ctx = ChainContext(repository=repository, chapters=source.chapters, hook_copy=source.hook_copy,
                           ask=ask, ask_text=ask_text, ask_body=lambda *_a: None,
                           budget=budget, digest=digest, provider=profile.provider, model=profile.model,
                           book_title=paths.title_of(repository.root), hook_constraints=constraints)
        try:
            NODE_RUNNERS[semantic_owner_unit(owner)[0]](ctx)
        except _Capture as captured:
            capture = captured
        else:
            raise ValueError('未捕获该 owner 的正式请求')
    request = str06_chat_request(profile, owner, capture.prompt, json_mode=capture.json_mode,
                                 system=capture.system, temperature=capture.temperature)
    return request, capture.validate


def obligation_review(repository):
    """只并列正式语义与承载坐标；未入选源拍保持可见，不推断其重要性。"""
    binding = repository.read_model('stage4/grounding/semantic_binding.json')
    graph = repository.read_model('stage4/design/presentation_graph.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    entries = {e.obligation_id: e for e in binding.entries}
    source_beats = {b.beat_id: (c.chapter, b) for c in blueprint.chapters for b in c.beats}
    # 初态/世界约束原子（s0_*／wc_*）不在拍表里——映射为「章前」（0），不推断拍位。
    non_beat_refs = {ref for o in binding.obligations
                     for ref in o.lineage.blueprint_refs if ref not in source_beats}
    scene_chapters = {s: c.chapter_ordinal for c in graph.chapters for s in c.scene_ids}
    selected = {ref for o in binding.obligations for ref in o.lineage.blueprint_refs}
    result = []
    for obligation in binding.obligations:
        entry = entries.get(obligation.obligation_id)
        scene = next((s for s in graph.scenes if entry and s.scene_id == entry.scene_id), None)
        carriers = (entry.beat_ref, *entry.supporting_beat_refs) if entry else ()
        target_chapters = sorted({scene_chapters[ref.split('#')[0]] for ref in carriers})
        result.append({'obligation': obligation.model_dump(mode='json'),
                       'source_chapters': sorted({source_beats[ref][0] if ref in source_beats else 0
                                                  for ref in obligation.lineage.blueprint_refs}),
                       'source_beats': [source_beats[ref][1].model_dump(mode='json')
                                        for ref in obligation.lineage.blueprint_refs
                                        if ref in source_beats],
                       'non_beat_refs': sorted(ref for ref in obligation.lineage.blueprint_refs
                                               if ref in non_beat_refs),
                       'target_chapters': target_chapters, 'cross_chapter': len(target_chapters) > 1,
                       'carrier': entry.model_dump(mode='json') if entry else None,
                       'beat': graph.beat(*entry.beat_ref.split('#')).model_dump(mode='json') if entry else None,
                       'brief': scene.realization_brief if scene else None,
                       'delivery_hints': [b.delivery_hint for b in scene.beats] if scene else [],
                       'unbound': [u.model_dump(mode='json') for u in binding.unbound if u.obligation_id == obligation.obligation_id]})
    return {'obligations': result,
            'unselected_source_beats': [{'chapter_ordinal': n, 'beat': beat.model_dump(mode='json')}
                                        for ref, (n, beat) in source_beats.items() if ref not in selected],
            'source_expression': blueprint.source_expression.model_dump(mode='json'),
            'expression_decisions': [d.model_dump(mode='json') for d in graph.expression_decisions]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=os.environ.get('NOVEL_OUTPUT'))
    parser.add_argument('--sid', required=True)
    parser.add_argument('--save', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    views = sub.add_parser('views')
    views.add_argument('--chapter', type=int)
    sub.add_parser('obligations')
    patch = sub.add_parser('patch-preview')
    patch.add_argument('--owner', required=True)
    patch.add_argument('--candidate', type=Path, required=True)
    patch.add_argument('--baseline-root', type=Path)
    patch.add_argument('--reasoning-effort', choices=('low', 'high', 'max'), default='high')
    args = parser.parse_args(argv)
    if not args.base:
        parser.error('NOVEL_OUTPUT／--base 未提供：没查')
    from novel import paths
    root = paths.resolve_novel_dir(Path(args.base), args.sid)
    repo = _ReadOnlyRepository(root)
    if args.command == 'views':
        ir = repo.read_model('stage4/ir/writer_book_ir.json')
        chars = repo.read_model('stage2/characters/characters.json')
        graph = repo.read_model('stage4/design/presentation_graph.json')
        binding = repo.read_model('stage4/grounding/semantic_binding.json')
        internal = material_identities(graph, binding, chars)
        chapters = [args.chapter] if args.chapter else [c.chapter_ordinal for c in ir.chapters]
        result = {'chapters': {n: {'text': render_chapter_packet(project_chapter_packet(ir, n)),
                                   'errors': validate_writer_materials(project_chapter_packet(ir, n), internal_ids=internal)}
                               for n in chapters}}
        context = repo.read_model('stage4/context/hook/hook.json')
        result['hook'] = {'text': render_hook_context(context), 'errors': validate_hook_materials(context, internal_ids=internal)}
    elif args.command == 'obligations':
        result = obligation_review(repo)
    else:
        source = load_stage1_chapters(args.base, args.sid)
        profile = ExecutionProfile(load_config(), args.reasoning_effort)
        request, validate = request_preview(repo, args.owner, source, profile)
        identity = digest({'owner': args.owner, 'provider': profile.provider, 'request': request.cache_payload()})
        baseline = purchased_baseline((args.baseline_root or root).resolve(), args.owner, identity)
        constraints = HookConstraints(target_chars=STR06_RUNTIME_CONFIG.hook_target_chars,
                                      hard_max_chars=STR06_RUNTIME_CONFIG.hook_hard_max_chars)
        result = preview_owner_patch(owner=args.owner, identity=identity,
            candidate=repo.read_external(args.candidate), baseline=baseline, validate=validate,
            character_literals=character_literals(repo),
            dry_run=lambda w: dry_run_patch(repo, args.owner, w, evidence=build_evidence(source.chapters, source.hook_copy),
                budget=derive_total_length_budget(source.chapters), hook_constraints=constraints, chapter_count=len(source.chapters),
                source_texts=tuple(c['text'] for c in source.chapters), book_title=paths.title_of(root)))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.save:
        Str06Repository(root).write_text(f'runtime/review/{args.command}.json', text)
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
