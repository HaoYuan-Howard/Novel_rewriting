#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain —— STR-06 新链的节点编排与运行装配体（F7b owner runner；F8c 分流）。

【为什么拆】原 harness 把「读输入 → LLM → compile → 落盘 → 状态」缠在一个长函数
里，单节点无法重跑、输入无法从盘面闭合。本模块按 `contracts/invariants.py::NODES`
把流程拆成 owner runner：每个 runner 只做本节点的编排——从 repository 读
本节点的正式输入、调 owner 引擎、经付费边界取已购响应、写本节点产物
（provenance 随写随铸）。harness 逻辑（证据切分、身份投影、状态记录）随之归
owner；`execute` 只剩参数、设施装配与计划推进（薄驱动）。

【与节点表的关系】`NODE_ORDER` 是执行序；`run_node` 按 id 分派。双向对账在
`tests/test_str06_candidates.py`：表里每个 paid/free 节点都有 runner，每个 runner
都落在表内节点上，produces 与唯一 writer 一致。复检走 `--check-only`（免费采用与
重放），不新造 check 节点。

【B2 五节点重建（2026-09-27，D9）】Stage2 由联合抽取 `str06.stage2.source`
改为 book／chapters／names／associate／compile 五 owner：book 与 chapters
产出观察（`stage2/observations/*.json`），names 重挂到书级观察输入，
associate 与 compile 的 runner 由 B2 后续提交落地（compile 是 Characters＋
Blueprint 双真源唯一 writer）。`run_source` 与派生输入
`stage2/compiled/book_extraction.json` 随之退役（D3/D9）。

【输入闭合边界（F7b 已知限制，F7c 收口）】源章仍由调用方经 `ChainContext.chapters`
传入（F7c 起改读正式 Stage1 章数组）；其余输入一律从盘面读——单节点重放因此可能。
Stage4 的 BodyDesign 落为正式派生产物（`stage4/design/body_design.json`），
不再是只活在内存里的中间态。

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Final, Mapping, NamedTuple

from contracts.artifacts import (
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT, str06_chapter_observation_rel,
)
from contracts.invariants import (
    BOOK_CONTENT_CALL_CEILING_DEFAULT,
    LLM_MAX_OUTPUT_TOKENS, PIPELINE_BOOK_CONCURRENCY,
    PIPELINE_DEFAULT_CONCURRENCY, PIPELINE_PARALLEL_NODES,
    PIPELINE_PROVIDER_CONCURRENCY,
)
from contracts.schema.grounding import OutputConstraints, PreserveCheckpoint
from contracts.schema.hook import HookConstraints, HookReceipt
from contracts.schema.observation import STAGE2_OBSERVATION_PROTOCOL
from contracts.schema.workflow import (
    OWNER_REVISION_MAX_ROUNDS, STR06_RUNTIME_CONFIG, OwnerIssueV1, STAGE4_PRESERVE_POLICY_VERSION,
    route_owner_issue,
)
from novel.config import Config, load_config
from novel.llm.paid_requests import (
    ExecutionError, PaidRequestSession, PurchaseRequired, AdoptionRef, digest,
    ask_json, ask_text_once, init_or_resume_candidate_workflow, purchased_baseline,
    semantic_owner_unit,
    ValidationStop,
)
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel.llm.budget import count_text_tokens
from novel import paths, prov
from novel.core.text import count_non_whitespace_chars
from novel.model_slots import pipeline_model_slot
from novel.stages.common import (
    NAMES_RULES, PRESERVE_RULES, DESIGN_RULES, design_rules, expand_surface_releases,
    HOOK_DESIGN_RULES, HOOK_WRITER_SYSTEM, NAMING_RULES, SYS,
    BODY_WRITER_SYSTEM, ChapterIdentities, chapter_identities, pack_prompt,
    packet_metrics, project_chapter_packet, render_chapter_packet,
    validate_hook_materials, validate_writer_materials,
    hook_material_issues, writer_material_issues, hook_name_projections,
)
from novel.stages.stage2 import (
    NamesObservation, STAGE2_OBSERVATION_PROTOCOL, book_observation_prompt,
    chapter_observation_prompt, chapter_patch_locate, character_core_view,
    compile_associate_observation,
    compile_book_observation, compile_chapter_observation, compile_name_structure,
    compile_source_truth, associate_observation_prompt, project_association_space,
    first_appearance_anchors, first_appearance_issues, NAMES_INPUT_VERSION,
    names_evidence_view, normalize_name_observations, validate_name_resolutions,
)
from novel.stages.owner_revision import (
    MechanicalDefectStop, OwnerRevisionRequired, OwnerRevisionStop,
    discover_owner_issues, open_issue_record, owner_product_digest,
    resolve_owner_issues, complete_owner_dispatch, revision_gate, revision_prompt_for_owner,
)
from novel.stages.stage4 import (
    assemble_hook_design,
    assemble_preserve, compile_execution_plan, compile_hook_context,
    compile_writer_book_ir, design_obligation_index,
    knowledge_change_index, lock_order_violations, obligation_core_id,
    project_brief, project_design_input, project_preserve_view, preserve_revision_wire, publish_graph,
)
from contracts.schema.observation import STAGE2_NAMES_PROTOCOL
from contracts.schema.orchestration import LegacyLlmProfile
from novel.stages.stage4 import (
    inspect_body_materials, inspect_design_candidate, locate_design_patches,
    plan_material_repairs,
)
from novel.core.validation import ValidationMessage
from novel.stages.stage5 import (
    AnsweredText, HOOK_VALIDATOR_VERSION, MAX_REWRITES, MECHANICAL_CHECKS,
    build_hook_user_prompt,
    build_rewrite_user_prompt, context_sha256,
    sha256_text, validate_hook_prose, validate_chapter_prose,
    write_chapter,
)
from novel.stages.stage7 import (
    apply_names, assemble_delivery, build_delivery_manifest, chapter_access,
    check_residual, compile_name_map, compile_surface_replacements,
    naming_projection, verify_chapter_receipts,
)
from novel.store import PublishItem, Str06Repository
from novel.work_units import read_book_budget, require_workflow

__all__ = [
    'ChainContext', 'ExecutionProfile', 'NODE_ORDER', 'NODE_RUNNERS',
    'Stage1Input', 'THROUGH_NODE',
    'build_evidence', 'derive_total_length_budget', 'execute', 'load_dev_source',
    'load_stage1_chapters', 'resolve_body_chapters', 'run_chain', 'run_node',
    'run_workflow',
    'PaidBoundary', 'assemble_paid_boundary', 'str06_chat_request',
]

#: 执行序（stage2 → stage4 → stage5 → stage7；与 --through 的腿边界一致）。
#: B2 五节点重建（D9）：Stage2 的 associate／compile runner 由 B2 后续提交
#: 落地；届时本序与 NODE_RUNNERS 双向闭合。
NODE_ORDER: tuple[str, ...] = (
    'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
    'str06.stage2.associate', 'str06.stage2.compile',
    'str06.stage4.preserve', 'str06.stage4.body_design',
    'str06.stage4.hook_design', 'str06.stage4.compile',
    'str06.stage5.hook', 'str06.stage5.body',
    'str06.stage7.naming', 'str06.stage7.assemble',
)

#: --through 腿 → 跑到哪个节点为止（含）。
THROUGH_NODE = {'stage2': 'str06.stage2.compile', 'stage4': 'str06.stage4.compile',
                'stage5': 'str06.stage5.body', 'stage7': 'str06.stage7.assemble'}

FINAL_STATUS = {'stage2': 'stage2_compiled', 'stage4': 'planned_validated',
                'stage5': 'body_hook_accepted_mechanical', 'stage7': 'delivered'}

#: 逐章失败留证（W2/D3）：「最近一次完成执行」语义——正常完成写空清单、带失败
#: 完成在上抛之前写失败子集；崩溃不写，文件缺失＝该节点还没有一次完成执行。
#: 章观察（`str06.stage2.chapters`）与正文（`str06.stage5.body`）共用一个形状
#: （I-11）：`{requested, attempted, chapters, recorded_at}`。
BODY_FAILURES_REL = 'runtime/body_failures.json'
CHAPTER_OBSERVATION_FAILURES_REL = 'runtime/chapter_observation_failures.json'


@dataclass
class ChainContext:
    """一次链执行的显式依赖：repository、付费边界回调与运行参数。

    B1：付费回调返回**耐久响应引用**——`ask` 返回 ``(data, ref)``，`ask_text`
    返回 ``(text, errors, ref)``，`ask_body` 返回带 ``ref`` 的 `AnsweredText`；
    runner 把本次写盘实际依据的引用显式传给 `repository.write(refs=...)`，
    provenance 与 CommitBundle 由引用纯函数构造（无进程内共享采用窗口）。
    """

    repository: Any
    ask: Callable[[str, str, Callable], tuple[Any, Any]]
    ask_text: Callable[[str, str], tuple[str | None, list[str], Any]]
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
    body_replay: Callable[[int, Any], bool] = lambda _ordinal, _packet: False
    after_node_commit: Callable[[str], None] = lambda _node: None
    #: 正文目标章集合（W1/D2 调用层选章）：None＝全书；非空 tuple＝恰好这些章。
    #: 只作调用元数据——记入 `runtime/workflow_status.json` 的独立字段供审计；
    #: 不进 run 身份摘要、请求身份、章回执三把摘要、provenance、CommitBundle
    #: 或契约身份（`NodeSpec.flags` 不变）。从 canary 续跑到全书时，已提交章
    #: 的采用与恢复身份不变（集合恢复按目标集合补缺）。
    body_chapters: tuple[int, ...] | None = None
    provider: str = 'manual-adoption'
    model: str = 'unknown'
    #: canonical JSON 摘要（请求身份）；由调用方从付费边界注入——chain 层不 import
    #: llm 设施（`novel/stages/common.py` 与各 stageN 包的纪律对本模块放宽：
    #: 它是装配体，但引擎段仍不得反向依赖这里）。
    digest: Callable[[object], str] | None = None
    #: 没单独配置的可并发节点使用这个回落值。正式执行里它是运行默认，
    #: 各节点自己的数在 ``node_concurrency``。并发度不进语义身份（`R-12.1c`）。
    concurrency: int = PIPELINE_DEFAULT_CONCURRENCY
    #: 书级与供应商总槽容量。默认是产品常量；正式执行传入本次冻结的
    #: ``PipelineConcurrency``，与付费客户端占同一组槽，避免内外层容量不一致。
    slot_per_book: int = PIPELINE_BOOK_CONCURRENCY
    slot_per_provider: int = PIPELINE_PROVIDER_CONCURRENCY
    #: 可并发节点各自的冻结并发。缺键时回落到 ``concurrency``。
    node_concurrency: dict[str, int] = field(default_factory=dict)

    def pool_workers(self, node: str, item_count: int) -> int:
        """该节点本次最多同时处理多少项；不超过待处理项数。"""
        configured = self.node_concurrency.get(node, self.concurrency)
        return max(1, min(configured, item_count))


