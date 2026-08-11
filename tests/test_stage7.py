#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage7.py —— 人名替换与成书。**盘上三本的对账。**

形态照 `tests/test_stage6.py`（单文件、整份挂 skipif）。
纯函数那一族在 `tests/test_stage7_subs.py`——**它不该因为语料不在就没人查**。

⚠ 本层最值钱的一条在 `test_the_assembled_book_is_byte_identical_to_the_one_on_disk`：
**重拼出来的 `book.txt` 与原型交付的那一份 sha1 逐字节相同**。
它一次性钉住了替换器（最长优先 / 恒等屏蔽 / 单次扫描）与组装器（章分隔 / 块分隔 /
文案位置 / 编码 / 换行）——那种「逐字移植时漏一个字符类」的错不会报错，
只会让某条判据在某类输入上静默地不再拦。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage7 import NameMap
from novel import prov
from novel.run import RunContext
from novel.stages.stage7 import assemble, prompts, rename
from tests import corpus, sandbox

#: 本层要的上游。⚠ `stage4/*` 两份是 **optional**：某本的 `plan_chapters.json`
#: 在新契约下**读不回来**（它没有 `_provenance`），而那是登记在案的事实。
STAGE7_INPUTS = ('stage1/main_text.json', 'stage2/global_grounding.json',
                 'stage4/plan_chapters.json', 'stage4/writer_context.json',
                 'stage6/hook.json', 'stage7/name_map.json')


def books() -> list[str]:
    if corpus.corpus_root() is None:
        return []
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage7/name_map.json'})


pytestmark = pytest.mark.skipif(
    not books(), reason='盘上没有 stage7 产物 —— 本文件整份**没在查**，不是「查过没事」')


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted({a.rel for a in corpus.iter_artifacts()
                         if a.book_dir == book and a.rel.startswith('stage5/')
                         and '/ch' in a.rel and '_rounds' not in a.rel}))


def _box(book: str) -> sandbox.Sandbox:
    """沙箱 + 盘上那份 `book.txt`（它不是 JSON，`Sandbox` 不管它）。

    ⚠ **`book_txt.sha1` 要按盘上那份 txt 重算**：迁移填的是占位符
    （`corpus.py::RESHAPED` —— 盘上记的是 10 位来源指纹，新契约要 40 位内容指纹，
    **旧值推不出新值**）。不重算，`rn_txt_stale` 会在每一本上误报红。
    """
    rels = tuple(r for r in STAGE7_INPUTS if sandbox.cut(book, r) is not None)
    box = sandbox.Sandbox(book, rels + _drafts_of(book))
    src = corpus.corpus_root() / book / 'stage7' / 'book.txt'
    if src.exists():
        shutil.copy2(src, box.dir / 'stage7' / 'book.txt')
        nm = box.read('stage7/name_map.json')
        nm['book_txt']['sha1'] = prov.content_sha1(src.read_bytes())
        box.write('stage7/name_map.json', nm)
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


# ── 一、报告的形状 ─────────────────────────────────────────────────────────

def test_every_registered_rule_produced_a_row(box):
    """⭐ **33 条一行不少，`skipped` 0 条。**

    报告由 `build()` 遍历**注册表**生成，不遍历产出 ⇒ 一条判据不可能因为
    「实现忘了调」而从报告里消失。少调一条，它会以 `skipped` 出现在这里。
    """
    rep = _check(box)
    want = {r.rule for r in REGISTRY.of_stage('stage7')}
    assert len(want) == 33
    assert {r.rule for r in rep.rules} == want
    skipped = sorted(r.rule for r in rep.rules if r.status == 'skipped')
    assert skipped == [], f'这些判据注册了却没被调用：{skipped}'


def test_the_badge_can_never_be_pass_and_that_is_by_design(box):
    """⭐ **stage7 的徽章在结构上永远不可能是 `pass`。**

    7 条 `unver` 每次都输出，而徽章见 `unver` 就至少 amber。
    **这不是缺陷**，是本层「能查的都查了、查不了的写在脸上」的可见形态：
    `passed` 仍是 `True`、退出码仍是 0 ——**「有查不了的」不等于「不合格」**。
    与 stage6 是同一个形状。
    """
    rep = _check(box)
    assert rep.badge == 'amber'
    assert rep.passed is True
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


def test_hits_match_the_reconciliation(box):
    """命中集恒 `{rn_residual_stem}`。

    ⚠ `rn_identity_baseline` **不在里面**，而那要靠 `_box` 之外的一步：
    盘上三份产物的 `identity` 都是 `null`（原型只 `print`，没有落点）
    ⇒ 原样复检时它会命中一条「从没跑过」。本条查的是**跑过基线之后**的状态。
    """
    rename.run_identity(_ctx(box))
    hits = sorted({r.rule for r in _check(box).rules if r.status == 'hit'})
    assert hits == ['rn_residual_stem']


