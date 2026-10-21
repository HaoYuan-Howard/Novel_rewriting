"""书级表达的确定性发布：保留观察陈述、派发编号及正文读数，不推断新语义。"""
from __future__ import annotations
import re
from contracts.schema.expression import ExpressionReadings, SourceExpression, SourceExpressionItem
from contracts.schema.hook import HookPov
from novel.core.text import count_non_whitespace_chars


def expression_readings(texts: tuple[str, ...] | None) -> ExpressionReadings:
    if texts is None:
        return ExpressionReadings(status='unavailable', paragraph_mean_chars=None,
                                  sentence_mean_chars=None, dialogue_ratio=None)
    paragraphs = [count_non_whitespace_chars(p) for text in texts for p in text.splitlines() if p.strip()]
    sentences = [count_non_whitespace_chars(p) for text in texts
                 for p in re.findall(r'[^。！？!?]+[。！？!?]*', text) if p.strip()]
    total = sum(count_non_whitespace_chars(t) for t in texts)
    dialogue = sum(count_non_whitespace_chars(m.group(1) or m.group(2) or '')
                   for text in texts for m in re.finditer(r'“([^”]*)”|「([^」]*)」', text))
    return ExpressionReadings(status='measured',
                              paragraph_mean_chars=sum(paragraphs) / len(paragraphs) if paragraphs else 0,
                              sentence_mean_chars=sum(sentences) / len(sentences) if sentences else 0,
                              dialogue_ratio=dialogue / total if total else 0)


def compile_source_expression(observation, *, texts: tuple[str, ...] | None) -> SourceExpression:
    narration = observation.narration
    claims = [('narration', narration.statement, narration.source_refs),
              *((m.kind, m.statement, m.source_refs) for m in observation.mechanisms)]
    return SourceExpression(narration=HookPov(person=narration.person, focalizers=narration.focalizers),
        items=tuple(SourceExpressionItem(ex_id=f'ex_{n:02d}', kind=kind,
                                        statement=statement, source_refs=refs)
                    for n, (kind, statement, refs) in enumerate(claims, 1)),
        readings=expression_readings(texts))
