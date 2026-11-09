"""实验 2.6 的五类测试，一条命令跑完并生成表格：python run_tests.py

说明：
- 正常流程（用例 1）在 main.py 里用真实模型跑；这里用脚本化模型重放同样的动作序列，
  保证无论有没有网络都能重复验证 Harness 的控制逻辑；
- 异常/边界情况本来就是"模拟触发"的（实验允许），用例 3 直接给工具打补丁让它抛异常。
"""
from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path

import config
import tools
from harness import Harness, RunResult
from models import MockModel


def good_report() -> str:
    stats = tools.doc_stats(3)
    top = "；".join(f"{d['path']}（{d['chars']} 字）" for d in stats["top"])
    return (
        "# 知识库巡检报告\n\n"
        f"- 文档总数：{stats['total_docs']}\n"
        f"- 总字数：{stats['total_chars']}\n"
        f"- 最长的文档：{top}\n\n"
        "## 巡检结论\n1. 文档数量与实验5知识库一致。\n2. 单篇文档均不足 800 字，暂不需要拆分。\n"
    )


def boom(*_args, **_kwargs):
    raise OSError("模拟磁盘故障：无法读取知识库目录")


class Case:
    def __init__(self, name, task, script, *, confirm="yes", max_steps=config.MAX_STEPS,
                 patch=None, expect_status="ok", note=""):
        self.name = name
        self.task = task
        self.script = script
        self.confirm = confirm
        self.max_steps = max_steps
        self.patch = patch
        self.expect_status = expect_status
        self.note = note


CASES = [
    Case(
        "1 正常完成",
        "巡检知识库并生成报告（用户同意写操作）",
        [
            {"tool": "doc_stats", "args": {"top_n": 3}},
            {"tool": "write_report", "args": {"content": good_report()}},
            {"final": "巡检完成，报告已生成。"},
        ],
        confirm="yes",
        expect_status="ok",
        note="应当校验通过：报告存在且含文档总数",
    ),
    Case(
        "2 参数错误",
        "模型先给了越界参数 top_n=99，应当被程序拦下并反馈",
        [
            {"tool": "doc_stats", "args": {"top_n": 99}},
            {"tool": "doc_stats", "args": {"top_n": 3}},
            {"tool": "write_report", "args": {"content": good_report()}},
            {"final": "已按错误提示修正参数并完成。"},
        ],
        expect_status="ok",
        note="第一次调用应返回 bad_args，模型修正后完成",
    ),
    Case(
        "3 工具异常",
        "工具内部抛 OSError（模拟磁盘故障）",
        [
            {"tool": "list_docs", "args": {}},
            {"final": "读取知识库失败，无法完成巡检。"},
        ],
        patch=("list_docs", boom),
        expect_status="unverified",
        note="异常应被捕获为 error 反馈给模型，任务结束且说明原因",
    ),
    Case(
        "4 达到步数上限",
        "模型反复调用工具，超过 max_steps=2",
        [{"tool": "list_docs", "args": {}} for _ in range(6)],
        max_steps=2,
        expect_status="max_steps",
        note="程序应在第 2 步后强制停止，不能无限循环",
    ),
    Case(
        "5 人工确认-拒绝",
        "写报告前用户拒绝确认",
        [
            {"tool": "doc_stats", "args": {"top_n": 3}},
            {"tool": "write_report", "args": {"content": good_report()}},
            {"final": "用户拒绝，未写入。"},
        ],
        confirm="no",
        expect_status="confirm_denied",
        note="拒绝后不应执行写工具，也不应生成报告文件",
    ),
]


def error_types(log_file: str) -> set[str]:
    kinds: set[str] = set()
    path = Path(log_file)
    if not path.is_file():
        return kinds
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "tool_result" and not record.get("ok", True):
            kinds.add(record.get("error_type", "?"))
    return kinds


def run_case(case: Case) -> tuple[RunResult, str, list[str]]:
    model = MockModel(case.script)
    original = None
    if case.patch:
        tool_name, replacement = case.patch
        original = tools.TOOLS[tool_name].func
        tools.TOOLS[tool_name].func = replacement
    buffer = io.StringIO()
    try:
        harness = Harness(
            model,
            max_steps=case.max_steps,
            confirm=lambda a, d: case.confirm == "yes",
        )
        with contextlib.redirect_stdout(buffer):
            result = harness.run(case.task)
    finally:
        if case.patch:
            tools.TOOLS[case.patch[0]].func = original
    kinds = sorted(error_types(result.log_file))
    return result, buffer.getvalue(), kinds


def main() -> None:
    rows = []
    passed = 0
    for case in CASES:
        print(f"\n===== {case.name} =====")
        result, output, kinds = run_case(case)
        ok = result.status == case.expect_status
        passed += ok
        detail = f"status={result.status}, steps={result.steps}, errors={result.errors}"
        if kinds:
            detail += f", 工具错误={kinds}"
        print(f"  -> {detail}")
        for line in output.strip().splitlines()[-6:]:
            print(f"     {line}")
        if not ok:
            print(f"  !! 预期 {case.expect_status}，实际 {result.status}")
        rows.append((case, result, kinds, ok))

    # 生成 Markdown 结果表（可直接贴进报告的 2.6 节）
    lines = [
        f"# 实验6 测试记录（{datetime.now().isoformat(timespec='seconds')}）",
        "",
        "| # | 输入 | 预期行为 | 实际结果 | 判断 | 日志 |",
        "|---|---|---|---|---|---|",
    ]
    for case, result, kinds, ok in rows:
        kind_text = f"；工具错误：{kinds}" if kinds else ""
        lines.append(
            f"| {case.name} | {case.task} | {case.expect_status} + {case.note} | "
            f"status={result.status}，步骤 {result.steps}，耗时 {result.elapsed_s}s{kind_text} | "
            f"{'通过' if ok else '不通过'} | `{Path(result.log_file).name}` |"
        )
    lines += [
        "",
        f"共 {len(rows)} 项，通过 {passed} 项。正常流程的真实模型调用见 `logs/runs.jsonl` 中 `python main.py` 的记录。",
        "",
    ]
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = config.LOG_DIR / "test_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"测试通过 {passed}/{len(rows)}，表格已写入 {out}")


if __name__ == "__main__":
    main()
