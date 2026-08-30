#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_layering.py —— 分层靠**测试**守，不靠约定。

【为什么不能靠约定】原型立过一条「读上游只有一条路，那条路记账」的纪律，
实测被**十几处裸读绕过**。原因很简单：绕过更省事。
一条只写在文档里的分层规矩，与「只写在 prompt 里的约束」是同一个形状（判据 35）。

【它守的那条线】
    contracts.artifacts    ← 路径叶子，只 import typing
         ↑
    contracts.invariants   ← 取值域叶子。**它不知道 pydantic 的存在**
         ↑
    contracts.rules / contracts.schema.*        ← 契约层，可单独 import
         ↑
    novel.*                                     ← 运行时，单向依赖 contracts
"""
from __future__ import annotations

import ast
import os
import tomllib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py_files(rel: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, rel)):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__', 'node_modules')]
        out += [os.path.join(dirpath, f) for f in filenames if f.endswith('.py')]
    return sorted(out)


def _imports(path: str) -> set[str]:
    """这个文件 import 了哪些**顶层**包。"""
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split('.')[0])
    return mods


def _import_paths(path: str) -> set[str]:
    """保留完整模块名，用于约束同一顶层包里的精确依赖边。"""
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    return mods


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


# ───────────────────── 叶子性 ─────────────────────

def test_invariants_and_artifact_paths_are_contract_leaves():
    """两个基础契约模块只许依赖标准库；invariants 只能额外依赖路径叶子。

    ⚠ 契约层现在依赖 pydantic 了（模型住在 `contracts/schema/`），
    **而这两个文件必须留在依赖之外**：`/api/enums`、prompt、节点表与 schema 路由都从
    它们派生。路径叶子不能反向 import invariants；invariants 也不能借 `contracts` 这个
    顶层名绕去依赖 schema。
    """
    invariant_path = os.path.join(ROOT, 'contracts', 'invariants.py')
    artifact_path = os.path.join(ROOT, 'contracts', 'artifacts.py')
    invariant_imports = _import_paths(invariant_path)
    artifact_imports = _import_paths(artifact_path)
    allowed_invariants = {'__future__', 'typing', 'contracts.artifacts'}
    allowed_artifacts = {'__future__', 'typing'}
    assert invariant_imports <= allowed_invariants, (
        'contracts/invariants.py 长出了新依赖：'
        f'{sorted(invariant_imports - allowed_invariants)}')
    assert artifact_imports <= allowed_artifacts, (
        'contracts/artifacts.py 长出了新依赖：'
        f'{sorted(artifact_imports - allowed_artifacts)}')


def test_contracts_never_imports_the_runtime():
    """契约层不许反向依赖运行时。有一条反向边，`tests/` 就再也不能单独 import 契约层了。"""
    bad = [(_rel(p), sorted(_imports(p) & {'novel'})) for p in _py_files('contracts')
           if _imports(p) & {'novel'}]
    assert not bad, f'契约层 import 了运行时：{bad}'


def test_the_scan_actually_finds_something():
    """守卫自己的守卫：AST 走错目录时上面几条会**全绿**，而那正是最坏的情况。"""
    files = _py_files('contracts')
    assert len(files) >= 6, f'只扫到 {len(files)} 个契约层模块——路径多半错了'
    assert any(_imports(p) & {'pydantic'} for p in files), \
        '一个 pydantic import 都没扫到——AST 解析多半失效了'


def test_every_runtime_package_is_in_the_distribution():
    """源码树可 import 不等于 wheel 里有；pythonpath 会把漏包静默遮住。"""
    with open(os.path.join(ROOT, 'pyproject.toml'), 'rb') as fh:
        configured = set(tomllib.load(fh)['tool']['setuptools']['packages'])
    actual = {
        _rel(os.path.dirname(p)).replace('/', '.')
        for p in _py_files('novel')
        if os.path.basename(p) == '__init__.py'
    }
    assert configured >= actual, (
        f'这些 runtime 包没有进 setuptools 清单：{sorted(actual - configured)}。'
        'pytest 的 pythonpath=["."] 会让源码树测试照常通过，非 editable 安装才会炸')


# ───────────────────── 全仓只有一个 `Finding` ─────────────────────

def test_there_is_exactly_one_finding_class():
    """⚠ 原型里 `Finding` 有 **5 份拷贝、3 种字段签名**。

    「同一件事的第 N 个落点必然分家」——而 findings 是跨 stage5/6/7 传的东西，
    分家意味着三层对「一条结论长什么样」各有各的说法。
    """
    hits = []
    for rel in ('contracts', 'tests', 'tools'):
        for p in _py_files(rel):
            tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
            hits += [f'{_rel(p)}:{n.lineno}' for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == 'Finding']
    if os.path.isdir(os.path.join(ROOT, 'novel')):
        for p in _py_files('novel'):
            tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
            hits += [f'{_rel(p)}:{n.lineno}' for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == 'Finding']
    assert len(hits) == 1, f'`class Finding` 出现 {len(hits)} 次：{hits}'


# ───────────────────── 运行时的几条（`novel/` 落地后才真的生效） ─────────────────────

def _runtime_files() -> list[str]:
    files = _py_files('novel') if os.path.isdir(os.path.join(ROOT, 'novel')) else []
    if not files:
        pytest.skip('`novel/` 还没有任何模块——这几条现在**没在查**，不是「查过没事」')
    return files


#: 允许裸读 JSON 的白名单。每一条都要写得出理由。
_JSON_READ_ALLOWED = {
    'novel/store.py': '它就是那条唯一的读路，记账在这里发生',
    'novel/orchestrator/jobs.py': (
        '作业记录**不是产物**：不进来源账、不进 `_provenance`、`ARTIFACTS` 里没有它。'
        '它是 append-only 的事件流（`_service/jobs.jsonl`），'
        '读的一侧还要容忍被 kill 截断的最后一行——那条容忍不该长在产物读路上'),
    'novel/orchestrator/queue.py': (
        '持久队列记录不是产物：`_service/queues.jsonl` 是 append-only 编排事件，'
        '不进来源账/ARTIFACTS；与 jobs.jsonl 一样只容忍被 kill 截断的最后一行'),
    'novel/orchestrator/beacon.py': (
        '进度信标**不是产物**：与 jobs.jsonl 同理，append-only 事件流'
        '（`_service/agent_progress.jsonl`），不进来源账、不进 `_provenance`；'
        '它记的是「agent 代跑的哪条命令何时结束」，观察轨迹而非判定（`R-13.6`）'),
    'novel/orchestrator/agent.py': (
        'agent 代跑记录**不是产物**：与 jobs.jsonl 同一份理由，append-only 事件流'
        '（`_service/agent_runs.jsonl` + `logs/agent-*.log`），'
        '读的一侧要容忍被 kill 截断的最后一行'),
    'novel/agent_session_audit.py': (
        'commander / executor 会话审计**不是产物**：'
        '`_service/agent_sessions.jsonl` 是 append-only 事件流，'
        '只记 profile/session/usage/指纹与双轴终态，不进来源账或 ARTIFACTS；'
        '读侧只容忍被 kill 截断的最后一行，中间坏行关闭式失败'),
    'novel/operator_telemetry.py': (
        'Commander / operator 阶段遥测**不是产物、节点或成本账**：'
        '`_service/operator_phases/*.jsonl` 是不含正文/提示词/token 的逐 session append-only 事件流，'
        '只记本地阶段身份、耗时与终态；读侧只容忍被 kill 截断的最后一行'),
    'novel/human_actions.py': (
        '人工改稿身份账**不是产物**：'
        '`_service/human_actions.jsonl` 是不含正文的 prepared→committed '
        'append-only 恢复/审计事件流，不进来源账或 ARTIFACTS；'
        '读侧要区分完整坏行与被 kill 截断的尾行'),
    'novel/orchestrator/trash.py': (
        '回收站条目的 `metadata.json` **不是产物**：`ARTIFACTS` 里没有它、'
        '不进来源账、不进 `_provenance`，它只回答「这条删除完成了没有」'
        '（`sweep_incomplete` 的硬中断恢复判据）。'
        '⚠ 更要紧的是它**读的时候一定坏得起**——半成品条目正是进程被硬中断留下的，'
        '写到一半的 JSON 必须当成「没有 metadata」继续往下判，'
        '而产物读路的纪律恰恰相反：坏 JSON 一律关闭式失败'),
    'novel/llm/stage2_runs.py': (
        'Stage2 验证后章节检查点与一次性 repair workpack 不是正式产物或普通缓存；'
        '它们跟书走、按 run_id 关闭式恢复，读取后仍须重跑当前 schema/validator/scope/HARD。'
        '单独成模块正是为了不给 extract.py/client.py 开裸 IO 豁免'),
    'novel/llm/stage6_runs.py': (
        'Stage6 双判官检查点与 prepared→committed 提交账不是正式产物；'
        '它们跟书走、按 run_id/role 关闭式恢复，读取后仍须复验'
        '当前 schema/validator/input/profile/session/ledger/audit，正式写盘仍回 semantic producer'),
    'novel/llm/agent_runs.py': (
        '逐节点 executor 的通用 run/checkpoint/commit journal 不是正式产物；'
        '它只读书内 `_stage*_runs/<run_id>/` 的不可变恢复账，并在恢复时重新闭合'
        'profile/prompt/schema/code/ledger/session audit。正式 artifact 仍只能由 stage producer '
        '经 ArtifactStore 读写'),
}


def test_only_the_store_reads_json_directly():
    """读上游**只有一条路**，而那条路记账（`Q-4`）。

    ⚠ 原型有十几处裸读绕过它，绕过就记不上账——而**绕过不会报错**，
    它只是让那条上游边从来源账里消失。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if rel in _JSON_READ_ALLOWED or rel.startswith('novel/llm/'):
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ('load', 'loads')
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == 'json'):
                bad.append(f'{rel}:{n.lineno}')
    assert not bad, (
        f'这些地方绕过了唯一的读路：{bad}\n'
        f'→ 要么改走 store，要么把它连同理由加进 `_JSON_READ_ALLOWED`'
    )


