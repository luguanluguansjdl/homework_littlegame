"""工具接口层（实验 2.2）。

要点：
- 白名单：模型只能请求 TOOLS 里登记过的工具，凡是不在表里的一律拒绝，
  绝不执行模型生成的任意函数名或命令；
- Schema：每个工具用 JSON Schema 描述参数，执行前由 validate_args() 逐项检查
  （必填、类型、数值范围、多余字段）；
- 路径边界：路径参数只能落在 knowledge/ 内，禁止绝对路径和 .. 跳转；
- 权限：write_report 会改数据，标记 requires_confirm=True，必须人工确认后才执行。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import config


class ToolError(Exception):
    """工具层的可预期错误，kind 用于报告分类（对应实验 2.5 的错误类型）。"""

    def __init__(self, kind: str, message: str):
        self.kind = kind  # unknown_tool | bad_args | denied | timeout | exception
        self.message = message
        super().__init__(f"{kind}: {message}")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON Schema
    func: Callable[..., dict]
    writes: bool = False      # 是否修改数据
    requires_confirm: bool = False
    timeout_s: float = config.TOOL_TIMEOUT_S

    def as_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# 运行上下文：writer 需要知道当前任务编号，用模块级状态传进去
_STATE: dict[str, str] = {"task_id": "manual"}


def set_task_id(task_id: str) -> None:
    _STATE["task_id"] = task_id


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def _iter_docs() -> list[Path]:
    if not config.KNOWLEDGE_DIR.is_dir():
        raise ToolError("exception", f"知识库目录不存在：{config.KNOWLEDGE_DIR}")
    return sorted(
        p for p in config.KNOWLEDGE_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in config.ALLOWED_SUFFIXES
    )


def list_docs() -> dict:
    """只读：列出知识库里的文档及字数。"""
    docs = []
    for p in _iter_docs():
        docs.append({
            "path": p.relative_to(config.KNOWLEDGE_DIR).as_posix(),
            "chars": len(p.read_text(encoding="utf-8")),
        })
    return {"count": len(docs), "total_chars": sum(d["chars"] for d in docs), "docs": docs}


def _safe_path(raw: str) -> Path:
    """把模型给的相对路径解析到 knowledge/ 内，越界一律拒绝。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("bad_args", "path 必须是非空字符串")
    if Path(raw).is_absolute():
        raise ToolError("denied", "只接受 knowledge/ 下的相对路径，禁止绝对路径")
    full = (config.KNOWLEDGE_DIR / raw).resolve()
    try:
        full.relative_to(config.KNOWLEDGE_DIR.resolve())
    except ValueError:
        raise ToolError("denied", f"路径越界，拒绝访问：{raw}") from None
    if full.suffix.lower() not in config.ALLOWED_SUFFIXES:
        raise ToolError("denied", f"只允许读取 {sorted(config.ALLOWED_SUFFIXES)} 文件：{raw}")
    if not full.is_file():
        raise ToolError("bad_args", f"文件不存在：{raw}")
    return full


def read_doc(path: str) -> dict:
    """只读：读取单个文档，超长截断，避免上下文被撑爆。"""
    full = _safe_path(path)
    text = full.read_text(encoding="utf-8")
    limit = 4000
    return {
        "path": path,
        "chars": len(text),
        "truncated": len(text) > limit,
        "text": text[:limit],
    }


def doc_stats(top_n: int) -> dict:
    """只读：统计文档数量与字数，给出最长的 top_n 个文档。"""
    listing = list_docs()
    ranked = sorted(listing["docs"], key=lambda d: d["chars"], reverse=True)[:top_n]
    return {
        "total_docs": listing["count"],
        "total_chars": listing["total_chars"],
        "top": ranked,
    }


def write_report(content: str) -> dict:
    """写类：把巡检报告写到 reports/report_<task_id>.md，需人工确认。"""
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = config.REPORT_DIR / f"report_{_STATE['task_id']}.md"
    target.write_text(content, encoding="utf-8")
    return {
        "path": target.relative_to(config.BASE_DIR).as_posix(),
        "chars": len(content),
        "note": "已写入；同名文件会被覆盖，重复调用只保留最后一次",
    }


# ---------------------------------------------------------------------------
# 白名单与参数校验
# ---------------------------------------------------------------------------

