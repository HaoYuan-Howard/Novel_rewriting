#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 匿名质量审判：矛盾边界、质量评分、sha1 绑定与批准硬闸。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.evaluation import (
    EvidenceRef, JudgeDecision, SemanticDecision,
)
from contracts.schema.api import HookApproveRequest
from contracts.schema.stage6 import (
    HookApprovalResolution,
    HookCandidateReview,
    HookEdit,
    HookSemanticResponse,
)
from novel.errors import TerminalState, UpstreamMissing
from novel.llm.transport import ChatResponse
from novel.stages.stage6 import semantic

WIRE = ChatResponse(content='{}')

TEXT_A = '她嫁进将军府那晚，就发现枕头下压着一封没有署名的信。\n\n信里只有一句话。'
TEXT_B = '他在城门口等了三年，等来的却是她与仇人成亲的喜帖。'
TEXT_C = '说书人拍醒木：这故事没人讲完过，因为讲到最后的人都死了。'


def _quality(**overrides) -> dict:
    data = {
        'read_on_desire': 9,
        'body_voice_match': 8,
        'concrete_scene_and_specificity': 7,
        'curiosity_and_payoff_promise': 9,
        'hook_craft': 8,
        'synopsis_penalty': 2,
    }
    data.update(overrides)
    return data


def _response_review(label: str, *, text: str = TEXT_A, contradiction: bool = False,
                     uncertain: bool = False, evidence: str | None = None) -> dict:
    contradictions = ([{
        'assertion': '她嫁给了仇人',
        'evidence': evidence or ('嫁进将军府' if text == TEXT_A else text[:6]),
        'reason': '全书人物关系与此相反',
    }] if contradiction else [])
    return {
        'label': label,
        'coverage': 'uncertain' if uncertain else 'complete',
        'uncertainty_reason': '指代无法确认' if uncertain else None,
        'contradictions': contradictions,
        'quality': _quality(),
        'concise_reason': '具体场面强，最后一击有效。',
    }


def _stored_review(label: str, text: str, *, contradiction: bool = False,
                   uncertain: bool = False, **score_overrides) -> HookCandidateReview:
    data = _response_review(label, text=text, contradiction=contradiction,
                            uncertain=uncertain)
    data['quality'] = _quality(**score_overrides)
    data['text_sha1'] = semantic.text_sha1(text)
    return HookCandidateReview.model_validate(data)


def _art(*, approved: str | None = None, bound_sha256: str | None = None):
    candidates = [
        SimpleNamespace(approach='source_architecture', text=TEXT_A, findings=[]),
        SimpleNamespace(approach='payoff_preview', text=TEXT_B, findings=[]),
        SimpleNamespace(approach='scene_punchline', text=TEXT_C, findings=[]),
    ]
    picked = next((c for c in candidates if c.approach == approved), None)
    approved_obj = (SimpleNamespace(
                        approach=picked.approach,
                        text=picked.text,
                        resolution=SimpleNamespace(
                            subject_sha256=(bound_sha256
                                            or semantic.text_sha256(picked.text)),
                            basis='human_verification'))
                    if picked is not None else None)
    return SimpleNamespace(candidates=candidates, approved=approved_obj)


def _semantic_artifact(reviews: list[HookCandidateReview], ranking: list[str] | None = None):
    texts = {semantic.text_sha1(text): text for text in (TEXT_A, TEXT_B, TEXT_C)}
    decisions = []
    for review in reviews:
        text = texts[review.text_sha1]
        verdict = ('UNVER' if review.coverage == 'uncertain' else
                   'FAIL' if review.contradictions else 'PASS')
        refs = [EvidenceRef(
            artifact='stage6/hook.json#candidate/' + review.label,
            subject_sha256=semantic.text_sha256(text), start=0, end=len(text))]
        # ⚠ 逐判官仍是三态（那是要留的证据）；**合并决议恒 UNVER**。
        judges = [JudgeDecision(
            judge=judge, verdict=verdict, evidence=refs,
            reason=review.uncertainty_reason or review.concise_reason)
            for judge in ('A', 'B')]
        decisions.append(SemanticDecision(
            ability='reference_boundary',
            subject_key=semantic.text_sha256(text),
            verdict='UNVER',
            evidence=refs,
            reason=review.uncertainty_reason or review.concise_reason,
            judges=judges,
        ))
    return SimpleNamespace(
        reviews=reviews,
        ranking=ranking or [review.label for review in reviews],
        decisions=decisions,
    )


