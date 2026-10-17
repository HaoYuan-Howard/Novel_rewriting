#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前产物版本、路径注册表与只读损坏隔离的最小合成样本。"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import contracts.schema
from contracts.artifacts import (
    ARTIFACT_PATHS, RETIRED_STAGE3_STYLE_GROUNDING, artifact_rel,
)
from contracts.invariants import NODES
from contracts.schema import (
    ARTIFACTS,
    ARTIFACT_SPECS,
    ArtifactSpec,
    ArtifactVersionError,
    MainText,
    artifact_key,
    decode_artifact,
    encode_artifact,
)
from novel.consumption import CONSUMED_PROJECTIONS
from novel.orchestrator.state import overview
from novel.orchestrator.inbox_projection import inbox_view

MAIN_TEXT = [
    {'chapter_ordinal': 1, 'text': '甲推门进屋。'},
    {'chapter_ordinal': 2, 'text': '乙随后离开。'},
]


def test_a_real_payload_survives_the_envelope_and_still_validates():
    """信封是**包装**不是改写：脱掉它之后模型照样认得那份 payload。

    ⚠ 下面那两条参数化用合成 payload 扫注册表全部产物（分母），这一条用一份**真的**
    `MainText` 补另一半：脱信封之后它仍能过模型校验、字段仍在原位。
    """
    # ⚠ 版本号**从它自己的 spec 现算**，不写 `== 1`——与下面两条参数化同一条理由：
    #   「全表恒 v1」只该有 `test_the_whole_registry_is_on_one_scale_...` 一个守卫，
    #   写死之后按规程给某份产物升版会连带炸掉一批与升版无关的用例。
    spec = ARTIFACT_SPECS['stage1/main_text.json']
    current = encode_artifact('stage1/main_text.json', MAIN_TEXT)

    assert current['_schema_version'] == spec.current_version
    assert current['payload'] == MAIN_TEXT
    decoded = decode_artifact('stage1/main_text.json', current)
    assert decoded.version == spec.current_version
    assert MainText.model_validate(decoded.payload).root[1].chapter_ordinal == 2


def test_schema_model_map_is_derived_from_the_current_registry():
    assert set(ARTIFACTS) == set(ARTIFACT_SPECS)
    assert all(ARTIFACTS[key] is spec.model for key, spec in ARTIFACT_SPECS.items())


#: **每一次「破坏某份产物的形状」都在这里显式登记：路径 → (版本, 破坏的是什么, 谁重建)。**
#:
#: ⚠ 这不是一张「已知豁免」表，是一份**账**：`current_version` +1 的含义是
#: 「盘上的旧字节从此读不进来」，而本仓**没有迁移机制** ⇒ 每一次都必须同批回答
#: 「存量产物由谁重建」。答不出来的破坏不许落。
_SHAPE_BREAKS: dict[str, tuple[int, str, str]] = {
    'stage2/observations/names.json': (
        2, 'CS-R：names v2显式声明局部名称同一性及原句证据，旧回答不能推断为空声明',
        'str06.stage2.names按新协议重建；冻结根保持只读，新epoch与付费授权核对后执行',
    ),
    'stage2/characters/characters.json': (
        2, 'CS-R：名称结构保留resolutions，canonical语义可按已证明对应归一',
        'names v2观察就绪后由str06.stage2.compile免费重建双真源，不手补字段',
    ),
    'stage1/main_text.json': (
        3,
        '两次破坏。v2（2026-08-22）：`section_id` 删除，`source_chapter_label: str` '
        '改为 `source_chapter_no: int`。v3（2026-08-25）：删除下游不应信任的 '
        '`source_chapter_no`；原文章号只由当前 clean_text 复扫并用于 Stage1 对照页',
        '`stage1.partition` 免费重跑（纯函数、零 LLM），前置是 Stage0 已批准。'
        '它是承重产物，重建会让 Stage2+ 全部下游判 stale；用户已明确现有两本书在 '
        'Stage0～7 减法完成后统一从头重建，不做迁移或手补字段',
    ),
    'stage0/revisions.json': (
        3,
        '2026-09-18 STR-03-S0：权威从 revision 链 + approval 收成一次 '
        '`Stage0Transform`；闸门为 mechanical admission；路径仍为 '
        'stage0/revisions.json。v2 拒读，由 stage0.remove --source 重建',
        '`stage0.remove --source <原文>` 免费重跑（零 LLM）；无人工重批。'
        '存量 v2 书须废弃后重建，不做迁移',
    ),
    'stage1/hook_copy.json': (
        2,
        '2026-08-22 阶段 B：恒 `sec_0001` 的 `section_id` 删除，产物收成 `{text}`',
        '同 `stage1/main_text.json`：`stage1.partition` 免费重跑',
    ),
    # ── STR-06 新链（F8a）────────────────────────────────────────────
    # 新链建立时 schema 化产物全部 current_version=1；CS-R两项例外已登记在上方。三级路径是
    # 切换时新建的命名空间，盘上没有旧信封字节（旧路径整体退役，见
    # `contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`）。形状破坏史由另外两条线
    # 承载，不在本表重复登记：
    #   ① 模型自带 `contract_version` 字面量（Characters v6／ReconstructionBrief／
    #      SemanticBinding／ExecutionPlan／PresentationGraph／HookContext／
    #      WriterBookIR／ChapterReceipt／NameMap v6，与候选产物表逐字对账，
    #      `tests/test_str06_candidates.py` 守着）；
    #   ② 候选产物表的 `schema_version` RC 标签（rc2→rc3→rc4 的批次定格，
    #      `tests/test_str06_f7e_freeze.py` 钉住冻结基线）。
    # 未声明 `contract_version` 的模型（Blueprints v17、CharactersCore 抽取）按
    # F8 义务在本注册表登记形状（即 `ARTIFACT_SPECS` 条目自身）——不往模型加非
    # exclude 字段：那会改 `model_dump` 字节，从而改 prompt 与 F7a 历史采用的
    # 已购请求身份。
}