#: 允许**往盘上写**的白名单。每一条都要写得出理由。
#:
#: ⚠ 这条守卫与上面那条读的守卫是一对：「读只有一条路」保证 `inputs` 不漏，
#: 「写只有一条路」保证**没有一份产物能绕开写盘口那两道闸**
#: （`prompts` 非空而 `inputs` 为空 ⇒ 拒 · 落盘前先证明读得回来）。
#: 原型没有写口守卫，于是落盘闸门的失败支用**裸 `json.dump`** 写 `.INVALID`
#: ——那份稿从此没有来源账，而提升路又「沿用原稿里那一份」。盘上留下 1 份铁证。
_WRITE_ALLOWED = {
    'novel/store.py': '它就是那条唯一的写路，两道闸在这里',
    'novel/appendlog.py': (
        'append-only 事件流的**唯一追加口**：五份 `_service/*.jsonl`（账本、作业、队列、'
        '进度信标、agent 代跑）都从这里落字节。它们不是产物——不进来源账、不进 '
        '`_provenance`、`ARTIFACTS` 里没有它们。⚠ 它同时是那把**跨进程**锁的唯一实现：'
        '`R-13.7` 允许不同小说并行，而 `mark` / CLI `queue` 都是独立进程 ⇒ '
        '只有线程锁时半截行会被顶到文件中间，而读侧对中间坏行是 `raise`。'
        '锁文件 `<目标>.lock` 也在这里创建'),
    'novel/human_actions.py': (
        '人工改稿两相账本身仍只经 appendlog 追加；本模块唯一的裸 IO 是把确证未换行的'
        '截断尾先原子隔离到 `_service/human_actions_tail_recovery/`，随后在同一账锁内'
        '截回完整前缀。隔离件不含正文、以完整 receipt 内容寻址，属于 kill 恢复审计，'
        '不是小说产物，也不会被普通读视图当作 committed 动作'),
    'novel/llm/cache.py': (
        '响应缓存**不是产物**：不进来源账、不进 `_provenance`、`.gitignore` 盖着。'
        '它还是全仓唯一一处**会主动删文件**的地方（`evict`），而那与判据 7'
        '「修不了的原样留着」不冲突——产物留着是给人看的，一份坏缓存留着'
        '只会被下一次重跑静默地当成新结果'),
    'novel/llm/keepsake.py': (
        '「答案流丢失」（正文空 + 非截断，答案写进了 reasoning）那一份的留证：'
        '整个模块只有一个函数、只做一件事——落字节。它**不是产物**（不进 ARTIFACTS / '
        '来源账 / `_provenance`，`_` 前缀目录，完成度与阶段视图都不看它），'
        '也**不是缓存**（缓存会被读回来当答案用，所以 `ResponseCache.put` 拒写空正文'
        '是对的；留证只给人看，救场仍禁止从 reasoning 里抠答案）。'
        '⚠ 它单独成一个模块正是**为了**这张表按文件豁免：把 `llm/client.py` 整个放进来'
        '等于给全仓最长、改动最频繁的模块开永久豁免，它下次真绕开 store 时这条守卫就哑了'),
    'novel/llm/response_repair.py': (
        '结构化拒绝响应的书内隔离区与不可变受托修复决定。它不是缓存或正式产物；'
        '只允许写 `_llm_rejected/`，真正采纳仍回 LlmClient 的同一套内容闸。'
        '单独成模块是为了不把 `client.py` 整体加入写盘白名单'),
    'novel/llm/stage2_runs.py': (
        'Stage2 验证后章节检查点与一次性 batch resolution。它不是正式 artifact 或普通缓存，'
        '只写书内 `_stage2_runs/<run_id>/blueprint/`，正式采纳仍回 producer 的完整编译/HARD/写盘口。'
        '单独成模块是为了不把 extract.py/client.py 整体加入写盘白名单'),
    'novel/llm/stage6_runs.py': (
        'Stage6 双判官验证后 checkpoint 与 commit journal，只写书内 '
        '`_stage6_runs/<run_id>/semantic/`；它们不是正式 artifact，'
        '正式 semantic 产物、typed readback、双 adoption 与终验仍由 producer 完成。'
        '独立模块让恢复账的裸 IO 豁免不扩散到 semantic.py'),
    'novel/llm/agent_runs.py': (
        '逐节点 executor 的 provider-neutral 恢复核心只原子写书内 '
        '`_stage*_runs/<run_id>/run.json`、append-only checkpoint revision 与 commit journal；'
        '这些 sidecar 不是正式 artifact，候选仍须回到对应 stage producer 的 schema/HARD/'
        'ArtifactStore/readback/adoption 终验后才能 delivered'),
    'novel/orchestrator/jobs.py': (
        '作业记录与子进程日志。⚠ **`R-11.3` 已于 2026-08-11（骨架 D）'
        '收窄成「绝不落**产物**盘」**，理由是一句话：'
        '「子进程被 OOM 杀掉了」这条记录**只有父进程写得出来**——'
        '子进程写不了自己的死讯，而退出码正是编排层判成败的依据（`R-12.3`）。'
        '它拥有的两处记录（`_service/jobs.jsonl` · `_service/logs/`）都不是产物，'
        '且都是 append-only：被 kill 最坏只损坏最后一行。'
        '⚠ 直写盘的只剩子进程日志——`jobs.jsonl` 已改走 `novel/appendlog.py`'),
    # ⚠ `queue.py` / `beacon.py` / `llm/ledger.py` **刻意不在这张表里**：
    #   它们的追加已经收到 `novel/appendlog.py` 那一个落点，自己不再碰盘。
    #   把它们留在白名单里等于永久豁免——那三个模块下一次真的直写盘时，
    #   这条守卫就不会出声了。
    'novel/orchestrator/uploads.py': (
        '浏览器原始上传与一次性 stage0 编辑计划，均只写 Output/_service/。'
        '前者要长期保留供 S-00 按 original_path 审校；后者只把人的选择交给 CLI '
        '子进程，作业结束即删。两者都不进 ARTIFACTS / 来源账，不是小说产物'),
    'novel/orchestrator/approvals.py': (
        '人工闸门与深度选章只写 Output/_service/approvals/。它们控制作业是否可启动，'
        '不是小说产物，不进 ARTIFACTS / provenance；原子替换防止并发读到半份状态'),
    'novel/orchestrator/trash.py': (
        '项目删除的 CLI 子进程把整本书目录与审批状态原子移入 Output/_service/trash/。'
        '它不改写任何产物字节；回收站保留原目录、元数据与恢复路径，父服务进程只负责互斥'
        '和拉起该子进程'),
    'novel/orchestrator/agent.py': (
        'agent 代跑的**子进程日志** `Output/_service/logs/agent-*.log` 按字节追加；'
        '它与 jobs.py 写 _service 同一份理由（「子进程被杀」这条记录只有父进程写得出来），'
        '不是小说产物。⚠ `agent_runs.jsonl` 那一份已改走 `novel/appendlog.py`'),
}


