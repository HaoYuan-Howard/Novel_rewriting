"""有据错漏的章级定向修订；共用编辑身份编译器和现有付费响应恢复，不重购整书。"""
from __future__ import annotations

import json

from contracts.schema import decode_artifact
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.plot import PlotChapterResponse
from contracts.schema.plot_edit import PlotChapterEdit, PlotEdit, PlotRevisionResponse
from contracts.schema.stage2 import BlueprintProvenance, ChapterBlueprints
from novel import paths, prov
from novel.consumption import consumed_digest, plot_global_projection
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.llm import stage2_runs
from novel.llm.ledger import combine_usage
from novel.store import ArtifactStore
from novel.stages.stage2 import plot_edit
from novel.stages.stage2.character_refs import blueprint_inventory_from_projection
from novel.stages.stage2.plot_inputs import read_input_digests
from novel.stages.stage2.plot_prompts import BLUEPRINT_SYSTEM, build_plot_user
from novel.stages.stage2.plot_sequence import with_entry_states
from novel.stages.stage2.plot_compile import state_materials

OPERATION = 'plot-blueprint-revision-v1'
SYSTEM = BLUEPRINT_SYSTEM + """
本轮是已存在蓝图的定向修订。repair_baseline 给出当前章正文和局部标识，
baseline_identities 给出局部标识对应的正式主体。对每个旧主体与新局部标识
恰好声明一条身份归属：普通措辞或属性订正用 preserve；语义拆合/删除用 replace；
有据新增用 add。不能为了减少身份操作而保留错误边界，不返回正式编号。
未受问题影响的完整语义保持。需要修正的内容必须有本章原文依据；不接受无据改剧情。
响应为指定章内容及 identities。身份操作不表示语义审阅通过。
"""


def response_system() -> str:
    def compact(value):
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()
                    if key not in ('title', 'description')}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value
    return SYSTEM + '\n响应契约：\n' + json.dumps(
        compact(PlotRevisionResponse.model_json_schema()), ensure_ascii=False, separators=(',', ':'))


def _finalization(ctx, store, *, chapters, review_token, candidate, live_before):
    return dict(operation=OPERATION, chapters=list(chapters), review_token=review_token,
                inputs=read_input_digests(store), artifact=candidate, live_before=live_before)


def finish_commit(ctx, *, chapters=(), review_token=None):
    """封存候选可在正式替换前后免费恢复；不能覆盖期间新出现的人工修改。"""
    from novel.stages.stage2 import extract
    journal = stage2_runs.commit_state(ctx)
    if journal is None:
        return None
    stored = journal.get('finalization_payload')
    if not isinstance(stored, dict) or stored.get('operation') != OPERATION:
        return None
    targets = tuple(stored.get('chapters') or ())
    if (chapters and tuple(chapters) != targets) or review_token != stored.get('review_token'):
        raise ProvenanceRefused('定向修订提交恢复的目标或裁决请求不一致')
    from novel.orchestrator import mechanical_admission as admission
    gate = admission.blueprint_generation_ready(ctx.novel_dir.parent, ctx.sid)
    if not gate.ok:
        from novel.errors import ApprovalRequired
        raise ApprovalRequired('统一机械准入未通过：' + '；'.join(gate.blocking))
    store = ArtifactStore(ctx)
    candidate, frozen = stage2_runs.restore_commit_payloads(ctx, journal)
    artifact = ChapterBlueprints.model_validate(candidate)
    expected = _finalization(ctx, store, chapters=targets, review_token=review_token,
                             candidate=candidate, live_before=frozen.get('live_before'))
    if not stage2_runs.verify_commit_finalization(journal, expected):
        raise ProvenanceRefused('定向修订已购候选的输入发生漂移，不能重新购买或补签')
    formal_matches = False
    if store.exists(extract.BLUEPRINT_REL):
        formal = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
        formal_matches = stage2_runs.verify_commit_artifact(
            journal, formal.model_dump(mode='json', by_alias=True))
    live = extract._blueprint_live_state(store)
    before = frozen.get('live_before')
    if not isinstance(before, dict):
        raise ProvenanceRefused('定向修订缺提交前盘面身份，不能覆盖当前文件')
    if not formal_matches:
        if journal.get('state') == 'committed' or live != before:
            raise ProvenanceRefused('定向修订提交前后盘面漂移，拒绝覆盖第三方修改')
    elif store.exists(extract._INVALID) and live[extract._INVALID] != before.get(extract._INVALID):
        raise ProvenanceRefused('正式修订已落，但问题稿已更新，拒绝清除新问题稿')
    main = extract._read_main_text(store)
    hits = extract._blueprint_hard_hits(extract._judge_candidate(None, candidate, main))
    if hits:
        raise ProvenanceRefused(f'封存修订尚未通过当前结构检查：{hits}')
    extract._require_blueprint_code_current(ctx, moment='定向修订恢复写回前')
    ctx.require_observed_roots_current()
    if not formal_matches:
        extract._write_formal(store, extract.BLUEPRINT_REL, artifact)
    elif store.exists(extract._INVALID):
        store.retire_invalid(extract.BLUEPRINT_REL)
    report = extract.verify_blueprint_delivery(ctx, artifact)
    adoptions = stage2_runs.restore_commit_adoptions(ctx, journal)
    extract._note_restored_adoptions(ctx, rel=extract.BLUEPRINT_REL,
                                     candidate=candidate, adoptions=adoptions)
    extract._require_blueprint_code_current(ctx, moment='定向修订恢复落哨兵前')
    ctx.require_observed_roots_current()
    stage2_runs.mark_committed(ctx)
    return report


