#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_screens.py —— 第二阶段读屏路由的**行为**（BE-03 的四类：
成功 / 上游未运行 / 产物损坏 / 参数不合法）。

【「未实现 ⇒ 501」那一格为什么不在】八层已全部移植（`PORTED_STAGES` 满员），
501 只属于「这条**跑法**还没接线」（`RunModeNotImplemented`）——读屏没有跑法，
没有一条读路会走到它。硬凑一个 501 用例等于给一个不存在的状态发绿灯。

【与其他 API 测试的分界】组装细节归 `test_views_stage2/6/7.py`；
surface 精确清单归 `test_api_http.py`；本份只问状态码与错误体的 `kind`。

⚠ 沙箱一律 `tempfile.mkdtemp()`；**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from novel.api.app import UI_HEADER, create_app    # noqa: E402
from tests import corpus, sandbox                  # noqa: E402

pytestmark = pytest.mark.skipif(corpus.corpus_root() is None,
                                reason='盘上没有语料——这一批**没在查**，不是「查过没事」')

#: 四个书级读屏一次全要的产物（stage7 的 rows 还要 stage5 成稿）。
INPUTS = ('stage1/main_text.json', 'stage1/hook_copy.json',
          'stage2/global_grounding.json', 'stage3/style_grounding.json',
          'stage4/writer_context.json', 'stage6/hook.json',
          'stage7/name_map.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage7/name_map.json'})


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture
def box():
    book = books()[0]
    s = sandbox.Sandbox(book, INPUTS + _drafts_of(book))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """把钥匙拿走：任何一条走到真发请求的路都会当场现形。"""
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


@pytest.fixture
def client(box):
    with TestClient(create_app(box.root)) as c:
        c.headers.update({UI_HEADER: '1'})
        yield c


