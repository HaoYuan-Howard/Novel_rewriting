#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FC-13 逐节点 effort 换臂 runner：**0 成本预览 → 显式 `--execute`**。

`reasoning_effort` 的唯一旋钮是 `contracts/invariants.py::NodeSpec.effort`
（`novel/cli/main.py::_llm_client` 从那里取，没有环境变量也没有 CLI flag）。
做 A/B 有两条路，本文件选了后一条：

  ① 直接改 `contracts/invariants.py` 那一格，跑完再改回来。
     不选的理由是**忘了改回来不会有任何声音**——此后每一次真跑都静默换档，
     而账本要事后逐行看 `reasoning_effort` 才看得出来。
  ② 进程内就地覆盖那一格，只活在这一个进程里。⇒ 仓里那份恒是 `None`（即 high），
     实验臂与交付跑物理上不可能混。

⚠⚠ **换臂之前先换 `NOVEL_CACHE_DIR`，否则这一臂可能一次都不发。**
  现役 client 不再自动降低 effort；缓存仍按完整请求配置分键，并会在上线前横扫
  同配置的 Flash→Pro 模型链。若目录里已有这一臂的响应，实验会直接命中缓存，
  **一次新样本都不发**。2026-08-21 实测踩过一次：27 次调用全是缓存命中。
  ⇒ 每条实验臂配一个独立缓存目录（`NOVEL_CACHE_DIR=<主仓>/.llm_cache_ab_<臂名>`），
  跑完看账本 `reasoning_effort` 那一列**全是你要的那一档**才算数。

⚠ 本工具**不构造 argv**。`--` 之后原样透传给 `novel.cli.main::main`，
  那一串必须逐字取自 `python -m novel.cli preview`（skill 工作流契约：
  「不要凭文档手拼真实 argv」）。付费确认仍由 CLI 自己的 `--yes` 把关，本工具不代批。

⚠ 覆盖值的取值面不在这里判——转交 `novel/llm/client.py::rungs_from_effort`
  （`medium`/`xhigh` 被服务端静默映射回 `high`，故关闭式拒绝；`max` 是合法的
  单一显式实验配置）。若主模型是 Flash，模型兜底仍可能发生；验收须同时核 ledger 的
  `model` 与产物 `adopted_models`，不能把 Pro 兜底样本归进 Flash 臂。

用法：
  # 0 成本预览：只摊开覆盖后的显式配置与将要透传的 argv，不跑
  python -X utf8 tools/effort_ab.py --set stage7.rename=low -- \\
      run stage7.rename --sid <sid> --base <臂 base> --yes

  # 真跑（`--execute` 是本工具这一层的显式闸；付费闸仍是被透传的 `--yes`）
  python -X utf8 tools/effort_ab.py --set stage7.rename=low --execute -- \\
      run stage7.rename --sid <sid> --base <臂 base> --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import NODES                 # noqa: E402
from novel.llm.client import rungs_from_effort         # noqa: E402


def _parse_set(spec: str) -> tuple[str, str]:
    node, sep, effort = spec.partition('=')
    if not sep:
        raise SystemExit(f'--set 的形状是 <node>=<effort>，实得 {spec!r}')
    if node not in NODES:
        raise SystemExit(f'未知节点 {node!r}；取值面是 contracts/invariants.py::NODES')
    if not NODES[node].paid:
        raise SystemExit(
            f'{node} 的默认路径不调 LLM（NodeSpec.paid=False），覆盖 effort 无处生效')
    # 取值面由生产那一处判，本工具不复制一份判据。
    rungs_from_effort(effort)
    return node, effort


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--set', action='append', dest='sets', default=[], required=True,
                    metavar='NODE=EFFORT', help='逐节点覆盖，可多次；FC-13 是 OFAT，正常只给一条')
    ap.add_argument('--execute', action='store_true',
                    help='真跑。不给时只摊开覆盖与 argv，一个请求都不发')
    ap.add_argument('cli_argv', nargs=argparse.REMAINDER,
                    help='`--` 之后原样透传给 novel.cli；逐字取自 preview')
    a = ap.parse_args(argv)

    overrides = [_parse_set(s) for s in a.sets]
    passthrough = a.cli_argv[1:] if a.cli_argv[:1] == ['--'] else a.cli_argv
    if not passthrough:
        raise SystemExit('`--` 之后要给一条 novel.cli 的 argv（逐字取自 preview）')

    print('# FC-13 effort 换臂')
    for node, effort in overrides:
        was = NODES[node].effort
        print(f'  {node}: effort {was!r} → {effort!r}')
        print(f'    显式配置 {list(rungs_from_effort(was))} → '
              f'{list(rungs_from_effort(effort))}')
    print(f'  透传 argv: {" ".join(passthrough)}')
    from novel.config import load_config
    print(f'  缓存目录: {load_config().cache_dir}')
    print('  ⚠ 上面这个目录里若已有本臂同请求的 Flash/Pro 响应，本臂可能一次都不发；'
          '换臂请一并换 NOVEL_CACHE_DIR，并核 ledger.model 与 adopted_models。')
    print(f'  真跑: {"是（--execute）" if a.execute else "否（0 成本预览）"}')
    if not a.execute:
        print('  ⚠ 未加 --execute，到此为止；仓内 NodeSpec.effort 一个字没动。')
        return 0

    for node, effort in overrides:
        NODES[node] = NODES[node]._replace(effort=effort)      # 只活在本进程

    from novel.cli.main import main as cli_main
    return cli_main(passthrough)


if __name__ == '__main__':
    raise SystemExit(main())
