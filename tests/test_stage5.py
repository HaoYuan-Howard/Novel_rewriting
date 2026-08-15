#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage5.py —— 逐章写作 + 全书验收。

⭐ **这一份里最值钱的是那 7 条 HARD 的反例样本。**
盘上 31 章对这 7 条是 **0 命中**，而「见过它通过」不是证据，「见过它红一次」才是
（LESSONS 第 3 条）。stage4 / stage6 / stage7 合计 36 条 HARD 至今 0 反例样本——
本层不再往那个数上加。

⚠ 反例配得出来，是因为**判据吃 `dict` 不吃模型**：一旦判据跑在已经过了 Pydantic 的
对象上，「必填齐全」这类判据在结构上就构造不出反例，它必然全绿。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.rules import REGISTRY
from novel.stages.stage5 import checks, measure as M, prompts
from tests import corpus

_REQUIRED = {
    'stage1/main_text.json', 'stage2/global_grounding.json',
    'stage3/style_grounding.json', 'stage4/writer_context.json',
}


def _eligible_books() -> list[str]:
    rels: dict[str, set[str]] = {}
    drafts: set[str] = set()
    for artifact in corpus.iter_artifacts():
        rels.setdefault(artifact.book_dir, set()).add(artifact.rel)
        if artifact.pattern == 'stage5/{mode}/ch{nn}.json' and '_rounds/' not in artifact.rel:
            drafts.add(artifact.book_dir)
    return sorted(book for book, seen in rels.items() if _REQUIRED <= seen and book in drafts)


_BOOKS = _eligible_books()
_BOOK = _BOOKS[0] if _BOOKS else ''
pytestmark = pytest.mark.skipif(
    not _BOOKS, reason='当前语料没有完整 Stage5 上游与正式成稿——本文件未执行')


# ─────────────────────────────────────────────────────────────────────────────
# 零、一本**真书**的 Book，与一份合成章
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def book() -> M.Book:
    """从显式配置的当前产物拼一个 `Book`。"""
    def cut(rel: str):
        a = next(x for x in corpus.iter_artifacts() if x.book_dir == _BOOK and x.rel == rel)
        return corpus.load_current(a)

    wc = cut('stage4/writer_context.json')
    return M.Book(
        title=_BOOK.rsplit('_', 1)[0],
        ctx=wc,
        style=cut('stage3/style_grounding.json'),
        source_text={s['chapter_ordinal']: s.get('text') or ''
                     for s in cut('stage1/main_text.json')},
        secrets=(cut('stage2/global_grounding.json').get('canon') or {}).get('secrets') or [],
    )


@pytest.fixture(scope='module')
def entry(book: M.Book) -> dict:
    """一章的 ctx 条目（第 1 章）。"""
    return book.chapters[min(book.chapters)]


def _fire(text: str, entry: dict, book: M.Book, *, mode: str = 'deep') -> set[str]:
    """这一份成稿触发了哪些判据。"""
    return {f.rule for f in checks.chapter_findings(text, entry, book, mode=mode, given='')}


#: 一段**通得过大多数判据**的底稿：完整句、够长、带本书的人名。
def _clean_body(book: M.Book, entry: dict) -> str:
    name = sorted(book.names)[0] if book.names else '她'
    line = f'{name}把杯子放下，没有说话。窗外的雨敲在玻璃上，一下，又一下。'
    return (line * 8)


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐⭐ 8 条 HARD，**每条一份会让它红的样本**
# ─────────────────────────────────────────────────────────────────────────────

def test_the_eight_hard_judges_are_exactly_the_ones_we_have_samples_for():
    """守卫的守卫：**本层的 HARD 判据集合改了，下面样本就要跟着改。**

    多一条 HARD 而这里没有对应的反例，就又回到了「36 条 HARD 盘上 0 反例样本」那个欠账。
    """
    hard = {r.rule for r in REGISTRY.of_stage('stage5') if r.sev == 'hard'}
    assert hard == {'draft_too_short', 'draft_truncated', 'meta_leak', 'verbatim_missing',
                    'keep_value_missing', 'secret_leak', 'semantic_secret_leak',
                    'dialogue_halved'}, (
        f'stage5 的 HARD 集合变成了 {sorted(hard)}——'
        f'每新加一条 HARD 就要有一份会让它红的样本（LESSONS 第 3 条）')


