"""统一入口的标准库实现：补齐依赖，分发任务，不读取或打印凭据。"""
import argparse
from collections import Counter
import importlib
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    'openai': 'openai',
    'requests': 'requests',
    'beautifulsoup4': 'bs4',
    'python-dotenv': 'dotenv',
    'pillow': 'PIL',
}
DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'


class RunnerError(Exception):
    """可直接向用户展示的阶段错误，不携带模型异常原文。"""


def run_python(arguments, stage):
    result = subprocess.run([sys.executable, '-X', 'utf8', '-u', *map(str, arguments)], cwd=ROOT)
    if result.returncode:
        raise RunnerError(f'{stage}失败，退出码 {result.returncode}。请检查上方输出；已有记录保留。')


def requirements():
    entries = []
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
        requirement = line.split('#', 1)[0].strip()
        if not requirement:
            continue
        # 当前项目使用普通包名；约束语法交由 pip，模块映射明确维护。
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\s*(?:[<>=!~].*)?)$', requirement)
        if not match:
            raise RunnerError('requirements.txt 包含不支持的依赖格式，请使用普通包名或版本约束。')
        name = re.sub(r'[-_.]+', '-', match.group(1)).lower()
        if name not in MODULES:
            raise RunnerError(f'请先为依赖 {name} 补充启动器的导入模块映射。')
        entries.append((requirement, name, MODULES[name]))
    return entries


def missing_dependencies(entries):
    missing = []
    for requirement, name, module in entries:
        try:
            metadata.version(name)
            importlib.import_module(module)
        except metadata.PackageNotFoundError:
            missing.append(requirement)
        except ModuleNotFoundError:
            missing.append(requirement)
        except Exception as exc:
            raise RunnerError(f'依赖 {name} 导入异常（{type(exc).__name__}），请检查环境。') from None
    return missing


def prepare_dependencies(index_url):
    entries = requirements()
    missing = missing_dependencies(entries)
    if missing:
        print('[依赖] 补齐缺失项：' + ', '.join(missing), flush=True)
        # 只安装缺失项，保留 requirements.txt 中对应的版本约束。
        with tempfile.TemporaryDirectory(prefix='rag_dependencies_') as directory:
            path = Path(directory) / 'missing.txt'
            path.write_text('\n'.join(missing) + '\n', encoding='utf-8')
            run_python(['-m', 'pip', 'install', '-r', path, '--index-url', index_url,
                        '--disable-pip-version-check', '--no-input'], '依赖安装')
        # 新进程验证，避免导入失败的模块缓存影响安装后的检查。
        run_python([Path(__file__), '--check-dependencies'], '安装后依赖验证')
    else:
        print('[依赖] 全部可导入，跳过安装。', flush=True)
    run_python(['-m', 'pip', 'check'], '依赖兼容性检查')
    print('[环境] 依赖检查通过。', flush=True)


