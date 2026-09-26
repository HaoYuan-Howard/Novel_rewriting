#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain —— STR-06 新链的节点编排与运行装配体（F7b owner runner；F8c 分流）。

【为什么拆】原 harness 把「读输入 → LLM → compile → 落盘 → 状态」缠在一个长函数
里，单节点无法重跑、输入无法从盘面闭合。本模块按 `contracts/invariants.py::NODES`
把流程拆成 11 个 owner runner：每个 runner 只做本节点的编排——从 repository 读
本节点的正式输入、调 owner 引擎、经付费边界取已购响应、写本节点产物
（provenance 随写随铸）。harness 逻辑（证据切分、身份投影、状态记录）随之归
owner；`execute` 只剩参数、设施装配与计划推进（薄驱动）。

【与节点表的关系】`NODE_ORDER` 是执行序；`run_node` 按 id 分派。双向对账在
`tests/test_str06_candidates.py`：表里每个 paid/free 节点都有 runner，每个 runner
都落在表内节点上，produces 与唯一 writer 一致。复检走 `--check-only`（免费采用与
重放），不新造 check 节点。

【输入闭合边界（F7b 已知限制，F7c 收口）】源章仍由调用方经 `ChainContext.chapters`
传入（F7c 起改读正式 Stage1 章数组）；其余输入一律从盘面读——单节点重放因此可能。
源抽取的 core 与 BodyDesign 落为正式派生产物（`stage2/compiled/book_extraction.json`、
`stage4/design/body_design.json`），不再是只活在内存里的中间态。

【本模块是装配体（F8c）】`execute`／`run_workflow`／`load_stage1_chapters` 原属
原工具层隔离驱动（已删除）；分流后与 runner 同模块——它是这条链唯一把
Stage1 输入、workflow／epoch、付费边界与恢复入口装到一起的地方，生产入口是
`novel.cli run <node> <sid>`。它与 `novel/stages/__init__.py`（只拿
`novel.run.RunContext` 做类型标注）是 `novel/stages/` 下仅有的两个碰运行时设施
的模块：编排层在上，确定性引擎层（各 stageN 包与 `novel/stages/common.py`）在
下，依赖单向（I-03）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from contracts.schema.grounding import OutputConstraints
from contracts.schema.hook import HookConstraints, HookReceipt
from contracts.schema.workflow import STR06_RUNTIME_CONFIG
from novel.config import load_config
from novel.llm.paid_requests import (
    PaidRequestSession, PurchaseRequired, digest,
    init_or_resume_candidate_workflow,
)
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel import paths, prov
from novel.stages.common import (
    BOOK_PROMPT, CHAPTER_PROMPT, PRESERVE_RULES, DESIGN_RULES, BIND_RULES,
    HOOK_DESIGN_RULES, HOOK_WRITER_SYSTEM, NAMES_RULES, NAMING_RULES, SYS,
    BODY_WRITER_SYSTEM, PRE_CHAPTER_RULE, pack_prompt,
)
from novel.stages.stage2 import (
    ChapterCompiler, assemble_blueprint, compile_book, compile_name_structure,
    chapter_input_snapshot, initial_snapshot, merge_name_structure, source_fold,
)
from novel.stages.stage4 import (
    assemble_binding, assemble_body_design,
    assemble_preserve, compile_execution_plan, compile_writer_book_ir,
    project_brief, run_mechanical_guard,
)
from novel.stages.stage5 import (
    AnsweredText, HOOK_VALIDATOR_VERSION, MAX_REWRITES, MECHANICAL_CHECKS,
    assemble_hook_design, book_ir_sha256, build_hook_user_prompt,
    build_rewrite_user_prompt, compile_hook_context, context_sha256,
    prior_body_text, publish_graph, sha256_text, validate_hook_prose,
    write_chapter,
)
from novel.stages.stage7 import (
    apply_names, assemble_delivery, build_delivery_manifest, chapter_access,
    check_residual, compile_name_map, compile_surface_replacements,
    naming_projection, verify_chapter_chain,
)
from novel.store import Str06Repository
from novel.work_units import require_workflow

