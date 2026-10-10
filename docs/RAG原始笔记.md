1.新建文件夹，放置代码
	在项目目录打开 PowerShell，安装依赖到项目虚拟环境：
	.\.venv\Scripts\python.exe -m pip install -r requirements.txt
	如果尚未创建虚拟环境，先执行：python -m venv .venv
	虚拟环境不会自动继承全局 Python 已安装的依赖。

2.配置 API Key（只需填写一次）
	打开项目目录中的 .env 文件，把 YOUR_API_KEY_HERE 替换为你的真实 Key 并保存：
	LLM_API_KEY=你的真实key
	程序每次启动会自动读取脚本同目录的 .env，无需重复输入设置命令。
	.env 已加入 .gitignore；不要分享该文件或把真实 Key 写入代码。
	.env.example 是不含真实 Key 的配置模板，换电脑时可复制为 .env 并填写。
	如果终端已设置 LLM_API_KEY，该环境变量优先；修改 .env 后请重新启动程序。

	可选配置（未填写时使用代码中的默认值）：
	API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com")
	API_KEY  = os.environ.get("LLM_API_KEY", "YOUR_API_KEY_HERE")   # ← 禁止硬编码，建议用环境变量
	MODEL    = os.environ.get("LLM_MODEL", "deepseek-flash")

	
3.修改AI设定
	把代码发给ai生成V2，V3版本


eg://只是例子
	#v2
	SYSTEM_PROMPT = """你是一名严谨的百科问答助手，面向需要准确信息的用户。

行为规则：
1. 涉及以下情况【必须】先搜索，不得凭记忆作答：
   - 时效性信息（新闻、天气、股价、赛事、政策）
   - 具体数据、日期、人名、事件细节
   - 你不确定或知识可能过时的内容
2. 常识、推理、计算类问题可直接作答，无需搜索。
3. 搜索请求格式（单独一行，不要输出其他内容）：
   搜索: 关键词
4. 收到搜索结果后，必须严格基于结果作答，格式：
   回答: 你的答案
"""

	#v3
	SYSTEM_PROMPT = """你是一名严谨的百科问答助手，必须基于事实作答。

行为规则：
1. 时效性/具体数据/不确定的信息，必须先搜索。
2. 常识与推理题直接回答。
3. 搜索格式（单独一行）：
   搜索: 关键词
4. 作答格式：
   回答: 答案内容
5. 【拒答规则】若搜索结果为空或与问题无关，必须回答：
   回答: 抱歉，未能检索到可靠信息，无法回答该问题。
   严禁编造、猜测。

示例：

用户：今天上海天气如何？
助手：搜索: 上海今天天气
（收到结果后）助手：回答: 今天上海多云，气温 18~25℃……

用户：介绍一下量子纠缠
助手：回答: 量子纠缠是指两个或多个粒子……

用户：2050年诺贝尔文学奖得主是谁？
助手：搜索: 2050年诺贝尔文学奖
（搜索无结果）助手：回答: 抱歉，未能检索到可靠信息，无法回答该问题。
"""

4.增加token统计模块  //把下面代码复制进去
	【在这里添加你的代码】
    print(f"  [Token] 输入 {resp.usage.prompt_tokens} | 输出 {resp.usage.completion_tokens} | 合计 {resp.usage.total_tokens}")


5.运行代码进行测试
	PowerShell：.\.venv\Scripts\python.exe .\search_rag_example.py
	这种方式无需激活虚拟环境，能确保使用正确的 Python。
	在终端输入问题，退出输入 exit 或 quit。
	如果使用编辑器运行，也应将 Python 解释器选择为项目中的 .venv\Scripts\python.exe。
	
