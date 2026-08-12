#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proto_prompts.py —— 原型模块级常量的**受限求值器**。两个测试文件共用这一份。

【为什么要求值，不能直接 import 原型模块】原型那些 SYS 不是字符串字面量，
是模块导入时 `一段三引号字面量 + fill(...) + '\\n'.join([...])` 拼出来的
⇒ `ast.literal_eval` 取不到。而 `import` 会拉进网络客户端与盘上路径
（`Stage5/n5_write.py` 一路带出 3216 行的 `stage5_common`）。
⇒ 在 AST 上算：只认常量 / 名字 / 字符串加法 / `join` / `fill` / 生成式，别的当场炸。

⚠⚠ **求值只用原型侧的值**（`INVENT_*` / `STYLE_WORDS` / `LAYER_*` / `RULES_V2` 都从
原型源码取），不碰本仓的 `contracts.invariants`——否则这些测试就变成拿本仓跟本仓自己比，恒绿。

【为什么抽成一份共用的】F4-5 时它只长在 `tests/test_stage5_prompts.py` 里；
F5-3（stage2 三条臂）是第二个消费方。照 `tests/legacy_names.py` 的先例
——那份的 docstring 记着「各抄一份的分家表现是**贵贱对账假绿**」：
一个 70 行的 AST 求值器复制两份，改对一边不会有任何东西提醒你另一边没跟上。

⚠⚠ **`files` 那个顺序是语义的一部分：先出现的赢。** 同名的模块级常量只取第一份，
而**原型里真的有同名并存**：`_legacy/n2_semantic.py` 的 `BLUEPRINT_SYS` / `BLUEPRINT_HINT`
与现役 `Stage2/n2_blueprint.py` 的**同名**（旧世代拼的是 `RULES` v1 不是 `RULES_V2`）。
⇒ **`_legacy/` 一律不许出现在 `files` 里**，而调用方还要另配一条断言
把「拿到的确实是现役那一份」钉住——只靠这条注释是不够的（判据 25）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from string import Template
from typing import Callable

import pytest

#: 原型仓。⚠ 按**相对路径**找兄弟目录，与 `tests/corpus.py` 同一条纪律。
PROTO = Path(__file__).resolve().parent.parent.parent / 'Novel_refactoring' / 'Rapid_Prototyping'


class Proto:
    """原型模块级常量的受限求值器。认不出的语法当场炸，不猜。"""

    def __init__(self, assigns: dict[str, ast.expr]) -> None:
        self.a = assigns

    def bind(self, target: ast.expr, item: object) -> None:
        if isinstance(target, ast.Name):
            self.a[target.id] = ast.Constant(value=item)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for sub, val in zip(target.elts, item):        # type: ignore[call-overload]
                self.bind(sub, val)
        else:
            raise TypeError(f'认不出的循环变量：{ast.dump(target)[:80]}')

    def ev(self, node: ast.expr):                          # noqa: C901 —— 一条分支一种语法
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.ev(self.a[node.id])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.ev(node.left) + self.ev(node.right)
        if isinstance(node, (ast.Tuple, ast.List)):
            return [self.ev(e) for e in node.elts]
        if isinstance(node, ast.Dict):
            return {self.ev(k): self.ev(v) for k, v in zip(node.keys, node.values)}
        if isinstance(node, ast.Subscript):
            return self.ev(node.value)[self.ev(node.slice)]
        if isinstance(node, ast.JoinedStr):
            out = ''
            for v in node.values:
                if isinstance(v, ast.Constant):
                    out += v.value
                else:
                    out += str(self.ev(v.value))           # type: ignore[attr-defined]
            return out
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.DictComp)):
            gen = node.generators[0]
            src, out = self.ev(gen.iter), ([], {})[isinstance(node, ast.DictComp)]
            for item in src:                               # type: ignore[union-attr]
                self.bind(gen.target, item)
                if isinstance(node, ast.DictComp):
                    out[self.ev(node.key)] = self.ev(node.value)     # type: ignore[index]
                else:
                    out.append(self.ev(node.elt))          # type: ignore[union-attr]
            return out
        if isinstance(node, ast.Call):
            return self._call(node)
        raise TypeError(f'受限求值器认不出：{ast.dump(node)[:120]}')

    def _call(self, node: ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == 'fill':
            # 原型的 `fill` 就是 `string.Template.substitute`（`Stage2/stage2_common.py:29`）。
            return Template(self.ev(node.args[0])).substitute(
                **{k.arg: self.ev(k.value) for k in node.keywords})     # type: ignore[arg-type]
        if isinstance(fn, ast.Name) and fn.id == '_hinge_heading':
            k = self.ev(node.args[0])
            return self.ev(self.a['HINGE_LABEL_PARTS'])[k][0].replace('【', '').replace('】', '')
        if isinstance(fn, ast.Attribute):
            recv = self.ev(fn.value)
            if fn.attr == 'join':
                return recv.join(self.ev(node.args[0]))
            if fn.attr == 'items':
                return list(recv.items())
            if fn.attr == 'replace':
                return recv.replace(self.ev(node.args[0]), self.ev(node.args[1]))
        raise TypeError(f'受限求值器不认这个调用：{ast.dump(node)[:120]}')


def assigns_of(files: tuple[str, ...]) -> dict[str, ast.expr] | None:
    """扫这几份原型文件的模块级赋值。⚠ **先出现的赢**（见模块 docstring）。
    原型仓不在旁边 ⇒ `None`。
    """
    paths = [PROTO / rel for rel in files]
    if not all(p.exists() for p in paths):
        return None
    assigns: dict[str, ast.expr] = {}
    for p in paths:
        for node in ast.parse(p.read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id not in assigns:
                        assigns[t.id] = node.value
    return assigns


def wanter(files: tuple[str, ...]) -> Callable[[str], object]:
    """返回一个 `want(name)`：算出那个常量**渲染之后**的值。

    ⚠ 原型仓不在旁边时 **`pytest.skip` 并写明「没在查」**，绝不静默产出一份
    「所有断言都通过」的结果——那种结果与真跑过的长得一模一样（`Q-2`）。
    """
    def want(name: str) -> object:
        assigns = assigns_of(files)
        if assigns is None:
            pytest.skip(f'原型仓不在 {PROTO} —— 本条**没在查**，不是「查过一致」')
        return Proto(assigns).ev(assigns[name])
    return want
