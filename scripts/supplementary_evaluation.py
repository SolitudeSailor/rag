"""离线补充评价：别名匹配与人工复核，不改课程评分或调用模型。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from experiment_utils import build_rows, latest_experiment


ROOT = Path(__file__).resolve().parents[1]
STANDARD = 'alias-and-review-v1'
RISK = re.compile(
    r"\b(?:not|no|never|neither|isn't|wasn't|don't|cannot|can't|maybe|perhaps|"
    r"possibly|probably|uncertain|unsure|unknown|either|or|guess|however)\b"
    r'|不是|并非|不确定|不知道|无法确定|可能|也许|或者|还是|但实际上', re.I)


def normalize(value):
    """只归一化字形、大小写、空白、引号和 Markdown，不删除数字或姓名词。"""
    value = unicodedata.normalize('NFKC', value).casefold()
    value = value.translate(str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"'}))
    value = value.replace('**', '').replace('__', '').replace('`', '')
    return re.sub(r'\s+', ' ', value).strip()


def contains_phrase(text, phrase):
    # 例如 7 不命中 1979，Rice 不命中 price。中文不强制英文式词边界。
    left = r'(?<!\w)' if re.match(r'[a-z0-9_]', phrase) else ''
    right = r'(?!\w)' if re.search(r'[a-z0-9_]$', phrase) else ''
    return bool(re.search(left + re.escape(phrase) + right, text))


def load_aliases(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('entries'), list):
        raise ValueError('别名文件格式无效')
    for entry in data['entries']:
        for key in ('question', 'gold_answer', 'rationale', 'verified_on'):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f'别名缺少 {key}')
        for key in ('aliases', 'sources'):
            if not isinstance(entry.get(key), list) or not entry[key] or any(
                    not isinstance(x, str) or not x.strip() for x in entry[key]):
                raise ValueError(f'别名缺少 {key}')
        if any(not url.startswith('https://') for url in entry['sources']):
            raise ValueError('别名核验来源必须是 HTTPS 链接')
    return data


def assess(question, answers, record, aliases):
    """matched 是规则匹配，needs_review 不自动计错；不等价于事实裁判。"""
    if record['status'] != 'ok' or not record['answer'].strip():
        return dict(decision='incorrect', reason='执行失败或没有最终回答',
                    matched_answer='', evidence='')
    response = normalize(record['answer'])
    candidates = [(normalize(a), '标准答案匹配', '') for a in answers]
    normalized_gold = {normalize(a) for a in answers}
    for entry in aliases['entries']:
        if (normalize(entry['question']) == normalize(question)
                and normalize(entry['gold_answer']) in normalized_gold):
            candidates.extend((normalize(a), '已核验替代表述匹配', '; '.join(entry['sources']))
                              for a in entry['aliases'])
    matches = [c for c in candidates if contains_phrase(response, c[0])]
    if not matches:
        reason = '未识别到等价表述，需核验；不能仅因未匹配就判错'
    elif RISK.search(response):
        reason = '含否定、不确定或候选表达，需核对最终结论'
    else:
        primary = normalize(re.split(r'\n|(?<=[.!?。！？])\s+', record['answer'].strip(), maxsplit=1)[0])
        leading = [c for c in matches if contains_phrase(primary, c[0])]
        if not leading:
            reason = '匹配仅出现在后续解释，需核对最终结论'
        elif any(char.isdigit() for char in leading[0][0]):
            reason = '数字、日期、范围或单位答案需核对语境'
        else:
            value, reason, source = leading[0]
            return dict(decision='matched', reason=reason, matched_answer=value, evidence=source)
    return dict(decision='needs_review', reason=reason,
                matched_answer=matches[0][0] if matches else '',
                evidence=matches[0][2] if matches else '')


def fingerprint(item, record):
    content = dict(question=item['question'], gold=item['answer'],
                   mode=record['mode'], status=record['status'], answer=record['answer'])
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_reviews(path, rows):
    if path is None:
        return {}
    known = {row['task']: row for row in rows}
    reviews = {}
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if not row.get('decision', '').strip():
                continue
            task = row.get('task')
            if task not in known or task in reviews:
                raise ValueError('人工复核任务不存在或重复')
            if row.get('record_sha256') != known[task]['record_sha256']:
                raise ValueError('人工复核与原始问题、标准答案、模式或回答不一致')
            if row['decision'] not in ('correct', 'incorrect'):
                raise ValueError('人工结论只能是 correct 或 incorrect')
            if any(not row.get(key, '').strip() for key in ('reviewer', 'reason', 'evidence')):
                raise ValueError('人工复核必须填写核验人、理由和证据')
            if known[task]['execution_status'] != 'ok':
                raise ValueError('执行失败任务保持计错，不能以人工复核覆盖')
            reviews[task] = row
    return reviews


def write_csv(path, rows, fields=None):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def evaluate(input_dir, aliases_path, reviews_path=None):
    input_dir = Path(input_dir).resolve()
    sample_path, raw_path = input_dir / 'sample30.json', input_dir / 'raw_results.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    course = build_rows(sample, raw)  # 沿用固定30题、60任务、失败留分母的校验。
    aliases = load_aliases(aliases_path)
    rows = []
    for i, item in enumerate(sample):
        for mode in ('blind', 'rag'):
            record = raw[f'nq_{i}_{mode}']
            result = assess(item['question'], item['answer'], record, aliases)
            rows.append(dict(task=f'nq_{i}_{mode}', sample=i + 1, mode=mode,
                             question=item['question'], gold_answers=json.dumps(item['answer'], ensure_ascii=False),
                             answer=record['answer'], execution_status=record['status'],
                             course_correct=course[i][f'{mode}_correct'],
                             automatic_decision=result['decision'], **result,
                             reviewer='', record_sha256=fingerprint(item, record)))
    reviews = load_reviews(reviews_path, rows)
    for row in rows:
        if row['task'] in reviews:
            review = reviews[row['task']]
            row.update({key: review[key] for key in ('decision', 'reviewer', 'reason', 'evidence')})
    parent = input_dir / 'supplementary_evaluation'
    parent.mkdir(exist_ok=True)
    number = 1
    while True:
        out = parent / f'review_{number}'
        try:
            out.mkdir()
            break
        except FileExistsError:
            number += 1
    write_csv(out / 'details.csv', rows)
    # 所有题均可复核，含规则匹配项，以免漏掉表面命中、语义错误。
    manual = [dict(task=r['task'], record_sha256=r['record_sha256'], question=r['question'],
                   gold_answers=r['gold_answers'], answer=r['answer'],
                   automatic_decision=r['automatic_decision'],
                   **{key: reviews.get(r['task'], {}).get(key, '')
                      for key in ('decision', 'reviewer', 'reason', 'evidence')})
              for r in rows if r['execution_status'] == 'ok']
    write_csv(out / 'manual_review.csv', manual,
              ['task', 'record_sha256', 'question', 'gold_answers', 'answer',
               'automatic_decision', 'decision', 'reviewer', 'reason', 'evidence'])
    summary = dict(standard=STANDARD, input_dir=str(input_dir), post_hoc=True, modes={})
    for mode in ('blind', 'rag'):
        group = [r for r in rows if r['mode'] == mode]
        counts = {key: sum(r['decision'] == key for r in group)
                  for key in ('matched', 'needs_review', 'correct', 'incorrect')}
        # 所有正常任务都经人工复核后才计算最终补充准确率。
        complete = not counts['matched'] and not counts['needs_review']
        summary['modes'][mode] = dict(total=30, course_correct=sum(r['course_correct'] for r in group),
                                     **counts, human_review_complete=complete,
                                     semantic_accuracy_percent=counts['correct'] / 30 * 100 if complete else None)
    files = dict(raw_results=raw_path, sample30=sample_path, evaluator=Path(__file__), aliases=Path(aliases_path))
    if reviews_path:
        files['reviews'] = Path(reviews_path)
    summary['source_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'aliases.json').write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'supplementary_evaluation.py').write_bytes(Path(__file__).read_bytes())
    if reviews_path:
        (out / 'imported_reviews.csv').write_bytes(Path(reviews_path).read_bytes())
    lines = ['# 补充评价结果', '',
             '这是查看本轮输出后新增的评价，不是独立测试，不修改课程 CSV、摘要或原始回答。', '',
             '| 模式 | 课程判对 | 规则匹配（待人工确认） | 待复核 | 人工判对 | 判错（含失败） |',
             '|---|---:|---:|---:|---:|---:|']
    for mode, stats in summary['modes'].items():
        lines.append(f'| {mode} | {stats["course_correct"]} | {stats["matched"]} | {stats["needs_review"]} | {stats["correct"]} | {stats["incorrect"]} |')
    lines.extend(['', '规则匹配不代表事实核验；复核尚未完成时不报告语义准确率。',
                  '别名按问题及标准答案限定作用范围，两模式使用同一份规则。',
                  '填写 manual_review.csv 的 decision（correct/incorrect）、reviewer、reason、evidence，',
                  '再通过 --reviews 导入；保持 task、record_sha256 不变，未填行继续保留原状态。',
                  '所有正常任务均可人工复核；否定、候选、数字语境等规则只能发现部分风险，不能保证消除误判。'])
    (out / '说明.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return out, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path)
    parser.add_argument('--aliases', type=Path, default=ROOT / 'data' / 'evaluation_aliases.json')
    parser.add_argument('--reviews', type=Path)
    args = parser.parse_args()
    directory = args.input_dir or latest_experiment(ROOT / 'outputs' / 'experiment')
    try:
        out, summary = evaluate(directory, args.aliases, args.reviews)
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(str(exc))
    print(f'[补充评价目录] {out}')
    print(json.dumps(summary['modes'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