def _segment_paragraphs(text: str) -> list[str]:
    """一个非空自然段一段（证据切分的唯一实现；RC-26，2026-10-04）。

    段文本＝原文一行去掉首尾空白（含全角缩进）后的连续片段。旧实现只在
    「。」后切：对白收尾「」」落到下一段开头、问号／叹号收尾的连续自然段
    并成一段，渲染时段号悬在上一段文字下方——模型系统性地把引文挂到偏大
    的段号上（三书停书根因）。自然段是模型肉眼可见的单位，段号与文字
    一一对应；收尾引号天然留在本句所在段。
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_evidence(chapters, hook_copy: str | None = None) -> dict:
    """源证据切分（book／chapters 观察共用的 harness 逻辑，F7b 从工具层收回）。

    章前文字（Stage1 `hook_copy`，即源引子）切成 `e00_*` 段并标 `pre_chapter`：
    书级调用与第 1 章观察看得到，其余各章看不到——引子只可能影响开场。引子按
    信息职责分流（规则在 BOOK_PROMPT／CHAPTER_PROMPT），不对整篇打标签：
    开场前已成立的状态/知情进 initial_state，实际发生的开场行动进第 1 章 beats，
    与正文重复的事件以正文为准，预告/概括不进 canonical，判断不了弃权。
    """
    evidence: dict[str, dict] = {}
    if hook_copy and hook_copy.strip():
        for i, segment in enumerate(_segment_paragraphs(hook_copy), 1):
            evidence[f'e00_{i:02d}'] = {'chapter': 0, 'text': segment, 'pre_chapter': True}
    for chapter in chapters:
        no, text = chapter['chapter_ordinal'], chapter['text']
        for i, segment in enumerate(_segment_paragraphs(text), 1):
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
    total = sum(count_non_whitespace_chars(c['text']) for c in chapters)
    if total <= 0:
        raise ValueError('源章数组为空，无法派生书级篇幅')
    return -(-total // rounding) * rounding


# ─── stage2 book／chapters（B2 五节点：观察 owner，D3/D9） ──────────────────

def run_book(ctx: ChainContext) -> dict:
    """`str06.stage2.book`：书级一次调用——人物候选、称谓及证据、初态、世界约束。

    输入是全书原文（含 e00 引子句段）与证据句段清单；输出观察 wire 落
    `stage2/observations/book.json`，作为 Characters 与 Blueprint 两份真源的
    素材（canonical 由 compile 发布，D9）。机械闸（闭集、锚点、canonical
    闭合）在 owner 引擎里，失败即停在该 owner（免费修复，不重购）。
    """
    repository = ctx.repository
    evidence = build_evidence(ctx.chapters, ctx.hook_copy)
    prompt = revision_prompt_for_owner(
        repository, 'book',
        book_observation_prompt(chapters=ctx.chapters, hook_copy=ctx.hook_copy,
                                evidence=evidence),
        'stage2/observations/book.json')
    wire, book_ref = ctx.ask(
        'book', prompt,
        lambda w: compile_book_observation(w, evidence=evidence)[1])
    observation, errors = compile_book_observation(wire, evidence=evidence)
    if errors:
        raise ValueError(errors)
    repository.write('stage2/observations/book.json', observation,
                     owners=('book',), refs=(book_ref,))
    return {'characters': len(observation.characters),
            'initial_state': len(observation.initial_state),
            'world_constraints': len(observation.world_constraints)}


def _chapter_evidence(evidence: dict, chapter: int) -> dict:
    """第 k 章的证据句段闭集：本章句段；第 1 章另见章前文字（e00）。"""
    return {k: v for k, v in evidence.items()
            if v['chapter'] == chapter or (chapter == 1 and v.get('pre_chapter'))}


def chapter_acceptance(wire, *, chapter: int, evidence_ids, character_slugs, anchors):
    """生产章观察与只读预检共用接受闸；首现锚点由书级 core 派生。"""
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence_ids, chapter=chapter, character_slugs=character_slugs)
    if observation is not None:
        errors.extend(first_appearance_issues(anchors, chapter=chapter, beats=observation.beats))
    return observation, errors


def _write_chapter_failures(repository, rel: str, *, requested: tuple[int, ...],
                            attempted: tuple[int, ...],
                            failures: dict[int, BaseException]) -> None:
    """逐章失败留证（D3/W2）：「最近一次完成执行」的逐章失败清单。

    形状 `{requested, attempted, chapters, recorded_at}`：`requested` 是本次
    请求的章范围（章观察＝全部源章；正文＝`body_chapters` 或全书）；`attempted`
    是本次实际进入逐章执行器的章（正文不含已提交章）；`chapters` 是
    `attempted` 的失败子集。写盘走 repository 的原子替换；崩溃不写，文件
    缺失因此表示「这个节点还没有一次完成执行」（I-42）。
    """
    repository.write(rel, {
        'requested': list(requested),
        'attempted': list(attempted),
        'chapters': {str(ordinal): {'failure_kind': type(exc).__name__,
                                    'detail': str(exc)}
                     for ordinal, exc in sorted(failures.items())},
        'recorded_at': int(time.time())})


def run_chapters(ctx: ChainContext) -> dict:
    """`str06.stage2.chapters`：逐章并行观察——只含章内局部句柄（D3）。

    输入是本章句段、第 1..k-1 章原文、书级人物表（盘上 book 观察）与抽取
    协议；**第 k 章请求不依赖任何前章产物**（没有快照、登记表与 beat 表），
    前章编译或关联失败不改变后章请求。各章与 `run_body` 同一并发纪律：经
    `novel/model_slots.py` 的书级＋供应商总槽在线程池并发执行（并发度 run
    启动时冻结、不进语义身份，`R-12.1c`）。单章失败隔离（I-31）：失败定位
    到该章 item、其余章照常执行，已落盘的章观察保持有效；全部章收敛后按
    第一章序的原始失败上抛（授权／账务故障保持原始类型），逐章失败清单按
    「最近一次完成执行」留证 `runtime/chapter_observation_failures.json`
    （D3/W2：正常完成写空清单，文件缺失＝本节点没有完成执行），恢复按请求
    身份重放、不重购已存结果（I-30/I-31）。
    """
    repository = ctx.repository
    book = repository.read_model('stage2/observations/book.json')
    evidence = build_evidence(ctx.chapters, ctx.hook_copy)
    core, core_errors = character_core_view(book, evidence=evidence)
    if core_errors:
        raise MechanicalDefectStop(f'书级称谓锚点无效：{core_errors}',
                                  node='str06.stage2.chapters', issues=tuple(core_errors))
    anchors = first_appearance_anchors(core)
    failures: dict[int, BaseException] = {}
    observed = 0

    def _one(no: int) -> None:
        # 书级与供应商总槽（`novel/model_slots.py`）：等待期取消或配置拒绝
        # 都表示尚未开始供应商调用；槽位等待不进请求身份（`R-12.1c`）。
        with pipeline_model_slot(repository.root, ctx.provider,
                                 per_book=ctx.slot_per_book,
                                 per_provider=ctx.slot_per_provider):
            chapter_evidence = _chapter_evidence(evidence, no)
            prompt = revision_prompt_for_owner(
                repository, f'ch{no:02d}',
                chapter_observation_prompt(chapter=no, book=book,
                                           chapters=ctx.chapters,
                                           evidence=chapter_evidence),
                str06_chapter_observation_rel(no))
            def validate(w):
                # R10a a1：传完整段映射（含 text），basis 引文可逐字判定
                return chapter_acceptance(w, evidence_ids=chapter_evidence, chapter=no,
                                          character_slugs=frozenset(book.characters), anchors=anchors)

            def locate(w, errs, *, _ev=chapter_evidence, _no=no,
                       _slugs=frozenset(book.characters)):
                # CS-patch（2026-10-03）：条目级返修的定位协议——与章观察验收
                # 同源（闭集／basis／人物闭集／句柄纪律），其它闸在拦时自动退
                # 整份返修（返回 None）。
                return chapter_patch_locate(w, errs, evidence_ids=_ev, chapter=_no,
                                            character_slugs=_slugs)

            wire, ch_ref = ctx.ask(f'ch{no:02d}', prompt, lambda w: validate(w)[1],
                                   patch=locate)
            observation, errors = validate(wire)
            if errors:
                # 缺陷定位到章 item（I-31）：不污染其余章的合法结果。
                raise MechanicalDefectStop(f'第{no}章观察校验失败：{errors}',
                                          node='str06.stage2.chapters', item=f'chapter:{no}',
                                          issues=tuple(errors))
            repository.write(str06_chapter_observation_rel(no), observation,
                             owners=(f'ch{no:02d}',), refs=(ch_ref,))

    ordinals = [chapter['chapter_ordinal'] for chapter in ctx.chapters]
    workers = ctx.pool_workers('str06.stage2.chapters', len(ordinals))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, no): no for no in ordinals}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # 单章失败就地定位（I-31），其余章继续
                failures[futures[future]] = exc
                continue
            observed += 1
    # 「最近一次完成执行」逐章留证（D3/W2）：正常完成写空清单；带失败完成在
    # 上抛之前写失败子集。章观察无集合恢复，requested＝attempted＝全部源章。
    _write_chapter_failures(repository, CHAPTER_OBSERVATION_FAILURES_REL,
                            requested=tuple(ordinals), attempted=tuple(ordinals),
                            failures=failures)
    if failures:
        # 其余章的合法观察已在盘上（I-31）；按第一章序的原始失败上抛——
        # PurchaseRequired／ExecutionError 保持原类型，run 级状态据此闭合。
        raise failures[min(failures)]
    return {'chapters': observed}


# ─── stage2 names ────────────────────────────────────────────────────────────

def run_names(ctx: ChainContext) -> dict:
    """`str06.stage2.names`：源名称结构观察（引擎不变，D9 只重挂输入）。

    输入改挂书级观察（`stage2/observations/book.json` 的人物表投影）＋完整
    MainText 与 hook_copy 的逐段证据；不再把同一正文另发一遍。名称结构 wire
    落 `stage2/observations/names.json`。
    Characters 的合并发布归 compile（S2-5 唯一 writer），本节点不再写
    characters.json。
    """
    repository = ctx.repository
    book = repository.read_model('stage2/observations/book.json')
    evidence = build_evidence(ctx.chapters, ctx.hook_copy)
    core, errors = character_core_view(book, evidence=evidence)
    if errors:
        raise ValueError(errors)
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in ctx.chapters)
    if ctx.hook_copy.strip():
        full = f'〔章前文字〕\n{ctx.hook_copy}\n\n' + full
    names_prompt = pack_prompt(
        NAMES_RULES, input_version=NAMES_INPUT_VERSION,
        characters=core.model_dump(mode='json'), evidence=names_evidence_view(evidence))
    names_wire, names_ref = ctx.ask(
        'names', names_prompt, lambda w: compile_name_structure(w, core, full, evidence=evidence)[1])
    structure, errors = compile_name_structure(names_wire, core, full, evidence=evidence)
    if errors:
        raise ValueError(errors)
    observation = NamesObservation(protocol_version=STAGE2_NAMES_PROTOCOL,
                                   structure=structure)
    repository.write('stage2/observations/names.json', observation,
                     owners=('names',), refs=(names_ref,))
    return {'name_structure_people': len(structure.people)}


# ─── stage2 associate／compile（B2 五节点：关联与双真源发布，D8/D9） ─────────

def run_associate(ctx: ChainContext) -> dict:
    """`str06.stage2.associate`：跨章关联——只选已给句柄建立同一性与引用（D8）。

    输入是 compiler 从 book 与 chapters 观察投影的句柄闭集及最小文本；输出观察
    wire 落 `stage2/observations/associate.json`。缺项、歧义、冲突保持未决——
    **RC-18～20**：请求前复验章内生命周期并按来源回派；验收时compile干跑
    只把有稳定身份且归属associate的诊断交同节点修订。最终未决拒绝发布保留
    （崩溃窗口／单节点重跑／owner patch 后的防御）。跨节点回派的 open 记录
    在案时，修订要求并入 prompt（原请求＋上一版＋typed 问题）。
    """
    repository = ctx.repository
    book = repository.read_model('stage2/observations/book.json')
    chapters = _read_chapter_observations(repository, ctx.chapters)
    _check_chapter_loops(repository, chapters, raised_by='str06.stage2.associate')
    # 名称同一性是关联的正式输入；实际语义文本归一改变请求身份。
    names = repository.read_model('stage2/observations/names.json')
    evidence = build_evidence(ctx.chapters, ctx.hook_copy)
    core, errors = character_core_view(book, evidence=evidence)
    if not errors:
        errors.extend(validate_name_resolutions(names.structure, core, evidence=evidence))
    errors.extend(f'名称同一性未决：{r.literal}；{r.reason}' for r in names.structure.resolutions
                  if r.status == 'unresolved')
    if errors:
        raise MechanicalDefectStop('名称同一性未闭合，不购买下游判断',
                                   node='str06.stage2.associate', issues=tuple(errors))
    try:
        book, chapters = normalize_name_observations(book, chapters, names.structure)
    except ValueError as exc:
        raise MechanicalDefectStop('名称局部归一未闭合，不购买下游判断',
                                   node='str06.stage2.associate', issues=(str(exc),)) from exc
    space = project_association_space(book, chapters)
    prompt = revision_prompt_for_owner(
        repository, 'associate', associate_observation_prompt(space),
        'stage2/observations/associate.json')

    def _validate(w):
        observation, errors = compile_associate_observation(w, space)
        if errors:
            return errors
        outcome = compile_source_truth(book=book, chapters=chapters, names=names,
                                       associate=observation, evidence=evidence,
                                       source_texts=tuple(c['text'] for c in ctx.chapters))
        # 干跑发现位置不授予修复权；代码缺陷/无归属诊断禁止购买关联自修。
        from novel.core.validation import ValidationIssue
        if outcome.errors or any(not isinstance(issue, ValidationIssue)
                                 or issue.owner != 'associate' for issue in outcome.undecided):
            raise MechanicalDefectStop('关联干跑存在非本单位可修问题',
                                       node='str06.stage2.associate',
                                       issues=tuple(outcome.errors + outcome.undecided))
        return outcome.undecided

    wire, associate_ref = ctx.ask('associate', prompt, _validate)
    observation, errors = compile_associate_observation(wire, space)
    if errors:
        raise ValueError(errors)
    repository.write('stage2/observations/associate.json', observation,
                     owners=('associate',), refs=(associate_ref,))
    return {'same_proposition': len(observation.same_proposition),
            'same_state_dimension': len(observation.same_state_dimension),
            'loop_pairing': len(observation.loop_pairing),
            'cross_chapter_cause': len(observation.cross_chapter_cause),
            'scene_continuation': len(observation.scene_continuation)}


def _read_chapter_observations(
        repository, chapters: list[dict]) -> dict[int, Any]:
    """按章序读全部章观察（单节点输入从盘面闭合，不靠内存传递）。"""
    return {chapter['chapter_ordinal']:
            repository.read_model(str06_chapter_observation_rel(
                chapter['chapter_ordinal']))
            for chapter in chapters}


def _check_chapter_loops(repository, chapters, *, raised_by: str) -> None:
    """薄编排消费同源只读判据；修复权按正式章scope回派，成功复验才清账。"""
    from novel.stages.stage2 import chapter_loop_issues
    first = None
    for chapter, observation in sorted(chapters.items()):
        owner = f'ch{chapter:02d}'
        issues = chapter_loop_issues(observation.model_dump(mode='json')['beats'],
                                    chapter=chapter)
        if not issues:
            record = open_issue_record(repository, owner)
            if (record is not None and record.raised_by == raised_by
                    and all(i.code == 'chapter_loop_conflict' for i in record.issues)):
                resolve_owner_issues(repository, owner=owner, raised_by=raised_by)
            continue
        action, node = route_owner_issue('chapter_loop_conflict')
        assert action == 'revise_upstream'
        try:
            discover_owner_issues(repository, raised_by=raised_by, owner=owner, node=node,
                                  issues=[OwnerIssueV1(
                                      code=i.code, item=i.scope, message=str(i),
                                      context={**i.context, 'validation_identity': i.identity})
                                      for i in issues])
        except OwnerRevisionRequired as required:
            if first is None:
                first = required
    if first is not None:
        raise first


def run_compile(ctx: ChainContext) -> dict:
    """`str06.stage2.compile`：解析证据、生成 ID、执行关联，发布双真源（D9）。

    Characters 与 Blueprint 两份真源的**唯一 writer**（S2-5/I-25）：四份观察 +
    证据句段 → 同一结果一次成形后落盘（来源账边记录四份观察）；fold 读数随发
    （`R-2.17` 度量不是门）。章内生命周期先按chapter scope回派；关联选择未决
    按`association_undecided`回派associate（RC-19/20），写原有回派账
    并抛 `OwnerRevisionRequired`（无进展／额度耗尽按 item 停）；复验通过时
    回写 resolved。不写半份 canonical。
    """
    repository = ctx.repository
    book = repository.read_model('stage2/observations/book.json')
    chapters = _read_chapter_observations(repository, ctx.chapters)
    _check_chapter_loops(repository, chapters, raised_by='str06.stage2.compile')
    names = repository.read_model('stage2/observations/names.json')
    associate = repository.read_model('stage2/observations/associate.json')
    evidence = build_evidence(ctx.chapters, ctx.hook_copy)
    outcome = compile_source_truth(book=book, chapters=chapters, names=names,
                                   associate=associate, evidence=evidence,
                                   source_texts=tuple(c['text'] for c in ctx.chapters))
    from novel.core.validation import ValidationIssue
    if outcome.errors or any(not isinstance(i, ValidationIssue) or i.owner != 'associate'
                             for i in outcome.undecided):
        raise MechanicalDefectStop('compile存在不可交给关联单位的缺陷',
                                   node='str06.stage2.compile',
                                   issues=tuple(outcome.errors + outcome.undecided))
    if outcome.undecided:
        discover_owner_issues(
            repository, raised_by='str06.stage2.compile', owner='associate',
            node='str06.stage2.associate',
            issues=[OwnerIssueV1(code='association_undecided', item=message.scope,
                                 message=str(message), context={
                                     'rule_code': message.code, 'location': message.location,
                                     'validation_identity': message.identity})
                    for message in outcome.undecided])
    assert outcome.characters is not None and outcome.blueprint is not None
    # R6 输出组发布：双真源＋fold 读数同一组、一个 seal（免费产物同样封存）。
    repository.publish((
        PublishItem(rel='stage2/characters/characters.json',
                    value=outcome.characters, owners=('compile',)),
        PublishItem(rel='stage2/blueprints/chapter_blueprints.json',
                    value=outcome.blueprint, owners=('compile',)),
        PublishItem(rel='stage2/acceptance/fold.json', value=outcome.fold,
                    owners=('compile',)),
    ))
    resolve_owner_issues(repository, owner='associate',
                         raised_by='str06.stage2.compile')
    return {'characters': len(outcome.characters.characters),
            'initial_state': len(outcome.characters.initial_state),
            'chapters': len(outcome.blueprint.chapters),
            'undecided': 0}


# ─── stage4 preserve ─────────────────────────────────────────────────────────

def _conflict_resolution_notes(repository, chapters) -> str:
    """Preserve 重跑时的确定性「冲突处理结果」块（R10a a3，§二.2）。

    对每个持有 open `source_evidence_conflict` 记录的 Stage2 owner：对比
    记录的 against 摘要与该 owner 正式产物的**当前**摘要——变了＝「已修订
    （重编译后的材料已变）」，没变＝「维持原解释」。纯机械对比（digest），
    不判语义；块进 Preserve prompt（材料变化 ⇒ 新请求身份，不会缓存命中
    上一轮带冲突的回答——同身份缓存重放会让重问拿回同一份冲突 wire）。
    """
    from novel.stages.owner_revision import open_issue_record
    lines: list[str] = []
    owners = ['book'] + [f'ch{c["chapter_ordinal"]:02d}' for c in chapters]
    for owner in owners:
        record = open_issue_record(repository, owner)
        if record is None or not any(
                i.code == 'source_evidence_conflict' for i in record.issues):
            continue
        current = owner_product_digest(repository, owner)
        verdict = ('已修订（该 owner 的观察在回派后变化，重编译后的投影视图'
                   '随之更新）' if current != record.against_sha256
                   else '维持原解释（该 owner 重看后未改观察；若你仍认为证据'
                        '矛盾，再次如实报告）')
        items = [i.item for i in record.issues
                 if i.code == 'source_evidence_conflict']
        lines.append(f'- {owner}（涉及 {items}）：{verdict}')
    if not lines:
        return ''
    return ('\n【冲突处理结果（上一轮 evidence_conflicts 的机械对比；本轮'
            '投影视图已是处理后的版本）】\n' + '\n'.join(lines) + '\n')


def _resolve_handled_conflicts(repository, chapters) -> None:
    """本轮 Preserve 未再报冲突且 checkpoint 发布：按 owner 清账（§二.2——
    处理完成不重置额度，轮数与指纹留账不复位）。"""
    from novel.stages.owner_revision import open_issue_record, resolve_owner_issues
    for owner in ['book'] + [f'ch{c["chapter_ordinal"]:02d}' for c in chapters]:
        record = open_issue_record(repository, owner)
        if record is not None and any(
                i.code == 'source_evidence_conflict' for i in record.issues):
            resolve_owner_issues(repository, owner=owner,
                                 raised_by='str06.stage4.preserve')


def _backfill_source_evidence_conflicts(repository, conflicts) -> None:
    """Preserve 报告的源证据冲突 → 按锚点句柄回派 Stage2 语义 owner（R10a a3）。

    §二.2 三分支的工程入口：拍内句柄（bNN_MM.*）回逐章观察（owner=chNN、
    item=chapter:N），书级句柄（s0_*/wc_*）回书级观察；句柄不可解析即停
    （机械路由不猜）。冲突条目已过 assemble 的形状与闭集校验；回答由 ask
    路径照常耐久——checkpoint 不发布。同指纹重现走 discover_owner_issues
    的无进展停（「已维持」原子再报同一冲突不二次回派，耗尽即停）。
    """
    grouped: dict[tuple[str, str, str], list[OwnerIssueV1]] = {}
    for conflict in conflicts:
        anchor = str(conflict.get('anchor') or '')
        action, target = route_owner_issue('source_evidence_conflict',
                                           conflict_anchor=anchor)
        if action != 'revise_upstream':
            raise ValueError(f'证据冲突锚点 {anchor!r} 无法路由（{target}）')
        if target == _CONFLICT_CHAPTER:
            chapter_no = int(anchor[1:3])
            owner, item = f'ch{chapter_no:02d}', f'chapter:{chapter_no}'
        else:
            owner, item = _REVISION_TARGET_OWNERS[target], anchor
        grouped.setdefault((owner, target, item), []).append(OwnerIssueV1(
            code='source_evidence_conflict', item=item,
            message=(f'Preserve 报告源证据冲突：锚点 {anchor}（句段 '
                     f'{conflict.get("segment")}）与原子内容矛盾——'
                     f'{conflict.get("note")}。请对照原句修订该观察，或维持原'
                     '解释并在观察中补足依据；源解释归 Stage2 owner，Preserve '
                     '不私建事实版本'),
            context={'anchor': anchor, 'segment': conflict.get('segment'),
                     'note': conflict.get('note')}))
    for (owner, target, item), issues in sorted(grouped.items()):
        discover_owner_issues(repository, raised_by='str06.stage4.preserve',
                              owner=owner, node=target, issues=issues)


def run_preserve(ctx: ChainContext) -> dict:
    repository = ctx.repository
    # Stage4 只读 canonical（S2-5/D9）：人物 core 由 Characters 投影，不读
    # 观察与派生输入（`stage2/compiled/book_extraction.json` 已退役）。
    chars = repository.read_model('stage2/characters/characters.json')
    core = chars.core_view()
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    # R10a a2：源证据投影视图（确定性渲染＋逐字引文＋证据窗口）取代整份
    # Blueprint JSON 倾倒（R10-0.3 实证 29.9k token/轮）；视图是文本块，
    # 人物表保持 JSON——pack_prompt 只装 JSON，视图直拼。
    # R10a a3：盘上有未闭合的源证据冲突时附确定性「冲突处理结果」（§二.2）——
    # 材料变化换请求身份，防止同身份缓存命中重放上一轮带冲突的回答。
    preserve_prompt = revision_prompt_for_owner(
        repository, 'preserve',
        PRESERVE_RULES + _conflict_resolution_notes(repository, ctx.chapters)
        + '\n输入（源证据投影视图，句柄即 anchors 闭集）：\n'
        + project_preserve_view(core, blueprint)
        + ('\nStage2已留证的局部名称同一性（语义文本已归一，原句保持原字面；'
           '不得因该字面差异改写命题或重复报源观察冲突）：'
           + json.dumps([r.model_dump(mode='json') for r in chars.name_structure.resolutions],
                        ensure_ascii=False, sort_keys=True)
           if chars.name_structure and chars.name_structure.resolutions else '')
        + '\n人物表：' + json.dumps(core.model_dump(mode='json', include={'characters'}),
                                    ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')),
        'runtime/preserve_checkpoint.json',
        previous_wire=lambda payload: preserve_revision_wire(
            PreserveCheckpoint.model_validate(payload)))

    def preserve_compile(w):
        return assemble_preserve(w, policy_id='preserve-default', policy_version=STAGE4_PRESERVE_POLICY_VERSION,
                                 characters=core, blueprint=blueprint,
                                 provider=ctx.provider, model=ctx.model,
                                 request_sha256=ctx.digest(preserve_prompt))

    pw, preserve_ref = ctx.ask('preserve', preserve_prompt,
                               lambda w: preserve_compile(w)[1])
    checkpoint, errors = preserve_compile(pw)
    if errors:
        raise ValueError(errors)
    conflicts = pw.get('evidence_conflicts') or []
    if conflicts:
        # R10a a3（§二.2 三分支）：冲突非空——回答已由 ask 路径耐久保存，
        # checkpoint 不发布；按锚点句柄回派 Stage2 语义 owner。同指纹重现
        # （Stage2 维持原解释后 Preserve 再报同一冲突）由 discover_owner_issues
        # 的无进展闸停，不二次回派。
        _backfill_source_evidence_conflicts(repository, conflicts)
        raise AssertionError('discover_owner_issues 必然抛错，不可达')
    brief, identities = project_brief(checkpoint, core, blueprint)
    # R6 输出组发布：checkpoint 与它的投影 brief 同组一个 seal——同一份付费
    # 产出的第二个 key（生成事实继承，用量不重复认领）。
    repository.publish((
        PublishItem(rel='runtime/preserve_checkpoint.json', value=checkpoint,
                    owners=('preserve',), refs=(preserve_ref,)),
        PublishItem(rel='stage4/grounding/reconstruction_brief.json', value=brief,
                    refs=(preserve_ref,), claim_usage=False),
    ))
    # R10a a3：本轮未再报冲突且 checkpoint 已发布＝复验通过——按 owner 清账
    # （§二.2：处理完成不重置额度，轮数与指纹留账不复位）。
    _resolve_handled_conflicts(repository, ctx.chapters)
    return {'obligations': len(checkpoint.obligations)}


# ─── stage4 body_design ──────────────────────────────────────────────────────

def run_body_design(ctx: ChainContext) -> dict:
    repository = ctx.repository
    chars = repository.read_model('stage2/characters/characters.json')
    core = chars.core_view()
    checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    brief, identities = project_brief(checkpoint, core, blueprint)
    obligation_index = design_obligation_index(checkpoint, brief)
    constraints = OutputConstraints(target_chapter_count=len(ctx.chapters),
                                    total_length_budget=ctx.budget)
    design_prompt = revision_prompt_for_owner(
        repository, 'design',
        pack_prompt(design_rules(known_char_ids=set(chars.characters),
                                 knowledge_refs={ref: change.holder == 'reader' and change.mode == 'known'
                                                 for ref, (_, change) in knowledge_change_index(obligation_index).items()},
                                 # 禁区（unanswered）不承载状态，不进 lock 闭集。
                                 fact_lock_ids=[f.kf_id for f in brief.fact_locks
                                                if f.kind != 'unanswered'],
                                 registered_surfaces=expand_surface_releases(
                                     {cid: {s.surface for s in c.surfaces} for cid, c in chars.characters.items()},
                                     chars.name_structure)),
                    brief=project_design_input(brief, obligation_index),
                    identities=[i.model_dump(mode='json') for i in identities],
                    constraints=constraints.model_dump(mode='json')),
        'stage4/design/body_design.json')

    def inspect(w):
        return inspect_design_candidate(
            w, checkpoint=checkpoint, brief=brief, obligations=obligation_index,
            chars=chars, constraints=constraints,
            length_band=ctx.length_band, book_title=ctx.book_title)

    def repairs_for(inspection):
        if not inspection.materials:
            return []
        # 单项只保留自己的来源与可修位置；完整报告在停止上下文中存一次，
        # 不复制进每条issue及其后继prompt，避免聚合诊断反而放大返修输入。
        return plan_material_repairs(inspection.materials,
                                      body=inspection.body, binding=inspection.binding,
                                      brief=brief, structure=chars.name_structure)

    last_inspection = None

    def validate(w):
        nonlocal last_inspection
        last_inspection = inspection = inspect(w)
        repairs = repairs_for(inspection)
        # 非Design责任的候选留给薄编排处理；validator不落issue、不偷改输入。
        # 此处只接受响应用于回派，并非接受正式Design。全部预检结果随回派留证；
        # 上游优先，禁止在已知上游错误下继续购买Design自修。
        if repairs and any(r.owner != 'design' or r.action == 'stop' for r in repairs):
            return []
        return inspection.feedback(list(inspection.errors) + [
            ValidationMessage(r.issue.message, r.issue.context['material_identity']) for r in repairs])

    try:
        # CS-F1 局部修订（RC-47）：判据已在判定处给每条问题带 wire 路径；可全部定位时
        # 只把最小完整父项发回返修，合并后重跑**完整**候选预检；有任一问题无法定位
        # （全局覆盖闸、上游派生材料等）自动退整份返修，不静默漏项。
        dw, design_ref = ctx.ask('design', design_prompt, validate,
                                 patch=locate_design_patches)
    except ValidationStop as exc:
        if last_inspection is not None:
            exc.context['candidate_preflight'] = last_inspection.report()
        raise
    inspection = inspect(dw)
    repairs = repairs_for(inspection)
    if repairs:
        # 上游输入将变化时，不为即将过期的Design诊断提前消耗回派轮数。
        # 全部问题仍在同一报告中；上游修订后重新计算Design自己的问题。
        upstream = [r for r in repairs if r.owner != 'design' or r.action == 'stop']
        try:
            _backfill_material_repairs(repository, upstream or repairs,
                                       raised_by='str06.stage4.body_design')
        except (OwnerRevisionRequired, OwnerRevisionStop, MechanicalDefectStop) as exc:
            exc.context['candidate_preflight'] = inspection.report()
            raise
    if not inspection.passed:
        stop = MechanicalDefectStop('Design候选完整预检失败', node='str06.stage4.body_design',
                                   issues=tuple(inspection.feedback(list(inspection.errors))))
        stop.context['candidate_preflight'] = inspection.report()
        raise stop
    body, binding = inspection.body, inspection.binding
    # 双产物按输出组一次发布（R6 权威；I-25：semantic_binding 的唯一 writer
    # 是 body_design——bind LLM 退役后承载位不再有第二个声明处，两份产物同
    # bundle 同 seal）。bind／hook_design 的单节点输入从盘面闭合。
    repository.publish((
        PublishItem(rel='stage4/design/body_design.json', value=body,
                    owners=('design',), refs=(design_ref,)),
        PublishItem(rel='stage4/grounding/semantic_binding.json', value=binding,
                    owners=('design',), refs=(design_ref,)),
    ))
    return {'scenes': len(body.scenes), 'entries': len(binding.entries),
            'unbound': len(binding.unbound), 'candidate_preflight': inspection.report()}


# ─── stage4 hook_design ──────────────────────────────────────────────────────

def run_hook_design(ctx: ChainContext) -> dict:
    repository = ctx.repository
    body = repository.read_model('stage4/design/body_design.json')
    chars = repository.read_model('stage2/characters/characters.json')
    checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    brief = repository.read_model('stage4/grounding/reconstruction_brief.json')
    _, identities = project_brief(checkpoint, chars.core_view(), blueprint)
    projection = chars.name_projection()
    kp_statements = {p.kp_id: p.statement for p in brief.propositions}
    world_statements = {wc.constraint_id: wc.statement for wc in brief.book_constraints}
    from novel.stages.stage4 import project_hook_semantics
    try:
        semantics = project_hook_semantics(body, brief)
    except ValueError as exc:
        raise MechanicalDefectStop('Hook设计输入的引用语义不闭合',
                                   node='str06.stage4.hook_design',
                                   issues=(str(exc),)) from exc
    hook_design_prompt = pack_prompt(
        HOOK_DESIGN_RULES, book_title=ctx.book_title, design=body.model_dump(mode='json'),
        semantics=semantics,
        identities=[i.model_dump(mode='json') for i in identities],
        constraints=ctx.hook_constraints.model_dump(mode='json'))
    hook_design_prompt = revision_prompt_for_owner(
        repository, 'hook_design', hook_design_prompt, 'stage4/design/presentation_graph.json')

    def hook_design_compile(w):
        hook, errors = assemble_hook_design(w, body=body, known_char_ids=set(chars.characters),
                                           name_projection=projection, name_structure=chars.name_structure)
        if hook is not None:
            context, context_errors = compile_hook_context(
                publish_graph(body, hook), projection, ctx.hook_constraints,
                chars.name_structure, kp_statements, world_statements)
            errors.extend(context_errors)
            if context is not None:
                errors.extend(validate_hook_materials(context, internal_ids=chars.characters))
        return (None, errors) if errors else (hook, [])

    hw, hook_design_ref = ctx.ask('hook_design', hook_design_prompt,
                                  lambda w: hook_design_compile(w)[1],
                                  patch=locate_design_patches)
    hook, errors = hook_design_compile(hw)
    if errors:
        raise ValueError(errors)
    graph = publish_graph(body, hook)
    repository.write('stage4/design/presentation_graph.json', graph,
                     owners=('design', 'hook_design'), refs=(hook_design_ref,))
    return {'materials': len(hook.materials)}


# ─── stage4 compile（Guard＋WriterBookIR＋HookContext，确定性） ───────────────

def run_compile_stage4(ctx: ChainContext) -> dict:
    """`str06.stage4.compile`：确定性编译（ExecutionPlan／Guard／IR／HookContext）。

    R3：必保义务未承载（binding.unbound 的合法非必保返回之外，漏承载的必保
    义务在此被义务覆盖 Guard 拦下）按 `must_keep_unbound` 回派——`unbound.owner` 经路由表闭集解析回
    body_design（design）或 preserve（grounding），写 `runtime/issues/<owner>.json`
    并抛 `OwnerRevisionRequired`；未映射 owner 显式停（E-8）。复验通过时回写
    resolved。R10a a5：锁位置违例（知情升级/读者确认早于授权承载位）按
    `epistemic_lock_order` 回派 body_design（`_backfill_epistemic_lock_order`）。

    R6 验过才发布：四个正式派生产物（execution_plan／hook 上下文／写手 IR／
    packet 指标）在全部机械判定通过后按**输出组**一次发布（免费产物同样
    封存）；失败结果只进诊断面（guard/compile.json 与 guard/report.json
    留证问题清单），正式位不落未验证字节。
    """
    repository = ctx.repository
    graph = repository.read_model('stage4/design/presentation_graph.json')
    binding = repository.read_model('stage4/grounding/semantic_binding.json')
    chars = repository.read_model('stage2/characters/characters.json')
    plan, errors = compile_execution_plan(graph, binding)
    # 诊断面：机械编译问题留证（正式组不发布）。
    repository.write('stage4/acceptance/guard/compile.json', {'errors': errors})
    _backfill_must_keep_unbound(repository, binding)
    if errors:
        # 无回派路由的机械缺陷：typed 停止给人（诊断面已落盘，exit 1 而非
        # exit 70——与 run_compile 同批修复，canary 20260930 实证）。
        raise MechanicalDefectStop(
            f'stage4 compile 机械缺陷：{errors}',
            node='str06.stage4.compile', issues=tuple(str(e) for e in errors))
    # R10a a5：锁位置 Guard（披露顺序）——机械面干净、必保义务全部承载之后
    # 才有位置分母；违例按 epistemic_lock_order 回派 body_design（披露顺序是
    # Design 的决定，Bind 只选承载位）。
    _backfill_epistemic_lock_order(repository, graph, binding)
    projection = chars.name_projection()
    brief = repository.read_model('stage4/grounding/reconstruction_brief.json')
    kp_statements = {p.kp_id: p.statement for p in brief.propositions}
    world_statements = {wc.constraint_id: wc.statement
                        for wc in brief.book_constraints}
    hook_context, errors = compile_hook_context(graph, projection, ctx.hook_constraints,
                                                chars.name_structure, kp_statements,
                                                world_statements)
    if errors:
        raise MechanicalDefectStop(
            f'stage4 hook 上下文机械缺陷：{errors}',
            node='str06.stage4.compile', issues=tuple(str(e) for e in errors))
    inspection = inspect_body_materials(
        graph, binding, brief, chars, budget=ctx.budget,
        length_band=ctx.length_band, book_title=ctx.book_title)
    writer_ir = inspection.ir
    assert writer_ir is not None  # 同输入的plan已在上方验证
    problems = list(inspection.errors)
    from novel.stages.common import material_identities
    internal_ids = material_identities(graph, binding, chars)
    material_issues = list(hook_material_issues(hook_context, internal_ids=internal_ids))
    material_issues.extend(inspection.materials)
    problems.extend(issue.message for issue in material_issues)
    guard_report = {'mode': 'planned-only', 'accepted_body': False,
                    'chapters': len(writer_ir.chapters), 'scenes': len(graph.scenes),
                    'obligations': len(binding.obligations), 'guard_errors': problems,
                    'material_issues': [asdict(issue) for issue in material_issues],
                    # 称谓投影留痕（CS-F2）：显式、可观测，不含行文本；正文与
                    # 楔子分别登记（楔子释放面由 Hook Design 自选）。
                    'name_projections': list(inspection.name_projections),
                    'hook_name_projections': list(hook_name_projections(hook_context)),
                    'chapter_budgets': {c.chapter_ordinal: c.length_budget
                                        for c in writer_ir.chapters}}
    # 失败结果只进诊断：guard report 先落（问题清单与预算读数留证），正式位不写。
    repository.write('stage4/acceptance/guard/report.json', guard_report)
    if problems:
        if material_issues and len(material_issues) == len(problems):
            _backfill_material_repairs(repository, plan_material_repairs(
                material_issues, body=graph, binding=binding, brief=brief,
                structure=chars.name_structure))
        # 其余 Guard 问题不在回派路由闭集（E-8）：typed 停止给人，恢复走
        # design 重跑（RED 通道/手动 CLI）或 owner patch；诊断面 report.json
        # 已留证全清单。
        raise MechanicalDefectStop(
            f'stage4 Guard 机械缺陷：{problems}',
            node='str06.stage4.compile', issues=tuple(str(p) for p in problems))
    # R6 输出组发布：Guard 全绿后四个正式派生产物同一组、一个 seal。
    repository.publish((
        PublishItem(rel='stage4/compiled/execution_plan.json', value=plan),
        PublishItem(rel='stage4/context/hook/hook.json', value=hook_context),
        PublishItem(rel='stage4/ir/writer_book_ir.json', value=writer_ir),
        # WriterPacket 指标（R2.8＋R10b.7）：派生读数、audit_only、重算免费；
        # 逐章记录分区条目数、章内重复行比例、被剔除历史回声数、锁数、离线
        # token（`count_text_tokens` 同 tokenizer、无请求 framing）与密度。
        PublishItem(rel='stage4/acceptance/packet_metrics.json',
                    value=packet_metrics(writer_ir,
                                         token_counter=count_text_tokens,
                                         fact_bindings=binding.fact_bindings)),
    ))
    resolve_owner_issues(repository, owner='design',
                         raised_by='str06.stage4.compile')
    resolve_owner_issues(repository, owner='preserve',
                         raised_by='str06.stage4.compile')
    resolve_owner_issues(repository, owner='book',
                         raised_by='str06.stage4.compile')
    resolve_owner_issues(repository, owner='hook_design',
                         raised_by='str06.stage4.compile')
    return guard_report


#: 回派目标节点 → runner 词汇 owner（issue 记录文件名与修订 prompt 的键）。
_REVISION_TARGET_OWNERS: Final[dict[str, str]] = {
    'str06.stage2.associate': 'associate',
    'str06.stage2.book': 'book',
    'str06.stage4.body_design': 'design',
    'str06.stage4.preserve': 'preserve',
    'str06.stage4.hook_design': 'hook_design',
}
#: `str06.stage2.chapters` 的 owner 逐章解析（R10a a3）：拍句柄 bNN_MM.* →
#: owner `chNN`（issue item=chapter:N）。表值放不了函数，回派组装处经
#: `_conflict_owner_of` 解析。
_CONFLICT_CHAPTER = 'str06.stage2.chapters'


class _NullObligation:
    """占位：obligation_id 不在 checkpoint 分母时按非必保处理（分母不闭合另有
    Guard 报错，本函数不重复拦）。"""

    must_preserve = False


_NULL_OBLIGATION = _NullObligation()


def _backfill_must_keep_unbound(repository, binding) -> None:
    """必保义务未承载 → 按 unbound.owner 回派（E-8：owner 轴闭集外显式停）。

    R8a-3：定位与消息一律用 Design 可见的源盲 core id
    （`novel/stages/stage4/grounding.py::obligation_core_id` 唯一铸造口），
    Bind reason 里出现的源序号同批映射——Design 源盲，obNN 近似源文顺序，
    对它是不可解析的噪声（canary 20260929b 实证）。context 带 typed 的依赖
    义务（含其当前承载位置）与参与人物，供修订 prompt 机械引用。

    多个目标时按执行序先回派最早的节点；其余目标在复验后的下一轮发现里
    继续走同一入口（每 owner 独立记轮，互不挤占额度）。
    """
    obligations = {o.obligation_id: o for o in binding.obligations}
    core_of = {oid: obligation_core_id(oid) for oid in obligations}
    carriers = {
        oid: sorted(entry.beat_ref for entry in binding.entries
                    if entry.obligation_id == oid)
        for oid in obligations}
    # 长id 先替换，防 ob1 抢先吃掉 ob10 的前缀。
    by_length = sorted(core_of, key=len, reverse=True)

    def _blind(text: str) -> str:
        for oid in by_length:
            text = text.replace(oid, core_of[oid])
        return text

    unbound_must = [entry for entry in binding.unbound
                    if (obligations.get(entry.obligation_id)
                        or _NULL_OBLIGATION).must_preserve]
    if not unbound_must:
        return
    grouped: dict[tuple[str, str], list[OwnerIssueV1]] = {}
    for entry in unbound_must:
        action, target = route_owner_issue('must_keep_unbound',
                                           unbound_owner=entry.owner)
        if action != 'revise_upstream':
            raise ValueError(
                f'必保义务 {entry.obligation_id} 未承载且无法路由（{target}）；'
                'owner 轴闭集外一律停，不猜回派目标')
        owner = _REVISION_TARGET_OWNERS.get(target)
        if owner is None:
            raise ValueError(f'回派目标 {target} 未登记 runner owner（编排缺陷）')
        obligation = obligations[entry.obligation_id]
        core_id = core_of[entry.obligation_id]
        grouped.setdefault((owner, target), []).append(OwnerIssueV1(
            code='must_keep_unbound', item=core_id,
            message=_blind(f'必保义务 {entry.obligation_id} 尚未承载'
                           f'（owner={entry.owner}；{entry.reason}）'),
            context={
                'unbound_owner': entry.owner,
                'core_id': core_id,
                'kind': (obligation.kind.value if hasattr(obligation.kind, 'value')
                         else str(obligation.kind)),
                'summary': obligation.statement,
                'requires_cores': sorted(core_of[dep]
                                         for dep in obligation.requires),
                'dependency_carriers': {core_of[dep]: carriers[dep]
                                        for dep in obligation.requires},
                'participants': sorted(obligation.lineage.character_ids),
            }))
    for (owner, target), issues in sorted(
            grouped.items(), key=lambda item: NODE_ORDER.index(item[0][1])):
        discover_owner_issues(repository, raised_by='str06.stage4.compile',
                              owner=owner, node=target, issues=issues)


def _backfill_epistemic_lock_order(repository, graph, binding) -> None:
    """锁位置违例 → 回派 body_design（R10a a5，§二.3 顺序三分的披露面）。

    kp 键知情升级/读者确认早于授权该模式的义务承载位置＝信息披露顺序前移
    （C1/C6 的位置面）。修复是 Design 的决定（移知情承载或 Design 侧让位）；
    Bind 只选承载位、不改披露顺序，故不回 bind。item 用 kp 句柄（Design 经
    Brief 可见，非源坐标）；context 带升级位置、授权位置与授权义务的源盲
    core_id。判据唯一实现在 `guard.lock_order_violations`（纯函数），本函数
    只做回派编排。无违例直接返回（复验通过后的清账归
    `resolve_owner_issues(owner='design')`，已有调用）。
    """
    violations = lock_order_violations(graph, binding)
    if not violations:
        return
    issues = [OwnerIssueV1(
        code='epistemic_lock_order', item=v.kp, message=v.message(),
        context={'holder': v.holder, 'kp': v.kp, 'mode': v.mode,
                 'upgrade_position': v.upgrade_ref,
                 'authorized_position': v.auth_ref,
                 'authorized_core_id': v.core_id})
        for v in violations]
    discover_owner_issues(repository, raised_by='str06.stage4.compile',
                          owner='design', node='str06.stage4.body_design',
                          issues=issues)


def _backfill_material_repairs(repository, repairs, *, raised_by='str06.stage4.compile') -> None:
    """消费纯归因的结果；任一未决先停止，同owner合并一轮，绝不重新解析消息。"""
    unresolved = [repair.issue for repair in repairs if repair.action == 'stop']
    if unresolved:
        raise MechanicalDefectStop(
            '材料归因未决：' + '；'.join(issue.message for issue in unresolved),
            node=raised_by, issues=tuple(issue.message for issue in unresolved))
    grouped: dict[tuple[str, str], list[OwnerIssueV1]] = {}
    for repair in repairs:
        grouped.setdefault((repair.owner, repair.node), []).append(repair.issue)
    first: OwnerRevisionRequired | None = None
    for (owner, target), issues in sorted(grouped.items()):
        try:
            discover_owner_issues(repository, raised_by=raised_by,
                                  owner=owner, node=target, issues=issues)
        except OwnerRevisionRequired as required:
            if first is None or NODE_ORDER.index(target) < NODE_ORDER.index(first.node):
                first = required
    if first is not None:
        raise first


# ─── stage5 hook ─────────────────────────────────────────────────────────────

def _write_hook(hook_context, ask_text) -> tuple[str | None, list[dict], Any]:
    """单产 + 机械验 + 同 Context 有限重写（stage5 hook owner 的成文编排）。

    B1：空回答（响应为空）在**同一内容轮**内重发原始 Hook 请求（同请求身份 →
    同轮下一 attempt，D5 不救回文本）；机械校验失败才构成修订（新请求身份、
    新内容轮）。
    """
    attempts: list[dict] = []
    errors: list[str] = []
    ref = None
    prose_seen = False
    for attempt in range(1 + MAX_REWRITES):
        prompt = (build_hook_user_prompt(hook_context) if not prose_seen
                  else build_rewrite_user_prompt(hook_context, errors))
        prose, response_errors, answered_ref = ask_text('hook_writer', prompt)
        ref = answered_ref if answered_ref is not None else ref
        if prose is None:
            errors = response_errors or ['响应为空']
            attempts.append({'attempt': attempt + 1, 'chars': 0, 'errors': errors})
            continue
        prose_seen = True
        errors = validate_hook_prose(prose, hook_context)
        attempts.append({'attempt': attempt + 1, 'chars': count_non_whitespace_chars(prose), 'errors': errors})
        if not errors:
            return prose, attempts, ref
    return None, attempts, ref


def run_hook(ctx: ChainContext) -> dict:
    repository = ctx.repository
    hook_context = repository.read_model('stage4/context/hook/hook.json')
    prose, attempts_log, hook_ref = _write_hook(hook_context, ctx.ask_text)
    status = 'hook_accepted_mechanical' if prose is not None else 'hook_rejected'
    receipt = HookReceipt(
        hook_context_sha256=context_sha256(hook_context),
        prose_sha256=sha256_text(prose or ''),
        request_identity=(hook_ref.request_sha256 if hook_ref is not None
                          else 'unidentified'),
        validator_version=HOOK_VALIDATOR_VERSION,
        status='accepted' if prose is not None else 'rejected',
        mechanical_checks=MECHANICAL_CHECKS)
    items: list[PublishItem] = []
    if prose is not None:
        items.append(PublishItem(rel='stage5/hook/hook.txt', text=prose))
    if hook_ref is not None:
        # 拒绝回执同样引用实际 attempt（拒绝结论以这些耐久响应为证）；付费节点
        # 不写无引用产物（provenance 闸，B4 前实测：refs=() 在此路径必被
        # ProvenanceRefused 拦下、typed 失败被掩盖）。无已应答 attempt 时不伪造
        # 写盘——失败证据留在 attempts 记录与 run 状态，随后按 typed 失败上抛。
        items.append(PublishItem(rel='stage5/hook/receipt.json', value=receipt,
                                 owners=('hook_writer',), refs=(hook_ref,)))
    # R6 输出组发布：Hook 正文与回执同组一个 seal（正文字节进回执组的输出摘要）。
    if items:
        repository.publish(tuple(items))
    repository.write('runtime/hook_attempts.json', {'attempts': attempts_log})
    if prose is None:
        repository.write('runtime/workflow_status.json',
                         {'status': status, 'accepted_body': False})
        raise MechanicalDefectStop(
            'Hook 写作在重写上限内未通过机械校验；先人工复核 HookContext',
            node='str06.stage5.hook', item='book',
            issues=tuple(str(e) for attempt in attempts_log for e in attempt['errors']))
    return {'hook_chars': count_non_whitespace_chars(prose), 'materials': len(hook_context.materials),
            'pov': hook_context.pov.model_dump(mode='json')}


# ─── stage5 body（章包写手：独立成章、并发、集合恢复；D2，B4） ────────────────

def resolve_body_chapters(body_chapters: tuple[int, ...] | None,
                          chapter_count: int) -> tuple[int, ...]:
    """正文目标章集合的机械选章规则（W1/D2）：None＝全书；非空 tuple＝恰好这些章。

    `()`、乱序、重复、越出 1..chapter_count 一律在任何付费调用之前
    ValueError 拒绝（I-34：不静默截断、不按默认补全）。返回升序目标章号。
    """
    if body_chapters is None:
        return tuple(range(1, chapter_count + 1))
    if not body_chapters:
        raise ValueError(
            'body_chapters 不允许为空集合：None 表示全书，空集合没有语义')
    if any(isinstance(n, bool) or not isinstance(n, int) for n in body_chapters):
        raise ValueError(f'body_chapters 必须是整数章号：{body_chapters!r}')
    if list(body_chapters) != sorted(body_chapters):
        raise ValueError(f'body_chapters 必须按章序升序给出：{body_chapters}')
    if len(set(body_chapters)) != len(body_chapters):
        raise ValueError(f'body_chapters 不允许重复章号：{body_chapters}')
    if body_chapters[0] < 1 or body_chapters[-1] > chapter_count:
        raise ValueError(
            f'body_chapters 越出 1..{chapter_count}：{body_chapters}；'
            '不静默截断，选章前先核对章数')
    return tuple(body_chapters)


def _packet_identities(writer_ir) -> dict[int, 'ChapterIdentities']:
    """当前编译下每章的回执分层摘要（rc6 身份拆分；Stage5 恢复绑定与 Stage7
    核对共用同一产地——写手视图／验证输入／Machine 审计三把）。"""
    return {chapter.chapter_ordinal: chapter_identities(
                project_chapter_packet(writer_ir, chapter.chapter_ordinal))
            for chapter in writer_ir.chapters}


def _committed_set(repository, identities: dict[int, 'ChapterIdentities'],
                  target: tuple[int, ...]):
    """集合恢复：逐章独立装载有效提交（回执＋正文＋章包摘要三方绑定）。

    各章互不依赖（D2）：某章缺失、章包过期或正文脱钩只使**该章**回到待补
    集合，其余章的合法提交原样保留（I-31）——已完成 1、2、4、6 章时只补
    3、5。崩溃窗口（正文已写、回执未写）按「无有效回执」处理：本章重做，
    同请求身份免费重放闭合（`R-7.4`）。`target` 是本次目标章集合（W1 选章；
    不在集合内的章既不装载也不触碰）。

    回执装载走 `inspect`（不记来源边、不做输入新鲜度）：章包摘要
    （写手视图与验证输入摘要）是比输入边新鲜度更强的逐章绑定——章回执的合法输入只有
    当前 IR，若经 `read` 装载会把其他章回执字节记进来源边，一章重做即误判
    他章 stale，破坏「各章独立有效」。
    """
    from novel.stages.common import check_chapter_receipt
    from pydantic import ValidationError
    committed: dict[int, str] = {}
    receipts: dict[int, dict] = {}
    for ordinal in target:
        rel_receipt = f'stage5/acceptance/body/{_chapter_ordinal(ordinal)}.json'
        rel_prose = f'stage5/body/{_chapter_ordinal(ordinal)}.txt'
        try:
            receipt = repository.inspect(rel_receipt)
            prose = repository.read_text(rel_prose)
            model = _receipt_model(receipt)
        except (FileNotFoundError, ValidationError):
            continue
        problems = check_chapter_receipt(
            model, identities=identities[ordinal], prose_sha256=sha256_text(prose))
        if problems:
            continue
        committed[ordinal] = prose
        receipts[ordinal] = receipt
    return committed, receipts


def _receipt_model(receipt: dict):
    from contracts.schema.receipt import ChapterReceipt
    return ChapterReceipt.model_validate(receipt)


def _write_and_commit_chapter(ctx: ChainContext, packet, ordinal: int):
    """单章独立执行、验证、提交（每章一个 owner 产物组；I-25/I-31）。"""
    repository = ctx.repository
    outcome = write_chapter(
        packet=packet,
        ask=lambda prompt: ctx.ask_body(f'body_{_chapter_ordinal(ordinal)}', prompt))
    repository.write(f'runtime/body_attempts_{_chapter_ordinal(ordinal)}.json',
                     {'attempts': [a._asdict() for a in outcome.attempts]})
    if outcome.prose is None or outcome.receipt is None:
        repository.write('runtime/workflow_status.json', {
            'status': 'body_rejected', 'accepted_body': False, 'chapter': ordinal,
            'detail': '重写额度内无合法候选（硬检查未全过）；该章无提交'})
        raise MechanicalDefectStop(
            f'第{ordinal}章在重写额度内无合法候选；先人工复核',
            node='str06.stage5.body', item=f'chapter:{ordinal}',
            issues=tuple(str(e) for attempt in outcome.attempts for e in attempt.errors))
    # R6 输出组发布：章正文与章回执同组一个 seal——正文先落、回执最后提交
    # （提交完成），拒绝候选留证在 attempts 记录。
    repository.publish((
        PublishItem(rel=f'stage5/body/{_chapter_ordinal(ordinal)}.txt',
                    text=outcome.prose),
        PublishItem(rel=f'stage5/acceptance/body/{_chapter_ordinal(ordinal)}.json',
                    value=outcome.receipt,
                    owners=(f'body_{_chapter_ordinal(ordinal)}',),
                    refs=(outcome.ref,) if outcome.ref is not None else ()),
    ))
    return outcome


def run_body(ctx: ChainContext) -> dict:
    """`str06.stage5.body`：章包写手——各章独立成文、并发执行、集合恢复（D2，B4）。

    输入是当前编译的 WriterBookIR（完整 IR 保留供存储与审计，I-16）；每章
    运行时确定性投影章包，Writer/Validator 只收当前章包——不读前文正文、
    Design、Characters 或其他章 IR。目标章集合由 `resolve_body_chapters`
    机械确定（W1/D2：`None`＝全书；非空 tuple＝恰好这些章，`()`／乱序／
    重复／越界在任何付费调用之前拒绝）。待补集合按有效提交集合在目标集合
    内计算（只补缺章）；    待补章经 `novel/model_slots.py` 的书级＋供应商总槽
    并发执行（线程数取本节点冻结并发，并发度不进语义身份）。
    单章失败不污染其他章的合法结果（I-31）：全部待补章收敛后按第一章序的
    原始失败上抛，失败清单留证 `runtime/body_failures.json`（「最近一次
    完成执行」语义，D3/W2：正常完成写空清单，缺失＝本节点没有完成执行）；
    授权／账务／取消故障保持原始类型（授权闸与 typed 状态在 run 级闭合）。
    选章（`ctx.body_chapters`）只作调用元数据，不进任何身份。
    """
    repository = ctx.repository
    writer_ir = repository.read_model('stage4/ir/writer_book_ir.json')
    target = resolve_body_chapters(ctx.body_chapters, len(writer_ir.chapters))
    identities = _packet_identities(writer_ir)
    committed, committed_receipts = _committed_set(repository, identities, target)
    for ordinal in tuple(committed):
        if ctx.body_replay(ordinal, project_chapter_packet(writer_ir, ordinal)):
            del committed[ordinal]
            del committed_receipts[ordinal]
    todo = tuple(ordinal for ordinal in target if ordinal not in committed)
    packets = {ordinal: project_chapter_packet(writer_ir, ordinal) for ordinal in todo}
    failures: dict[int, BaseException] = {}
    if todo:
        workers = ctx.pool_workers('str06.stage5.body', len(todo))

        def _one(ordinal: int):
            # 书级与供应商总槽（`novel/model_slots.py`）：等待期取消或配置拒绝
            # 都表示尚未开始供应商调用；槽位等待不进请求身份（`R-12.1c`）。
            with pipeline_model_slot(repository.root, ctx.provider,
                                     per_book=ctx.slot_per_book,
                                     per_provider=ctx.slot_per_provider):
                return _write_and_commit_chapter(ctx, packets[ordinal], ordinal)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, ordinal): ordinal for ordinal in todo}
            for future in as_completed(futures):
                ordinal = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:  # 单章失败就地定位（I-31），其余章继续
                    failures[ordinal] = exc
                    continue
                committed[ordinal] = outcome.prose
                committed_receipts[ordinal] = outcome.receipt.model_dump(mode='json')
    # 「最近一次完成执行」逐章留证（D3/W2）：带失败完成在上抛之前写一次，
    # 正常完成写空清单（此时 failures 为空）。崩溃不写 ⇒ 文件缺失表示本节点
    # 还没有一次完成执行（I-42：缺席与合法空清单是两件事）。
    _write_chapter_failures(repository, BODY_FAILURES_REL,
                            requested=target, attempted=todo, failures=failures)
    if failures:
        # 其余章的合法提交已在盘上（I-31）；按第一章序的原始失败上抛——
        # PurchaseRequired／ExecutionError 保持原类型，run 级状态据此闭合。
        raise failures[min(failures)]
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
    nw, naming_ref = ctx.ask('naming', naming_prompt, lambda w: compile_name_map(w, chars)[1])
    name_map, errors = compile_name_map(nw, chars)
    if errors:
        raise ValueError(errors)
    repository.write('stage7/naming/name_map.json', name_map, owners=('naming',),
                     refs=(naming_ref,))
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
    # Stage7 集合核对（R-10.3）：逐章装载盘上回执与正文原样（inspect，不记来源
    # 边、不经 _committed_set 预过滤——预过滤只能用于恢复补缺，不能替 Stage7
    # 隐瞒盘上事实），再与当前编译出的章包摘要逐章核对。分类与逐条问题共用
    # 同一逐章绑定实现（assembly._binding_problems）：有效/缺失/拒绝/未知/失效
    # 五态分开（I-42），不一致或缺口按 item 报告，不静默跳过。
    identities = _packet_identities(writer_ir)
    expected = range(1, len(writer_ir.chapters) + 1)
    raw_receipts: dict[int, dict] = {}
    raw_prose: dict[int, str] = {}
    for ordinal in expected:
        try:
            raw_receipts[ordinal] = repository.inspect(
                f'stage5/acceptance/body/{_chapter_ordinal(ordinal)}.json')
        except FileNotFoundError:
            continue
        try:
            raw_prose[ordinal] = repository.read_text(
                f'stage5/body/{_chapter_ordinal(ordinal)}.txt')
        except FileNotFoundError:
            continue
    access = chapter_access(expected, raw_receipts,
                            identities_by_ordinal=identities,
                            chapter_prose=raw_prose)
    chapter_prose = {ordinal: raw_prose[ordinal] for ordinal in access.accepted}
    chain_problems = verify_chapter_receipts(
        raw_receipts, raw_prose, identities_by_ordinal=identities)
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
    hook_sha = sha256_text(prose)
    outcome = assemble_delivery(
        chapter_prose=chapter_prose, body=access, hook_prose=prose,
        hook_receipt=receipt.model_dump(mode='json'),
        hook_context_sha256=context_sha256(hook_context),
        replacements=replacements, kept_literals=kept_literals)
    report = {'mode': 'body+hook+naming', 'accepted_body': access.ready and not chain_problems,
              'chapters': len(writer_ir.chapters),
              'committed_chapters': sorted(access.accepted),
              'body_access': {k: list(v) for k, v in access._asdict().items()},
              'scenes': len(graph.scenes), 'obligations': len(binding.obligations),
              'chain_problems': chain_problems,
              'hook': {'chars': count_non_whitespace_chars(prose), 'renamed_chars': count_non_whitespace_chars(renamed_hook),
                       'materials': len(hook_context.materials),
                       'replacements': len(replacements), 'kept': len(kept_literals)},
              'assembly': {'status': outcome.status, 'reason': outcome.reason,
                           'delivery_identity': outcome.delivery_identity}}
    if outcome.status == 'ready':
        final_rel = f'stage7/final/{ctx.book_title or "book"}_rewriting.txt'
        final_bytes = outcome.delivery_bytes
        # R6 输出组发布：最终 TXT 与 delivery manifest 同组一个 seal——文件先
        # 落盘、manifest 最后提交，消费者核对 manifest（及其 seal）才算就绪；
        # manifest 内嵌的交付字节摘要按待写字节预计算（与落盘后文件摘要一致）。
        manifest = build_delivery_manifest(
            delivery_identity=outcome.delivery_identity,
            final_text_sha256=hashlib.sha256(final_bytes).hexdigest(),
            receipts={ordinal: raw_receipts[ordinal] for ordinal in access.accepted},
            chapter_prose=chapter_prose,
            hook_prose_sha256=hook_sha,
            name_map_sha256=repository.file_sha256(
                repository.path('stage7/naming/name_map.json')),
            total_length_budget=ctx.budget)
        repository.publish((
            PublishItem(rel=final_rel, text=final_bytes.decode('utf-8')),
            PublishItem(rel='stage7/final/delivery_manifest.json', value=manifest),
        ))
        repository.write('runtime/delivery/assembly_report.json', report)
        return report
    repository.write('runtime/delivery/assembly_report.json', report)
    return report


NODE_RUNNERS: dict[str, Callable[[ChainContext], dict]] = {
    'str06.stage2.book': run_book,
    'str06.stage2.chapters': run_chapters,
    'str06.stage2.names': run_names,
    'str06.stage2.associate': run_associate,
    'str06.stage2.compile': run_compile,
    'str06.stage4.preserve': run_preserve,
    'str06.stage4.body_design': run_body_design,
    'str06.stage4.hook_design': run_hook_design,
    'str06.stage4.compile': run_compile_stage4,
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
    report = runner(ctx)
    ctx.after_node_commit(node_id)
    complete_owner_dispatch(ctx.repository, node_id,
        chapter_owners=tuple(f'ch{c["chapter_ordinal"]:02d}' for c in ctx.chapters)
        if node_id == 'str06.stage2.chapters' else ())
    from novel.stages.revisions import activate_node_revisions, resolve_node_revisions
    activate_node_revisions(ctx.repository, node_id)
    if node_id in {'str06.stage2.compile', 'str06.stage4.compile', 'str06.stage7.assemble'}:
        if node_id != 'str06.stage7.assemble' or report.get('assembly', {}).get('status') == 'ready':
            resolve_node_revisions(ctx.repository, node_id)
    return report


def _record_failure(ctx: ChainContext, node_id: str, exc: Exception) -> None:
    """owner 停止的 run 级留痕：失败归属精确到候选节点（含 R3 回派停止）。"""
    repository = ctx.repository
    status: dict[str, Any] = {}
    try:
        status = repository.read('runtime/workflow_status.json')
    except FileNotFoundError:
        pass
    status.update({'status': 'failed', 'failed_node': node_id,
                   'failure_kind': type(exc).__name__, 'detail': str(exc)})
    if isinstance(exc, ExecutionError):
        # 执行设施统一错误形状：stage/node/item/phase/code/context（I-36）。
        status.update(exc.as_status())
        status['failed_node'] = exc.node or node_id
    elif isinstance(exc, OwnerRevisionRequired):
        status['error_context'] = exc.context
    repository.write('runtime/workflow_status.json', status)


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
    #: R3 跨节点回派：执行位置按序推进，`OwnerRevisionRequired` 把位置回跳到
    #: 修订目标（目标必须更早且在本次选中集合内）；有界性由盘上 issue 记录
    #: 保证（每 owner 最多 OWNER_REVISION_MAX_ROUNDS 轮、同问题集重复即停），
    #: 这里只执行回跳、不另记第二本账（I-17）。R8a-7：回跳与否由
    #: `revision_gate` 唯一判定（与编排层 resume_verdict 同一把尺）——产物
    #: 已变（等下游复验）或轮耗尽（停给人）时不回跳，按原样上抛交驱动层。
    #: 现役消费者：开发线多节点运行（`python -m novel.stages.chain --node …
    #: --node …` 的 wire 采用/自愈）与自愈测试面；单节点路径恒走「目标不在
    #: 选中集合」的上抛分支（exit 1，编排器按 landed 重规划）。
    order_index = {node: index for index, node in enumerate(NODE_ORDER)}
    index = 0
    while index < len(NODE_ORDER):
        node_id = NODE_ORDER[index]
        if selected is not None and node_id not in selected:
            index += 1
            continue
        try:
            report.update(run_node(node_id, ctx))
        except OwnerRevisionRequired as exc:
            target = exc.node
            record = open_issue_record(ctx.repository, exc.owner)
            product_sha = (owner_product_digest(ctx.repository, exc.owner)
                           if record is not None else None)
            verdict, _gate_why = (
                revision_gate(record.against_sha256, product_sha,
                              record.rounds_total,
                              no_progress=record.no_progress, dispatch_pending=record.dispatch_pending)
                if record is not None else ('wait_reverify', '无 open 记录'))
            if (verdict != 'redo' or target not in order_index
                    or order_index[target] >= index
                    or (selected is not None and target not in selected)):
                # 门控不放行（产物已变/轮耗尽）或回派目标不在本腿更早处：
                # 单节点重跑路径与停止面统一交编排层按 issue 记录重规划
                # （resume_verdict 判 owner_revision）。
                _record_failure(ctx, node_id, exc)
                raise
            index = order_index[target]
            continue
        except Exception as exc:
            # owner 停止：失败归属精确到候选节点，run 级状态留痕后外抛；
            # 后续节点不再执行（不继续问模型、不放行半成品）。
            _record_failure(ctx, node_id, exc)
            raise
        if node_id == 'str06.stage5.body':
            accepted_body = report.get('body_complete', False)
        if selected is None and node_id == boundary:
            break
        index += 1
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
    批次 C）。现役消费者是 Stage2 book／names（B2 五节点；chapters 互不依赖、
    不吃引子）。
    """

    chapters: list[dict]
    hook_copy: str
    source_identity: str      # main_text＋hook_copy 盘上字节的组合 SHA-256
    root_sha1: str            # 同一组合的 SHA-1（provenance 的根身份格）


