"""新版实验的离线边界、证据隔离、全流程导出验证；模拟数据只存在临时目录。"""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import search_rag_weather_fx as rag
from scripts import evaluate, weather_experiment as exp
from scripts.export_experiment_summary import export, merge_summary
from scripts.weather_prompts import PROMPTS


def fixture():
    sample = exp.load_sample()
    truths = {str(i): dict(type=item['type'], item=item['item'], value=20 if item['type'] == 'weather' else 7.2)
              for i, item in enumerate(sample)}
    usage = [dict(prompt_tokens=10, completion_tokens=2, total_tokens=12)]
    raw = {f'live_{i}_{mode}': dict(question=item['question'], mode=mode, status='ok',
           answer='当前气温20℃' if item['type'] == 'weather' else f'1{item["item"]}=7.2人民币',
           usage=copy.deepcopy(usage), prompt_tokens=10, completion_tokens=2, total_tokens=12, search_log=[])
           for i, item in enumerate(sample) for mode in ('blind', 'rag')}
    return sample, raw, truths


def archive_fixture(directory, sample, raw, truths):
    hashes = {}
    for name in exp.SOURCE_FILES:
        dest = directory/'source'/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT/name).read_bytes())
        hashes[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    metadata = dict(schema=exp.STANDARD_ID, time_injection=True, time_zone='Asia/Shanghai',
                    max_search=2, top_k=5, source_sha256=hashes)
    for name, value in [('metadata.json', metadata), ('sample30.json', sample),
                        ('raw_results.json', raw), ('truths.json', truths), ('prompts.json', PROMPTS)]:
        exp.write_json(directory/name, value)
    exp.write_results(directory, sample, raw, truths)


class WeatherExperimentTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_teacher_helpers_and_v1_remain_unchanged(self):
        current = ast.parse((ROOT/'search_rag_weather_fx.py').read_text(encoding='utf-8-sig'))
        # 教师评测规则的语法树指纹，避免为测试保留整份历史源码。
        expected = {
            'get_current_temp': '8fda12302b0ad08fa8b0c080e448c48412163b3d13c2338ff016d990bfc72135',
            'get_current_rate': '3283fcb5899b3edb8cceea05fc521c05ea5c45d9e4e6e257febf01d9f56ad822',
            'load_testset': '53f9afaccaecbfcb89bb52df2f5a0a0758af596d670d2607b7cc0beb7287f270',
            'get_ground_truth': 'f62f630f45928b356549b0d5b4a88d82a06bb78de0bdb4b1a9f04395a63a6cc6',
            'answer_correct': 'dca0723c8b45d1128da1de080ffeae6ccf041b54a6db1cd90f9378d859af481b',
        }
        for name, digest in expected.items():
            node = next(n for n in current.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertEqual(hashlib.sha256(ast.dump(node).encode()).hexdigest(), digest)
        self.assertEqual(hashlib.sha256(PROMPTS['V1'].encode()).hexdigest(),
                         'be1d7b9a7a99b3ca990a54c426e7de6f4ac87f407f349bf8adf50893f31e6680')
        self.assertEqual(rag.SYSTEM_PROMPT, PROMPTS['V3'])

    def test_teacher_tolerances_and_interfering_numbers(self):
        for text, truth, kind, expected in [
            ('当前气温23℃', 20, 'weather', True), ('当前气温23.1℃', 20, 'weather', False),
            ('16小时前更新', 16, 'weather', False), ('体感温度20℃，最高气温21℃', 20, 'weather', False),
            ('当前气温-10℃', -12, 'weather', True), ('1美元=7.2人民币', 7.2, 'fx', True),
            ('1美元=8人民币', 7.2, 'fx', False), ('当前气温20℃', None, 'weather', None)]:
            self.assertEqual(rag.answer_correct(text, truth, kind), expected)

    def test_missing_truth_excluded_but_failed_model_counted_wrong(self):
        sample, raw, truths = fixture()
        truths['0']['value'] = None
        raw['live_1_rag']['status'] = 'ConnectionError'
        summary = exp.summarize(exp.build_rows(sample, raw, truths), raw)
        self.assertEqual(summary['scored'], 29)
        self.assertEqual(summary['by_type']['weather']['scored'], 14)
        self.assertEqual(summary['rag_correct'], 28)
        self.assertFalse(summary['meets_course_threshold'])
        self.assertFalse(summary['complete_truth'])
        for truth in truths.values():
            truth['value'] = None
        empty = exp.summarize(exp.build_rows(sample, raw, truths), raw)
        self.assertIsNone(empty['blind_accuracy'])
        self.assertIsNone(empty['difference_pp'])

    def test_missing_tasks_wrong_sample_and_partial_usage_rejected(self):
        sample, raw, truths = fixture()
        with self.assertRaises(ValueError):
            exp.build_rows(list(reversed(sample)), raw, truths)
        missing = dict(raw)
        missing.pop('live_0_rag')
        with self.assertRaises(ValueError):
            exp.build_rows(sample, missing, truths)
        raw['live_0_rag']['usage'].append(dict(prompt_tokens=None, completion_tokens=None, total_tokens=None))
        with self.assertRaises(ValueError):
            exp.build_rows(sample, raw, truths)
        self.assertIsNone(exp.cumulative(raw['live_0_rag']['usage'], 'prompt_tokens'))

    def test_exact_ten_point_gain_meets_accuracy_threshold(self):
        sample, raw, truths = fixture()
        for i in range(30):
            raw[f'live_{i}_blind']['answer'] = '不知道' if i >= 2 else raw[f'live_{i}_blind']['answer']
            raw[f'live_{i}_rag']['answer'] = '不知道' if i >= 5 else raw[f'live_{i}_rag']['answer']
        summary = exp.summarize(exp.build_rows(sample, raw, truths), raw)
        self.assertEqual(summary['difference_pp'], 10)
        self.assertTrue(summary['meets_excellent_accuracy_threshold'])

    def test_real_time_experiment_cannot_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'metadata.json').write_text('{}')
            with patch.object(rag, 'get_ground_truth') as truth, self.assertRaises(SystemExit):
                evaluate.main(['--output-dir', directory])
            truth.assert_not_called()

    def test_truth_never_enters_model_messages_or_blind_search(self):
        messages = []
        def respond(value):
            messages.append(copy.deepcopy(value))
            return '回答: 不知道'
        with tempfile.TemporaryDirectory() as directory, patch.object(rag, 'get_ground_truth', return_value=918273.125), \
             patch.object(rag, 'call_llm', side_effect=respond), patch.object(rag, 'bing_search') as search:
            _, truth, records = evaluate.run_pair(0, exp.load_sample()[0], Path(directory))
        self.assertEqual(truth['value'], 918273.125)
        self.assertEqual(len(records), 2)
        self.assertNotIn('918273', json.dumps(messages))
        search.assert_not_called()

    def test_time_injection_both_modes_and_off(self):
        for enabled in (False, True):
            token = rag.TIME_INJECTION.set(enabled)
            try:
                with patch.object(rag, 'call_llm', return_value='回答: 不知道') as llm:
                    rag.blind_answer('今天日期是？')
                    first = llm.call_args.args[0][0]['content']
                    rag.rag_answer('今天日期是？', system_prompt=PROMPTS['V1'])
                    second = llm.call_args.args[0][0]['content']
                for content in (first, second):
                    self.assertEqual('当前系统时间' in content, enabled)
            finally:
                rag.TIME_INJECTION.reset(token)

    def test_parallel_usage_and_search_records_are_isolated(self):
        def respond(**kwargs):
            content = kwargs['messages'][-1]['content']
            result = '搜索: 北京 当前气温' if 'blind' not in content and content == 'rag' else '回答: 当前气温20℃'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12))
        with patch.object(rag.client.chat.completions, 'create', side_effect=respond), \
             patch('requests.get', return_value=SimpleNamespace(text='<li class="b_algo"><h2>天气</h2><p>当前气温20℃</p></li>', raise_for_status=lambda: None)):
            with ThreadPoolExecutor(2) as pool:
                blind = pool.submit(evaluate.run_one, 'blind', 'blind')
                searched = pool.submit(evaluate.run_one, 'rag', 'rag')
                b, r = blind.result(), searched.result()
        self.assertEqual(b['prompt_tokens'], 10)
        self.assertEqual(r['prompt_tokens'], 20)
        self.assertEqual(len(r['search_events']), 1)
        self.assertEqual(r['search_events'][0]['selected_sources'], ['bing_snippet'])
        self.assertEqual(b['search_log'], [])

    def test_weather_page_requires_current_column_correct_city_and_today(self):
        today = datetime.now(timezone(timedelta(hours=8))).strftime('%Y年%m月%d日')
        def page(city='北京', date=today, current='13', with_current=True):
            html = f'<dl class="weather_info"><dd class="name"><h1>{city}天气</h1></dd><dd class="week">{date}</dd>'
            if with_current:
                html += f'<p class="now"><b>{current}</b><i>℃</i></p>'
            html += '<span>最高26℃，最低6℃，体感温度99℃</span></dl>'
            return SimpleNamespace(text=html, url='https://www.tianqi.com/beijing/',
                                   apparent_encoding='utf-8', raise_for_status=lambda: None)
        with patch('requests.get', return_value=page()):
            result = rag._weather_page('https://www.tianqi.com/beijing/', '北京天气')
        self.assertIn('当前气温：13℃', result)
        self.assertNotIn('26℃', result)
        self.assertIn('页面未提供具体观测时刻', result)
        for response in [page(city='上海'), page(date='2000年01月01日'), page(with_current=False)]:
            with patch('requests.get', return_value=response):
                self.assertIsNone(rag._weather_page('https://www.tianqi.com/beijing/', '北京天气'))

    def test_only_actual_weather_search_link_is_fetched_once(self):
        target = 'https://www.tianqi.com/beijing/'
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip('=')
        link = 'https://www.bing.com/ck/a?u=a1'+encoded
        self.assertEqual(rag._weather_result_url(link), target)
        for invalid in ['https://wttr.in/Beijing', 'https://example.com/beijing/',
                        'https://www.tianqi.com/beijing/15/', 'https://www.bing.com/ck/a?u=bad']:
            self.assertIsNone(rag._weather_result_url(invalid))
        html = ''.join(f'<li class="b_algo"><h2><a href="{url}">天气</a></h2><p>天气摘要</p></li>'
                       for url in [link, 'https://www.tianqi.com/shanghai/'])
        with patch('requests.get', return_value=SimpleNamespace(text=html, raise_for_status=lambda: None)), \
             patch.object(rag, '_weather_page', return_value=None) as fetch:
            snippets = rag.bing_search('北京天气')
        self.assertEqual(len(snippets), 2)
        fetch.assert_called_once_with(target, '北京天气')

    def test_weather_link_without_slash_or_summary_is_still_read(self):
        target = 'https://www.tianqi.com/shenzhen'
        self.assertEqual(rag._weather_result_url(target), target)
        html = f'<li class="b_algo"><h2><a href="{target}">深圳天气</a></h2></li>'
        with patch('requests.get', return_value=SimpleNamespace(text=html, raise_for_status=lambda: None)), \
                patch.object(rag, '_weather_page', return_value='【天气网页当前栏】城市：深圳；当前气温：26℃') as fetch:
            snippets = rag.bing_search('深圳天气')
        fetch.assert_called_once_with(target, '深圳天气')
        self.assertTrue(snippets[0].startswith('【天气网页当前栏】'))
        for link in ('https://www.tianqi.com/shenzhen/15/', 'https://www.tianqi.com/shenzhen.html'):
            self.assertIsNone(rag._weather_result_url(link))

    def test_search_transport_retries_same_url_and_records_attempts(self):
        import requests
        from scripts.experiment_utils import capture
        response = SimpleNamespace(text='<li class="b_algo"><h2>天气</h2><p>暂无数值</p></li>',
                                   raise_for_status=lambda: None)
        events = []
        with patch('requests.get', side_effect=[requests.exceptions.SSLError(), response]) as get, patch('time.sleep'):
            with capture(events):
                snippets = rag.bing_search('北京天气')
        self.assertEqual(len(snippets), 1)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0], get.call_args_list[1])
        self.assertEqual([r['status'] for r in events[0]['request_attempts']], ['SSLError', 'ok'])
        self.assertEqual(events[0]['status'], 'ok')
        self.assertNotIn('verify', get.call_args.kwargs)
        events = []
        with patch('requests.get', side_effect=requests.exceptions.Timeout) as get, patch('time.sleep'):
            with self.assertRaises(requests.exceptions.Timeout), capture(events):
                rag.bing_search('北京天气')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(events[0]['status'], 'Timeout')
        self.assertEqual(len(events[0]['request_attempts']), 2)

    def test_search_transport_does_not_retry_permanent_http_failure(self):
        import requests
        response = SimpleNamespace(status_code=404)
        error = requests.exceptions.HTTPError(response=response)
        with patch('requests.get', side_effect=error) as get, self.assertRaises(requests.exceptions.HTTPError):
            rag.bing_search('北京天气')
        self.assertEqual(get.call_count, 1)

    def test_refined_weather_uses_locale_without_extra_query(self):
        response = SimpleNamespace(text='', raise_for_status=lambda: None)
        for query, localized in [('北京天气', False), ('"北京天气"', True), ('"中国银行外汇牌价" "美元"', False)]:
            with self.subTest(query=query), patch('requests.get', return_value=response) as get:
                rag.bing_search(query)
            get.assert_called_once()
            params = get.call_args.kwargs['params']
            self.assertEqual(params['q'], query)
            self.assertEqual('mkt' in params, localized)
            if localized:
                self.assertEqual(params['mkt'], 'zh-CN')

    def test_ninety_percent_target_requires_twenty_seven_and_all_truths(self):
        sample, raw, truths = fixture()
        for i in range(30):
            if i >= 27:
                raw[f'live_{i}_rag']['answer'] = '无法确定'
        result = exp.summarize(exp.build_rows(sample, raw, truths), raw)
        self.assertEqual(result['rag_accuracy'], 90)
        self.assertTrue(result['meets_rag_target'])
        answer = raw['live_26_rag']['answer']
        raw['live_26_rag']['answer'] = '无法确定'
        self.assertFalse(exp.summarize(exp.build_rows(sample, raw, truths), raw)['meets_rag_target'])
        raw['live_26_rag']['answer'] = answer
        truths['29']['value'] = None
        self.assertFalse(exp.summarize(exp.build_rows(sample, raw, truths), raw)['meets_rag_target'])

    def bank_page(self, currency='加拿大元', bid='703', ask='707', date=None, units=True):
        date = date or datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d %H:%M:%S')
        html = '<p>本汇率表单位为100外币换算人民币</p>' if units else ''
        html += '<table id="priceTable"><tr>'+''.join(f'<th>{x}</th>' for x in
                  ['货币名称','现汇买入价','现钞买入价','现汇卖出价','现钞卖出价','中行折算价','发布日期','发布时间'])+'</tr>'
        for name, buy, sell in [(currency,bid,ask), ('美元','9999','10001')]:
            html += '<tr>'+''.join(f'<td>{x}</td>' for x in [name,buy,'',sell,'','',date,date.split()[-1]])+'</tr>'
        return SimpleNamespace(text=html+'</table>', url='https://www.boc.cn/sourcedb/whpj/',
                               apparent_encoding='utf-8', raise_for_status=lambda: None)

    def test_bank_fx_units_alias_and_only_requested_currency(self):
        with patch('requests.get', return_value=self.bank_page()), patch.object(rag,'get_ground_truth') as truth:
            result = rag._fx_page('https://www.boc.cn/sourcedb/whpj/', '中国银行外汇牌价 加元')
        self.assertIn('1加拿大元约合7.05人民币', result)
        self.assertIn('原表单位：100外币兑人民币', result)
        self.assertIn('不是银行公布的成交报价', result)
        self.assertIn('银行发布时间', result)
        self.assertNotIn('美元', result)
        truth.assert_not_called()
        for official, query in [('港币','港元'), ('澳大利亚元','澳元'), ('泰国铢','泰铢')]:
            with patch('requests.get', return_value=self.bank_page(currency=official)):
                self.assertIn('1'+official+'约合7.05人民币', rag._fx_page('https://www.boc.cn/sourcedb/whpj/',query))

    def test_bank_fx_refuses_missing_units_bad_values_stale_or_ambiguous_data(self):
        responses = [self.bank_page(units=False), self.bank_page(bid=''), self.bank_page(bid='NaN'),
                     self.bank_page(bid='708', ask='707'), self.bank_page(date='2000/01/01 12:00:00'),
                     self.bank_page(date=(datetime.now(timezone(timedelta(hours=8)))+timedelta(days=1)).strftime('%Y/%m/%d %H:%M:%S'))]
        for response in responses:
            with patch('requests.get', return_value=response):
                self.assertIsNone(rag._fx_page('https://www.boc.cn/sourcedb/whpj/','加元'))
        with patch('requests.get', return_value=self.bank_page()):
            self.assertIsNone(rag._fx_page('https://www.boc.cn/sourcedb/whpj/','加元 美元'))
            self.assertIsNone(rag._fx_page('https://www.boc.cn/sourcedb/whpj/','不存在币'))

    def test_bank_fx_uses_actual_search_link_and_records_source(self):
        from scripts.experiment_utils import capture
        target='https://www.boc.cn/sourcedb/whpj/'
        encoded=base64.urlsafe_b64encode(target.encode()).decode().rstrip('=')
        link='https://www.bing.com/ck/a?u=a1'+encoded
        html=f'<li class="b_algo"><h2><a href="{link}">中国银行外汇牌价</a></h2><p>网页摘要没有报价</p></li>'
        events=[]
        with patch('requests.get', side_effect=[SimpleNamespace(text=html,raise_for_status=lambda:None),self.bank_page()]) as request, \
             patch.object(rag,'get_current_rate') as truth:
            with capture(events):
                snippets=rag.bing_search('中国银行外汇牌价 加元')
        self.assertTrue(snippets[0].startswith('【银行汇率网页】'))
        self.assertEqual(request.call_args_list[1].args[0],target)
        self.assertEqual(request.call_count,2)
        self.assertIn('bank_fx_page',events[0]['selected_sources'])
        truth.assert_not_called()
        for invalid in ['https://open.er-api.com/v6/latest/USD', 'https://www.boc.cn/sourcedb/whpjSearch/index.html',
                        'https://example.com/sourcedb/whpj/']:
            self.assertIsNone(rag._fx_result_url(invalid))

    def test_export_eight_columns_by_type_tokens_and_tamper_detection(self):
        sample, raw, truths = fixture()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            archive_fixture(directory, sample, raw, truths)
            summary = export(directory)
            self.assertEqual(summary['by_type']['weather']['rag_correct'], 15)
            self.assertEqual(summary['by_type']['fx']['scored'], 15)
            self.assertTrue((directory/'accuracy.png').stat().st_size > 10000)
            with (directory/'results.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(next(csv.reader(f)), exp.FIELDS)
            with (directory/'token_comparison.csv').open(encoding='utf-8-sig') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 5)
            self.assertEqual({r['type'] for r in rows}, {'weather', 'fx'})
            text = (directory/'results.csv').read_text(encoding='utf-8-sig')
            (directory/'results.csv').write_text(text.replace('20.0', '9999').replace('True', 'False', 1), encoding='utf-8-sig')
            with self.assertRaises(ValueError):
                export(directory)

    def test_export_preserves_analysis_and_is_idempotent(self):
        sample, raw, truths = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive_fixture(directory, sample, raw, truths)
            path = directory/'实验结果摘要.md'
            analysis = '# 真实分析\n\n失败项必须保留，不能替换。\n'
            path.write_text(analysis, encoding='utf-8')
            with patch.object(rag, 'get_ground_truth') as truth:
                export(directory)
                first = path.read_text(encoding='utf-8')
                export(directory)
            self.assertEqual(path.read_text(encoding='utf-8'), first)
            self.assertIn(analysis, first)
            truth.assert_not_called()
        with self.assertRaises(ValueError):
            merge_summary('统计', '<!-- auto-statistics:start -->未闭合')

    def test_archive_rejects_changed_snapshot_and_false_configuration(self):
        sample, raw, truths = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive_fixture(directory, sample, raw, truths)
            metadata = json.loads((directory/'metadata.json').read_text(encoding='utf-8'))
            metadata['time_injection'] = False
            exp.write_json(directory/'metadata.json', metadata)
            with self.assertRaises(ValueError):
                exp.read_archive(directory)
            metadata['time_injection'] = True
            exp.write_json(directory/'metadata.json', metadata)
            exp.write_json(directory/'prompts.json', {**PROMPTS, 'V3': '替换的提示词'})
            with self.assertRaises(ValueError):
                exp.read_archive(directory)
            exp.write_json(directory/'prompts.json', PROMPTS)
            (directory/'source/scripts/weather_prompts.py').write_text('changed', encoding='utf-8')
            with self.assertRaises(ValueError):
                exp.read_archive(directory)

    def test_usage_rejects_invalid_counts_and_blind_search(self):
        for value in (-1, True, 1.5, '10'):
            with self.assertRaises(ValueError):
                exp.cumulative([{'prompt_tokens': value}], 'prompt_tokens')
        sample, raw, truths = fixture()
        raw['live_0_blind']['search_log'] = [('北京天气', [])]
        with self.assertRaises(ValueError):
            exp.build_rows(sample, raw, truths)

    def test_interruption_keeps_all_planned_tasks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(rag, 'API_KEY', 'test-placeholder'), \
             patch.object(evaluate, 'ThreadPoolExecutor', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                evaluate.main(['--output-dir', tmp])
            directory = Path(tmp)
            raw = json.loads((directory/'raw_results.json').read_text(encoding='utf-8'))
            self.assertEqual(set(raw), exp.required_tasks())
            self.assertTrue(all(r['status'] == 'not_started' for r in raw.values()))
            self.assertEqual(len(json.loads((directory/'truths.json').read_text(encoding='utf-8'))), 30)

    def test_sdk_has_no_hidden_retries_and_has_timeout(self):
        self.assertEqual(rag.client.max_retries, 0)
        self.assertEqual(rag.client.timeout, 60.0)

    def test_full_batch_sixty_tasks_with_mocked_services(self):
        def respond(**kwargs):
            question = kwargs['messages'][1]['content']
            answer = '当前气温20℃' if '气温' in question else '1原币=7.2人民币'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='回答: '+answer))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12))
        with tempfile.TemporaryDirectory() as directory, patch.object(rag, 'API_KEY', 'test-placeholder'), \
             patch.object(rag, 'get_ground_truth', side_effect=lambda kind, key: 20 if kind == 'weather' else 7.2), \
             patch.object(rag.client.chat.completions, 'create', side_effect=respond):
            evaluate.main(['--workers', '3', '--output-dir', directory])
            directory = Path(directory)
            raw = json.loads((directory/'raw_results.json').read_text(encoding='utf-8'))
            self.assertEqual(set(raw), exp.required_tasks())
            summary = export(directory)
            self.assertTrue(summary['complete_truth'])
            self.assertEqual(summary['rag_accuracy'], 100)
            self.assertTrue((directory/'source/search_rag_weather_fx.py').exists())

    def test_missing_usage_is_unknown_and_exhausted_api_is_failure(self):
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='回答: 未知'))], usage=None)
        with patch.object(rag.client.chat.completions, 'create', return_value=reply):
            result = evaluate.run_one('测试', 'blind')
        self.assertIsNone(result['prompt_tokens'])
        self.assertEqual(result['status'], 'ok')
        with patch.object(rag.client.chat.completions, 'create', side_effect=ConnectionError), patch('time.sleep'):
            result = evaluate.run_one('测试', 'blind')
        self.assertNotEqual(result['status'], 'ok')
        self.assertEqual(len(result['usage']), 2)
        self.assertIsNone(result['total_tokens'])


if __name__ == '__main__':
    unittest.main()
