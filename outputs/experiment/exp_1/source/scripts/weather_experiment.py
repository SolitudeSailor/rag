"""天气/汇率实验的存档和统计。评分直接调用教师函数，不联网取标准值。"""
import ast
import csv
import hashlib
import json
import math
import re
import time
from pathlib import Path

STANDARD_ID = 'weather-fx-teacher-tolerance-v1'
FIELDS = ['type', 'item', 'question', 'truth', 'blind_answer', 'rag_answer',
          'blind_correct', 'rag_correct']
TOKEN_SAMPLES = [0, 1, 2, 15, 16]
SOURCE_FILES = {'search_rag_weather_fx.py', 'scripts/weather_prompts.py',
                'scripts/evaluate.py', 'scripts/weather_experiment.py',
                'scripts/experiment_utils.py'}


def snapshot_prompts(path):
    """只解析归档文本中的字符串与拼接，不执行归档Python源码。"""
    values = {}

    def string(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return string(node.left) + string(node.right)
        raise ValueError('归档提示词只能使用字符串常量与拼接')

    for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ('V1', 'V2', 'V3'):
                values[name] = string(node.value)
    return values


def read_archive(directory):
    """只读核验同轮配置、源码指纹、提示词、题目、原始记录和八列CSV。"""
    directory = Path(directory)
    read = lambda name: json.loads((directory/name).read_text(encoding='utf-8'))
    metadata = read('metadata.json')
    if (metadata.get('schema') != STANDARD_ID or metadata.get('time_injection') is not True
            or metadata.get('time_zone') != 'Asia/Shanghai'
            or metadata.get('max_search') != 2 or metadata.get('top_k') != 5):
        raise ValueError('档案缺少固定评测配置或北京时间注入证明')
    hashes = metadata.get('source_sha256', {})
    if not SOURCE_FILES <= hashes.keys():
        raise ValueError('档案缺少必需源码指纹')
    source_root = (directory/'source').resolve()
    for name, digest in hashes.items():
        path = (source_root/name).resolve()
        if not path.is_relative_to(source_root) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('无效的源码快照路径或指纹')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('源码快照与本轮记录的指纹不一致')
    prompts = read('prompts.json')
    if set(prompts) != {'V1', 'V2', 'V3'} or not all(isinstance(v, str) and v.strip() for v in prompts.values()):
        raise ValueError('缺少本轮完整三版提示词')
    if prompts != snapshot_prompts(source_root/'scripts/weather_prompts.py'):
        raise ValueError('提示词档案与本轮源码快照不一致')
    sample, raw, truths = read('sample30.json'), read('raw_results.json'), read('truths.json')
    rows = build_rows(sample, raw, truths)
    with (directory/'results.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        actual = list(reader)
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV列必须与八列要求一致')
    serialized = [{k: '' if row[k] is None else str(row[k]) for k in FIELDS} for row in rows]
    if actual != serialized:
        raise ValueError('CSV与原始回答、标准值或教师判分不一致')
    return metadata, sample, raw, truths, rows


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
    if not isinstance(usage, list) or any(not isinstance(u, dict) for u in usage):
        raise ValueError('usage必须保留逐次调用记录')
    for entry in usage:
        value = entry.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('Token用量必须为非负整数或null')
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
            if (mode == 'blind' and record['search_log']) or len(record['search_log']) > 2:
                raise ValueError('盲答不得搜索，RAG最多搜索两轮')
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
    summary['rag_target_accuracy'] = 90
    summary['rag_target_correct'] = 27
    summary['meets_rag_target'] = summary['complete_truth'] and summary['rag_correct'] >= 27
    summary['search_questions'] = sum(bool(raw[f'live_{i}_rag']['search_log']) for i in range(30))
    summary['search_count'] = sum(len(raw[f'live_{i}_rag']['search_log']) for i in range(30))
    summary['nonempty_searches'] = sum(bool(s) for i in range(30) for _, s in raw[f'live_{i}_rag']['search_log'])
    return summary
