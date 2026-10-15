"""验证实时工具时间、位置、单位、缺失值及失败边界；不访问网络。"""
from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import runtime_tools as runtime


class RuntimeToolTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 14, 22, 0, tzinfo=runtime.TIMEZONE)
        self.enterContext(patch.object(runtime, 'now_china', return_value=self.current))

    def test_clock_uses_runtime_time_without_network(self):
        events = []
        with patch.object(runtime.requests, 'get') as get:
            result = runtime.search_runtime('当前时间', events.append)
        get.assert_not_called()
        self.assertIn('2026-10-14 22:00:00', result[0])
        self.assertIn('UTC+08:00', result[0])
        self.assertEqual(events[0]['status'], 'accepted')

    def test_weather_resolves_tomorrow_and_preserves_units_and_unknown_values(self):
        events = []
        def fetch(url, params, **kwargs):
            if url == runtime.GEOCODING_URL:
                data = {'results': [{'name': '示例市', 'admin1': '示例省', 'country': '中国',
                                     'latitude': 30, 'longitude': 110, 'feature_code': 'PPLA'}]}
            else:
                self.assertEqual(params['start_date'], '2026-10-15')
                self.assertNotIn('current', params)
                data = {'daily': {'time': ['2026-10-15'], 'temperature_2m_max': [None],
                                  'temperature_2m_min': [10], 'precipitation_probability_max': [20]},
                        'daily_units': {'temperature_2m_max': '°C', 'precipitation_probability_max': '%'}}
            return SimpleNamespace(status_code=200, url=url+'?'+urlencode(params), json=lambda: data)
        with patch.object(runtime.requests, 'get', side_effect=fetch):
            result = runtime.search_runtime('天气 城市=示例市 日期=明天', events.append)
        self.assertIn('2026-10-15', result[0])
        self.assertIn('null', result[0])
        self.assertIn('°C', result[0])
        self.assertIn('未来日期为预报', result[0])
        self.assertEqual(events[-1]['status'], 'accepted')

    def test_missing_city_and_far_future_do_not_guess_or_access_network(self):
        with patch.object(runtime.requests, 'get') as get:
            result = runtime.search_runtime('天气 城市=未知 日期=今天', lambda event: None)
            self.assertIn('请询问用户城市', result[0])
            future = (self.current.date() + timedelta(days=30)).isoformat()
            self.assertIn('超出', runtime.search_runtime('天气 城市=北京 日期='+future, lambda event: None)[0])
        get.assert_not_called()

    def test_ambiguous_location_requires_clarification(self):
        data = {'results': [{'name': '示例镇', 'admin1': region, 'country': '中国',
                            'latitude': 30, 'longitude': 110, 'feature_code': 'PPL'}
                           for region in ('甲省', '乙省')]}
        fake = SimpleNamespace(status_code=200, url=runtime.GEOCODING_URL, json=lambda: data)
        with patch.object(runtime.requests, 'get', return_value=fake) as get:
            result = runtime.search_runtime('天气 城市=示例镇 日期=今天', lambda event: None)
        self.assertEqual(get.call_count, 1)
        self.assertIn('请用户补充省份或国家', result[0])


if __name__ == '__main__':
    unittest.main()
