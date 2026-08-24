#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_schema_common.py —— 盯住载体层真的在守它声称要守的那几件事。

这些断言各自对应 `docs/schema/_common.md` 里一条**已经发生过**的损失：
未知字段被静默丢弃 · 两把尺子混用 · 提升路伪造来源账 · 三态被压成两态。
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from contracts.invariants import OWNER, SEVERITY
from contracts.schema import (
    Finding, Fp10, FreeProvenance, HashBoundHumanApproval, HumanApproval,
    InputEdge, LlmProvenance, Owner, RevalidationAudit, Severity, Usage,
)
from contracts.schema.base import StrictModel

EDGE = {'stage1/main_text.json': InputEdge(file='0123456789', consumed=None)}
ROOT = '0' * 40


def _llm_prov(**kw):
    base = dict(code='abc1234-dirty', root_clean_sha1=ROOT, inputs=EDGE,
                prompts={'SYS': 'aaaaaaaaaa'}, model='deepseek-v4-pro')
    return LlmProvenance(**{**base, **kw})


def _revalidation_audit() -> RevalidationAudit:
    return RevalidationAudit(
        actor='reviewer', reason='当前判据已逐条复核',
        baseline_locator_sha256='0' * 64,
        baseline_raw_sha256='1' * 64, candidate_raw_sha256='2' * 64,
        baseline_canonical_sha256='3' * 64,
        candidate_canonical_sha256='4' * 64,
        raw_changed=True, content_changed=True, edited_by_human=True,
        changed_json_pointers=('/surface_habits/0',),
    )


# ───────────────────── ① extra='forbid'：未知字段不许被静默丢弃 ─────────────────────

def test_unknown_field_is_rejected_not_dropped():
    """Pydantic 默认 `extra='ignore'`——上游多写一个字段、或字段名拼错，
    校验照常通过而那个值消失了。**那正是 `Q-1`（不静默）要挡的形态。**
    """
    with pytest.raises(ValidationError, match='extra_forbidden|Extra inputs'):
        _llm_prov(typo_field=1)


def test_every_model_in_the_package_forbids_extra():
    """守卫的守卫：新加一个模型忘了继承 `StrictModel`，上面那条测不到它。"""
    import contracts.schema as pkg
    models = [v for v in vars(pkg).values()
              if isinstance(v, type) and issubclass(v, StrictModel) and v is not StrictModel]
    assert len(models) > 5, f'只扫到 {len(models)} 个模型——re-export 多半改了形状'
    for m in models:
        assert m.model_config.get('extra') == 'forbid', f'{m.__name__} 没有 extra=forbid'


# ───────────────────── ② 两把尺子：10 位永不与 40 位比较 ─────────────────────

@pytest.mark.parametrize('bad', [
    'a' * 40,                    # 40 位内容身份混进了来源指纹的位置
    'a' * 9, 'a' * 11, '',
    'A' * 10,                    # 大写
    'g' * 10,                    # 非十六进制
])
def test_fp10_rejects_anything_that_is_not_ten_hex(bad):
    """⚠ 第一个样本是关键的那个：**40 位混进 10 位的位置**。

    原型的病不是「有两个宽度」，是同一个字段在两处用不同宽度
    （`n1_partition` 记全宽而 `stage2_common` 记 10 位），
    于是同一份文件的指纹在两份产物里长得不一样，谁也不敢拿它们对比。
    """
    class M(StrictModel):
        fp: Fp10
    with pytest.raises(ValidationError):
        M(fp=bad)


# ───────────────────── ③ 三态：`null` 与「键不在」必须是两份字节 ─────────────────────

def test_none_keys_are_absent_on_disk_not_null():
    """`revalidated: null` 与「没有 `revalidated` 这个键」在 JSON 里必须不同。

    这是三态能成立的前提（`Q-2`）：`False` = 跑出来的，键不在 = 这份产物太老。
    """
    d = _llm_prov().to_disk()
    for k in ('revalidated', 'gates_skipped', 'origin', 'input_basis', 'usage'):
        assert k not in d, f'{k} 是 None，不该出现在盘上'

    d2 = _llm_prov(revalidated=False).to_disk()
    assert d2['revalidated'] is False, '显式的 False 必须写出来——它与「键不在」不是一回事'


def test_gates_skipped_empty_list_survives_to_disk():
    """**空列表也要写。** 空列表 = 这次没有闸门缺料；键不在 = 这份产物早于这套机制。

    它是「阻断级判据没跑成」这件事的唯一痕迹。
    """
    d = _llm_prov(gates_skipped=[]).to_disk()
    assert d['gates_skipped'] == [], '空列表被当成 None 抹掉了，那就把两种状态压成了一种'


def test_consumed_is_required_but_nullable():
    """`consumed` 必填**但可为 null**：`null` = 本节点没声明读了哪些字段
    ⇒ 该边新鲜度是 `unknown`，不是 `fresh`。而「键不在」是另一回事，不许。
    """
    assert InputEdge(file='0' * 10, consumed=None).consumed is None
    with pytest.raises(ValidationError):
        InputEdge(file='0' * 10)          # 键不在 —— 不是合法形态