def test_draft_too_short_goes_red_and_swallows_everything_else(book, entry):
    """⭐ **< 50 内容字 ⇒ 早退，其余判据一条都不跑。**

    早退是**有意的**：后面每一条判据都建立在「这是一篇文章」之上，对 12 个字跑它们
    只会产出一堆没有意义的数。而「这不是『其余都干净』」由这条 HARD 自己承担。
    """
    fs = checks.chapter_findings('太短了。', entry, book, mode='deep', given='')
    assert [f.rule for f in fs] == ['draft_too_short'], (
        '早退没生效——短稿上跑出了别的判据，那些数全是噪声')


def test_draft_truncated_goes_red_on_a_missing_final_punctuation(book, entry):
    """⭐ 结尾没有终止标点 ⇒ 疑似截断。"""
    body = _clean_body(book, entry)
    assert 'draft_truncated' not in _fire(body, entry, book)
    assert 'draft_truncated' in _fire(body.rstrip('。') + '他忽然', entry, book)


@pytest.mark.parametrize('message', [
    '【好感度已降低。】',
    '[好感度已降低。]',
    '［好感度已降低。］',
])
def test_draft_truncated_accepts_a_closed_interface_message(book, entry, message):
    """三种界面方括号的闭合符都表示消息已经完整结束。"""
    body = _clean_body(book, entry).rstrip('。') + message
    assert 'draft_truncated' not in _fire(body, entry, book)


@pytest.mark.parametrize('shell', ['第三章 雨夜', '本章共 8 拍，先写第一拍。',
                                   '## 小标题', '正文：她把杯子放下。'])
def test_meta_leak_goes_red_on_narrative_shell(book, entry, shell):
    """⭐ 叙述外壳（章节标题 / 编号 / 任务术语）**不该进正文**。

    ⚠ 四种形态各测一次：这条判据是**词表类**（`wordlist=True`），
    而一张只被单一形态验过的词表，与一张没被验过的词表没有区别。
    """
    body = shell + '\n' + _clean_body(book, entry)
    assert 'meta_leak' in _fire(body, entry, book)


def test_verbatim_missing_goes_red_when_the_mandated_line_is_gone(book, entry):
    """⭐ 【原样写入】那一组**要求逐字**，改一个字就废。"""
    line = '这句话一个字都不能改，写手必须原样放回去。'
    e = dict(entry, beats=[{**(entry['beats'][0]), 'verbatim': [line]}])
    body = _clean_body(book, entry)
    assert 'verbatim_missing' in _fire(body, e, book)
    assert 'verbatim_missing' not in _fire(body + line, e, book)


def test_verbatim_typo_is_a_separate_conclusion_not_a_miss(book, entry):
    """⚠ **一字之差按错别字放行**，但**单独留痕**。

    原文里本来就有错字（实测「这**为**公子伤口溃烂…」），写手写成「这**位**公子…」
    ——那是**纠正**不是改写。要求它复现原文的错别字是荒谬的。
    """
    line = '这为公子的伤口已经溃烂了整整三日无人问津。'
    e = dict(entry, beats=[{**(entry['beats'][0]), 'verbatim': [line]}])
    fired = _fire(_clean_body(book, entry) + line.replace('这为', '这位'), e, book)
    assert 'verbatim_typo' in fired and 'verbatim_missing' not in fired


def test_verbatim_split_by_a_speech_tag_is_allowed_but_still_recorded(book, entry):
    """⚠ `「…，」他说，「…」` 是中文小说的**标准写法** ⇒ 放行；

    但「一个字都没动」与「切成两截后逐字出现」不是同一件事，**不能都走 `[OK]`**。
    """
    line = '「你要是真的想走，那就走吧，我不拦着你，一次都不会。」'
    e = dict(entry, beats=[{**(entry['beats'][0]), 'verbatim': [line]}])
    split = '「你要是真的想走，」他说，「那就走吧，我不拦着你，一次都不会。」'
    fired = _fire(_clean_body(book, entry) + split, e, book)
    assert 'verbatim_split' in fired and 'verbatim_missing' not in fired