def request_inputs(ctx, *, chapters, store, main_text, character_projection):
    """预览与执行共用同一只读构造器；调用前不会生成新章或接受任何语义。"""
    from novel.stages.stage2 import extract
    _, previous = plot_edit._current(store)
    template = PlotEdit.model_validate(decode_artifact(
        plot_edit.REQUEST_REL, plot_edit.edit_template(ctx)).payload)
    baselines = {row.chapter.chapter_ordinal: row for row in template.chapters}
    if not chapters or not set(chapters) <= baselines.keys():
        raise TerminalState('定向修订必须指定当前书中已有的非空章节范围')
    inventory = blueprint_inventory_from_projection(character_projection)
    visible = inventory['visible_character_refs']
    knowledge = store.read(KnowledgeGrounding, extract.GLOBAL_REL).model_dump(mode='json', by_alias=True)
    sources = {row['chapter_ordinal']: row['text'] for row in main_text}
    globals_ = {ordinal: plot_global_projection(knowledge, ordinal) for ordinal in sources}
    inputs = read_input_digests(store)
    system = response_system()
    issues: list = []
    requests = []
    for ordinal in chapters:
        baseline = baselines[ordinal]
        user = build_plot_user(ctx.title, sources[ordinal], ordinal,
            character_refs=visible.get(ordinal, {}), global_projection=globals_[ordinal],
            character_bindings=inventory['character_bindings'].get(ordinal, {}),
            repair_baseline=baseline.chapter.model_dump(mode='json'), repair_findings=issues)
        metadata, source = user.split('\n', 1)
        metadata = json.loads(metadata)
        metadata['baseline_identities'] = [row.model_dump(mode='json') for row in baseline.identities]
        requests.append((ordinal, system, json.dumps(metadata, ensure_ascii=False, separators=(',', ':')) + '\n' + source))
    return dict(previous=previous, template=template, visible=visible, globals_=globals_,
                character_bindings=inventory['character_bindings'],
                inputs=inputs, requests=requests, sources=sources, system=system)


def build_prompt(ctx, *, chapters=(), review_token=None):
    if review_token is not None:
        raise TerminalState(
            'Stage2 语义审阅已退役：旧审阅返工票不再可用；'
            '请按 --chapters 指定目标章重新发起定向修订')
    from novel.stages.stage2 import extract
    store = ArtifactStore(ctx)
    main = extract._read_main_text(store)
    _, projection = extract._require_blueprint_character_projection(store)
    data = request_inputs(ctx, chapters=chapters, store=store, main_text=main,
                          character_projection=projection)
    first, system, user = data['requests'][0]
    earlier = [chapter for chapter in data['previous'].blueprints if chapter.chapter_ordinal < first]
    carried = earlier[-1].carry_out_refs if earlier else []
    return system, with_entry_states(user, state_materials(earlier, sorted(carried)))


