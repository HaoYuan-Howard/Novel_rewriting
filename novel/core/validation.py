"""接受诊断的稳定身份；兼容字符串展示，控制流不得从新诊断的文案猜位置。"""
from __future__ import annotations

import hashlib
import json


class ValidationMessage(str):
    """临时校验值，不落领域schema；缓存重放时从正式输入重新计算identity。"""

    def __new__(cls, message: str, identity: object):
        value = super().__new__(cls, message)
        value.identity = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')).hexdigest()
        return value


class ValidationIssue(ValidationMessage):
    """判据生成的派生诊断；身份与文案分离，责任和位置不由控制器反向猜测。"""

    def __new__(cls, message: str, *, code: str, location: tuple,
                owner: str, scope: str = 'book', context: dict | None = None):
        value = super().__new__(cls, message, (code, owner, scope, location))
        value.code, value.location = code, location
        value.owner, value.scope = owner, scope
        value.context = dict(context or {})
        return value


def validation_identity(message: str) -> str:
    if isinstance(message, ValidationMessage):
        return message.identity
    # 存量字符串判据的集中兼容；新材料归因禁止走该分支。
    return str(message).split('：', 1)[0][:80]
