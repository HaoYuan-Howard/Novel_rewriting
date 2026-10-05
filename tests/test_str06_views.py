#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_views —— F7d 候选链只读监控面：views 纯函数与 HTTP 路由（免费）。

【验证边界】fake transport 全链造一个合成候选根，逐个监控面核对盘面事实：
Characters v4、Blueprint v12、graph／binding（Hook 与导演说明）、WriterBookIR
导航、章正文与章回执（机械范围／语义未评／篇幅披露）、Hook 与回执、NameMap v5、
成书 manifest 与下载、Run 节点图与 ledger 成本，以及 run 面的失败形状
（failure_kind／detail／ExecutionError 的 error_* 六字段，I-36）、章级失败留证
（`runtime/body_failures.json`）与 Stage7 集合核对五态报告
（`runtime/delivery/assembly_report.json`）。半程候选根（--through stage4）
的 present=false 与 unknown 分开；不存在的候选根与非法候选名在 HTTP 上是 404。

【纪律对账（与现役 api 同构）】响应模型不抄字面量取值面、不带判定字段
（badge／passed／counts）、契约层不 import web 栈；监控读口不写产物盘。

【R3 监控面（首版 PLAN.md（Git 2fc03832）R3 末行）】修订轮与停止原因：跨节点回派账
（`runtime/issues/<owner>.json`，OwnerIssueFileV1）投影进 run 面——owner／
node／raised_by／status／rounds_total 与逐条 typed 问题；停止原因仍在
failure_kind／detail 直投影（OwnerRevisionStop 的承载消息含按 item 的
问题清单）。从未起过修订轮的根 owner_issues 是 null（I-42：缺席不是空账）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from contracts.schema import api as str06_api   # noqa: E402
from contracts.schema.hook import HookConstraints      # noqa: E402
from novel.api.app import create_app                         # noqa: E402
from novel.errors import ArtifactUnreadable                  # noqa: E402
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot   # noqa: E402
from novel.llm.paid_requests import PaidRequestSession      # noqa: E402
from novel.llm.paid_requests import init_or_resume_candidate_workflow  # noqa: E402
from novel.store import Str06Repository                      # noqa: E402
from novel.views import str06 as views                       # noqa: E402
from tests.str06_facilities import make_context              # noqa: E402
from tests.test_str06_body import (
    _RoutingTransport, _chapters, _fake_ask, _fake_body, _fake_text,
)
from tests.test_str06_hook import _full_chain_wires
from novel.stages.chain import run_workflow

STR06_API_PY = Path(str06_api.__file__)

#: F8c 起候选监控面的响应模型与现役 API 契约同住 `contracts/schema/api.py`；
#: 本条只管 `Str06` 前缀的那一段（现役模型另有 `contracts/schema/api.py`
#: 自己的对账，不在候选面重复判）。
_STR06_MODELS = tuple(name for name in dir(str06_api) if name.startswith('Str06'))


