"""天气/汇率实验的存档和统计。评分直接调用教师函数，不联网取标准值。"""
import csv
import json
import math
import re
import time
from pathlib import Path

STANDARD_ID = 'weather-fx-teacher-tolerance-v1'
FIELDS = ['type', 'item', 'question', 'truth', 'blind_answer', 'rag_answer',
          'blind_correct', 'rag_correct']
TOKEN_SAMPLES = [0, 1, 2, 15, 16]


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_sample():
    from search_rag_weather_fx import load_testset
    return [dict(question=q, type=kind, item=key) for q, kind, key in load_testset()]


def required_tasks():
    return {f'live_{i}_{mode}' for i in range(30) for mode in ('blind', 'rag')}


def final_status(answer):
    if re.search(r'^\s*(?:\*\*)?搜索(?:\*\*)?[:：]', answer, re.MULTILINE) or answer == '（搜索轮数已用完，模型未给出最终答案）':
        return 'unfinished_search'
    return 'ok' if answer.strip() else 'empty_answer'


def cumulative(usage, key):
    return sum(u[key] for u in usage) if usage and all(u.get(key) is not None for u in usage) else None


def build_rows(sample, raw, truths):
    from search_rag_weather_fx import answer_correct
    if sample != load_sample():
        raise ValueError('必须完整使用教师30题及原始顺序，不得混用NQ或替换题目')
    if set(raw) != required_tasks() or set(truths) != {str(i) for i in range(30)}:
        raise ValueError('必须保留30条标准答案获取记录及两模式60项任务，失败不删除')
    rows = []
    for i, item in enumerate(sample):
        truth_record = truths[str(i)]
        if truth_record['type'] != item['type'] or truth_record['item'] != item['item']:
            raise ValueError('标准答案对象与题目不一致')
        truth = truth_record['value']
        if truth is not None and (isinstance(truth, bool) or not isinstance(truth, (int, float)) or not math.isfinite(truth) or (item['type'] == 'fx' and truth <= 0)):
            raise ValueError('标准答案必须是有效数值或null')
        row = {**item, 'truth': truth}
        for mode in ('blind', 'rag'):
            record = raw[f'live_{i}_{mode}']
            if record['question'] != item['question'] or record['mode'] != mode:
                raise ValueError('问题或模式与教师题目不一致')
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                if record[key] != cumulative(record['usage'], key):
                    raise ValueError('Token必须按全部调用累计，任一缺失则未知')
            row[f'{mode}_answer'] = record['answer']
            row[f'{mode}_correct'] = (None if truth is None else
                record['status'] == 'ok' and final_status(record['answer']) == 'ok' and answer_correct(record['answer'], truth, item['type']))
        rows.append(row)
    return rows


def write_results(directory, sample, raw, truths):
    rows = build_rows(sample, raw, truths)
    directory = Path(directory)
    with (directory / 'results.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (directory / 'task_details.csv').open('w', newline='', encoding='utf-8-sig') as f:
        fields = ['task', 'status', 'search_count', 'prompt_tokens', 'completion_tokens', 'total_tokens']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for key in sorted(raw):
            record = raw[key]
            writer.writerow(dict(task=key, status=record['status'], search_count=len(record['search_log']),
                                 **{k: record[k] for k in fields[3:]}))
    return rows


def summarize(rows, raw):
    summary = {'scoring_standard': STANDARD_ID, 'sample_size': 30,
               'non_ok_tasks': [k for k in sorted(raw) if raw[k]['status'] != 'ok'], 'by_type': {}}
    for kind in ('weather', 'fx', 'overall'):
        group = [r for r in rows if kind == 'overall' or r['type'] == kind]
        valid = [r for r in group if r['truth'] is not None]
        n = len(valid)
        scores = {mode: sum(r[f'{mode}_correct'] is True for r in valid) for mode in ('blind', 'rag')}
        rates = {mode: scores[mode] / n * 100 if n else None for mode in scores}
        value = dict(total=len(group), scored=n, truth_failures=len(group)-n,
                     blind_correct=scores['blind'], rag_correct=scores['rag'],
                     blind_accuracy=rates['blind'], rag_accuracy=rates['rag'],
                     difference_pp=(scores['rag']-scores['blind']) / n * 100 if n else None)
        if kind == 'overall':
            summary.update(value)
        else:
            summary['by_type'][kind] = value
    summary['complete_truth'] = summary['scored'] == 30
    summary['meets_course_threshold'] = summary['complete_truth'] and summary['difference_pp'] >= 5
    summary['meets_excellent_accuracy_threshold'] = summary['complete_truth'] and summary['difference_pp'] >= 10
    summary['search_questions'] = sum(bool(raw[f'live_{i}_rag']['search_log']) for i in range(30))
    summary['search_count'] = sum(len(raw[f'live_{i}_rag']['search_log']) for i in range(30))
    summary['nonempty_searches'] = sum(bool(s) for i in range(30) for _, s in raw[f'live_{i}_rag']['search_log'])
    return summary
