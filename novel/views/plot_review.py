"""Blueprint 范围审阅的只读快照：进度只复算消费身份，模型材料按请求构建。

审批 owner 持有写锁并保存决定；这里不写产物、不产生接受。文件摘要保护本轮
竞态，语义摘要保护范围复用，二者不能混成整书失效开关。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any, Sequence

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.api import (
    BlueprintReviewBinding, BlueprintReviewWorkpack, Stage2NodeReviewDecision,
)
from contracts.schema.dependencies import ScopeSelection
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.knowledge_links import KnowledgeLinkHistory, KnowledgeLinkScope
from contracts.schema.plot_review import (
    PlotIssueRecord, PlotReviewProgress, PlotScopeAcceptance, PlotScopeStatus,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel import paths, prov
from novel.consumption import compile_scope_inputs, consumed_digest, select_scope_input
from novel.errors import ApprovalRequired, ArtifactUnreadable, HumanEditProtected
from novel.store import ArtifactStore
from novel.stages.stage2 import knowledge_links, plot_review
from novel.stages.stage2.plot_compile import validate_book


@dataclass
class PlotReviewSnapshot:
    store: ArtifactStore
    main: MainText
    blueprints: ChapterBlueprints
    knowledge: KnowledgeGrounding
    blueprint_rel: str
    hashes: dict[str, str]
    history: Sequence[Stage2NodeReviewDecision]

    @classmethod
    def read(cls, store: ArtifactStore, history: Sequence[Stage2NodeReviewDecision]):
        rel = paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS)
        rel = rel if store.exists(rel) else STAGE2_CHAPTER_BLUEPRINTS
        values, hashes = [], {}
        for path, model in ((STAGE1_MAIN_TEXT, MainText), (rel, ChapterBlueprints),
                            (STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding)):
            value, sha = store.read_own_with_sha256(model, path, require_current=True)
            values.append(value)
            hashes[path] = sha
        snapshot = cls(store, *values, rel, hashes, tuple(history))
        validate_book(snapshot.blueprints.blueprints,
                      {row.chapter_ordinal: row.text for row in snapshot.main.root})
        snapshot.verify()
        return snapshot

    def verify(self) -> None:
        if self.hashes != {rel: self.store.full_sha256_or_none(rel) for rel in self.hashes}:
            raise HumanEditProtected('范围审阅快照读取期间输入发生变化')
        current = paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS)
        current = current if self.store.exists(current) else STAGE2_CHAPTER_BLUEPRINTS
        if current != self.blueprint_rel:
            raise HumanEditProtected('范围审阅快照的正式/INVALID 主体发生切换')

    @cached_property
    def inputs(self) -> dict[str, Any]:
        return {
            STAGE1_MAIN_TEXT: self.main.model_dump(mode='json'),
            STAGE2_CHAPTER_BLUEPRINTS: self.blueprints.model_dump(mode='json', by_alias=True),
            STAGE2_GLOBAL_GROUNDING: self.knowledge.model_dump(mode='json', by_alias=True),
        }

    @cached_property
    def ordinals(self) -> list[int]:
        return [row.chapter_ordinal for row in self.main.root]

    @cached_property
    def records(self) -> dict[str, Stage2NodeReviewDecision]:
        return {row.scope: row for row in self.history if row.node == 'stage2.blueprint'
                and row.plot_record is not None
                and row.review_contract_version == plot_review.CONTRACT}

    @cached_property
    def issues(self) -> tuple[PlotIssueRecord, ...]:
        latest = {}
        for row in self.history:
            if row.plot_record is not None:
                for issue in row.plot_record.issues:
                    previous = latest.get(issue.issue_id)
                    if previous is not None and issue.history[:len(previous.history)] != previous.history:
                        raise ArtifactUnreadable('实际问题历史不是已保存事件的合法后继')
                    latest[issue.issue_id] = issue
        return tuple(latest[key] for key in sorted(latest))

    @cached_property
    def knowledge_history(self) -> list[KnowledgeLinkHistory]:
        return [KnowledgeLinkHistory(
            decision_sha256=consumed_digest(row.model_dump(mode='json')),
            accepted=row.decision == 'accept', submission=row.knowledge_links)
            for row in self.history if row.node == 'stage2.blueprint'
            and row.review_contract_version == plot_review.CONTRACT
            and row.knowledge_links is not None]

    @cached_property
    def all_knowledge(self) -> KnowledgeLinkScope:
        return knowledge_links.build_scope(
            self.main, self.knowledge, self.blueprints,
            main_sha256=self.hashes[STAGE1_MAIN_TEXT],
            knowledge_sha256=self.hashes[STAGE2_GLOBAL_GROUNDING],
            blueprint_sha256=self.hashes[self.blueprint_rel])

    def knowledge_scope(self, scope: str) -> KnowledgeLinkScope:
        if scope == 'cross_chapter':
            return self.all_knowledge
        ordinal = int(scope.split(':')[1])
        obligations = [row for row in self.all_knowledge.obligations if row.chapter_ordinal == ordinal]
        semantic = {'contract': self.all_knowledge.contract, 'chapter_ordinal': ordinal,
                    'obligations': [row.model_dump(mode='json') for row in obligations]}
        return self.all_knowledge.model_copy(update={
            'chapter_ordinal': ordinal, 'obligations': obligations,
            'scope_sha256': consumed_digest(semantic)})

    def additional_selections(self, scope: str) -> tuple[ScopeSelection, ...]:
        from novel.stages.stage2.plot_review_dependencies import selections
        return selections(self, scope)

    def relevant_issues(self, scope: str) -> tuple[PlotIssueRecord, ...]:
        if scope == 'cross_chapter':
            return self.issues
        ordinal = int(scope.split(':')[1])
        return tuple(row for row in self.issues if row.first_scope == scope
                     or any(e.chapter_ordinal == ordinal for e in row.evidence_refs))

    def identity(self, scope: str, acceptances: Sequence[PlotScopeAcceptance] = ()):
        selections = (plot_review.scope_selections(scope, self.ordinals)
                      + self.additional_selections(scope))
        _, manifest = compile_scope_inputs(
            self.inputs, selections, scope=scope, contract=plot_review.CONTRACT,
            depends_on_scopes=tuple(sorted(row.scope for row in acceptances)))
        extra = self.knowledge_scope(scope).scope_sha256
        return plot_review.review_subject(manifest, acceptances, extra), manifest

    def scope_status(self, scope: str, acceptances: Sequence[PlotScopeAcceptance] = ()
                     ) -> PlotScopeStatus:
        subject, _ = self.identity(scope, acceptances)
        record = self.records.get(scope)
        issues = [row.issue_id for row in self.relevant_issues(scope) if row.state == 'open']
        status = 'missing'
        decision_sha = None
        if record is not None:
            decision_sha = record.plot_record.decision_sha256
            if record.review_subject_sha256 != subject:
                status = 'expired'
            else:
                status = {'accept': 'accepted', 'rework': 'rework',
                          'uncertain': 'uncertain', 'stop': 'stopped'}[record.decision]
                if status == 'accepted' and (issues or knowledge_links.review(
                        self.knowledge_scope(scope), self.knowledge_history).pending_obligation_ids):
                    status = 'expired'
        return PlotScopeStatus(scope=scope, state=status, subject_sha256=subject,
                               decision_sha256=decision_sha, open_issue_ids=issues)

    def progress(self) -> PlotReviewProgress:
        rows = [self.scope_status(f'chapter:{o}') for o in self.ordinals]
        accepted = [PlotScopeAcceptance(scope=row.scope, subject_sha256=row.subject_sha256,
                                       decision_sha256=row.decision_sha256)
                    for row in rows if row.state == 'accepted']
        if len(accepted) == len(rows):
            cross = self.scope_status('cross_chapter', accepted)
        else:
            cross = PlotScopeStatus(
                scope='cross_chapter', state='expired' if 'cross_chapter' in self.records else 'missing',
                open_issue_ids=[i.issue_id for i in self.issues if i.state == 'open'])
        rows.append(cross)
        next_scope = next((row.scope for row in rows if row.state != 'accepted'), None)
        self.verify()
        return PlotReviewProgress(
            total_chapters=len(self.ordinals), generated_chapters=len(self.blueprints.blueprints),
            accepted_chapters=len(accepted), next_scope=next_scope, scopes=rows,
            unresolved_issues=sum(row.state == 'open' for row in self.issues),
            accepted=next_scope is None)

    def workpack(self, scope: str | None = None) -> BlueprintReviewWorkpack:
        from novel.stages.stage2 import extract
        from novel.views.stage2 import _compact_check

        progress = self.progress()
        scope = scope or progress.next_scope or 'cross_chapter'
        acceptances = []
        if scope == 'cross_chapter':
            if progress.accepted_chapters != progress.total_chapters:
                raise ApprovalRequired('全部章审有效接受后才开放跨章复核')
            acceptances = [PlotScopeAcceptance(scope=row.scope,
                subject_sha256=row.subject_sha256, decision_sha256=row.decision_sha256)
                for row in progress.scopes if row.scope != 'cross_chapter']
        links = self.knowledge_scope(scope)
        pack = plot_review.build_workpack(
            scope, self.inputs, issues=self.issues, chapter_acceptances=acceptances,
            additional_selections=self.additional_selections(scope),
            extra_identity=links.scope_sha256)
        report = extract.run_check_only(self.store.ctx)
        if report.provenance.invalid != (self.blueprint_rel != STAGE2_CHAPTER_BLUEPRINTS):
            raise HumanEditProtected('审阅校验报告与实际主体不一致')
        report_sha = consumed_digest(report.model_dump(mode='json', by_alias=True))
        seal = consumed_digest({'code': self.store.ctx.code, 'rules': prov.rules_fingerprint('stage2'),
                                'hashes': self.hashes, 'report': report_sha})
        knowledge_review = knowledge_links.review(links, self.knowledge_history)
        binding_sha = consumed_digest({'request': pack.request_sha256, 'seal': seal,
                                       'knowledge_links': knowledge_review.model_dump(mode='json')})
        self.verify()
        return BlueprintReviewWorkpack(
            sid=self.store.ctx.sid, title=paths.title_of(self.store.ctx.novel_dir), scope=scope,
            review_identity_sha256=pack.subject_sha256,
            subject_sha256=pack.subject_sha256, validation_seal_sha256=seal,
            review_token='bpr5_' + binding_sha, binding_sha256=binding_sha,
            binding=BlueprintReviewBinding(
                blueprint_rel=self.blueprint_rel,
                subject_kind='formal' if self.blueprint_rel == STAGE2_CHAPTER_BLUEPRINTS else 'invalid',
                main_text_artifact_sha256=self.hashes[STAGE1_MAIN_TEXT],
                blueprint_artifact_sha256=self.hashes[self.blueprint_rel],
                check_report_sha256=report_sha, chapter_ordinals=self.ordinals),
            dependency_manifest=pack.manifest, knowledge_links=knowledge_review,
            material=pack.material, progress=progress, check=_compact_check(report),
            accounting=list(report.accounting))

    def apply(self, workpack: BlueprintReviewWorkpack, response, actor: str):
        """提交 owner 已核 opaque token；重新编译同一材料后生成审计记录。"""
        acceptances = [PlotScopeAcceptance.model_validate(row)
                       for row in workpack.material['chapter_acceptances']]
        current = plot_review.build_workpack(
            workpack.scope, self.inputs, issues=self.issues, chapter_acceptances=acceptances,
            additional_selections=self.additional_selections(workpack.scope),
            extra_identity=workpack.knowledge_links.scope.scope_sha256)
        if current.material != workpack.material or current.subject_sha256 != workpack.subject_sha256:
            raise HumanEditProtected('提交时范围消费材料已经变化')
        allowed = frozenset((row['kind'], row['ref'])
            for dependency in current.material['dependencies'] if isinstance(dependency, list)
            for row in dependency if isinstance(row, dict) and 'kind' in row and 'ref' in row)
        return plot_review.apply_review(current, response,
            expected_request_sha256=current.request_sha256, actor=actor,
            allowed_global_subjects=allowed)
