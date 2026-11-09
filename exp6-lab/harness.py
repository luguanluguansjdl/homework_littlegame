"""运行时 Harness（实验 2.3 / 2.4 / 2.5）。

一个任务 = 一次 run：
  接收目标 → 模型提出工具调用 → 程序校验并执行 → 结果按 tool 消息回填 →
  模型继续或给最终回答 → 程序核对成功标准。

由程序（而不是提示词）强制执行的东西：
  · 步数上限 MAX_STEPS、总时限 TOTAL_LIMIT_S、工具超时、模型重试次数；
  · 工具白名单与参数 Schema 校验、目录边界、写操作人工确认；
  · 结果验收：报告文件必须真实存在，且内容包含统计工具返回的文档总数。

日志：每次 run 写 logs/run_<task_id>.jsonl，结束后追加一行到 logs/runs.jsonl。
"""
from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import config
import tools
from models import AssistantMessage, ModelError, ToolCall

SYSTEM_PROMPT = """你是「知识库巡检助手」，只能使用下面提供的工具来完成任务。

工作顺序：
1. 先调用 doc_stats（top_n 取 2-3 即可）了解知识库规模；
2. 需要细节时用 read_doc 读取具体文档；
3. 调用 write_report 写入巡检报告。报告正文必须包含「文档总数」的具体数字和「总字数」，
   并给出 1-3 条基于工具返回数据的结论；不要编造没有依据的内容；
4. 写入成功后，用一句话说明结果，不要重复报告全文。

规则：
- 参数必须符合工具 Schema；拿不准就先调 list_docs 看清楚再决定，不要猜；
- 工具返回 error 时按错误信息修正再试，同一错误不要连续犯；
- 整个过程最多 {max_steps} 步，请在必要步骤内完成任务。
""".replace("{max_steps}", str(config.MAX_STEPS))


