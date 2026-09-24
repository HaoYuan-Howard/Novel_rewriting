"""runner —— STR-06 候选链的 owner runner 编排（F7b）。

【为什么拆】原 harness 把「读输入 → LLM → compile → 落盘 → 状态」缠在一个长函数
里，单节点无法重跑、输入无法从盘面闭合。本模块按 F7a 候选节点表把流程拆成 11 个
owner runner：每个 runner 只做本节点的编排——从 repository 读本节点的正式输入、
调 owner 引擎、经付费边界取已购响应、写本节点产物（provenance 随写随铸）。
harness 逻辑（证据切分、身份投影、状态记录）随之归 owner；`tools/str06_workflow.py`
只剩参数、设施装配与计划推进（薄驱动）。

【与候选节点表的关系】`NODE_ORDER` 是执行序；`run_node` 按 id 分派。双向对账在
`tests/test_str06_candidates.py`：表里每个 paid/free 节点都有 runner，每个 runner
都落在表内节点上，produces 与唯一 writer 一致。复检走 `--check-only`（免费采用与
重放），不新造 check 节点。

【输入闭合边界（F7b 已知限制，F7c 收口）】源章仍由调用方经 `ChainContext.chapters`
传入（F7c 起改读正式 Stage1 章数组）；其余输入一律从盘面读——单节点重放因此可能。
源抽取的 core 与 BodyDesign 落为正式派生产物（`stage2/compiled/book_extraction.json`、
`stage4/design/body_design.json`），不再是只活在内存里的中间态。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from contracts.schema.str06 import OutputConstraints
from contracts.schema.str06.hook import HookConstraints, HookReceipt
from novel.stages.str06.assembly import (
    assemble_delivery, build_delivery_manifest, chapter_access, verify_chapter_chain,
)
from novel.stages.str06.body_writer import (
    AnsweredText, book_ir_sha256, prior_body_text, sha256_text, write_chapter,
)
from novel.stages.str06.book_ir import compile_writer_book_ir
from novel.stages.str06.checks import check_chapter_receipt
from novel.stages.str06.compile import compile_execution_plan
from novel.stages.str06.design_compile import assemble_body_design
from novel.stages.str06.grounding import assemble_preserve, project_brief, assemble_binding
from novel.stages.str06.guard import run_mechanical_guard
from novel.stages.str06.hook_context import compile_hook_context
from novel.stages.str06.hook_design import assemble_hook_design, publish_graph
from novel.stages.str06.hook_writer import (
    HOOK_VALIDATOR_VERSION, MECHANICAL_CHECKS, MAX_REWRITES,
    build_hook_user_prompt, build_rewrite_user_prompt, context_sha256, validate_hook_prose,
)
from novel.stages.str06.name_structure import compile_name_structure, merge_name_structure
from novel.stages.str06.naming import (
    apply_names, check_residual, compile_name_map, compile_surface_replacements,
    naming_projection,
)
from novel.stages.str06.prompts import (
    SYS, BOOK_PROMPT, CHAPTER_PROMPT, PRESERVE_RULES, DESIGN_RULES, BIND_RULES,
    HOOK_DESIGN_RULES, HOOK_WRITER_SYSTEM, NAMES_RULES, NAMING_RULES, pack_prompt,
)
from novel.stages.str06.source_compile import (
    ChapterCompiler, assemble_blueprint, compile_book, chapter_input_snapshot,
    initial_snapshot, source_fold,
)

#: 执行序（stage2 → stage4 → stage5 → stage7；与 --through 的腿边界一致）。
NODE_ORDER: tuple[str, ...] = (
    'str06.stage2.source', 'str06.stage2.names',
    'str06.stage4.preserve', 'str06.stage4.body_design', 'str06.stage4.bind',
    'str06.stage4.hook_design', 'str06.stage4.compile',
    'str06.stage5.hook', 'str06.stage5.body',
    'str06.stage7.naming', 'str06.stage7.assemble',
)

#: --through 腿 → 跑到哪个节点为止（含）。
THROUGH_NODE = {'stage2': 'str06.stage2.names', 'stage4': 'str06.stage4.compile',
                'stage5': 'str06.stage5.body', 'stage7': 'str06.stage7.assemble'}

FINAL_STATUS = {'stage2': 'names_applied', 'stage4': 'planned_validated',
                'stage5': 'body_hook_accepted_mechanical', 'stage7': 'delivered'}


@dataclass
class ChainContext:
    """一次链执行的显式依赖：repository、付费边界回调与运行参数。"""

    repository: Any
    ask: Callable[[str, str, Callable], Any]
    ask_text: Callable[[str, str], tuple[str | None, list[str], str]]
    ask_body: Callable[[str, str], AnsweredText]
    chapters: list[dict]
    budget: int = 11000
    book_title: str = ''
    hook_constraints: HookConstraints = field(
        default_factory=lambda: HookConstraints(target_chars=280, hard_max_chars=360))
    length_band: tuple[float, float] = (0.6, 1.3)
    body_chapter_limit: int | None = None
    provider: str = 'manual-adoption'
    model: str = 'unknown'
    #: canonical JSON 摘要（请求身份）；由调用方从付费边界注入——迁移包不
    #: import llm 层（test_str06_invariants 纪律）。
    digest: Callable[[object], str] | None = None


def build_evidence(chapters) -> dict:
    """源证据切分（stage2 source owner 的 harness 逻辑，F7b 从工具层收回）。"""
    evidence: dict[str, dict] = {}
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


def _chapter_ordinal(n: int) -> str:
    return f'ch{n:02d}'


def derive_total_length_budget(chapters: list[dict], *,
                               rounding: int = 1000) -> int:
    """书级正文篇幅目标：从源章数组总字数确定性派生（保留全书篇幅边界）。

    向上取整到 `rounding` 的整数倍——同一份源章数组永远得到同一个预算，预算
    进 IR（book_brief.total_length_budget）从而进请求身份；换源即换预算，
    不沿用旧根的正文请求结论。付费调用额度不参与本计算（Q-10 两层是次数闸，
    不是篇幅语义）。
    """
    total = sum(len(c['text']) for c in chapters)
    if total <= 0:
        raise ValueError('源章数组为空，无法派生书级篇幅')
    return -(-total // rounding) * rounding


# ─── stage2 source ───────────────────────────────────────────────────────────

def run_source(ctx: ChainContext) -> dict:
    repository = ctx.repository
    chapters = ctx.chapters
    evidence = build_evidence(chapters)
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in chapters)
    block = '\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items())
    book = ctx.ask('book', BOOK_PROMPT.format(n=len(chapters), text=full, evidence=block),
                   lambda w: compile_book(w, evidence_ids=evidence)[2])
    core, wcs, errors = compile_book(book, evidence_ids=evidence)
    if errors:
        raise ValueError(errors)
    # core 落为正式派生产物：names／preserve 的单节点输入从盘面闭合。
    repository.write('stage2/compiled/book_extraction.json', core,
                     owners=('book', 'ch01', 'ch02', 'ch03', 'ch04', 'ch05', 'ch06',
                             'ch07', 'ch08', 'ch09', 'ch10', 'ch11'))
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
            evidence='\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items()
                               if v['chapter'] == no),
            known_beats=', '.join(sorted(compiler.known_beats)))
        wire = ctx.ask(f'ch{no:02d}', prompt, lambda w: compiler.validate(w, no))
        bp, errors = compiler.compile(wire, no)
        if errors:
            raise ValueError(errors)
        compiled.append(bp)
    blueprint = assemble_blueprint(wcs, compiled)
    repository.write('stage2/blueprints/chapter_blueprints.json', blueprint)
    repository.write('stage2/compiled/input_snapshots.json', snapshots)
    _, events = source_fold(core, blueprint)
    repository.write('stage2/acceptance/fold.json', {'events': events})
    return {'chapters': len(compiled), 'fold_events': events}


# ─── stage2 names ────────────────────────────────────────────────────────────

def run_names(ctx: ChainContext) -> dict:
    repository = ctx.repository
    core = repository.read_model('stage2/compiled/book_extraction.json')
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in ctx.chapters)
    names_prompt = pack_prompt(NAMES_RULES, characters=core.model_dump(mode='json'),
                               text=full)
    names_wire = ctx.ask('names', names_prompt,
                         lambda w: compile_name_structure(w, core, full)[1])
    structure, errors = compile_name_structure(names_wire, core, full)
    if errors:
        raise ValueError(errors)
    chars, errors = merge_name_structure(core, structure)
    if errors:
        raise ValueError(errors)
    repository.write('stage2/characters/characters.json', chars, owners=('names',))
    return {'name_structure_people': len(structure.people)}


# ─── stage4 preserve ─────────────────────────────────────────────────────────

def run_preserve(ctx: ChainContext) -> dict:
    repository = ctx.repository
    core = repository.read_model('stage2/compiled/book_extraction.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    preserve_prompt = pack_prompt(PRESERVE_RULES,
                                  characters=core.model_dump(mode='json'),
                                  blueprints=blueprint.model_dump(mode='json'))

    def preserve_compile(w):
        return assemble_preserve(w, policy_id='preserve-default', policy_version=1,
                                 characters=core, blueprint=blueprint,
                                 provider=ctx.provider, model=ctx.model,
                                 request_sha256=ctx.digest(preserve_prompt))

    pw = ctx.ask('preserve', preserve_prompt, lambda w: preserve_compile(w)[1])
    checkpoint, errors = preserve_compile(pw)
    if errors:
        raise ValueError(errors)
    repository.write('runtime/preserve_checkpoint.json', checkpoint, owners=('preserve',))
    brief, identities = project_brief(checkpoint, core)
    repository.write('stage4/grounding/reconstruction_brief.json', brief)
    return {'obligations': len(checkpoint.obligations)}


# ─── stage4 body_design ──────────────────────────────────────────────────────

def run_body_design(ctx: ChainContext) -> dict:
    repository = ctx.repository
    core = repository.read_model('stage2/compiled/book_extraction.json')
    checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
    brief, identities = project_brief(checkpoint, core)
    chars = repository.read_model('stage2/characters/characters.json')
    constraints = OutputConstraints(target_chapter_count=len(ctx.chapters),
                                    total_length_budget=ctx.budget)
    design_prompt = pack_prompt(DESIGN_RULES, brief=brief.model_dump(mode='json'),
                                identities=[i.model_dump(mode='json') for i in identities],
                                constraints=constraints.model_dump(mode='json'))

    def design_compile(w):
        return assemble_body_design(
            w, known_char_ids=set(chars.characters), constraints=constraints,
            char_canonicals={c.canonical: cid for cid, c in chars.characters.items()})

    dw = ctx.ask('design', design_prompt, lambda w: design_compile(w)[1])
    body, errors = design_compile(dw)
    if errors:
        raise ValueError(errors)
    # BodyDesign 落为正式派生产物：bind／hook_design 的单节点输入从盘面闭合。
    repository.write('stage4/design/body_design.json', body, owners=('design',))
    return {'scenes': len(body.scenes)}


# ─── stage4 bind ─────────────────────────────────────────────────────────────

def run_bind(ctx: ChainContext) -> dict:
    repository = ctx.repository
    body = repository.read_model('stage4/design/body_design.json')
    checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
    positions = {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes}
    bind_prompt = pack_prompt(BIND_RULES, design=body.model_dump(mode='json'),
                              obligations=[o.model_dump(mode='json')
                                           for o in checkpoint.obligations])
    bw = ctx.ask('bind', bind_prompt,
                 lambda w: assemble_binding(w, checkpoint, positions)[1])
    binding, errors = assemble_binding(bw, checkpoint, positions)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/grounding/semantic_binding.json', binding, owners=('bind',))
    return {'entries': len(binding.entries), 'unbound': len(binding.unbound)}


# ─── stage4 hook_design ──────────────────────────────────────────────────────

def run_hook_design(ctx: ChainContext) -> dict:
    repository = ctx.repository
    body = repository.read_model('stage4/design/body_design.json')
    chars = repository.read_model('stage2/characters/characters.json')
    checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
    core = repository.read_model('stage2/compiled/book_extraction.json')
    _, identities = project_brief(checkpoint, core)
    projection = chars.name_projection()
    hook_design_prompt = pack_prompt(
        HOOK_DESIGN_RULES, book_title=ctx.book_title, design=body.model_dump(mode='json'),
        identities=[i.model_dump(mode='json') for i in identities],
        constraints=ctx.hook_constraints.model_dump(mode='json'))

    def hook_design_compile(w):
        return assemble_hook_design(w, body=body, known_char_ids=set(chars.characters),
                                    name_projection=projection)

    hw = ctx.ask('hook_design', hook_design_prompt, lambda w: hook_design_compile(w)[1])
    hook, errors = hook_design_compile(hw)
    if errors:
        raise ValueError(errors)
    graph = publish_graph(body, hook)
    repository.write('stage4/design/presentation_graph.json', graph,
                     owners=('design', 'hook_design'))
    return {'materials': len(hook.materials)}


# ─── stage4 compile（Guard＋WriterBookIR＋HookContext，确定性） ───────────────

def run_compile(ctx: ChainContext) -> dict:
    repository = ctx.repository
    graph = repository.read_model('stage4/design/presentation_graph.json')
    binding = repository.read_model('stage4/grounding/semantic_binding.json')
    chars = repository.read_model('stage2/characters/characters.json')
    plan, errors = compile_execution_plan(graph, binding)
    repository.write('stage4/acceptance/guard/compile.json', {'errors': errors})
    if errors:
        raise ValueError(errors)
    repository.write('stage4/compiled/execution_plan.json', plan)
    projection = chars.name_projection()
    hook_context, errors = compile_hook_context(graph, projection, ctx.hook_constraints)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/context/hook/hook.json', hook_context)
    writer_ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                       total_budget=ctx.budget,
                                       length_band=ctx.length_band,
                                       book_title=ctx.book_title)
    problems = []
    for chapter_ir in writer_ir.chapters:
        for context in chapter_ir.scenes:
            problems.extend(run_mechanical_guard(binding=binding, context=context,
                                                 graph=graph))
    repository.write('stage4/ir/writer_book_ir.json', writer_ir)
    guard_report = {'mode': 'planned-only', 'accepted_body': False,
                    'chapters': len(writer_ir.chapters), 'scenes': len(graph.scenes),
                    'obligations': len(binding.obligations), 'guard_errors': problems,
                    'chapter_budgets': {c.chapter_ordinal: c.length_budget
                                        for c in writer_ir.chapters}}
    repository.write('stage4/acceptance/guard/report.json', guard_report)
    if problems:
        raise ValueError(problems)
    return guard_report


# ─── stage5 hook ─────────────────────────────────────────────────────────────

def _write_hook(hook_context, ask_text) -> tuple[str | None, list[dict], str]:
    """单产 + 机械验 + 同 Context 有限重写（stage5 hook owner 的成文编排）。"""
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


def run_hook(ctx: ChainContext) -> dict:
    repository = ctx.repository
    hook_context = repository.read_model('stage4/context/hook/hook.json')
    prose, attempts_log, attempt_identity = _write_hook(hook_context, ctx.ask_text)
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
    repository.write('stage5/hook/receipt.json', receipt, owners=('hook_writer',))
    repository.write('runtime/hook_attempts.json', {'attempts': attempts_log})
    if prose is None:
        repository.write('runtime/workflow_status.json',
                         {'status': status, 'accepted_body': False})
        raise ValueError('Hook 写作在重写上限内未通过机械校验；先人工复核 HookContext')
    return {'hook_chars': len(prose), 'materials': len(hook_context.materials),
            'pov': hook_context.pov.model_dump(mode='json')}


# ─── stage5 body ─────────────────────────────────────────────────────────────

def prior_sha(committed: dict[int, str]) -> str:
    return sha256_text(prior_body_text(committed))


def _committed_prefix(repository, writer_ir, ir_sha, target_chapters):
    """恢复：从连续有效章前缀装载已提交正文与回执；首个断裂处停止。

    回执绑定当前全书 IR 与实际前文；正文文件与回执 prose_sha256 必须逐字一致。
    无有效回执的文本不能作为前文（崩溃窗口：正文已写、回执未写 → 本章重做）。
    读回执时遇到上游过期（IR／前文已变）同样视为前缀断裂——该章按新身份重做，
    与回执自身的 IR 绑定校验是同一语义的两道闸。
    """
    from novel.errors import ProvenanceRefused, UpstreamStale
    committed: dict[int, str] = {}
    receipts: dict[int, dict] = {}
    for ordinal in range(1, target_chapters + 1):
        rel_receipt = f'stage5/acceptance/body/{_chapter_ordinal(ordinal)}.json'
        rel_prose = f'stage5/body/{_chapter_ordinal(ordinal)}.txt'
        try:
            receipt = repository.read(rel_receipt)
        except (FileNotFoundError, UpstreamStale, ProvenanceRefused):
            break
        try:
            prose = repository.read_text(rel_prose)
        except FileNotFoundError:
            break
        problems = check_chapter_receipt(
            _receipt_model(receipt), book_ir_sha256=ir_sha,
            prior_text_sha256=prior_sha(committed), prose_sha256=sha256_text(prose))
        if problems:
            break
        committed[ordinal] = prose
        receipts[ordinal] = receipt
    return committed, receipts


def _receipt_model(receipt: dict):
    from contracts.schema.str06.receipt import ChapterReceipt
    return ChapterReceipt.model_validate(receipt)


def run_body(ctx: ChainContext) -> dict:
    repository = ctx.repository
    writer_ir = repository.read_model('stage4/ir/writer_book_ir.json')
    graph = repository.read_model('stage4/design/presentation_graph.json')
    chars = repository.read_model('stage2/characters/characters.json')
    projection = chars.name_projection()
    ir_sha = book_ir_sha256(writer_ir)
    target_chapters = (min(ctx.body_chapter_limit, len(writer_ir.chapters))
                       if ctx.body_chapter_limit is not None else len(writer_ir.chapters))
    committed, committed_receipts = _committed_prefix(repository, writer_ir, ir_sha,
                                                      target_chapters)
    for ordinal in range(len(committed) + 1, target_chapters + 1):
        outcome = write_chapter(
            book_ir=writer_ir, graph=graph, projection=projection,
            chapter_ordinal=ordinal, committed=committed,
            structure=chars.name_structure,
            ask=lambda prompt, ordinal=ordinal: ctx.ask_body(
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
                         outcome.receipt, owners=(f'body_{_chapter_ordinal(ordinal)}',))
        committed[ordinal] = outcome.prose
        committed_receipts[ordinal] = outcome.receipt.model_dump(mode='json')
    body_complete = len(committed) == len(writer_ir.chapters)
    return {'body_chapters': sorted(committed), 'body_complete': body_complete,
            'length_disclosed_chapters': [
                k for k, r in committed_receipts.items() if r.get('length_disclosed')]}


# ─── stage7 naming ───────────────────────────────────────────────────────────

def run_naming(ctx: ChainContext) -> dict:
    repository = ctx.repository
    chars = repository.read_model('stage2/characters/characters.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    naming_prompt = pack_prompt(NAMING_RULES, book_title=ctx.book_title,
                                projection=naming_projection(chars, blueprint))
    nw = ctx.ask('naming', naming_prompt, lambda w: compile_name_map(w, chars)[1])
    name_map, errors = compile_name_map(nw, chars)
    if errors:
        raise ValueError(errors)
    repository.write('stage7/naming/name_map.json', name_map, owners=('naming',))
    return {'entries': len(name_map.entries)}


# ─── stage7 assemble ─────────────────────────────────────────────────────────

def _kept_surfaces(chars, char_ref: str) -> set[str]:
    person = chars.name_structure.people[char_ref]
    return {s for s, form in person.surface_forms.items() if form is not None}


def run_assemble(ctx: ChainContext) -> dict:
    repository = ctx.repository
    writer_ir = repository.read_model('stage4/ir/writer_book_ir.json')
    graph = repository.read_model('stage4/design/presentation_graph.json')
    chars = repository.read_model('stage2/characters/characters.json')
    name_map = repository.read_model('stage7/naming/name_map.json')
    hook_context = repository.read_model('stage4/context/hook/hook.json')
    binding = repository.read_model('stage4/grounding/semantic_binding.json')
    receipt = repository.read_model('stage5/hook/receipt.json')
    prose = repository.read_text('stage5/hook/hook.txt')
    ir_sha = book_ir_sha256(writer_ir)
    committed, committed_receipts = _committed_prefix(
        repository, writer_ir, ir_sha, len(writer_ir.chapters))
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
              'chapters': len(writer_ir.chapters), 'committed_chapters': sorted(committed),
              'scenes': len(graph.scenes), 'obligations': len(binding.obligations),
              'chain_problems': chain_problems,
              'hook': {'chars': len(prose), 'renamed_chars': len(renamed_hook),
                       'materials': len(hook_context.materials),
                       'replacements': len(replacements), 'kept': len(kept_literals)},
              'assembly': {'status': outcome.status, 'reason': outcome.reason,
                           'delivery_identity': outcome.delivery_identity}}
    if outcome.status == 'ready':
        final_rel = f'stage7/final/{ctx.book_title or "book"}_rewriting.txt'
        # 文件先落盘，manifest 最后提交：消费者核对 manifest 才算就绪。
        repository.write_text(final_rel, outcome.delivery_bytes.decode('utf-8'))
        manifest = build_delivery_manifest(
            delivery_identity=outcome.delivery_identity,
            final_text_sha256=repository.file_sha256(repository.path(final_rel)),
            receipts=committed_receipts, chapter_prose=chapter_prose,
            hook_prose_sha256=hook_sha,
            name_map_sha256=repository.file_sha256(
                repository.path('stage7/naming/name_map.json')),
            total_length_budget=ctx.budget)
        repository.write('stage7/final/delivery_manifest.json', manifest)
        repository.write('runtime/delivery/assembly_report.json', report)
        return report
    repository.write('runtime/delivery/assembly_report.json', report)
    return report


NODE_RUNNERS: dict[str, Callable[[ChainContext], dict]] = {
    'str06.stage2.source': run_source,
    'str06.stage2.names': run_names,
    'str06.stage4.preserve': run_preserve,
    'str06.stage4.body_design': run_body_design,
    'str06.stage4.bind': run_bind,
    'str06.stage4.hook_design': run_hook_design,
    'str06.stage4.compile': run_compile,
    'str06.stage5.hook': run_hook,
    'str06.stage5.body': run_body,
    'str06.stage7.naming': run_naming,
    'str06.stage7.assemble': run_assemble,
}


def run_node(node_id: str, ctx: ChainContext) -> dict:
    """单节点执行（可单节点重跑）；未登记节点关闭式拒绝。"""
    runner = NODE_RUNNERS.get(node_id)
    if runner is None:
        raise ValueError(f'未登记的候选节点 runner：{node_id}')
    return runner(ctx)


def run_chain(ctx: ChainContext, *, through: str = 'stage4',
              node_filter: tuple[str, ...] | None = None) -> tuple[dict, str, bool]:
    """按候选节点表顺序执行到 through 腿（或 node_filter 指定节点集合）。"""
    if through not in THROUGH_NODE:
        raise ValueError(f'--through 取值面为 stage2|stage4|stage5|stage7：{through}')
    if [c['chapter_ordinal'] for c in ctx.chapters] != list(range(1, len(ctx.chapters) + 1)):
        raise ValueError('source JSON 必须为非空连续章序列表')
    if not ctx.chapters or any(not c['text'].strip() for c in ctx.chapters):
        raise ValueError('源章不能为空')
    context = getattr(ctx.repository, 'context', None)
    if context is not None:
        context.begin_run()
    boundary = THROUGH_NODE[through]
    selected = set(node_filter) if node_filter else None
    if selected is not None:
        unknown = selected - set(NODE_RUNNERS)
        if unknown:
            raise ValueError(f'未登记的候选节点：{sorted(unknown)}')
    report: dict[str, Any] = {}
    accepted_body = False
    for node_id in NODE_ORDER:
        if selected is not None and node_id not in selected:
            continue
        report.update(run_node(node_id, ctx))
        if node_id == 'str06.stage5.body':
            accepted_body = report.get('body_complete', False)
        if selected is None and node_id == boundary:
            break
    if selected is None:
        status = FINAL_STATUS[through]
        if through == 'stage5' and not accepted_body:
            status = 'body_partial'
        if through == 'stage7':
            assembly = report.get('assembly', {})
            status = 'delivered' if assembly.get('status') == 'ready' else 'assembly_not_ready'
            accepted_body = bool(assembly.get('status') == 'ready')
        report['accepted_body'] = accepted_body
        return report, status, accepted_body
    return report, 'node_run', accepted_body
