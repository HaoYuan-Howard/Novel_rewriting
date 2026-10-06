#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_preserve_gate —— CS-H H3 判据④：Preserve 自闸与条目返修（RC-64）。

注册判据：`preserve_name_literal_gate`（本文件即其反例套件）。

自闸＝与下游材料归因同一把尺：能被显示面投影的唯一归属带名面合法；**永不投影**
的字面（裸姓／家族构词／多义带名面；未登记别名因含裸姓成分同被查出）在义务
statement／satisfaction 里被逐条定位（`obligations[i]` 路径）。条目返修＝同一
协议：窄输入（协议规则＋失败条目＋该义务 anchors 的源原子切片＋合法取值），
无关义务字节不变。零付费：fake transport。
"""
from __future__ import annotations

from copy import deepcopy


def _wires():
    from tests.test_str06_hook import _full_chain_wires
    return _full_chain_wires()


def _prepared(tmp_path, wires, transport=None):
    """跑通 Stage2（真实入口）后返回 (ctx, repo, chars, blueprint)。"""
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_workflow
    from tests.str06_facilities import json_ask, text_ask
    from tests.test_str06_body import _chapters, _make_session
    repo, session, _transport = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    return ctx, repo, repo.read_model('stage2/characters/characters.json'), \
        repo.read_model('stage2/blueprints/chapter_blueprints.json')


def test_self_gate_flags_bare_surname_and_unregistered_alias_with_item_paths(tmp_path):
    """裸姓／家族构词／未登记别名逐条定位（`obligations[i]`）；可投影带名面合法。"""
    from contracts.schema.workflow import STAGE4_PRESERVE_POLICY_VERSION
    from novel.stages.stage4 import assemble_preserve, preserve_name_issues
    wires = _wires()
    _ctx, _repo, chars, blueprint = _prepared(tmp_path, wires)

    def assemble(wire):
        return assemble_preserve(
            wire, policy_id='preserve-default',
            policy_version=STAGE4_PRESERVE_POLICY_VERSION,
            characters=chars.core_view(), blueprint=blueprint,
            provider='x', model='y', request_sha256='r' * 64)

    wire = deepcopy(wires['preserve'])
    wire['obligations'][0]['statement'] = '陆某登门兴师问罪，芸娘心神不宁'
    checkpoint, errors = assemble(wire)
    assert checkpoint is not None and not errors, errors
    issues = preserve_name_issues(checkpoint, chars)
    flagged = {(i.context['obligation_id'], i.context['field'], i.context['literal'])
               for i in issues}
    assert flagged == {('ob01', 'statement', '陆')}, flagged
    assert issues[0].context['wire_paths'] == [['obligations', 0]]
    assert all(i.code == 'preserve_name_literal' and i.owner == 'preserve'
               for i in issues)
    # 绿对照：现役好稿（纯称谓＋可投影带名面）不触发。
    checkpoint, _errors = assemble(wires['preserve'])
    assert checkpoint is not None
    assert preserve_name_issues(checkpoint, chars) == [], \
        [str(i) for i in preserve_name_issues(checkpoint, chars)]


def test_one_obligation_repair_sends_only_that_item_and_keeps_others_intact(tmp_path):
    """判据④：修一条义务时请求输入输出只含该条及其依赖（对照本根 54,390／
    19,428 的整份重写），无关义务字节不变。"""
    from novel.llm.ledger import read_cost_snapshot
    from novel.stages.chain import run_preserve
    from tests.test_str06_design_patch import PatchingTransport

    wires = _wires()
    bad = deepcopy(wires['preserve'])
    bad['obligations'][0]['statement'] = '陆某登门兴师问罪，夫君面色沉沉'
    transport = PatchingTransport(wires, bad, owner='preserve')
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires, transport=transport)
    run_preserve(ctx)

    assert transport.calls.count('preserve') == 2, '首答＋一轮条目返修'
    revision = transport.owner_requests[1]
    # 窄输入：条目返修＋窄返修形状；不带整份投影视图，也不带其他义务的内容。
    assert '【条目返修】' in revision and '【窄返修】' in revision
    assert '句柄即 anchors 闭集' not in revision, '不重发整份输入投影视图'
    assert '【协议规则】' in revision and '【依赖切片】' in revision
    assert 'obligations[0]' in revision
    good = wires['preserve']
    for item in good['obligations'][1:]:
        assert item['statement'][:10] not in revision, '无关义务的 statement 不进窄请求'
    # 输出面：发布的 checkpoint 只换该条 statement，其余义务逐字段不变。
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    published = {o.obligation_id: o for o in checkpoint.obligations}
    for index, item in enumerate(good['obligations']):
        oid = f'ob{index + 1:02d}'
        assert oid in published, item
        if index == 0:
            assert published[oid].statement == item['statement'], '修复值来自好稿'
        else:
            assert published[oid].statement == item['statement'], '无关义务字节不变'
            assert published[oid].satisfaction == (item.get('satisfaction') or '')
    # 成本：原轮＋条目返修各一次实付。
    rows, _partial = read_cost_snapshot(repo.root.parent)
    assert sum(1 for r in rows if not r['cached'] and r['purpose'] == 'preserve') == 2


def test_replay_preserve_round_reports_gate_items(tmp_path):
    """判据⑤（preserve 面）：只读重放把已购 Preserve 回答按现役自闸逐条定位。"""
    from tools.replay_candidates import replay_preserve_round
    wires = _wires()
    _ctx, _repo, chars, blueprint = _prepared(tmp_path, wires)
    wire = deepcopy(wires['preserve'])
    wire['obligations'][0]['statement'] = '陆某登门兴师问罪'
    report = replay_preserve_round(wire, chars, blueprint)
    assert report['status'] == 'ran' and not report['passed']
    assert report['codes'].get('preserve_name_literal') == 1
    assert report['gate_items'] == ['ob01:statement:陆']
    good = replay_preserve_round(wires['preserve'], chars, blueprint)
    assert good['passed'] and good['reuse'] == 'green'
