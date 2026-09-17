"""Blueprint 拒绝分级：可确定性消化的失败不触发付费重采。

A/B 类（schema/wire/可编译消化）由 compile / response-repair 收口；
C 类语义缺失仍走既有 Flash→Pro / rebuild 策略。
"""
from __future__ import annotations

# 命中即视为确定性消化失败（不再同档付费重采）。XOR Extract 落地后多数
# 旧 XOR 文案不会再出现；保留标记防存量 repair 链与回归夹具。
_DETERMINISTIC_WHY_MARKERS: tuple[str, ...] = (
    '持续状态只保存正文或共享主体引用之一',
    'presentation_ref 必须是本章',
    '人物呈现绑定必须是本章 actor_bindings',
)


def blueprint_failure_is_deterministic(*, gate: str, why: str | None) -> bool:
    """gate+why 是否应停止内容重采（仍可走 deterministic repair / Pro rebuild 策略链）。"""
    if gate not in ('schema', 'validator', 'parse'):
        return False
    text = why or ''
    return any(marker in text for marker in _DETERMINISTIC_WHY_MARKERS)
