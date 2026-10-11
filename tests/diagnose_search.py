"""只检查公开搜索页面，不加载 .env、不调用模型、不改核心搜索函数。"""
import argparse
import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def current_search_url():
    source = ast.parse((ROOT / 'search_rag_example.py').read_text(encoding='utf-8'))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'bing_search')
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'url' for target in node.targets))
    return ast.literal_eval(assignment.value)


def inspect_page(query, url):
    record = {'query': query, 'endpoint': url}
    try:
        response = requests.get(url, params={'q': query},
                                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
                                timeout=10)
    except requests.RequestException as exc:
        record['request_error'] = type(exc).__name__
        return record
    soup = BeautifulSoup(response.text, 'html.parser')
    final = urlsplit(response.url)
    items = soup.select('li.b_algo')
    record.update(
        http_status=response.status_code,
        final_host=final.hostname, final_path=final.path,
        redirects=[r.status_code for r in response.history],
        content_type=response.headers.get('Content-Type'),
        response_bytes=len(response.content),
        page_title=soup.title.get_text(strip=True) if soup.title else '',
        result_items=len(items),
        items_with_h2=sum(bool(item.find('h2')) for item in items),
        items_with_p=sum(bool(item.find('p')) for item in items),
        items_with_h2_and_p=sum(bool(item.find('h2') and item.find('p')) for item in items),
        challenge_elements=len(soup.select('#b_captcha, #b_captcha_verify, iframe[src*="captcha"]')),
    )
    # 将这次响应回放给原函数，核对原解析器结果，不再发送请求。
    source = ast.parse((ROOT / 'search_rag_example.py').read_text(encoding='utf-8'))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'bing_search')
    namespace = {'requests': SimpleNamespace(get=lambda *args, **kwargs: response), 'BeautifulSoup': BeautifulSoup}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'original_bing_search', 'exec'), namespace)
    record['original_parser_snippet_count'] = len(namespace['bing_search'](query))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', action='append', help='可以重复提供关键词')
    parser.add_argument('--compare-global', action='store_true', help='额外检查 www.bing.com；不修改原搜索地址')
    parser.add_argument('--compare-legacy', action='store_true', help='额外检查旧 cn.bing.com 入口')
    args = parser.parse_args()
    queries = args.query or ['世卫组织疟疾疫苗', 'NQ open dataset']
    urls = [current_search_url()]
    if args.compare_global and 'https://www.bing.com/search' not in urls:
        urls.append('https://www.bing.com/search')
    if args.compare_legacy and 'https://cn.bing.com/search' not in urls:
        urls.append('https://cn.bing.com/search')
    results = []
    for url in urls:
        for query in queries:
            record = inspect_page(query, url)
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    timestamp = datetime.now(timezone(timedelta(hours=8)))
    out = ROOT / 'outputs' / 'diagnostics'
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'search_{timestamp:%Y%m%d_%H%M%S_%f}.json'
    path.write_text(json.dumps({'time': timestamp.isoformat(), 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[诊断记录] {path}', flush=True)


if __name__ == '__main__':
    main()
