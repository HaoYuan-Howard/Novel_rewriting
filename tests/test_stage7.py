#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage7 当前改名、替换、定稿与成书契约。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage7 import NameMap
from novel import prov
from novel.errors import UpstreamMissing
from novel.run import RunContext
from novel.stages.stage7 import assemble, prompts, rename
from tests import corpus, sandbox

STAGE7_REQUIRED = ('stage1/main_text.json', 'stage2/global_grounding.json',
                   'stage4/plan_chapters.json', 'stage4/writer_context.json',
                   'stage7/name_map.json')
STAGE7_OPTIONAL = ('stage6/hook.json',)


def books() -> list[str]:
    if corpus.corpus_root() is None:
        return []
    by_book: dict[str, set[str]] = {}
    for artifact in corpus.iter_artifacts():
        by_book.setdefault(artifact.book_dir, set()).add(artifact.rel)
    required = set(STAGE7_REQUIRED)
    return sorted(book for book, rels in by_book.items() if required <= rels)


pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        not books(), reason='盘上没有 stage7 产物 —— 本文件整份**没在查**，不是「查过没事」'),
]


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted({a.rel for a in corpus.iter_artifacts()
                         if a.book_dir == book and a.pattern == 'stage5/{mode}/ch{nn}.json'
                         and '_rounds' not in a.rel}))


def _box(book: str) -> sandbox.Sandbox:
    """当前契约 JSON 沙箱，并带上由 Stage7 生成的 `book.txt`。"""
    rels = STAGE7_REQUIRED + tuple(
        r for r in STAGE7_OPTIONAL if sandbox.cut(book, r) is not None)
    box = sandbox.Sandbox(book, rels + _drafts_of(book))
    src = corpus.corpus_root() / book / 'stage7' / 'book.txt'
    if src.exists():
        shutil.copy2(src, box.dir / 'stage7' / 'book.txt')
    return box


@pytest.fixture(params=books(), ids=lambda b: b[:10])
def box(request):
    b = _box(request.param)
    yield b
    b.close()


def _ctx(box: sandbox.Sandbox, node: str = 'stage7.rename') -> RunContext:
    return RunContext.open(str(box.root), box.sid, node, paid=False)


def _check(box: sandbox.Sandbox):
    return rename.run_check_only(_ctx(box))


def _allow_hook_quality(monkeypatch):
    """隔离 Stage7 自身；Stage6 下游质量闸由专门反例覆盖。"""
    from novel.stages.stage6 import semantic
    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args, **_kwargs: None)


# ── 一、报告的形状 ─────────────────────────────────────────────────────────

def test_every_registered_rule_produced_a_row(box):
    """当前判据一行不少，`skipped` 0 条。

    报告由 `build()` 遍历**注册表**生成，不遍历产出 ⇒ 一条判据不可能因为
    「实现忘了调」而从报告里消失。少调一条，它会以 `skipped` 出现在这里。
    """
    rep = _check(box)
    want = {r.rule for r in REGISTRY.of_stage('stage7')}
    assert len(want) == 33
    assert {r.rule for r in rep.rules} == want
    skipped = sorted(r.rule for r in rep.rules if r.status == 'skipped')
    assert skipped == [], f'这些判据注册了却没被调用：{skipped}'


def test_body_stale_reports_every_draft_as_its_scan_denominator(box):
    """全都 fresh 时 stale 列表为 0，但判据仍实际扫描了全部成稿。"""
    rep = _check(box)
    line = next(r for r in rep.rules if r.rule == 'rn_body_stale')
    assert line.scanned == f'{len(_drafts_of(box.book))} 份成稿 + 文案'


@pytest.mark.parametrize('rel', ('stage4/plan_chapters.json',
                                 'stage4/writer_context.json'))
def test_stage7_requires_current_stage4_upstreams(box, rel):
    """Stage7 只接收完整的当前 Stage4 契约，不降级到缺计划/缺上下文模式。"""
    (box.dir / rel).unlink()
    with pytest.raises(UpstreamMissing, match=rel):
        _check(box)


