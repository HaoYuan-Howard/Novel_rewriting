"""系列概括的目标位置判据：只比较显式 covers，不把源顺序强加给重设计。

Design 接受与免费 compile 共用同一位置比较。接受位由 real 的最后一次标注
或正式 Binding.entries 给出；相同拍合法，未承载实例不能冒充已经发生。
本模块纯函数，不读盘、不修改输入、不生成新的语义关系。
"""
from __future__ import annotations

from typing import Iterable, Mapping, NamedTuple

from contracts.schema.grounding import ObligationSeries


class SeriesOrderViolation(NamedTuple):
    summary: str
    instance: str
    reason: str


def summary_covers(series: Iterable[ObligationSeries]) -> dict[str, tuple[str, ...]]:
    """正式 series → 概括义务的显式覆盖表；模型未声明的实例不自行补入。"""
    return {row.member: row.covers for group in series for row in group.summaries}


def summary_order_violations(
    covers: Mapping[str, tuple[str, ...]],
    accept_sites: Mapping[str, tuple[int, int]],
) -> tuple[SeriesOrderViolation, ...]:
    """概括接受拍不得早于所覆盖实例；可任意重排实例，或同拍完成并概括。

    未采用的概括不约束目标故事；采用的概括必须覆盖已采用实例。
    """
    errors: list[SeriesOrderViolation] = []
    for summary, instances in sorted(covers.items()):
        summary_site = accept_sites.get(summary)
        if summary_site is None:
            continue
        for instance in instances:
            instance_site = accept_sites.get(instance)
            if instance_site is None:
                errors.append(SeriesOrderViolation(summary, instance, 'unbound'))
            elif instance_site > summary_site:
                errors.append(SeriesOrderViolation(summary, instance, 'early'))
    return tuple(errors)
