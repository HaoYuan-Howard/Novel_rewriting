#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选链恢复运行：workflow 身份记录的落盘形状、损坏 fail-closed 与 epoch 复用。

⚠ **F8b-3 载荷切换**：原文件是旧链 Stage2 blueprint 的验证后 checkpoint／一次性
batch workpack／run_id 闭环（`novel.llm.stage2_runs` 与 `novel.stages.stage2.extract`，
约 35 条用例）。那套机器随旧链整层退役：新链的「恢复运行」是**候选根自身的
workflow 身份生命周期**（`novel/llm/paid_requests.py`）＋ 付费会话的续跑
（`novel/llm/paid_requests.py::PaidRequestSession`）。旧反例的历史字节见 Git；
本文件只保留新链上仍成立的
恢复运行纪律，并不复述已由 `tests/test_str06_f7a_wiring.py` 覆盖的
init/resume/drift 正向面（那条已钉住「同代码复用、漂移滚动、源身份拒绝」）。

【本文件钉的三件事】
  ① `workflow_to_disk`／`workflow_from_disk` round-trip 后身份逐字相等；
  ② 记录形状损坏（缺键／错型／非 dict）一律 fail-closed，不猜、不补默认值；
  ③ 复用路径里 ledger 的 parent 恒为当前 epoch 的 session_run_id——
     每次 CLI 调用是新 run，epoch 是稳定窗口（Q-10 的账不能断）。
