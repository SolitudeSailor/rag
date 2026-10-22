"""第四部分：教师天气/汇率30题，现场取值，两模式共60项；不复用跨时段答案。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import search_rag_weather_fx as rag
from scripts.weather_prompts import PROMPTS
from scripts.experiment_utils import api_base_fingerprint, capture, completed_log, install, new_experiment, now
from scripts.weather_experiment import (STANDARD_ID, cumulative, final_status, load_sample,
                                       write_json, write_results)

install(rag)


def run_one(question, mode, prompt=None, trace_path=None):
    usage, events = [], []
    token = rag.CALL_USAGE.set(usage)
    start = time.perf_counter()
    result = dict(question=question, mode=mode, answer='', status='running',
                  started_at=now(), search_log=[], search_events=events, usage=usage)

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
                result['answer'], result['search_log'] = rag.rag_answer(question, system_prompt=prompt or PROMPTS['V3'])
        result['status'] = final_status(result['answer'])
    except Exception as exc:
        result['status'] = type(exc).__name__
    finally:
        rag.CALL_USAGE.reset(token)
    result.update(finished_at=now(), seconds=round(time.perf_counter()-start, 3))
    result.update({key: cumulative(usage, key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')})
    checkpoint()
    return result


def run_pair(index, item, traces):
    truth = dict(type=item['type'], item=item['item'], value=None, status='running',
                 started_at=now(), source='教师get_ground_truth：wttr.in' if item['type'] == 'weather' else '教师get_ground_truth：open.er-api.com / currency-api备用')
    try:
        truth['value'] = rag.get_ground_truth(item['type'], item['item'])
        truth['status'] = 'ok' if truth['value'] is not None else 'unavailable'
    except Exception as exc:
        truth['status'] = type(exc).__name__
    truth['finished_at'] = now()
    write_json(traces / f'truth_{index}.json', truth)
    # 标准值只存档评分，不进入问题、提示词或检索上下文。
    records = {}
    for mode in ('blind', 'rag'):
        key = f'live_{index}_{mode}'
        records[key] = run_one(item['question'], mode, trace_path=traces / f'{key}.json')
    return index, truth, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--output-dir', type=Path, help='仅允许空目录；实时实验中断后须新建轮次')
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 64:
        parser.error('--workers 必须在1至64之间')
    out = args.output_dir.resolve() if args.output_dir else new_experiment(ROOT / 'outputs' / 'experiment')
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit('实时标准答案会变化，禁止续跑或混用已有目录；请新建一轮，旧证据保留。')
    if not rag.API_KEY.strip() or rag.API_KEY == 'YOUR_API_KEY_HERE':
        raise SystemExit('请先配置LLM_API_KEY；不会打印密钥。')
    sample = load_sample()
    sources = ['search_rag_weather_fx.py', 'scripts/weather_prompts.py', 'scripts/evaluate.py',
               'scripts/weather_experiment.py', 'scripts/experiment_utils.py']
    metadata = dict(schema=STANDARD_ID, started_at=now(), model=rag.MODEL, temperature=0.3,
                    api_base_sha256=api_base_fingerprint(rag), workers=args.workers,
                    max_search=2, top_k=5, time_injection=True, time_zone='Asia/Shanghai',
                    search_configuration=rag.SEARCH_CONFIGURATION.copy(),
                    extra_body_sha256=hashlib.sha256(json.dumps(rag.EXTRA_BODY, sort_keys=True).encode()).hexdigest(),
                    source_sha256={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
                    note='教师30题全覆盖；同题两模式共享现场标准值。标准值失败不计分并显式报告；模型/检索失败计错。基础绘图路径，无查询改写。')
    write_json(out/'metadata.json', metadata)
    write_json(out/'sample30.json', sample)
    write_json(out/'prompts.json', PROMPTS)
    snapshots = out/'source'
    for name in sources:
        dest = snapshots/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT/name).read_bytes())
    traces = out/'search_traces'
    traces.mkdir()
    raw, truths = {}, {}
    print(f'[实验目录] {out}\n[任务] 天气15题+汇率15题，两模式60项；每题现场获取标准值。', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_pair, i, item, traces) for i, item in enumerate(sample)]
        for future in as_completed(futures):
            i, truth, records = future.result()
            truths[str(i)] = truth
            raw.update(records)
            write_json(out/'truths.json', truths)
            write_json(out/'raw_results.json', raw)
            print(f'[进度] {len(raw)}/60；{sample[i]["item"]} 标准值状态={truth["status"]}', flush=True)
    write_results(out, sample, raw, truths)
    metadata['finished_at'] = now()
    write_json(out/'metadata.json', metadata)
    print('60项记录已保存；请使用export导出统计与图表。', flush=True)


if __name__ == '__main__':
    main()