class _Store:
    def __init__(self, fresh: bool = True) -> None:
        self._fresh = fresh

    def freshness(self, _rel):
        return SimpleNamespace(state='fresh' if self._fresh else 'stale')


def _all_clean_reviews() -> list[HookCandidateReview]:
    return [
        _stored_review('source_architecture', TEXT_A),
        _stored_review('payoff_preview', TEXT_B),
        _stored_review('scene_punchline', TEXT_C),
    ]


def _install(monkeypatch, artifact, *, seq: int = 1):
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _store: (seq, artifact))


def test_system_publishes_quality_and_material_contradiction_contract():
    system = semantic.SEMANTIC_SYS
    assert '剧透本身不加分也不扣分' in system
    assert '重写台词、压缩时间、倒叙、合并数章和合成场面' in system
    assert '人物身份、人物关系、关键因果或故事结果' in system
    assert '"read_on_desire": "number 0..10"' in system
    assert '"synopsis_penalty": "number 0..10"' in system
    assert '"ranking": [' in system
    assert semantic.JUDGE_ROLE_SUFFIX['A'] != semantic.JUDGE_ROLE_SUFFIX['B']


def test_both_judges_share_a_byte_identical_system_and_differ_only_at_the_user_tail():
    """⚠⚠ **这是一条成本判据，不是文案判据。**

    供应商按 `<BOS>{system}<｜User｜>{user}…` 的**字节前缀**做上下文缓存。身份标签一旦
    回到 system 末尾，前缀就在那里分叉，B 判官那一份 ~45k token 的材料**全额 miss**
    ——而两个判官的材料逐字节相同。实测一本书两次判官调用共 95,972 prompt token
    几乎全额付费。⇒ system 必须逐字节相同，差异只许落在 user 末尾。
    """
    material = '匿名评审以下候选。' + 'x' * 4096
    system_a, user_a = semantic.judge_request('SYS', material, 'A')
    system_b, user_b = semantic.judge_request('SYS', material, 'B')

    assert system_a == system_b == 'SYS', 'system 分叉 ⇒ 整包材料全额 miss'
    assert user_a.startswith(material) and user_b.startswith(material)
    assert user_a != user_b, '两个判官必须仍然拿到不同的身份标签'
    assert user_a[len(material):] == semantic.JUDGE_ROLE_SUFFIX['A']
    assert user_b[len(material):] == semantic.JUDGE_ROLE_SUFFIX['B']


def test_an_unknown_judge_role_is_rejected_instead_of_falling_back():
    """拼错的 role 兜底会安静地跑成另一个判官，而产物里照样写着两个键。"""
    with pytest.raises(KeyError, match='取值面'):
        semantic.judge_request('SYS', 'U', 'C')


def test_the_judge_prompt_is_assembled_in_exactly_one_place():
    """⚠⚠ `evaluator_fingerprint` 记的必须是**真发出去的字节**。

    两处各拼一份就会分家，而分家之后没有任何东西说得出「那一次到底发的是什么」
    （同 `novel/stages/stage5/prompts.py::write_system` 那条纪律）。
    ⇒ 换掉唯一拼装口，指纹必须跟着动；不动就说明它自己又拼了一份。
    """
    import inspect

    source = inspect.getsource(semantic)
    assert source.count('JUDGE_ROLE_SUFFIX[') == 1, \
        '除 `judge_request` 外还有第二处按 role 取标签 ⇒ 两份拼法迟早分家'

    baseline = semantic._runtime_contract('fixture-model')
    original = semantic.judge_request
    try:
        semantic.judge_request = (
            lambda system, user, role: (system, f'{user}<{role}>'))
        assert semantic._runtime_contract('fixture-model') != baseline, \
            '`_runtime_contract` 没走唯一拼装口，它自己重拼了一份'
    finally:
        semantic.judge_request = original
    assert semantic._runtime_contract('fixture-model') == baseline


