# 备份与恢复策略

本策略的当前验收范围是**代码灾备**。任何备份成功提示都不算验收；验收是把代码推到经核验的
私有 Git 远程，并从代码快照恢复到空目录、逐文件校验，再运行授权合成样本的免费测试。

2026-08-16 产品裁决：版权语料与付费输出暂不备份，不再作为 TODO-19 的验收条件。它们一旦丢失，
需要重新取得语料并重新付费生成；这是已接受的残余风险，不得把代码恢复能力宣传成数据灾备能力。

| 数据级别 | 权威备份 | 是否进代码远程 | 恢复优先级 |
|---|---|---|---|
| 源码、契约、文档、合成 fixture、`.env.example`、`web/.env` | 私有 Git 远程；code snapshot 用于恢复演练 | 是（仓必须私有） | P0 |
| 根 `.env`、API key | 操作系统/密码管理器；不进入本工具的任何归档 | 绝不 | P0，但人工重新注入 |
| 原始版权语料、`clean_text`/`main_text`、付费 LLM 产物、`_rounds`、ledger/job 账 | **当前不备份**；若将来启用，只能进明确授权的加密离线介质 | 绝不 | 已接受丢失与重付费风险 |
| `.llm_cache` | 默认不备份；可再生且可能含正文，只在短期同机迁移时单独加密处理 | 绝不 | P2 |
| `node_modules`、`dist`、`__pycache__`、pytest 临时物 | 不备份，由 lockfile/源码重建 | 绝不 | 不恢复 |

代码权威远程为 GitHub 私库 `yhzhenshuai/Novel_rewriting`；只有 `gh repo view` 核验
`visibility=PRIVATE` 且远程 `main` 与本地 `HEAD` 一致后，才算实际落地。code snapshot 是恢复机制演练，
同一块磁盘上的归档不冒充第二份异地副本。禁止为了补缺口把仓推到公开远程。

## 工具与边界

创建代码快照：

```powershell
conda run -n novel_refactor python tools/backup_snapshot.py create-code `
  --repo . --archive E:\offline-backup\novel-code.tar.gz
```

代码集合来自 `git ls-files --cached --others --exclude-standard`，因此包含当前工作树里的已跟踪修改和
未忽略的新源码；随后只放行公开配置 `.env.example`、`web/.env`，硬排除其他 `.env*`、
`settings.local.json`、`outputs/`/`Output/`、`Novel_data/`、`Test_data/`、缓存与构建目录。
两种 profile 都拒绝跟随符号链接，避免链接越出授权数据根。只靠 `.gitignore` 不足以守版权边界，
硬排除是第二道门。

### 可选的私密数据快照（不属于当前验收）

若以后重新裁决要备份私密数据，必须显式给数据根、目标位于受控加密离线介质，并给确认 flag：

```powershell
conda run -n novel_refactor python tools/backup_snapshot.py create-private `
  --source E:\authorized-output `
  --archive X:\encrypted\novel-private.tar.gz `
  --acknowledge-encrypted-offline-target
```

工具不会替磁盘加密；确认 flag 是操作者对目标介质的声明，不是加密实现。私密快照保留付费产物、
轮次和服务账，排除密钥、LLM 缓存与构建物。归档拒绝覆盖已有文件，避免把最后一份好备份静默替换。

恢复只允许空目录：

```powershell
conda run -n novel_refactor python tools/backup_snapshot.py restore `
  --archive E:\offline-backup\novel-code.tar.gz `
  --destination E:\restore-drill\novel
```

归档内含每个文件的 SHA-256/字节数。恢复先检查路径穿越、重复成员和归档哈希，落盘后再从目标目录
逐文件复核；非空目标直接拒绝。代码恢复后从密码管理器重建根 `.env`，不得从仓库或聊天记录复制 key。

## 恢复演练判据

1. 在空目录恢复 code snapshot；确认 `.env.example`、`pyproject.toml`、`web/.env`、
   `web/package.json`、`web/package-lock.json`、`tests/fixtures/manifest.json` 存在。
2. 确认根 `.env`、`outputs/`、`Test_data/`、`.llm_cache/`、`settings.local.json` 不存在。
3. 在恢复目录运行 `conda run -n novel_refactor python -m pytest -q tests/test_fixture_registry.py`；这份
   授权样本全为合成数据，并逐文件校验固定 SHA-256。

可选私密数据备份一旦启用，其恢复必须另开空目录，先离线核 manifest，再抽查一份授权书的
stage1→stage7 与 ledger；不得把恢复目录放进 Git 工作树。

每季度、依赖/契约大改后，以及备份目标迁移后各演练一次。记录日期、归档 SHA-256、恢复文件数、
测试结果和失败原因；只记“备份任务成功”不算演练。

## 2026-08-16 恢复演练记录

- 从包含本轮 TODO-14/19 实现（不含本段事后记录）的当前工作树创建 code snapshot：384 个文件、
  5,062,923 字节；归档 SHA-256
  `c303064a565ecad877d76b9bb488ae2a1c0d687052edfa53f7d1f898d2e088be`。
- 恢复目标在命令执行前不存在；工具创建空目录并完成归档内、落盘后两轮 SHA-256 核验。
- 恢复后 `.env.example`、`pyproject.toml`、`web/.env`、`web/package.json`、
  `web/package-lock.json`、`tests/fixtures/manifest.json` 存在；根 `.env`、`outputs/`、
  `Test_data/`、`.llm_cache/`、`.claude/settings.local.json` 全部不存在。
- 在恢复目录运行 `tests/test_fixture_registry.py`：6 passed。该测试同时验证 7 类合成 fixture 的固定
  字节哈希与消费分支，因此不是只看“解包命令返回 0”。
- 本次归档位于同机临时目录，只证明恢复机制与分类边界；代码的异地权威副本由私有 Git 远程承担。
  `outputs/` 与 `Test_data/` 没有进入归档或远程，按 2026-08-16 产品裁决接受其丢失风险。