def test_keep_value_missing_compares_the_value_not_the_literal(book, entry):
    """⭐⭐ **比值不比字面**：下发面「30岁」、成稿「三十岁」＝**满足**。

    ⚠ 这条是被一次**全量误报**逼出来的：原型只搜阿拉伯字面，某章因此被**整章拦下**
    ——而契约发给写手的原话是「数字必须保住，**措辞随你**」，写手做的正是它让做的事。
    """
    e = dict(entry, beats=[{**(entry['beats'][0]), 'keep_value': ['她今年30岁了。']}])
    body = _clean_body(book, entry)
    assert 'keep_value_missing' in _fire(body, e, book)
    assert 'keep_value_missing' not in _fire(body + '她今年三十岁了。', e, book), (
        '「三十岁」没被认成 30——判据退回了字面比对，那会把一次正确的改写判成丢数字')
    assert 'keep_value_missing' not in _fire(body + '她今年30岁了。', e, book)


def test_secret_leak_goes_red_and_the_evidence_is_the_drafts_own_bytes(book, entry):
    """⭐ 提前点破本章不该揭示的事。

    ⚠⚠ **凭据必须是成稿自己的字节，不是 secret 的字节**：命中的片段来自 `secret.fact`，
    把它当凭据发回给写手就是**把秘密告诉它**。
    """
    # 旧实现在 fact 侧剥「一 / 三」、成稿侧不剥，这份原样转抄
    # 会系统性假阴。反例要直接保留这两类数字词，不许退回不含数字的 happy path。
    fact = '她其实一直都知道那三笔钱是从公司账上挪出来的'
    b = M.Book(title=book.title, ctx=book.ctx, style=book.style,
               source_text=book.source_text,
               secrets=[{'secret_id': 'secret_x', 'fact': fact,
                         'character_known_by': [], 'setup_chapters': [],
                         'reader_reveal_events': [{'chapter': 9}]}])
    e = dict(entry, must_not_reveal=['secret_x'])
    body = _clean_body(book, entry) + fact + '。'
    fs = checks.chapter_findings(body, e, b, mode='deep', given='')
    leak = [f for f in fs if f.rule == 'secret_leak']
    assert leak, 'secret_leak 没红——转抄式泄底是它唯一挡得住的那一类'
    assert all(ev in body for ev in leak[0].evidence), (
        '凭据不是成稿自己的字节——把 secret 的原文发回写手就是把秘密告诉它')


def test_secret_timeline_uses_the_same_number_ruler(book):
    """揭示章原样写出带数字的 fact 时，不得误报「连揭示章都没命中」。"""
    fact = '她其实一直都知道他是三皇子'
    secret = {'secret_id': 'secret_x', 'fact': fact, 'character_known_by': [],
              'setup_chapters': [], 'reader_reveal_events': [{'chapter': 9}]}
    synthetic = M.Book(title=book.title, ctx=book.ctx, style=book.style,
                       source_text=book.source_text, secrets=[secret])
    fs = checks.book_findings(synthetic, rows=[], bodies={9: fact},
                              inv_numbers=[], inv_names=[])
    timeline = next(f for f in fs if f.rule == 'secret_timeline')
    assert '出现于 [9]' in timeline.detail
    assert '没有判别力' not in timeline.detail


def test_style_plot_scan_uses_the_same_number_ruler(book, monkeypatch):
    """风格块是第五个消费方，不得偷偷退回只对 fact 剥数字的旧尺子。"""
    fact = '她其实一直都知道他是三皇子'
    secret = {'secret_id': 'secret_x', 'fact': fact, 'character_known_by': [],
              'setup_chapters': [], 'reader_reveal_events': [{'chapter': 9}]}
    synthetic = M.Book(title=book.title, ctx=book.ctx, style=book.style,
                       source_text=book.source_text, secrets=[secret])
    monkeypatch.setattr(prompts, 'style_block', lambda *_args, **_kwargs: fact)
    fs = checks.book_findings(synthetic, rows=[], bodies={},
                              inv_numbers=[], inv_names=[])
    assert 'style_block_carries_plot' in {f.rule for f in fs}


