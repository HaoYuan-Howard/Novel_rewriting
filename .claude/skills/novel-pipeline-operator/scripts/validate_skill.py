#!/usr/bin/env python3
"""Validate the project-local novel pipeline skill without third-party packages."""
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


def _invariants(root: Path):
    """注册表本体。**行为断言全部从它反查**，不在校验器里手抄第二份清单。"""
    sys.path.insert(0, str(root))
    try:
        from contracts import invariants
    finally:
        sys.path.pop(0)
    return invariants


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


def _behaviour_errors(
    root: Path,
    inv,
    skill_text: str,
    workflow: str,
    skill_texts: dict[Path, str],
) -> list[str]:
    """「文档声称的 CLI 行为」与「代码实际行为」的对账。

    结构检查（链接、节点顺序、经验格式）测不到这一类：一次真实复盘里
    `--advisory` 这条注册在案的付费主路径**整个 skill 一次都没提**，
    每本书因此白丢一整层证据，而校验器全绿。三条断言各钉一个已发生的漏法。
    """
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
        if f"`{kind}`：" not in recovery:
            errors.append(
                f"recovery.md 没有 `{kind}` 这一类的读法（inbox 共 {len(inv.INBOX_KIND)} 类）"
            )
        if kind not in done:
            errors.append(f"SKILL.md 的「完成定义」没有交代 inbox 的 `{kind}`")

    # ② flag 双向对账。**单向不够**——`--advisory` 正是被单向检查漏掉的那一半。
    node_flags = {flag for spec in inv.NODES.values() for flag in spec.flags}
    known = (
        node_flags
        | set(inv.FREE_ENTRIES)
        | set(inv.PAID_FLAGS)
        | set(inv.VALUED_FLAGS)
        | _argparse_options(root / "novel" / "cli" / "main.py")
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

    return errors


def validate() -> list[str]:
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
    for token in ("novel.cli", "final_review_", "付费授权", "正文外发授权"):
        if token not in description:
            errors.append(f"description 缺少触发/边界关键词：{token}")

    line_count = len(text.splitlines())
    if line_count >= 500:
        errors.append(f"SKILL.md 共 {line_count} 行，应少于 500 行")
    if re.search(r"(?im)(?:^|\s)v\d+(?:\.\d+)*\b|20\d{2}(?:年|[-/])", body):
        errors.append("SKILL.md 含版本号或日期残留；历史应由 Git/经验证据承担")

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
        for target in _local_links(path, linked_text):
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

    inv = _invariants(root)

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

    errors += _behaviour_errors(root, inv, text, workflow, skill_texts)

    notes_path = SKILL_DIR / "references" / "field-notes.md"
    notes = notes_path.read_text(encoding="utf-8")
    entry_matches = list(re.finditer(r"(?m)^### (FN-\d{3}) · .+$", notes))
    ids = [m.group(1) for m in entry_matches]
    if not ids:
        errors.append("经验库没有 FN-NNN 条目")
    if len(ids) != len(set(ids)):
        errors.append(f"经验 ID 重复：{ids}")
    required_fields = ("状态", "触发", "判断", "处置", "验证", "依据")
    allowed_statuses = {"contract-confirmed", "observed", "open-gap"}
    requirements = (root / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
    for index, entry in enumerate(entry_matches):
        end = entry_matches[index + 1].start() if index + 1 < len(entry_matches) else len(notes)
        block = notes[entry.end() : end]
        for field in required_fields:
            if not re.search(rf"(?m)^- {re.escape(field)}：\S", block):
                errors.append(f"{entry.group(1)} 缺少字段：{field}")
        status = re.search(r"(?m)^- 状态：`([^`]+)`", block)
        if status and status.group(1) not in allowed_statuses:
            errors.append(f"{entry.group(1)} 状态非法：{status.group(1)}")
        if status and status.group(1) == "open-gap":
            issue_ids = re.findall(r"\b(?:BE|VAL)-\d+\b", block)
            if not issue_ids:
                errors.append(f"{entry.group(1)} 是 open-gap 但没有活动缺口编号")
            for issue_id in issue_ids:
                if issue_id not in requirements:
                    errors.append(
                        f"{entry.group(1)} 引用的 {issue_id} 已不在 REQUIREMENTS；重新验证并退役"
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
        if heading not in template:
            errors.append(f"终审模板缺少章节：{heading}")

    consumer = root / "novel" / "orchestrator" / "agent.py"
    if consumer.is_file():
        prompt_source = _build_prompt_source(consumer)
        if not prompt_source:
            errors.append("headless consumer 缺少 build_prompt")
        elif "§" in prompt_source:
            errors.append("headless build_prompt 仍引用易漂移的 SKILL 章节号 §")

    return errors


def main() -> int:
    _utf8_console()
    try:
        errors = validate()
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
    print("  inbox kinds, flags (both directions) and exit codes match the registries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
