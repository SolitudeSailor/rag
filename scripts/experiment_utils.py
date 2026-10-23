"""当前实验共用的目录编号、时间、配置指纹和Bing搜索追踪。"""
from pathlib import Path
import re
import contextvars
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


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
                 'source_attempts': [], 'request_attempts': []}
        events.append(event)
        if persist:
            persist()
        token = rag.SEARCH_REQUEST_LOG.set(event['request_attempts'])
        try:
            snippets = original(query, *args, **kwargs)
        except (Exception, KeyboardInterrupt) as exc:
            event.update(status=type(exc).__name__, finished_at=now())
            if persist:
                persist()
            raise
        finally:
            rag.SEARCH_REQUEST_LOG.reset(token)
        event['source_attempts'].append({'source': 'bing_snippet', 'status': 'accepted' if snippets else 'empty'})
        selected = ['bing_snippet'] if snippets else []
        for source, prefix in [('weather_page', '【天气网页当前栏】'), ('bank_fx_page', '【银行汇率网页】')]:
            if any(s.startswith(prefix) for s in snippets):
                selected.append(source)
                event['source_attempts'].append({'source': source, 'status': 'accepted'})
        event.update(status='ok', snippets=snippets, finished_at=now(),
                     selected_sources=selected)
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