class RunAborted(Exception):
    """需要立刻结束本次运行（例如用户拒绝确认、工具错误超限）。"""

    def __init__(self, status: str, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(reason)


@dataclass
class RunResult:
    task_id: str
    status: str
    reason: str
    steps: int
    elapsed_s: float
    final_answer: str = ""
    report_path: str = ""
    log_file: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    errors: int = 0


_SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{6,}")


def _clip(value, limit: int = 300) -> str:
    """参数/结果的摘要：截断 + 打码，避免日志里出现密钥或超长内容。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = _SECRET_RE.sub("sk-***", text)
    return text if len(text) <= limit else text[:limit] + f"...(共{len(text)}字符)"


class RunLogger:
    def __init__(self, task_id: str):
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.task_id = task_id
        self.path = config.LOG_DIR / f"run_{task_id}.jsonl"
        self.start = time.time()

    def event(self, kind: str, *, step: int | None = None, echo: bool = True, **fields) -> None:
        record = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "task_id": self.task_id,
            "elapsed_s": round(time.time() - self.start, 3),
            "event": kind,
            "step": step,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        if echo:
            head = f"[步 {step}] " if step else ""
            extra = " ".join(f"{k}={_clip(v, 120)}" for k, v in fields.items() if v is not None)
            print(f"  {head}{kind:<14} {extra}")

    def summary(self, result: RunResult) -> None:
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "task_id": result.task_id,
            "status": result.status,
            "reason": result.reason,
            "steps": result.steps,
            "elapsed_s": result.elapsed_s,
            "errors": result.errors,
            "report_path": result.report_path,
            "tool_calls": result.tool_calls,
        }
        with (config.LOG_DIR / "runs.jsonl").open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def ask_confirm(action: str, detail: str) -> bool:
    """默认的人工确认方式：命令行 y/N。空输入视为拒绝。"""
    print("\n" + "-" * 60)
    print(f"[需要人工确认] {action}")
    print(detail)
    print("-" * 60)
    try:
        answer = input("同意执行？(y/N) ").strip().lower()
    except EOFError:  # 非交互环境（重定向/管道）按拒绝处理
        print()
        return False
    return answer in {"y", "yes", "是", "同意"}


class Harness:
    def __init__(
        self,
        model,
        *,
        max_steps: int = config.MAX_STEPS,
        total_limit_s: float = config.TOTAL_LIMIT_S,
        confirm: Callable[[str, str], bool] = ask_confirm,
        echo: bool = True,
    ):
        self.model = model
        self.max_steps = max_steps
        self.total_limit_s = total_limit_s
        self.confirm = confirm
        self.echo = echo
        self.task_id = ""

    # ------------------------------------------------------------------ run --
    def run(self, task: str) -> RunResult:
        task_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        self.task_id = task_id
        tools.set_task_id(task_id)
        log = RunLogger(task_id)
        start = time.time()
        deadline = start + self.total_limit_s

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        state: dict = {"stats": None, "report_path": "", "answer": "", "errors": 0}
        call_events: list[dict] = []
        steps = 0

        print(f"\n任务 {task_id}：{task}")
        print(f"限制：最大 {self.max_steps} 步 / 总时限 {self.total_limit_s:.0f}s / 工具超时 {config.TOOL_TIMEOUT_S}s")
        log.event("run_start", task=task, max_steps=self.max_steps, total_limit_s=self.total_limit_s)

        try:
            while steps < self.max_steps:
                if time.time() > deadline:
                    raise RunAborted("timeout", f"超过总运行时限 {self.total_limit_s:.0f}s")

                steps += 1
                message = self._call_model(messages, steps, log)

                if message.tool_calls:
                    messages.append(message.to_message())
                    for call in message.tool_calls:
                        payload, ok = self._run_tool(call, steps, log, state)
                        call_events.append({
                            "step": steps, "tool": call.name,
                            "args": _clip(call.args, 200), "ok": ok,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(payload, ensure_ascii=False),
                        })
                        if not ok:
                            state["errors"] += 1
                            if state["errors"] > config.MAX_TOOL_ERRORS:
                                raise RunAborted(
                                    "tool_error_limit",
                                    f"工具错误累计 {state['errors']} 次，超过上限 {config.MAX_TOOL_ERRORS}，结束任务",
                                )
                    continue

                # 模型给出最终回答：不能直接判定成功，必须用程序核对成功标准
                messages.append(message.to_message())
                state["answer"] = message.content or ""
                log.event("final_answer", step=steps, content=_clip(state["answer"], 400))
                passed, why = self._verify(state)
                log.event("verify", step=steps, passed=passed, detail=why)
                return self._finish(
                    RunResult(
                        task_id=task_id,
                        status="ok" if passed else "unverified",
                        reason=why,
                        steps=steps,
                        elapsed_s=round(time.time() - start, 2),
                        final_answer=state["answer"],
                        report_path=state["report_path"],
                        log_file=str(log.path),
                        tool_calls=call_events,
                        errors=state["errors"],
                    ),
                    log,
                )

            raise RunAborted("max_steps", f"达到最大步数 {self.max_steps}，任务未完成")
        except RunAborted as stop:
            log.event("stop", status=stop.status, reason=stop.reason)
            return self._finish(
                RunResult(
                    task_id=task_id,
                    status=stop.status,
                    reason=stop.reason,
                    steps=steps,
                    elapsed_s=round(time.time() - start, 2),
                    final_answer=state["answer"],
                    report_path=state["report_path"],
                    log_file=str(log.path),
                    tool_calls=call_events,
                    errors=state["errors"],
                ),
                log,
            )
        except ModelError as err:
            log.event("stop", status="model_error", reason=str(err))
            return self._finish(
                RunResult(
                    task_id=task_id,
                    status="model_error",
                    reason=f"模型调用失败：{err}",
                    steps=steps,
                    elapsed_s=round(time.time() - start, 2),
                    log_file=str(log.path),
                    tool_calls=call_events,
                    errors=state["errors"],
                ),
                log,
            )

    # ------------------------------------------------------------- 模型调用 --
    def _call_model(self, messages: list[dict], step: int, log: RunLogger) -> AssistantMessage:
        schemas = [spec.as_openai() for spec in tools.TOOLS.values()]
        last: Exception | None = None
        for attempt in range(1, config.MODEL_RETRIES + 2):
            started = time.time()
            try:
                message = self.model.chat(messages, schemas)
            except ModelError as err:
                last = err
                log.event("model_error", step=step, attempt=attempt, error=_clip(str(err), 300))
                if attempt <= config.MODEL_RETRIES:
                    time.sleep(1)
                continue
            log.event(
                "model_reply",
                step=step,
                attempt=attempt,
                elapsed_s=round(time.time() - started, 2),
                content=_clip(message.content or "", 200),
                tool_calls=[{"name": c.name, "args": _clip(c.args, 120)} for c in message.tool_calls],
                echo=self.echo,
            )
            return message
        raise ModelError(str(last))

    # ------------------------------------------------------------- 工具执行 --
    def _run_tool(self, call: ToolCall, step: int, log: RunLogger, state: dict) -> tuple[dict, bool]:
        spec = tools.TOOLS.get(call.name)
        log.event("tool_request", step=step, tool=call.name, args=_clip(call.args, 200), echo=self.echo)

        if spec is None:
            payload = {"ok": False, "error_type": "unknown_tool",
                       "message": f"工具 {call.name!r} 不在允许列表 {sorted(tools.TOOLS)} 中，未执行"}
            log.event("tool_result", step=step, tool=call.name, ok=False, error_type="unknown_tool")
            return payload, False

        # 先校验参数再问确认：参数就不合法时没必要打扰用户
        if spec.requires_confirm:
            try:
                tools.validate_args(call.name, call.args)
            except tools.ToolError as err:
                payload = {"ok": False, "error_type": err.kind, "message": err.message}
                log.event("tool_result", step=step, tool=call.name, ok=False,
                          error_type=err.kind, message=_clip(err.message, 300))
                return payload, False

            detail = (
                f"工具：{call.name}\n"
                f"动作：写入 reports/report_{self.task_id}.md，会修改磁盘数据\n"
                f"参数摘要：{_clip(call.args, 400)}"
            )
            approved = self.confirm(f"执行 {call.name}（写操作）", detail)
            log.event("confirm", step=step, tool=call.name, approved=approved, echo=self.echo)
            if not approved:
                raise RunAborted("confirm_denied", f"用户拒绝执行写操作 {call.name}，该工具未运行")

        started = time.time()
        try:
            data = tools.execute(call.name, call.args)
        except tools.ToolError as err:
            payload = {"ok": False, "error_type": err.kind, "message": err.message}
            log.event("tool_result", step=step, tool=call.name, ok=False,
                      error_type=err.kind, message=_clip(err.message, 300),
                      elapsed_s=round(time.time() - started, 3))
            return payload, False

        payload = {"ok": True, "data": data}
        log.event("tool_result", step=step, tool=call.name, ok=True,
                  result=_clip(data, 300), elapsed_s=round(time.time() - started, 3))

        # 记录验收需要的证据，供 _verify 使用
        if call.name == "doc_stats":
            state["stats"] = data
        elif call.name == "write_report":
            state["report_path"] = data.get("path", "")
        return payload, True

    # ----------------------------------------------------------------- 验收 --
    def _verify(self, state: dict) -> tuple[bool, str]:
        stats = state.get("stats")
        if not stats:
            return False, "未调用 doc_stats，缺少可核对的统计数据，不能判定完成"
        report = state.get("report_path") or ""
        if not report:
            return False, "未生成报告文件（write_report 未成功）"
        full = config.BASE_DIR / report
        if not full.is_file() or full.stat().st_size == 0:
            return False, f"报告文件不存在或为空：{report}"
        text = full.read_text(encoding="utf-8")
        total = stats.get("total_docs")
        if str(total) not in text:
            return False, f"报告未包含文档总数 {total}，无法确认结论来自工具数据"
        return True, f"报告已生成（{report}），且包含文档总数 {total}"

    # ----------------------------------------------------------------- 收尾 --
    def _finish(self, result: RunResult, log: RunLogger) -> RunResult:
        log.summary(result)
        print(
            f"\n结果：{result.status} —— {result.reason}\n"
            f"步数 {result.steps} / 耗时 {result.elapsed_s}s / 错误 {result.errors} 次\n"
            f"报告：{result.report_path or '（未生成）'}\n"
            f"日志：{result.log_file}"
        )
        return result


def new_run_id_preview() -> str:
    """给报告/文档用的示例编号。"""
    return time.strftime("%Y%m%d-%H%M%S") + "-xxxx"


def log_dir() -> Path:
    return config.LOG_DIR
