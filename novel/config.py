#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py —— 运行期配置。**不在 import 期跑，永不打日志。**

【为什么这两条都是硬要求】

  ① **不在 import 期跑。** 原型的 `_load_dotenv()` 是模块级单例，在 `import llm_client`
     那一刻就执行了。后果有两个：配置从哪来**取决于谁先 import 谁**（测试尤其难办），
     以及它**改了 `os.environ`**——一个 import 副作用改进程全局状态。
     那是原型不得不把每个节点跑成子进程的五条理由之一（`R-11.3` 的理由改写，已登记退役）。
     ⇒ 这里 `load_config()` 是个普通函数，**不改 `os.environ`**，读进来就用。

  ② **永不打日志。** 这不是靠纪律，是靠形状：`api_key` 的 `repr` 是关掉的，
     `Config.__repr__` 只说「设了没设」。一次 `print(cfg)` 或一条异常里带上 `cfg`
     就足以把密钥写进日志、写进作业记录、写进界面——而那三处都不是给密钥待的地方。
     配一条测试逐字断言 `repr` / `str` 里找不到密钥。

【默认值都在这里，别在调用点再写一份】
⚠ 重试与并发那两个数**没有标定过**（`Q-10` 的反面：不知道就该说不知道）。
`docs/schema/_README.md` 那条纪律同样适用——**「拍的」与「量的」必须分得开**，
所以它们各带一句注释说明自己是拍的。真实 429 数据到手之后再调。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from contracts.invariants import (
    BLUEPRINT_ARCHITECTURE, BLUEPRINT_ARCHITECTURES, PIPELINE_PARALLEL_NODES,
    PIPELINE_DEFAULT_CONCURRENCY, PIPELINE_CONCURRENCY_TRIALS,
)
from contracts.schema.orchestration import PipelineConcurrency

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 模型 id（生成侧默认）。2026-08-19/20 用户裁决：开发期成本不可接受
#: （pro 约 9 元/本、flash 约 3 元/本）⇒ **生成侧默认 `deepseek-v4-flash`**。
#: **两个节点例外，钉死 pro**：`stage6.semantic`（判官给不出有区分度的证据，
#: 2026-08-19/20）与 `stage4.plan`（重采粒度：全书一次调用，一次空正文重采就是
#: 整本书，2026-08-22）。两格都在 `contracts/invariants.py::NODES`
#: （`NodeSpec.model` 优先于本默认与环境变量），换模型是一次产品裁决
#: （成稿风格会整体变），不是一个可以随手改的环境变量。
#: ⚠ 允许用 `DEEPSEEK_MODEL` 覆盖，是为了 A/B 时**显式**换一次（对照臂、诊断跑）；
#: 换了之后 `_provenance.model` 记主模型配置，`adopted_models` 记最终真正进入
#: 产物的 Flash / Pro 响应集合；两格分开，模型兜底才不会把 Pro 产物标成 Flash。
DEFAULT_MODEL = 'deepseek-v4-flash'
DEFAULT_BASE_URL = 'https://api.deepseek.com'

#: 思考模式。`on` = 单档 `('high', True)`；模型异常不会自动降低 effort，
#: Flash 恢复链改由 Pro 保持同配置兜底；
#: `off` = 单档关思考（`novel/llm/client.py::NOTHINK_RUNGS`）。
#: ⚠ 与 `DEEPSEEK_MODEL` 同一条纪律：这是给 A/B 显式拧一次的口，
#: 不是随手改的环境变量——账本每行的 `reasoning_effort`/`thinking` 列会如实记下来。
DEFAULT_THINKING_MODE = 'on'
THINKING_MODES = ('on', 'off')

#: `stage2.blueprint` 的响应契约（架构）。默认值只认
#: `contracts/invariants.py::BLUEPRINT_ARCHITECTURE`；其他登记臂经
#: `NOVEL_STAGE2_BLUEPRINT_CONTRACT` **显式**选择——
#: 与 `DEEPSEEK_MODEL` 同一条纪律：这是给 A/B 拧一次的口，不是随手改的环境变量，
#: 产物 `_provenance.context.architecture` 会如实记下来。A/B 达标后翻的是
#: 这里的默认（`contracts/invariants.py::BLUEPRINT_ARCHITECTURE`），不是取值面。
#: ⚠ 名字带 `BLUEPRINT`：global 将来可能有自己的契约版本，不占它的命名空间。
DEFAULT_STAGE2_BLUEPRINT_CONTRACT = BLUEPRINT_ARCHITECTURE