def _tree(root) -> set:
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out.add((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return out


#: 读屏 → 它的产物。`inbox` 不在此表：它没有「自己的产物」，缺哪层跳哪层。
SCREENS = {
    'stage2/global': 'stage2/global_grounding.json',
    'stage6': 'stage6/hook.json',
    'stage7': 'stage7/name_map.json',
}


# ───────────────────────── 一、成功 200 ─────────────────────────

def test_every_screen_answers_200_on_a_complete_book(client, box):
    urls = [f'/api/novels/{box.sid}/{tail}' for tail in (*SCREENS, 'inbox')]
    body7 = client.get(f'/api/novels/{box.sid}/stage7').json()
    urls.append(f'/api/novels/{box.sid}/stage7/{body7["chapters"][0]}')
    for url in urls:
        r = client.get(url)
        assert r.status_code == 200, f'{url} → {r.status_code} {r.text[:200]}'


def test_the_underscore_provenance_key_survives_the_wire(client, box):
    """`by_alias` 一旦被关，`_provenance` 静悄悄变 `provenance`——前端拿到 undefined。"""
    for tail in SCREENS:
        body = client.get(f'/api/novels/{box.sid}/{tail}').json()
        assert '_provenance' in body and 'provenance' not in body, tail


def test_stage7_chapter_block_is_all_or_nothing_over_the_wire(client, box):
    book_level = client.get(f'/api/novels/{box.sid}/stage7').json()
    assert book_level['ordinal'] is None and book_level['rows'] is None
    o = book_level['chapters'][0]
    chapter = client.get(f'/api/novels/{box.sid}/stage7/{o}').json()
    assert chapter['ordinal'] == o and chapter['rows']


def test_the_inbox_counts_are_dense(client, box):
    """六键全出（0 也要出）——稀疏计数会把「没有」与「0」混掉。"""
    body = client.get(f'/api/novels/{box.sid}/inbox').json()
    assert set(body['counts']) == {'invalid', 'rejected', 'stalled',
                                   'human_only', 'upstream', 'approval'}
    assert body['total'] == len(body['items'])


# ───────────────────────── 二、上游未运行 ⇒ 404 ─────────────────────────

@pytest.mark.parametrize('tail', sorted(SCREENS))
def test_a_missing_artifact_is_404_with_an_id_not_a_phrase(client, box, tail):
    """这些屏没有「左半边」：产物缺席 ⇒ 404 + `kind == 'UpstreamMissing'`。

    404 只表示**资源不存在**；`kind` 让前端把它渲染成「下一步点这里」（`U-17`），
    而不是一屏报错。
    """
    (box.dir / SCREENS[tail]).unlink()
    r = client.get(f'/api/novels/{box.sid}/{tail}')
    assert r.status_code == 404, r.text[:200]
    assert r.json()['kind'] == 'UpstreamMissing'


def test_an_ordinal_outside_the_judged_set_is_404(client, box):
    r = client.get(f'/api/novels/{box.sid}/stage7/999')
    assert r.status_code == 404 and r.json()['kind'] == 'UpstreamMissing'


def test_an_unknown_book_is_404_on_every_screen(client):
    for tail in (*SCREENS, 'inbox'):
        r = client.get(f'/api/novels/9999999999999999999/{tail}')
        assert r.status_code == 404, tail
        assert r.json()['kind'] == 'UpstreamMissing'


def test_the_inbox_skips_a_layer_it_cannot_read(client, box):
    """inbox 对缺席/读不回的层**跳过不编条目**——「查不了」不是「待办」。

    ⇒ 打坏一层不是 409，是**少了那一层条目的 200**。这一条是 inbox 与
    三个单产物屏在「产物损坏」这一格上的**刻意分叉**。
    """
    (box.dir / 'stage3' / 'style_grounding.json').write_text('废字节', encoding='utf-8')
    r = client.get(f'/api/novels/{box.sid}/inbox')
    assert r.status_code == 200, r.text[:200]


# ───────────────────────── 三、产物损坏 ⇒ 409 ─────────────────────────

@pytest.mark.parametrize('tail', sorted(SCREENS))
def test_a_corrupt_artifact_is_409_not_404(client, box, tail):
    """「读不回来」与「没跑过」对人的下一步是**相反**的指示，不许折成一态。"""
    (box.dir / SCREENS[tail]).write_text('这不是 JSON', encoding='utf-8')
    r = client.get(f'/api/novels/{box.sid}/{tail}')
    assert r.status_code == 409, r.text[:200]
    assert r.json()['kind'] == 'ArtifactUnreadable'


# ───────────────────────── 四、参数不合法 ⇒ 400 ─────────────────────────

@pytest.mark.parametrize('sid', ['C:', 'a b ', 'x%5Cy'])
def test_an_illegal_sid_is_400_because_the_client_sent_it(client, sid):
    """`paths.check_sid` 挡的每一种走法都是一个任意目录读写入口，产地是客户端 ⇒ 400。"""
    for tail in ('stage2/global', 'stage6', 'stage7', 'inbox'):
        r = client.get(f'/api/novels/{sid}/{tail}')
        assert r.status_code == 400, (sid, tail)
        assert r.json()['kind'] == 'BadRequest'


def test_a_non_integer_ordinal_is_422_from_the_carrier(client, box):
    """`:o` 的类型闸在 FastAPI 载体层——不是 404，也绝不是 500。"""
    assert client.get(f'/api/novels/{box.sid}/stage7/abc').status_code == 422


# ───────────────────────── 五、`R-11.3`：一个字节都不写 ─────────────────────────

def test_the_screens_write_nothing_at_all(client, box):
    """打完四个读屏（含逐章对照与 inbox 的 0 成本复检）⇒ 整棵树逐字节相同。"""
    before = _tree(box.root)
    for tail in (*SCREENS, 'inbox'):
        client.get(f'/api/novels/{box.sid}/{tail}')
    body7 = client.get(f'/api/novels/{box.sid}/stage7').json()
    client.get(f'/api/novels/{box.sid}/stage7/{body7["chapters"][0]}')
    assert _tree(box.root) == before, '读屏往产物盘上写了东西（`R-11.3`）'
    assert not (box.root / '_service').exists(), '只读端点建出了 `_service/`'
    assert len(before) >= 6, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