def test_the_badge_can_never_be_pass_and_that_is_by_design(box):
    """⭐ **stage7 的徽章在结构上永远不可能是 `pass`。**

    7 条 `unver` 每次都输出，而徽章见 `unver` 就至少 amber。
    **这不是缺陷**，是本层「能查的都查了、查不了的写在脸上」的可见形态：
    `passed` 仍是 `True`、退出码仍是 0 ——**「有查不了的」不等于「不合格」**。
    与 stage6 是同一个形状。
    """
    rep = _check(box)
    hard_hit = any(r.sev == 'hard' and r.status == 'hit' for r in rep.rules)
    assert rep.badge == ('fail' if hard_hit else 'amber')
    assert rep.passed is (not hard_hit)
    assert sum(1 for r in rep.rules if r.sev == 'unver') == 7


def test_no_hard_rule_is_hit_on_disk(box):
    """15 条 HARD 盘上 **0 命中**。

    ⚠ **别读成「判据多余」**：恒等基线三本逐字节无损（说明替换器与组装器是对的），
    而残留探针每本都在报东西（说明探针在工作）。
    ⚠ 它同时是一条登记在案的空白：跨层规矩「每新加一条 HARD 就要有一份会让它红的样本」
    在本层是 **0/15**——那几份样本由 `test_the_gate_blocks_...` 一族合成。
    """
    hard_hits = sorted(r.rule for r in _check(box).rules
                       if r.sev == 'hard' and r.status == 'hit')
    assert hard_hits == []


def test_the_residual_probe_always_reports_its_denominator(box):
    """是否命中随小说变化，但探针分母必须常驻。"""
    row = next(r for r in _check(box).rules if r.rule == 'rn_residual_stem')
    assert row.status in ('ok', 'hit')
    assert row.scanned and '姓字覆盖率' in row.scanned


# ── 二、⭐ 逐字节对账 ──────────────────────────────────────────────────────

def test_the_assembled_book_is_byte_identical_to_the_one_on_disk(box, monkeypatch):
    """已有当前交付物时，确定性重拼必须逐字节相同。"""
    source = corpus.corpus_root() / box.book / 'stage7' / 'book.txt'
    if not source.exists():
        pytest.skip('当前小说尚未组装 book.txt')
    on_disk = source.read_bytes()
    _allow_hook_quality(monkeypatch)
    rename.run_approve(_ctx(box, 'stage7.approve'))
    path, art, _rep = assemble.run_assemble(_ctx(box, 'stage7.assemble'))
    assert Path(path).read_bytes() == on_disk
    assert art.book_txt is not None
    assert art.book_txt.sha1 == prov.content_sha1(on_disk)


def test_the_book_txt_byte_form_holds(box, monkeypatch):
    """字节形态：LF-only · 章分隔是**裸阿拉伯数字**且恰好 `1..N` · 没有审阅元信息。"""
    _allow_hook_quality(monkeypatch)
    rename.run_approve(_ctx(box, 'stage7.approve'))
    path, art, _rep = assemble.run_assemble(_ctx(box, 'stage7.assemble'))
    txt = Path(path).read_bytes().decode('utf-8')
    assert '\r' not in txt
    digits = [ln for ln in txt.split('\n') if ln.strip().isdigit()]
    assert digits == [str(i) for i in range(1, art.book_txt.chapters + 1)]
    assert '第1章' not in txt and box.book.rsplit('_', 1)[0] not in txt
    # ⚠ **全链第三个 `chars` 口径**：含空白，**并且含章号行与全部块间分隔符**。
    assert art.book_txt.chars == len(txt)