#: 已退役的 global provider-wire 选臂口。它刻意不进 ``ENV_KEYS``、不进
#: ``Config``：这里只留下关闭式 tombstone，避免旧部署显式配置 locator-v1 后
#: 悄悄改跑唯一现役 candidate wire。
RETIRED_STAGE2_GLOBAL_EVIDENCE_ENV = 'NOVEL_STAGE2_GLOBAL_EVIDENCE_CONTRACT'


def reject_retired_global_evidence_selector(
    env: Mapping[str, str] | None = None,
) -> None:
    """旧 global 选臂变量只作 tombstone；任何 provider 入口都必须显式拒绝。"""
    source = dict(env if env is not None else os.environ)
    if env is None:
        path = _REPO_ROOT / '.env'
        if path.is_file():
            for key, value in parse_dotenv(
                    path.read_text(encoding='utf-8')).items():
                source.setdefault(key, value)
    if RETIRED_STAGE2_GLOBAL_EVIDENCE_ENV in source:
        raise ValueError(
            f'{RETIRED_STAGE2_GLOBAL_EVIDENCE_ENV} 选臂已退役；'
            'stage2.global 现只接受 paragraph-candidate-v1，请移除该旧变量')

#: 缓存目录。`.gitignore` 已经盖住它（连同 `Output/` / `Novel_data/` / `.env`）。
DEFAULT_CACHE_DIRNAME = '.llm_cache'

#: 单次请求超时（秒）。**推理模型一次全书级抽取是分钟量级**，
#: 默认 60 秒那种值会把一次正常的付费调用判成超时并重试——**那是在重复花钱**。
DEFAULT_TIMEOUT = 600.0

#: 传输层重试次数（429 / 5xx / 超时）。⚠ **这个数是拍的，不是量的**：
#: 手上没有真实的 429 分布（`Q-10` 的「重试退避调参」已登记为「现在不建」）。
#: 它与**同档重采 / 模型兜底是三件事**——混成一条会让一次网络抖动消耗内容恢复预算。
DEFAULT_MAX_RETRIES = 3

#: 退避基数（秒），指数退避 `base * 2**n`。⚠ 同上，拍的。
DEFAULT_BACKOFF_BASE = 2.0

#: 逐章 LLM 并发闸。2026-08-13 用户裁决：初始抽取与写作都按章并发，硬上限 32。
#: 默认四并发；上限只保留给显式 legacy 配置，Agent 逐节点对照另受 8 的上限。
MAX_CONCURRENCY = 32
DEFAULT_CONCURRENCY = PIPELINE_DEFAULT_CONCURRENCY

#: 字段 → 喂它的那个环境变量。**唯一落点**：`load_config` 与出错文案共用这一张表，
#: 免得报错说的变量名与真正读的那个分了家（那种错最费时间：人照着改，而它一直没生效）。
#: ⚠ 必须是 dict **字面量**：模块顶层不许有函数调用（`load_config` 不在 import 期跑）。
ENV_KEYS: dict[str, str] = {
    'api_key': 'DEEPSEEK_API_KEY',
    'model': 'DEEPSEEK_MODEL',
    'thinking_mode': 'DEEPSEEK_THINKING',
    'base_url': 'DEEPSEEK_BASE_URL',
    'proxy_url': 'DEEPSEEK_PROXY',
    'cache_dir': 'NOVEL_CACHE_DIR',
    'timeout': 'DEEPSEEK_TIMEOUT',
    'max_retries': 'DEEPSEEK_MAX_RETRIES',
    'backoff_base': 'DEEPSEEK_BACKOFF_BASE',
    'concurrency': 'NOVEL_CONCURRENCY',
    'concurrency_policy': 'NOVEL_CONCURRENCY_POLICY',
    'stage2_blueprint_contract': 'NOVEL_STAGE2_BLUEPRINT_CONTRACT',
}


