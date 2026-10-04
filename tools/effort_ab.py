#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐节点思考配置：默认免费预览，--execute 才调用正式 CLI。

本工具只选择配置，不宣称已完成 A/B：实验须另冻结相同正式输入、代码、协议、
停止规则和独立输出根，保留全部失败与缓存采用。配置进入真实请求身份，不能通过
修改 NODES 或缓存目录伪造实验。无自动降档、换模型或追加额度。

示例：python tools/effort_ab.py --set str06.stage2.names=low
  --model deepseek-flash --thinking on -- run str06.stage2.names SID --base ROOT --yes --allow-egress
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.schema.orchestration import LegacyLlmProfile
from novel.cli import main as cli
from novel.orchestrator import nodes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', required=True, metavar='NODE=EFFORT')
    parser.add_argument('--model', required=True)
    parser.add_argument('--thinking', required=True, choices=('on', 'off'))
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('cli_argv', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    node, sep, effort = args.set.partition('=')
    if not sep:
        parser.error('--set 必须是 NODE=EFFORT')
    passthrough = args.cli_argv[1:] if args.cli_argv[:1] == ['--'] else args.cli_argv
    invocation = cli._parser().parse_args(passthrough)
    if invocation.cmd != 'run' or invocation.node != node:
        parser.error('一次只比较一个节点；透传必须是同一节点的 run 命令')
    if any(getattr(invocation, key, None) is not None for key in (
            'request_profile', 'executor_backend', 'legacy_provider', 'legacy_model',
            'legacy_effort', 'legacy_thinking', 'reasoning_effort')):
        parser.error('请求配置由本工具显式提供，透传命令不能再声明第二份')
    if any(getattr(invocation, key, None) for key in (
            'wire_dir', 'adopt_root', 'resample', 'reason', 'body_chapters', 'run_id')):
        parser.error('本工具不混入历史采用、重采、选章或运行身份覆盖')
    profile = nodes.resolve_request_profile(node, LegacyLlmProfile(
        provider='deepseek', model=args.model, reasoning_effort=effort, thinking_mode=args.thinking))
    invocation.request_profile = profile.model_dump_json()
    print('请求配置：' + profile.model_dump_json())
    cli._cmd_preview(invocation)
    if not args.execute:
        print('免费预览结束；没有请求模型。')
        return 0
    # 不代填 --yes / --allow-egress，正式CLI仍独立检查两项授权。
    return cli.main([*passthrough, '--request-profile', profile.model_dump_json()])


if __name__ == '__main__':
    raise SystemExit(main())
