# -*- coding: utf-8 -*-
"""天气与汇率RAG核心：模型调用、Bing检索、ReAct问答与教师评测工具。

直接运行用于交互问答；批量评测运行同目录的run_30.py。
配置读取根目录.env和环境变量；三版提示词统一维护于scripts/weather_prompts.py。
"""

import os
import re
import json
import base64
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from datetime import datetime
from decimal import Decimal, InvalidOperation
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

# 配置只从根目录.env或环境变量加载，不覆盖已有环境变量。
load_dotenv(Path(__file__).resolve().with_name('.env'), override=False)


API_BASE = os.environ.get("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
API_KEY  = os.environ.get("LLM_API_KEY", "YOUR_API_KEY_HERE")   # ← 禁止硬编码，建议用环境变量
MODEL    = os.environ.get("LLM_MODEL", "qwen-plus")

# 禁用已识别服务商的内置联网，检索统一由本地Bing工具执行。
EXTRA_BODY = json.loads(os.environ.get('LLM_EXTRA_BODY', '{}'))
if not isinstance(EXTRA_BODY, dict):
    raise ValueError('LLM_EXTRA_BODY 必须是 JSON 对象')
if 'dashscope.aliyuncs.com' == urlsplit(API_BASE).hostname:
    EXTRA_BODY.setdefault('enable_search', False)
if 'bigmodel.cn' == urlsplit(API_BASE).hostname or (urlsplit(API_BASE).hostname or '').endswith('.bigmodel.cn'):
    EXTRA_BODY.setdefault('tools', [{'type': 'web_search', 'web_search': {'enable': False}}])
if EXTRA_BODY.get('enable_search') or any(
    tool.get('type') == 'web_search' and tool.get('web_search', {}).get('enable', True)
    for tool in EXTRA_BODY.get('tools', [])
):
    raise ValueError('课程对比实验要求关闭服务商联网搜索')

# 重试统一由call_llm记录，避免SDK隐式重试导致usage漏记；网络请求有界等待。
client = OpenAI(api_key=API_KEY, base_url=API_BASE, timeout=60.0, max_retries=0)
SEARCH_CONFIGURATION = {
    'provider': 'bing_snippets_and_live_pages',
    'url': 'https://www.bing.com/search',
    'weather_page_host': 'www.tianqi.com',
    'weather_page_limit': 1,
    'fx_page_host': 'www.boc.cn',
    'fx_page_limit': 1,
    'fx_reference': '现汇买入卖出价均值除以100，非成交报价',
    'direct_truth_tool': False,
    'http_attempt_limit': 2,
    'weather_root_path': '城市拼音路径，允许有或无末尾斜杠',
    'refined_weather_params': {'mkt': 'zh-CN', 'setlang': 'zh-hans'},
}

# 对照实验由入口设置；ContextVar 防止批量任务之间互相覆盖。
TIME_INJECTION = ContextVar('weather_time_injection', default=True)
CALL_USAGE = ContextVar('weather_call_usage', default=None)
SEARCH_REQUEST_LOG = ContextVar('search_request_log', default=None)


def current_time_note():
    """按开关注入当前北京时间；知道日期不等于取得实时天气或汇率。"""
    if not TIME_INJECTION.get():
        return ''
    from datetime import timezone, timedelta
    return f"\n\n（当前系统时间：{datetime.now(timezone(timedelta(hours=8))):%Y年%m月%d日 %H:%M}，Asia/Shanghai，UTC+08:00）"

# 三版统一维护，V1保留教师原文；交互对照使用scripts/run_demo.py --version。
from scripts.weather_prompts import PROMPTS
SYSTEM_PROMPT = PROMPTS['V3']


# ------------------------------------------------------------
# 工具函数 1：调用大模型（OpenAI 兼容 chat 接口）
# ------------------------------------------------------------
def call_llm(messages):
    """把完整的消息列表发给大模型，返回模型的文本回复。"""
    import time
    kwargs = dict(
        model=MODEL,
        messages=messages,
        temperature=0.3,
        extra_body=EXTRA_BODY or None,   # 关闭服务端自带的联网搜索（见配置区说明）
    )
    resp = None
    for attempt in range(2):           # 网络/服务端偶发超时：最多尝试 2 次
        try:
            resp = client.chat.completions.create(**kwargs)
            usage = getattr(resp, 'usage', None)
            entry = {k: getattr(usage, k, None) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
            entry.update(status='ok', temperature=kwargs.get('temperature'))
            if CALL_USAGE.get() is not None:
                CALL_USAGE.get().append(entry)
            break
        except Exception as e:
            if CALL_USAGE.get() is not None:
                CALL_USAGE.get().append({'status': type(e).__name__, 'prompt_tokens': None,
                                         'completion_tokens': None, 'total_tokens': None,
                                         'temperature': kwargs.get('temperature')})
            if "temperature" in str(e).lower():
                # 部分模型（如 kimi-k2.6）只允许 temperature=1 → 去掉温度参数用模型默认值
                kwargs.pop("temperature", None)
            print(f"  [系统] 调用大模型出错（{type(e).__name__}），{'3 秒后重试...' if attempt == 0 else '放弃本次调用'}")
            if attempt == 0:
                time.sleep(3)
    if resp is None:
        raise RuntimeError('大模型接口暂时不可用')
    usage = getattr(resp, 'usage', None)
    print(f"  [Token] prompt_tokens={getattr(usage, 'prompt_tokens', None)} completion_tokens={getattr(usage, 'completion_tokens', None)}（None=未知）")
    return resp.choices[0].message.content.strip()


# ------------------------------------------------------------
# 工具函数 2：Bing 网页搜索（免 API Key，抓取搜索结果摘要）
# ------------------------------------------------------------
def _search_get(url, *, purpose, **kwargs):
    """同一公开URL最多尝试两次，记录全部请求；不新增搜索词、不关闭TLS校验。"""
    import time
    for attempt in range(2):
        event = dict(purpose=purpose, url=url, params=kwargs.get('params', {}).copy(),
                     attempt=attempt+1, status='running')
        log = SEARCH_REQUEST_LOG.get()
        if log is not None:
            log.append(event)
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            event.update(status='ok', final_url=getattr(response, 'url', url))
            return response
        except requests.RequestException as exc:
            event['status'] = type(exc).__name__
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            retryable = isinstance(exc, (requests.ConnectionError, requests.Timeout)) or status == 429 or (status is not None and status >= 500)
            if attempt == 1 or not retryable:
                raise
            print(f'  [系统] {purpose}请求暂时失败，重试同一URL（2/2）', flush=True)
            time.sleep(0.4)


def _bing_result_url(link):
    """解出本次搜索结果的公开链接，不猜测结果地址。"""
    try:
        parsed = urlsplit(link)
        if parsed.hostname == 'www.bing.com' and parsed.path == '/ck/a':
            encoded = parse_qs(parsed.query).get('u', [''])[0]
            if not encoded.startswith('a1'):
                return None
            encoded = encoded[2:]
            link = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8')
            parsed = urlsplit(link)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        return link
    except (ValueError, UnicodeError):
        return None


def _weather_result_url(link):
    link = _bing_result_url(link)
    if link is None:
        return None
    parsed = urlsplit(link)
    if parsed.hostname not in ('www.tianqi.com', 'tianqi.com') or not re.fullmatch(r'/[a-z]+/?', parsed.path):
        return None
    return link


def _fx_result_url(link):
    link = _bing_result_url(link)
    if link is None:
        return None
    parsed = urlsplit(link)
    if parsed.hostname not in ('www.boc.cn', 'boc.cn') or parsed.path not in ('/sourcedb/whpj/', '/sourcedb/whpj/index.html'):
        return None
    return link


def _fx_page(link, query):
    """从本次搜索返回的中行网页读取目标币种当日牌价；与评分API独立。"""
    from datetime import timezone, timedelta
    link = _fx_result_url(link)
    if link is None:
        return None
    # 常用名称与银行名称的对应，不含任何汇率数值。
    aliases = {'澳大利亚元': ('澳元',), '加拿大元': ('加元',), '港币': ('港元',),
               '林吉特': ('马来西亚林吉特',), '卢布': ('俄罗斯卢布',), '泰国铢': ('泰铢',)}
    try:
        response = _search_get(link, purpose='bank_page', headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=10)
        if _fx_result_url(response.url) is None:
            return None
        response.encoding = response.apparent_encoding
        page = BeautifulSoup(response.text, 'html.parser')
        if not re.search(r'本汇率表单位为100外币换算人民币', re.sub(r'\s+', '', page.get_text())):
            return None
        table = page.select_one('table#priceTable')
        if table is None:
            return None
        headers = [node.get_text(strip=True) for node in table.select('th')]
        fields = ('货币名称', '现汇买入价', '现汇卖出价', '发布日期', '发布时间')
        if not all(field in headers for field in fields):
            return None
        matches = []
        for row in table.select('tr'):
            cells = [node.get_text(' ', strip=True) for node in row.select('td')]
            if len(cells) != len(headers):
                continue
            values = dict(zip(headers, cells))
            currency = values['货币名称']
            if currency and any(name in query for name in (currency, *aliases.get(currency, ()))):
                matches.append(values)
        if len(matches) != 1:
            return None
        values = matches[0]
        bid, ask = (Decimal(values[field].replace(',', '')) for field in ('现汇买入价', '现汇卖出价'))
        if not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
            return None
        date = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', values['发布日期'])
        clock = re.search(r'\d{1,2}:\d{2}:\d{2}', values['发布日期'] + ' ' + values['发布时间'])
        if date is None or clock is None:
            return None
        fetched_at = datetime.now(timezone(timedelta(hours=8)))
        published = datetime(*map(int, date.groups()), *map(int, clock.group().split(':')), tzinfo=fetched_at.tzinfo)
        if published.date() != fetched_at.date() or published > fetched_at + timedelta(minutes=5):
            return None
        # 单位换算和报价口径固定，绝不根据评分标准值选择价格。
        rate = format((bid + ask) / Decimal(200), '.8f').rstrip('0').rstrip('.')
        return (f'【银行汇率网页】币种：{values["货币名称"]}；原表单位：100外币兑人民币；'
                f'现汇买入价：{bid}；现汇卖出价：{ask}。'
                f'据两价均值并除以100计算参考值：1{values["货币名称"]}约合{rate}人民币。'
                f'这是计算参考中间值，不是银行公布的成交报价。'
                f'银行发布时间：{published:%Y-%m-%d %H:%M:%S}（北京时间）；'
                f'抓取时间：{fetched_at:%Y-%m-%d %H:%M:%S}；来源：{response.url}')
    except (requests.RequestException, ValueError, InvalidOperation):
        return None


def _weather_page(link, query):
    """只读取天气网当前栏，不读取评分API或预报温度。"""
    from datetime import timezone, timedelta
    link = _weather_result_url(link)
    if link is None:
        return None
    try:
        response = _search_get(link, purpose='weather_page', headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=10)
        if _weather_result_url(response.url) is None:
            return None
        response.encoding = response.apparent_encoding
        page = BeautifulSoup(response.text, 'html.parser')
        city_tag = page.select_one('.weather_info .name h1')
        current = page.select_one('.weather_info .now')
        date_tag = page.select_one('.weather_info .week')
        if city_tag is None or current is None or date_tag is None:
            return None
        city = re.sub(r'天气(?:预报)?$', '', city_tag.get_text(strip=True)).strip()
        temperature = re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*(?:℃|°C)', current.get_text(' ', strip=True))
        date = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_tag.get_text())
        fetched_at = datetime.now(timezone(timedelta(hours=8)))
        if not city or city not in query or temperature is None or date is None:
            return None
        if tuple(map(int, date.groups())) != (fetched_at.year, fetched_at.month, fetched_at.day):
            return None
        return (f'【天气网页当前栏】城市：{city}；当前气温：{temperature.group(1)}℃；'
                f'页面日期：{date.group(0)}；抓取时间：{fetched_at:%Y-%m-%d %H:%M:%S}（北京时间）。'
                f'页面未提供具体观测时刻，不能视为独立现场实测。来源：{response.url}')
    except (requests.RequestException, ValueError, UnicodeError):
        return None


def bing_search(query, top_k=5):
    """
    用 Bing 搜索 query，返回前 top_k 条结果的"标题 + 摘要"文本。
    天气或汇率问题可从本次实际结果补读一个相关网页，仍最多返回top_k条。
    （默认取 5 条：条数太少可能漏掉含答案的结果，太多则浪费 token）
    """
    url = "https://www.bing.com/search"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if top_k <= 0:
        return []
    weather_query = bool(re.search(r'天气|气温|weather|temperature', query, re.IGNORECASE))
    params = {'q': query}
    # 精确天气查询使用中文地区；同一轮仍只有一个Bing查询，不增加隐藏搜索。
    if weather_query and re.search(r'["“”]', query):
        params.update(SEARCH_CONFIGURATION['refined_weather_params'])
    response = _search_get(url, purpose='bing', params=params, headers=headers, timeout=10)
    html = response.text

    soup = BeautifulSoup(html, "html.parser")
    snippets = []
    result_links = []
    for item in soup.select("li.b_algo"):          # Bing 的每条搜索结果在 <li class="b_algo"> 中
        title = item.find("h2")
        summary = item.find("p")
        if title:
            text = summary.get_text(strip=True) if summary else '（此结果未提供摘要）'
            snippet = f"【{title.get_text(strip=True)}】{text}"
            anchor = title.find('a', href=True)
            if anchor:
                result_links.append(anchor['href'])
                source = _bing_result_url(anchor['href'])
                if source:
                    snippet += f' 来源：{source}'
            snippets.append(snippet)
        if len(snippets) >= top_k:
            break
    if weather_query:
        for link in result_links:
            candidate = _weather_result_url(link)
            if candidate is None:
                continue
            detail = _weather_page(candidate, query)
            if detail:
                snippets = [detail] + snippets[:top_k-1]
                print('  [系统] 已读取搜索结果中的天气网页当前栏')
            break  # 每次模型搜索最多补读一个实际返回的天气页，失败仍保留原摘要。
    elif top_k > 0 and re.search(r'汇率|外汇|兑换|exchange|forex', query, re.IGNORECASE):
        for link in result_links:
            candidate = _fx_result_url(link)
            if candidate is None:
                continue
            detail = _fx_page(candidate, query)
            if detail:
                snippets = [detail] + snippets[:top_k-1]
                print('  [系统] 已读取搜索结果中的银行汇率网页')
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
      若模型没有按格式输出（例如直接说"我无法查询实时信息"），
      程序会自动提醒它一次，给它使用搜索功能的机会。
    返回 (最终答案, 搜索过程记录列表)。
    """
    messages = [
        {"role": "system", "content": system_prompt + current_time_note()},
        {"role": "user", "content": question},
    ]
    search_log = []
    nudged = False   # 是否已经提醒过模型一次（最多提醒一次）

    for _ in range(max_search + 2):
        reply = call_llm(messages)

        # 检查模型是否请求搜索：匹配独立一行开头的 "搜索: xxx"（容忍 **搜索**: 这类加粗写法）
        m = re.search(r"^\s*(?:\*\*)?搜索(?:\*\*)?[:：]\s*(.+)$", reply, flags=re.MULTILINE)
        if m and len(search_log) < max_search:
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
            continue

        # 模型没有请求搜索 → 提取最终答案
        ans = re.search(r"(?:\*\*)?回答(?:\*\*)?[:：]\s*(.+)", reply, flags=re.DOTALL)
        if ans:
            return ans.group(1).strip(), search_log

        # 模型既没有搜索、也没有按格式回答（典型情况：直接说"我无法查询实时天气"）
        # → 提醒它一次，引导它使用搜索功能
        if not nudged:
            nudged = True
            messages.append({"role": "assistant", "content": reply})
            messages.append({
                "role": "user",
                "content": "你可以使用搜索功能获取最新信息。如需搜索，请只输出一行「搜索: 关键词」；"
                           "如果已经可以作答，请输出「回答: 你的答案」。",
            })
            continue

        # 提醒过仍不按格式输出 → 原样返回，避免死循环
        return reply, search_log

    return "（搜索轮数已用完，模型未给出最终答案）", search_log


def blind_answer(question):
    """无搜索"盲答"模式：模型仅凭自身记忆回答（用于对比实验）。"""
    return call_llm([
        {"role": "system", "content": "你是一个问答助手，请直接回答问题。" + current_time_note()},
        {"role": "user", "content": question},
    ])


# ------------------------------------------------------------
# 评测工具区：实时天气 + 实时汇率混合评测集（教师提供，用于第四部分，无需修改）
# ------------------------------------------------------------
# 为什么不用传统的静态问答数据集？静态数据集的答案停留在多年前，
# 大模型的训练语料早已覆盖（"闭卷"也能答对），无法体现搜索 RAG 的价值。
# 而气温与汇率都在实时变化，模型仅凭记忆必然答错或给出旧值，
# 只有真正检索到实时信息才能答对——这是检验 RAG 最公平的考题。
# 本评测集故意混合两类题目：两类题在"检索结果质量"上差异很大，
# 恰好可以对比分析（详见项目文档第四部分的分析要求）。
# 标准答案通过免费服务（wttr.in / open.er-api.com，均免 API Key）实时获取。
# ------------------------------------------------------------

# 天气题用的 15 个城市（中文名 → wttr.in 查询名）
CITIES = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "成都": "Chengdu", "杭州": "Hangzhou", "西安": "Xi'an", "武汉": "Wuhan",
    "南京": "Nanjing", "重庆": "Chongqing", "哈尔滨": "Harbin", "昆明": "Kunming",
    "青岛": "Qingdao", "厦门": "Xiamen", "拉萨": "Lhasa",
}

# 汇率题用的 15 种货币（中文名 → 国际标准代码）
CURRENCIES = {
    "美元": "USD", "欧元": "EUR", "英镑": "GBP", "港元": "HKD",
    "澳元": "AUD", "加元": "CAD", "瑞士法郎": "CHF", "新西兰元": "NZD",
    "新加坡元": "SGD", "泰铢": "THB", "俄罗斯卢布": "RUB", "南非兰特": "ZAR",
    "马来西亚林吉特": "MYR", "菲律宾比索": "PHP", "丹麦克朗": "DKK",
}


def get_current_temp(city_cn):
    """获取指定城市（中文名）的当前真实气温（摄氏度），作为天气题的标准答案。
    返回 (温度:int, 观测时间:str)；多次尝试仍失败则返回 (None, "")。"""
    import time
    for attempt in range(3):                       # 最多重试 3 次（应对偶发的网络抖动/限流）
        try:
            resp = requests.get(
                f"https://wttr.in/{CITIES[city_cn]}",
                params={"format": "j1"},           # j1 = JSON 格式输出
                headers={"User-Agent": "curl/8.0"},  # wttr.in 对 curl 风格的 UA 更友好
                timeout=10,
            )
            cur = resp.json()["current_condition"][0]
            obs_time = cur.get("localObsDateTime", "")
            return int(cur["temp_C"]), obs_time
        except Exception:
            if attempt < 2:
                time.sleep(2)                      # 失败后稍等再试
    return None, ""


def get_current_rate(cur_cn):
    """获取指定货币（中文名）兑人民币的当前真实汇率，作为汇率题的标准答案。
    返回 (汇率:float, 数据日期:str)；两个数据源都失败则返回 (None, "")。"""
    import time
    code = CURRENCIES[cur_cn]
    for attempt in range(2):                       # 每个数据源最多试 2 次
        # 数据源一：open.er-api.com（免 Key，每小时更新）
        try:
            resp = requests.get(f"https://open.er-api.com/v6/latest/{code}", timeout=10)
            data = resp.json()
            return float(data["rates"]["CNY"]), data.get("time_last_update_utc", "")
        except Exception:
            pass
        # 数据源二（备用）：jsdelivr 托管的 currency-api（免 Key，每日更新）
        try:
            resp = requests.get(
                f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{code.lower()}.json",
                timeout=10)
            data = resp.json()
            return float(data[code.lower()]["cny"]), data.get("date", "")
        except Exception:
            if attempt == 0:
                time.sleep(2)                      # 失败后稍等再试
    return None, ""


def load_testset():
    """生成混合评测题目列表：15 道实时气温题 + 15 道实时汇率题 = 30 题。
    返回 [(问题文本, 类型, 对象)]，类型为 "weather" 或 "fx"。"""
    testset = []
    for city in CITIES:
        testset.append((f"{city}现在的气温是多少摄氏度？", "weather", city))
    for cur in CURRENCIES:
        testset.append((f"现在1{cur}兑换多少人民币？", "fx", cur))
    return testset


def get_ground_truth(kind, key):
    """实时获取某道题的标准答案（数值）。失败返回 None。"""
    if kind == "weather":
        return get_current_temp(key)[0]
    return get_current_rate(key)[0]


def answer_correct(answer_text, truth, kind):
    """判对：天气题用 ±3℃ 绝对容差，汇率题用 ±1.5% 相对容差。
    标准答案获取失败（truth 为 None）时本题不计分。
    天气题额外做两层过滤，避免误判：
    ① 只检查含温度关键词的片段（防止把"16小时前"等时间数字当成气温）；
    ② 跳过含"最低/最高/体感/预报/明天/未来/平均"的片段
       （防止把最低气温、体感温度、预报温度误当作当前气温）。"""
    if truth is None:
        return None
    if kind == "weather":
        temp_words = ("气温", "温度", "℃", "°c", "°", "度")
        exclude_words = ("最低", "最高", "体感", "预报", "明天", "明日", "后天", "未来", "平均")
        for seg in re.split(r"[。！？；;，,\n]", answer_text.lower()):
            if not any(w in seg for w in temp_words):
                continue
            if any(w in seg for w in exclude_words):
                continue
            for num in re.findall(r"-?\d+\.?\d*", seg):
                if abs(float(num) - truth) <= 3:
                    return True
        return False
    # 汇率题：回答中的任意数值与真实汇率相对误差不超过 ±1.5% 即算答对
    for num in re.findall(r"-?\d+\.?\d*", answer_text):
        value = float(num)
        if truth * 0.985 <= value <= truth * 1.015:
            return True
    return False


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