def test_the_whole_registry_is_on_one_scale_and_the_migration_ladder_stays_gone():
    """⭐⭐ **除显式登记过的破坏之外全表恒 v1，且没有迁移机制。**

    ⚠ 这里曾是一大坨「谁是 v2、谁是 v3、谁刻意没有 migration」的分族断言，配一梯
    v2→v3 迁移函数。删除那天的实测：登记了 migration 的 8 个家族在盘上**一份低版本
    文件都没有**，而盘上唯二的 v2 文件恰好属于刻意不登记 migration 的那一族
    ⇒ 整条梯子零消费者，却让「只接受当前版本」这句话假了一整段时间。

    ⇒ 本条是它的**反向守卫**：梯子不许悄悄回来。要破坏某个产物的形状，做法是把该路径的
    `current_version` +1 **并在 `_SHAPE_BREAKS` 里写出破坏了什么、存量由谁重建**——
    不是加一条迁移把旧字节读成新形状。
    ⚠ 漏登记会红、登记了但版本对不上也会红：那张表与注册表**互为对方的守卫**。
    """
    live = {rel: spec.current_version for rel, spec in ARTIFACT_SPECS.items()
            if spec.current_version != 1}
    assert live == {rel: v for rel, (v, _what, _who) in _SHAPE_BREAKS.items()}, \
        '有产物升了版却没在 `_SHAPE_BREAKS` 里登记（或登记了却没升）——' \
        '升版的含义是「存量读不进来」，必须同批答出「由谁重建」'
    for rel, (_v, what, who) in _SHAPE_BREAKS.items():
        assert rel in ARTIFACT_SPECS and what.strip() and who.strip()
    assert [f.name for f in dataclasses.fields(ArtifactSpec)] == \
        ['model', 'current_version'], 'ArtifactSpec 多了一格——migrations 回来了？'
    assert not [name for name in vars(contracts.schema) if name.startswith('MIGRATE')]


#: 模板 → 产物 id。`ARTIFACT_PATHS` 的逆，用来把参数化的 spec 键喂回 `artifact_rel`。
_ID_OF_REL = {rel: name for name, rel in ARTIFACT_PATHS.items()}


