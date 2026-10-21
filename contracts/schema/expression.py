"""R9 源表达与目标表达：语义陈述带证据，派生编号与读数由 compiler 负责。"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from contracts.schema.base import StrictModel
from contracts.schema.hook import HookPov


ExpressionKind = Literal['narration', 'pov_permission', 'humor', 'contrast']


class ExpressionClaim(StrictModel):
    kind: Literal['pov_permission', 'humor', 'contrast']
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)


class ObservedNarration(HookPov):
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)


class ExpressionObservation(StrictModel):
    narration: ObservedNarration
    mechanisms: tuple[ExpressionClaim, ...]


class SourceExpressionItem(StrictModel):
    ex_id: str = Field(pattern=r'^ex_\d{2,}$')
    kind: ExpressionKind
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)


class ExpressionReadings(StrictModel):
    """非空白长度与引号内字数占比；度量不可冒充质量闸。"""
    status: Literal['measured', 'unavailable']
    paragraph_mean_chars: float | None = Field(ge=0)
    sentence_mean_chars: float | None = Field(ge=0)
    dialogue_ratio: float | None = Field(ge=0, le=1)

    @model_validator(mode='after')
    def _measurement_closed(self):
        values = (self.paragraph_mean_chars, self.sentence_mean_chars, self.dialogue_ratio)
        if (self.status == 'measured' and any(v is None for v in values)
                or self.status == 'unavailable' and any(v is not None for v in values)):
            raise ValueError('表达读数未测量时必须全为 null；已测量时必须齐全')
        return self


class SourceExpression(StrictModel):
    narration: HookPov
    items: tuple[SourceExpressionItem, ...] = Field(min_length=1)
    readings: ExpressionReadings

    @model_validator(mode='after')
    def _ids_unique(self):
        ids = [i.ex_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError('源表达 ex_id 必须唯一')
        return self


class ExpressionRequirement(StrictModel):
    """源盲投影：只带语义，拒收证据引用与源坐标。"""
    ex_id: str = Field(pattern=r'^ex_\d{2,}$')
    kind: ExpressionKind
    statement: str = Field(min_length=1)


class ExpressionDecisionWire(StrictModel):
    model_config = {'extra': 'forbid', 'json_schema_extra': {'allOf': [
        {'if': {'properties': {'action': {'const': 'keep'}}},
         'then': {'properties': {'reason': {'type': 'null'}, 'target_statement': {'type': 'null'}}},
         'else': {'required': ['reason', 'target_statement'],
                  'properties': {'reason': {'type': 'string', 'minLength': 1},
                                 'target_statement': {'type': 'string', 'minLength': 1}}}}]}}
    ex_id: str = Field(pattern=r'^ex_\d{2,}$')
    action: Literal['keep', 'change']
    reason: str | None = Field(default=None, min_length=1)
    target_statement: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _decision_closed(self):
        if self.action == 'keep' and (self.reason is not None or self.target_statement is not None):
            raise ValueError('keep 沿用输入表达，不重复填写改写理由或目标说法')
        if self.action == 'change' and not (self.reason and self.reason.strip()
                                          and self.target_statement and self.target_statement.strip()):
            raise ValueError('change 必须给出非空理由与目标表达说法')
        return self


class ExpressionDecision(StrictModel):
    ex_id: str = Field(pattern=r'^ex_\d{2,}$')
    action: Literal['keep', 'change']
    reason: str | None
    statement: str = Field(min_length=1)

    @model_validator(mode='after')
    def _reason_closed(self):
        if self.action == 'change' and not (self.reason and self.reason.strip()):
            raise ValueError('改变表达必须保留理由')
        if self.action == 'keep' and self.reason is not None:
            raise ValueError('保留表达不带改写理由')
        return self