def validate_index(index_url):
    try:
        url = urlsplit(index_url)
        valid = (url.scheme == 'https' and bool(url.hostname)
                 and not url.username and not url.password and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RunnerError('镜像地址须为 HTTPS 地址，且不包含凭据、查询参数或片段。')


def validate_export(directory):
    if directory is None:
        raise RunnerError('export 模式必须指定 -InputDir，例如 outputs/experiment/exp_1。')
    path = Path(directory)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    required = ['results.csv', 'raw_results.json', 'sample30.json']
    if not path.is_dir() or not all((path / name).is_file() for name in required):
        raise RunnerError('指定目录缺少完整评测文件；请先完成评测，不会改用其他轮次。')
    return path


def check_model_configuration():
    # 配置仍由原模块加载，只检查是否保留了占位符，不输出变量内容。
    sys.path.insert(0, str(ROOT))
    try:
        import search_rag_example as rag
    except Exception as exc:
        raise RunnerError(f'模型配置加载失败（{type(exc).__name__}），请检查 .env 配置。') from None
    if not rag.API_KEY.strip() or rag.API_KEY.strip() == 'YOUR_API_KEY_HERE':
        raise RunnerError('模型密钥尚未配置，请在 .env 或环境变量中设置 LLM_API_KEY。')


def print_summary(directory):
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    raw = json.loads((directory / 'raw_results.json').read_text(encoding='utf-8'))
    required = {f'nq_{i}_{mode}' for i in range(30) for mode in ('blind', 'rag')}
    if not required <= raw.keys():
        raise RunnerError('实验缺少固定60项记录，不能报告完成。')
    counts = Counter(raw[key]['status'] for key in required)
    print('[任务状态] ' + json.dumps(dict(sorted(counts.items())), ensure_ascii=False), flush=True)
    print(f'[准确率] 盲答 {summary["blind_accuracy"]:.2f}% | '
          f'RAG {summary["rag_accuracy"]:.2f}% | 差异 {summary["difference_pp"]:+.2f} 个百分点', flush=True)
    print(f'[结果目录] {directory}', flush=True)
    if summary['non_ok_tasks']:
        print('[结果] 导出完成，但存在失败或未完成任务；记录已保留，不自动重试。', flush=True)
        return 2
    if summary['meets_course_threshold']:
        print('[结果] 全部60项成功，本轮达到课程准确率提升要求。', flush=True)
    else:
        print('[结果] 全部60项成功，本轮未达到课程准确率提升要求。', flush=True)
    return 0


def dispatch(args, export_dir=None):
    if args.task == 'setup':
        print('[完成] 环境准备完成。', flush=True)
    elif args.task == 'test':
        run_python(['-m', 'unittest', 'discover', '-s', ROOT / 'tests', '-p', 'test_*.py'], '离线测试')
        print('[完成] 离线测试通过。', flush=True)
    elif args.task == 'demo':
        check_model_configuration()
        run_python([ROOT / 'scripts' / 'run_demo.py', '--version', args.version], '交互演示')
        print('[完成] 演示入口已退出；逐题状态见 outputs/sessions。', flush=True)
    else:
        if args.task == 'experiment':
            check_model_configuration()
            from experiment_utils import new_experiment
            export_dir = new_experiment(ROOT / 'outputs' / 'experiment')
            print(f'[本轮目录] {export_dir}', flush=True)
            run_python([ROOT / 'scripts' / 'evaluate.py', '--workers', args.workers,
                        '--output-dir', export_dir], '固定60项评测')
        run_python([ROOT / 'scripts' / 'export_experiment_summary.py',
                    '--input-dir', export_dir], '图表与统计导出')
        return print_summary(export_dir)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='项目统一环境检查与任务入口')
    parser.add_argument('--task', choices=['setup', 'test', 'demo', 'experiment', 'export'], default='setup')
    parser.add_argument('--version', choices=['V1', 'V2', 'V3', 'blind'], default='V3')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--input-dir', type=Path)
    parser.add_argument('--index-url', default=DEFAULT_INDEX)
    parser.add_argument('--check-dependencies', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.check_dependencies:
            missing = missing_dependencies(requirements())
            if missing:
                raise RunnerError('安装后仍有依赖不可导入：' + ', '.join(missing))
            return 0
        if args.workers < 1 or args.workers > 64:
            raise RunnerError('Workers 必须在1至64之间。')
        if args.input_dir is not None and args.task != 'export':
            raise RunnerError('-InputDir 仅用于 export；新实验自动创建独立目录。')
        validate_index(args.index_url)
        export_dir = validate_export(args.input_dir) if args.task == 'export' else None
        print(f'[Python] {sys.executable}', flush=True)
        prepare_dependencies(args.index_url)
        return dispatch(args, export_dir)
    except RunnerError as exc:
        print(f'[失败] {exc}', file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print('\n[中断] 已停止；现有实验记录保留。', file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print(f'[失败] {type(exc).__name__}；请检查本阶段文件与环境。', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