def _concrete(rel: str) -> str:
    """把注册表模板变成一条**真实**路径。

    ⚠⚠ 别拿模板路径直接去 `decode_artifact`：`artifact_regex` 把 `{n}` 编译成 `\\d+`，
    模板自己匹配不上任何路由 ⇒ `artifact_spec()` 返回 `None` ⇒ 未登记分支**原样返回、
    一个异常都不抛**。用模板写的「拒读」用例会全程绿着，却一次闸都没查到。

    ⚠ 填充**调生产那一个落点** `artifact_rel`，不在这里 `str.replace` 重拼一份：
    「`nn` 补两位」这条约定归它管，手拼那份哪天与它分家（比如补三位），
    生产写出的是 `ch001.json` 而这里仍造 `ch01.json`——而路由把 `{nn}` 编译成 `\\d+`，
    两条参数化照样全绿，扫的却是一批生产永远不会写出来的路径。
    """
    return artifact_rel(_ID_OF_REL[rel], n=1, nn=1)


def test_the_placeholder_helper_actually_produces_registered_paths():
    """`_concrete()` 是上面两条参数化的前提——前提本身也要有人查。

    它错了的后果不是红，是**两条参数化一起变成空验证**（走未登记分支、永不抛）。
    ⚠ 顺带钉住那张逆表：两个产物 id 共用一个模板时 `_ID_OF_REL` 会**静默丢掉一个**，
    那一份从此不在扫描面上，而分母看起来仍是满的。
    """
    assert len(_ID_OF_REL) == len(ARTIFACT_PATHS), \
        '有两个产物 id 共用同一个路径模板 ⇒ 逆表丢了一个，它已悄悄掉出扫描面'
    for rel in ARTIFACT_SPECS:
        assert artifact_key(_concrete(rel)) == rel, \
            f'{rel} 的占位符没替干净 ⇒ 它在版本用例里根本没被查到'


@pytest.mark.parametrize('rel', sorted(
    r for r in ARTIFACT_SPECS if '{' in r), ids=lambda r: r)
def test_a_template_path_is_refused_instead_of_silently_passing_through(rel: str):
    """⭐⭐ 拿**模板**去 decode/encode 必须当场抛，而不是走「未注册」原样返回。

    ⚠ 这是 `docs/LESSONS.md` 第 23 条那个形状的根治点的守卫：占位符匹配不上任何路由
    ⇒ 曾经**一个异常都不抛** ⇒ 用模板写的「拒读」用例会全程绿着、却一次闸都没查到。
    ⇒ 闸下沉到 `contracts/schema` 之后，这一条钉住它：**下一条忘了替占位符的守卫
    会当场红**，而不是安静地变成空验证。
    """
    for call in (lambda: decode_artifact(rel, {'_schema_version': 1}),
                 lambda: encode_artifact(rel, {'anything': 1})):
        with pytest.raises(ArtifactVersionError, match='模板'):
            call()


@pytest.mark.parametrize('rel', sorted(ARTIFACT_SPECS), ids=lambda r: r)
def test_every_registered_artifact_round_trips_through_the_v1_envelope(rel: str):
    """⭐ 分母**从注册表派生**：新增一份产物自动进扫描面，不必记得改这里。

    ⚠ 一条不知道自己扫了多少的绿灯没有判别力（判据 31 的同一个道理）。
    """
    spec = ARTIFACT_SPECS[rel]
    path = _concrete(rel)
    payload = [] if spec.root else {'anything': 1}
    encoded = encode_artifact(path, payload)

    # ⚠ 版本号**逐条从它自己的 spec 现算**，不写 `== 1`：「全表恒 v1」只该有
    #   `test_the_whole_registry_is_on_one_scale_...` 一个守卫。写死之后，将来按文档
    #   规定的方式给某一份产物 `current_version` +1，会连带炸掉这两条参数化的
    #   注册表全表 × N 个用例——真正该红的那一条淹没在里面，而那正是本批要消灭的形状。
    assert encoded['_schema_version'] == spec.current_version
    if spec.root:
        assert set(encoded) == {'_schema_version', 'payload'}, \
            'root 产物的信封只能有这两个键'
        assert encoded['payload'] == payload
    else:
        assert encoded['anything'] == 1

    decoded = decode_artifact(path, encoded)
    assert decoded.version == spec.current_version
    assert decoded.payload == payload


@pytest.mark.parametrize('rel', sorted(ARTIFACT_SPECS), ids=lambda r: r)
@pytest.mark.parametrize('marker', [None, 0, 2, 3, 99, True, -1, '1'],
                         ids=['缺失', 'v0', 'v2', 'v3', 'v99', 'True', 'v-1', '字符串'])
