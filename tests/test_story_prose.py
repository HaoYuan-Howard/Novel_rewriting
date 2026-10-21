"""story-v3 顺序写作、窄修及交付证明的确定性反例。

所有响应来源显式使用 synthetic 标识；不调用真实模型，不读取本地小说。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema.story_prose import (
    ChapterBody, KeepParagraph, NeedsScope, OpeningPatch, ReplaceParagraph,
)
from contracts.schema.target_story import ChapterPacket, HookPacket
from novel.stages.stage5.story import (
    ProseRejected, accept_body, accept_hook, apply_opening_patch,
    build_body_request, build_hook_request, nonspace_chars, opening_patch_request,
    parse_opening_patch, parse_prose_answer, prior_tail, sha256_text,
    stable_sha256, validate_body, validate_opening_patch_answer, validate_prose_answer, verify_body,
)
from novel.stages.stage7.story import assemble_story, verify_final_story


def packet(chapter=1, *, count=2, **changes):
    fields = dict(contract_version='story-v3', chapter_ordinal=chapter, source_chapter_count=count,
                  length_budget=100, min_chars=1, max_chars=5000,
                  participants=('e1',), names={'e1': '沈青'}, entry_state=(), exit_state=(),
                  actions=('沈青推门，向姐姐说明来意。',), must_realize=('说明来意。',),
                  must_respect=('沈青尚不知道密信是谁送的。',), continuity=('从院门进屋。',),
                  creative_guidance=('可自由选择动作、对话与停顿。',),
                  background=('屋外正在下雨。',), guard_only=('不能提前回答送信人身份。',))
    fields.update(changes)
    return ChapterPacket(**fields)


def hook_packet():
    return HookPacket(contract_version='story-v3', length_budget=60, min_chars=1, max_chars=5000,
                      names={'e1': '沈青'}, must_realize=('一封来历不明的信。',),
                      must_respect=(), continuity=(), creative_guidance=('停在她拆信前。',),
                      background=(), guard_only=('不揭示送信人。',))


def accepted(text, current_packet, *, prior=None):
    return accept_body(text, build_body_request(current_packet, prior_body=prior),
                       response_id='synthetic-response', provider='synthetic', model='synthetic')


def patch_answer(request, *, replacement=None):
    return OpeningPatch(kind='patch', paragraphs=tuple(
        ReplaceParagraph(kind='replace', handle=row.handle, text=replacement)
        if index == 0 and replacement is not None else KeepParagraph(kind='keep', handle=row.handle)
        for index, row in enumerate(request.paragraphs)))


def test_request_requires_sequential_context_and_delivers_six_sections():
    first = build_body_request(packet())
    assert first.prior_tail == '' and first.prior_tail_status == 'first_chapter'
    for heading in ('必须写出', '必须遵守', '连续性', '表达空间', '背景', '禁止越界'):
        assert f'【{heading}】' in first.user
    assert 'e1' not in first.user
    with pytest.raises(ValueError, match='前章正文'):
        build_body_request(packet(2))
    with pytest.raises(ValueError, match='第一章'):
        build_body_request(packet(), prior_body='未授权前文')
    states = ('沈青的入场站位未知，不得补猜。', '先前的通行许可已明确解除。')
    rendered = build_body_request(packet(entry_state=states)).user
    assert all(state in rendered for state in states)


def test_tail_uses_whole_paragraph_suffix_and_exact_newline_bytes():
    previous = '不送入' * 300 + '\r\n\r\n' + '雨' * 400 + '\r\n\r\n' + '风' * 400 + '\r\n'
    tail = prior_tail(previous)
    assert tail == '雨' * 400 + '\r\n\r\n' + '风' * 400 + '\r\n'
    assert nonspace_chars(tail) == 800
    assert previous.endswith(tail)
    request = build_body_request(packet(2), prior_body='字' * 801)
    assert request.prior_tail == ''
    assert request.prior_tail_status == 'no_complete_paragraph_fits'


def test_only_delivered_prior_tail_changes_request_identity():
    first = '旧' * 900 + '\n\n保留的最后一段。\r\n'
    different_prefix = '新' * 900 + '\n\n保留的最后一段。\r\n'
    a = build_body_request(packet(2), prior_body=first)
    b = build_body_request(packet(2), prior_body=different_prefix)
    assert a.request_id == b.request_id
    c = build_body_request(packet(2), prior_body=first.replace('最后', '末尾'))
    assert c.request_id != a.request_id
    assert c.prior_tail_sha256 != a.prior_tail_sha256


def test_tagged_design_gap_and_duplicate_json_keys():
    answer = parse_prose_answer({'kind': 'needs_design', 'problems': [
        {'section': 'continuity', 'item_no': 1, 'detail': '入场时在门外，下一行动却直接拿起屋内信件。'}]})
    assert answer.kind == 'needs_design'
    issues = validate_prose_answer(answer, build_body_request(packet()))
    assert issues[0].owner == 'stage4' and issues[0].code == 'needs_design'
    assert '[must_realize:1]' in build_body_request(packet()).user
    with pytest.raises(ValidationError, match='item_no'):
        parse_prose_answer({'kind': 'needs_design', 'problems': [
            {'section': 'continuity', 'detail': '没有给明确位置'}]})
    out_of_range = parse_prose_answer({'kind': 'needs_design', 'problems': [
        {'section': 'continuity', 'item_no': 99, 'detail': '越出实际送达条目'}]})
    assert validate_prose_answer(out_of_range, build_body_request(packet()))[0].code == 'design_location'
    with pytest.raises(ValueError, match='重复 JSON 键'):
        parse_prose_answer('{"kind":"prose","kind":"needs_design","text":"正文"}')
    with pytest.raises(ValidationError):
        parse_prose_answer({'kind': 'prose', 'text': '正文', 'request_id': '模型自造'})


def test_receipt_binds_current_prompt_packet_tail_and_body_without_quality_claim():
    current_packet = packet(2)
    body = accepted('沈青推门进来。', current_packet, prior='姐姐在门内等她。')
    assert verify_body(body, current_packet, prior_body='姐姐在门内等她。') == ()
    assert body.receipt.semantic == 'unver'
    assert body.receipt.provider == 'synthetic'
    assert body.receipt.body_sha256 == sha256_text(body.text)
    assert verify_body(body, current_packet, prior_body='姐姐已经离开。')
    altered = body.model_copy(update={'text': '另写的一章。'})
    assert verify_body(altered, current_packet, prior_body='姐姐在门内等她。')
    new_packet = packet(2, must_respect=('沈青已经知道送信人。',))
    assert verify_body(body, new_packet, prior_body='姐姐在门内等她。')
    missing_version = body.model_dump(mode='json')
    del missing_version['contract_version']
    with pytest.raises(ValidationError):
        ChapterBody.model_validate(missing_version)
    for ordinal in (True, 2.0, '2', 0):
        invalid = body.model_dump(mode='json')
        invalid['receipt']['chapter_ordinal'] = ordinal
        with pytest.raises(ValidationError):
            ChapterBody.model_validate(invalid)


def test_truncated_empty_and_length_rejection_do_not_create_receipts():
    request = build_body_request(packet(length_budget=4, min_chars=3, max_chars=6))
    for text, finish in (('两字', 'stop'), ('足够字数', 'length'), ('\n\t', 'stop')):
        with pytest.raises(ProseRejected):
            accept_body(text, request, response_id='synthetic', provider='synthetic',
                        model='synthetic', finish_reason=finish)
    altered = request.model_copy(update={'user': request.user + '改变材料'})
    assert any(issue.code == 'request_identity' for issue in validate_body('足够字数', altered))


def test_opening_patch_preserves_unapproved_bytes_and_records_actual_source():
    current_packet = packet()
    original = '\r\n'.join(f'第{n}个自然段，保留字节。  ' for n in range(1, 11)) + '\r\n\r\n'
    chapter = accepted(original, current_packet)
    request = opening_patch_request(chapter, current_packet, reason='首段接续动作不自然。')
    assert len(request.paragraphs) == 8
    answer = patch_answer(request, replacement='沈青接着姐姐的话，迈过门槛。')
    patched = apply_opening_patch(chapter, answer, request, current_packet,
                                  response_id='synthetic-patch-response', provider='synthetic', model='synthetic')
    suffix = original[original.index('\r\n'):]
    assert patched.text == '沈青接着姐姐的话，迈过门槛。' + suffix
    assert original == chapter.text
    assert patched.receipt.request_id == chapter.receipt.request_id
    assert patched.receipt.patches[0].response_id == 'synthetic-patch-response'
    assert patched.receipt.patches[0].request_id == request.request_id
    assert verify_body(patched, current_packet) == ()


def test_patch_rejects_ninth_paragraph_duplicates_newlines_and_stale_base():
    current_packet = packet()
    chapter = accepted('\n\n'.join(f'段落{n}' for n in range(1, 10)), current_packet)
    request = opening_patch_request(chapter, current_packet, reason='衔接修订。')
    answer = patch_answer(request)
    bad = answer.model_copy(update={'paragraphs': (*answer.paragraphs[:-1],
                                                   KeepParagraph(kind='keep', handle='p009_unauthorized'))})
    with pytest.raises(ProseRejected, match='patch_scope'):
        apply_opening_patch(chapter, bad, request, current_packet,
                            response_id='synthetic', provider='synthetic', model='synthetic')
    assert any(issue.code == 'patch_scope' for issue in
               validate_opening_patch_answer(bad, request, chapter, current_packet))
    assert validate_opening_patch_answer(answer, request, chapter, current_packet) == ()
    with pytest.raises(ValidationError, match='不得增加换行'):
        ReplaceParagraph(kind='replace', handle=request.paragraphs[0].handle, text='一段\n新段')
    with pytest.raises(ValidationError, match='不得增加换行'):
        ReplaceParagraph(kind='replace', handle=request.paragraphs[0].handle, text='一段\u2028新段')
    changed = accepted(chapter.text + '改变基底', current_packet)
    with pytest.raises(ProseRejected, match='stale_patch'):
        apply_opening_patch(changed, answer, request, current_packet,
                            response_id='synthetic', provider='synthetic', model='synthetic')


def test_needs_scope_never_changes_body_or_claims_success():
    current_packet = packet()
    chapter = accepted('沈青停在门口。', current_packet)
    request = opening_patch_request(chapter, current_packet, reason='站位矛盾。')
    answer = parse_opening_patch({'kind': 'needs_scope', 'detail': '第九段仍写她在院外，需回设计层。'})
    assert isinstance(answer, NeedsScope)
    before = stable_sha256(chapter)
    assert apply_opening_patch(chapter, answer, request, current_packet,
                               response_id='synthetic', provider='synthetic', model='synthetic') == answer
    assert stable_sha256(chapter) == before


def test_changed_opening_outside_prior_tail_does_not_invalidate_next_chapter():
    one = packet(1)
    original = '开头段。\n\n' + '\n\n'.join('中' * 450 for _ in range(9)) + '\n\n末尾段。'
    chapter = accepted(original, one)
    two = packet(2)
    next_chapter = accepted('姐姐接过信。', two, prior=original)
    request = opening_patch_request(chapter, one, reason='开头语气。')
    patched = apply_opening_patch(chapter, patch_answer(request, replacement='修订开头段。'), request, one,
                                  response_id='synthetic-patch', provider='synthetic', model='synthetic')
    assert prior_tail(patched.text) == prior_tail(original)
    assert verify_body(next_chapter, two, prior_body=patched.text) == ()


def test_stale_prior_tail_can_be_rebound_only_by_real_patch_lineage():
    current_packet = packet(2)
    old_prior, new_prior = '姐姐在门外。', '姐姐已经进屋。'
    chapter = accepted('沈青在门外迎上姐姐。\n\n她递出了信。', current_packet, prior=old_prior)
    assert verify_body(chapter, current_packet, prior_body=new_prior)
    request = opening_patch_request(chapter, current_packet, prior_body=new_prior,
                                     reason='前章已进屋，首段迎面站位需衔接。')
    patched = apply_opening_patch(chapter,
        patch_answer(request, replacement='沈青跟着姐姐进屋。'), request, current_packet,
        prior_body=new_prior, response_id='synthetic-seam-patch', provider='synthetic', model='synthetic')
    assert patched.receipt.request_id == chapter.receipt.request_id
    assert patched.receipt.original_prior_tail_sha256 == chapter.receipt.prior_tail_sha256
    assert patched.receipt.prior_tail_sha256 == sha256_text(new_prior)
    assert patched.receipt.patches[-1].response_id == 'synthetic-seam-patch'
    assert verify_body(patched, current_packet, prior_body=new_prior) == ()
    assert verify_body(patched, current_packet, prior_body=old_prior)


def test_assembly_checks_final_bytes_and_complete_order_without_mutating_prose():
    packets = (packet(1), packet(2))
    one = accepted('  第一章正文。\r\n', packets[0])
    two = accepted('第二章正文。  ', packets[1], prior=one.text)
    hp = hook_packet()
    hook = accept_hook('开篇正文。', build_hook_request(hp), response_id='synthetic-hook',
                       provider='synthetic', model='synthetic')
    args = dict(title='合成书', source_chapter_count=2, packets=packets,
                bodies=(one, two), hook=hook, hook_packet=hp)
    final = assemble_story(**args)
    assert final.text.endswith(two.text) and one.text in final.text
    assert tuple(row.chapter_ordinal for row in final.manifest.chapters) == (1, 2)
    assert final.manifest.final_text_sha256 == sha256_text(final.text)
    assert final.manifest.semantic == 'unver'
    assert assemble_story(**args) == final
    assert verify_final_story(final, **args) == ()
    assert verify_final_story(final.model_copy(update={'text': final.text + '偷偷追加'}), **args)
    for wrong in ((two, one), (one,), (one, one)):
        with pytest.raises(ProseRejected, match='body_chapter_set'):
            assemble_story(**{**args, 'bodies': wrong})
    tampered = two.model_copy(update={'text': two.text + '私改'})
    with pytest.raises(ProseRejected, match='receipt_identity'):
        assemble_story(**{**args, 'bodies': (one, tampered)})
