"""离线整理当前医疗助手实验，不修改核心代码，不调用模型。"""
import csv
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'experiment'
if not (OUT / 'results.csv').exists():
    raise SystemExit('尚无新实验结果，请先运行 scripts/evaluate.py。')
with (OUT / 'results.csv').open(encoding='utf-8-sig', newline='') as f:
    rows = list(csv.DictReader(f))
raw = json.loads((OUT / 'raw_results.json').read_text(encoding='utf-8'))
sample = json.loads((OUT / 'sample30.json').read_text(encoding='utf-8'))
assert len(rows) == len(sample) == 30 and len(raw) == 72, '记录不完整，停止统计'
bc = sum(r['blind_correct'] == 'True' for r in rows)
rc = sum(r['rag_correct'] == 'True' for r in rows)
summary = {
    'blind_correct': bc, 'rag_correct': rc,
    'blind_accuracy': bc / 30 * 100, 'rag_accuracy': rc / 30 * 100,
    'difference_pp': (rc - bc) / 30 * 100,
    'search_questions': sum(bool(raw[f'nq_{i}_rag']['search_log']) for i in range(30)),
    'search_count': sum(len(raw[f'nq_{i}_rag']['search_log']) for i in range(30)),
    'nonempty_searches': sum(bool(s) for i in range(30) for _, s in raw[f'nq_{i}_rag']['search_log']),
}
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

with (OUT / 'token_comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['sample', 'question', 'blind_prompt_tokens', 'rag_prompt_tokens', 'rag_calls', 'rag_searches'])
    for i in range(5):
        b, r = raw[f'nq_{i}_blind'], raw[f'nq_{i}_rag']
        writer.writerow([i + 1, sample[i]['question'], b['prompt_tokens'], r['prompt_tokens'], len(r['usage']), len(r['search_log'])])

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

manual = json.loads((OUT / 'manual_questions.json').read_text(encoding='utf-8'))
template = OUT / '手工问答记录模板.csv'
if not template.exists():
    with template.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['版本', '题号', '类型', '完整问题', '搜索次数', '实际关键词', '关键词是否合理', '最终答案是否正确', '角色风格', '是否编造', '完整回答', '核验来源', '截图文件'])
        for version in ['V1', 'V2', 'V3']:
            for i, (kind, question) in enumerate(manual, 1):
                writer.writerow([version, f'Q{i}', kind, question] + [''] * 9)

lines = ['# 医疗助手实验结果摘要', '',
    f'- 盲答：{bc}/30，{bc/30*100:.2f}%。',
    f'- 医疗助手 V3 RAG：{rc}/30，{rc/30*100:.2f}%。',
    f'- 差异：{(rc-bc)/30*100:+.2f} 个百分点。',
    f'- 实际搜索：{summary["search_count"]} 次，非空摘要 {summary["nonempty_searches"]} 次。',
    '- 判分：任一标准答案的小写字符串包含于输出即判对；失败保留在分母。',
    '- 图表：accuracy.png；五题输入 Token：token_comparison.csv。',
    '- 手工截图与人工对比请自行完成，空白模板不是实际成绩。', '',
    '本摘要对应医疗身份和原核心搜索函数，不能混用其他配置的历史成绩。']
(OUT / '实验结果摘要.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
print('已离线生成图表、Token 表、摘要和空白记录模板；核心代码未修改。')