def test_secret_unknown_says_it_could_not_check_instead_of_passing(book, entry):
    """⚠ `must_not_reveal` 点了一条 secret 而全书 secrets 里没有它
    ⇒ **根本没得查**，不是「查过、没泄漏」。"""
    e = dict(entry, must_not_reveal=['secret_不存在'])
    assert 'secret_unknown' in _fire(_clean_body(book, entry), e, book)


def test_dialogue_halved_only_fires_on_the_open_book_arm(book, entry):
    """⭐⭐ **窄 HARD 门**：只在 `shallow + 计划没净压缩 + 源章 ≥4 句` 时拦腰斩。

    ⚠ 闭卷写手手里**没有源章**，「把台词写回来」对它只能是**编一句出来**
    ——而那正是实测发生过的那次自造台词。所以闭卷那一侧只报提示级 `dialogue_fewer`。
    """
    src_ord = (entry.get('from_source_ordinals') or [1])[0]
    src = '「一。」「二。」「三。」「四。」「五。」「六。」「七。」「八。」' * 3
    b = M.Book(title=book.title, ctx=book.ctx, style=book.style,
               source_text={**book.source_text, src_ord: src}, secrets=book.secrets)
    e = dict(entry, source_chars=100, budget_chars=100)
    body = _clean_body(book, entry)          # 一句直接引语都没有
    assert 'dialogue_halved' in _fire(body, e, b, mode='shallow')
    assert 'dialogue_halved' not in _fire(body, e, b, mode='deep'), (
        '闭卷臂被拦了——「把台词写回来」对它只能是编一句出来')
    # 计划有净压缩时也不拦：N 高估了合法目标，强拦只会逼写手拿对白凑数
    assert 'dialogue_halved' not in _fire(
        body, dict(e, budget_chars=40), b, mode='shallow')


# ─────────────────────────────────────────────────────────────────────────────
# 二、⭐ 一张表，两相共用
# ─────────────────────────────────────────────────────────────────────────────

def test_the_two_phases_read_one_and_the_same_rule_table():
    """⭐ **落盘闸门与全书验收相调同一个 `chapter_findings`。**

    原型把书级那 8 条住在另一个模块的 `RULES6` 里，靠一个 `_merged_rules()` 合起来，
    而共享层得**反向 lazy-import** 才拿得到——一张表拆成两半的全部成本都在那个环上。
    """
    import inspect
    from novel.stages.stage5 import review, write
    assert 'chapter_findings' in inspect.getsource(write._judge)
    assert 'chapter_findings' in inspect.getsource(review.build_review)


def test_every_registered_rule_has_exactly_one_producer():
    """⭐ 48 条判据**一条不多一条不少**地被实现覆盖。

    ⚠ 少列一条，那一条在报告上就变 `skipped`——而 `skipped`（实现未被调用）与
    「跑了没命中」是**两种颜色**；多列一条，`builder` 会抛（那是「注册了没实现」的镜像病）。
    """
    reg = {r.rule for r in REGISTRY.of_stage('stage5')}
    mine = [*checks._FROM_CHAPTER, *checks._FROM_REVIEW, *checks._FROM_SEMANTIC]
    assert len(mine) == len(set(mine)), '同一条判据被列了两次'
    assert set(mine) == reg, (f'注册了没实现：{sorted(reg - set(mine))}；'
                              f'实现了没注册：{sorted(set(mine) - reg)}')
    assert len(reg) == 48


