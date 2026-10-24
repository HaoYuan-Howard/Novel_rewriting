# Novel Rewriting — AI 小说重写（洗稿）工作流

> **⚠️ 半成品项目**：工程链路与契约体系已搭建并持续开发中，真实全书质量验证尚未完成，**请勿用于生产环境**。

输入一部中文源小说，由多阶段 AI 流水线理解原著的人物与情节，**保留情节骨架、替换全部人物姓名**，产出重写后的新书正文。

产品范围固定为**短篇小说（中文约 2 万字）**，明确不做长篇。

## 输入与输出

| | |
|---|---|
| 输入 | 一部中文小说 `.txt`（源文本） |
| 输出 | `stage7/final/{书名}_rewriting.txt`（重写后的成书） |

## 工作方式

本项目不把整本书一次性丢给大模型重写，而是拆成一条**理解 → 建模 → 设计 → 写作 → 交付**的流水线：先由代码和模型分层抽取原著的人物关系、情节结构、悬念与认知边界，形成机器可校验的中间产物，再按新姓名体系逐章重新设计并写作。每一步的产物都有显式契约（schema）和确定性校验，失败可定位到具体 stage / node / item 并支持断点恢复。

```
源小说 txt
   │
   ▼
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
│ Stage0  │ → │ Stage1  │ → │ Stage2  │ → │ Stage4  │ → │ Stage5  │ → │ Stage7  │
│ 摄入清洗 │   │ 结构分段 │   │ 理解建模 │   │ 剧本设计 │   │ 正文写作 │   │ 终稿交付 │
└─────────┘   └─────────┘   └─────────┘   └─────────┘   └─────────┘   └─────────┘
   文本规整      章节/拍切分     人物·情节·姓名映射    Design·IR·Hook   逐章渲染正文   组装成书
```

（Stage3 / Stage6 已退役，不在现役链路中。）

各阶段职责：

- **Stage0 摄入清洗**：源文本规整、噪声清除与修订留痕。
- **Stage1 结构分段**：切分章节与叙事单元（hook / beat 等），产出结构化主文本。
- **Stage2 理解建模**：抽取人物实体与身份绑定、情节蓝图、悬念与认知边界，建立**源姓名 → 目标姓名的 NameMap**。
- **Stage4 剧本设计**：把蓝图折叠为逐拍设计（Design / IR / Hook 材料），套用目标姓名体系，Guard 机械校验。
- **Stage5 正文写作**：按目标姓名与写手协议逐章渲染新正文，正文级硬闸校验。
- **Stage7 终稿交付**：按阅读序组装成书，输出最终文件。

## 架构设计原则

- **契约先行**：`contracts/` 是唯一契约真相源（pydantic v2 schema + 判据 + 版本登记），运行时单向消费契约。
- **确定性归代码，语义归模型**：compiler 只做可机械推导的确定性转换（ID、引用、统计、路径、hash）；语义判断与生成全部由 LLM 节点承担，代码不从自然语言猜补语义。
- **单一写者与单一真相源**：每类正式产物只有一个 canonical schema 和唯一 writer；产物 I/O 只经 repository（`novel/store.py`），服务只写 `_service/`。
- **付费显式授权**：真实模型调用需要 `--yes` 与 `--allow-egress` 两项授权；`--check-only` / `--dry-run` 零成本。已购回答按请求身份免费重验 / 重放，成本账以 `_service/ledger.jsonl` 为权威。
- **可恢复**：状态机显式，相同正式输入与配置产生语义等价的确定性输出；局部失败定位到 node / item，重跑不重复业务实体。

## 快速开始

环境要求：Windows、conda、Python ≥ 3.10（Node 用于前端）。

```powershell
# 1. 创建后端环境（固定命名 novel_refactor，不用 base）
conda create -n novel_refactor python=3.12 -y
conda run -n novel_refactor pip install -e ".[llm,api,dev]"

# 2. 配置模型密钥（复制 .env.example 为 .env 后填写；不配 key 也能跑 0 成本检查链路）
copy .env.example .env
#    .env: DEEPSEEK_API_KEY=sk-your-key-here

# 3. 启动审校台（双击 scripts\start.cmd 亦可）
#    按需构建前端、启动服务并打开浏览器，默认 http://127.0.0.1:8765/
scripts\start.cmd

# 4. 命令行用法
conda run -n novel_refactor python -X utf8 -u -m novel.cli --help
conda run -n novel_refactor python -m novel.api --base <Output目录>
```

前端为只读监控界面（上传即自动开跑，Stage 页只读监控，无人工审批闸）。

## 运行测试

```powershell
conda run -n novel_refactor python -m pytest            # 完整非 corpus 回归
conda run -n novel_refactor python -m tests.golden       # golden set 成书质量（需显式提供语料）
```

普通测试不调用真实线上 LLM（0 成本链路走 FakeTransport）。

## 技术栈

- **后端**：Python ≥ 3.10 · pydantic v2（契约）· FastAPI / uvicorn（API）· DeepSeek（openai 兼容 SDK，懒加载）
- **前端**：React · TypeScript · Vite
- **测试**：pytest（含契约 / 分层 / 双向覆盖率 / golden set）

## 仓库结构

```
contracts/      契约真相源：schema、判据、不变量、版本登记
novel/
  stages/       现役 Stage0/1/2/4/5/7 实现（每阶段本地指南见其 AGENTS.md）
  orchestrator/ 编排与许可：AgentRun、执行许可、预算、恢复
  llm/          模型客户端、缓存、成本账、响应修复
  store.py      产物持久化（唯一 I/O 边界）
  api/          HTTP 服务
  views/        只读投影面
web/            审校台前端（React + Vite）
docs/           产品契约（BACKEND_CONTRACT）、架构规格（schema/）、运维手册（OPERATIONS）、历史裁决（DECISIONS）
tests/          后端测试与 fixture
scripts/        启动 / 重启入口
```

## 项目现状与已知限制

- 工程链路、契约体系、0 成本检查链与各 Stage 测试已就位；Stage2 修复批次工程部分完成。
- **真实全书验证未完成**：原书真实语料的 Stage2 新根验证与成书质量核读仍在进行中，见 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 活动计划。
- 历史破坏性契约升级后，旧输出根只读归档、不提供跨版本续跑；已购证据保留原字节。
- 需要真实模型调用与正文外发时，分别显式授权并自行承担费用。

## 文档导航

- [AGENTS.md](AGENTS.md)：开发规则与架构不变量（I-\* / S-\*）
- [docs/BACKEND_CONTRACT.md](docs/BACKEND_CONTRACT.md)：产品行为与守卫（R 编号）
- [docs/schema/str06.md](docs/schema/str06.md)：终态架构契约
- [docs/OPERATIONS.md](docs/OPERATIONS.md)：环境、启动、测试与工程恢复
- [docs/DECISIONS.md](docs/DECISIONS.md)：历史裁决记录

## 用途声明

本项目处理使用者自行提供的源文本，产出物仅供学习与研究使用。