#: effort 取值面与 `NodeSpec.effort` 一致（`contracts/invariants.py`）：
#: `medium` 虽被 API 收但被服务端静默映射回 `high`，CLI 已随 B1 移除（D6 裁决）。
STR06_EFFORT_LEVELS: Final = ('low', 'high', 'max')


@dataclass(frozen=True)
class ExecutionProfile:
    """run 启动时冻结的执行配置（B1）：模型、effort、thinking、max_tokens 与整书上限。

    【只冻结一次】``cfg``（传输与模型配置：api_key、base_url、proxy、timeout、
    thinking_mode）由 CLI／orchestrator 在 run 启动时读入——`load_config()` 在
    执行期的唯一调用点是 `execute`。此后同一份 profile 经 Runtime（请求身份）、
    transport 实参（DeepSeekTransport 客户端参数）到 ledger（effort/max_tokens
    逐行入账）全程贯通：**一次 run 内环境或 `.env` 变化不再改变请求身份或账面
    归属**，执行中重读配置的通道已删除（I-16：profile 是本次 run 的权威执行事实）。

    【effort 取值面】与 `NodeSpec.effort` 一致：low／high／max。
    【max_tokens】统一 `LLM_MAX_OUTPUT_TOKENS`＝131072（D6）；截断即停，
    不以降档或调小上限代替。
    【整书上限】单次运行上限从同一准入账派生（I-17）：显式 `--book-call-ceiling`
    优先；否则冻结持久总闸的 ceiling＋grants（账缺失用
    `BOOK_CONTENT_CALL_CEILING_DEFAULT`）。运行中人工追加不改变本 run 的停止点，
    重启后生效——重启不重置、不隐式扩大授权。
    """

    cfg: Config
    reasoning_effort: str
    max_tokens: int = LLM_MAX_OUTPUT_TOKENS
    book_call_ceiling: int | None = None

    def __post_init__(self) -> None:
        if self.reasoning_effort not in STR06_EFFORT_LEVELS:
            raise ValueError(
                f'effort 取值面是 {STR06_EFFORT_LEVELS}（与 NodeSpec 一致），'
                f'得到 {self.reasoning_effort!r}')
        if self.max_tokens != LLM_MAX_OUTPUT_TOKENS:
            raise ValueError(
                f'全部付费节点 max_tokens 统一 {LLM_MAX_OUTPUT_TOKENS}（D6），'
                f'得到 {self.max_tokens!r}；截断处置靠节点设计，不靠调小上限')
        if self.book_call_ceiling is not None and (
                isinstance(self.book_call_ceiling, bool)
                or not isinstance(self.book_call_ceiling, int)
                or self.book_call_ceiling < 1):
            raise ValueError('book_call_ceiling 必须是正整数或 None')

    @property
    def model(self) -> str:
        return self.cfg.model

    @property
    def provider(self) -> str:
        return self.cfg.base_url

    @property
    def thinking(self) -> bool:
        return self.cfg.thinking_mode == 'on'


