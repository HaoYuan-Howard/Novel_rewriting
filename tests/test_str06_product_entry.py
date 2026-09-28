#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_product_entry —— F8d：真实产品入口全链集成（fake transport，零真实 LLM）。

【验什么】把「HTTP 上传 → autostart → `agent.start` → Supervisor 按 operator next
逐动作驱动 → 11 个 `str06.` 节点 → stage7 交付」用 fake transport 真跑一遍，并核对
零调用恢复、`--check-only`、旧书拒读与最终交付。规划面（`operator.next`／
`queue.plan`）**不打桩**：节点顺序、贵贱、写盘与恢复判定全部由现役 `NODES` 派生。
fake transport 复用 `tests/test_str06_body.py` 的路由表（同一份 wires），正文按章
给出落在章篇幅带内的稿——真实调用走 `PaidRequestSession` 的 reserve／记账／缓存
同一条路（I-51：不依赖真实线上 LLM）。

【基座形状】最小合法 base = `initialize_workflow` + stage0/stage1 产物（经真实
`novel.cli run` 落盘）；上传触发 autostart 后 Supervisor 的第一个 ready 动作就是
`str06.stage2.source`——stage1 章数组是新链的正式输入，预置它正是任务口径的
「最小合法 base」。上传自身仍会真跑一次 stage0 作业（同一份源字节，确定性重放）。

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
而 `_run_str06` 拒收（真实接缝，F9 产品跑书时实证），F9 已在生产
`nodes.build_argv` 修复：直连 LLM 节点（`str06.`）的 canonical argv 不挂
`--executor-backend/--legacy-*`（`DIRECT_LLM_NODE_PREFIX`），冻结 profile 只落
Step／run 契约记录面；本文件的派发断言据此钉住「无 executor 旗标」。两处替身的
影响面：

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
③ 产品入口逐节点执行 ⇒ run 级 `runtime/workflow_status.json` 落在最后一次单节点
   执行上（`node_run`），`delivered` 只由单次整链跑法写出；就绪结论的权威记录是
   assemble 最后提交的 manifest（本测试按后者断言，并钉住前者的现状）。
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
from novel.llm.transport import ChatResponse  # noqa: E402
from novel.orchestrator import (  # noqa: E402
    agent, agent_settings, operator, queue, state,
)
from novel.stages.chain import NODE_ORDER  # noqa: E402
from novel.stages.stage5.body import sha256_text  # noqa: E402
from tests.test_str06_body import _RoutingTransport  # noqa: E402
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

#: 假正文：只用在第 1／2 章已释放的称谓（夫君／芸娘／苏芸娘），不碰未释放专名
#: （陆／陆家／苏姑娘／沈…），不带场景标记；字数落在章篇幅带内——书级目标由源章
#: 总字数派生（本书 1000），两章各 500、带 [300, 650]，故两稿都取 300–650 非空白字符。
GOOD_CH01 = (
    '拜堂之夜，红烛高烧，苏芸娘望着夫君，把心头那句旧约问了个明白。夫君沉吟许久，才说这桩婚事不是寻常姻缘，'
    '而是多年前许下的承诺，今日不过是要兑现它。她想起陪嫁那夜，母亲拉着她的手说，做人妻子的本分是守住这个家，'
    '也要守住自己的心，莫叫一时的委屈改了性子。烛火摇了三摇，窗外风声渐紧，芸娘把旧约的每一条都记在心里，'
    '知道往后的日子都要照着这句话走下去，一步也错不得。天快亮时，夫君起身告辞，说此去是为了查明一桩旧案的真相，'
    '叫她不必等候，也不必牵挂，门要关好，灯要留一盏。她送他到门前，没有回头，只怕一回头就失了底气，'
    '叫他也跟着为难。回到房中，芸娘把旧约压在枕下，暗暗告诉自己，无论将来发生什么，这个家的门要一直开着，'
    '灯也要一直亮着。晨光透进窗纸，照见一地烛泪，她忽然觉得，从前怕的黑夜，也不过如此，'
    '只要心里有了着落，再长的夜也能坐到天亮。'
)
GOOD_CH02 = (
    '夫君夜半翻墙而出，芸娘在他枕下发现一方玉佩，刻着的字面陌生，不像家中旧物。她把玉佩藏回原处，只怕惊动了谁，'
    '索性坐到天明，把昨夜至今的每一件事都翻来覆去地想。起初她疑心夫君有了别的心思，可想起他临行前的眼神，'
    '又觉得不像，反倒像是怕牵连了谁。天将明时，她在灯下把那方玉佩的穗子拆开又系上，忽然想起母亲说过，'
    '旧家曾有一位亲人早年出走在远方，此去经年，杳无音信。她把这点疑惑按在心底，决定等夫君回来再问个明白，'
    '问的时候还要装作无意，免得他为难。鸡叫三遍，门外有了动静，芸娘起身梳洗，把鬓发理得平平的，'
    '像是什么也没有发生过，只是那方玉佩的凉意，一整日都留在她的指尖上，连写字的时候也跟着颤。'
    '她想起旧约里那句无论将来发生什么，忽然觉得这句话原是说给自己听的。'
)


