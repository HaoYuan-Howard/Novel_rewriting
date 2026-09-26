#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_views —— F7d 候选链只读监控面：views 纯函数与 HTTP 路由（免费）。

【验证边界】fake transport 全链造一个合成候选根，逐个监控面核对盘面事实：
Characters v4、Blueprint v12、graph／binding（Hook 与导演说明）、WriterBookIR
导航、章正文与章回执（机械范围／语义未评／篇幅披露）、Hook 与回执、NameMap v5、
成书 manifest 与下载、Run 节点图与 ledger 成本。半程候选根（--through stage4）
的 present=false 与 unknown 分开；不存在的候选根与非法候选名在 HTTP 上是 404。

【纪律对账（与现役 api 同构）】响应模型不抄字面量取值面、不带判定字段
（badge／passed／counts）、契约层不 import web 栈；监控读口不写产物盘。
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


def _candidate_root(tmp_path, wires, *, through='stage7') -> Path:
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
                 length_band=(0.01, 0.05))
    init_or_resume_candidate_workflow(
        repository=repo, base=root, code='code-test', root_sha1='a' * 40,
        source_identity='b' * 64, task_id='task-1', configured_model='fake',
        created_at=1759000000)
    return tmp_path


@pytest.fixture
def base(tmp_path):
    return _candidate_root(tmp_path, _full_chain_wires())


# ─────────────────────────────── views 纯函数 ───────────────────────────────

def test_views_cover_the_whole_delivered_chain(base):
    name = 'cand'
    chars = views.characters_view(base, name)
    assert chars.present and chars.contract_version == 'str06-rc3'
    assert {c.char_id for c in chars.characters} == {'fu_jun', 'yun_niang'}
    assert chars.name_structure is not None
    assert chars.name_structure.families == {'fam_lu': '陆', 'fam_su': '苏'}

    bp = views.blueprint_view(base, name)
    assert bp.present and [c.chapter for c in bp.chapters] == [1, 2]
    assert bp.chapters[0].beats[0].event.startswith('苏芸娘嫁入陆家')

    design = views.design_view(base, name)
    assert design.present and design.graph_id
    assert [s.scene_id for s in design.scenes] == ['s01', 's02']
    assert design.scenes[0].realization_brief, '导演说明原样进监控面'
    assert [c.chapter_ordinal for c in design.chapters] == [1, 2]
    assert design.hook is not None and design.hook.person == 'first'
    assert list(design.hook.materials) == ['s02#bt1', 's01#bt2'], '按设计序（乱序冷开合法）'
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
    assert len(run.nodes) == 11 and all(n.produced for n in run.nodes), '全链后每个节点都有产物'
    cost = run.cost
    assert cost.present and cost.new_paid_rows == 12 and cost.cached_rows == 0
    assert cost.prompt_tokens is not None and cost.unknown_cost_rows == 0
    assert cost.per_node, '成本按候选节点归属'


def test_half_run_candidate_keeps_present_and_missing_apart(tmp_path):
    """半程候选根：未跑的腿 present=false（不是错误、不是空数组冒充合法空值）。"""
    base = _candidate_root(tmp_path, _full_chain_wires(), through='stage4')
    run = views.run_view(base, 'cand')
    assert run.status == 'planned_validated' and run.accepted_body is False
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