def freeze_execution_profile(
        *, cfg: Config, reasoning_effort: str, base, sid: str,
        book_call_ceiling: int | None = None,
        request_profile: LegacyLlmProfile | None = None) -> ExecutionProfile:
    """冻结一次 run 的执行配置：effort 校验 + 整书上限从持久准入账派生。

    上限判定顺序：显式值（工具路径 `--book-call-ceiling`）→ 持久总闸
    ceiling＋grants（`read_book_budget`；坏账关闭式上抛，不把坏账当零）
    → `BOOK_CONTENT_CALL_CEILING_DEFAULT`（首跑账尚未建立）。
    """
    if request_profile is not None:
        from novel.llm.providers import validate_legacy_profile
        validate_legacy_profile(request_profile)
        cfg = replace(cfg, model=request_profile.model, thinking_mode=request_profile.thinking_mode)
        reasoning_effort = request_profile.reasoning_effort
    if book_call_ceiling is None:
        budget = read_book_budget(base, sid)
        if budget is None:
            book_call_ceiling = BOOK_CONTENT_CALL_CEILING_DEFAULT
        else:
            book_call_ceiling = budget.ceiling + sum(
                g.additional for g in budget.grants)
    return ExecutionProfile(cfg=cfg, reasoning_effort=reasoning_effort,
                            book_call_ceiling=book_call_ceiling)


