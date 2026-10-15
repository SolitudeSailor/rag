"""维基百科官方API优先检索；不加载配置或调用模型。"""
import base64
from contextlib import contextmanager
import contextvars
import hashlib
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from scripts.runtime_tools import search_runtime


POLICY = 'wikipedia-web-runtime-v2'
WIKI_APIS = {'en': 'https://en.wikipedia.org/w/api.php',
             'zh': 'https://zh.wikipedia.org/w/api.php'}
BING_URL = 'https://www.bing.com/search'
TIMEOUT = 10
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
WIKI_HEADERS = {'User-Agent': 'RAGCourseProject/1.0 '
                '(https://github.com/SolitudeSailor/rag; educational retrieval)'}
AUTHORITY_HOSTS = ('nobelprize.org', 'who.int', 'medlineplus.gov', 'nhc.gov.cn',
                   'nmpa.gov.cn', 'chinacdc.cn', 'stats.gov.cn')
ACTIVE = contextvars.ContextVar('retrieval_attempts', default=None)
STOPWORDS = set('a an the of for in on at to and or is are was were be who what when where '
                'which how does did do has have with from by it its this that '
                'site baike baidu com wikipedia'.split())


def configuration():
    return {'policy': POLICY, 'primary_source': '维基百科',
            'primary_apis': WIKI_APIS.copy(), 'language_rule': '中文实体用zh，其余用en',
            'discovery_query': 'MediaWiki list=search，限定正文命名空间0',
            'fallback_source': 'Bing 网页搜索', 'fallback_url': BING_URL,
            'candidate_limit': 6, 'excerpt_chars': 1800,
            'auxiliary_rule': '涉及年份或时效词时，即使有百科摘录也补充网页搜索及权威引用正文',
            'authority_hosts': list(AUTHORITY_HOSTS),
            'runtime_tools': ['system_clock UTC+08:00', 'Open-Meteo geocoding and forecast'],
            'runtime_source_sha256': hashlib.sha256(Path(__file__).with_name('runtime_tools.py').read_bytes()).hexdigest(),
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


@contextmanager
def capture_attempts(events, persist=None):
    token = ACTIVE.set((events, persist))
    try:
        yield
    finally:
        ACTIVE.reset(token)


def _record(event):
    active = ACTIVE.get()
    if active is not None:
        events, persist = active
        events.append(event)
        if persist:
            persist()


def _fetch(url, source, query, params=None):
    event = {'source': source, 'query': query, 'request_host': urlsplit(url).hostname,
             'request_path': urlsplit(url).path, 'status': 'running'}
    _record(event)
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        final = urlsplit(response.url)
        event.update(http_status=response.status_code, final_host=final.hostname,
                     final_path=final.path, redirects=[r.status_code for r in response.history],
                     page_title=soup.title.get_text(' ', strip=True) if soup.title else '',
                     response_bytes=len(response.content))
        # 验证页即使返回200也不是词条或搜索证据。
        if (any(word in final.path.lower() for word in ('captcha', 'anticrawl'))
                or '验证' in event['page_title']
                or soup.select_one('#b_captcha, #b_captcha_verify, iframe[src*="captcha"]')):
            event['status'] = 'challenge'
            return None, None, event
        if response.status_code != 200:
            event['status'] = 'http_error'
            return None, None, event
        event['status'] = 'ok'
        return soup, response.url, event
    except requests.RequestException as exc:
        event['status'] = type(exc).__name__
        return None, None, event
    finally:
        active = ACTIVE.get()
        if active is not None and active[1]:
            active[1]()


def _clean_query(query):
    # 信息源由程序控制，不采纳模型附带的site:或来源扩展指令。
    return re.sub(r'\s+', ' ', re.sub(r'\bsite:\S+', '', query, flags=re.I)).strip()


def _terms(query):
    return [word for word in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', query.casefold())
            if word not in STOPWORDS and len(word) >= 2]


def _entity_query(query):
    quoted = re.search(r'["“]([^"”]+)["”]', query)
    return quoted.group(1).strip() if quoted else query


def _years(query):
    return re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', query)


def _lookup_entity(query):
    entity = _entity_query(query)
    entity = re.sub(r'(?<!\d)(?:19|20)\d{2}(?!\d)\s*年?', '', entity).strip()
    entity = re.sub(r'\s*(?:的)?(?:获奖者名单|获奖名单|获奖者|得主|冠军|名单|是谁|有哪些)\s*$', '', entity).strip()
    return entity or _entity_query(query)


def _term_present(term, text):
    if re.fullmatch(r'[a-z0-9]+', term):
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text))
    return term in text


def _relevant(query, title, text):
    """保守的词面检查，只排除明显无关项，不声称证明答案正确。"""
    terms = _terms(query)
    if not terms:
        return False
    title, text = title.casefold(), text.casefold()
    matches = sum(_term_present(term, title) or _term_present(term, text) for term in terms)
    return matches / len(terms) >= 0.6


