"""只读补丁干跑；调用正式编译器与判定，缺失输入明确报告未运行。"""
from __future__ import annotations

from pydantic import ValidationError

from contracts.schema.grounding import OutputConstraints
from contracts.schema.hook import HookConstraints
from contracts.schema.observation import BookObservation, NamesObservation, STAGE2_OBSERVATION_PROTOCOL
from contracts.schema.workflow import STR06_RUNTIME_CONFIG, STAGE4_PRESERVE_POLICY_VERSION
from novel.stages.stage2 import (
    character_core_view, compile_associate_observation, compile_chapter_observation,
    compile_name_structure, compile_source_truth, project_association_space,
)
from novel.stages.stage4 import (
    assemble_body_design, assemble_binding, assemble_preserve, assemble_hook_design,
    publish_graph, compile_execution_plan, compile_hook_context, compile_writer_book_ir,
    run_mechanical_guard,
)
from novel.stages.stage5 import context_sha256, validate_chapter_prose, validate_hook_prose, sha256_text
from novel.stages.stage7 import (
    assemble_delivery, chapter_access, compile_name_map, compile_surface_replacements,
    verify_chapter_receipts,
)
from novel.stages.common import (
    chapter_identities, project_chapter_packet, validate_writer_materials, validate_hook_materials,
    material_identities,
)


def character_literals(repository) -> dict[str, tuple[str, ...]]:
    """残余引用报告使用正式人物表；不猜自然语言指代。"""
    try:
        chars = repository.read_model('stage2/characters/characters.json')
    except FileNotFoundError:
        return {}
    return {cid: tuple(s.surface for s in char.surfaces) for cid, char in chars.characters.items()}


def dry_run_patch(repository, owner, candidate, *, evidence, budget,
                  hook_constraints: HookConstraints, chapter_count: int,
                  source_texts: tuple[str, ...] | None = None, book_title: str = '',
                  length_band: tuple[float, float] = (
                      STR06_RUNTIME_CONFIG.length_band_low, STR06_RUNTIME_CONFIG.length_band_high)) -> dict:
    """最近确定性入口的真实干跑；候选正文的拟定回执不落盘、不授予接受状态。"""
    try:
        if owner in {'book', 'names', 'associate'} or owner.startswith('ch'):
            book = BookObservation.model_validate(candidate) if owner == 'book' else repository.read_model('stage2/observations/book.json')
            core, errors = character_core_view(book, evidence=evidence)
            if errors:
                return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
            chapters = {}
            for n in range(1, chapter_count + 1):
                if owner == f'ch{n:02d}':
                    chapters[n], errors = compile_chapter_observation(
                        candidate, chapter=n, evidence_ids=[k for k, v in evidence.items()
                                                          if v['chapter'] == n or n == 1 and v['chapter'] == 0],
                        character_slugs=book.characters)
                    if errors:
                        return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                else:
                    chapters[n] = repository.read_model(f'stage2/observations/ch{n:02d}.json')
            if owner == 'names':
                structure, errors = compile_name_structure(candidate, core, '\n'.join(v['text'] for v in evidence.values()))
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                names = NamesObservation(protocol_version=STAGE2_OBSERVATION_PROTOCOL, structure=structure)
            else:
                names = repository.read_model('stage2/observations/names.json')
            if owner == 'associate':
                associate, errors = compile_associate_observation(candidate, project_association_space(book, chapters))
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
            else:
                associate = repository.read_model('stage2/observations/associate.json')
            result = compile_source_truth(book=book, chapters=chapters, names=names,
                                          associate=associate, evidence=evidence, source_texts=source_texts)
            return {'node': 'str06.stage2.compile', 'status': 'ran',
                    'errors': result.errors, 'undecided': result.undecided}
        chars = repository.read_model('stage2/characters/characters.json')
        if owner == 'naming' or owner == 'hook_writer' or owner.startswith('body_ch'):
            return _delivery_preview(repository, owner, candidate, chars)
        projection = chars.name_projection()
        body = repository.read_model('stage4/design/body_design.json')
        checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
        graph = repository.read_model('stage4/design/presentation_graph.json')
        binding = repository.read_model('stage4/grounding/semantic_binding.json')
        errors = []
        if owner == 'design':
            brief = repository.read_model('stage4/grounding/reconstruction_brief.json')
            body, errors = assemble_body_design(
                candidate, known_char_ids=set(chars.characters), expression_requirements=brief.expression,
                source_narration=brief.source_narration,
                constraints=OutputConstraints(target_chapter_count=chapter_count, total_length_budget=budget),
                registered_surfaces={cid: {s.surface for s in c.surfaces} for cid, c in chars.characters.items()},
                name_projection=projection, name_structure=chars.name_structure)
            if body is not None:
                graph = publish_graph(body, graph.hook)
        elif owner == 'preserve':
            checkpoint, errors = assemble_preserve(
                candidate, policy_id='preserve-default', policy_version=STAGE4_PRESERVE_POLICY_VERSION,
                characters=chars.core_view(), blueprint=repository.read_model('stage2/blueprints/chapter_blueprints.json'),
                provider='preview', model='preview', request_sha256='0' * 64)
            if checkpoint is not None:
                binding = binding.model_copy(update={'obligations': checkpoint.obligations})
        elif owner == 'bind':
            binding, errors = assemble_binding(candidate, checkpoint,
                                               {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes})
        elif owner == 'hook_design':
            hook, errors = assemble_hook_design(candidate, body=body, known_char_ids=set(chars.characters),
                                                name_projection=projection, name_structure=chars.name_structure)
            if hook is not None:
                graph = publish_graph(body, hook)
        else:
            raise ValueError(f'未登记 owner：{owner}')
        if errors:
            return {'node': 'str06.stage4.compile', 'status': 'ran', 'errors': errors}
        plan, errors = compile_execution_plan(graph, binding)
        hook_context, hook_errors = compile_hook_context(graph, projection, hook_constraints, chars.name_structure)
        errors.extend(hook_errors)
        if plan is not None and hook_context is not None:
            ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding, total_budget=budget,
                                        length_band=length_band, book_title=book_title, projection=projection,
                                        structure=chars.name_structure)
            identities = material_identities(graph, binding, chars)
            errors.extend(validate_hook_materials(hook_context, internal_ids=identities))
            for chapter in ir.chapters:
                errors.extend(validate_writer_materials(project_chapter_packet(ir, chapter.chapter_ordinal), internal_ids=identities))
                for context in chapter.scenes:
                    errors.extend(run_mechanical_guard(binding=binding, context=context, graph=graph))
        return {'node': 'str06.stage4.compile', 'status': 'ran', 'errors': errors}
    except FileNotFoundError as exc:
        return {'status': 'not_run', 'reason': f'下游正式输入缺失：{exc.filename}'}
    except ValidationError as exc:
        return {'status': 'ran', 'errors': [f'候选与下游契约不闭合：{e}' for e in exc.errors(include_url=False)]}


