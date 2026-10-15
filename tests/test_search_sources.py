"""离线验证官方API、词条身份、消歧义、回退及并发日志隔离。"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import search_sources as sources
from scripts.experiment_utils import capture, install


def response(html, url, status=200, payload=None):
    return SimpleNamespace(text=html, url=url, status_code=status, history=[],
                           content=html.encode('utf-8'), encoding='utf-8',
                           json=lambda: payload if payload is not None else json.loads(html))


def wiki(data, params, language='en'):
    payload = {'query': data}
    return response(json.dumps(payload), sources.WIKI_APIS[language] + '?' + urlencode(params),
                    payload=payload)


def article(title='Photosynthesis', language='en', **fields):
    return {'pages': [{'pageid': 123, 'ns': 0, 'title': title,
            'fullurl': f'https://{language}.wikipedia.org/wiki/' + title.replace(' ', '_'),
            'extract': 'Photosynthesis converts light energy into chemical energy.\n'
                       'In plants, photosynthesis occurs in chloroplasts and releases oxygen.',
            'revisions': [{'revid': 456, 'timestamp': '2026-10-14T00:00:00Z'}], **fields}]}


def bing(query, links):
    items = ''.join(f'<li class="b_algo"><h2><a href="{url}">{title}</a></h2>'
                    f'<p>{text}</p></li>' for url, title, text in links)
    return response('<title>Search</title><ul>' + items + '</ul>',
                    sources.BING_URL + '?' + urlencode({'q': query}))


class SearchSourceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_primary_article_avoids_secondary_requests(self):
        events = []
        with patch.object(sources.requests, 'get', side_effect=lambda url, **kw: wiki(article(), kw['params'])) as get:
            with sources.capture_attempts(events):
                result = sources.search('"Photosynthesis" chloroplast')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs['params']['titles'], 'Photosynthesis')
        self.assertIn('维基百科·英文', result[0])
        self.assertIn('来源：https://en.wikipedia.org/wiki/Photosynthesis', result[0])
        self.assertIn('chloroplasts', result[0])
        self.assertEqual(events[0]['revision_id'], 456)
        self.assertEqual(events[0]['status'], 'accepted')

    def test_chinese_entity_selects_chinese_api(self):
        def fetch(url, **kw):
            self.assertEqual(url, sources.WIKI_APIS['zh'])
            return wiki(article('地球', 'zh', extract='地球是太阳系的一颗行星，平均半径约为6371千米，拥有大量液态水。'),
                        kw['params'], 'zh')
        with patch.object(sources.requests, 'get', side_effect=fetch):
            result = sources.search('"地球" 半径')
        self.assertIn('维基百科·中文', result[0])
        self.assertIn('6371', result[0])

    def test_api_redirects_resolve_alias_without_guessing(self):
        data = article('United States', extract='The United States is a country located primarily in North America.')
        data['redirects'] = [{'from': 'USA', 'to': 'United States'}]
        with patch.object(sources.requests, 'get', side_effect=lambda url, **kw: wiki(data, kw['params'])):
            self.assertIn('United States', sources.search('"USA" location')[0])

    def test_wrong_entity_domain_and_namespace_are_rejected(self):
        cases = [article('Other Page'),
                 article(fullurl='https://en.wikipedia.org.evil.example/wiki/Photosynthesis'),
                 article(ns=1), article(extract='')]
        for data in cases:
            with self.subTest(data=data['pages'][0]['title']):
                with patch.object(sources.requests, 'get', side_effect=lambda url, **kw: wiki(data, kw['params'])):
                    self.assertIsNone(sources._wiki_entry('Photosynthesis', 'Photosynthesis', 'en'))

    def test_disambiguation_uses_search_and_real_article_content(self):
        def fetch(url, **kw):
            params = kw['params']
            if 'srsearch' in params:
                self.assertEqual(params['srnamespace'], 0)
                return wiki({'search': [{'ns': 0, 'title': 'Mercury', 'snippet': 'Mercury'},
                                        {'ns': 0, 'title': 'Mercury (planet)', 'snippet': 'Mercury is a planet.'}]}, params)
            if params['titles'] == 'Mercury':
                return wiki(article('Mercury', pageprops={'disambiguation': ''}), params)
            return wiki(article('Mercury (planet)', extract='Mercury is the smallest planet in the Solar System.'), params)
        events = []
        with patch.object(sources.requests, 'get', side_effect=fetch) as get:
            with sources.capture_attempts(events):
                result = sources.search('"Mercury" planet')
        self.assertEqual(get.call_count, 3)
        self.assertIn('Mercury (planet)', result[0])
        self.assertEqual([e['status'] for e in events], ['disambiguation', 'candidates', 'accepted'])

    def test_empty_search_falls_back_and_filters_irrelevant_snippets(self):
        query = 'Photosynthesis'
        def fetch(url, **kw):
            if url == sources.BING_URL:
                return bing(query, [('https://example.org/australia', 'Australia time', 'Current time in Australia'),
                                    ('https://example.org/science', query, 'Photosynthesis converts light to chemical energy.')])
            params = kw['params']
            return wiki({'search': []} if 'srsearch' in params else article(missing=True), params)
        events = []
        with patch.object(sources.requests, 'get', side_effect=fetch):
            with sources.capture_attempts(events):
                result = sources.search(query)
        self.assertEqual(len(result), 1)
        self.assertIn('Bing 网页摘要', result[0])
        self.assertNotIn('维基百科', result[0])
        self.assertNotIn('Australia', result[0])
        self.assertEqual([e['status'] for e in events], ['not_entry', 'no_relevant_results', 'accepted'])

    def test_timeouts_are_retained_as_attempts(self):
        events = []
        with patch.object(sources.requests, 'get', side_effect=requests.Timeout):
            with sources.capture_attempts(events):
                self.assertEqual(sources.search('Photosynthesis'), [])
        self.assertEqual([e['status'] for e in events], ['Timeout'] * 3)

    def test_invalid_json_api_errors_and_wrong_queries_are_rejected(self):
        for kind in ('invalid_json', 'api_error', 'query_mismatch'):
            events = []
            def fetch(url, **kw):
                fake = wiki(article(), kw['params'])
                if kind == 'invalid_json':
                    fake.json = lambda: json.loads('<html>error</html>')
                elif kind == 'api_error':
                    fake.json = lambda: {'error': {'code': 'maxlag'}}
                else:
                    fake.url = sources.WIKI_APIS['en'] + '?action=query&titles=wrong'
                return fake
            with self.subTest(kind=kind), patch.object(sources.requests, 'get', side_effect=fetch):
                with sources.capture_attempts(events):
                    self.assertIsNone(sources._wiki_entry('Photosynthesis', 'Photosynthesis', 'en'))
                self.assertEqual(events[0]['status'], kind)

    def test_property_excerpt_includes_body_beyond_long_lead(self):
        text = 'General introductory background. ' * 100 + '\n' + \
               'A historical paragraph with unrelated background details.\n' + \
               'Chloroplast organelles perform the photosynthesis reactions in plants.'
        excerpt = sources._wiki_excerpt(text, '"Photosynthesis" chloroplast')
        self.assertIn('Chloroplast organelles', excerpt)
        self.assertLessEqual(len(excerpt), 1800)

    def test_bing_redirects_boundaries_and_bad_urls(self):
        url = 'https://example.org/real-source'
        encoded = 'a1' + base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')
        self.assertEqual(sources._result_url('https://www.bing.com/ck/a?u=' + encoded), url)
        self.assertFalse(sources._relevant('rice', 'Price', 'Prices have increased.'))
        self.assertIsNone(sources._result_url('https://[invalid'))
        self.assertFalse(sources._wiki_url('https://[invalid', 'en'))
        fake = bing('Photosynthesis', [('https://example.org/science', 'Photosynthesis', 'Photosynthesis')])
        fake.url = sources.BING_URL + '?q=other'
        with patch.object(sources.requests, 'get', return_value=fake):
            self.assertEqual(sources._bing_candidates('Photosynthesis'), [])

    def test_parallel_source_logs_remain_per_question(self):
        rag = SimpleNamespace(bing_search=sources.search)
        install(rag)
        def fetch(url, **kw):
            return wiki(article(kw['params']['titles']), kw['params'])
        def run(query):
            events = []
            with capture(events):
                result = rag.bing_search(query)
            return query, result, events
        with patch.object(sources.requests, 'get', side_effect=fetch):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, ['Photosynthesis', 'Chloroplast']))
        for query, result, events in results:
            self.assertEqual(len(result), 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['selected_sources'], ['wikipedia_entry'])
            self.assertEqual([a['query'] for a in events[0]['source_attempts']], [query])

    def test_year_is_a_constraint_not_required_in_article_title(self):
        self.assertEqual(sources._lookup_entity('2030年示例科学奖 得主'), '示例科学奖')
        self.assertEqual(sources._lookup_entity('"2030年示例科学奖"'), '示例科学奖')
        self.assertFalse(sources._relevant('诺贝尔物理学奖', '诺贝尔生理学或医学奖', '2030年得主名单'))

    def test_table_headers_and_rowspan_preserve_year_recipient_relationship(self):
        html = '''<div class="mw-parser-output"><h2>示例科学奖得主</h2>
        <table class="wikitable"><tr><th>年份</th><th>姓名</th><th>类别</th></tr>
        <tr><td rowspan="2">2030</td><td>示例甲</td><td>物理学 <a href="#ref1">1</a></td></tr>
        <tr><td>示例乙</td><td>物理学</td></tr></table>
        <li id="ref1"><a href="https://www.who.int/example/2030">官方引用</a></li></div>'''
        text, links = sources._html_content(html, '2030年示例科学奖 得主')
        self.assertIn('年份：2030 | 姓名：示例甲 | 类别：物理学', text)
        self.assertIn('年份：2030 | 姓名：示例乙 | 类别：物理学', text)
        self.assertEqual(links, ['https://www.who.int/example/2030'])
        self.assertIn('示例乙', sources._wiki_excerpt(text, '2030年示例科学奖 得主'))

    def test_temporal_query_does_not_stop_after_a_wikipedia_excerpt(self):
        with patch.object(sources, '_wiki_entry', return_value='【维基百科】2030年示例奖得主'), \
                patch.object(sources, '_web_supplement', return_value=['【辅助网页正文】2030年官方公告']) as web:
            result = sources.search('2030年示例奖 得主')
        web.assert_called_once()
        self.assertEqual(len(result), 2)
        self.assertIn('辅助网页正文', result[1])

    def test_missing_requested_year_is_not_accepted_as_year_specific_evidence(self):
        with patch.object(sources.requests, 'get', side_effect=lambda url, **kw: wiki(article(), kw['params'])), \
                patch.object(sources, '_wiki_html', return_value=('A general article without the requested year.', [])):
            events = []
            with sources.capture_attempts(events):
                self.assertIsNone(sources._wiki_entry('Photosynthesis', '2030 Photosynthesis award', 'en'))
        self.assertEqual(events[0]['status'], 'insufficient_constraints')


if __name__ == '__main__':
    unittest.main()