class _EntryTransport(_RoutingTransport):
    """产品入口用的假 transport：沿用链级路由表，只把两章正文换成带内长度的稿。"""

    def send(self, request):
        owner = self._owner(request)
        if owner.startswith('body_ch'):
            self.calls.append(owner)
            text = GOOD_CH01 if owner.endswith('ch01') else GOOD_CH02
            return ChatResponse(content=text, finish_reason='stop',
                                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                                model='fake')
        return super().send(request)


class _InProcessPopen:
    """测试替身：把 canonical 子进程 argv 改为进程内执行真实 `main()`。

    只认两种 argv：`-m novel.cli`（节点执行，含上传里的 stage0 作业）与
    `-m novel.orchestrator.delivery`（交付终验）。其余一律关闭式报错——不静默
    跳过，也不真起进程。退出码由真实 `main()` 原样返回，日志按调用方给的
    stdout（管道或文件）原样落盘。
    """

    def __init__(self, argv, *, stdout=None, **_kwargs):
        self.argv = [str(token) for token in argv]
        self.pid = -1
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                returncode = self._dispatch()
        except BaseException:                       # noqa: BLE001 —— 栈要留全
            traceback.print_exc()
            returncode = 70
        payload = (out.getvalue() + err.getvalue()).encode('utf-8')
        if stdout is subprocess.PIPE:
            self.stdout = io.BytesIO(payload)
        else:
            if stdout is not None:
                stdout.write(payload)
            self.stdout = None
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

    # ③ Supervisor 真实规划 + 逐动作驱动：stage1 已就绪 ⇒ 首动作起正是 11 个节点。
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
    #    ⚠ 产品入口逐节点执行，run 级 `workflow_status.json` 落在最后一次单节点
    #    执行上（`node_run`）；就绪结论的权威记录是 assemble 最后提交的 manifest
    #    与 assembly report，`delivered` 只由单次整链跑法写出（见遗留问题③）。
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    status = _read_json(book / 'runtime' / 'workflow_status.json')
    assert status['status'] == 'node_run' and status['through'] == 'stage7'
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
    assert len(receipts[1]['packet_sha256']) == 64
    assert 'prior_text_sha256' not in receipts[2]
    assert 'book_ir_sha256' not in receipts[2]
    assert receipts[1]['packet_sha256'] != receipts[2]['packet_sha256']
    for entry in manifest['chapters']:
        assert entry['prose_sha256'] == receipts[entry['chapter_ordinal']]['prose_sha256']

    # ⑥ fake transport 也走真实 reserve／记账：12 次新购、真实 token、节点归属正确。
    assert len(transport.calls) == 13
    # B1：admission 平行计数退役，整书总闸（按 attempt 计数）是唯一准入账。
    from novel.work_units import read_book_budget
    assert read_book_budget(base, '联调样本_9001').used == 13
    rows = _ledger_rows(base)
    paid = [row for row in rows if not row['cached']]
    assert len(paid) == 13
    assert all(row['prompt_tokens'] > 0 and row['completion_tokens'] > 0
               for row in paid)
    assert {row['node'] for row in paid} == {
        'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
        'str06.stage2.associate', 'str06.stage4.preserve',
        'str06.stage4.body_design', 'str06.stage4.bind', 'str06.stage4.hook_design',
        'str06.stage5.hook', 'str06.stage5.body', 'str06.stage7.naming'}
    assert all(row['sid'] == '联调样本_9001' for row in paid)
    # Supervisor 走到 pipeline_complete 才终验：现役终验按 F8c 登记的话术回答
    # unknown（新链就绪记录是 manifest），不把未知洗成 ready。
    record = next(row for row in agent.records(base) if row.run_id == run_id)
    assert record.ended is not None and record.outcome is not None
    assert record.outcome.state == 'unknown'
    assert 'delivery_manifest.json' in record.outcome.reason

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
    repo = os.environ['NOVEL_B5_FAKE_REPO']
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import novel.stages.chain as _chain
    from novel.llm.transport import ChatResponse
    from tests.test_str06_body import _RoutingTransport
    from tests.test_str06_hook import _full_chain_wires

    _BODY_TEXTS = {'ch01': '拜堂之夜，红烛高烧，苏芸娘望着夫君，把心头那句旧约问了个明白。夫君沉吟许久，才说这桩婚事不是寻常姻缘，而是多年前许下的承诺，今日不过是要兑现它。她想起陪嫁那夜，母亲拉着她的手说，做人妻子的本分是守住这个家，也要守住自己的心，莫叫一时的委屈改了性子。烛火摇了三摇，窗外风声渐紧，芸娘把旧约的每一条都记在心里，知道往后的日子都要照着这句话走下去，一步也错不得。天快亮时，夫君起身告辞，说此去是为了查明一桩旧案的真相，叫她不必等候，也不必牵挂，门要关好，灯要留一盏。她送他到门前，没有回头，只怕一回头就失了底气，叫他也跟着为难。回到房中，芸娘把旧约压在枕下，暗暗告诉自己，无论将来发生什么，这个家的门要一直开着，灯也要一直亮着。晨光透进窗纸，照见一地烛泪，她忽然觉得，从前怕的黑夜，也不过如此，只要心里有了着落，再长的夜也能坐到天亮。', 'ch02': '夫君夜半翻墙而出，芸娘在他枕下发现一方玉佩，刻着的字面陌生，不像家中旧物。她把玉佩藏回原处，只怕惊动了谁，索性坐到天明，把昨夜至今的每一件事都翻来覆去地想。起初她疑心夫君有了别的心思，可想起他临行前的眼神，又觉得不像，反倒像是怕牵连了谁。天将明时，她在灯下把那方玉佩的穗子拆开又系上，忽然想起母亲说过，旧家曾有一位亲人早年出走在远方，此去经年，杳无音信。她把这点疑惑按在心底，决定等夫君回来再问个明白，问的时候还要装作无意，免得他为难。鸡叫三遍，门外有了动静，芸娘起身梳洗，把鬓发理得平平的，像是什么也没有发生过，只是那方玉佩的凉意，一整日都留在她的指尖上，连写字的时候也跟着颤。她想起旧约里那句无论将来发生什么，忽然觉得这句话原是说给自己听的。'}

    class _EntryTransport(_RoutingTransport):
        # 正文两章换成带内长度的稿（与本文件进程内 _EntryTransport 同一形状）。

        def send(self, request):
            owner = self._owner(request)
            if owner.startswith('body_ch'):
                self.calls.append(owner)
                key = 'ch01' if owner.endswith('ch01') else 'ch02'
                return ChatResponse(
                    content=_BODY_TEXTS[key], finish_reason='stop',
                    usage={'prompt_tokens': 100, 'completion_tokens': 40},
                    model='fake')
            return super().send(request)

    _chain.DeepSeekTransport = lambda cfg: _EntryTransport(_full_chain_wires())
