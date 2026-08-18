#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_screens.py —— 第二阶段读屏路由的**行为**（BE-03 的四类：
成功 / 上游未运行 / 产物损坏 / 参数不合法）。

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
from tests.markers import corpus_params             # noqa: E402

#: 九个读屏一次全要的产物（stage7 的 rows 与 stage5 屏还要成稿与验收）。
INPUTS = ('stage1/main_text.json', 'stage1/hook_copy.json',
          'stage1/partition_report.json',
          'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
          'stage3/style_grounding.json',
          'stage4/plan_chapters.json', 'stage4/writer_context.json',
          'stage6/hook.json', 'stage7/name_map.json')


def books() -> list[str]:
    """同时具备当前 name_map 与 plan 的书。"""
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage7/name_map.json'}):
        plan = sandbox.cut(book, 'stage4/plan_chapters.json')
        if plan is not None:
            out.append(book)
    return out


def _stage5_rels(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture(params=corpus_params(
    books()[:1], reason='盘上没有读屏语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    book = request.param
    s = sandbox.Sandbox(book, INPUTS + _stage5_rels(book))
    yield s
    s.close()


@pytest.fixture
def current_map(box):
    """给**要经交付闸**的读屏用例用：先声明「`stage7/name_map.json` 相对上游是当前的」。

    ⚠ 三本书当前都不满足：`stage7_context` 投影的是 `stage4/writer_context.json` 的
    `_provenance.code`（`novel/consumption.py:330-334`），上游一次内容一字未改的
    0 成本重跑就把三本的映射全判 stale。交付闸因此正确地拒绝拿非 fresh 的映射交书
    （`TerminalState: 旧 book.txt 不再绑定当前批准映射`），而那是**产品在正常工作**
    ——用例要验的是「读屏不绕过这道闸」，它需要一份能过闸的映射当输入。
    ⚠ 刻意不提到 `box` 上：本文件另外 23 条读屏用例不经交付闸，照跑。
    """
    corpus.requires_current(box.book, 'stage7/name_map.json',
                            produced_by='stage7.rename --recheck', cost='0 成本')
    return box


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


#: 读屏 → 它的产物（缺席 ⇒ 404 的那批）。`inbox` 不在此表：它没有「自己的产物」，
#: 缺哪层跳哪层。`reading`/`stage4`/`stage5` 也不在：它们对「stage2/4 没跑」
#: 各有自己的 200 形态（下面各有专条）。
SCREENS = {
    'stage2/global': 'stage2/global_grounding.json',
    'stage6': 'stage6/hook.json',
    'stage7': 'stage7/name_map.json',
}

#: 章级读屏（`:o` 取 1——沙箱书从第 1 章起必有）。
CHAPTER_SCREENS = ('reading/1', 'stage4/1', 'stage5/1', 'review/1')


# ───────────────────────── 一、成功 200 ─────────────────────────

def test_every_screen_answers_200_on_a_complete_book(client, box):
    urls = [f'/api/novels/{box.sid}/{tail}'
            for tail in (*SCREENS, 'inbox', *CHAPTER_SCREENS)]
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


def test_stage4_index_carries_every_chapter_goal_in_one_response(client, box):
    body = client.get(f'/api/novels/{box.sid}/stage4/1').json()
    assert len(body['index']) == len(body['chapters'])
    assert all(row['chapter_goal'] for row in body['index'])


def test_stage7_book_can_be_previewed_and_downloaded(client, box, monkeypatch):
    from novel.stages.stage7 import assemble
    monkeypatch.setattr(
        assemble, 'read_current_book', lambda _ctx: '第一章\n成书正文')

    preview = client.get(f'/api/novels/{box.sid}/stage7/book.txt')
    assert preview.status_code == 200 and preview.text == '第一章\n成书正文'
    assert 'content-disposition' not in preview.headers

    download = client.get(f'/api/novels/{box.sid}/stage7/book.txt?download=true')
    assert download.status_code == 200
    assert download.headers['content-disposition'] == 'attachment; filename="book.txt"'


def test_stage7_book_endpoint_never_bypasses_the_current_delivery_gate(
        client, box, monkeypatch):
    """⭐ 交付闸的每一条拒绝都要以**具名 4xx** 到达客户端。

    ⚠⚠ **此前这里造的是一个裸 `TerminalState`，而那条断言从来没成立过**：
    `status_of` 对两个抽象基类刻意不给格（「加一个类就得决定一次」），认不出就原样抛
    ⇒ 那条异常一路冒到 Starlette，客户端拿到的是 500 而不是 409。
    守卫**替生产伪造了一种它不该产生的失败形状**，于是它红着的其实是
    `novel/errors.py` 开头那条「不设默认异常」——不是端点漏了闸。
    ⇒ 现在逐条走 `read_current_book` **真会抛的那几个类**。
    """
    from novel.errors import (
        ApprovalRequired, BlockingFindingsUnresolved, GateBlocked, UpstreamMissing,
        UpstreamStale, UpstreamUnproven,
    )
    from novel.stages.stage7 import assemble

    cases = [
        (ApprovalRequired('人物映射还没定稿'), 409, None),
        (UpstreamStale('name_map.json 已过期'), 409, None),
        (UpstreamUnproven('name_map.json 证明不了自己出自当前根'), 409, None),
        (BlockingFindingsUnresolved('仍有阻断级问题', rules=['rn_ambiguous']), 409, None),
        (GateBlocked('有一份没裁决的废稿', invalid_path='stage7/name_map.INVALID.json'),
         409, 'stage7/name_map.INVALID.json'),
        (UpstreamMissing('book.txt 不在盘上'), 404, None),
    ]
    for exc, code, missing in cases:
        monkeypatch.setattr(
            assemble, 'read_current_book',
            lambda _ctx, _exc=exc: (_ for _ in ()).throw(_exc))
        response = client.get(f'/api/novels/{box.sid}/stage7/book.txt?download=true')
        body = response.json()
        assert response.status_code == code, (type(exc).__name__, body)
        assert body['kind'] == type(exc).__name__
        # `GateBlocked` 的全部价值：那份被拦下的稿在哪，必须到得了客户端。
        assert body.get('missing') == missing, type(exc).__name__


@pytest.mark.usefixtures('current_map')
def test_a_fully_written_book_survives_the_overview(client, box):
    """⭐ 回归锚：**全链跑完的书在语料库上不许整本消失。**

    第一本真跑完的书（e2e_20260813）曾把这条走红：stage5 的 `done=True`
    被 `StageState` 旧断言（「无产物清单 ⇒ done 只能是 None」）当场拒收，
    `/api/novels` 的逐本 try 把它静默跳过——**满盘产物的书在界面上不存在**。
    stage5 的完成度证据在计划 + 逐章成稿，载体看不见它，也就不许替它断言。
    """
    body = client.get(f'/api/novels/{box.sid}')
    assert body.status_code == 200, body.text[:300]
    st5 = next(s for s in body.json()['stages'] if s['stage'] == 'stage5')
    assert st5['done'] is True, (
        f'沙箱书每章都有稿而 done={st5["done"]}——逐章折叠多半又被载体拦了')
    stage1 = client.get(f'/api/novels/{box.sid}/stage1/chapters').json()
    assert body.json()['chapters'] == len(stage1['chapters'])
    novels = client.get('/api/novels').json()['novels']
    assert any(n['sid'] == box.sid for n in novels), '语料库把这本书整本吞了'


def test_the_inbox_counts_are_dense(client, box):
    """八键全出（0 也要出）——稀疏计数会把「没有」与「0」混掉。"""
    body = client.get(f'/api/novels/{box.sid}/inbox').json()
    assert set(body['counts']) == {'invalid', 'rejected', 'stalled',
                                   'repairable', 'human_only', 'verification',
                                   'upstream', 'approval'}
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


def test_an_unknown_book_is_404_on_every_screen(bare_client):
    for tail in (*SCREENS, 'inbox'):
        r = bare_client.get(f'/api/novels/9999999999999999999/{tail}')
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


# ───────────────────────── 二b、章级读屏的「还没跑」形态 ─────────────────────────

def test_reading_without_a_blueprint_is_200_with_one_hole(client, box):
    """⭐ stage2 没跑 ⇒ 200 且 `blueprint=false`：**整章一个 hole、beats 空**。

    左半边是 stage1 的原文，与 stage2 无关（S-06 左右半分治的同款道理）。
    """
    (box.dir / 'stage2' / 'chapter_blueprints.json').unlink()
    r = client.get(f'/api/novels/{box.sid}/reading/1')
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body['blueprint'] is False and body['beats'] == []
    assert len(body['segments']) == 1 and body['segments'][0]['beat_id'] is None
    assert body['loose'] == body['text_chars']
    assert body['blueprint_state']['exists'] is False


def test_stage4_without_a_plan_is_200_identity_baseline(client, box):
    """⭐ `04_delivery.md:133`：没跑过 stage4 时 S-07 **也有内容**——恒等基线。"""
    (box.dir / 'stage4' / 'plan_chapters.json').unlink()
    r = client.get(f'/api/novels/{box.sid}/stage4/1')
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body['identity_plan'] is True
    assert body['plan_chapter']['beats'], '恒等基线也要有拍（写手实际会拿到的形状）'
    assert body['ctx_chapter'] is not None and body['writer_target'] is not None
    assert body['_provenance'] is None, '现算的基线没有落盘来源账可发'
    assert body['plan_state']['exists'] is False


def test_stage5_without_a_ctx_shows_no_fake_materials(client, box):
    """⭐ `04_delivery.md:134`：`ctx_exists=false` ⇒ 给「先跑 stage4」，**不显示假材料**。"""
    (box.dir / 'stage4' / 'writer_context.json').unlink()
    r = client.get(f'/api/novels/{box.sid}/stage5/1')
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body['ctx_exists'] is False and body['materials'] is None
    assert body['drafts'], '材料缺席不该带走成稿那一侧'


def test_a_chapter_without_a_draft_is_200_with_empty_drafts(client, box):
    """某章两臂皆无稿 ⇒ 200 空 drafts（「还没写」不是错误）。删掉第 1 章的稿来制造它。"""
    for mode in ('deep', 'shallow'):
        p = box.dir / 'stage5' / mode / 'ch01.json'
        if p.exists():
            p.unlink()
    got = client.get(f'/api/novels/{box.sid}/stage5/1')
    assert got.status_code == 200, got.text[:200]
    body = got.json()
    assert body['drafts'] == [], '稿删了还有卡片——是从哪儿来的？'
    assert body['materials'] is not None, '材料先于成稿存在，不该跟着稿一起消失'
    assert 1 in body['missing']


def test_the_review_screen_serves_dense_counts(client, box):
    """`finding_counts` 四 sev 键恒在（0 也写）——产物那格是稀疏 Counter，出线侧修掉。"""
    r = client.get(f'/api/novels/{box.sid}/review/1')
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert set(body['finding_counts']) == {'hard', 'info', 'metric', 'unver'}
    for row in body['index']:
        assert set(row['finding_counts']) == {'hard', 'info', 'metric', 'unver'}


def test_review_without_any_review_is_404(client, box):
    for p in (box.dir / 'stage5').glob('review_*.json'):
        p.unlink()
    r = client.get(f'/api/novels/{box.sid}/review/1')
    assert r.status_code == 404 and r.json()['kind'] == 'UpstreamMissing'


def test_a_chapter_outside_the_book_is_404_on_every_chapter_screen(client, box):
    for tail in ('reading/999', 'stage4/999', 'stage5/999', 'review/999'):
        r = client.get(f'/api/novels/{box.sid}/{tail}')
        assert r.status_code == 404, tail
        assert r.json()['kind'] == 'UpstreamMissing'


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
def test_an_illegal_sid_is_400_because_the_client_sent_it(bare_client, sid):
    """`paths.check_sid` 挡的每一种走法都是一个任意目录读写入口，产地是客户端 ⇒ 400。"""
    for tail in ('stage2/global', 'stage6', 'stage7', 'inbox'):
        r = bare_client.get(f'/api/novels/{sid}/{tail}')
        assert r.status_code == 400, (sid, tail)
        assert r.json()['kind'] == 'BadRequest'


def test_a_non_integer_ordinal_is_422_from_the_carrier(bare_client):
    """`:o` 的类型闸在 FastAPI 载体层——不是 404，也绝不是 500。"""
    assert bare_client.get('/api/novels/123/stage7/abc').status_code == 422


# ───────────────────────── 五、`R-11.3`：一个字节都不写 ─────────────────────────

def test_the_screens_write_nothing_at_all(client, box):
    """打完全部九个读屏（含逐章对照、恒等基线现算与 inbox 的 0 成本复检）
    ⇒ 整棵树逐字节相同。恒等基线**现算不落盘**这一条也在这里一起钉住
    （删掉 plan 打 stage4 屏，`run_identity` 那条路是会写盘的，view 不许）。"""
    before = _tree(box.root)
    for tail in (*SCREENS, 'inbox', *CHAPTER_SCREENS):
        client.get(f'/api/novels/{box.sid}/{tail}')
    body7 = client.get(f'/api/novels/{box.sid}/stage7').json()
    client.get(f'/api/novels/{box.sid}/stage7/{body7["chapters"][0]}')
    assert _tree(box.root) == before, '读屏往产物盘上写了东西（`R-11.3`）'
    assert not (box.root / '_service').exists(), '只读端点建出了 `_service/`'
    assert len(before) >= 6, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'


def test_the_identity_baseline_writes_nothing(client, box):
    """`--identity` 那条 CLI 路**会落盘**（它要留痕）；view 的恒等基线**不许**——
    读屏是问句不是动作。"""
    (box.dir / 'stage4' / 'plan_chapters.json').unlink()
    before = _tree(box.root)
    assert client.get(f'/api/novels/{box.sid}/stage4/1').status_code == 200
    assert _tree(box.root) == before, '恒等基线把东西写到盘上了'