def test_provenance_prompt_keys_stop_claiming_the_label_lives_in_the_system():
    """来源账的键名跟着**真实请求形状**走。

    ⚠ 原来是 `HOOK_SEMANTIC_SYS_A` / `_B` 各存一份 `SEMANTIC_SYS + 标签`：既把同一段 SYS
    存了两遍，又在标签已经不在 system 里之后继续叫自己 SYS——那就是来源账在说假话。
    """
    prompts = semantic.judge_prompts(semantic.SEMANTIC_SYS)

    assert set(prompts) == {
        'HOOK_SEMANTIC_SYS', 'HOOK_SEMANTIC_ROLE_A', 'HOOK_SEMANTIC_ROLE_B'}
    assert prompts['HOOK_SEMANTIC_SYS'] == semantic.SEMANTIC_SYS
    for role, suffix in semantic.JUDGE_ROLE_SUFFIX.items():
        assert prompts[f'HOOK_SEMANTIC_ROLE_{role}'] == suffix
        assert suffix not in prompts['HOOK_SEMANTIC_SYS'], \
            '标签又混回 SYS 了 ⇒ 前缀重新分叉'


def test_runtime_contract_binds_the_exact_request_surface_and_nothing_else():
    """运行时指纹只回答「这一次看的是哪一版 prompt / schema / 解码面」。

    ⚠ 这里曾有两条用例：一条断言「无 profile ⇒ 在 transport 前 exit 2」，一条断言
    `--advisory` 走哨兵 dataset。自动放行门整批下线后**没有第二条路**，那两条各自
    描述的分岔都不存在了；剩下的真问题只有一个——指纹绑没绑对面。
    ⚠ `dataset_sha256` 也一并消失：没有 dataset 了，一个恒定哨兵看起来像 dataset 身份
    而其实不是。这条用例连带把它钉成「不许回来」。
    """
    current = semantic._runtime_contract('fixture-model')

    assert current.model == 'fixture-model'
    assert set(type(current).model_fields) == {
        'model', 'prompt_sha256', 'schema_sha256', 'decoder_sha256'}
    assert len(current.prompt_sha256) == 64
    assert len(current.schema_sha256) == 64
    assert len(current.decoder_sha256) == 64
    # 换一个 model 就是另一份身份；否则「同一份结论」会跨模型复用。
    assert semantic._runtime_contract('other-model') != current


def test_human_edit_and_resolution_contract_require_explicit_hash_binding():
    subject = semantic.text_sha256(TEXT_A)
    with pytest.raises(ValueError, match='actor'):
        HookEdit(text=TEXT_A)
    # schema 只验证审计字段形状；subject 绑定归一后的真实定稿字节，而归一依赖
    # 本书 src_hook，必须由 run_apply_edit 在真实链路上校验。
    shaped = HookEdit(
        text=TEXT_A, actor='editor-a', reason='核验完成',
        subject_sha256='0' * 64)
    assert shaped.subject_sha256 == '0' * 64
    edit = HookEdit(
        text=TEXT_A, actor='editor-a', reason='核验完成',
        subject_sha256=subject)
    assert edit.subject_sha256 == subject
    # ⚠ `calibrated_pass` 是**被裁决掉的取值**（同 `ApprovedHook.origin` 的形状）：
    #   它只能由自动放行门产生，而那套装置已整批下线 ⇒ 类型上就写不进去。
    with pytest.raises(ValueError):
        HookApprovalResolution(
            actor='editor-a', reason='选择此稿', subject_sha256=subject,
            basis='calibrated_pass')
    # 人工核验时**没读过任何语义审判**是合法的处境，`semantic_review_seq` 因此可空：
    # 「人在零证据下拍的板」与「人读了第 3 轮判官证据」必须分得开。
    blind = HookApprovalResolution(
        actor='editor-a', reason='零证据下拍板', subject_sha256=subject,
        basis='human_verification')
    assert blind.semantic_review_seq is None
    with pytest.raises(ValueError):
        HookApproveRequest(pick=1)
    with pytest.raises(ValueError, match='空白'):
        HookApproveRequest(
            pick=1, actor=' ', reason='已核验', subject_sha256=subject)


