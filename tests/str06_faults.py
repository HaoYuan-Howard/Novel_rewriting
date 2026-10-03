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
  • ``obligation_unbound``——必保义务漏标注承载：design wire 的 real 标注被
    清空（CS-3 bind 退役后承载位唯一声明处是 design wire），assemble_body_design
    的 real 覆盖闸在 body_design 同节点修订轮拦下；
  • ``duplicate_json_key``——wire 多键：响应 JSON 带重复键，
    `novel/llm/paid_requests.py::_decode_json` 拒绝（契约：拒绝重复 JSON 键）。

【注入路径】进程内直接构造 ``FaultTransport``；子进程/产品入口经
``fault_sitecustomize_source()`` 写出的 sitecustomize（PYTHONPATH）＋环境门
（``NOVEL_STR06_FAULT``）替换 ``novel.stages.chain.DeepSeekTransport``——与
`tests/test_str06_product_entry.py` 的 B5 注入同一形状。R0 只验证「注入确实产生
typed 机械失败」；R3 起复用本底座验证修订（``fault_once=True`` 时受影响 owner
的首响应带故障、修订轮返回干净 wire——「改对了」的脚本化模型）。
"""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

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
        # CS-3 bind 退役：承载位＝design wire 的 real 标注。清空全部标注 ⇒
        # 必保义务覆盖闸在 body_design 同节点修订轮拦下（不再有跨节点回派面）。
        for scene in mutated['design']['scenes']:
            for beat in scene['beats']:
                beat.pop('real', None)
    elif fault == 'duplicate_json_key':
        pass            # 该故障在 transport 层返回原始文本，见 FaultTransport
    else:
        raise ValueError(f'未登记的故障类：{fault!r}（闭集={FAULTS}）')
    return mutated


class FaultTransport:
    """按故障路由的假 transport：wires 走 JSON 面，多键故障返回原始坏文本。

    复用 `tests/test_str06_body.py::_RoutingTransport` 的路由嗅探与正文/楔子
    应答（同一份 wires 形状），只在 JSON 面上施加故障。``fault_once=True``
    时故障只施加到受影响 owner 的**首次**响应——修订轮（R3）拿到干净 wire，
    供「修订成功走完全链」的正例；默认持续施加（无进展停止的反例）。

    ``body_texts``（R5 产品入口用）：链级用例的默认章稿只有 35／37 字，落不进
    产品入口按源书派生的章篇幅带（如 [300, 650]）——走真实 CLI 的产品链测试
    传入带内长度的章稿，正文面与故障面互不干扰（故障只施加 JSON owner）。
    """

    def __init__(self, wires, fault=None, fault_once=False, body_texts=None):
        from tests.test_str06_body import _RoutingTransport

        self._wires = wires
        self._clean_wires = apply_fault(wires, None)
        self._inner = _RoutingTransport(apply_fault(wires, fault))
        self.fault = fault
        self.fault_once = fault_once
        self.body_texts = dict(body_texts) if body_texts else None
        self._served_faulty: set[str] = set()
        # R8a 验收：supervisor 每节点一个子进程，进程内 `_served_faulty` 会随
        # 进程重置、bind 每个新子进程都重新吃到故障 wire——「fault_once」在
        # 多进程产品链上不成立。环境门 NOVEL_STR06_FAULT_ONCE_STICKY 指向一个
        # 状态文件（owner 集合，追加写）：跨子进程只施一次故障，之后的响应
        # 一律干净（与进程内自愈用例同语义）。
        sticky = os.environ.get('NOVEL_STR06_FAULT_ONCE_STICKY')
        self._sticky_path = Path(sticky) if sticky else None

    def _sticky_served(self) -> set[str]:
        if self._sticky_path is None or not self._sticky_path.is_file():
            return set()
        import json as _json

        return set(_json.loads(self._sticky_path.read_text(encoding='utf-8')))

    def _sticky_mark(self, owner: str) -> None:
        if self._sticky_path is None:
            return
        import json as _json

        served = self._sticky_served() | {owner}
        self._sticky_path.parent.mkdir(parents=True, exist_ok=True)
        self._sticky_path.write_text(
            _json.dumps(sorted(served)), encoding='utf-8')

    @property
    def calls(self) -> list[str]:
        return self._inner.calls

    def _owner_of(self, request) -> str:
        return self._inner._owner(request)

    def _send_clean(self, request):
        saved = self._inner.wires
        self._inner.wires = self._clean_wires
        try:
            return self._inner.send(request)
        finally:
            self._inner.wires = saved

    def send(self, request):
        from novel.llm.transport import ChatResponse

        owner = self._owner_of(request)
        if '【条目返修】' in request.user and owner in self._clean_wires:
            # CS-patch（2026-10-03）：条目返修的脚本化模型——fault_once 且故障
            # 已施过＝「改对了」（替换值取**干净** wire 的对应条目）；持续故障
            # ＝patch 原样重抄故障条目（merged 仍不过 → 无进展即停）。只有接了
            # patch 协议的 owner 会收到条目返修 prompt。
            import json as _json
            import re as _re

            source = (self._clean_wires if self.fault_once
                      and self._fault_already_served(owner) else self._inner.wires)

            def _walk(doc, path):
                node = doc
                for m in _re.finditer(r'([^.\[]+)|\[(\d+)\]', path):
                    node = node[m.group(1) if m.group(1) is not None
                                else int(m.group(2))]
                return node

            patches = [{'path': path, 'value': _walk(source[owner], path)}
                       for path in _re.findall(r'- 路径 ([^\s：]+)：', request.user)]
            self._inner.calls.append(owner)
            return ChatResponse(
                content=_json.dumps({'patches': patches}, ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 20, 'completion_tokens': 20}, model='fake')
        if self.body_texts is not None and owner.startswith('body_ch'):
            key = owner[len('body_'):]          # 'body_ch01' → 'ch01'
            text = self.body_texts[key]        # 缺章即 KeyError：关闭式，不兜底
            self._inner.calls.append(owner)
            return ChatResponse(content=text, finish_reason='stop',
                                usage={'prompt_tokens': 100,
                                       'completion_tokens': 40},
                                model='fake')
        if self.fault == 'duplicate_json_key' and owner == 'book':
            if self.fault_once and self._fault_already_served('book'):
                return self._send_clean(request)
            self._mark_fault_served('book')
            self._inner.calls.append('book')
            return ChatResponse(
                content='{"protocol_version": "stage2-observation-v1",'
                        ' "protocol_version": "stage2-observation-v1"}',
                finish_reason='stop',
                usage={'prompt_tokens': 10, 'completion_tokens': 10},
                model='fake')
        if self.fault_once and owner in _FAULT_TARGET_OWNERS.get(self.fault, ()):
            if self._fault_already_served(owner):
                return self._send_clean(request)   # 修订轮：模型「改对了」
            self._mark_fault_served(owner)
        return self._inner.send(request)

    def _fault_already_served(self, owner: str) -> bool:
        if owner in self._served_faulty:
            return True
        return owner in self._sticky_served()

    def _mark_fault_served(self, owner: str) -> None:
        self._served_faulty.add(owner)
        if self._sticky_path is not None and owner not in self._sticky_served():
            self._sticky_mark(owner)


#: 每类故障受影响的 owner（fault_once 的施加面）。
_FAULT_TARGET_OWNERS = {
    'evidence_out_of_range': ('book',),
    'unknown_slug': ('ch01',),
    'missing_pairing': ('associate',),
    'obligation_unbound': ('design',),
    'duplicate_json_key': ('book',),
}


def install_fault_transport(fault: str, fault_once: bool = False,
                            body_texts: dict[str, str] | None = None):
    """把 FaultTransport 挂上生产付费缝（sitecustomize 的注入点）。

    `novel.stages.chain` 构造 `PaidRequestSession` 时唯一读取的 transport 工厂
    就是 `DeepSeekTransport`（与 `tests/test_str06_product_entry.py` 的进程内
    注入同一缝）。幂等：重复调用只替换不叠加。``body_texts`` 见
    `FaultTransport`（R5 产品链测试传带内章稿）。
    """
    from novel.stages import chain as _chain

    wires = looped_full_chain_wires()
    _chain.DeepSeekTransport = lambda cfg: FaultTransport(
        wires, fault, fault_once, body_texts=body_texts)
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