def str06_chat_request(profile: ExecutionProfile, owner: str, prompt: str, *,
                       json_mode: bool, system: str, temperature: float,
                       enforce: bool = True) -> ChatRequest:
    """新链请求构造：模型、effort、thinking 与 max_tokens 全部取自冻结的 profile。

    预览与真实购买走同一构造：token 估算、预算闸与缓存键逐字一致。
    请求身份（model／effort／thinking／max_tokens 因此全在
    `ChatRequest.cache_payload()` 里）与 ledger 行（同列逐行入账）由同一份
    profile 决定——配置贯通到身份与账，没有第二条读取通道。`enforce=False`
    只用于历史 wire 采用（不真实发送，也不该被上线预算闸代审）。
    """
    request = ChatRequest(model=profile.model, system=system, user=prompt,
                          reasoning_effort=profile.reasoning_effort,
                          thinking=profile.thinking, json_mode=json_mode,
                          max_tokens=profile.max_tokens, temperature=temperature)
    if enforce:
        from novel.llm.budget import enforce_request_budget
        # 完整输入超限就阻断：精确 tokenizer + 官方 framing，不摘要、不截断。
        enforce_request_budget([request], node='str06', purpose=owner)
    return request


@dataclass
class PaidBoundary:
    """付费边界装配体：workflow／epoch、ledger、`PaidRequestSession` 与请求构造。

    正式 CLI 节点执行（`execute`）经本装配走付费边界——**不另建付费通路**：
    同一 session、同一 ledger、同一 `enforce_request_budget` 闸、同一 workflow／
    epoch 生命周期。授权仍由调用方显式给出（`--yes`／`--allow-source-export`），
    本装配不代批。
    """

    repository: Any
    session: PaidRequestSession
    context: Any
    profile: ExecutionProfile
    provider: str
    model: str

    def build_request(self, owner: str, prompt: str, *, json_mode: bool, system: str,
                      temperature: float, enforce: bool = True) -> ChatRequest:
        """新链请求构造；effort 与 max_tokens 来自 run 冻结的 profile。

        预览与真实购买走同一构造：token 估算、预算闸与缓存键因此逐字一致。
        """
        return str06_chat_request(
            self.profile, owner, prompt, json_mode=json_mode, system=system,
            temperature=temperature, enforce=enforce)


