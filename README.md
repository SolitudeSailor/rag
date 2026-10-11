# 搜索增强 RAG 问答助手

Python命令行课程项目，通过OpenAI兼容接口调用模型，并按模型请求检索Bing摘要；支持盲答、V1/V2/V3演示及固定数据集评测。

已清空此前生成的实验数据和数据集缓存，当前成绩需重新运行取得。保留现有环境与提示词，不沿用旧成绩。

## 快速运行

在项目根目录打开PowerShell，复用已有`.env`和`.venv`：

```powershell
.\.venv\Scripts\python.exe -X utf8 search_rag_example.py
```

输入问题使用RAG，输入“盲答 问题”不搜索，输入quit或exit退出。新环境先创建虚拟环境并安装`requirements.txt`；首次配置时复制`.env.example`为`.env`，已有配置不要覆盖。配置项为`LLM_API_KEY`、`LLM_API_BASE`和`LLM_MODEL`，系统环境变量优先。不展示或提交密钥。

## 项目结构

| 位置 | 用途 |
|---|---|
| search_rag_example.py | 核心问答，默认医疗助手V3 |
| scripts/ | 日常运行所需的提示词、演示、评测、统计及辅助模块 |
| tests/ | 离线测试与搜索诊断 |
| docs/实验指南.md | 第三部分操作、第四部分评分与报告要求 |
| docs/第三部分对比表模板.csv | 用户填写的人工评价模板 |
| data/ | 官方数据集缓存，缺失时评测脚本自动下载 |
| outputs/ | 实验按experiment/exp_1、exp_2依次分目录；另存会话、诊断与用户截图 |
| reports/、tools/ | 新Word报告及分页检查工具 |
| RAG项目目标.md、AGENTS.md | 课程要求与协作约定 |

完整操作见[实验指南](docs/实验指南.md)。第三部分与第四部分独立执行、独立评价。核心提示词与`scripts/prompt_versions.py`按协作约定同步，V1保留；不改动当前授权范围之外的核心逻辑。

实验输出、数据缓存、`.env`和`.venv`不提交Git。问答需要有效接口与网络；搜索摘要可能无关，事实应独立核验。
