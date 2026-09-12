"""Stage6 准备材料的独立只读面；Hook 尚不存在时也能核验与修订。"""
from __future__ import annotations

import os

from contracts.schema.api import Stage6PrepareView, Stage6PrepareWorkpack
from novel import paths, prov
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore


def prepare_view(base: str | os.PathLike[str], sid: str) -> Stage6PrepareView:
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage6.prepare', prov.code_version()))
    return Stage6PrepareView(sid=sid, title=paths.title_of(novel_dir),
                            status=prepare.view_state(store))


def prepare_workpack(base: str | os.PathLike[str], sid: str) -> Stage6PrepareWorkpack:
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage6.prepare', prov.code_version()))
    pack = prepare.approval_workpack(store)
    return Stage6PrepareWorkpack(sid=sid, title=paths.title_of(novel_dir),
                                **pack.model_dump(mode='json', by_alias=True))