__all__ = [
    'ChainContext', 'NODE_ORDER', 'NODE_RUNNERS', 'Stage1Input', 'THROUGH_NODE',
    'build_evidence', 'derive_total_length_budget', 'execute', 'load_dev_source',
    'load_stage1_chapters', 'run_chain', 'run_node', 'run_workflow',
]

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
    #: 章前文字（源引子）：Stage6 退役后 hook_copy 的唯一消费者是 Stage2.source
    # （质量改进批次 C）。空字符串＝显式无引子；进 run 级身份与 e00 证据切分。
    hook_copy: str = ''
    budget: int = 11000
    book_title: str = ''
    hook_constraints: HookConstraints = field(
        default_factory=lambda: HookConstraints(target_chars=280, hard_max_chars=360))
    length_band: tuple[float, float] = (0.6, 1.3)
    body_chapter_limit: int | None = None
    provider: str = 'manual-adoption'
    model: str = 'unknown'
    #: canonical JSON 摘要（请求身份）；由调用方从付费边界注入——chain 层不 import
    #: llm 设施（`novel/stages/common.py` 与各 stageN 包的纪律对本模块放宽：
    #: 它是装配体，但引擎段仍不得反向依赖这里）。
    digest: Callable[[object], str] | None = None


def _segment_sentences(text: str, *, limit: int = 300) -> list[str]:
    """按句号切段并合并不超 limit 的段（证据切分的唯一实现）。"""
    segments, buffer = [], ''
    for sentence in re.split(r'(?<=。)', text):
        if buffer and len(buffer) + len(sentence) > limit:
            segments.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
    if buffer.strip():
        segments.append(buffer)
    return segments


def build_evidence(chapters, hook_copy: str | None = None) -> dict:
    """源证据切分（stage2 source owner 的 harness 逻辑，F7b 从工具层收回）。

    章前文字（Stage1 `hook_copy`，即源引子）切成 `e00_*` 段并标 `pre_chapter`：
    书级调用与第 1 章抽取看得到，其余各章看不到——引子只可能影响开场。引子按
    信息职责分流（规则在 BOOK_PROMPT／CHAPTER_PROMPT），不对整篇打标签：
    开场前已成立的状态/知情进 initial_state，实际发生的开场行动进第 1 章 beats，
    与正文重复的事件以正文为准，预告/概括不进 canonical，判断不了弃权。
    """
    evidence: dict[str, dict] = {}
    if hook_copy and hook_copy.strip():
        for i, segment in enumerate(_segment_sentences(hook_copy), 1):
            evidence[f'e00_{i:02d}'] = {'chapter': 0, 'text': segment, 'pre_chapter': True}
    for chapter in chapters:
        no, text = chapter['chapter_ordinal'], chapter['text']
        for i, segment in enumerate(_segment_sentences(text), 1):
            evidence[f'e{no:02d}_{i:02d}'] = {'chapter': no, 'text': segment}
    return evidence


def _pre_chapter_evidence_ids(evidence: dict) -> frozenset[str]:
    return frozenset(k for k, v in evidence.items() if v.get('pre_chapter'))


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
    evidence = build_evidence(chapters, ctx.hook_copy)
    pre_chapter = _pre_chapter_evidence_ids(evidence)
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in chapters)
    if ctx.hook_copy.strip():
        full = f'〔章前文字〕\n{ctx.hook_copy}\n\n' + full
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
        # 章前文字（e00）只进书级调用与第 1 章：引子只可能影响开场，其余各章不变。
        chapter_evidence = '\n'.join(
            f'[{k}] {v["text"]}' for k, v in evidence.items()
            if v['chapter'] == no or (no == 1 and v.get('pre_chapter')))
        prompt = CHAPTER_PROMPT.format(
            ch=no, glossary=pack_prompt('人物闭集', characters=book['characters']),
            snapshot='\n'.join(snapshot),
            kreg='\n'.join(f'{k} -> {v}' for k, v in compiler.statements.items()),
            evidence=chapter_evidence,
            pre_chapter=PRE_CHAPTER_RULE if no == 1 else '',
            known_beats=', '.join(sorted(compiler.known_beats)))
        wire = ctx.ask(f'ch{no:02d}', prompt, lambda w: compiler.validate(w, no))
        bp, errors = compiler.compile(wire, no)
        if errors:
            raise ValueError(errors)
        compiled.append(bp)
    blueprint = assemble_blueprint(wcs, compiled)
    repository.write('stage2/blueprints/chapter_blueprints.json', blueprint)
    repository.write('stage2/compiled/input_snapshots.json', snapshots)
    _, events = source_fold(core, blueprint, pre_chapter_evidence=pre_chapter)
    repository.write('stage2/acceptance/fold.json', {'events': events})
    return {'chapters': len(compiled), 'fold_events': events}


