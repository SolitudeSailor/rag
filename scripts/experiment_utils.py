"""实验共用辅助函数：课程评分、编号目录及搜索过程记录；不加载密钥。"""
import csv
import json
from pathlib import Path
import re
import contextvars
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


STANDARD_ID = 'nq-open-course-substring-v1'


FIELDS = ['question', 'gold_answers', 'blind_answer', 'rag_answer',
          'blind_correct', 'rag_correct']


def correct(response, answers):
    """严格使用课程原式，不做同义词、标点、词边界或人工调整。"""
    return any(ans.lower() in response.lower() for ans in answers)


def record_correct(record, answers):
    # 未完成搜索指令、异常中的部分输出不是最终回答；失败仍计入30题。
    return record['status'] == 'ok' and correct(record['answer'], answers)


def build_rows(sample, raw):
    if len(sample) != 30:
        raise ValueError('必须是固定30题')
    required = {f'nq_{i}_{mode}' for i in range(30) for mode in ('blind', 'rag')}
    if not required <= raw.keys():
        raise ValueError('必须包含两模式全部60项，禁止丢弃失败或缺失任务')
    rows = []
    for i, item in enumerate(sample):
        if not item['answer'] or any(not isinstance(a, str) or not a.strip() for a in item['answer']):
            raise ValueError('标准答案必须是非空字符串列表')
        b, r = (raw[f'nq_{i}_{mode}'] for mode in ('blind', 'rag'))
        for mode, record in [('blind', b), ('rag', r)]:
            if record['question'] != item['question'] or record['mode'] != mode:
                raise ValueError('问题或模式与样本不一致')
        rows.append(dict(question=item['question'],
                         gold_answers=json.dumps(item['answer'], ensure_ascii=False),
                         blind_answer=b['answer'], rag_answer=r['answer'],
                         blind_correct=record_correct(b, item['answer']),
                         rag_correct=record_correct(r, item['answer'])))
    return rows


def write_results(directory, sample, raw):
    rows = build_rows(sample, raw)
    directory = Path(directory)
    with (directory / 'results.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    details = []
    for i in range(30):
        for mode in ('blind', 'rag'):
            record = raw[f'nq_{i}_{mode}']
            details.append(dict(task=f'nq_{i}_{mode}', status=record['status'],
                                search_count=len(record['search_log']),
                                prompt_tokens=record['prompt_tokens'],
                                completion_tokens=record['completion_tokens'],
                                total_tokens=record['total_tokens']))
    with (directory / 'task_details.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(details[0]))
        writer.writeheader()
        writer.writerows(details)
    return rows


def numbered_entries(root):
    root = Path(root)
    if not root.exists():
        return []
    entries = []
    for path in root.iterdir():
        match = re.fullmatch(r'exp_([1-9][0-9]*)', path.name)
        if match:
            entries.append((int(match.group(1)), path))
    return entries


def new_experiment(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    number = max((n for n, _ in numbered_entries(root)), default=0) + 1
    while True:
        path = root / f'exp_{number}'
        try:
            path.mkdir()
            return path.resolve()
        except FileExistsError:
            number += 1


def latest_experiment(root):
    entries = [(n, path) for n, path in numbered_entries(root) if path.is_dir()]
    if not entries:
        raise ValueError('尚无exp_编号实验目录，请先运行scripts/evaluate.py。')
    return max(entries, key=lambda entry: entry[0])[1].resolve()


ACTIVE = contextvars.ContextVar('search_tracking', default=None)


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def api_base_fingerprint(rag):
    # 仅保存指纹，接口地址中可能包含敏感参数，不记录明文。
    return hashlib.sha256(rag.API_BASE.strip().rstrip('/').encode('utf-8')).hexdigest()


def install(rag):
    original = rag.bing_search
    if getattr(original, '_records_search', False):
        return

    def tracked(query, *args, **kwargs):
        active = ACTIVE.get()
        if active is None:
            return original(query, *args, **kwargs)
        events, persist = active
        event = {'query': query, 'started_at': now(), 'status': 'running', 'snippets': [],
                 'source_attempts': []}
        events.append(event)
        if persist:
            persist()
        try:
            from scripts.search_sources import capture_attempts
            with capture_attempts(event['source_attempts'], persist):
                snippets = original(query, *args, **kwargs)
        except (Exception, KeyboardInterrupt) as exc:
            event.update(status=type(exc).__name__, finished_at=now())
            if persist:
                persist()
            raise
        labels = {'wikipedia_entry': '【维基百科', 'bing_fallback': '【Bing 网页摘要',
                  'web_page': '【辅助网页正文', 'system_clock': '【系统时钟',
                  'weather': '【Open-Meteo天气数据'}
        selected = {attempt['source'] for attempt in event['source_attempts']
                    if attempt['status'] == 'accepted' and attempt['source'] in labels
                    and any(snippet.startswith(labels[attempt['source']]) for snippet in snippets)}
        event.update(status='ok', snippets=snippets, finished_at=now(), selected_sources=sorted(selected))
        if persist:
            persist()
        return snippets

    tracked._records_search = True
    rag.bing_search = tracked


@contextmanager
def capture(events, persist=None):
    token = ACTIVE.set((events, persist))
    try:
        yield
    finally:
        ACTIVE.reset(token)


def completed_log(events):
    return [(event['query'], event['snippets']) for event in events if event['status'] == 'ok']
