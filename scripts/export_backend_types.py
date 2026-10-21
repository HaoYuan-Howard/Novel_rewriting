"""从 contracts.invariants 的取值域元组生成前端静态联合类型；单源派生，勿手改。

在 novel_refactor 环境运行；--check 只验证已提交类型与当前取值域相等。
vocabulary.ts 曾手抄这些联合（enums.json 夹具因此分家过两个月），本生成器把
Freshness / Badge / Severity / JobState / InboxKind / StageId / FreshnessDisplay
收归后端唯一源。Op、DotShape、GateKey、交付状态等无直接后端元组或形状
不同的联合仍在 vocabulary.ts 手写，属登记的例外，不经本生成器。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: 直接以脚本文件运行时 sys.path 只含 scripts/；本仓包不经 editable 安装，
#: 显式补仓根，保证任何 cwd/启动方式下都能 import contracts。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import invariants  # noqa: E402

#: (invariants 常量名, TS 常量名, TS 类型名)
_SPECS = (
    ('FRESHNESS', 'FRESHNESS', 'Freshness'),
    ('ARTIFACT_DISPLAY', 'ARTIFACT_DISPLAY', 'FreshnessDisplay'),
    ('STORY_ARTIFACT_STATUS', 'STORY_ARTIFACT_STATUS', 'StoryArtifactStatus'),
    ('BADGE', 'BADGES', 'Badge'),
    ('SEVERITY', 'SEVERITIES', 'Severity'),
    ('JOB_STATE', 'JOB_STATES', 'JobState'),
    ('INBOX_KIND', 'INBOX_KINDS', 'InboxKind'),
    ('STAGES', 'STAGE_IDS', 'StageId'),
)


def render() -> str:
    lines = [
        '/**',
        ' * 由 scripts/export_backend_types.py 从 contracts/invariants.py 生成；',
        ' * 修改取值域后重新生成，勿手改。Op / DotShape 等显示层联合不在此列。',
        ' */',
    ]
    for inv_name, const, ty in _SPECS:
        values = getattr(invariants, inv_name)
        rendered = ', '.join(f"'{value}'" for value in values)
        lines.append(f'export const {const} = [{rendered}] as const')
        lines.append(f'export type {ty} = (typeof {const})[number]')
        lines.append('')
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = (Path(__file__).resolve().parents[1]
            / 'web' / 'src' / 'types' / 'backend.generated.ts')
    expected = render()
    if args.check:
        if not path.is_file() or path.read_text(encoding='utf-8') != expected:
            raise SystemExit(
                'web/src/types/backend.generated.ts 已过期，'
                '请运行 scripts/export_backend_types.py')
    else:
        path.write_text(expected, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
