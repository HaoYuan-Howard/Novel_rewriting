#!/usr/bin/env python3
"""冻结 current-schema corpus 的新增付费范围；纯只读、无执行入口。

本工具只接受两本书、恰好 17 + 25 章的隔离输入。每本输入可以是原始 ``source``
（output base 必须不存在或为空），也可以是已经落稳且已批准的当前 schema Stage0/1
（Stage2～7 必须完全为空）。它会：

* 用生产 ``build_argv`` 展开每本八条付费 CLI dispatch；即时机械测量不是独立 dispatch；
* 固定当前模型/思考模式、独立空 cache 与 ``DEEPSEEK_MAX_RETRIES=0``；
* 从真实节点 ladder 复算基础、语义审判和条件调用的 wire 可达上限；
* 只对在上游生成前已经可知的 Stage2 global prompt 做精确 tokenizer 计数；
* 把旧 ledger 作为带哈希的历史观察列出，不冒充新 schema 的预测；
* 单列受限校准 runner 的最低 800-case 调用拓扑；在最终私有 manifest、case
  catalog 与 rubric/prompt bundle 未就绪前明确保持 ``blocked``，不猜 token。

它没有 ``--execute``、不会创建 base/cache、不会写计划文件，也不会联网。打印出的
``production_cli_argv`` 仍须在生成最终人工决策后另行冻结并再次确认，不能把
本次只读预览当成付费授权。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import (  # noqa: E402
    STAGE0_CLEAN_TEXT,
    STAGE0_REVISIONS,
    STAGE1_HOOK_COPY,
    STAGE1_MAIN_TEXT,
    STAGE2_GLOBAL_GROUNDING,
)
from contracts.invariants import NODES, WORKFLOW_ROLE_CONTRACT  # noqa: E402
from contracts.schema import artifact_spec, decode_artifact  # noqa: E402
from contracts.schema.corpus import CURRENT_CORPUS_PAID_NODES  # noqa: E402
from contracts.schema.stage0 import Stage0Revisions  # noqa: E402
from contracts.schema.stage1 import HookCopy, MainText  # noqa: E402
from novel import config, paths, prov  # noqa: E402
from novel.llm.budget import (  # noqa: E402
    TOKENIZER_LABEL,
    count_input_tokens,
    model_budget,
)
from novel.llm.client import (  # noqa: E402
    MAX_SAMPLES_PER_RUNG,
    models_for,
    rungs_from_effort,
)
from novel.llm.transport import ChatRequest  # noqa: E402
from novel.orchestrator.nodes import build_argv  # noqa: E402
from novel.stages.stage0.removal import remove_bytes  # noqa: E402
from novel.stages.stage0 import revisions as stage0_revisions  # noqa: E402
from novel.stages.stage1.partition import partition_text  # noqa: E402
from novel.stages.stage1.audit import audit_surface  # noqa: E402
from novel.stages.stage2 import extract as stage2_extract  # noqa: E402
from novel.stages.stage2 import characters as stage2_characters  # noqa: E402
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable  # noqa: E402
from novel.stages.stage4.prompts import (  # noqa: E402
    BLIND_BOOK_STRATEGY_MAX_TOKENS,
    BLIND_CHAPTER_DESIGN_MAX_TOKENS,
    EVIDENCE_BIND_MAX_TOKENS,
)
from novel.stages.stage5 import write as stage5_write  # noqa: E402
from novel.stages.stage6.hook import (  # noqa: E402
    CANDIDATE_RETRY_MAX_TOKENS as HOOK_RETRY_MAX_TOKENS,
    CANDIDATE_RETRY_RUNGS as HOOK_RETRY_RUNGS,
    MAX_TOKENS as HOOK_MAX_TOKENS,
)
from novel.stages.stage7.rename import MAX_TOKENS as RENAME_MAX_TOKENS  # noqa: E402


# current corpus 的付费拓扑只有这一份；预览不得再手抄一张会独立漂移的表。
PAID_NODES = CURRENT_CORPUS_PAID_NODES
EXPECTED_CHAPTERS = (17, 25)
FIXED_MODEL = config.DEFAULT_MODEL
FIXED_THINKING = config.DEFAULT_THINKING_MODE
FIXED_MAX_RETRIES = 0
CONTEXT_LIMIT = model_budget(FIXED_MODEL).context_limit

EXISTING_AUTHORIZED = {
    'scope': 'FC08/12/13 isolated experiments',
    'cli_runs': 10,
    'minimum_wire_attempts': 134,
    'fc08_hard_limit': 252,
    'fc13_base_ladder_limit': 322,
    'fc13_conditional_habit_quote_limit': 14,
    'fc13_hard_limit': 336,
    'external_hard_limit': 588,
    'included_in_new_corpus_totals': False,
    'overlaps_current_corpus_scope': False,
}

#: 跑双盲语义判官的节点 → 它问的那一类能力。
#: ⚠ 这里曾叫 `SEMANTIC_NODES`——那个名字说的是「依赖哪一份 evaluator profile」，
#: 而 profile 那套装置已整批下线；后来它登记的是双盲判官节点。
#: `stage6.semantic` 已随判官节点退役（2026-09-15），付费预览面不再有语义 dispatch。
SEMANTIC_NODES: dict[str, str] = {}

PREREQUISITES = {
    'stage2.characters': (
        'Stage0 必须真正 approved；Stage1 正文为当前 schema 且 fresh。',
        '整书静态人物注册单独计入首轮调用；证据使用本次源文候选，不能复用旧人物表。',
    ),
    'stage2.global': (
        'Stage0 必须是当前 v2 且真正 approved；Stage1 三份当前 schema 产物完整。',
        '当前人物登记与称名投影必须已由 stage2.characters 完整提交。',
        'source-only 输入若不满足首次零 removed/零 warning/clean==source，'
        '须先由人工显式 actor/reason 批准。',
    ),
    'stage2.blueprint': (
        f'{STAGE2_GLOBAL_GROUNDING} 必须是本次正式、fresh 的 '
        f'v{artifact_spec(STAGE2_GLOBAL_GROUNDING).current_version} 产物。',
        '逐章输入范围由当前 Stage1 的 17/25 章复算，不使用旧 corpus 章表。',
        '当前 Global 必须已通过独立 typed 节点接受；预审或已读记录不能放行。',
    ),
    'stage4.design': (
        'Stage2 blueprint 必须 fresh，去序真相图投影可完整闭合。',
        'Global generation-safe 且统一机械准入（stage4_ready）通过，进入 Stage4。',
    ),
    'stage4.plan': (
        'Stage4 blind_candidate 必须 fresh；Stage2 global + blueprint 证据层必须 fresh。',
        'A 相候选已单独预览与批准；B 相只做证据绑定，不重规划。',
    ),
    'stage5.write': (
        'Stage1 主体与 Stage4 plan/context 必须 fresh，且 s4 已人工确认；'
        'writing 已明确授权全部计划章的单一闭卷写作。',
        '超上限章零调用保留 pretrim 并转人工：本仓没有自动收缩路径，'
        '这一层不存在第二条付费回路。',
    ),
    'stage6.hook': (
        'Stage5 机械/语义结论已处理，s5 gate 经人工批准。',
    ),
    'stage6.semantic': (
        'Stage6 hook 正式候选必须 fresh。',
        '语义审判的合并决议恒是带证据的 UNVER；具体 contradiction 仍完整展示，'
        '所选候选只能经 actor/reason/subject hash 人工闭环。',
    ),
    'stage7.rename': (
        'Stage6 候选必须已人工 approve，且语义 clear；或存在 hash-bound 的有效人工 resolution。',
        'rename 后仍须走 0 成本 stage7.approve，再 assemble；两者不计入本付费预览。',
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BookSpec(_Strict):
    sid: str = Field(min_length=1)
    base: Path
    input_kind: Literal['source', 'stage1']
    source: Path | None = None

    @model_validator(mode='after')
    def _source_matches_kind(self) -> 'BookSpec':
        if (self.input_kind == 'source') != (self.source is not None):
            raise ValueError('input_kind=source 时且仅此时必须给 source')
        return self


class PreviewSpec(_Strict):
    books: Annotated[list[BookSpec], Field(min_length=2, max_length=2)]
    historical_ledger: Path | None = None

    @model_validator(mode='after')
    def _two_isolated_books(self) -> 'PreviewSpec':
        if len({book.sid for book in self.books}) != 2:
            raise ValueError('current corpus 必须是两本不同 sid')
        bases = [book.base.resolve() for book in self.books]
        # Canonical corpus 两本书共享一个 NOVEL_OUTPUT 根，隔离边界是
        # ``<title>_<sid>`` 书目录与每 sid cache，不是伪造两个 output 根。
        # 完全相同的 base 因此合法；一本指向另一本 base 子树仍会把
        # service/cache 与书目边界绕乱，必须拒绝。
        if bases[0] != bases[1] \
                and (bases[0] in bases[1].parents or bases[1] in bases[0].parents):
            raise ValueError(
                '两本 current corpus 的 base 可完全相同或互不重叠；'
                '不得为祖先/子孙目录')
        return self


def _read_current(rel: str, path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        disk = json.loads(path.read_text(encoding='utf-8'))
        payload = decode_artifact(rel, disk).payload
        return model.model_validate(payload)
    except Exception as exc:
        raise ValueError(f'{path} 不是当前 {model.__name__} schema：{exc}') from exc


def _empty_cache(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f'current corpus 专用 cache 必须不存在或为空：{path}')


def _source_book(book: BookSpec) -> dict[str, Any]:
    assert book.source is not None
    source = book.source.resolve()
    if not source.is_file():
        raise ValueError(f'原文不存在：{source}')
    sid, title, _platform = paths.derive_source_meta(source.name)
    if sid != book.sid:
        raise ValueError(f'{source.name} 派生 sid={sid!r}，与 spec 的 {book.sid!r} 不同')
    base = book.base.resolve()
    if base.exists() and (not base.is_dir() or any(base.iterdir())):
        raise ValueError(f'source-only 隔离 base 必须不存在或为空：{base}')
    removal = remove_bytes(source.read_bytes(), original_path=str(source), code='preview000')
    partition = partition_text(removal.clean_text)
    novel_dir = base / paths.novel_dirname(title, sid)
    return {
        'sid': sid,
        'title': title,
        'base': base,
        'novel_dir': novel_dir,
        'input_kind': 'source',
        'source': source,
        'normalized_source_sha1': removal.report.normalized_source_sha1,
        'clean_sha1': removal.report.clean_sha1,
        # ⚠ 零命中不是「可自动批准」：那条路 2026-08-21 停产，取值 `auto_zero_hit`
        #   已于 2026-08-22 随死值一并删除。摄入之后恒 pending，只有人能批。
        'stage0_gate': '必须先由人工 actor/reason 批准，预览不替代该决定',
        'main_text': partition.main_text,
        'source_hook': partition.hook_copy.text,
        'chapters': len(partition.main_text.root),
    }


def _stage1_book(book: BookSpec) -> dict[str, Any]:
    base = book.base.resolve()
    try:
        novel_dir = paths.resolve_novel_dir(base, book.sid)
    except Exception as exc:
        raise ValueError(f'stage1 输入找不到唯一书目录：{exc}') from exc
    unexpected = sorted(
        path.name for path in novel_dir.iterdir()
        if path.name not in {'stage0', 'stage1'}
    )
    if unexpected:
        raise ValueError(
            f'{novel_dir} 必须只含 stage0/1；发现下游或未知目录/文件 {unexpected}')
    revisions = _read_current(
        STAGE0_REVISIONS, novel_dir / STAGE0_REVISIONS, Stage0Revisions)
    assert isinstance(revisions, Stage0Revisions)
    stage0_revisions.verify_chain(revisions)
    report = stage0_revisions.snapshot_of(revisions)
    if report.approval.status != 'approved':
        raise ValueError(
            f'{novel_dir}: Stage0 状态 {report.approval.status!r}，不允许预览付费重建')
    clean_path = novel_dir / STAGE0_CLEAN_TEXT
    try:
        clean = clean_path.read_text(encoding='utf-8')
    except Exception as exc:
        raise ValueError(f'{clean_path} 不可读：{exc}') from exc
    clean_sha1 = prov.content_sha1(clean.encode('utf-8'))
    if clean_sha1 != report.clean_sha1:
        raise ValueError('Stage0 clean_text 与已批准 current revision 不一致')
    main = _read_current(STAGE1_MAIN_TEXT, novel_dir / STAGE1_MAIN_TEXT, MainText)
    hook = _read_current(STAGE1_HOOK_COPY, novel_dir / STAGE1_HOOK_COPY, HookCopy)
    assert isinstance(main, MainText)
    assert isinstance(hook, HookCopy)
    audit = audit_surface(
        clean_text=clean,
        stage0=report,
        main_text=main,
        hook_copy=hook,
    )
    if not audit.input_current or not audit.main_current or not audit.hook_current:
        raise ValueError('Stage1 两份材料不是当前已批准 Stage0 正文的确定性分章结果')
    if audit.hard_findings:
        raise ValueError('Stage1 当前材料未通过 HARD 审计：' +
                         '；'.join(item.detail for item in audit.hard_findings[:6]))
    return {
        'sid': book.sid,
        'title': paths.title_of(novel_dir),
        'base': base,
        'novel_dir': novel_dir,
        'input_kind': 'stage1',
        'source': None,
        'normalized_source_sha1': report.normalized_source_sha1,
        'clean_sha1': report.clean_sha1,
        'stage0_gate': f'已批准（by={report.approval.by}）',
        'main_text': main,
        'source_hook': hook.text,
        'chapters': len(main.root),
    }


def inspect_books(spec: PreviewSpec) -> list[dict[str, Any]]:
    books = [(_source_book(book) if book.input_kind == 'source' else _stage1_book(book))
             for book in spec.books]
    got = tuple(sorted(book['chapters'] for book in books))
    if got != EXPECTED_CHAPTERS:
        raise ValueError(f'current corpus 章数必须恰好是 {EXPECTED_CHAPTERS}，实得 {got}')
    for book in books:
        cache = (book['base'] / '_service' / 'current_schema_corpus_cache'
                 / book['sid'])
        _empty_cache(cache)
        book['cache'] = cache
    return books


def _schema_targets(node: str) -> list[str]:
    out: list[str] = []
    for template in NODES[node].produces:
        rel = template.format(nn=1, n=1)
        spec = artifact_spec(rel)
        out.append(f'{template}:v{spec.current_version}' if spec else f'{template}:bytes')
    return out


def _logical_topology(node: str, chapters: int, rung_count: int,
                      model_count: int) -> dict[str, Any]:
    # ⚠ wire 上限按 `MAX_SAMPLES_PER_RUNG` 放大：答案流丢失（content 空 + stop）时
    #   同一配置先重采一次（`novel/llm/client.py`），随后 Flash 才切 Pro；每个模型
    #   配置最坏烧两采。
    #   与档数一样，这个乘数从生产落点 import，不写死。
    base = 1
    conditional = 0
    conditional_wire = 0
    condition: str | None = None
    if node == 'stage2.blueprint':
        # 当前首次抽取每章一个逻辑请求；下面仍计入内容重采与供应商恢复上限。
        base = chapters
    elif node == 'stage4.design':
        # 对外仍是一个付费节点、一次 argv dispatch 与一次授权；节点内部的
        # 真实逻辑拓扑是「一份整书策略 + 每章一个私有 session」。预览必须
        # 把这 N+1 份分别计入，否则会把稳定性拆分误报成仍然只有一次请求。
        base = 1 + chapters
    elif node == 'stage5.write':
        # 收缩臂整臂下线后本层只剩逐章一次调用；超上限只落 audit，0 次调用。
        base = chapters
    elif node == 'stage6.hook':
        # 本预览强制空缓存：首次生成占一轮，全部槽位共用其余内容预算。
        # 同槽持续改善可继续修订；不能把书级上限再乘槽位数。
        lifecycle = NODES[node].lifecycle
        assert lifecycle is not None
        conditional = max(0, lifecycle.executor_content_limit - base)
        conditional_wire = (conditional * len(HOOK_RETRY_RUNGS) * model_count
                            * MAX_SAMPLES_PER_RUNG)
        condition = (
            f'整书共用最多 {lifecycle.executor_content_limit} 次内容调用；'
            f'冷缓存首次生成占 {base} 次，全部槽位共用其余 {conditional} 次 '
            'low/no-thinking 定向修订；同槽 HARD 严格下降时可继续，'
            '格式纠错也占同一预算，无进展提前停止')
    base_wire = base * rung_count * model_count * MAX_SAMPLES_PER_RUNG
    return {
        'base_logical_requests': base,
        'conditional_logical_requests': conditional,
        'logical_request_hard_limit': base + conditional,
        # ⚠ 输出键保留旧名以便既有只读预览 JSON 可继续比较；这里的 ladder 自
        # 2026-08-24 起实际指「单配置采样 × Flash→Pro 模型恢复链」，不再含 effort 降级。
        'base_ladder_wire_hard_limit': base_wire,
        'conditional_wire_hard_limit': conditional_wire,
        'wire_attempt_hard_limit': base_wire + conditional_wire,
        'conditional_reason': condition,
    }


def _request_envelopes(node: str, models: tuple[str, ...],
                       rungs: tuple[tuple[str, bool], ...]) -> list[dict[str, Any]]:
    if node == 'stage4.design':
        return [{
            'kind': kind,
            'max_tokens': max_tokens,
            'models': list(models),
            'rungs': [list(row) for row in rungs],
        } for kind, max_tokens in (
            ('book_strategy', BLIND_BOOK_STRATEGY_MAX_TOKENS),
            ('chapter_design', BLIND_CHAPTER_DESIGN_MAX_TOKENS),
        )]
    normal_max = {
        'stage2.characters': stage2_characters.MAX_TOKENS,
        'stage2.global': stage2_extract.GLOBAL_MAX_TOKENS,
        'stage2.blueprint': stage2_extract.BLUEPRINT_MAX_TOKENS,
        'stage4.plan': EVIDENCE_BIND_MAX_TOKENS,
        'stage5.write': stage5_write.MAX_TOKENS,
        'stage6.hook': HOOK_MAX_TOKENS,
        'stage7.rename': RENAME_MAX_TOKENS,
    }[node]
    rows = [{
        'kind': 'default',
        'max_tokens': normal_max,
        'models': list(models),
        'rungs': [list(row) for row in rungs],
    }]
    if node == 'stage6.hook':
        rows.append({
            'kind': 'conditional_candidate_retry',
            'max_tokens': HOOK_RETRY_MAX_TOKENS,
            'models': list(models),
            'rungs': [list(row) for row in HOOK_RETRY_RUNGS],
        })
    return rows


def _dispatch(book: dict[str, Any], node: str) -> dict[str, Any]:
    effort = NODES[node].effort
    rungs = rungs_from_effort(effort)
    primary_model = NODES[node].model or FIXED_MODEL
    models = models_for(primary_model)
    topology = _logical_topology(
        node, book['chapters'], len(rungs), len(models))
    flags: list[str] = []
    argv = build_argv(node, book['sid'], flags, base=book['base'])
    ability = SEMANTIC_NODES.get(node)
    return {
        'sid': book['sid'],
        'node': node,
        'production_cli_argv': argv,
        'production_cli_command': subprocess.list2cmdline(argv),
        'frozen_environment': {
            'DEEPSEEK_MODEL': FIXED_MODEL,
            'DEEPSEEK_THINKING': FIXED_THINKING,
            'DEEPSEEK_MAX_RETRIES': str(FIXED_MAX_RETRIES),
            'NOVEL_CACHE_DIR': str(book['cache']),
        },
        'effort_start': effort or 'high',
        'primary_model': primary_model,
        'models': list(models),
        'rungs': [list(row) for row in rungs],
        'retry': FIXED_MAX_RETRIES,
        'cache_must_start_empty': True,
        'semantic_ability': ability,
        'semantic_gate_behavior': (
            '照常双盲调用，但合并决议恒是带证据的 UNVER：'
            '本仓没有自动放行门，必须人工核验，绝不冒充自动 PASS'
            if ability else None),
        'human_gate_dependencies': list(PREREQUISITES[node]),
        'schema_targets': _schema_targets(node),
        'request_envelopes': _request_envelopes(node, models, rungs),
        **topology,
    }


TokenCounter = Callable[[ChatRequest], int]


def _characters_token_estimate(
    book: dict[str, Any], counter: TokenCounter = count_input_tokens,
) -> dict[str, Any]:
    main = [row.model_dump(by_alias=True, mode='json') for row in book['main_text'].root]
    system = stage2_characters.SYSTEM
    user = stage2_characters.build_user(book['title'], GlobalEvidenceCandidateTable.build(main))
    rungs = rungs_from_effort(NODES['stage2.characters'].effort)
    models = models_for(NODES['stage2.characters'].model or FIXED_MODEL)
    counts: list[dict[str, Any]] = []
    try:
        for model in models:
            for effort, thinking in rungs:
                request = ChatRequest(
                    model=model,
                    system=system,
                    user=user,
                    reasoning_effort=effort,
                    thinking=thinking,
                    json_mode=True,
                    max_tokens=stage2_characters.MAX_TOKENS,
                )
                counts.append({
                    'model': model,
                    'effort': effort,
                    'thinking': thinking,
                    'input_tokens': counter(request),
                })
    except Exception as exc:
        return {
            'status': 'unknown',
            'reason': f'{type(exc).__name__}: {exc}',
            'tokenizer': TOKENIZER_LABEL,
            'prompt_sha256': hashlib.sha256(
                (system + '\0' + user).encode('utf-8')).hexdigest(),
        }
    wire_count = len(models) * len(rungs) * MAX_SAMPLES_PER_RUNG
    return {
        'status': 'exact_first_request_dynamic_recovery_bounded',
        'tokenizer': TOKENIZER_LABEL,
        'prompt_sha256': hashlib.sha256(
            (system + '\0' + user).encode('utf-8')).hexdigest(),
        'input_tokens_by_rung': counts,
        'first_success_input_tokens': counts[0]['input_tokens'],
        # agent 路径的后三采会消费运行时才知道的失败候选/诊断，开跑前不能把
        # 「原 prompt × wire」冒充精确输入。旧键保留，但诚实写 unknown；硬上限
        # 由每个 wire 的上下文闸给出，仍可用于付费边界确认。
        'exhausted_ladder_input_tokens': 'unknown_dynamic_recovery_prompts',
        'exhausted_ladder_input_token_hard_ceiling': sum(
            model_budget(model).context_limit - stage2_characters.MAX_TOKENS
            for model in models for _rung in rungs
        ) * MAX_SAMPLES_PER_RUNG,
        'completion_tokens_lower_bound': 0,
        'completion_tokens_ladder_upper_bound': (
            wire_count * stage2_characters.MAX_TOKENS),
        'completion_includes_reasoning': True,
    }


def _numeric_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [row.get(key) for row in rows]
    known = [value for value in values
             if isinstance(value, int) and not isinstance(value, bool)]
    return {
        'known_rows': len(known),
        'null_or_invalid_rows': len(values) - len(known),
        'min': min(known) if known else None,
        'max': max(known) if known else None,
        'sum': sum(known) if known else None,
    }


def historical_observations(
    ledger: Path | None, sids: set[str],
) -> dict[str, Any]:
    if ledger is None:
        return {
            'available': False,
            'reason': 'spec 未提供 historical_ledger；历史 token 观察为 unknown',
        }
    path = ledger.resolve()
    if not path.is_file():
        return {
            'available': False,
            'path': str(path),
            'reason': 'historical_ledger 不存在；不猜历史 token',
        }
    raw = path.read_bytes()
    malformed = 0
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for line in raw.decode('utf-8', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue
        sid, node = row.get('sid'), row.get('node')
        if sid in sids and node in PAID_NODES:
            grouped[(str(sid), str(node))].append(row)
    groups: list[dict[str, Any]] = []
    for (sid, node), rows in sorted(grouped.items()):
        prompt = _numeric_stats(rows, 'prompt_tokens')
        completion = _numeric_stats(rows, 'completion_tokens')
        reasoning = _numeric_stats(rows, 'reasoning_tokens')
        prompt_sum = prompt['sum']
        completion_sum = completion['sum']
        groups.append({
            'sid': sid,
            'node': node,
            'rows': len(rows),
            'cached_rows': sum(row.get('cached') is True for row in rows),
            'wire_rows': sum(row.get('cached') is False for row in rows),
            'error_rows': sum(row.get('outcome') == 'error' for row in rows),
            'prompt_tokens': prompt,
            'completion_tokens': completion,
            'reasoning_tokens_diagnostic_subset': reasoning,
            'prompt_plus_completion_observed_sum': (
                prompt_sum + completion_sum
                if prompt_sum is not None and completion_sum is not None else None),
            'completion_includes_reasoning': True,
        })
    return {
        'available': True,
        'path': str(path),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'malformed_rows_skipped': malformed,
        'groups': groups,
        'interpretation': (
            '旧 ledger 仅是观察：含缓存回放、旧 schema/旧 prompt 和可能的重复轮；'
            'completion_tokens 已含 reasoning_tokens，统计没有再次相加。'),
        'known_contract_drift': [
            '最终 stage6.semantic 为 A/B 两条；旧账可能只有单 judge。',
            '最终 Stage5 已无自动收缩路径；旧账里的 trim 调用不能外推到这一次。',
        ],
    }


def _aggregate(dispatches: list[dict[str, Any]]) -> dict[str, int]:
    # ⚠ 局部名曾叫 `profile_*`——那个名字说的是「依赖哪一份 evaluator profile」，
    #   而 profile 那套装置已整批下线（见本文件开头）。改名只改了常量、没改用它的这一段，
    #   于是一个退役概念以现役身份留在了唯一的成本聚合处。⇒ 对齐到已有的输出键名。
    semantic_nodes = set(SEMANTIC_NODES)
    mandatory = [row for row in dispatches if row['node'] not in semantic_nodes]
    semantic_rows = [row for row in dispatches if row['node'] in semantic_nodes]
    hooks = [row for row in dispatches if row['node'] == 'stage6.hook']
    base_logical = sum(row['base_logical_requests'] for row in mandatory)
    base_wire = sum(row['base_ladder_wire_hard_limit'] for row in mandatory)
    semantic_logical = sum(row['base_logical_requests'] for row in semantic_rows)
    semantic_wire = sum(row['base_ladder_wire_hard_limit'] for row in semantic_rows)
    hook_retry_logical = sum(row['conditional_logical_requests'] for row in hooks)
    hook_retry_wire = sum(row['conditional_wire_hard_limit'] for row in hooks)
    full_logical = (
        base_logical + semantic_logical + hook_retry_logical)
    full_wire = (
        base_wire + semantic_wire + hook_retry_wire)
    return {
        'production_cli_dispatches': len(dispatches),
        'base_nonsemantic_logical_requests': base_logical,
        'base_nonsemantic_ladder_wire_hard_limit': base_wire,
        'semantic_logical': semantic_logical,
        'semantic_wire_hard_limit': semantic_wire,
        'stage6_conditional_candidate_retry_logical': hook_retry_logical,
        'stage6_conditional_candidate_retry_wire_hard_limit': hook_retry_wire,
        'full_conditional_logical_hard_limit': full_logical,
        'full_conditional_wire_hard_limit': full_wire,
        'full_conditional_context_token_hard_ceiling': full_wire * CONTEXT_LIMIT,
    }


# ⚠⚠ 这里曾有 `_calibration_evaluation()`：它冻结「800 case × 2 judge × 3 run = 4800 次」
#     那场标定战役的拓扑与 `tools/restricted_evaluator.py` 的 argv 模板。整套 evaluator
#     profile 装置（含那个 runner）已被裁决删除，因此这段连同 `scope_accounting` 里的
#     三块合并数一并移除——**留着它等于给一个不存在的付费计划报预算**。


def workflow_role_budget(chapters: int) -> dict[str, Any]:
    """当前生命周期的内容上限；B 只有真实歧义章开模型工作单元。

    Commander 审阅席位已随角色退役（2026-09-15）：这里只剩 executor 预算行，
    审批闸一律人工，不产生任何模型审阅工作单元。
    """
    rows: list[dict[str, Any]] = []
    for node, spec in NODES.items():
        life = spec.lifecycle
        if life is None:
            raise ValueError(f'{node} 缺少职责登记')
        units = (chapters if life.unit_kind in {'chapter', 'ambiguous_chapter'} else
                 chapters + 1 if life.unit_kind == 'strategy_and_chapter' else
                 2 if life.unit_kind == 'judge' else 1)
        minimum = 0 if life.unit_kind == 'ambiguous_chapter' else units
        if life.executor_content_limit:
            rows.append({'node': node, 'role': 'executor', 'minimum_units': minimum,
                         'maximum_units': units, 'content_limit_per_unit': life.executor_content_limit,
                         'content_hard_limit': units * life.executor_content_limit})
    return {'contract': WORKFLOW_ROLE_CONTRACT, 'scope': 'new_clean_base', 'units': rows,
            'content_hard_limit': sum(row['content_hard_limit'] for row in rows),
            'network_attempts': '独立有界并逐次留账；由实际运行 profile/transport 冻结',
            'token_estimate': None, 'cost_estimate': None}


def build_plan(
    spec: PreviewSpec,
    *,
    token_counter: TokenCounter = count_input_tokens,
) -> dict[str, Any]:
    books = inspect_books(spec)
    dispatches = [
        _dispatch(book, node)
        for book in books
        for node in PAID_NODES
    ]
    aggregate = _aggregate(dispatches)
    character_tokens = {
        book['sid']: _characters_token_estimate(book, token_counter)
        for book in books
    }
    global_tokens = {
        book['sid']: {'status': 'unknown', 'reason': (
            '干净输出根尚未生成本次人物登记；知识请求必须消费其真实投影，不能用空表或旧人物表估算。')}
        for book in books
    }
    rendered_books = [{
        key: (str(value) if isinstance(value, Path) else value)
        for key, value in book.items()
        if key != 'main_text'
    } for book in books]
    return {
        'kind': 'current-schema-corpus-new-paid-preview-v1',
        'read_only': True,
        'has_execution_entry': False,
        'authorization': 'not_granted; regenerate after human gates, then request confirmation',
        'model': FIXED_MODEL,
        'thinking': FIXED_THINKING,
        'retry': FIXED_MAX_RETRIES,
        'books': rendered_books,
        'dispatches': dispatches,
        'aggregate': aggregate,
        'workflow_role_budget': {
            book['sid']: workflow_role_budget(book['chapters']) for book in books},
        'transport_estimate_scope': (
            '既有 legacy CLI 首批请求观察；不含已退役的历史审阅轮，不能作为新工作流总付费上限。'
            '现役内容预算以 workflow_role_budget 为准；总 wire/token/费用必须由冻结运行 profile 另行预览。'),
        'token_estimate': {
            'stage2_characters_exact_before_generation': character_tokens,
            'stage2_global_before_generation': global_tokens,
            'downstream_prompt_tokens': 'unknown',
            'downstream_unknown_reason': (
                'Stage2 global 之后的 prompt 依赖尚未生成的当前-schema artifacts；'
                '字符比例与旧 schema ledger 都不能替代精确 tokenizer 计数。'),
            'hard_ceiling_basis': (
                f'每个 wire 在发出前受已登记模型 {CONTEXT_LIMIT} token context 闸约束；'
                '总上界=full conditional wire × context limit，不是预计实付。'),
            'price': 'unknown（本工具不含费率/单价，也不猜）',
            'completion_includes_reasoning': True,
        },
        'historical_observations': historical_observations(
            spec.historical_ledger, {book['sid'] for book in books}),
        'existing_authorized_boundary': {
            **EXISTING_AUTHORIZED,
            'explanation': (
                f'这 10 条 FC08/12/13 实验 run 与本次 {len(dispatches)} 条 canonical corpus dispatch '
                '各列一次；新 totals 不含已有实验，空 base/cache 也不跨范围复用产物。'),
            'combined_wire_hard_ceiling_once_each': (
                EXISTING_AUTHORIZED['external_hard_limit']
                + aggregate['full_conditional_wire_hard_limit']),
        },
        'scope_accounting': {
            'existing_fc08_fc13_wire_hard_limit': (
                EXISTING_AUTHORIZED['external_hard_limit']),
            'current_corpus_wire_hard_limit': (
                aggregate['full_conditional_wire_hard_limit']),
            'existing_fc08_fc13_overlaps_current_corpus': False,
            'existing_plus_current_once_each': (
                EXISTING_AUTHORIZED['external_hard_limit']
                + aggregate['full_conditional_wire_hard_limit']),
            'authorization_note': (
                '588 是既有隔离实验上限，'
                f'{aggregate["full_conditional_wire_hard_limit"]} 是 canonical '
                'current corpus 上限，'
                '二者互不复用且各计一次；合并数不是新增授权。'),
        },
    }


def render(plan: dict[str, Any]) -> str:
    aggregate = plan['aggregate']
    accounting = plan['scope_accounting']
    lines = [
        '# current-schema corpus 新增付费范围（只读冻结预览）',
        '',
        f'model={plan["model"]} thinking={plan["thinking"]} retry={plan["retry"]}',
        '授权状态：未授权执行；必须在人工 gate 完成后重生成并再次确认。',
        plan['transport_estimate_scope'],
        '现役新工作流内容预算：' + json.dumps(plan['workflow_role_budget'], ensure_ascii=False),
        '',
        '## 输入',
    ]
    for book in plan['books']:
        lines.append(
            f'- sid={book["sid"]} chapters={book["chapters"]} '
            f'kind={book["input_kind"]} base={book["base"]} '
            f'stage0={book["stage0_gate"]}')
    # ⚠ 条数由 dispatch 面现算，不写死：节点表增删时这一行不许还在报旧数
    #   （`stage5.semantic` 下线时它就是这么漂的）。
    total = len(plan['dispatches'])
    lines.extend(['', f'## {total} 条 production CLI dispatch'])
    for index, row in enumerate(plan['dispatches'], 1):
        lines.extend([
            '',
            f'[{index:02d}/{total:02d}] {row["sid"]} · {row["node"]}',
            f'  prerequisite={" / ".join(row["human_gate_dependencies"])}',
            f'  model_chain={"→".join(row["models"])} thinking={plan["thinking"]} '
            f'effort={row["effort_start"]} retry=0',
            f'  cache={row["frozen_environment"]["NOVEL_CACHE_DIR"]}（必须为空）',
            f'  logical=基础 {row["base_logical_requests"]} + 条件 '
            f'{row["conditional_logical_requests"]}；hard={row["logical_request_hard_limit"]}',
            f'  wire_recovery=基础 {row["base_ladder_wire_hard_limit"]} + 条件 '
            f'{row["conditional_wire_hard_limit"]}；hard={row["wire_attempt_hard_limit"]}',
        ])
        if row['conditional_reason']:
            lines.append(f'  conditional={row["conditional_reason"]}')
        if row['semantic_ability']:
            lines.append(
                f'  semantic_ability={row["semantic_ability"]}；'
                f'{row["semantic_gate_behavior"]}')
        lines.extend([
            '  frozen_environment=' + json.dumps(
                row['frozen_environment'], ensure_ascii=False, sort_keys=True),
            f'  production_cli_argv={row["production_cli_command"]}',
        ])
    lines.extend([
        '',
        '## 新 corpus 小计（不含已有 FC08/12/13）',
        f'基础非语义：logical={aggregate["base_nonsemantic_logical_requests"]} / '
        f'wire 上限={aggregate["base_nonsemantic_ladder_wire_hard_limit"]}',
        f'Stage6 条件候选定向重试：logical='
        f'{aggregate["stage6_conditional_candidate_retry_logical"]} / wire 上限='
        f'{aggregate["stage6_conditional_candidate_retry_wire_hard_limit"]}',
        f'语义审判（恒产 UNVER）：logical='
        f'{aggregate["semantic_logical"]} / '
        f'wire 上限={aggregate["semantic_wire_hard_limit"]}',
        f'全条件硬上限：logical={aggregate["full_conditional_logical_hard_limit"]} / '
        f'wire={aggregate["full_conditional_wire_hard_limit"]} / context-token ceiling='
        f'{aggregate["full_conditional_context_token_hard_ceiling"]:,}',
        '以上均为可达硬上限，不是预计实付。',
        '',
        '## token 口径',
    ])
    for node, key in (
            ('stage2.characters', 'stage2_characters_exact_before_generation'),
            ('stage2.global', 'stage2_global_before_generation')):
        for sid, estimate in plan['token_estimate'][key].items():
            if estimate['status'].startswith('exact'):
                lines.append(
                    f'- {sid}/{node}：首档 input={estimate["first_success_input_tokens"]}；'
                    f'走完整模型恢复链 input={estimate["exhausted_ladder_input_tokens"]}；'
                    f'input hard ceiling={estimate["exhausted_ladder_input_token_hard_ceiling"]}；'
                    f'completion=0..{estimate["completion_tokens_ladder_upper_bound"]} '
                    '（已含 reasoning）')
            else:
                lines.append(f'- {sid}/{node}：unknown（{estimate["reason"]}）')
    lines.extend([
        '- 其余 prompt token：unknown（依赖尚未生成的当前 schema 上游）。',
        '- 历史 ledger 仅作带 SHA256 的旧观察；不拿它冒充新 prompt 预测。',
        '- price：unknown；未猜单价。completion 已含 reasoning，不重复相加。',
        '',
        '## 与已有 10 run 的边界',
        f'已有 FC08/12/13：10 CLI run，wire 硬上限 '
        f'{plan["existing_authorized_boundary"]["external_hard_limit"]}；'
        '未计入上面的新 corpus 小计。',
        f'两块各计一次的合并硬上限：'
        f'{plan["existing_authorized_boundary"]["combined_wire_hard_ceiling_once_each"]}。',
        f'重叠判断：588 与 {aggregate["full_conditional_wire_hard_limit"]} 不重叠；'
        '实验与 canonical corpus 使用隔离 base/cache，'
        '没有把同一调用计两次。',
        '',
        f'范围合计：existing+current 各一次={accounting["existing_plus_current_once_each"]}。',
        '⚠ 这里曾还有第三块「最低 evaluator 标定 campaign」。整套 evaluator profile '
        '装置已被裁决删除，那场战役不会发生，因此它不再作为一个待解锁的付费范围出现'
        '——给一个不存在的计划报预算，比不报更容易被当成「还差这一步」。',
        '',
        '仅预览：本工具没有执行入口，未发请求、未创建 cache/base、未写产物。',
    ])
    return '\n'.join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, help='本地只读 JSON spec')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        spec = PreviewSpec.model_validate_json(
            Path(args.spec).read_text(encoding='utf-8'))
        plan = build_plan(spec)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(render(plan))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