def test_only_the_current_version_is_read(rel: str, marker: object):
    """**无标记 / 低版本 / 高版本 / 非法值一律拒读**，注册表全部产物逐个查。

    ⚠ `v2` 与 `v3` 特意留在取值面里：它们正是删掉迁移梯子之前**会被放行**的那两档。
    ⚠ 报错必须同时说出文件与版本——只说「读不了」的错误，会把人推回去手补字段。
    """
    spec = ARTIFACT_SPECS[rel]
    # ⚠ 取值面是写死的「非法」标记，而「非法」是**相对当前版本**说的：某份产物升到 v2 之后，
    #   `2` 这一格对它就不再非法。此前这里是一条 `assert` + 一句「去把这档换掉」的提示，
    #   而「换掉」会让**全部** v1 产物丢掉「高版本拒读」那一档的覆盖。
    #   ⇒ 改成**逐产物替换**：对升过版的那一份，把这一档换成它自己的上一版号。
    #   那不是降级，恰恰是最要紧的真实场景——**盘上存量正是上一版**，必须被拒。
    #   ⚠ 比较**先看类型**：`True == 1` 在 Python 里成立，而 `True` 恰恰是取值面里
    #     一个必须被拒的标记（生产那侧 `isinstance(marker, bool)` 单独拦它）。
    #     写成 `marker == current` 会在 v1 上把 `True` 这一档整批换掉。
    if type(marker) is int and marker == spec.current_version:
        marker = spec.current_version - 1
        assert marker >= 1, 'current_version 必须 ≥1，否则这里换出来的不是「上一版」'
    path = _concrete(rel)
    payload: object = [] if spec.root else {'anything': 1}
    if marker is None:
        raw = {'payload': payload} if spec.root else {'anything': 1}
    elif spec.root:
        raw = {'_schema_version': marker, 'payload': payload}
    else:
        raw = {'_schema_version': marker, 'anything': 1}

    with pytest.raises(ArtifactVersionError) as caught:
        decode_artifact(path, raw)
    message = str(caught.value)
    assert path in message, f'错误没说是哪个文件：{message}'
    assert 'schema' in message, f'错误没提版本：{message}'


def test_a_root_envelope_with_an_extra_key_is_refused():
    """root 信封只有两个键。多一个 ⇒ 它不是本仓写出来的，拒读而不是挑着读。"""
    with pytest.raises(ArtifactVersionError, match='envelope 只能有'):
        decode_artifact('stage1/main_text.json', {
            '_schema_version': 1, 'payload': MAIN_TEXT, 'extra': 1,
        })


def test_paths_feed_schema_nodes_consumption_and_runtime_from_one_catalog():
    catalog = set(ARTIFACT_PATHS.values())
    assert len(catalog) == len(ARTIFACT_PATHS), '两个产物 id 指向同一路径，身份分家了'
    assert set(ARTIFACT_SPECS) <= catalog
    assert {rel for spec in NODES.values() for rel in spec.produces} <= catalog
    assert {
        rel for projections in CONSUMED_PROJECTIONS.values() for rel in projections
    } <= catalog
    # F8a 后逐章正文/回执的字面键直接以模板为 id；旧 stage5.chapter／
    # rejected_revision（Stage5 Review 收敛账）随旧链退役。
    assert artifact_rel('stage5/body/ch{nn}.txt', nn=3) == 'stage5/body/ch03.txt'
    assert artifact_rel('stage5/acceptance/body/ch{nn}.json', nn=12) == (
        'stage5/acceptance/body/ch12.json')


@pytest.mark.parametrize('rel,key', [
    ('stage5/acceptance/body/ch01.json', 'stage5/acceptance/body/ch{nn}.json'),
    ('stage5/acceptance/body/_rounds/ch01.r3.json',
     'stage5/acceptance/body/ch{nn}.json'),
    ('stage5/acceptance/body/_rounds/ch01.INVALID.r3.json',
     'stage5/acceptance/body/ch{nn}.json'),
    # 旧 stage5/ch{nn}.json 与 review_{n}.json 路径族随旧链退役
    # （F8b），路径路由反例归旧链注销批次，不保留现役参数。
])
def test_runtime_and_corpus_share_the_same_path_router(rel: str, key: str):
    assert artifact_key(rel) == key