def test_the_unver_five_must_say_why_they_cannot_check():
    """`sev='unver'` 的五条**必须**走 `unverifiable(rule, why)`。

    `builder._status_of` 会拒收一条 `sev=unver` 却没说出「这次为什么够不着」的判据——
    **`unver` 是判据的边界，而边界要说得出在哪。**
    """
    unver = {r.rule for r in REGISTRY.of_stage('stage5') if r.sev == 'unver'}
    assert unver == set(checks._UNVER) and len(unver) == 5


def test_the_badge_can_never_be_pass_on_this_layer():
    """⭐⭐ **本层徽章结构上永远到不了 `pass`**，而那是对的。

    5 条 `unver`，其中 `must_hold_unver` **每本都产**（全书那一组按定义查不了文面）
    ⇒ 最好情况是 `amber` + 退出码 0。**与 stage4 相反**（那一层没有一条 `unver`）。
    """
    from contracts.schema.report import RuleLine, badge_of
    lines = [RuleLine(rule=r.rule, sev=r.sev, status=('unver' if r.sev == 'unver' else 'ok'),
                      item=r.item, requirement=r.requirement,
                      why='查不了' if r.sev == 'unver' else None)
             for r in REGISTRY.of_stage('stage5')]
    assert badge_of(lines) == 'amber', '一份「全部通过」的 stage5 报告也只能是 amber'


# ─────────────────────────────────────────────────────────────────────────────
# 三、两条臂 —— 差异面只有两个
# ─────────────────────────────────────────────────────────────────────────────

def test_the_two_arms_differ_in_exactly_two_things(book, entry):
    """⭐ `R-7.1`：两条臂产同一个形状，差别只有 `(system prompt, 给不给源章原文)`。

    ⚠ **「差别只有两件事」说的是【自变量】只有两个，不是「下发面只差一个块」**：
    `dialogue_block` 里那句「本章原文有 N 句直接引语」也**只发给开卷**——
    而它是 `with_source_text` 这**同一个开关**的后果，不是第三条轴：
    闭卷写手拿不到原文，给它一个**无法回查内容的 N**，只会把「对白保真」变成「凭空凑 N 句」。
    ⇒ 这条测试钉的是**自变量的个数**：`_Arm` 恰好一个字段。
    """
    import dataclasses

    from novel.stages.stage5 import write
    assert [f.name for f in dataclasses.fields(write._Arm)] == ['with_source_text'], (
        '`_Arm` 多了一个字段——两条臂的差异面就不止 `R-7.1` 说的那两件事了')
    deep = prompts.build_material(entry, book, with_source_text=False)
    shallow = prompts.build_material(entry, book, with_source_text=True)
    assert write._ARMS['deep'].with_source_text is False
    assert write._ARMS['shallow'].with_source_text is True
    assert deep.source_text == '' and shallow.source_text
    # 开卷多出来的块**只有源章原文**；其余块要么逐字相同，要么只差那一句引语数。
    extra = shallow.blocks[len(deep.blocks):]
    assert extra and all(b.startswith('── 源章原文') for b in extra)
    diff = [(a, b) for a, b in zip(deep.blocks, shallow.blocks) if a != b]
    assert len(diff) <= 1, f'两条臂的下发面在源章原文之外还差了 {len(diff)} 个块'
    if diff:
        assert diff[1 - 1][1].startswith('── 对白形态') and '句直接引语' in diff[0][1]


def test_the_closed_book_arm_never_touches_the_source_text(book, entry):
    """⚠⚠ **「闭卷」这件事由 `build_material` 保证，不是由 system prompt 里那句话保证。**"""
    deep = prompts.build_material(entry, book, with_source_text=False)
    src = M.chapter_source(entry, book.source_text)
    assert len(src) > 200, '这一章源文太短，这条断言失去判别力'
    assert src[:120] not in deep.given


def test_chapter_local_style_projection_keeps_global_measures(book, entry):
    local = {
        'source_ordinal': entry['from_source_ordinals'][0],
        'voice': {key: f'本章-{key}' for key in book.style['voice']},
        'surface_habits': ['本章局部手法'],
    }
    style = {**book.style, 'chapter_styles': [local]}
    got = prompts.style_for_sources(style, entry['from_source_ordinals'])
    assert got['surface_habits'] == ['本章局部手法']
    assert got['measurable'] == book.style['measurable']
    assert all(value.startswith('本章-') for value in got['voice'].values())


