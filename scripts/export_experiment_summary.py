"""离线核验并导出新版天气/汇率实验，拒绝旧数据及不一致的CSV。"""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from scripts.experiment_utils import latest_experiment
from scripts.weather_experiment import STANDARD_ID, FIELDS, TOKEN_SAMPLES, build_rows, summarize, write_json


def percent(value):
    return '不适用' if value is None else f'{value:.2f}%'


def chart(directory, summary):
    font = lambda size: ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', size)
    im = Image.new('RGB', (1680, 1000), 'white')
    d = ImageDraw.Draw(im)
    d.text((840, 35), '实时天气与汇率：盲答 / V3 搜索 RAG 准确率', font=font(38), fill='#172B4D', anchor='mt')
    d.text((840, 100), '教师容差：天气 ±3℃；汇率 ±1.5%｜标准值获取失败不计分', font=font(25), fill='#44546F', anchor='mt')
    for value in range(0, 101, 20):
        y = 760-value*5
        d.line((160, y, 1600, y), fill='#DFE1E6', width=2)
        d.text((140, y), str(value), font=font(23), fill='black', anchor='rm')
    d.line((160, 260, 160, 760, 1600, 760), fill='black', width=3)
    d.text((30, 440), '准确率\n（%）', font=font(24), fill='black')
    for index, (label, group) in enumerate([('天气', summary['by_type']['weather']), ('汇率', summary['by_type']['fx']), ('总体', summary)]):
        center = 390+index*480
        for offset, mode, color in [(-92, 'blind', '#50769A'), (92, 'rag', '#4D9278')]:
            value = group[f'{mode}_accuracy']
            x = center+offset
            if value is not None:
                y = 760-value*5
                d.rectangle((x-68, y, x+68, 760), fill=color)
                d.text((x, y-12), percent(value), font=font(25), fill='black', anchor='mb')
            else:
                d.text((x, 725), '不适用', font=font(22), fill='black', anchor='mb')
        d.text((center, 790), f'{label}（有效 {group["scored"]}/{group["total"]}）', font=font(27), fill='black', anchor='mt')
    for x, color, name in [(570, '#50769A', '盲答'), (830, '#4D9278', 'V3 搜索 RAG')]:
        d.rectangle((x, 190, x+28, 218), fill=color)
        d.text((x+45, 186), name, font=font(25), fill='black')
    d.text((870, 875), '评测题型', font=font(27), fill='black', anchor='mt')
    d.text((840, 940), '同题共用现场标准值；模型/检索失败计错；不复用旧轮次结果', font=font(23), fill='#44546F', anchor='mt')
    im.save(directory/'accuracy.png')


def export(directory):
    read = lambda name: json.loads((directory/name).read_text(encoding='utf-8'))
    metadata = read('metadata.json')
    if metadata.get('schema') != STANDARD_ID:
        raise ValueError('该目录不是新版天气/汇率实验，禁止混用旧NQ结果')
    sample, raw, truths = read('sample30.json'), read('raw_results.json'), read('truths.json')
    expected = build_rows(sample, raw, truths)
    with (directory/'results.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        actual = list(reader)
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV列必须与新版八列要求一致')
    serialized = [{k: '' if row[k] is None else str(row[k]) for k in FIELDS} for row in expected]
    if actual != serialized:
        raise ValueError('CSV与原始回答、标准值或教师判分不一致，停止统计')
    summary = summarize(expected, raw)
    write_json(directory/'summary.json', summary)
    with (directory/'token_comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['sample', 'type', 'question', 'blind_prompt_tokens', 'rag_prompt_tokens', 'rag_calls', 'rag_searches'])
        for i in TOKEN_SAMPLES:
            b, r = raw[f'live_{i}_blind'], raw[f'live_{i}_rag']
            writer.writerow([i+1, sample[i]['type'], sample[i]['question'],
                             '未知' if b['prompt_tokens'] is None else b['prompt_tokens'],
                             '未知' if r['prompt_tokens'] is None else r['prompt_tokens'],
                             len(r['usage']), len(r['search_log'])])
    chart(directory, summary)
    lines = ['# 天气与汇率实验结果摘要', '', '|题型|计划/有效|盲答|V3 RAG|差异（百分点）|', '|---|---|---|---|---|']
    for label, group in [('天气', summary['by_type']['weather']), ('汇率', summary['by_type']['fx']), ('总体', summary)]:
        diff = '不适用' if group['difference_pp'] is None else f'{group["difference_pp"]:+.2f}'
        lines.append(f'|{label}|{group["total"]}/{group["scored"]}|{percent(group["blind_accuracy"])}|{percent(group["rag_accuracy"])}|{diff}|')
    lines += ['', f'标准值缺失 {summary["truth_failures"]} 题；非正常模型任务 {len(summary["non_ok_tasks"])} 项。',
              f'实际搜索 {summary["search_count"]} 次，非空摘要 {summary["nonempty_searches"]} 次。',
              '缺少标准值时按教师规则不计分，必须报告有效分母；未取得全部30题标准值时不宣称正式达标。',
              '失败任务完整保留；本摘要只统计第四部分，不包含手工提示词对比或时间注入实验。', '',
              '## Token分析', '',
              '五题明细见 token_comparison.csv，覆盖三道天气与两道汇率。RAG 的系统规则、搜索摘要和重复调用都会占用输入 Token；请结合实际调用次数与材料长度解释变化，缺失 usage 标为未知。上下文长度增加不保证准确率提高，需要结合两类题的得分判断材料价值。', '',
              '## 待人工完成的结果分析', '',
              '- 对照原始回答，分别统计盲答的猜测数字、旧值与拒答表现。',
              '- 比较天气与汇率增益，检查摘要是否含所问数值、是否有多个干扰数字、是否过期。',
              '- 从 search_traces 选择真实搜索词，对比城市/币种、单位、方向、日期等写法；无对照证据时不编造改进结论。']
    (directory/'实验结果摘要.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        directory = args.input_dir.resolve() if args.input_dir else latest_experiment(ROOT/'outputs'/'experiment')
        print(f'[统计目录] {directory}', flush=True)
        summary = export(directory)
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f'统计失败（{type(exc).__name__}）：请检查新版完整实验及文件一致性。') from None
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
