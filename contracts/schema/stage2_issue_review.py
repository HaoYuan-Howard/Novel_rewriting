#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2CoreReview issues 协议：模型 wire 载荷、prepared scope 与审阅事务。

一次 Analyze → 确定性编译 → 一次 Commander 整体审阅 → 免费提交审阅账与节点接受。
本模块定义这次审阅的三类新载体：

* **issues 回答**（``novel-stage2-issue-review-v1``）：业务回答只有 ``issues``；
  非空 issue 使用固定 typed 结构。模型不输出逐项 PASS、reason 卡、actor、token、
  hash 或动作菜单。
* **PreparedScope**（``stage2-global-review-workpack-v7``）：调用前由代码冻结的
  不可变审阅包——原文/Characters/Global 的精确身份、本轮覆盖义务及 hash、
  历史问题与删除问题原材料、可信 token 与调用身份。同时覆盖主体义务、
  集合/元义务与历史问题义务；``issues=[]`` 只接受这份 scope 明确覆盖的义务。
* **审阅事务**（``stage2-issue-review-transaction-v1``）：一份回答完成两套账的
  事务层审计——``decision_basis=scope_review`` 的派生决定来源、精确覆盖的
  item/hash、原始语义回答与恢复链来源。

协议边界（与执行计划一致）：

* ``issue_type`` / ``repair_hint`` 只是分类与建议，不能授予写权限、决定 owner
  或扩大范围；实际派修、权限、失效和材料扩充由代码校验并计算。
* 模型不得伪造不存在的业务 ID；目标闭集由 prepared 包冻结，loader 校验。
* ``decision_basis`` / ``review_protocol`` 属于事务层审计来源，不写进逐项 reason，
  不声称模型专门逐字段裁决。
