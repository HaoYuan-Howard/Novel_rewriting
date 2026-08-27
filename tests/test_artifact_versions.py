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
from contracts.schema.legacy_stage3 import decode_legacy_stage3_v2
from novel.consumption import CONSUMED_PROJECTIONS
from novel.orchestrator.state import overview
from novel.views.inbox import inbox_view

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
        2,
        '2026-08-25 Stage0 减法：原文身份提升到根部；revision content 删除 '
        '`original_filename/source_ref/normalized_source_sha1/source_encoding/text_norm/'
        'total_code_points/clean_sha1/body_code_points/warnings` 派生或链级字段；'
        '`removed[]` 删除 line_no/code_points/head_preview/tail_preview；'
        '`removal_report.json` 整份下线，运行时 snapshot 现算',
        '`stage0.remove --source <原文>` 免费重跑（零 LLM），随后人工重批。'
        '用户已明确现有两本书留到 Stage0～7 减法完成后统一重建',
    ),
    'stage1/hook_copy.json': (
        2,
        '2026-08-22 阶段 B：恒 `sec_0001` 的 `section_id` 删除，产物收成 `{text}`',
        '同 `stage1/main_text.json`：`stage1.partition` 免费重跑',
    ),
    'stage2/global_grounding.json': (
        4,
        '两次破坏。v2（2026-08-23）secret 时间线三刀：① `setup_chapters: list[int]` 换成 `setup` '
        '判别联合（`{"kind":"cold"}` / `{"kind":"foreshadowed","chapters":[…]}`）'
        '——v1 的 `[]` 同时表示「声明冷揭示」与「模型压根没想过」，而这一格是两道'
        '下发闸门的前件；② `reader_reveal_events[]` 加 `kind: first|confirm`，'
        '载体强制恰有一条 `first`——旧的下发闸门按「任意一次揭示 ≤ 本章」判，'
        '分两次揭示的秘密在第一次之后就把整份 `fact` 泄给了写前面章节的写手；'
        '③ 新增可空 `group_id`，复合真相拆成多条后归组用。'
        'v3（2026-08-24）：① relation 新增 public/via_secret 显式 visibility；'
        '② secret 的无时间 `character_known_by` 换成 CharacterRef/GroupRef + known_from_chapter '
        '的 `knowledge_events`；③ role/fate、relation predicate、setting、rule desc、secret fact、'
        'knowledge event、arc trajectory 新增代码按“指定章 + 短 locator”唯一回取的正式证据，'
        '失败态 source=null；'
        '④ `valid_to_chapter` 改名 `valid_through_chapter`，把原本即包含式的端点语义写进字段名。'
        'v4（2026-08-26）删除 SourceSpan.normalized/recovery、secret group_id/确认事件，'
        '把唯一消费的首次揭示收成 first_reveal_chapter 标量，并删除 arc dimension/change_type',
        '`stage2.global` 整跑（**付费**）；`.INVALID` 与 `_rounds/` 一律作废，'
        'v1/v2 都不手补字段冒充当前产地。重建后 review 按 claim+evidence 新 hash 重新 pending；'
        '这是形状破坏的真实人工成本，不能靠迁移伪装成旧决定仍有效。',
    ),
    'stage2/global_edit.json': (
        3,
        '2026-08-24：secret patch/replacement 从 `character_known_by` 换成带 locator 的 '
        '`knowledge_events`，并新增 relation visibility、setting、fate、arc 与全部自由事实证据的 '
        'typed patch/replacement；旧 v1 决策文件不能表达知情时点或证据',
        'v3（2026-08-26）随 GlobalGrounding v4 删除同批冗余字段。'
        '它是人产决策集，不由模型重建：人工按当前 `GlobalEdit` v3 重写书内 JSON。'
        '不根据旧 known_by 猜 known_from_chapter，不为旧 claim 伪造 locator；盘上 v1 字节原样保留'
        '只能作为历史输入，当前运行时关闭式拒读',
    ),
    'stage2/chapter_blueprints.json': (
        6,
        '四次破坏。2026-08-19 bounded-fidelity-v2 → v3：beat 的区间改由段号'
        '算出，SourceSpan.recovery 去掉 lcs，BlueprintProvenance.verbatim_recovered '
        '删除。2026-08-23：BlueprintContext.architecture 从七值收窄为 '
        'extraction-ir-v1.1 / v2 / v3.1 三值，退役 bounded-fidelity-v3、v1、v2.1、v3。'
        '2026-08-24：再收窄为 v2 / v3.1 两值，退役 v1.1。2026-08-25：删除复合 '
        '`Beat.event` 事实源，新增章级 `chapter_function`、`fact_atoms`、`causal_edges`、'
        '`entry_state_refs` 与 `ending_state_refs`；hinge 新增稳定 id。2026-08-26 v6 删除'
        'summary、present_names、叙述显示名、hinge 重复父键与无消费者 provenance 账',
        'stage2.blueprint 整跑（付费）；.INVALID 与 _rounds/ 一律作废，'
        '不手补字段冒充当前产地。仓内没有迁移机制，任何 v3 信封'
        '都必须由 producer 重建为当前 v6',
    ),
    'stage4/plan_chapters.json': (
        4,
        '三次破坏。2026-08-25 v2 删除 `PlanBeat`/`segments`/`from_beats`，改为原子事实 '
        '`semantic_contract` + `scenes`。2026-08-25 v3 把源序可见的一次规划拆成 '
        '`stage4.design` 盲设计与 `stage4.plan` 证据绑定，逐章新增 retain/reconstruct '
        '决定与声明/代码复算变化；2026-08-26 v4 删除展示/过程/派生字段：goal rationale、'
        '权重、reconstruction_strategy、semantic_contract、FactRef.chapter、PlanParams、'
        'provenance params 与持久化 computed_change_kinds',
        '依次付费重跑 `stage4.design` 与 `stage4.plan`；旧 v2/v3 不迁移、不补字段。'
        '`stage4.context` 随后免费重建；Stage5 canonical 因消费计划改变而全书重写',
    ),
    'stage4/plan_edit.json': (
        4,
        '三次破坏：v2 改为语义契约 + scenes；v3 与正式计划同步新增章级结构决定及代码复算变化；'
        'v4 与正式计划同步删除展示/过程/派生字段',
        '旧人工编辑集不能凭空推造当前结构；需要时由人按当前 PlanEdit v4 重写',
    ),
    'stage4/blind_candidate.json': (
        2,
        '2026-08-26 删除三个 goal 的 rationale；reconstruction_strategy 只保留为 A 相规划脚手架',
        '`stage4.design` 付费重跑；旧 v1 不迁移、不手补版本号',
    ),
    'stage4/evidence_binding.json': (
        2,
        '2026-08-26 删除 participant_refs；人物到场统一读取绑定舞台的 present',
        '`stage4.plan` 付费重跑 B 相；旧 v1 不迁移、不手补版本号',
    ),
    'stage4/writer_context.json': (
        4,
        '2026-08-25 删除写手面的 `CtxBeat.events[]`，新增 chapter_rewrite_brief、'
        '四类 delta 在内的人话 semantic_contract 与可直接执行的 CtxScene；v3 随 Stage2 '
        'ArcTrack 删除 dimension/change_type；2026-08-26 v4 删除 world_facts、逐场源字数/参与者、'
        '秘密与规则内部 id、must_not_reveal，并把 characters 改为带 char_id 的数组',
        '`stage4.context` 从当前 Stage2 v6 + Stage4 plan v4 免费重建；下游 Stage5/6 按消费投影判 stale',
    ),
    'stage5/ch{nn}.json': (
        2,
        '2026-08-24 Stage5 单臂折平：删除 `mode`，正式稿从 '
        '`stage5/{mode}/chNN.json` 收成唯一 `stage5/chNN.json`',
        '`stage5.write` 在冻结的 stage0～4 上游上全书重跑（付费）；旧 deep/shallow '
        '目录原样保留为只读历史证据，运行时明确报 `legacy_incompatible`，不迁移、不选臂',
    ),
    'stage5/review_{n}.json': (
        2,
        '2026-08-24 Stage5 单臂折平：ReviewChapter 删除 `mode/mode_dir`，judged '
        '主体改绑 canonical 章集合',
        '`stage5.review` 在 canonical 成稿上免费重跑；旧 Review 不得驱动付费 `--revise`',
    ),
    'stage5/trim_audit_ch{nn}.json': (
        2,
        '2026-08-24 Stage5 单臂折平：TrimAudit 删除 `mode`，章身份只由 ordinal '
        '与 canonical 路径承载',
        '新的超长 canonical 章由 `stage5.write` 生成审计；旧双臂审计仅作历史证据',
    ),
    'stage5/trim_resolution_ch{nn}.json': (
        2,
        '2026-08-24 Stage5 单臂折平：TrimResolution 删除 `mode`，`chapter_rel` '
        '固定绑定 `stage5/chNN.json`',
        '在当前 v2 TrimAudit 上重新走免费、hash-bound 的 `--retain-pretrim` 人工裁决',
    ),
    'stage6/hook.json': (
        2,
        '2026-08-23 候选身份与策略名解耦：`ApprovedHook.approach`'
        '（策略名 + 一个 `human_edit`）换成 `slot`（位置身份 `c1`/`c2`/`c3`，'
        '`None` = 无前身），`HookCandidateRetry.approach` 同样换成 `slot`。'
        '⚠ 破坏的理由不是「换个名字更好看」：同一种策略采两条样本时，`approach` '
        '**在结构上答不出「这份定稿是从哪条候选来的」**，而 `_texts_of` 的标签、'
        'wontfix 的证据键、判官的 `judged_texts` 键全挂在那个答案上。'
        '策略名没有丢——由 `candidates[slot].approach` 现算。'
        '`HOOK_APPROVED_APPROACHES` 随之删除',
        '`stage6.hook` 重跑（**付费**）。⚠ 重跑写 `approved=None` ⇒ 人工定稿一并失效，'
        '必须重新走 `stage6.approve --pick` 的 actor / reason / 完整 subject SHA-256。'
        '⚠⚠ **没有自动迁移，也不能宣称盘上代价为零**：`outputs/_ab/flash/` 仍保留两份'
        '已到 stage7 的 v1 实验快照，而且都缺 `stage2/global_review_decisions.json`，'
        '不能直接从 producer 付费重建。本次把它们明确退役为**只读历史实验证据**：'
        '不纳入 current-schema corpus，也不承诺当前运行时可读；字节原样保留，'
        '需要检视时使用形状破坏前的代码身份。现役交付必须由 producer 生成 v2 并重新人工定稿。'
        '当次 `stage6/semantic_*.json` 未破坏形状，但它按 `judged_texts` '
        '绑候选文本、重跑后会判 stale；后续的 semantic v2 破坏另见独立记录',
    ),
    'stage6/hook_edit.json': (
        2,
        '2026-08-23 同批：`HookEdit.candidate` 的取值面从策略名换成位置身份 '
        '`c1`/`c2`/`c3`（`None` 仍表示「写/替换定稿」）。策略名不再唯一 ⇒ 选不中那一条',
        '**盘上零存量**（全 `outputs/` 找不到一份 `hook_edit.json`）⇒ 本次代价为零。'
        '它是人工决策集不是产出物，下次写的时候按新取值面写即可',
    ),
    'stage6/semantic_{n}.json': (
        2,
        '2026-08-27 agent 执行者纵切：新增 `judged`，以 '
        '`JudgedDraft.consumed.text_sha1` 绑定 A/B 判官实际看见的 Stage5 '
        '开篇成稿。v1 只绑 hook 候选文本；Stage5 正文改字后，它无法证明'
        '旧的矛盾证据与质量判断仍是对当前参考材料作出',
        '不迁移、不手补 `judged` 或版本号；由 `stage6.semantic` 对当前 '
        'hook + canonical Stage5 成稿重跑（A/B 两次付费判官调用）。'
        '旧人工定稿仍由它自己的 actor/reason/完整 subject SHA-256 绑定；'
        '若 hook 文本未变，不伪造一次重批需求',
    ),
    'stage7/name_map.json': (
        2,
        '2026-08-24 stage7 前置整改批：`NameDecision` 加跨字段 validator——'
        '`renamed=true` 时 `not_renamed_reason` **只准是 `null`**。'
        '契约注释早就这么写着，但 `_build` 一直原样透传 ⇒ 盘上空串合法通行，'
        '收窄之后带 `\'\'` 的存量一律读不进来。'
        '（同批的 `ExtraSub.origin` 加 `adopted` 一格是 Literal **加**格，'
        '旧值 `human` 仍合法 ⇒ 那一项不构成破坏。）',
        '`stage7.rename` 重跑（**付费**）。⚠ 重跑产新决策 ⇒ `approved` 一并失效，'
        '必须重新走 `stage7.approve` 的 actor / reason / 完整 subject SHA-256，'
        '旧 `book_txt` 也要重新 `stage7.assemble`。'
        '⚠⚠ **`--apply-edit` 不是这一格的 0 成本出路**（别照抄别处那句）：'
        '`run_apply_edit` 会先 `store.read_own(NameMap, ...)` 读盘上那份旧决策集，'
        '而版本闸恰恰在那一步拒掉 v1 ⇒ 那条路进不去。'
        '⚠⚠ **不能宣称盘上代价为零**：`outputs/` 现有 1 本走完全链的书'
        '（`偶像公寓打工日常_2066575217393144601`），它的 `stage7/name_map.json` 是 v1、'
        '已人工定稿且已交付 `_rewriting.txt`（22044 字 / 18 章）'
        '⇒ 升版后该书 stage7 读不进来，须付费重跑并重新定稿。'
        '两条减轻情节：它的 `RULES` 指纹在本批之前**就已经 stale**'
        '（`rn_derived_alias_candidate` 落地晚于那次跑），而本批改了 `RENAME_SYS` 字节 ⇒ '
        '它要变 current 本来就得付费重跑一次。'
        '⚠ `outputs/_ab/flash/` 那两份 v1 快照不在此列——它们已由同表 '
        '`stage6/hook.json` 那一条明确退役为**只读历史实验证据**，且本批之前就读不进来'
        '（`stage0/removal_report.json` v1 而现役已下线、`stage1/*` v1 而当前 v2，'
        '还带着阶段 C 已下线的 `stage0/_audit/edit_1.json`）',
    ),
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
    assert artifact_rel('stage5.chapter', nn=3) == 'stage5/ch03.json'
    assert artifact_rel('stage5.rejected_revision', nn=12) == 'stage5/ch12.rejected.json'


