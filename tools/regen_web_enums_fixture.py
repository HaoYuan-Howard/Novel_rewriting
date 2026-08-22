#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regen_web_enums_fixture.py —— 把 `web/src/fixtures/backend_v1/enums.json` 重新生成。

【它存在的理由不是「省一次手工 dump」】
这份夹具是 `novel/api/enums.py::payload()` 的**第二落点**——mock 模式下前端读到的
整份取值面是它。`enums.py` 开头论证过「第 N 个落点必然分家」，而这份夹具就分家过
两个月（2026-08-22 量到：新增 18 键 · 消失 5 键 · 内容变化 14 键），因为
**两者之间一条对账都没有**。守卫现在有了
（`tests/test_api_enums.py::test_the_web_enums_fixture_is_the_same_payload`），
这个脚本是它的另一半：守卫说「不一致」，这里给出**唯一一条**把它弄回一致的路。

⚠⚠ **不许拿它当「把红变绿」的按钮。** 守卫报的 `stale`（夹具多出来的键）里可能
藏着真问题——「前端还在读一个后端已经删掉的键」——直接覆盖会把那个真问题一起冲掉。
⇒ 跑之前先逐键看清 `stale` 那一列：常量真的删了（夹具跟着删），还是被某个合成键
吃掉了（`enums.py::_CONSUMED`），还是前端确实还在读它（那要改的是前端不是夹具）。

⚠ 它**只重生成 enums.json 一份**。同目录另外那些夹具是真实响应抓包（要一本书、
要起 `create_app`），来路完全不同，见该目录 README。
⚠ 序列化口径与 `tests/test_api_enums.py::_DUMP` 必须一致，否则重生成完对账照样红。

用法：
    python tools/regen_web_enums_fixture.py            # 写盘
    python tools/regen_web_enums_fixture.py --check    # 只报差异，不写盘
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel.api.enums import payload                                  # noqa: E402

TARGET = ROOT / 'web' / 'src' / 'fixtures' / 'backend_v1' / 'enums.json'
#: 与 `tests/test_api_enums.py::_DUMP` 同一份口径。改一处要改两处。
DUMP = dict(ensure_ascii=False, indent=1)


def render() -> str:
    return json.dumps(payload(), **DUMP) + '\n'


def diff() -> tuple[list[str], list[str], list[str]]:
    """(夹具缺少, 夹具多出, 内容不同)。`payload()` 里的 tuple 先归一成 list。"""
    live = json.loads(render())
    if not TARGET.is_file():
        return sorted(live), [], []
    fixture = json.loads(TARGET.read_text(encoding='utf-8'))
    return (
        sorted(set(live) - set(fixture)),
        sorted(set(fixture) - set(live)),
        sorted(k for k in set(live) & set(fixture) if live[k] != fixture[k]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='只报差异并以非零退出，不写盘')
    args = parser.parse_args()

    missing, stale, changed = diff()
    print(f'夹具缺少 {len(missing)} 键：{missing}')
    print(f'夹具多出 {len(stale)} 键：{stale}')
    print(f'内容不同 {len(changed)} 键：{changed}')
    if stale:
        print('⚠ `stale` 非空：先逐键判定「常量真删了 / 被合成键吃掉 / 前端还在读」，'
              '再决定是不是该覆盖。')
    if not (missing or stale or changed):
        print('已一致，无需重生成。')
        return 0
    if args.check:
        return 1
    io.open(TARGET, 'w', encoding='utf-8', newline='\n').write(render())
    print(f'已重生成 {TARGET.relative_to(ROOT).as_posix()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
