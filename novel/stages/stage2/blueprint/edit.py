"""完整剧情的零模型订正。蓝图与身份账同文件提交，重放不会新增轮次或自动接受。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from contracts.artifacts import STAGE2_BLUEPRINT_EDIT, STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema import encode_artifact
from contracts.schema.plot import PlotBlueprint, PlotNodeTombstone
from contracts.schema.plot_edit import (
    PlotEdit, PlotEditPreview, PlotIdentityChange, PlotRevisionRecord,
)
from contracts.schema.stage2 import ChapterBlueprints
from novel import paths
from novel.consumption import consumed_digest, plot_global_projection
from novel.errors import ProvenanceRefused, TerminalState, UpstreamMissing
from novel.store import ArtifactStore
from novel.stages.stage2.blueprint.compile import compile_chapter, response_projection, state_materials, validate_book

REL, REQUEST_REL = STAGE2_CHAPTER_BLUEPRINTS, STAGE2_BLUEPRINT_EDIT
COLLECTIONS = (('beats', 'beat_id', 'beat_key', 'b'),
               ('states', 'state_id', 'state_key', 'st'),
               ('constraints', 'constraint_id', 'constraint_key', 'c'),
               ('actor_bindings', 'presentation_id', 'presentation_key', 'ap'))


def subject(artifact: ChapterBlueprints) -> str:
    return consumed_digest([chapter.model_dump(mode='json') for chapter in artifact.blueprints])


def _current(store):
    """读取显式修订基线，允许已被当前 Global/称名变更标旧的自身产物。

    这不是接受旧结论：Store 仍验证当前 schema 与来源根，逐章源文摘要和证据
    必须与当前 MainText 完全一致。编辑 token 另绑定当前输入，候选在写回前按
    当前可见人物、Global 与章集合重编译/检查；审阅接受没有随基线继承。
    """
    from contracts.schema.stage1 import MainText
    from contracts.artifacts import STAGE1_MAIN_TEXT
    from novel.stages.stage2.blueprint.compile import validate_book_sources
    rel = paths.invalid_of(REL) if store.exists(paths.invalid_of(REL)) else REL
    artifact = store.read_own(ChapterBlueprints, rel)[0]
    main = store.read(MainText, STAGE1_MAIN_TEXT)
    try:
        validate_book_sources(artifact.blueprints, {row.chapter_ordinal: row.text for row in main.root})
    except ValueError as exc:
        from novel.errors import ArtifactUnreadable
        raise ArtifactUnreadable('蓝图编辑基线的源文或证据尚未闭合：' + str(exc)) from exc
    return rel, artifact


def _token(store, artifact) -> str:
    from novel.stages.stage2.blueprint.inputs import read_input_digests
    return 'bpe2_' + consumed_digest({
        'subject': subject(artifact), 'inputs': read_input_digests(store),
        'history': [row.model_dump(mode='json') for row in artifact.provenance.plot_revisions],
        'code': store.ctx.code,
    })


def edit_template(ctx) -> dict:
    from contracts.schema.stage1 import MainText
    from novel.stages.stage2 import extract
    store = ArtifactStore(ctx)
    _, artifact = _current(store)
    sources = {row.chapter_ordinal: row.text for row in store.read(MainText, extract.MAIN_TEXT).root}
    chapters = []
    for chapter in artifact.blueprints:
        response = response_projection(chapter.model_dump(mode='json'), sources[chapter.chapter_ordinal])
        identities = []
        for collection, field, key, _ in COLLECTIONS:
            for before, after in zip(getattr(chapter, collection), response[collection], strict=True):
                identities.append(dict(collection=collection, kind='preserve',
                    old_refs=[getattr(before, field)], new_keys=[after[key]]))
        chapters.append(dict(chapter=response, identities=identities))
    request = PlotEdit(actor='待填·编辑人', reason='待填·原文依据与真实错漏',
                       review_token=_token(store, artifact), chapters=chapters)
    return encode_artifact(REQUEST_REL, request.model_dump(mode='json'))


def edit_template_json(ctx) -> str:
    return json.dumps(edit_template(ctx), ensure_ascii=False, indent=2) + '\n'


def _read_request(store, ctx, supplied: str) -> PlotEdit:
    candidate = Path(supplied) if supplied else Path(ctx.novel_dir) / REQUEST_REL
    if not candidate.is_absolute():
        candidate = Path(ctx.novel_dir) / paths.edit_rel(ctx.novel_dir, supplied, REQUEST_REL)
    resolved = candidate.resolve()
    allowed = (Path(ctx.novel_dir).resolve(),
               (paths.service_dir(ctx.novel_dir.parent) / 'edits' / ctx.sid).resolve())
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ProvenanceRefused('Blueprint 编辑输入必须位于当前书或本书 service 编辑目录')
    if not resolved.is_file():
        raise UpstreamMissing('缺 Blueprint 编辑输入；先输出当前模板并填写')
    return store.read_external(PlotEdit, REQUEST_REL, resolved)


def _rewrite_refs(value, mapping):
    """只改正式引用字段；正文恰好等于某个 ID 时仍按原样保留。"""
    if isinstance(value, list):
        return [_rewrite_refs(row, mapping) for row in value]
    if not isinstance(value, dict):
        return value
    scalar = {'beat_id', 'state_id', 'constraint_id', 'beat_ref', 'before_beat_ref',
              'after_beat_ref', 'target_beat_ref', 'state_ref', 'at_beat_ref',
              'presentation_id', 'presentation_ref', 'public_state_ref'}
    many = {'entry_state_refs', 'carry_out_refs', 'supersedes_state_refs', 'beat_refs'}
    return {key: (mapping.get(item, item) if key in scalar else
                  [mapping.get(ref, ref) for ref in item] if key in many else
                  _rewrite_refs(item, mapping)) for key, item in value.items()}


def _compile_edit(edit, previous, source, *, available, chars, globals_, retired,
                  character_bindings=None, actor_bindings=None, global_subjects=None):
    old = {collection: {getattr(row, field): row for row in getattr(previous, collection)}
           for collection, field, _, _ in COLLECTIONS}
    raw = edit.chapter.model_dump(mode='json')
    starts = {}
    all_used = set(retired) | {ref for values in old.values() for ref in values}
    for _, _, _, prefix in COLLECTIONS:
        matching = [int(ref.rsplit('_', 1)[1]) for ref in all_used
                    if ref.startswith(f'{prefix}{previous.chapter_ordinal:02d}_')]
        starts[prefix] = max(matching, default=0) + 1
    compiled = compile_chapter(edit.chapter, source, allowed_char_ids=chars,
        allowed_entry_state_refs=available, allowed_global_refs=globals_, next_ids=starts,
        character_bindings=character_bindings, allowed_actor_bindings=actor_bindings,
        global_subjects=global_subjects)
    mapping, changes, tombstones = {}, [], []
    for collection, field, key, _ in COLLECTIONS:
        outputs = {row[key]: getattr(formal, field) for row, formal in zip(
            raw[collection], getattr(compiled, collection), strict=True)}
        operations = [row for row in edit.identities if row.collection == collection]
        consumed = [ref for op in operations for ref in op.old_refs]
        assigned = [ref for op in operations for ref in op.new_keys]
        if (len(set(consumed)) != len(consumed) or set(consumed) != set(old[collection])
                or len(set(assigned)) != len(assigned) or set(assigned) != set(outputs)):
            raise TerminalState(f'{collection} 身份操作必须不重不漏覆盖旧主体与候选局部标识')
        for operation in operations:
            new_refs = [outputs[key] for key in operation.new_keys]
            if operation.kind == 'preserve':
                mapping[new_refs[0]] = operation.old_refs[0]
                new_refs = list(operation.old_refs)
            else:
                for ref in operation.old_refs:
                    tombstones.append(PlotNodeTombstone(
                        node_id=ref, subject_sha256=consumed_digest(old[collection][ref].model_dump(mode='json')),
                        superseded_by=new_refs, reason='显式拆合或删除'))
            changes.append(PlotIdentityChange(chapter_ordinal=previous.chapter_ordinal,
                collection=collection, kind=operation.kind, old_refs=operation.old_refs, new_refs=new_refs))
    result = PlotBlueprint.model_validate(_rewrite_refs(compiled.model_dump(mode='json'), mapping))
    return result, changes, tombstones


@dataclass(frozen=True)
class PreparedPlotEdit:
    request: PlotEdit
    artifact: ChapterBlueprints
    changed: tuple[int, ...]
    hard_hits: tuple[str, ...]
    record: PlotRevisionRecord

    def preview(self) -> PlotEditPreview:
        return PlotEditPreview(review_token=self.request.review_token,
            result_subject_sha256=self.record.result_subject_sha256, changed_chapters=list(self.changed),
            changes=self.record.changes, tombstones=self.record.tombstones,
            hard_hits=list(self.hard_hits), would_apply=not self.hard_hits)


def prepare_edit(ctx, request: PlotEdit, *, allow_unchanged: bool = False) -> PreparedPlotEdit:
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.stages.stage2 import extract
    from novel.stages.stage2.shared.character_refs import blueprint_inventory_from_projection
    store = ArtifactStore(ctx)
    main = extract._read_main_text(store)
    projection, _ = extract._require_blueprint_character_projection(store)
    inventory = blueprint_inventory_from_projection(projection)
    visible = inventory['visible_character_refs']
    knowledge = store.read(KnowledgeGrounding, extract.GLOBAL_REL).model_dump(mode='json', by_alias=True)
    _, previous = _current(store)
    if request.review_token != _token(store, previous):
        raise ProvenanceRefused('Blueprint 编辑已过期，需重新读取当前材料')
    target = {row.chapter.chapter_ordinal: row for row in request.chapters}
    if not set(target) <= {row.chapter_ordinal for row in previous.blueprints}:
        raise TerminalState('编辑不能增删、重排或拆合原文章节')
    sources = {row['chapter_ordinal']: row['text'] for row in main}
    retired = {row.node_id for event in previous.provenance.plot_revisions for row in event.tombstones}
    retired |= {ref for event in previous.provenance.plot_revisions for row in event.changes for ref in row.new_refs}
    chapters, changes, tombstones, changed = [], [], [], []
    carried = frozenset()
    for chapter in previous.blueprints:
        ordinal = chapter.chapter_ordinal
        if ordinal in target:
            # 问题稿保留在模板中；新候选只能消费上章实际携入的状态及呈现。
            candidate, identities, removed = _compile_edit(target[ordinal], chapter, sources[ordinal],
                available=carried,
                chars=frozenset(visible.get(ordinal, {})),
                globals_=frozenset((row['kind'], row['ref']) for row in plot_global_projection(knowledge, ordinal)),
                retired=retired,
                character_bindings=inventory['character_bindings'].get(ordinal, {}),
                actor_bindings={row['binding']['presentation_id']: row['binding']
                    for material in state_materials(chapters, sorted(carried))
                    for row in material['actor_bindings']},
                global_subjects={(row['kind'], row['ref']): row for row in plot_global_projection(knowledge, ordinal)})
            if candidate != chapter:
                changed.append(ordinal)
                changes.extend(identities)
                tombstones.extend(removed)
            chapters.append(candidate)
        else:
            chapters.append(chapter)
        carried = frozenset(chapters[-1].carry_out_refs)
    validate_book(chapters, sources)
    if not changed and not allow_unchanged:
        raise TerminalState('Blueprint 编辑没有改变任何主体')
    rewritten = ctx.rewrite_provenance(previous.provenance, by='--apply-edit').model_copy(update={'origin': 'human'})
    artifact = ChapterBlueprints(blueprints=chapters, _provenance=rewritten)
    record = PlotRevisionRecord(request_sha256=consumed_digest(request.model_dump(mode='json')),
        before_subject_sha256=subject(previous), result_subject_sha256=subject(artifact),
        actor=request.actor, reason=request.reason, changes=changes,
        tombstones=[row.model_copy(update={'reason': request.reason}) for row in tombstones])
    artifact = artifact.model_copy(update={'provenance': artifact.provenance.model_copy(
        update={'plot_revisions': [*previous.provenance.plot_revisions, record]})})
    outcomes = extract._judge_candidate(knowledge, artifact.model_dump(mode='json', by_alias=True),
                                        main, characters=extract._read_characters(store))
    return PreparedPlotEdit(request, artifact, tuple(changed),
                            tuple(extract._blueprint_hard_hits(outcomes)), record)


def validate_edit(ctx, edit_path: str = '') -> PlotEditPreview:
    return prepare_edit(ctx, _read_request(ArtifactStore(ctx), ctx, edit_path)).preview()


def apply_edit(ctx, edit_path: str = ''):
    from novel.llm import stage2_runs
    from novel.stages.stage2 import extract
    store = ArtifactStore(ctx)
    with stage2_runs.repair_ownership_lock(ctx.novel_dir):
        request = _read_request(store, ctx, edit_path)
        request_sha = consumed_digest(request.model_dump(mode='json'))
        _, current = _current(store)
        # 硬杀可能发生在 formal 原子替换后、旧 INVALID 归档前。只按原请求的
        # 同文件身份账收尾，不让更新的问题稿被历史成功覆盖。
        if store.exists(REL):
            # 旧 formal 可以是本次显式编辑的基线；只有发现原请求的提交记录时才
            # 进入下面要求 fresh 的重放收尾，不能先因基线 stale 封死修订入口。
            formal = store.read_own(ChapterBlueprints, REL)[0]
            landed_record = next((row for row in formal.provenance.plot_revisions
                                 if row.request_sha256 == request_sha), None)
            if landed_record is not None and subject(formal) == landed_record.result_subject_sha256:
                if store.exists(paths.invalid_of(REL)):
                    invalid = store.read_own(ChapterBlueprints, paths.invalid_of(REL), require_current=True)[0]
                    if subject(invalid) != landed_record.before_subject_sha256:
                        raise ProvenanceRefused('已有更新的问题稿，不能按历史编辑将它清除')
                    if store.freshness(REL).state != 'fresh':
                        raise ProvenanceRefused('编辑结果的来源待复验，暂不能清理旧问题稿')
                    store.retire_invalid(REL)
                current = formal
        replay = next((row for row in current.provenance.plot_revisions if row.request_sha256 == request_sha), None)
        if replay is not None:
            if replay.result_subject_sha256 != subject(current):
                raise ProvenanceRefused('该编辑曾已提交，但蓝图又有变化；不能回滚或重复套用')
            if store.freshness(REL).state != 'fresh':
                raise ProvenanceRefused('已提交编辑的输入或验证身份变化，先免费复检再恢复')
            both = extract._Both(store)
            return current, extract._report(ctx, store, both, both.judge(), judged_rel=REL)
        prepared = prepare_edit(ctx, request)
        if prepared.hard_hits:
            raise TerminalState(f'Blueprint 编辑未通过当前结构检查：{list(prepared.hard_hits)}')
        # 所有来源校验和身份账都已完成，最终写回前再核对当前主体和输入。
        _, before = _current(store)
        if request.review_token != _token(store, before):
            raise ProvenanceRefused('Blueprint 编辑预检后输入发生变化')
        ctx.require_observed_roots_current()
        extract._require_blueprint_code_current(ctx, moment='剧情编辑写回前')
        extract._write_formal(store, REL, prepared.artifact)
        landed, _ = store.read_own(ChapterBlueprints, REL, require_current=True)
        if landed.model_dump(mode='json') != prepared.artifact.model_dump(mode='json'):
            raise ProvenanceRefused('剧情编辑写回后与预检候选不一致')
        both = extract._Both(store)
        return landed, extract._report(ctx, store, both, both.judge(), judged_rel=REL)


def submitted_edit_is_current(base: str, sid: str, payload: dict, *,
                              require_action_complete: bool = True) -> bool:
    """区分正式替换已完成与清理尾窗可恢复；不签署 Blueprint 接受决定。"""
    from contracts.schema import decode_artifact
    from novel.orchestrator import approvals, state
    request = PlotEdit.model_validate(decode_artifact(REQUEST_REL, payload).payload)
    store = ArtifactStore(state.readonly_ctx(paths.resolve_novel_dir(base, sid),
                          'stage2.blueprint', approvals._projection_code()))
    if not store.exists(REL) or store.freshness(REL).state != 'fresh':
        return False
    current = store.read_own(ChapterBlueprints, REL, require_current=True)[0]
    digest = consumed_digest(request.model_dump(mode='json'))
    record = next((row for row in current.provenance.plot_revisions
                   if row.request_sha256 == digest and row.result_subject_sha256 == subject(current)), None)
    if record is None:
        return False
    invalid_rel = paths.invalid_of(REL)
    if store.exists(invalid_rel):
        invalid = store.read_own(ChapterBlueprints, invalid_rel, require_current=True)[0]
        return (not require_action_complete
                and subject(invalid) == record.before_subject_sha256)
    return True