def _wiki_url(url, language):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname == f'{language}.wikipedia.org'
                and parsed.path.startswith('/wiki/') and not parsed.username and not parsed.password)
    except ValueError:
        return False


def _result_url(href):
    try:
        return _decode_result_url(href)
    except (ValueError, UnicodeDecodeError):
        return None


def _decode_result_url(href):
    url = urljoin(BING_URL, href)
    parsed = urlsplit(url)
    # Bing常将真实来源放在u=a1+URL安全Base64中。
    if parsed.hostname in ('www.bing.com', 'bing.com') and parsed.path.startswith('/ck/'):
        encoded = parse_qs(parsed.query).get('u', [''])[0]
        if not encoded.startswith('a1'):
            return None
        try:
            raw = encoded[2:]
            url = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.hostname in ('www.bing.com', 'bing.com')):
        return None
    return url


def _wiki_request(language, query, source, parameters):
    url = WIKI_APIS[language]
    params = {'action': 'query', 'format': 'json', 'formatversion': 2, 'maxlag': 5,
              **parameters}
    event = {'source': source, 'query': query, 'language': language,
             'request_host': urlsplit(url).hostname, 'request_path': '/w/api.php',
             'parameters': parameters.copy(), 'status': 'running'}
    _record(event)
    try:
        response = requests.get(url, params=params, headers=WIKI_HEADERS, timeout=TIMEOUT)
        final = urlsplit(response.url)
        event.update(http_status=response.status_code, final_host=final.hostname,
                     final_path=final.path, response_bytes=len(response.content))
        if response.status_code != 200:
            event['status'] = 'http_error'
            return None, event
        returned = parse_qs(final.query)
        if (final.scheme != 'https' or final.hostname != f'{language}.wikipedia.org'
                or final.path != '/w/api.php'
                or any(returned.get(key, [''])[0] != str(params[key])
                       for key in ('action', 'titles', 'srsearch', 'oldid', 'page') if key in params)):
            event['status'] = 'query_mismatch'
            return None, event
        try:
            data = response.json()
        except ValueError:
            event['status'] = 'invalid_json'
            return None, event
        if not isinstance(data, dict):
            event['status'] = 'invalid_json'
            return None, event
        if 'error' in data:
            event['status'] = 'api_error'
            return None, event
        root = 'parse' if params['action'] == 'parse' else 'query'
        if not isinstance(data.get(root), dict):
            event['status'] = 'invalid_response'
            return None, event
        event['status'] = 'ok'
        return data[root], event
    except requests.RequestException as exc:
        event['status'] = type(exc).__name__
        return None, event
    finally:
        active = ACTIVE.get()
        if active is not None and active[1]:
            active[1]()


def _resolved_titles(entity, data):
    normalize = lambda text: text.replace('_', ' ').strip().casefold()
    allowed = {normalize(entity)}
    mappings = [item for key in ('normalized', 'converted', 'redirects')
                for item in data.get(key, []) if isinstance(item, dict)]
    for _ in range(len(mappings) + 1):
        for item in mappings:
            if normalize(item.get('from', '')) in allowed:
                allowed.add(normalize(item.get('to', '')))
    return allowed


def _wiki_excerpt(text, query):
    paragraphs = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()
                  if len(line.strip()) >= 20 and not line.strip().startswith('==')]
    if not paragraphs:
        return ''
    terms = _terms(re.sub(r'["“][^"”]+["”]', '', query)) or _terms(query)
    years = _years(query)
    ranked = sorted(enumerate(paragraphs),
                    key=lambda pair: (-100 * sum(year in pair[1] for year in years)
                                      - sum(_term_present(term, pair[1].casefold()) for term in terms), pair[0]))
    chosen = sorted({0, *(i for i, _ in ranked[:2])})
    # 每段限长，保证导语不会挤掉与所问属性相关的正文段落。
    return '\n'.join(paragraphs[i][:600] for i in chosen)[:1800]


def _authority_url(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and any(host == domain or host.endswith('.' + domain) for domain in AUTHORITY_HOSTS))
    except ValueError:
        return False