@pytest.mark.parametrize('rel', [
    'stage5/acceptance/body/_rounds/ch01.INVALID.superseded.json',
    'stage5/acceptance/body/_rounds/review_4.invalid_metadata_convergence.json',
])
def test_retired_archive_aliases_are_not_current_artifact_paths(rel: str):
    assert artifact_key(rel) is None


def test_retired_stage4_identity_artifact_is_not_discovered_or_registered():
    rel = 'stage4/writer_context.identity.json'
    assert rel not in ARTIFACT_PATHS.values()
    assert rel not in ARTIFACT_SPECS
    assert artifact_key(rel) is None


def test_retired_stage3_path_is_not_a_current_artifact_or_node_output():
    assert RETIRED_STAGE3_STYLE_GROUNDING not in ARTIFACT_SPECS
    assert artifact_key(RETIRED_STAGE3_STYLE_GROUNDING) is None
    assert RETIRED_STAGE3_STYLE_GROUNDING not in {
        rel for spec in NODES.values() for rel in spec.produces
    }


def _legacy_stage3_v2() -> dict[str, object]:
    block = {
        'chars': 10, 'paragraphs': 1, 'sentences': 1,
        'avg_paragraph_chars': 10.0, 'paragraph_sep_chars': 0.0,
        'avg_sentence_chars': 10.0, 'dialogue_ratio': None,
        'dialogue_ratio_applicable': False, 'quote_styles': [],
    }
    return {
        '_schema_version': 2,
        'measurable': {
            'body': {
                **block,
                'chapter_chars': {'min': 10, 'max': 10, 'avg': 10.0},
                'per_chapter_avg_sentence': {'min': 10.0, 'max': 10.0},
            },
            'hook': block,
        },
        '_provenance': {
            'code': 'legacy-code', 'root_clean_sha1': '0' * 40, 'inputs': {},
            'measurement_contract': 'stage3-measurable-v1',
        },
    }


@pytest.mark.parametrize('marker', [None, 1, 2, 3, 99])
def test_retired_stage3_payload_has_no_current_decoder(marker):
    raw = _legacy_stage3_v2()
    if marker is None:
        raw.pop('_schema_version')
    else:
        raw['_schema_version'] = marker
    with pytest.raises(ArtifactVersionError):
        decode_artifact(RETIRED_STAGE3_STYLE_GROUNDING, raw)


def test_unreadable_current_artifacts_do_not_take_down_read_only_aggregation(tmp_path: Path):
    root = tmp_path
    book = root / '合成损坏书_123'
    (book / 'stage1').mkdir(parents=True)
    (book / 'stage2').mkdir()
    (book / 'stage1' / 'main_text.json').write_text(
        json.dumps(encode_artifact('stage1/main_text.json', MAIN_TEXT), ensure_ascii=False),
        encoding='utf-8')
    (book / 'stage1' / 'hook_copy.json').write_text(
        json.dumps(encode_artifact('stage1/hook_copy.json', {
            'text': '门外忽然响了一声。',
        }), ensure_ascii=False),
        encoding='utf-8')
    malformed = json.dumps({'_schema_version': 1}, ensure_ascii=False)
    # B2（2026-09-27）：`stage2/compiled/book_extraction.json` 已退役（D9）；
    # 损坏样本改用现役观察产物（Characters core 的同族形状闸不变）。
    (book / 'stage2' / 'characters').mkdir()
    (book / 'stage2' / 'characters' / 'characters.json').write_text(
        malformed, encoding='utf-8')

    ov = overview(root, '123')
    inbox = inbox_view(root, '123')

    assert ov.summary.verdict == 'broken'
    assert inbox.total == len(inbox.items)


@pytest.mark.parametrize('rel', [
    'stage3/style_grounding.json',
    'stage3/style_grounding.INVALID.json',
    'stage3/_rounds/style_grounding.r1.json',
    'stage3/_rounds/style_grounding.INVALID.r1.json',
])
def test_retired_registered_path_cannot_borrow_unregistered_data_io(rel):
    with pytest.raises(ArtifactVersionError, match='产物路径已退役'):
        encode_artifact(rel, {'historical': True})
    with pytest.raises(ArtifactVersionError, match='产物路径已退役'):
        decode_artifact(rel, {'historical': True})