def test_only_the_store_writes_to_disk():
    """**没有模块级 `save_json`**：写盘只经 store 实例，而 store 只由 `RunContext` 造。

    ⚠ **`store.write_text(...)` 不算绕过，它就是那条路。** 本条按方法名扫，
    而 `ArtifactStore` 的字节形态写口恰好也叫 `write_text`（那个名字是对的：
    它与 `write` 是同一族）。⇒ 收一条**很窄**的例外：接收者必须字面是 `store`。
    第一个撞上它的是 F2 的 `stage7/assemble.py`（`book.txt` 是本仓第一份
    真的由代码写出来的字节形态产物）。
    ⚠ 窄到什么程度是有讲究的：只认名字 `store`，不认任意表达式——
    放宽成「任何看起来像 store 的东西」，这条守卫就再也拦不住一个叫 `store` 的 `Path`。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if rel in _WRITE_ALLOWED:
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ('write_bytes', 'write_text', 'mkdir', 'unlink'):
                recv = n.func.value
                if isinstance(recv, ast.Name) and recv.id == 'store':
                    continue
                bad.append(f'{rel}:{n.lineno} .{n.func.attr}()')
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'open':
                mode = next((a.value for a in n.args[1:2] if isinstance(a, ast.Constant)), 'r')
                if any(c in str(mode) for c in 'wax+'):
                    bad.append(f'{rel}:{n.lineno} open(mode={mode!r})')
    assert not bad, (
        f'这些地方绕过了唯一的写路：{bad}\n'
        f'→ 要么改走 store，要么把它连同理由加进 `_WRITE_ALLOWED`'
    )


# ───────────────────── 编排层（骨架 D）─────────────────────

def _orchestrator_files() -> list[str]:
    files = [p for p in _runtime_files() if _rel(p).startswith('novel/orchestrator/')]
    assert len(files) >= 2, f'只扫到 {len(files)} 个编排层模块——路径多半错了'
    return files


def test_the_orchestrator_never_imports_the_web_stack():
    """编排层不许 import `fastapi`。**现在就钉住，E 期才不会顺手在这里开路由。**

    原型的这条纪律写在 `Web/paths.py` 的 docstring 里（「只有 app.py 能 import fastapi」），
    靠人守；靠人守的分层实测是守不住的（同一份 docstring 里那条
    「Web 进程只读不写」就已经被 `jobs.py` 自己破了）。
    """
    bad = [(_rel(p), sorted(_imports(p) & _WEB_STACK))
           for p in _orchestrator_files() if _imports(p) & _WEB_STACK]
    assert not bad, f'编排层 import 了 HTTP 栈：{bad}——那一层在 E 期，不在这里'


# ───────────────────────── HTTP 层（骨架 E5）─────────────────────────

_WEB_STACK = {'fastapi', 'starlette', 'uvicorn'}

#: 允许 import web 栈的文件。**每一条都要写得出理由**（形态同 `_WRITE_ALLOWED`）。
_WEB_STACK_ALLOWED = {
    'novel/api/app.py': '服务本体：路由挂载、CSRF 中间件、异常归一，以及把 `web/dist` '
                        '静态挂在同一个端口上（只读构建产物，不碰产物盘）。这一层就是它',
    'novel/api/__main__.py': '起进程（`uvicorn.run`）。**绑定地址也只在这里出现一次**',
    'novel/api/deps.py': '路由共用的两道边界校验，要拿 `Request` 与 `HTTPException`',
    'novel/api/routes/': 'HTTP 端点。`APIRouter` 是它们的定义方式',
}


def _api_files() -> list[str]:
    files = [p for p in _runtime_files() if _rel(p).startswith('novel/api/')]
    assert len(files) >= 4, f'只扫到 {len(files)} 个 API 层模块——路径多半错了'
    return files


def _view_files() -> list[str]:
    files = [p for p in _runtime_files() if _rel(p).startswith('novel/views/')]
    assert len(files) >= 2, f'只扫到 {len(files)} 个视图层模块——路径多半错了'
    return files


def test_only_the_api_layer_imports_the_web_stack():
    """web 栈只许出现在白名单里那几个文件。

    ⚠ 一条 `import fastapi` 出现在别处，代价不是风格问题：那一层从此**没装 fastapi
    就 import 不进来**，于是它的测试要么装整个 web 栈、要么退化成 `importorskip`
    ——而 `importorskip` 跳过去的东西看起来像绿的。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if any(rel.startswith(k) for k in _WEB_STACK_ALLOWED):
            continue
        if _imports(p) & _WEB_STACK:
            bad.append((rel, sorted(_imports(p) & _WEB_STACK)))
    assert not bad, (
        f'这些地方 import 了 HTTP 栈：{bad}\n'
        f'→ 要么挪进 `novel/api/`，要么把它连同理由加进 `_WEB_STACK_ALLOWED`')
    # 守卫的守卫：一个 fastapi import 都没扫到 ⇒ AST 走错目录，上面那条会静静地绿。
    assert any(_imports(p) & {'fastapi'} for p in _api_files())


