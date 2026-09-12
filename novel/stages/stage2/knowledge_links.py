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
) -> KnowledgeLinkScope:
    """范围只包含必要知识事件；完整语义仍从既有主体读取，不另存 claim。"""
    from contracts.schema.plot import PlotSubjectRef
    from contracts.schema.dependencies import ScopeSelection
    from novel.consumption import select_scope_input
    from novel.stages.stage2.paragraphs import paragraph_range_of
    from novel.stages.stage2.plot_compile import validate_book
    texts = {row.chapter_ordinal: row.text for row in main.root}
    chapters = {row.chapter_ordinal: row for row in blueprints.blueprints}
    validate_book(blueprints.blueprints, texts)
    if chapter_ordinal is not None and chapter_ordinal not in chapters:
        raise ProvenanceRefused('知识关联章范围不存在')
    obligations: list[KnowledgeLinkObligation] = []

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
        domain = ('secret' if kind in ('secret_reveal', 'secret_setup', 'knowledge_event')
                  else 'world_rule' if kind in ('world_rule', 'world_rule_reveal')
                  else 'relation' if kind in ('relation', 'relation_visibility')
                  else kind)
        target = subject_ref or PlotSubjectRef(kind=domain, ref=ref.split('/')[0])
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
        identity = {
            'contract': 'stage2-knowledge-links-v2', 'kind': kind, 'knowledge_ref': ref,
            'chapter_ordinal': chapter, 'subject': subject,
            'audience': None if audience is None else audience.model_dump(mode='json'),
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
            candidate_subject_refs=candidates, entry_subject_refs=entry_candidates))

    for secret in knowledge.canon.secrets:
        data = secret.model_dump(mode='json')
        reveal = {key: data[key] for key in ('secret_id', 'fact', 'fact_evidence',
                                            'first_reveal_chapter')}
        add('secret_reveal', secret.secret_id, secret.first_reveal_chapter,
            [secret.fact_evidence], reveal)
        if secret.setup.kind == 'foreshadowed':
            for chapter in secret.setup.chapters:
                add('secret_setup', secret.secret_id, chapter, [secret.fact_evidence], reveal)
        for event in secret.knowledge_events:
            who = (event.subject.char_id if event.subject.kind == 'character'
                   else 'group_' + canonical_sha256(event.subject.model_dump())[:24])
            add('knowledge_event', f'{secret.secret_id}/{who}', event.known_from_chapter,
                [event.evidence], {'secret_id': secret.secret_id, 'fact': secret.fact,
                                   'event': event.model_dump(mode='json')},
                entry=True, audience=event.subject)
    for rule in knowledge.canon.world_rules:
        for version in rule.versions:
            ref = f'{rule.rule_id}/{version.effective_from_chapter}'
            data = {'kind': rule.kind, 'version': version.model_dump(mode='json')}
            add('world_rule', ref, version.effective_from_chapter,
                [version.desc_evidence], data, entry=True)
            add('world_rule_reveal', ref, version.desc_evidence.chapter_ordinal,
                [version.desc_evidence], data)
    for relation in knowledge.entities.relations:
        data = relation.model_dump(mode='json')
        add('relation', relation.relation_id, relation.valid_from_chapter,
            [relation.predicate_evidence], data, entry=True)
        if relation.visibility.kind == 'public':
            add('relation_visibility', relation.relation_id, relation.visibility.from_chapter,
                [relation.predicate_evidence], data)
    for cid, state in knowledge.entities.character_states.items():
        add('character_state', cid, state.fate_evidence.chapter_ordinal,
            [state.fate_evidence], state.model_dump(mode='json'))
    setting = knowledge.canon.setting
    for ref, value, evidence in (
        ('时代背景', setting.era, setting.era_evidence),
        ('世界观', setting.worldview, setting.worldview_evidence),
    ):
        add('setting', ref, evidence.chapter_ordinal, [evidence],
            {'value': value, 'evidence': evidence.model_dump(mode='json')}, entry=True)
    # 稀疏状态的性质不授予读者知情；其首次表达仍需要 C 显式关联到节点。
    for chapter in blueprints.blueprints:
        for state in chapter.states:
            add('state_reveal', state.state_id, chapter.chapter_ordinal, [],
                state.model_dump(mode='json'),
                subject_ref=PlotSubjectRef(kind='state', ref=state.state_id))
    obligations.sort(key=lambda row: row.obligation_id)
    semantic = {
        'contract': 'stage2-knowledge-links-v2', 'chapter_ordinal': chapter_ordinal,
        'obligations': [row.model_dump(mode='json') for row in obligations],
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
    return build_scope(*values, main_sha256=before[STAGE1_MAIN_TEXT],
                       knowledge_sha256=before[STAGE2_GLOBAL_GROUNDING],
                       blueprint_sha256=before[blueprint_rel], chapter_ordinal=chapter_ordinal)


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
        allowed = (subject.entry_subject_refs if decision.resolution.kind == 'at_entry'
                   else subject.candidate_subject_refs)
        if not ref_keys(decision.resolution.subject_refs) <= ref_keys(allowed):
            raise HumanEditProtected('知识关联主体或 at_entry 时点不在当前关闭范围')


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
            allowed = (subject.entry_subject_refs if decision.resolution.kind == 'at_entry'
                       else subject.candidate_subject_refs)
            if not ref_keys(decision.resolution.subject_refs) <= ref_keys(allowed):
                raise ProvenanceRefused('历史知识关联来源与当前义务不相容，不能降格为空结果')
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