def assemble_paid_boundary(
    *, base, run_dir, source_identity: str, root_sha1: str, profile: ExecutionProfile,
    yes: bool = False, allow_source_export: bool = False,
    adopt_roots: tuple = (), wire_dir=None, readonly: bool = False,
) -> PaidBoundary:
    """把 Stage1 源身份、workflow／epoch、付费边界与预算闸装到一起。

    `profile` 必须由调用方在 run 启动时冻结（`freeze_execution_profile`）：
    本装配**不再读执行期配置**——`load_config()` 不在这里，一次 run 内
    环境／`.env` 变化不影响请求身份、传输实参与账面归属。`run_dir` 必须位于
    `base` 下的隔离根：一个候选根一个 workflow 身份，sid 取根目录名；
    `readonly`（`--check-only`）只复校不签发新调用。
    """
    base_path = Path(base).resolve()
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_relative_to(base_path):
        raise ValueError('run-dir 必须位于 base 下的隔离根')
    sid = run_dir.relative_to(base_path).as_posix().replace('/', '_')
    repository = Str06Repository(run_dir)
    workflow, context = init_or_resume_candidate_workflow(
        repository=repository, base=base_path, code=prov.code_version(),
        root_sha1=root_sha1, source_identity=source_identity,
        task_id=require_workflow(base_path).task_id,
        configured_model='unknown' if wire_dir else profile.model,
        created_at=int(time.time()), readonly=readonly)
    session = PaidRequestSession(
        repository, context.ledger, DeepSeekTransport(profile.cfg), yes=yes,
        allow_source_export=allow_source_export, provider=profile.provider,
        book_call_ceiling=profile.book_call_ceiling, sid=sid,
        adopt_roots=tuple(Path(root) for root in adopt_roots))
    return PaidBoundary(repository=repository, session=session, context=context,
                        profile=profile, provider=profile.provider,
                        model=profile.model)


