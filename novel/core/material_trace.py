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
class MaterialFragment:
    """renderer 显式拼接的一段输出；固定模板不带正式字段来源。"""

    text: str
    sources: tuple[MaterialSource, ...] = ()


@dataclass(frozen=True)
class MaterialSpan:
    """输出半开区间与该片段的正式来源；只在内存中传递。"""

    start: int
    end: int
    sources: tuple[MaterialSource, ...]


@dataclass(frozen=True)
class MaterialPart:
    text: str
    sources: tuple[MaterialSource, ...] = ()
    positive: bool = True
    projected: tuple[NameProjectionHit, ...] = ()
    spans: tuple[MaterialSpan, ...] = ()
    ordinary_spans: tuple[tuple[int, int], ...] = ()


def sources_for_span(part: MaterialPart, start: int, end: int) -> tuple[MaterialSource, ...]:
    """只返回完整覆盖命中区间的显式来源，不从同一行其它文本猜测 owner。

    旧调用的 ``sources`` 仍保留诊断信息，但没有输出区间证明不能据此回派。
    命中跨过固定模板或其它无来源片段时同样保持 unknown。
    """
    if start < 0 or end <= start or end > len(part.text):
        raise ValueError('材料命中区间必须是文本内非空半开区间')
    relevant = tuple(span for span in part.spans
                     if span.start < end and start < span.end and span.sources)
    cursor = start
    for span in sorted(relevant, key=lambda item: (item.start, item.end)):
        if span.start > cursor:
            return ()
        cursor = max(cursor, span.end)
        if cursor >= end:
            return tuple(dict.fromkeys(source for item in relevant for source in item.sources))
    return ()


@dataclass(frozen=True)
class MaterialIssue:
    code: str
    where: str
    start: int
    literal: str
    sources: tuple[MaterialSource, ...] = ()
    chapter_ordinal: int = 0
    reason: str = ''
    char_id: str = ''

    @property
    def identity(self) -> dict:
        """正式语义位置的集合身份；文本偏移和展示文案不是控制字段。"""
        return {'code': self.code, 'literal': self.literal,
                **({'char_id': self.char_id} if self.char_id else {}),
                'chapter_ordinal': self.chapter_ordinal,
                'sources': sorted({(s.kind, s.ref, s.field, s.position) for s in self.sources})}

    @property
    def message(self) -> str:
        if self.code == 'positive_material_leak':
            detail = f'未释放专名字面 {self.literal!r}'
        elif self.code == 'internal_id_leak':
            detail = f'内部句柄 {self.literal!r} 不得进入写作材料'
        elif self.code == 'name_text_syntax':
            detail = '身份文本协议语法不闭合'
        elif self.code == 'name_text_reference':
            detail = f'人物身份引用未登记或未获授权 {self.literal!r}'
        elif self.code == 'name_text_unclassified':
            detail = f'姓名候选 {self.literal!r} 的本次出现尚未声明人物或普通义'
        else:
            detail = f'未识别的材料问题码 {self.code!r}，保持未决'
        if self.reason:
            detail += f'；{self.reason}'
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

    def append_fragments(self, *fragments: str | MaterialFragment, positive: bool = True) -> None:
        """按调用方明确的模板／字段边界拼接，来源随输出片段传递。

        str 片段是 renderer 明确拥有的模板／授权标签，声明为普通文本，不能被
        姓名投影改写。MaterialFragment 才是语义字段：各字段单独投影，保留其
        内部同位语折叠；不能把模板或另一个字段当成本字段的同位语证据。
        """
        ordinary = tuple(isinstance(value, str) for value in fragments)
        values = tuple(MaterialFragment(value) if is_ordinary else value
                       for value, is_ordinary in zip(fragments, ordinary))
        sources = tuple(dict.fromkeys(source for value in values for source in value.sources))
        outputs = tuple(self._project(value.text) if positive and self._project is not None
                        and not is_ordinary else (value.text, ())
                        for value, is_ordinary in zip(values, ordinary))
        projected = tuple(output for output, _hits in outputs)
        hits = tuple(hit for _output, found in outputs for hit in found)
        text = ''.join(projected)
        spans: list[MaterialSpan] = []
        ordinary_spans: list[tuple[int, int]] = []
        cursor = 0
        for value, output, is_ordinary in zip(values, projected, ordinary):
            if output and value.sources:
                spans.append(MaterialSpan(cursor, cursor + len(output), value.sources))
            if output and is_ordinary:
                ordinary_spans.append((cursor, cursor + len(output)))
            cursor += len(output)
        self._parts.append(MaterialPart(text, sources, positive, hits, tuple(spans), tuple(ordinary_spans)))

    def extend(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.append(text)

    def parts(self, *, deduplicate: bool = False) -> tuple[MaterialPart, ...]:
        if not deduplicate:
            return tuple(self._parts)
        by_text: dict[str, list[MaterialSource]] = {}
        positive: dict[str, bool] = {}
        projected: dict[str, list[NameProjectionHit]] = {}
        grouped: dict[str, list[MaterialPart]] = {}
        for part in self._parts:
            by_text.setdefault(part.text, []).extend(part.sources)
            positive[part.text] = positive.get(part.text, False) or part.positive
            projected.setdefault(part.text, []).extend(part.projected)
            grouped.setdefault(part.text, []).append(part)

        def proven_spans(text: str) -> tuple[MaterialSpan, ...]:
            # 去重合并的是多条实际产出路径：任一路径缺来源，改另一个有来源的
            # 字段仍修不掉该行。只有所有路径都证明同一区间时才保留回派证明。
            parts = grouped[text]
            edges = sorted({0, len(text), *(edge for part in parts for span in part.spans
                                           for edge in (span.start, span.end))})
            result: list[MaterialSpan] = []
            for start, end in zip(edges, edges[1:]):
                origins = tuple(sources_for_span(part, start, end) for part in parts)
                if all(origins):
                    result.append(MaterialSpan(start, end, tuple(dict.fromkeys(
                        source for sources in origins for source in sources))))
            return tuple(result)

        def ordinary_spans(text: str) -> tuple[tuple[int, int], ...]:
            parts = grouped[text]
            edges = sorted({edge for part in parts for span in part.ordinary_spans for edge in span})
            return tuple((start, end) for start, end in zip(edges, edges[1:])
                         if all(any(left <= start and end <= right for left, right in part.ordinary_spans)
                                for part in parts))

        return tuple(MaterialPart(text, tuple(dict.fromkeys(sources)), positive[text],
                                  tuple(dict.fromkeys(projected[text])),
                                  proven_spans(text), ordinary_spans(text))
                     for text, sources in by_text.items())

    def __iter__(self):
        return (part.text for part in self._parts)
