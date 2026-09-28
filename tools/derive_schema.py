#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从盘上真实产物机械反推 schema。0 成本、可重跑。

不猜、不推断语义——只报「观察到了什么」：
  · 每条路径的类型（多态就都列出来）
  · 出现率 present/total（< 1 = 可选，这是 required 的唯一依据）
  · 是否见过 null
  · 取值面：唯一值 ≤ ENUM_MAX 时全列（枚举候选），否则给样例与范围

⚠ 观察不等于契约：3 本书没见过的取值不代表非法。所以输出里
   「出现率」和「基数」一律带上分母，别把 3/3 读成「必填」。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

# 产物目录，**只从 NOVEL_OUTPUT 取，没有默认值**。
# 原来兜底的兄弟目录 `../Novel_refactoring/Output` 已于 2026-08-16 随原型仓删除；
# 留着一个指不到的默认值，比没有默认值更坏——因为 glob 的空结果**不报错**，
# 它只会产出一份「所有字段都零样本」的 schema，看起来和真跑过一模一样。
OUT = os.environ.get('NOVEL_OUTPUT')
if not OUT:
    raise SystemExit(
        '[derive_schema] 显式终态：没设 NOVEL_OUTPUT，而本脚本没有默认产物目录。\n'
        '    指到一个批次目录（其下是若干本书的目录）再跑，例如：\n'
        '        $env:NOVEL_OUTPUT = "outputs/skill_e2e_20260815"')
if not os.path.isdir(OUT):
    raise SystemExit(
        f'[derive_schema] 显式终态：找不到产物目录 {OUT}\n'
        f'    空目录会静默产出一份「所有字段都零样本」的 schema，\n'
        f'    而那份产物与真跑过的长得一模一样——比报错坏得多')
ENUM_MAX = 12          # 唯一值不超过这个数就当枚举候选全列出来
SAMPLE_LEN = 60
LONG_STR = 40          # 超过这个长度的字符串不当枚举候选看

# 产物清单：(标签, 相对 glob, 顶层是不是数组)
# ⚠ F8a：清单随 STR-06 切新链坐标（旧 stage2/4/5/6/7 路径产物整链退役，旧盘只读
#   拒识为 legacy_incompatible，新根上不会再出现）。旧链的 review/hook 轮次归档
#   （`_rounds/`）、`.INVALID` 稿与 rejected 归档面随旧链删除，不在此列。
ARTIFACTS = [
    ('stage1/main_text.json',        'stage1/main_text.json'),
    ('stage1/hook_copy.json',        'stage1/hook_copy.json'),
    # ⚠ B2/B5：联合抽取派生输入 `stage2/compiled/book_extraction.json` 已退役
    #   （D9，写路与读取全部停止）；book／chapters 观察是 Stage2 的现役产物面。
    ('stage2/observations/book.json', 'stage2/observations/book.json'),
    ('stage2/observations/chNN.json', 'stage2/observations/ch[0-9]*[0-9].json'),
    ('stage2/observations/names.json', 'stage2/observations/names.json'),
    ('stage2/observations/associate.json', 'stage2/observations/associate.json'),
    ('stage2/blueprints/chapter_blueprints.json',
     'stage2/blueprints/chapter_blueprints.json'),
    ('stage2/characters/characters.json', 'stage2/characters/characters.json'),
    ('stage4/compiled/execution_plan.json', 'stage4/compiled/execution_plan.json'),
    ('stage4/ir/writer_book_ir.json', 'stage4/ir/writer_book_ir.json'),
    ('stage4/design/body_design.json', 'stage4/design/body_design.json'),
    ('stage4/design/presentation_graph.json', 'stage4/design/presentation_graph.json'),
    ('stage4/context/hook/hook.json', 'stage4/context/hook/hook.json'),
    ('stage4/grounding/semantic_binding.json',
     'stage4/grounding/semantic_binding.json'),
    # 章号至少补齐两位，但可以超过 99；末尾再钉一位数字，避免把形状不同的
    # `.INVALID` 孪生（str06 bind 的 typed witness）一起吞进正式章样本。
    ('stage5/acceptance/body/chNN.json',
     'stage5/acceptance/body/ch[0-9]*[0-9].json'),
    ('stage7/naming/name_map.json',  'stage7/naming/name_map.json'),
    ('stage7/final/delivery_manifest.json',
     'stage7/final/delivery_manifest.json'),
]