def test_assemble_refuses_when_the_approved_hook_is_not_quality_clear(box, monkeypatch):
    """定稿后改字也不能绕过 Stage6 审判进入交付物。"""
    from novel.errors import TerminalState
    from novel.stages.stage6 import semantic

    if not (box.dir / 'stage6' / 'hook.json').exists():
        pytest.skip('本书没有 Stage6 文案产物')
    target = box.dir / 'stage7' / 'book.txt'
    before = target.read_bytes() if target.exists() else None

    def reject(*_args, **_kwargs):
        raise TerminalState('定稿文本没有当前 clear 质量审判')

    monkeypatch.setattr(semantic, 'require_approved_clear', reject)
    with pytest.raises(TerminalState, match='clear'):
        assemble.run_assemble(_ctx(box, 'stage7.assemble'))
    after = target.read_bytes() if target.exists() else None
    assert after == before


def test_the_identity_baseline_is_byte_lossless(box):
    """恒等基线：占位符双射往返逐字节无损。

    ⚠ 它**验不了**的三样必须与结论存在一起（`R-10.7b`）——分开放，
    下一个人就会把它跑绿了读成「替换是安全的」。
    """
    base, _rep = rename.run_identity(_ctx(box))
    assert base.roundtrip_ok is True and base.mismatches == []
    assert base.chapters > 0, 'roundtrip_ok=True 而量了 0 章是一句空话'
    assert len(base.blind) == 3
    # 落进产物，而不是打印完就扔
    assert NameMap.model_validate(box.read('stage7/name_map.json')).identity == base


def test_identity_preflight_works_before_the_first_name_map_exists(box):
    """首次付费前没有本层产物：预检仍应可运行，且不得把未跑的映射规则报绿。"""
    (box.dir / 'stage7' / 'name_map.json').unlink()
    base, rep = rename.run_identity(_ctx(box))
    assert base.roundtrip_ok and base.chapters > 0
    row = next(r for r in rep.rules if r.rule == 'rn_identity_baseline')
    assert row.status == 'ok'
    assert rep.provenance.freshness == 'unknown'
    assert any(r.status == 'skipped' for r in rep.rules if r.rule != 'rn_identity_blind')
    assert not (box.dir / 'stage7' / 'name_map.json').exists()


def test_rejudging_reproduces_every_stored_current_rule(box):
    """产物中的判据 id 必须全部属于当前注册表，并可由重判路径报出。"""
    stored = {f.rule for f in NameMap.model_validate(box.read('stage7/name_map.json')).book}
    current = {r.rule for r in REGISTRY.of_stage('stage7')}
    assert stored <= current
    rep = _check(box)
    spoke = {r.rule for r in rep.rules if r.findings or r.status == 'unver'}
    assert stored <= spoke, f'盘上有而重判没产出：{sorted(stored - spoke)}'


# ── 三、当前 prompt 契约 ─────────────────────────────────────────────────

def test_the_current_system_prompt_is_the_rendered_prompt():
    """真发出去的是 `rename_system()`，而它 = `fill(RENAME_SYS)` + JSON 形状块。

    ⚠ 本条原来断言 `rename_system() == RENAME_SYS`，于是 `with_json_shape` 一进来就恒红。
    那条断言从口径上就是错的：`_common.md` §八 写着来源账记的是**插值之后**的指纹，
    而 `rename.py` 两个调用点（`366` / `581`）发的都是 `rename_system()`。
    ⇒ 该钉的是「常量原样在渲染结果里，且渲染只做加法」，不是「两者相等」。
    """
    from novel.llm.prompts import fill

    assert prompts.RENAME_SYS.strip()
    rendered = prompts.rename_system()
    assert fill(prompts.RENAME_SYS) in rendered, '渲染把 system 本体改写了，不只是补形状'
    assert rendered != prompts.RENAME_SYS, '形状块没接上——`ask_json` 会收到一个没说形状的 prompt'
    assert 'char_id' in rendered, '形状块接上了却不是 `RENAME_HINT`'


