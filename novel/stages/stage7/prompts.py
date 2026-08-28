#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 7 改名节点的现行 system prompt 与 user 下发面。

system 和 `build_material` 生成的 user 内容都是契约的一部分，必须分别由测试锁定。

⚠ **本节点没有剧透闸门，这是对的不是疏漏。** stage6 同样已改为完整故事面；
stage7 的下游只有确定性替换器和人。这里不发 `fate`/`secrets`/`arc`/正文的理由只有
「改名不需要知道结局」与噪声，**不是**剧透。**写明它，否则下一个人会来「修」一个不存在的洞。**
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from novel.core.naming import strip_distinguisher
from novel.llm.prompts import fill, with_json_shape
from novel.stages.stage7.subs import surface_hits

#: 改名模型的现行 system prompt；六条规则的证据写在规则旁边。
#: ⚠ 今天没有需要插值的枚举——但仍然走 `fill()`，理由见 `rename_system()`。
RENAME_SYS = """\
你在给一本已经重构完成的小说【换掉人物的真实姓名】，好让成品表达原创。只输出 JSON。

【总原则】只变【真名】。人物之间的关系、身份、职位、称谓体系**一个字都不许变**。
读者读完新版，人物关系图必须与旧版完全同构，只是每个人换了名字。

规则：
1. 【只改真名】只给拥有【本名/独特称呼】的角色改名。以下两类一律 renamed=false，
   并在 not_renamed_reason 里写明理由：
   · 纯角色词 / 头衔 / 官职 / 亲属称谓，且没有本名的（如 圣上、府医、婆母、采草贼、外地商人、书童）；
   · **不是人的实体**（如 系统、空间、金手指）——换掉它会破坏世界观设定。
   ⚠ 注意反面：带姓的称呼**是真名**，必须改（如 李嬷嬷、王婆、老王、许总——「李」「王」「许」
   都是原作的姓）。别因为它看起来像通用称谓就放过。
2. 【姓氏要单独交代一遍】凡是你决定改名、而原名**带姓**的角色，都要在 surname_map 里
   给出「原姓 → 新姓」，并且新名必须真的以那个新姓开头。**单独一个人也要写**。
   · 下面的【姓氏族群】是按原作算好的**同姓多人**分组：这些原姓一个都不能漏，
     **一组只挑一个新姓**，组内所有成员共用它。不要自己重新划分族群，
     也不要让同族的人姓不同的姓。
   · 不在族群表里的改名角色，只要原名带姓，同样要写进 surname_map——
     他们只是不需要跟谁协调。
3. 【保住名字之间的结构关系】原作在名字上做的文章要照做：
   · 同辈共用一个排行字的（如 X青禾 / X青兰 共「青」），新名也要共用一个排行字；
   · 名字高度相似表示是兄弟姐妹的（如 沈佐 / 沈佑 只差一字），新名也要只差一字。
   读者是靠这些看出人物关系的，丢了就等于改了关系。
4. 【时代与题材契合】新名必须贴合给定的【时代背景】（古代/古风 → 古典雅名，忌现代感与网络感；
   现代都市 → 现代名）。性别气质也要与原名一致。
5. 【内部无碰撞】新名彼此不得重名、不得近音；**不得与任何一个原作的名字相同或近音**
   （读者会串味）；也不要挑那种本身就是常用词的名字。
6. 【逐条给出每一个称呼的新形】`canonical` 已由 `new_canonical` 单独回答，**不要在 aliases 里
   重复 canonical**；aliases 只把给你的其余每一个称呼列一遍，一一对应地给出新形。三类分开处理：
   · 含真名的（本名、小名、叠字、表字、别号、姓+称谓、姓+职位）→ 跟着新名改，
     **构词方式保持不变**（叠字仍叠字，「阿X」仍「阿X」，「X哥哥」仍「X哥哥」，
     「姓X的」仍「姓X的」，「X总」仍「X总」，「X嬷嬷」仍「X嬷嬷」）；
   · **表字/别号**与本名没有字面关系，但要与新名同一个气质，自己另起一个；
   · 通用亲属称谓与敬称（姐姐、夫君、娘娘、少爷、婆母、世子、夫人、嫂嫂、太太、宝贝、
     以及排行称呼七郎/二小姐/大哥、骂人的贱妇/毒妇之类）→ **原样抄回来，new 与 old 完全相同**。
     它们是关系或排行，不是名字；数字尤其不能换。
   · canonical 是描述词、但某个 alias 含真实姓氏时（如 canonical=胖婶、alias=王婶），
     角色可设 renamed=true；new_canonical 仍填胖婶，只改王婶那条 alias。不要把描述词改造成姓。

输出 JSON：
- surname_map: {"原姓": "新姓"}，覆盖**每一个改名且原名带姓的角色**的姓
  （下面【姓氏族群】列出的原姓一个都不能漏，单人角色的姓也要写）；
- characters: [{char_id, renamed, new_canonical, not_renamed_reason, aliases:[{old,new}], reason}]
  · char_id 必须沿用给定的编号，一个不漏、不多不少；
  · renamed=false 时 new_canonical 原样填旧名、aliases 全部 old==new、必须写 not_renamed_reason；
  · reason 一句话，说明这个新名为什么贴合。"""

