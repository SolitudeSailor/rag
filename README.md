# 搜索增强 RAG 问答助手

面向人工智能课程实验的 Python 命令行示例。程序通过 OpenAI 兼容接口调用大模型；当模型请求搜索时，检索 Bing 网页摘要并将结果加入上下文，再由模型作答。支持搜索 RAG 与无搜索“盲答”对比，以及提示词版本和 Token 用量实验。

## 目录

- [项目结构](#项目结构)
- [安装与运行](#安装与运行)
- [接口配置](#接口配置)
- [提示词实验：V1 → V2 → V3](#提示词实验v1--v2--v3)
- [Token 统计与对比实验](#token-统计与对比实验)
- [常见问题](#常见问题)

## 项目结构

```text
rag/
├── search_rag_example.py   # 核心问答程序，默认医疗助手 V3
├── RAG项目目标.md         # 课程目标和评分要求
├── AGENTS.md              # 项目协作约定
├── requirements.txt       # 核心与统计依赖（含 Pillow）
├── .env.example           # 配置模板
├── scripts/               # 三版提示词、演示、评测及离线统计
├── docs/                  # 问答清单、操作教程及原始笔记
├── tools/                 # Word 分页检查工具
├── data/                  # 官方数据集缓存，不提交数据文件
├── outputs/
│   ├── sessions/          # 第三部分与演示的会话 JSON
│   ├── screenshots/       # 用户手工截图
│   ├── experiment/        # 第四部分数据、Token 表及图表
└── reports/               # 新 Word 报告
```

复用已有 `.env` 和 `.venv`，不将凭据、环境、实验输出或旧材料提交 Git。新电脑需自行配置环境。旧材料集中在 `待清理旧文件/`，不参与新实验。

第三部分用 `scripts/run_demo.py` 测试自选的同一组问题；第四部分用 `scripts/evaluate.py` 执行固定 30 题、共 60 个任务，再用 `scripts/export_experiment_summary.py` 单独统计第四部分。两部分分别记录与评价。

逐题命令和次数见 [问答操作清单](docs/问答操作清单.md)，完整流程见 [实验教程](docs/RAG实验操作教程.md)。

## 安装与运行

### 1. 准备项目和虚拟环境

先安装 Python，在项目目录打开 CMD 或 PowerShell。已有项目文件可直接使用；也可以克隆仓库（私有仓库需要访问权限）：

```bat
git clone https://github.com/SolitudeSailor/rag.git
cd rag
```

如果尚未创建虚拟环境，执行：

```bat
python -m venv .venv
```

将依赖安装到项目虚拟环境：

```bat
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

虚拟环境不会自动继承全局 Python 已安装的依赖。直接指定 `.venv` 中的解释器，无需激活环境，也能确保安装和运行使用同一套 Python。

### 2. 填写 API Key（只需一次）

首次配置时，将 `.env.example` 复制为 `.env`。CMD 和 PowerShell 均可执行：

```bat
copy .env.example .env
```

如果已有 `.env`，直接编辑，避免覆盖已填写的配置。将占位符替换为真实密钥并保存：

```dotenv
LLM_API_KEY=你的真实DeepSeek_API_Key
```

程序每次启动自动读取脚本同目录的 `.env`，不需要重复输入设置命令。文件名应为 `.env`，不能是 `.env.txt`；修改后需重新启动程序。不要将真实 Key 写入 Python 代码或分享 `.env`；该文件已加入 `.gitignore`。

### 3. 启动与交互

```bat
.\.venv\Scripts\python.exe search_rag_example.py
```

| 输入 | 行为 |
| --- | --- |
| `介绍一下量子纠缠` | 搜索 RAG 模式，由模型判断是否需要搜索 |
| `盲答 介绍一下量子纠缠` | 无搜索模式，用于对比实验 |
| `quit` 或 `exit` | 退出程序 |

若使用编辑器运行，将 Python 解释器选为项目的 `.venv\Scripts\python.exe`。

## 接口配置

以下变量可以写入 `.env`。如果终端或系统已经设置了同名环境变量，它们优先于 `.env`。

| 变量 | 用途 | 代码默认值 |
| --- | --- | --- |
| `LLM_API_KEY` | 服务商 API Key，必填 | `YOUR_API_KEY_HERE`（占位符，不能用于认证） |
| `LLM_API_BASE` | OpenAI 兼容接口地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 服务商提供的模型名称 | `deepseek-flash` |

只需填写 Key 即可使用代码默认配置；需要切换服务商或模型时，在 `.env` 中增加 `LLM_API_BASE` 和 `LLM_MODEL`，确保接口、模型与密钥属于同一服务商。模型名称以该服务商实际可用模型为准。

代码通过以下方式读取配置，无需在代码中填入真实密钥：

```python
API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com")
API_KEY = os.environ.get("LLM_API_KEY", "YOUR_API_KEY_HERE")
MODEL = os.environ.get("LLM_MODEL", "deepseek-flash")
```

## 提示词实验：V1 → V2 → V3

核心文件只允许修改 V2/V3 提示词内容，当前默认启用医疗助手 V3，V1/V2 以注释保留。使用 `scripts/run_demo.py --version V1/V2/V3` 选择实验版本；不带 `--question` 时可连续提问，输入 `quit` 或 `exit` 退出；带 `--question` 时仅运行指定问题。每题独立作答，不保留跨题上下文；每次启动的全部问答统一保存到 `outputs/sessions/` 的会话 JSON，`outputs/screenshots/` 仅供手工存放截图。修改提示词时同步 `scripts/prompt_versions.py`。会话文件名使用“版本_session_本次会话第一道问题.json”，非法字符替换、过长截断，同名加序号。

| 版本 | 设计重点 | 观察内容 |
| --- | --- | --- |
| V1 | 基础搜索能力与输出格式 | 是否主动搜索 |
| V2 | 角色身份、搜索条件与回答规则 | 搜索是否及时，答案是否基于结果 |
| V3 | V2 的规则，加上示例与拒答要求 | 搜索无结果时是否避免编造 |

可以请 AI 辅助生成或调整 V2、V3，但必须保留程序识别的 `搜索:` 和 `回答:` 格式。提示词示例用于说明行为，不保证模型始终遵守；需要实际测试。

实际 V2/V3 以 [prompt_versions.py](scripts/prompt_versions.py) 为准：V2 包含医疗角色、搜索条件、格式和能力边界；V3 追加两段明确标注的模拟示例与搜索无果时的拒答规则。示例不是实验结果。

### 修改后的验证

切换版本时退出会话，重新运行 --version V1/V2/V3，无需修改代码。使用同一组问题，覆盖时效性信息、常识推理和缺乏可靠结果的场景；记录是否搜索、搜索次数、回答内容及拒答情况，并保留截图用于课程报告。

## Token 统计与对比实验

`call_llm()` 的“学生加代码处 ③”已包含 Token 统计，无需再次复制。对应代码为：

```python
usage = getattr(resp, 'usage', None)
tokens = [getattr(usage, name, None) for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')]
tokens = ['未知' if value is None else value for value in tokens]
print(f"  [Token] 输入 {tokens[0]} | 输出 {tokens[1]} | 合计 {tokens[2]}")
```

`prompt_tokens` 表示输入 Token，`completion_tokens` 表示输出 Token，`total_tokens` 表示本次调用的总用量；统计依赖服务商返回 `usage` 字段。

第四部分运行 evaluate.py 自动记录两模式用量，export_experiment_summary.py 生成前五题的 token_comparison.csv，无需另问五题。手工检查时可在原程序对同一道题分别输入原问题和 `盲答 原问题`。搜索 RAG 可能多次调用模型，应将该问题下所有 `[Token]` 行相加，而不是只记录最后一次调用。

| 问题 | 提示词版本 | 模式 | 搜索次数 | 输入 Token 合计 | 输出 Token 合计 | 总 Token | 回答表现 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 待填写 | V3 | 搜索 RAG | 待记录 | 待记录 | 待记录 | 待记录 | 待评价 |
| 同一道题 | 无搜索专用提示词 | 盲答 | 0 | 待记录 | 待记录 | 待记录 | 待评价 |

盲答使用单独的提示词，不受 V1/V2/V3 的 `SYSTEM_PROMPT` 控制。对比时记录所用模型和配置，结合答案质量分析 Token 用量，不能只看 Token 总数。

第四部分运行命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\evaluate.py --workers 3 --output-dir outputs/experiment_search_fixed
& 'E:\Professional\py\python.exe' -X utf8 scripts\export_experiment_summary.py --input-dir outputs/experiment_search_fixed
```

本机虚拟环境已有核心依赖，全局解释器已有 Pillow，统计优先复用它，无需安装。新环境依赖清单已包含 Pillow。

同配置续跑只补缺失任务，已有失败不自动重试，原实验日期和配置保留。改变模型或提示词后使用新目录，例如评测加 `--output-dir outputs/experiment_v2`，统计对应加 `--input-dir outputs/experiment_v2`；配置不一致会停止续跑。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `ModuleNotFoundError` | 在项目目录用 `.venv\Scripts\python.exe -m pip install -r requirements.txt` 安装依赖，并确认编辑器使用项目解释器 |
| 提示未配置 API Key | 检查 `.env` 文件名、保存位置，以及 `LLM_API_KEY` 是否仍为空或占位符 |
| 接口返回 401 | 确认 Key 有效且对应当前服务商；检查已有环境变量是否覆盖 `.env` |
| 修改配置后仍使用旧值 | 重新启动程序；若终端有同名环境变量，先清除或更新它 |
| 搜索结果为空 | 检查网络；Bing 网页结构变化或访问限制也可能影响解析 |

问答需要联网及有效 API Key，可能产生服务商费用。模型答案和网页摘要需结合可靠来源核验。

原始笔记保留在 [RAG原始笔记](docs/RAG原始笔记.md)，历史说明与当前版本不一致时，以实际源码和新操作教程为准。


接口检查、失败记录、搜索修复、未知 Token 处理与测试结论统一见 [项目检查与修复记录](docs/项目检查与修复记录.md)。修复前 outputs/experiment/ 保留，修复后实验另存 outputs/experiment_search_fixed/，不能混用成绩。
