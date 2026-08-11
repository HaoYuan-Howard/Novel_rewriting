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
from pathlib import Path
from typing import Mapping

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 模型 id。**锁定 `deepseek-v4-pro`**——盘上 3 本产物的 `_provenance.model` 恒是它，
#: 而换模型是一次产品裁决（成稿风格会整体变），不是一个可以随手改的环境变量。
#: ⚠ 允许用 `DEEPSEEK_MODEL` 覆盖，是为了 A/B 时**显式**换一次；
#: 换了之后 `_provenance.model` 会如实记下来——那正是它存在的理由。
DEFAULT_MODEL = 'deepseek-v4-pro'
DEFAULT_BASE_URL = 'https://api.deepseek.com'

#: 缓存目录。`.gitignore` 已经盖住它（连同 `Output/` / `Novel_data/` / `.env`）。
DEFAULT_CACHE_DIRNAME = '.llm_cache'

#: 单次请求超时（秒）。**推理模型一次全书级抽取是分钟量级**，
#: 默认 60 秒那种值会把一次正常的付费调用判成超时并重试——**那是在重复花钱**。
DEFAULT_TIMEOUT = 600.0

#: 传输层重试次数（429 / 5xx / 超时）。⚠ **这个数是拍的，不是量的**：
#: 手上没有真实的 429 分布（`Q-10` 的「重试退避调参」已登记为「现在不建」）。
#: 它与**降级阶梯是两件事**——混成一条会让一次网络抖动消耗掉整条阶梯。
DEFAULT_MAX_RETRIES = 3

#: 退避基数（秒），指数退避 `base * 2**n`。⚠ 同上，拍的。
DEFAULT_BACKOFF_BASE = 2.0

#: 并发闸。⚠ **拍的**，取 2 是因为本项目是**批量**服务而不是交互服务：
#: 并发越高，一次配置错误（比如 prompt 漂了）在被人看见之前烧掉的钱越多。
#: 提高它之前先问「这一批的单价是多少」。
DEFAULT_CONCURRENCY = 2


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
    base_url: str = DEFAULT_BASE_URL
    cache_dir: Path = _REPO_ROOT / DEFAULT_CACHE_DIRNAME
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    concurrency: int = DEFAULT_CONCURRENCY

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def __repr__(self) -> str:
        """**结构上不可能泄露密钥。** 只说设了没设。"""
        return (f'Config(api_key=<{"set" if self.has_key else "unset"}>, '
                f'model={self.model!r}, base_url={self.base_url!r}, '
                f'cache_dir={str(self.cache_dir)!r}, timeout={self.timeout}, '
                f'max_retries={self.max_retries}, concurrency={self.concurrency})')

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

    def _num(key: str, default: float, cast) -> float:
        raw = src.get(key)
        if raw is None or not str(raw).strip():
            return default
        try:
            return cast(raw)
        except ValueError:
            # ⚠ **不静默兜底成默认值**：写错一个数字比不写更危险
            #   （`DEEPSEEK_TIMEOUT=6O` 里那是个字母 O），而兜底会让它一路跑到超时重试。
            raise ValueError(f'{key}={raw!r} 不是合法的数字') from None

    cache = src.get('NOVEL_CACHE_DIR')
    return Config(
        api_key=src.get('DEEPSEEK_API_KEY') or None,
        model=src.get('DEEPSEEK_MODEL') or DEFAULT_MODEL,
        base_url=src.get('DEEPSEEK_BASE_URL') or DEFAULT_BASE_URL,
        cache_dir=Path(cache) if cache else _REPO_ROOT / DEFAULT_CACHE_DIRNAME,
        timeout=_num('DEEPSEEK_TIMEOUT', DEFAULT_TIMEOUT, float),
        max_retries=int(_num('DEEPSEEK_MAX_RETRIES', DEFAULT_MAX_RETRIES, int)),
        backoff_base=_num('DEEPSEEK_BACKOFF_BASE', DEFAULT_BACKOFF_BASE, float),
        concurrency=int(_num('NOVEL_CONCURRENCY', DEFAULT_CONCURRENCY, int)),
    )
