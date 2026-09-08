#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从唯一节点注册表渲染职责表；默认只读，--check 核对文档镜像。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import NODES

START = '<!-- node-lifecycle:start -->'
END = '<!-- node-lifecycle:end -->'


def render() -> str:
    rows = [START, '| 节点 | 工作单元／输入 | 生成／审阅 | 审阅动作 | 修复属主／动作 | 会话 | E/C 内容上限 | 恢复 |',
            '|---|---|---|---|---|---|---|---|']
    for node, spec in NODES.items():
        life = spec.lifecycle
        if life is None:
            raise ValueError(f'{node} 缺少职责登记')
        rows.append('| ' + ' | '.join((
            f'`{node}`', f'{life.unit_kind}：{life.input_workpack}',
            f'{life.generation_mode} / {life.review_mode}：{life.review_scope}',
            ', '.join(life.review_actions) or 'none',
            ', '.join(life.repair_owners) + '；' + ', '.join(life.repair_actions),
            life.session_scope, f'{life.executor_content_limit}/{life.commander_content_limit}',
            ', '.join(life.recovery_entries),
        )) + ' |')
    return '\n'.join([*rows, END])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', type=Path)
    args = parser.parse_args()
    expected = render()
    if args.check:
        content = args.check.read_text(encoding='utf-8')
        if START not in content or END not in content:
            parser.error('职责文档缺少生成区间')
        actual = content[content.index(START):content.index(END) + len(END)]
        if actual != expected:
            parser.error('职责文档与 NODES 漂移；请用当前 render() 更新生成区间')
    else:
        print(expected)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
