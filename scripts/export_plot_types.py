"""从正式 Pydantic 响应契约生成剧情链 TypeScript 类型；不推导任何 UI 判定。

在 novel_refactor 环境运行；--check 只验证已提交类型与当前契约相等。
不处理的 JSON Schema 形状必须报错，不能静默生成 any 掩盖数据缝。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from contracts.schema.api import (
    BlueprintReadingWorkpack, CtxChapterView, PlanChapterLine, ReadingView, Stage4View,
    ApprovalWorkpack, DesignReviewTemplate, DesignReviewView,
)
from contracts.schema.plot_review import BlueprintRunProgress
from contracts.schema.plot_edit import PlotEdit, PlotEditPreview


def ts(node: dict) -> str:
    if '$ref' in node:
        return node['$ref'].rsplit('/', 1)[-1]
    if 'const' in node:
        return json.dumps(node['const'], ensure_ascii=False)
    if 'enum' in node:
        return ' | '.join(json.dumps(v, ensure_ascii=False) for v in node['enum'])
    for union in ('anyOf', 'oneOf'):
        if union in node:
            return ' | '.join(ts(v) for v in node[union])
    kind = node.get('type')
    if isinstance(kind, list):
        return ' | '.join(ts({**node, 'type': item}) for item in kind)
    if kind in ('string', 'boolean', 'null'):
        return kind
    if kind in ('number', 'integer'):
        return 'number'
    if kind == 'array':
        if 'prefixItems' in node:
            return '[' + ', '.join(ts(item) for item in node['prefixItems']) + ']'
        return '(' + ts(node.get('items', {})) + ')[]'
    if kind == 'object':
        fields = node.get('properties', {})
        # 目标是默认 model_dump 的响应形状：默认值和 null 同样序列化，
        # 不能把带默认 discriminator 生成可缺字段而破坏 TS 联合收窄。
        parts = [json.dumps(key) + ': ' + ts(value)
                 for key, value in fields.items()]
        extra = node.get('additionalProperties')
        if isinstance(extra, dict):
            parts.append('[key: string]: ' + ts(extra))
        elif extra is True or (not fields and extra is not False):
            parts.append('[key: string]: unknown')
        return '{\n' + '\n'.join('  ' + part.replace('\n', '\n  ') for part in parts) + '\n}'
    if not node or set(node) <= {'title', 'description', 'default', 'examples'}:
        return 'unknown'
    raise ValueError(f'未支持的 schema：{node}')


def render() -> str:
    definitions = {}
    for model in (ReadingView, BlueprintReadingWorkpack, BlueprintRunProgress,
                  PlotEdit, PlotEditPreview,
                  PlanChapterLine, CtxChapterView, Stage4View,
                  ApprovalWorkpack, DesignReviewTemplate, DesignReviewView):
        schema = model.model_json_schema(mode='serialization', by_alias=True)
        nested = schema.pop('$defs', {})
        for key, value in {**nested, model.__name__: schema}.items():
            if key in definitions and definitions[key] != value:
                raise ValueError(f'类型同名但形状不同：{key}')
            definitions[key] = value
    return ('/** 由 scripts/export_plot_types.py 生成；修改正式契约后重新生成。 */\n'
            + '\n\n'.join(f'export type {name} = {ts(definitions[name])}'
                           for name in sorted(definitions)) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / 'web/src/types/plot.generated.ts'
    expected = render()
    if args.check:
        if not path.is_file() or path.read_text(encoding='utf-8') != expected:
            raise SystemExit('剧情类型已过期，请运行 scripts/export_plot_types.py')
    else:
        path.write_text(expected, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
