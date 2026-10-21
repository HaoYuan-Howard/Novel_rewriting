"""Stage5 当前 story-v1 公开写作入口。

请求、短尾及字节回执证明由共同纯边界提供，Stage7 使用同一份判据，
不反向调用 Stage5 实现。付费与落盘由外层明确承担。
"""
from novel.core.story_prose import (
    OPENING_PARAGRAPH_LIMIT, TAIL_CHAR_LIMIT, ProseRejected,
    accept_body, accept_hook, apply_opening_patch, build_body_request,
    build_hook_request, nonspace_chars, opening_patch_request,
    opening_patch_schema, parse_opening_patch, parse_prose_answer,
    prior_tail, prose_answer_schema, sha256_text, stable_sha256,
    validate_body, validate_opening_patch_answer, validate_prose_answer, verify_body, verify_hook,
)

__all__ = [
    'OPENING_PARAGRAPH_LIMIT', 'TAIL_CHAR_LIMIT', 'ProseRejected',
    'accept_body', 'accept_hook', 'apply_opening_patch', 'build_body_request',
    'build_hook_request', 'nonspace_chars', 'opening_patch_request',
    'opening_patch_schema', 'parse_opening_patch', 'parse_prose_answer',
    'prior_tail', 'prose_answer_schema', 'sha256_text', 'stable_sha256',
    'validate_body', 'validate_opening_patch_answer', 'validate_prose_answer', 'verify_body', 'verify_hook',
]