def test_the_user_blocks_are_a_byte_for_byte_port_too(box):
    """⚠ **user 那一段在原型里没有具名常量** —— 它是五个内联块头。

    只比 system 会漏掉整个下发面：`given` 一变，`universe` 就变，
    而 `universe` 同时是**模型看见的**与**替换器屏蔽面**。
    """
    _sys, user, mat = rename.build_prompt(_ctx(box))
    assert len(mat.blocks) == 5
    for head in ('—— 给这本书的人物换真名。',
                 '── 时代背景（新名必须贴合它）──',
                 '── 人物表（attested=false 的 canonical 是内部记账标签，不是原作里的叫法）──',
                 '── 人物关系（只为理解，不要据此重新划分族群）──',
                 '── 姓氏族群（已按原作算好；给每一组挑一个新姓，组内共用）──'):
        assert head in user
    assert '不在这张表里的角色**照样要按规则 1 判断改不改名**' in user
    # 下发面**不许**带正文、结局、秘密
    assert 'fate' not in mat.given and 'name_distinctiveness' not in mat.given


def test_the_downsend_face_and_the_probe_share_one_universe(box):
    """`universe` 只有一处产出：下发面与屏蔽面一旦分家，
    「模型看见的」与「替换器认得的」就不是同一批字面了。"""
    _sys, _user, mat = rename.build_prompt(_ctx(box))
    art = NameMap.model_validate(box.read('stage7/name_map.json'))
    covered = set(art.subs) | set(art.kept)
    missing = sorted(set(mat.universe) - covered)
    assert not missing, f'下发了却既没换也没留：{missing[:8]}'


# ── 四、`_provenance` 的账 ────────────────────────────────────────────────

def test_approve_leaves_provenance_alone(box):
    """`stage7.approve` **`_provenance` 一个字都不动**（`stage7.md` 事实⑦）。

    原型的四条 0 成本路走同一个保存函数，它无条件盖上**当前**的 prompt 指纹
    ——而 `stage7.approve` 根本不重判。盘上 11 份产物 `code` 全是同一个值，
    `N9_SYS` 却取过**三个**，那是逐字节的铁证。
    """
    before = box.read('stage7/name_map.json')['_provenance']
    art, _rep = rename.run_approve(_ctx(box, 'stage7.approve'))
    assert art.approved is not None and art.approved.origin == 'human'
    assert box.read('stage7/name_map.json')['_provenance'] == before


def test_approve_refuses_while_a_hard_rule_is_still_hit(box):
    """还有 HARD 时**拒绝定稿**——人被挡住，不是被静默放行。"""
    from contracts.schema.report import finding
    from novel.errors import TerminalState
    nm = box.read('stage7/name_map.json')
    # ⚠ 用**真的那个工厂**造这条合成结论：手写一个 dict 就等于给自己发一张
    #   「载体后置条件不适用」的通行证，而那正是本项目要消灭的形状。
    nm['book'].append(finding('rn_ambiguous', detail='合成的一条阻断级结论',
                              evidence=['阿禾']).model_dump(by_alias=True))
    box.write('stage7/name_map.json', nm)
    with pytest.raises(TerminalState, match='不该定稿'):
        rename.run_approve(_ctx(box, 'stage7.approve'))


def test_recheck_reproduces_every_stored_conclusion(box):
    """⭐ **重判 == 存盘**：`--recheck` 从 registry 重算整条确定性链，
    盘上那 7 条结论**一条不少**地被重新产出来。

    对位 stage3 的「23 项逐值」与 stage6 的「12 份文本」。它抓的是**逐字移植时
    漏一个字符类、少一个前瞻断言**——那种错不会报错，只会让某条判据在某类输入上
    静默地不再拦。
    """
    stored = {f.rule for f in NameMap.model_validate(box.read('stage7/name_map.json')).book}
    art, _rep = rename.run_recheck(_ctx(box))
    assert stored <= {f.rule for f in art.book}, \
        f'重判丢了：{sorted(stored - {f.rule for f in art.book})}'
    # ⚠ 归档在写之前：`--recheck` 会覆盖 `book[]`，而那是「上一轮判成什么样」的唯一记录。
    assert (box.dir / 'stage7' / '_rounds' / 'name_map.r1.json').exists()
    # ⚠ 来源账**继承**，只标混版——本次一个请求都没发过。
    prov_d = box.read('stage7/name_map.json')['_provenance']
    assert prov_d['partial_rewrite'] == '--recheck'
    assert prov_d['prompts'] == \
        box.read('stage7/_rounds/name_map.r1.json')['_provenance']['prompts']


