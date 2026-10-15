"""系统时钟与实时天气工具；不读取密钥，不使用模型记忆补数据。"""
from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

import requests


TIMEZONE = timezone(timedelta(hours=8))
GEOCODING_URL = 'https://geocoding-api.open-meteo.com/v1/search'
WEATHER_URL = 'https://api.open-meteo.com/v1/forecast'


def now_china():
    return datetime.now(TIMEZONE)


def _json_get(url, params, source, query, record):
    event = {'source': source, 'query': query, 'parameters': params,
             'request_host': urlsplit(url).hostname, 'status': 'running'}
    record(event)
    try:
        response = requests.get(url, params=params, timeout=10,
                                headers={'User-Agent': 'RAGCourseProject/1.0 (educational retrieval)'})
        event.update(http_status=response.status_code, final_host=urlsplit(response.url).hostname)
        if response.status_code != 200 or urlsplit(response.url).hostname != urlsplit(url).hostname:
            event['status'] = 'http_error'
            return None, event
        data = response.json()
        if not isinstance(data, dict) or data.get('error'):
            event['status'] = 'invalid_response'
            return None, event
        event['status'] = 'ok'
        return data, event
    except (requests.RequestException, ValueError) as exc:
        event['status'] = type(exc).__name__
        return None, event


def _date_range(value, today):
    offsets = {'今天': 0, '今日': 0, 'today': 0, '明天': 1, 'tomorrow': 1, '后天': 2, '昨天': -1}
    if value.casefold() in offsets:
        selected = today + timedelta(days=offsets[value.casefold()])
        return selected, selected
    if value == '本周':
        return today - timedelta(days=today.weekday()), today + timedelta(days=6 - today.weekday())
    if value == '未来一周':
        return today, today + timedelta(days=6)
    selected = date.fromisoformat(value)
    return selected, selected


def search_runtime(query, record):
    """非实时工具指令返回None；工具失败返回明确的工具观察。"""
    if query.strip() in ('当前时间', '当前日期', '现在时间', 'current time', 'current date'):
        current = now_china()
        record({'source': 'system_clock', 'query': query, 'status': 'accepted',
                'timezone': 'UTC+08:00', 'time': current.isoformat()})
        return [f'【系统时钟】北京时间：{current:%Y-%m-%d %H:%M:%S}；'
                f'日期：{current:%Y-%m-%d}；时区UTC+08:00。来源：运行程序的系统时钟。']
    match = re.fullmatch(r'天气\s+城市=(.*?)\s+日期=(\S+)', query.strip())
    if not match:
        if query.startswith('天气') and ('城市=' in query or query.strip() == '天气'):
            record({'source': 'weather', 'query': query, 'status': 'missing_parameters'})
            return ['【天气工具】需要城市及日期。请用户提供城市；指令格式：天气 城市=城市名称 日期=今天。']
        return None
    city, day = match.groups()
    if not city or city in ('未知', '未提供', '所在地', '这里'):
        record({'source': 'weather', 'query': query, 'status': 'missing_city'})
        return ['【天气工具】未提供明确城市，无法确定位置。请询问用户城市，不猜测用户所在地。']
    current = now_china()
    try:
        start, end = _date_range(day, current.date())
    except ValueError:
        record({'source': 'weather', 'query': query, 'status': 'invalid_date'})
        return ['【天气工具】日期格式无法解析。支持今天、明天、后天、本周、未来一周或YYYY-MM-DD。']
    if start < current.date() - timedelta(days=7) or end > current.date() + timedelta(days=15):
        record({'source': 'weather', 'query': query, 'status': 'date_out_of_range'})
        return ['【天气工具】所问日期超出本接口的近期资料范围，无法提供该日期天气；不编造历史或远期预报。']
    locations, geo_event = _json_get(GEOCODING_URL, {'name': city, 'count': 5, 'language': 'zh', 'format': 'json'},
                                   'weather_geocoding', query, record)
    if locations is None or not locations.get('results'):
        geo_event['status'] = 'no_location' if locations is not None else geo_event['status']
        return ['【天气工具】城市定位未成功，未取得天气数据。请确认城市名称或稍后重试。']
    matches = locations['results']
    place = matches[0]
    # 优先明确的首都/一级行政中心；普通同名地点请用户澄清。
    if len(matches) > 1 and place.get('feature_code') not in ('PPLC', 'PPLA'):
        geo_event['status'] = 'ambiguous_location'
        options = '；'.join(' '.join(str(p.get(k, '')) for k in ('name', 'admin1', 'country')).strip()
                           for p in matches[:3])
        return [f'【天气工具】城市名存在多个地点：{options}。请用户补充省份或国家，不自行选择。']
    geo_event.update(status='accepted', location={k: place.get(k) for k in ('name', 'admin1', 'country')})
    params = {'latitude': place['latitude'], 'longitude': place['longitude'],
              'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
              'timezone': 'Asia/Shanghai', 'start_date': start.isoformat(), 'end_date': end.isoformat()}
    if start <= current.date() <= end:
        params['current'] = 'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m'
    weather, event = _json_get(WEATHER_URL, params, 'weather', query, record)
    if weather is None:
        return ['【天气工具】天气接口访问失败，未取得天气数据。不能根据季节或常识推测当前天气。']
    daily = weather.get('daily') or {}
    dates = daily.get('time') or []
    if not dates or dates[0] != start.isoformat() or dates[-1] != end.isoformat():
        event['status'] = 'date_mismatch'
        return ['【天气工具】接口返回日期与所问日期不一致，无法可靠提供天气。']
    # null保持未知，不把缺失数据替换为零；附单位和接口数据时间。
    result = {'定位': {k: place.get(k) for k in ('name', 'admin1', 'country', 'latitude', 'longitude')},
              '查询时间': current.isoformat(), '时区': 'Asia/Shanghai',
              '所问日期': [start.isoformat(), end.isoformat()],
              '每日天气数据': daily, '每日单位': weather.get('daily_units', {}),
              '当前状况': weather.get('current'), '当前单位': weather.get('current_units')}
    event.update(status='accepted', source_url=WEATHER_URL,
                 data_time=(weather.get('current') or {}).get('time'), requested_dates=result['所问日期'])
    return ['【Open-Meteo天气数据】\n来源：https://open-meteo.com/\n'
            '当前状况为天气模型估计，未来日期为预报，过去日期为模型资料，不是现场实测；'
            '所有日期和时间按北京时间UTC+08:00解释。天气代码为WMO代码，null表示未知。\n'
            + json.dumps(result, ensure_ascii=False)]