def test_the_orchestrator_can_never_be_whitelisted_for_the_web_stack():
    """⚠ 上面那张白名单**是可以被顺手加一行的**，而编排层进不进它是一次**结构决定**。

    `R-11.3`：一切写操作走子进程，而编排层正是那条缝所在的地方。
    它一旦够得到 HTTP 栈，「服务进程只读」与「节点跑在子进程里」这两条
    就没有任何结构上的东西挡着了。
    """
    bad = sorted(k for k in _WEB_STACK_ALLOWED if k.startswith('novel/orchestrator/'))
    assert not bad, f'编排层被加进了 web 栈白名单：{bad}——那不该是一行 diff 的事'


def test_contracts_never_imports_the_web_stack():
    """契约层不许背上 web 栈（`contracts/schema/api.py` 落地之后才有意义）。

    ⚠ 理由是具体的：离线产前端 TS 类型的脚本要 import 契约层，
    而它**一行 HTTP 都不发**——不该为此装整个 web 栈。
    """
    bad = [(_rel(p), sorted(_imports(p) & _WEB_STACK)) for p in _py_files('contracts')
           if _imports(p) & _WEB_STACK]
    assert not bad, f'契约层 import 了 HTTP 栈：{bad}'
    # 守卫的守卫：`api.py` 得真的在那儿被扫到。
    assert any(_rel(p).endswith('schema/api.py') for p in _py_files('contracts'))


