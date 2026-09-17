"""从免费联合首抽及真实只读投影导出 Stage2 Core 界面材料。

仅使用确定性合成正文和 FakeTransport；不访问 provider，不代表真实小说质量或成本。
独立临时书在 worktree 的 .test_cache 内创建并清理，不占 pytest 的 basetemp。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from novel.views.stage2 import character_registry_view, global_view
from tests.stage2_fixtures import knowledge_response
from tests.test_stage2_analyze import _ctx, _generate, _response


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    scratch = root / '.test_cache'
    if os.name == 'nt':
        scratch = Path('\\\\?\\' + str(scratch))
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stage2-core-fixture-', dir=scratch) as directory:
        ctx = _ctx(Path(directory))
        response = _response()
        response['global'] = knowledge_response()
        _generate(ctx, response)
        captured = {
            'metadata': {
                'synthetic': True,
                'source': 'analyze.run + GlobalView/CharacterRegistryView',
                'schemas': {'characters': 2, 'global': 7},
                'scope': '免费合成数据与真实 producer/Store/read model；不代表真实模型质量或成本',
            },
            'global': global_view(directory, ctx.sid).model_dump(mode='json', by_alias=True),
            'characters': character_registry_view(directory, ctx.sid).model_dump(mode='json', by_alias=True),
        }
    output = root / 'web/src/fixtures/plot_v1/core.json'
    output.write_text(json.dumps(captured, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8', newline='')
    print(f'已导出独立 Stage2 Core 只读样例：{output}')


if __name__ == '__main__':
    main()