def test_approve_http_adapter_forwards_audited_optimistic_lock(monkeypatch):
    from novel.api.routes import edits

    captured = {}

    def run(base, node, sid, extra):
        captured.update(base=base, node=node, sid=sid, extra=extra)
        return SimpleNamespace(exit_code=2)

    monkeypatch.setattr(edits.jobs, 'run', run)
    monkeypatch.setattr(
        edits, '_stamp',
        lambda *_args: (_ for _ in ()).throw(AssertionError('失败作业不许盖章')))
    request = HookApproveRequest(
        pick=2, actor='editor-a', reason='核验完成',
        subject_sha256='a' * 64)
    edits.approve_hook(request, sid='book-a', base='out')
    assert captured == {
        'base': 'out', 'node': 'stage6.approve', 'sid': 'book-a',
        'extra': ['--pick', '2', '--actor', 'editor-a', '--reason', '核验完成',
                  '--subject-sha256', 'a' * 64],
    }


def test_response_validator_closes_review_and_ranking_coverage():
    aliases = [('T1', 'source_architecture', TEXT_A),
               ('T2', 'payoff_preview', TEXT_B),
               ('T3', 'scene_punchline', TEXT_C)]
    validate = semantic.response_validator(aliases)
    good = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1'),
                    _response_review('T2', text=TEXT_B),
                    _response_review('T3', text=TEXT_C)],
        'ranking': ['T1', 'T2', 'T3'],
    })
    assert validate(good, WIRE) is None

    missing = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1')],
        'ranking': ['T1'],
    })
    assert 'reviews 必须恰好覆盖' in (validate(missing, WIRE) or '')

    duplicate_rank = good.model_copy(update={'ranking': ['T1', 'T1', 'T3']})
    assert 'ranking 必须恰好覆盖' in (validate(duplicate_rank, WIRE) or '')


def test_validator_requires_verbatim_contradiction_evidence_and_recovers_whitespace():
    aliases = [('T1', 'source_architecture', TEXT_A)]
    validate = semantic.response_validator(aliases)

    bad = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1', contradiction=True, evidence='候选里根本没有这句话')],
        'ranking': ['T1'],
    })
    assert '连续子串' in (validate(bad, WIRE) or '')

    spaced = TEXT_A.replace('\n\n', '\n')
    recovered = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1', contradiction=True, evidence=spaced)],
        'ranking': ['T1'],
    })
    assert validate(recovered, WIRE) is None
    assert recovered.reviews[0].contradictions[0].evidence == TEXT_A


def test_validator_forces_clean_candidates_ahead_of_bad_candidates():
    aliases = [('T1', 'source_architecture', TEXT_A), ('T2', 'payoff_preview', TEXT_B)]
    validate = semantic.response_validator(aliases)
    response = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1'),
                    _response_review('T2', text=TEXT_B, contradiction=True)],
        'ranking': ['T2', 'T1'],
    })
    assert '全部排在' in (validate(response, WIRE) or '')


def test_double_blind_disagreement_materializes_unver(monkeypatch):
    aliases = [('T1', 'source_architecture', TEXT_A),
               ('T2', 'payoff_preview', TEXT_B),
               ('T3', 'scene_punchline', TEXT_C)]
    first = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1'), _response_review('T2', text=TEXT_B),
                    _response_review('T3', text=TEXT_C)],
        'ranking': ['T1', 'T2', 'T3'],
    })
    second = HookSemanticResponse.model_validate({
        'reviews': [_response_review('T1'),
                    _response_review('T2', text=TEXT_B, contradiction=True),
                    _response_review('T3', text=TEXT_C)],
        'ranking': ['T1', 'T3', 'T2'],
    })

    # ⚠ 不再 monkeypatch 合并器：合并的行为本身（恒 UNVER + 逐句不同的 reason）
    #   正是这条用例要看的东西，替身会把它遮掉。
    reviews, decisions, ranking, judge_rankings = semantic.materialize_double_blind(
        first, second, aliases=aliases)
    decision = next(row for row in decisions
                    if row.subject_key == semantic.text_sha256(TEXT_B))
    review = next(row for row in reviews if row.label == 'payoff_preview')
    assert decision.verdict == 'UNVER'
    assert '不一致' in decision.reason
    assert [judge.verdict for judge in decision.judges] == ['PASS', 'FAIL']
    assert review.coverage == 'uncertain'
    assert review.uncertainty_reason == decision.reason
    # ⚠ 排序不再含「PASS 排前面」那一段（合并 verdict 恒定），只按质量分。
    assert set(ranking) == {'source_architecture', 'payoff_preview', 'scene_punchline'}
    assert set(judge_rankings) == {'A', 'B'}


