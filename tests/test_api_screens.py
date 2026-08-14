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

#: 九个读屏一次全要的产物（stage7 的 rows 与 stage5 屏还要成稿与验收）。
INPUTS = ('stage1/main_text.json', 'stage1/hook_copy.json',
          'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
          'stage3/style_grounding.json',
          'stage4/plan_chapters.json', 'stage4/writer_context.json',
          'stage6/hook.json', 'stage7/name_map.json')


def books() -> list[str]:
    """有 name_map **且 plan 迁移后可读**的书（`成年人不做选择` 那本的 plan
    没有 `_provenance`，是「exists 而 readable=False」的真实样本，单独一条测它）。"""
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


@pytest.fixture
def box():
    book = books()[0]
    s = sandbox.Sandbox(book, INPUTS + _stage5_rels(book))
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
