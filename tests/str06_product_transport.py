"""产品入口测试 transport：共享带内章稿与故障模型，不加载 HTTP 测试模块。

父进程与 CLI 子进程共用同一份响应材料；仅替换模型传输，正式请求、编译、
校验、落盘和成本仍由生产路径执行。sitecustomize 在 transport 构造时才导入。
"""
from __future__ import annotations

from novel.llm.transport import ChatResponse
from tests.test_str06_body import _RoutingTransport
from tests.test_str06_hook import _declared_prose, _full_chain_wires


# 只用当前章已释放的称谓，不碰未释放专名或场景标记。
# 两章正文均落在产品入口由源书派生的 [300, 650] 非空白字符带内。
# rc26（命名前移批 C）：正文直接以目标姓名成文（yun_niang→柳婉，芸娘→婉；
# 夫君是保留称谓）。
GOOD_CH01 = (
    '拜堂之夜，红烛高烧，柳婉望着夫君，把心头那句旧约问了个明白。夫君沉吟许久，才说这桩婚事不是寻常姻缘，'
    '而是多年前许下的承诺，今日不过是要兑现它。她想起陪嫁那夜，母亲拉着她的手说，做人妻子的本分是守住这个家，'
    '也要守住自己的心，莫叫一时的委屈改了性子。烛火摇了三摇，窗外风声渐紧，婉把旧约的每一条都记在心里，'
    '知道往后的日子都要照着这句话走下去，一步也错不得。天快亮时，夫君起身告辞，说此去是为了查明一桩旧案的真相，'
    '叫她不必等候，也不必牵挂，门要关好，灯要留一盏。她送他到门前，没有回头，只怕一回头就失了底气，'
    '叫他也跟着为难。回到房中，婉把旧约压在枕下，暗暗告诉自己，无论将来发生什么，这个家的门要一直开着，'
    '灯也要一直亮着。晨光透进窗纸，照见一地烛泪，她忽然觉得，从前怕的黑夜，也不过如此，'
    '只要心里有了着落，再长的夜也能坐到天亮。'
)


GOOD_CH02 = (
    '夫君夜半翻墙而出，婉在他枕下发现一方玉佩，刻着的字面陌生，不像家中旧物。她把玉佩藏回原处，只怕惊动了谁，'
    '索性坐到天明，把昨夜至今的每一件事都翻来覆去地想。起初她疑心夫君有了别的心思，可想起他临行前的眼神，'
    '又觉得不像，反倒像是怕牵连了谁。天将明时，她在灯下把那方玉佩的穗子拆开又系上，忽然想起母亲说过，'
    '旧家曾有一位亲人早年出走在远方，此去经年，杳无音信。她把这点疑惑按在心底，决定等夫君回来再问个明白，'
    '问的时候还要装作无意，免得他为难。鸡叫三遍，门外有了动静，婉起身梳洗，把鬓发理得平平的，'
    '像是什么也没有发生过，只是那方玉佩的凉意，一整日都留在她的指尖上，连写字的时候也跟着颤。'
    '她想起旧约里那句无论将来发生什么，忽然觉得这句话原是说给自己听的。'
)


class _EntryTransport(_RoutingTransport):
    """产品入口用的假 transport：沿用链级路由表，只把两章正文换成带内长度的稿。"""

    def send(self, request):
        owner = self._owner(request)
        if owner.startswith('body_ch'):
            self.calls.append(owner)
            text = GOOD_CH01 if owner.endswith('ch01') else GOOD_CH02
            return ChatResponse(content=_declared_prose(text), finish_reason='stop',
                                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                                model='fake')
        return super().send(request)


def _make_entry_transport(fault: str | None = None):
    if fault is None:
        return _EntryTransport(_full_chain_wires())
    from tests.str06_faults import FaultTransport, looped_full_chain_wires

    return FaultTransport(looped_full_chain_wires(), fault=fault, fault_once=True,
                          body_texts={'ch01': _declared_prose(GOOD_CH01),
                                      'ch02': _declared_prose(GOOD_CH02)})