def test_quality_total_is_code_owned_and_fixed_weighted():
    review = _stored_review('source_architecture', TEXT_A)
    assert semantic.quality_score(review) == 8.15
    with pytest.raises(ValueError):
        HookCandidateReview.model_validate({
            **review.model_dump(mode='python'),
            'total_score': 10,
        })


def test_build_prompt_is_anonymous_and_carries_all_quality_references(monkeypatch):
    art = _art()
    store = SimpleNamespace(
        exists=lambda rel: rel == 'stage6/hook.json',
        freshness=lambda _rel: SimpleNamespace(state='fresh'),
        read_own=lambda _model, _rel: (art, {}),
    )
    mat = SimpleNamespace(
        lead=({'output_ordinal': 1, 'text': '这是重写后的第一章。'},),
        story_truth={'entities': {'characters': {'c1': {'canonical': '她'}}},
                     'canon': {'secrets': []}, 'arc': {'ending': '真结局'}},
        story_map=({'chapter': 1, 'events': ['她收到匿名信']},),
        style_packet={'book_surface_habits': ['短句']},
    )
    upstream = SimpleNamespace(src_hook='原作文案金句。', mat=mat)
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(semantic.hook, '_Upstreams', lambda _ctx, _store: upstream)

    system, user, got_art, _up, aliases = semantic.build_prompt(SimpleNamespace())
    assert system == semantic.SEMANTIC_SYS and got_art is art
    assert [item[0] for item in aliases] == ['T1', 'T2', 'T3']
    assert all(real not in user for _neutral, real, _text in aliases), 'approach 名泄露给匿名主编'
    payload = json.loads(user.split('\n\n', 1)[1])
    assert payload['source_reference'] == '原作文案金句。'
    assert payload['rewritten_opening'][0]['text'] == '这是重写后的第一章。'
    assert payload['story_truth']['arc']['ending'] == '真结局'
    assert payload['whole_story_map'][0]['events'] == ['她收到匿名信']
    assert payload['style']['book_surface_habits'] == ['短句']


def test_outcomes_are_unver_when_quality_review_is_missing_or_stale(monkeypatch):
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _store: None)
    got = semantic.outcomes(_Store(), _art())
    assert len(got) == 3 and all(out.unver_why for out in got)
    assert {out.rule for out in got} == {
        'hook_semantic_contradiction', 'hook_candidate_contradiction', 'hook_quality_rank'}

    current = _semantic_artifact(_all_clean_reviews())
    _install(monkeypatch, current)
    got = semantic.outcomes(_Store(fresh=False), _art())
    assert all(out.unver_why and '上游已变化' in out.unver_why for out in got)

    # ⚠ 这里曾有第三段：monkeypatch `_calibration_status` 造 profile 漂移。
    #   自动放行门下线后没有 calibrated 这个状态，也就没有「漂移」这条处境了。


def test_outcomes_keep_quality_and_evidence_visible_although_the_verdict_is_unver(monkeypatch):
    """⚠ 恒 UNVER **不等于**「这一层没产出」——证据与质量排序照常进报告。"""
    _install(monkeypatch, _semantic_artifact(_all_clean_reviews()))

    hard, candidates, quality = semantic.outcomes(_Store(), _art())

    assert hard.unver_why and '没有自动放行门' in hard.unver_why
    assert len(candidates.findings) == 3
    assert quality.findings and '匿名排序' in quality.findings[0].detail


def test_contradiction_keeps_assertion_evidence_and_reason_visible(monkeypatch):
    reviews = _all_clean_reviews()
    reviews[1] = _stored_review('payoff_preview', TEXT_B, contradiction=True)
    _install(monkeypatch, _semantic_artifact(reviews))

    _hard, candidates, _quality = semantic.outcomes(_Store(), _art())
    row = next(item for item in candidates.findings if '她嫁给了仇人' in item.detail)
    assert 'UNVER，不自动阻断' in row.detail
    assert TEXT_B[:6] in row.detail
    assert '全书人物关系与此相反' in row.detail