def _html_content(html, query):
    """保留表头和跨行值，避免名单中的年份、类别和得主失去对应关系。"""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.select('script, style, nav, footer, aside, .navbox, .vertical-navbox'):
        tag.decompose()
    container = soup.select_one('.mw-parser-output, article, main') or soup
    blocks = [node.get_text(' ', strip=True) for node in container.select('p')
              if len(node.get_text(' ', strip=True)) >= 20]
    related_links = []
    years = _years(query)
    for table in container.select('table'):
        headers, spanning = [], {}
        for row in table.select('tr'):
            cells = row.find_all(['th', 'td'], recursive=False)
            if not cells:
                continue
            values = {index: value for index, (remaining, value) in spanning.items() if remaining > 0}
            spanning = {index: (remaining - 1, value) for index, (remaining, value) in spanning.items()
                        if remaining > 1}
            column = 0
            for cell in cells:
                while column in values:
                    column += 1
                value = cell.get_text(' ', strip=True)
                span = int(cell.get('rowspan', 1)) if str(cell.get('rowspan', 1)).isdigit() else 1
                width = int(cell.get('colspan', 1)) if str(cell.get('colspan', 1)).isdigit() else 1
                for index in range(column, column + min(width, 30)):
                    values[index] = value
                    if span > 1:
                        spanning[index] = (min(span - 1, 100), value)
                column += min(width, 30)
            ordered = [values.get(index, '') for index in range(max(values, default=-1) + 1)]
            if all(cell.name == 'th' for cell in cells) and not any(year in ' '.join(ordered) for year in years):
                headers = ordered
                continue
            heading = table.find_previous(['h2', 'h3', 'h4'])
            context = heading.get_text(' ', strip=True) if heading else ''
            block = context + ' | ' + ' | '.join(
                f'{headers[i]}：{value}' if i < len(headers) and headers[i] else value
                for i, value in enumerate(ordered) if value)
            if len(block) >= 20:
                blocks.append(block)
            if years and all(year in block for year in years):
                for link in row.select('a[href]'):
                    href = link['href']
                    if href.startswith('#'):
                        reference = soup.find(id=href[1:])
                        urls = [a.get('href', '') for a in reference.select('a[href]')] if reference else []
                    else:
                        urls = [href]
                    related_links.extend(url for url in urls if _authority_url(url))
    return '\n'.join(blocks), list(dict.fromkeys(related_links))


def _wiki_html(page, query, language):
    revisions = page.get('revisions') or [{}]
    revision = revisions[0].get('revid')
    params = {'action': 'parse', 'prop': 'text|revid', 'disableeditsection': 1}
    params.update({'oldid': revision} if revision else {'page': page['title']})
    if language == 'zh':
        params['variant'] = 'zh-cn'
    data, event = _wiki_request(language, query, 'wikipedia_html', params)
    if data is None:
        return '', []
    if data.get('pageid') != page.get('pageid'):
        event['status'] = 'entity_mismatch'
        return '', []
    html = data.get('text', '')
    if isinstance(html, dict):
        html = html.get('*', '')
    if not isinstance(html, str):
        event['status'] = 'invalid_response'
        return '', []
    text, links = _html_content(html, query)
    event.update(status='parsed' if text else 'no_content', related_links=links)
    return text, links


def _wiki_entry(entity, query, language):
    data, event = _wiki_request(language, query, 'wikipedia_entry', {
        'titles': entity, 'redirects': 1, 'converttitles': 1,
        'prop': 'extracts|info|pageprops|revisions', 'explaintext': 1, 'exlimit': 1,
        'inprop': 'url', 'rvprop': 'ids|timestamp'})
    if data is None:
        return None
    pages = data.get('pages', [])
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        event['status'] = 'invalid_response'
        return None
    page = pages[0]
    title, url = page.get('title', ''), page.get('fullurl', '')
    if 'missing' in page or 'invalid' in page or page.get('ns') != 0:
        event['status'] = 'not_entry'
        return None
    if 'disambiguation' in (page.get('pageprops') or {}):
        event['status'] = 'disambiguation'
        return None
    if (title.replace('_', ' ').strip().casefold() not in _resolved_titles(entity, data)
            or not _wiki_url(url, language)):
        event['status'] = 'entity_mismatch'
        return None
    text = page.get('extract', '')
    links = []
    if _years(query) or not text:
        parsed_text, links = _wiki_html(page, query, language)
        if parsed_text:
            text = parsed_text
    excerpt = _wiki_excerpt(text, query) if isinstance(text, str) else ''
    if not excerpt:
        event['status'] = 'no_content'
        return None
    if _years(query) and not all(year in excerpt for year in _years(query)):
        event['status'] = 'insufficient_constraints'
        return None
    revisions = page.get('revisions') or [{}]
    event.update(status='accepted', source_url=url, page_title=title,
                 pageid=page.get('pageid'), excerpt_chars=len(excerpt),
                 related_links=links,
                 revision_id=revisions[0].get('revid'), revision_time=revisions[0].get('timestamp'))
    label = '中文' if language == 'zh' else '英文'
    return f'【维基百科·{label}｜{title}】\n来源：{url}\n词条摘录：{excerpt}'


