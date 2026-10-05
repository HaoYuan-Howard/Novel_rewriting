#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_product_entry —— F8d：真实产品入口全链集成（fake transport，零真实 LLM）。

【验什么】把「HTTP 上传 → autostart → `agent.start` → Supervisor 按 operator next
逐动作驱动 → 13 个 `str06.` 节点 → stage7 交付」用 fake transport 真跑一遍，并核对
零调用恢复、`--check-only`、旧书拒读与最终交付。规划面（`operator.next`／
`queue.plan`）**不打桩**：节点顺序、贵贱、写盘与恢复判定全部由现役 `NODES` 派生。
fake transport 复用 `tests/test_str06_body.py` 的路由表（同一份 wires），正文按章
给出落在章篇幅带内的稿——真实调用走 `PaidRequestSession` 的 reserve／记账／缓存
同一条路（I-51：不依赖真实线上 LLM）。第七节（R5b）另验 B6-A 前置链：HTTP 起跑
→ 正文前暂停 → CLI 选章 canary → HTTP 续跑只补剩余正文 → 交付，续跑是新 run
（continuation 指向原 run）。

【基座形状】最小合法 base = `initialize_workflow` + stage0/stage1 产物（经真实
`novel.cli run` 落盘）；上传触发 autostart 后 Supervisor 的第一个 ready 动作就是
`str06.stage2.book`——stage1 章数组是新链的正式输入，预置它正是任务口径的
「最小合法 base」。上传自身仍会真跑一次 stage0 作业（同一份源字节，确定性重放）。

【耗时与覆盖】完整子进程链只保留第四节一条，继续验证真实代码身份。
其余五例将关键 owner 的 run_step/CLI 留在真实子进程；非关键节点在进程内
执行同一生产 CLI，全部业务、账务、产物、免费恢复及重启读回断言保留。
跨节点回派的 design/bind/compile 均为真实子进程；封存丢失后的全部节点恢复
也为真实子进程。普通场景父子使用确定性测试身份，不缓存生产 code_version。
每例以 _assert_process_window 核对真实窗口，串行登记不变。

【与生产 Popen 的差异（必读）】
生产路径上 Supervisor 每个动作经 `agent._run_step_argv` 起
`novel.orchestrator.run_step` 子进程：由它铸 admission claim、打 start/end 信标、
落 evidence，再 `subprocess.run` 真正的 `novel.cli run <node>`。本文件把这条
**节点执行的子进程缝**换成进程内调用真实 `novel.cli.main.main([...])`
（`_InProcessPopen`）：被执行的仍是生产 CLI 的同一行代码（`_cmd_run` →
`_run_str06` → `novel/stages/chain.py::execute`），但 run_step 的
claim／信标／evidence 三层不在进程内复刻。理由：这三层是 admission/beacon 设施，
进程内复刻只会把测试变成基础设施的自证；**argv 形状不在豁免之列**——F8b 时
run_step 会把 executor backend 旗标（`--executor-backend legacy …`）冻进 argv
而 `_run_str06` 拒收（真实接缝，F9 产品跑书时实证）。CS-T进一步修正配置断链：
canonical argv经`--request-profile`传递冻结配置，付费和免费复检使用同一身份。
本文件的进程内替身只覆盖业务推进，不复刻父运行配置环境；配置传递由真实子进程
全链及显式配置CLI用例核对到provider与ledger。两处替身的影响面：

  · `agent._run_step_argv`：**原样使用生产 `nodes.build_argv`**（含 `--yes` 与
    `--allow-egress`；F8c 已把外发授权与 executor backend 解钩，直连 LLM
    节点由同一构造点拿到这枚 I-43 独立授权）。测试不补任何旗标。
  · `subprocess.Popen`：不真起进程；识别 `-m novel.cli` 与
    `-m novel.orchestrator.delivery` 两种 canonical argv，进程内跑对应 `main()`，
    退出码原样返回（其余 argv 关闭式报错，不静默跳过）。上传里的 stage0 作业
    （`jobs.run` → `default_spawn`）也走这条缝、同样进程内执行——stage0/stage1
    是免费确定性节点，不涉及模型调用。

【本批附带的生产 seam（两处，均在完成度／规划面，未改任何盘上格式）】
① 完成度面此前读不出候选链盘面（候选产物不带版本信封、`root_clean_sha1` 记的是
   Stage1 章数组而非 stage0 clean_text）⇒ 一整本跑完的书在完成度上恒 broken/stale，
   operator 据此把每个已跑完的节点判成 REDO/UNKNOWN 并关闭式停住，真实产品入口的
   全链因此跑不通。修复落在 `novel/store.py::candidate_freshness`／
   `candidate_readable` 与 `novel/orchestrator/state.py::_candidate_artifact_state`
   （同一把 `prov.freshness` 尺子，删除条件写在 store 的桥段注释里）。
② `artifacts_for_node` 对 Stage5 的非章稿节点回落整层 artifacts：干净书只物化问题行，
   Hook 节点自己的两行健康时不在盘面里，回落就把**章稿缺失**算到 Hook 节点头上——
   实测 Supervisor 对一个已成功的节点反复派发（死循环：每轮 rc=0、盘面不变）。
   修复：Stage5 的非 `str06.stage5.body` 节点只认自己的 produces。

【遗留问题（本文件实测发现）】
① `--check-only` 声明零写盘（`WRITE_EFFECTS`），但候选 repository 的
   provenance 铸账仍会重写产物字节（usage 窗口归零）——本测试只断言零新购与
   交付字节不变，不把重写当作已验收行为。
② （F9 已修复，记录备查）生产 run_step 曾把 executor backend 旗标
   （`--executor-backend legacy …`）冻进直连 LLM 节点 argv，`_run_str06` 按
   直连 LLM 纪律拒收——F9 产品跑书时实证；`nodes.build_argv` 已按
   `DIRECT_LLM_NODE_PREFIX` 修复（str06 节点不挂 executor 旗标），派发断言
   钉住新行为，claim／信标／evidence 三层的进程内替身差异仍在文件头说明。
③ （CS-H H1 已修复，记录备查）产品入口逐节点执行 ⇒ run 级 `runtime/workflow_status.json`
   落在最后一次单节点执行上，曾恒记 `node_run`／`accepted_body=false`，已交付的书在状态里
   仍是未接受；现单节点路径跑了交付组装即取交付报告终态（`delivered`／`assembly_not_ready`），
   与整链同源（RC-54）。就绪结论的权威记录仍是 assemble 最后提交的 manifest。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import traceback
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批没在查，不是查过没事')
from fastapi.testclient import TestClient  # noqa: E402

from contracts.schema.orchestration import (  # noqa: E402
    JobRecord, JobStart, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles, Step,
)
from novel import paths  # noqa: E402
from novel.api.app import UI_HEADER, create_app  # noqa: E402
from novel.cli import main as cli  # noqa: E402
from novel.errors import TerminalState  # noqa: E402
from novel.llm.ledger import read_cost_snapshot  # noqa: E402
from novel.orchestrator import (  # noqa: E402
    agent, agent_settings, operator, queue, state,
)
from novel.stages.chain import NODE_ORDER  # noqa: E402
from novel.stages.stage5.body import sha256_text  # noqa: E402
from tests.str06_product_transport import (  # noqa: E402
    GOOD_CH01, GOOD_CH02, _EntryTransport, _make_entry_transport,
)
from tests.test_str06_hook import _full_chain_wires  # noqa: E402

FILENAME = 'FQ_联调样本_9001.txt'
LEGACY_FILENAME = 'FQ_旧书样本_9002.txt'
SOURCE_TEXT = (
    '开篇钩子。\n\n'
    '1\n\n'
    '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。\n\n'
    '2\n\n'
    '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。\n'
)