@dataclass(frozen=True)
class Config:
    """一次跑的运行期配置。

    ⚠ **`api_key` 的 `repr` 是关掉的**，且本类自带 `__repr__`。
    密钥泄漏从来不是「有人故意打印它」，是一条异常消息里带上了整个 config 对象。
    """

    #: `None` = 没设。**0 成本路径（`FREE_ENTRIES` 里那 12 个 flag）在这种情况下必须跑得完**
    #: ——那是一条测试（原型靠逐个人工核对保证）。
    api_key: str | None = field(default=None, repr=False)

    model: str = DEFAULT_MODEL
    thinking_mode: str = DEFAULT_THINKING_MODE
    base_url: str = DEFAULT_BASE_URL
    #: 显式代理只属于 DeepSeek 传输，不灌进进程环境，也不影响本地 API / headless agent。
    #: `repr=False` 与 `api_key` 同理：代理 URL 可能含账号口令。
    proxy_url: str | None = field(default=None, repr=False)
    cache_dir: Path = _REPO_ROOT / DEFAULT_CACHE_DIRNAME
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    concurrency: int = DEFAULT_CONCURRENCY
    concurrency_policy: PipelineConcurrency = field(default_factory=PipelineConcurrency)
    stage2_blueprint_contract: str = DEFAULT_STAGE2_BLUEPRINT_CONTRACT

    def concurrency_for(self, node: str) -> int:
        """只覆盖目标节点；对照实验不能顺带提升其他阶段。"""
        if node in self.concurrency_policy.node_overrides:
            return self.concurrency_policy.node_overrides[node]
        return (min(self.concurrency, max(PIPELINE_CONCURRENCY_TRIALS))
                if node in PIPELINE_PARALLEL_NODES else self.concurrency)

    def __post_init__(self) -> None:
        """取值面的闸。**放这里不放 `load_config`**，于是手搭一个 `Config` 也拦得住。

        ⚠ `load_config._num` 只查「能不能转成数字」，不查范围：
        `DEEPSEEK_MAX_RETRIES=-1`（一次都不发，而调用方以为发过了）·
        `NOVEL_CONCURRENCY=0`（信号量取 0，整跑当场死锁；今天靠 `max(1, …)` 兜着，
        而那是在**另一个文件**里替配置擦屁股）· `DEEPSEEK_TIMEOUT=-5` / `nan` / `inf`
        （httpx 收到就炸，或者永不超时）——全都能一路走到发请求那一刻才发作，
        而那时的报错说的是别的事。

        ⚠ **抛 `ValueError`，不新造一个 `errors.py` 的类**：这是**构造期**的取值面问题，
        与 `_num` / `RunContext.__post_init__` / `CachePolicy.__post_init__` /
        `ask_json` 的 require 检查同一个既定习惯。`errors.py` 里那一族答的是
        「这一次跑该以什么终态停下」，两件事。别顺手「升级」它。
        """
        def bad(field_name: str, why: str) -> ValueError:
            return ValueError(
                f'{ENV_KEYS[field_name]}（{field_name}）={getattr(self, field_name)!r}：{why}')
        if not isinstance(self.concurrency_policy, PipelineConcurrency):
            raise bad('concurrency_policy', '必须是已校验的 PipelineConcurrency')

        if not isfinite(self.timeout) or self.timeout <= 0:
            raise bad('timeout', '超时必须是一个有限的正数（秒）')
        if self.max_retries < 0:
            raise bad('max_retries', '重试次数不能是负的——负数会让「首发 + N 次重试」一次都不发')
        if not isfinite(self.backoff_base) or self.backoff_base < 0:
            raise bad('backoff_base', '退避基数必须是一个有限的非负数（秒）')
        if self.concurrency < 1:
            raise bad('concurrency', '并发闸至少是 1——取 0 的话整跑在信号量上死锁')
        if self.concurrency > MAX_CONCURRENCY:
            raise bad('concurrency', f'逐章并发硬上限是 {MAX_CONCURRENCY}')
        if not self.model:
            raise bad('model', '模型 id 不能是空的（`_provenance.model` 靠它记录主模型配置）')
        if self.thinking_mode not in THINKING_MODES:
            raise bad('thinking_mode',
                      f'思考模式只有 {THINKING_MODES}（大小写敏感）——静默兜底会把一次 A/B '
                      '跑成另一个臂，而账本里看不出来是拼写错了')
        if not self.base_url:
            raise bad('base_url', 'base_url 不能是空的')
        if self.proxy_url:
            parsed = urlsplit(self.proxy_url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                # 不回显原值：代理 URL 可能内嵌账号口令。
                raise ValueError(
                    f'{ENV_KEYS["proxy_url"]}（proxy_url）必须是合法的 http(s) 代理 URL')
        if self.stage2_blueprint_contract not in BLUEPRINT_ARCHITECTURES:
            raise bad('stage2_blueprint_contract',
                      f'stage2.blueprint 的契约只有 {BLUEPRINT_ARCHITECTURES}'
                      '（大小写敏感）——静默兜底会把一次 A/B 跑成另一个臂，'
                      '而账本里看不出来是拼写错了')

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def __repr__(self) -> str:
        """**结构上不可能泄露密钥。** 只说设了没设。"""
        return (f'Config(api_key=<{"set" if self.has_key else "unset"}>, '
                 f'model={self.model!r}, thinking_mode={self.thinking_mode!r}, '
                 f'base_url={self.base_url!r}, '
                 f'proxy_url=<{"set" if self.proxy_url else "unset"}>, '
                 f'cache_dir={str(self.cache_dir)!r}, timeout={self.timeout}, '
                f'max_retries={self.max_retries}, concurrency={self.concurrency}, '
                f'stage2_blueprint_contract={self.stage2_blueprint_contract!r})')

    __str__ = __repr__


def parse_dotenv(text: str) -> dict[str, str]:
    """极简 `.env` 解析：`KEY=VALUE`，`#` 开头是注释，两端引号剥掉。

    ⚠ **不引 `python-dotenv`。** 契约层与骨架的依赖越少越好，而这段逻辑一共十行、
    且它的失败方式（读不出某个键）会被 `has_key` 立刻暴露出来。
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_config(*, env: Mapping[str, str] | None = None,
                dotenv_path: Path | None = None) -> Config:
    """读环境变量 + `.env`。**真环境变量优先。**

    ⚠ **不改 `os.environ`。** 原型用 `os.environ.setdefault` 把 `.env` 灌进进程全局，
    于是「这次跑用的是哪个 key」取决于此前有没有别的模块先 import 过它。
    这里读进来就用，进程全局一个字节都不动 ⇒ 同进程跑两次不同配置是可能的，
    而且不会互相污染（同 `RunContext` 的那条纪律）。

    ⚠ 找不到 `.env` **不是错误**：0 成本路径本来就不需要密钥。
    真正需要它的时候由 `novel/llm/transport.py` 报出显式终态，
    那时的报错能说清「哪个节点、要跑什么、缺什么」——比在这里早报要有用得多。
    """
    src = dict(env if env is not None else os.environ)
    path = dotenv_path if dotenv_path is not None else _REPO_ROOT / '.env'
    if path.is_file():
        # 真环境变量优先：`.env` 只补没有的那些。
        for k, v in parse_dotenv(path.read_text(encoding='utf-8')).items():
            src.setdefault(k, v)

    reject_retired_global_evidence_selector(src)

    def _num(field_name: str, default: float, cast) -> float:
        key = ENV_KEYS[field_name]
        raw = src.get(key)
        if raw is None or not str(raw).strip():
            return default
        try:
            return cast(raw)
        except ValueError:
            # ⚠ **不静默兜底成默认值**：写错一个数字比不写更危险
            #   （`DEEPSEEK_TIMEOUT=6O` 里那是个字母 O），而兜底会让它一路跑到超时重试。
            #   ⚠ 范围/有限性那一层的闸在 `Config.__post_init__`——放那里，
            #     手搭一个 `Config` 也拦得住。
            raise ValueError(f'{key}={raw!r} 不是合法的数字') from None

    cache = src.get(ENV_KEYS['cache_dir'])
    return Config(
        api_key=src.get(ENV_KEYS['api_key']) or None,
        model=src.get(ENV_KEYS['model']) or DEFAULT_MODEL,
        thinking_mode=src.get(ENV_KEYS['thinking_mode']) or DEFAULT_THINKING_MODE,
        base_url=src.get(ENV_KEYS['base_url']) or DEFAULT_BASE_URL,
        proxy_url=src.get(ENV_KEYS['proxy_url']) or None,
        cache_dir=Path(cache) if cache else _REPO_ROOT / DEFAULT_CACHE_DIRNAME,
        timeout=_num('timeout', DEFAULT_TIMEOUT, float),
        max_retries=int(_num('max_retries', DEFAULT_MAX_RETRIES, int)),
        backoff_base=_num('backoff_base', DEFAULT_BACKOFF_BASE, float),
        concurrency=int(_num('concurrency', DEFAULT_CONCURRENCY, int)),
        concurrency_policy=(PipelineConcurrency.model_validate_json(src[ENV_KEYS['concurrency_policy']])
            if ENV_KEYS['concurrency_policy'] in src else PipelineConcurrency()),
        stage2_blueprint_contract=(
            src.get(ENV_KEYS['stage2_blueprint_contract'])
            or DEFAULT_STAGE2_BLUEPRINT_CONTRACT),
    )