def _wiki_candidates(query, language):
    data, event = _wiki_request(language, query, 'wikipedia_discovery', {
        'list': 'search', 'srsearch': _lookup_entity(query), 'srnamespace': 0, 'srlimit': 6,
        'srprop': 'snippet|redirecttitle'})
    if data is None:
        return []
    hits = data.get('search', [])
    if not isinstance(hits, list):
        event['status'] = 'invalid_response'
        return []
    results, seen = [], set()
    for hit in hits:
        if not isinstance(hit, dict) or hit.get('ns') != 0:
            continue
        title = hit.get('title', '')
        snippet = BeautifulSoup(hit.get('snippet', ''), 'html.parser').get_text(' ', strip=True)
        if title and title not in seen and _relevant(
                _lookup_entity(query), title + ' ' + hit.get('redirecttitle', ''), snippet):
            results.append(title)
            seen.add(title)
    event.update(status='candidates' if results else 'no_relevant_results', candidate_count=len(results))
    return results


def _bing_candidates(query):
    actual_query = query
    soup, final_url, event = _fetch(BING_URL, 'bing_fallback',
                                    actual_query, {'q': actual_query})
    if soup is None:
        return []
    final = urlsplit(final_url)
    returned = parse_qs(final.query).get('q', [''])[0]
    if (final.hostname not in ('www.bing.com', 'bing.com') or final.path != '/search'
            or returned != actual_query):
        event['status'] = 'query_mismatch'
        return []
    input_box = soup.select_one('input[name="q"]')
    if input_box and input_box.get('value', actual_query) != actual_query:
        event['status'] = 'query_mismatch'
        return []
    results, seen = [], set()
    for item in soup.select('li.b_algo')[:6]:
        heading, summary = item.find('h2'), item.find('p')
        anchor = heading.find('a', href=True) if heading else None
        if not anchor or not summary:
            continue
        url = _result_url(anchor['href'])
        title, text = heading.get_text(' ', strip=True), summary.get_text(' ', strip=True)
        if not url or url in seen:
            continue
        if not _relevant(_lookup_entity(query), title, text):
            continue
        seen.add(url)
        results.append((url, title, text))
    event.update(status='accepted' if results else 'no_relevant_results',
                 accepted_results=len(results))
    return results


def _web_supplement(query, links, limit=2):
    candidates = _bing_candidates(query)
    urls = list(dict.fromkeys([*links, *(url for url, _, _ in candidates if _authority_url(url))]))
    results = []
    for url in urls[:limit]:
        soup, final_url, event = _fetch(url, 'web_page', query)
        if soup is None:
            continue
        if not _authority_url(final_url):
            event['status'] = 'source_mismatch'
            continue
        text, _ = _html_content(str(soup), query)
        excerpt = _wiki_excerpt(text, query)
        if not excerpt or (_years(query) and not all(year in excerpt for year in _years(query))):
            event['status'] = 'no_relevant_content'
            continue
        event.update(status='accepted', source_url=final_url)
        results.append(f'【辅助网页正文｜{event["page_title"]}】\n来源：{final_url}\n正文摘录：{excerpt}')
    if not results:
        results = [f'【Bing 网页摘要｜{title}】\n来源：{url}\n摘要：{text[:1200]}\n'
                   '说明：此条仅为搜索摘要，未读取网页正文。' for url, title, text in candidates[:limit]]
    return results


def search(query, top_k=3):
    query = _clean_query(query)
    if not query or top_k <= 0:
        return []
    runtime = search_runtime(query, _record)
    if runtime is not None:
        return runtime
    entity = _lookup_entity(query)
    language = 'zh' if re.search(r'[\u4e00-\u9fff]', entity) else 'en'
    # 内部也收集尝试，方便将已读取词条的引用用于辅助官网核验。
    parent = ACTIVE.get()
    attempts = parent[0] if parent else []
    with capture_attempts(attempts, parent[1] if parent else None):
        excerpt = _wiki_entry(entity, query, language)
        excerpts = [excerpt] if excerpt else []
        if not excerpts:
            for title in _wiki_candidates(query, language)[:top_k]:
                if title.casefold() == entity.casefold():
                    continue
                excerpt = _wiki_entry(title, query, language)
                if excerpt:
                    excerpts.append(excerpt)
        temporal = bool(_years(query) or re.search(r'最新|目前|当前|今天|本周|latest|current|today|recent', query, re.I))
        links = [url for attempt in attempts if attempt.get('status') == 'accepted'
                 for url in attempt.get('related_links', [])]
        supplemental = _web_supplement(query, links) if temporal or not excerpts else []
    if excerpts:
        print('  [检索] 已读取维基百科页面摘录，答案仍需核对', flush=True)
    if temporal or not excerpts:
        print(f'  [检索] 辅助网页检索取得 {len(supplemental)} 条材料', flush=True)
    if supplemental and top_k > 1:
        auxiliary_count = min(len(supplemental), top_k - bool(excerpts))
        return excerpts[:top_k - auxiliary_count] + supplemental[:auxiliary_count]
    return (excerpts + supplemental)[:top_k]
