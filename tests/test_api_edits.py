#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_edits.py —— 旧链人工写回路由族的退役守卫（F8b，STR-06 切换）。

旧链的 review 改稿、stage2 关系/蓝图/characters/global 编辑与 stage7 残留
采纳端点全部硬编码旧节点，随「上传即授权开跑、Stage 页只读监控」的全自动
单模式退役。这里守的是退役事实：载体不再接受任何写回请求体，405/404 是
合同的一部分；rc26（命名前移批次 C）起疑似旧名残留改由 `name_residue_report` 定位披露（不阻断），`naming_residual` 随零改名装配注销。

历史行为测试（成功/上游未运行/产物损坏/参数不合法四类）见 Git 历史；
其有效职责已由新链 `--check-only` 复检与机械验接管（F7c 迁移反例）。
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from novel.api.app import UI_HEADER, create_app    # noqa: E402


@pytest.fixture()
def validation_client(tmp_path):
    """只测 HTTP 载体 schema；不得借真语料把 422 回归排出默认套件。"""
    with TestClient(create_app(tmp_path)) as c:
        c.headers.update({UI_HEADER: '1'})
        yield c


_RETIRED_POSTS = (
    '/api/novels/422/review/1/edit',
    '/api/novels/422/review/1/edit/preview',
    '/api/novels/422/stage2/characters/edit',
    '/api/novels/422/stage7/approve',
    '/api/novels/422/stage7/adopt-residual',
)

_RETIRED_GETS = (
    '/api/novels/422/stage2/blueprint/edit/template',
    '/api/novels/422/stage2/global/edit/template',
)


@pytest.mark.parametrize('path', _RETIRED_POSTS)
def test_old_edit_post_endpoints_are_gone(validation_client, path):
    """POST 面已删：405/404 而不是静默接受 typed 请求体。"""
    assert validation_client.post(path, json={}).status_code in (404, 405)


@pytest.mark.parametrize('path', _RETIRED_GETS)
def test_old_edit_get_endpoints_are_gone(validation_client, path):
    assert validation_client.get(path).status_code in (404, 405)