# ───────────────────── ④ 0 成本节点不许有 `model`（结构上不存在） ─────────────────────

def test_free_provenance_has_no_model_field_at_all():
    """不是「`model` 可以是 null」，是**结构上不存在**。

    理由是假账：`--apply-edit` 落的是人手写的稿、`review_<n>.json` 是 0 成本算出来的，
    记一个 `model` 上去就是宣称「某个模型产了它」。
    """
    assert 'model' not in FreeProvenance.model_fields
    with pytest.raises(ValidationError, match='extra_forbidden|Extra inputs'):
        FreeProvenance(code='c', root_clean_sha1=ROOT, inputs=EDGE, model='deepseek-v4-pro')


# ───────────────────── ⑤ 提升路：结构上不可能伪造 ─────────────────────

def test_promoted_signature_cannot_take_prompts_model_or_inputs():
    """⚠⚠ **这条测试就是那个 bug 本身。**

    原型的 `--revalidate` 能伪造，是因为提升分支够得到一个会造新来源账的函数。
    **没有参数，就没有可以传错的东西。**
    """
    params = set(inspect.signature(LlmProvenance.promoted).parameters) - {'self'}
    assert params == {'audit', 'code'}, (
        f'promoted() 的参数变成了 {sorted(params)}。'
        f'一旦它能接受 prompts / model / inputs，伪造就又成为可能了'
    )


def test_promotion_keeps_the_original_source_account():
    """提升之后，来源账必须还是**问题稿自己那一份**。

    原型实测：一份上一轮付费产出的问题稿被提升后，来源账写着这一轮的 SYS 指纹、
    这一轮的 model、这一轮读到的上游指纹——而没有任何一次新落笔发生过。
    """
    old = _llm_prov(prompts={'SYS': 'aaaaaaaaaa'}, gates_skipped=['name_leak'])
    audit = _revalidation_audit()
    new = old.promoted(code='newcode-dirty', audit=audit)

    assert new.prompts == {'SYS': 'aaaaaaaaaa'}, '盖上了当前的 SYS 指纹 —— 这就是伪造'
    assert new.model == old.model and new.inputs == old.inputs
    assert new.revalidated is True
    assert new.origin is None, 'origin=human 专属 --apply-edit 新一轮；INVALID 改动由 audit 留痕'
    assert new.revalidation_audit.edited_by_human is True
    assert new.code == 'newcode-dirty'
    assert new.revalidation_audit == audit, '重判的人、理由与字节差异没有进入来源账'
    assert new.invalid_baseline_sha256 is None, '正式产物不该继续冒充活的 INVALID 基线'
    assert 'gates_skipped' not in new.to_disk(), '提升上来的是正式产物，不该再挂缺料记录'


def test_human_approval_models_reject_blank_credentials_and_bad_hashes():
    assert HumanApproval(actor='甲', reason='逐项复核').actor == '甲'
    with pytest.raises(ValidationError, match='空白'):
        HumanApproval(actor='  ', reason='逐项复核')
    with pytest.raises(ValidationError, match='空白'):
        HumanApproval(actor='甲', reason='\t')
    with pytest.raises(ValidationError):
        HashBoundHumanApproval(actor='甲', reason='逐项复核', subject_sha256='short')


def test_revalidation_audit_requires_hash_flags_and_json_pointer_diff_to_agree():
    audit = _revalidation_audit()
    assert audit.content_changed and audit.raw_changed
    with pytest.raises(ValidationError, match='raw_changed'):
        RevalidationAudit(**{**audit.model_dump(), 'raw_changed': False})
    with pytest.raises(ValidationError, match='edited_by_human'):
        RevalidationAudit(**{**audit.model_dump(), 'edited_by_human': False})
    with pytest.raises(ValidationError, match='字典序'):
        RevalidationAudit(**{
            **audit.model_dump(),
            'changed_json_pointers': ['/z', '/a'],
        })


def test_apply_edit_drops_prompts_and_model_but_keeps_inputs():
    """`--apply-edit` 那一份是教科书：继承 `inputs`、**丢掉 `prompts` 与 `model`**、记 `origin`。"""
    new = _llm_prov().to_human_edit(code='c2', input_basis='apply_edit_current_validation')
    assert new.inputs == EDGE
    assert new.origin == 'human'
    d = new.to_disk()
    assert 'model' not in d and 'prompts' not in d, '人工稿记了 prompt/模型就是假账'
    assert d['input_basis'] == 'apply_edit_current_validation'


# ───────────────────── ⑥ Finding 的三条后置条件 ─────────────────────

def _finding(**kw):
    base = dict(rule='name_leak', sev='hard', item='描述里出现人物专名', detail='第 3 维命中「阿禾」',
                evidence=['voice.interiority', '阿禾'], evidence_key='voice.interiority|阿禾',
                owner='stage3', actionable=False, identity_hit=False,
                converge='new', unresolved_rounds=0)
    return Finding(**{**base, **kw})


