"""10 秒自检：确认当前读到的密钥能不能调用 qwen-plus。

用法：
    python check_key.py

只发一句话、不产生报告、不写运行日志；密钥只显示头尾几位。
"""
from __future__ import annotations

import config


def mask(value: str) -> str:
    if not value:
        return "（空）"
    if len(value) <= 10:
        return value[:2] + "***"
    return f"{value[:6]}...{value[-4:]}（长度 {len(value)}）"


def main() -> None:
    print(f"密钥来源：环境变量 / .env（当前值 {mask(config.DASHSCOPE_API_KEY)}）")
    print(f"接口地址：{config.BASE_URL}")
    print(f"模型：{config.QWEN_MODEL}")
    if not config.DASHSCOPE_API_KEY:
        print("失败：没有读到密钥。请在本目录新建 .env，写入 DASHSCOPE_API_KEY=sk-...")
        return

    from openai import OpenAI

    client = OpenAI(
        api_key=config.DASHSCOPE_API_KEY,
        base_url=config.BASE_URL,
        timeout=20,
        max_retries=0,
    )
    try:
        resp = client.chat.completions.create(
            model=config.QWEN_MODEL,
            messages=[{"role": "user", "content": "只回复两个字：成功"}],
            max_tokens=10,
        )
    except Exception as exc:
        print(f"\n失败：{type(exc).__name__}: {exc}")
        print(
            "\n常见原因：\n"
            "  1) 密钥复制不完整或已被删除/停用 → 到百炼控制台重新创建 API-KEY；\n"
            "  2) 账户欠费或未开通该模型 → 控制台看「费用与配额」；\n"
            "  3) 密钥字符串被粘贴成了别的格式（正常是 sk- 开头、约 32 位）。"
        )
        return
    print("\n认证成功，模型回复：" + (resp.choices[0].message.content or "").strip())


if __name__ == "__main__":
    main()
