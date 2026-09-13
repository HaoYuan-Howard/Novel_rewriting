"""V8 任务书的正文检查投影；只解析必要约束，不从剧情重抽事实。"""
from __future__ import annotations
from typing import Any
from contracts.schema.report import finding
from novel.core.facts import locate_secret, secret_keywords


def fidelity_scenes(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """已有字面/数值/物件探针共用这一临时选择；不保存第二份材料正文。"""
    constraints = {row['constraint_ref']: row for row in entry['constraints']}
    fields = {'literal': 'verbatim', 'value': 'keep_value', 'object': 'must_appear'}
    result = []
    for scene in entry['scenes']:
        row = dict(scene)
        for field in fields.values():
            row[field] = []
        for ref in scene['constraint_refs']:
            constraint = constraints[ref]
            field = fields.get(constraint['kind'])
            if field is not None:
                row[field].append(constraint['value'])
        result.append(row)
    return result


def delivery_surface_findings(segments: list[dict[str, str]], entry: dict[str, Any], book):
    """全文语义不能按每场关键词核销；即使探针命中也保持联合呈现未判。"""
    refs = [row['beat_ref'] for row in entry['beats']]
    return [finding('fact_landing_unver',
        detail='完整节点由多场联合呈现；不能要求每场都复述父节点，也不能以词面命中证明联合完整。',
        evidence=refs, output_ordinal=entry['output_ordinal'])]