def test_a_human_added_mapping_that_collides_is_reported_but_never_blocked(box):
    """人工补充映射的碰撞只进入 INFO，不扩大模型碰撞 HARD 的取值面。"""
    art, rep = rename.run_recheck(_ctx(box))
    collided = [f for f in art.book if f.rule == 'rn_new_collide_human']
    hard = next(r for r in rep.rules if r.rule == 'rn_new_collide')
    assert hard.status == 'ok', 'HARD 那条不许因为人工补充而红'
    assert all(f.sev == 'info' for f in collided)


def test_identity_marks_the_artifact_as_partially_rewritten(box):
    """`--identity` 写盘 ⇒ 那份产物是**混版的**，而它必须说得出来。

    ⚠ 它与 `stage7.approve` 的区别正在这里：定稿一个由代码算出来的字段都没改。
    """
    rename.run_identity(_ctx(box))
    prov_d = box.read('stage7/name_map.json')['_provenance']
    assert prov_d.get('partial_rewrite') == '--identity'


# ── 五、闸门与提升路（**本层 0/15 反例样本，这几条就是那几份样本**）──────────

def _fake_client(ctx, model_out: dict):
    """按剧本回答的传输层。**不发任何请求。**"""
    import json as _json

    from novel.llm.client import LlmClient
    from novel.llm.transport import ChatResponse, FakeTransport
    t = FakeTransport.always(ChatResponse(
        content=_json.dumps(model_out, ensure_ascii=False), finish_reason='stop',
        usage=None, model='fake'))
    return LlmClient(ctx, t, model='fake', max_retries=1, backoff_base=0, concurrency=1)


def _model_out_from_disk(box: sandbox.Sandbox, *, break_it: bool) -> dict:
    """拿盘上那份当模型产出。`break_it` ⇒ 造一条 `rn_ambiguous`（一旧名两新名）。"""
    art = NameMap.model_validate(box.read('stage7/name_map.json'))
    chars = [{'char_id': c.char_id, 'new_canonical': c.new_canonical,
              'renamed': c.renamed, 'not_renamed_reason': c.not_renamed_reason,
              'aliases': [{'old': a.old, 'new': a.new} for a in c.aliases],
              'reason': c.reason} for c in art.characters]
    if break_it:
        victim = next(c for c in chars if c['renamed'])
        # 同一个旧名被同一个角色要求换成两个不同的新名 ⇒ **不猜**，判红。
        victim['aliases'] = list(victim['aliases']) + [
            {'old': victim['new_canonical'] and next(
                (a['old'] for a in victim['aliases'] if a['old'] != a['new']),
                victim['new_canonical']), 'new': '仌仌'}]
    return {'surname_map': dict(art.surname_map), 'characters': chars}


def test_the_gate_blocks_a_dirty_map_and_revalidate_promotes_it_after_the_fix(box, monkeypatch):
    """⭐ **落盘闸门照 stage3：任何一条 hard ⇒ `.INVALID`**（本层一次只产一份）。

    配套的 `--revalidate` 是它的**必然结果**——闸门是我们自己加的，
    不给一条 0 成本提升路，一次付费产出就被永久锁死。
    """
    from novel import paths
    from novel.errors import GateBlocked

    # ⚠ **先取出模型产出，再删产物**：付费路要求盘上没有正式稿（先校验后落盘）。
    dirty = _model_out_from_disk(box, break_it=True)
    _allow_hook_quality(monkeypatch)
    (box.dir / 'stage7' / 'name_map.json').unlink()
    ctx = RunContext.open(str(box.root), box.sid, 'stage7.rename', paid=True)
    with pytest.raises(GateBlocked, match='阻断级'):
        rename.run_rename(ctx, _fake_client(ctx, dirty))
    invalid = box.dir / paths.invalid_of('stage7/name_map.json')
    assert invalid.exists(), '闸门拦下了却没落盘 —— 那份稿就丢了'
    assert not (box.dir / 'stage7' / 'name_map.json').exists()

    # 提升路：问题还在 ⇒ 不许提升
    promoted, _rep = rename.run_revalidate(_ctx(box))
    assert promoted is False and invalid.exists()


