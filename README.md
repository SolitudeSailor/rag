# 搜索增强 RAG 问答助手

Python 命令行课程示例：使用 OpenAI 兼容接口调用大模型，并根据模型请求检索 Bing 搜索结果，支持搜索问答和无搜索的“盲答”对比。

## Windows 快速启动

在项目目录的 CMD 或 PowerShell 中执行：

```bat
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

将 `.env.example` 复制为 `.env`，填入自己的 API Key：

```dotenv
LLM_API_KEY=你的真实DeepSeek_API_Key
```

启动程序：

```bat
.\.venv\Scripts\python.exe search_rag_example.py
```

直接输入问题进入搜索问答；输入 `盲答 问题` 进行无搜索问答；输入 `quit` 或 `exit` 退出。

## 配置

程序自动读取脚本同目录的 `.env`，已设置的环境变量优先。

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `LLM_API_KEY` | 服务商 API Key，必填 | 无有效默认值 |
| `LLM_API_BASE` | OpenAI 兼容接口地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 服务商提供的模型名称 | `deepseek-flash` |

模型名称需以服务商实际可用模型为准。问答需要联网及有效 API Key，可能产生服务商费用。Bing 网页结构变化或访问限制可能导致检索为空。

`.env`、`.venv` 和 Python 缓存已加入 `.gitignore`，不要上传真实密钥。提示词修改与课程实验说明见 [RAG.md](RAG.md)。