def run(ctx, client, *, chapters, store, main_text, character_projection, review_token=None):
    from novel.stages.stage2 import extract
    if review_token is not None:
        raise TerminalState(
            'Stage2 语义审阅已退役：旧审阅返工票不再可用；'
            '请按 --chapters 指定目标章重新发起定向修订')
    if ctx.cache.name != 'refresh-write':
        raise TerminalState('主动定向修订须使用 refresh-write；恢复仍复用本轮封存响应')
    data = request_inputs(ctx, chapters=chapters, store=store, main_text=main_text,
                          character_projection=character_projection)
    previous, template = data['previous'], data['template']
    if previous.provenance.model != client.model:
        raise TerminalState('定向修订须沿用原主模型配置，不能混写来源账')
    visible, globals_, sources = data['visible'], data['globals_'], data['sources']
    inputs, requests, system = data['inputs'], data['requests'], data['system']
    stage2_runs.ensure_run(ctx, architecture='plot-blueprint-v1', topology='single',
        requests=requests, request_policy=client.json_request_policy(
            [('repair', PlotRevisionResponse, extract.BLUEPRINT_MAX_TOKENS, False)],
            recovery_policy='stage2-blueprint-v1'), refresh=True,
        operation='chapters-refresh',
        subject_fingerprint=consumed_digest(dict(baseline=previous.model_dump(mode='json', by_alias=True),
            inputs=inputs, chapters=chapters, review_token=review_token)),
        resume_flags=(('--chapters', ','.join(map(str, chapters)))), input_projections=inputs)
    # 有后续检查点而中间缺失时，先拒绝；不能先购买缺章才发现旧依赖不再成立。
    gap = False
    for ordinal in chapters:
        exists = paths.stage2_blueprint_checkpoint_path(ctx.novel_dir, ctx.run_id, 'repair', ordinal).exists()
        if exists and gap:
            raise ProvenanceRefused('定向修订的状态依赖链缺中间检查点，不能先购买缺章')
        gap = gap or not exists
    by = {row.chapter_ordinal: row for row in previous.blueprints}
    requested = {ordinal: (system, user) for ordinal, system, user in requests}
    retired = {ref for event in previous.provenance.plot_revisions for row in event.changes
               for ref in [*row.old_refs, *row.new_refs]}
    carried, compiled, edits = frozenset(), [], []
    for ordinal in sorted(by):
        chapter = by[ordinal]
        if ordinal in requested:
            _, seed = requested[ordinal]
            entry_materials = state_materials(compiled, sorted(carried))
            user = with_entry_states(seed, entry_materials)
            def parse(row):
                payload = row.model_dump(mode='json')
                identities = payload.pop('identities')
                return PlotChapterEdit(chapter=PlotChapterResponse.model_validate(payload), identities=identities)
            def compile_row(row):
                return plot_edit._compile_edit(parse(row), chapter, sources[ordinal],
                    available=carried, chars=frozenset(visible.get(ordinal, {})),
                    globals_=frozenset((row['kind'], row['ref']) for row in globals_[ordinal]),
                    retired=retired, character_bindings=data['character_bindings'].get(ordinal, {}),
                    actor_bindings={entry['binding']['presentation_id']: entry['binding']
                        for row in entry_materials for entry in row['actor_bindings']},
                    global_subjects={(row['kind'], row['ref']): row for row in globals_[ordinal]})[0]
            def validate(response, _wire):
                if [row.chapter_ordinal for row in response.blueprints] != [ordinal]:
                    return f'本轮只可返回第 {ordinal} 章'
                try:
                    compile_row(response.blueprints[0])
                except (ValueError, TypeError, TerminalState) as exc:
                    return str(exc)
                return None
            rows, violations, errors = extract._run_chapter_requests(ctx, client,
                [(ordinal, system, user)], model_cls=PlotRevisionResponse,
                validator_of=lambda _ordinal: validate, phase='repair',
                architecture='plot-blueprint-v1', checkpoints=True, restore_checkpoints=True)
            extract._raise_response_repair(ctx, errors, architecture='plot-blueprint-v1')
            if errors:
                raise errors[0]
            if violations or not rows:
                raise ProvenanceRefused('定向修订回答越出本章范围，未替换任何正式章')
            response = PlotRevisionResponse.model_validate({'blueprints': rows}).blueprints[0]
            edits.append(parse(response))
            chapter = compile_row(response)
        compiled.append(chapter)
        carried = frozenset(chapter.carry_out_refs)
    reason = '操作者请求按原文定向修订'
    request = PlotEdit(actor='stage2.blueprint Executor', reason=reason,
                       review_token=template.review_token, chapters=edits)
    prepared = plot_edit.prepare_edit(ctx, request, allow_unchanged=True)
    purposes = {f'{ctx.node}:ch{ordinal}': ordinal for ordinal in chapters}
    delta = ctx.llm_provenance(BlueprintProvenance,
        prompts={'BLUEPRINT_PLOT_REVISION_SYS': system}, model=client.model,
        adopted_models=client.accepted_models(*purposes), scope_violations=[],
        context=previous.provenance.context.model_dump())
    provenance = BlueprintProvenance.model_validate({
        **delta.model_dump(), 'prompts': {**previous.provenance.prompts, **delta.prompts},
        'adopted_models': sorted(set(previous.provenance.adopted_model_ids()) | set(delta.adopted_model_ids())),
        'usage': combine_usage(previous.provenance.usage, delta.usage),
        'plot_revisions': prepared.artifact.provenance.plot_revisions})
    artifact = prepared.artifact.model_copy(update={'provenance': provenance})
    if prepared.hard_hits:
        path = extract._write_invalid(store, extract.BLUEPRINT_REL, artifact)
        raise GateBlocked(f'定向修订仍有结构问题：{list(prepared.hard_hits)}；可免费编辑或重验问题稿',
                          invalid_path=path)
    candidate = artifact.model_dump(mode='json', by_alias=True)
    if inputs != read_input_digests(store):
        raise ProvenanceRefused('定向修订期间输入变化，已购响应保留，拒绝按新输入提交')
    adoptions = extract._accepted_adoptions(client, purposes)
    stage2_runs.prepare_commit(ctx, artifact=extract.BLUEPRINT_REL,
        artifact_fingerprint=prov.projection_fingerprint(candidate), artifact_payload=candidate,
        finalization_payload=_finalization(ctx, store, chapters=chapters, review_token=review_token,
            candidate=candidate, live_before=extract._blueprint_live_state(store)), adoptions=adoptions)
    # 使用同一尾窗实现，正常提交和重启提交具有完全相同的验证与收尾。
    return finish_commit(ctx, chapters=chapters, review_token=review_token)