def test_revalidate_rebuilds_stale_stored_findings_before_promoting(box):
    """判据修好后不能继续复读 `.INVALID.book[]` 里的旧 HARD。"""
    from contracts.schema.report import finding
    from novel import paths

    raw = box.read('stage7/name_map.json')
    raw['book'].append(finding(
        'rn_ambiguous', detail='旧代码留下、当前映射重算不出的 HARD',
        evidence=['幽灵结论']).model_dump(by_alias=True))
    (box.dir / 'stage7' / 'name_map.json').unlink()
    inv = paths.invalid_of('stage7/name_map.json')
    box.write(inv, raw)

    promoted, _rep = rename.run_revalidate(_ctx(box))
    assert promoted is True
    live = NameMap.model_validate(box.read('stage7/name_map.json'))
    assert not [f for f in live.book if f.rule == 'rn_ambiguous']


def test_apply_edit_can_patch_the_first_invalid_map_without_full_characters(box):
    """首次生成被拦时只有 `.INVALID`；patch-only 编辑仍必须有真实出路。"""
    from novel import paths

    raw = box.read('stage7/name_map.json')
    (box.dir / 'stage7' / 'name_map.json').unlink()
    box.write(paths.invalid_of('stage7/name_map.json'), raw)
    cid = raw['characters'][0]['char_id']
    box.write('stage7/name_map_edit.json', {
        'character_patches': [{
            'char_id': cid,
            'reason': '人工确认：只改说明，不重抄整张人物表',
        }],
    })

    art, rep = rename.run_apply_edit(_ctx(box), '')
    assert rep.passed is True
    assert (box.dir / 'stage7' / 'name_map.json').exists()
    assert next(c for c in art.characters if c.char_id == cid).reason.startswith('人工确认')


def test_a_clean_map_lands_and_the_gate_stays_open(box, monkeypatch):
    """同一条路，干净的那一份**照常落盘**——闸门不许把合法产出也拦住。"""
    clean = _model_out_from_disk(box, break_it=False)
    _allow_hook_quality(monkeypatch)
    (box.dir / 'stage7' / 'name_map.json').unlink()
    ctx = RunContext.open(str(box.root), box.sid, 'stage7.rename', paid=True)
    art = rename.run_rename(ctx, _fake_client(ctx, clean))
    assert art.book_txt is None and art.approved is None
    assert (box.dir / 'stage7' / 'name_map.json').exists()
    assert art.provenance.model == 'fake'
    assert art.identity is not None and art.identity.roundtrip_ok


def test_an_unused_surname_mapping_is_blocked_instead_of_polluting_the_probe(box, monkeypatch):
    """`surname_map` 不能独立于角色决定存在；实测 `太→宋` 曾让 14 个普通词假报警。"""
    from novel.errors import GateBlocked

    clean = _model_out_from_disk(box, break_it=False)
    _allow_hook_quality(monkeypatch)
    clean['surname_map']['太'] = '宋'
    (box.dir / 'stage7' / 'name_map.json').unlink()
    ctx = RunContext.open(str(box.root), box.sid, 'stage7.rename', paid=True)
    with pytest.raises(GateBlocked, match='rn_surname_split'):
        rename.run_rename(ctx, _fake_client(ctx, clean))
