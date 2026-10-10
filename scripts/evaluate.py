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
from search_tracking import api_base_fingerprint, capture, completed_log, install

install(rag)

OUT = ROOT / 'outputs' / 'experiment'
DATA = ROOT / 'data'
DATA_URL = 'https://raw.githubusercontent.com/google-research-datasets/natural-questions/master/nq_open/NQ-open.dev.jsonl'
USAGE = contextvars.ContextVar('usage')
original_create = rag.client.chat.completions.create


def tracked_create(*args, **kwargs):
    response = original_create(*args, **kwargs)
    u = getattr(response, 'usage', None)
    USAGE.get().append({
        'prompt_tokens': getattr(u, 'prompt_tokens', None),
        'completion_tokens': getattr(u, 'completion_tokens', None),
        'total_tokens': getattr(u, 'total_tokens', None),
    })
    return response


rag.client.chat.completions.create = tracked_create


def write_json(path, obj):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def versions():
    from prompt_versions import PROMPTS
    assert PROMPTS['V3'] == rag.SYSTEM_PROMPT, '提示词副本与核心源码不同，停止实验'
    return PROMPTS.copy()


def run_one(question, mode, prompt=None, trace_path=None):
    usage = []
    token = USAGE.set(usage)
    start = time.perf_counter()
    events = []
    result = {'question': question, 'mode': mode, 'answer': '', 'status': 'running',
              'search_log': [], 'search_events': events}

    def checkpoint():
        result['search_log'] = completed_log(events)
        if trace_path is not None:
            write_json(trace_path, result)

    checkpoint()
    try:
        with capture(events, checkpoint):
            if mode == 'blind':
                result['answer'] = rag.blind_answer(question)
            else:
                result['answer'], result['search_log'] = rag.rag_answer(question, system_prompt=prompt)
        result['status'] = 'unfinished_search' if re.match(r'^\s*搜索[:：]', result['answer']) else 'ok'
    except Exception as exc:
        result['status'] = type(exc).__name__
        # 不存储可能含请求头或凭据的异常原文。
    finally:
        USAGE.reset(token)
    result.update(usage=usage, seconds=round(time.perf_counter() - start, 3))
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        result[key] = sum(x[key] for x in usage) if usage and all(x[key] is not None for x in usage) else None
    checkpoint()
    return result


def correct(answer, answers):
    return any(str(gold).lower() in answer.lower() for gold in answers if str(gold).strip())


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--output-dir', type=Path, default=OUT, help='第四部分实验目录；重测请指定新目录')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers 必须至少为 1')
    OUT = args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    prompts = versions()
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
    metadata = {
        'date': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'model': rag.MODEL, 'temperature': 0.3, 'max_search': 2, 'top_k': 3,
        'api_base_sha256': api_base_fingerprint(rag),
        'search_url': 'https://www.bing.com/search',
        'seed': 42, 'workers': args.workers, 'dataset_size': len(rows),
        'dataset_url': DATA_URL, 'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
        'source_sha256': hashlib.sha256((ROOT / 'search_rag_example.py').read_bytes()).hexdigest(),
        'prompt_sha256': hashlib.sha256((ROOT / 'scripts' / 'prompt_versions.py').read_bytes()).hexdigest(),
        'note': '第四部分固定 30 题，盲答与医疗助手 V3 RAG 共 60 个任务；使用已修复的 www.bing.com/search；缺失 usage 标为未知。仅同配置可断点续跑；固定样本工程评测，非独立泛化测试。',
    }
    jobs = [(f'nq_{i}_{mode}', item['question'], mode, prompts['V3'])
            for i, item in enumerate(selected) for mode in ('blind', 'rag')]
    results = {}
    cache = OUT / 'raw_results.json'
    if cache.exists():
        results = json.loads(cache.read_text(encoding='utf-8'))
        evidence = ['metadata.json', 'sample30.json', 'prompts.json']
        if not all((OUT / name).exists() for name in evidence):
            raise SystemExit('缓存缺少配置证据，停止续跑；请用 --output-dir 指定新实验目录。')
        previous = json.loads((OUT / 'metadata.json').read_text(encoding='utf-8'))
        config_keys = ['model', 'temperature', 'max_search', 'top_k', 'seed',
                       'dataset_sha256', 'source_sha256', 'prompt_sha256']
        same_config = all(previous.get(key) == metadata[key] for key in config_keys)
        same_sample = json.loads((OUT / 'sample30.json').read_text(encoding='utf-8')) == selected
        same_prompt = json.loads((OUT / 'prompts.json').read_text(encoding='utf-8')).get('V3') == prompts['V3']
        if not (same_config and same_sample and same_prompt):
            raise SystemExit('缓存与当前模型、提示词或样本不一致，停止续跑；请用 --output-dir 指定新实验目录。')
        for key, question, mode, _ in jobs:
            if key in results and (results[key].get('question') != question or results[key].get('mode') != mode):
                raise SystemExit('缓存任务与抽样题目或模式不一致，停止续跑。')
        if 'api_base_sha256' not in previous:
            if all(key in results for key, *_ in jobs):
                print('历史实验已完整，但未记录接口指纹：保留原文件，不续跑或补写配置；可使用离线统计脚本。', flush=True)
                return
            raise SystemExit('旧缓存缺少接口指纹，无法确认原接口；请用 --output-dir 指定新目录，不补写历史配置。')
        if previous['api_base_sha256'] != metadata['api_base_sha256']:
            raise SystemExit('接口地址与原实验不一致，停止续跑；请用 --output-dir 指定新目录。')
        print('[续跑] 保留已有记录和原配置，仅运行缺失任务；已有失败不重试。', flush=True)
    else:
        write_json(OUT / 'prompts.json', prompts)
        write_json(OUT / 'sample30.json', selected)
        write_json(OUT / 'metadata.json', metadata)
    # 每次执行独立存放检查点，续跑不会覆盖上次中断的搜索过程。
    traces = OUT / 'search_traces' / datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    traces.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, q, m, p, traces / f'{key}.json'): key for key, q, m, p in jobs if key not in results}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()
            write_json(cache, results)
            completed = sum(job_key in results for job_key, *_ in jobs)
            print(f'[进度] {completed}/{len(jobs)} {key} {results[key]["status"]}', flush=True)
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
                blind_prompt_tokens='未知' if b['prompt_tokens'] is None else b['prompt_tokens'],
                rag_prompt_tokens='未知' if r['prompt_tokens'] is None else r['prompt_tokens']))
    print('实验完成；真实结果已保存。', flush=True)


if __name__ == '__main__':
    main()
