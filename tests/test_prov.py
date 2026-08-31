#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_prov.py —— `_common.md` §四那张表，**逐行**。

新鲜度是 `Q-2` 四个落点里最贵的一个：实测代价是某次 23 章成稿与任何一版上游都配不上
（匹配 1/23），配对关系永久丢失，**而界面全绿**。
这一份把那张表钉成断言——0 成本、不落盘、不碰网络。
"""
from __future__ import annotations

import pytest

from contracts.invariants import FRESHNESS, FRESHNESS_REASON
from contracts.schema.stage7 import NameMapFreeProvenance, NameMapParams
from novel import prov
from novel.run import RunContext

FP_V1 = prov.fingerprint('v1')
FP_V2 = prov.fingerprint('v2')
PROJ_A = prov.projection_fingerprint({'a': 1})
PROJ_B = prov.projection_fingerprint({'a': 2})
KEY = 'stage1/main_text.json'


def test_rule_table_fingerprint_is_stage_scoped_and_covers_every_rule_field():
    from contracts.rules import REGISTRY

    rules = sorted(REGISTRY.of_stage('stage5'), key=lambda rule: rule.rule)
    expected_rows = tuple(
        tuple(getattr(rule, name) for name in rule.__dataclass_fields__)
        for rule in rules
    )
    got = prov.rules_fingerprint('stage5')
    assert got == prov.fingerprint(repr(expected_rows))
    assert len(got) == 10
    assert got != prov.rules_fingerprint('stage6')


def test_stage7_human_only_provenance_has_no_model(tmp_path):
    """首次人工改名没有模型基线时，也必须能构造符合当前 NameMap 契约的来源账。"""
    novel_dir = tmp_path / 'book_1'
    (novel_dir / 'stage0').mkdir(parents=True)
    (novel_dir / 'stage0' / 'clean_text.txt').write_text('根正文\n', encoding='utf-8')
    ctx = RunContext.open(tmp_path, '1', 'stage7.rename', paid=False, write_ledger=False)
    provenance = ctx.free_provenance(
        NameMapFreeProvenance,
        prompts={'RULES': 'rules'},
        params=NameMapParams(max_tokens=16000),
        origin='human',
    )
    dumped = provenance.to_disk()
    assert dumped['origin'] == 'human'
    assert 'model' not in dumped


def _fresh(recorded, *, now=FP_V1, consumed=None):
    return prov.freshness(recorded,
                          file_now=lambda _k: now,
                          consumed_now=lambda _k: consumed)


def _edge(file=FP_V1, consumed=None):
    return {KEY: {'file': file, 'consumed': consumed}}


# ─────────────────────────────────────────────────────────────────────────────
# 一、`_common.md` §四 那张表，一行一条
# ─────────────────────────────────────────────────────────────────────────────

def test_no_provenance_is_unknown_never_fresh():
    """**永不 `fresh`。** 缺失记成匹配就是替人断言一件没人验证过的事。"""
    r = prov.freshness(None, file_now=lambda _k: FP_V1)
    assert (r.state, r.reason) == ('unknown', 'no_provenance')


def test_empty_inputs_is_unknown_and_says_so_differently():
    """「有来源账但 `inputs` 是空的」与「根本没有来源账」是两件事。

    合成一个 reason 之后，「这个节点漏记了上游」与「这份产物早于这套机制」就分不开了。
    """
    r = prov.freshness({}, file_now=lambda _k: FP_V1)
    assert (r.state, r.reason) == ('unknown', 'no_inputs')


def test_a_ghost_edge_is_unknown_and_gets_named():
    """⚠⚠ **这条是 `Q-4` 亲自点过的一个 bug。**

    原型的比对函数对「取不到指纹」的条目**静默跳过**——于是一条指向已删文件的边
    既不报错、也不报「不知道」，只是安静地当作一条正常的上游边。
    盘上真有一条：`成年人不做选择` 那本的 `stage4/writer_context.json` 挂着
    `stage4/plan_chapters.INVALID.json`，而 0 成本提升路**读完就把它删了**。

    ⇒ 取不到指纹 = 那份上游已经不在盘上，**要报出来，并按名字说是哪一条**。
    """
    r = prov.freshness(_edge(), file_now=lambda _k: None)
    assert (r.state, r.reason) == ('unknown', 'ghost')
    assert r.ghosts == (KEY,), '不点名的话，人无从判断这条边要不要重新挂钩'


def test_file_changed_is_stale():
    assert _fresh(_edge(), now=FP_V2).state == 'stale'


def test_consumed_null_is_unknown_not_fresh():
    """**「没声明读了什么」不是「读的东西没变」。**

    `consumed=null` 的含义是「本节点没有声明自己读了哪些字段」——
    **建槽位，不填槽位**：七个现役层逐步补投影，在补上之前这条边诚实地是 `unknown`。
    """
    r = _fresh(_edge(consumed=None), now=FP_V1)
    assert (r.state, r.reason) == ('unknown', 'consumed_undeclared')


def test_consumed_match_beats_a_changed_file():
    """⚠⚠ **这一格存在的全部理由。**

    `Q-4` 逐字：「指纹是**文件级**的：改一个不相干的字段会让整份产物的指纹失配，
    把一批结论降级成『读数不可用』，且没有重新挂钩的路。
    **字段级指纹是一次契约改版，值得在新骨架里就做对。**」
    ⇒ **判定顺序是 `consumed` 优先于 `file`**，不是反过来。
    """
    r = _fresh(_edge(consumed=PROJ_A), now=FP_V2, consumed=PROJ_A)
    assert (r.state, r.reason) == ('fresh', 'match'), \
        '整份文件变了但我依赖的那部分没变 —— 这时候判 stale 就是那次「读数不可用」的降级'


def test_consumed_changed_is_stale_even_when_the_file_matches():
    r = _fresh(_edge(consumed=PROJ_A), now=FP_V1, consumed=PROJ_B)
    assert (r.state, r.reason) == ('stale', 'consumed_changed')


def test_declared_projection_that_cannot_be_recomputed_never_upgrades_to_fresh():
    """声明过投影、这次却算不出来 ⇒ 只能降级，不许升级。

    文件没变也只到 `unknown`——我们**没验过**投影这一层。
    """
    r = _fresh(_edge(consumed=PROJ_A), now=FP_V1, consumed=None)
    assert (r.state, r.reason) == ('unknown', 'consumed_unresolved')
    # 文件同时也变了 ⇒ stale 压过 unknown
    assert _fresh(_edge(consumed=PROJ_A), now=FP_V2, consumed=None).state == 'stale'


def test_all_edges_match_is_fresh():
    r = _fresh(_edge(consumed=PROJ_A), now=FP_V1, consumed=PROJ_A)
    assert (r.state, r.reason) == ('fresh', 'match')


def test_stale_outranks_unknown_outranks_fresh():
    """**优先级 `stale` > `unknown` > `fresh`。** 三条边各一种，汇总必须取最坏的那条。"""
    recorded = {
        'stage1/a.json': {'file': FP_V1, 'consumed': PROJ_A},   # fresh
        'stage1/b.json': {'file': FP_V1, 'consumed': None},     # unknown
        'stage1/c.json': {'file': FP_V2, 'consumed': None},     # stale
    }
    fps = {'stage1/a.json': FP_V1, 'stage1/b.json': FP_V1, 'stage1/c.json': FP_V1}
    r = prov.freshness(recorded, file_now=fps.get,
                       consumed_now=lambda k: PROJ_A if k == 'stage1/a.json' else None)
    assert r.state == 'stale'
    assert {e.key: e.state for e in r.edges} == {
        'stage1/a.json': 'fresh', 'stage1/b.json': 'unknown', 'stage1/c.json': 'stale'}


def test_a_flat_input_edge_is_rejected_as_stale_without_crashing():
    """不合当前契约的裸指纹只报告 stale，不升级也不导致报告崩溃。"""
    r = _fresh({KEY: FP_V1}, now=FP_V1)
    assert (r.state, r.reason) == ('stale', 'file_changed')


def test_a_malformed_edge_is_stale_not_an_exception():
    """真正畸形的边（`int` / `list` / `None`）判 **stale**，不抛。

    ⚠ 这是**挑过的一边**：判 stale 的代价是一次多余重跑，判 fresh 的代价是
    拿一份来历说不清的产物当「没问题」。而抛异常的代价最大——
    `store.freshness` 承诺过「别让一条坏边把整份报告干掉」。
    """
    for bad in (123, ['a'], None, {'file': 123}):
        r = _fresh({KEY: bad}, now=FP_V1)
        assert (r.state, r.reason) == ('stale', 'file_changed'), f'{bad!r} 判成了 {r.state}'


def test_a_non_dict_inputs_container_reads_as_no_inputs():
    """`inputs` 整个不是 dict ⇒ `no_inputs`，**不是 `no_provenance`**。

    「有来源账但读不出上游」与「根本没有来源账」是两件事——合成一个 reason 之后，
    「这个节点漏记了上游」与「这份产物早于这套机制」就分不开了。
    """
    assert prov.freshness(['a'], file_now=lambda _k: FP_V1).reason == 'no_inputs'
    assert prov.freshness('abc', file_now=lambda _k: FP_V1).reason == 'no_inputs'


def test_every_reason_in_the_enum_is_reachable():
    """守卫的守卫：`FRESHNESS_REASON` 里出现一个**没有任何路径产得出来**的取值，
    上面那些断言全都照绿——而那正是「取值面在说假话」的形状。

    ``global_naming_blocked`` 与 ``legacy_blueprint_inputs`` 都不是通用
    provenance 比对结果；前者由 store 的称名闸生成，后者只有 store 亲见
    Blueprint 原始 inputs 的精确旧键形才铸造，故不要求 ``prov.freshness``
    伪造可达路径。
    """
    seen = set()
    for r in (prov.freshness(None, file_now=lambda _k: None),
              prov.freshness({}, file_now=lambda _k: None),
              prov.freshness(_edge(), file_now=lambda _k: None),
              _fresh(_edge(), now=FP_V2),
              _fresh(_edge(consumed=PROJ_A), now=FP_V1, consumed=PROJ_B),
              _fresh(_edge(consumed=None), now=FP_V1),
              _fresh(_edge(consumed=PROJ_A), now=FP_V1, consumed=None),
              _fresh(_edge(consumed=PROJ_A), now=FP_V1, consumed=PROJ_A)):
        seen.add(r.reason)
        seen |= {e.reason for e in r.edges}
    store_only = {'global_naming_blocked', 'legacy_blueprint_inputs'}
    generic_reasons = set(FRESHNESS_REASON) - store_only
    assert store_only <= set(FRESHNESS_REASON)
    assert seen == generic_reasons, (
        f'这些通用取值没有任何路径产得出来：{sorted(generic_reasons - seen)}')


def test_states_are_exactly_the_invariant_domain():
    """算法产出的三态必须**逐值等于** `contracts.invariants.FRESHNESS`。"""
    from novel.prov import _REASON_STATE
    assert set(_REASON_STATE.values()) == set(FRESHNESS)


# ─────────────────────────────────────────────────────────────────────────────
# 二、两把尺子 —— 10 位永不与 40 位比较
# ─────────────────────────────────────────────────────────────────────────────

def test_the_two_rulers_are_two_functions_not_one_with_a_width_argument():
    """两把尺子**故意留两把**，但产地各只产一种宽度。

    ⚠ 原型的病不是「有两个宽度」，是**同一个字段在两处用不同宽度**
    （`n1_partition` 记全宽而 `stage2_common` 记 10 位），
    于是同一份文件的指纹在两份产物里长得不一样，谁也不敢拿它们对比。
    ⇒ 没有 `width=` 这个参数，就没有可以传错的东西。
    """
    assert len(prov.fingerprint('x')) == 10
    assert len(prov.fingerprint_bytes(b'x')) == 10
    assert len(prov.content_sha1(b'x')) == 40
    assert prov.content_sha1(b'x').startswith(prov.fingerprint_bytes(b'x'))
    import inspect
    for fn in (prov.fingerprint, prov.fingerprint_bytes, prov.content_sha1):
        assert 'width' not in inspect.signature(fn).parameters


def test_projection_fingerprint_is_stable_across_key_order():
    """投影指纹必须是**确定性**的，否则每条边都恒 `stale`——一个恒响的告警等于没有告警。"""
    assert prov.projection_fingerprint({'a': 1, 'b': 2}) == prov.projection_fingerprint({'b': 2, 'a': 1})
    assert prov.projection_fingerprint({'a': '中文'}) != prov.projection_fingerprint({'a': '中'})


# ─────────────────────────────────────────────────────────────────────────────
# 三、代码身份 —— 同提交下的不同脏树必须分得开
# ─────────────────────────────────────────────────────────────────────────────

COMMIT = b'0123456789abcdef0123456789abcdef01234567\n'


def _fake_git(*, diff: bytes = b'', untracked: bytes = b''):
    def run(*args: str) -> bytes | None:
        if args == ('rev-parse', 'HEAD'):
            return COMMIT
        if args[:2] == ('diff', '--binary'):
            return diff
        if args == ('ls-files', '--others', '--exclude-standard', '-z'):
            return untracked
        raise AssertionError(args)
    return run


def test_git_query_allows_windows_cold_start_but_stays_bounded(monkeypatch):
    """Windows 冷启动长尾不得被旧的 5 秒上限误读成 ``unknown``。"""
    seen: dict[str, object] = {}

    class _Done:
        returncode = 0
        stdout = b'answer'

    def _run(argv, **kwargs):
        seen['argv'] = argv
        seen.update(kwargs)
        return _Done()

    monkeypatch.setattr(prov.subprocess, 'run', _run)
    assert prov._git('rev-parse', 'HEAD') == b'answer'
    argv = seen['argv']
    assert isinstance(argv, list)
    assert any(str(arg).startswith('safe.directory=') for arg in argv)
    assert seen['cwd'] == prov._REPO_ROOT
    assert seen['capture_output'] is True
    assert seen['timeout'] == prov._GIT_QUERY_TIMEOUT_SECONDS
    assert prov._GIT_QUERY_TIMEOUT_SECONDS >= 45


def test_git_query_timeout_remains_fail_closed(monkeypatch):
    def _timeout(*_args, **_kwargs):
        raise prov.subprocess.TimeoutExpired(
            'git', prov._GIT_QUERY_TIMEOUT_SECONDS)

    monkeypatch.setattr(prov.subprocess, 'run', _timeout)
    assert prov._git('rev-parse', 'HEAD') is None


def test_worktree_digest_stops_after_the_first_failed_query(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def _failed_diff(*args: str):
        calls.append(args)
        if args[0] == 'diff':
            return None
        raise AssertionError('diff 已失败，不应继续等待 ls-files')

    monkeypatch.setattr(prov, '_git', _failed_diff)
    assert prov._worktree_digest() is None
    assert calls == [('diff', '--binary', '--no-ext-diff', 'HEAD', '--', '.')]


def test_clean_code_identity_is_the_full_commit(monkeypatch):
    monkeypatch.setattr(prov, '_git', _fake_git())
    assert prov.code_version() == COMMIT.decode().strip()


def test_tracked_dirty_identity_is_stable_and_distinguishes_the_diff(monkeypatch):
    monkeypatch.setattr(prov, '_git', _fake_git(diff=b'patch-a'))
    first = prov.code_version()
    assert first == prov.code_version()
    monkeypatch.setattr(prov, '_git', _fake_git(diff=b'patch-b'))
    second = prov.code_version()
    assert first != second
    assert first.startswith(COMMIT.decode().strip() + '-dirty.')


def test_untracked_file_bytes_are_part_of_the_dirty_identity(monkeypatch, tmp_path):
    (tmp_path / 'new.py').write_bytes(b'one')
    monkeypatch.setattr(prov, '_REPO_ROOT', str(tmp_path))
    monkeypatch.setattr(prov, '_git', _fake_git(untracked=b'new.py\0'))
    first = prov.code_version()
    (tmp_path / 'new.py').write_bytes(b'two')
    second = prov.code_version()
    assert first != second


def test_incomplete_git_identity_is_unknown_not_a_plausible_commit(monkeypatch):
    monkeypatch.setattr(prov, '_git', lambda *_args: None)
    assert prov.code_version() == prov.CODE_UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# 四、轮次
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('seen,expect', [
    (set(), 1),
    ({1}, 2),
    ({1, 3}, 4),        # ← **缺号的那一档**：`len(glob)+1` 在这里会返回 3，与现存的 r3 撞号
])
def test_next_round_is_max_plus_one_not_count_plus_one(seen, expect):
    """⚠ `len(glob(...)) + 1` 有一个**静默的**失效模式：归档目录里少了一份，
    计数就退回去，下一轮与现存的某一轮**同号**，历史从那里断掉——
    而这个错误不会报任何东西，它只是安静地覆盖掉一份归档。
    """
    assert prov.next_round(seen) == expect
    assert prov.next_round(seen) != len(seen) + 1 or seen != {1, 3}


# ─────────────────────────────────────────────────────────────────────────────
# 四、`code`
# ─────────────────────────────────────────────────────────────────────────────

def test_code_version_never_returns_empty():
    """`code` 是必填。问不到 git 时也必须给一个**读得出「说不出来」**的值，不许留空。"""
    v = prov.code_version()
    assert v and v.strip() == v
    assert v == prov.CODE_UNKNOWN or len(v) >= 4


def test_code_version_uses_full_commit_and_content_sensitive_dirty_digest():
    """不能退回短提交或只有一个无内容区分力的 ``-dirty`` 后缀。"""
    import inspect
    src = inspect.getsource(prov.code_version)
    assert "'rev-parse', 'HEAD'" in src
    assert '_worktree_digest()' in src
    assert "f'{commit}-dirty.{dirty}'" in src


def test_code_version_is_asked_again_for_every_run(monkeypatch):
    """⭐ **「一次跑只问一次」不许用 `lru_cache` 实现。**

    零参函数上的 `lru_cache` 是**进程级**不是 run 级：长驻服务里
    **第一次跑问到的答案会被盖到此后每一份产物上**——包括那个 `-dirty` 后缀在不在。
    于是「跑到一半有人 commit 了 / 改了工作树」再也读不出来，
    正是加 `--dirty` 要消灭的那个失败换了个更隐蔽的形态回来。
    ⇒ 固化的落点是 `RunContext.code`（run 级字段），本函数保持无状态。
    """
    calls: list[tuple[str, ...]] = []
    commit = b'a' * 40 + b'\n'

    def _fake_git(*args: str):
        calls.append(args)
        return commit

    monkeypatch.setattr(prov, '_git', _fake_git)
    monkeypatch.setattr(prov, '_worktree_digest', lambda: '')
    assert prov.code_version() == 'a' * 40
    assert prov.code_version() == 'a' * 40
    assert len(calls) == 2, '第二次没有重新问 git —— 答案被固化到了进程上，而不是这一次跑上'
    assert not hasattr(prov.code_version, 'cache_info'), '`lru_cache` 又回来了'


def test_an_untracked_nested_directory_does_not_erase_the_code_identity(monkeypatch):
    """⭐ **仓根有一个 `.worktrees/` 就让 `code_version()` 恒 `unknown`。**

    `git ls-files --others` 把「整块未跟踪的目录」折叠成一条**目录项**，链接
    worktree / 嵌套仓库尤其如此（git 明确拒绝 descend 进去）。原来那一行直接
    `open()` 它，目录 `open` 抛 `PermissionError` ⇒ 摘要 `None` ⇒ 身份 `unknown`。
    实测形态最坏的地方在于它**不报错**：本机产出的每一份产物都把 `code` 记成
    `unknown`，而这一格的全部价值就是回答「这次跑用的是哪版代码」。
    """
    commit = b'b' * 40 + b'\n'

    def _fake_git(*args: str):
        if args[0] == 'rev-parse':
            return commit
        if args[0] == 'diff':
            return b''
        return b'.worktrees/some-linked-worktree/\0'

    monkeypatch.setattr(prov, '_git', _fake_git)
    got = prov.code_version()
    assert got != prov.CODE_UNKNOWN, (
        '未跟踪的嵌套目录把代码身份抹成了 unknown —— '
        '「不知道」被安静地写进了每一份产物的 `code`')
    assert got.startswith('b' * 40 + '-dirty.'), got


def test_a_nested_directory_still_changes_the_identity(monkeypatch):
    """目录项按名字入摘要：它出现/消失仍然改变身份，只是不假装读得到内容。"""
    commit = b'c' * 40 + b'\n'

    def _git_with(untracked: bytes):
        def _fake(*args: str):
            if args[0] == 'rev-parse':
                return commit
            if args[0] == 'diff':
                return b''
            return untracked
        return _fake

    monkeypatch.setattr(prov, '_git', _git_with(b''))
    clean = prov.code_version()
    monkeypatch.setattr(prov, '_git', _git_with(b'.worktrees/a/\0'))
    with_dir = prov.code_version()
    monkeypatch.setattr(prov, '_git', _git_with(b'.worktrees/b/\0'))
    other_dir = prov.code_version()
    assert clean != with_dir != other_dir and clean != other_dir, (
        '嵌套目录换了一个名字，代码身份却没变')