#: 响应形状提示。**它不是 prompt 文本**，是给 `ask_json` 的 JSON 形状。
RENAME_HINT = {
    'surname_map': {'原姓': '新姓'},
    'characters': [{'char_id': 'char_001', 'renamed': True, 'new_canonical': 'str',
                    'not_renamed_reason': 'str（renamed=false 时必填）',
                    'aliases': [{'old': 'str', 'new': 'str'}], 'reason': 'str'}],
}


def rename_system() -> str:
    """system prompt。**唯一落点。**

    今天没有需要插值的枚举——但仍然走 `fill()`：来源账拿它记指纹、`--dry-run` 拿它打印、
    将来加枚举时插值点也在这里（prompt 里的枚举必须是插值来的，手抄一份会慢慢漂）。
    """
    return with_json_shape(fill(RENAME_SYS), RENAME_HINT)


@dataclass(frozen=True)
class Material:
    """下发材料。`given` 是**真实下发面的字符串拼接**，与 prompt 同源。

    ⚠⚠ **`universe` 必须由这里产出。** 它既是「模型看见的全部称呼」（判漏报），
    又是替换器的**屏蔽面**（`sub_table` 的恒等条目）。
    下发面与屏蔽面一旦分家，「模型看见的」与「替换器认得的」就不是同一批字面了。
    """

    blocks: list[str]
    given: str
    chars: dict[str, dict]
    groups: list[dict]
    universe: list[str]