def test_the_residual_probe_is_the_only_one_that_actually_fires(box):
    """残留探针盘上 **3/3 命中**，条数与文档实测对得上（2 / 2 / 15）。"""
    row = next(r for r in _check(box).rules if r.rule == 'rn_residual_stem')
    assert row.status == 'hit' and len(row.findings) == 1
    assert '旧姓字落在替换面之外' in row.findings[0].detail
    # 分母必须报出来——没有分母的绿灯不携带信息
    assert row.scanned and '姓字覆盖率' in row.scanned


def test_the_identity_baseline_says_it_never_ran_instead_of_staying_quiet(box):
    """⭐ 盘上 `identity` 是 `null`，而报告必须**出声说「没跑过」**。

    原型的 `--identity` 整个输出是 `print` + 返回码 ⇒
    「跑过且逐字节无损」与「从没跑过」在盘上长得一模一样（`Q-2`）。
    ⚠ 那个 `null` **不是「迁移没填」，是实况**。
    """
    art = NameMap.model_validate(box.read('stage7/name_map.json'))
    assert art.identity is None, '盘上这三份产物早于 `identity` 这个键'
    row = next(r for r in _check(box).rules if r.rule == 'rn_identity_baseline')
    assert row.status == 'hit' and '从没跑过' in row.findings[0].detail


# ── 二、⭐ 逐字节对账 ──────────────────────────────────────────────────────

def test_the_assembled_book_is_byte_identical_to_the_one_on_disk(box):
    """⭐⭐ **本层最值钱的一条：重拼 == 盘上那份交付物，逐字节。**

    它一次钉住替换器（最长优先 / 恒等屏蔽 / 单次扫描不级联）与组装器
    （章分隔＝裸数字 / 块分隔 / 文案在最前 / UTF-8 / LF）。
    **不一致就是骨架抄错了，不是数据变了**——那三份 `book.txt` 一个字节都没动过。
    """
    on_disk = (corpus.corpus_root() / box.book / 'stage7' / 'book.txt').read_bytes()
    rename.run_approve(_ctx(box, 'stage7.approve'))
    path, art, _rep = assemble.run_assemble(_ctx(box, 'stage7.assemble'))
    assert Path(path).read_bytes() == on_disk
    assert art.book_txt is not None
    assert art.book_txt.sha1 == prov.content_sha1(on_disk)


def test_the_book_txt_byte_form_holds(box):
    """字节形态：LF-only · 章分隔是**裸阿拉伯数字**且恰好 `1..N` · 没有审阅元信息。"""
    rename.run_approve(_ctx(box, 'stage7.approve'))
    path, art, _rep = assemble.run_assemble(_ctx(box, 'stage7.assemble'))
    txt = Path(path).read_bytes().decode('utf-8')
    assert '\r' not in txt
    digits = [ln for ln in txt.split('\n') if ln.strip().isdigit()]
    assert digits == [str(i) for i in range(1, art.book_txt.chapters + 1)]
    assert '第1章' not in txt and box.book.rsplit('_', 1)[0] not in txt
    # ⚠ **全链第三个 `chars` 口径**：含空白，**并且含章号行与全部块间分隔符**。
    assert art.book_txt.chars == len(txt)


def test_the_identity_baseline_is_byte_lossless(box):
    """⭐ 恒等基线：占位符双射往返 **逐字节无损**（`R-10.7`），3/3。

    ⚠ 它**验不了**的三样必须与结论存在一起（`R-10.7b`）——分开放，
    下一个人就会把它跑绿了读成「替换是安全的」。
    """
    base, _rep = rename.run_identity(_ctx(box))
    assert base.roundtrip_ok is True and base.mismatches == []
    assert base.chapters > 0, 'roundtrip_ok=True 而量了 0 章是一句空话'
    assert len(base.blind) == 3
    # 落进产物，而不是打印完就扔
    assert NameMap.model_validate(box.read('stage7/name_map.json')).identity == base


