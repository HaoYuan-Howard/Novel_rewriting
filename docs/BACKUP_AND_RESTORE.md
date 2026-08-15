# 备份与恢复策略

本策略把“能不能重新生成”与“丢了会不会再付钱/侵权”分开。任何备份成功提示都不算验收；验收是
从空目录恢复并逐文件校验，再运行授权样本的免费测试。

| 数据级别 | 权威备份 | 是否进代码远程 | 恢复优先级 |
|---|---|---|---|
| 源码、契约、文档、合成 fixture、`.env.example`、`web/.env` | 私有 Git 远程；离线 code snapshot 作第二份 | 是（仓必须私有） | P0 |
| 根 `.env`、API key | 操作系统/密码管理器；不进入本工具的任何归档 | 绝不 | P0，但人工重新注入 |
| 原始版权语料、`clean_text`/`main_text`、付费 LLM 产物、`_rounds`、ledger/job 账 | 明确授权的加密离线盘或私有制品库；与代码归档物理分开 | 绝不 | P0（不可公开、重建会再花钱） |
| `.llm_cache` | 默认不备份；可再生且可能含正文，只在短期同机迁移时单独加密处理 | 绝不 | P2 |
| `node_modules`、`dist`、`__pycache__`、pytest 临时物 | 不备份，由 lockfile/源码重建 | 绝不 | 不恢复 |

当前仓没有配置 Git remote，因此“私有远程”职责尚无实际落点；在提供远程 URL 前，code snapshot 是
可验证的离线副本，但同一块磁盘上的副本不算灾难恢复。禁止为了补这个缺口把仓推到公开远程。

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

私密数据快照必须显式给数据根、目标位于受控加密离线介质，并给确认 flag：

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
4. 私密数据恢复另开空目录，先离线核 manifest，再抽查一份授权书的 stage1→stage7 与 ledger；
   不把恢复目录放进公开 Git 工作树。

每季度、依赖/契约大改后，以及备份目标迁移后各演练一次。记录日期、归档 SHA-256、恢复文件数、
测试结果和失败原因；只记“备份任务成功”不算演练。

## 2026-08-16 恢复演练记录

- 从当前工作树创建 code snapshot：384 个文件、5,062,784 字节；归档 SHA-256
  `64c754b5a7d27eccafb0435e22c855df5526909c9025ae9733a6e97ac5686806`。
- 恢复目标在命令执行前不存在；工具创建空目录并完成归档内、落盘后两轮 SHA-256 核验。
- 恢复后 `.env.example`、`pyproject.toml`、`web/.env`、`web/package.json`、
  `web/package-lock.json`、`tests/fixtures/manifest.json` 存在；根 `.env`、`outputs/`、
  `Test_data/`、`.llm_cache/`、`.claude/settings.local.json` 全部不存在。
- 在恢复目录运行 `tests/test_fixture_registry.py`：8 passed。该测试同时验证 7 类合成 fixture 的固定
  字节哈希与消费分支，因此不是只看“解包命令返回 0”。
- 本次归档位于同机临时目录，只证明恢复机制与分类边界；它不冒充异地副本。待提供私有 Git remote
  与加密离线目标后，必须把相同步骤在目标介质再做一次，才具备硬盘损坏级灾难恢复能力。
