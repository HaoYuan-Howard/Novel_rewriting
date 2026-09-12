"""Prepare 单次 Executor 的原请求与已购回答恢复声明；通用协议只由 agent_runs 实现。"""
from contracts.artifacts import STAGE6_PREPARE, STAGE6_PREPARE_REVIEW
from novel import paths
from novel.llm import agent_runs

NODE = 'stage6.prepare'
RECOVERY_POLICY = 'stage6-prepare-v1'


def run_spec():
    return agent_runs.SingleSessionRunSpec(label='Stage6 文案准备', node=NODE, session_owner=NODE,
        purpose=NODE, chapter=None, phase='prepare', recovery_policy=RECOVERY_POLICY,
        session_isolation='per-node', allow_response_repair=True, initial_artifacts=(STAGE6_PREPARE,
            paths.invalid_of(STAGE6_PREPARE), STAGE6_PREPARE_REVIEW),
        run_path=lambda ctx: paths.stage6_prepare_run_path(ctx.novel_dir, ctx.run_id),
        checkpoint_dir=lambda ctx: paths.stage6_prepare_run_path(ctx.novel_dir, ctx.run_id) / 'checkpoints')
