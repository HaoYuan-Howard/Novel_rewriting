#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_port_equivalence_stage1.py —— **移植等价性**：新实现与快速原型逐字节相同吗？

移植的判据不是「跑得通」，是「**与被移植的那份产出同一批字节**」。
分章判据是对 161 本语料实跑标定出来的（命中 27 本 / 0 假阳），
任何一处「顺手改得更清楚」的重写都可能在某一本上换一个答案，
而那本书不在手边时，没有任何东西会出声。

两层验收，都 0 成本：
  ① **全语料等价**（163 本）—— 纯函数对纯函数，覆盖那 161 本标定集
  ② **对盘上产物逐字节**（3 本）—— 连序列化在内，端到端

⚠ **这个文件是移植期的脚手架**：stage1 移植验收通过、且原型仓退场之后，它该被删。
   现在不删是因为判据还得反复跑。原型仓不在时它 **skip 并说明**，不静默通过。
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO_ROOT = os.path.join(os.path.dirname(ROOT), 'Novel_refactoring')
PROTO = os.path.join(PROTO_ROOT, 'Rapid_Prototyping')
CORPUS = os.path.join(PROTO_ROOT, 'Novel_data')
OUTPUT = os.path.join(PROTO_ROOT, 'Output')

pytestmark = pytest.mark.skipif(
    not os.path.isdir(PROTO),
    reason=f'找不到原型仓 {PROTO}——移植等价性无从验证。'
           f'这不是「通过」，是「查不了」（需求 Q-2）',
)


def _old():
    """import 快速原型的 stage1。它自己会往 sys.path 塞 Stage1/Stage2。"""
    for p in (os.path.join(PROTO, 'Stage1'), os.path.join(PROTO, 'Stage2'), PROTO):
        if p not in sys.path:
            sys.path.insert(0, p)
    import n1_partition                                                  # noqa: PLC0415
    return n1_partition


def _corpus_paths() -> list[str]:
    return sorted(glob.glob(os.path.join(CORPUS, '**', '*.txt'), recursive=True) +
                  glob.glob(os.path.join(CORPUS, '**', '*.md'), recursive=True))


def _dump(obj) -> str:
    """与两版落盘时**完全相同**的序列化参数。口径不同就不是逐字节比较了。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ───────────────────────── ① 全语料等价 ─────────────────────────

def test_corpus_is_present():
    paths = _corpus_paths()
    assert len(paths) >= 160, (
        f'只找到 {len(paths)} 份语料——分章判据是在 161 本上标定的，'
        f'少了就不是同一个验收'
    )


def test_new_matches_prototype_over_whole_corpus():
    """163 本逐本对拍：切分结果与报告必须逐字节相同。"""
    old = _old()
    from novel.core.text import read_source
    from novel.stages.stage1.partition import build_report, build_sections

    from novel.core.naming import derive_source_meta

    paths = _corpus_paths()
    diffs, skipped = [], []
    for p in paths:
        try:
            text, enc, sha1 = read_source(p)
        except Exception as e:                                           # noqa: BLE001
            # 解不开的书两版都解不开——它不参与等价性比较，但要记下来，不能静默跳过
            skipped.append((os.path.basename(p), type(e).__name__))
            continue

        sid, title, platform = derive_source_meta(p)
        ingest = {'original_filename': os.path.basename(p),
                  'original_path': os.path.abspath(p), 'sha1': sha1, 'encoding': enc}

        o_secs, o_mk, o_end, o_lim, o_drop = old.build_sections(text, sid)
        n_secs, n_mk, n_end, n_lim, n_drop = build_sections(text, sid)
        o_rep = old.build_report(sid, title, platform, text, o_secs, o_mk, o_end,
                                 o_lim, o_drop, ingest)
        n_rep = build_report(sid, title, platform, text, n_secs, n_mk, n_end,
                             n_lim, n_drop, ingest)

        for label, a, b in (('sections', o_secs, n_secs), ('report', o_rep, n_rep)):
            if _dump(a) != _dump(b):
                diffs.append(f'{os.path.basename(p)} 的 {label} 不同')

    assert not diffs, f'{len(diffs)}/{len(paths)} 本对不上：\n' + '\n'.join(diffs[:20])
    # 解不开的书也要报出来——「跳过了 N 本」与「全都比过了」必须能区分
    assert len(skipped) <= 2, f'意外地跳过了 {len(skipped)} 本：{skipped}'


# ───────────────────────── ② 对盘上产物逐字节 ─────────────────────────

def _books_on_disk() -> list[str]:
    return sorted(d for d in glob.glob(os.path.join(OUTPUT, '*'))
                  if os.path.isdir(d) and not os.path.basename(d).startswith('_')
                  and os.path.exists(os.path.join(d, 'stage1', 'partition_report.json')))


@pytest.mark.parametrize('novel_dir', _books_on_disk() or [pytest.param(None, marks=pytest.mark.skip(
    reason='盘上没有 stage1 产物可对'))])
def test_new_reproduces_on_disk_artifacts_byte_for_byte(novel_dir):
    """端到端：新实现重跑一遍，三份产物与盘上那份**逐字节相同**。

    这一条比 ① 更强：它连 `main_text` / `hook_copy` 的组装与序列化一起验了。
    """
    from novel.core.naming import derive_source_meta
    from novel.core.text import read_source
    from novel.stages.stage1.partition import partition

    s1 = os.path.join(novel_dir, 'stage1')
    on_disk = {name: open(os.path.join(s1, f'{name}.json'), encoding='utf-8').read()
               for name in ('main_text', 'hook_copy', 'partition_report')}
    src = json.loads(on_disk['partition_report'])['original_path']
    if not os.path.exists(src):
        pytest.skip(f'原始语料已不在盘上：{src}——这是「查不了」，不是「通过」')

    text, enc, sha1 = read_source(src)
    sid, title, platform = derive_source_meta(src)
    ingest = {'original_filename': os.path.basename(src),
              'original_path': os.path.abspath(src), 'sha1': sha1, 'encoding': enc}
    main_text, hook_copy, report = partition(text, sid, title, platform, ingest)

    for name, produced in (('main_text', main_text), ('hook_copy', hook_copy),
                           ('partition_report', report)):
        assert _dump(produced) == on_disk[name], (
            f'{os.path.basename(novel_dir)} 的 {name}.json 与盘上那份不同'
        )