#: 经 store / 事件流写盘的那些方法名。
#:
#: ⚠ 裸写盘（`write_bytes` / `mkdir` / `open(mode='w')`…）已由
#: `test_only_the_store_writes_to_disk` 守着——API 与视图层不在它的白名单里。
#: 这里补的是**另一半**：调白名单模块里的写方法。那一半绕不过上面那条守卫，
#: 因为它调的正是被允许写的那几个模块。
_STORE_WRITERS = {'write', 'write_text', 'write_invalid',
                  'promote_invalid', 'archive_round', 'archive_bytes', 'append_event'}


def _writer_hits(path: str) -> list[str]:
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    return [f'{_rel(path)}:{n.lineno} .{n.func.attr}()' for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _STORE_WRITERS]


def test_the_api_and_view_layers_never_write_artifacts_directly():
    """⭐ `R-11.3`：**服务进程绝不直接落产物盘。**

    变更路由只经 `jobs.run` / `jobs.start` 起 CLI 子进程；上传、一次性编辑计划与审批
    只进 `_service/`。这些窄入口由下一条单独钉住，本条继续禁止绕过 store 的产物写。
    """
    bad = [h for p in _api_files() + _view_files() for h in _writer_hits(p)]
    assert not bad, f'API / 视图层往盘上写了东西（`R-11.3`）：{bad}'
    # 守卫的守卫（**探测器自测**）：同一个探测器在 `store.py` 上必须抓得到东西，
    # 否则「一条都没抓到」既可能是干净、也可能是探测器坏了，而两者长得一样。
    assert _writer_hits(os.path.join(ROOT, 'novel', 'store.py')), '探测器在 store.py 上一条都没抓到'


_SERVICE_MUTATORS = {
    ('jobs', 'run'),
    ('jobs', 'start'),
    ('uploads', 'save_upload'),
    ('uploads', 'save_edit_plan'),
    ('uploads', 'save_draft_edit'),
    ('uploads', 'discard_edit_plan'),
    ('approvals', 'approve_gate'),
    ('approvals', 'mark_seen'),
    ('approvals', 'begin_writing'),
    ('approvals', 'reset'),
    ('approvals', 'backfill_from_disk'),
    ('trash', 'run'),
}


def _service_mutator_hits(path: str) -> list[tuple[str, str]]:
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    module_aliases: dict[str, str] = {}
    direct_aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'novel.orchestrator':
            for alias in node.names:
                if alias.name in ('jobs', 'uploads', 'approvals', 'trash'):
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in (
            'novel.orchestrator.jobs',
            'novel.orchestrator.uploads',
            'novel.orchestrator.approvals',
            'novel.orchestrator.trash',
        ):
            module = node.module.rsplit('.', 1)[-1]
            for alias in node.names:
                pair = (module, alias.name)
                if pair in _SERVICE_MUTATORS:
                    direct_aliases[alias.asname or alias.name] = pair

    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            pair = (module_aliases.get(node.func.value.id, ''), node.func.attr)
            if pair in _SERVICE_MUTATORS:
                out.append(pair)
        elif isinstance(node.func, ast.Name) and node.func.id in direct_aliases:
            out.append(direct_aliases[node.func.id])
    return out


def test_service_mutations_are_narrowly_owned_by_mutation_routes():
    """API 只准备 `_service` 输入并起作业/回收子进程；视图层保持纯读。新增写入口必须显式改表。"""
    hits = {
        _rel(path): _service_mutator_hits(path)
        for path in _api_files() + _view_files()
        if _service_mutator_hits(path)
    }
    expected = {
        'novel/api/routes/ingest.py': {
            ('jobs', 'run'), ('uploads', 'save_upload'),
            ('uploads', 'save_edit_plan'), ('uploads', 'discard_edit_plan'),
            ('approvals', 'approve_gate'),
        },
        'novel/api/routes/jobs.py': {
            ('jobs', 'start'),
        },
        'novel/api/routes/approvals.py': {
            ('approvals', 'approve_gate'), ('approvals', 'mark_seen'),
            ('approvals', 'begin_writing'),
            ('approvals', 'reset'), ('approvals', 'backfill_from_disk'),
        },
        'novel/api/routes/edits.py': {
            ('jobs', 'run'), ('uploads', 'save_draft_edit'),
            ('uploads', 'discard_edit_plan'), ('approvals', 'approve_gate'),
        },
        'novel/api/routes/maintenance.py': {('trash', 'run')},
    }
    assert set(hits) == set(expected)
    assert {path: set(values) for path, values in hits.items()} == expected


def test_the_service_process_cannot_spend_money():
    """⭐ `R-11.3` 的孪生条：**一切花钱的操作走子进程** ⇒ 服务层结构上发不出请求。

    ⚠ 这也是 `novel/api/errors.py` 里 `UNREACHABLE_OVER_HTTP` 那张表成立的前提：
    LLM 那一族异常越不过 HTTP 边界，因为它们只在子进程里抛。
    ⚠ 查的是**直接** import；传递闭包会把共享的运行时工具一并算进来，无法准确表达
    “服务能否发起付费请求”。**主保证是调用面**——没有一条端点会构造 `LlmClient`。
    """
    bad = []
    for p in _api_files() + _view_files():
        leaked = sorted(m for m in _full_imports(p) if m.startswith('novel.llm'))
        if leaked:
            bad.append((_rel(p), leaked))
    assert not bad, f'服务层直接 import 了 LLM 层：{bad}'


