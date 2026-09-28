#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06_faults —— R0 故障注入 fake 底座（脚本化模型式错误；R3 修订逻辑复用）。

【为什么单独成模块】PLAN.md R0：自愈（R3）只修 typed、可定位的机械问题，验证它
需要能**可重复地**让 fake 模型犯「模型式错误」——不是让测试自己拼 issue 对象
（那只会自证修复器），而是让错误从 transport 的响应内容流经真实付费边界
（`ask_json` 解码＋owner 引擎机械闸），在真实链入口被拦下。本模块集中五类错误
的 wire 变异与注入路径，R3 的修订循环与 R5 的产品路径测试直接复用。

【五类故障（FAULTS 闭集）】
  • ``evidence_out_of_range``——证据 id 越界：book 观察的称谓证据引用闭集外句段
    （``e99_99``），Stage2 owner 引擎的 Resolver 闭集核对拦下；
  • ``unknown_slug``——未知 slug：章观察拍 participants 引用书级人物账之外的
    人物（``ghost``），闭集归 `str06.stage2.compile`（R-2.3）；
  • ``missing_pairing``——漏配对：跨章悬念有 open 有 close，但 associate 的
    loop_pairing 缺席，compile 按 item 报未决（D8，拒绝发布）；
  • ``obligation_unbound``——必保义务未承载：bind 把唯一的必保义务放进 unbound
    （带 owner 的合法返回，正式 binding 照发），stage4 compile 的义务覆盖
    Guard 拦下并停在该 owner（R3 按 unbound.owner 回派）；
  • ``duplicate_json_key``——wire 多键：响应 JSON 带重复键，
    `novel/llm/paid_requests.py::_decode_json` 拒绝（契约：拒绝重复 JSON 键）。

【注入路径】进程内直接构造 ``FaultTransport``；子进程/产品入口经
``fault_sitecustomize_source()`` 写出的 sitecustomize（PYTHONPATH）＋环境门
（``NOVEL_STR06_FAULT``）替换 ``novel.stages.chain.DeepSeekTransport``——与
`tests/test_str06_product_entry.py` 的 B5 注入同一形状。R0 只验证「注入确实产生
typed 机械失败」，不接修订逻辑。
"""
from __future__ import annotations

from copy import deepcopy

#: 环境门：sitecustomize 只有看到它才注入（不设即完全不动生产导入）。
FAULT_ENV = 'NOVEL_STR06_FAULT'
#: 注入时需要知道的仓根（tests 包与 novel 包都可导入）。
REPO_ENV = 'NOVEL_STR06_FAULT_REPO'

#: 五类脚本化模型式错误（闭集；扩类必须同批更新本模块与守卫测试）。
FAULTS = ('evidence_out_of_range', 'unknown_slug', 'missing_pairing',
          'obligation_unbound', 'duplicate_json_key')


def looped_full_chain_wires() -> dict:
    """带一条跨章悬念的全链 fake wires（`_full_chain_wires` 的绿基线扩展）。

    ch01 开悬念 ``q_yu``、ch02 收束，associate 给出配对——`missing_pairing`
    故障删掉的就是这条配对。基线必须全链绿（守卫测试钉住），否则「漏配对」
    红例无从谈起。
    """
    from tests.test_str06_hook import _full_chain_wires

    wires = deepcopy(_full_chain_wires())
    wires['ch01']['beats'][0]['loops'] = [
        {'ref': 'q_yu', 'op': 'open', 'question': '玉佩藏着什么秘密？'}]
    wires['ch02']['beats'][0]['loops'] = [
        {'ref': 'q_yu', 'op': 'close', 'resolution': '玉佩刻着沈字，指向旧案。'}]
    wires['associate']['loop_pairing'] = [
        {'open': 'ch01.q_yu', 'close': 'ch02.q_yu'}]
    return wires


def apply_fault(wires: dict, fault: str | None) -> dict:
    """在 wires 深拷贝上施加一类故障；``None`` 原样返回（绿基线）。"""
    mutated = deepcopy(wires)
    if fault is None:
        return mutated
    if fault == 'evidence_out_of_range':
        first = next(iter(mutated['book']['characters']))
        mutated['book']['characters'][first]['surfaces'][0]['source_refs'] = ['e99_99']
    elif fault == 'unknown_slug':
        mutated['ch01']['beats'][0]['participants'] = ['ghost']
    elif fault == 'missing_pairing':
        mutated['associate']['loop_pairing'] = []
    elif fault == 'obligation_unbound':
        mutated['bind'] = {
            'protocol_version': 'stage4-bind-v1', 'entries': [],
            'unbound': [{'obligation_id': 'ob01', 'owner': 'design',
                         'reason': '变异：漏承载必保义务'}]}
    elif fault == 'duplicate_json_key':
        pass            # 该故障在 transport 层返回原始文本，见 FaultTransport
    else:
        raise ValueError(f'未登记的故障类：{fault!r}（闭集={FAULTS}）')
    return mutated


class FaultTransport:
    """按故障路由的假 transport：wires 走 JSON 面，多键故障返回原始坏文本。

    复用 `tests/test_str06_body.py::_RoutingTransport` 的路由嗅探与正文/楔子
    应答（同一份 wires 形状），只在 JSON 面上施加故障。
    """

    def __init__(self, wires, fault=None):
        from tests.test_str06_body import _RoutingTransport

        self._inner = _RoutingTransport(apply_fault(wires, fault))
        self.fault = fault

    @property
    def calls(self) -> list[str]:
        return self._inner.calls

    def send(self, request):
        from novel.llm.transport import ChatResponse

        if self.fault == 'duplicate_json_key' and \
                self._inner._owner(request) == 'book':
            self._inner.calls.append('book')
            return ChatResponse(
                content='{"protocol_version": "stage2-observation-v1",'
                        ' "protocol_version": "stage2-observation-v1"}',
                finish_reason='stop',
                usage={'prompt_tokens': 10, 'completion_tokens': 10},
                model='fake')
        return self._inner.send(request)


def install_fault_transport(fault: str):
    """把 FaultTransport 挂上生产付费缝（sitecustomize 的注入点）。

    `novel.stages.chain` 构造 `PaidRequestSession` 时唯一读取的 transport 工厂
    就是 `DeepSeekTransport`（与 `tests/test_str06_product_entry.py` 的进程内
    注入同一缝）。幂等：重复调用只替换不叠加。
    """
    from novel.stages import chain as _chain

    wires = looped_full_chain_wires()
    _chain.DeepSeekTransport = lambda cfg: FaultTransport(wires, fault)
    return fault


def fault_sitecustomize_source() -> str:
    """sitecustomize 源文本（写入 tmp 目录后经 PYTHONPATH 生效）。

    环境门与 B5 的 fake transport 注入同一纪律：不设 ``NOVEL_STR06_FAULT``
    时本文件不做任何事，生产导入不受影响。
    """
    return (
        'import os\n'
        '\n'
        f'_fault = os.environ.get({FAULT_ENV!r})\n'
        'if _fault:\n'
        f'    _repo = os.environ[{REPO_ENV!r}]\n'
        '    import sys\n'
        '    if _repo not in sys.path:\n'
        '        sys.path.insert(0, _repo)\n'
        '    from tests.str06_faults import install_fault_transport\n'
        '    install_fault_transport(_fault)\n'
    )
