"""只读补丁干跑；调用正式编译器与判定，缺失输入明确报告未运行。"""
from __future__ import annotations

from pydantic import ValidationError

from contracts.schema.grounding import OutputConstraints
from contracts.schema.observation import (
    AssociateObservation,
)
from contracts.schema.source import SourceNameResolution
from contracts.schema.receipt import HOOK_MECHANICAL_CHECKS, HOOK_VALIDATOR_VERSION
from contracts.schema.workflow import STR06_RUNTIME_CONFIG, STAGE4_PRESERVE_POLICY_VERSION
from novel.stages.stage2 import (
    associate_observation_issues, character_core_view, compile_book_observation,
    compile_chapter_observation, compile_cross_cause,
    compile_name_variants, compile_names_observation, compile_source_truth,
    extend_name_surfaces, inspect_identity_associations,
    name_variant_candidates,
    normalize_name_observations, project_association_space, project_cause_candidates,
    validate_name_resolutions,
)
from novel.stages.stage4 import (
    assemble_preserve, assemble_hook_design,
    design_obligation_index, project_brief, inspect_design_candidate,
    publish_graph, compile_execution_plan, compile_hook_context, compile_writer_book_ir,
    run_mechanical_guard, inspect_body_materials, plan_material_repairs,
    hook_combination_errors,
)
from novel.stages.stage5 import (
    chapter_prose_validator, context_sha256, resolve_chapter_prose, resolve_hook_prose,
    validate_hook_prose, sha256_text,
)
from novel.stages.stage7 import (
    assemble_delivery, chapter_access, verify_chapter_receipts,
)
from novel.stages.stage4 import (
    compile_name_map, compile_bound_replacements, name_map_content_sha256,
)
from novel.stages.common import (
    chapter_identities, project_chapter_packet, validate_writer_materials, validate_hook_materials,
    material_identities,
)
from novel.stages.stage4 import preserve_name_issues


def character_literals(repository) -> dict[str, tuple[str, ...]]:
    """残余引用报告使用正式人物表；不猜自然语言指代。"""
    try:
        chars = repository.read_model('stage2/characters/characters.json')
    except FileNotFoundError:
        return {}
    return {cid: tuple(s.surface for s in char.surfaces) for cid, char in chars.characters.items()}