def test_the_view_layer_cannot_reach_the_judgement_layer():
    """⭐ `R-11.4` 的第二道：视图层够不到判官，于是**顺手 `build()` 一份报告**做不到。

    ⚠⚠ **说清这道守卫的边界，免得读的人高估它**：本条只查**直接** import，
    因为共享运行时工具可能传递地接触报告层。`R-11.4` 的另一道保证是 API 类型中
    不为普通视图提供 `badge` / `passed` / `counts`（由 schema API 回归钉住）。
    """
    judge = ('novel.report', 'contracts.judges')
    bad = [(_rel(p), sorted(m for m in _full_imports(p) if m.startswith(judge)))
           for p in _view_files() + _api_files()
           if any(m.startswith(judge) for m in _full_imports(p))]
    assert not bad, (
        f'服务层直接 import 了判定层：{bad}\n'
        f'→ **转发**一份别人算好的报告与**造**一份报告是两件事'
        f'（`novel/api/routes/checks.py` 只做前者）')
    # 守卫的守卫：扫错目录时上面那条会静静地绿。
    assert any('contracts.schema.api' in _full_imports(p) for p in _view_files())


def test_the_api_layer_has_no_second_argv_builder():
    """`R-11.8`：**展示与执行共用同一个构造函数** ⇒ API 层不许自己拼一条命令。

    原型那条裂缝是具体的：前端用 `argv.slice(4)` 硬编码切掉解释器前缀来渲染
    （`Web/static/app.js:291`），`build_argv` 的前缀长度一变它就**静默错位**。
    ⇒ 这几个字面量出现在 API 层，就说明有人在这里重拼 argv。
    """
    marks = ("'novel.cli'", '"novel.cli"', "'-X'", 'sys.executable')
    bad = []
    for p in _api_files():
        src = open(p, encoding='utf-8').read()
        tree = ast.parse(src, filename=p)
        code = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in _docstring_ids(tree)}
        if {'novel.cli', '-X'} & code or 'sys.executable' in {
                f'{n.value.id}.{n.attr}' for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}:
            bad.append(_rel(p))
    assert not bad, f'这些地方像是在自己拼 argv：{bad}（应当只调 `build_steps`）。标记={marks}'


def test_the_api_package_init_imports_nothing():
    """⭐ `novel/api/__init__.py` **必须是空的**，那是一条设计约束不是懒。

    它一旦 re-export 了 `app`，`import novel.api.enums` 就会连带把 fastapi 拖进来
    ——于是 E 期那几批本来跑得动的测试（enums / errors）全部退化成 `importorskip`，
    **而 `importorskip` 跳过去的东西看起来像绿的**。
    """
    path = os.path.join(ROOT, 'novel', 'api', '__init__.py')
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, (ast.Import, ast.ImportFrom))
           and not (isinstance(n, ast.ImportFrom) and n.module == '__future__')]
    assert not bad, f'`novel/api/__init__.py` 有 import（第 {bad} 行）'


def _docstring_ids(tree: ast.AST) -> set[int]:
    """docstring 里那些字符串常量的 id。**扫代码不扫文档**——

    这条纪律本身就要写在 docstring 里说清楚，拿字面 `in src` 去查，
    第一个被判红的是解释这条纪律的那段话。
    （从 `test_completeness_never_peeks_at_the_job_records` 提出来共用。）
    """
    return {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef,
                              ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)}


def _full_imports(path: str) -> set[str]:
    """完整模块名（`novel.report.builder`），不是顶层包名。

    ⚠ `_imports()` 只取顶层（`novel`），那对分层够用、对**包内**分层不够：
    `from novel.report import build` 与 `from novel.store import ArtifactStore`
    在它眼里长得一模一样。
    """
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
            mods |= {f'{node.module}.{a.name}' for a in node.names}
    return mods


def test_the_queue_cannot_reach_the_judgement_layer():
    """⭐ `R-13.6`（→ `R-11.4`）：**队列本身不产生新的判定。**

    造第二个判官就是造第二个落点，而这一条**由结构保证**：
    `queue.py` 够不到 `novel.report`，于是它连「顺手在这里判一下」都做不到。
    ⚠ 队列该读的是**完成度**（盘上有什么），不是**判定**（那份东西合不合格）。
    """
    path = os.path.join(ROOT, 'novel', 'orchestrator', 'queue.py')
    bad = sorted(m for m in _full_imports(path) if m.startswith('novel.report'))
    assert not bad, (
        f'`queue.py` import 了判定层：{bad}\n'
        f'→ 队列只决定顺序，不产生判定（`R-13.6`）。要用报告就让节点自己去用'
    )
    # 守卫的守卫：扫错文件时上面那条会静静地绿。
    assert 'novel.orchestrator.state' in _full_imports(path)


def test_completeness_never_peeks_at_the_job_records():
    """⭐ `R-14.1`：**完成度按产物存在性 + 来源新鲜度判定，不靠进度文件。**

    ⚠ 作业记录（`Output/_service/`）恰恰就是一份进度文件。它回答「跑过什么命令」，
    完成度回答「盘上有什么」——**两者不是同一个问题**：
    一个作业退出码 0 也可能留下残缺产物（F-087），一份产物存在也可能是上一版代码产的。
    ⇒ `state.py` 里出现 `_service` 三个字，这条纪律就已经开始漏了。
    """
    path = os.path.join(ROOT, 'novel', 'orchestrator', 'state.py')
    tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
    #: ⚠ 扫**代码**不扫文档：这条纪律本身就要写在 docstring 里说清楚，
    #: 拿字面 `in src` 去查，第一个被判红的是解释这条纪律的那段话。
    #: （这段逻辑已提成 `_docstring_ids`，E 期那条禁 `0.0.0.0` 的守卫共用它。）
    docstrings = _docstring_ids(tree)
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and '_service' in n.value and id(n) not in docstrings]
    hits += [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Name) and '_service' in n.id]
    hits += [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and '_service' in n.attr]
    assert not hits, (
        f'`state.py` 的代码里够到了 `_service`（第 {sorted(hits)} 行）——'
        f'完成度不许偷看作业记录（`R-14.1`）'
    )
    # 守卫的守卫：它该是经 store 读盘的。扫错文件时上面那条会静静地绿。
    assert 'ArtifactStore' in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


