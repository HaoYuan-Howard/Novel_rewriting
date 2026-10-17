"""身份引用文本的唯一协议；语义选择归作者，坐标由 compiler 派生。

[[name:人物句柄|已登记称谓]] 是人物提及；[[text|普通文字]] 是明确的非人物
文字声明。后者可以覆盖与姓名同形的普通词。声明的语义正确性保持 unver，
机械层只证明语法、引用、坐标、候选覆盖和释放权限。
"""
from __future__ import annotations

from typing import Final

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel


NAME_TEXT_PROTOCOL: Final = 'identity-text-v1'
NAME_TEXT_INSTRUCTIONS: Final = """身份引用文本协议 identity-text-v1：
身份名称表达写成 [[name:char_id|称谓]]，char_id 与称谓只能取本次给出的合法身份表。
其余文字用 [[text|普通文字]] 明确声明，例如 [[text|心里明白]]；不带姓名的代词和
关系称谓可作为普通叙述。不得把真实名称表达藏进 text 片段。普通词不因和姓名
部件同形而变成人物名称。
不要生成字符偏移、区间、统计或新人物。片段不嵌套，内容不得含 [[、]] 或 |。
未标记的候选是指称未决，需要补充声明；它不等于已经证明姓名泄漏。
代码将解析引用和普通义片段，正式正文不保留标记。标注的语义准确性仍属未评。
"""


class NameTextSpan(StrictModel):
    """compiler 生成的明确普通义片段（正文 Unicode 字符坐标，左闭右开）。"""
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('文本区间必须非空且递增')
        return self


class NameMention(NameTextSpan):
    """已显式声明的身份名称表达（含登记家族构词）；不从字面猜 char_id。"""
    char_id: str = Field(min_length=1)
    surface: str = Field(min_length=1)