def load_stage1_chapters(base, sid: str) -> Stage1Input:
    """从正式 Stage1 章数组读候选链源输入（`--book-sid` 生产路径）。

    章数组即全链唯一章节键（阅读序 `chapter_ordinal`，从 1 连续）；目标章数等于
    源章数由 `MainText` 模型自身保证。`stage1/hook_copy.json`（章前文字／源引子）
    随本函数进入 Stage2 book／names 的 e00 证据段；缺失按空字节参与组合摘要。
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
                 body_chapters=None, node_filter=None, hook_copy='',
                 concurrency: int = PIPELINE_DEFAULT_CONCURRENCY,
                 node_concurrency: Mapping[str, int] | None = None,
                 slot_per_book: int = PIPELINE_BOOK_CONCURRENCY,
                 slot_per_provider: int = PIPELINE_PROVIDER_CONCURRENCY,
                 body_replay=lambda _ordinal, _packet: False,
                 after_node_commit=lambda _node: None):
    """记录显式执行状态；失败保留旧产物供恢复，但不能仍宣称上次绿灯有效。

    流程编排已归 owner runner（本模块上半）：本函数只装配 ChainContext、驱动
    节点计划并落 run 级状态（F7b 薄驱动）。`node_concurrency` 按可并发节点
    分别冻结线程数；缺键时用 `concurrency`。并发度不进语义身份（`R-12.1c`）。
    `body_chapters` 是
    调用层选章（W1/D2）：只作调用元数据记入 `runtime/workflow_status.json`
    的独立字段供审计，**不进** `input_sha256` 身份摘要、请求身份、回执或
    provenance——从 canary 续跑到全书不改变任何已提交章的采用与恢复身份。
    """
    hook_constraints = hook_constraints or HookConstraints(
        target_chars=280, hard_max_chars=360)
    ctx = ChainContext(
        repository=repository, ask=ask, ask_text=ask_text, ask_body=ask_body,
        chapters=chapters, hook_copy=hook_copy, budget=budget, book_title=book_title,
        hook_constraints=hook_constraints, length_band=length_band,
        body_chapters=body_chapters, provider=provider, model=model,
        body_replay=body_replay, after_node_commit=after_node_commit,
        digest=digest, concurrency=concurrency,
        node_concurrency=dict(node_concurrency or {}),
        slot_per_book=slot_per_book, slot_per_provider=slot_per_provider)
    status_rel = 'runtime/workflow_status.json'
    identity = digest({'chapters': chapters, 'hook_copy': hook_copy, 'budget': budget,
                       'through': through, 'band': length_band})
    #: 选章只作调用元数据（W1/D2）：run 级状态各态都带上这个审计字段。
    audit_chapters = list(body_chapters) if body_chapters is not None else None
    repository.write(status_rel, {'status': 'running', 'input_sha256': identity,
                                  'accepted_body': False,
                                  'body_chapters': audit_chapters})
    try:
        report, final_status, accepted_body = run_chain(
            ctx, through=through, node_filter=tuple(node_filter) if node_filter else None)
    except PurchaseRequired as exc:
        repository.write(status_rel, {
            'status': 'needs_authorization', 'input_sha256': identity,
            'accepted_body': False, 'blocked_owner': exc.owner, 'detail': str(exc),
            'body_chapters': audit_chapters})
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
                                  'accepted_body': accepted_body, 'through': through,
                                  'body_chapters': audit_chapters})
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


def frozen_concurrency(cfg: Config) -> dict[str, Any]:
    """把本次冻结的节点并发和两级总槽交给 ``run_workflow``。

    可并发节点各自取值。没写覆盖的节点保持 ``Config.concurrency``，
    不跟随另一个节点的覆盖。
    """
    return {
        'concurrency': cfg.concurrency,
        'node_concurrency': {
            node: cfg.concurrency_for(node) for node in PIPELINE_PARALLEL_NODES},
        'slot_per_book': cfg.concurrency_policy.per_book,
        'slot_per_provider': cfg.concurrency_policy.per_provider,
    }


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
    resample_owner = getattr(args, 'resample', None)
    resample_reason = getattr(args, 'reason', None)
    if bool(resample_owner) != bool(resample_reason):
        raise ValueError('--resample 与非空 --reason 必须同时提供')
    if resample_owner:
        target_node, _scope = semantic_owner_unit(resample_owner)
        if args.check_only or args.wire_dir:
            raise ValueError('定向重采不能与 check-only／wire 采用混用')
        if args.node and target_node not in args.node:
            raise ValueError('--resample owner 不属于本次执行节点')
        if not args.node and NODE_ORDER.index(target_node) > NODE_ORDER.index(THROUGH_NODE[args.through]):
            raise ValueError('--resample owner 超出本次 --through 范围')
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
    # 装配体内部按同一规则再取 sid（一个 NOVEL_OUTPUT 根一个候选链）：facilities
    # 的 sid 取 run 目录名，与这里一致，不为执行面另立一套命名。
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
    if resample_owner and resample_owner.startswith(('ch', 'body_ch')):
        n = int(resample_owner.removeprefix('body_')[2:])
        if not 1 <= n <= len(chapters):
            raise ValueError('--resample 章号超出正式输入')
        if resample_owner.startswith('body_ch') and n not in resolve_body_chapters(args.body_chapters, len(chapters)):
            raise ValueError('--resample 目标章不在 --body-chapters 内')
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
    # workflow／epoch 身份：首开落记录，复用不换身份，代码漂移滚动，源输入变化拒绝。
    # 执行配置在 run 启动时冻结一次（B1）：`load_config()` 的唯一执行期调用点
    # 在这里，此后同一份 ExecutionProfile 贯通 Runtime、transport、请求身份与
    # ledger——执行中重读配置的通道不存在。
    cfg = load_config()
    request_profile = getattr(args, 'request_profile', None)
    if request_profile is not None:
        from novel.llm.providers import validate_legacy_profile
        for node in args.node or ():
            validate_legacy_profile(request_profile, node=node)
    sid = run_dir.relative_to(base_path).as_posix().replace('/', '_')
    profile = freeze_execution_profile(
        cfg=cfg,
        reasoning_effort=(request_profile.reasoning_effort if request_profile is not None
                          else getattr(args, 'reasoning_effort', None) or 'high'),
        base=base_path, sid=sid,
        request_profile=request_profile,
        book_call_ceiling=getattr(args, 'book_call_ceiling', None))
    boundary = assemble_paid_boundary(
        base=base, run_dir=run_dir, source_identity=source_identity,
        root_sha1=root_sha1, profile=profile, yes=args.yes,
        allow_source_export=args.allow_source_export,
        adopt_roots=tuple(args.adopt_root), wire_dir=args.wire_dir,
        readonly=args.check_only)
    # 换成附着了候选运行上下文（provenance 铸造与 freshness）的同一根 repository：
    # 正式产物落盘拒绝「无来源账」这条静默路径。
    repository, session = boundary.repository, boundary.session

    def build_request(owner, prompt, *, json_mode, system, temperature):
        return boundary.build_request(
            owner, prompt, json_mode=json_mode, system=system, temperature=temperature,
            enforce=not args.wire_dir)

    def adopt(owner, path, validate, *, system, prompt, json_mode, temperature):
        """wire 采用：核验后落修订记录（ContentRevision 层），返回显式采用引用。"""
        if args.repair_manifest and (
                owner not in adopted or Path(adopted[owner]['path']).resolve() != path.resolve()
                or repository.file_sha256(path) != adopted[owner]['sha256']):
            raise ValueError(f'{owner} wire 不在已核实修订 manifest 内')
        original_bytes = path.read_bytes()
        raw = repository.read_external(path)
        errors = validate(raw)
        revision = {
            'owner': owner, 'kind': 'wire_adoption', 'mode': 'wire-adoption',
            'path': str(path.resolve()),
            'sha256': repository.file_sha256(path),
            'request_sha256': 'wire-adoption',
            'new_paid_calls': 0, 'historical_usage': None, 'errors': errors,
            'created_at': int(time.time())}
        if errors:
            revision['kind'] = 'wire_rejected'
            repository.write(f'runtime/revisions/{owner}.json', revision)
            node, scope = semantic_owner_unit(owner)
            raise MechanicalDefectStop(f'{owner} 免费采用失败：{errors}', node=node,
                                       item=scope, issues=tuple(str(e) for e in errors))
        # F7a-3：wire 采用登记身份／成本绑定——注册源根有同请求身份已购行时三方
        # 绑定（身份／响应／成本），否则退到采用链（批次口径）或如实标未核。
        model = 'unknown'
        note_path = args.wire_dir / f'{owner}_patch_note.json' if args.wire_dir else None
        patch_requested = note_path is not None and note_path.exists()
        if args.adopt_root or patch_requested:
            request = build_request(owner, prompt, json_mode=json_mode, system=system,
                                    temperature=temperature)
            identity = digest({'owner': owner, 'provider': session.provider,
                               'request': request.cache_payload()})
            revision['request_sha256'] = identity
            allow_patch, patch_reason = False, None
            note_path = (args.wire_dir / f'{owner}_patch_note.json'
                         if args.wire_dir else None)
            if note_path is not None and note_path.exists():
                note = repository.read_external(note_path)
                if note.get('base_response_identity') != identity:
                    from novel.stages.revisions import revision_error
                    raise revision_error(owner, f'{owner} patch note 的基线身份与当前请求不一致，拒绝采用')
                allow_patch, patch_reason = True, note.get('reason')
                if not isinstance(patch_reason, str) or not patch_reason.strip():
                    from novel.stages.revisions import revision_error
                    raise revision_error(owner, 'owner patch 必须登记非空修订理由')
            if patch_requested and not session.adopt_roots:
                session.adopt_roots = (repository.root,)
            previous_patch_sha = None
            if allow_patch:
                from novel.stages.revisions import verified_previous_revision
                previous = verified_previous_revision(repository, owner)
                if previous is not None:
                    previous_patch_sha = previous.candidate_sha256
            bound = session.bind_wire_adoption(owner, identity, path,
                                               allow_patch=allow_patch,
                                               previous_patch_sha256=previous_patch_sha)
            revision.update({
                'mode': bound.get('mode'), 'cost_link': bound.get('cost_link'),
                'source_root': bound.get('source_root'),
                'attempt_id': bound.get('attempt_id'),
                'model': bound.get('model'), 'owner_patch': bound.get('owner_patch', False),
                'historical_usage': bound.get('historical_usage'),
                'adoption_chain_verified': bound.get('adoption_chain_verified', False)})
            if bound.get('owner_patch'):
                # owner patch 是无外发的修订：绑定基线请求身份与修订说明。
                revision['patch'] = {'baseline_request_sha256': identity,
                                     'reason': patch_reason,
                                     'candidate_sha256': hashlib.sha256(original_bytes).hexdigest()}
            model = bound.get('model') or 'unknown'
        # 原字节在本根耐久保存；采用记录不再依赖根外临时补丁。
        evidence_rel = f'runtime/revisions/evidence/{hashlib.sha256(original_bytes).hexdigest()}.json'
        local_path = repository.write_evidence_bytes(evidence_rel, original_bytes)
        revision['path'] = str(local_path)
        if revision.get('owner_patch'):
            from novel.stages.revisions import preview_owner_patch, stage_revision
            from novel.stages.review import dry_run_patch, character_literals
            source_root = Path(revision['source_root'])
            baseline = purchased_baseline(source_root, owner, revision['request_sha256'])
            preview = preview_owner_patch(
                owner=owner, identity=revision['request_sha256'], candidate=raw,
                baseline=baseline, validate=validate,
                character_literals=character_literals(Str06Repository(repository.root)),
                dry_run=lambda w: dry_run_patch(Str06Repository(repository.root), owner, w,
                    evidence=build_evidence(chapters, hook_copy), budget=args.budget,
                    hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                     hard_max_chars=args.hook_hard_max),
                    chapter_count=len(chapters), source_texts=tuple(c['text'] for c in chapters),
                    book_title=args.book_title, length_band=(args.body_band_low, args.body_band_high)))
            stage_revision(repository, owner=owner, identity=revision['request_sha256'],
                           baseline=baseline, raw_bytes=original_bytes, preview=preview,
                           reason=patch_reason)
        repository.write(f'runtime/revisions/{owner}.json', revision)
        ref = AdoptionRef(
            owner=owner, model=model, prompts={'system': system, 'user': prompt},
            request_sha256=revision['request_sha256'],
            historical=({k: revision[k] for k in
                         ('mode', 'cost_link', 'source_root', 'attempt_id',
                          'historical_usage', 'new_paid_calls')
                         if revision.get(k) is not None} if args.adopt_root or patch_requested else None),
            patch=revision.get('patch'))
        return raw, ref

    def wire_path(owner):
        if not args.wire_dir:
            return None
        path = args.wire_dir / f'{owner}_wire.json'
        return path if path.exists() else None

    def replay_patch(owner, request, validate, system, prompt):
        if owner == resample_owner:
            return None
        from novel.stages.revisions import adopted_patch
        identity = digest({'owner': owner, 'provider': session.provider, 'request': request.cache_payload()})
        replay = adopted_patch(repository, owner, identity, validate)
        if replay is None:
            return None
        data, record, baseline = replay
        ref = AdoptionRef(
            owner=owner, model=profile.model, request_sha256=identity,
            prompts={'system': system, 'user': prompt},
            historical={'mode': 'owner-patch-replay', 'cost_link': 'ledger-row',
                        'source_root': baseline['source_root'], 'attempt_id': baseline['attempt_id'],
                        'new_paid_calls': 0},
            patch={'baseline_request_sha256': identity, 'reason': record.reason,
                   'candidate_sha256': record.candidate_sha256})
        return data, ref

    selected_samples = {}

    def sample_request(owner, request):
        from dataclasses import replace
        from novel.stages.revisions import sampling_request
        if owner in selected_samples:
            selection = selected_samples[owner]
            return (replace(request, sampling_nonce=selection.nonce), selection) if selection else (request, None)
        selected, selection = sampling_request(repository, session, owner, request,
                                               explicit=owner == resample_owner, reason=resample_reason or '')
        selected_samples[owner] = selection
        return selected, selection

    def record_sample(owner, data, selection, validate):
        if selection is None:
            return
        from novel.stages.revisions import preview_owner_patch, stage_revision
        from novel.stages.review import dry_run_patch, character_literals
        baseline = purchased_baseline(repository.root, owner, selection.baseline_request_sha256)
        preview = preview_owner_patch(
            owner=owner, identity=selection.baseline_request_sha256, candidate=data,
            baseline=baseline, validate=validate,
            character_literals=character_literals(Str06Repository(repository.root)),
            dry_run=lambda w: dry_run_patch(Str06Repository(repository.root), owner, w,
                evidence=build_evidence(chapters, hook_copy), budget=args.budget,
                hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                 hard_max_chars=args.hook_hard_max), chapter_count=len(chapters),
                source_texts=tuple(c['text'] for c in chapters), book_title=args.book_title,
                length_band=(args.body_band_low, args.body_band_high)))
        stage_revision(repository, owner=owner, identity=selection.baseline_request_sha256,
                       baseline=baseline, raw_bytes=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                       preview=preview, reason=selection.reason, kind='resample', sequence=selection.sequence)

    def ask(owner, prompt, validate, patch=None):
        path = wire_path(owner)
        if path is not None:
            raw, ref = adopt(owner, path, validate, system=SYS, prompt=prompt,
                             json_mode=True, temperature=0.3)
            return raw, ref
        request = build_request(owner, prompt, json_mode=True, system=SYS, temperature=0.3)
        patch_replay = replay_patch(owner, request, validate, SYS, prompt)
        if patch_replay is not None:
            return patch_replay
        request, selection = sample_request(owner, request)
        from novel.stages.common import semantic_revision_rules
        raw, answer = ask_json(session, owner, request, validate, patch=patch,
                               revision_rules=semantic_revision_rules(owner))
        record_sample(owner, raw, selection, validate)
        return raw, answer

    def text_validation(owner, wire):
        from novel.stages.revisions import validate_prose_wire
        if owner == 'hook_writer':
            context = repository.read_model('stage4/context/hook/hook.json')
            return validate_prose_wire(wire, lambda text: validate_hook_prose(text, context))
        packet = project_chapter_packet(repository.read_model('stage4/ir/writer_book_ir.json'), int(owner[7:]))
        contract = packet.realization_contract
        return validate_prose_wire(wire, lambda text: validate_chapter_prose(text, withheld_literals=packet.chapter.withheld_literals,
            released_surfaces=packet.chapter.released_surfaces, target_chars=contract.target_chars,
            band_low=contract.band_low, band_high=contract.band_high))

    def ask_text(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw, ref = adopt(owner, path, lambda w: text_validation(owner, w), system=HOOK_WRITER_SYSTEM,
                             prompt=prompt, json_mode=False, temperature=0.7)
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return prose, [], ref
        request = build_request(owner, prompt, json_mode=False, system=HOOK_WRITER_SYSTEM,
                                temperature=0.7)
        patch = replay_patch(owner, request, lambda w: text_validation(owner, w), HOOK_WRITER_SYSTEM, prompt)
        if patch is not None:
            data, ref = patch
            return data['prose'], [], ref
        request, selection = sample_request(owner, request)
        text, errors, answer = ask_text_once(session, owner, request)
        return text, errors, answer.adoption_ref(
            prompts={'system': HOOK_WRITER_SYSTEM, 'user': prompt})

    def ask_body(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw, ref = adopt(owner, path, lambda w: text_validation(owner, w), system=BODY_WRITER_SYSTEM,
                             prompt=prompt, json_mode=False, temperature=0.7)
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return AnsweredText(text=prose, finish_reason=None, identity='wire-adoption',
                                cached=True, ref=ref)
        request = build_request(owner, prompt, json_mode=False, system=BODY_WRITER_SYSTEM,
                                temperature=0.7)
        patch = replay_patch(owner, request, lambda w: text_validation(owner, w), BODY_WRITER_SYSTEM, prompt)
        if patch is not None:
            data, ref = patch
            return AnsweredText(text=data['prose'], finish_reason=None,
                                identity=ref.request_sha256, cached=True, ref=ref)
        request, selection = sample_request(owner, request)
        text, errors, answer = ask_text_once(session, owner, request)
        return AnsweredText(text=text, finish_reason=answer.finish_reason,
                            identity=answer.request_sha256, cached=answer.cached,
                            ref=answer.adoption_ref(
                                prompts={'system': BODY_WRITER_SYSTEM, 'user': prompt}))

    def body_replay(ordinal, packet):
        from contracts.schema.workflow import OwnerRevisionRecordV1
        from novel.stages.revisions import sampling_request
        owner = f'body_ch{ordinal:02d}'
        if owner == resample_owner:
            return True
        if wire_path(owner) is not None:
            return True
        request = build_request(owner, render_chapter_packet(packet), json_mode=False,
                                system=BODY_WRITER_SYSTEM, temperature=0.7)
        _request, selection = sampling_request(repository, session, owner, request,
                                               explicit=False, reason='')
        if selection is None:
            return False
        try:
            record = OwnerRevisionRecordV1.model_validate(
                repository.inspect(f'runtime/revisions/active/{owner}.json'))
        except FileNotFoundError:
            return True
        return (record.kind != 'resample' or record.sequence != selection.sequence
                or record.baseline_request_sha256 != selection.baseline_request_sha256
                or record.status == 'staged'
                or record.product_sha256 != repository.file_sha256(repository.path(record.product_rel)))

    def after_node_commit(node):
        for owner, selection in selected_samples.items():
            if selection is None or semantic_owner_unit(owner)[0] != node:
                continue
            if owner.startswith('body_ch'):
                text = repository.read_text(f'stage5/body/{owner.removeprefix("body_")}.txt')
            elif owner == 'hook_writer':
                text = repository.read_text('stage5/hook/hook.txt')
            else:
                continue
            record_sample(owner, {'prose': text}, selection, lambda w: text_validation(owner, w))

    print(run_workflow(repository=repository, chapters=chapters, hook_copy=hook_copy,
                       ask=ask, ask_text=ask_text,
                       ask_body=ask_body, budget=args.budget, through=args.through,
                       node_filter=tuple(args.node) or None,
                       provider='manual-adoption' if args.wire_dir else profile.provider,
                       model='unknown' if args.wire_dir else profile.model,
                       book_title=args.book_title,
                       hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                         hard_max_chars=args.hook_hard_max),
                       length_band=(args.body_band_low, args.body_band_high),
                       body_chapters=args.body_chapters,
                       **frozen_concurrency(cfg),
                       body_replay=body_replay, after_node_commit=after_node_commit))
