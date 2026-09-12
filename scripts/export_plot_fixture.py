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
from tests.test_plot_pipeline import (grounded_global, complete_stage5,
                                      complete_stage6, complete_stage7)


def capture_final(book: Path, captured: dict) -> None:
    """新服务从正式盘面读取终态；原 Stage4/首章检查点保留独立键。"""
    from fastapi.testclient import TestClient
    from novel import paths
    from novel.api.app import create_app
    sid = paths.derive_source_meta(book.name)[0]
    with TestClient(create_app(str(book.parent))) as http:
        suffixes = ['', 'approvals', 'run', 'run/metrics', 'inbox', 'stage1',
                    'stage2/global', 'stage2/characters', 'stage2/global-review',
                    'stage6/prepare', 'stage6/prepare/workpack', 'stage6', 'stage7',
                    'approvals/stage6/workpack', 'approvals/stage7/workpack',
                    'approvals/design/workpack', 'approvals/structure/workpack',
                    'stage2/blueprint-review-workpack?scope=chapter%3A1',
                    'stage2/blueprint-review-workpack?scope=cross_chapter',
                    'check/stage2', 'check/stage4', 'check/stage5', 'check/stage6', 'check/stage7']
        for ordinal in (1, 2, 3):
            suffixes.extend(f'{surface}/{ordinal}' for surface in
                            ('reading', 'stage4', 'stage5', 'review', 'stage7'))
        for suffix in suffixes:
            reply = http.get(f'/api/novels/{sid}' + ('/' + suffix if suffix else ''))
            assert reply.status_code == 200, f'{suffix}: {reply.text}'
            captured['final:http:' + (suffix or 'overview')] = reply.json()
        view = captured['final:http:stage7']
        assert captured['final:http:stage6/prepare']['status']['state'] == 'accepted'
        assert captured['final:http:stage6/prepare/workpack']['state']['state'] == 'accepted'
        assert all(captured['final:http:approvals']['gate'][gate] for gate in ('s5', 's6', 's7'))
        for stage in ('stage6', 'stage7'):
            assert captured[f'final:http:approvals/{stage}/workpack']['disposition'] == 'already_current'
        assert view['delivery']['state'] in ('ready', 'ready_with_disclosures')
        preview = http.get(f'/api/novels/{sid}/stage7/book.txt')
        download = http.get(f'/api/novels/{sid}/stage7/book.txt?download=true')
        assert preview.status_code == download.status_code == 200
        assert preview.content == download.content
        assert preview.text == (book / paths.book_txt_rel(book)).read_text(encoding='utf-8')
        captured['final:http:stage7/book.txt'] = preview.text
        captured['metadata'] = {
            'synthetic': True, 'sid': sid,
            'source': '正式 producer、独立 Prepare 接受、Hook A/B 与最终审阅、Stage7 组装后的 HTTP 重启读回',
            'scope': '模型运输为确定性合成响应，不代表真实模型质量或成本；只读样本不模拟新审批',
        }


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
            book = produce_through_first_chapter(
                Path(directory), patch, distributed=True,
                grounding=grounded_global(), capture=captured)
            complete_stage5(book)
            complete_stage6(book)
            complete_stage7(book)
            capture_final(book, captured)
    output = root / 'web/src/fixtures/plot_v1/flow.json'
    output.write_text(json.dumps(captured, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8', newline='')
    review = {
        'chapter': captured['http:stage2/blueprint-review-workpack?scope=chapter%3A1'],
        'cross': captured['http:stage2/blueprint-review-workpack?scope=cross_chapter'],
        'reading': captured['http:reading/1'],
        'prepare': captured['final:http:stage6/prepare/workpack'],
        'hook': captured['final:http:approvals/stage6/workpack'],
        'rename': captured['final:http:approvals/stage7/workpack'],
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