def _delivery_preview(repository, owner, candidate, chars) -> dict:
    ir = repository.read_model('stage4/ir/writer_book_ir.json')
    hook = repository.read_model('stage4/context/hook/hook.json')
    name_map = repository.read_model('stage7/naming/name_map.json')
    if owner == 'naming':
        name_map, errors = compile_name_map(candidate, chars)
        if errors:
            return {'node': 'str06.stage7.assemble', 'status': 'ran', 'errors': errors}
    receipts = {c.chapter_ordinal: repository.inspect(f'stage5/acceptance/body/ch{c.chapter_ordinal:02d}.json')
                for c in ir.chapters}
    prose = {n: repository.read_text(f'stage5/body/ch{n:02d}.txt') for n in receipts}
    hook_prose = repository.read_text('stage5/hook/hook.txt')
    hook_receipt = repository.inspect('stage5/hook/receipt.json')
    identities = {n: chapter_identities(project_chapter_packet(ir, n)) for n in receipts}
    errors = []
    if owner == 'hook_writer':
        hook_prose = candidate['prose']
        errors = validate_hook_prose(hook_prose, hook)
        hook_receipt = {**hook_receipt, 'prose_sha256': sha256_text(hook_prose),
                        'hook_context_sha256': context_sha256(hook), 'status': 'accepted' if not errors else 'rejected'}
    elif owner.startswith('body_ch'):
        n = int(owner[7:])
        packet = project_chapter_packet(ir, n)
        contract = packet.realization_contract
        prose[n] = candidate['prose']
        errors = validate_chapter_prose(prose[n], withheld_literals=packet.chapter.withheld_literals,
                                       released_surfaces=packet.chapter.released_surfaces,
                                       target_chars=contract.target_chars, band_low=contract.band_low, band_high=contract.band_high)
        receipts[n] = {**receipts[n], 'prose_sha256': sha256_text(prose[n]),
                       'writer_view_sha256': identities[n].writer_view_sha256,
                       'validation_sha256': identities[n].validation_sha256,
                       'status': 'accepted' if not errors else 'rejected'}
    errors.extend(verify_chapter_receipts(receipts, prose, identities_by_ordinal=identities))
    replacements, name_errors = compile_surface_replacements(chars, name_map)
    errors.extend(name_errors)
    if errors:
        return {'node': 'str06.stage7.assemble', 'status': 'ran', 'errors': errors}
    kept = tuple(sorted({surface for entry in name_map.entries if entry.name is None
                         for surface, form in chars.name_structure.people[entry.char_ref].surface_forms.items()
                         if form is not None}))
    outcome = assemble_delivery(chapter_prose=prose,
        body=chapter_access(receipts, receipts, identities_by_ordinal=identities, chapter_prose=prose),
        hook_prose=hook_prose, hook_receipt=hook_receipt, hook_context_sha256=context_sha256(hook),
        replacements=replacements, kept_literals=kept)
    return {'node': 'str06.stage7.assemble', 'status': 'ran',
            'errors': [] if outcome.status == 'ready' else [outcome.reason],
            'delivery_status': outcome.status, 'candidate_receipt': owner != 'naming'}
