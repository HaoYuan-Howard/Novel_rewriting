# -*- coding: utf-8 -*-
"""Blueprint Commander 决定的知识关联：只读构包、关闭式继承和独立免费物化。

候选是已向审阅者开放的完整剧情主体闭集，不能按字符串相似度替人决定支持关系。
必要知识事件绑定实际证据、候选及消费正文；整书 SHA 只负责本次提交竞态。
关联规则改变不进入 Blueprint 抽取请求，也不改写 Global。
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
import threading
from typing import TYPE_CHECKING, Any

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_KNOWLEDGE_LINKS,
)
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.knowledge_links import (
    KnowledgeLinkHistory, KnowledgeLinkObligation, KnowledgeLinkRecord,
    KnowledgeLinkReview, KnowledgeLinkScope, KnowledgeLinks,
    KnowledgeLinksProvenance, KnowledgeLinkSubmission, ref_keys,
    KnowledgeLinkDecision, RegisteredPlotKnowledgeEvent, validate_identity_resolution,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, FactEvidence
from novel.errors import HumanEditProtected, ProvenanceRefused
from novel import paths
from novel.appendlog import process_append_lock
from novel.llm.agent_runs import canonical_sha256
from novel.store import ArtifactStore

if TYPE_CHECKING:
    from novel.run import RunContext

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def event_registry(history: Iterable[KnowledgeLinkHistory]) -> list[RegisteredPlotKnowledgeEvent]:
    """重放已审计的显式登记；否定裁决可以登记未决事件，不能赋予知识许可。"""
    events: dict[str, RegisteredPlotKnowledgeEvent] = {}
    for record in history:
        for operation in record.submission.event_operations:
            current = events.get(operation.event_id)
            expected = None if current is None else current.event_sha256
            if operation.expected_event_sha256 != expected:
                raise ProvenanceRefused('知识事件历史不是当前身份的合法后继')
            if operation.operation == 'retire' and current is None:
                raise ProvenanceRefused('不能撤销不存在的知识事件')
            body = {
                'event_id': operation.event_id,
                'event': (operation.event if operation.operation == 'put' else current.event).model_dump(mode='json'),
                'retired': operation.operation == 'retire',
                'reason': operation.reason if operation.operation == 'retire' else None,
            }
            events[operation.event_id] = RegisteredPlotKnowledgeEvent(
                **body, event_sha256=canonical_sha256(body))
    active = []
    for row in events.values():
        if not row.retired:
            key = (row.event.kind, row.event.subject_ref.kind, row.event.subject_ref.ref,
                   canonical_sha256(row.event.audience.model_dump() if row.event.audience else None))
            if key in active:
                raise ProvenanceRefused('同一主体与受众的必要许可已登记；应修订原事件而不是另铸并行时间线')
            active.append(key)
    return [events[key] for key in sorted(events)]


def events_for_chapter(events, chapter):
    """事件本章必读；未来读者揭示约束早章，未来人物获知不反向改变早章。"""
    subjects = {('beat', row.beat_id) for row in chapter.beats}
    subjects.update(('state', row.state_id) for row in chapter.states)
    subjects.update(('state', ref) for ref in chapter.entry_state_refs)
    return [row for row in events if row.event.chapter_ordinal == chapter.chapter_ordinal
            or ((row.event.subject_ref.kind, row.event.subject_ref.ref) in subjects
                and (row.event.kind == 'reader_reveal'
                     or row.event.chapter_ordinal < chapter.chapter_ordinal))]


@contextmanager
def _commit_lock(ctx: RunContext):
    target = paths.stage2_knowledge_links_commit_lock_path(ctx.novel_dir)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(target.resolve()).casefold(), threading.RLock())
    with lock, process_append_lock(target):
        yield


def build_scope(
    main: MainText, knowledge: KnowledgeGrounding, blueprints: ChapterBlueprints,
    *, main_sha256: str, knowledge_sha256: str, blueprint_sha256: str,
    chapter_ordinal: int | None = None,
    registered_events: Iterable[RegisteredPlotKnowledgeEvent] = (),
) -> KnowledgeLinkScope:
    """范围只包含必要知识事件；完整语义仍从既有主体读取，不另存 claim。"""
    from contracts.schema.plot import PlotSubjectRef, ActorRef, RelationActors
    from contracts.schema.dependencies import ScopeSelection
    from novel.consumption import select_scope_input
    from novel.stages.stage2.paragraphs import paragraph_range_of
    from novel.stages.stage2.plot_compile import validate_book, actor_refs, state_materials
    texts = {row.chapter_ordinal: row.text for row in main.root}
    chapters = {row.chapter_ordinal: row for row in blueprints.blueprints}
    validate_book(blueprints.blueprints, texts)
    if chapter_ordinal is not None and chapter_ordinal not in chapters:
        raise ProvenanceRefused('知识关联章范围不存在')
    obligations: list[KnowledgeLinkObligation] = []
    events = list(registered_events)

    def refs(ordinal: int) -> list[PlotSubjectRef]:
        row = chapters[ordinal]
        return [*(PlotSubjectRef(kind='beat', ref=beat.beat_id) for beat in row.beats),
                *(PlotSubjectRef(kind='state', ref=state.state_id) for state in row.states)]

    def add(kind: str, ref: str, chapter: int, evidence: Iterable[FactEvidence],
            subject: object, *, entry: bool = False,
            subject_ref: PlotSubjectRef | None = None, audience=None) -> None:
        if chapter_ordinal is not None and chapter != chapter_ordinal:
            return
        proof = list(evidence)
        if chapter not in chapters or any(row.chapter_ordinal not in texts for row in proof):
            raise ProvenanceRefused(f'{ref} 的知识时点或证据章不在当前源文中')
        domain = ('secret' if kind in ('secret_reveal', 'secret_setup')
                  else 'world_rule' if kind in ('world_rule', 'world_rule_reveal')
                  else 'relation' if kind in ('relation', 'relation_visibility')
                  else kind)
        target = subject_ref or PlotSubjectRef(kind=domain, ref=ref)
        proof_chapters = {row.chapter_ordinal for row in proof} | {chapter}
        candidates = refs(chapter)
        entry_candidates = [r for ordinal in sorted(proof_chapters) for r in refs(ordinal)] if entry else []
        if entry and (target.kind, target.ref) not in {(r.kind, r.ref) for r in entry_candidates}:
            entry_candidates.append(target)
        candidate_chapters = proof_chapters if entry else {chapter}
        ranges = {}
        for row in proof:
            if row.chapter_ordinal not in candidate_chapters:
                start, end = paragraph_range_of(texts[row.chapter_ordinal], row.source.start, row.source.end)
                ranges.setdefault(row.chapter_ordinal, set()).add(f'p:{start}:{end}')
        actor_support = state_materials(blueprints.blueprints,
            [r for r in chapters[chapter].entry_state_refs if not any(
                row.state_id == r for row in chapters[chapter].states)])
        actor_material = [chapters[chapter], *actor_support]
        candidate_actors = {actor.key(): actor for material in actor_material for actor in actor_refs(material)}
        identity = {
            'contract': 'stage2-knowledge-links-v3', 'kind': kind, 'knowledge_ref': ref,
            'chapter_ordinal': chapter, 'subject': subject,
            'audience': None if audience is None else audience.model_dump(mode='json'),
            'actor_support': actor_support,
            'evidence': [row.model_dump(mode='json') for row in proof],
            'candidate_chapters': {str(o): chapters[o].model_dump(mode='json')
                                   for o in sorted(candidate_chapters)},
            'source_chapters': {str(o): texts[o] for o in sorted(candidate_chapters)},
            'source_ranges': [select_scope_input(ScopeSelection(
                input_rel=STAGE1_MAIN_TEXT, projection='source_ranges', chapter_ordinal=o,
                selected_ids=tuple(sorted(refs))), main.model_dump(mode='json'))
                for o, refs in sorted(ranges.items())],
        }
        obligations.append(KnowledgeLinkObligation(
            obligation_id=f'{kind}:{ref}:{chapter}', kind=kind,
            knowledge_ref=ref, subject_ref=target, audience=audience,
            chapter_ordinal=chapter, subject_sha256=canonical_sha256(identity),
            candidate_subject_refs=candidates, entry_subject_refs=entry_candidates,
            candidate_actors=[candidate_actors[key] for key in sorted(candidate_actors)]))

    def proof_of(ids):
        proof = []
        for evidence_id in ids:
            anchor = knowledge.evidence_anchors.get(evidence_id)
            if anchor is None or anchor.chapter_ordinal not in texts:
                raise ProvenanceRefused('Global 共享证据不在当前源文闭集')
            text = texts[anchor.chapter_ordinal][anchor.start:anchor.end]
            from hashlib import sha256
            if (not 0 <= anchor.start < anchor.end <= len(texts[anchor.chapter_ordinal])
                    or sha256(text.encode('utf-8')).hexdigest() != anchor.text_sha256):
                raise ProvenanceRefused('Global 共享证据与源文字节不一致')
            proof.append(FactEvidence.model_validate({'chapter_ordinal': anchor.chapter_ordinal,
                'source': {'start': anchor.start, 'end': anchor.end, 'text': text}}))
        return proof

    relations = {row.relation_id: row for row in knowledge.relations}
    versions = {(rule.rule_id, version.version_id): version for rule in knowledge.world_rules
                for version in rule.versions}
    for secret in knowledge.arc_secrets:
        target = (relations[secret.fact_ref.relation_id] if secret.fact_ref.kind == 'relation'
                  else versions[secret.fact_ref.rule_id, secret.fact_ref.version_id]) if secret.fact_ref else secret
        proof = proof_of(target.evidence_refs)
        data = {'secret': secret.model_dump(mode='json'), 'fact': target.model_dump(mode='json')}
        add('secret_reveal', secret.secret_id, secret.revealed_from, proof, data)
        for chapter in secret.setup_chapters:
            add('secret_setup', secret.secret_id, chapter, proof, data)
    for rule in knowledge.world_rules:
        for version in rule.versions:
            ref = f'{rule.rule_id}/{version.version_id}'
            data = {'kind': rule.kind, 'version': version.model_dump(mode='json')}
            proof = proof_of(version.evidence_refs)
            add('world_rule', ref, version.effective_from, proof, data, entry=True)
            add('world_rule_reveal', ref, version.revealed_from, proof, data)
    for relation in knowledge.relations:
        data = relation.model_dump(mode='json')
        proof = proof_of(relation.evidence_refs)
        # 这是内部事实身份；真正消费必须从 Blueprint 的显式 relation use 取得呈现端点。
        target = PlotSubjectRef(kind='relation', ref=relation.relation_id,
            relation_actors=RelationActors(subject=ActorRef(char_id=relation.subject_id),
                                          object=ActorRef(char_id=relation.object_id)))
        add('relation', relation.relation_id, relation.effective_from, proof, data, entry=True, subject_ref=target)
        add('relation_visibility', relation.relation_id, relation.revealed_from, proof, data, subject_ref=target)
    setting = knowledge.setting
    for ref in ('era', 'social_context'):
        value = getattr(setting, ref)
        if value is not None:
            proof = proof_of(getattr(setting, ref + '_evidence_refs'))
            add('setting', ref, min(row.chapter_ordinal for row in proof), proof,
                {'value': value, 'evidence_refs': getattr(setting, ref + '_evidence_refs')}, entry=True)
    # 只有 C 明确登记的必要传播事件进入闭集；普通叙述和 State 不自动产生披露义务。
    known_subjects = {(kind, getattr(row, field)): (chapter, row)
        for chapter in blueprints.blueprints
        for kind, field, rows in (('beat', 'beat_id', chapter.beats), ('state', 'state_id', chapter.states))
        for row in rows}
    for registered in events:
        event = registered.event
        if registered.retired or chapter_ordinal is not None and event.chapter_ordinal != chapter_ordinal:
            continue
        target = known_subjects.get((event.subject_ref.kind, event.subject_ref.ref))
        source_chapter, subject = target if target is not None else (None, None)
        proof = []
        for item in event.evidence_refs:
            if item.chapter_ordinal not in chapters:
                raise ProvenanceRefused('知识事件证据章不在当前原文中')
            evidence_chapter = chapters[item.chapter_ordinal]
            anchors = {row.evidence_id: row for row in evidence_chapter.evidence_anchors}
            if item.evidence_ref in anchors:
                anchor = anchors[item.evidence_ref]
                start, end = anchor.start, anchor.end
            else:
                from novel.stages.stage2.paragraphs import span_of
                parts = item.evidence_ref.split(':')
                if len(parts) != 3 or parts[0] != 'p':
                    raise ProvenanceRefused('知识事件证据没有当前锚点或明确段范围')
                start, end = span_of(texts[item.chapter_ordinal], int(parts[1]), int(parts[2]))
            proof.append(FactEvidence.model_validate({'chapter_ordinal': item.chapter_ordinal,
                'source': {'start': start, 'end': end, 'text': texts[item.chapter_ordinal][start:end]}}))
        add(event.kind, registered.event_id, event.chapter_ordinal, proof,
            {'registration': registered.model_dump(mode='json'),
             'subject': None if subject is None else subject.model_dump(mode='json'),
             'subject_evidence': [] if subject is None else [
                 {'start_para': anchor.start_para, 'end_para': anchor.end_para,
                  'text': texts[source_chapter.chapter_ordinal][anchor.start:anchor.end]}
                 for anchor in source_chapter.evidence_anchors if anchor.evidence_id in subject.evidence_refs]},
            subject_ref=event.subject_ref, audience=event.audience)
        obligation = obligations[-1]
        if subject is not None and event.subject_ref not in obligation.candidate_subject_refs:
            obligations[-1] = obligation.model_copy(update={
                'candidate_subject_refs': [*obligation.candidate_subject_refs, event.subject_ref]})
    obligations.sort(key=lambda row: row.obligation_id)
    semantic = {
        'contract': 'stage2-knowledge-links-v3', 'chapter_ordinal': chapter_ordinal,
        'obligations': [row.model_dump(mode='json') for row in obligations],
        'registered_events': [row.model_dump(mode='json') for row in (
            events if chapter_ordinal is None else events_for_chapter(events, chapters[chapter_ordinal]))],
    }
    return KnowledgeLinkScope(
        **semantic, scope_sha256=canonical_sha256(semantic),
        main_text_sha256=main_sha256, knowledge_sha256=knowledge_sha256,
        blueprint_sha256=blueprint_sha256)


def read_scope(store: ArtifactStore, *, blueprint_rel: str = STAGE2_CHAPTER_BLUEPRINTS,
               consuming: bool = False, chapter_ordinal: int | None = None) -> KnowledgeLinkScope:
    """生产物化显式登记三个输入；只读审阅不污染 Blueprint E 的消费边。"""
    sources = ((STAGE1_MAIN_TEXT, MainText),
               (STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding),
               (blueprint_rel, ChapterBlueprints))
    before = {rel: store.full_sha256_or_none(rel) for rel, _model in sources}
    values: list[Any] = []
    for rel, model in sources:
        values.append(store.read(model, rel) if consuming else
                      store.read_own(model, rel, require_current=True)[0])
    if before != {rel: store.full_sha256_or_none(rel) for rel, _model in sources}:
        raise HumanEditProtected('构造知识关联期间源文、知识或 Blueprint 漂移')
    if any(value is None for value in before.values()):
        raise ProvenanceRefused('知识关联不能绑定缺失的输入')
    from novel.orchestrator import approvals
    events = event_registry(approvals.knowledge_link_history(store.ctx.novel_dir.parent, store.ctx.sid))
    return build_scope(*values, main_sha256=before[STAGE1_MAIN_TEXT],
                       knowledge_sha256=before[STAGE2_GLOBAL_GROUNDING],
                       blueprint_sha256=before[blueprint_rel], chapter_ordinal=chapter_ordinal,
                       registered_events=events)


def validate_submission(scope: KnowledgeLinkScope, submission: KnowledgeLinkSubmission) -> None:
    if submission.scope_sha256 != scope.scope_sha256:
        raise HumanEditProtected('知识关联范围已变化，旧决定不落盘')
    subjects = {row.obligation_id: row for row in scope.obligations}
    for decision in submission.decisions:
        subject = subjects.get(decision.obligation_id)
        if subject is None or subject.subject_sha256 != decision.expected_subject_sha256:
            raise HumanEditProtected('知识关联引用了过期或未开放的义务')
        if decision.resolution.kind == 'unresolved':
            continue
        if (decision.resolution.kind == 'at_beats' and not decision.resolution.trigger_beat_ref.startswith(
                f'b{subject.chapter_ordinal:02d}_')):
            raise HumanEditProtected('知识触发点必须属于登记的目标章；来源证据可以跨章')
        try:
            validate_identity_resolution(subject, decision.resolution)
        except ValueError as exc:
            raise HumanEditProtected(str(exc)) from exc
        allowed = (subject.entry_subject_refs if decision.resolution.kind == 'at_entry'
                   else subject.candidate_subject_refs)
        if not ref_keys(decision.resolution.subject_refs) <= ref_keys(allowed):
            raise HumanEditProtected('知识关联主体或 at_entry 时点不在当前关闭范围')
        if (subject.kind in ('reader_reveal', 'character_knowledge')
                and subject.subject_ref.key() not in ref_keys(decision.resolution.subject_refs)):
            raise HumanEditProtected('必要剧情许可必须引用其完整主体，缺失主体只能修订或明确撤销事件')


def materialize_submission(scope: KnowledgeLinkScope, resulting: KnowledgeLinkScope,
                           submission: KnowledgeLinkSubmission) -> KnowledgeLinkSubmission:
    """C 同轮登记和关联的代码展开；不发明接受，也不要求模型计算新事件摘要。"""
    validate_submission(scope, submission)
    decisions = {row.obligation_id: row for row in submission.decisions}
    obligations = {row.knowledge_ref: row for row in resulting.obligations
                   if row.kind in ('reader_reveal', 'character_knowledge')}
    changed = {row.event_id for row in submission.event_operations}
    if any(row.knowledge_ref in changed and row.obligation_id in decisions for row in scope.obligations):
        raise HumanEditProtected('同轮修改的事件须在 put 内给关联，不能同时提交旧身份决定')
    for operation in submission.event_operations:
        if operation.operation == 'put':
            obligation = obligations[operation.event_id]
            decisions[obligation.obligation_id] = KnowledgeLinkDecision(
                obligation_id=obligation.obligation_id, expected_subject_sha256=obligation.subject_sha256,
                resolution=operation.resolution)
    result = submission.model_copy(update={'scope_sha256': resulting.scope_sha256,
                                            'decisions': list(decisions.values())})
    validate_submission(resulting, result)
    return result


def inherited_records(scope: KnowledgeLinkScope,
                      history: Iterable[KnowledgeLinkHistory]) -> list[KnowledgeLinkRecord]:
    subjects = {row.obligation_id: row for row in scope.obligations}
    chosen: dict[str, KnowledgeLinkRecord | None] = {}
    for event in reversed(list(history)):
        for decision in event.submission.decisions:
            subject = subjects.get(decision.obligation_id)
            if subject is None or decision.obligation_id in chosen:
                continue
            # 最新否定/未决不能被较早的同主体接受洗白。
            chosen[decision.obligation_id] = None
            if (decision.expected_subject_sha256 != subject.subject_sha256
                    or not event.accepted or decision.resolution.kind == 'unresolved'):
                continue
            validate_identity_resolution(subject, decision.resolution)
            allowed = (subject.entry_subject_refs if decision.resolution.kind == 'at_entry'
                       else subject.candidate_subject_refs)
            if not ref_keys(decision.resolution.subject_refs) <= ref_keys(allowed):
                raise ProvenanceRefused('历史知识关联来源与当前义务不相容，不能降格为空结果')
            if (decision.resolution.kind == 'at_beats' and not decision.resolution.trigger_beat_ref.startswith(
                    f'b{subject.chapter_ordinal:02d}_')) or (
                    subject.kind in ('reader_reveal', 'character_knowledge') and
                    subject.subject_ref.key() not in ref_keys(decision.resolution.subject_refs)):
                raise ProvenanceRefused('历史知识事件的触发章或完整主体不相容')
            chosen[decision.obligation_id] = KnowledgeLinkRecord(
                **decision.model_dump(), actor=event.submission.actor,
                reason=event.submission.reason, source_decision_sha256=event.decision_sha256)
    return [chosen[key] for key in sorted(chosen) if chosen[key] is not None]


def review(scope: KnowledgeLinkScope, history: Iterable[KnowledgeLinkHistory]) -> KnowledgeLinkReview:
    inherited = inherited_records(scope, history)
    covered = {row.obligation_id for row in inherited}
    return KnowledgeLinkReview(
        scope=scope, inherited=inherited,
        pending_obligation_ids=[row.obligation_id for row in scope.obligations
                                if row.obligation_id not in covered])


def require_complete(scope: KnowledgeLinkScope,
                     history: Iterable[KnowledgeLinkHistory]) -> list[KnowledgeLinkRecord]:
    current = review(scope, history)
    if current.pending_obligation_ids:
        raise HumanEditProtected('知识关联仍有待审义务：' + '、'.join(current.pending_obligation_ids))
    return current.inherited


def run_close_review(ctx: RunContext) -> KnowledgeLinks:
    """同一免费汇合使用冻结代码坐标；入口、写审批及返回仍复验真实代码。"""
    from novel.orchestrator import approvals

    if ctx.node != 'stage2.blueprint':
        raise ProvenanceRefused('知识关联只由 stage2.blueprint producer 物化')
    with approvals.stage2_closure_projection(ctx.code):
        return _close_review(ctx)


def _close_review(ctx: RunContext) -> KnowledgeLinks:
    """已有 Commander 接受的合法后继；任一落盘窗口中断均重放此免费入口。"""
    from novel.orchestrator import approvals

    if ctx.node != 'stage2.blueprint':
        raise ProvenanceRefused('知识关联只由 stage2.blueprint producer 物化')
    approvals.close_stage2_inherited(ctx.novel_dir.parent, ctx.sid, node='stage2.blueprint')
    with _commit_lock(ctx):
        approvals.require_stage2_node_accepted(ctx.novel_dir.parent, ctx.sid, 'stage2.blueprint')
        store = ArtifactStore(ctx)
        scope = read_scope(store, consuming=True)
        history, decision_sha = approvals.current_knowledge_link_decisions(
            ctx.novel_dir.parent, ctx.sid)
        records = require_complete(scope, history)
        artifact = KnowledgeLinks(
            scope=scope, links=records,
            _provenance=ctx.free_provenance(
                KnowledgeLinksProvenance, decision_sha256=decision_sha))
        # 不为重复点击制造新来源账。旧文件损坏保持 unknown，不覆盖成“恢复成功”。
        if store.exists(STAGE2_KNOWLEDGE_LINKS):
            old = store.read_own(KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS)[0]
            freshness = store.freshness(STAGE2_KNOWLEDGE_LINKS)
            if freshness.state == 'unknown':
                raise ProvenanceRefused('既有知识关联来源未知，不能用免费重算覆盖其来源问题')
            if (old.scope == scope and old.links == records
                    and old.provenance.decision_sha256 == decision_sha
                    and freshness.state == 'fresh'):
                store.read_own(KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS, require_current=True)
                artifact = old
            else:
                store.write(STAGE2_KNOWLEDGE_LINKS, artifact)
        else:
            store.write(STAGE2_KNOWLEDGE_LINKS, artifact)
        if read_scope(store) != scope:
            raise HumanEditProtected('物化知识关联期间输入发生变化，审批保持关闭')
        require_current(ctx)
    # approval 文件锁与书级写锁分别持有，避免 Windows 非重入锁死。
    approvals.close_stage2_accepted(ctx.novel_dir.parent, ctx.sid)
    return artifact


def require_current(ctx: RunContext, *, consuming: bool = False) -> KnowledgeLinks:
    """正式产物、当前义务和 durable typed 来源三方一致才授予知识消费。"""
    from novel.orchestrator import approvals

    store = ArtifactStore(ctx)
    scope = read_scope(store)
    artifact = (store.read(KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS) if consuming else
                store.read_own(KnowledgeLinks, STAGE2_KNOWLEDGE_LINKS,
                               require_current=True)[0])
    history, decision_sha = approvals.current_knowledge_link_decisions(
        ctx.novel_dir.parent, ctx.sid)
    if (artifact.scope != scope or artifact.links != require_complete(scope, history)
            or artifact.provenance.decision_sha256 != decision_sha):
        raise ProvenanceRefused('知识关联与当前输入或已保存决定不一致；运行 --close-review 免费恢复')
    return artifact
