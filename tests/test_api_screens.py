#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_screens.py —— `/api/novels/:sid/*` 读屏路由的**载体与错误形状**
（BE-03 的四类：成功 / 上游未运行 / 产物损坏 / 参数不合法）。

【与其他 API 测试的分界】新链候选监控面归 `tests/test_str06_views.py`
（ Characters/Blueprint/Design/IR/chapters/Hook/Naming/Delivery/book 的
200/404 与零写断言）；surface 精确清单归 `tests/test_api_http.py`；
本份只问**现役 novel 域读屏**的状态码与错误体 `kind`。

⚠ **F8b-3 载荷切换**：旧链读屏（stage2/global、stage2/characters、
reading/:o、stage4/:o、stage5/:o、review/:o、stage7 与 inbox）随旧链整层退役，
其路由已删——本文件改钉现役读屏（stage0/report、stage0/segments、stage1、
check/:kind、run、run/metrics、operator-next）与 str06 章级读屏的 `:ordinal`
类型闸；旧屏的响应体形状随路由一起退役，不在这份文件里留第二套描述。

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from novel.api.app import UI_HEADER, create_app    # noqa: E402
from tests import corpus, sandbox                  # noqa: E402
from tests.markers import corpus_params             # noqa: E402

#: 现役读屏一次全要的产物（stage0 revision 链 + Stage1 章数组 + clean 正文）。
INPUTS = ('stage1/main_text.json', 'stage0/revisions.json',
          'stage0/clean_text.txt')


def books() -> list[str]:
    """同时具备 Stage1 章数组与 stage0 revision 链的书。"""
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage1/main_text.json'}):
        if sandbox.cut(book, 'stage0/revisions.json') is not None:
            out.append(book)
    return out


@pytest.fixture(params=corpus_params(
    books()[:1], reason='盘上没有读屏语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    book = request.param
    s = sandbox.Sandbox(book, INPUTS)
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


@pytest.fixture
def bare_client(tmp_path):
    """Empty synthetic output root for carrier/error-shape contracts."""
    with TestClient(create_app(tmp_path)) as c:
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


#: 读屏 → 它背后的产物（缺席 ⇒ 404 的那批）。
#:
#: ⚠ `run`／`run/metrics` 不在此表：信标折叠对「没有这本书」也有诚实读数
#: （空轨迹），它们的形状由 `tests/test_work_unit_progress_http.py` 钉。
#: ⚠ `check/:kind` 不在此表：它的取值面只有 stage0/stage1，产物缺席形态
#: 与 stage0/1 读屏同族（404/409），但请求一个不在取值面的层名是 400
#: （见 `test_api_http.py::test_new_chain_stages_have_no_layer_check_kind`）。
SCREENS = {
    'stage1': 'stage1/main_text.json',
    'stage0/report': 'stage0/revisions.json',
}

#: 新链章级读屏的 `:ordinal` 类型闸（载体层 422）。
CHAPTER_ORDINAL_SCREEN = '/api/str06/candidates/probe/chapters/{ordinal}'


# ───────────────────────── 一、成功 200 ─────────────────────────

def test_every_screen_answers_200_on_a_complete_book(client, box):
    urls = [f'/api/novels/{box.sid}/{tail}' for tail in (*SCREENS, 'stage0/segments')]
    for url in urls:
        r = client.get(url)
        assert r.status_code == 200, f'{url} → {r.status_code} {r.text[:200]}'


def test_the_underscore_provenance_key_survives_the_wire(client, box):
    """`by_alias` 一旦被关，`_provenance` 静悄悄变 `provenance`——前端拿到 undefined。"""
    for tail in SCREENS:
        body = client.get(f'/api/novels/{box.sid}/{tail}').json()
        assert '_provenance' in body and 'provenance' not in body, tail


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


def test_an_unknown_book_is_404_on_every_screen(bare_client):
    """敲错 sid 在每一条现役读屏上都是**带类型的 404**，不是一个裸 404。

    ⚠ `run`／`run/metrics` 刻意不在此列：信标折叠对不存在的书也有诚实的
    空读数（200），把「没有轨迹」画成 404 会让监控页把没跑过的书显示成错误。
    """
    for tail in ('stage1', 'stage0/report', 'stage0/segments',
                 'check/stage0', 'operator-next'):
        r = bare_client.get(f'/api/novels/9999999999999999999/{tail}')
        assert r.status_code == 404, tail
        assert r.json()['kind'] == 'UpstreamMissing', tail


# ───────────────────────── 三、产物损坏 ⇒ 409 ─────────────────────────

@pytest.mark.parametrize('tail', sorted(SCREENS))
def test_a_corrupt_artifact_is_409_not_404(client, box, tail):
    """「读不回来」与「没跑过」对人的下一步是**相反**的指示，不许折成一态。"""
    (box.dir / SCREENS[tail]).write_text('这不是 JSON', encoding='utf-8')
    r = client.get(f'/api/novels/{box.sid}/{tail}')
    assert r.status_code == 409, r.text[:200]
    assert r.json()['kind'] == 'ArtifactUnreadable'


# ───────────────────────── 四、参数不合法 ⇒ 400 / 422 ─────────────────────────

@pytest.mark.parametrize('sid', ['C:', 'a b ', 'x%5Cy'])
def test_an_illegal_sid_is_400_because_the_client_sent_it(bare_client, sid):
    """`paths.check_sid` 挡的每一种走法都是一个任意目录读写入口，产地是客户端 ⇒ 400。

    ⚠ F8b-3：现役带 sid 的读屏面是 stage0/report、stage0/segments、stage1、
    check/:kind、run、run/metrics、operator-next（旧链 stage2/4/5/7 读屏与
    inbox 已随旧链退役）。
    """
    for tail in ('stage1', 'stage0/report', 'check/stage0'):
        r = bare_client.get(f'/api/novels/{sid}/{tail}')
        assert r.status_code == 400, (sid, tail)
        assert r.json()['kind'] == 'BadRequest'


def test_a_non_integer_ordinal_is_422_from_the_carrier(bare_client):
    """`:ordinal` 的类型闸在 FastAPI 载体层——不是 404，也绝不是 500。

    （F8b-3：章级读屏随旧链退役；新链章级读面是
    `/api/str06/candidates/:candidate/chapters/:ordinal`，同一道载体闸。）
    """
    r = bare_client.get(CHAPTER_ORDINAL_SCREEN.format(ordinal='abc'))
    assert r.status_code == 422


# ───────────────────────── 五、`R-11.3`：一个字节都不写 ─────────────────────────

def test_the_screens_write_nothing_at_all(client, box):
    """打完全部现役读屏 ⇒ 整棵树逐字节相同。"""
    before = _tree(box.root)
    for tail in (*SCREENS, 'stage0/segments', 'run', 'run/metrics'):
        client.get(f'/api/novels/{box.sid}/{tail}')
    assert _tree(box.root) == before, '读屏往产物盘上写了东西（`R-11.3`）'
    assert not (box.root / '_service').exists(), '只读端点建出了 `_service/`'
    assert len(before) >= 3, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
