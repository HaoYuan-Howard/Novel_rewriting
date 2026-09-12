"""当前审阅义务的唯一投影：材料裁剪、提交、恢复共用，不产生语义判断。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from contracts.invariants import STAGE2_CHARACTER_REVIEW_KINDS
from contracts.schema.stage2_review import (
    Stage2ReviewCoverage, Stage2ReviewObligation, Stage2ReviewReceipt,
    Stage2ReviewScope, Stage2ReviewSubmission, Stage2ReviewUnit,
)
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.orchestrator import review_inheritance


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def global_key(row: Mapping[str, Any]) -> str:
    return f"{row['kind']}:{row['subject_path']}"


def global_catalog(pack: Mapping[str, Any]) -> list[Stage2ReviewObligation]:
    rows = pack['items']
    catalog = []
    for row in rows:
        key = global_key(row)
        path = row['subject_path']
        group = row['group_key']
        context = [global_key(other) for other in rows if global_key(other) != key and (
            other['group_key'] == group or path.startswith(other['subject_path'] + '.'))]
        material = {name: value for name, value in row.items()
                    if name not in {'item_id', 'current_reason'}}
        # 正式证据/命中已经绑定当前章字节；没有局部证明的完整性义务绑定全部源文。
        if row['reading_scope'] == 'whole_book':
            material['source'] = pack['main_text_subject_sha256']
        chapters = set()
        for evidence in row.get('formal_evidence', []):
            ordinal = evidence.get('chapter_ordinal')
            if type(ordinal) is not int or ordinal < 1:
                raise ArtifactUnreadable('正文正式证据缺合法章号，不能构造审阅义务')
            chapters.add(ordinal)
        catalog.append(Stage2ReviewObligation(
            obligation_id=key, subject_sha256=sha(material),
            owner=('stage2.characters' if row['kind'] in STAGE2_CHARACTER_REVIEW_KINDS
                   else 'stage2.global'),
            chapter_ordinals=sorted(chapters),
            subject_path=path, kind='global_item', context_refs=sorted(context)))
    return catalog


def _global_material_context(rows: Sequence[Mapping[str, Any]],
                             obligations: Mapping[str, Stage2ReviewObligation],
                             pending: set[str]) -> set[str]:
    """补齐实际引用的阅读前提；不改义务身份，不重签已接受决定。

    fact_ref 指向的命题、规则版本的类型、关系端点的实体身份都可能已经接受。
    它们仍是理解待审项的必要材料，不能因无需再次决定而只发一个裸 ID。
    """
    by_key = {global_key(row): row for row in rows}
    by_path = {row['subject_path']: global_key(row) for row in rows}

    def paths(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            if {'subject_id', 'object_id', 'predicate'} <= value.keys():
                found.update(f'characters.characters.{value[field]}'
                             for field in ('subject_id', 'object_id'))
            ref = value.get('fact_ref')
            if isinstance(ref, Mapping):
                if ref.get('kind') == 'relation':
                    found.add(f"global.relations.{ref['relation_id']}")
                elif ref.get('kind') == 'world_rule_version':
                    rule = f"global.world_rules.{ref['rule_id']}"
                    found.update((rule + '.kind', rule + f".versions.{ref['version_id']}"))
            for child in value.values():
                found.update(paths(child))
        elif isinstance(value, list):
            for child in value:
                found.update(paths(child))
        return found

    selected = set(pending)
    frontier = list(pending)
    while frontier:
        key = frontier.pop()
        obligation = obligations[key]
        dependencies = set(obligation.context_refs)
        row = by_key.get(key)
        # 已接受的父集合只提供当前集合上下文，不把它的每个无关成员递归展开。
        if row is not None and (key in pending or row['reading_scope'] != 'whole_book'):
            for path in paths(row.get('subject')):
                target = by_path.get(path)
                if target is None:
                    raise ArtifactUnreadable(f'Global 审阅缺少被引用主体的阅读上下文：{path}')
                dependencies.add(target)
        for ref in dependencies:
            if ref in obligations and ref not in selected:
                selected.add(ref)
                frontier.append(ref)
    return selected - pending




def _unit(row: Stage2ReviewUnit) -> review_inheritance.DecisionUnit:
    # context_refs 只是当前工作包的阅读导航；同组增删主体会改变它，却不改变
    # 本项已审业务。原回执的完整 scope 仍逐字绑定摘要，继承只比较其语义载荷。
    payload = row.model_dump(mode='json', exclude={'source_decision_sha256'})
    payload['obligation'].pop('context_refs')
    return review_inheritance.DecisionUnit(
        subject_sha256=row.obligation.subject_sha256,
        payload=payload,
        source_decision_sha256=row.source_decision_sha256)


def _history(history: Sequence[Any]) -> list[review_inheritance.DecisionReceipt]:
    for row in history:
        if row.obligation_receipt is None:
            continue  # 无当前版本回执的历史不授予义务继承。
        receipt = row.obligation_receipt
        expected = sha(receipt.scope.model_dump(mode='json', exclude={'scope_sha256'}))
        if (receipt.scope.scope_sha256 != expected or receipt.scope.node != row.node
                or (row.decision == 'accept'
                    and any(unit.decision != 'accept' for unit in receipt.units))):
            raise ArtifactUnreadable('Stage2 历史回执的范围摘要或接受覆盖损坏')
    return [review_inheritance.DecisionReceipt(
        decision_sha256=sha(row.model_dump(mode='json')),
        units={unit.obligation.obligation_id: _unit(unit) for unit in row.obligation_receipt.units},
        valid=True) for row in history if row.obligation_receipt is not None]


def coverage(node: str, catalog: Sequence[Stage2ReviewObligation],
             history: Sequence[Any]) -> Stage2ReviewCoverage:
    """保留删除问题的复审义务；新的明确接受才可消除否决栅栏。"""
    history = [row for row in history if row.node == node]
    latest: dict[str, Stage2ReviewUnit] = {}
    for decision in history:
        if decision.obligation_receipt is not None:
            for unit in decision.obligation_receipt.units:
                latest[unit.obligation.obligation_id] = unit
    current = {row.obligation_id: row for row in catalog}
    present = dict(current)
    for key, unit in latest.items():
        old = unit.obligation
        if key in current or (unit.decision == 'accept' and old.kind != 'removed_problem'):
            continue
        origin = old.problem_subject_sha256 or old.subject_sha256
        # 删除后仍展示当前父集合/原引用上下文；其他组变化不重开这项删除裁决。
        dependencies = {ref: present[ref].subject_sha256 if ref in present else None
                        for ref in old.context_refs}
        current[key] = old.model_copy(update={
            'kind': 'removed_problem', 'problem_subject_sha256': origin,
            'subject_sha256': sha({'removed': key, 'problem': origin, 'context': dependencies}),
        })
    rows = sorted(current.values(), key=lambda row: row.obligation_id)
    scope = Stage2ReviewScope(node=node, scope_sha256=sha({
        'contract': 'stage2-review-obligations-v1', 'node': node,
        'obligations': [row.model_dump(mode='json') for row in rows]}), obligations=rows)
    sources = review_inheritance.accepted_sources(
        _history(history), {key: row.subject_sha256 for key, row in current.items()})
    inherited = []
    for key, receipt in sorted(sources.items()):
        payload = receipt.units[key].payload
        obligation = current[key]
        if payload['obligation'] != obligation.model_dump(mode='json', exclude={'context_refs'}):
            raise ArtifactUnreadable(f'Stage2 继承义务 {key} 的业务身份不一致')
        # 来源已由 accepted_sources 逐层核验；只恢复当前导航，不重写原决定或理由。
        inherited.append(Stage2ReviewUnit.model_validate({
            **payload, 'obligation': obligation,
            'source_decision_sha256': receipt.decision_sha256,
        }))
    pending = sorted(current.keys() - sources.keys())
    return Stage2ReviewCoverage(
        scope=scope, inherited=inherited, pending_obligation_ids=pending,
        prior_problems=[latest[key] for key in pending
                        if key in latest and latest[key].decision != 'accept'])


def materialize(coverage_: Stage2ReviewCoverage, history: Sequence[Any], *,
                decision: str, actor: str, reason: str,
                submission: Stage2ReviewSubmission | None) -> Stage2ReviewReceipt:
    """确定性展开明确的范围决定与例外；继承绝不改写原 actor/reason。"""
    if submission is not None and submission.scope_sha256 != coverage_.scope.scope_sha256:
        raise HumanEditProtected('Stage2 当前义务范围已变化；拒绝旧范围决定')
    exceptions = {row.obligation_id: row for row in submission.exceptions} if submission else {}
    known = {row.obligation_id: row for row in coverage_.scope.obligations}
    if exceptions.keys() - known.keys():
        raise HumanEditProtected('Stage2 例外包含当前范围之外的义务')
    default = submission.default_decision if submission else decision
    retained = {row.obligation.obligation_id: row for row in coverage_.inherited}
    units = []
    for key, obligation in known.items():
        exception = exceptions.get(key)
        if (key in retained and exception is None and decision != 'stop'
                and not (submission is None and decision != 'accept')):
            units.append(retained[key])
        else:
            units.append(Stage2ReviewUnit(
                obligation=obligation, decision=exception.decision if exception else default,
                actor=actor, reason=exception.reason if exception else reason))
    if decision == 'accept' and any(row.decision != 'accept' for row in units):
        raise HumanEditProtected('Stage2 接受必须由新接受与有效继承完整覆盖，不能留下未知或否决')
    if decision != 'accept' and all(row.decision == 'accept' for row in units):
        raise HumanEditProtected('Stage2 非接受决定必须保留明确的问题或未知义务')
    if decision == 'rework' and not any(row.decision == 'rework' for row in units):
        raise HumanEditProtected('Stage2 返工必须明确指向至少一项当前问题义务')
    review_inheritance.validate_submission(
        _history([row for row in history if row.node == coverage_.scope.node]),
        {key: row.subject_sha256 for key, row in known.items()},
        {row.obligation.obligation_id: _unit(row) for row in units})
    return Stage2ReviewReceipt(scope=coverage_.scope, units=units)


def pending_materials(pack: Mapping[str, Any], coverage_: Stage2ReviewCoverage) -> dict[str, Any]:
    """交给人和模型的同一投影：只发待审材料及其明确引用上下文。"""
    import copy

    result = copy.deepcopy(dict(pack))
    pending = set(coverage_.pending_obligation_ids)
    obligations = {row.obligation_id: row for row in coverage_.scope.obligations}
    refs = {ref for key in pending for ref in obligations[key].context_refs}
    result['review_coverage'] = coverage_.model_dump(mode='json')
    if coverage_.scope.node == 'stage2.global':
        removed = [row for row in result.get('removed_problem_materials', [])
                   if row['obligation_id'] in pending]
        result['removed_problem_materials'] = removed
        refs = _global_material_context(result['items'], obligations, pending)
        result['items'] = [row for row in result['items'] if global_key(row) in pending | refs]
        if not removed and not any(row['reading_scope'] == 'whole_book' and global_key(row) in pending
                   for row in result['items']):
            result['main_text_chapters'] = []
    else:
        ordinals = {ordinal for key in pending for ordinal in obligations[key].chapter_ordinals}
        # 关联问题仍由同一 Blueprint C 裁决，不能因章节接受已继承而隐藏其候选材料。
        links = result['knowledge_links']
        link_pending = set(links['pending_obligation_ids'])
        ordinals.update(row['chapter_ordinal'] for row in links['scope']['obligations']
                        if row['obligation_id'] in link_pending)
        result['chapters'] = [row for row in result['chapters'] if row['chapter_ordinal'] in ordinals]
        result['global_review']['items'] = [row for row in result['global_review']['items']
                                           if global_key(row) in refs]
        result['global_review'].pop('main_text_chapters', None)
        result['knowledge_links'] = {
            'scope_sha256': links['scope']['scope_sha256'],
            'pending': [row for row in links['scope']['obligations']
                        if row['obligation_id'] in link_pending],
            'inherited_count': len(links['inherited']),
        }
    result['material_projection'] = {
        'full_workpack_sha256': sha(pack), 'complete_scope_sha256': coverage_.scope.scope_sha256,
        'context_obligation_ids': sorted(refs - pending),
        'purpose': '待审项引用的已接受主体仅作只读上下文，不重开义务；提交仍校验完整义务，删除问题仍须明确复审。',
    }
    # 完整范围已由 exact_request 绑定。wire 不重复发送每项继承的元数据和理由，
    # 原始完整回执仍在 API/审批历史里，由提交服务逐层核验。
    result['review_coverage'] = {
        'scope_sha256': coverage_.scope.scope_sha256,
        'pending': [row.model_dump(mode='json') for row in coverage_.scope.obligations
                    if row.obligation_id in pending],
        'inherited_count': len(coverage_.inherited),
        'prior_problems': [row.model_dump(mode='json') for row in coverage_.prior_problems],
    }
    return result
