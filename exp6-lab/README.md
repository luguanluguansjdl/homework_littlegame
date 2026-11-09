# 实验6 · 工具调用、Agent 与 Harness（最小可演示版）

承接实验5的「跃学星球」知识库，做一个**单 Agent 巡检助手**：模型自己决定先查什么、
看到结果再决定下一步，程序（Harness）负责校验、执行、拦截、确认、记账和验收。

## 一、快速开始

```powershell
cd exp6-lab

python check_key.py              # 先确认密钥能用（只发一句话，不写报告）
python main.py --mock            # 不联网跑通整条链路（演示用，30 秒出结果）
python main.py                   # 真实通义千问 + 交互式确认（y 同意写入）
python main.py --confirm no      # 演示"用户拒绝写操作"
python main.py --max-steps 2     # 演示"达到步数上限"
python run_tests.py              # 跑 5 类测试，生成 logs/test_report.md
```

> 真实模型需要 `DASHSCOPE_API_KEY`：优先读环境变量，其次本目录 `.env`，
> 最后回落到 `../rag-lab/.env`。`rag-lab/.env` 里那个密钥被服务端拒绝（401
> `invalid_api_key`），**要真实演示必须先填一个有效密钥**：
> 打开本目录的 `.env`，把 `DASHSCOPE_API_KEY=` 后面补上 `sk-...`，保存后
> `python check_key.py` 看到"认证成功"再 `python main.py`。

## 二、这份代码对应作业的哪几条

| 实验要求 | 代码位置 | 说明 |
|---|---|---|
| 2.1 任务边界、权限三档 | `main.py` / `harness.py` 顶部说明 | 只读工具自动跑；写报告必须人工确认；越界路径、未知工具禁止 |
| 2.2 工具接口 + Schema + 白名单 | `tools.py` | 4 个工具、JSON Schema、逐字段校验、目录边界、超时 |
| 2.3 Agent 循环 + 步数/超时 | `harness.py` `Harness.run` | 1 步 = 1 次模型调用；结果按 `role=tool` 回填上下文 |
| 2.4 Harness 模块划分 | `harness.py` + `models.py` | 输入、模型调用、工具分发、确认、验收、日志各一摊 |
| 2.5 日志与失败处理 | `harness.py` `RunLogger` | task_id、时间、参数摘要、错误类型、步数、耗时、最终状态；密钥打码 |
| 2.6 五类测试 | `run_tests.py` | 正常/参数错/工具异常/步数上限/确认拒绝，输出表格 |

## 三、四个工具

| 工具 | 用途 | 参数 | 写数据 | 需确认 |
|---|---|---|---|---|
| `list_docs` | 列出 knowledge/ 下文档及字数 | 无 | 否 | 否 |
| `read_doc` | 读单个文档（超 4000 字截断） | `path` 相对路径 | 否 | 否 |
| `doc_stats` | 文档数、总字数、最长 top_n | `top_n` 1-10 | 否 | 否 |
| `write_report` | 写 `reports/report_<任务号>.md` | `content` 20-6000 字 | **是** | **是** |

## 四、Harness 强制执行的限制（不靠提示词）

| 限制 | 值 | 在哪里生效 |
|---|---|---|
| 最大步数 | 6（`--max-steps` 可改） | `Harness.run` 循环条件 |
| 总运行时限 | 180s | 每步开始前检查 `deadline` |
| 单次模型超时 / 重试 | 30s / 1 次 | `config.py` + `RealModel` + `_call_model` |
| 单次工具超时 | 5s | `tools.execute` 线程池 `future.result(timeout)` |
| 工具错误上限 | 3 次 | `Harness.run` 计数后 `RunAborted` |
| 可读目录 | 仅 `knowledge/` | `tools._safe_path` 解析后 `relative_to` 校验 |
| 可写目录 | 仅 `reports/` | `write_report` 固定路径 |
| 成功标准 | 报告存在 + 含 `doc_stats` 返回的文档总数 | `Harness._verify`（程序判定，不信模型） |

> 工具超时只是"停止等待"，后台线程可能仍在跑——这一点是作业 2.5 明确要求说明的，
> 代码注释和日志里都写了，报告里要照抄这一句。

## 五、目录结构

```
exp6-lab/
├── main.py          # 入口：任务、模型选择、确认方式、步数
├── check_key.py     # 密钥自检：一句话调用，确认能不能真跑
├── harness.py       # 运行时 Harness：循环、限制、确认、验收、日志
├── models.py        # RealModel（通义千问 function calling）/ MockModel（脚本化）
├── tools.py         # 工具实现 + Schema + 白名单 + 参数校验 + 超时
├── config.py        # 路径、密钥、各项限制常量
├── run_tests.py     # 2.6 五类测试，输出 logs/test_report.md
├── knowledge/       # 只读数据源（实验5的两份产品文档）
├── reports/         # write_report 的输出（运行后生成）
└── logs/            # 每次运行的 jsonl + runs.jsonl 汇总 + test_report.md
```

## 六、生成 PDF

报告正文在 `报告.md`，截图在 `图片/`。修改后执行：

```powershell
python build_pdf.py
```

脚本自带一个极简 Markdown 解析器，把 `报告.md` 转成 HTML，再调用 Edge 无头模式
打印成 `实验6-工具调用、Agent与Harness实现.pdf`（A4，约 10 页）。
提交前把 PDF 按学习通要求改名：`班级-学号-姓名-实验6-工具调用、Agent与Harness实现.pdf`。

## 七、日志怎么看

```powershell
# 用 UTF-8 打开，否则 PowerShell 会按 GBK 显示成乱码
Get-Content logs\runs.jsonl -Encoding UTF8
Get-Content logs\run_<task_id>.jsonl -Encoding UTF8
```

`run_<id>.jsonl` 里一行一个事件：`run_start` → `model_reply` → `tool_request` →
`tool_result` → `confirm` → `verify` → `stop`；`runs.jsonl` 一行一次运行的汇总。

## 八、留给报告的部分（代码写不了）

- 2.1 里"一组输入及预期结果"的具体例子、个人软件是否接入的说明；
- 2.2 手工测试的截图（`python tools.py` 的输出可直接截）；
- 2.3 循环图（`报告.md` 里有 ASCII 版，可重画成图）；
- 2.6 的"判断依据"与"问题定位分析"；
- 3 操作记录截图编号、4 小结；
- 真实模型跑通后的 `runs.jsonl` 记录（现在只有 401 的失败记录，正好当"模型异常"证据）。