def build_material(*, title: str, registry: dict[str, dict], relations: list[dict],
                   era: str, groups: dict[int, list[str]],
                   sur: dict[str, str | None], book_text: str,
                   targets: list[str]) -> Material:
    """按现行 Stage 7 契约组装下发材料。

    **按消费方投影**：改名要知道的是「这个人叫什么、是谁、和谁一家」。
    `fate` / `secrets` / `arc` / `chapter_blueprints` / **任何正文**都不发。
    `name_distinctiveness` 也不发——它的消费方是确定性代码（残留探针的 stems），
    prompt 里从没定义过这个枚举是什么意思，发过去只是噪声。

    ⚠ 时代背景直接收 typed ``Setting.era``，不收整份 setting dict。上一版把
    ``Setting`` 用字段名 dump 成 ``era``，这里却查中文 alias ``时代背景``，于是
    真实 prompt 恒落「未知」；显式参数同时保证世界观与两份 evidence 不会误进消费面。
    """
    chars: dict[str, dict] = {}
    universe: set[str] = set()
    for cid, v in sorted((registry or {}).items()):
        raw = (v or {}).get('canonical') or ''
        canon = strip_distinguisher(raw)
        # 称呼**去重但保序**：`address[]` 里实测有重复条目（不同 by/from_chapter），
        # 直接发下去会让模型以为某个叫法更重要。
        seen: set[str] = set()
        surs: list[str] = []
        for a in ((v or {}).get('address') or []):
            s = ((a or {}).get('surface') or '').strip()
            # ⚠ canonical 有自己的 `new_canonical` 决策位，**不得再作为 alias 下发一次**。
            #   实跑某本时 registry 的 address 重复收了 canonical，模型于是同时交出
            #   `new_canonical` 与一条同名 alias，替换器只能正确地判成「一旧名两新名」。
            #   **那是接口把同一个 surface 要了两遍，不是模型需要猜哪一份才算数。**
            if s and s != canon and s not in seen:
                seen.add(s)
                surs.append(s)
        # ⚠ `universe` 收**剥掉区分词的** canonical，不收 `raw`：带区分词的形态
        #   （`书童(陆明洲)`）是 registry 为满足键唯一造出来的，**按构造在正文里出现 0 次**，
        #   拿它去查覆盖只会得到一条必然命中的假阳（实测两本各 2 条）。
        #   `raw` 的替换由 `build_subs` 自动补一条，不需要模型表态。
        universe |= set(surs) | ({canon} if canon else set())
        chars[cid] = {
            'canonical': canon,
            # 带区分词的原形也要给：它进不了正文，但**新人物表要显示它**，
            # 而且模型据此知道「这个名字是记账标签不是真名」。
            **({'canonical_raw': raw} if raw != canon else {}),
            'aliases': surs,
            'role': (v or {}).get('role'),
            'attested': canon in (book_text or ''),   # canonical 在原文里有没有据
        }
    # 每个称呼在成品里出现几次——确定性派生的事实，帮模型分辨「这是名字还是一次性绰号」。
    # ⚠⚠ 走 `surface_hits`（单次扫描的**独立命中**），**绝不 `str.count`**：
    #    虚高三倍的数字会让模型按错的轻重去分配注意力。
    hits = surface_hits(universe, targets)
    for c in chars.values():
        c['aliases'] = [{'surface': s, 'count': hits.get(s, 0)} for s in c['aliases']]

    grp: list[dict] = []
    for _i, cids in sorted(groups.items()):
        ss = sorted({sur[c] for c in cids if sur.get(c)})
        grp.append({'原姓': ss,
                    '成员': [{'char_id': c, 'canonical': chars[c]['canonical']}
                             for c in cids]})

    blocks = [
        f'《{title}》 —— 给这本书的人物换真名。',
        '── 时代背景（新名必须贴合它）──\n'
        + json.dumps(era or '（未知）', ensure_ascii=False),
        '── 人物表（attested=false 的 canonical 是内部记账标签，不是原作里的叫法）──\n'
        + json.dumps(chars, ensure_ascii=False, indent=1),
        # 关系只发**谓词边**，用来让模型理解「谁和谁是什么关系」以便挑名字的气质；
        # 族群已经算好了，模型不需要自己从这里归。
        '── 人物关系（只为理解，不要据此重新划分族群）──\n'
        + '\n'.join(f"  {chars.get(r.get('subject_id'), {}).get('canonical', r.get('subject_id'))}"
                    f" —{r.get('predicate')}→ "
                    f"{chars.get(r.get('object_id'), {}).get('canonical', r.get('object_id'))}"
                    for r in (relations or [])),
        # ⚠ 只列**至少两个人共姓**的族群（`novel/core/naming.py::family_groups`，
        #   「≥2 人才成族」是它兜住 `采`/`府`/`圣`/`老` 那类假姓的唯一一道，不许放宽）。
        # ⚠⚠ **但 `surname_map` 的口径比这张表宽**：它收全部改名角色的姓，
        #   本表只是其中必须协调一致的子集。这两句话必须同时写在下发面里——
        #   实测某本这张表**是空的**，模型仍正确地交了 4 条单人映射，
        #   而那 4 条正是 `R-10.6` 探针捞回真姓的唯一来源。
        #   **写明这一句，否则模型会把「不在表里」读成「不改」或「不用报姓」**。
        '── 姓氏族群（已按原作算好；给每一组挑一个新姓，组内共用）──\n'
        + (json.dumps(grp, ensure_ascii=False) if grp else '（本书没有两人以上共姓的族群）')
        + '\n不在这张表里的角色**照样要按规则 1 判断改不改名**，只是不需要与谁协调姓氏；'
          '**他们改名后的姓同样要写进 surname_map**。',
    ]
    return Material(blocks=blocks, given='\n'.join(blocks), chars=chars,
                    groups=grp, universe=sorted(universe))


def build_user(mat: Material) -> str:
    return '\n\n'.join(mat.blocks)