def test_evidence_key_must_equal_the_joined_evidence():
    """收敛键 = (章, rule, evidence_key)。对不上会让「同一个问题」被数成两次。"""
    _finding()
    with pytest.raises(ValidationError, match='evidence_key'):
        _finding(evidence_key='对不上')


def test_owner_is_none_exactly_when_it_is_not_a_conclusion():
    """`owner is None` ⇔ `sev ∈ (metric, unver)`。这条不变量抓出过一个 sev 写错的判据。"""
    _finding(sev='metric', owner=None, actionable=False)
    with pytest.raises(ValidationError, match='owner'):
        _finding(sev='metric', owner='stage3')      # 度量不该有 owner
    with pytest.raises(ValidationError, match='owner'):
        _finding(sev='hard', owner=None)            # 结论必须有 owner


def test_actionable_implies_owner_is_stage5():
    """根因不在写作层的条目进反哺 = 花钱让写手去修一个它修不了的东西。"""
    _finding(owner='stage5', actionable=True)
    with pytest.raises(ValidationError, match='actionable'):
        _finding(owner='stage3', actionable=True)


# ───────────────────── ⑦ 枚举的唯一落点没有被绕开 ─────────────────────

def test_severity_and_owner_literals_come_from_invariants():
    """`Literal[SEVERITY]` 的取值面必须**逐值等于** `invariants` 里那一份。

    有人把它改回手写的 `Literal['hard', ...]` 时，这条测试是唯一会出声的地方——
    而手抄一份枚举正是本项目实测漂过两次的那个病。
    """
    from typing import get_args
    assert get_args(Severity) == SEVERITY
    assert get_args(Owner) == OWNER
    schema = Finding.model_json_schema()
    assert '$defs' in schema or 'properties' in schema, 'JSON Schema 生成不了，前端 TS 就没法派生'


# ───────────────────── ⑧ 轮次账的两种形状，与盘上逐字相同 ─────────────────────

def test_round_provenance_reproduces_the_two_shapes_seen_on_disk():
    """⚠ 轮次账在盘上有**两种**形状，而它们的差别不是可选字段填没填。

    2026-08-11 扫过 `Output/` 下 **79 份**带 `_provenance` 的产物，键组合实测：
      · `['code','fixes','inputs','model','prompts','review','revision_of','round']` ×4
        —— 模型产的修订轮
      · `['code','input_basis','inputs','origin','revision_of','round']` ×1
        —— `--apply-edit` 落的人工稿：**没有 `prompts`/`model`，却仍带着 `round`/`revision_of`**

    ⇒ 轮次账必须能与**两种**来源账各组合一次，所以它是 mixin 不是子类。
    第二种形状是这条设计的**唯一**证据，盘上只有 1 份，别把它当噪声。
    """
    from contracts.schema import RoundFreeProvenance, RoundLlmProvenance

    paid = RoundLlmProvenance(
        code='c', root_clean_sha1=ROOT, inputs=EDGE,
        prompts={'SYS': 'a' * 10}, model='deepseek-v4-pro',
        round=2, revision_of='abc', review='def', fixes=[('secret_leak', 'ch3|x')])
    assert sorted(paid.to_disk()) == [
        'code', 'fixes', 'inputs', 'model', 'prompts', 'review', 'revision_of',
        'root_clean_sha1', 'round']
    current = paid.model_copy(update={'request_fingerprint': 'f' * 64})
    assert current.to_disk()['request_fingerprint'] == 'f' * 64
    with pytest.raises(ValidationError):
        RoundLlmProvenance.model_validate({
            **paid.model_dump(), 'request_fingerprint': 'f' * 32})
    assert 'request_fingerprint' not in paid.to_disk(), \
        '字段引入前的 v2 章没有请求身份，兼容读取时不得伪造；现役 producer 另有落盘守卫'

    human = RoundFreeProvenance(
        code='c', root_clean_sha1=ROOT, inputs=EDGE, round=3, revision_of='abc',
        origin='human', input_basis='apply_edit_current_validation')
    assert sorted(human.to_disk()) == [
        'code', 'input_basis', 'inputs', 'origin', 'revision_of', 'root_clean_sha1', 'round'], \
        '人工稿的键组合与盘上那一份对不上'
    assert 'model' not in human.to_disk(), '人工稿记了模型就是假账'


def test_usage_keeps_two_separate_counters():
    """只数真调用，会让「参数不变的重跑什么都没跑」与「什么都没干」同形。"""
    u = Usage(calls_made=0, calls_cached=3, prompt_tokens=None,
              completion_tokens=None, reasoning_tokens=None)
    assert (u.calls_made, u.calls_cached) == (0, 3)
    with pytest.raises(ValidationError):
        Usage(calls_made=-1, calls_cached=0, prompt_tokens=None,
              completion_tokens=None, reasoning_tokens=None)
