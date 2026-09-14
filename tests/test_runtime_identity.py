"""退役 skill 后，历史 canonical 字节面不重签，新身份仍关闭式校验。"""
from __future__ import annotations

import hashlib
import json

import pytest

from contracts.schema.orchestration import AgentRunStart, ExecutionEpoch
from novel.commanders import context as commander_context
from tests.test_commander_context import _profiles


def test_legacy_start_and_epoch_roundtrip_preserve_the_original_hash_surface(tmp_path):
    historical = dict(sid='42', base=str(tmp_path.resolve()), argv=['runtime'],
                      prompt='旧运行原文', authorized=True, egress_authorized=True,
                      code_version='old-code', skill_sha256='a' * 64,
                      profiles=_profiles().model_dump(mode='json'))
    start = AgentRunStart.model_validate(historical)
    assert start.identity_version == 1
    assert start.model_dump(mode='json') == historical
    epoch_wire = dict(epoch_id='epoch-1', ordinal=1, reason='initial', argv=['runtime'],
                      prompt_sha256='b' * 64, protocol_version='test-v1', cli_version=None,
                      code_version='old-code', skill_sha256='a' * 64,
                      session_run_id='producer-1', resume_provider_session_id=None)
    epoch = ExecutionEpoch.model_validate(epoch_wire)
    assert epoch.identity_version == 1
    assert epoch.model_dump(mode='json') == epoch_wire
    original = dict(protocol=commander_context.CONTRACT_V2_PROTOCOL,
                    run_id='parent-1', epoch=epoch_wire)
    digest = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()
    assert commander_context.epoch_sha256(run_id='parent-1', epoch=epoch) == digest
    current = ExecutionEpoch.model_validate(dict(epoch_wire, identity_version=2, skill_sha256=None))
    assert current.model_dump(mode='json')['identity_version'] == 2
    assert commander_context.epoch_sha256(run_id='parent-1', epoch=current) != digest
    with pytest.raises(ValueError, match='不接受 skill'):
        ExecutionEpoch.model_validate(dict(epoch_wire, identity_version=2))
    with pytest.raises(ValueError, match='不接受 skill'):
        AgentRunStart.model_validate(dict(historical, identity_version=2))
