"""STR-06 隔离入口：source JSON → Stage2(+names) → Preserve/Design/Hook/Bind → Compile+Guard+WriterIR
→ 按章正文 → Hook 成文 → Naming → Assembly（F5/F6；F7a-2 接正式设施）。

--wire-dir 显式采用已购/人工修订 wire，逐份记录字节 SHA；owner wire 缺失时走付费
边界：同请求身份结果优先免费采用（缓存先于授权），缺失且未授权停在该 owner 并
报告需要授权（PurchaseRequired）。--run-dir 固定重用。

F5 起的腿边界：Stage4 完整发布 graph 与 WriterBookIR 后结束（零正文购买）；
Stage5 完成正文（严格按章串行，--body-chapter-limit 限定前缀）与 Hook；
Stage7 由真实 Assembly 产生交付（文件先落盘、manifest 最后提交）。
--check-only 强制不新购：只做免费采用与免费重放。

F7a-2：候选根首次运行初始化 workflow／epoch 身份（runtime/candidate_workflow.json），
之后原 epoch 复用、代码漂移滚动、源输入变化拒绝；付费边界经正式工作单元与整书
总闸记账，ledger 行带候选节点归属。此入口不切换现役工作流（删除条件 STR-06-F8）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import time
from pathlib import Path

from contracts.schema.str06 import OutputConstraints
from contracts.schema.str06.hook import HookConstraints, HookReceipt
from novel.config import load_config
from novel.llm.str06_run_context import init_or_resume_candidate_workflow
from novel.llm.str06_requests import PaidRequestSession, PurchaseRequired, digest
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel import prov
from novel.store import Str06Repository
from novel.work_units import require_workflow
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
from novel.stages.str06.assembly import (
    assemble_delivery, build_delivery_manifest, chapter_access, verify_chapter_chain,
)
from novel.stages.str06.compile import compile_execution_plan
from novel.stages.str06.book_ir import compile_writer_book_ir
from novel.stages.str06.body_writer import (
    AnsweredText, book_ir_sha256, prior_body_text, sha256_text, write_chapter,
)
from novel.stages.str06.checks import check_chapter_receipt
from novel.stages.str06.guard import run_mechanical_guard
from novel.stages.str06.prompts import (
    SYS, BOOK_PROMPT, CHAPTER_PROMPT, PRESERVE_RULES, DESIGN_RULES, BIND_RULES,
    BODY_WRITER_SYSTEM, HOOK_DESIGN_RULES, HOOK_WRITER_SYSTEM, NAMES_RULES, NAMING_RULES, pack_prompt,
)


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


#: --through → 终态（body_chapter_limit 未覆盖全书或组装未就绪时另记状态）。
FINAL_STATUS = {
    'stage2': 'names_applied',
    'stage4': 'planned_validated',
    'stage5': 'body_hook_accepted_mechanical',
    'stage7': 'delivered',
}


def _chapter_ordinal(n: int) -> str:
    return f'ch{n:02d}'


def run_workflow(*, repository, chapters, ask, ask_text, ask_body, budget=11000,
                 through='stage4', provider='manual-adoption', model='unknown',
                 book_title='', hook_constraints=None, length_band=(0.6, 1.3),
                 body_chapter_limit=None):
    """记录显式执行状态；失败保留旧产物供恢复，但不能仍宣称上次绿灯有效。"""
    hook_constraints = hook_constraints or HookConstraints(
        target_chars=280, hard_max_chars=360)
    identity = digest({'chapters': chapters, 'budget': budget, 'through': through,
                       'band': length_band, 'body_limit': body_chapter_limit})
    status_rel = 'runtime/workflow_status.json'
    repository.write(status_rel, {'status': 'running', 'input_sha256': identity,
                                  'accepted_body': False})
    try:
        report, final_status, accepted_body = _run_workflow(
            repository=repository, chapters=chapters, ask=ask,
            ask_text=ask_text, ask_body=ask_body, budget=budget,
            through=through, provider=provider, model=model,
            book_title=book_title, hook_constraints=hook_constraints,
            length_band=length_band, body_chapter_limit=body_chapter_limit)
    except PurchaseRequired as exc:
        repository.write(status_rel, {
            'status': 'needs_authorization', 'input_sha256': identity,
            'accepted_body': False, 'blocked_owner': exc.owner, 'detail': str(exc)})
        raise
    except Exception as exc:
        repository.write(status_rel, {'status': 'failed', 'input_sha256': identity,
                                      'accepted_body': False, 'failure_kind': type(exc).__name__,
                                      'detail': str(exc)})
        raise
    repository.write(status_rel, {'status': final_status, 'input_sha256': identity,
                                  'accepted_body': accepted_body, 'through': through})
    return report


def _run_workflow(*, repository, chapters, ask, ask_text, ask_body, budget, through,
                  provider, model, book_title, hook_constraints, length_band,
                  body_chapter_limit):
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
                'name_structure_people': len(structure.people)}, FINAL_STATUS['stage2'], False

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
        return assemble_body_design(
            w, known_char_ids=set(chars.characters), constraints=constraints,
            char_canonicals={c.canonical: cid for cid, c in chars.characters.items()})
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
    repository.write('stage4/grounding/semantic_binding.json', binding, owners=('bind',))
    plan, errors = compile_execution_plan(body, binding)
    repository.write('stage4/acceptance/guard/compile.json', {'errors': errors})
    if errors:
        raise ValueError(errors)
    repository.write('stage4/compiled/execution_plan.json', plan)

    # ── Hook Design：Body Design 完成后的第二次语义请求；唯一 publisher 发布 ──
    # （F5 起 Hook Design 属 Stage4 腿：完整 graph 与 WriterIR 一起发布。）
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
    repository.write('stage4/design/presentation_graph.json', graph,
                     owners=('design', 'hook_design'))
    hook_context, errors = compile_hook_context(graph, projection, hook_constraints)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/context/hook/hook.json', hook_context)

    # ── WriterBookIR：任何正文购买前完成整本 Compile/Fold/Guard/IR ──
    writer_ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                       total_budget=budget, length_band=length_band,
                                       book_title=book_title)
    problems = []
    for chapter_ir in writer_ir.chapters:
        for context in chapter_ir.scenes:
            problems.extend(run_mechanical_guard(binding=binding, context=context, graph=body))
    repository.write('stage4/ir/writer_book_ir.json', writer_ir)
    guard_report = {'mode': 'planned-only', 'accepted_body': False, 'chapters': len(compiled),
                    'scenes': len(body.scenes), 'obligations': len(binding.obligations),
                    'guard_errors': problems, 'source_fold_events': events,
                    'chapter_budgets': {c.chapter_ordinal: c.length_budget
                                        for c in writer_ir.chapters}}
    repository.write('stage4/acceptance/guard/report.json', guard_report)
    if problems:
        raise ValueError(problems)
    if through == 'stage4':
        return guard_report, FINAL_STATUS['stage4'], False

    # ── Hook Writer：单产 + 机械验；超长同 Context 有限重写（attempt 进请求身份） ──
    prose, attempts_log, attempt_identity = _write_hook(hook_context, ask_text)
    status = 'hook_accepted_mechanical' if prose is not None else 'hook_rejected'
    receipt = HookReceipt(
        hook_context_sha256=context_sha256(hook_context),
        prose_sha256=sha256_text(prose or ''),
        request_identity=attempt_identity,
        validator_version=HOOK_VALIDATOR_VERSION,
        status='accepted' if prose is not None else 'rejected',
        mechanical_checks=MECHANICAL_CHECKS)
    if prose is not None:
        repository.write_text('stage5/hook/hook.txt', prose)
    repository.write('stage5/hook/receipt.json', receipt)
    repository.write('runtime/hook_attempts.json', attempts_log)
    if prose is None:
        repository.write('runtime/workflow_status.json', {
            'status': status, 'accepted_body': False})
        raise ValueError('Hook 写作在重写上限内未通过机械校验；先人工复核 HookContext')

    # ── Body：严格按章串行；恢复从连续有效章前缀开始 ──
    ir_sha = book_ir_sha256(writer_ir)
    target_chapters = (min(body_chapter_limit, len(writer_ir.chapters))
                       if body_chapter_limit is not None else len(writer_ir.chapters))
    committed, committed_receipts = _committed_prefix(
        repository, writer_ir, ir_sha, target_chapters)
    for ordinal in range(len(committed) + 1, target_chapters + 1):
        outcome = write_chapter(
            book_ir=writer_ir, graph=body, projection=projection,
            chapter_ordinal=ordinal, committed=committed, structure=chars.name_structure,
            ask=lambda prompt, ordinal=ordinal: ask_body(
                f'body_{_chapter_ordinal(ordinal)}', prompt))
        repository.write(f'runtime/body_attempts_{_chapter_ordinal(ordinal)}.json',
                         {'attempts': [a._asdict() for a in outcome.attempts]})
        if outcome.prose is None or outcome.receipt is None:
            repository.write('runtime/workflow_status.json', {
                'status': 'body_rejected', 'accepted_body': False,
                'chapter': ordinal,
                'detail': '重写额度内无合法候选（硬检查未全过）；停止后续章'})
            raise ValueError(f'第{ordinal}章在重写额度内无合法候选；先人工复核')
        # 原子写正文 → 原子写章回执（提交完成）；拒绝候选留证在 attempts 记录。
        repository.write_text(f'stage5/body/{_chapter_ordinal(ordinal)}.txt', outcome.prose)
        repository.write(f'stage5/acceptance/body/{_chapter_ordinal(ordinal)}.json',
                         outcome.receipt)
        committed[ordinal] = outcome.prose
        committed_receipts[ordinal] = outcome.receipt.model_dump(mode='json')
    body_complete = len(committed) == len(writer_ir.chapters)
    if through == 'stage5':
        status = 'body_hook_accepted_mechanical' if body_complete else 'body_partial'
        return {'hook_chars': len(prose), 'materials': len(hook_context.materials),
                'pov': hook_context.pov.model_dump(mode='json'),
                'body_chapters': sorted(committed), 'body_complete': body_complete,
                'length_disclosed_chapters': [
                    k for k, r in committed_receipts.items() if r.get('length_disclosed')],
                }, status, body_complete

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

    # ── Assembly：章级回执链核对 → 改名 → 最终 TXT 先落盘 → manifest 最后提交 ──
    chapter_prose = {k: committed[k] for k in sorted(committed)}
    access = chapter_access(range(1, len(writer_ir.chapters) + 1), committed_receipts)
    chain_problems = verify_chapter_chain(committed_receipts, chapter_prose,
                                          book_ir_sha256=ir_sha)
    body_text = prior_body_text(chapter_prose) if chapter_prose else None
    hook_sha = sha256_text(prose)
    outcome = assemble_delivery(
        body_text=body_text, body=access, hook_prose=prose,
        hook_receipt=receipt.model_dump(mode='json'),
        hook_context_sha256=context_sha256(hook_context),
        replacements=replacements, kept_literals=kept_literals)
    report = {'mode': 'body+hook+naming', 'accepted_body': access.ready and not chain_problems,
              'chapters': len(compiled), 'committed_chapters': sorted(committed),
              'scenes': len(body.scenes), 'obligations': len(binding.obligations),
              'guard_errors': problems, 'source_fold_events': events,
              'chain_problems': chain_problems,
              'hook': {'chars': len(prose), 'renamed_chars': len(renamed_hook),
                       'materials': len(hook_context.materials),
                       'replacements': len(replacements), 'kept': len(kept_literals)},
              'assembly': {'status': outcome.status, 'reason': outcome.reason,
                           'delivery_identity': outcome.delivery_identity}}
    if outcome.status == 'ready':
        final_rel = f'stage7/final/{book_title or "book"}_rewriting.txt'
        # 文件先落盘，manifest 最后提交：消费者核对 manifest 才算就绪。
        repository.write_text(final_rel, outcome.delivery_bytes.decode('utf-8'))
        manifest = build_delivery_manifest(
            delivery_identity=outcome.delivery_identity,
            final_text_sha256=repository.file_sha256(repository.path(final_rel)),
            receipts=committed_receipts, chapter_prose=chapter_prose,
            hook_prose_sha256=hook_sha, name_map_sha256=repository.file_sha256(
                repository.path('stage7/naming/name_map.json')),
            total_length_budget=budget)
        repository.write('stage7/final/delivery_manifest.json', manifest)
        repository.write('runtime/delivery/assembly_report.json', report)
        return report, 'delivered', True
    repository.write('runtime/delivery/assembly_report.json', report)
    return report, 'assembly_not_ready', False


def _committed_prefix(repository, writer_ir, ir_sha, target_chapters):
    """恢复：从连续有效章前缀装载已提交正文与回执；首个断裂处停止。

    回执绑定当前全书 IR 与实际前文；正文文件与回执 prose_sha256 必须逐字一致。
    无有效回执的文本不能作为前文（崩溃窗口：正文已写、回执未写 → 本章重做）。
    """
    committed: dict[int, str] = {}
    receipts: dict[int, dict] = {}
    for ordinal in range(1, target_chapters + 1):
        rel_receipt = f'stage5/acceptance/body/{_chapter_ordinal(ordinal)}.json'
        rel_prose = f'stage5/body/{_chapter_ordinal(ordinal)}.txt'
        try:
            receipt = repository.read(rel_receipt)
        except FileNotFoundError:
            break
        try:
            prose = repository.read_text(rel_prose)
        except FileNotFoundError:
            break
        prior = prior_sha(committed)
        problems = check_chapter_receipt(
            _receipt_model(receipt), book_ir_sha256=ir_sha,
            prior_text_sha256=prior, prose_sha256=sha256_text(prose))
        if problems:
            break
        committed[ordinal] = prose
        receipts[ordinal] = receipt
    return committed, receipts


def prior_sha(committed: dict[int, str]) -> str:
    from novel.stages.str06.body_writer import prior_body_text
    return sha256_text(prior_body_text(committed))


def _receipt_model(receipt: dict):
    from contracts.schema.str06.receipt import ChapterReceipt
    return ChapterReceipt.model_validate(receipt)


def _write_hook(hook_context, ask_text):
    """单产 + 机械验 + 同 Context 有限重写。返回 (prose|None, attempts_log, 请求身份)。"""
    attempts: list[dict] = []
    errors: list[str] = []
    identity = ''
    for attempt in range(1 + MAX_REWRITES):
        prompt = (build_hook_user_prompt(hook_context) if attempt == 0
                  else build_rewrite_user_prompt(hook_context, errors))
        prose, response_errors, answered = ask_text('hook_writer', prompt)
        identity = answered
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
    parser.add_argument('--body-band-low', type=float, default=0.6)
    parser.add_argument('--body-band-high', type=float, default=1.3)
    parser.add_argument('--body-chapter-limit', type=int,
                        help='只生成前 N 章（前缀式）；缺省为全书')
    parser.add_argument('--book-call-ceiling', type=int,
                        help='本次运行允许的新购调用上限（锁内核对；缓存采用不计）')
    parser.add_argument('--book-title', default='')
    parser.add_argument('--reasoning-effort', default='high',
                        help='JSON/正文请求的思考档位；high 档 reasoning 可能耗尽 '
                             'max_tokens 导致响应截断（F5e 实证），可显式降档')
    parser.add_argument('--check-only', action='store_true',
                        help='免费检查：只允许缓存采用与 wire 采用，不签发新调用')
    parser.add_argument('--adopt-root', type=Path, action='append', default=[],
                        help='登记的显式历史采用源根（候选 run 目录，可重复）：'
                             '同请求身份的已购结果经身份／字节／成本关联核对后零新购 '
                             '采用；身份不符、证据损坏或付费状态未知明确阻断')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--allow-source-export', action='store_true')
    args = parser.parse_args()
    if args.hook_target_chars > args.hook_hard_max:
        raise ValueError('--hook-target-chars 不得大于 --hook-hard-max')
    if not 0 < args.body_band_low < args.body_band_high:
        raise ValueError('--body-band-low/high 必须满足 0 < low < high')
    if args.check_only and (args.yes or args.allow_source_export):
        raise ValueError('--check-only 不签发新调用：去掉 --yes/--allow-source-export 再跑')
    for root in args.adopt_root:
        if not root.resolve().is_dir():
            raise ValueError(f'--adopt-root 不是目录：{root}')
    base = os.environ.get('NOVEL_OUTPUT')
    if not base:
        raise ValueError('NOVEL_OUTPUT 未设置：没查、未运行')
    base_path = Path(base).resolve()
    run_dir = args.run_dir.resolve()
    if not run_dir.is_relative_to(base_path):
        raise ValueError('--run-dir 必须位于 NOVEL_OUTPUT 下的隔离根')
    # 一个 NOVEL_OUTPUT 根一个候选链：facilities 的 sid 取 run 目录名。
    sid = run_dir.relative_to(base_path).as_posix().replace('/', '_')
    source_bytes = args.source_json.read_bytes()
    source_identity = hashlib.sha256(source_bytes).hexdigest()
    root_sha1 = prov.content_sha1(source_bytes)
    repository = Str06Repository(run_dir)
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
    # workflow／epoch 身份：首开落记录，复用不换身份，代码漂移滚动，源输入变化拒绝。
    workflow, context = init_or_resume_candidate_workflow(
        repository=repository, base=base_path, code=prov.code_version(),
        root_sha1=root_sha1, source_identity=source_identity,
        task_id=require_workflow(base_path).task_id,
        configured_model='unknown' if args.wire_dir else cfg.model,
        created_at=int(time.time()), readonly=args.check_only)
    ledger = context.ledger
    session = PaidRequestSession(repository, ledger, DeepSeekTransport(cfg), yes=args.yes,
                                 allow_source_export=args.allow_source_export,
                                 provider=cfg.base_url,
                                 book_call_ceiling=args.book_call_ceiling, sid=sid,
                                 adopt_roots=tuple(args.adopt_root))

    def adopt(owner, path, validate, *, system, prompt, json_mode, temperature):
        if args.repair_manifest and (
                owner not in adopted or Path(adopted[owner]['path']).resolve() != path.resolve()
                or repository.file_sha256(path) != adopted[owner]['sha256']):
            raise ValueError(f'{owner} wire 不在已核实修订 manifest 内')
        raw = repository.read_external(path)
        errors = validate(raw)
        record = {
            'owner': owner, 'path': str(path.resolve()),
            'sha256': repository.file_sha256(path),
            'new_paid_calls': 0, 'historical_usage': None, 'errors': errors}
        if errors:
            repository.write(f'runtime/adopt_{owner}.json', record)
            raise ValueError(f'{owner} 免费采用失败：{errors}')
        # F7a-3：wire 采用登记身份／成本绑定——注册源根有同请求身份已购行时三方
        # 绑定（身份／响应／成本），否则退到采用链（批次口径）或如实标未核。
        model = 'unknown'
        if args.adopt_root:
            request = build_request(owner, prompt, json_mode=json_mode, system=system,
                                    temperature=temperature)
            identity = digest({'owner': owner, 'provider': session.provider,
                               'request': request.cache_payload()})
            allow_patch, patch_reason = False, None
            note_path = (args.wire_dir / f'{owner}_patch_note.json'
                         if args.wire_dir else None)
            if note_path is not None and note_path.exists():
                note = repository.read_external(note_path)
                if note.get('base_response_identity') != identity:
                    raise ValueError(
                        f'{owner} patch note 的基线身份与当前请求不一致，拒绝采用')
                allow_patch, patch_reason = True, note.get('reason')
            record.update(session.bind_wire_adoption(owner, identity, path,
                                                     allow_patch=allow_patch))
            if patch_reason:
                record['owner_patch_reason'] = patch_reason
            model = record.get('model') or 'unknown'
        repository.write(f'runtime/adopt_{owner}.json', record)
        context.note_adoption(owner=owner, model=model,
                              prompts={'system': system, 'user': prompt})
        return raw

    def wire_path(owner):
        if not args.wire_dir:
            return None
        path = args.wire_dir / f'{owner}_wire.json'
        return path if path.exists() else None

    def build_request(owner, prompt, *, json_mode, system, temperature):
        from novel.llm.budget import enforce_request_budget
        request = ChatRequest(model=cfg.model, system=system, user=prompt,
                              reasoning_effort=args.reasoning_effort,
                              thinking=cfg.thinking_mode == 'on',
                              json_mode=json_mode, max_tokens=65536, temperature=temperature)
        if not args.wire_dir:
            # 完整输入超限就阻断：精确 tokenizer + 官方 framing，不摘要、不截断。
            enforce_request_budget([request], node='str06', purpose=owner)
        return request

    def ask(owner, prompt, validate):
        path = wire_path(owner)
        if path is not None:
            return adopt(owner, path, validate, system=SYS, prompt=prompt,
                         json_mode=True, temperature=0.3)
        request = build_request(owner, prompt, json_mode=True, system=SYS, temperature=0.3)
        return session.ask(owner, request, validate)

    def ask_text(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw = adopt(owner, path, lambda w: [], system=HOOK_WRITER_SYSTEM,
                        prompt=prompt, json_mode=False, temperature=0.7)
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return prose, [], 'wire-adoption'
        request = build_request(owner, prompt, json_mode=False, system=HOOK_WRITER_SYSTEM,
                                temperature=0.7)
        answered = session.ask_answered(owner, request)
        return answered.text, answered.errors, answered.identity

    def ask_body(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw = adopt(owner, path, lambda w: [], system=BODY_WRITER_SYSTEM,
                        prompt=prompt, json_mode=False, temperature=0.7)
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return AnsweredText(text=prose, finish_reason=None, identity='wire-adoption',
                                cached=True)
        request = build_request(owner, prompt, json_mode=False, system=BODY_WRITER_SYSTEM,
                                temperature=0.7)
        answered = session.ask_answered(owner, request)
        return AnsweredText(text=answered.text, finish_reason=answered.finish_reason,
                            identity=answered.identity, cached=answered.cached)

    print(run_workflow(repository=repository, chapters=chapters, ask=ask, ask_text=ask_text,
                       ask_body=ask_body, budget=args.budget, through=args.through,
                       provider='manual-adoption' if args.wire_dir else cfg.base_url,
                       model='unknown' if args.wire_dir else cfg.model,
                       book_title=args.book_title,
                       hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                         hard_max_chars=args.hook_hard_max),
                       length_band=(args.body_band_low, args.body_band_high),
                       body_chapter_limit=args.body_chapter_limit))


if __name__ == '__main__':
    main()
