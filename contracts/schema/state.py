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
    """本人尚未释放的登记个人姓名面；身份引用按 (char_id, literal) 查表。

    compiler 从 NameStructure 与按人物释放表派生；含姓名部件的单字真名及同形多人
    可登记，姓与家族构词、纯称谓不进表。显式身份引用可投影为本人的显示面，已释放
    面保持原文。旧裸文只允许长度≥2且 unmarked_unique 为真的绑定；该证明统计全书
    全部个人 surface（包括其他人的纯称谓与已释放面），不能从本表行数反猜唯一性。
    原字段、人物来源与内部身份不变；家族构词无本人许可即拒绝，不投影为个人称谓。
    """

    literal: str = Field(min_length=1)
    char_id: str = Field(min_length=1)
    unmarked_unique: bool = Field(strict=True)
    #: 已登记的、严格包含该字面的更长称谓（本场禁用的与已释放的都算）。渲染期这些区间
    #: 先被认领并原样保留——例如绑定字面「陆大哥」不会在更长的「陆大哥夫人」里被单独
    #: 替换。预先随绑定算好，渲染因此只依赖本表，不依赖验证材料（禁用／许可字面）。
    enclosing: tuple[str, ...] = Field(default=(), min_length=0)


class NameWatch(StrictModel):
    """compile 期派生的姓名监视行（IR/HookContext 携带；普通正文主链）。

    命名前移批（rc26）：identity-text 协议退役后，正文里的姓名只形成**定位报告**，
    不阻断、不购返修。监视行给报告提供归因（哪个人物、哪类字面）：

    · `old_name_residue`——改名人物的源旧字面（name-bearing 面、姓、名、化名）。
      目标链任何自然语言都不应再出现；出现在写手材料（确定性投影面）是
      `positive_material_leak` 硬闸，出现在正文只进定位报告；
    · `unreleased_name`——本章尚未释放的人物的目标字面。出现在写手材料是
      提前披露硬闸；出现在正文只进定位报告（疑似提前揭名）。

    字面集合由 compiler 从 NameMap＋名称结构按章派生，LLM 不生成（I-15/I-21）。
    """

    code: Literal['old_name_residue', 'unreleased_name']
    char_id: str = Field(min_length=1)
    literals: tuple[str, ...] = Field(min_length=1)