def _candidate_root(tmp_path, wires, *, through='stage7', body_chapters=None) -> Path:
    """fake transport 全链 + workflow 身份记录 ⇒ 一个合成候选根（返回输出根）。"""
    root = tmp_path / 'cand'
    root.mkdir(exist_ok=True)
    repo = Str06Repository(root / 'run')
    ledger = CostLedger(path=ledger_path(root))
    repo.attach_context(make_context(root / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, _RoutingTransport(wires), yes=True,
                                 allow_source_export=True, provider='fake', sid='run')
    run_workflow(repository=repo, chapters=_chapters(),
                 ask=_fake_ask(session), ask_text=_fake_text(session),
                 ask_body=_fake_body(session), budget=2000, through=through,
                 book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                 length_band=(0.01, 0.05), body_chapters=body_chapters)
    init_or_resume_candidate_workflow(
        repository=repo, base=root, code='code-test', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)
    return tmp_path


@pytest.fixture
def base(tmp_path):
    return _candidate_root(tmp_path, _full_chain_wires())


def _failed_candidate_root(tmp_path) -> Path:
    """fake transport 截断反例（body_ch02 finish_reason=length）⇒ 失败候选根。

    第 1 章合法提交保留，第 2 章截断即停（D6）⇒ run 级状态带失败形状、
    章级失败留证落盘；Stage7 未跑 ⇒ 集合核对报告缺席。
    """
    root = tmp_path / 'cand'
    root.mkdir(exist_ok=True)
    repo = Str06Repository(root / 'run')
    ledger = CostLedger(path=ledger_path(root))
    repo.attach_context(make_context(root / 'run', ledger=ledger))
    session = PaidRequestSession(
        repo, ledger, _RoutingTransport(_full_chain_wires(), truncate=('body_ch02',)),
        yes=True, allow_source_export=True, provider='fake', sid='run')
    with pytest.raises(Exception):
        run_workflow(repository=repo, chapters=_chapters(),
                     ask=_fake_ask(session), ask_text=_fake_text(session),
                     ask_body=_fake_body(session), budget=2000, through='stage7',
                     book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                     length_band=(0.01, 0.05))
    init_or_resume_candidate_workflow(
        repository=repo, base=tmp_path, code='code-test', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)
    return tmp_path


# ─────────────────────────────── views 纯函数 ───────────────────────────────

def test_views_cover_the_whole_delivered_chain(base):
    name = 'cand'
    chars = views.characters_view(base, name)
    assert chars.present and chars.contract_version == 'str06-rc15'  # rc15 证据寻址
    assert {c.char_id for c in chars.characters} == {'fu_jun', 'yun_niang'}
    assert chars.name_structure is not None
    assert chars.name_structure.families == {'fam_lu': '陆', 'fam_su': '苏'}

    bp = views.blueprint_view(base, name)
    assert bp.present and [c.chapter for c in bp.chapters] == [1, 2]
    assert bp.chapters[0].beats[0].event.startswith('苏芸娘嫁入陆家')

    design = views.design_view(base, name)
    assert design.present and design.graph_id
    assert [s.scene_id for s in design.scenes] == ['sc01', 'sc02']
    assert design.scenes[0].realization_brief, '导演说明原样进监控面'
    assert [c.chapter_ordinal for c in design.chapters] == [1, 2]
    assert design.hook is not None and design.hook.person == 'first'
    assert list(design.hook.materials) == ['sc02#b01', 'sc01#b02'], '按设计序（乱序冷开合法）'
    assert design.binding is not None and design.binding.obligations == 1

    ir = views.ir_nav_view(base, name)
    assert ir.present and ir.target_chapter_count == 2 and ir.total_length_budget == 2000
    assert [c.chapter_ordinal for c in ir.chapters] == [1, 2]
    assert all(c.length_budget == 1000 for c in ir.chapters)
    assert {e['char_id'] for e in ir.release_schedule} == {'fu_jun', 'yun_niang'}

    ch1 = views.chapter_view(base, name, 1)
    assert ch1.present and ch1.prose and ch1.receipt is not None
    assert ch1.receipt.status == 'accepted'
    assert ch1.receipt.semantic == 'unverified', '机械回执不得升级为语义已验'
    assert ch1.receipt.length_disclosed is False
    assert ch1.receipt.mechanical_checks

    hook = views.hook_view(base, name)
    assert hook.present and hook.prose == '苏芸娘望着空房。'
    assert hook.receipt['status'] == 'accepted'

    naming = views.naming_view(base, name)
    assert naming.present and {e.char_ref for e in naming.entries} == {'fu_jun', 'yun_niang'}
    assert {e.family for e in naming.entries} == {'顾', '柳'}

    delivery = views.delivery_view(base, name)
    assert delivery.present and delivery.delivery_identity
    assert delivery.final_text_sha256 and len(delivery.final_text_sha256) == 64
    assert [c.chapter_ordinal for c in delivery.chapters] == [1, 2]
    assert delivery.length_reading['total_length_budget'] == 2000
    assert 'unverified' in (delivery.disclosure or '')

    run = views.run_view(base, name)
    assert run.present and run.status == 'delivered' and run.accepted_body is True
    assert run.epoch is not None and run.epoch.reason == 'initial'
    # B2 五节点（D9）＋CS-3 bind 退役：现役注册面 13 个 str06 节点，全链后
    # 每个节点都有产物（semantic_binding 归 body_design 双产物）。
    assert len(run.nodes) == 13 and all(n.produced for n in run.nodes), '全链后每个节点都有产物'
    cost = run.cost
    assert cost.present and cost.new_paid_rows == 12 and cost.cached_rows == 0
    assert cost.prompt_tokens is not None and cost.unknown_cost_rows == 0
    assert cost.per_node, '成本按候选节点归属'
    # 收尾批：成功形状的失败面是 None（不是空对象冒充「查过没事」）；
    # 集合核对报告就绪、五态全覆盖（空态是合法空，不是 unknown）。
    assert run.failure_kind is None and run.detail is None
    assert run.error_code is None
    # 逐章留证（D3/W2）：「最近一次完成执行」语义——干净链的正文与章观察都
    # 完成过且无失败 ⇒ 合法空清单（连同 requested/attempted/recorded_at），
    # 不是 None（缺席＝该节点没有完成执行）。
    assert run.body_failures is not None and run.body_failures.present
    assert run.body_failures.chapters == ()
    assert run.body_failures.requested == (1, 2)
    assert run.body_failures.attempted == (1, 2)
    assert run.body_failures.recorded_at is not None
    assert run.chapter_observation_failures is not None
    assert run.chapter_observation_failures.chapters == ()
    assert run.chapter_observation_failures.requested == (1, 2)
    # 干净链从没起过修订轮：回派账缺席是 None（R3 监控面）。
    assert run.owner_issues is None
    report = run.assembly_report
    assert report is not None and report.present
    assert report.accepted_body is True
    assert report.body_access is not None
    assert report.body_access.accepted == (1, 2)
    assert report.body_access.missing == () and report.body_access.rejected == ()
    assert report.body_access.unknown == () and report.body_access.stale == ()
    assert report.chain_problems == ()
    assert report.assembly is not None and report.assembly.status == 'ready'
    assert report.assembly.delivery_identity == delivery.delivery_identity


def test_run_view_projects_failure_shape_and_chapter_failures(tmp_path):
    """失败类别与 item 定位直投影（收尾批缺口 1）；章级失败留证同面（缺口 2）。

    视图字段与盘上 run 级状态逐字一致——前端不发明读数；body_failures 按
    章序留证（I-31 局部失败定位到 item）；Stage7 未跑 ⇒ 报告缺席是 None。
    """
    base = _failed_candidate_root(tmp_path)
    run = views.run_view(base, 'cand')
    assert run.present and run.status == 'failed'
    assert run.failed_node == 'str06.stage5.body'
    assert run.failure_kind == 'ExecutionError'
    assert run.error_code == 'truncated' and run.error_phase == 'accept'
    assert run.error_node == 'str06.stage5.body'
    assert run.error_item == 'chapter:2', 'item 是工作单元章序 scope，不是 owner 名'
    # 直投影对账：视图与盘上 workflow_status.json 逐字相等（含 context）。
    disk = Str06Repository(base / 'cand' / 'run').inspect(
        'runtime/workflow_status.json')
    assert run.failure_kind == disk['failure_kind']
    assert run.detail == disk['detail']
    for field in ('error_code', 'error_stage', 'error_node', 'error_item',
                  'error_phase', 'error_context'):
        assert getattr(run, field) == disk.get(field), field
    # 章级失败留证：第 2 章截断；第 1 章合法提交不在失败清单。
    assert run.body_failures is not None and run.body_failures.present
    assert [c.chapter_ordinal for c in run.body_failures.chapters] == [2]
    assert run.body_failures.chapters[0].failure_kind == 'ExecutionError'
    assert run.body_failures.chapters[0].detail
    # requested/attempted（D3/W2）：本次请求全书、两章都进了执行器。
    assert run.body_failures.requested == (1, 2)
    assert run.body_failures.attempted == (1, 2)
    assert run.body_failures.recorded_at is not None
    # 章观察顺利完成 ⇒ 它自己的留证是合法空清单，与正文失败并存不混。
    assert run.chapter_observation_failures is not None
    assert run.chapter_observation_failures.chapters == ()
    # 没跑到 Stage7：报告缺席是 None，不造空报告冒充「核对过」。
    assert run.assembly_report is None
    # HTTP 面与视图同形（同一 response_model，字段不丢）。
    with TestClient(create_app(base)) as client:
        response = client.get('/api/str06/candidates/cand/run')
        assert response.status_code == 200
        assert response.json() == json.loads(run.model_dump_json())


def test_run_view_projects_assembly_report_five_states(base):
    """Stage7 集合核对五态按 item 进 run 面（I-42：失效不是缺失、unknown 不作零）。"""
    import json as _json
    repo = Str06Repository(base / 'cand' / 'run')
    tampered = repo.inspect('stage5/acceptance/body/ch02.json')
    tampered['writer_view_sha256'] = 'f' * 64
    repo.path('stage5/acceptance/body/ch02.json').write_text(
        _json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    ledger = CostLedger(path=ledger_path(base / 'cand'))
    repo.attach_context(make_context(base / 'cand' / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, _RoutingTransport(_full_chain_wires()),
                                 yes=True, allow_source_export=True, provider='fake',
                                 sid='run')
    run_workflow(repository=repo, chapters=_chapters(),
                 ask=_fake_ask(session), ask_text=_fake_text(session),
                 ask_body=_fake_body(session), budget=2000, through='stage7',
                 book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                 length_band=(0.01, 0.05),
                 node_filter=('str06.stage7.assemble',))
    run = views.run_view(base, 'cand')
    report = run.assembly_report
    assert report is not None and report.present
    assert report.accepted_body is False
    assert report.body_access is not None
    assert report.body_access.stale == (2,) and report.body_access.accepted == (1,)
    assert report.body_access.missing == () and report.body_access.unknown == ()
    assert report.body_access.rejected == ()
    assert report.assembly is not None and report.assembly.status == 'not_ready'
    assert 'stale=[2]' in report.assembly.reason
    assert any('第2章' in problem for problem in report.chain_problems)
    # 章级失败留证与失败形状不受 assemble 单节点重跑影响：仍是完成执行时的
    # 合法空清单（D3/W2——assemble 不写正文留证，也不该抹掉它）。
    assert run.body_failures is not None and run.body_failures.chapters == ()
    assert run.body_failures.requested == (1, 2) and run.body_failures.attempted == (1, 2)


def test_half_run_candidate_keeps_present_and_missing_apart(tmp_path):
    """半程候选根：未跑的腿 present=false（不是错误、不是空数组冒充合法空值）。"""
    base = _candidate_root(tmp_path, _full_chain_wires(), through='stage4')
    run = views.run_view(base, 'cand')
    assert run.status == 'planned_validated' and run.accepted_body is False
    # 半程没有失败、没有失败留证、没有集合核对：三个都是 None，不是空对象。
    assert run.failure_kind is None and run.body_failures is None
    # 章观察已经完成执行过（stage2 在 stage4 之前）⇒ 它的留证是合法空清单，
    # 与从未执行的正文（None）分开（I-42：缺席≠空）。
    assert run.chapter_observation_failures is not None
    assert run.chapter_observation_failures.chapters == ()
    assert run.chapter_observation_failures.requested == (1, 2)
    assert run.assembly_report is None
    # 也没起过任何修订轮：回派账缺席是 None，不是空账本。
    assert run.owner_issues is None
    produced = {n.node for n in run.nodes if n.produced}
    assert 'str06.stage5.body' not in produced and 'str06.stage7.assemble' not in produced
    assert views.chapter_view(base, 'cand', 1).present is False
    assert views.chapter_view(base, 'cand', 1).prose is None
    assert views.hook_view(base, 'cand').present is False
    assert views.delivery_view(base, 'cand').present is False
    assert views.final_text_path(base, 'cand') is None
    # Stage2/4 面仍然可读：半程不是全黑。
    assert views.characters_view(base, 'cand').present
    assert views.design_view(base, 'cand').present
    assert views.ir_nav_view(base, 'cand').present


# ───────────────────────── D3/W2：逐章失败留证（章观察 × 正文） ─────────────

def test_chapter_observation_failures_project_the_last_completed_execution(
        tmp_path, monkeypatch):
    """章观察逐章失败留证（D3/W2）：失败投影 requested/attempted/chapters。

    粘性故障（unknown_slug）⇒ 第 1 章观察修订后同问题集停 ⇒ 失败按章留证；
    正文从未执行 ⇒ 它的留证缺席（None），两个字段不互相冒充。
    """
    from tests.str06_facilities import json_ask, text_ask, stub_body_runner
    from tests.str06_faults import FaultTransport, looped_full_chain_wires
    from tests.test_str06_body import _make_session

    root = tmp_path / 'obs'
    root.mkdir(exist_ok=True)
    repo = Str06Repository(root / 'run')
    ledger = CostLedger(path=ledger_path(root))
    repo.attach_context(make_context(root / 'run', ledger=ledger))
    sticky = FaultTransport(looped_full_chain_wires(), 'unknown_slug')
    session = PaidRequestSession(repo, ledger, sticky, yes=True,
                                 allow_source_export=True, provider='fake', sid='run')
    stub_body_runner(monkeypatch)
    with pytest.raises(Exception, match='ch01 校验失败'):
        run_workflow(repository=repo, chapters=_chapters(),
                     ask=json_ask(session), ask_text=text_ask(session),
                     ask_body=lambda owner, prompt: None, budget=2000,
                     through='stage7', book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10,
                                                      hard_max_chars=360))
    init_or_resume_candidate_workflow(
        repository=repo, base=root, code='code-test', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)

    run = views.run_view(tmp_path, 'obs')
    assert run.status == 'failed' and run.failed_node == 'str06.stage2.chapters'
    failures = run.chapter_observation_failures
    assert failures is not None and failures.present
    assert failures.requested == (1, 2), '章观察的 requested 是全部源章'
    assert failures.attempted == (1, 2)
    assert [c.chapter_ordinal for c in failures.chapters] == [1]
    assert failures.chapters[0].failure_kind == 'ValidationStop'
    assert 'ghost' in failures.chapters[0].detail
    assert failures.recorded_at is not None
    # 两个逐章留证互不冒充：正文从未执行 ⇒ 缺席（None），不是空清单。
    assert run.body_failures is None
    # HTTP 面与视图同形（同一 response_model，字段不丢）。
    with TestClient(create_app(tmp_path)) as client:
        response = client.get('/api/str06/candidates/obs/run')
        assert response.status_code == 200
        assert response.json() == json.loads(run.model_dump_json())


def test_body_failures_clear_after_authorized_recovery(tmp_path):
    """恢复后留证清空（D3/W2）：未授权执行把 PurchaseRequired 按章留证，
    授权重跑完成 ⇒ 同一文件改写成合法空清单——不是删文件冒充没执行过。"""
    root = tmp_path / 'auth'
    root.mkdir(exist_ok=True)
    repo = Str06Repository(root / 'run')
    ledger = CostLedger(path=ledger_path(root))
    repo.attach_context(make_context(root / 'run', ledger=ledger))
    authorized = PaidRequestSession(
        repo, ledger, _RoutingTransport(_full_chain_wires()), yes=True,
        allow_source_export=True, provider='fake', sid='run')
    # 语义层先跑完（付费授权在）；正文单独一次未授权执行。
    run_workflow(repository=repo, chapters=_chapters(),
                 ask=_fake_ask(authorized), ask_text=_fake_text(authorized),
                 ask_body=_fake_body(authorized), budget=2000, through='stage4',
                 book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                 length_band=(0.01, 0.05))
    init_or_resume_candidate_workflow(
        repository=repo, base=root, code='code-test', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)
    unauthorized = PaidRequestSession(
        repo, ledger, _RoutingTransport(_full_chain_wires()), yes=False,
        allow_source_export=False, provider='fake', sid='run')
    with pytest.raises(Exception, match='未授权新调用'):
        run_workflow(repository=repo, chapters=_chapters(),
                     ask=_fake_ask(unauthorized), ask_text=_fake_text(unauthorized),
                     ask_body=_fake_body(unauthorized), budget=2000,
                     node_filter=('str06.stage5.body',), book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10,
                                                      hard_max_chars=360),
                     length_band=(0.01, 0.05))
    run = views.run_view(tmp_path, 'auth')
    assert run.status == 'needs_authorization'
    blocked = run.body_failures
    assert blocked is not None and blocked.present
    assert blocked.requested == (1, 2) and blocked.attempted == (1, 2)
    assert [c.chapter_ordinal for c in blocked.chapters] == [1, 2]
    assert all(c.failure_kind == 'PurchaseRequired' for c in blocked.chapters)

    # 恢复：授权后重跑同一节点 ⇒ 完成执行无失败 ⇒ 留证改写成合法空清单。
    run_workflow(repository=repo, chapters=_chapters(),
                 ask=_fake_ask(authorized), ask_text=_fake_text(authorized),
                 ask_body=_fake_body(authorized), budget=2000,
                 node_filter=('str06.stage5.body',), book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                 length_band=(0.01, 0.05))
    healed = views.run_view(tmp_path, 'auth')
    cleared = healed.body_failures
    assert cleared is not None and cleared.present, '文件仍在，不是删掉冒充没跑过'
    assert cleared.chapters == () and cleared.requested == (1, 2)
    assert cleared.attempted == (1, 2) and cleared.recorded_at is not None


def test_body_failures_project_the_selected_chapter_range(tmp_path):
    """canary 选章（W1）进逐章留证：requested=attempted=选中章，不是全书。"""
    base = _candidate_root(tmp_path, _full_chain_wires(), body_chapters=(2,))
    run = views.run_view(base, 'cand')
    failures = run.body_failures
    assert failures is not None and failures.present
    assert failures.requested == (2,) and failures.attempted == (2,)
    assert failures.chapters == ()
    # 选章只作调用元数据：run 级状态里是可审计的独立字段，视图直投影。
    assert run.status == 'assembly_not_ready'
    assert run.body_failures.recorded_at is not None


def test_chapter_failures_view_refuses_malformed_file(tmp_path):
    """畸形留证 fail fast（I-34）：丢一条失败比整面 500 更坏，不静默清空。"""
    base = _candidate_root(tmp_path, _full_chain_wires())
    repo = Str06Repository(base / 'cand' / 'run')
    broken = repo.path('runtime/body_failures.json')
    import json as _json
    broken.write_text(_json.dumps({'requested': [1, 2], 'attempted': [1]},
                                  ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ArtifactUnreadable, match='形状损坏'):
        views.run_view(base, 'cand')


# ───────────────────────── R3 监控面：修订轮与停止原因 ─────────────────────────

def _faulted_candidate_root(tmp_path, monkeypatch, *, fault_once: bool,
                            name: str) -> Path:
    """R0 故障注入底座（obligation_unbound）跑一条候选链 ⇒ 带 issues 记录的根。

    `fault_once=True`：受影响 owner 首响应带故障、修订轮返回干净 wire——链经
    修订自愈跑完（回派账 resolved 留轮数）；`fault_once=False`：粘性故障 ⇒
    同问题集重现按 item 停（回派账 open、run 级失败形状带停止原因）。
    """
    from tests.str06_facilities import json_ask, text_ask, stub_body_runner
    from tests.str06_faults import FaultTransport, looped_full_chain_wires
    from tests.test_str06_body import _make_session

    wires = looped_full_chain_wires()
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    repo = Str06Repository(root / 'run')
    ledger = CostLedger(path=ledger_path(root))
    repo.attach_context(make_context(root / 'run', ledger=ledger))
    session = PaidRequestSession(
        repo, ledger, FaultTransport(wires, 'obligation_unbound', fault_once),
        yes=True, allow_source_export=True, provider='fake', sid='run')
    stub_body_runner(monkeypatch)
    try:
        run_workflow(repository=repo, chapters=_chapters(),
                     ask=json_ask(session), ask_text=text_ask(session),
                     ask_body=lambda owner, prompt: None, budget=2000,
                     through='stage7', book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10,
                                                      hard_max_chars=360))
    except Exception:
        pass          # 粘性故障停在 OwnerRevisionStop；失败形状由链自己留痕
    init_or_resume_candidate_workflow(
        repository=repo, base=root, code='code-test', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)
    return root.parent


def test_run_view_projects_revision_rounds_of_a_healed_chain(tmp_path, monkeypatch):
    """修订轮投影（R3＋CS-3）：承载闸在 body_design 同节点修订轮自愈——
    无跨节点回派账，投影缺席如实呈现（不虚构 resolved 记录）。"""
    base = _faulted_candidate_root(tmp_path, monkeypatch, fault_once=True,
                                   name='healed')
    run = views.run_view(base, 'healed')
    assert run.present
    issues = run.owner_issues
    assert issues is None or not issues.present, '同节点修订自愈不产生回派账'
    # 自愈链没有失败形状：停止原因缺席是 None，不是空串。
    assert run.failure_kind is None and run.detail is None
    # HTTP 面与视图同形（同一 response_model，字段不丢）。
    with TestClient(create_app(base)) as client:
        response = client.get('/api/str06/candidates/healed/run')
        assert response.status_code == 200
        assert response.json() == json.loads(run.model_dump_json())


def test_run_view_projects_owner_issue_records_from_disk(tmp_path, monkeypatch):
    """回派账投影（R3）：盘上 runtime/issues/*.json 忠实投影——轮数上限、
    typed 问题与源盲 core id 定位（回派账的现役产生面：associate 未决回派、
    epistemic_lock_order 与 material_leak，链上构造见 test_str06_r3_selfheal）。"""
    from contracts.schema.workflow import OwnerIssueFileV2
    from novel.stages.stage4 import obligation_core_id
    from novel.stages.owner_revision import owner_issues_rel

    base = _faulted_candidate_root(tmp_path, monkeypatch, fault_once=True,
                                   name='issues')
    repo = Str06Repository(base / 'issues' / 'run')
    repo.attach_context(make_context(base / 'issues' / 'run',
                                     ledger=CostLedger(path=None)))
    core = obligation_core_id('ob01')
    kp = f'kp_{"0" * 16}'
    record = OwnerIssueFileV2.model_validate({
        'owner': 'design', 'node': 'str06.stage4.body_design',
        'raised_by': 'str06.stage4.compile', 'status': 'resolved',
        'rounds_total': 1, 'fingerprint': 'f' * 16,
        'against_sha256': 'a' * 64, 'no_progress': False, 'updated_at': 1759000000,
        'issues': [{'code': 'epistemic_lock_order', 'item': kp,
                    'message': f'必保义务 {core} 的知情锁升级早于授权承载位',
                    'context': {'core_id': core,
                                'upgrade_position': 'sc01#b03',
                                'authorized_position': 'sc01#b04',
                                'mode': 'known'}}]})
    repo.write(owner_issues_rel('design'), record)
    run = views.run_view(base, 'issues')
    issues = run.owner_issues
    assert issues is not None and issues.present, '回派账在盘上，投影不得缺席'
    assert issues.rounds_cap == 2, '修订轮上限是契约常量的投影，前端不抄'
    assert [r.owner for r in issues.records] == ['design']
    first = issues.records[0]
    assert first.node == 'str06.stage4.body_design'
    assert first.raised_by == 'str06.stage4.compile'
    assert first.status == 'resolved' and first.rounds_total == 1
    assert first.fingerprint and first.updated_at >= 0
    assert first.issues[0].code == 'epistemic_lock_order'
    assert first.issues[0].item == kp
    assert 'ob01' not in first.issues[0].message
    assert first.issues[0].context['core_id'] == core


def test_run_view_shows_stop_reason_with_open_issue_rounds(tmp_path, monkeypatch):
    """停止原因（R3＋CS-3）：粘性 design 坏稿在同节点修订轮按同指纹停 ⇒
    failure_kind/detail 直投影 typed 停止；正式位不落半份产物。"""
    base = _faulted_candidate_root(tmp_path, monkeypatch, fault_once=False,
                                   name='stuck')
    run = views.run_view(base, 'stuck')
    assert run.present and run.status == 'failed'
    assert run.failure_kind == 'ValidationStop'
    assert '重问后无进展' in (run.detail or '')
    assert run.failed_node == 'str06.stage4.body_design'
    issues = run.owner_issues
    assert issues is None or not issues.present, '同节点停止不产生回派账'
    with TestClient(create_app(base)) as client:
        response = client.get('/api/str06/candidates/stuck/run')
        assert response.status_code == 200
        assert response.json()['failure_kind'] == 'ValidationStop'


def test_corrupt_issue_record_fails_fast_instead_of_reading_as_none(tmp_path):
    """回派账形状损坏 ⇒ 监控面显式拒绝（I-34）：它是修订路由的输入，
    不能静默当作「没有问题」继续。"""
    base = _candidate_root(tmp_path, _full_chain_wires(), through='stage4')
    issues_dir = base / 'cand' / 'run' / 'runtime' / 'issues'
    issues_dir.mkdir(parents=True)
    (issues_dir / 'associate.json').write_text('{"schema_version": 1}',
                                               encoding='utf-8')
    with pytest.raises(ArtifactUnreadable, match='associate.json'):
        views.run_view(base, 'cand')


def test_free_recovery_adds_only_cached_rows(base):
    """同根重放（零新购）后：实付行不增，缓存行按 Q-11 每次命中记一行。"""
    root = base / 'cand'
    repo = Str06Repository(root / 'run')
    ledger = CostLedger(path=ledger_path(root))
    repo.attach_context(make_context(root / 'run', ledger=ledger))
    transport = _RoutingTransport(_full_chain_wires())
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake', sid='run')
    before = views.run_view(base, 'cand').cost
    run_workflow(repository=repo, chapters=_chapters(),
                 ask=_fake_ask(session), ask_text=_fake_text(session),
                 ask_body=_fake_body(session), budget=2000, through='stage7',
                 book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                 length_band=(0.01, 0.05))
    after = views.run_view(base, 'cand').cost
    assert not transport.calls, '同请求身份重放零新购'
    assert after.new_paid_rows == before.new_paid_rows
    assert after.cached_rows > before.cached_rows
    assert after.prompt_tokens == before.prompt_tokens, '缓存采用不重复计历史 token'


def test_candidate_listing_only_reports_real_candidate_roots(tmp_path):
    """清单只认带 workflow 身份记录的目录；普通书目录与非候选输出根不混入。"""
    (tmp_path / 'ordinary_book' / 'stage1').mkdir(parents=True)
    (tmp_path / 'not_a_root').mkdir()
    base = _candidate_root(tmp_path, _full_chain_wires())
    names = {c.candidate for c in views.list_candidates(base).candidates}
    assert names == {'cand'}
    assert views.list_candidates(tmp_path / 'missing').candidates == ()


# ─────────────────────────────── HTTP 路由 ───────────────────────────────

@pytest.fixture
def client(base):
    with TestClient(create_app(base)) as c:
        yield c


def test_candidate_reads_over_http_match_the_views(client, base):
    listed = client.get('/api/str06/candidates')
    assert listed.status_code == 200
    assert [c['candidate'] for c in listed.json()['candidates']] == ['cand']
    for path, expected in (
            ('/api/str06/candidates/cand/run', views.run_view(base, 'cand')),
            ('/api/str06/candidates/cand/characters', views.characters_view(base, 'cand')),
            ('/api/str06/candidates/cand/blueprint', views.blueprint_view(base, 'cand')),
            ('/api/str06/candidates/cand/design', views.design_view(base, 'cand')),
            ('/api/str06/candidates/cand/ir', views.ir_nav_view(base, 'cand')),
            ('/api/str06/candidates/cand/hook', views.hook_view(base, 'cand')),
            ('/api/str06/candidates/cand/naming', views.naming_view(base, 'cand')),
            ('/api/str06/candidates/cand/delivery', views.delivery_view(base, 'cand'))):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.json() == json.loads(expected.model_dump_json()), path
    chapter = client.get('/api/str06/candidates/cand/chapters/1')
    assert chapter.status_code == 200
    assert chapter.json()['receipt']['semantic'] == 'unverified'
    book = client.get('/api/str06/candidates/cand/book')
    assert book.status_code == 200
    assert book.text == views.final_text_path(base, 'cand').read_text(encoding='utf-8')


def test_unknown_candidate_is_404_with_a_typed_body(client):
    for path in ('/api/str06/candidates/nope/run',
                 '/api/str06/candidates/nope/characters',
                 '/api/str06/candidates/nope/book'):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()['kind'] == 'UpstreamMissing'


def test_illegal_candidate_name_is_refused_as_missing_not_as_a_path(client):
    """`..`／分隔符候选名在边界上就不是候选根：404，不做任何路径解析。"""
    for name in ('..', 'a/b', '.'):
        response = client.get(f'/api/str06/candidates/{name}/run')
        assert response.status_code == 404, name


def test_candidate_reads_never_write_the_disk(client, base):
    """监控读口一个字节都不写（R-11.3）：读前读后整棵树指纹不变。"""
    def tree() -> set:
        return {(p.relative_to(base).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                for p in base.rglob('*') if p.is_file()}
    before = tree()
    for path in ('/api/str06/candidates', '/api/str06/candidates/cand/run',
                 '/api/str06/candidates/cand/design', '/api/str06/candidates/cand/ir',
                 '/api/str06/candidates/cand/chapters/2',
                 '/api/str06/candidates/cand/book'):
        assert client.get(path).status_code == 200, path
    assert tree() == before


# ─────────────────────────────── 契约纪律对账 ───────────────────────────────

def test_candidate_api_models_keep_the_active_disciplines():
    """与 `contracts/schema/api.py` 同构的三条：不抄取值面、不带判定、不背 web 栈。

    ⚠ F8c 起候选模型与现役模型同住一个文件，扫描面随之收窄到 `Str06` 前缀的
    类（现役那一半由 `tests/test_api_discipline.py` 之类另立，不在这里重复判）。
    字面量与依赖两条扫整个文件——它们与归属无关，候选段多一个字面量就是多一个
    取值面落点。
    """
    tree = ast.parse(STR06_API_PY.read_text(encoding='utf-8'), filename=str(STR06_API_PY))
    literals = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript)
                and isinstance(n.value, ast.Name) and n.value.id == 'Literal']
    spelled = []
    for node in literals:
        items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        spelled += [n.lineno for n in items
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not spelled, f'候选视图模型抄了字面量取值面（第 {spelled} 行）'
    verdicts = {'badge', 'passed', 'counts', 'hard_count', 'unver_count'}
    assert _STR06_MODELS, '一个 Str06 视图模型都没扫到——扫描面失效了'
    for name in _STR06_MODELS:
        model = getattr(str06_api, name)
        fields = getattr(model, 'model_fields', None)
        if fields is None:
            continue
        assert not (verdicts & set(fields)), f'{name} 带判定字段——第二个判官'
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split('.')[0])
    assert not (mods & {'fastapi', 'starlette', 'uvicorn', 'novel'}), \
        f'候选契约层长出了不该有的依赖：{sorted(mods)}'
    assert 'pydantic' in mods


