"""只读补丁干跑；调用正式编译器与判定，缺失输入明确报告未运行。"""
from __future__ import annotations

from pydantic import ValidationError

from contracts.schema.grounding import OutputConstraints
from contracts.schema.hook import HookConstraints
from contracts.schema.observation import BookObservation, NamesObservation, STAGE2_NAMES_PROTOCOL
from contracts.schema.workflow import STR06_RUNTIME_CONFIG, STAGE4_PRESERVE_POLICY_VERSION
from novel.stages.stage2 import (
    character_core_view, compile_associate_observation, compile_chapter_observation,
    compile_name_structure, compile_source_truth, project_association_space,
    normalize_name_observations, validate_name_resolutions,
)
from novel.stages.stage4 import (
    assemble_preserve, assemble_hook_design,
    design_obligation_index, project_brief, inspect_design_candidate,
    publish_graph, compile_execution_plan, compile_hook_context, compile_writer_book_ir,
    run_mechanical_guard, inspect_body_materials, plan_material_repairs,
    hook_combination_errors,
)
from novel.stages.stage5 import chapter_prose_validator, context_sha256, validate_hook_prose, sha256_text
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
                structure, errors = compile_name_structure(candidate, core, '\n'.join(v['text'] for v in evidence.values()), evidence=evidence)
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                names = NamesObservation(protocol_version=STAGE2_NAMES_PROTOCOL, structure=structure)
            else:
                names = repository.read_model('stage2/observations/names.json')
            if owner == 'associate':
                errors = validate_name_resolutions(names.structure, core, evidence=evidence)
                errors.extend(f'名称同一性未决：{r.literal}' for r in names.structure.resolutions
                              if r.status == 'unresolved')
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                book, chapters = normalize_name_observations(book, chapters, names.structure)
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
        checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
        errors = []
        # Brief 是确定性派生；免费预览从正式 checkpoint 重建，旧 Brief 缺认知
        # 分母时不猜值。Design 补丁先验证候选，不能被待修复的旧正文阻断。
        brief, _ = project_brief(checkpoint, chars.core_view(),
                                  repository.read_model('stage2/blueprints/chapter_blueprints.json'),
                                  structure=chars.name_structure)
        if owner == 'design':
            return _design_preview(repository, candidate, checkpoint, brief, chars,
                                   budget=budget, chapter_count=chapter_count,
                                   hook_constraints=hook_constraints,
                                   length_band=length_band, book_title=book_title)
        binding = repository.read_model('stage4/grounding/semantic_binding.json')
        body = repository.read_model('stage4/design/body_design.json')
        graph = repository.read_model('stage4/design/presentation_graph.json')
        if owner == 'preserve':
            checkpoint, errors = assemble_preserve(
                candidate, policy_id='preserve-default', policy_version=STAGE4_PRESERVE_POLICY_VERSION,
                characters=chars.core_view(), blueprint=repository.read_model('stage2/blueprints/chapter_blueprints.json'),
                provider='preview', model='preview', request_sha256='0' * 64)
            if checkpoint is not None:
                brief, _ = project_brief(checkpoint, chars.core_view(),
                                          repository.read_model('stage2/blueprints/chapter_blueprints.json'),
                                          structure=chars.name_structure)
                binding = binding.model_copy(update={'obligations': checkpoint.obligations})
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
        kp_statements = {p.kp_id: p.statement for p in brief.propositions}
        world_statements = {wc.constraint_id: wc.statement for wc in brief.book_constraints}
        hook_context, hook_errors = compile_hook_context(graph, projection, hook_constraints,
                                                        chars.name_structure, kp_statements,
                                                        world_statements,
                                                        characters=chars.core_view())
        errors.extend(hook_errors)
        if plan is not None and hook_context is not None:
            inspection = inspect_body_materials(graph, binding, brief, chars, budget=budget,
                                               length_band=length_band, book_title=book_title)
            identities = material_identities(graph, binding, chars)
            errors.extend(validate_hook_materials(hook_context, internal_ids=identities))
            errors.extend(inspection.errors)
            errors.extend(r.issue.message for r in plan_material_repairs(
                inspection.materials, body=graph, binding=binding, brief=brief,
                structure=chars.name_structure))
        return {'node': 'str06.stage4.compile', 'status': 'ran', 'errors': errors}
    except FileNotFoundError as exc:
        return {'status': 'not_run', 'reason': f'下游正式输入缺失：{exc.filename}'}
    except ValidationError as exc:
        return {'status': 'ran', 'errors': [f'候选与下游契约不闭合：{e}' for e in exc.errors(include_url=False)]}


def _design_preview(repository, candidate, checkpoint, brief, chars, *, budget,
                    chapter_count, hook_constraints, length_band, book_title):
    """失败书没有正式Design/Binding/Hook时，仍运行同一份完整候选预检。"""
    inspection = inspect_design_candidate(
        candidate, checkpoint=checkpoint, brief=brief,
        obligations=design_obligation_index(checkpoint, brief), chars=chars,
        constraints=OutputConstraints(target_chapter_count=chapter_count, total_length_budget=budget),
        length_band=length_band, book_title=book_title)
    report = inspection.report()
    errors = list(inspection.errors) + [m.message for m in inspection.materials]
    # 已有Hook的恢复预览还须核对它与新候选的组合；缺失只标未运行，不能遮住正文报告。
    if inspection.body is not None and inspection.binding is not None:
        try:
            hook = repository.read_graph_hook()
        except FileNotFoundError:
            hook = None
        if hook is not None:
            try:
                # CS-H H2：与生产接受循环（chain.run_hook_design）共用同一把尺的
                # 组合检查，内部句柄闭集取 material_identities 全集。
                hook_errors = hook_combination_errors(
                    hook, body=inspection.body, binding=inspection.binding, chars=chars,
                    hook_constraints=hook_constraints,
                    kp_statements={p.kp_id: p.statement for p in brief.propositions},
                    world_statements={w.constraint_id: w.statement
                                      for w in brief.book_constraints})
            except ValidationError as exc:
                hook_errors = [f'已有Hook与候选不闭合：{e}' for e in exc.errors(include_url=False)]
            errors.extend(hook_errors)
            report['errors'].extend(hook_errors)
            report['checks'][-1] = {'name': 'hook_materials',
                                    'status': 'failed' if hook_errors else 'passed', 'reason': ''}
            report['passed'] = report['passed'] and not hook_errors
    return {'node': 'str06.stage4.compile', 'status': 'ran', 'errors': errors,
            'candidate_preflight': report}


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
        prose[n] = candidate['prose']
        errors = chapter_prose_validator(packet)(prose[n])
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
