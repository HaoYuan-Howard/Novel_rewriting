#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''人工写回入口：已随旧链整层退役（F8b，STR-06 切换）。

旧链的人工改稿面（review／stage2 关系与蓝图／characters／global／stage7
残留采纳，全部硬编码旧节点）随「上传即授权开跑、Stage 页只读监控」的
全自动单模式退役：新链产物由 11 个 `str06.` 节点确定性推进，命名残留由
`naming_residual` 机械验硬闸守卫，不存在待人工核验的改选或采纳面。

本模块只保留空 router 以维持 app 的模块面稳定；路由族、typed 请求模型与
前端消费已在 F8b 同批删除，历史形状见 Git 与 `contracts/schema/
retirement.py` 的登记。
'''
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix='/novels')
