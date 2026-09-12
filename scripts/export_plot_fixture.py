"""运行证据自洽的免费贯通夹具，从真实 HTTP/审阅返回值导出前端示例。

从仓根用 novel_refactor 环境执行 python -m scripts.export_plot_fixture。
所有模型边界都是 FakeTransport，目录仅保存合成书；
输出用于前端消费测试，不能作为真实模型质量、成本或生产配置的证明。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import pytest

from tests.test_plot_design import produce_through_first_chapter
from tests.test_plot_pipeline import grounded_global


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-root', type=Path, default=root / '.test_cache',
                        help='合成书临时父目录；Windows 长路径使用 E:/_tr，勿与 pytest 同时运行')
    args = parser.parse_args()
    scratch = args.scratch_root.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    captured: dict = {}
    with tempfile.TemporaryDirectory(prefix='plot-fixture-', dir=scratch) as directory:
        with pytest.MonkeyPatch.context() as patch:
            produce_through_first_chapter(
                Path(directory), patch, distributed=True,
                grounding=grounded_global(), capture=captured)
    output = root / 'web/src/fixtures/plot_v1/flow.json'
    output.write_text(json.dumps(captured, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8', newline='')
    review = {
        'chapter': captured['http:stage2/blueprint-review-workpack?scope=chapter%3A1'],
        'cross': captured['http:stage2/blueprint-review-workpack?scope=cross_chapter'],
        'reading': captured['http:reading/1'],
        'enum_values': {key: captured['api:enums'][key]
                        for key in ('plot_review_dimensions', 'plot_dimension_states')},
    }
    review_output = output.with_name('review.json')
    review_output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8', newline='')
    print(f'已从免费贯通链导出 {len(captured)} 份真实响应：{output}')
    print(f'已从同次 HTTP 响应导出人物呈现与范围审阅：{review_output}')


if __name__ == '__main__':
    main()
