"""只读报告当前输出根的首次语义候选、材料续答、窄修与成本；不调用模型。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from novel.story_metrics import story_metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=os.environ.get('NOVEL_OUTPUT'))
    parser.add_argument('--sid', required=True)
    args = parser.parse_args(argv)
    if args.base is None:
        parser.error('必须显式提供--base或NOVEL_OUTPUT，没查')
    print(json.dumps(story_metrics(args.base, args.sid), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
