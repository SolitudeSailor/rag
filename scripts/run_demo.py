"""第三部分真实交互：V1/V2/V3、盲答及时间注入前后；不预设问题。"""
import argparse
import hashlib
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import search_rag_weather_fx as rag
from scripts.evaluate import run_one
from scripts.weather_prompts import PROMPTS
from scripts.experiment_utils import api_base_fingerprint, now
from scripts.weather_experiment import write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=['V1', 'V2', 'V3', 'blind'], default='V3')
    parser.add_argument('--question', help='实际问题，便于完整截图')
    parser.add_argument('--time-injection', choices=['on', 'off'], default='on')
    args = parser.parse_args(argv)
    if not rag.API_KEY.strip() or rag.API_KEY == 'YOUR_API_KEY_HERE':
        raise SystemExit('请先配置LLM_API_KEY；不会打印密钥。')
    rag.TIME_INJECTION.set(args.time_injection == 'on')
    print(f'RAG课程实验 | {args.version} | {rag.MODEL} | 时间注入={args.time_injection}', flush=True)
    print('真实模型调用；逐题独立作答；quit退出。', flush=True)
    directory = ROOT/'outputs'/'sessions'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/f'{args.version}_time-{args.time_injection}_{uuid4().hex}.json'
    prompt = '你是一个问答助手，请直接回答问题。' if args.version == 'blind' else PROMPTS[args.version]
    session = dict(started_at=now(), version=args.version, model=rag.MODEL,
                   api_base_sha256=api_base_fingerprint(rag), time_injection=args.time_injection == 'on',
                   base_system_prompt=prompt, time_zone='Asia/Shanghai', context_enabled=False,
                   source_sha256=hashlib.sha256((ROOT/'search_rag_weather_fx.py').read_bytes()).hexdigest(),
                   search_configuration={**rag.SEARCH_CONFIGURATION, 'top_k': 5}, turns=[])
    while True:
        try:
            question = args.question if args.question is not None else input('\n问题> ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in ('quit', 'exit'):
            break
        if not question.strip():
            if args.question is not None:
                break
            continue
        print('问题> '+question, flush=True)
        mode = 'blind' if args.version == 'blind' else 'rag'
        # 保存实际注入时间，便于解释前后对照，不记录密钥。
        turn = dict(question=question, status='running', asked_at=now(), time_note=rag.current_time_note())
        session['turns'].append(turn)
        write_json(path, session)
        trace = path.with_name(path.stem+f'_turn{len(session["turns"])}.json')
        result = run_one(question, mode, prompt, trace)
        turn.update(result)
        session['updated_at'] = now()
        write_json(path, session)
        print(f'[状态] {result["status"]}；实际搜索 {len(result["search_log"])} 次', flush=True)
        print('[盲答]' if mode == 'blind' else '[最终回答]', result['answer'], flush=True)
        print(f'[会话记录] {path}', flush=True)
        if args.question is not None:
            return 0 if result['status'] == 'ok' else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
