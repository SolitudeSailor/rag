"""真实命令行演示入口，复用教师问答函数与三版提示词。"""
import argparse
from datetime import datetime
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import search_rag_example as rag
from prompt_versions import PROMPTS

p = argparse.ArgumentParser()
p.add_argument('--version', choices=['V1', 'V2', 'V3', 'blind'], default='V3')
p.add_argument('--question', help='通过命令参数提供实际问题，便于完整截图')
args = p.parse_args()
print(f'RAG课程实验 | {args.version} | {rag.MODEL}', flush=True)
print('真实模型调用；不读取缓存答案。', flush=True)
question = args.question if args.question is not None else input('问题> ')
if args.question is not None:
    print('问题> ' + question, flush=True)
logs = []
if args.version == 'blind':
    answer = rag.blind_answer(question)
    print('[盲答：未调用搜索]\n' + answer, flush=True)
else:
    answer, logs = rag.rag_answer(question, system_prompt=PROMPTS[args.version])
    print(f'[实际搜索 {len(logs)} 次；非空摘要 {sum(bool(s) for _, s in logs)} 次]', flush=True)
    print('[最终回答] ' + answer, flush=True)
out = ROOT / 'outputs' / 'screenshots'
out.mkdir(parents=True, exist_ok=True)
(out / (args.version + '_demo.json')).write_text(json.dumps({
    'time': datetime.now().isoformat(), 'version': args.version, 'question': question,
    'answer': answer, 'search_log': logs,
    'source_sha256': hashlib.sha256((ROOT / 'search_rag_example.py').read_bytes()).hexdigest(),
}, ensure_ascii=False, indent=2), encoding='utf-8')
