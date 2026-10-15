"""验证实际信息源优先级与公开网页；不加载 .env、不调用模型。"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.search_sources import BING_URL, capture_attempts, configuration, search


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
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', action='append', help='可以重复提供关键词')
    parser.add_argument('--compare-global', action='store_true', help='额外检查未过滤的 www.bing.com 页面')
    parser.add_argument('--compare-legacy', action='store_true', help='额外检查旧 cn.bing.com 入口')
    args = parser.parse_args()
    queries = args.query or ['光合作用', 'photosynthesis']
    urls = []
    if args.compare_global:
        urls.append(BING_URL)
    if args.compare_legacy and 'https://cn.bing.com/search' not in urls:
        urls.append('https://cn.bing.com/search')
    results = []
    for query in queries:
        attempts = []
        with capture_attempts(attempts):
            snippets = search(query)
        record = {'query': query, 'snippet_count': len(snippets),
                  'snippets': snippets, 'source_attempts': attempts}
        results.append(record)
        print(json.dumps({'query': query, 'snippet_count': len(snippets),
                          'attempt_statuses': [f"{a['source']}:{a['status']}" for a in attempts]},
                         ensure_ascii=False), flush=True)
    for url in urls:
        for query in queries:
            record = inspect_page(query, url)
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    timestamp = datetime.now(timezone(timedelta(hours=8)))
    out = ROOT / 'outputs' / 'diagnostics'
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'search_{timestamp:%Y%m%d_%H%M%S_%f}.json'
    path.write_text(json.dumps({'time': timestamp.isoformat(), 'search_configuration': configuration(),
                               'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[诊断记录] {path}', flush=True)


if __name__ == '__main__':
    main()