def test_rejudging_reproduces_the_rule_set_on_disk(box):
    """⭐ **重判 == 存盘**：盘上 `book[]` 恒 7 条，三本规则集完全相同。

    ⚠ 新骨架会**多出**几条（`rn_identity_baseline` 是新增的、`unver` 全量输出），
    差额要逐条说得出来 ⇒ 本条断言的是「盘上那 7 条**一条不少地**都被重判出来」。
    """
    stored = {f.rule for f in NameMap.model_validate(box.read('stage7/name_map.json')).book}
    assert stored == {'rn_residual_stem', 'rn_stem_noise', 'rn_report', 'rn_style_report',
                      'rn_taste_unver', 'rn_coverage_unver', 'rn_title_unver'}
    # ⚠ 判「产出了没有」要看 `findings`，**不能看 `status`**：
    #   `metric` 永不命中（度量一命中就重新并回告警那一支），它的读数照样装在 `findings` 里。
    rep = _check(box)
    spoke = {r.rule for r in rep.rules if r.findings or r.status == 'unver'}
    assert stored <= spoke, f'盘上有而重判没产出：{sorted(stored - spoke)}'


# ── 三、prompt 逐字 ───────────────────────────────────────────────────────

def test_the_prompt_is_a_byte_for_byte_port():
    """`RENAME_SYS` 与原型 `N9_SYS` **逐字节相同**。"""
    src = _prototype_const('N9_SYS')
    if src is None:
        pytest.skip('原型仓不在旁边 —— 本条**没在查**')
    assert prompts.RENAME_SYS == src
    assert len(prompts.RENAME_SYS) == 1658


def test_the_rendered_prompt_is_byte_for_byte_too():
    """⭐⭐ **钉的是发出去的字节，不是模板。**

    F1 的教训①：`HOOK_SYS` 确实逐字节相同，而从原型搬进 `invariants` 的三句说明
    被**转述**了一遍，渲染结果短了 48 字——丢掉的正是那条唯一还禁的硬约束。
    **只钉模板的测试抓不到它**（占位符两侧一个字节都没变）。
    本层今天没有插值点，所以「渲染 == 模板」本身就是要钉的那件事。
    """
    src = _prototype_const('N9_SYS')
    if src is None:
        pytest.skip('原型仓不在旁边 —— 本条**没在查**')
    assert prompts.rename_system() == src


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


def _prototype_const(name: str) -> str | None:
    """从原型源码里**用 AST 取**那个字符串常量。不 import 它（那会拉进整条链）。"""
    import ast
    p = (Path(__file__).resolve().parent.parent.parent
         / 'Novel_refactoring' / 'Rapid_Prototyping' / 'Stage7' / 'n9_rename.py')
    if not p.exists():
        return None
    tree = ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name
                and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
            return n.value.value
    return None


# ── 四、`_provenance` 的账 ────────────────────────────────────────────────

