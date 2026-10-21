"""同根升级已购产物的免费换算层（S-02 登记面；2026-10-10 裁决③）。

只有登记过的**无损**换算才允许上桌：``convert`` 是纯函数，把升级归档的旧
canonical 产物映射为当前 wire；换出的 wire 经既有 ``--wire-dir`` 免费采用通路
（同验收判据、零模型调用、F7a 历史采用）回到正式位，不新建第二套采用面。
缺一个语义字段都不得机械补出（I-21）：rc22→rc23 的 Hook 缺 emphasis／
transition／reader_expectation／local_focus 四项设计决定，登记为**不可换算**
（见 ``contracts/contract_upgrades.py`` 模块头），走重建＋轮次返还。

每个登记条目自带删除条件（``note``）：升级边退役、或源 wire 协议从现役
请求面消失时删除。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable

from novel.errors import ProvenanceRefused, TerminalState


class ConverterRefused(TerminalState):
    """明确停点：未登记的换算边或源证据不闭合。"""


@dataclass(frozen=True)
class WireConverter:
    upgrade_id: str
    node: str
    #: wire 文件名采用的 owner（与 chain 的 ``wire_path`` 命名一致）。
    owner: str
    #: 被换算的归档产物相对路径（须在升级计划 archive 表内）。
    source_rel: str
    #: 登记理由与删除条件；登记面可审计。
    note: str
    #: 归档 canonical payload（dict）→ 当前 wire（dict）；纯函数，不落盘。
    convert: Callable[[dict], dict]


#: (upgrade_id, node) → 登记换算器。默认为空：现役升级边均含语义缺口，
#: 不满足无损条件；新边满足时在此登记。
CONVERTERS: dict[tuple[str, str], WireConverter] = {}


def upgrade_id_for_node(node: str) -> str:
    """节点 → 唯一登记的升级边 id；多边或无边都显式拒绝。"""
    found = sorted({upgrade_id for (upgrade_id, n) in CONVERTERS if n == node})
    if len(found) != 1:
        raise ConverterRefused(
            f'节点 {node} 的换算边不唯一或未登记：{found or "（无）"}；'
            '须显式指定升级边后再换算')
    return found[0]


def convert_archived_wire(repository, *, upgrade_id: str, node: str,
                          wire_dir: Path, actor: str, reason: str) -> dict:
    """把升级归档的已购产物免费换算成当前 wire，落到 ``wire_dir`` 供采用。

    步骤全部确定性：找已应用回执 → 按计划核对归档字节 → 纯函数换算 →
    写 ``{owner}_wire.json`` 与换算留痕。随后的正式采用走
    ``run --node <node> --wire-dir <wire_dir>``（同一验收判据，零调用）。
    """
    if not actor.strip() or not reason.strip():
        raise ConverterRefused('免费换算必须登记执行者与理由')
    converter = CONVERTERS.get((upgrade_id, node))
    if converter is None:
        registered = sorted(f'{uid}:{n}' for uid, n in CONVERTERS)
        raise ConverterRefused(
            f'({upgrade_id}, {node}) 未登记无损换算器；当前登记边：'
            f'{registered or "（无）"}。缺语义字段的边不得机械补出（I-21），'
            '走重建；重建轮次由升级返还（裁决「升级清账」）')
    applied = []
    for path in sorted((repository.root / 'runtime' / 'upgrades').glob('*/receipt.json')):
        receipt = repository.inspect(path.relative_to(repository.root).as_posix())
        if receipt.get('status') == 'applied' and \
                receipt.get('record', {}).get('plan', {}).get('upgrade') == upgrade_id:
            applied.append(receipt)
    if len(applied) != 1:
        raise ConverterRefused(
            f'期望恰一份已应用回执，实得 {len(applied)}；换算必须绑定唯一升级')
    receipt = applied[0]
    subject = receipt['subject_sha256']
    plan_files = {row['rel']: row['sha256']
                  for row in receipt['record']['plan']['archive']}
    source = repository.path(f'runtime/upgrades/{subject}/files/{converter.source_rel}')
    if converter.source_rel not in plan_files or not source.is_file():
        raise ProvenanceRefused(f'换算源不在归档表或字节缺失：{converter.source_rel}')
    if repository.file_sha256(source) != plan_files[converter.source_rel]:
        raise ProvenanceRefused('换算源字节与升级计划不符')
    payload = repository.read_external(source)
    wire = converter.convert(payload)
    wire_dir = Path(wire_dir)
    wire_dir.mkdir(parents=True, exist_ok=True)
    target = wire_dir / f'{converter.owner}_wire.json'
    data = json.dumps(wire, ensure_ascii=False, indent=2) + '\n'
    if target.exists() and target.read_text(encoding='utf-8') != data:
        raise ConverterRefused(f'换算目标已存在且内容不同：{target}')
    target.write_text(data, encoding='utf-8')
    record = {'schema_version': 1, 'upgrade_id': upgrade_id, 'node': node,
              'owner': converter.owner, 'subject_sha256': subject,
              'source_rel': converter.source_rel,
              'source_sha256': plan_files[converter.source_rel],
              'wire_sha256': repository.file_sha256(target),
              'actor': actor, 'reason': reason, 'note': converter.note,
              'created_at': int(time.time())}
    repository.write(f'runtime/upgrades/{subject}/converted/{converter.owner}.json',
                     record)
    return {'status': 'converted', 'wire': str(target),
            'adopt_with': f'run {node} --wire-dir {wire_dir}', **record}