@pytest.mark.parametrize('rel,key', [
    ('stage5/ch01.json', 'stage5/ch{nn}.json'),
    ('stage5/_rounds/ch01.r3.json', 'stage5/ch{nn}.json'),
    ('stage5/_rounds/ch01.INVALID.r3.json', 'stage5/ch{nn}.json'),
    ('stage5/_rounds/review_4.r2.json', 'stage5/review_{n}.json'),
    ('stage6/hook.INVALID.json', 'stage6/hook.json'),
    ('stage6/_rounds/hook.r2.json', 'stage6/hook.json'),
])
def test_runtime_and_corpus_share_the_same_path_router(rel: str, key: str):
    assert artifact_key(rel) == key


@pytest.mark.parametrize('rel', [
    'stage5/_rounds/ch01.INVALID.superseded.json',
    'stage5/_rounds/review_4.invalid_metadata_convergence.json',
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


def test_retired_stage3_decoder_accepts_only_the_frozen_v2_shape():
    raw = _legacy_stage3_v2()
    assert decode_legacy_stage3_v2(raw).measurable.body.chars == 10

    for marker in (None, 1, 3, 99):
        changed = {**raw}
        if marker is None:
            changed.pop('_schema_version')
        else:
            changed['_schema_version'] = marker
        with pytest.raises(ValueError, match='只接受 v2'):
            decode_legacy_stage3_v2(changed)

    with pytest.raises(ValidationError):
        decode_legacy_stage3_v2({**raw, 'voice': {'historical': True}})


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
    (book / 'stage2' / 'global_grounding.json').write_text(
        malformed, encoding='utf-8')

    ov = overview(root, '123')
    inbox = inbox_view(root, '123')

    assert ov.summary.verdict == 'broken'
    assert inbox.total == len(inbox.items)
