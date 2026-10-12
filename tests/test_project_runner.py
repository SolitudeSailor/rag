"""验证统一入口补齐依赖、精确导出目录及失败停止，不联网或调用模型。"""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_runner as runner


def results_fixture(directory, failed=False):
    directory.mkdir(parents=True, exist_ok=True)
    raw = {f'nq_{i}_{mode}': {'status': 'ok'}
           for i in range(30) for mode in ('blind', 'rag')}
    if failed:
        raw['nq_0_rag']['status'] = 'APIConnectionError'
    summary = dict(blind_accuracy=60, rag_accuracy=60, difference_pp=0,
                   meets_course_threshold=False,
                   non_ok_tasks=['nq_0_rag'] if failed else [])
    (directory / 'raw_results.json').write_text(json.dumps(raw), encoding='utf-8')
    (directory / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    (directory / 'sample30.json').write_text('[]', encoding='utf-8')
    (directory / 'results.csv').write_text('question\n', encoding='utf-8')


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_ready_environment_skips_install(self):
        with patch.object(runner, 'missing_dependencies', return_value=[]), \
                patch.object(runner, 'run_python') as run:
            runner.prepare_dependencies(runner.DEFAULT_INDEX)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['-m', 'pip', 'check'])

    def test_missing_pillow_only_installs_missing_requirement(self):
        calls = []

        def capture(arguments, stage):
            calls.append(list(arguments))
            if arguments[:3] == ['-m', 'pip', 'install']:
                path = Path(arguments[arguments.index('-r') + 1])
                self.assertEqual(path.read_text(encoding='utf-8'), 'Pillow\n')
                self.assertNotIn('--upgrade', arguments)
                self.assertEqual(arguments[arguments.index('--index-url') + 1], runner.DEFAULT_INDEX)

        with patch.object(runner, 'missing_dependencies', return_value=['Pillow']), \
                patch.object(runner, 'run_python', side_effect=capture):
            runner.prepare_dependencies(runner.DEFAULT_INDEX)
        self.assertEqual(len(calls), 3)
        self.assertIn('--check-dependencies', calls[1])
        self.assertEqual(calls[2], ['-m', 'pip', 'check'])

    def test_failed_install_does_not_run_task(self):
        with patch.object(runner, 'missing_dependencies', return_value=['Pillow']), \
                patch.object(runner, 'run_python', side_effect=runner.RunnerError('安装失败')) as run, \
                patch.object(runner, 'dispatch') as dispatch, redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(['--task', 'setup']), 1)
        self.assertEqual(run.call_count, 1)
        dispatch.assert_not_called()

    def test_subprocess_uses_current_interpreter_and_argument_list(self):
        with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            runner.run_python(['some file.py', '--input-dir', 'a path with spaces'], '导出')
        self.assertEqual(run.call_args.args[0],
                         [sys.executable, '-X', 'utf8', '-u', 'some file.py', '--input-dir', 'a path with spaces'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_import_check_does_not_assume_pillow_module_name(self):
        with patch.object(runner.metadata, 'version', return_value='12.3.0'), \
                patch.object(runner.importlib, 'import_module', side_effect=ModuleNotFoundError):
            self.assertEqual(runner.missing_dependencies([('Pillow', 'pillow', 'PIL')]), ['Pillow'])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_export_requires_explicit_directory_before_environment_changes(self):
        with patch.object(runner, 'prepare_dependencies') as prepare, redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(['--task', 'export']), 1)
        prepare.assert_not_called()

    def test_export_does_not_evaluate_or_load_model(self):
        with tempfile.TemporaryDirectory(prefix='rag runner ') as tmp:
            root = Path(tmp)
            chosen = root / 'outputs' / 'experiment' / 'exp_2'
            results_fixture(chosen)
            results_fixture(root / 'outputs' / 'experiment' / 'exp_10')
            with patch.object(runner, 'ROOT', root), \
                    patch.object(runner, 'prepare_dependencies'), \
                    patch.object(runner, 'run_python') as run, \
                    patch.object(runner, 'check_model_configuration') as model:
                self.assertEqual(runner.main(['--task', 'export', '--input-dir', str(chosen)]), 0)
            model.assert_not_called()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0],
                             [root / 'scripts' / 'export_experiment_summary.py', '--input-dir', chosen.resolve()])

    def test_experiment_exports_exact_new_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'outputs' / 'experiment' / 'exp_1'
            results_fixture(old)
            old_answer = (old / 'raw_results.json').read_bytes()
            args = SimpleNamespace(task='experiment', workers=3)
            calls = []

            def execute(arguments, stage):
                calls.append(arguments)
                if stage == '固定60项评测':
                    directory = arguments[-1]
                    self.assertEqual(directory.name, 'exp_2')
                    self.assertEqual(list(directory.iterdir()), [])
                    # 填充本次评测完成的文件，保留新目录本身。
                    results_fixture(directory)

            with patch.object(runner, 'ROOT', root), \
                    patch.object(runner, 'check_model_configuration'), \
                    patch.object(runner, 'run_python', side_effect=execute):
                self.assertEqual(runner.dispatch(args), 0)
            self.assertEqual(calls[0][-1], calls[1][-1])
            self.assertEqual((old / 'raw_results.json').read_bytes(), old_answer)

    def test_failed_evaluation_stops_before_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(runner, 'ROOT', root), \
                    patch.object(runner, 'check_model_configuration'), \
                    patch.object(runner, 'run_python', side_effect=runner.RunnerError('评测失败')) as run:
                with self.assertRaises(runner.RunnerError):
                    runner.dispatch(SimpleNamespace(task='experiment', workers=3))
            self.assertEqual(run.call_count, 1)
            self.assertTrue((root / 'outputs' / 'experiment' / 'exp_1').is_dir())

    def test_failed_tasks_have_distinct_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'exp_1'
            results_fixture(directory, failed=True)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(runner.print_summary(directory), 2)
            self.assertIn('不自动重试', output.getvalue())

    def test_below_accuracy_threshold_is_not_execution_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'exp_1'
            results_fixture(directory)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(runner.print_summary(directory), 0)
            self.assertIn('未达到', output.getvalue())

    def test_incomplete_input_never_falls_back_to_another_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaises(runner.RunnerError):
                runner.validate_export(directory)

    def test_invalid_mirror_does_not_trigger_installation(self):
        with patch.object(runner, 'prepare_dependencies') as prepare, redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(['--index-url', 'https://user:password@example.com/simple']), 1)
        prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