def test_chapter_local_style_never_falls_back_to_global_when_mapping_is_missing(book, entry):
    local = {
        'source_ordinal': 999,
        'voice': book.style['voice'],
        'surface_habits': ['别章手法'],
    }
    with pytest.raises(ValueError, match='拒绝回退'):
        prompts.style_for_sources(
            {**book.style, 'chapter_styles': [local]}, entry['from_source_ordinals'])


def test_deep_overlap_high_guards_the_arms_identity_not_the_prose(book, entry):
    """⚠⚠ **它守的是【臂的身份】不是文风**：越线说明「分工写反 / 开卷稿落进 deep/」。

    ⚠ **开卷不判**（`R-7.6b` → `B-03`）：开卷逐章跨度 0.9%~96.8%，
    那不是一个可以一刀切的量。
    """
    src = M.chapter_source(entry, book.source_text)
    assert 'deep_overlap_high' in _fire(src, entry, book, mode='deep')
    assert 'deep_overlap_high' not in _fire(src, entry, book, mode='shallow')


# ─────────────────────────────────────────────────────────────────────────────
# 四、下发面的三条纪律
# ─────────────────────────────────────────────────────────────────────────────

def test_the_downsend_band_is_the_very_one_the_judges_use(book, entry):
    """⭐ 发给写手的区间 == 判据用的区间。

    ⚠ LESSONS 第 9 条那个形状：**模型按 A 写、校验按 B 判，谁也不出声。**
    原型在 `DRAFT_BAND` 之外又乘了一次 ±10%，实际执行 0.81~1.21×——而两端极值
    恰好卡在旧口径内侧、各差不到 1%，**两条判据一次都没响**。
    """
    lo, hi = prompts.target_band(entry)
    assert M.target_range(entry) == (lo, hi)
    mat = prompts.build_material(entry, book, with_source_text=False)
    assert f'**{lo}~{hi} 字**' in mat.given


def test_the_secret_ids_never_reach_the_writer(book, entry):
    """⚠ `must_not_reveal` 只有裸 id、对写手**无从执行**——真正的保护是那些 secret
    **根本不下发**。把 id 发过去只会让它去猜。"""
    e = dict(entry, must_not_reveal=['secret_01', 'secret_02'])
    given = prompts.build_material(e, book, with_source_text=False).given
    assert 'must_not_reveal' not in given and 'secret_01' not in given


# ─────────────────────────────────────────────────────────────────────────────
# 五、`--wontfix` 的三条约束
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def box():
    """一份能跑 `stage5.review` 的当前契约沙箱（跑完即删）。"""
    root = Path(tempfile.mkdtemp())
    d = root / _BOOK
    for a in corpus.iter_artifacts():
        if a.book_dir != _BOOK or '_rounds' in a.rel:
            continue
        if a.rel.startswith(('stage0/', 'stage6/', 'stage7/')) or 'review' in a.rel:
            continue
        p = d / a.rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(corpus.load_current(a), ensure_ascii=False), encoding='utf-8')
    yield root, _BOOK.rsplit('_', 1)[-1]
    shutil.rmtree(root, ignore_errors=True)


def _run(*argv: str) -> int:
    from novel.cli.main import main
    return main(list(argv))


