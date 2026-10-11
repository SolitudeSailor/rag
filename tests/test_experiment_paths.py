"""离线检查编号、已有结果保护及并发创建。"""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from experiment_utils import latest_experiment, new_experiment


class ExperimentPathTests(unittest.TestCase):
    def test_first_run_and_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'experiment'
            with self.assertRaises(ValueError):
                latest_experiment(root)
            first = new_experiment(root)
            (first / 'answer.json').write_text('original', encoding='utf-8')
            self.assertEqual(first.name, 'exp_1')
            self.assertEqual(new_experiment(root).name, 'exp_2')
            self.assertEqual((first / 'answer.json').read_text(encoding='utf-8'), 'original')

    def test_numeric_order_and_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ['exp_2', 'exp_10', 'misc']:
                (root / name).mkdir()
            self.assertEqual(latest_experiment(root).name, 'exp_10')
            self.assertEqual(new_experiment(root).name, 'exp_11')

    def test_concurrent_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: new_experiment(tmp), range(8)))
            self.assertEqual(len(set(paths)), 8)
            self.assertEqual({p.name for p in paths}, {f'exp_{i}' for i in range(1, 9)})


if __name__ == '__main__':
    unittest.main()
