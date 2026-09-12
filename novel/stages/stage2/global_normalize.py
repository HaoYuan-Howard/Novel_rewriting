"""Global v7 的免费归一；仅编译人工证据引用，不裁剪语义字段。"""
from __future__ import annotations
import copy
from typing import Any
from novel.stages.stage2.core_evidence import compile_manual_evidence_refs, evidence_slots
from novel.stages.stage2.normalize import EvidenceLocatorCorrection


class GlobalNormalizationAccounting(dict[str, Any]):
    evidence_locator_corrections: tuple[EvidenceLocatorCorrection, ...] = ()

    def __init__(self, *, evidence_locator_failures=(), evidence_locator_corrections=()):
        super().__init__(evidence_locator_failures=list(evidence_locator_failures))
        self.evidence_locator_corrections = tuple(evidence_locator_corrections)


def normalize_global(data: dict[str, Any], main_text: list[dict[str, Any]]):
    """0 LLM、无I/O；失败保留原输入供用户修复，不造不存在的锚点或剪掉铺垫。"""
    if all(isinstance(ref, str) for _path, owner, key in evidence_slots(data) for ref in owner[key]):
        # 正式证据失配必须由HARD明说；复验不能隐式移动坐标或抹掉问题稿。
        payload = copy.deepcopy(data)
        used = {ref for _path, owner, key in evidence_slots(data) for ref in owner[key]}
        anchors = {key: copy.deepcopy(value) for key, value in data.get('evidence_anchors', {}).items() if key in used}
    else:
        payload, anchors = compile_manual_evidence_refs(
            data, data.get('evidence_anchors', {}), main_text)
    payload['evidence_anchors'] = anchors
    return payload, GlobalNormalizationAccounting()
