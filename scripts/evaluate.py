"""固定样本真实实验，逐次保存调用与 usage，不向模型提供标准答案。"""
import argparse
import contextvars
import csv
import hashlib
import json
import random
import re
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import search_rag_example as rag

OUT = ROOT / 'outputs' / 'experiment'
DATA = ROOT / 'data'
DATA_URL = 'https://raw.githubusercontent.com/google-research-datasets/natural-questions/master/nq_open/NQ-open.dev.jsonl'
USAGE = contextvars.ContextVar('usage')
original_create = rag.client.chat.completions.create


def tracked_create(*args, **kwargs):
    response = original_create(*args, **kwargs)
    u = response.usage
    USAGE.get().append({
        'prompt_tokens': u.prompt_tokens if u else None,
        'completion_tokens': u.completion_tokens if u else None,
        'total_tokens': u.total_tokens if u else None,
    })
    return response


rag.client.chat.completions.create = tracked_create


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def versions():
    from prompt_versions import PROMPTS
    assert PROMPTS['V3'] == rag.SYSTEM_PROMPT, '提示词副本与核心源码不同，停止实验'
    return PROMPTS.copy()


def run_one(question, mode, prompt=None):
    usage = []
    token = USAGE.set(usage)
    start = time.perf_counter()
    result = {'question': question, 'mode': mode, 'answer': '', 'status': 'ok', 'search_log': []}
    try:
        if mode == 'blind':
            result['answer'] = rag.blind_answer(question)
        else:
            result['answer'], result['search_log'] = rag.rag_answer(question, system_prompt=prompt)
        if re.match(r'^搜索[:：]', result['answer']):
            result['status'] = 'unfinished_search'
    except Exception as exc:
        result['status'] = type(exc).__name__
        # 不存储可能含请求头或凭据的异常原文。
    finally:
        USAGE.reset(token)
    result.update(usage=usage, seconds=round(time.perf_counter() - start, 3))
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        result[key] = sum(x[key] for x in usage) if all(x[key] is not None for x in usage) else None
    return result


def correct(answer, answers):
    return any(str(gold).lower() in answer.lower() for gold in answers if str(gold).strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    prompts = versions()
    write_json(OUT / 'prompts.json', prompts)
    DATA.mkdir(parents=True, exist_ok=True)
    dataset = DATA / 'NQ-open.dev.jsonl'
    if not dataset.exists():
        response = requests.get(DATA_URL, timeout=30)
        response.raise_for_status()
        dataset.write_bytes(response.content)
    rows = [json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    random.seed(42)
    indices = random.sample(range(len(rows)), 30)
    selected = [dict(index=i, **rows[i]) for i in indices]
    write_json(OUT / 'sample30.json', selected)
    write_json(OUT / 'metadata.json', {
        'date': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'model': rag.MODEL, 'temperature': 0.3, 'max_search': 2, 'top_k': 3,
        'seed': 42, 'workers': args.workers, 'dataset_size': len(rows),
        'dataset_url': DATA_URL, 'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
        'source_sha256': hashlib.sha256((ROOT / 'search_rag_example.py').read_bytes()).hexdigest(),
        'prompt_sha256': hashlib.sha256((ROOT / 'scripts' / 'prompt_versions.py').read_bytes()).hexdigest(),
        'note': '医疗助手 V2/V3；核心文件除 V2/V3 提示词外保持原样，使用原 cn.bing.com 搜索；本目录两模式全部重新调用，不复用旧版本成绩。固定样本工程评测，非独立泛化测试。',
    })
    jobs = [(f'nq_{i}_{mode}', item['question'], mode, prompts['V3'])
            for i, item in enumerate(selected) for mode in ('blind', 'rag')]
    tests = [
        ('时效事实', '2026年诺贝尔物理学奖获奖者是谁？'),
        ('冷门参数', 'STM32F103C8T6 的 Flash 容量是多少 KB？'),
        ('常识计算', '计算 12×8 并说明计算过程。'),
        ('虚假前提', 'J.K.罗琳已经出版的《哈利波特》第九部小说叫什么？请概述剧情。'),
    ]
    for version, prompt in prompts.items():
        for i, (_, question) in enumerate(tests):
            jobs.append((f'prompt_{version}_{i}', question, version, prompt))
    results = {}
    cache = OUT / 'raw_results.json'
    if cache.exists():
        results = json.loads(cache.read_text(encoding='utf-8'))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, q, m, p): key for key, q, m, p in jobs if key not in results}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()
            write_json(cache, results)
            print(f'[进度] {len(results)}/{len(jobs)} {key} {results[key]["status"]}', flush=True)
    with (OUT / 'results.csv').open('w', newline='', encoding='utf-8-sig') as f:
        fields = ['question', 'gold_answers', 'blind_answer', 'rag_answer', 'blind_correct', 'rag_correct', 'blind_status', 'rag_status', 'rag_search_count', 'blind_prompt_tokens', 'rag_prompt_tokens']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, item in enumerate(selected):
            b, r = results[f'nq_{i}_blind'], results[f'nq_{i}_rag']
            writer.writerow(dict(question=item['question'], gold_answers=json.dumps(item['answer'], ensure_ascii=False),
                blind_answer=b['answer'], rag_answer=r['answer'],
                blind_correct=b['status'] == 'ok' and correct(b['answer'], item['answer']),
                rag_correct=r['status'] == 'ok' and correct(r['answer'], item['answer']),
                blind_status=b['status'], rag_status=r['status'], rag_search_count=len(r['search_log']),
                blind_prompt_tokens=b['prompt_tokens'], rag_prompt_tokens=r['prompt_tokens']))
    write_json(OUT / 'manual_questions.json', tests)
    print('实验完成；真实结果已保存。', flush=True)


if __name__ == '__main__':
    main()
