# 搜索增强 RAG 问答助手

Python命令行课程项目，通过OpenAI兼容接口调用模型，并按模型请求检索Bing摘要；支持盲答、V1/V2/V3演示及固定数据集评测。

历史生成数据曾清理，实际结果以对应实验目录为准；不同轮次独立保存，不混用成绩。

## 快速运行

在项目根目录打开PowerShell，使用统一入口：

```powershell
.\scripts\run_project.ps1 -Task setup
.\scripts\run_project.ps1 -Task demo -Version V3
```

启动器复用项目`.venv`，缺失时才创建；按`requirements.txt`检查导入，缺少依赖时自动安装缺失项（包括Pillow），然后执行`pip check`。问答、测试和绘图使用同一解释器，无需激活环境；不加`--upgrade`，不覆盖已有环境。默认清华镜像，可用`-IndexUrl https://pypi.org/simple`切换，不修改全局pip配置。首次模型配置时复制`.env.example`为`.env`，已有配置不要覆盖。配置项为`LLM_API_KEY`、`LLM_API_BASE`和`LLM_MODEL`，系统环境变量优先。不展示或提交密钥。

| 任务 | 命令 | 是否调用模型 |
|---|---|---|
| 环境准备 | `.\scripts\run_project.ps1 -Task setup` | 否 |
| 离线测试 | `.\scripts\run_project.ps1 -Task test` | 否 |
| 三版演示 | `.\scripts\run_project.ps1 -Task demo -Version V1`（或V2、V3） | 是 |
| 盲答演示 | `.\scripts\run_project.ps1 -Task demo -Version blind` | 是 |
| 新一轮评测并导出 | `.\scripts\run_project.ps1 -Task experiment -Workers 3` | 是 |
| 导出已有实验 | `.\scripts\run_project.ps1 -Task export -InputDir outputs\experiment\exp_1` | 否 |

demo模式逐题输入，quit或exit退出；盲答演示通过`-Version blind`选择，不加“盲答”前缀。experiment自动新建编号目录，将本轮实际目录明确传给评测和导出。export必须指定目录，避免统计错轮次；依赖缺失时仍可能联网安装，但不调用模型。若PowerShell阻止执行，可仅为本次进程运行：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_project.ps1 -Task setup`，不修改系统策略。

退出码：0表示阶段执行成功（准确率可能未达标）；1表示环境、输入或子进程失败；2表示结果已导出但评测存在失败/未完成任务；130表示中断。失败记录保留，不自动重跑。直接运行原核心入口仍可使用`.\.venv\Scripts\python.exe -X utf8 search_rag_example.py`，该入口支持“盲答 问题”。

## 项目结构

| 位置 | 用途 |
|---|---|
| search_rag_example.py | 核心问答，默认医疗助手V3 |
| scripts/run_project.ps1、project_runner.py | 统一启动、依赖检查及任务分发 |
| scripts/ | 提示词、演示、评测、统计及辅助模块 |
| tests/ | 离线测试与搜索诊断 |
| docs/实验指南.md | 第三部分操作、第四部分评分与报告要求 |
| docs/第三部分对比表模板.csv | 用户填写的人工评价模板 |
| data/ | 官方数据集缓存，缺失时评测脚本自动下载 |
| outputs/ | 实验按experiment/exp_1、exp_2依次分目录；另存会话、诊断与用户截图 |
| reports/、tools/ | 新Word报告及分页检查工具 |
| RAG项目目标.md、AGENTS.md | 课程要求与协作约定 |

完整操作见[实验指南](docs/实验指南.md)。第三部分与第四部分独立执行、独立评价。核心提示词与`scripts/prompt_versions.py`按协作约定同步，V1保留；不改动当前授权范围之外的核心逻辑。

实验输出、数据缓存、`.env`和`.venv`不提交Git。问答需要有效接口与网络；搜索摘要可能无关，事实应独立核验。
