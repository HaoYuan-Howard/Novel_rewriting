"""独立 Prepare 的只读入口；正式产物与接受仅由 CLI/producer 写入。

批次 B4：人工 edit/review 写面退役；机械校验通过即
``system:stage6-prepare-auto`` 自动接受。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import Stage6PrepareView, Stage6PrepareWorkpack
from novel.api.deps import checked_sid, get_base
from novel.views.stage6_prepare import prepare_view, prepare_workpack

router = APIRouter(prefix='/novels/{sid}/stage6/prepare')


@router.get('', response_model=Stage6PrepareView, response_model_by_alias=True)
def get_prepare(sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    return prepare_view(base, sid)


@router.get('/workpack', response_model=Stage6PrepareWorkpack, response_model_by_alias=True)
def get_prepare_workpack(sid: str = Depends(checked_sid), base: str = Depends(get_base)):
    return prepare_workpack(base, sid)
