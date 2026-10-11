# -*- coding: utf-8 -*-
"""
==============================================================
 极简"搜索引擎 RAG"示例代码（物联网工程专业 · 人工智能课程项目）
==============================================================
功能：在命令行中与 大语言模型 对话。模型可以按约定格式主动请求
     调用 Bing 搜索引擎，程序自动执行搜索并把结果拼回上下文，
     让模型基于"联网检索到的最新信息"作答（即搜索增强 RAG）。

使用方法：
  1. 安装依赖：  pip install openai requests beautifulsoup4
  2. 在下方"配置区"填入你的大模型 API Key（或设置环境变量）
  3. 运行：      python search_rag_example.py
  4. 直接输入问题开始对话；输入 "盲答 问题" 可切换到无搜索模式
     （用于第四部分的数据集对比实验）；输入 quit 退出。

【学生可以动手的位置一览】（正文中均有醒目的横幅标记）
  ★ 修改区 ① ：配置区 —— 填入你的 API Key / 选择模型（必做）
  ★ 修改区 ② ：SYSTEM_PROMPT —— 设计 V1→V2→V3 三版提示词（第三部分核心任务）
  ★ 加代码处 ③：call_llm() 内 —— 添加约 2 行代码打印 token 用量（第四部分·任务 b）
  ○ 选做     ：call_llm() 的 temperature 参数、bing_search() 换成百度搜索

 除以上位置外，本文件的其余代码【无需修改】。
==============================================================
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# ★★★ 学生修改区 ① ：配置区（必做）★★★
# 选择任意一家"OpenAI 兼容"接口的大模型服务，填入 API Key。
# 示例一：通义千问（阿里云百炼）
#   API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
#   MODEL    = "qwen-plus"
# 示例二：DeepSeek
#   API_BASE = "https://api.deepseek.com/v1"
#   MODEL    = "deepseek-chat"
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

API_BASE = os.environ.get("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
API_KEY  = os.environ.get("LLM_API_KEY", "YOUR_API_KEY_HERE")   # ← 禁止硬编码，建议用环境变量
MODEL    = os.environ.get("LLM_MODEL", "qwen-plus")

client = OpenAI(api_key=API_KEY, base_url=API_BASE)

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# ★★★ 学生修改区 ② ：System Prompt（第三部分核心任务，必做）★★★
#
# 下面是【V1 基础版】，只告诉模型"可以搜索"，没有角色和规则。
# 请按项目要求依次设计并替换为：
#   V2 角色+规则版：角色身份 + 行为规则（何时必须搜索、回答要求等）
#   V3 完整版    ：V2 + Few-shot 示例 + "拒答规则"（搜不到不许编造）
# 每改一版就重新运行程序做对比测试，并在报告中截图记录。
#
# 约定输出格式（程序靠它识别，不要随意改动"搜索:"和"回答:"两个关键词）：
#   需要搜索时，模型另起一行输出：   搜索: 关键词
#   最终作答时，模型另起一行输出：   回答: 答案内容
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
SYSTEM_PROMPT = """你是一个问答助手。你拥有一次调用搜索引擎的机会。

规则：
1. 如果你认为回答问题需要查阅最新或你不确定的信息，
   请另起一行，严格按以下格式输出（只输出这一行，不要输出其他内容）：
   搜索: 你想搜索的关键词
2. 收到搜索结果后，或直接作答时，请另起一行，按以下格式输出：
   回答: 你的答案
"""
# ▲▲▲ 学生修改区 ② 结束 ▲▲▲


# ------------------------------------------------------------
# 工具函数 1：调用大模型（OpenAI 兼容 chat 接口）
# ------------------------------------------------------------
def call_llm(messages):
    """把完整的消息列表发给大模型，返回模型的文本回复。"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.3,   # ○ 选做：可尝试修改该参数，观察输出变化
    )
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # ★★★ 学生加代码处 ③ ：第四部分·任务 b（必做，约 2 行）★★★
    # 请在此处添加代码，打印每次调用的 token 用量，例如：
    #     prompt_tokens 在 resp.usage.prompt_tokens
    #     completion_tokens 在 resp.usage.completion_tokens
    # 然后用它记录同一道题"盲答 vs 搜索 RAG"的 token 对比表。
    #
    #    【在这里添加你的代码】
    #
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
    return resp.choices[0].message.content.strip()