def tname(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'bool'
    if isinstance(v, int):
        return 'int'
    if isinstance(v, float):
        return 'float'
    if isinstance(v, str):
        return 'str'
    if isinstance(v, list):
        return 'list'
    return 'obj'


class Node:
    __slots__ = ('types', 'seen', 'parent_seen', 'values', 'too_many',
                 'lens', 'nums', 'child_keys')

    def __init__(self):
        self.types = set()
        self.seen = 0            # 这个 key 出现了几次
        self.parent_seen = 0     # 它的父对象出现了几次（分母）
        self.values = set()
        self.too_many = False
        self.lens = []           # str 长度 / list 长度
        self.nums = []
        self.child_keys = set()  # obj 作 map 用时的键（如 characters{char_id}）


def walk(node_map: dict, path: str, val, parent_count_key: str | None):
    n = node_map[path]
    n.seen += 1
    n.types.add(tname(val))
    if isinstance(val, str):
        n.lens.append(len(val))
        if not n.too_many:
            n.values.add(val if len(val) <= SAMPLE_LEN else val[:SAMPLE_LEN] + '…')
            if len(n.values) > ENUM_MAX:
                n.too_many = True
    elif isinstance(val, bool):
        n.values.add(val)
    elif isinstance(val, (int, float)):
        n.nums.append(val)
    elif isinstance(val, list):
        n.lens.append(len(val))
        for item in val:
            walk(node_map, path + '[]', item, path)
    elif isinstance(val, dict):
        keys = list(val)
        # 启发：键全都长得像 id（char_001 / rel_05 / b01_02 / 数字）→ 当成 map 而不是固定对象
        as_map = len(keys) >= 3 and all(re.fullmatch(r'[a-z]{1,10}_?\d+|\d+', k) for k in keys)
        if as_map:
            n.child_keys.update(keys[:3])
            for k in keys:
                walk(node_map, path + '{*}', val[k], path)
        else:
            for k in keys:
                walk(node_map, f'{path}.{k}' if path else k, val[k], path)
            for k in keys:
                node_map[f'{path}.{k}' if path else k].parent_seen += 0  # 占位
            # 记下这一层出现过的所有键，供 required 判定
            n.child_keys.update(keys)


def summarize(label: str, docs: list) -> list[str]:
    node_map: dict[str, Node] = defaultdict(Node)
    for d in docs:
        walk(node_map, '', d, None)

    root = node_map['']
    lines = [f'\n### `{label}`  —— 样本 {len(docs)} 份，顶层 {"/".join(sorted(root.types))}']
    if root.lens and 'list' in root.types:
        lines.append(f'顶层数组长度：{min(root.lens)}~{max(root.lens)}')
    lines.append('')
    lines.append('| 路径 | 类型 | 出现 | 取值面 / 范围 |')
    lines.append('|---|---|---|---|')

    for path in sorted(node_map):
        if path == '':
            continue
        n = node_map[path]
        # 分母 = 它父节点被看见的次数
        parent = path.rsplit('.', 1)[0] if '.' in path else ''
        for suf in ('[]', '{*}'):
            if path.endswith(suf):
                parent = path[:-len(suf)]
                break
        types = '/'.join(sorted(n.types))
        if path.endswith(('[]', '{*}')):
            # 【元素不是「可选字段」】数组/映射的元素按定义总是存在，
            # 拿它跟容器出现次数比会得出 `31/3 ⚠可选` 这种读起来像缺失的假象。
            rate = f'{n.seen} 项'
        else:
            denom = node_map[parent].seen if parent in node_map else len(docs)
            opt = '' if (denom and n.seen == denom) else ' ⚠可选'
            rate = f'{n.seen}/{denom}{opt}'

        if n.nums:
            lo, hi = min(n.nums), max(n.nums)
            face = f'{lo} ~ {hi}' if lo != hi else f'恒 {lo}'
        elif n.values and not n.too_many and (not n.lens or max(n.lens) <= LONG_STR):
            # 【长文本不是枚举】一组三段各 150 字的正文不构成取值面，标成「枚举候选」
            # 只会让读的人以为那是契约（判据 32：名字先于证据定下来，后来的人就按名字解释它）。
            vs = sorted(n.values, key=str)
            face = ' · '.join(f'`{v}`' for v in vs[:ENUM_MAX])
            if 'str' in n.types and len(vs) > 1:
                face = f'**枚举候选（{len(vs)}）** ' + face
        elif n.lens:
            face = f'长度 {min(n.lens)}~{max(n.lens)}'
        else:
            face = ''
        if n.child_keys and 'obj' in n.types and path.endswith('{*}') is False:
            pass
        lines.append(f'| `{path}` | {types} | {rate} | {face} |')
    return lines


def main():
    books = sorted(d for d in glob.glob(os.path.join(OUT, '*'))
                   if os.path.isdir(d) and not os.path.basename(d).startswith('_'))
    print(f'# 产物 schema —— 从盘上 {len(books)} 本机械反推\n')
    print('> 只报「观察到了什么」，不猜语义。**出现率的分母是样本数，不是契约**——')
    print('> 3 本书没见过的取值不代表非法，`3/3` 也不自动等于「必填」。\n')
    for label, pat in ARTIFACTS:
        docs = []
        for b in books:
            for p in sorted(glob.glob(os.path.join(b, pat))):
                try:
                    docs.append(json.load(open(p, encoding='utf-8')))
                except Exception as e:                                 # noqa: BLE001
                    print(f'  ⚠ 读不了 {p}: {e!r}')
        if not docs:
            print(f'\n### `{label}` —— **盘上没有样本**，schema 只能从契约文档来')
            continue
        print('\n'.join(summarize(label, docs)))


if __name__ == '__main__':
    main()
