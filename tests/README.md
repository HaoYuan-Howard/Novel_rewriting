# 测试分层

默认测试必须可复现，不读取本地小说产物：

```powershell
conda run -n novel_refactor python -m pytest
```

标记为 `corpus` 的测试是针对生成产物或源语料的可选集成检查。设置 `NOVEL_OUTPUT`
（需要源文件时再设置 `NOVEL_DATA`）后显式运行：

```powershell
$env:NOVEL_OUTPUT = 'E:\path\to\current\outputs'
conda run -n novel_refactor python -m pytest -m corpus
```

语料产物必须带当前 `_schema_version`，测试不会迁移旧文件。纯判据与 prompt 测试应使用
`tests/factories.py` 中的确定性构造器，不得随机选择一本本地小说。测试若直接写注册 JSON
产物，须先用 `contracts.schema.encode_artifact()` 包装逻辑 payload。
