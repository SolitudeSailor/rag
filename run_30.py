# -*- coding: utf-8 -*-
"""直接运行本文件，自动完成教师30题、两模式共60项评测并导出结果。"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        if (ROOT / '.venv').exists():
            print('[启动失败] 现有.venv缺少Python，请先修复该环境。', flush=True)
            return 1
        python = Path(sys.executable)
    print('[30题测评] 自动运行15道天气＋15道汇率、两模式共60项，完成后导出CSV和图表。', flush=True)
    try:
        return subprocess.run(
            [str(python), '-X', 'utf8', '-u', str(ROOT / 'scripts' / 'project_runner.py'),
             '--task', 'experiment', '--workers', '3'], cwd=ROOT).returncode
    except KeyboardInterrupt:
        print('\n[中断] 已有记录保留；下次运行将新建轮次。', flush=True)
        return 130
    except OSError as exc:
        print(f'[启动失败] {type(exc).__name__}；请检查项目Python环境。', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