class _InProcessPopen:
    """测试替身：把 canonical 子进程 argv 改为进程内执行真实 `main()`。

    只认两种 argv：`-m novel.cli`（节点执行，含上传里的 stage0 作业）与
    `-m novel.orchestrator.delivery`（交付终验）。其余一律关闭式报错——不静默
    跳过，也不真起进程。退出码由真实 `main()` 原样返回，日志按调用方给的
    stdout（管道或文件）原样落盘。
    """

    def __init__(self, argv, *, stdout=None, stderr=None, **_kwargs):
        self.argv = [str(token) for token in argv]
        self.args = self.argv
        self.pid = -1
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                returncode = self._dispatch()
        except BaseException:                       # noqa: BLE001 —— 栈要留全
            traceback.print_exc(file=err)
            returncode = 70
        payload = (out.getvalue() + (err.getvalue() if stderr is subprocess.STDOUT
                                    else '')).encode('utf-8')
        if stdout is subprocess.PIPE:
            self.stdout = io.BytesIO(payload)
        else:
            if stdout is not None:
                stdout.write(payload)
            self.stdout = None
        self.stderr = (io.BytesIO(err.getvalue().encode('utf-8'))
                       if stderr is subprocess.PIPE else None)
        if stderr is not None and stderr not in (subprocess.PIPE, subprocess.STDOUT):
            stderr.write(err.getvalue().encode('utf-8'))
        self.returncode = int(returncode)

    def _dispatch(self) -> int:
        module_at = self.argv.index('-m')
        module, rest = self.argv[module_at + 1], self.argv[module_at + 2:]
        if module == 'novel.cli':
            return cli.main(rest)
        if module == 'novel.orchestrator.delivery':
            from novel.orchestrator import delivery
            return delivery.main(rest)
        raise AssertionError(f'测试替身不认识的 canonical 子进程 argv：{self.argv}')

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def communicate(self, input=None, timeout: float | None = None):
        assert input is None, 'CLI 测试缝不接受 stdin 材料'
        return (self.stdout.getvalue() if self.stdout is not None else None,
                self.stderr.getvalue() if self.stderr is not None else None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _profiles() -> PipelineAgentProfiles:
    return PipelineAgentProfiles(
        executor_default=LegacyExecutorBackend(profile=LegacyLlmProfile(
            provider='deepseek', model='deepseek-v4-pro',
            reasoning_effort='high', thinking_mode='on')),
        executor_overrides={})


@pytest.fixture
def base(tmp_path):
    from novel.work_units import initialize_workflow

    root = tmp_path / 'out'
    root.mkdir()
    initialize_workflow(root)
    return root


def _install_fake_transport(monkeypatch, wires) -> _EntryTransport:
    """把 fake transport 注入真实 CLI 路径：`chain.execute` 构造 PaidRequestSession
    时唯一读取的 transport 工厂就是 `novel.stages.chain.DeepSeekTransport`。"""
    transport = _EntryTransport(wires)
    monkeypatch.setattr('novel.stages.chain.DeepSeekTransport', lambda cfg: transport)
    return transport


def _install_inprocess_subprocess(monkeypatch, box: dict) -> None:
    def fake_popen(argv, **kwargs):
        process = _InProcessPopen(argv, **kwargs)
        box['processes'].append((list(argv), process.returncode))
        return process

    monkeypatch.setattr(subprocess, 'Popen', fake_popen)


def _install_step_argv(monkeypatch, box: dict) -> None:
    """节点执行缝：**原样使用生产 `build_argv`**，不再手工补任何旗标。

    F8c 修复后生产 argv 面能为直连 LLM 节点同时给出 `--yes` 与
    `--allow-egress`（`resolve_executor_egress` 与 `build_argv` 的解钩），
    因此本替身只记录派发事实，命令形状完全由生产构造——它分家就等于生产分家。
    """
    from novel.orchestrator import nodes as node_planner

    def step_argv(base, sid, epoch, permit, *, stem, **_kwargs):
        argv = node_planner.build_argv(
            permit.node, sid, list(permit.flags), base=base,
            egress_authorized=True)
        box['dispatched'].append({'node': permit.node, 'flags': list(permit.flags),
                                  'stem': stem, 'argv': list(argv)})
        return argv

    monkeypatch.setattr(agent, '_run_step_argv', step_argv)


def _ledger_rows(base) -> list[dict]:
    rows, partial = read_cost_snapshot(base)
    assert not partial, '成本账有未提交尾；先恢复再断言'
    return rows


def test_explicit_profile_reaches_transport_ledger_and_free_replay(base, monkeypatch):
    """CS-T：真实CLI/请求账/免费重放；本地默认不同也不能改变冻结选择。"""
    from novel.config import Config
    from novel.orchestrator.nodes import build_steps
    source = base.parent / FILENAME
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    assert cli.main(['run', 'stage0.remove', '--source', str(source), '--base', str(base)]) == 0
    assert cli.main(['run', 'stage1.partition', '9001', '--base', str(base)]) == 0
    monkeypatch.setattr('novel.stages.chain.load_config', lambda: Config(
        model='deepseek-v4-pro', thinking_mode='off'))
    requests = []
    transport = _EntryTransport(_full_chain_wires())

    class RecordingTransport:
        def send(self, request):
            requests.append(request)
            return transport.send(request)

    configs = []
    def factory(cfg):
        configs.append(cfg)
        return RecordingTransport()
    monkeypatch.setattr('novel.stages.chain.DeepSeekTransport', factory)
    profile = LegacyLlmProfile(provider='deepseek', model='deepseek-flash',
                               reasoning_effort='low', thinking_mode='on')
    steps = build_steps('str06.stage2.book', '9001', base=base,
                       executor_backend=LegacyExecutorBackend(profile=profile),
                       egress_authorized=True)
    for step in steps:
        assert cli.main(step.argv[step.argv.index('run'):]) == 0
    assert len(requests) == 1, '免费复检必须重放已购回答，不再调用 transport'
    assert requests[0].model == 'deepseek-flash'
    assert requests[0].reasoning_effort == 'low' and requests[0].thinking is True
    assert all(c.model == 'deepseek-flash' and c.thinking_mode == 'on' for c in configs)
    paid = [r for r in _ledger_rows(base) if not r.get('cached')]
    assert len(paid) == 1
    assert paid[0]['reasoning_effort'] == 'low' and paid[0]['thinking'] is True
    assert paid[0]['request_model'] == 'deepseek-flash'
    # 同一材料改档是独立请求身份；免费路径不能为它补购。
    other = build_steps('str06.stage2.book', '9001', ['--check-only'], base=base,
                       request_profile=profile.model_copy(update={'reasoning_effort': 'high'}))
    assert cli.main(other[0].argv[other[0].argv.index('run'):]) == 2
    assert len(requests) == 1


def _dispatched_nodes(box: dict) -> list[str]:
    return [item['node'] for item in box['dispatched']]


def _read_json(path) -> dict:
    return json.loads(path.read_text('utf-8'))


# ───────────────────── 一、真实产品入口全链到交付 ─────────────────────

def test_full_chain_from_http_upload_to_stage7_delivery(base, monkeypatch):
    # ① 最小合法基座：workflow manifest + stage0/stage1 产物（经真实 CLI 落盘）。
    source = base.parent / FILENAME
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    assert cli.main(['run', 'stage0.remove', '--source', str(source),
                     '--base', str(base)]) == 0
    assert cli.main(['run', 'stage1.partition', '9001', '--base', str(base)]) == 0

    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    transport = _install_fake_transport(monkeypatch, _full_chain_wires())
    box: dict = {'dispatched': [], 'processes': []}
    _install_inprocess_subprocess(monkeypatch, box)
    _install_step_argv(monkeypatch, box)

    # ② 真实 HTTP 上传：幂等键 + 两项授权 ⇒ 先跑 stage0（进程内）再 autostart。
    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': FILENAME, 'authorized': 'true',
                    'egress_authorized': 'true', 'idempotency_key': 'f8d-entry-9001'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['sid'] == '9001'
    assert uploaded['autostart'] == 'started'
    assert uploaded['job']['state'] == 'ok'
    assert uploaded['run']['run_id']

    # ③ Supervisor 真实规划 + 逐动作驱动：stage1 已就绪 ⇒ 按 NODE_ORDER 推进。
    run_id = uploaded['run']['run_id']
    assert agent.wait(run_id, 600), 'agent 代跑未在时限内收口'
    assert _dispatched_nodes(box) == list(NODE_ORDER)
    assert all(item['flags'] == [] for item in box['dispatched'])
    assert [item['stem'] for item in box['dispatched']] == [
        f'ready-{node}' for node in NODE_ORDER]
    # F9 实证修复（F8d 遗留③）：profiles 冻结了 legacy 默认 backend，但直连 LLM
    # 节点的 canonical argv 不挂 executor 旗标——否则 `_run_str06` 当场拒收；
    # 付费节点另带 `--yes`＋`--allow-egress`（I-43 两份授权）。免费节点的 egress
    # 由本文件的替身统一传 True（生产只对付费节点解析外发授权），不断言那一格。
    from contracts.invariants import NODES
    for item in box['dispatched']:
        assert not any(token.startswith('--executor-') for token in item['argv'])
        assert not any(token.startswith('--legacy-') for token in item['argv'])
        if NODES[item['node']].paid:
            assert '--allow-egress' in item['argv'] and '--yes' in item['argv']

    book = paths.resolve_novel_dir(base, '9001')
    # ④ 交付就绪：规划面 pipeline_complete + 成书 + 最后提交的 manifest。
    #    产品入口逐节点执行，run 级 `workflow_status.json` 落在最后一次单节点执行上；
    #    最后一个节点是交付组装，终态取交付报告（RC-54：与整链同源，不再恒记 node_run）。
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    status = _read_json(book / 'runtime' / 'workflow_status.json')
    assert status['status'] == 'delivered' and status['through'] == 'stage7'
    assert status['accepted_body'] is True
    final_path = book / paths.book_txt_rel(book)
    assert final_path.is_file()
    final_text = final_path.read_text('utf-8')
    assert '楔子' not in final_text                      # 系统不添加标题
    assert '柳婉' in final_text and '苏芸娘' not in final_text   # 改名后交付
    # 章标记由系统按阅读序添加（A4）：引子在前不加标题，其后「第N章」齐全且有序
    markers = [line for line in final_text.splitlines()
               if line.startswith('第') and line.endswith('章')]
    assert markers == ['第1章', '第2章'], markers
    assert final_text.index('第1章') < final_text.index('拜堂之夜')
    assert final_text.index('第2章') < final_text.index('夫君夜半翻墙而出')
    manifest_path = book / 'stage7' / 'final' / 'delivery_manifest.json'
    manifest = _read_json(manifest_path)
    assert manifest['final_text_sha256'] == hashlib.sha256(
        final_path.read_bytes()).hexdigest()
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    assert manifest['length_reading']['whole_book_deviation_ratio'] is not None
    # 文件先落盘、manifest 最后提交：消费者核对 manifest 才算就绪。
    assert manifest_path.stat().st_mtime_ns >= final_path.stat().st_mtime_ns
    report = _read_json(book / 'runtime' / 'delivery' / 'assembly_report.json')
    assert report['assembly']['status'] == 'ready' and report['chain_problems'] == []

    # ⑤ 章连续非空 + 章级回执绑定（章包摘要 / 改名前正文字节，D2 rc5；无前文链）。
    receipts = {}
    for ordinal in (1, 2):
        prose = (book / f'stage5/body/ch{ordinal:02d}.txt').read_text('utf-8')
        assert prose.strip(), f'第 {ordinal} 章正文为空'
        receipt = _read_json(book / f'stage5/acceptance/body/ch{ordinal:02d}.json')
        assert receipt['status'] == 'accepted'
        assert receipt['prose_sha256'] == sha256_text(prose)
        assert receipt['length_disclosed'] is False, '假正文应落在章篇幅带内'
        receipts[ordinal] = receipt
    assert len(receipts[1]['writer_view_sha256']) == 64
    assert 'prior_text_sha256' not in receipts[2]
    assert 'book_ir_sha256' not in receipts[2]
    assert receipts[1]['writer_view_sha256'] != receipts[2]['writer_view_sha256']
    for entry in manifest['chapters']:
        assert entry['prose_sha256'] == receipts[entry['chapter_ordinal']]['prose_sha256']

    # ⑥ fake transport 也走真实 reserve／记账：12 次新购（CS-3 bind 退役）、
    # 真实 token、节点归属正确。
    assert len(transport.calls) == 12
    # B1：admission 平行计数退役，整书总闸（按 attempt 计数）是唯一准入账。
    from novel.work_units import read_book_budget
    assert read_book_budget(base, '联调样本_9001').used == 12
    rows = _ledger_rows(base)
    paid = [row for row in rows if not row['cached']]
    assert len(paid) == 12
    assert all(row['prompt_tokens'] > 0 and row['completion_tokens'] > 0
               for row in paid)
    assert {row['node'] for row in paid} == {
        'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
        'str06.stage2.associate', 'str06.stage4.preserve',
        'str06.stage4.body_design', 'str06.stage4.hook_design',
        'str06.stage5.hook', 'str06.stage5.body', 'str06.stage7.naming'}
    assert all(row['sid'] == '联调样本_9001' for row in paid)
    # Supervisor 走到 pipeline_complete 才终验：STR-06 关闭批改接后终验读
    # 候选根 delivery manifest 并核对成书字节——交付完成时回答
    # ready_with_disclosures（机械披露如实保留），不把未知洗成裸 ready。
    record = next(row for row in agent.records(base) if row.run_id == run_id)
    assert record.ended is not None and record.outcome is not None
    assert record.outcome.state == 'ready_with_disclosures'
    assert 'delivery_identity' in record.outcome.reason
    assert 'unverified' in record.outcome.reason

    # ⑦ 零调用恢复：同 base 同 flags 再开一次 agent ⇒ 零派发、零新购、交付字节一致。
    calls_before = len(transport.calls)
    rows_before = list(rows)
    final_before = final_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    second = agent.start(base, '9001', authorized=True, egress_authorized=True,
                         profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert _dispatched_nodes(box) == list(NODE_ORDER), (
        '全部节点已就绪：第二次代跑不得派发任何节点')
    assert len(transport.calls) == calls_before
    assert _ledger_rows(base) == rows_before
    assert final_path.read_bytes() == final_before
    assert manifest_path.read_bytes() == manifest_before
    assert operator.next_action(base, '9001').state == 'pipeline_complete'

    # ⑧ `--check-only`：对已付费节点免费复检——零新购、不签新调用。
    for node in ('str06.stage2.book', 'str06.stage5.body'):
        assert cli.main(['run', node, '9001', '--base', str(base),
                         '--check-only']) == 0
        assert len(transport.calls) == calls_before
    rows_after = _ledger_rows(base)
    fresh_rows = rows_after[len(rows_before):]
    assert fresh_rows, '缓存命中也要记账（cached 行）'
    assert all(row['cached'] and row['prompt_tokens'] == 0
               and row['completion_tokens'] == 0 for row in fresh_rows)
    assert final_path.read_bytes() == final_before
    assert manifest_path.read_bytes() == manifest_before


# ───────────────────── 二、旧书拒读（legacy_incompatible） ─────────────────────

def test_legacy_book_is_refused_at_every_product_entry_face(base, monkeypatch, capsys):
    """旧链盘面：现役 stage0/1 + 旧 Stage5 双臂布局 + 旧 stage4 计划痕迹。

    产品入口三面都必须拒读，且**不触发任何模型调用**：旧书的盘上产物只读保留，
    不继承旧批准、不接受旧记录，也不显示成「加 --yes 就能跑」。
    """
    transport = _install_fake_transport(monkeypatch, _full_chain_wires())
    source = base.parent / LEGACY_FILENAME
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    assert cli.main(['run', 'stage0.remove', '--source', str(source),
                     '--base', str(base)]) == 0
    assert cli.main(['run', 'stage1.partition', '9002', '--base', str(base)]) == 0
    book = paths.resolve_novel_dir(base, '9002')
    (book / 'stage5' / 'deep').mkdir(parents=True, exist_ok=True)
    (book / 'stage5' / 'deep' / 'ch01.json').write_text('{}', encoding='utf-8')
    (book / 'stage4').mkdir(parents=True, exist_ok=True)
    (book / 'stage4' / 'plan_chapters.json').write_text('{}', encoding='utf-8')
    (book / '_stage4_runs').mkdir(parents=True, exist_ok=True)

    # ① argv 面：旧节点名不在 argparse choices ⇒ exit 2 + invalid choice（不是 3）。
    with pytest.raises(SystemExit) as stopped:
        cli.main(['run', 'stage5.write', '9002', '--base', str(base), '--yes'])
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert 'invalid choice' in err and 'stage5.write' in err and 'Traceback' not in err

    # ② 程序化面：`RETIRED_NODES` ⇒ legacy_incompatible 显式终态。
    with pytest.raises(TerminalState, match='legacy_incompatible'):
        cli._cmd_run(SimpleNamespace(node='stage4.plan'))

    # ③ 规划面：旧双臂层及其下游全部 legacy_incompatible 阻断，且不冻结任何付费 argv。
    plan = queue.plan(base, ['9002'], until_stage='stage7')
    blocked = {item.node: item for item in plan.blocked}
    assert blocked, '旧书必须在规划面被挡住'
    for node in ('str06.stage5.hook', 'str06.stage5.body',
                 'str06.stage7.naming', 'str06.stage7.assemble'):
        assert blocked[node].waiting_on == 'legacy_incompatible', node
        assert blocked[node].steps == [], 'blocker 不得冻结默认付费 argv'
    assert blocked['str06.stage7.assemble'].waiting_on == 'legacy_incompatible'

    # ④ 完成度面：旧书只读识别，不当成 missing、不触发静默付费重跑。
    overview = state.overview(base, '9002')
    assert overview.summary.verdict == 'legacy_incompatible'
    assert overview.stage5_legacy_paths == ['stage5/deep']

    # ⑤ 零模型调用：拒读路径一次都不碰 transport。
    assert transport.calls == []


# ───────────────────── 三、上传即授权 ─────────────────────

def test_upload_without_authorization_starts_no_paid_run(base, monkeypatch):
    """未授权上传只跑免费 stage0：不起付费 run、不留 pending autostart。

    （同形状的 HTTP 面已由 `tests/test_upload_autostart.py` 覆盖；这里补上
    「不起 agent.start」这一格在本文件入口面上的可见断言。）
    """
    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    def _ok_job() -> JobRecord:
        step = Step(argv=['python', '-m', 'novel.cli', 'stage0.remove'], cwd='.',
                    label='stage0.remove', paid=False, writes='stage0')
        return JobRecord(
            job_id='job-ok',
            start=JobStart(sid=None, node='stage0.remove', flags=[], steps=[step],
                           python='3.11', code='test', paid=False, writes=True),
            started=1, ended=2, exit_code=0, state='ok', steps_started=1, note=None)

    starts: list[str] = []
    monkeypatch.setattr('novel.api.routes.ingest.jobs.run', lambda *a, **k: _ok_job())
    monkeypatch.setattr(
        'novel.api.routes.ingest.agent.start',
        lambda *a, **k: starts.append('hit') or (_ for _ in ()).throw(
            AssertionError('未授权上传不得 agent.start')))

    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': FILENAME, 'authorized': 'false'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['sid'] == '9001'
    assert body['job']['state'] == 'ok'
    assert body.get('autostart') is None and body.get('run') is None
    assert starts == []


# ───────────────────── 四、真实 CLI 子进程全链 + 服务重启读回（B5） ─────────────────────
#
# 【B5 完成条件】「真实 HTTP 上传 → 自动编排 → CLI 子进程 → Repository → Stage7
# 交付，页面刷新与服务重启后读回一致」。与第一节的全链测试互补：那里把节点执行
# 的**子进程缝**换成进程内调用（run_step 的 claim／信标／evidence 三层不在进程内
# 复刻）；本节把这条缝**原样交还给生产**——Supervisor 起**真的**
# `novel.orchestrator.run_step` 子进程，run_step 再起**真的** `novel.cli run
# <node>` 子进程，fake transport 经 `sitecustomize`（PYTHONPATH 注入）在子进程
# 内替换 `novel.stages.chain.DeepSeekTransport`——I-51 的 fake transport 边界
# 保持零真实模型调用，付费仍走 PaidRequestSession 同一条路。
#
# 代码身份用 `real_code_version`：run_step 子进程会真实重算
# `prov.code_version()` 并与 execution token 对账，进程内替换的确定性测试身份
# 过不了这道闸（见 tests/conftest.py::_stable_code_version）。测试期间仓库无
# 写入，脏树指纹稳定。

_SITECUSTOMIZE_SOURCE = """
import os, sys

if os.environ.get('NOVEL_B5_FAKE_TRANSPORT') == '1':
    import novel.prov as _prov
    _test_code = os.environ.get('NOVEL_STR06_TEST_CODE_VERSION')
    if _test_code is not None:
        _prov.code_version = lambda: _test_code

    _argv = sys.orig_argv
    _cli = _argv.index('novel.cli') if 'novel.cli' in _argv else None
    _args = _argv[_cli + 1:] if _cli is not None else []
    if len(_args) >= 2 and _args[0] == 'run' and _args[1].startswith('str06.'):
        # 这里只安装工厂；mark/run_step 不导入 chain，免费采用不加载测试 wires。
        import novel.stages.chain as _chain

        def _entry_transport(cfg):
            from tests.str06_product_transport import _make_entry_transport
            return _make_entry_transport(os.environ.get('NOVEL_STR06_FAULT'))

        _chain.DeepSeekTransport = _entry_transport
"""


def _install_transport_site(monkeypatch, tmp_path, *, code: str | None = None,
                            fault: str | None = None) -> None:
    from pathlib import Path

    fake_site = tmp_path / 'entry_transport_site'
    fake_site.mkdir()
    (fake_site / 'sitecustomize.py').write_text(_SITECUSTOMIZE_SOURCE, encoding='utf-8')
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.setenv('NOVEL_B5_FAKE_TRANSPORT', '1')
    existing = os.environ.get('PYTHONPATH')
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(
        part for part in (str(fake_site), repo_root, existing) if part))
    if code is None:
        monkeypatch.delenv('NOVEL_STR06_TEST_CODE_VERSION', raising=False)
    else:
        # 仅非身份测试使用与 tests/conftest.py 相同的确定性身份，父子一致。
        # 完整链的 real_code_version 仍读取真实 Git，生产函数不缓存或改写。
        monkeypatch.setenv('NOVEL_STR06_TEST_CODE_VERSION', code)
    if fault is None:
        monkeypatch.delenv('NOVEL_STR06_FAULT', raising=False)
    else:
        monkeypatch.setenv('NOVEL_STR06_FAULT', fault)


def _process_node(argv) -> tuple[str | None, str | None]:
    """只识别现役 Python module argv；不把 Git 等工具送进 CLI 替身。"""
    if '-m' not in argv:
        return None, None
    at = argv.index('-m')
    module, rest = argv[at + 1], argv[at + 2:]
    if module == 'novel.orchestrator.run_step':
        return module, rest[rest.index('--execute') + 1]
    if module == 'novel.cli' and rest and rest[0] == 'run':
        return module, rest[1]
    return module, None


def _install_process_window(monkeypatch, tmp_path, *, real_nodes: set[str],
                            fault: str | None = None):
    """关键节点保留真实 run_step/CLI；其余通过现有缝执行真实 CLI 和交付判定。

    operator、permit、付费边界、validator、repository 与 HTTP 均不返回假成功。
    每例独立目录和 transport；进程事实逐例断言，不能悄悄把关键窗口降为进程内。
    """
    from novel import prov
    from novel.orchestrator import nodes as node_planner

    _install_transport_site(monkeypatch, tmp_path, code=prov.code_version(), fault=fault)
    monkeypatch.setattr('novel.stages.chain.DeepSeekTransport',
                        lambda cfg: _make_entry_transport(fault))
    native_popen, native_step = subprocess.Popen, agent._run_step_argv
    window = SimpleNamespace(real_nodes=set(real_nodes), real=[], inprocess=[])

    def step_argv(base, sid, epoch, permit, *, stem):
        if permit.node in window.real_nodes:
            return native_step(base, sid, epoch, permit, stem=stem)
        return node_planner.build_argv(permit.node, sid, list(permit.flags),
                                       base=base, egress_authorized=True)

    def popen(argv, *args, **kwargs):
        module, node = _process_node(argv)
        if module == 'novel.orchestrator.run_step' or node in window.real_nodes:
            window.real.append((module, node))
            return native_popen(argv, *args, **kwargs)
        if module in ('novel.cli', 'novel.orchestrator.delivery'):
            assert not args, '进程内 CLI 只接受具名 Popen 参数'
            window.inprocess.append((module, node))
            return _InProcessPopen(argv, **kwargs)
        return native_popen(argv, *args, **kwargs)

    monkeypatch.setattr(agent, '_run_step_argv', step_argv)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    return window


def _assert_process_window(window, expected: set[str]) -> None:
    actual = {node for _, node in window.real}
    assert actual == expected, f'关键窗口必须真实派发：{actual} != {expected}'
    assert window.inprocess, '非关键前置仍在重复执行完整子进程链'


def _run_cli_subprocess(argv, cwd: str):
    """执行 canonical CLI argv；由当前用例的窗口选择真实进程或进程内入口。"""
    proc = subprocess.run(argv, cwd=cwd, capture_output=True)
    assert proc.returncode == 0, (argv, proc.stdout, proc.stderr)
    return proc


@pytest.mark.parametrize('load_transport', (False, True), ids=('auxiliary', 'model_transport'))
def test_auxiliary_process_does_not_load_model_test_modules(tmp_path, monkeypatch,
                                                           load_transport):
    """辅助进程不加载模型脚手架；模型工厂不加载 HTTP，父子测试身份一致。"""
    from novel import prov

    _install_transport_site(monkeypatch, tmp_path, code=prov.code_version())
    forbidden = ('tests.test_str06_product_entry', 'fastapi')
    setup = ''
    if load_transport:
        setup = ('from tests.str06_product_transport import _make_entry_transport; '
                 '_make_entry_transport(); ')
    else:
        forbidden += ('novel.stages.chain', 'tests.str06_product_transport',
                      'tests.test_str06_body', 'pytest')
    result = subprocess.run([
        sys.executable, '-B', '-c',
        "import json,sys; from novel import prov; "
        + setup +
        "print(json.dumps({'code':prov.code_version(), 'heavy':any("
        f"name in sys.modules for name in {forbidden!r})}}))",
    ], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'code': prov.code_version(), 'heavy': False}


@pytest.mark.real_code_version
def test_service_full_chain_real_subprocess_and_restart_readback(base, tmp_path,
                                                                 monkeypatch):
    """HTTP 上传 → Supervisor → 真实 run_step/CLI 子进程 → Stage7 交付；
    页面刷新与服务重启（新建服务实例）后的只读读回逐字节一致。"""
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])

    _install_transport_site(monkeypatch, tmp_path)

    # ② 最小合法基座：stage0/stage1 经真实 CLI 子进程落盘（零模型调用）。
    source = base.parent / 'input_9003.txt'
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    for argv in (
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
             '--source', str(source), '--base', str(base)],
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
             '9003', '--base', str(base)]):
        proc = subprocess.run(argv, cwd=repo_root, capture_output=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    profiles = _profiles().model_copy(update={'executor_overrides': {
        'str06.stage2.names': LegacyExecutorBackend(profile=LegacyLlmProfile(
            provider='deepseek', model='deepseek-flash', reasoning_effort='low',
            thinking_mode='on'))}})
    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=profiles,
                          expected_revision=settings.revision)

    # ③ 真实 HTTP 上传（两项授权）⇒ 上传作业子进程跑 stage0 + autostart。
    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': 'input_9003.txt', 'authorized': 'true',
                    'egress_authorized': 'true',
                    'idempotency_key': 'b5-subprocess-9003'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['sid'] == '9003'
    assert uploaded['autostart'] == 'started'
    run_id = uploaded['run']['run_id']

    # ④ Supervisor 逐动作驱动真实子进程到 stage7 交付。
    assert agent.wait(run_id, 1800), 'agent 代跑未在时限内收口'
    record = next(row for row in agent.records(base) if row.run_id == run_id)
    assert record.ended is not None and record.outcome is not None

    book = paths.resolve_novel_dir(base, '9003')
    final_path = book / paths.book_txt_rel(book)
    manifest_path = book / 'stage7' / 'final' / 'delivery_manifest.json'
    assert final_path.is_file(), '真实子进程链没有交付成书'
    final_bytes = final_path.read_bytes()
    manifest = _read_json(manifest_path)
    manifest_before = manifest_path.read_bytes()
    assert manifest['final_text_sha256'] == hashlib.sha256(final_bytes).hexdigest()
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    assert '苏芸娘' not in final_bytes.decode('utf-8')
    assert '柳婉' in final_bytes.decode('utf-8')

    # ⑤ 付费账：fake transport 的每一跳都经真实 PaidRequestSession 入账
    #    （12 次新购、10 个付费节点——CS-3 bind 退役；unknown 不冒充零）。
    rows = _ledger_rows(base)
    paid = [row for row in rows
            if row['sid'] == book.name and not row['cached']]
    assert len(paid) == 12
    for row in paid:
        expected = profiles.executor_overrides.get(row['node'], profiles.executor_default).profile
        assert row['request_model'] == expected.model
        assert row['reasoning_effort'] == expected.reasoning_effort
        assert row['thinking'] == (expected.thinking_mode == 'on')
    assert {row['node'] for row in paid} == {
        'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
        'str06.stage2.associate', 'str06.stage4.preserve',
        'str06.stage4.body_design',
        'str06.stage4.hook_design', 'str06.stage5.hook', 'str06.stage5.body',
        'str06.stage7.naming'}

    # Golden 收集消费同一套正式请求账和采用证据，不把文件存在当自动完成。
    from tests.golden import capture_execution
    execution = capture_execution(SimpleNamespace(output_root=str(base), profiles=profiles), book, [run_id])
    assert execution['automation'] == 'pass', execution
    assert execution['request_profiles_current'] is True
    assert execution['interventions'] == []
    assert execution['usage']['paid_attempts'] == 12

    # ⑥ 只读监控面：run/delivery/book 三面读回，页面刷新与重启前后一致。
    candidate = book.name                     # 候选根＝书目录名（title_sid）
    def _read_faces(client):
        run_face = client.get(f'/api/str06/candidates/{candidate}/run')
        delivery_face = client.get(f'/api/str06/candidates/{candidate}/delivery')
        book_face = client.get(f'/api/str06/candidates/{candidate}/book')
        assert run_face.status_code == 200, run_face.text
        assert delivery_face.status_code == 200, delivery_face.text
        assert book_face.status_code == 200, book_face.text
        return (run_face.json(), delivery_face.json(), book_face.text)

    with TestClient(create_app(base)) as client:
        first = _read_faces(client)
        refreshed = _read_faces(client)             # 页面刷新：同一服务再读一次
    with TestClient(create_app(base)) as client:    # 服务重启：全新服务实例
        restarted = _read_faces(client)

    assert refreshed == first, '页面刷新后读回不一致'
    assert restarted == first, '服务重启后读回不一致'
    run_face, delivery_face, book_text = restarted
    assert run_face['present'] is True
    nodes = {n['node'] for n in run_face['nodes']}
    from contracts.invariants import NODES
    assert nodes == {node for node in NODES if node.startswith('str06.')},         'run 面的节点集合必须恰为现役 13 节点的 str06 全集'
    assert delivery_face['present'] is True
    assert delivery_face['final_text_sha256'] == manifest['final_text_sha256']
    assert book_text == final_bytes.decode('utf-8')

    # ⑦ 零调用恢复：全部就绪后再次 agent.start ⇒ 零派发、零新购、交付原样。
    second = agent.start(base, '9003', authorized=True, egress_authorized=True,
                         profiles=profiles)
    assert agent.wait(second.run_id, 600)
    assert final_path.read_bytes() == final_bytes
    assert manifest_path.read_bytes() == manifest_before,         '零派发的代跑不得改写交付 manifest'


