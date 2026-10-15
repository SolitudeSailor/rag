"""真实命令行演示入口，复用教师问答函数与三版提示词。"""
import argparse
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re
import sys
from uuid import uuid4
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import search_rag_example as rag
from prompt_versions import PROMPTS
from experiment_utils import api_base_fingerprint, capture, completed_log, install
from scripts.search_sources import configuration as search_configuration

install(rag)

p = argparse.ArgumentParser()
p.add_argument('--version', choices=['V1', 'V2', 'V3', 'blind'], default='V3')
p.add_argument('--question', help='通过命令参数提供实际问题，便于完整截图')
args = p.parse_args()
print(f'RAG课程实验 | {args.version} | {rag.MODEL}', flush=True)
print('真实模型调用；不读取缓存答案。', flush=True)

session_started = datetime.now()
session_id = uuid4().hex
session_dir = ROOT / 'outputs' / 'sessions'
session_dir.mkdir(parents=True, exist_ok=True)
session_path = None
session = {
    'session_id': session_id,
    'started_at': session_started.isoformat(),
    'version': args.version,
    'model': rag.MODEL,
    'api_base_sha256': api_base_fingerprint(rag),
    'system_prompt': PROMPTS[args.version] if args.version != 'blind' else '你是一个问答助手，请直接回答问题。',
    'source_sha256': hashlib.sha256((ROOT / 'search_rag_example.py').read_bytes()).hexdigest(),
    'search_configuration': search_configuration(),
    'context_enabled': False,
    'turns': [],
}


def save_session():
    session['updated_at'] = datetime.now().isoformat()
    temporary = session_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(session_path)


def start_session(first_question):
    global session_path
    # Windows 文件名不能包含这些符号；完整问题仍保留在会话记录中。
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', first_question).strip().rstrip('. ')
    title = title.encode('utf-16-le')[:160].decode('utf-16-le', errors='ignore').rstrip('. ') or '未命名问题'
    session['first_question'] = first_question
    stem = f'{args.version}_session_{title}'
    number = 1
    while True:
        suffix = '' if number == 1 else f'_{number}'
        candidate = session_dir / f'{stem}{suffix}.json'
        try:
            with candidate.open('x', encoding='utf-8') as f:
                f.write(json.dumps(session, ensure_ascii=False, indent=2))
        except FileExistsError:
            number += 1
            continue
        session_path = candidate
        break
    print(f'[会话记录] {session_path}', flush=True)


def answer_and_save(question):
    if session_path is None:
        start_session(question)
    logs = []
    asked_at = datetime.now().isoformat()
    events = []
    turn = {'turn': len(session['turns']) + 1, 'asked_at': asked_at,
            'question': question, 'answer': None, 'status': 'running',
            'search_log': [], 'search_events': events}
    session['turns'].append(turn)

    def checkpoint():
        turn['search_log'] = completed_log(events)
        save_session()

    checkpoint()
    try:
        with capture(events, checkpoint):
            if args.version == 'blind':
                answer = rag.blind_answer(question)
                print('[盲答：未调用搜索]\n' + answer, flush=True)
            else:
                answer, logs = rag.rag_answer(question, system_prompt=PROMPTS[args.version])
                print(f'[实际搜索 {len(logs)} 次；非空摘要 {sum(bool(s) for _, s in logs)} 次]', flush=True)
                print('[最终回答] ' + answer, flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        # 仅记录异常类型，避免将可能含凭据的异常原文写入日志。
        turn.update(status=type(exc).__name__, finished_at=datetime.now().isoformat())
        checkpoint()
        raise
    turn.update({
        'answered_at': datetime.now().isoformat(), 'question': question,
        'answer': answer, 'search_log': logs,
        'status': 'unfinished_search' if re.match(r'^\s*搜索[:：]', answer) else 'ok',
    })
    checkpoint()
    print(f'[会话已更新] 第 {len(session["turns"])} 题已保存。', flush=True)



if args.question is not None:
    print('问题> ' + args.question, flush=True)
    answer_and_save(args.question)
else:
    print('可连续提问；输入 quit 或 exit 退出。每题独立作答，不保留跨题上下文。', flush=True)
    while True:
        try:
            question = input('\n问题> ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n已退出。', flush=True)
            break
        if question.lower() in ('quit', 'exit'):
            print('已退出。', flush=True)
            break
        if not question:
            continue
        try:
            answer_and_save(question)
        except KeyboardInterrupt:
            print('\n已退出。', flush=True)
            break
        except Exception as exc:
            print(f'[本题失败] {type(exc).__name__}；可继续输入下一题。', flush=True)