# ------------------------------------------------------------
# 工具函数 2：Bing 网页搜索（免 API Key，抓取搜索结果摘要）
# ------------------------------------------------------------
def bing_search(query, top_k=3):
    """
    用 Bing 搜索 query，返回前 top_k 条结果的"标题 + 摘要"文本。
    ○ 选做：也可以换成百度搜索（https://www.baidu.com/s?wd=...），
      只需修改下面的网址和解析标签（百度结果在 div.result 中）。
    """
    url = "https://cn.bing.com/search"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    html = requests.get(url, params={"q": query}, headers=headers, timeout=10).text

    soup = BeautifulSoup(html, "html.parser")
    snippets = []
    for item in soup.select("li.b_algo"):          # Bing 的每条搜索结果在 <li class="b_algo"> 中
        title = item.find("h2")
        summary = item.find("p")
        if title and summary:
            snippets.append(f"【{title.get_text(strip=True)}】{summary.get_text(strip=True)}")
        if len(snippets) >= top_k:
            break
    return snippets


# ------------------------------------------------------------
# 核心逻辑：ReAct 式"思考 → 行动(搜索) → 观察 → 作答"循环
# ------------------------------------------------------------
def rag_answer(question, system_prompt=SYSTEM_PROMPT, max_search=2):
    """
    搜索增强（RAG）模式：
      把问题交给模型 → 若模型输出"搜索: xxx"则执行搜索并把结果拼回上下文
      → 模型基于搜索结果给出"回答: ..."。最多允许搜索 max_search 轮。
    返回 (最终答案, 搜索过程记录列表)。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    search_log = []

    for _ in range(max_search + 1):
        reply = call_llm(messages)

        # 检查模型是否请求搜索：匹配独立一行开头的 "搜索: xxx"
        m = re.search(r"^\s*搜索[:：]\s*(.+)$", reply, flags=re.MULTILINE)
        if not m or len(search_log) >= max_search:
            # 模型不再搜索（或搜索次数用完）→ 提取最终答案
            ans = re.search(r"回答[:：]\s*(.+)", reply, flags=re.DOTALL)
            return (ans.group(1).strip() if ans else reply), search_log

        # ---- 模型请求搜索：执行搜索，把结果作为新上下文拼回去 ----
        keyword = m.group(1).strip()
        print(f"  [系统] 模型请求搜索 → 「{keyword}」")
        snippets = bing_search(keyword)
        search_log.append((keyword, snippets))

        search_result_text = "\n".join(snippets) if snippets else "（本次搜索没有找到结果）"
        messages.append({"role": "assistant", "content": reply})
        messages.append({
            "role": "user",
            "content": f"以下是你刚才搜索「{keyword}」得到的网络结果，请参考它们作答：\n{search_result_text}",
        })

    return "（搜索轮数已用完，模型未给出最终答案）", search_log


def blind_answer(question):
    """无搜索"盲答"模式：模型仅凭自身记忆回答（用于对比实验）。"""
    return call_llm([
        {"role": "system", "content": "你是一个问答助手，请直接回答问题。"},
        {"role": "user", "content": question},
    ])


# ------------------------------------------------------------
# 命令行交互入口
# ------------------------------------------------------------
def main():
    print("=" * 60)
    print(" 极简搜索增强 RAG 问答助手（命令行版）")
    print(" 直接输入问题        → 搜索 RAG 模式")
    print(" 输入「盲答 问题」   → 无搜索盲答模式（对比用）")
    print(" 输入 quit           → 退出")
    print("=" * 60)

    while True:
        question = input("\n请输入问题> ").strip()
        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            break

        if question.startswith("盲答"):
            answer = blind_answer(question[2:].strip())
            print(f"\n[盲答] {answer}")
        else:
            answer, search_log = rag_answer(question)
            if search_log:
                used = "；".join(k for k, _ in search_log)
                print(f"\n[本次对话共调用搜索 {len(search_log)} 次：{used}]")
            print(f"\n[回答] {answer}")


if __name__ == "__main__":
    main()