def dry_run_patch(repository, owner, candidate, *, evidence, budget,
                  chapter_count: int,
                  source_texts: tuple[str, ...] | None = None, book_title: str = '',
                  length_band: tuple[float, float] = (
                      STR06_RUNTIME_CONFIG.length_band_low, STR06_RUNTIME_CONFIG.length_band_high),
                  associate_candidate: dict | None = None) -> dict:
    """最近确定性入口的真实干跑；候选正文的拟定回执不落盘、不授予接受状态。"""
    try:
        if owner in {'book', 'names', 'name_variants', 'associate', 'cross_cause'} or owner.startswith('ch'):
            if owner == 'book':
                book, errors = compile_book_observation(candidate, evidence=evidence)
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
            else:
                book = repository.read_model('stage2/observations/book.json')
            core, errors = character_core_view(book, evidence=evidence)
            if errors:
                return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
            chapters = {}
            for n in range(1, chapter_count + 1):
                if owner == f'ch{n:02d}':
                    chapters[n], errors = compile_chapter_observation(
                        candidate, chapter=n, evidence_ids={k: v for k, v in evidence.items()
                                                           if v['chapter'] == n or n == 1 and v['chapter'] == 0},
                        character_slugs=book.characters,
                        fact_ids=(fact.fact_id for fact in book.facts))
                    if errors:
                        return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                else:
                    chapters[n] = repository.read_model(f'stage2/observations/ch{n:02d}.json')
            if owner == 'names':
                names, core, errors = compile_names_observation(
                    candidate, core, '\n'.join(v['text'] for v in evidence.values()), evidence=evidence)
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                structure = names.structure
                # 主结构 wire 已不携带变体判定：保留已购判定，再按当前候选完整
                # 复验；新候选缺判定不能默认 not_name，缺正式上游由 not_run 报告。
                current_names = repository.read_model('stage2/observations/names.json')
                structure = structure.model_copy(update={
                    'resolutions': current_names.structure.resolutions})
                names = names.model_copy(update={'structure': structure})
            elif owner == 'name_variants':
                current_names = repository.read_model('stage2/observations/names.json')
                core, errors = extend_name_surfaces(core, current_names, evidence=evidence)
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                structure = current_names.structure
                derived, located = compile_name_variants(
                    candidate, name_variant_candidates(structure, core, evidence),
                    evidence=evidence, characters=core)
                if located:
                    return {'node': 'str06.stage2.compile', 'status': 'ran',
                            'errors': [message for _path, message in located]}
                assert derived is not None
                structure = structure.model_copy(update={'resolutions': tuple(
                    SourceNameResolution.model_validate(item) for item in derived)})
                names = current_names.model_copy(update={'structure': structure})
            else:
                names = repository.read_model('stage2/observations/names.json')
                core, errors = extend_name_surfaces(core, names, evidence=evidence)
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
            if owner in {'names', 'name_variants', 'cross_cause'}:
                errors = validate_name_resolutions(names.structure, core, evidence=evidence)
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
            if owner == 'associate':
                # owner patch 的主调用原 wire 只有身份答案，因果窄任务尚未执行。
                # 只返回完整身份诊断，不把未购买/未组装的 cause 当缺项，也不
                # 暗示最终 canonical 已通过；相位由调用 owner 决定，不猜字段。
                associate = AssociateObservation.model_validate(candidate)
                errors, undecided = inspect_identity_associations(
                    book=book, chapters=chapters, names=names, associate=associate,
                    evidence=evidence, source_texts=source_texts)
                return {'node': 'str06.stage2.compile', 'status': 'ran',
                        'association_phase': 'identity', 'final_status': 'not_run',
                        'errors': errors, 'undecided': undecided}
            elif owner == 'cross_cause':
                current_associate = (AssociateObservation.model_validate(associate_candidate)
                                     if associate_candidate is not None else
                                     repository.read_model('stage2/observations/associate.json'))
                identity = current_associate.model_copy(update={'cross_chapter_cause': ()})
                try:
                    book, chapters = normalize_name_observations(book, chapters, names.structure)
                except ValueError as exc:
                    return {'node': 'str06.stage2.compile', 'status': 'ran',
                            'errors': [f'名称局部归一未闭合：{exc}']}
                space = project_association_space(book, chapters)
                errors = associate_observation_issues(
                    identity, space, book, chapters, phase='identity')
                if not errors:
                    causes, errors = compile_cross_cause(
                        candidate, project_cause_candidates(space, chapters, book, identity))
                if errors:
                    return {'node': 'str06.stage2.compile', 'status': 'ran', 'errors': errors}
                associate = identity.model_copy(update={'cross_chapter_cause': causes})
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
        if owner == 'preserve':
            blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
            name_map = repository.read_model('stage4/naming/name_map.json')
            checkpoint, errors = assemble_preserve(
                candidate, policy_id='preserve-default', policy_version=STAGE4_PRESERVE_POLICY_VERSION,
                characters=chars.core_view(), blueprint=blueprint,
                name_map=name_map, name_map_sha256=name_map_content_sha256(name_map),
                provider='preview', model='preview', request_sha256='0' * 64)
            errors = list(errors) + preserve_name_issues(checkpoint, chars, name_map)
            conflicts = candidate.get('evidence_conflicts', []) if isinstance(candidate, dict) else []
            if checkpoint is not None and not errors:
                project_brief(checkpoint, chars.core_view(), blueprint,
                              structure=chars.name_structure, name_map=name_map)
            # 新候选的义务集合与身份可能变化，旧 Binding 不得 model_copy 冒充
            # 重新承载；接受 Preserve 后由正式 Design 恢复入口重建。
            return {'node': 'str06.stage4.preserve', 'status': 'ran', 'errors': errors,
                    'evidence_conflicts': conflicts,
                    'publishable': checkpoint is not None and not errors and not conflicts,
                    'downstream': {'node': 'str06.stage4.body_design', 'status': 'not_run',
                                   'reason': '须依据新 Brief 重新验证正式 Design 候选'}}
        checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
        errors = []
        # Brief 是确定性派生；免费预览从正式 checkpoint 重建，旧 Brief 缺认知
        # 分母时不猜值。Design 补丁先验证候选，不能被待修复的旧正文阻断。
        brief, _ = project_brief(checkpoint, chars.core_view(),
                                  repository.read_model('stage2/blueprints/chapter_blueprints.json'),
                                  structure=chars.name_structure,
                                  name_map=repository.read_model('stage4/naming/name_map.json'))
        if owner == 'design':
            return _design_preview(repository, candidate, checkpoint, brief, chars,
                                   budget=budget, chapter_count=chapter_count,
                                   length_band=length_band, book_title=book_title)
        binding = repository.read_model('stage4/grounding/semantic_binding.json')
        body = repository.read_model('stage4/design/body_design.json')
        if owner == 'hook_design':
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
        hook_context, hook_errors = compile_hook_context(graph, projection,
                                                        chars.name_structure, kp_statements,
                                                        world_statements,
                                                        characters=chars.core_view(),
                                                        total_budget=budget)
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
                    chapter_count, length_band, book_title):
    """失败书没有正式Design/Binding/Hook时，仍运行同一份完整候选预检。"""
    inspection = inspect_design_candidate(
        candidate, checkpoint=checkpoint, brief=brief,
        obligations=design_obligation_index(checkpoint, brief), chars=chars,
        name_map=repository.read_model('stage4/naming/name_map.json'),
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
                    kp_statements={p.kp_id: p.statement for p in brief.propositions},
                    world_statements={w.constraint_id: w.statement
                                      for w in brief.book_constraints},
                    total_budget=budget)
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
    name_map = repository.read_model('stage4/naming/name_map.json')
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
        resolved = resolve_hook_prose(hook_prose, hook)
        hook_prose = resolved.text
        hook_receipt = {**hook_receipt, 'prose_sha256': sha256_text(hook_prose),
                        'validator_version': HOOK_VALIDATOR_VERSION,
                        'mechanical_checks': list(HOOK_MECHANICAL_CHECKS),
                        'name_mentions': [row.model_dump() for row in resolved.mentions],
                        'non_name_spans': [row.model_dump() for row in resolved.non_name_spans],
                        'hook_context_sha256': context_sha256(hook), 'status': 'accepted' if not errors else 'rejected'}
    elif owner.startswith('body_ch'):
        n = int(owner[7:])
        packet = project_chapter_packet(ir, n)
        prose[n] = candidate['prose']
        errors = chapter_prose_validator(packet)(prose[n])
        resolved = resolve_chapter_prose(prose[n], packet)
        prose[n] = resolved.text
        # 合成回执按**本次重验**的尺子记版本：沿盘上旧值会在下一次换版后
        # 把刚验证过的候选稿误报成「须先免费重验」（假红）。
        from contracts.schema.receipt import BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION
        receipts[n] = {**receipts[n], 'prose_sha256': sha256_text(prose[n]),
                       'writer_view_sha256': identities[n].writer_view_sha256,
                       'validation_sha256': identities[n].validation_sha256,
                       'validator_version': BODY_VALIDATOR_VERSION,
                       'mechanical_checks': list(BODY_MECHANICAL_CHECKS),
                       'name_mentions': [row.model_dump() for row in resolved.mentions],
                       'non_name_spans': [row.model_dump() for row in resolved.non_name_spans],
                       'status': 'accepted' if not errors else 'rejected'}
    errors.extend(verify_chapter_receipts(receipts, prose, identities_by_ordinal=identities))
    replacements, name_errors = compile_bound_replacements(chars, name_map)
    errors.extend(name_errors)
    if errors:
        return {'node': 'str06.stage7.assemble', 'status': 'ran', 'errors': errors}
    kept = tuple(sorted({surface for entry in name_map.entries if entry.name is None
                         for surface, form in chars.name_structure.people[entry.char_ref].surface_forms.items()
                         if form is not None}))
    outcome = assemble_delivery(chapter_prose=prose,
        body=chapter_access(receipts, receipts, identities_by_ordinal=identities, chapter_prose=prose),
        hook_prose=hook_prose, hook_receipt=hook_receipt, hook_context_sha256=context_sha256(hook),
        replacements=replacements, kept_literals=kept, chapter_receipts=receipts,
        identities_by_ordinal=identities)
    return {'node': 'str06.stage7.assemble', 'status': 'ran',
            'errors': [] if outcome.status == 'ready' else [outcome.reason],
            'delivery_status': outcome.status, 'candidate_receipt': owner != 'naming'}
