# 天气与汇率 RAG 项目

以 [RAG项目目标.md](RAG项目目标.md) 为任务与评分依据。主入口 `search_rag_weather_fx.py` 提供盲答与 Bing 搜索增强，三版提示词见 `scripts/weather_prompts.py`。

## 启动

在项目根目录使用 PowerShell 7（pwsh）。复用 `.env`、`.venv`；首次配置可参照 `.env.example` 设置 `LLM_API_KEY`、`LLM_API_BASE`、`LLM_MODEL`，不要输出或提交密钥。服务商自带联网必须关闭，通义千问与智谱官方接口已有关闭设置，其他服务需自行核查。可用 `LLM_EXTRA_BODY` JSON 设置位传递额外参数。

```powershell
.\scripts\run_project.ps1 -Task setup
.\scripts\run_project.ps1 -Task demo -Version V3
.\scripts\run_project.ps1 -Task test
```

启动器复用环境，只补缺失依赖；绘图使用 Pillow。执行策略阻止时，可使用 `pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_project.ps1 -Task setup`。

## 第三部分：手工问答与截图

- 跑通：分别执行 `-Task demo -Version V3` 和 `-Task demo -Version blind`，各提问至少一次，保存完整真实截图。交互时输入问题，`quit` 退出；blind入口无需加“盲答”前缀。
- 时间注入：分别执行下方两条命令，两次均问“今天日期是？”，截图并用2～3句分析真实差异，不预设模型一定答错。
- 三版提示词：分别使用 `-Version V1`、`V2`、`V3`，同一组至少四题（两道时效性/冷门题、一道常识题、一道已核验陷阱题）。记录是否搜索、真实关键词、关键词合理性、答案正确性、角色风格、是否编造及核验依据，每版保存实际截图。

```powershell
.\scripts\run_project.ps1 -Task demo -Version blind -TimeInjection off
.\scripts\run_project.ps1 -Task demo -Version blind -TimeInjection on
```

V1保留教师原文，V2增加角色规则，V3增加拒答规则和两个明确标记为虚构的Few-shot。时间注入只提供当前北京时间，不等于取得了实时气温或汇率。真实会话与搜索轨迹自动保存到 `outputs/sessions/`；手工问答、事实核验及截图由组员完成。

## 第四部分：批量评测与导出

```powershell
.\scripts\run_project.ps1 -Task experiment -Workers 3
.\scripts\run_project.ps1 -Task export -InputDir outputs\experiment\exp_1
```

第一条命令新建独立编号目录，完整执行15道天气和15道汇率题、两模式共60项任务并导出图表。第二条仅离线重导出，须将 `exp_1` 替换为实际目录。实时实验禁止跨时段续跑或合并轮次；中断后保留证据，新建一轮。

每题现场调用教师取值函数，两模式共享该题标准值；标准值仅用于评分，不进入模型消息或搜索材料。RAG由模型自主决定是否搜索，读取Bing摘要；天气查询还会从本次结果中最多一个天气网城市链接读取当天“当前温度”栏。汇率查询则从实际结果中的中国银行官网牌价表读取对应币种、现汇买卖价与发布时间，核实100外币兑人民币单位后计算两价均值/100，明确其为参考中间值而非银行成交报价。每次搜索最多补读一个相关网页，均保留来源、发布日期与抓取时间，不调用评分API。任务c采用基础绘图路径，不启用选做辅助模型查询改写。

当前天气关键词先用“城市天气”，汇率先用“中国银行外汇牌价 币种名”，避免堆叠日期或以单独数字1开头。网页缺少具体观测时刻时必须说明，抓取时间不能充当观测时间；天气当前栏或银行当日牌价无效时保留原摘要并允许拒答，不用其他币种或其他日期代替。银行名称别名只用于实体匹配，不含预存汇率数值。修改后的检索与提示词须新建实验轮次，不能把旧成绩当作修复后成绩；固定集调优不等于独立泛化测试。银行原表及100外币单位说明见[中国银行外汇牌价](https://www.boc.cn/sourcedb/whpj/)。

判分沿用教师函数：天气±3℃、汇率±1.5%。标准值获取失败不计分并报告有效分母；模型或检索失败计错，保留全部任务。统计天气、汇率和总体准确率；标准值未完整取得时不宣称正式达标。完整30题下RAG多对2题满足≥5个百分点、多对3题满足≥10个百分点的准确率要求。

|产物|用途|
|---|---|
|`results.csv`|30行八列：type、item、question、truth、blind_answer、rag_answer、blind_correct、rag_correct|
|`sample30.json`、`truths.json`|固定题目及现场标准值、获取状态和时间|
|`metadata.json`、`prompts.json`、`source/`|配置、提示词、源码快照|
|`raw_results.json`、`search_traces/`、`task_details.csv`|答案、失败状态、搜索轨迹与逐次usage|
|`summary.json`、`accuracy.png`、`实验结果摘要.md`|分题型和总体统计、柱状图、结果摘要|
|`token_comparison.csv`|三道天气与两道汇率的累计输入Token对比|

Token按每题全部模型调用累计，任次usage缺失则完整累计未知。结合调用次数、摘要长度及得分写2～3句分析；报告还需分析盲答的猜测/旧值/拒答、摘要的数值唯一性与时效性、真实搜索关键词质量。导出会核验CSV、原始记录、教师评分和Token累计，不重新查询标准值。

退出码0表示阶段成功，不代表准确率达标；1表示环境或文件失败；2表示已导出但有标准值缺失或失败任务；130表示中断。

## 报告与文件

保留目标的“一”至“七”与原小题名称，封面和三人真实分工在前。理论12小题各100～200字、公式规范；包含时间对照、三版四题表与真实截图、分题型准确率图、五题Token表和分析。目标文本中空缺的公式符号写报告时须核对补全，不改写原目标文件。

报告先另存可编辑Word到 `reports/`，当前不自动生成PDF；课程正式提交要求PDF，提交前须导出并检查图片、公式、表格与分页。姓名学号、分工及每人不少于200字的总结由本人提供，不代编。源码与结果若压缩仅用zip，排除 `.env`、`.venv` 和密钥；按课程规则说明工具辅助。

仅保留当前源码、三份离线测试、本说明、目标及必要配置。`scripts/`包含演示、评测、导出和统一启动；`tests/`验证当前评分、信息隔离、并发和入口。`outputs/`由程序在需要时自动创建，`reports/`在制作报告时创建。