"""
from __future__ import annotations

import json

import pytest

from novel.errors import ProvenanceRefused
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import (
    WORKFLOW_REL,
    init_or_resume_candidate_workflow,
    workflow_from_disk,
    workflow_to_disk,
)
from novel.store import Str06Repository
from tests.str06_facilities import make_context

_SOURCE = 'b' * 64


def _repo(tmp_path, *, node='str06.stage2.book'):
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(
        tmp_path / 'run', ledger=CostLedger(path=None), node=node))
    return repo


def _init(repo, *, source_identity=_SOURCE, code='code-a', created_at=1759000000,
          readonly=False):
    return init_or_resume_candidate_workflow(
        repository=repo, base=repo.root.parent, code=code, root_sha1='a' * 40,
        source_identity=source_identity, task_id='task-1', configured_model='fake',
        created_at=created_at, readonly=readonly)


def _epoch_identity(workflow) -> tuple[str, int]:
    """`(epoch_id, ordinal)`——并发续跑对账用的最小身份对。"""
    return workflow.epoch.epoch_id, workflow.epoch.ordinal


def _ledger_parent(context) -> tuple[str, str]:
    """`(ledger 路径, parent run)`——并发下父身份不许分叉（Q-10 的账不能断）。"""
    return str(context.ledger.path), context.ledger.parent_run_id


def test_workflow_record_round_trips_byte_for_byte_identity(tmp_path):
    """落盘形状 round-trip 后 epoch／契约表／源身份逐字相等（不允许静默补默认值）。"""
    repo = _repo(tmp_path)
    workflow, _context = _init(repo)
    stored = repo.read(WORKFLOW_REL)
    assert workflow_from_disk(stored) == workflow
    assert workflow_from_disk(workflow_to_disk(workflow)) == workflow
    assert stored['epoch']['epoch_id'] == workflow.epoch.epoch_id
    assert stored['contract_identity'] == workflow.contract_identity


@pytest.mark.parametrize('tamper', [
    {'drop': 'epoch'},
    {'drop': 'contract_identity'},
    {'drop': 'source_identity'},
    {'drop': 'created_at'},
    {'blank': 'epoch'},
    {'not_dict': True},
    {'epoch_ordinal': 'one'},
    {'epoch_reason': 'because'},
])


def test_corrupted_workflow_record_fails_closed_before_any_resume(tmp_path, tamper):
    """形状损坏的 workflow 记录是关闭式拒绝，不是「当没记录重开」也不是补默认值。

    ⚠ 重开会把旧根的已购采用结论接到新输入上；补默认值会让 epoch 身份悄悄变形。
    两种都等于拿旧账给新跑背书，所以这里只接受 `ProvenanceRefused`——
    装载期缺键/非 dict 由 `workflow_from_disk` 翻成「形状损坏」，epoch 内字段
    型错误由 `check_candidate_workflow` 复校成「epoch 形状损坏」问题。
    """
    repo = _repo(tmp_path)
    _init(repo)
    record = repo.read(WORKFLOW_REL)
    if tamper.get('not_dict'):
        broken = ['not', 'a', 'dict']
    elif 'epoch_ordinal' in tamper:
        broken = dict(record)
        broken['epoch'] = {**record['epoch'], 'ordinal': tamper['epoch_ordinal']}
    elif 'epoch_reason' in tamper:
        broken = dict(record)
        broken['epoch'] = {**record['epoch'], 'reason': tamper['epoch_reason']}
    elif tamper.get('blank'):
        broken = dict(record)
        broken[tamper['blank']] = {}
    else:
        broken = dict(record)
        broken.pop(tamper['drop'])
    repo.path(WORKFLOW_REL).write_text(
        json.dumps(broken, ensure_ascii=False), encoding='utf-8')

    with pytest.raises(ProvenanceRefused, match='形状损坏|复校失败'):
        _init(repo)


def test_resume_keeps_ledger_parent_on_the_current_epoch_session(tmp_path):
    """复用不开新 epoch；ledger parent 恒为当前 epoch 的 session_run_id。

    （Q-10：每次 CLI 调用是一条新 run，epoch 是稳定窗口——parent 断了，
    累计额度与工作单元账就再也归不到同一本书上。）
    """
    repo = _repo(tmp_path)
    first, first_ctx = _init(repo)
    second, second_ctx = _init(repo)

    assert second.epoch.epoch_id == first.epoch.epoch_id
    assert first_ctx.ledger.parent_run_id == first.epoch.session_run_id
    assert second_ctx.ledger.parent_run_id == second.epoch.session_run_id
    assert second_ctx.ledger.parent_run_id == first_ctx.ledger.parent_run_id
    assert second_ctx.ledger.path == ledger_path(repo.root.parent), (
        '候选根的 ledger 必须落在本根 `_service/` 下——父身份换根就归不到同一本书')


def test_readonly_resume_reuses_the_epoch_and_refuses_nothing_extra(tmp_path):
    """只读复用（检查/监控路径）同样原样接当前 epoch，不滚动、不拒。"""
    repo = _repo(tmp_path)
    first, _first_ctx = _init(repo)
    again, readonly_ctx = _init(repo, created_at=1759000001, readonly=True)

    assert again.epoch.epoch_id == first.epoch.epoch_id
    assert readonly_ctx.readonly is True
    assert readonly_ctx.ledger.parent_run_id == first.epoch.session_run_id


def test_concurrent_resume_of_one_root_converges_on_a_single_epoch(tmp_path):
    """同一候选根被多个执行者同时续跑：epoch 与 ledger 父身份必须收敛到一份。

    恢复运行的并发面：盘上 workflow 记录是唯一真相源，同代码下复用路径不写盘，
    因此多少个线程同时 init/resume 都只能读到**同一份**身份；ledger 的 parent
    也不许按线程分叉——分叉了累计额度与工作单元账就再也归不到同一本书（Q-10）。
    """
    import threading

    repo = _repo(tmp_path)
    first, _first_ctx = _init(repo)
    seen: list[tuple[str, int]] = []
    parents: list[tuple[str, str]] = []
    lock = threading.Lock()

    def resume() -> None:
        workflow, context = _init(repo)
        with lock:
            seen.append(_epoch_identity(workflow))
            parents.append(_ledger_parent(context))

    threads = [threading.Thread(target=resume) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == [_epoch_identity(first)] * len(threads), (
        '并发续跑给出了不同 epoch——身份真相源出现了第二个落点')
    assert len(set(parents)) == 1, f'ledger 父身份按线程分叉了：{sorted(set(parents))}'
