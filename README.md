# 搜索增强 RAG 问答助手

Python命令行课程项目：通过OpenAI兼容接口调用模型，以维基百科正文、Bing及权威引用网页增强回答，提供系统时间与城市天气工具，并支持三版提示词和固定30题评测。

**了解架构和查询各模块：[索引 index.md](索引%20index.md)**。操作见[实验指南](docs/实验指南.md)，实现见[检索代码说明](docs/检索代码说明.md)。

评分采用课程原规则，另提供[别名与人工复核补充评价](docs/补充评价说明.md)，减少姓名写法等造成的误判。

## 启动

在项目根目录打开PowerShell，电脑需已有Python。首次模型配置用`.env.example`创建本地`.env`，填写LLM_API_KEY、LLM_API_BASE、LLM_MODEL；已有配置不覆盖，环境变量优先，不展示或提交密钥。

```powershell
.\scripts\run_project.ps1 -Task setup
.\scripts\run_project.ps1 -Task demo -Version V3
```

启动器复用`.venv`，缺失时创建；安装requirements.txt中缺失依赖（含Pillow）并执行pip check。默认清华镜像，可用`-IndexUrl https://pypi.org/simple`切换。依赖齐全跳过安装，不加--upgrade；绘图也使用项目解释器。

| 任务 | 命令 |
|---|---|
| 离线测试 | `.\scripts\run_project.ps1 -Task test` |
| 三版／盲答演示 | `.\scripts\run_project.ps1 -Task demo -Version V1`（可选V2、V3、blind） |
| 新实验并导出图表 | `.\scripts\run_project.ps1 -Task experiment -Workers 3` |
| 仅导出指定实验 | `.\scripts\run_project.ps1 -Task export -InputDir outputs\experiment\exp_1` |

demo逐题输入，quit或exit退出；blind不加“盲答”前缀。experiment新建独立编号目录；export不调用模型，但缺依赖时可能联网安装。PowerShell阻止执行时，可仅为本次进程用`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_project.ps1 -Task setup`。

退出码：0为阶段成功（准确率可能未达标）；1为环境、输入或子进程失败；2为已导出但有失败／未完成任务；130为中断。原核心入口仍为`.\.venv\Scripts\python.exe -X utf8 search_rag_example.py`，支持“盲答 问题”。

课程依据见[RAG项目目标.md](RAG项目目标.md)，修改约束见[AGENTS.md](AGENTS.md)。实验独立保存，失败保留；.env、.venv、输出及数据缓存不提交Git。网络、资料和模型均可能出错，事实需独立核验。
