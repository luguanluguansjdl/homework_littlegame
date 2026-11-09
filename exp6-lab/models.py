"""模型接入层（实验 2.3）。

两种模型：
- RealModel：通过阿里云百炼 OpenAI 兼容接口调用通义千问，使用标准 function calling
  （assistant.tool_calls → 程序执行工具 → role=tool 结果回填 → 再请求模型）；
- MockModel：按脚本给出"工具调用/最终回答"，用于断网演示和边界测试。
  真实模型只在正常流程里用，异常/边界用脚本触发，报告里要写清楚。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import config


class ModelError(Exception):
    """模型调用失败（断网、超时、密钥错误、接口 4xx/5xx）。"""


@dataclass
class ToolCall:
    id: str
    name: str
    args: Any          # 已解析的 dict；解析失败时保留 raw 文本
    raw: str = ""


@dataclass
class AssistantMessage:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict:
        """转成可以塞回 messages 的 assistant 消息（保持工具调用链完整）。"""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw or json.dumps(tc.args, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


class RealModel:
    """真实模型。model.chat(messages, tool_schemas) -> AssistantMessage。"""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        if not config.DASHSCOPE_API_KEY:
            raise ModelError("未找到 DASHSCOPE_API_KEY，请在 .env 或环境变量里配置")
        from openai import OpenAI

        self.model = model or config.QWEN_MODEL
        self._client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=base_url or config.BASE_URL,
            timeout=config.MODEL_TIMEOUT_S,
            max_retries=0,  # 重试由 harness 统一控制，便于记录次数
        )

    def chat(self, messages: list[dict], tool_schemas: list[dict]) -> AssistantMessage:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tool_schemas,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc

        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for i, tc in enumerate(msg.tool_calls or []):
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = raw  # 交给参数校验层去报 bad_args，保持链路可解释
            calls.append(
                ToolCall(id=tc.id or f"call_{i}", name=tc.function.name, args=args, raw=raw)
            )
        return AssistantMessage(content=msg.content, tool_calls=calls)


class MockModel:
    """脚本化模型：script 里每项是 {"tool": 名称, "args": {...}} 或 {"final": "文本"}。

    用途：
    1. 断网时也能完整演示 Agent 循环与 Harness 行为；
    2. 稳定触发参数错误、工具异常、步数超限、拒绝确认等边界情况。
    """

    def __init__(self, script: list[dict], final: str = "（脚本执行完毕，未再请求工具）"):
        self.script = list(script)
        self.final = final
        self.calls: list[list[dict]] = []
        self._index = 0

    def chat(self, messages: list[dict], tool_schemas: list[dict]) -> AssistantMessage:
        self.calls.append([dict(m) for m in messages])
        if self._index >= len(self.script):
            return AssistantMessage(content=self.final)
        item = self.script[self._index]
        self._index += 1
        if "tool" in item:
            return AssistantMessage(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"call_{self._index}",
                        name=item["tool"],
                        args=item.get("args", {}),
                        raw=json.dumps(item.get("args", {}), ensure_ascii=False),
                    )
                ],
            )
        return AssistantMessage(content=item.get("final", ""))