#: 允许拿 `sys.exit` 当终态的白名单。**只有【进程入口】那一层**，每条写得出理由。
#:
#: ⚠ 判准不是「哪个包」，是「这个文件是不是一个进程的最外层」：
#: 到了那一层，异常已经没有别的调用方可以接住，把它翻成退出码是**唯一**的出路，
#: 而退出码正是编排层判成败的依据（`R-12.3`）。
_SYS_EXIT_ALLOWED = {
    'novel/cli/': '节点跑法的进程入口。**全仓唯一把异常翻成退出码的地方**'
                  '（`novel/cli/exit_codes.py` 是那五个数的唯一落点）',
    'novel/api/__main__.py': '服务进程的入口（骨架 E5 加）。`--base` 敲错时要以非零退出——'
                             '不这样的话，一个起不来的服务会以退出码 0 结束，'
                             '而「起来了但没有书」与「参数敲错了」在那一刻长得一样。'
                             '⚠ 它**只在 `if __name__ == "__main__"` 那一行**，'
                             '`main()` 自己返回 int，所以它仍然可以被 import 进来测',
}


def test_the_runtime_never_uses_sys_exit():
    """显式终态用异常，不用 `sys.exit`。

    ⚠ `SystemExit` 是 `BaseException`，它**穿透 `except Exception`**——
    原型正因如此才不得不把每个节点跑成子进程。只有**进程入口**那一层
    可以把异常翻译成退出码。
    """
    bad = []
    for p in _runtime_files():
        rel = _rel(p)
        if any(rel.startswith(k) for k in _SYS_EXIT_ALLOWED):
            continue
        tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == 'exit'
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == 'sys'):
                bad.append(f'{rel}:{n.lineno}')
    assert not bad, f'这些地方拿 sys.exit 当终态：{bad}'


# ───────────────────── CLI 与 stage 模块之间的 API 漂移 ─────────────────────
#
# ⚠⚠ 这一节补的是一个**真事故**留下的洞。
# `325424e` 重写 `novel/stages/stage6/semantic.py`（`build_prompts` → `build_prompt`，
# 并删掉 `ALIGN_SYSTEM`），却没改 `novel/cli/main.py:379` 那段调用。
# 于是 `stage6.semantic --dry-run` 在那个提交上直接 `AttributeError` → exit 70，
# 而**整套测试仍然全绿**：那一层的测试直接 `import` 模块函数，从不经过 CLI 分派。
#
# ⚠ 为什么光靠 `tests/test_cli.py` 的 CLI 跑法不够：那些用例要真语料，
# 而语料一旦整批过期（改判据表就会），`build_prompt` 里的新鲜度闸门会在
# 函数体第一行就抛终态 —— 于是「返回几个值」这一半**根本没被执行到**，
# 扫描面靠「语料碰巧是新鲜的」维持（LESSONS 第 4 条那个形状）。
# ⇒ 这一条改用 AST + `hasattr`：**不要语料、不要产物、不要密钥**，
#   只回答「CLI 点名的每个符号今天还在不在」。

_CLI_MAIN = os.path.join(ROOT, 'novel', 'cli', 'main.py')


def _stage_aliases(scope: ast.AST) -> dict[str, str]:
    """这段作用域里 `from novel.stages.X import a, b` 建立的 别名 → 模块路径。

    ⚠ **必须按函数分**：`semantic` 这个名字在 `_run_stage5` 里指 stage5 那份，
    在 `_run_stage6` 里指 stage6 那份。全文件合成一张表会把两者混成一个。
    """
    out: dict[str, str] = {}
    for n in ast.walk(scope):
        if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('novel.stages'):
            for al in n.names:
                out[al.asname or al.name] = f'{n.module}.{al.name}'
    return out


def _cli_stage_references() -> list[tuple[str, str, int, str]]:
    """CLI 里所有 `<stage模块别名>.<名字>` 的引用，去重后按 (模块, 名字) 排序。"""
    tree = ast.parse(open(_CLI_MAIN, encoding='utf-8').read(), filename=_CLI_MAIN)
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, int, str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        aliases = _stage_aliases(fn)
        if not aliases:
            continue
        for n in ast.walk(fn):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id in aliases):
                key = (aliases[n.value.id], n.attr)
                if key not in seen:
                    seen.add(key)
                    out.append((key[0], key[1], n.lineno, fn.name))
    return sorted(out)


def test_every_symbol_the_cli_names_still_exists_in_that_stage_module():
    """⭐ **改了 stage 模块的函数名/常量名而没改 CLI，这条当场红。**

    CLI 是唯一一层用「模块.属性」跨层点名的地方（stage 之间走的是 import 的符号，
    解释器会当场报 ImportError；而 `semantic.build_prompts` 这种写法要等**运行到那一行**
    才发现名字没了）。⇒ 把「运行到才发现」提前成「导入就发现」。
    """
    import importlib

    missing = []
    for mod_path, attr, lineno, fn in _cli_stage_references():
        try:
            mod = importlib.import_module(mod_path)
        except Exception as e:                       # pragma: no cover - 真坏了才走到
            missing.append(f'novel/cli/main.py:{lineno} 的 {mod_path} import 不了：{e}')
            continue
        if not hasattr(mod, attr):
            missing.append(
                f'novel/cli/main.py:{lineno}（{fn}）点名 `{mod_path}.{attr}`，'
                f'而那个模块上**没有这个名字**')
    assert not missing, (
        'CLI 与 stage 模块的 API 漂移了：\n  ' + '\n  '.join(missing)
        + '\n→ 改 stage 模块的对外名字时，`novel/cli/main.py` 的对应分支要一起改。')