TOOLS: dict[str, ToolSpec] = {
    "list_docs": ToolSpec(
        name="list_docs",
        description="列出知识库 knowledge/ 下的全部 .md/.txt 文档及每个文档的字数。无参数。",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        func=list_docs,
    ),
    "read_doc": ToolSpec(
        name="read_doc",
        description="读取 knowledge/ 下的单个文档内容。path 为相对路径，如 '跃学星球-学生端使用指南.md'。",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "knowledge/ 下的相对文件路径，只允许 .md/.txt",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        func=read_doc,
    ),
    "doc_stats": ToolSpec(
        name="doc_stats",
        description="统计知识库：返回文档总数、总字数，以及字数最多的 top_n 个文档。",
        parameters={
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "返回前多少个最长文档，1-10",
                    "minimum": 1,
                    "maximum": 10,
                }
            },
            "required": ["top_n"],
            "additionalProperties": False,
        },
        func=doc_stats,
    ),
    "write_report": ToolSpec(
        name="write_report",
        description=(
            "把巡检报告写入 reports/ 目录，会修改磁盘数据，执行前需要用户确认。"
            "报告内容必须包含文档总数等统计结论。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "报告正文（Markdown），20-6000 字符",
                    "minLength": 20,
                    "maxLength": 6000,
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        },
        func=write_report,
        writes=True,
        requires_confirm=True,
    ),
}

_TYPE_MAP = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def validate_args(name: str, args: Any) -> dict:
    """按 Schema 校验参数，失败抛 ToolError('bad_args')。"""
    spec = TOOLS[name]
    if not isinstance(args, dict):
        raise ToolError("bad_args", f"参数必须是 JSON 对象，实际收到 {type(args).__name__}")

    props: dict = spec.parameters["properties"]
    required: list = spec.parameters.get("required", [])

    missing = [k for k in required if k not in args]
    if missing:
        raise ToolError("bad_args", f"缺少必填参数：{missing}")
    extra = [k for k in args if k not in props]
    if extra:
        raise ToolError("bad_args", f"不允许的参数：{extra}，可用参数：{list(props)}")

    for key, value in args.items():
        schema = props[key]
        expected = _TYPE_MAP[schema["type"]]
        if schema["type"] == "integer" and isinstance(value, bool):
            raise ToolError("bad_args", f"{key} 必须是整数，不能是布尔值")
        if not isinstance(value, expected):
            raise ToolError("bad_args", f"{key} 应为 {schema['type']}，实际是 {type(value).__name__}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolError("bad_args", f"{key} 不能小于 {schema['minimum']}，实际 {value}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolError("bad_args", f"{key} 不能大于 {schema['maximum']}，实际 {value}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ToolError("bad_args", f"{key} 长度不能小于 {schema['minLength']}，实际 {len(value)}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolError("bad_args", f"{key} 长度不能大于 {schema['maxLength']}，实际 {len(value)}")
    return args


# 单线程池：工具超时后线程可能仍在跑，这正是 2.5 要求说明的"超时≠后台已停止"
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool")


def execute(name: str, raw_args: Any) -> dict:
    """执行一次工具调用。成功返回结果字典，失败抛 ToolError。"""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError("unknown_tool", f"工具 {name!r} 不在允许列表 {sorted(TOOLS)} 中")

    args = validate_args(name, raw_args)
    future = _EXECUTOR.submit(lambda: spec.func(**args))
    try:
        return future.result(timeout=spec.timeout_s)
    except FutureTimeout:
        raise ToolError(
            "timeout",
            f"工具 {name} 超过 {spec.timeout_s}s 未返回，已停止等待（后台线程可能仍在运行）",
        ) from None
    except ToolError:
        raise
    except Exception as exc:  # 工具内部异常：不吞掉，转成可反馈给模型的错误
        raise ToolError("exception", f"{type(exc).__name__}: {exc}") from exc


def tool_catalog_text() -> str:
    """给报告用：工具清单表格（Markdown）。"""
    rows = [
        "| 工具 | 用途 | 参数（必填*） | 是否写数据 | 需确认 |",
        "|---|---|---|---|---|",
    ]
    for spec in TOOLS.values():
        props = spec.parameters["properties"]
        required = spec.parameters.get("required", [])
        params = ", ".join(
            f"{k}:{v['type']}{'*' if k in required else ''}" for k, v in props.items()
        ) or "无"
        rows.append(
            f"| `{spec.name}` | {spec.description.splitlines()[0]} | {params} "
            f"| {'是' if spec.writes else '否'} | {'是' if spec.requires_confirm else '否'} |"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    # 手工冒烟：正确参数 + 错误参数各一次（实验 2.2 要求）
    print(tool_catalog_text())
    print("\n[正确参数]", json.dumps(execute("doc_stats", {"top_n": 2}), ensure_ascii=False))
    for bad in [
        ("doc_stats", {"top_n": 99}),
        ("read_doc", {"path": "../config.py"}),
        ("rm_rf", {}),
    ]:
        try:
            execute(*bad)
        except ToolError as err:
            print(f"[错误参数] {bad[0]} -> {err}")
