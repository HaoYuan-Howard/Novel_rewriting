"""RC2 最小状态词汇：地址、操作与作用域只有一份定义，不从自然语言猜主体。"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel


class EffectDomain(str, Enum):
    CHARACTER = 'character'
    RELATION = 'relation'
    KNOWLEDGE = 'knowledge'
    WORLD = 'world'
    READER = 'reader'


class StateAddress(StrictModel):
    domain: EffectDomain
    key: str = Field(min_length=1)
    subjects: tuple[str, ...] = ()
    holder: str | None = None

    @model_validator(mode='after')
    def _address(self) -> 'StateAddress':
        required = {EffectDomain.CHARACTER: 1, EffectDomain.RELATION: 2}.get(self.domain)
        if required is not None and len(self.subjects) != required:
            raise ValueError(f'{self.domain.value} 必须显式声明 {required} 个作用对象')
        if len(set(self.subjects)) != len(self.subjects) or any(not s for s in self.subjects):
            raise ValueError('作用对象不得为空或重复')
        if (self.domain is EffectDomain.KNOWLEDGE) != bool(self.holder):
            raise ValueError('knowledge 必须给 holder；其它域不得带 holder')
        return self

    def address(self) -> tuple:
        return self.domain.value, self.subjects, self.holder or '', self.key


class StateChange(StateAddress):
    op: Literal['set', 'unset', 'unknown']
    value: str | None
    scope: Literal['book', 'scene']
    knowledge_mode: Literal['known', 'suspected', 'heard'] | None = None

    @model_validator(mode='after')
    def _operation(self) -> 'StateChange':
        if self.op == 'set' and self.value is None:
            raise ValueError('set 需要显式值（空字符串是合法空值）')
        if self.op != 'set' and self.value is not None:
            raise ValueError('unset/unknown 不得伪造值')
        if (self.domain is EffectDomain.KNOWLEDGE) != (self.knowledge_mode is not None):
            raise ValueError('knowledge 必须声明知情模式；其它域不得带模式')
        return self


class SurfaceIntroduction(StrictModel):
    char_id: str = Field(min_length=1)
    surfaces: tuple[str, ...] = Field(min_length=1)