# ─── stage2 names ────────────────────────────────────────────────────────────

def run_names(ctx: ChainContext) -> dict:
    repository = ctx.repository
    core = repository.read_model('stage2/compiled/book_extraction.json')
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in ctx.chapters)
    if ctx.hook_copy.strip():
        full = f'〔章前文字〕\n{ctx.hook_copy}\n\n' + full
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
        return assemble_preserve(w, policy_id='preserve-default', policy_version=2,
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
            char_canonicals={c.canonical: cid for cid, c in chars.characters.items()},
            registered_surfaces={cid: {s.surface for s in c.surfaces}
                                 for cid, c in chars.characters.items()})

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
    # 校验闸在节点自身（不在 ask 回调）：机械闸拦下的稿要落到本产物的
    # .INVALID 孪生上（typed witness），ask 只负责取回并耐久原响应。
    bw = ctx.ask('bind', bind_prompt, lambda w: [])
    binding, errors = assemble_binding(bw, checkpoint, positions)
    if errors:
        # Binding INVALID＋typed witness：机械闸拦下的稿进 .INVALID 孪生
        # （gates_skipped＝问题清单，wire 原样留证），正式位不写；再 fail-fast
        # 停在该 owner——不静默放行、不自动重购（I-34／Q-13）。
        repository.write_invalid(
            'stage4/grounding/semantic_binding.json',
            {'wire': bw, 'errors': list(errors)}, problems=tuple(errors))
        raise ValueError(f'bind 机械校验失败；INVALID 孪生已留证：{errors}')
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
    from novel.stages.common import check_chapter_receipt
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
    from contracts.schema.receipt import ChapterReceipt
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
    hook_sha = sha256_text(prose)
    outcome = assemble_delivery(
        chapter_prose=chapter_prose, body=access, hook_prose=prose,
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
        try:
            report.update(run_node(node_id, ctx))
        except Exception as exc:
            # owner 停止：失败归属精确到候选节点，run 级状态留痕后外抛；
            # 后续节点不再执行（不继续问模型、不放行半成品）。
            repository = ctx.repository
            status = {}
            try:
                status = repository.read('runtime/workflow_status.json')
            except FileNotFoundError:
                pass
            status.update({'status': 'failed', 'failed_node': node_id,
                           'failure_kind': type(exc).__name__, 'detail': str(exc)})
            repository.write('runtime/workflow_status.json', status)
            raise
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


# ═════════════════════════════════════════════════════════════════════════════
# 装配体：Stage1 输入、workflow／epoch、付费边界与恢复入口
# （F8c 分流自原工具层隔离驱动；与 runner 同模块，它是这条链唯一
#  把设施装到一起的地方，生产入口是 `novel.cli run <node> <sid>`）
# ═════════════════════════════════════════════════════════════════════════════

class Stage1Input(NamedTuple):
    """候选链的 Stage1 源输入：章数组 + 章前文字 + 盘上字节身份 + 组合 SHA-1。

    输入装配属装配体（薄驱动）：读正式 Stage1 章数组要经 `novel.paths` 定位书目录、
    经正式契约解码——确定性引擎段（各 stageN 包与 `novel/stages/common.py`）按纪律
    不 import 现役设施。

    `source_identity`／`root_sha1` 是 **main_text＋hook_copy 两份字节的组合摘要**
    （长度前缀防拼接歧义）：只改引子也会换源身份，旧根不得沿用旧购买（质量改进
    批次 C）。Stage6 退役后 hook_copy 的唯一消费者是 Stage2.source。
    """

    chapters: list[dict]
    hook_copy: str
    source_identity: str      # main_text＋hook_copy 盘上字节的组合 SHA-256
    root_sha1: str            # 同一组合的 SHA-1（provenance 的根身份格）


def load_stage1_chapters(base, sid: str) -> Stage1Input:
    """从正式 Stage1 章数组读候选链源输入（`--book-sid` 生产路径）。

    章数组即全链唯一章节键（阅读序 `chapter_ordinal`，从 1 连续）；目标章数等于
    源章数由 `MainText` 模型自身保证。`stage1/hook_copy.json`（章前文字／源引子）
    随本函数进入 Stage2.source 的 e00 证据段；缺失按空字节参与组合摘要。
    """
    from contracts.schema import artifact_spec, decode_artifact
    book_dir = paths.resolve_novel_dir(Path(base), sid)
    raw = (book_dir / STAGE1_MAIN_TEXT).read_bytes()
    decoded = decode_artifact(STAGE1_MAIN_TEXT, json.loads(raw.decode('utf-8-sig')))
    model = artifact_spec(STAGE1_MAIN_TEXT).model.model_validate(decoded.payload)
    chapters = [{'chapter_ordinal': section.chapter_ordinal, 'text': section.text}
                for section in model.root]
    if [c['chapter_ordinal'] for c in chapters] != list(range(1, len(chapters) + 1)):
        raise ValueError('Stage1 章数组必须为非空连续章序列表')
    hook_path = book_dir / STAGE1_HOOK_COPY
    hook_raw = hook_path.read_bytes() if hook_path.exists() else b''
    hook_copy = ''
    if hook_raw:
        hook_model = artifact_spec(STAGE1_HOOK_COPY).model.model_validate(
            decode_artifact(STAGE1_HOOK_COPY,
                            json.loads(hook_raw.decode('utf-8-sig'))).payload)
        hook_copy = hook_model.text
    return Stage1Input(chapters=chapters, hook_copy=hook_copy,
                       source_identity=prov.combined_content_sha256(raw, hook_raw),
                       root_sha1=prov.combined_content_sha1(raw, hook_raw))


def run_workflow(*, repository, chapters, ask, ask_text, ask_body, budget=11000,
                 through='stage4', provider='manual-adoption', model='unknown',
                 book_title='', hook_constraints=None, length_band=(0.6, 1.3),
                 body_chapter_limit=None, node_filter=None, hook_copy=''):
    """记录显式执行状态；失败保留旧产物供恢复，但不能仍宣称上次绿灯有效。

    流程编排已归 owner runner（本模块上半）：本函数只装配 ChainContext、驱动
    节点计划并落 run 级状态（F7b 薄驱动）。
    """
    hook_constraints = hook_constraints or HookConstraints(
        target_chars=280, hard_max_chars=360)
    ctx = ChainContext(
        repository=repository, ask=ask, ask_text=ask_text, ask_body=ask_body,
        chapters=chapters, hook_copy=hook_copy, budget=budget, book_title=book_title,
        hook_constraints=hook_constraints, length_band=length_band,
        body_chapter_limit=body_chapter_limit, provider=provider, model=model,
        digest=digest)
    status_rel = 'runtime/workflow_status.json'
    identity = digest({'chapters': chapters, 'hook_copy': hook_copy, 'budget': budget,
                       'through': through, 'band': length_band,
                       'body_limit': body_chapter_limit})
    repository.write(status_rel, {'status': 'running', 'input_sha256': identity,
                                  'accepted_body': False})
    try:
        report, final_status, accepted_body = run_chain(
            ctx, through=through, node_filter=tuple(node_filter) if node_filter else None)
    except PurchaseRequired as exc:
        repository.write(status_rel, {
            'status': 'needs_authorization', 'input_sha256': identity,
            'accepted_body': False, 'blocked_owner': exc.owner, 'detail': str(exc)})
        raise
    except Exception as exc:
        # run_chain 已按候选节点留痕（failed_node）；此处只补 run 级身份，
        # 不覆盖精确 owner 归属。
        status = {}
        try:
            status = repository.read(status_rel)
        except FileNotFoundError:
            pass
        status.update({'status': 'failed', 'input_sha256': identity,
                       'accepted_body': False,
                       'failure_kind': type(exc).__name__, 'detail': str(exc)})
        repository.write(status_rel, status)
        raise
    repository.write(status_rel, {'status': final_status, 'input_sha256': identity,
                                  'accepted_body': accepted_body, 'through': through})
    return report


def load_dev_source(payload: object) -> tuple[list[dict], str]:
    """开发路径源输入解析：必须显式给出引子，或显式声明「无引子」——不猜（C 批）。

    形状：`{"chapters":[{chapter_ordinal,text},...], "hook_copy": "引子正文"|null}`。
    裸章数组（历史形状）不再接受：那等于把「有没有引子」交给调用方默认值。
    """
    if not isinstance(payload, dict) or 'hook_copy' not in payload:
        raise ValueError(
            '--source-json 必须是 {"chapters":[{chapter_ordinal,text},...], '
            '"hook_copy": "引子正文" 或 null}：显式给出引子，或显式给 null 声明无引子')
    unknown = set(payload) - {'chapters', 'hook_copy'}
    if unknown:
        raise ValueError(f'源 JSON 携带未知字段：{sorted(unknown)}')
    chapters = payload['chapters']
    if not isinstance(chapters, list) or not chapters:
        raise ValueError('源 JSON 的 chapters 必须是非空章数组')
    return chapters, payload['hook_copy'] or ''


def execute(args) -> None:
    """装配显式依赖并驱动链执行（F8b：CLI `run <str06 节点>` 与工具共用本函数）。

    `args` 是任一来源解析出的同形命名空间（argparse Namespace 或等价对象）：
    CLI 节点执行与 orchestrator 的节点执行共用这一条装配路径，不为切换长第二套。
    """
    if not args.book_sid and args.source_json is None:
        raise ValueError('必须给出 --book-sid（正式 Stage1 输入）或 --source-json（开发路径）')
    if args.hook_target_chars > args.hook_hard_max:
        raise ValueError('--hook-target-chars 不得大于 --hook-hard-max')
    if not 0 < args.body_band_low < args.body_band_high:
        raise ValueError('--body-band-low/high 必须满足 0 < low < high')
    if args.check_only and (args.yes or args.allow_source_export):
        raise ValueError('--check-only 不签发新调用：去掉 --yes/--allow-source-export 再跑')
    for root in args.adopt_root:
        if not root.resolve().is_dir():
            raise ValueError(f'--adopt-root 不是目录：{root}')
    # F8b：`base` 显式传入（CLI 节点执行路径）；工具 CLI 仍可经 NOVEL_OUTPUT 提供。
    base = getattr(args, 'base', None) or os.environ.get('NOVEL_OUTPUT')
    if not base:
        raise ValueError('NOVEL_OUTPUT 未设置：没查、未运行')
    base_path = Path(base).resolve()
    run_dir = args.run_dir.resolve()
    if not run_dir.is_relative_to(base_path):
        raise ValueError('--run-dir 必须位于 NOVEL_OUTPUT 下的隔离根')
    # 一个 NOVEL_OUTPUT 根一个候选链：facilities 的 sid 取 run 目录名。
    sid = run_dir.relative_to(base_path).as_posix().replace('/', '_')
    repository = Str06Repository(run_dir)
    if args.book_sid:
        # 生产路径：正式 Stage1 章数组＋章前文字（F7c；C 批起含 hook_copy）。
        stage1 = load_stage1_chapters(base_path, args.book_sid)
        chapters, hook_copy, source_identity, root_sha1 = (
            stage1.chapters, stage1.hook_copy, stage1.source_identity, stage1.root_sha1)
    else:
        # 开发路径：源 JSON 必须显式给出引子，或显式声明「无引子」——不猜。
        source_bytes = args.source_json.read_bytes()
        source_identity = hashlib.sha256(source_bytes).hexdigest()
        root_sha1 = prov.content_sha1(source_bytes)
        chapters, hook_copy = load_dev_source(repository.read_external(args.source_json))
    if args.budget is None:
        args.budget = derive_total_length_budget(
            chapters, rounding=STR06_RUNTIME_CONFIG.length_budget_rounding)
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
            for field_name in ('base', 'result'):
                item = entry[field_name]
                actual = repository.file_sha256(Path(item['path']))
                if actual != item['sha256']:
                    raise ValueError(f'owner patch {entry["owner"]} 的 {field_name} 身份变化，拒绝采用')
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

    print(run_workflow(repository=repository, chapters=chapters, hook_copy=hook_copy,
                       ask=ask, ask_text=ask_text,
                       ask_body=ask_body, budget=args.budget, through=args.through,
                       node_filter=tuple(args.node) or None,
                       provider='manual-adoption' if args.wire_dir else cfg.base_url,
                       model='unknown' if args.wire_dir else cfg.model,
                       book_title=args.book_title,
                       hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                         hard_max_chars=args.hook_hard_max),
                       length_band=(args.body_band_low, args.body_band_high),
                       body_chapter_limit=args.body_chapter_limit))
