"""阅读材料的确定性来源；只在投影与验证间传递，不成为领域真源或落盘副本。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Literal


MaterialKind = Literal['design', 'obligation', 'knowledge', 'world', 'expression',
                       'hook', 'projection']


@dataclass(frozen=True)
class MaterialSource:
    kind: MaterialKind
    ref: str
    field: str
    text: str
    position: str = ''


@dataclass(frozen=True)
class NameProjectionHit:
    """一次确定性称谓投影：已证明唯一绑定人物的完整登记称谓 → 本场已释放称谓。

    只留痕字面、人物与替换结果（不含所在行文本）；原字段文本由 `MaterialSource.text`
    保持不变，衍生文本不反写事实（CS-F2）。
    """

    literal: str
    char_id: str
    replacement: str


@dataclass(frozen=True)
class ProjectionBlock:
    """一次被无损性守卫拦下的投影（CS-H H3，RC-60）：同一句里同一人物两个
    **不相邻的不同**称谓若都换成显示面会压成一个，信息有损——保持原文，
    该行不可投影，归 owner 修（Design 挪拍位或改指称）。

    与 `NameProjectionHit` 分开留痕：命中是「已投影」的审计读数，拦截是
    「投影被拒绝」的残余缺口（后续字面检查如实报告，不静默吞掉）。
    """

    literal: str
    char_id: str


@dataclass(frozen=True)
class MaterialPart:
    text: str
    sources: tuple[MaterialSource, ...] = ()
    positive: bool = True
    projected: tuple[NameProjectionHit, ...] = ()


@dataclass(frozen=True)
class MaterialIssue:
    code: str
    where: str
    start: int
    literal: str
    sources: tuple[MaterialSource, ...] = ()
    chapter_ordinal: int = 0

    @property
    def identity(self) -> dict:
        """正式语义位置的集合身份；文本偏移和展示文案不是控制字段。"""
        return {'code': self.code, 'literal': self.literal,
                'chapter_ordinal': self.chapter_ordinal,
                'sources': sorted({(s.kind, s.ref, s.field, s.position) for s in self.sources})}

    @property
    def message(self) -> str:
        detail = (f'未释放专名字面 {self.literal!r}' if self.code == 'positive_material_leak'
                  else f'内部句柄 {self.literal!r} 不得进入写作材料')
        return f'{self.code}[{self.where}@{self.start}]：{detail}'


class MaterialLines:
    """renderer在拼接一行时显式登记原子来源，去重时保留全部来源证明。

    `project` 是渲染期的确定性称谓投影（仅作用于正向行）：返回 `(新文本, 命中)`。
    来源 `MaterialSource.text` 始终是原字段文本，投影只改变送达写手的衍生行。
    """

    def __init__(self, project: Callable[[str], tuple[str, tuple[NameProjectionHit, ...]]]
                 | None = None) -> None:
        self._parts: list[MaterialPart] = []
        self._project = project

    def append(self, text: str, *sources: MaterialSource, positive: bool = True) -> None:
        hits: tuple[NameProjectionHit, ...] = ()
        if positive and self._project is not None:
            text, hits = self._project(text)
        self._parts.append(MaterialPart(text, tuple(sources), positive, hits))

    def extend(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.append(text)

    def parts(self, *, deduplicate: bool = False) -> tuple[MaterialPart, ...]:
        if not deduplicate:
            return tuple(self._parts)
        by_text: dict[str, list[MaterialSource]] = {}
        positive: dict[str, bool] = {}
        projected: dict[str, list[NameProjectionHit]] = {}
        for part in self._parts:
            by_text.setdefault(part.text, []).extend(part.sources)
            positive[part.text] = positive.get(part.text, False) or part.positive
            projected.setdefault(part.text, []).extend(part.projected)
        return tuple(MaterialPart(text, tuple(dict.fromkeys(sources)), positive[text],
                                  tuple(dict.fromkeys(projected[text])))
                     for text, sources in by_text.items())

    def __iter__(self):
        return (part.text for part in self._parts)