def test_the_review_runs_on_a_current_book_and_reports_all_baselines(box):
    """端到端重算当前正式成稿，并核对退出码与 HARD 结论。"""
    from novel.cli import exit_codes as X
    root, sid = box
    rc = _run('run', 'stage5.review', sid, '--base', str(root))
    rev = json.loads((root / _BOOK / 'stage5' / 'review_1.json').read_text(encoding='utf-8'))
    assert rc in (X.OK, X.CHECK_FAILED)
    assert (rc == X.CHECK_FAILED) is (rev['counts'].get('hard', 0) > 0)
    mechanical = [r for r in REGISTRY.of_stage('stage5')
                  if r.sev in ('hard', 'info') and r.check_kind == 'mechanical']
    assert len(rev['baselines']) == len(mechanical) + 1
    nch = len([c for c in rev['chapters'] if c.get('relpath')])
    for rule, baseline in rev['baselines'].items():
        if rule == '_beat_event_landing':
            continue
        hits, total = map(int, baseline.split('/'))
        assert total == nch and hits <= total, \
            f'{rule}: baseline 的单位应是命中章节数，不是 finding 条数（{baseline}）'
    # ⚠ **`name_surface_survey` 无条件产出**：原型只在命中时才产，于是
    #   「全书 0 个自造称谓」这个**读数**永远说不出来。
    assert any(f['rule'] == 'name_surface_survey' for f in rev['book'])


def test_wontfix_refuses_a_conclusion_that_does_not_exist(box):
    """⚠ **不给不存在的结论发豁免** ⇒ 显式终态（缺省是「不知道」，不许猜）。"""
    from novel.cli import exit_codes as X
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    assert _run('run', 'stage5.review', sid, '--wontfix',
                '1|draft_too_short=这条我不修', '--base', str(root)) == X.TERMINAL_STATE


def test_wontfix_needs_a_reason_and_a_third_segment_when_ambiguous(box):
    """⚠ 两条约束一起验：

      · **`why` 必填**——写不出理由的豁免，与「没看过」在盘上没有区别；
      · 同一 (章, 判据) 有多条结论时**要人写出第三段**——原型那条路把它们**一并豁免**了，
        而「换了个位置的同类问题」不是人做的决定。
    """
    from novel.cli import exit_codes as X
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    assert _run('run', 'stage5.review', sid, '--wontfix', '1|band_drift',
                '--base', str(root)) == X.TERMINAL_STATE
    rev = json.loads((root / _BOOK / 'stage5' / 'review_1.json').read_text(encoding='utf-8'))
    multi = next((c for c in rev['chapters']
                  if len([f for f in c['findings'] if f['rule'] == 'band_drift']) > 1), None)
    if multi is None:
        pytest.skip('这本书没有一章报了两条 `band_drift`——本条**没在查**')
    assert _run('run', 'stage5.review', sid, '--wontfix',
                f"{multi['output_ordinal']}|band_drift=一并豁免", '--base', str(root)) \
        == X.TERMINAL_STATE


def test_the_wontfix_key_survives_an_evidence_key_that_contains_a_pipe():
    """⭐⭐ **`evidence_key` 自己就带 `|`**，而 `--wontfix` 的键也用 `|` 分段。

    `evidence_key = '|'.join(evidence)` ⇒ 一条 `length_report` 的证据键长成 `887|880|875`。
    裸 `split('|')` 会切出 5 段而不是 3 段，于是**每一条多凭据的结论都豁免不了**，
    报错还说「键读不出来」——**那句话是假的**：键是对的，是解析器错了。
    ⇒ 限定切两刀，第三段吃掉剩下的全部（证据键在最后，这样恒正确）。
    """
    # ⚠ 解析器已收成全链一份（`novel/wontfix.py`）：stage5 与 stage6 共用它。
    from novel.wontfix import parse_spec
    got = parse_spec('1|length_report|887|880|875=这本书就这么写')
    assert got == ('1', 'length_report', '887|880|875', '这本书就这么写')
    assert parse_spec('book|book_length=预算就是这么定的')[2] is None


