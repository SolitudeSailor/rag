"""在实验入口记录原搜索函数的调用，不改变查询、返回值或重试规则。"""
import contextvars
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
        event = {'query': query, 'started_at': now(), 'status': 'running', 'snippets': []}
        events.append(event)
        if persist:
            persist()
        try:
            snippets = original(query, *args, **kwargs)
        except (Exception, KeyboardInterrupt) as exc:
            event.update(status=type(exc).__name__, finished_at=now())
            if persist:
                persist()
            raise
        event.update(status='ok', snippets=snippets, finished_at=now())
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