def test_approve_leaves_provenance_alone(box):
    """`stage7.approve` **`_provenance` 一个字都不动**（`stage7.md` 事实⑦）。

    原型的四条 0 成本路走同一个保存函数，它无条件盖上**当前**的 prompt 指纹
    ——而 `--approve` 根本不重判。盘上 11 份产物 `code` 全是同一个值，
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
    """⭐ **事实⑤：`rn_new_collide`（HARD）的取值面够不到 `extra_subs` 那扇门。**

    盘上某本有一条 `祈辕→叶翎`（人手补）撞上 `char_009` 的 `叶翎`（模型写），
    **而那条 HARD 打的是 `[OK]`**。⚠ **那一次撞名恰好是对的**（原作对同一个人
    有两种写法），硬拦它的唯一出路是撤销一个对的决定 ⇒ **不扩 HARD 的取值面**，
    另落一条提示。

    ⚠ `--check-only` 看不到它：那条路**读产物不重算**（重算就是第二个判官），
    而盘上那份 `book[]` 是原型产的。要 `--recheck` 才会重新走一遍确定性链。
    """
    art, rep = rename.run_recheck(_ctx(box))
    collided = [f for f in art.book if f.rule == 'rn_new_collide_human']
    hard = next(r for r in rep.rules if r.rule == 'rn_new_collide')
    assert hard.status == 'ok', 'HARD 那条不许因为人工补充而红'
    if box.sid.endswith('3176'):
        assert len(collided) == 1 and '祈辕' in collided[0].detail
    else:
        assert collided == [], f'这本书不该有人工撞名：{[f.detail for f in collided]}'


def test_identity_marks_the_artifact_as_partially_rewritten(box):
    """`--identity` 写盘 ⇒ 那份产物是**混版的**，而它必须说得出来。

    ⚠ 它与 `--approve` 的区别正在这里：定稿一个由代码算出来的字段都没改。
    """
    rename.run_identity(_ctx(box))
    prov_d = box.read('stage7/name_map.json')['_provenance']
    assert prov_d.get('partial_rewrite') == '--identity'


# ── 四之二、盘上那三条只能从【归档】看见的事实 ────────────────────────────
#
# 这一族不吃沙箱，直接读语料——它们说的就是「只反推正式产物会得出什么错结论」。

def _name_maps():
    return [a for a in corpus.iter_artifacts() if a.pattern == 'stage7/name_map.json']


def test_high_confidence_residuals_only_exist_in_the_archives():
    """⭐ **只反推正式产物，会把 `high` 定成一个零样本分支。**

    3 份正式产物 19 条残留**全部是 `low`**——`high` 的那些早被人勾选、
    走 `extra_subs` 修掉了，正式产物是**修完之后的状态**。
    而 8 份归档里有 **35 条 `high`**。
    ⇒ **凡是有「人修完再落盘」这条路的层，归档必须一起反推。**
    """
    live = [r for a in _name_maps() if '_rounds' not in a.rel
            for r in (corpus.load_raw(a).get('residual') or [])]
    arch = [r for a in _name_maps() if '_rounds' in a.rel
            for r in (corpus.load_raw(a).get('residual') or [])]
    assert len(live) == 19 and {r['confidence'] for r in live} == {'low'}
    assert sum(1 for r in arch if r['confidence'] == 'high') == 35


def test_the_code_fingerprint_never_moved_while_the_prompt_took_three_values():
    """⭐ **事实⑦ 的字节级铁证：按内容算的指纹戳穿了按 git HEAD 算的那个。**

    盘上 11 份产物 `code` **全是同一个短 sha**，而同一批的 `N9_SYS` 取过 **3 个**值
    ⇒ **代码确实变过三次，而 `code` 一个字没动**（工作树脏时它照样给出一个
    看起来很精确的答案）。全链没有一层比对过它们。

    ⚠ 处置**不在本轮**：`provenance()` 在共享层，改 `git describe --always --dirty`
    一改就是八层一起改（`stage6.md` §三 已登记「处置只定一次，别按层分别裁」）。
    本条只是**把它钉在盘上**，免得下一个人以为这件事没人知道。
    """
    codes, prompts_seen = set(), set()
    for a in _name_maps():
        pv = corpus.load_raw(a).get('_provenance') or {}
        codes.add(pv.get('code'))
        prompts_seen.add((pv.get('prompts') or {}).get('N9_SYS'))
    assert len(_name_maps()) == 11
    assert len(codes) == 1, f'`code` 变过了：{codes}'
    assert len(prompts_seen) == 3, f'`N9_SYS` 取过 {len(prompts_seen)} 个值'


def test_the_book_txt_fingerprint_can_only_be_recomputed(box):
    """⭐ 盘上记的是 **10 位来源指纹**，新契约要 **40 位内容指纹**，
    **旧值推不出新值**（`corpus.py::RESHAPED`）。

    迁移填的是占位符 ⇒ **不重算，`rn_txt_stale` 会在每一本上误报红**。
    ⚠ 别把那个占位符读成实测值。
    """
    raw = next(a for a in _name_maps()
               if a.book_dir == box.book and '_rounds' not in a.rel)
    on_disk_old = (corpus.load_raw(raw).get('book_txt') or {}).get('sha1')
    assert on_disk_old and len(on_disk_old) == 10, '盘上那份不再是 10 位了？'
    migrated = corpus.load_cut(raw)['book_txt']['sha1']
    assert migrated == '0' * 40, '迁移这一格填的必须是占位符，不是一个看起来像真的值'
    # `_box` 已按盘上那份 txt 重算过——重算出来的与占位符**必然**不同
    assert box.read('stage7/name_map.json')['book_txt']['sha1'] != migrated
    assert next(r for r in _check(box).rules if r.rule == 'rn_txt_stale').status == 'ok'


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


def test_the_gate_blocks_a_dirty_map_and_revalidate_promotes_it_after_the_fix(box):
    """⭐ **落盘闸门照 stage3：任何一条 hard ⇒ `.INVALID`**（本层一次只产一份）。

    配套的 `--revalidate` 是它的**必然结果**——闸门是我们自己加的，
    不给一条 0 成本提升路，一次付费产出就被永久锁死。
    """
    from novel import paths
    from novel.errors import GateBlocked

    # ⚠ **先取出模型产出，再删产物**：付费路要求盘上没有正式稿（先校验后落盘）。
    dirty = _model_out_from_disk(box, break_it=True)
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


def test_a_clean_map_lands_and_the_gate_stays_open(box):
    """同一条路，干净的那一份**照常落盘**——闸门不许把合法产出也拦住。"""
    clean = _model_out_from_disk(box, break_it=False)
    (box.dir / 'stage7' / 'name_map.json').unlink()
    ctx = RunContext.open(str(box.root), box.sid, 'stage7.rename', paid=True)
    art = rename.run_rename(ctx, _fake_client(ctx, clean))
    assert art.book_txt is None and art.approved is None
    assert (box.dir / 'stage7' / 'name_map.json').exists()
    assert art.provenance.model == 'fake'


