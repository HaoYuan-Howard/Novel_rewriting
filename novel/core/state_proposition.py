"""状态命题的纯格式器：机器审计文本与有身份映射的阅读文本共用类型化输入。

只序列化正式 target/key/value，不识别或改写自然语言命题。使用者自行决定
哪些原子可确认；本模块不把 claim/inference 升格为已成立的事实。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from contracts.schema.source import STATE_KEY_LABELS


def _target(effect: Mapping[str, Any]) -> Mapping[str, Any]:
    target = effect['target']
    return target.model_dump() if hasattr(target, 'model_dump') else target


def _json_value(effect: Mapping[str, Any]) -> str:
    return json.dumps(effect.get('value'), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))


def state_statement(effect: Mapping[str, Any]) -> str:
    """状态原子的机器命题：主体、键与 JSON 值共同决定身份，不以真值折叠值。"""
    target = _target(effect)
    kind = target.get('kind')
    if kind == 'character':
        address = f"char:{target.get('char_id')}"
    elif kind == 'relation':
        address = f"rel:{target.get('subject')},{target.get('object')}"
    elif kind == 'world':
        address = 'world'
    else:
        raise ValueError(f'作用对象 kind 非法：{kind!r}')
    return f"{address}.{effect['key']} = {_json_value(effect)}"


def state_proposition_text(effect: Mapping[str, Any], *, names: Mapping[str, str]) -> str:
    """从正式结构主体查目标姓名；值原样序列化，不对全文执行 ID 替换。"""
    target = _target(effect)
    kind = target.get('kind')

    def name(char_id: str | None) -> str:
        text = names.get(char_id or '')
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f'状态命题的目标姓名缺失：{char_id!r}，须修复确定性名称投影')
        return text

    if kind == 'character':
        subject = name(target.get('char_id'))
    elif kind == 'relation':
        subject = f"{name(target.get('subject'))}对{name(target.get('object'))}"
    elif kind == 'world':
        subject = '世界'
    else:
        raise ValueError(f'作用对象 kind 非法：{kind!r}')
    key = effect['key']
    if key not in STATE_KEY_LABELS:
        raise ValueError(f'状态命题的显示标签未登记：{key!r}，不得猜测标签')
    return f'{subject}的{STATE_KEY_LABELS[key]}：{_json_value(effect)}'
