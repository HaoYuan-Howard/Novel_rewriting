#!/usr/bin/env python3
"""Validate the project-local novel pipeline skill against the live registries.

不引入 pytest/yaml 这类工具依赖，但**必须用项目解释器跑**（`novel_refactor` 环境）：
「这条入口会不会写盘」的真值只有一个落点 `novel/orchestrator/nodes.py::write_effect`，
而那条 import 链上有 pydantic。**不要在这里重拼一份 `WRITE_EFFECTS.get(f) == ''` 的规则**
——它判不出 `stage6.hook --identity` 不写盘而 `stage7.rename --identity` 重写 name map
这类跨节点例外，正是 `NODE_WRITE_EFFECTS` 存在的理由。换个解释器跑会走 `main()` 的
崩溃分支报 FAIL，不会假绿。

需要 pydantic 模型实例才能验的事（`render()` 的真实输出、CLI 真跑）不放这里，
放 `tests/test_skill_operator.py`；本文件只做"读代码 + 读文档"的静态对账。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"


def _repo_root() -> Path:
    for candidate in (SKILL_DIR, *SKILL_DIR.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "contracts" / "invariants.py"
        ).is_file():
            return candidate
    raise RuntimeError("找不到仓根（需要 pyproject.toml 与 contracts/invariants.py）")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md 必须以 YAML frontmatter 开头")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("SKILL.md frontmatter 缺少结束分隔符") from exc
    data: dict[str, str] = {}
    for lineno, line in enumerate(lines[1:end], 2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):\s*(.+)", line)
        if not match:
            raise ValueError(f"frontmatter 第 {lineno} 行不是简单 key: value：{line!r}")
        key, value = match.groups()
        if key in data:
            raise ValueError(f"frontmatter 重复字段：{key}")
        data[key] = value.strip().strip('"\'')
    return data, "\n".join(lines[end + 1 :])


def _local_links(path: Path, text: str) -> list[Path]:
    links: list[Path] = []
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = target.strip().strip("<>").split("#", 1)[0]
        if not target or re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I):
            continue
        links.append((path.parent / target).resolve())
    return links


class _Production:
    """生产侧真值。**行为断言全部从它反查**，不在校验器里手抄第二份清单。"""

    def __init__(self, root: Path) -> None:
        sys.path.insert(0, str(root))
        try:
            from contracts import invariants
            from contracts.rules import REGISTRY
            import contracts.judges  # noqa: F401  判据靠 import 副作用注册进 REGISTRY
            from novel.orchestrator.nodes import is_paid, write_effect
        finally:
            sys.path.pop(0)
        self.inv = invariants
        self.registry = REGISTRY
        self.is_paid = is_paid
        self.write_effect = write_effect

    def writes(self, node: str, flag: str) -> str:
        """这一条入口会往盘上写什么；空串 = 只看不动。取自生产那一个落点。"""
        return self.write_effect(node, [flag])

    def free_writing_pairs(self) -> list[tuple[str, str]]:
        """0 成本**且会改动盘上产物**的 `(节点, 入口)`。

        「入口」用 `FREE_ENTRIES` 划界，不用 `VALUED_FLAGS`：后者按仓里的说法是
        「带值的参数，不是入口」（`--actor` / `--reason` / `--pick` 属于这一类），
        把它们逼进允许写入面表只会制造噪音。
        """
        return [(node, flag)
                for node, spec in self.inv.NODES.items()
                for flag in spec.flags
                if flag in self.inv.FREE_ENTRIES and self.writes(node, flag)]

    def writing_pairs(self) -> list[tuple[str, str]]:
        """所有 0 成本且会写盘的 `(节点, flag)`，含带值参数。断言 A 用它。"""
        return [(node, flag)
                for node, spec in self.inv.NODES.items()
                for flag in spec.flags
                if not self.is_paid(node, [flag]) and self.writes(node, flag)]


def _strip_code(text: str) -> str:
    """剥掉 ``` 块与行内 code span 之后的散文。

    命令、路径、终端输出里的数字**是数据**；散文里的同一个数字才是会过期的历史日志。
    不剥就只能在「假红」和「不查」之间二选一，而这两条本仓都禁止。
    """
    text = re.sub(r"(?ms)^```.*?^```", "", text)
    return re.sub(r"`[^`\n]*`", "", text)


def _dict_constant(path: Path, name: str) -> dict:
    """模块级 `NAME = {字面量: 字面量}`。ast 读，不 import。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name and isinstance(node.value, ast.Dict):
            return ast.literal_eval(node.value)
    return {}


def _subcommands(path: Path) -> set[str]:
    """argparse 注册的子命令名。ast 读，不 import。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_parser" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
    return names


#: 每跑必读的那几份。经验库的**整读预算**由它们的体量算出，不写死一个数字
#: ——写死就是第二个落点，而必读集本身会随维护变厚变薄。
_MUST_READ = ('SKILL.md', 'references/workflow.md',
              'references/delegated-review.md', 'references/runtime-profiles.md',
              'references/execution-modes.md')

#: 经验库文件按这个 glob 收。`field-notes.md` 是索引兼入口，
#: `field-notes/*.md` 是超过整读预算之后的分片（**二层渐进披露只对经验库开放**）。
_NOTES_GLOBS = ('references/field-notes.md', 'references/field-notes/*.md')


def _notes_files() -> list[Path]:
    out: list[Path] = []
    for pattern in _NOTES_GLOBS:
        out += sorted(SKILL_DIR.glob(pattern))
    return out


def _defined_names(path: Path) -> set[str]:
    """一个模块里定义过的名字（函数/类/赋值，含嵌套）。ast 读，不 import。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _section(text: str, heading: str) -> str:
    """取一个二级标题到下一个二级标题之间的正文。"""
    match = re.search(
        rf"(?m)^{re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.S)
    return match.group(1) if match else ""


def _argparse_options(path: Path) -> set[str]:
    """CLI 自己的选项（`--base` / `--yes` / `--exit` …）。

    它们不是 `NodeSpec.flags`，但文档里必然出现 ⇒ 正向对账要认它们，
    否则会把一堆真实存在的选项报成写错。ast 读，不 import。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    options: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and arg.value.startswith("--"):
                options.add(arg.value)
    return options


def _int_constants(path: Path) -> set[int]:
    """模块级 `NAME = <int 字面量>`。ast 读，不 import。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: set[int] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) \
                and isinstance(value.value, int) and not isinstance(value.value, bool):
            values.add(value.value)
    return values


def _build_prompt_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_prompt":
            return ast.get_source_segment(source, node) or ""
    return ""


#: 中文数词 → 整数。只覆盖终审模板里会出现的量级。
_CN_NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                "七": 7, "八": 8, "九": 9, "十": 10}


def _behaviour_errors(
    root: Path,
    prod: _Production,
    skill_text: str,
    workflow: str,
    skill_texts: dict[Path, str],
    counters: dict[str, int],
) -> list[str]:
    """「文档声称的 CLI 行为」与「代码实际行为」的对账。

    结构检查（链接、节点顺序、经验格式）测不到这一类：一次真实复盘里
    现役付费主路径参数若**整个 skill 一次都没提**，
    每本书因此白丢一整层证据，而校验器全绿。下面每一条都钉一个已发生的漏法。

    ⚠ 失败文案一律指向**文档**。红的是 skill 说错了，不是注册表写错了——
    把人指向 `contracts/invariants.py` 去改注册表是这一类守卫最贵的假红。
    """
    inv = prod.inv
    errors: list[str] = []

    # ① inbox 分类反查：文档少写一类，执行者就得自己猜它算不算阻断。
    # ⚠ 直接读，不从 `skill_texts` 里捞：捞不到会静默退化成空串，
    #   于是「文件不在」会被报成八条「分类漏写」——读不到就当没查是本仓明令禁止的形状。
    recovery_path = SKILL_DIR / "references" / "recovery.md"
    recovery = recovery_path.read_text(encoding="utf-8") if recovery_path.is_file() else None
    done = _section(skill_text, "## 完成定义")
    if recovery is None:
        errors.append("recovery.md 不在盘上，inbox 分类反查无从谈起")
        recovery = ""
    for kind in inv.INBOX_KIND:
        counters["inbox_kinds"] += 1
        if f"`{kind}`：" not in recovery:
            errors.append(
                f"recovery.md 没有 `{kind}` 这一类的读法（inbox 共 {len(inv.INBOX_KIND)} 类）"
            )
        if kind not in done:
            errors.append(f"SKILL.md 的「完成定义」没有交代 inbox 的 `{kind}`")

    # ② flag 双向对账。**单向不够**——历史上 `--advisory` 正是被单向检查漏掉的那一半。
    node_flags = {flag for spec in inv.NODES.values() for flag in spec.flags}
    known = (
        node_flags
        | set(inv.FREE_ENTRIES)
        | set(inv.PAID_FLAGS)
        | set(inv.VALUED_FLAGS)
        | _argparse_options(root / "novel" / "cli" / "main.py")
        # operator_phase.py 是刻意的薄入口，argparse 唯一落点在生产模块；
        # 守卫必须读取那个落点，不能因为 wrapper 不重拼 parser 就把合法 flag 判红。
        | _argparse_options(root / "novel" / "operator_telemetry.py")
        | set().union(*(
            _argparse_options(path)
            for path in sorted((SKILL.parent / "scripts").glob("*.py"))
        ))
        | {"--help"}
    )
    used: dict[str, set[str]] = {}
    for path, body in skill_texts.items():
        for token in re.findall(r"--[a-z][a-z0-9-]*", body):
            used.setdefault(token, set()).add(path.name)
    for token in sorted(set(used) - known):
        errors.append(
            f"文档写了 `{token}`（{sorted(used[token])}），"
            f"但 NODES/FREE_ENTRIES/PAID_FLAGS/VALUED_FLAGS 与 CLI 选项里都没有它"
        )
    for flag in sorted(set(inv.PAID_FLAGS) & node_flags):
        if flag not in used:
            owners = sorted(n for n, s in inv.NODES.items() if flag in s.flags)
            errors.append(
                f"付费 flag `{flag}` 挂在 {owners}，而整个 skill 一次都没提它"
                f"——漏一条现役路径的后果是每本书白丢那一层"
            )

    # ③ 退出码反查：`结果判定` 表少一行，执行者会把未登记的码当成未知崩溃。
    codes = _int_constants(root / "novel" / "cli" / "exit_codes.py")
    counters["exit_codes"] = len(codes)
    documented = {
        int(value)
        for value in re.findall(r"(?m)^\|\s*(\d+)\s*\|", _section(workflow, "## 结果判定"))
    }
    if documented != codes:
        errors.append(
            "workflow.md「结果判定」的退出码集合与 exit_codes.py 不一致：\n"
            f"  文档：{sorted(documented)}\n"
            f"  代码：{sorted(codes)}"
        )

    # ④【断言 A】会改动盘上产物的 0 成本入口，必须至少被提过一次。
    #   这是 ② 反向那条的另一半，理由同样强、而且方向相反的两种漏法都真发生过：
    #   漏 `--review-decisions` ⇒ 本可 0 成本销账的 verification 留成了余额；
    #   漏 `--identity` ⇒ 执行者以为「只是看看」，而它在 stage7 直接重写 name map。
    for node, flag in sorted(prod.writing_pairs()):
        counters["writing_flags"] += 1
        if flag not in used:
            errors.append(
                f"会改动盘上产物的 0 成本入口 `{flag}` 挂在 `{node}` 上，"
                f"而整个 skill 一次都没提它。它会：{prod.writes(node, flag)}\n"
                f"    → 补进 references/recovery.md 的「允许写入面」或对应处置段落；"
                f"确认它对受托 agent 不适用时，也要写一句「不使用它」并说明理由，不能靠沉默"
            )

    # ⑤【断言 B】「允许写入面」表与 `NODES[node].flags` 双向对账。
    #   散文里的「本层没有 X」不可判定，所以把它转成「表必须覆盖 X」——
    #   `stage2.global` 明明挂着 `--apply-edit`，文档却曾写「本层没有通用 --apply-edit」。
    #   只有这条断言抓得住那种否定陈述。
    surface = {
        node: set(re.findall(r"--[a-z][a-z0-9-]*", cell))
        for node, cell in re.findall(
            r"(?m)^\|\s*`([a-z0-9]+\.[a-z]+)`\s*\|([^|]*)\|", recovery)
    }
    counters["write_surface_rows"] = len(surface)
    for node, flags in sorted(surface.items()):
        spec = inv.NODES.get(node)
        if spec is None:
            errors.append(f"「允许写入面」表里的 `{node}` 不是注册在案的节点")
            continue
        for flag in sorted(flags - set(spec.flags)):
            errors.append(
                f"「允许写入面」把 `{flag}` 写在 `{node}` 那一行，"
                f"而该节点不接受它（现有：{sorted(spec.flags)}）"
            )
    for node, flag in sorted(prod.free_writing_pairs()):
        counters["free_writing_pairs"] += 1
        if flag not in surface.get(node, set()):
            errors.append(
                f"「允许写入面」表少了 (`{node}`, `{flag}`)。该层确实接受它，"
                f"且它会：{prod.writes(node, flag)}\n"
                f"    → 补这一行；不要去改注册表"
            )

    # ⑥【断言 I】判据 `fix` 指名的路由，skill 必须提过。
    #   与 ④ 并存不合并：④ 说「这条路会改产物，你得知道它存在」，
    #   ⑥ 说「finding 会当场指着这条路，而 skill 里没有它」。
    #   将来若有 fix 指向只读 flag，④ 抓不到、⑥ 抓得到。
    routes = {tuple(rule.fix) for rule in prod.registry.all() if getattr(rule, "fix", None)}
    counters["fix_routes"] = len(routes)
    for route in sorted(routes):
        if len(route) < 2:
            continue
        node, flag = route[0], route[1]
        if flag not in used:
            errors.append(
                f"判据的 `fix` 路由指向 `{node} {flag}`，而整个 skill 一次都没提它"
                f"——执行者读到 finding 的 fix 会不知道那是哪一类成本。"
                f"\n    → 补进 references/recovery.md 的 `deterministic` 说明"
            )

    # ⑦【断言 F】子命令**正向**对账。
    #   刻意不做反向：queue 系列是 UI/批量路径，硬要求全提会与
    #   「一次只运行一个写盘节点」的主循环打架，那是典型假红。
    registered = _subcommands(root / "novel" / "cli" / "main.py")
    counters["cli_subcommands"] = len(registered)
    written = {token for body in skill_texts.values()
               for token in re.findall(r"novel\.cli\s+([a-z][a-z-]*)", body)}
    for token in sorted(written - registered):
        errors.append(
            f"skill 写了 `novel.cli {token}`，而 novel/cli/main.py 没有注册这个子命令"
            f"（现有：{sorted(registered)}）"
        )

    # ⑧【断言 E】推荐过滤正则必须接住每一种非 ok 状态。
    #   过滤吞掉 `[--]`、`── 来源 ──` 或 `⚠` 就不再是「只改变读法」了。
    #   真值取 `render.py::_STATUS_MARK`，不在这里手抄第二份标记表。
    marks = _dict_constant(root / "novel" / "report" / "render.py", "_STATUS_MARK")
    workflow_path = SKILL_DIR / "references" / "workflow.md"
    pattern_match = re.search(
        r"Select-String -Pattern '([^']+)'",
        workflow_path.read_text(encoding="utf-8") if workflow_path.is_file() else "")
    if not marks:
        errors.append("读不到 render.py::_STATUS_MARK，过滤正则的对账无从谈起")
    elif not pattern_match:
        errors.append("workflow.md 里找不到 `Select-String -Pattern '...'` 的推荐过滤")
    else:
        pattern = pattern_match.group(1)
        counters["status_marks"] = len(marks)
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            errors.append(f"workflow.md 的过滤正则编译不过：{exc}")
            compiled = None
        if compiled is not None:
            for status, mark in sorted(marks.items()):
                hit = compiled.search(f"  {mark} some_rule  详情")
                if status != "ok" and not hit:
                    errors.append(
                        f"推荐过滤会吞掉 `{mark}`（{status}）那一类行。"
                        f"render.py::_STATUS_MARK 有 {len(marks)} 种标记，"
                        f"而 workflow.md 明写各态「各自保留语义」。"
                        f"\n    → 改 workflow.md 里的正则，不要改 render.py"
                    )
                if status == "ok" and hit:
                    errors.append(
                        f"推荐过滤连 `{mark}` 也一并留下了，那它就没有在过滤"
                        f"——这条反向自检防的是把正则写成 `.*` 换绿灯"
                    )
            for line, why in (
                ("── 来源 ── fresh（原因）", "freshness 的唯一出处"),
                ("  ⚠ 判的这一份是 .INVALID 稿", "把 .INVALID 稿读成正式结论"),
                ("  [????] some.key：未记录 —— item", "「没人说」与「没有」分不开"),
            ):
                if not compiled.search(line):
                    errors.append(f"推荐过滤吞掉了 `{line.strip()}`——{why}")

    # ⑨【断言 C】终审模板的计数口径必须跟着 `INBOX_KIND` 走。
    #   `860e1b1` 那次十二处对账改了七个文件，独独漏了模板，而模板才是用户读到的那份。
    template_text = skill_texts.get(SKILL_DIR / "assets" / "final-review-template.md", "")
    for written_count in re.findall(r"([一二三四五六七八九十]|\d+)\s*类计数", template_text):
        value = _CN_NUMERALS.get(written_count, None)
        if value is None:
            value = int(written_count) if written_count.isdigit() else None
        if value is not None and value != len(inv.INBOX_KIND):
            errors.append(
                f"终审模板写「{written_count}类计数」，而 INBOX_KIND 是 "
                f"{len(inv.INBOX_KIND)} 类（recovery.md 与 SKILL.md 已经是八类，只有模板漏改）"
            )

    return errors


def validate(counters: dict[str, int] | None = None,
             advice: list[str] | None = None) -> list[str]:
    """返回逐条问题；`counters` 填**扫描分母**，`advice` 填**只提示不报错**的那些。

    绿灯必须带分母：只说「一致」看不出查了几条，正则失配或 glob 变空时会全绿。
    `main()` 在 PASS 分支把两者都印出来，`tests/test_skill_operator.py` 给每个计数设下界。

    ⚠ `advice` 与 `errors` **分开**是刻意的：晋升与规模这两件事的判据是产品判断，
    做成红灯必然假红（一条边角经验会被逼进主链文档）。既要暴露生命周期又不误导，
    出路就是提示而不是失败。
    """
    if counters is None:
        counters = {}
    if advice is None:
        advice = []
    for key in ("markdown_files", "links", "inbox_kinds", "exit_codes", "fn_entries",
                "writing_flags", "free_writing_pairs", "write_surface_rows",
                "fix_routes", "cli_subcommands", "status_marks",
                "template_headings", "evidence_symbols", "notes_files",
                "notes_chars", "notes_budget", "index_rows", "repo_fn_citations",
                "open_gap_entries", "stage0_workpack_contract",
                "finalizer_contract", "operator_telemetry_contract",
                "routing_axes", "discovery_shims", "operator_next_contract",
                "operator_execution_permit_contract"):
        counters.setdefault(key, 0)
    errors: list[str] = []
    root = _repo_root()
    text = SKILL.read_text(encoding="utf-8")

    try:
        frontmatter, body = _frontmatter(text)
    except ValueError as exc:
        return [str(exc)]

    if set(frontmatter) != {"name", "description"}:
        errors.append(
            "frontmatter 只能包含 name/description，实际为 "
            + repr(sorted(frontmatter))
        )
    if frontmatter.get("name") != SKILL_DIR.name:
        errors.append(
            f"name 必须与目录名一致：{frontmatter.get('name')!r} != {SKILL_DIR.name!r}"
        )
    description = frontmatter.get("description", "")
    for token in ("novel.cli", "_review.md", "付费授权", "正文外发授权"):
        if token not in description:
            errors.append(f"description 缺少触发/边界关键词：{token}")

    line_count = len(text.splitlines())
    if line_count >= 500:
        errors.append(f"SKILL.md 共 {line_count} 行，应少于 500 行")

    markdown_files = [SKILL, *sorted((SKILL_DIR / "references").glob("*.md"))]
    asset_template = SKILL_DIR / "assets" / "final-review-template.md"
    markdown_files.append(asset_template)
    skill_texts: dict[Path, str] = {}
    for path in markdown_files:
        if not path.is_file():
            errors.append(f"缺少文件：{path.relative_to(SKILL_DIR)}")
            continue
        linked_text = path.read_text(encoding="utf-8")
        skill_texts[path] = linked_text
        counters["markdown_files"] += 1
        for target in _local_links(path, linked_text):
            counters["links"] += 1
            if not target.exists():
                errors.append(
                    f"失效链接：{path.relative_to(SKILL_DIR)} -> {target}"
                )

    direct = set(_local_links(SKILL, text))
    for reference in sorted((SKILL_DIR / "references").glob("*.md")):
        if reference.resolve() not in direct:
            errors.append(
                f"reference 未从 SKILL.md 直接链接：{reference.relative_to(SKILL_DIR)}"
            )

    # 宿主与 executor 是正交两轴。过去 runtime-profiles.md 同时装宿主等待、
    # DeepSeek doctor 与 agent session 恢复；执行者即使只想读 headless，也会被迫把
    # 另一 transport 一起装进上下文。这里守文件职责，不复制 capability 枚举。
    runtime_path = SKILL_DIR / "references" / "runtime-profiles.md"
    execution_path = SKILL_DIR / "references" / "execution-modes.md"
    runtime_text = skill_texts.get(runtime_path, "")
    execution_text = skill_texts.get(execution_path, "")
    if runtime_text:
        counters["routing_axes"] += 1
        for heading in ("## 交互式主会话", "## headless agent", "## 只读进度观察"):
            if heading not in runtime_text:
                errors.append(f"runtime-profiles.md 缺宿主小节：{heading}")
        for forbidden in ("doctor --llm-transport", "response-repair", "ResponseRepairRequired"):
            if forbidden in runtime_text:
                errors.append(
                    f"runtime-profiles.md 仍夹带 executor/transport 过程 `{forbidden}`；"
                    "移到 execution-modes.md")
    if execution_text:
        counters["routing_axes"] += 1
        for heading in ("## Agent driver", "## Legacy LLM transport"):
            if heading not in execution_text:
                errors.append(f"execution-modes.md 缺执行模式小节：{heading}")
        if "禁止自动降级" not in execution_text:
            errors.append("execution-modes.md 没有关闭式禁止 Agent/legacy 自动降级")

    # Codex/Claude 各扫描自己的发现目录。宿主可能把 `.agents` 的同名 skill
    # 物化到 `.claude`；因此两边只能放逐字相同的薄 shim，真实契约必须待在
    # 不参与宿主同步的 `.workflow`。这样再次覆盖也只是相同字节替换相同字节。
    expected_canonical = (
        root / ".workflow" / "skills" / "novel-pipeline-operator")
    if SKILL_DIR != expected_canonical:
        errors.append(
            "canonical skill 必须位于 neutral `.workflow/skills/novel-pipeline-operator`，"
            "不得放进 `.agents` 或 `.claude` 的宿主同步命名空间")
    discovery_entries = {
        "Codex": root / ".agents" / "skills" / "novel-pipeline-operator" / "SKILL.md",
        "Claude": root / ".claude" / "skills" / "novel-pipeline-operator" / "SKILL.md",
    }
    shim_bytes: dict[str, bytes] = {}
    canonical_pointer = (
        "../../../.workflow/skills/novel-pipeline-operator/SKILL.md")
    for host, entry in discovery_entries.items():
        if not entry.is_file():
            errors.append(f"缺少 {entry.relative_to(root)} {host} 发现入口")
            continue
        counters["discovery_shims"] += 1
        raw = entry.read_bytes()
        shim_bytes[host] = raw
        try:
            shim = raw.decode("utf-8")
            shim_frontmatter, shim_body = _frontmatter(shim)
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{host} 发现入口无效：{exc}")
            continue
        if shim_frontmatter.get("name") != "novel-pipeline-operator":
            errors.append(f"{host} 发现入口 name 与 canonical skill 不一致")
        if canonical_pointer not in shim_body:
            errors.append(f"{host} 发现入口没有指向 neutral canonical SKILL.md")
        for duplicated in ("## 执行主循环", "scripts/run_step.py", "## 完成定义"):
            if duplicated in shim_body:
                errors.append(
                    f"{host} 发现入口复制了主链内容 `{duplicated}`；只允许保留指针")
        for copied_dir in ("references", "scripts", "assets"):
            copied = entry.parent / copied_dir
            if copied.exists() and any(copied.rglob("*")):
                errors.append(
                    f"{host} 发现入口出现第二份 {copied_dir}/；"
                    "主链资源只能留在 neutral canonical skill")
    if len(shim_bytes) == 2 and len(set(shim_bytes.values())) != 1:
        errors.append("Codex 与 Claude 发现 shim 必须逐字节相同，防止宿主同步改写语义")

    next_command = "novel.cli operator next <SID> --base <BASE> --json"
    if next_command not in text:
        errors.append("SKILL.md 主循环没有先调用确定性 `operator next --json`")
    else:
        counters["operator_next_contract"] += 1
        if ("scripts/run_step.py" in text
                and text.index(next_command) > text.index("scripts/run_step.py")):
            errors.append("SKILL.md 在取得 operator next action 之前就进入了 run_step")
    if "每次节点、typed approval、人工修订或恢复动作结束后" not in text:
        errors.append("SKILL.md 没有要求每次状态变更后重新取得 operator next action")
    permit_command = (
        "novel.cli operator permit <SID> <node> --base <BASE> --json -- [flags]")
    next_file_arg = "--operator-permit-file <NEXT_FILE>"
    if permit_command not in text:
        errors.append("SKILL.md 没有保留 blocked recovery 的 exact operator permit")
    if next_file_arg not in text:
        errors.append("SKILL.md 的 ready run_step 没有原样复用 operator next token 文件")
    if "ready action 不再第二次调用 `operator permit`" not in text:
        errors.append("SKILL.md 的 ready 主路仍可能重复调用 operator permit/full snapshot")
    if "--operator-permit-sha256" in text:
        errors.append(
            "SKILL.md 仍把 SHA-only execution permit 写进默认主路；"
            "该参数只保留给旧脚本诊断兼容")
    if permit_command in text and next_file_arg in text:
        counters["operator_execution_permit_contract"] += 1

    workflow_path = SKILL_DIR / "references" / "workflow.md"
    delegated_path = SKILL_DIR / "references" / "delegated-review.md"
    workflow = skill_texts.get(workflow_path, "")
    delegated = skill_texts.get(delegated_path, "")
    if permit_command not in workflow:
        errors.append("workflow.md 没有保留 blocked recovery 的 exact operator permit")
    if next_file_arg not in workflow:
        errors.append("workflow.md 的 ready run_step 没有复用原样 operator next 文件")
    if "只有 blocked" not in workflow:
        errors.append("workflow.md 没有把显式 operator permit 限定到 blocked recovery")
    if "--operator-permit-sha256" in workflow:
        errors.append(
            "workflow.md 仍把 SHA-only execution permit 写进默认命令；"
            "兼容慢路只能在维护/诊断说明中出现")
    for name, body in (("workflow.md", workflow),
                       ("delegated-review.md", delegated)):
        if "scripts/finalize_review.py" not in body:
            errors.append(f"{name} 没有指向唯一 canonical finalize_review.py 收尾")
        for legacy_finalizer in (
                "scripts/collect_final_evidence.py",
                "scripts/sync_review_evidence.py",
                "scripts/validate_review.py",
                "scripts/verify_delivery.py"):
            if legacy_finalizer in body:
                errors.append(
                    f"{name} 仍把兼容脚本 {legacy_finalizer} 写进主操作规程；"
                    "诊断入口只在 maintenance.md 登记")

    # Stage0 快速审阅工作包是一条只读能力契约：文档若提到它，文件、生产读取缝与
    # “不审批/不写盘”必须一起存在。只验调用的生产名字，不在这里复制任何模式词表。
    workpack_path = SKILL_DIR / "scripts" / "stage0_review_workpack.py"
    maintenance_path = SKILL_DIR / "references" / "maintenance.md"
    delegated = skill_texts.get(delegated_path, "")
    maintenance = skill_texts.get(maintenance_path, "")
    workpack_token = "scripts/stage0_review_workpack.py"
    if not workpack_path.is_file():
        errors.append("Stage0 审阅契约指向的 scripts/stage0_review_workpack.py 不在盘上")
    else:
        counters["stage0_workpack_contract"] += 1
        tree = ast.parse(workpack_path.read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        required_calls = {
            "stage0_report", "stage0_segments", "run_check_only",
            "readonly_ctx", "scan_text",
        }
        missing_calls = sorted(required_calls - calls)
        if missing_calls:
            errors.append(
                "stage0_review_workpack.py 没有调用完整的生产只读缝："
                f"缺 {missing_calls}。不要在脚本里重拼报告/分区/词法"
            )
        forbidden_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } & {"ArtifactStore", "run_approve", "run_edit", "run_remove"}
        forbidden_attrs = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        } & {"load", "loads", "write_text", "write_bytes", "write_own", "append_text"}
        if forbidden_names or forbidden_attrs:
            errors.append(
                "stage0_review_workpack.py 越过了只读生产出口："
                f"names={sorted(forbidden_names)} attrs={sorted(forbidden_attrs)}"
            )
    for doc, body_text in (("delegated-review.md", delegated),
                           ("maintenance.md", maintenance)):
        if workpack_token not in body_text:
            errors.append(f"{doc} 没有登记 {workpack_token}")

    # canonical 终审必须只 collect 一次，在内存 validate 通过后才 commit，随后另建
    # fresh delivery 读作用域。这里只核生产函数的调用面，不在守卫里重拼顺序判据。
    finalizer_path = SKILL_DIR / "scripts" / "finalize_review.py"
    finalizer_token = "scripts/finalize_review.py"
    if not finalizer_path.is_file():
        errors.append("canonical 终审契约指向的 scripts/finalize_review.py 不在盘上")
    else:
        counters["finalizer_contract"] += 1
        tree = ast.parse(finalizer_path.read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        required_calls = {"collect", "render", "validate", "commit_review", "assess"}
        missing_calls = sorted(required_calls - calls)
        if missing_calls:
            errors.append(
                "finalize_review.py 没有贯穿 collect/render/validate/commit/fresh assess："
                f"缺 {missing_calls}"
            )
    for doc, body_text in (("SKILL.md", text), ("maintenance.md", maintenance)):
        if finalizer_token not in body_text:
            errors.append(f"{doc} 没有登记 canonical {finalizer_token}")
    if "--with-inbox" not in text:
        errors.append("SKILL.md 没有使用 invocation-scoped state --with-inbox")

    # operator_phase.py 只保留为可选诊断薄入口。正式主路由 RunSupervisor 自动记录；
    # 守卫验证 wrapper 没重拼实现，但**不得**反过来要求 Commander 每动作调用它。
    telemetry_path = SKILL_DIR / "scripts" / "operator_phase.py"
    telemetry_token = "scripts/operator_phase.py"
    if not telemetry_path.is_file():
        errors.append("operator 遥测契约指向的 scripts/operator_phase.py 不在盘上")
    else:
        counters["operator_telemetry_contract"] += 1
        tree = ast.parse(telemetry_path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "novel.operator_telemetry"
            for alias in node.names
        }
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "cli_main" not in imported or "cli_main" not in calls:
            errors.append(
                "operator_phase.py 必须只把 CLI 交给 novel.operator_telemetry.cli_main"
            )
    if telemetry_token not in maintenance:
        errors.append("maintenance.md 没有登记可选诊断 scripts/operator_phase.py")
    for manual_token in ("phase-start", "phase-end", "session-start", "session-end"):
        if manual_token in text:
            errors.append(
                f"SKILL.md 默认主循环仍要求 Commander 手工 `{manual_token}`；"
                "正式 phase/ledger 联结应由 RunSupervisor 自动记录")
    if "RunSupervisor" not in text or "确定性状态机" not in text:
        errors.append("SKILL.md 没有声明 RunSupervisor 是不做模型推理的确定性状态机")
    if "unavailable_host_boundary" not in text:
        errors.append("SKILL.md 没有声明 Commander 宿主模型 usage 的不可见边界")

    # 【断言 D】日期、版本号与无锚的一次性读数——**全部 skill markdown**，不只 SKILL.md。
    # 那条唯一的日期戳能长期活着，就是因为原来只查 SKILL.md 正文。
    # 先剥代码块与行内 code，再扫散文：不剥的话「每 60 秒汇报一次」这种合法指令会假红。
    for path in sorted(skill_texts, key=str):
        prose = _strip_code(skill_texts[path])
        rel = path.relative_to(SKILL_DIR)
        if re.search(r"(?im)(?:^|\s)v\d+(?:\.\d+)*\b|20\d{2}(?:年|[-/])", prose):
            errors.append(
                f"{rel} 的正文里写了日期或版本号。"
                f"「何时改过」由 Git 回答，schema 版本由 `--emit-edit-template` 现取"
            )
        for sentence in re.split(r"[。！？\n]", prose):
            if re.search(r"实测|实跑", sentence) and re.search(r"\d", sentence):
                errors.append(
                    f"{rel} 有一处带数字的「实测/实跑」读数且没有 freshness 锚："
                    f"{sentence.strip()[:60]}\n"
                    f"    → 它明年还对吗？保留「为什么这么做」，删掉「当时量到多少」"
                )
        for unit in re.findall(r"\d+(?:\.\d+)?\s*(?:KB|MB|GB|%)", prose):
            errors.append(
                f"{rel} 的正文写了一次性体量/比例读数 `{unit}`；"
                f"改成定性表述，判据条数与报告体积都会随注册表增删而变"
            )

    prod = _Production(root)
    inv = prod.inv

    workflow = (SKILL_DIR / "references" / "workflow.md").read_text(encoding="utf-8")
    match = re.search(
        r"<!-- pipeline-steps:start -->(.*?)<!-- pipeline-steps:end -->",
        workflow,
        re.S,
    )
    if not match:
        errors.append("workflow.md 缺少 pipeline-steps 标记区")
    else:
        documented = tuple(
            re.findall(r"(?m)^\|\s*\d+\s*\|\s*`([^`]+)`\s*\|", match.group(1))
        )
        actual = tuple(inv.PIPELINE_STEPS)
        if documented != actual:
            errors.append(
                "主链顺序漂移：\n"
                f"  文档：{documented}\n"
                f"  代码：{actual}"
            )

    errors += _behaviour_errors(root, prod, text, workflow, skill_texts, counters)

    # ── 经验库 ──
    # 索引恒在 `field-notes.md`；条目可以留在同一份，也可以分片到 `field-notes/`。
    # 条目扫描面是整棵树，索引扫描面只有入口那一份。
    index_path = SKILL_DIR / "references" / "field-notes.md"
    notes_files = _notes_files()
    counters["notes_files"] = len(notes_files)
    if not notes_files:
        errors.append("经验库一个文件都没有；条目与索引的对账无从谈起")
    notes = "\n".join(path.read_text(encoding="utf-8") for path in notes_files)
    entry_matches = list(re.finditer(r"(?m)^### (FN-\d{3}) · (.+)$", notes))
    ids = [m.group(1) for m in entry_matches]
    if not ids:
        errors.append("经验库没有 FN-NNN 条目")
    if len(ids) != len(set(ids)):
        errors.append(f"经验 ID 重复：{ids}")
    counters["fn_entries"] = len(ids)

    # 【断言 G】索引是条目的**投影**，不是第二份清单。
    # 标题逐字对账 ⇒ 改标题而忘了改索引这类漂移在结构上不可能存在。
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    index_rows = re.findall(
        r"(?m)^\|\s*(FN-\d{3})\s*\|([^|]*)\|\s*([a-z-]+)\s*\|([^|]*)\|([^|]*)\|",
        index_text)
    counters["index_rows"] = len(index_rows)
    if [row[0] for row in index_rows] != ids:
        errors.append(
            "经验索引与条目对不上（顺序与集合都要一致）：\n"
            f"  索引：{[row[0] for row in index_rows]}\n"
            f"  条目：{ids}"
        )
    else:
        titles = {m.group(1): m.group(2).strip() for m in entry_matches}
        for fn_id, title_cell, status_cell, trigger_cell, entry_cell in index_rows:
            if title_cell.strip() != titles[fn_id]:
                errors.append(
                    f"{fn_id} 索引标题与条目标题不一致（索引是条目的投影，不是第二个名字）：\n"
                    f"  索引：{title_cell.strip()}\n"
                    f"  条目：{titles[fn_id]}"
                )

    # 【断言 K】分片必须由索引直接链接，且**不得**被 SKILL.md 直接链接。
    # 保证渐进披露仍是 SKILL → 索引 → 分片的严格树，不出现旁路。
    # 现在就检查，即使还没分片——空扫描面由 `notes_files` 这个分母兜住。
    index_links = set(_local_links(index_path, index_text)) if index_text else set()
    for shard in sorted((SKILL_DIR / "references" / "field-notes").glob("*.md")):
        if shard.resolve() not in index_links:
            errors.append(
                f"经验分片 {shard.relative_to(SKILL_DIR)} 没有被 field-notes.md 直接链接"
            )
        if shard.resolve() in direct:
            errors.append(
                f"经验分片 {shard.relative_to(SKILL_DIR)} 被 SKILL.md 直接链接了。"
                f"经验库是唯一开二层渐进披露的地方，路径必须是 SKILL → 索引 → 分片，不开旁路"
            )

    # 【整读预算】经验库单份的体量不得超过每跑必读集——超过之后「按需检索」名存实亡，
    # 因为文件看起来不大时执行者会直接整读。预算由必读集**算出**，不写死数字。
    budget = sum(len((SKILL_DIR / rel).read_text(encoding="utf-8"))
                 for rel in _MUST_READ if (SKILL_DIR / rel).is_file())
    counters["notes_budget"] = budget
    for path in notes_files:
        size = len(path.read_text(encoding="utf-8"))
        counters["notes_chars"] = counters.get("notes_chars", 0) + size
        if budget and size > budget:
            errors.append(
                f"{path.relative_to(SKILL_DIR)} 的体量已超过每跑必读集"
                f"（{size} > {budget} 字符），整读它比读完主链文档还贵。\n"
                f"    → **分片**到 references/field-notes/ 并从索引链接，"
                f"不要为了缩体积删经验"
            )
    required_fields = ("状态", "触发", "判断", "处置", "验证", "依据")
    allowed_statuses = {"contract-confirmed", "observed", "open-gap"}
    by_index = {row[0]: row for row in index_rows}
    for index, entry in enumerate(entry_matches):
        end = entry_matches[index + 1].start() if index + 1 < len(entry_matches) else len(notes)
        block = notes[entry.end() : end]
        for field in required_fields:
            if not re.search(rf"(?m)^- {re.escape(field)}：\S", block):
                errors.append(f"{entry.group(1)} 缺少字段：{field}")
        status = re.search(r"(?m)^- 状态：`([^`]+)`", block)
        if status and status.group(1) not in allowed_statuses:
            errors.append(f"{entry.group(1)} 状态非法：{status.group(1)}")

        # 【断言 G 续】索引其余三列也必须是投影，不能各说各话。
        row = by_index.get(entry.group(1))
        if row is not None:
            _fn, _title, status_cell, trigger_cell, entry_cell = row
            if status and status_cell.strip() != status.group(1):
                errors.append(
                    f"{entry.group(1)} 索引「状态」是 {status_cell.strip()}，"
                    f"条目里是 {status.group(1)}——状态决定命中后要不要打开它，不能两说"
                )
            for token in re.findall(r"`([^`]+)`", trigger_cell):
                if token not in block:
                    errors.append(
                        f"{entry.group(1)} 索引把 `{token}` 列为触发键，而条目正文里没有它"
                        f"——触发键是 rg 的实际命中面，检索得到索引却打不开条目是最坏的一种"
                    )
            disposal = re.search(r"(?s)^- 处置：(.*?)(?=\n- 验证：)", block, re.M)
            for flag in re.findall(r"--[a-z][a-z0-9-]*", entry_cell):
                if disposal is None or flag not in disposal.group(1):
                    errors.append(
                        f"{entry.group(1)} 索引把 `{flag}` 列为处置入口，"
                        f"而条目的「处置」字段里没有它"
                    )

        # 【断言 J】`open-gap` 的时效性由条目**自带的复核命令**承担，不挂仓内待办编号。
        # 编号方案试过一版（指向另一份文档的 `TODO-NN`）：它把 skill 绑在那份文档的标题
        # 形状上——对方换个写法，这里要么假红要么静默漏查，而两种都是本仓明令禁止的。
        # 自带命令没有这个耦合，且对读者更有用：它回答的是"现在还缺不缺"，不是"谁登记过"。
        if status and status.group(1) == "open-gap":
            counters["open_gap_entries"] += 1
            recheck = re.search(r"(?m)^- 复核：(.+)$", block)
            if recheck is None:
                errors.append(
                    f"{entry.group(1)} 是 open-gap 但没有 `- 复核：` 那一行。"
                    f"缺口会被别人修掉，而经验不会自己退役"
                    f"\n    → 补一条 0 成本命令，跑一次就能判定这个缺口还在不在"
                )
            elif "`" not in recheck.group(1):
                errors.append(
                    f"{entry.group(1)} 的 `复核` 只有散文，没有可跑的命令。"
                    f"散文复核不报错，但下一个维护者判不了缺口还在不在"
                    f"\n    → 把命令写进反引号"
                )
        # 【断言 H】`依据` 里写成 `路径::符号` 的引用必须落地。
        # 只查已经写了这个形态的，不强制每条都有——强制会让那批散文依据当场全红，
        # 那是内容工作，不是守卫该逼出来的。
        for rel_path, symbol in re.findall(
                r"`((?:novel|contracts|tests|tools)/[\w./-]+\.py)::(\w+)[^`]*`", block):
            counters["evidence_symbols"] += 1
            target = root / rel_path
            if not target.is_file():
                errors.append(f"{entry.group(1)} 的依据指着 {rel_path}，该文件不在盘上")
            elif symbol not in _defined_names(target):
                errors.append(
                    f"{entry.group(1)} 的依据指着 {rel_path}::{symbol}，该符号不在盘上。"
                    f"经验的价值等于它的依据能不能回查"
                    f"\n    → 改成当前符号名，或按经验生命周期第 7 步退役这一条"
                )

    template = asset_template.read_text(encoding="utf-8")
    required_headings = (
        "## 1. 用户终审入口",
        "## 2. 交付与完整性",
        "## 3. Agent 代批记录",
        "## 4. 各层质量读数",
        "## 5. 残红、不可验证项与实现边界",
        "## 6. 调用与耗时账本",
        "## 7. 恢复路径与证据指针",
    )
    for heading in required_headings:
        counters["template_headings"] += 1
        if heading not in template:
            errors.append(f"终审模板缺少章节：{heading}")

    # 【FN 编号纪律的可查那一半】仓内按号引用的经验必须存在。
    # 「退役 ⇒ 留空号 ⇒ 引用悬空 ⇒ 当场红」形成闭环；**复用编号抓不住**，只能靠纪律。
    # 实测过复用的代价：六处代码注释指着 FN-014，而那个号上挂的是另一件事。
    known_fn = set(ids)
    cited: dict[str, list[str]] = {}
    for pkg in ("novel", "contracts", "tests"):
        for source in sorted((root / pkg).rglob("*.py")):
            if "__pycache__" in source.parts:
                continue
            for fn_id in set(re.findall(r"\bFN-\d{3}\b", source.read_text(encoding="utf-8"))):
                cited.setdefault(fn_id, []).append(
                    str(source.relative_to(root)).replace("\\", "/"))
    counters["repo_fn_citations"] = len(cited)
    for fn_id in sorted(set(cited) - known_fn):
        errors.append(
            f"仓内 {cited[fn_id]} 按号引用 {fn_id}，而经验库里没有它。"
            f"编号退役要留空号，引用也要跟着退役——读的人会去找它，"
            f"然后按自己的猜测把文档读通"
        )

    consumer = root / "novel" / "orchestrator" / "agent.py"
    if consumer.is_file():
        prompt_source = _build_prompt_source(consumer)
        if not prompt_source:
            errors.append("headless consumer 缺少 build_prompt")
        elif "§" in prompt_source:
            errors.append("headless build_prompt 仍引用易漂移的 SKILL 章节号 §")

    # ── 只提示，不报错 ──
    # ⚠ **刻意没有「晋升候选」提示。** 试过一版判据（`contract-confirmed` 且依据已点名
    #   代码符号），它在当前盘面上命中 16 条里的 12 条——那不是信号，是噪音，
    #   而一条总在喊的提示会把人训练成忽略它。晋升的真判据是「会不会影响所有运行」，
    #   那是产品判断，没有机械代理；它的复核时机是维护协议里的前向测试。
    if ids and counters["notes_chars"] > budget * 0.7:
        advice.append(
            f"经验库已用掉整读预算的 {counters['notes_chars'] * 100 // max(budget, 1)}%"
            f"（{len(ids)} 条）。检索精度随条数下降——最常用的检索键是节点名，"
            f"选择度只有 1/8。该考虑按 stage 分片到 references/field-notes/ 了"
        )

    return errors


def main() -> int:
    _utf8_console()
    counters: dict[str, int] = {}
    advice: list[str] = []
    try:
        errors = validate(counters, advice)
    except Exception as exc:  # validator failure must be visible, never a false pass
        print(f"FAIL validator crashed: {type(exc).__name__}: {exc}")
        return 1
    if errors:
        print(f"FAIL novel-pipeline-operator ({len(errors)} issues)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASS novel-pipeline-operator")
    print("  frontmatter, links, progressive disclosure, pipeline order, field notes,")
    print("  final-review template, and headless consumer references are consistent;")
    print("  inbox kinds, flags (both directions), write surface, fix routes,")
    print("  subcommands, report filter and exit codes match the registries")
    # 绿灯必须带分母：只说「一致」看不出查了几条，正则失配时会全绿。
    print("  扫描分母：" + " · ".join(
        f"{key} {value}" for key, value in sorted(counters.items())))
    for note in advice:
        print(f"  · {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