# ───────────────────── 五、R3 故障注入：产品链靠修订跑完（真实子进程） ─────────────
#
# 【R5 补齐】首版 PLAN.md（Git 2fc03832）R5：fake 全链**产品路径**必须含 R3 故障注入与 R6 发布两节。
# 与第四节共用产品入口；associate 留在真实 run_step/CLI，其余节点进程内
# 跑到交付与重启读回。故障注入
# 底座（`tests/str06_faults.py`）：`missing_pairing`＋`fault_once=True`，受影响
# owner（associate）的**首响应**删掉悬念配对，验收即拦（左移：compile 干跑的
# 未决项归它自己），修订轮（`ask_json` 有界内容轮，同节点子进程内）拿到干净
# wire「改对了」。正文两章换成带内长度的稿（`FaultTransport(body_texts=…)`，
# 故障只施加 JSON 面）。语义断言：修订轮真实多一笔实付、全链照常交付、
# 重启读回一致、二次代跑零新购。


def test_faulted_product_entry_heals_via_revision_and_delivers(base, tmp_path,
                                                               monkeypatch):
    """R3×产品入口：脚本化模型错误经有界修订自愈，全链照常交付并可重启读回。"""
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])

    window = _install_process_window(
        monkeypatch, tmp_path, real_nodes={'str06.stage2.associate'}, fault='missing_pairing')

    # ① 非关键基座经进程内真实 CLI 落盘；被测 associate 留在真实进程。
    source = base.parent / 'input_9004.txt'
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    for argv in (
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
             '--source', str(source), '--base', str(base)],
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
             '9004', '--base', str(base)]):
        _run_cli_subprocess(argv, repo_root)

    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    # ② 真实 HTTP 上传（两项授权）⇒ autostart 起 Supervisor。
    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': 'input_9004.txt', 'authorized': 'true',
                    'egress_authorized': 'true',
                    'idempotency_key': 'r5-fault-9004'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['sid'] == '9004' and uploaded['autostart'] == 'started'
    run_id = uploaded['run']['run_id']

    # ③ associate 的真实 run_step/CLI 完成修订；其余节点用真实 CLI 到交付。
    assert agent.wait(run_id, 1800), '故障注入链未在时限内靠修订收口'
    book = paths.resolve_novel_dir(base, '9004')
    final_path = book / paths.book_txt_rel(book)
    manifest_path = book / 'stage7/final/delivery_manifest.json'
    assert final_path.is_file(), '修订自愈后的链没有交付成书'
    final_bytes = final_path.read_bytes()
    manifest = _read_json(manifest_path)
    assert manifest['final_text_sha256'] == hashlib.sha256(
        final_bytes).hexdigest()
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    final_text = final_bytes.decode('utf-8')
    assert '柳婉' in final_text and '苏芸娘' not in final_text

    # ④ 修订轮真实发生并如实计费：12 笔干净外发＋1 笔 associate 修订轮实付
    #    （CS-3 bind 退役）；整书总闸按 attempt 合账（I-17/I-43）。
    rows = _ledger_rows(base)
    paid = [row for row in rows
            if row['sid'] == book.name and not row['cached']]
    assert len(paid) == 13, '受影响 owner 应恰好多一笔修订轮实付'
    associate_rows = [row for row in paid
                      if row['node'] == 'str06.stage2.associate']
    assert len(associate_rows) == 2, 'associate 一原轮＋一修订轮'
    from novel.work_units import read_book_budget
    assert read_book_budget(base, book.name).used == 13

    # ⑤ 重启读回：全新服务实例三面读回一致，成书与交付摘要互相咬合。
    candidate = book.name
    with TestClient(create_app(base)) as client:
        run_face = client.get(f'/api/str06/candidates/{candidate}/run')
        delivery_face = client.get(
            f'/api/str06/candidates/{candidate}/delivery')
        book_face = client.get(f'/api/str06/candidates/{candidate}/book')
        assert run_face.status_code == 200, run_face.text
        assert delivery_face.status_code == 200, delivery_face.text
        assert book_face.status_code == 200, book_face.text
        assert delivery_face.json()['final_text_sha256'] == \
            manifest['final_text_sha256']
        assert book_face.text == final_text

    # ⑥ 全部就绪后再次代跑：零新购、交付字节不变。
    rows_before = list(rows)
    second = agent.start(base, '9004', authorized=True,
                         egress_authorized=True, profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert final_path.read_bytes() == final_bytes
    assert _ledger_rows(base) == rows_before, '二次代跑不得新购'
    _assert_process_window(window, {'str06.stage2.associate'})


# ───────────────────── 六、R6 发布权威：封存丢失拒绝正式消费，免费恢复重封存 ───────
#
# 【R5 补齐】R6 的 seal 语义此前只在链层验证（`tests/test_str06_runtime_records.py`）；
# 本节把它钉在产品路径上：干净全链交付后抹掉全部 CommitBundle（封存窗口崩溃
# 形态），单跑组装节点必须显式终态拒读（`UnsealedArtifact`，exit 2——不是照旧
# ready）；恢复走**产品节点执行的同一 canonical argv** 按执行序重跑——付费段按
# 请求身份缓存重放、免费段确定性重编译，零新购、交付字节不变、重新封存；恢复
# 后单跑组装重新放行，服务重启读回与丢失前一致。

def test_seal_loss_refuses_formal_consume_then_free_recovery_reseals(
        base, tmp_path, monkeypatch):
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])

    window = _install_process_window(
        monkeypatch, tmp_path, real_nodes={'str06.stage7.assemble'})

    # ② 进程内真实 CLI 准备完整交付；正式组装仍由真实子进程执行。
    source = base.parent / 'input_9005.txt'
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    for argv in (
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
             '--source', str(source), '--base', str(base)],
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
             '9005', '--base', str(base)]):
        _run_cli_subprocess(argv, repo_root)

    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': 'input_9005.txt', 'authorized': 'true',
                    'egress_authorized': 'true',
                    'idempotency_key': 'r5-seal-9005'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['sid'] == '9005' and uploaded['autostart'] == 'started'
    run_id = uploaded['run']['run_id']
    assert agent.wait(run_id, 1800), 'agent 代跑未在时限内收口'

    book = paths.resolve_novel_dir(base, '9005')
    final_path = book / paths.book_txt_rel(book)
    manifest_path = book / 'stage7/final/delivery_manifest.json'
    assert final_path.is_file(), '干净链没有交付成书'
    final_bytes = final_path.read_bytes()
    manifest = _read_json(manifest_path)
    assert manifest['final_text_sha256'] == hashlib.sha256(
        final_bytes).hexdigest()

    # ③ 封存窗口崩溃：抹掉全部 CommitBundle（产物字节都在、发布证明全失）。
    bundles = list((book / 'runtime' / 'commit').rglob('*.json'))
    assert bundles, '正式提交必须先有封存记录'
    for bundle in bundles:
        bundle.unlink()

    # ④ 正式消费拒绝：单跑组装（生产 canonical argv、真实子进程）⇒ 显式终态
    #    exit 2＋「未封存」——统一发布权威面前没有 seal 就不是正式输入。
    from novel.orchestrator import nodes as node_planner
    assemble_argv = node_planner.build_argv(
        'str06.stage7.assemble', '9005', [], base=base, egress_authorized=True)
    refused = subprocess.run(assemble_argv, cwd=repo_root, capture_output=True)
    assert refused.returncode == 2, (refused.stdout, refused.stderr)
    assert '未封存' in (refused.stdout + refused.stderr).decode('utf-8')

    # ⑤ 免费恢复：按执行序重跑全部节点（同一 canonical argv 构造点）——
    #    付费段缓存重放、免费段确定性重编译，零新购、交付字节不变、重新封存。
    rows_before = _ledger_rows(base)
    window.real_nodes.update(NODE_ORDER)
    for node in NODE_ORDER:
        argv = node_planner.build_argv(
            node, '9005', [], base=base, egress_authorized=True)
        _run_cli_subprocess(argv, repo_root)
    fresh_rows = _ledger_rows(base)[len(rows_before):]
    assert not [row for row in fresh_rows if not row['cached']], \
        '恢复重跑不得产生任何新购（缓存重放行须全为 cached）'
    assert final_path.read_bytes() == final_bytes, '恢复不得改写交付字节'
    resealed = list((book / 'runtime' / 'commit').rglob('*.json'))
    assert len(resealed) >= 12, '重跑必须把全部重写产物重新封存'

    # ⑥ 恢复后单跑组装重新放行；交付摘要与字节仍与丢失前一致。
    _run_cli_subprocess(assemble_argv, repo_root)
    manifest_after = _read_json(manifest_path)
    assert manifest_after['final_text_sha256'] == manifest['final_text_sha256']
    assert final_path.read_bytes() == final_bytes

    # ⑦ 服务重启读回与丢失前一致；零派发代跑不改交付。
    candidate = book.name
    with TestClient(create_app(base)) as client:
        delivery_face = client.get(
            f'/api/str06/candidates/{candidate}/delivery')
        book_face = client.get(f'/api/str06/candidates/{candidate}/book')
        assert delivery_face.status_code == 200, delivery_face.text
        assert book_face.status_code == 200, book_face.text
        assert delivery_face.json()['final_text_sha256'] == \
            manifest['final_text_sha256']
        assert book_face.text == final_bytes.decode('utf-8')
    second = agent.start(base, '9005', authorized=True,
                         egress_authorized=True, profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert final_path.read_bytes() == final_bytes
    _assert_process_window(window, set(NODE_ORDER))


# ───────────────────── 七、R5b：HTTP 起跑 → CLI canary → HTTP 续跑（B6-A 前置链） ─────
#
# 【R5b 完成条件】首版 PLAN.md（Git 2fc03832）R5b W4：AgentRun 跑语义层 → 停在正文前 → CLI 写 canary
# 章 → AgentRun 续跑 → 有效提交集合恢复 → 只购买剩余正文。fake transport 经
# sitecustomize 注入正文关键窗口的真实子进程；HTTP 面（上传／起跑／
# 续跑）与 CLI 节点执行（canary）都走产品代码同一行（I-51：零真实模型调用）。
#
# ⚠ 与计划稿的一处如实修正：`body_access`（章就绪五态）只在
# `str06.stage7.assemble` 写过 `runtime/delivery/assembly_report.json` 之后才
# 可读，而 assemble 需要 name_map——canary 时刻 naming 尚未跑，监控面上没有
# body_access。故 canary 时刻断言盘面事实（只落 ch02）＋`state`（正文未完成）
# ＋逐章留证（requested/attempted/chapters）；`body_access` 与 canary 章字节
# 不变的断言放在续跑交付后（那时 accepted 应为全书两章）。
#
# ⚠ 「续跑」在产品里是**新 run**（continuation 指向原 run），不是恢复同一个
# run 记录——本节据此断言两个 run_id 不同。

def test_agent_run_pauses_before_body_cli_canary_then_continuation_delivers(
        base, tmp_path, monkeypatch):
    """B6-A 前置链：语义层自动跑 → CLI 选章 canary → 续跑只补剩余正文 → 交付。"""
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])

    window = _install_process_window(
        monkeypatch, tmp_path, real_nodes={'str06.stage5.body'})

    # ② 未经授权的 HTTP 上传：只跑免费 stage0（不起付费 run、不留 autostart）；
    #    stage1 经进程内真实 CLI 补齐（免费确定性节点），备好语义层的正式输入。
    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload', params={'filename': 'input_9006.txt'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['sid'] == '9006'
    assert uploaded.get('autostart') is None and uploaded.get('run') is None, \
        '未授权上传不得起付费 run'
    assert uploaded['job']['state'] == 'ok'
    _run_cli_subprocess(
        [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
         '9006', '--base', str(base)], repo_root)

    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    # ③ 经 HTTP 起 AgentRun：两项授权＋fake profiles＋正文前暂停。
    with TestClient(create_app(base)) as client:
        started = client.post('/api/agent/runs', json={
            'sid': '9006', 'authorized': True, 'egress_authorized': True,
            'profiles': json.loads(_profiles().model_dump_json()),
            'pause_before_node': 'str06.stage5.body'},
            headers={UI_HEADER: '1'})
    assert started.status_code == 202, started.text
    run_id = started.json()['run_id']
    assert agent.wait(run_id, 1800), '语义层代跑未在时限内停在正文前'

    book = paths.resolve_novel_dir(base, '9006')
    # Supervisor 跑完 Stage2、Stage4 与 Hook 后停在正文前：盘上有语义层与 Hook
    # 产物，没有任何正文交付痕迹。
    for rel in ('stage2/characters/characters.json',
                'stage2/blueprints/chapter_blueprints.json',
                'stage4/ir/writer_book_ir.json', 'stage5/hook/hook.txt'):
        assert (book / rel).is_file(), rel
    assert not (book / 'stage5/body').exists(), '停在正文前不得落任何正文'
    assert not (book / 'stage7/naming/name_map.json').exists()
    rows = _ledger_rows(base)
    paid = [row for row in rows if row['sid'] == book.name and not row['cached']]
    from contracts.invariants import NODES
    before_body = list(NODE_ORDER)[:list(NODE_ORDER).index('str06.stage5.body')]
    assert {row['node'] for row in paid} == {
        node for node in before_body if NODES[node].paid}, (
        '应恰好是正文之前跑过的付费节点（两个 compile 免费、不入账）')
    assert len(paid) == 9, '语义层含 ch01/ch02 两次章观察共 9 次外发（bind 退役）'
    assert not any(row['purpose'].startswith('body_') for row in rows), \
        '正文一行都没有'
    record = next(row for row in agent.records(base) if row.run_id == run_id)
    assert record.ended is not None and record.outcome is not None
    assert record.outcome.state == 'stopped'
    assert 'str06.stage5.body' in (record.outcome.reason or ''), \
        '停止原因必须点明范围边界，不冒充 pipeline_complete'

    # ④ CLI canary：真实子进程执行选章正文（`--body-chapters 2`，fake 书第 2 章
    #    是非前缀子集——前缀式 canary 在 2 章书上与整书无异，测不出集合恢复）。
    canary_argv = [sys.executable, '-B', '-m', 'novel.cli', 'run',
                   'str06.stage5.body', '9006', '--base', str(base),
                   '--yes', '--allow-egress', '--body-chapters', '2']
    _run_cli_subprocess(canary_argv, repo_root)
    # 只落 ch02 的正文与回执；ch01 未被触碰（选章不静默补全）。
    assert (book / 'stage5/body/ch02.txt').read_text('utf-8') == GOOD_CH02
    assert _read_json(book / 'stage5/acceptance/body/ch02.json')['status'] == 'accepted'
    assert not (book / 'stage5/body/ch01.txt').exists()
    assert not (book / 'stage5/acceptance/body/ch01.json').exists()
    canary_receipt = (book / 'stage5/acceptance/body/ch02.json').read_bytes()
    canary_prose = (book / 'stage5/body/ch02.txt').read_bytes()
    # 逐章留证：「最近一次完成执行」＝请求且只执行了选中章，无失败。
    evidence = _read_json(book / 'runtime/body_failures.json')
    assert evidence['requested'] == [2] and evidence['attempted'] == [2]
    assert evidence['chapters'] == {}
    # 选章只作调用元数据：run 级状态记审计字段，不进任何身份。
    status = _read_json(book / 'runtime/workflow_status.json')
    assert status['body_chapters'] == [2]
    # state 判正文未完成：stage5／stage7 都未就绪，成书未落盘。
    state_proc = subprocess.run(
        [sys.executable, '-B', '-m', 'novel.cli', 'state', '9006',
         '--base', str(base)], cwd=repo_root, capture_output=True)
    assert state_proc.returncode == 0, state_proc.stdout + state_proc.stderr
    state_out = state_proc.stdout.decode('utf-8')
    assert '✓ stage5' not in state_out and '✓ stage7' not in state_out
    assert not (book / 'stage7' / 'final').exists(), '正文未完成不得有交付目录'

    # ⑤ HTTP 续跑：指向原 run 的新 run（continuation），显式解除范围上界。
    rows_before = _ledger_rows(base)
    with TestClient(create_app(base)) as client:
        cont = client.get(f'/api/agent/runs/{run_id}/continuation')
        assert cont.status_code == 200, cont.text
        source = cont.json()
        assert source['available'] is True, source
        assert source['pause_before_node'] == 'str06.stage5.body'
        resumed = client.post('/api/agent/runs', json={
            'sid': '9006', 'authorized': True, 'egress_authorized': True,
            'profiles': json.loads(_profiles().model_dump_json()),
            'continuation': source['source'], 'pause_before_node': None},
            headers={UI_HEADER: '1'})
    assert resumed.status_code == 202, resumed.text
    continued_run = resumed.json()['run_id']
    assert continued_run != run_id, '续跑是新 run，不是恢复同一个 run 记录'
    assert agent.wait(continued_run, 1800), '续跑未在时限内收口'

    # 新增 ledger 行恰好是 body_ch01 一行加 naming 一行：其余付费节点（book、
    # 章观察、names、associate、四个 Stage4 节点、hook）与 canary 的 body_ch02
    # 零新行（已提交章不进待补集合，不重购）。
    fresh = [row for row in _ledger_rows(base)[len(rows_before):]
             if row['sid'] == book.name]
    fresh_paid = [row for row in fresh if not row['cached']]
    assert sorted(row['purpose'] for row in fresh_paid) == ['body_ch01', 'naming'], \
        fresh_paid
    assert sorted({row['node'] for row in fresh_paid}) == [
        'str06.stage5.body', 'str06.stage7.naming']
    # canary 章的回执与正文字节在整跑后不变（已提交章的采用与恢复身份稳定）。
    assert (book / 'stage5/acceptance/body/ch02.json').read_bytes() == canary_receipt
    assert (book / 'stage5/body/ch02.txt').read_bytes() == canary_prose

    # 交付就绪：成书按阅读序带两章、改名后无旧名残留。
    final_path = book / paths.book_txt_rel(book)
    assert final_path.is_file(), '续跑没有交付成书'
    final_bytes = final_path.read_bytes()
    final_text = final_bytes.decode('utf-8')
    markers = [line for line in final_text.splitlines()
               if line.startswith('第') and line.endswith('章')]
    assert markers == ['第1章', '第2章'], markers
    assert '柳婉' in final_text and '苏芸娘' not in final_text
    manifest = _read_json(book / 'stage7/final/delivery_manifest.json')
    assert manifest['final_text_sha256'] == hashlib.sha256(final_bytes).hexdigest()
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    # 章就绪五态（R5b 修正点）：全书两章都有效，没有 missing/stale。
    report = _read_json(book / 'runtime/delivery/assembly_report.json')
    assert report['body_access']['accepted'] == [1, 2]
    assert report['body_access']['missing'] == [] and report['body_access']['stale'] == []
    assert report['assembly']['status'] == 'ready' and report['chain_problems'] == []

    # ⑥ 服务重启读回：三面与盘面逐字一致（沿用第四节的读回形状）。
    candidate = book.name

    def _read_faces():
        run_face = client.get(f'/api/str06/candidates/{candidate}/run')
        delivery_face = client.get(f'/api/str06/candidates/{candidate}/delivery')
        book_face = client.get(f'/api/str06/candidates/{candidate}/book')
        assert run_face.status_code == 200, run_face.text
        assert delivery_face.status_code == 200, delivery_face.text
        assert book_face.status_code == 200, book_face.text
        return (run_face.json(), delivery_face.json(), book_face.text)

    with TestClient(create_app(base)) as client:
        first = _read_faces()
        assert first[0]['present'] is True and first[1]['present'] is True
        assert first[1]['final_text_sha256'] == manifest['final_text_sha256']
        assert first[2] == final_text
    with TestClient(create_app(base)) as client:      # 重启：全新服务实例
        restarted = _read_faces()
    assert restarted == first, '服务重启后读回不一致'
    # 监控面的逐章留证与正文裁决：合法空清单＋全书 accepted。
    run_view = first[0]
    assert run_view['body_failures']['chapters'] == []
    assert run_view['body_failures']['requested'] == [1, 2]
    assert run_view['assembly_report']['body_access']['accepted'] == [1, 2]
    assert operator.next_action(base, '9006').state == 'pipeline_complete'

    # ⑦ 全部就绪后再次代跑：零派发、零新购、交付字节不变。
    rows_final = _ledger_rows(base)
    second = agent.start(base, '9006', authorized=True,
                         egress_authorized=True, profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert _ledger_rows(base) == rows_final, '二次代跑不得新购'
    assert final_path.read_bytes() == final_bytes
    _assert_process_window(window, {'str06.stage5.body'})


# ─────────────────── 七、CS-3 验收：承载闸在单节点子进程内修订自愈 ───────────
#
# 【为什么单独一条】obligation_unbound 的拦截点随 bind 退役前移——real 覆盖闸
# 在 body_design 的 ask 同节点修订轮内（不再跨到 compile 的 must_keep_unbound
# 回派）。supervisor 每节点一个子进程：fault_once 需跨子进程粘滞（状态文件），
# 首答吃到坏 wire（real 清空）、修订轮拿到干净稿，一轮治好；下游（hook_design
# 等）按产物新鲜度照常推进交付。req_ 源盲定位与修订轮计费面保持验收。

@pytest.mark.parametrize('design_fault', ['obligation_unbound', 'knowledge_release'])
def test_obligation_unbound_replans_across_single_node_subprocesses(base,
                                                                    tmp_path,
                                                                    monkeypatch, design_fault):
    """CS-3×产品入口：design 首答漏标 real → 覆盖闸同节点修订轮自愈 →
    双产物（body_design＋semantic_binding）发布 → 下游照常交付；无跨节点
    回派、无重复购买。"""
    from pathlib import Path

    from novel.orchestrator import agent
    from novel.orchestrator import agent_settings

    repo_root = str(Path(__file__).resolve().parents[1])

    window = _install_process_window(
        monkeypatch, tmp_path,
        real_nodes={'str06.stage4.body_design', 'str06.stage4.hook_design',
                    'str06.stage4.compile'},
        fault=design_fault)

    # supervisor 每节点一个子进程——fault_once 需跨子进程粘滞（状态文件），
    # 否则 body_design 子进程重跑时首答又吃到故障 wire，一轮修订永远治不好。
    monkeypatch.setenv('NOVEL_STR06_FAULT_ONCE_STICKY',
                       str(tmp_path / 'fault_once_served.json'))

    source = base.parent / 'input_9005.txt'
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    for argv in (
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
             '--source', str(source), '--base', str(base)],
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
             '9005', '--base', str(base)]):
        _run_cli_subprocess(argv, repo_root)
    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': 'input_9005.txt', 'authorized': 'true',
                    'egress_authorized': 'true',
                    'idempotency_key': 'r8a-replan-9005'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded['sid'] == '9005' and uploaded['autostart'] == 'started'
    run_id = uploaded['run']['run_id']
    assert agent.wait(run_id, 1800), '跨节点回派链未在时限内经重规划收口'

    book = paths.resolve_novel_dir(base, '9005')
    assert (book / paths.book_txt_rel(book)).is_file(), '回派重规划后没有交付成书'

    # ① 同节点修订轮自愈：无回派负结论、无 exit 70 崩溃；子进程日志干净。
    from novel.orchestrator.agent import run_log_path
    log_bytes = run_log_path(base, run_id).read_bytes().decode('utf-8', 'replace')
    assert '未捕获异常（exit 70' not in log_bytes

    # ② 承载闸在 body_design 内自愈：无跨节点回派账（issue 记录不产生）；
    #    双产物发布（semantic_binding 的唯一 writer 是 body_design）。
    from novel.stages.owner_revision import read_owner_issue_file
    from novel.store import Str06Repository
    assert read_owner_issue_file(Str06Repository(book), 'design') is None
    assert (book / 'stage4/grounding/semantic_binding.json').is_file()
    assert (book / 'stage4/compiled/execution_plan.json').is_file()

    # ③ 修订提示真实发生：design 的第二轮请求。CS-I输入按ob短名寻址，不再发送派生
    #    req ID；接受闸的问题定位和源盲边界仍须成立。
    from novel.stages.stage4 import obligation_core_id
    revision_payload = _turn_request_payload(base, book, 'str06.stage4.body_design',
                                             ordinal=2)
    messages = revision_payload['messages']
    revision_user = messages[-1]['content']
    assert obligation_core_id('ob01') not in revision_user
    assert 'stage4-design-input-v1' in revision_user and 'ob01' in revision_user
    if design_fault == 'obligation_unbound':
        # 必保义务覆盖闸是全局判据，没有可局部定位的 wire 路径 → 整份修订【修订要求】。
        # CS-3：obNN 是义务清单的正式短名（real 标注的闭集词汇，D6）；问题面确为
        # real 承载缺失。
        assert '【修订要求】' in revision_user
        requirement_only = revision_user.split('【修订要求】', 1)[1].split('上一版全文', 1)[0]
        assert 'real' in requirement_only and '必保义务' in requirement_only
    else:
        # Design 自有文本的名称冲突在判定处带 wire 路径 → 只把出问题的拍发回条目返修
        # （RC-47）；合并后仍重跑完整预检。反馈保持源盲，没有跨节点回派账。
        assert '【条目返修】' in revision_user and '【修订要求】' not in revision_user
        item_section = revision_user.split('【条目返修】', 1)[1]
        assert '路径 scenes[0].beats[0]' in item_section and '陆大哥' in item_section
        assert 'b01_01.c1' not in item_section, 'Design反馈保持源盲'
        assert read_owner_issue_file(Str06Repository(book), 'preserve') is None

    # ④ 付费账：design 一原轮＋一修订轮；bind 不存在（退役）；下游照常。
    rows = _ledger_rows(base)
    paid = [row for row in rows
            if row['sid'] == book.name and not row['cached']]
    from collections import Counter
    by_purpose = Counter(row['purpose'] for row in paid)
    assert by_purpose['design'] == 2, 'design 一原轮＋一修订轮'
    assert 'bind' not in by_purpose, 'bind 已退役，不得出现 bind 实付行'
    from novel.work_units import read_book_budget
    assert read_book_budget(base, book.name).used == len(paid)

    # ⑤ 二次代跑：零新购（两步间崩溃重启的免费恢复同构：按请求身份重放）。
    rows_before = list(rows)
    second = agent.start(base, '9005', authorized=True,
                         egress_authorized=True, profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert _ledger_rows(base) == rows_before, '二次代跑不得新购'
    _assert_process_window(window, {
        'str06.stage4.body_design', 'str06.stage4.hook_design',
        'str06.stage4.compile'})


def test_registered_owner_patch_survives_file_removal_then_agent_delivers(base, tmp_path, monkeypatch):
    """R9：真实 CLI 采用耐久补丁 → AgentRun 确定性级联 → 交付 → 二次零新购。"""
    from pathlib import Path
    from copy import deepcopy
    from novel.config import load_config
    from novel.llm.paid_requests import digest, purchased_baseline
    from novel.stages.revisions import revision_states_at

    repo_root = str(Path(__file__).resolve().parents[1])

    window = _install_process_window(
        monkeypatch, tmp_path,
        real_nodes={'str06.stage4.body_design', 'str06.stage4.hook_design',
                    'str06.stage4.compile'})

    source = base.parent / 'input_9007.txt'
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    _run_cli_subprocess([sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
                        '--source', str(source), '--base', str(base)], repo_root)
    _run_cli_subprocess([sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
                        '9007', '--base', str(base)], repo_root)
    for node in NODE_ORDER:
        _run_cli_subprocess([sys.executable, '-B', '-m', 'novel.cli', 'run', node,
                            '9007', '--base', str(base), '--yes', '--allow-egress'], repo_root)
        if node == 'str06.stage4.compile':
            break
    book = paths.resolve_novel_dir(base, '9007')
    payload = _turn_request_payload(base, book, 'str06.stage4.body_design', ordinal=1)
    identity = digest({'owner': 'design', 'provider': load_config().base_url, 'request': payload})
    baseline = purchased_baseline(book, 'design', identity)
    candidate = deepcopy(json.loads(baseline['content']))
    candidate['book_style'] += '对白克制，句式简明。'
    patch_dir = tmp_path / 'outside_patch'
    patch_dir.mkdir()
    raw = json.dumps(candidate, ensure_ascii=False, indent=2).encode('utf-8') + b'\r\n'
    (patch_dir / 'design_wire.json').write_bytes(raw)
    (patch_dir / 'design_patch_note.json').write_text(json.dumps({
        'base_response_identity': identity, 'reason': '修正实际送达的全书表达要求'}, ensure_ascii=False), encoding='utf-8')
    paid_before_patch = len([r for r in read_cost_snapshot(base)[0] if not r.get('cached')])
    _run_cli_subprocess([sys.executable, '-B', '-m', 'novel.cli', 'run', 'str06.stage4.body_design',
                        '9007', '--base', str(base), '--wire-dir', str(patch_dir),
                        '--yes', '--allow-egress'], repo_root)
    assert len([r for r in read_cost_snapshot(base)[0] if not r.get('cached')]) == paid_before_patch
    record = _read_json(book / 'runtime/revisions/active/design.json')
    assert record['status'] == 'open'
    assert (book / record['evidence_rel']).read_bytes() == raw
    assert revision_states_at(book)
    # 同一已购身份的第二份显式补丁须接续首份，而非因为旧采用摘要不同就拒绝。
    first_record = record
    first_raw = raw
    candidate['book_style'] += '保留叙述者的内外反差。'
    raw = json.dumps(candidate, ensure_ascii=False, indent=2).encode('utf-8') + b'\r\n'
    (patch_dir / 'design_wire.json').write_bytes(raw)
    _run_cli_subprocess([sys.executable, '-B', '-m', 'novel.cli', 'run', 'str06.stage4.body_design',
                        '9007', '--base', str(base), '--wire-dir', str(patch_dir),
                        '--yes'], repo_root)
    assert len([r for r in read_cost_snapshot(base)[0] if not r.get('cached')]) == paid_before_patch
    record = _read_json(book / 'runtime/revisions/active/design.json')
    assert record['status'] == 'open' and (book / record['evidence_rel']).read_bytes() == raw
    assert (book / first_record['evidence_rel']).read_bytes() == first_raw
    assert len(list((book / 'runtime/revisions/history/design').glob('*.json'))) == 1
    (patch_dir / 'design_wire.json').unlink()
    (patch_dir / 'design_patch_note.json').unlink()
    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(), expected_revision=settings.revision)
    continued = agent.start(base, '9007', authorized=True, egress_authorized=True, profiles=_profiles())
    assert agent.wait(continued.run_id, 900)
    run = next(r for r in agent.records(base) if r.run_id == continued.run_id)
    assert run.outcome is not None, run
    assert (book / paths.book_txt_rel(book)).is_file(), run.outcome
    assert _read_json(book / 'runtime/revisions/active/design.json')['status'] == 'resolved'
    before = _ledger_rows(base)
    second = agent.start(base, '9007', authorized=True, egress_authorized=True, profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert _ledger_rows(base) == before
    _assert_process_window(window, {'str06.stage4.body_design',
                                     'str06.stage4.hook_design',
                                     'str06.stage4.compile'})


def _turn_request_payload(base, book, node: str, *, ordinal: int) -> dict:
    """某节点工作单元第 N 内容轮的请求工作包 blob（reserve 时冻结的 prompt 面）。"""
    from contracts.schema.workflow import WorkUnitBlobV1
    from novel.work_units import WorkUnitBudget

    unit = WorkUnitBudget(base, book.name, node, 'executor', 'book')
    turns = [t for t in unit._read().turns if t.ordinal == ordinal]
    assert len(turns) == 1, f'第 {ordinal} 轮不唯一'
    blob = WorkUnitBlobV1.model_validate(json.loads(
        (unit.directory / f'{turns[0].request_payload_sha256}.json')
        .read_text(encoding='utf-8')))
    return blob.payload


def test_carried_forward_reruns_the_real_cli_node_with_zero_new_design_calls(
        base, tmp_path, monkeypatch):
    """CS-H H1b×产品入口：上游修订后重跑 design 单节点（真实 CLI＋付费边界），
    已购候选零调用沿用——不重复购买、不重复发布，只多一行零 token cached 行。"""
    from pathlib import Path

    from novel.store import Str06Repository

    repo_root = str(Path(__file__).resolve().parents[1])
    window = _install_process_window(
        monkeypatch, tmp_path, real_nodes={'str06.stage4.body_design'})

    source = base.parent / 'input_9006.txt'
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    for argv in (
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage0.remove',
             '--source', str(source), '--base', str(base)],
            [sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage1.partition',
             '9006', '--base', str(base)]):
        _run_cli_subprocess(argv, repo_root)
    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
                          expected_revision=settings.revision)

    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/upload',
            params={'filename': 'input_9006.txt', 'authorized': 'true',
                    'egress_authorized': 'true',
                    'idempotency_key': 'csh-h1b-9006'},
            content=SOURCE_TEXT.encode('utf-8'),
            headers={UI_HEADER: '1', 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    run_id = response.json()['run']['run_id']
    assert agent.wait(run_id, 1800), '干净链未在时限内交付'

    book = paths.resolve_novel_dir(base, '9006')
    design_rel = 'stage4/design/body_design.json'
    before_design = Str06Repository(book).read_model(design_rel)
    rows_before = _ledger_rows(base)
    paid_before = [row for row in rows_before if not row['cached']]
    design_paid_before = [row for row in paid_before
                          if row['purpose'] == 'design']
    assert len(design_paid_before) == 1, '干净链只买过一份 design'

    # 上游新版本：修订对照 wire 条目不变（只改 lineage 派生）⇒ 请求身份变。
    from novel.llm.ledger import CostLedger, ledger_path
    from tests.str06_facilities import make_context, republish_checkpoint
    from tests.test_str06_candidate_preflight import _lineage_only_mutation

    repo = Str06Repository(book)
    repo.attach_context(make_context(book, ledger=CostLedger(path=ledger_path(base))))
    republish_checkpoint(repo, _lineage_only_mutation)

    # 重跑 design 单节点（生产 canonical argv：付费旗标＋真实进程窗口）。
    from novel.orchestrator import nodes as node_planner
    argv = node_planner.build_argv('str06.stage4.body_design', '9006', [],
                                   base=base, egress_authorized=True)
    _run_cli_subprocess(argv, repo_root)
    _assert_process_window(window, {'str06.stage4.body_design'})

    assert Str06Repository(book).read_model(design_rel) == before_design, \
        '采用同一候选：产物内容不变（来源账如实记新上游指纹）'
    fresh = _ledger_rows(base)[len(rows_before):]
    assert fresh and all(row['cached'] and row['prompt_tokens'] == 0
                         and row['completion_tokens'] == 0 for row in fresh), \
        f'只允许零 token 缓存行：{[row.get("cache_policy") for row in fresh]}'
    assert any(row.get('cache_policy') == 'carried-forward' for row in fresh)
    assert len([row for row in _ledger_rows(base)
                if not row['cached'] and row['purpose'] == 'design']) == 1, \
        'design 不重复购买'
    carried = list((book / 'runtime' / 'revisions' / 'carried' / 'design').glob('*.json'))
    assert len(carried) == 1
    record = _read_json(carried[0])
    assert record['kind'] == 'carried_forward' and record['schema_version'] == 2
    assert record['baseline_request_sha256'] == design_paid_before[0]['request_fingerprint']
