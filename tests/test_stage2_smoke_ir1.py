#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_smoke_ir1.py —— 7A 高难包冒烟工具（`tools/smoke_stage2_ir1.py`）。

它钉住三件事，每一件都是「钱会不会白花 / 读数会不会说假话」：
① **子集请求与整跑逐字节同构**——冒烟付费买到的响应必须能被后续整跑的缓存
   原样接住；哪天 `build_blueprint_user` 依赖了「本次跑几章」，这条当场红；
② **只发目标章**——子集就是子集，不许顺手把整本发出去；
③ **合并稿只活在内存里**：未动章逐字节沿用盘上 v3 正式稿、目标章换成 ir1
   编译结果，跑完书目录零改动（工具自己也核一道，这里断言它真的核得住）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from contracts.schema import MainText
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, main_text
from tests.test_stage2_contract_dispatch import _ir1_responses, _summary_for
from tests.test_stage2_generation import (
    _client, _ctx, _ok, _response_blueprints, _response_global,
)
from tools import smoke_stage2_ir1 as S

_ENV_KEY = 'NOVEL_STAGE2_BLUEPRINT_CONTRACT'


def _target_of(user: str) -> int:
    found = re.findall(r'阅读序第(\d+)章', user)
    assert found, '单章 user prompt 没有任务范围'
    return int(found[-1])


def _seed_v3_book(tmp_path: Path, monkeypatch) -> tuple[Path, Path, str]:
    """造一本「盘上已有 v3 正式蓝图」的书——冒烟的现实起点就是这个形状。

    ⚠ 基线要**显式拧到 v3**（选择口是进程级的环境变量，而 2026-08-21 起默认
    已经是 ir1）：拿 ir1 的响应模型去收 v3 的假响应，schema 闸会当场把整轮拒光。
    产完再拧回 ir1。
    """
    monkeypatch.setenv(_ENV_KEY, 'bounded-fidelity-v3')
    base = tmp_path / 'outputs'
    book = base / '冒烟_777'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    by_ordinal = _response_blueprints()
    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(
        ctx, lambda req, _i: _ok({'blueprints': [by_ordinal[_target_of(req.user)]]}))
    report = extract.run_blueprint(ctx, client)
    assert report.passed, 'v3 基线自己就没落成，后面的对照没有意义'
    monkeypatch.setenv(_ENV_KEY, 'extraction-ir-v1')
    return base, book, '777'


def _ir1_responder(ordinals: list[int]):
    by_ordinal = _ir1_responses()

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            payload = json.loads(req.user.split('\n', 1)[1])
            got = [c['chapter_ordinal'] for c in payload['chapters']]
            assert got == sorted(ordinals), '摘要批只该带本次目标章'
            return _ok({'chapters': [{'chapter_ordinal': o,
                                      'summary': _summary_for(o)} for o in got]})
        return _ok({'blueprints': [by_ordinal[_target_of(req.user)]]})

    return respond


def test_a_chapter_subset_asks_byte_identical_questions(tmp_path, monkeypatch):
    """子集请求 == 整跑里同章那几条。**这条性质就是冒烟能省钱的全部依据。**"""
    base, book, sid = _seed_v3_book(tmp_path, monkeypatch)
    ctx = _ctx(book, 'stage2.blueprint')

    whole = {o: (s, u) for o, s, u in extract.build_prompts(ctx)}
    subset = extract.build_prompts(ctx, chapters=(2,))

    assert [o for o, _s, _u in subset] == [2]
    for ordinal, system, user in subset:
        assert whole[ordinal] == (system, user)


def test_the_smoke_pays_only_for_the_targets_and_leaves_the_book_untouched(
        tmp_path, monkeypatch):
    base, book, sid = _seed_v3_book(tmp_path, monkeypatch)
    before = S._dir_fingerprint(book)
    calls: list[object] = []

    def fake_client(ctx):
        client, transport = _client(ctx, _ir1_responder([2]))
        calls.append(transport)
        return client

    monkeypatch.setattr(S, '_llm_client', fake_client)
    code = S.main(['--base', str(base), '--sid', sid, '--chapters', '2',
                   '--execute', '--json-out', str(tmp_path / 'r.json'),
                   '--md-out', str(tmp_path / 'r.md')])

    assert code == 0
    assert len(calls[0].calls) == 2, '一章 + 一次摘要批'
    assert S._dir_fingerprint(book) == before, '冒烟工具不许改书目录一个字节'

    report = json.loads((tmp_path / 'r.json').read_text(encoding='utf-8'))
    assert report['contract'] == 'extraction-ir-v1'
    assert [row['chapter_ordinal'] for row in report['per_chapter']] == [2]
    assert report['summary_mode'].startswith('summary-batch')
    assert report['cost']['calls_paid'] == 2
    assert '高难包付费冒烟' in (tmp_path / 'r.md').read_text(encoding='utf-8')


def test_the_merged_candidate_keeps_untouched_chapters_byte_identical(
        tmp_path, monkeypatch):
    """合并稿：未动章一个字节不动，目标章整章换新，架构如实记 ir1。"""
    base, book, sid = _seed_v3_book(tmp_path, monkeypatch)
    ctx = _ctx(book, 'stage2.blueprint')
    both = extract._Both(ArtifactStore(ctx))
    baseline = both.blueprints
    characters = (both.global_g or {})['entities']['characters']

    compiled, accounting = S.contract_for('extraction-ir-v1').compile_fn(
        {'blueprints': [_ir1_responses()[2]]}, both.main_text, characters,
        summaries={2: '合并稿测试用摘要'})
    merged = S._merged_candidate(baseline, compiled['blueprints'], accounting,
                                 both.main_text, characters)

    by_ordinal = {bp['chapter_ordinal']: bp for bp in merged['blueprints']}
    assert sorted(by_ordinal) == [1, 2, 3]
    for untouched in (1, 3):
        assert by_ordinal[untouched] == next(
            bp for bp in baseline['blueprints']
            if bp['chapter_ordinal'] == untouched)
    assert by_ordinal[2]['summary'] == '合并稿测试用摘要'
    assert merged['_provenance']['context']['architecture'] == 'extraction-ir-v1'
    # 目标章的逐字账**整段换新**：旧账里属于第 2 章的行不许混进来。
    for key in ('verbatim_rewrites', 'verbatim_failures'):
        assert [row for row in merged['_provenance'][key]
                if S._chapter_of(row[0]) == 2] == \
            [list(row) for row in accounting[key]]
