"""离线整理当前医疗助手实验，不修改核心代码，不调用模型。"""
import csv
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from experiment_utils import record_correct, STANDARD_ID
from experiment_utils import latest_experiment

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'experiment'
parser = argparse.ArgumentParser(description='仅统计第四部分，不读取第三部分会话')
parser.add_argument('--input-dir', type=Path, help='指定exp_编号目录；不指定则统计最新编号')
input_dir = parser.parse_args().input_dir
try:
    OUT = latest_experiment(OUT) if input_dir is None or input_dir.resolve() == OUT.resolve() else input_dir.resolve()
except ValueError as exc:
    raise SystemExit(str(exc))
print(f'[统计目录] {OUT}', flush=True)
if not (OUT / 'results.csv').exists():
    raise SystemExit('尚无新实验结果，请先运行 scripts/evaluate.py。')
with (OUT / 'results.csv').open(encoding='utf-8-sig', newline='') as f:
    rows = list(csv.DictReader(f))
raw = json.loads((OUT / 'raw_results.json').read_text(encoding='utf-8'))
sample = json.loads((OUT / 'sample30.json').read_text(encoding='utf-8'))
required = {f'nq_{i}_{mode}' for i in range(30) for mode in ('blind', 'rag')}
assert len(rows) == len(sample) == 30 and required <= raw.keys(), '第四部分记录不完整，停止统计'
# 兼容已有 72 条的历史文件，只统计固定 30 题的 60 个任务。
raw = {key: raw[key] for key in required}
for i, item in enumerate(sample):
    assert rows[i]['question'] == item['question'], 'CSV 与抽样题目不一致，停止统计'
    assert all(raw[f'nq_{i}_{mode}']['question'] == item['question'] for mode in ('blind', 'rag')), '回答与抽样题目不一致，停止统计'
    assert json.loads(rows[i]['gold_answers']) == item['answer'], '标准答案不一致，停止统计'
    for mode in ('blind', 'rag'):
        record = raw[f'nq_{i}_{mode}']
        expected = record_correct(record, item['answer'])
        assert rows[i][f'{mode}_answer'] == record['answer'], 'CSV 与原始回答不一致，停止统计'
        assert rows[i][f'{mode}_correct'] == str(expected), 'CSV 与课程判分不一致，停止统计'
        usage = record['usage']
        cumulative = sum(u['prompt_tokens'] for u in usage) if usage and all(u['prompt_tokens'] is not None for u in usage) else None
        assert record['prompt_tokens'] == cumulative, '输入 Token 不是逐次调用累计，停止统计'
bc = sum(r['blind_correct'] == 'True' for r in rows)
rc = sum(r['rag_correct'] == 'True' for r in rows)
summary = {
    'scoring_standard': STANDARD_ID,
    'sample_size': 30,
    'blind_correct': bc, 'rag_correct': rc,
    'blind_accuracy': bc / 30 * 100, 'rag_accuracy': rc / 30 * 100,
    'difference_pp': (rc - bc) / 30 * 100,
    'search_questions': sum(bool(raw[f'nq_{i}_rag']['search_log']) for i in range(30)),
    'search_count': sum(len(raw[f'nq_{i}_rag']['search_log']) for i in range(30)),
    'nonempty_searches': sum(bool(s) for i in range(30) for _, s in raw[f'nq_{i}_rag']['search_log']),
    'meets_course_threshold': rc - bc >= 2,
    'meets_excellent_accuracy_threshold': rc - bc >= 3,
    'non_ok_tasks': [key for key in sorted(raw) if raw[key]['status'] != 'ok'],
}
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

with (OUT / 'token_comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['sample', 'question', 'blind_prompt_tokens', 'rag_prompt_tokens', 'rag_calls', 'rag_searches'])
    for i in range(5):
        b, r = raw[f'nq_{i}_blind'], raw[f'nq_{i}_rag']
        writer.writerow([i + 1, sample[i]['question'],
                         '未知' if b['prompt_tokens'] is None else b['prompt_tokens'],
                         '未知' if r['prompt_tokens'] is None else r['prompt_tokens'],
                         len(r['usage']), len(r['search_log'])])

font = lambda size: ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', size)
im = Image.new('RGB', (1500, 850), 'white')
d = ImageDraw.Draw(im)
d.text((750, 35), '盲答与医疗助手 V3 RAG 准确率对比', font=font(40), fill='black', anchor='mt')
d.text((750, 100), 'NQ-open 固定 30 题   课程字符串包含评分', font=font(25), fill='#444444', anchor='mt')
for value in range(0, 101, 20):
    y = 690 - value * 5
    d.line((180, y, 1380, y), fill='#DDDDDD', width=2)
    d.text((150, y), str(value), font=font(25), fill='black', anchor='rm')
d.line((180, 190, 180, 690, 1380, 690), fill='black', width=3)
d.text((40, 420), '准确率\n（%）', font=font(25), fill='black')
for x, count, color, name in [(430, bc, '#50769A', '盲答'), (1020, rc, '#7D9C88', '医疗助手 V3 RAG')]:
    value = count / 30 * 100
    y = 690 - value * 5
    d.rectangle((x - 130, y, x + 130, 690), fill=color)
    d.text((x, y - 15), f'{value:.2f}%  {count}/30', font=font(30), fill='black', anchor='mb')
    d.text((x, 710), name, font=font(29), fill='black', anchor='mt')
d.text((780, 790), '模式', font=font(26), fill='black', anchor='mt')
d.rectangle((990, 145, 1015, 170), fill='#50769A')
d.text((1030, 140), '盲答', font=font(23), fill='black')
d.rectangle((1130, 145, 1155, 170), fill='#7D9C88')
d.text((1170, 140), '医疗 V3 RAG', font=font(23), fill='black')
im.save(OUT / 'accuracy.png')

lines = ['# 医疗助手实验结果摘要', '',
    f'- 盲答：{bc}/30，{bc/30*100:.2f}%。',
    f'- 医疗助手 V3 RAG：{rc}/30，{rc/30*100:.2f}%。',
    f'- 差异：{(rc-bc)/30*100:+.2f} 个百分点。',
    f'- 实际搜索：{summary["search_count"]} 次，非空摘要 {summary["nonempty_searches"]} 次。',
    '- 判分：任一标准答案的小写字符串包含于输出即判对；失败保留在分母。',
    '- 图表：accuracy.png；五题输入 Token：token_comparison.csv。',
    '- 本摘要仅统计第四部分固定 30 题；第三部分会话与人工评价另行整理。', '',
    '本摘要对应医疗身份和原核心搜索函数，不能混用其他配置的历史成绩。']
(OUT / '实验结果摘要.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
print('已离线生成第四部分图表、Token 表和摘要；不读取第三部分会话。')
