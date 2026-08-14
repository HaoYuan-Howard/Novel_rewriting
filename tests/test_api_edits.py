#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_edits.py —— 人工写回三条路由的**行为**（真起 CLI 子进程）。

【四类行为】（BE-03）：成功 / 上游未运行（200+failed，不误发 5xx）/
产物损坏（200+failed，日志可读）/ 参数不合法（422 载体层）。
「未实现 ⇒ 501」不适用：三条路由后面的节点全部接线（`RunModeNotImplemented`
只属未接线跑法）。

【钱】三条全是 0 成本节点（`--apply-edit` / approve 均在 FREE 面）——
`no_api_key` 把钥匙拿走，任何一条走到真发请求的路都当场现形。

⚠ 沙箱一律 `tempfile.mkdtemp()`；**原语料一个字节都不许动**。
"""
from __future__ import annotations

import json
import os

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from novel.api.app import UI_HEADER, create_app    # noqa: E402
from tests import corpus, sandbox                  # noqa: E402

pytestmark = pytest.mark.skipif(corpus.corpus_root() is None,
                                reason='盘上没有语料——这一批**没在查**，不是「查过没事」')

INPUTS = ('stage1/main_text.json', 'stage1/hook_copy.json',
          'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
          'stage3/style_grounding.json',
          'stage4/plan_chapters.json', 'stage4/writer_context.json',
          'stage6/hook.json', 'stage7/name_map.json')


def books() -> list[str]:
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage7/name_map.json'}):
        plan = sandbox.cut(book, 'stage4/plan_chapters.json')
        if plan is not None and '_provenance' in plan:
            out.append(book)
    return out


def _stage5_rels(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


@pytest.fixture
def box():
    book = books()[0]
    s = sandbox.Sandbox(book, INPUTS + _stage5_rels(book))
    yield s
    s.close()


@pytest.fixture
def client(box):
    with TestClient(create_app(box.root)) as c:
        c.headers.update({UI_HEADER: '1'})
        yield c


def _draft_mode(box, ordinal: int) -> str:
    for mode in ('deep', 'shallow'):
        if (box.dir / 'stage5' / mode / f'ch{ordinal:02d}.json').exists():
            return mode
    raise AssertionError('沙箱书第 1 章一条臂都没有稿——语料多半变了')


def _edits_dir(box):
    return box.root / '_service' / 'edits'


def _stamp_chain(box, upto: str) -> None:
    """预置审批检查单到 `upto`（含）——模拟一本正常走到中后段的书。

    审批文档是 `_service/` 的服务状态（不是产物），测试直接落一份合法 doc
    是在摆前置事实，不是绕守卫；`approve_gate` 的链式约束由 approvals 自己的测试守。
    """
    n = int(upto[1:])
    doc = {
        'v': 1, 'sid': box.sid,
        'gate': {f's{i}': i <= n for i in range(8)},
        'seen': {'read': True, 'global': True},
        'deep': [], 'writing': True, 'spawns': {},
    }
    p = box.root / '_service' / 'approvals' / f'{box.sid}.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')


# ───────────────────────── 一、章节改稿 ─────────────────────────

def test_a_draft_edit_lands_as_a_new_human_round(client, box):
    """⭐ 成功路：人工稿落成新一轮——`origin=human`、round+1、旧轮进 `_rounds/`、
    一次性稿已清理。**与生成稿同一个判定落点**（仍有阻断级只报不拦）。"""
    mode = _draft_mode(box, 1)
    rel = box.dir / 'stage5' / mode / 'ch01.json'
    before = json.loads(rel.read_text(encoding='utf-8'))
    marker = '她把那句话咽了回去，转身出门。'
    text = (before['text'] + '\n\n' + marker)

    r = client.post(f'/api/novels/{box.sid}/review/1/edit',
                    json={'mode': mode, 'text': text})
    assert r.status_code == 200, r.text[:300]
    assert r.json()['state'] == 'ok', r.text[:300]

    after = json.loads(rel.read_text(encoding='utf-8'))
    assert marker in after['text']
    assert after['_provenance']['origin'] == 'human'
    assert 'prompts' not in after['_provenance'] and 'model' not in after['_provenance'], \
        '人工稿记了 prompt/model——那是假账（RoundFreeProvenance 在结构上没有那两格）'
    assert int(after['_provenance']['round']) == int(
        before.get('_provenance', {}).get('round', 1)) + 1
    rounds = list((box.dir / 'stage5' / mode / '_rounds').glob('ch01.r*.json'))
    assert rounds, '旧轮没有归档——覆盖前的那一份再也回不去了'
    assert not any(_edits_dir(box).rglob('*.txt')), '一次性人工稿没清理'


def test_a_draft_edit_without_upstream_is_a_failed_job_not_a_5xx(client, box):
    """上游未运行：删 ctx ⇒ CLI 显式终态 ⇒ **200 + state=failed**（作业失败不是 HTTP 错误）。"""
    (box.dir / 'stage4' / 'writer_context.json').unlink()
    r = client.post(f'/api/novels/{box.sid}/review/1/edit',
                    json={'mode': _draft_mode(box, 1), 'text': '改过的正文。'})
    assert r.status_code == 200, r.text[:300]
    assert r.json()['state'] == 'failed'
    assert not any(_edits_dir(box).rglob('*.txt')), '失败的作业也要清理一次性稿'


def test_a_draft_edit_for_a_chapter_outside_the_plan_fails_loudly(client, box):
    """章号不在计划里：CLI `TerminalState` ⇒ failed，日志说得出为什么。"""
    r = client.post(f'/api/novels/{box.sid}/review/999/edit',
                    json={'mode': 'deep', 'text': '不存在的章。'})
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    log = client.get(f'/api/jobs/{r.json()["job_id"]}/log').json()['text']
    assert '999' in log, '日志里连章号都没有——人无从知道错在哪'


@pytest.mark.parametrize('body,why', [
    ({'mode': 'deep', 'text': ''}, '空稿不是一次修改'),
    ({'mode': 'weird', 'text': '正文'}, 'mode 取值面外'),
    ({'text': '正文'}, '缺 mode'),
])
def test_an_illegal_edit_body_is_422_from_the_carrier(client, box, body, why):
    r = client.post(f'/api/novels/{box.sid}/review/1/edit', json=body)
    assert r.status_code == 422, why


# ───────────────────────── 二、文案定稿 ─────────────────────────

def test_picking_a_hook_candidate_approves_and_stamps_the_gate(client, box):
    """⭐ `--pick 1` 后：`approved` 指向第 1 条候选、`s6` 审批章盖上。

    ⚠ 退出码契约：`0/1` 都是「定稿**落了**」（`1` 只是报告有红行——迁移语料上
    常态如此），`2/3` 才是没跑成。**盖章看落没落，红不红归 check 屏。**
    """
    _stamp_chain(box, 's5')
    before = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json={'pick': 1})
    assert r.status_code == 200, r.text[:300]
    assert r.json()['exit_code'] in (0, 1), r.text[:300]

    after = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    assert after['approved'] is not None
    assert after['approved']['approach'] == before['candidates'][0]['approach']
    assert after['approved']['origin'] == 'human'
    doc = client.get(f'/api/novels/{box.sid}/approvals').json()
    assert doc['gate']['s6'] is True


def test_an_approve_before_the_chain_still_lands_in_the_artifact(client, box):
    """⭐ 审批链没到位（中途接手的书）⇒ 定稿**照样落**（产物是真相），
    检查单那一格保持未盖——**不把检查单的顺序翻成定稿的失败**。"""
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json={'pick': 1})
    assert r.status_code == 200, r.text[:300]
    assert r.json()['exit_code'] in (0, 1)
    after = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    assert after['approved'] is not None, '定稿没落——链只是检查单，不该拦产物'
    doc = client.get(f'/api/novels/{box.sid}/approvals').json()
    assert doc['gate']['s6'] is False, '链没到位却盖了章——前置约束被绕过了'


def test_an_out_of_range_pick_fails_without_stamping(client, box):
    """越界 pick = 显式终态（exit 2）——**没跑成**，一个脚印都不留。"""
    _stamp_chain(box, 's5')
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json={'pick': 99})
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2, '越界 pick 该是「没跑成」，不是「跑完了判定红」'
    doc = client.get(f'/api/novels/{box.sid}/approvals').json()
    assert doc['gate']['s6'] is False, '没跑成的定稿不许盖章'


def test_a_corrupt_hook_makes_the_approve_job_fail(client, box):
    """产物损坏：CLI 读不回 hook.json ⇒ failed（不是 5xx，也绝不盖章）。"""
    _stamp_chain(box, 's5')
    (box.dir / 'stage6' / 'hook.json').write_text('这不是 JSON', encoding='utf-8')
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json={'pick': 1})
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s6'] is False


def test_a_zero_pick_is_422_from_the_carrier(client, box):
    assert client.post(f'/api/novels/{box.sid}/stage6/approve',
                       json={'pick': 0}).status_code == 422


# ───────────────────────── 三、人名定稿 ─────────────────────────

def test_approving_the_name_map_stamps_s7(client, box):
    """定稿落了（exit 0/1）就盖 `s7`——迁移语料的 `rn_txt_stale` 硬红是常态，
    人定稿的语义正是「看过红行之后拍板」。"""
    _stamp_chain(box, 's6')
    r = client.post(f'/api/novels/{box.sid}/stage7/approve', json={})
    assert r.status_code == 200, r.text[:300]
    assert r.json()['exit_code'] in (0, 1), r.text[:300]
    after = json.loads((box.dir / 'stage7' / 'name_map.json').read_text(encoding='utf-8'))
    assert after['approved'] == {'origin': 'human'}
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is True


def test_approving_names_without_a_name_map_fails(client, box):
    _stamp_chain(box, 's6')
    (box.dir / 'stage7' / 'name_map.json').unlink()
    r = client.post(f'/api/novels/{box.sid}/stage7/approve', json={})
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is False