def test_wontfix_lands_and_never_deletes_the_conclusion(box):
    """⭐ **豁免不删结论**：它照常列在报告与界面上，只是不再进反哺、不再占「待人工」。

    「有人看过并决定不修」与「没人看过」必须分得开——那正是加这个机制的全部理由。
    """
    from novel.cli import exit_codes as X
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    rev = json.loads((root / _BOOK / 'stage5' / 'review_1.json').read_text(encoding='utf-8'))
    row = next((c for c in rev['chapters'] if c['findings']), None)
    if row is None:
        pytest.skip('当前成稿没有可豁免结论')
    f = row['findings'][0]
    spec = f"{row['output_ordinal']}|{f['rule']}|{f['evidence_key']}=这本书就这么写"
    assert _run('run', 'stage5.review', sid, '--wontfix', spec, '--base', str(root)) == X.OK
    wf = json.loads((root / _BOOK / 'stage5' / 'wontfix.json').read_text(encoding='utf-8'))
    assert list(wf) == [f"{row['output_ordinal']}|{f['rule']}|{f['evidence_key']}"]
    assert wf[list(wf)[0]]['why'] == '这本书就这么写'
    # 再跑一次验收：那条结论**照样在**，只是 `actionable=False` 且带着 `wontfix`
    _run('run', 'stage5.review', sid, '--base', str(root))
    rev2 = json.loads((root / _BOOK / 'stage5' / 'review_2.json').read_text(encoding='utf-8'))
    row2 = next(c for c in rev2['chapters'] if c['output_ordinal'] == row['output_ordinal'])
    hit = next(x for x in row2['findings'] if x['evidence_key'] == f['evidence_key']
               and x['rule'] == f['rule'])
    assert hit['wontfix'] and hit['actionable'] is False


def test_rerunning_the_review_on_one_and_the_same_draft_never_advances_the_counter(box):
    """⭐⭐ `R-8.4`：**成稿指纹没变时不许前进未消轮次计数。**

    拿同一份成稿重跑一次验收（改判据、补上游、或者手滑跑了两遍）**不是「写手又试了一次」**
    ——那时把计数 +1，等于让一次 **0 成本重算**把「停止重写」的闸门推到跟前。
    """
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    _run('run', 'stage5.review', sid, '--base', str(root))
    rev2 = json.loads((root / _BOOK / 'stage5' / 'review_2.json').read_text(encoding='utf-8'))
    fs = [f for c in rev2['chapters'] for f in c['findings']]
    if not fs:
        pytest.skip('当前成稿没有可用于收敛计数的结论')
    assert all(f['converge'] == 'same_draft' for f in fs), (
        '同一份稿重跑了一次验收，而收敛状态前进了——0 成本重算不许有副作用')
    assert all(f['unresolved_rounds'] == 0 for f in fs)


def test_recheck_is_idempotent_on_stored_drafts(box):
    """⭐ 产物里存的是**归一之后**的字节 ⇒ 再归一一遍必须**一个字节都不动**。

    ⚠ 搬错一个字符类不会报错，只会让某类输入**静默地不再归一**——而落盘闸门与验收相
    看的是同一份字节，两边会一起错成一样，**谁也不会出声**。
    """
    from novel.cli import exit_codes as X
    root, sid = box
    before = {p: p.read_bytes() for p in (root / _BOOK / 'stage5' / 'deep').glob('ch*.json')}
    assert before, '这本书没有成稿——本条**没在查**'
    rc = _run('run', 'stage5.write', sid, '--mode', 'deep', '--recheck', '--base', str(root))
    assert rc in (X.OK, X.CHECK_FAILED)
    for p, raw in before.items():
        now = json.loads(p.read_text(encoding='utf-8'))
        assert now['text'] == json.loads(raw.decode('utf-8'))['text'], (
            f'{p.name}：`--recheck` 改写了正文字节——归一不幂等了')


def test_dry_run_builds_the_same_prompt_the_paid_path_would_send(box):
    """⭐ `R-11.8`：预览与真跑**共用同一个构造入口**。

    ⚠ 否则「预览里看到的」与「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    from novel.run import RunContext
    from novel.stages.stage5 import write
    root, sid = box
    ctx = RunContext.open(root, sid, 'stage5.write', paid=False, mode='deep',
                          write_ledger=False)
    system, user, mat = write.build_prompt(ctx, '1')
    assert system == prompts.write_system('deep')
    assert user == prompts.build_user(mat)
    assert '── 本章任务 ──' in user and '篇幅方式' not in user, (
        '「篇幅方式」那一行回来了——`op` 已整族裁撤，它只会渲染出一个带窟窿的指令')
