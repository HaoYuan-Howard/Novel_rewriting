"""三层当前契约的薄编排；业务判断只调用各层纯语义编译边界。

本模块通过现役 repository 与 PaidRequestSession 读写和外发。中间观察是已购
恢复材料；SourceStory 与 TargetStory 分别是跨层的唯一正式故事真相。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Final, Mapping, NamedTuple

from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from contracts.invariants import (
    NODES, BOOK_CONTENT_CALL_CEILING_DEFAULT, LLM_MAX_OUTPUT_TOKENS,
    PIPELINE_BOOK_CONCURRENCY, PIPELINE_DEFAULT_CONCURRENCY,
    PIPELINE_PARALLEL_NODES, PIPELINE_PROVIDER_CONCURRENCY,
)
from contracts.story_paths import *
from contracts.schema.orchestration import LegacyLlmProfile
from contracts.schema.workflow import STR06_RUNTIME_CONFIG, OwnerIssueV1
from contracts.schema.story_prose import ChapterBody, HookBody, NeedsDesign, NeedsScope
from contracts.schema.target_story import ChapterPacket, TargetStory
from novel import paths, prov
from novel.config import Config, load_config
from novel.core.text import count_non_whitespace_chars
from novel.llm.paid_requests import (
    PaidRequestSession, PurchaseRequired, ValidationStop, digest, init_or_resume_candidate_workflow, semantic_owner_unit,
)
from novel.llm.story_requests import MATERIAL_RULES, RevisionSeed, SemanticResult, request_json, replay_semantic_trace, strict_json
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel.stages.stage2 import story as source_engine
from novel.stages.stage4 import story as target_engine
from novel.stages.stage5 import story as prose_engine
from novel.stages.stage7 import story as assembly_engine
from novel.store import PublishItem, Str06Repository
from novel.work_units import read_book_budget, require_workflow
from novel.errors import ContextBudgetRefused, UnsealedArtifact

SYSTEM = '按当前任务的独立协议理解或生成小说材料。只使用给定源材料和正式输入；不要补猜缺失事实。'
NODE_ORDER = tuple(node for node in NODES if node.startswith('str06.'))
THROUGH_NODE = {'stage2': 'str06.stage2.compile', 'stage4': 'str06.stage4.hook_compile',
                'stage5': 'str06.stage5.body', 'stage7': 'str06.stage7.assemble'}


@dataclass
class ChainContext:
    repository: Any
    ask: Callable[..., SemanticResult]
    chapters: list[dict]
    hook_copy: str = ''
    budget: int = 11000
    book_title: str = ''
    length_band: tuple[float, float] = (.6, 1.3)
    provider: str = 'unknown'
    model: str = 'unknown'
    concurrency: int = PIPELINE_DEFAULT_CONCURRENCY
    node_concurrency: dict[str, int] = field(default_factory=dict)
    body_chapters: tuple[int, ...] | None = None
    source_identity: str | None = None

    @property
    def index(self):
        return source_engine.build_source_index(self.chapters, self.hook_copy, source_identity=self.source_identity)

    def pool_workers(self, node: str, count: int) -> int:
        return max(1, min(count, self.node_concurrency.get(node, self.concurrency)))


def _raw_trace_steps(trace):
    for step in trace:
        if step['kind'] == 'changes':
            yield from _raw_trace_steps(step['value'])
        else:
            yield step


def _verified_semantic_candidate(ctx, owner, saved):
    """重建当前根原始已购回答；自报wire/hash或换根响应不能成为修订基线。"""
    from novel.llm.paid_requests import AdoptionRef
    from novel.work_units import readonly_budget
    if saved['contract_version'] != 'story-v1':
        raise ValueError('修订基线版本不是当前契约')
    refs, actual = [], {}
    for row in saved['refs']:
        ref = AdoptionRef(**row)
        if ref.owner != owner or ref.sid != paths.derive_source_meta(ctx.repository.root.name)[0]:
            raise ValueError('修订基线的真实回答不属于当前根owner')
        node, scope = semantic_owner_unit(owner)
        unit = readonly_budget(ctx.repository.root.parent, ref.sid, ref.unit_id)
        if unit.identity['node'] != node or unit.identity['scope'] != scope:
            raise ValueError('修订基线工作单元的节点或scope不匹配')
        turn = unit.turn(ref.turn_id)
        attempts = [item for item in turn.attempts if item.attempt_id == ref.attempt_id]
        if (unit.unit_id != ref.unit_id or turn.request_sha256 != ref.request_sha256
                or len(attempts) != 1 or attempts[0].response_sha256 != ref.response_sha256):
            raise ValueError('修订基线的请求或响应身份不闭合')
        payload = unit.attempt_request_payload(ref.turn_id, ref.attempt_id)
        messages = payload['request_payload']['messages']
        system = next((item['content'] for item in messages if item['role'] == 'system'), '')
        user = next(item['content'] for item in reversed(messages) if item['role'] == 'user')
        if ref.prompts != {'system': system, 'user': user}:
            raise ValueError('修订基线未绑定真实送达请求字节')
        response = payload['response']
        actual[(ref.turn_id, ref.attempt_id)] = (dict(ref._asdict()),
            strict_json(response['content'] if (response.get('content') or '').strip() else response.get('reasoning') or ''))
        refs.append(ref)
    for step in _raw_trace_steps(saved['trace']):
        ref = step['ref']
        if actual.get((ref['turn_id'], ref['attempt_id'])) != (ref, step['value']):
            raise ValueError('修订trace不是对应真实响应的原始JSON')
    rebuilt = replay_semantic_trace(saved['trace'])
    if rebuilt != saved['wire']:
        raise ValueError('修订基线不能由耐久原响应和授权补丁完整重建')
    last = saved['trace'][-1]['ref']
    if (last['request_sha256'] != saved['request_sha256']
            or not last['prompts']['user'].startswith(saved['owner_prompt'])):
        raise ValueError('修订基线与原owner请求身份不一致')
    return tuple(refs)


def _seed_dump(seed):
    return {'kind': seed.kind, 'baseline': seed.baseline, 'paths': seed.paths,
        'append_paths': seed.append_paths, 'remove_paths': seed.remove_paths,
        'refs': [dict(ref._asdict()) for ref in seed.refs], 'trace': seed.trace}


def _seed_load(data):
    from novel.llm.paid_requests import AdoptionRef
    return RevisionSeed(kind=data['kind'], baseline=data['baseline'],
        paths=tuple(tuple(path) for path in data['paths']), append_paths=tuple(tuple(path) for path in data['append_paths']),
        remove_paths=tuple(tuple(path) for path in data['remove_paths']),
        refs=tuple(AdoptionRef(**row) for row in data['refs']), trace=tuple(data['trace']))


def _frozen_seed(ctx, owner, frozen):
    refs = _verified_semantic_candidate(ctx, owner, frozen['baseline_record'])
    seed = _seed_load(frozen['seed'])
    if (seed.baseline != frozen['baseline_record']['wire'] or seed.refs != refs
            or seed.trace != tuple(frozen['baseline_record']['trace'])):
        raise ValueError('冻结seed未绑定原始已购基线、真实引用与trace')
    marker = '\n【限定工作单元的修订】\n'
    control, _end = json.JSONDecoder().raw_decode(frozen['prompt'].rsplit(marker, 1)[1])
    if (control['kind'] != 'repair' or control['baseline'] != seed.baseline
            or control['baseline_sha256'] != digest(seed.baseline)
            or tuple(tuple(path) for path in control['paths']) != seed.paths
            or tuple(tuple(path) for path in control['append_paths']) != seed.append_paths
            or tuple(tuple(path) for path in control['remove_paths']) != seed.remove_paths):
        raise ValueError('冻结修订提示词未绑定同一基线与typed范围')
    return seed


def _owner_repair_scope(ctx, owner, issue, baseline):
    """只采typed路径；没有准确定位时停，不从message猜字段或授权整书改写。"""
    replace_paths, append_paths, remove_paths = [], [], []
    for row in issue.issues:
        context = row.context
        for raw in context.get('append_paths', []):
            path = tuple(raw)
            value = baseline
            for part in path:
                value = value[part]
            if not isinstance(value, (list, dict)):
                raise ValueError('typed append必须定位既有容器')
            append_paths.append(path)
        declared = context.get('paths') or ([context['path']] if context.get('path') else [])
        operation = context.get('operation')
        if (declared or context.get('append_paths')) and operation not in ('replace', 'append', 'remove'):
            raise ValueError('回派问题的typed operation未登记')
        for raw in declared:
            path = tuple(raw)
            value = baseline
            for part in path:
                if isinstance(part, bool):
                    raise ValueError('修订路径索引不接受布尔值')
                value = value[part]
            if operation == 'append':
                if not isinstance(value, (list, dict)):
                    raise ValueError('typed append必须定位既有容器')
                append_paths.append(path)
            elif operation == 'remove':
                parent = baseline
                for part in path[:-1]:
                    parent = parent[part]
                if not isinstance(parent, dict) or not isinstance(path[-1], str):
                    raise ValueError('typed remove只能定位字典语义键条目，不得压缩列表位置')
                remove_paths.append(path)
            elif isinstance(value, list):
                raise ValueError('整表修订没有既有条目定位；须显式扩大设计范围')
            else:
                replace_paths.append(path)
    if not replace_paths and not append_paths and not remove_paths:
        raise ValueError('回派问题没有明确可授权位置，须显式扩大设计范围')
    return (tuple(dict.fromkeys(replace_paths)), tuple(dict.fromkeys(append_paths)),
            tuple(dict.fromkeys(remove_paths)))


def _json_call(ctx, owner, prompt, compiler, *, index=None, max_chapter=None, system=SYSTEM):
    from novel.stages.owner_revision import open_issue_record
    base_prompt_sha256 = digest({'system': system, 'user': prompt})
    revision_seed = None
    issue = open_issue_record(ctx.repository, owner)
    if issue is not None:
        request_rel = f'runtime/revision_requests/{owner}/{issue.fingerprint}.json'
        try:
            frozen = ctx.repository.inspect(request_rel)
        except FileNotFoundError:
            saved = ctx.repository.inspect(f'runtime/semantic_candidates/{owner}.json')
            refs = _verified_semantic_candidate(ctx, owner, saved)
            try:
                from contracts.schema.workflow import OWNER_PRODUCT_RELS, chapter_owner_product_rel
                rel = OWNER_PRODUCT_RELS.get(owner) or chapter_owner_product_rel(owner)
                if rel is None or compiler(saved['wire']) != ctx.repository.inspect_sealed_model(rel):
                    raise ValueError('真实已购基线与问题所指的当前正式owner产物不同')
                scoped, appends, removes = _owner_repair_scope(ctx, owner, issue, saved['wire'])
                revision_seed = RevisionSeed(kind='repair', baseline=saved['wire'], paths=scoped,
                    refs=refs, trace=tuple(saved['trace']), append_paths=appends, remove_paths=removes)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                node, item = semantic_owner_unit(owner)
                raise ValidationStop(str(exc), code='revision_scope_required', node=node,
                    stage=node.split('.')[1], item=item, phase='accept') from exc
            frozen = {'kind': 'repair', 'contract_version': 'story-v1', 'fingerprint': issue.fingerprint,
                'against_sha256': issue.against_sha256, 'seed': _seed_dump(revision_seed),
                'baseline_record': saved,
                'prompt': prompt + '\n【限定工作单元的修订】\n' + json.dumps({
                    'kind': 'repair', 'baseline': saved['wire'], 'baseline_sha256': digest(saved['wire']),
                    'paths': scoped, 'append_paths': appends, 'remove_paths': removes,
                    'issues': [row.model_dump(mode='json') for row in issue.issues]}, ensure_ascii=False)
                    + '\n只返回 {"patches":[{"path":[授权路径],"value":替换值}]}'
                    + ('，另必填appends:[{"path":[授权容器],"value":追加列表或新键对象}]。' if appends else '。')
                    + ('另必填removes:[{"path":[授权字典语义键]}]。' if removes else '')
                    + '每个授权路径恰答一次；授权外保持原字节，禁止全量JSON、删除未授权条目或重排既有列表。'
                    + '修订须补齐所报真实剧情桥梁或必要决定，保持已冻结跨章边界；范围不足明确停止。'}
            ctx.repository.write(request_rel, frozen)
        if (frozen['kind'] != 'repair' or frozen['contract_version'] != 'story-v1'
                or frozen['fingerprint'] != issue.fingerprint or frozen['against_sha256'] != issue.against_sha256):
            raise ValueError('冻结修订请求的种类、问题或原产物身份不一致')
        revision_seed = _frozen_seed(ctx, owner, frozen)
        if (revision_seed.paths, revision_seed.append_paths, revision_seed.remove_paths) != _owner_repair_scope(
                ctx, owner, issue, revision_seed.baseline):
            raise ValueError('冻结补丁权限与当前typed问题范围不同')
        prompt = frozen['prompt']
    else:
        try:
            accepted = ctx.repository.inspect(f'runtime/semantic_candidates/{owner}.json')
        except FileNotFoundError:
            accepted = None
        if accepted is not None and accepted['base_prompt_sha256'] == base_prompt_sha256:
            # 原始输入未变时重放当前已接受的修订请求；不能把修订品覆盖成原缓存。
            prompt = accepted['owner_prompt']
            if accepted['kind'] == 'repair':
                _verified_semantic_candidate(ctx, owner, accepted)
                frozen = accepted['revision']
                revision_seed = _frozen_seed(ctx, owner, frozen)
                if prompt != frozen['prompt']:
                    raise ValueError('修订重放必须使用原冻结请求提示词')
    result = ctx.ask(owner, prompt, compiler, index=index, max_chapter=max_chapter, system=system,
                     revision_seed=revision_seed)
    ctx.repository.write(f'runtime/semantic_candidates/{owner}.json', {
        'kind': 'repair' if revision_seed is not None else 'result',
        'contract_version': 'story-v1', 'request_sha256': result.answer.request_sha256,
        'base_prompt_sha256': base_prompt_sha256, 'owner_prompt': prompt, 'wire': result.value,
        'refs': [dict(ref._asdict()) for ref in result.refs], 'trace': result.trace,
        **({'revision': frozen} if revision_seed is not None else {})})
    return result


def _publish_answer(ctx, rel, result, value):
    ctx.repository.write(rel, value, refs=result.refs)


def _observations(ctx):
    return {ch['chapter_ordinal']: ctx.repository.read_model(
        chapter_rel(CHAPTER_OBSERVATION, ch['chapter_ordinal'])) for ch in ctx.chapters}


def run_chapters(ctx):
    index = ctx.index
    def one(chapter):
        ordinal = chapter['chapter_ordinal']
        compiler = lambda wire: source_engine.compile_chapter(wire, index=index, chapter_ordinal=ordinal)
        result = _json_call(ctx, f'ch{ordinal:02d}', source_engine.chapter_prompt(index, ordinal),
                            compiler, index=index, max_chapter=ordinal)
        return ordinal, result, compiler(result.value)
    with ThreadPoolExecutor(max_workers=ctx.pool_workers('str06.stage2.chapters', len(ctx.chapters))) as pool:
        results = list(pool.map(one, ctx.chapters))
    for ordinal, result, observation in results:
        _publish_answer(ctx, chapter_rel(CHAPTER_OBSERVATION, ordinal), result, observation)
    return {'chapters': len(results)}


_BOOK_PENDING = 'runtime/source_repairs/book.json'
_BOOK_INTENT = 'runtime/source_repairs/book_request.json'
_BOOK_CANDIDATE = 'runtime/source_repairs/book_candidate.json'


def _chapter_snapshot(chapters):
    return {str(ch): value.model_dump(mode='json') for ch, value in chapters.items()}


def _restore_chapter_snapshot(value):
    from contracts.schema.source_story import ChapterObservation
    return {int(ch): ChapterObservation.model_validate(row) for ch, row in value.items()}


def _verified_book_trace(ctx, group, prompt):
    """冻结草稿必须能从本根耐久原响应重建，不能仅以自报hash证明来源。"""
    from novel.llm.paid_requests import AdoptionRef
    from novel.work_units import readonly_budget
    refs, actual = [], {}
    for row in group['refs']:
        ref = AdoptionRef(**row)
        if ref.owner != 'book' or ref.sid != paths.derive_source_meta(ctx.repository.root.name)[0]:
            raise ValueError('全书冻结候选的响应引用不属于当前根book owner')
        node, scope = semantic_owner_unit(ref.owner)
        unit = readonly_budget(ctx.repository.root.parent, ref.sid, ref.unit_id)
        if unit.identity['node'] != node or unit.identity['scope'] != scope:
            raise ValueError('全书冻结候选工作单元的节点或scope不匹配')
        turn = unit.turn(ref.turn_id)
        attempts = [item for item in turn.attempts if item.attempt_id == ref.attempt_id]
        if (unit.unit_id != ref.unit_id or turn.request_sha256 != ref.request_sha256
                or len(attempts) != 1 or attempts[0].response_sha256 != ref.response_sha256):
            raise ValueError('全书冻结候选的请求或响应摘要漂移')
        payload = unit.attempt_request_payload(ref.turn_id, ref.attempt_id)
        messages = payload['request_payload']['messages']
        system = next((item['content'] for item in messages if item['role'] == 'system'), '')
        user = next(item['content'] for item in reversed(messages) if item['role'] == 'user')
        if ref.prompts != {'system': system, 'user': user} or not user.startswith(prompt):
            raise ValueError('全书冻结候选不绑定声明的章观察或修订权限')
        response = payload['response']
        actual[(ref.turn_id, ref.attempt_id)] = (dict(ref._asdict()),
            strict_json(response['content'] if (response.get('content') or '').strip()
                        else response.get('reasoning') or ''))
        refs.append(ref)
    for step in group['trace']:
        ref = step['ref']
        matched = actual.get((ref['turn_id'], ref['attempt_id']))
        if matched is None or matched != (ref, step['value']):
            raise ValueError('全书冻结候选trace不等于耐久原响应')
    return replay_semantic_trace(group['trace']), tuple(refs)


def _load_book_pending(ctx):
    from contracts.schema.source_story import BookRepairScope
    if not ctx.repository.path(_BOOK_PENDING).is_file():
        if not ctx.repository.path(_BOOK_CANDIDATE).is_file():
            return None
        candidate, intent = ctx.repository.inspect(_BOOK_CANDIDATE), ctx.repository.inspect(_BOOK_INTENT)
        ctx.repository.write(_BOOK_PENDING, {
            'contract_version': 'story-v1', 'source_identity': intent['source_identity'],
            'content_sha256': intent['content_sha256'], 'status': 'waiting',
            'wire': candidate['wire'], 'wire_sha256': digest(candidate['wire']),
            'history': [{'kind': 'full', 'include_source': intent['include_source'],
                         'chapters': intent['chapters'], 'trace': candidate['trace'], 'refs': candidate['refs']}]})
    pending = ctx.repository.inspect(_BOOK_PENDING)
    if (pending['contract_version'] != 'story-v1' or pending['source_identity'] != ctx.index.source_identity
            or pending['content_sha256'] != ctx.index.content_sha256 or not pending['history']):
        raise ValueError('全书冻结候选的源绑定损坏，拒绝从失效草稿继续')
    draft, before, all_refs = None, None, []
    for group in pending['history']:
        after = _restore_chapter_snapshot(group['chapters'])
        if group['kind'] == 'full' and draft is None:
            prompt = source_engine.book_prompt(ctx.index, after, include_source=group['include_source'])
            draft, refs = _verified_book_trace(ctx, group, prompt)
        elif group['kind'] == 'repair' and draft is not None:
            scope = BookRepairScope.model_validate(group['scope'])
            if scope != source_engine.book_repair_scope(draft, index=ctx.index, before=before, after=after):
                raise ValueError('全书修订权限不匹配章观察的显式引用闭包')
            prompt = source_engine.book_repair_prompt(draft, scope=scope, after=after)
            patch, refs = _verified_book_trace(ctx, group, prompt)
            draft, _expanded = source_engine.apply_book_repair(draft, patch, scope=scope, index=ctx.index)
        elif group['kind'] == 'owner_repair' and draft is not None:
            saved = group['candidate']
            refs = _verified_semantic_candidate(ctx, 'book', saved)
            seed = _frozen_seed(ctx, 'book', saved['revision'])
            if seed.baseline != draft or before != after:
                raise ValueError('全书owner修订未绑定同一完整基线及章观察')
            draft = saved['wire']
        else:
            raise ValueError('全书冻结候选的修订历史顺序非法')
        before = after
        all_refs.extend(refs)
    if draft != pending['wire'] or digest(draft) != pending['wire_sha256']:
        raise ValueError('全书冻结候选被修改，不能以自报摘要冒充已购理解')
    return pending, draft, before, tuple({(ref.turn_id, ref.attempt_id): ref for ref in all_refs}.values())


def _book_complete_trace(pending):
    trace = []
    for group in pending['history']:
        if group['kind'] == 'full':
            trace = list(group['trace'])
        elif group['kind'] == 'repair':
            trace.append({'kind': 'changes', 'value': group['trace'], 'paths': [],
                          'ref': group['trace'][-1]['ref']})
        elif group['kind'] == 'owner_repair':
            trace = list(group['candidate']['trace'])
        else:
            raise ValueError('未知全书理解历史种类')
    return tuple(trace)


def _save_complete_book_candidate(ctx, pending, wire, refs):
    """完整理解保持唯一基线；BookRepairWire只占其真实组合trace的一步。"""
    trace = _book_complete_trace(pending)
    if replay_semantic_trace(trace) != wire:
        raise ValueError('全书完整基线不能由真实组合trace重建')
    last, group = trace[-1]['ref'], pending['history'][-1]
    record = {'kind': 'result', 'contract_version': 'story-v1', 'wire': wire,
        'request_sha256': last['request_sha256'], 'owner_prompt': last['prompts']['user'],
        'base_prompt_sha256': digest({'system': SYSTEM, 'user': source_engine.book_prompt(
            ctx.index, _restore_chapter_snapshot(group['chapters']))}),
        'refs': [dict(ref._asdict()) for ref in refs], 'trace': trace}
    if group['kind'] == 'owner_repair':
        record = group['candidate']
    _verified_semantic_candidate(ctx, 'book', record)
    ctx.repository.write('runtime/semantic_candidates/book.json', record)
    return SemanticResult(wire, refs, None, trace)


def _current_book_wire(ctx):
    # 下游授权只读核查；缺历史不能在预检中初始化或重建恢复账。
    if not ctx.repository.path(_BOOK_PENDING).is_file():
        raise ValueError('缺少完整全书理解的可验证历史，不能授予下游修订')
    pending, wire, _before, _refs = _load_book_pending(ctx)
    if pending['status'] != 'published':
        raise ValueError('全书理解尚未正式发布，不能授予下游修订')
    return wire


def run_book(ctx):
    from novel.stages.owner_revision import open_issue_record
    index, observations = ctx.index, _observations(ctx)
    restored = _load_book_pending(ctx)
    if restored is None:
        compiler = lambda wire: source_engine.compile_book(wire, index=index, chapters=observations)
        for include_source in (True, False):
            intent = {'contract_version': 'story-v1', 'source_identity': index.source_identity,
                      'content_sha256': index.content_sha256, 'chapters': _chapter_snapshot(observations),
                      'include_source': include_source}
            ctx.repository.write(_BOOK_INTENT, intent)
            try:
                result = _json_call(ctx, 'book', source_engine.book_prompt(index, observations,
                    include_source=include_source), compiler, index=index)
                break
            except ContextBudgetRefused:
                if not include_source:
                    raise
            except source_engine.SourceObservationIssueRequired:
                _load_book_pending(ctx)  # 先冻结可验证草稿，再回派局部owner。
                raise
        observation = compiler(result.value)
        pending = {'contract_version': 'story-v1', 'source_identity': index.source_identity,
            'content_sha256': index.content_sha256, 'status': 'published', 'wire': result.value,
            'wire_sha256': digest(result.value), 'history': [{'kind': 'full', 'include_source': include_source,
                'chapters': _chapter_snapshot(observations), 'trace': result.trace,
                'refs': [dict(ref._asdict()) for ref in result.refs]}]}
        ctx.repository.write(_BOOK_PENDING, pending)
    else:
        pending, baseline, before, baseline_refs = restored
        if before == observations:
            if pending['status'] != 'published':
                issues = source_engine.BookWire.model_validate(baseline).source_issues
                if issues:
                    raise source_engine.SourceObservationIssueRequired(issues)
                raise ValueError('冻结全书理解未发布且无可执行局部修订')
            compiler = lambda wire: source_engine.compile_book(wire, index=index, chapters=observations)
            result = _save_complete_book_candidate(ctx, pending, baseline, baseline_refs)
            if open_issue_record(ctx.repository, 'book') is not None:
                result = _json_call(ctx, 'book', source_engine.book_prompt(index, observations), compiler, index=index)
                if result.value != baseline:
                    saved = ctx.repository.inspect('runtime/semantic_candidates/book.json')
                    pending = {**pending, 'wire': result.value, 'wire_sha256': digest(result.value),
                        'history': [*pending['history'], {'kind': 'owner_repair',
                            'chapters': _chapter_snapshot(observations), 'candidate': saved}]}
                    ctx.repository.write(_BOOK_PENDING, pending)
            observation = compiler(result.value)
            _publish_answer(ctx, BOOK_OBSERVATION, result, observation)
            return {'integrated_chapters': len(observations),
                    'narrow_repair': any(group['kind'] != 'full' for group in pending['history'])}
        issues = source_engine.BookWire.model_validate(baseline).source_issues
        unchanged = [row for row in issues if before[row.chapter_ordinal] == observations[row.chapter_ordinal]]
        if unchanged:
            raise source_engine.SourceObservationIssueRequired(unchanged)
        scope = source_engine.book_repair_scope(baseline, index=index, before=before, after=observations)
        prompt = source_engine.book_repair_prompt(baseline, scope=scope, after=observations)
        def compiler(wire):
            merged, _expanded = source_engine.apply_book_repair(baseline, wire, scope=scope, index=index)
            try:
                return source_engine.compile_book(merged, index=index, chapters=observations)
            except source_engine.SourceObservationIssueRequired as exc:
                exc.book_wire = merged
                raise
            except source_engine.StoryCompileError as exc:
                from contracts.schema.source_story import StoryIssue
                raise source_engine.StoryCompileError(tuple(StoryIssue(code=row.code, owner='book',
                    item=row.item, path=('changes',), message=row.message) for row in exc.issues)) from exc
        try:
            result = _json_call(ctx, 'book', prompt, compiler, index=index)
        except source_engine.SourceObservationIssueRequired as exc:
            response = exc.semantic_result
            merged = exc.book_wire
            group = {'kind': 'repair', 'chapters': _chapter_snapshot(observations),
                     'scope': scope.model_dump(mode='json'), 'trace': list(response.trace),
                     'refs': [dict(ref._asdict()) for ref in response.refs]}
            ctx.repository.write(_BOOK_PENDING, {**pending, 'status': 'waiting', 'wire': merged,
                'wire_sha256': digest(merged), 'history': [*pending['history'], group]})
            raise
        merged, expanded = source_engine.apply_book_repair(baseline, result.value, scope=scope, index=index)
        observation = compiler(result.value)
        repair_refs = result.refs
        refs = tuple({(ref.turn_id, ref.attempt_id): ref for ref in (*baseline_refs, *result.refs)}.values())
        result = replace(result, refs=refs)
        pending = {**pending, 'status': 'published', 'wire': merged, 'wire_sha256': digest(merged),
            'history': [*pending['history'], {'kind': 'repair', 'chapters': _chapter_snapshot(observations),
                'scope': scope.model_dump(mode='json'), 'trace': list(result.trace),
                'refs': [dict(ref._asdict()) for ref in repair_refs]}], 'publication': {
            'wire_sha256': digest(merged), 'scope': scope.model_dump(mode='json'),
            'expanded_paths': [list(path) for path in expanded], 'trace': list(result.trace),
            'refs': [dict(ref._asdict()) for ref in result.refs]}}
        ctx.repository.write(_BOOK_PENDING, pending)
        result = _save_complete_book_candidate(ctx, pending, merged, refs)
    _publish_answer(ctx, BOOK_OBSERVATION, result, observation)
    return {'integrated_chapters': len(observations), 'narrow_repair': restored is not None}


def run_associate(ctx):
    book = ctx.repository.read_model(BOOK_OBSERVATION)
    prompt = source_engine.associate_prompt(book)
    if prompt is None:
        from contracts.schema.source_story import AssociateObservation
        ctx.repository.write(ASSOCIATION, AssociateObservation(contract_version='story-v1', verdicts=()),
                             declared_free=True)
        return {'candidates': 0}
    compiler = lambda wire: source_engine.compile_associate(wire, book=book)
    result = _json_call(ctx, 'associate', prompt, compiler, index=ctx.index)
    _publish_answer(ctx, ASSOCIATION, result, compiler(result.value))
    return {'candidates': len(book.unresolved)}


def run_names(ctx):
    book = ctx.repository.read_model(BOOK_OBSERVATION)
    association = ctx.repository.read_model(ASSOCIATION)
    compiler = lambda wire: source_engine.compile_names(wire, book=book, associate=association)
    result = _json_call(ctx, 'names', source_engine.names_prompt(book, associate=association), compiler, index=ctx.index)
    _publish_answer(ctx, NAME_OBSERVATION, result, compiler(result.value))
    return {'people': len(compiler(result.value).people)}


def run_compile(ctx):
    value = source_engine.compile_source_story(index=ctx.index, chapters=_observations(ctx),
        book=ctx.repository.read_model(BOOK_OBSERVATION),
        names=ctx.repository.read_model(NAME_OBSERVATION),
        associate=ctx.repository.read_model(ASSOCIATION))
    ctx.repository.write(SOURCE_STORY, value)
    return {'entities': len(value.entities), 'events': len(value.events)}


def run_naming(ctx):
    source = ctx.repository.read_model(SOURCE_STORY)
    compiler = lambda wire: target_engine.compile_naming(wire, source)
    result = _json_call(ctx, 'naming', target_engine.build_naming_prompt(source, book_title=ctx.book_title), compiler)
    _publish_answer(ctx, NAME_MAP, result, compiler(result.value))
    return {'named': len(compiler(result.value).names)}


def run_preserve(ctx):
    source, names = ctx.repository.read_model(SOURCE_STORY), ctx.repository.read_model(NAME_MAP)
    compiler = lambda wire: target_engine.compile_preserve(wire, source, names)
    result = _json_call(ctx, 'preserve', target_engine.build_preserve_prompt(source, names), compiler)
    value = compiler(result.value)
    _publish_answer(ctx, PRESERVATION, result, value)
    return {'requirements': len(value.requirements)}


def run_body_design(ctx):
    preservation, names = ctx.repository.read_model(PRESERVATION), ctx.repository.read_model(NAME_MAP)
    compiler = lambda wire: target_engine.compile_target_model(wire, preservation,
        source_chapter_count=len(ctx.chapters), total_length_budget=ctx.budget, name_map=names)
    prompt = target_engine.build_target_model_prompt(preservation, names,
        source_chapter_count=len(ctx.chapters), total_length_budget=ctx.budget)
    result = _json_call(ctx, 'design', prompt, compiler)
    _publish_answer(ctx, TARGET_MODEL, result, compiler(result.value))
    return {'chapters': len(ctx.chapters)}


def run_target_chapters(ctx):
    model = ctx.repository.read_model(TARGET_MODEL)
    preservation = ctx.repository.read_model(PRESERVATION)
    def one(chapter):
        ordinal = chapter.chapter_ordinal
        compiler = lambda wire: target_engine.compile_chapter(wire, model, chapter_ordinal=ordinal)
        result = _json_call(ctx, f'plan_ch{ordinal:02d}',
            target_engine.build_chapter_prompt(model, preservation, chapter_ordinal=ordinal), compiler)
        return ordinal, result, compiler(result.value)
    with ThreadPoolExecutor(max_workers=ctx.pool_workers('str06.stage4.chapters', len(model.chapters))) as pool:
        results = list(pool.map(one, model.chapters))
    for ordinal, result, value in results:
        _publish_answer(ctx, chapter_rel(CHAPTER_PLAN, ordinal), result, value)
    return {'chapters': len(results)}


def run_compile_stage4(ctx):
    model = ctx.repository.read_model(TARGET_MODEL)
    plans = tuple(ctx.repository.read_model(chapter_rel(CHAPTER_PLAN, ch.chapter_ordinal)) for ch in model.chapters)
    story, adaptation = target_engine.compile_target_story(model, plans,
        ctx.repository.read_model(PRESERVATION), ctx.repository.read_model(NAME_MAP))
    items = [PublishItem(TARGET_STORY, value=story), PublishItem(ADAPTATION_MAP, value=adaptation)]
    items.extend(PublishItem(chapter_rel(CHAPTER_PACKET, ch.chapter_ordinal),
        value=target_engine.project_chapter_packet(story, ch.chapter_ordinal, length_band=ctx.length_band))
        for ch in story.chapters)
    ctx.repository.publish(tuple(items))
    return {'chapters': len(story.chapters)}


def run_hook_design(ctx):
    story = ctx.repository.read_model(TARGET_STORY)
    budget = max(100, min(600, ctx.budget // 20))
    compiler = lambda wire: target_engine.compile_hook(wire, story, length_budget=budget)
    result = _json_call(ctx, 'hook_design', target_engine.build_hook_prompt(story, length_budget=budget), compiler)
    _publish_answer(ctx, HOOK_PLAN, result, compiler(result.value))
    return {'hook_planned': True}


def run_hook_compile(ctx):
    packet = target_engine.project_hook_packet(ctx.repository.read_model(TARGET_STORY),
        ctx.repository.read_model(HOOK_PLAN), length_band=ctx.length_band)
    ctx.repository.write(HOOK_PACKET, packet)
    return {'hook_ready': True}


def _prose_compiler(ctx, request):
    def compile_value(wire):
        value = prose_engine.parse_prose_answer(wire)
        if isinstance(value, NeedsDesign):
            problems = []
            for problem in value.problems:
                try:
                    if request.kind == 'body':
                        story = ctx.repository.inspect_sealed_model(TARGET_STORY)
                        current_packet = target_engine.project_chapter_packet(story,
                            request.packet.chapter_ordinal, length_band=ctx.length_band)
                        scoped = target_engine.chapter_packet_repair_paths(story,
                            request.packet.chapter_ordinal, problem.section, problem.item_no)
                    else:
                        story = ctx.repository.inspect_sealed_model(TARGET_STORY)
                        hook = ctx.repository.inspect_sealed_model(HOOK_PLAN)
                        current_packet = target_engine.project_hook_packet(story, hook, length_band=ctx.length_band)
                        scoped = target_engine.hook_packet_repair_paths(hook, problem.section, problem.item_no)
                    if current_packet != request.packet:
                        raise ValueError('问题引用的实际送达包与当前正式投影不同')
                except (ValueError, IndexError, KeyError) as exc:
                    raise ValidationStop(str(exc), code='revision_scope_required',
                        node='str06.stage5.body' if request.kind == 'body' else 'str06.stage5.hook',
                        stage='stage5', item=str(request.packet.chapter_ordinal) if request.kind == 'body' else 'hook',
                        phase='accept', context={'problem': problem.model_dump(mode='json')}) from exc
                problems.append({**problem.model_dump(mode='json'), 'paths': [list(path) for path in scoped],
                    'operation': 'replace',
                    'packet_sha256': request.packet_sha256,
                    'validation_identity': f'{request.kind}:{problem.section}:{problem.item_no}'})
            raise StoryUpstreamIssue('str06.stage4.chapters' if request.kind == 'body' else 'str06.stage4.hook_design',
                request.packet.chapter_ordinal if request.kind == 'body' else None, problems)
        issues = prose_engine.validate_body(value.text, request)
        if issues:
            from contracts.schema.source_story import StoryIssue
            raise source_engine.StoryCompileError(tuple(StoryIssue(code=issue.code, owner='body',
                item=str(issue.chapter_ordinal or ''), path=('text',), message=issue.detail) for issue in issues))
        return value
    return compile_value


def _existing_body(ctx, ordinal):
    receipt = ctx.repository.inspect_sealed_model(chapter_rel(BODY_RECEIPT, ordinal))
    text = ctx.repository.read_text(chapter_rel(BODY, ordinal))
    return ChapterBody(contract_version='story-v1', chapter_ordinal=ordinal, text=text, receipt=receipt)


def _origin(result, ctx):
    return {'response_id': result.answer.response_sha256, 'provider': ctx.provider,
            'model': result.answer.model or ctx.model, 'finish_reason': result.answer.finish_reason or 'stop'}


def run_body(ctx):
    ordinals = resolve_body_chapters(ctx.body_chapters, len(ctx.chapters))
    # 顺序执行；前章只按当前章包与实际短尾证明，不读源故事或全书设计作隐藏依赖。
    prior = None
    completed = []
    for ordinal in range(1, max(ordinals) + 1):
        packet = ctx.repository.inspect_sealed_model(chapter_rel(CHAPTER_PACKET, ordinal))
        previous = None
        try:
            previous = _existing_body(ctx, ordinal)
        except (FileNotFoundError, UnsealedArtifact):
            # 未封存正文不可接受；回到同一章包/前章短尾构造的耐久请求，
            # 按真实已购响应免费重放，再重过 accept 并重新发布。
            pass
        if previous is not None and not prose_engine.verify_body(previous, packet, prior_body=prior):
            prior = previous.text
            if ordinal in ordinals:
                completed.append(ordinal)
            continue
        if ordinal not in ordinals:
            raise ValueError(f'正文选章依赖第{ordinal}章的当前有效正文，请先恢复该章')
        request = prose_engine.build_body_request(packet, prior_body=prior)
        if previous is not None and previous.receipt.packet_sha256 == request.packet_sha256:
            patch_request = prose_engine.opening_patch_request(previous, packet, prior_body=prior, reason='前章已接受正文的短尾改变，校准本章开篇衔接')
            def patch_compile(wire):
                issues = prose_engine.validate_opening_patch_answer(wire, patch_request, previous,
                    packet, prior_body=prior)
                if issues:
                    from contracts.schema.source_story import StoryIssue
                    raise source_engine.StoryCompileError(tuple(StoryIssue(code=issue.code, owner='body',
                        item=str(ordinal), path=('paragraphs',), message=issue.detail) for issue in issues))
                return prose_engine.parse_opening_patch(wire)
            result = _json_call(ctx, f'body_ch{ordinal:02d}', patch_request.user, patch_compile,
                               system=patch_request.system)
            accepted = prose_engine.apply_opening_patch(previous, patch_compile(result.value), patch_request, packet, prior_body=prior,
                                                        **_origin(result, ctx))
            if isinstance(accepted, NeedsScope):
                raise StoryUpstreamIssue('str06.stage5.body', ordinal, [{'detail': accepted.detail}])
        else:
            result = _json_call(ctx, f'body_ch{ordinal:02d}', request.user, _prose_compiler(ctx, request), system=request.system)
            accepted = prose_engine.accept_body(_prose_compiler(ctx, request)(result.value), request, **_origin(result, ctx))
        ctx.repository.publish((PublishItem(chapter_rel(BODY, ordinal), text=accepted.text),
            PublishItem(chapter_rel(BODY_RECEIPT, ordinal), value=accepted.receipt, refs=result.refs)))
        prior = accepted.text
        completed.append(ordinal)
    return {'chapters': completed, 'sequential': True}


def run_hook(ctx):
    packet = ctx.repository.read_model(HOOK_PACKET)
    request = prose_engine.build_hook_request(packet)
    result = _json_call(ctx, 'hook_writer', request.user, _prose_compiler(ctx, request), system=request.system)
    value = prose_engine.accept_hook(_prose_compiler(ctx, request)(result.value), request, **_origin(result, ctx))
    ctx.repository.publish((PublishItem(HOOK, text=value.text),
        PublishItem(HOOK_RECEIPT, value=value.receipt, refs=result.refs)))
    return {'hook_chars': count_non_whitespace_chars(value.text)}


def run_assemble(ctx):
    packets = tuple(ctx.repository.inspect_sealed_model(chapter_rel(CHAPTER_PACKET, i))
                    for i in range(1, len(ctx.chapters) + 1))
    bodies = tuple(_existing_body(ctx, i) for i in range(1, len(ctx.chapters) + 1))
    hook = HookBody(contract_version='story-v1', text=ctx.repository.read_text(HOOK), receipt=ctx.repository.read_model(HOOK_RECEIPT))
    value = assembly_engine.assemble_story(title=ctx.book_title or '新书', source_chapter_count=len(ctx.chapters),
                          packets=packets, bodies=bodies, hook=hook,
                          hook_packet=ctx.repository.read_model(HOOK_PACKET))
    title = value.manifest.title
    if any(ch in title for ch in '/\\:*?"<>|') or title in ('.', '..'):
        raise ValueError('书名包含非法路径字符')
    ctx.repository.publish((PublishItem(FINAL.format(title=title), text=value.text),
                            PublishItem(MANIFEST, value=value.manifest)))
    return {'delivered': True, 'chapters': len(bodies), 'semantic': 'unver'}


NODE_RUNNERS = dict(zip(NODE_ORDER, (
    run_chapters, run_book, run_associate, run_names, run_compile,
    run_naming, run_preserve, run_body_design, run_target_chapters, run_compile_stage4,
    run_hook_design, run_hook_compile, run_hook, run_body, run_assemble,
), strict=True))


class StoryUpstreamIssue(ValueError):
    def __init__(self, owner, chapter, problems):
        super().__init__('正式故事材料需修订：' + str(problems))
        self.owner, self.chapter, self.problems = owner, chapter, problems


def resolve_body_chapters(selected, count):
    result = tuple(range(1, count + 1)) if selected is None else tuple(selected)
    if not result or len(set(result)) != len(result) or any(isinstance(i, bool) or not 1 <= i <= count for i in result):
        raise ValueError('正文选章必须是源章范围内无重复的非空章序')
    return tuple(sorted(result))


def derive_total_length_budget(chapters, *, rounding=1000):
    import math
    return max(rounding, math.ceil(sum(count_non_whitespace_chars(ch['text']) for ch in chapters) / rounding) * rounding)


def _upstream_rows(exc):
    if getattr(exc, 'source_issues', None):
        return [{'owner': f'ch{row.chapter_ordinal:02d}', 'node': 'str06.stage2.chapters',
            'item': row.item, 'code': 'source_' + row.kind, 'message': row.message,
            'context': {'path': list(row.path), 'operation': row.operation,
                        'evidence_refs': list(row.evidence_refs)}} for row in exc.source_issues]
    if isinstance(exc, StoryUpstreamIssue):
        if exc.owner == 'str06.stage4.chapters':
            owner = f'plan_ch{exc.chapter:02d}'
        elif exc.owner == 'str06.stage4.hook_design':
            owner = 'hook_design'
        else:
            return []
        return [{'owner': owner, 'node': exc.owner, 'item': str(exc.chapter or 'hook'),
            'code': 'needs_design', 'message': row['detail'], 'context': row} for row in exc.problems]
    if hasattr(exc, 'as_issue'):
        rows = getattr(exc, 'context', {}).get('owner_issues') or [exc.as_issue()]
        result = []
        for row in rows:
            row = dict(row)
            if (row['node'] in ('str06.stage2.chapters', 'str06.stage4.chapters')
                    and not row.get('context', {}).get('source_ref')):
                ordinal = int(row['item'].split(':')[-1])
                row['owner'] = ('ch' if row['node'] == 'str06.stage2.chapters' else 'plan_ch') + f'{ordinal:02d}'
            elif row['owner'] == 'body_design':
                row['owner'] = 'design'
            result.append(row)
        return result
    return []


def _raise_upstream(ctx, node, exc):
    from collections import defaultdict
    from novel.stages.owner_revision import discover_owner_issues, OwnerRevisionRequired
    groups = defaultdict(list)
    for row in _upstream_rows(exc):
        if row.get('context', {}).get('source_ref'):
            context = row['context']
            try:
                address = source_engine.resolve_source_revision(ctx.repository.inspect_sealed_model(SOURCE_STORY),
                    source_ref=context['source_ref'], path=context['path'], operation=context['operation'],
                    book=ctx.repository.inspect_sealed_model(BOOK_OBSERVATION), book_wire=_current_book_wire(ctx),
                    chapters=_observations(ctx), chapter_wires={chapter['chapter_ordinal']:
                        ctx.repository.inspect(f"runtime/semantic_candidates/ch{chapter['chapter_ordinal']:02d}.json")['wire']
                        for chapter in ctx.chapters}, index=ctx.index,
                    names_wire=ctx.repository.inspect('runtime/semantic_candidates/names.json')['wire'],
                    associate=ctx.repository.inspect_sealed_model(ASSOCIATION))
            except (ValueError, KeyError, IndexError, TypeError) as failure:
                raise ValidationStop(str(failure), code='revision_scope_required', node=node,
                    stage=node.split('.')[1], item=row['item'], phase='accept') from failure
            row = {**row, 'owner': address.owner, 'node': address.node, 'item': address.item,
                'context': {**context, 'canonical_path': list(address.canonical_path),
                    'path': list(address.path), 'operation': address.operation}}
        if row['node'] == node or NODE_ORDER.index(row['node']) >= NODE_ORDER.index(node):
            raise exc
        if (row['node'] == 'str06.stage2.chapters' and row.get('context', {}).get('path')
                and not row.get('context', {}).get('source_ref')):
            ordinal = int(row['owner'][2:])
            observation = ctx.repository.inspect_sealed_model(chapter_rel(CHAPTER_OBSERVATION, ordinal))
            baseline = ctx.repository.inspect(f"runtime/semantic_candidates/{row['owner']}.json")['wire']
            canonical_path = row['context']['path']
            wire_path = source_engine.chapter_wire_path(observation, canonical_path,
                                                       wire=baseline, index=ctx.index)
            row = {**row, 'context': {**row['context'], 'canonical_path': canonical_path,
                                      'path': list(wire_path)}}
        groups[(row['owner'], row['node'])].append(OwnerIssueV1(code=row['code'], item=row.get('item', ''),
            message=row['message'], context=row.get('context', {})))
    pending = []
    for (owner, target), issues in groups.items():
        try:
            discover_owner_issues(ctx.repository, raised_by=node, owner=owner, node=target, issues=issues)
        except OwnerRevisionRequired as dispatch:
            pending.append(dispatch)
    if pending:
        raise min(pending, key=lambda dispatch: NODE_ORDER.index(dispatch.node)) from exc
    raise exc


def run_node(node_id, ctx):
    from novel.stages.owner_revision import complete_owner_dispatch, resolve_owner_issues, read_owner_issue_file
    if node_id not in NODE_RUNNERS:
        raise ValueError(f'未知当前节点：{node_id}')
    if ctx.repository.context is not None:
        ctx.repository.context.enter_node(node_id)
        ctx.repository.context.begin_run()
    state = {'contract_version': 'story-v1', 'node': node_id, 'status': 'running', 'semantic': 'unver'}
    ctx.repository.write('runtime/node_status/' + node_id + '.json', state)
    try:
        result = NODE_RUNNERS[node_id](ctx)
    except Exception as exc:
        state.update(status='failed', failure_kind=type(exc).__name__, detail=str(exc))
        if hasattr(exc, 'as_status'):
            state.update(exc.as_status())
        if _upstream_rows(exc):
            state['owner_issues'] = _upstream_rows(exc)
        elif getattr(exc, 'issues', None):
            state['issues'] = [issue.model_dump(mode='json') if hasattr(issue, 'model_dump') else str(issue)
                               for issue in exc.issues]
        ctx.repository.write('runtime/node_status/' + node_id + '.json', state)
        if _upstream_rows(exc):
            _raise_upstream(ctx, node_id, exc)
        raise
    chapter_owners = tuple(f'ch{i:02d}' for i in range(1, len(ctx.chapters) + 1)) + tuple(
        f'plan_ch{i:02d}' for i in range(1, len(ctx.chapters) + 1))
    complete_owner_dispatch(ctx.repository, node_id, chapter_owners=chapter_owners)
    # 只有原发现者在新正式输入上成功复验，才清除它自己提出的问题。
    from contracts.schema.workflow import OWNER_PRODUCT_RELS
    for owner in (*OWNER_PRODUCT_RELS, *chapter_owners):
        record = read_owner_issue_file(ctx.repository, owner)
        if record is not None and record.status == 'open' and record.raised_by == node_id:
            resolve_owner_issues(ctx.repository, owner=owner, raised_by=node_id)
    state.update(status='ready', result=result)
    ctx.repository.write('runtime/node_status/' + node_id + '.json', state)
    return result


def run_workflow(*, repository, chapters, ask, budget=11000, through='stage7', hook_copy='',
                 book_title='', length_band=(.6,1.3), node_filter=None, body_chapters=None,
                 provider='unknown', model='unknown', concurrency=PIPELINE_DEFAULT_CONCURRENCY,
                 node_concurrency=None, source_identity=None):
    ctx = ChainContext(repository, ask, chapters, hook_copy, budget, book_title, length_band,
                       provider, model, concurrency, dict(node_concurrency or {}), body_chapters, source_identity)
    if through not in THROUGH_NODE:
        raise ValueError('through 必须是 stage2/stage4/stage5/stage7')
    selected = tuple(node_filter) if node_filter else NODE_ORDER[:NODE_ORDER.index(THROUGH_NODE[through]) + 1]
    if any(node not in NODE_ORDER for node in selected):
        raise ValueError('包含非当前契约节点')
    status = {'contract_version': 'story-v1', 'status': 'running', 'through': through, 'accepted_body': False}
    workflow = repository.inspect('runtime/candidate_workflow.json')
    if (workflow['source_identity'] != ctx.index.source_identity
            or workflow['source_content_sha256'] != ctx.index.content_sha256):
        raise ValueError('源输入与当前根冻结身份不同；请在新根重新生成')
    repository.write('runtime/source_snapshot.json', {
        'contract_version': 'story-v1', 'source_identity': ctx.index.source_identity,
        'content_sha256': ctx.index.content_sha256, 'chapters': chapters, 'hook_copy': hook_copy})
    repository.write('runtime/workflow_status.json', status)
    reports = {}
    try:
        from novel.stages.owner_revision import OwnerRevisionRequired
        queue = list(selected)
        while queue:
            node = queue.pop(0)
            try:
                reports[node] = run_node(node, ctx)
            except OwnerRevisionRequired as dispatch:
                affected = NODE_ORDER[NODE_ORDER.index(dispatch.node):NODE_ORDER.index(node) + 1]
                queue = list(dict.fromkeys((*affected, *queue)))
    except Exception as exc:
        status.update(status='needs_authorization' if isinstance(exc, PurchaseRequired) else 'failed',
                      failed_node=node, failure_kind=type(exc).__name__, detail=str(exc))
        status.update(exc.as_status() if hasattr(exc, 'as_status') else {
            'error_code': type(exc).__name__, 'error_stage': NODES[node].stage,
            'error_node': node, 'error_item': getattr(exc, 'item', 'book'),
            'error_phase': 'accept', 'error_context': getattr(exc, 'context', {})})
        repository.write('runtime/workflow_status.json', status)
        raise
    status.update(status='delivered' if 'str06.stage7.assemble' in selected else 'node_run',
                  accepted_body='str06.stage5.body' in selected or 'str06.stage7.assemble' in selected)
    repository.write('runtime/workflow_status.json', status)
    return reports


def load_dev_source(payload):
    if not isinstance(payload, dict) or set(payload) != {'chapters', 'hook_copy'}:
        raise ValueError('开发源须显式给 chapters 与 hook_copy（无引子用 null）')
    source_engine.build_source_index(payload['chapters'], payload['hook_copy'] or '')
    return payload['chapters'], payload['hook_copy'] or ''


def frozen_concurrency(cfg):
    return {'concurrency': cfg.concurrency,
            'node_concurrency': {node: cfg.concurrency_for(node) for node in PIPELINE_PARALLEL_NODES}}


def execute(args):
    from novel.appendlog import path_transaction_lock
    root = Path(args.run_dir).resolve()
    with path_transaction_lock(root / 'runtime/candidate_workflow.json'):
        _execute(args)


def _execute(args):
    if getattr(args, 'adopt_root', ()) or getattr(args, 'wire_dir', None) or getattr(args, 'repair_manifest', None):
        raise ValueError('只支持当前契约在新根生成，不支持旧根、旧候选或协议迁移')
    base_value = getattr(args, 'base', None) or os.environ.get('NOVEL_OUTPUT')
    if not base_value:
        raise ValueError('必须显式提供 --base 或 NOVEL_OUTPUT')
    base = Path(base_value).resolve()
    root = Path(args.run_dir).resolve()
    if not root.is_relative_to(base):
        raise ValueError('输出书根必须位于显式输出根内')
    if getattr(args, 'book_sid', None):
        stage1 = load_stage1_chapters(base, args.book_sid)
        chapters, hook, source_id, root_sha1 = stage1
    elif getattr(args, 'source_json', None):
        raw = Path(args.source_json).read_bytes()
        chapters, hook = load_dev_source(Str06Repository.read_external(Path(args.source_json)))
        source_id, root_sha1 = hashlib.sha256(raw).hexdigest(), prov.content_sha1(raw)
    else:
        raise ValueError('缺少正式 Stage1 或开发源输入')
    if args.check_only and (args.yes or args.allow_source_export):
        raise ValueError('免费复验不能附带新调用授权')
    cfg = load_config()
    profile = freeze_execution_profile(cfg=cfg, reasoning_effort=args.reasoning_effort,
        base=base, sid=paths.derive_source_meta(root.name)[0], book_call_ceiling=getattr(args, 'book_call_ceiling', None),
        request_profile=getattr(args, 'request_profile', None))
    boundary = assemble_paid_boundary(base=base, run_dir=root, source_identity=source_id,
        root_sha1=root_sha1, source_content_sha256=source_engine.build_source_index(chapters, hook).content_sha256,
        profile=profile, yes=args.yes,
        allow_source_export=args.allow_source_export, readonly=args.check_only)
    def ask(owner, prompt, compiler, *, index=None, max_chapter=None, system=SYSTEM, revision_seed=None):
        if index is not None:
            prompt += MATERIAL_RULES
        request = boundary.build_request(owner, prompt, json_mode=True, system=system, temperature=.4)
        return request_json(boundary.session, owner, request, compiler,
                            repository=boundary.repository, index=index, max_chapter=max_chapter,
                            revision_seed=revision_seed)
    print(run_workflow(repository=boundary.repository, chapters=chapters, hook_copy=hook,
        ask=ask, budget=args.budget or derive_total_length_budget(chapters), through=args.through,
        node_filter=args.node or None, body_chapters=getattr(args, 'body_chapters', None),
        book_title=args.book_title, length_band=(args.body_band_low, args.body_band_high),
        provider=profile.provider, model=profile.model, source_identity=source_id, **frozen_concurrency(cfg)))


class Stage1Input(NamedTuple):
    """候选链的 Stage1 源输入：章数组 + 章前文字 + 盘上字节身份 + 组合 SHA-1。

    输入装配属装配体（薄驱动）：读正式 Stage1 章数组要经 `novel.paths` 定位书目录、
    经正式契约解码；各 Stage 的纯语义引擎不 import 仓储或模型调用设施。

    `source_identity`／`root_sha1` 是 **main_text＋hook_copy 两份字节的组合摘要**
    （长度前缀防拼接歧义）：只改章前文字也会换源身份。Stage2 第1章观察同时处理
    章前文字，全书整合再确认其归属；其余章观察互不依赖。
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
    epoch 生命周期。授权仍由调用方显式给出（`--yes`／`--allow-egress`），
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
    *, base, run_dir, source_identity: str, root_sha1: str, source_content_sha256: str, profile: ExecutionProfile,
    yes: bool = False, allow_source_export: bool = False,
    readonly: bool = False, transport=None,
) -> PaidBoundary:
    """把 Stage1 源身份、workflow／epoch、付费边界与预算闸装到一起。

    `profile` 必须由调用方在 run 启动时冻结（`freeze_execution_profile`）：
    本装配**不再读执行期配置**——`load_config()` 不在这里，一次 run 内
    环境／`.env` 变化不影响请求身份、传输实参与账面归属。`run_dir` 必须位于
    `base` 下的隔离根：一个书根一个 workflow 身份，sid沿用摄入时的源SID；
    `readonly`（`--check-only`）只复校不签发新调用。
    """
    base_path = Path(base).resolve()
    run_dir = Path(run_dir).resolve()
    if run_dir.parent != base_path:
        raise ValueError('run-dir 必须为 base 下的直接书根')
    sid = paths.derive_source_meta(run_dir.name)[0]
    repository = Str06Repository(run_dir)
    workflow, context = init_or_resume_candidate_workflow(
        repository=repository, base=base_path, code=prov.code_version(),
        root_sha1=root_sha1, source_identity=source_identity, source_content_sha256=source_content_sha256,
        task_id=require_workflow(base_path).task_id,
        configured_model=profile.model,
        created_at=int(time.time()), readonly=readonly)
    session = PaidRequestSession(
        repository, context.ledger, transport or DeepSeekTransport(profile.cfg), yes=yes,
        allow_source_export=allow_source_export, provider=profile.provider,
        book_call_ceiling=profile.book_call_ceiling, sid=sid)
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
