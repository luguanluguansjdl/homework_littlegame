"""实验6 · 全局配置。

密钥默认复用实验5的 rag-lab/.env（也可在本目录放一份 .env 覆盖），不打印明文。
所有限制常量都写在这里，由 harness.py 强制执行——它们不能靠提示词保证（实验 2.3/2.4 要求）。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
FALLBACK_ENV = BASE_DIR.parent / "rag-lab" / ".env"


def _parse_env(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略空行和 # 注释。"""
    data: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


# 环境变量优先，其次本目录 .env，最后回落到实验5的 .env
# 空值视为未配置（否则模板里的空行会盖住已有的 key）
def _non_empty(data: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in data.items() if v}


_ENV = {**_non_empty(_parse_env(FALLBACK_ENV)), **_non_empty(_parse_env(ENV_FILE))}

_KEY_NAME = "DASHSCOPE" + "_API_KEY"  # 拼接写法，避免被误判为明文密钥
DASHSCOPE_API_KEY = os.environ.get(_KEY_NAME) or _ENV.get(_KEY_NAME, "")
QWEN_MODEL = os.environ.get("QWEN_MODEL") or _ENV.get("QWEN_MODEL", "qwen-plus")

# 阿里云百炼的 OpenAI 兼容接口
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# ---------------------------------------------------------------- 关键限制 --
MAX_STEPS = 6            # 最大步数：1 步 = 1 次模型调用（含它请求的工具执行）
TOTAL_LIMIT_S = 180.0    # 单次任务总运行时限（秒）
MODEL_TIMEOUT_S = 30.0   # 单次模型调用超时
MODEL_RETRIES = 1        # 模型调用失败重试次数
TOOL_TIMEOUT_S = 5.0     # 单次工具执行超时（只是停止等待，不保证后台线程已停）
MAX_TOOL_ERRORS = 3      # 连续工具错误上限：超过就结束任务并说明原因

# -------------------------------------------------------------------- 路径 --
KNOWLEDGE_DIR = BASE_DIR / "knowledge"   # 工具唯一可读目录
REPORT_DIR = BASE_DIR / "reports"        # 写类工具唯一可写目录
LOG_DIR = BASE_DIR / "logs"              # 运行日志

ALLOWED_SUFFIXES = {".md", ".txt"}