"""


@pytest.mark.real_code_version
def test_service_full_chain_real_subprocess_and_restart_readback(base, tmp_path,
                                                                 monkeypatch):
    """HTTP 上传 → Supervisor → 真实 run_step/CLI 子进程 → Stage7 交付；
    页面刷新与服务重启（新建服务实例）后的只读读回逐字节一致。"""
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])

    # ① fake transport 经 sitecustomize 注入子进程（PYTHONPATH + 环境门）。
    fake_site = tmp_path / 'fake_transport_site'
    fake_site.mkdir()
    (fake_site / 'sitecustomize.py').write_text(_SITECUSTOMIZE_SOURCE,
                                                encoding='utf-8')
    monkeypatch.setenv('NOVEL_B5_FAKE_TRANSPORT', '1')
    monkeypatch.setenv('NOVEL_B5_FAKE_REPO', repo_root)
    existing = os.environ.get('PYTHONPATH')
    monkeypatch.setenv(
        'PYTHONPATH',
        str(fake_site) + (os.pathsep + existing if existing else ''))

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

    settings = agent_settings.read(base)
    agent_settings.update(base, profiles=_profiles(),
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
    #    （13 次新购、11 个付费节点、unknown 不冒充零）。
    rows = _ledger_rows(base)
    paid = [row for row in rows
            if row['sid'] == book.name and not row['cached']]
    assert len(paid) == 13
    assert {row['node'] for row in paid} == {
        'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
        'str06.stage2.associate', 'str06.stage4.preserve',
        'str06.stage4.body_design', 'str06.stage4.bind',
        'str06.stage4.hook_design', 'str06.stage5.hook', 'str06.stage5.body',
        'str06.stage7.naming'}

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
    assert nodes == {node for node in NODES if node.startswith('str06.')},         'run 面的节点集合必须恰为现役 14 节点的 str06 全集'
    assert delivery_face['present'] is True
    assert delivery_face['final_text_sha256'] == manifest['final_text_sha256']
    assert book_text == final_bytes.decode('utf-8')

    # ⑦ 零调用恢复：全部就绪后再次 agent.start ⇒ 零派发、零新购、交付原样。
    second = agent.start(base, '9003', authorized=True, egress_authorized=True,
                         profiles=_profiles())
    assert agent.wait(second.run_id, 600)
    assert final_path.read_bytes() == final_bytes
    assert manifest_path.read_bytes() == manifest_before,         '零派发的代跑不得改写交付 manifest'
