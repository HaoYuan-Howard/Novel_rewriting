"""免费改选（候选集解耦）的确定性反例（2026-09-15 全自动裁决）。

承诺：正文、NameMap、候选集合和组装规则均未变化时，**三条候选之间切换的
模型调用数为零**——rename 的 stage6 消费投影绑定候选集合而不是选中的那条；
碰撞校验目标面含全部候选；组装恒从未改名正文 + 原始候选全量重放。
"""
from __future__ import annotations

import hashlib

from novel.consumption import stage7_hook
from novel.stages.stage7.assemble import assemble_txt
from novel.stages.stage7.measure import hook_consumed_of


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]


C1 = '城头的火光映在她的眼底，这一夜，京都要变天了。'
C2 = '他把那封信压在她掌心，转身走进了雨里。'
C3 = '十年后再见时，她叫的不是他的名字。'


def test_hook_consumed_projection_binds_candidate_set_not_selection():
    """改选（approved 变化、候选集合不变）⇒ 投影逐字段相等；候选集变化 ⇒ 变。"""
    base = {'candidates': [{'text': C1}, {'text': C2}, {'text': C3}],
            'approved': {'text': C1}}
    repick = {'candidates': [{'text': C1}, {'text': C2}, {'text': C3}],
              'approved': {'text': C2}}
    assert hook_consumed_of(base) == hook_consumed_of(repick)
    assert hook_consumed_of(base).candidates_sha1 == _sha1(C1 + '\x1f' + C2 + '\x1f' + C3)

    edited = {'candidates': [{'text': C1 + '改'}, {'text': C2}, {'text': C3}],
              'approved': {'text': C1 + '改'}}
    assert hook_consumed_of(edited) != hook_consumed_of(base)
    assert hook_consumed_of(None).present is False
    assert hook_consumed_of({'candidates': []}).present is True


def test_consumption_stage7_hook_projection_is_the_candidate_list():
    rec = {'candidates': [{'text': C1}, {'text': C2}, {'text': C3}],
           'approved': {'text': C2}}
    assert stage7_hook(rec) == [C1, C2, C3]
    # 改选不动投影
    assert stage7_hook({**rec, 'approved': {'text': C1}}) == stage7_hook(rec)


def test_assemble_replay_is_byte_identical_across_c1_c2_c1_roundtrip():
    """组装恒从原文全量重放：c1→c2→c1 往返后与直接组装 c1 逐字节相同。"""
    recs = [{'text': '旧名甲推门进来。旧名乙跟在后面。'},
            {'text': '旧名乙说：走吧。'}]
    subs = {'旧名甲': '林川', '旧名乙': '沈砚'}
    book_c1 = assemble_txt(C1, recs, subs, kept=())
    book_c2 = assemble_txt(C2, recs, subs, kept=())
    book_c1_again = assemble_txt(C1, recs, subs, kept=())
    assert book_c1 == book_c1_again          # 往返无漂移
    assert book_c1 != book_c2                # 换候选确实换了文案
    assert '林川' in book_c2 and '旧名甲' not in book_c2


def test_hook_drift_is_false_across_reselection_true_on_set_change():
    """`_hook_drift`：改选 ⇒ False（映射仍新鲜）；候选集变化 ⇒ True。"""
    from novel.stages.stage7.rename import _hook_drift

    class _FakeUp:
        def __init__(self, raw):
            self.hook_consumed = hook_consumed_of(raw)

    pick_c1 = {'candidates': [{'text': C1}, {'text': C2}, {'text': C3}],
               'approved': {'text': C1}}
    pick_c2 = {'candidates': [{'text': C1}, {'text': C2}, {'text': C3}],
               'approved': {'text': C2}}

    art = type('M', (), {})()
    art.hook_consumed = hook_consumed_of(pick_c1)
    assert _hook_drift(art, _FakeUp(pick_c2)) is False      # 改选不 drift
    edited = {'candidates': [{'text': C1 + '改'}, {'text': C2}, {'text': C3}],
              'approved': {'text': C1 + '改'}}
    assert _hook_drift(art, _FakeUp(edited)) is True         # 集合变化才 drift