def test_the_cli_drift_scan_actually_finds_something():
    """守卫的守卫：扫不到东西时，上面那条会**静静地绿**。

    ⚠ 下界是实测值。CLI 少接一层、或者有人把 `from novel.stages.X import y` 改成
    别的写法（例如 `importlib.import_module`），这个数就会掉下来——
    而那正是上面那条**失去扫描面**的样子。
    """
    refs = _cli_stage_references()
    assert len(refs) >= 66, (
        f'只扫到 {len(refs)} 个「CLI 点名 stage 模块符号」的配对（曾实测 66）——'
        f'`_stage_aliases` 多半没认出 CLI 现在的 import 写法')
    fns = {fn for _, _, _, fn in refs}
    assert len(fns) >= 8, f'只覆盖到 {sorted(fns)}——七个现役层的派发函数应该都在里面'


# ───────────────── 跨模块「返回元数」漂移 ─────────────────
#
# ⚠⚠ 这是上一节抓不到的**第二个维度**，也由一个真事故留下：
# `08240cf` 把 `stage5/prompts.py::target_band` 从 `tuple[int,int,bool]` 改成
# `tuple[int,int]`（「是不是估的」那一位随估算路一起退役），改了同文件内 3 个调用方，
# **漏了文件外 4 个** —— `novel/views/stage4.py` 与 `novel/views/stage5.py` 仍按 3 元解包，
# 于是 S-07 / S-08 两个读屏经 HTTP 直接 500，**持续了 12 个提交没人发现**。
#
# ⚠ 名字还在，所以上一节的 `hasattr` 扫描一个字都不会说。
# ⚠ 而它没被测试发现的原因也登记在案：那些用例要真语料，
#   不设 `NOVEL_OUTPUT` 就整批 skip —— **「没在查」伪装成「查过没事」**。
# ⇒ 本条改用声明：函数自己写了 `-> tuple[A, B]`，调用方就必须按那么多元解包。
#   纯 AST，不 import、不要语料。

def _fixed_tuple_arity(ann: ast.expr | None) -> int | None:
    """`-> tuple[A, B]` → 2。变长 `tuple[A, ...]`、非元组、`| None` 一律不管
    （返回 `None` 表示「这条不在本守卫的射程内」，宁可漏报也不误报）。"""
    if not isinstance(ann, ast.Subscript):
        return None
    base = ann.value
    name = base.id if isinstance(base, ast.Name) else getattr(base, 'attr', None)
    if name not in ('tuple', 'Tuple'):
        return None
    sl = ann.slice
    elts = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]
    if any(isinstance(e, ast.Constant) and e.value is Ellipsis for e in elts):
        return None
    return len(elts)


def _repo_py_files() -> list[str]:
    out: list[str] = []
    for rel in ('novel', 'contracts', 'tests', 'tools'):
        out += _py_files(rel)
    return out


def _arity_table(trees: dict[str, ast.Module]) -> dict[tuple[str, str], int]:
    """全仓「声明返回定长元组」的函数：(模块点名, 函数名) → 元数。"""
    table: dict[tuple[str, str], int] = {}
    for path, tree in trees.items():
        mod = os.path.relpath(path, ROOT).replace(os.sep, '/')[:-3].replace('/', '.')
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = _fixed_tuple_arity(n.returns)
                if a is not None and a >= 2:
                    table[(mod, n.name)] = a
    return table


def _unpack_sites(trees: dict[str, ast.Module], table: dict[tuple[str, str], int]):
    """`a, b = f(...)` 形态的解包点，能解析到 `table` 里某个函数的那些。"""
    sites = []
    for path, tree in trees.items():
        fn_bind: dict[str, tuple[str, str]] = {}      # 直接 import 进来的函数
        mod_bind: dict[str, str] = {}                 # import 进来的模块别名
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for al in n.names:
                    local = al.asname or al.name
                    if (n.module, al.name) in table:
                        fn_bind[local] = (n.module, al.name)
                    mod_bind[local] = f'{n.module}.{al.name}'
        for n in ast.walk(tree):
            if (not isinstance(n, ast.Assign) or len(n.targets) != 1
                    or not isinstance(n.targets[0], ast.Tuple)
                    or not isinstance(n.value, ast.Call)):
                continue
            tgt = n.targets[0]
            if any(isinstance(e, ast.Starred) for e in tgt.elts):
                continue                              # `a, *rest =` 不受定长约束
            f, key = n.value.func, None
            if isinstance(f, ast.Name):
                key = fn_bind.get(f.id)
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                base = mod_bind.get(f.value.id)
                if base and (base, f.attr) in table:
                    key = (base, f.attr)
            if key is not None:
                sites.append((_rel(path), n.lineno, key, table[key], len(tgt.elts)))
    return sites


def _arity_scan():
    trees: dict[str, ast.Module] = {}
    for p in _repo_py_files():
        trees[p] = ast.parse(open(p, encoding='utf-8').read(), filename=p)
    table = _arity_table(trees)
    return table, _unpack_sites(trees, table)


def test_no_caller_unpacks_a_different_number_of_values_than_declared():
    """⭐ **改了一个函数的返回元数而漏掉文件外的调用方，这条当场红。**"""
    _table, sites = _arity_scan()
    bad = [f'{rel}:{ln} 按 {got} 元解包 `{mod}.{name}()`，而它声明返回 {want} 元'
           for rel, ln, (mod, name), want, got in sites if want != got]
    assert not bad, ('跨模块的返回元数对不上了：\n  ' + '\n  '.join(bad)
                     + '\n→ 改函数返回几个值时，**文件外**的调用方也要一起改。')


def test_the_arity_scan_actually_finds_something():
    """守卫的守卫：两个数都是实测值，掉下来就说明解析器没认出现在的写法。"""
    table, sites = _arity_scan()
    assert len(table) >= 132, f'只认出 {len(table)} 个「返回定长元组」的函数（曾实测 132）'
    assert len(sites) >= 201, f'只查到 {len(sites)} 个可解析的解包点（曾实测 201）'
