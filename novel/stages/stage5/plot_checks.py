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


def knowledge_position_findings(segments: list[dict[str, str]], entry: dict[str, Any], book):
    """只给同章许可前的转抄式命中线索，人物声称/信念与世界真相仍需语义区分。"""
    secrets = {row['secret_id']: row for row in book.secrets}
    out = []
    for event in entry['knowledge_events']:
        subject = event['subject_ref']
        if event['kind'] != 'secret_reveal' or subject['kind'] != 'secret':
            continue
        secret = secrets.get(subject['ref'])
        if secret is None:
            continue
        for segment in segments:
            if segment['slot'] in event['allowed_slots']:
                continue
            body = segment['text']
            spans = [locate_secret(needle, body) for needle in secret_keywords(secret['fact'])]
            hits = [body[a:b] for span in spans if span is not None for a, b in [span]]
            if hits:
                out.append(finding('knowledge_position_unver',
                    detail='许可前出现与受限内容相似的表达；须结合归因/声称/信念判断，不能机械判为揭底。',
                    evidence=[segment['slot'], *dict.fromkeys(hits)],
                    output_ordinal=entry['output_ordinal']))
    if not out:
        out.append(finding('knowledge_position_unver',
            detail='未发现许可前的转抄式命中不能证明无改写式提前揭示；按逐场许可核对完整成稿。',
            evidence=[row['slot'] for row in segments], output_ordinal=entry['output_ordinal']))
    return out
