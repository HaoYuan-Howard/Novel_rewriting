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
    # READER 已随 D5 统一（rc13，CS-3）删除：读者认知统一走
    # knowledge(holder='reader')——与 Brief 知情锁同一写法，不再两址一义。
    # 旧 rc12 产物的 reader 行经 store 读取层的登记迁移归一后进入本枚举域。


def address_schema(schema: dict) -> None:
    """与地址 validator 同源的局部分支；书级人物闭集由请求投影注入。"""
    schema['properties']['subjects']['uniqueItems'] = True
    schema.setdefault('allOf', []).extend([
        {'if': {'properties': {'domain': {'const': domain}}},
         'then': {'properties': {'subjects': {'minItems': size, 'maxItems': size}}}}
        for domain, size in (('character', 1), ('relation', 2))])
    schema['allOf'].append({
        'if': {'properties': {'domain': {'const': 'knowledge'}}},
        'then': {'required': ['holder'], 'properties': {'holder': {'type': 'string', 'minLength': 1}}},
        'else': {'properties': {'holder': {'type': 'null'}}}})


def change_schema(schema: dict) -> None:
    address_schema(schema)
    schema['allOf'].extend([
        {'if': {'properties': {'op': {'const': 'set'}}},
         'then': {'properties': {'value': {'type': 'string'}}},
         'else': {'properties': {'value': {'type': 'null'}}}},
        {'if': {'properties': {'op': {'const': 'set'}, 'domain': {'const': 'knowledge'}}},
         'then': {'required': ['knowledge_mode'],
                  'properties': {'knowledge_mode': {'not': {'type': 'null'}}}}},
        {'if': {'properties': {'op': {'const': 'set'}, 'domain': {'not': {'const': 'knowledge'}}}},
         'then': {'properties': {'knowledge_mode': {'type': 'null'}}}}])


class StateAddress(StrictModel):
    model_config = {'extra': 'forbid', 'json_schema_extra': address_schema}
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


class StateRead(StateAddress):
    """状态读取声明（B3 后半）：定位到入场或场内具体拍，按「先读旧值、后写
    新值」解析。

    `at=None` 定位到**入场**——读取本场写入前的旧值（本场会写该地址不再是
    删除入场读取的理由，F5e 实证那会迫使 Design 用 unknown 初态垫底）；
    `at` 给场内拍 id（compiler 派发的 `b{序:02d}`）定位到**该拍之前**——先写
    后读的「新值」读取。任何读取在其定位点没有已建立事实即拒（章闭包证明，
    `novel/stages/common.py::scene_read_views`）。
    """

    at: str | None = None

    @model_validator(mode='after')
    def _anchor_shape(self) -> 'StateRead':
        if self.at is not None and '#' in self.at:
            raise ValueError('读取定位是场内拍 id；完整引用由 compiler 组成')
        return self


class StateChange(StateAddress):
    model_config = {'extra': 'forbid', 'json_schema_extra': change_schema}
    #: knowledge 域的知情模式（v13 质量改进批次二补 believes）：believes＝持有者
    #: 以为为真，不代表世界为真——错误看法以此结构形式传到写手（D14）。
    op: Literal['set', 'unset', 'unknown']
    value: str | None
    scope: Literal['book', 'scene']
    knowledge_mode: Literal['known', 'suspected', 'heard', 'believes'] | None = None

    @model_validator(mode='after')
    def _operation(self) -> 'StateChange':
        if self.op == 'set' and self.value is None:
            raise ValueError('set 需要显式值（空字符串是合法空值）')
        if self.op != 'set' and self.value is not None:
            raise ValueError('unset/unknown 不得伪造值')
        # 知情模式只对「建立」有语义：unset/unknown 是解除与未知标记，无模式可言
        # （F5e 真书 wire 实证——模型在 unknown 解除项上留 null；此为解除项的合法形态）。
        if self.op == 'set' and (self.domain is EffectDomain.KNOWLEDGE) != (self.knowledge_mode is not None):
            raise ValueError('knowledge 的 set 必须声明知情模式；其它域不得带模式')
        return self


class SurfaceIntroduction(StrictModel):
    char_id: str = Field(min_length=1)
    surfaces: tuple[str, ...] = Field(min_length=1)


class SurfaceRelease(StrictModel):
    """一个人物本场可用的称谓面（IR/Hook 投影的查询面）。

    原定义在 context.py；F6 起 hook 投影也消费它，为避免 context↔design↔hook
    循环 import 下沉到 state（最小状态词汇层）。"""
    char_id: str = Field(min_length=1)
    surfaces: tuple[str, ...] = Field(min_length=1)


class WithheldNameBinding(StrictModel):
    """本场尚未释放、但已证明唯一绑定某一人物的完整登记称谓（CS-F2 称谓投影的验证材料）。

    只收**完整登记称谓**：来自该人物 Characters 的 surface、含姓名部件、全书只属于
    这一人物、字面长度≥2，且不是姓／家族构词本身。短姓、关系称呼、多义字面与未登记
    表达没有绑定——不据字符串相似性推断人物，由原 owner 登记或改写。正文章包与楔子
    上下文（rc16 起 Hook 自选释放面）共用这一把尺：渲染期把这类字面换成本场已释放的
    称谓；原字段、人物来源与内部身份保持不变，衍生文本不反写事实。原定义在
    book_ir.py；rc16 起被 HookContext 消费，为避免 hook↔book_ir 循环 import
    随 SurfaceRelease 同层放在 state。
    """

    literal: str = Field(min_length=2)
    char_id: str = Field(min_length=1)
    #: 已登记的、严格包含该字面的更长称谓（本场禁用的与已释放的都算）。渲染期这些区间
    #: 先被认领并原样保留——例如绑定字面「陆大哥」不会在更长的「陆大哥夫人」里被单独
    #: 替换。预先随绑定算好，渲染因此只依赖本表，不依赖验证材料（禁用／许可字面）。
    enclosing: tuple[str, ...] = Field(default=(), min_length=0)