def test_picking_duplicate_text_does_not_stale_sha_bound_review(monkeypatch):
    artifact = _semantic_artifact(_all_clean_reviews())
    _install(monkeypatch, artifact)
    hard, candidates, quality = semantic.outcomes(
        _Store(), _art(approved='source_architecture'))
    # 定稿是某条候选的**逐字拷贝** ⇒ 它按 sha1 索引到同一条评审，不会掉成 unreviewed。
    assert '定稿文本状态为 uncertain' in (hard.unver_why or '')
    assert candidates.unver_why is None and quality.unver_why is None


def test_one_bad_candidate_is_visible_but_does_not_fail_the_candidate_set(monkeypatch):
    reviews = _all_clean_reviews()
    reviews[1] = _stored_review('payoff_preview', TEXT_B, contradiction=True)
    artifact = _semantic_artifact(
        reviews, ['source_architecture', 'scene_punchline', 'payoff_preview'])
    _install(monkeypatch, artifact)
    hard, info, metric = semantic.outcomes(_Store(), _art())
    # HARD 那一条现在是 `unver` 判据：矛盾**报**而不**拦**，裁决权在人。
    assert not hard.findings and hard.unver_why
    # ⚠ 三条候选**各**出一行，不是只出有矛盾的那一行：合并结论恒 UNVER 之后，
    #   「这条有矛盾线索」与「这条判官说不好说」都是待人核的项，少报一条就是把
    #   「没人验过」说成「没问题」。凭据键是 `<候选>:<文本指纹>`，逐条可单独豁免。
    assert info.rule == 'hook_candidate_contradiction' and len(info.findings) == 3
    assert len({tuple(row.evidence) for row in info.findings}) == 3
    bad = next(row for row in info.findings if TEXT_B[:6] in row.detail)
    assert '她嫁给了仇人' in bad.detail
    assert metric.rule == 'hook_quality_rank' and metric.findings


def test_every_reviewed_candidate_lands_in_uncertain_with_its_own_reason(monkeypatch):
    """⚠⚠ 这里曾有三条用例：`全部存在实质矛盾` 的 HARD、`uncertain 不能定稿`、
    `contradiction 人选了也不行`。三条都建立在 `state == 'clear' | 'contradiction'` 上，
    而那两个状态只由 automated PASS/FAIL 产生 ⇒ **自动放行门下线后不可达**。
    `require_selectable` 随之整函数删除（它恒抛）。

    换成这一条：判过的候选**一律** `uncertain`，但每条带自己的理由——
    「三条都 uncertain」不等于「三条一样」，人要靠 reason 与 judges 分辨。
    """
    reviews = _all_clean_reviews()
    reviews[0] = _stored_review('source_architecture', TEXT_A, uncertain=True)
    reviews[2] = _stored_review('scene_punchline', TEXT_C, contradiction=True)
    _install(monkeypatch, _semantic_artifact(reviews))

    states = {
        text: semantic.assessment_for_text(_Store(), _art(), text)
        for text in (TEXT_A, TEXT_B, TEXT_C)
    }
    assert {row.state for row in states.values()} == {'uncertain'}
    assert not hasattr(semantic.CandidateAssessment, 'selectable')
    assert not hasattr(semantic, 'require_selectable')
    assert states[TEXT_A].reason == '指代无法确认'
    assert len({row.reason for row in states.values()}) > 1, (
        '三条候选共用一句理由 ⇒ 人分不出它们，等于什么都没说')


