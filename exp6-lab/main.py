"""实验6 入口：跑一次完整的 Agent 任务。

用法：
    python main.py                  # 真实通义千问 + 交互确认（默认演示方式）
    python main.py --confirm no     # 演示"用户拒绝写操作"
    python main.py --max-steps 2    # 演示"达到步数上限"
    python main.py --mock           # 不联网：脚本化模型跑通同一套循环
"""
from __future__ import annotations

import argparse

import config
import tools
from harness import Harness
from models import MockModel, RealModel

DEFAULT_TASK = (
    "巡检 knowledge/ 目录下的知识库文档：统计文档总数和总字数，"
    "找出字数最多的 3 个文档，并生成一份巡检报告。"
)


def build_mock_script() -> list[dict]:
    """脚本化模型的动作序列。报告正文用只读工具现算，保证数字对得上。"""
    stats = tools.doc_stats(3)
    top = "；".join(f"{d['path']}（{d['chars']} 字）" for d in stats["top"])
    report = (
        "# 知识库巡检报告\n\n"
        f"- 文档总数：{stats['total_docs']}\n"
        f"- 总字数：{stats['total_chars']}\n"
        f"- 最长的文档：{top}\n\n"
        "## 巡检结论\n"
        "1. 文档数量与实验5知识库一致，未发现多余文件。\n"
        "2. 单篇文档均不足 800 字，暂不需要拆分。\n"
    )
    return [
        {"tool": "doc_stats", "args": {"top_n": 3}},
        {"tool": "write_report", "args": {"content": report}},
        {"final": "巡检完成：已生成报告，文档总数与字数均来自 doc_stats 工具。"},
    ]


def make_confirm(mode: str):
    """把 --confirm 选项包装成 Harness 需要的确认回调。"""

    def confirm(action: str, detail: str) -> bool:
        print("\n" + "-" * 60)
        print(f"[需要人工确认] {action}")
        print(detail)
        print("-" * 60)
        if mode == "yes":
            print("同意执行？(yes)  ← 由 --confirm yes 自动同意")
            return True
        if mode == "no":
            print("同意执行？(no)   ← 由 --confirm no 自动拒绝")
            return False
        try:
            answer = input("同意执行？(y/N) ").strip().lower()
        except EOFError:  # 非交互环境按拒绝处理
            print()
            return False
        return answer in {"y", "yes", "是", "同意"}

    return confirm


def main() -> None:
    parser = argparse.ArgumentParser(description="实验6：知识库巡检单 Agent")
    parser.add_argument("--task", default=DEFAULT_TASK, help="任务描述")
    parser.add_argument("--mock", action="store_true", help="用脚本化模型，不调用真实接口")
    parser.add_argument("--confirm", choices=["ask", "yes", "no"], default="ask",
                        help="写操作的确认方式：ask 交互 / yes 自动同意 / no 自动拒绝")
    parser.add_argument("--max-steps", type=int, default=config.MAX_STEPS, help="最大步数")
    args = parser.parse_args()

    print(f"模型：{'MockModel（脚本化）' if args.mock else config.QWEN_MODEL}")
    print(f"知识库：{config.KNOWLEDGE_DIR}")
    print(f"允许的工具：{sorted(tools.TOOLS)}")

    model = MockModel(build_mock_script()) if args.mock else RealModel()
    harness = Harness(model, max_steps=args.max_steps, confirm=make_confirm(args.confirm))
    harness.run(args.task)


if __name__ == "__main__":
    main()