"""
from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.stage2_review import (
    Stage2ReviewMaterialSource,
    Stage2ReviewObligation,
)

#: 统一 issue 协议名；业务回答只有 ``{"issues":[]}``。
ISSUE_REVIEW_PROTOCOL: Final = 'novel-stage2-issue-review-v1'

#: PreparedScope 载体 kind；Global 工作包 v7（v6 只读工作包保留给人读/API 投影）。
PREPARED_PACKAGE_KIND: Final = 'stage2-global-review-workpack-v7'

#: 审阅事务层接受契约；旧 v2 接受保留历史，不转换成新协议接受。
ISSUE_REVIEW_ACCEPTANCE_CONTRACT: Final = 'stage2-global-acceptance-v3'

#: 派生 PASS 的审计来源分类；逐项 reason 使用固定来源说明。
DECISION_BASIS_SCOPE_REVIEW: Final = 'scope_review'

#: 非空 issue 的固定问题分类；只是分类，不授予任何动作权限。
ISSUE_TYPES: Final = (
    'missing', 'incorrect', 'conflict', 'over_extracted', 'under_extracted',
    'timing', 'identity', 'evidence', 'unverifiable',
)

#: 修复建议闭集；派修路由由代码按 AffectedScope 计算，不采纳模型的 owner 指定。
REPAIR_HINTS: Final = (
    'none', 'needs_local_context', 'needs_chapter', 'needs_owner_context',
)


class Stage2IssueTarget(StrictModel):
    """只能引用本轮 prepared 包提供的目标；闭集由 loader 校验。"""

    kind: Literal['existing_item', 'scope_obligation']
    ref: str = Field(min_length=1)


class Stage2CandidateCharactersPatch(StrictModel):
    """人物侧窄候选：只允许既有主体的字段/证据订正（replace）。

    人物新建、删除、合并、称谓改归属属于 owner 级高影响修复，交局部
    Executor；候选 patch 不提供 add/delete。
    """

    replaces: list[dict[str, Any]] = Field(default_factory=list, min_length=1)


class Stage2CandidateGlobalPatch(StrictModel):
    """Global 侧窄候选：明确字段/时点/证据订正与明确误抽删除。

    不提供 ``*_replace`` 整组替换、add/split 或任意 JSON 路径写入；
    复杂重建交局部 Executor。
    """

    relations: list[dict[str, Any]] = Field(default_factory=list)
    world_rules: list[dict[str, Any]] = Field(default_factory=list)
    arc_secrets: list[dict[str, Any]] = Field(default_factory=list)
    setting: dict[str, Any] | None = None
    collection_deletes: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def _non_empty(self) -> 'Stage2CandidateGlobalPatch':
        if not (self.relations or self.world_rules or self.arc_secrets
                or self.setting is not None or self.collection_deletes):
            raise ValueError('Global 候选 patch 至少包含一项 typed 订正')
        return self


class Stage2IssueCandidatePatch(StrictModel):
    """可空、受限的 typed 候选操作；先持久化问题，预检通过才由 owner 免费应用。"""

    owner: Literal['stage2.characters', 'stage2.global']
    characters: Stage2CandidateCharactersPatch | None = None
    global_edit: Stage2CandidateGlobalPatch | None = Field(
        default=None, alias='global')

    model_config = {'populate_by_name': True}

    @model_validator(mode='after')
    def _owner_matches_body(self) -> 'Stage2IssueCandidatePatch':
        if self.owner == 'stage2.characters' and self.characters is None:
            raise ValueError('人物候选 patch 缺 replaces')
        if self.owner == 'stage2.global' and self.global_edit is None:
            raise ValueError('Global 候选 patch 缺 typed 订正')
        other = self.global_edit if self.owner == 'stage2.characters' else self.characters
        if other is not None:
            raise ValueError('候选 patch 只能提交所属 owner 一侧')
        return self


class Stage2Issue(StrictModel):
    """统一 typed 问题；先找错、再核对历史、最后才附明确小错候选 patch。"""

    target: Stage2IssueTarget
    state: Literal['FAIL', 'UNVER']
    issue_type: Literal[ISSUE_TYPES]
    problem: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    repair_hint: Literal[REPAIR_HINTS] = 'none'
    candidate_patch: Stage2IssueCandidatePatch | None = None

    @model_validator(mode='after')
    def _visible_problem(self) -> 'Stage2Issue':
        if not self.problem.strip():
            raise ValueError('issue.problem 必须是简明的具体问题描述')
        return self


class Stage2IssueReviewSubmission(StrictModel):
    """``--review-issues`` 公开请求：``review_token、actor、issues``。

    自动运行时由可信代码注入身份；服务只暂存 ``_service`` 请求，CLI 在同一
    可恢复事务中复验 prepared package 和当前字节身份。
    """

    review_token: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    issues: list[Stage2Issue] = Field(default_factory=list)

    @model_validator(mode='after')
    def _unique_targets(self) -> 'Stage2IssueReviewSubmission':
        refs = [(row.target.kind, row.target.ref) for row in self.issues]
        if len(refs) != len(set(refs)):
            raise ValueError('同一目标在一轮回答中只能报告一个问题')
        return self


class Stage2PreparedIdentities(StrictModel):
    """PreparedScope 冻结的精确来源身份；任一格变化都使本轮包失效。"""

    characters_sha256: Sha256
    global_sha256: Sha256
    review_sha256: Sha256
    main_text_sha256: Sha256
    main_text_subject_sha256: Sha256
    source_sha256: Sha256
    blocking_scope_sha256: Sha256
    characters_full_sha256: Sha256
    global_full_sha256: Sha256


class Stage2PreparedTargetIndex(StrictModel):
    """目标闭集的一行：义务身份 + 它挡住（不能派生 PASS）的当前 item。"""

    obligation_id: str = Field(min_length=1)
    target_kind: Literal['item', 'meta', 'historical']
    owner: str = Field(min_length=1)
    subject_sha256: Sha256
    #: meta/historical 问题在该目标上报告时，这些当前 item 不得派生 PASS。
    holds_item_ids: list[str] = Field(default_factory=list)


class Stage2PreparedEvidence(StrictModel):
    """证据候选闭集的一行；``evidence_refs`` 只能引用这里的 id。"""

    ref: str = Field(min_length=1)
    chapter_ordinal: int | None = None
    start: int | None = None
    end: int | None = None


class Stage2PreparedCoreReviewPackage(StrictModel):
    """调用前由代码生成并冻结的不可变审阅包（Global 工作包 v7）。"""

    kind: Literal[PREPARED_PACKAGE_KIND]
    sid: str = Field(min_length=1)
    protocol: Literal[ISSUE_REVIEW_PROTOCOL]
    review_scope: Literal['blocking']
    review_token: str = Field(min_length=1)
    coverage_scope_id: Sha256
    review_contract_version: Literal[ISSUE_REVIEW_ACCEPTANCE_CONTRACT]
    identities: Stage2PreparedIdentities
    obligations: list[Stage2ReviewObligation] = Field(min_length=1)
    targets: list[Stage2PreparedTargetIndex]
    evidence: list[Stage2PreparedEvidence] = Field(default_factory=list)
    #: 候选 patch 的主体 ID 闭集：人物、关系、规则、秘密及各集合身份。
    binding: dict[str, Any] = Field(default_factory=dict)
    #: Stage2CoreReview 渲染面：紧凑业务对象、必要证据、原文一次、历史问题后置。
    materials: dict[str, Any]
    material_source: Stage2ReviewMaterialSource
    prepared_sha256: Sha256


class Stage2IssueCoverageEntry(StrictModel):
    """派生决定的一行审计；派生 PASS 不声称模型逐字段裁决。"""

    obligation_id: str = Field(min_length=1)
    subject_sha256: Sha256
    #: derived_pass=PreparedScope 覆盖且未被问题挡住；issue=本轮明确问题；
    #: inherited=按既有证明继承；held=meta/historical 问题挡住的当前主体。
    basis: Literal['derived_pass', 'issue', 'inherited', 'held']
    issue_index: int | None = None


class Stage2IssueReviewTransaction(StrictModel):
    """一份回答完成两套账的事务层记录；只增不改，供 API 追溯展示。"""

    kind: Literal['stage2-issue-review-transaction-v1']
    sid: str = Field(min_length=1)
    protocol: Literal[ISSUE_REVIEW_PROTOCOL]
    decision_basis: Literal['scope_review']
    coverage_scope_id: Sha256
    prepared_sha256: Sha256
    review_token: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    coverage: list[Stage2IssueCoverageEntry] = Field(min_length=1)
    issues: list[Stage2Issue]
    #: 原始语义回答及恢复链来源；格式修复/语义重试分类在此可追溯。
    answer_sha256: Sha256
    recovery: dict[str, Any] = Field(default_factory=dict)
    node_accepted: bool
    decided_at: str = Field(min_length=1)
    transaction_sha256: Sha256


class Stage2IssueReviewTransactionLedger(StrictModel):
    """审阅事务账；按书持久，追加写，不迁移历史行。"""

    transactions: list[Stage2IssueReviewTransaction] = Field(default_factory=list)