def test_book_download_carries_the_chinese_filename(base):
    """成书下载带 `Content-Disposition`（F8b 从旧 stage7/book.txt 移植）。

    ⚠ 书名是中文而 header 值只能是 latin-1：`filename=` 留 ASCII 回退
    （`book.txt`，与 URL 同名），真名走 `filename*=UTF-8''<百分号编码>`——
    另存文件名用**盘上那份的名字**，前端不拿书名重拼（`R-11.4`）。
    ⚠ `download` 缺省 false 时不带头：页面内预览与另存是两件事。
    """
    from urllib.parse import unquote

    with TestClient(create_app(base)) as http:
        preview = http.get('/api/str06/candidates/cand/book')
        assert preview.status_code == 200, preview.text[:200]
        assert 'content-disposition' not in preview.headers

        download = http.get('/api/str06/candidates/cand/book?download=true')
        assert download.status_code == 200
        header = download.headers['content-disposition']
        assert header.startswith('attachment; filename="book.txt"; filename*=')
        name = unquote(header.split("filename*=UTF-8''", 1)[1])
        assert name.endswith('_rewriting.txt') and name != 'book.txt'
        assert download.text == views.final_text_path(base, 'cand').read_text(
            encoding='utf-8')


def test_carried_forward_adoption_adds_only_zero_token_cached_rows(base):
    """免费沿用在成本面上只增零 token 缓存行：实付行与历史 token 不变（I-43）。"""
    from dataclasses import replace

    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_body_design
    from tests.str06_facilities import carry_forward_ask, republish_checkpoint
    from tests.test_str06_candidate_preflight import _lineage_only_mutation

    # base fixture 是全链交付根（输出根＝tmp_path，候选目录名 cand）：design 单元
    # 账里已有已购候选，候选登记随 H1b 落盘。
    repo = Str06Repository(base / 'cand' / 'run')
    ledger = CostLedger(path=ledger_path(base / 'cand'))
    repo.attach_context(make_context(repo.root, ledger=ledger))
    transport = _RoutingTransport(_full_chain_wires())
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake', sid='run')
    ctx = ChainContext(repository=repo, ask=_fake_ask(session),
                       ask_text=_fake_text(session), ask_body=_fake_body(session),
                       chapters=_chapters(), budget=2000, digest=digest)
    before = views.run_view(base, 'cand').cost
    # 上游新版本（修订对照 wire 条目不变）→ 已购 design 候选零调用沿用
    republish_checkpoint(repo, _lineage_only_mutation)
    result = run_body_design(replace(ctx, carry_forward=carry_forward_ask(session)))
    assert result['reuse']['action'] == 'carried_forward'
    after = views.run_view(base, 'cand').cost
    assert after.new_paid_rows == before.new_paid_rows, '免费沿用不加实付行'
    assert after.cached_rows == before.cached_rows + 1, '只多一行零 token 缓存行'
    assert after.prompt_tokens == before.prompt_tokens
    assert after.completion_tokens == before.completion_tokens
    assert after.reasoning_tokens == before.reasoning_tokens, '不把历史 token 抄第二遍'