def test_human_resolution_needs_actor_reason_and_the_exact_current_bytes(monkeypatch):
    """⚠⚠ 这里曾有四条用例守「已有校准 FAIL 的候选，人工也不得绕过」（含历史 FAIL、
    stale 后仍拦、集合不匹配时只拦没改过的那一条）。那道闸的输入是 automated FAIL，
    自动放行门下线后**不可再产**，四条一起退役。

    ⇒ **产品含义变了，这里必须说清**：现在没有任何机制会替人否决一个候选。
    矛盾线索仍逐条呈现（`hook_candidate_contradiction`），但拦住错误定稿的只剩两样：
    人自己读证据，以及下面这道**字节绑定**——改一个字，旧决定立刻失效。
    """
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _store: None)
    assessment, basis = semantic.require_human_resolution(
        _Store(), _art(), TEXT_A, '1', actor='editor-a', reason='逐项对照原文',
        expected_subject_sha256=semantic.text_sha256(TEXT_A))
    assert assessment.state == 'unreviewed' and basis == 'human_verification'

    with pytest.raises(TerminalState, match='已变化'):
        semantic.require_human_resolution(
            _Store(), _art(), TEXT_A, '1', actor='editor-a', reason='已核验',
            expected_subject_sha256='0' * 64)
    for blank in ('   ', ''):
        with pytest.raises(TerminalState, match='actor'):
            semantic.require_human_resolution(
                _Store(), _art(), TEXT_A, '1', actor=blank, reason='已核验',
                expected_subject_sha256=semantic.text_sha256(TEXT_A))

    # 有矛盾线索的候选**也**能被人工收口——那是产品裁决，不是漏了一道闸。
    reviews = _all_clean_reviews()
    reviews[0] = _stored_review('source_architecture', TEXT_A, contradiction=True)
    _install(monkeypatch, _semantic_artifact(reviews))
    picked, basis = semantic.require_human_resolution(
        _Store(), _art(), TEXT_A, '1', actor='editor-a', reason='读过判官证据后仍选它',
        expected_subject_sha256=semantic.text_sha256(TEXT_A))
    assert picked.state == 'uncertain' and basis == 'human_verification'


def test_a_stale_or_mismatched_review_set_makes_every_candidate_unreviewed(monkeypatch):
    """「判过但过期了」与「判的不是这批文本」都必须掉成 `unreviewed`，不许沿用旧结论。"""
    edited_b = TEXT_B + '她没有拆开。'
    artifact = _semantic_artifact(_all_clean_reviews())

    stale = _Store(fresh=False)
    monkeypatch.setattr(semantic, 'latest_semantic', lambda _store: (1, artifact))
    assert {row.state for row in semantic.current_assessments(stale, _art()).values()} \
        == {'unreviewed'}

    current = _art()
    current.candidates[1] = SimpleNamespace(
        approach='payoff_preview', text=edited_b, findings=[])
    assessments = semantic.current_assessments(_Store(), current)
    assert {row.state for row in assessments.values()} == {'unreviewed'}


def test_downstream_requires_the_current_approved_bytes(monkeypatch):
    """下游消费闸剩下的那一条：**resolution 必须仍绑着当前进成书的字节。**

    ⚠ 这条不是「变松了」。原来的三条分支（校准 FAIL 拒绝 / clear 放行 / PASS 漂了兜底）
    全部由 automated 结论驱动，随自动放行门消失；而真正在拦人的一直是这道字节绑定。
    """
    _install(monkeypatch, _semantic_artifact(_all_clean_reviews()))
    with pytest.raises(TerminalState, match='还没定稿'):
        semantic.require_approved_clear(_Store(), _art())

    got = semantic.require_approved_clear(
        _Store(), _art(approved='source_architecture'))
    assert got.state == 'uncertain'

    with pytest.raises(TerminalState, match='不是当前文本字节'):
        semantic.require_approved_clear(
            _Store(), _art(approved='source_architecture', bound_sha256='0' * 64))


def test_build_prompt_refuses_missing_or_stale_hook(monkeypatch):
    monkeypatch.setattr(
        semantic, 'ArtifactStore',
        lambda _ctx: SimpleNamespace(exists=lambda _rel: False),
    )
    with pytest.raises(UpstreamMissing, match='先跑 stage6.hook'):
        semantic.build_prompt(SimpleNamespace())

    stale = SimpleNamespace(
        exists=lambda _rel: True,
        freshness=lambda _rel: SimpleNamespace(state='stale'),
    )
    monkeypatch.setattr(semantic, 'ArtifactStore', lambda _ctx: stale)
    with pytest.raises(TerminalState, match='不要付费审'):
        semantic.build_prompt(SimpleNamespace())
