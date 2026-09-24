"""STR-06 隔离入口：source JSON → Stage2(+names) → Preserve/Design/Hook/Bind → planned 干跑
→ Hook 设计/成文 → Naming → Assembly（F6）。

--wire-dir 显式采用已购/人工修订 wire，逐份记录字节 SHA；owner wire 缺失时走付费
边界：没有同时 --yes --allow-source-export 就停在该 owner 并报告需要授权。
--run-dir 固定重用；同请求身份结果免费采用，零重复付费。
此入口不发布 Stage5 body 接受回执；无 accepted body 时 Assembly 显式 not_ready
（正文成文与组装归 F5），也不切换现役工作流（删除条件 STR-06-F8）。
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from contracts.schema.str06 import OutputConstraints
from contracts.schema.str06.hook import HookConstraints, HookReceipt
from novel.config import load_config
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.str06_requests import PaidRequestSession, digest
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel.store import Str06Repository
from novel.stages.str06.source_compile import (
    ChapterCompiler, assemble_blueprint, compile_book, chapter_input_snapshot, initial_snapshot, source_fold,
)
from novel.stages.str06.name_structure import compile_name_structure, merge_name_structure
from novel.stages.str06.grounding import assemble_preserve, project_brief, assemble_binding
from novel.stages.str06.design_compile import assemble_body_design
from novel.stages.str06.hook_design import assemble_hook_design, publish_graph
from novel.stages.str06.hook_context import compile_hook_context
from novel.stages.str06.hook_writer import (
    HOOK_VALIDATOR_VERSION, MECHANICAL_CHECKS, MAX_REWRITES,
    build_hook_user_prompt, build_rewrite_user_prompt, context_sha256, validate_hook_prose,
)
from novel.stages.str06.naming import (
    apply_names, check_residual, compile_name_map, compile_surface_replacements,
    naming_projection,
)
from novel.stages.str06.assembly import assemble_delivery, body_access
from novel.stages.str06.compile import compile_execution_plan
from novel.stages.str06.fold import fold_accepted_prefix
from novel.stages.str06.project import project_context
from novel.stages.str06.guard import run_mechanical_guard
from novel.stages.str06.prompts import (
    SYS, BOOK_PROMPT, CHAPTER_PROMPT, PRESERVE_RULES, DESIGN_RULES, BIND_RULES,
    HOOK_DESIGN_RULES, HOOK_WRITER_SYSTEM, NAMES_RULES, NAMING_RULES, pack_prompt,
)


class AuthorizationRequired(Exception):
    """owner wire 缺失且未授权付费：停在该 owner，报告需要授权。"""

    def __init__(self, owner: str):
        super().__init__(
            f'{owner} 的 wire 缺失且未授权新调用；如需继续请同时提供 '
            '--yes --allow-source-export（外发与付费分别授权）')
        self.owner = owner


def build_evidence(chapters):
    evidence = {}
    for chapter in chapters:
        no, text = chapter['chapter_ordinal'], chapter['text']
        segments, buffer = [], ''
        for sentence in re.split(r'(?<=。)', text):
            if buffer and len(buffer) + len(sentence) > 300:
                segments.append(buffer)
                buffer = sentence
            else:
                buffer += sentence
        if buffer.strip():
            segments.append(buffer)
        for i, segment in enumerate(segments, 1):
            evidence[f'e{no:02d}_{i:02d}'] = {'chapter': no, 'text': segment}
    return evidence


FINAL_STATUS = {
    'stage2': 'names_applied',
    'stage4': 'planned_validated',
    'stage5': 'hook_accepted_mechanical',
    'stage7': 'assembly_not_ready',
}


def run_workflow(*, repository, chapters, ask, ask_text, budget=11000, through='stage4',
                 provider='manual-adoption', model='unknown', book_title='',
                 hook_constraints=None):
    """记录显式执行状态；失败保留旧产物供恢复，但不能仍宣称上次绿灯有效。"""
    hook_constraints = hook_constraints or HookConstraints(
        target_chars=280, hard_max_chars=360)
    identity = digest({'chapters': chapters, 'budget': budget, 'through': through})
    status_rel = 'runtime/workflow_status.json'
    repository.write(status_rel, {'status': 'running', 'input_sha256': identity,
                                  'accepted_body': False})
    try:
        report = _run_workflow(repository=repository, chapters=chapters, ask=ask,
                               ask_text=ask_text, budget=budget, through=through,
                               provider=provider, model=model, book_title=book_title,
                               hook_constraints=hook_constraints)
    except AuthorizationRequired as exc:
        repository.write(status_rel, {
            'status': 'needs_authorization', 'input_sha256': identity,
            'accepted_body': False, 'blocked_owner': exc.owner,
            'detail': str(exc)})
        raise
    except Exception as exc:
        repository.write(status_rel, {'status': 'failed', 'input_sha256': identity,
                                      'accepted_body': False, 'failure_kind': type(exc).__name__,
                                      'detail': str(exc)})
        raise
    repository.write(status_rel, {'status': FINAL_STATUS[through], 'input_sha256': identity,
                                  'accepted_body': False, 'through': through})
    return report


def _run_workflow(*, repository, chapters, ask, ask_text, budget, through, provider,
                  model, book_title, hook_constraints):
    stage_order = {'stage2': 0, 'stage4': 1, 'stage5': 2, 'stage7': 3}
    if through not in stage_order:
        raise ValueError(f'--through 取值面为 stage2|stage4|stage5|stage7：{through}')
    if [c['chapter_ordinal'] for c in chapters] != list(range(1, len(chapters) + 1)):
        raise ValueError('source JSON 必须为非空连续章序列表')
    if not chapters or any(not c['text'].strip() for c in chapters):
        raise ValueError('源章不能为空')
    evidence = build_evidence(chapters)
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in chapters)
    block = '\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items())
    book = ask('book', BOOK_PROMPT.format(n=len(chapters), text=full, evidence=block),
               lambda w: compile_book(w, evidence_ids=evidence)[2])
    core, wcs, errors = compile_book(book, evidence_ids=evidence)
    if errors:
        raise ValueError(errors)
    compiler, compiled, snapshots = ChapterCompiler(core, evidence_ids=evidence), [], {}
    for chapter in chapters:
        no = chapter['chapter_ordinal']
        snapshot = (initial_snapshot(core) if no == 1 else
                    chapter_input_snapshot(core, assemble_blueprint(wcs, compiled), no))
        snapshots[str(no)] = snapshot
        prompt = CHAPTER_PROMPT.format(
            ch=no, glossary=pack_prompt('人物闭集', characters=book['characters']),
            snapshot='\n'.join(snapshot),
            kreg='\n'.join(f'{k} -> {v}' for k, v in compiler.statements.items()),
            evidence='\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items() if v['chapter'] == no),
            known_beats=', '.join(sorted(compiler.known_beats)))
        wire = ask(f'ch{no:02d}', prompt, lambda w: compiler.validate(w, no))
        bp, errors = compiler.compile(wire, no)
        if errors:
            raise ValueError(errors)
        compiled.append(bp)
    blueprint = assemble_blueprint(wcs, compiled)
    repository.write('stage2/blueprints/chapter_blueprints.json', blueprint)
    repository.write('stage2/compiled/input_snapshots.json', snapshots)
    _, events = source_fold(core, blueprint)
    repository.write('stage2/acceptance/fold.json', {'events': events})

    # ── Stage2 names 窄调用：源名称结构（发布 Characters 前合并） ──
    names_prompt = pack_prompt(NAMES_RULES, characters=book['characters'], text=full)
    names_wire = ask('names', names_prompt,
                     lambda w: compile_name_structure(w, core, full)[1])
    structure, errors = compile_name_structure(names_wire, core, full)
    if errors:
        raise ValueError(errors)
    chars, errors = merge_name_structure(core, structure)
    if errors:
        raise ValueError(errors)
    repository.write('stage2/characters/characters.json', chars)
    if through == 'stage2':
        return {'chapters': len(compiled), 'fold_events': events,
                'name_structure_people': len(structure.people)}

    def preserve_compile(w):
        return assemble_preserve(w, policy_id='preserve-default', policy_version=1,
                                 characters=core, blueprint=blueprint,
                                 provider=provider, model=model,
                                 request_sha256=digest(preserve_prompt))
    preserve_prompt = pack_prompt(PRESERVE_RULES, characters=core.model_dump(mode='json'),
                                  blueprints=blueprint.model_dump(mode='json'))
    pw = ask('preserve', preserve_prompt, lambda w: preserve_compile(w)[1])
    checkpoint, errors = preserve_compile(pw)
    if errors:
        raise ValueError(errors)
    repository.write('runtime/preserve_checkpoint.json', checkpoint)
    brief, identities = project_brief(checkpoint, core)
    repository.write('stage4/grounding/reconstruction_brief.json', brief)
    constraints = OutputConstraints(target_chapter_count=len(chapters), total_length_budget=budget)
    design_prompt = pack_prompt(DESIGN_RULES, brief=brief.model_dump(mode='json'),
                                identities=[i.model_dump(mode='json') for i in identities],
                                constraints=constraints.model_dump(mode='json'))
    def design_compile(w):
        return assemble_body_design(w, known_char_ids=set(chars.characters), constraints=constraints)
    dw = ask('design', design_prompt, lambda w: design_compile(w)[1])
    body, errors = design_compile(dw)
    if errors:
        raise ValueError(errors)
    positions = {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes}
    bind_prompt = pack_prompt(BIND_RULES, design=body.model_dump(mode='json'),
                              obligations=[o.model_dump(mode='json') for o in checkpoint.obligations])
    bw = ask('bind', bind_prompt, lambda w: assemble_binding(w, checkpoint, positions)[1])
    binding, errors = assemble_binding(bw, checkpoint, positions)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/grounding/semantic_binding.json', binding)
    plan, errors = compile_execution_plan(body, binding)
    repository.write('stage4/acceptance/guard/compile.json', {'errors': errors})
    if errors:
        raise ValueError(errors)
    repository.write('stage4/compiled/execution_plan.json', plan)
    problems = []
    for i, scene in enumerate(body.scenes):
        folded = fold_accepted_prefix(plan.scenes[:i], initial=plan.initial_establishments)
        context, slice_ = project_context(scene_id=scene.scene_id, attempt_id='planned-dry-run',
                                          graph=body, plan=plan, binding=binding,
                                          folded_prefix=folded, total_budget=budget)
        errors = run_mechanical_guard(binding=binding, context=context, graph=body)
        problems.extend(errors)
        repository.write(f'stage4/context/{scene.scene_id}.json', context)
        repository.write(f'stage4/compiled/slice_{scene.scene_id}.json', slice_)
    guard_report = {'mode': 'planned-only', 'accepted_body': False, 'chapters': len(compiled),
                    'scenes': len(body.scenes), 'obligations': len(binding.obligations),
                    'guard_errors': problems, 'source_fold_events': events}
    repository.write('stage4/acceptance/guard/report.json', guard_report)
    if problems:
        raise ValueError(problems)
    if through == 'stage4':
        return guard_report

    # ── Hook Design：Body Design 完成后的第二次语义请求；唯一 publisher 发布 ──
    projection = chars.name_projection()
    hook_design_prompt = pack_prompt(
        HOOK_DESIGN_RULES, book_title=book_title, design=body.model_dump(mode='json'),
        identities=[i.model_dump(mode='json') for i in identities],
        constraints=hook_constraints.model_dump(mode='json'))
    def hook_design_compile(w):
        return assemble_hook_design(w, body=body, known_char_ids=set(chars.characters),
                                    name_projection=projection)
    hw = ask('hook_design', hook_design_prompt, lambda w: hook_design_compile(w)[1])
    hook, errors = hook_design_compile(hw)
    if errors:
        raise ValueError(errors)
    graph = publish_graph(body, hook)
    repository.write('stage4/design/presentation_graph.json', graph)
    hook_context, errors = compile_hook_context(graph, projection, hook_constraints)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/context/hook/hook.json', hook_context)
    repository.write('runtime/workflow_status.json', {
        'status': 'hook_context_compiled', 'accepted_body': False})

    # ── Hook Writer：单产 + 机械验；超长同 Context 有限重写（attempt 进请求身份） ──
    prose, attempts_log, attempt_identity = _write_hook(hook_context, ask_text)
    status = 'hook_accepted_mechanical' if prose is not None else 'hook_rejected'
    receipt = HookReceipt(
        hook_context_sha256=context_sha256(hook_context),
        prose_sha256=_sha256_text(prose or ''),
        request_identity=attempt_identity,
        validator_version=HOOK_VALIDATOR_VERSION,
        status='accepted' if prose is not None else 'rejected',
        mechanical_checks=MECHANICAL_CHECKS)
    if prose is not None:
        repository.write_text('stage5/hook/hook.txt', prose)
    repository.write('stage5/hook/receipt.json', receipt)
    repository.write('runtime/hook_attempts.json', attempts_log)
    repository.write('runtime/workflow_status.json', {
        'status': status, 'accepted_body': False})
    if prose is None:
        raise ValueError('Hook 写作在重写上限内未通过机械校验；先人工复核 HookContext')
    if through == 'stage5':
        return {'hook_chars': len(prose), 'materials': len(hook_context.materials),
                'pov': hook_context.pov.model_dump(mode='json')}

    # ── Naming：只消费名称投影；Apply 与装配 ──
    naming_prompt = pack_prompt(NAMING_RULES, book_title=book_title,
                                projection=naming_projection(chars, blueprint))
    nw = ask('naming', naming_prompt,
             lambda w: compile_name_map(w, chars)[1])
    name_map, errors = compile_name_map(nw, chars)
    if errors:
        raise ValueError(errors)
    repository.write('stage7/naming/name_map.json', name_map)
    replacements, errors = compile_surface_replacements(chars, name_map)
    if errors:
        raise ValueError(errors)
    kept_literals = tuple(sorted({
        surface for entry in name_map.entries if entry.name is None
        for surface in _kept_surfaces(chars, entry.char_ref)}))
    renamed_hook, hook_claims = apply_names(prose, replacements)
    residual = check_residual(renamed_hook, replacements, hook_claims, kept_literals)
    if residual:
        raise ValueError(f'Hook 改名残留：{residual}')
    repository.write_text('runtime/delivery/hook_renamed.txt', renamed_hook)

    # ── Assembly：无 accepted body ⇒ 显式 not_ready（F5 接管整书组装）；
    # stage7/final/ 不落任何产物，组装报告只进 runtime/delivery/。 ──
    access = body_access(_scene_ids(body), {})
    outcome = assemble_delivery(
        body_text=None, body=access, hook_prose=prose,
        hook_receipt=receipt.model_dump(mode='json'),
        hook_context_sha256=context_sha256(hook_context),
        replacements=replacements, kept_literals=kept_literals)
    report = {'mode': 'hook+naming', 'accepted_body': False, 'chapters': len(compiled),
              'scenes': len(body.scenes), 'obligations': len(binding.obligations),
              'guard_errors': problems, 'source_fold_events': events,
              'hook': {'chars': len(prose), 'renamed_chars': len(renamed_hook),
                       'materials': len(hook_context.materials),
                       'replacements': len(replacements), 'kept': len(kept_literals)},
              'assembly': {'status': outcome.status, 'reason': outcome.reason,
                           'delivery_identity': outcome.delivery_identity}}
    repository.write('runtime/delivery/assembly_report.json', report)
    return report


def _sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _write_hook(hook_context, ask_text):
    """单产 + 机械验 + 同 Context 有限重写。返回 (prose|None, attempts_log, 请求身份)。"""
    attempts: list[dict] = []
    errors: list[str] = []
    identity = ''
    for attempt in range(1 + MAX_REWRITES):
        prompt = (build_hook_user_prompt(hook_context) if attempt == 0
                  else build_rewrite_user_prompt(hook_context, errors))
        identity = digest({'owner': 'hook_writer', 'prompt': prompt})
        prose, response_errors = ask_text('hook_writer', prompt)
        if prose is None:
            errors = response_errors or ['响应为空']
            attempts.append({'attempt': attempt + 1, 'chars': 0, 'errors': errors})
            continue
        errors = validate_hook_prose(prose, hook_context)
        attempts.append({'attempt': attempt + 1, 'chars': len(prose), 'errors': errors})
        if not errors:
            return prose, attempts, identity
    return None, attempts, identity


def _kept_surfaces(chars, char_ref: str) -> set[str]:
    person = chars.name_structure.people[char_ref]
    return {s for s, form in person.surface_forms.items() if form is not None}


def _scene_ids(body):
    return [s.scene_id for s in body.scenes]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-json', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--wire-dir', type=Path)
    parser.add_argument('--repair-manifest', type=Path)
    parser.add_argument('--through', choices=['stage2', 'stage4', 'stage5', 'stage7'],
                        default='stage4')
    parser.add_argument('--budget', type=int, default=11000)
    parser.add_argument('--hook-target-chars', type=int, default=280)
    parser.add_argument('--hook-hard-max', type=int, default=360)
    parser.add_argument('--book-title', default='')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--allow-source-export', action='store_true')
    args = parser.parse_args()
    if args.hook_target_chars > args.hook_hard_max:
        raise ValueError('--hook-target-chars 不得大于 --hook-hard-max')
    base = os.environ.get('NOVEL_OUTPUT')
    if not base:
        raise ValueError('NOVEL_OUTPUT 未设置：没查、未运行')
    if not args.run_dir.resolve().is_relative_to(Path(base).resolve()):
        raise ValueError('--run-dir 必须位于 NOVEL_OUTPUT 下的隔离根')
    repository = Str06Repository(args.run_dir)
    chapters = repository.read_external(args.source_json)
    adopted = {}
    if args.repair_manifest:
        manifest = repository.read_external(args.repair_manifest)
        source_input = manifest['source_input']
        if (Path(source_input['path']).resolve() != args.source_json.resolve()
                or repository.file_sha256(args.source_json) != source_input['sha256']):
            raise ValueError('owner patch 源章输入身份不匹配')
        adopted = {entry['owner']: entry['result'] for entry in manifest['files']}
        if len(adopted) != len(manifest['files']):
            raise ValueError('owner patch manifest 中 owner 重复')
        for entry in manifest['files']:
            for field in ('base', 'result'):
                item = entry[field]
                actual = repository.file_sha256(Path(item['path']))
                if actual != item['sha256']:
                    raise ValueError(f'owner patch {entry["owner"]} 的 {field} 身份变化，拒绝采用')
        repository.write('runtime/repair_manifest.json', manifest)
    cfg = load_config()
    ledger = CostLedger(path=ledger_path(Path(base)))
    session = PaidRequestSession(repository, ledger, DeepSeekTransport(cfg), yes=args.yes,
                                 allow_source_export=args.allow_source_export,
                                 provider=cfg.base_url)
    authorized = args.yes and args.allow_source_export

    def adopt(owner, path, validate):
        if args.repair_manifest and (
                owner not in adopted or Path(adopted[owner]['path']).resolve() != path.resolve()
                or repository.file_sha256(path) != adopted[owner]['sha256']):
            raise ValueError(f'{owner} wire 不在已核实修订 manifest 内')
        raw = repository.read_external(path)
        errors = validate(raw)
        repository.write(f'runtime/adopt_{owner}.json', {
            'owner': owner, 'path': str(path.resolve()),
            'sha256': repository.file_sha256(path),
            'new_paid_calls': 0, 'historical_usage': None, 'errors': errors})
        if errors:
            raise ValueError(f'{owner} 免费采用失败：{errors}')
        return raw

    def wire_path(owner):
        if not args.wire_dir:
            return None
        path = args.wire_dir / f'{owner}_wire.json'
        return path if path.exists() else None

    def ask(owner, prompt, validate):
        path = wire_path(owner)
        if path is not None:
            return adopt(owner, path, validate)
        if not authorized:
            raise AuthorizationRequired(owner)
        request = ChatRequest(model=cfg.model, system=SYS, user=prompt, reasoning_effort='high',
                              thinking=cfg.thinking_mode == 'on', json_mode=True,
                              max_tokens=65536, temperature=0.3)
        return session.ask(owner, request, validate)

    def ask_text(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw = adopt(owner, path, lambda w: [])
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return prose, []
        if not authorized:
            raise AuthorizationRequired(owner)
        request = ChatRequest(model=cfg.model, system=HOOK_WRITER_SYSTEM, user=prompt,
                              reasoning_effort='high', thinking=cfg.thinking_mode == 'on',
                              json_mode=False, max_tokens=65536, temperature=0.7)
        return session.ask_text(owner, request)

    print(run_workflow(repository=repository, chapters=chapters, ask=ask, ask_text=ask_text,
                       budget=args.budget, through=args.through,
                       provider='manual-adoption' if args.wire_dir else cfg.base_url,
                       model='unknown' if args.wire_dir else cfg.model,
                       book_title=args.book_title,
                       hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                         hard_max_chars=args.hook_hard_max)))


if __name__ == '__main__':
    main()
