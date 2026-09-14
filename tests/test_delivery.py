"""报告退役后的交付消费缝：真实重拼、盘面封印和 HTTP 下载。

上游质量与批准前置在本文件用已通过的输入替身隔离；各前置的红/绿对照由
test_stage7_delivery_gate 等生产入口测试覆盖。这里不把替身当作整书质量验收。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from novel import paths, prov

from novel.orchestrator import mechanical_admission as admission
from novel.api.app import create_app
from novel.errors import ArtifactUnreadable, BlockingFindingsUnresolved, UpstreamStale
from novel.orchestrator import delivery
from novel.stages.stage7 import assemble
from novel.work_units import initialize_workflow


@pytest.fixture
def current(tmp_path, monkeypatch):
    initialize_workflow(tmp_path)
    book = tmp_path / '交付测试_982'
    target = book / paths.book_txt_rel(book)
    target.parent.mkdir(parents=True)
    up = SimpleNamespace(hook_text='张明收到了信。', recs=[
        {'output_ordinal': 1, 'text': '张明打开木门。'},
        {'output_ordinal': 2, 'text': '张明把信放在桌上。'},
    ])
    subs = {'张明': '李青'}
    hook, _, _ = assemble.apply_subs(up.hook_text, subs, kept=[])
    expected = assemble.assemble_txt(hook, up.recs, subs, [])
    target.write_bytes(expected.encode('utf-8'))
    art = SimpleNamespace(subs=subs, kept=[], book_txt=SimpleNamespace(
        chars=len(expected), chapters=2))
    (book / assemble.REL).write_text('{}', encoding='utf-8')
    # Stage2 语义审阅退役：交付前置由统一机械准入驱动。
    # gate_ok=False 模拟准入阻断；semantic=True 模拟「机械可用但语义未审」披露。
    live = SimpleNamespace(code='delivery-test', items=[],
                           gate_ok=True, semantic=True)
    def fake_stage4_ready(base, sid):
        if not live.gate_ok:
            return admission.AdmissionCheck(
                'stage4', False, blocking=('准入替身：Stage2 必要产物不可用',))
        return admission.AdmissionCheck(
            'stage4', True,
            semantic_unreviewed=live.semantic,
            disclosures=(admission.SEMANTIC_UNREVIEWED_NOTE,) if live.semantic else ())
    monkeypatch.setattr(admission, 'stage4_ready', fake_stage4_ready)

    class BookStore:
        def __init__(self, ctx):
            self.ctx = ctx

        def exists(self, rel):
            return (book / rel).is_file()

        def read_own(self, *_a, **_k):
            return art, b'{}'

        def freshness(self, _rel):
            return SimpleNamespace(state='fresh', reason='隔离前置来源检查')

        def read_text(self, rel):
            return (book / rel).read_bytes().decode('utf-8')

    monkeypatch.setattr(prov, 'code_version', lambda: live.code)
    monkeypatch.setattr(delivery, 'ArtifactStore', BookStore)
    monkeypatch.setattr(assemble, 'ArtifactStore', BookStore)
    monkeypatch.setattr(assemble, '_Upstreams', lambda *_a: up)
    monkeypatch.setattr(assemble, 'require_stage2_delivery_approved', lambda *_a: None)
    monkeypatch.setattr(assemble, '_require_mapping_current', lambda *_a: None)
    monkeypatch.setattr(delivery, 'inbox_view', lambda *_a: SimpleNamespace(items=live.items))
    return SimpleNamespace(base=tmp_path, sid='982', book=book, target=target,
                           expected=expected, live=live)


@pytest.mark.parametrize('report', [None, b'', b'# obsolete\nready', b'\xff\xfeinvalid'])
def test_current_delivery_and_http_do_not_read_or_generate_a_markdown_report(current, report):
    c = current
    if report is not None:
        (c.book / '交付测试_review.md').write_bytes(report)
    before = {p.relative_to(c.base): p.read_bytes() for p in c.base.rglob('*') if p.is_file()}
    # 默认替身带语义未审披露：成书可交付，但终验如实报 ready_with_disclosures。
    outcome = delivery.assess(c.base, c.sid, start_code_version=c.live.code)
    assert outcome.state == 'ready_with_disclosures'
    assert 'Stage2 语义未审' in outcome.reason
    for _ in range(2):
        with TestClient(create_app(c.base)) as api:
            response = api.get(f'/api/novels/{c.sid}/stage7/book.txt?download=true')
            assert response.status_code == 200, response.text
            assert response.content == c.expected.encode('utf-8')
            assert 'attachment;' in response.headers['content-disposition']
    assert {p.relative_to(c.base): p.read_bytes() for p in c.base.rglob('*') if p.is_file()} == before


def test_same_length_corruption_is_refused_even_when_a_size_or_freshness_account_claims_current(current):
    c = current
    damaged = c.expected.replace('木门', '城门')
    assert len(damaged) == len(c.expected) and damaged != c.expected
    c.target.write_bytes(damaged.encode('utf-8'))
    with pytest.raises(UpstreamStale, match='确定性重建'):
        delivery.read_deliverable_book(c.base, c.sid)
    assert delivery.assess(c.base, c.sid, start_code_version=c.live.code).state == 'stopped'
    with TestClient(create_app(c.base)) as api:
        response = api.get(f'/api/novels/{c.sid}/stage7/book.txt')
        assert response.status_code == 409
        assert 'stage7.assemble' in response.json()['hint']
    c.target.write_bytes(c.expected.encode('utf-8'))
    assert delivery.read_deliverable_book(c.base, c.sid) == c.expected


@pytest.mark.parametrize('change', ['book', 'upstream', 'code'])
@pytest.mark.parametrize('entry', ['assessment', 'download'])
def test_current_surface_changes_during_delivery_cannot_return_ready(current, monkeypatch, change, entry):
    c = current
    original = delivery._read_delivery

    def racing_read(*args):
        result = original(*args)
        if change == 'book':
            c.target.write_bytes(c.expected.replace('木门', '城门').encode('utf-8'))
        elif change == 'upstream':
            path = c.book / 'stage2' / 'global_grounding.json'
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'{"changed":true}')
        else:
            c.live.code = 'another-code'
        return result

    monkeypatch.setattr(delivery, '_read_delivery', racing_read)
    if entry == 'download':
        with pytest.raises(ArtifactUnreadable, match='校验期间'):
            delivery.read_deliverable_book(c.base, c.sid)
    else:
        outcome = delivery.assess(c.base, c.sid, start_code_version=c.live.code)
        assert outcome.state == 'stopped' and '校验期间' in outcome.reason


@pytest.mark.parametrize('blocker', ['admission', 'inbox'])
def test_existing_book_and_old_ready_report_cannot_override_active_blockers(current, blocker):
    c = current
    (c.book / '交付测试_review.md').write_text('所有项目 PASS，可以交付', encoding='utf-8')
    if blocker == 'admission':
        c.live.gate_ok = False
    else:
        c.live.items = [SimpleNamespace(delivery_effect='blocking', stage='stage5',
                                       kind='closure', rule='typed-review')]
    assert delivery.assess(c.base, c.sid, start_code_version=c.live.code).state == 'stopped'
    with pytest.raises(BlockingFindingsUnresolved):
        delivery.read_deliverable_book(c.base, c.sid)


def test_nonblocking_disclosure_remains_downloadable(current):
    c = current
    c.live.semantic = False
    c.live.items = [SimpleNamespace(delivery_effect='disclosure')]
    assert delivery.assess(c.base, c.sid, start_code_version=c.live.code).state == 'ready_with_disclosures'
    assert delivery.read_deliverable_book(c.base, c.sid) == c.expected
