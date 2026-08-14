#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_enums.py —— `/api/enums` 是不是**真的**把取值面整份发了出去。

【本文件的三条主线】
  ① **超集**（`docs/BACKEND_CONTRACT.md` §一「命名体系」第 5 条）：抓包那 14 个键一个都不许少。
  ② **回归**：拿现役后端真的在跑的 36 条 `actions` 反过来查新表——
     这是全仓**唯一**一处能抓到 `is_paid` 对现役行为回归的地方。
  ③ **三个桶焊死**：往 `invariants` 加一个常量而不决定发不发，当场红。

⚠ 本文件**不需要 fastapi**：`novel/api/enums.py` 是纯函数，而
`novel/api/__init__.py` 一行 import 都没有（`test_layering.py` 钉着那一条）。
所以它跑得到，不会退化成一条 `importorskip` 的假绿。
"""
from __future__ import annotations

import pytest

from contracts import invariants as inv
from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
from novel.api import enums as E
from novel.errors import ValueDomainRefused
from novel.orchestrator import nodes as N
from tests import legacy_names as legacy

BASE = 'E:/nowhere/Output'          # 纯路径运算，不摸盘


@pytest.fixture(scope='module')
def paid_payload():
    return E.payload()


#: 抓包里**值变了**的键，逐条登记。⚠ 每一条都必须在本文件别处有一条**替代断言**，
#: 不许只是豁免——豁免掉而不补，等于把「这个键没人在查」写成「这个键查过没事」。
_VALUE_CHANGED = {
    'job_kinds': '原型那 12 个 kind（`n1..n9`）整套退役，键换成节点 id。'
                 '替代断言：test_every_captured_action_exists_in_the_new_table',
    'actions': '同上；且抓包 36 条是新表 57 条的**子集**。'
               '替代断言：同上 + test_the_cost_verdict_still_agrees_with_the_live_backend',
}


# ───────────────────────── 一、超集 ─────────────────────────

def test_every_captured_key_is_still_there(paid_payload):
    """抓包 14 个键一个都不许少。**少一个 = 前端某一屏当场空掉。**"""
    cap = legacy.captured_enums()
    # 守卫的守卫：样本换了形状（比如有人拿新后端的输出覆盖了它），
    # 上面那条会**静静地绿**——一份空的抓包是任何东西的子集。
    assert len(cap) == 14, (
        f'抓包只有 {len(cap)} 个顶层键，期望 14——'
        f'`docs/design_brief/05_samples/enums.json` 是现役后端的真实抓包，'
        f'**它是本文件全部对账的证据基线，不许用新后端的输出覆盖**')
    missing = sorted(set(cap) - set(paid_payload))
    assert not missing, f'抓包里有而 /api/enums 不发：{missing}'


def test_everything_else_is_value_identical(paid_payload):
    """除登记在 `_VALUE_CHANGED` 里的两个键外，**逐值相等**。

    ⚠ 它比 `test_invariants.py::test_enum_matches_live_backend` 那 6 条**严一层**：
    那 6 条查的是 `invariants` 的取值面，本条查的是**发布器有没有在中间改形状**——
    序列化把元组变成列表、把嵌套元组漏掉一层、把键名 lower 错，那 6 条一条都不会红。
    """
    cap = legacy.captured_enums()
    checked = []
    for key, want in cap.items():
        if key in _VALUE_CHANGED:
            continue
        assert paid_payload[key] == want, (
            f'{key} 与现役后端下发的那一份不一样了：\n  发布：{paid_payload[key]!r}\n  抓包：{want!r}')
        checked.append(key)
    # 守卫的守卫：往 `_VALUE_CHANGED` 里多加一个键，逐值比的面就少一块——
    # 而「豁免」与「查过没事」在测试结果上长得一模一样。
    assert len(checked) >= 12, (
        f'只逐值比了 {len(checked)} 个键（{sorted(checked)}）——'
        f'有人往 `_VALUE_CHANGED` 里加了东西？每加一条都要配一条替代断言')


def test_the_frontend_hardcoded_unions_are_all_published(paid_payload):
    """⭐ **本期存在的理由，写成一条测试。**

    这 7 个联合类型今天硬编码在 `web/src/constants/vocabulary.ts:12-24`
    （另有一份逐字相同的在 `docs/design_brief/04_delivery.md:99-105`），
    而同一份交付规格第 107 行写着「其余枚举从 `/api/enums` 运行时取，不要硬编码」——
    **这 7 个是被排除在那句话之外的**，理由是当初后端不发它们。
    现在发了。⇒ 前端那份硬编码从「不得不」变成「该删」。

    ⚠ 值也要逐个对上：只查键在不在，会漏掉「发了一份取值面但它比前端那份少一个值」，
    而那种漏的表现是界面上一个合法状态**渲染不出来**。
    """
    hardcoded = {
        'freshness': ['fresh', 'stale', 'unknown'],
        'badge': ['pass', 'amber', 'fail'],
        'severity': ['hard', 'info', 'metric', 'unver'],
        'job_state': ['running', 'ok', 'failed', 'unknown'],
        'inbox_kind': ['invalid', 'rejected', 'stalled', 'human_only', 'upstream', 'approval'],
        'ops': ['keep', 'compress', 'amplify', 'rewrite'],
        'depth_modes': ['deep', 'shallow'],
    }
    for key, want in hardcoded.items():
        assert paid_payload.get(key) == want, (
            f'/api/enums 的 {key!r} 与前端硬编码的那份对不上：'
            f'{paid_payload.get(key)!r} vs {want!r}')


def test_prompt_experiment_controls_are_not_published(paid_payload):
    assert 'prompt_defaults' not in paid_payload
    assert 'prompt_variants' not in paid_payload
    assert '--prompt-variant' not in paid_payload['valued_flags']


# ───────────────────────── 二、对现役后端的回归 ─────────────────────────

def test_every_captured_action_exists_in_the_new_table(paid_payload):
    """36 条现役后端真的在跑的跑法，新表一条都不许接不住。

    `kind` 的取值变了 ⇒ 逐值比不了 ⇒ **比它翻译过去的落点**。
    """
    cap = legacy.captured_enums()['actions']
    assert len(cap) > 30, f'只抓到 {len(cap)} 条 action——样本换形状了，本文件要跟着改'
    have = {(a['kind'], a['mode'], tuple(a['extra'] or ())) for a in paid_payload['actions']}
    missing = [f'{a["kind"]}{a["extra"] or []} → {legacy.translate(a)}'
               for a in cap if legacy.translate(a) not in have]
    assert not missing, (
        '现役后端跑得动、而新的 actions 表里没有的跑法：\n  ' + '\n  '.join(missing))


def test_the_cost_verdict_still_agrees_with_the_live_backend():
    """⭐ **全仓唯一一条能抓到 `is_paid` 对现役行为回归的测试。**

    `R-11.7` 要求贵贱只有一处算，而「只有一处」不等于「算得对」。
    抓包那 36 条各自带着现役后端当时给出的 `paid`——那是**真的在生产里用过**的答案。

    ⚠ 只能在 `actions` 这一层对账，**不能在 `job_kinds` 那一层**：
    抓包的 `check_blueprints` 是 `paid=false`，而它映到的 `stage2.blueprint` 节点
    `paid=true`——那不是不一致，是原型把复检做成了独立 kind 而两层命名下它是一个 flag。
    在 actions 这一层，隐含的 `--check-only` 被应用了，两边才是同一个问题的答案。
    """
    bad = []
    for a in legacy.captured_enums()['actions']:
        node, _mode, flags = legacy.translate(a)
        got = N.is_paid(node, flags)
        if got != a['paid']:
            bad.append(f'{a["kind"]}{a["extra"] or []} → {node}{list(flags)}：'
                       f'我们算 paid={got}，现役后端说 {a["paid"]}')
    assert not bad, '贵贱与现役后端分家了：\n  ' + '\n  '.join(bad)


def test_every_action_is_a_command_that_build_argv_accepts(paid_payload):
    """`R-11.8`：**界面上摆得出来的按钮，不可能是一条拼错的命令。**

    ⚠ 带值 flag 在 `actions[]` 里**不带值**（值是人点下去那一刻才给的），
    所以这里替它补一个占位值——`needs_value` 那一格就是为了让前端知道要补。
    """
    for a in paid_payload['actions']:
        flags = list(a['extra'] or [])
        if a['needs_value']:
            index = next(i for i, flag in enumerate(flags) if flag in VALUED_FLAGS)
            if flags[index] == '--deep':
                value = '1'
            else:
                value = 'C:/placeholder.json'
            flags.insert(index + 1, value)
        argv = N.build_argv(a['kind'], 'sid123', flags, base=BASE, mode=a['mode'])
        # 付费的那条命令**必须**带 `--yes`（`Q-10`：花钱前必须人确认）；不付费的不许带。
        assert ('--yes' in argv) == a['paid'], (
            f'{a["label"]}：paid={a["paid"]} 而 argv 里的 --yes 对不上：{argv}')
    # 守卫的守卫：节点表被清空 / 遍历写错时，上面那个 for 一条都不跑而测试照样绿。
    # ⚠ 57 → 59（2026-08-11，F 期移植 stage6）：`stage6.hook --revalidate` +
    #   `stage6.approve --pick`。两条都是**有意加的**，而这条断言的全部意义
    #   就是逼人来改这个数、顺便说清为什么。
    # ⚠ 59 → 60（2026-08-12，F2 移植 stage7）：**只多一条** `stage7.rename --revalidate`
    #   ——本层加了落盘闸门（任何一条 hard ⇒ `.INVALID`），而不给 0 成本提升路
    #   一次付费产出就被永久锁死。
    #   ⚠ **`stage7.approve` 没有跟着 +1**，那不是漏了：本层一本书只有**一份**人物表，
    #   定稿是「给整份盖章」不是「从三条候选里选一条」⇒ 它没有参数可起名，
    #   `flags=()` 在那一格是**终态**（`invariants.py` 那条注释已改）。
    # ⚠ 60 → 61（2026-08-12，F3 移植 stage4）：**只多一条** `stage4.plan --dry-run`。
    #   那一格从前只有 `--prompt-only`（只量体量），而「渲染出来的 system 与原型
    #   逐字节相同」这条对账**在 CLI 上根本走不通**——贵贱闸会把 `--dry-run`
    #   当成付费默认路径拦下（实测过一次）。两个 flag 都留着是因为它们答的是
    #   不同的问题：一个「这次要花多少 token」，一个「发出去的到底是哪几个字节」。
    #   ⚠ `stage4.context` **没有跟着 +1**，那不是漏了：它的 `flags=()` 是**终态**
    #   ——本节点**本身**就是那个动作，原型的 `--context-only` 已作为 flag 退役。
    # ⚠ 61 → 63（2026-08-12，F4-1 给 stage5 接线）：**+2，而其中一条是「挪」不是「加」**。
    #   · `--wontfix` 是新加的（`stage5.review`，1 条）；
    #   · `--revise` 从 `stage5.review` **挪到 `stage5.write`**（用户裁决）——
    #     而写作节点**有两条臂**，同一个 flag 于是从 1 条变成 2 条。
    #   ⚠ 这正是「臂是节点的参数」那条裁决的可见后果：界面上一个 flag 摆几个按钮，
    #     取决于它挂在哪个节点上。挪节点不是零成本的改名。
    # ⚠ 65 → 70（2026-08-12，F6/F7 移植 stage0/stage1）：
    #   stage0.remove 的三个 flag、stage0.edit 的两个带值输入各增加一个公开动作。
    # ⚠ 70 → 74（2026-08-12，F5-1 给 stage2 立契约层）：**+4，两个节点各 +2**。
    #   · `stage2.global`    += `--drop`（0 成本，人工裁决）· `--purge-role`（**付费**，语义判官臂）
    #   · `stage2.blueprint` += `--chapters`（定向重生成）· `--replay-cache`（0 成本重放）
    #   ⚠⚠ 这四格**不是补全癖**：它们是 `Rule.fix` 的取值面。`docs/schema/stage2.md`
    #     §5.1 那 24 条带修法的判据里，**19 条**指着 `blueprint --chapters`，
    #     另外三条分别指着 `global --purge-role` / `global --drop` / `global --migrate`
    #     ⇒ 少登记一个，`contracts/rules.py::_validate` ⑧ 就在 F5-4 注册那一刻把它判红。
    #   ⚠ `--drop` / `--replay-cache` 是本文件第一次遇到「**同时**是 0 成本入口
    #     与带值参数」的一对（此前只有 `--apply-edit`）——两个轴正交，不是重复登记。
    # ⚠ 74 → 76（2026-08-13，真实 E2E）：两个 Stage2 付费落盘闸门各补一条
    #   `--revalidate`。闸门由系统加，不能让合格的问题稿永久锁死。
    # ⚠ 76 → 78：stage2 并发首跑 +1；stage5 深浅分配是一个复合动作 +1。
    # ⚠ 78 → 80：Stage 5 书级语义审判默认路径与 `--dry-run` +2。
    # prompt 实验入口在完成选型后清理，不再占 action。
    # ⚠ 80 → 82（2026-08-14）：Stage 6 文案语义审判（`stage6.semantic`）
    #   默认路径与 `--dry-run` +2——形态与 stage5.semantic 那次完全同型。
    assert len(paid_payload['actions']) == 82, (
        f'actions 有 {len(paid_payload["actions"])} 条，期望 82 '
        f'（每个节点的默认路径 + 每个独立 flag；复合动作只发布一次）。'
        f'这个数**会随 `NODES` 改**——改动它是有意的就来改这个数')


# ───────────────────────── 三、三个桶焊死 ─────────────────────────

def test_every_public_constant_lands_in_exactly_one_bucket():
    """⭐ **本设计的全部承重点：往 `invariants` 加一个常量而不决定发不发，当场红。**

    「发」是自动的、「不发」要手写，两种烂法成本相同而方向相反：
    手写发布清单会**静默地烂**（前端拿不到 ⇒ 前端自己硬编码一份，没人出声）；
    手写不发登记表会**响亮地烂**（落不进任何桶 ⇒ `payload()` 抛）。
    """
    published = E.payload()
    for name, value in E.public_constants().items():
        homes = [t for t, table in (('_CONSUMED', E._CONSUMED),
                                    ('_WITHHELD', E._WITHHELD),
                                    ('_PUBLISHED_SCALARS', E._PUBLISHED_SCALARS))
                 if name in table]
        assert len(homes) <= 1, f'{name} 同时登记在 {homes} 里，三张表必须互斥'
        if homes:
            assert homes[0] != '_CONSUMED' or name.lower() not in published, \
                f'{name} 说自己被合成键吃掉了，却又单独发了一份 —— 那是两个落点'
            continue
        # 没登记 ⇒ 只有容器才允许，而容器一律自动发。
        assert isinstance(value, (tuple, dict)), (
            f'{name} 是标量而三张表里都没有它。'
            f'⇒ 补进 `_PUBLISHED_SCALARS`（说明前端拿它做什么）'
            f'或 `_WITHHELD`（写出为什么不发）。**不许只是不提它**')
        assert name.lower() in published, f'{name} 是容器却没被发出去'


def test_withheld_reasons_are_written_down():
    """「不发」的每一条都要写得出理由。**一个空理由等于没登记**——

    它挡住了测试，却没有告诉下一个人为什么。形态同 `test_layering.py`
    的 `_WRITE_ALLOWED` / `_JSON_READ_ALLOWED` 两张白名单。
    """
    for table_name in ('_CONSUMED', '_WITHHELD', '_PUBLISHED_SCALARS'):
        table = getattr(E, table_name)
        blank = sorted(k for k, v in table.items() if not (v or '').strip())
        assert not blank, f'{table_name} 里这些条目没写理由：{blank}'
        # 守卫的守卫：表被清空时上面那条会静静地绿。
        assert table, f'{table_name} 是空的——本文件的对账多半在扫一个空表'


def test_the_registries_never_name_a_constant_that_is_gone():
    """三张表里不许有 `invariants` 已经没有的名字。

    ⚠ 这条防的是**反方向的腐烂**：删掉一个常量而忘了删登记，那一行会一直挂在表里
    让人以为它管着事——同 `test_paid_flags_are_a_subset_of_the_nodes_flags` 的形态。
    """
    known = set(E.public_constants())
    for table_name in ('_CONSUMED', '_WITHHELD', '_PUBLISHED_SCALARS'):
        ghosts = sorted(set(getattr(E, table_name)) - known)
        assert not ghosts, f'{table_name} 里有 invariants 已经没有的常量：{ghosts}'


# ───────────────────────── 四、会红的反例（判据 32：守卫要有反例） ─────────────────────────

def test_the_publisher_refuses_a_namedtuple(monkeypatch):
    """⚠ **这条反例就是那个 bug 本身。**

    `NodeSpec` 是 `NamedTuple` ⇒ 它是 `tuple` 的子类。发布器如果不查「元素同质」，
    `json.dumps` 会把它写成一个**位置数组** `["stage3","R-4",true,...]` 发给前端，
    字段名全部丢掉，而没有任何一层会出声。
    """
    with pytest.raises(ValueDomainRefused, match='NamedTuple'):
        E._jsonable('FAKE', (inv.NODES['stage3.style'],))


def test_the_publisher_refuses_an_unregistered_scalar(monkeypatch):
    """新加一个标量而不登记 ⇒ `payload()` 当场抛，不是静默不发。"""
    monkeypatch.setattr(inv, 'NEW_THRESHOLD_FOR_TEST', 42, raising=False)
    with pytest.raises(ValueDomainRefused, match='NEW_THRESHOLD_FOR_TEST'):
        E.payload()


def test_the_publisher_refuses_a_constant_in_two_buckets(monkeypatch):
    """一个常量同时登记在两张表里 ⇒ 谁赢取决于 `payload()` 的 if 顺序。当场抛。"""
    monkeypatch.setitem(E._WITHHELD, 'STAGES', '假装不发')
    monkeypatch.setitem(E._PUBLISHED_SCALARS, 'STAGES', '又假装要发')
    with pytest.raises(ValueDomainRefused, match='互斥'):
        E.payload()


def test_the_publisher_refuses_a_container_in_the_scalar_exception_table(monkeypatch):
    """容器放进标量例外表 ⇒ 抛。容器本来就自动发，放进去只会让人以为它需要例外。"""
    monkeypatch.setitem(E._PUBLISHED_SCALARS, 'STAGES', '容器不该在这儿')
    with pytest.raises(ValueDomainRefused, match='_PUBLISHED_SCALARS'):
        E.payload()


# ───────────────────────── 五、派生，不手写 ─────────────────────────

def test_action_labels_are_unique_and_come_from_step_label(paid_payload):
    """label 一个字都不手写，且**与作业记录里那一步共用同一个函数**（`R-11.8`）。

    ⚠ 唯一性不是洁癖：两个按钮共用一句话，界面上就分不出点了哪个。
    ⚠ 「派生」也不是洁癖：原型那 36 条 label 是手抄的，于是 `n1_partition` 的 label
    写着「stage1 摄入+分章」，而「摄入」早已搬到 stage0——**手写的 label 会说谎**。
    """
    labels = [a['label'] for a in paid_payload['actions']]
    assert len(set(labels)) == len(labels), '有两条 action 共用一句 label'
    for a in paid_payload['actions']:
        flags = list(a['extra'] or ())
        if a['needs_value']:
            index = next(i for i, flag in enumerate(flags) if flag in VALUED_FLAGS)
            flags.insert(index + 1, '1')
        assert a['label'] == N.step_label(a['kind'], flags, mode=a['mode'])


def test_writes_and_paid_are_two_orthogonal_axes(paid_payload):
    """⭐ 「贵不贵」与「写不写」是两个轴，而原型只实现了前者。

    ⚠ 那条信息在原型注释里**一字不差出现了 8 次**（「`FREE_FLAGS` 只管钱不管写」），
    却没有落点。本条断言它现在有了，并且**盘上真的存在「免费但会写盘」的那一类**——
    没有这半条，一张全是 `writes_disk=false` 的表也会绿。
    """
    for a in paid_payload['actions']:
        assert a['writes_disk'] == bool(a['writes']), a['label']
        assert a['writes_disk'] == N.would_write(a['kind'], a['extra'] or ())
    free_and_writes = [a['label'] for a in paid_payload['actions']
                       if not a['paid'] and a['writes_disk']]
    assert free_and_writes, (
        '一条「免费但会写盘」的跑法都没有——那正是原型那 8 条注释说的那一类，'
        '它们真实存在（`--remeasure` / `--revalidate` / `--apply-edit` / `--migrate`）。'
        '一条都没有说明 `write_effect` 或本表的遍历坏了')


def test_free_flag_and_needs_value_are_derived_from_the_registries(paid_payload):
    """`free_flag` / `needs_value` 两格都从 `invariants` 的表派生，不手写。"""
    for a in paid_payload['actions']:
        flags = a['extra'] or []
        assert a['free_flag'] == next((f for f in flags if f in FREE_ENTRIES), None)
        assert a['needs_value'] == next((VALUED_FLAGS[f] for f in flags
                                         if f in VALUED_FLAGS), None)
    # 两格各自都要真的出现过非空值，否则「派生对了」与「恒为 None」同形。
    assert any(a['free_flag'] for a in paid_payload['actions'])
    assert any(a['needs_value'] for a in paid_payload['actions'])


def test_job_kinds_is_keyed_by_node_id(paid_payload):
    """`job_kinds` 的键换成了节点 id，而**形状不变**（`docs/BACKEND_CONTRACT.md` §一「命名体系」第 5 条）。"""
    jk = paid_payload['job_kinds']
    assert set(jk) == set(NODES)
    for node, spec in NODES.items():
        assert jk[node]['label'] == spec.label
        assert jk[node]['paid'] == spec.paid
    # 抓包那 12 个原型 kind 一个都不许还在——它们整套退役了。
    assert not (set(jk) & set(legacy.KIND_TO_NODE)), '原型 kind 复活了'
