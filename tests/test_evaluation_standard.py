"""离线验证课程评分口径与失败题处理。"""
import csv
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from experiment_utils import FIELDS, build_rows, correct, record_correct, write_results


class CourseScoringTests(unittest.TestCase):
    def test_literal_course_rule(self):
        cases = [
            ('BEIJING, China', ['beijing'], True),
            ('Jerry Rice', ['other', 'Jerry Rice'], True),
            ('Tiffany Coyne', ['Tiffany Adams Coyne'], False),
            ('3–6%', ['3 to 6%'], False),
            ('1979', ['7'], True),
            ('Gadsden; Union County', ['Union'], True),
            ('', ['white'], False),
        ]
        for response, gold, expected in cases:
            with self.subTest(response=response):
                self.assertEqual(correct(response, gold), expected)

    def test_nonfinal_outputs_do_not_score(self):
        for status in ['unfinished_search', 'ConnectionError']:
            self.assertFalse(record_correct(dict(status=status, answer='Jerry Rice'), ['Jerry Rice']))

    def test_fixed_denominator_and_csv(self):
        sample = [dict(question=str(i), answer=['white']) for i in range(30)]
        raw = {f'nq_{i}_{mode}': dict(question=str(i), mode=mode,
               status='ok' if i else 'ConnectionError', answer='white' if i else '',
               search_log=[], prompt_tokens=1, completion_tokens=1, total_tokens=2)
               for i in range(30) for mode in ['blind', 'rag']}
        with tempfile.TemporaryDirectory() as directory:
            rows = write_results(directory, sample, raw)
            self.assertEqual(len(rows), 30)
            self.assertEqual(sum(r['blind_correct'] for r in rows), 29)
            with (Path(directory) / 'results.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(next(csv.reader(f)), FIELDS)
        raw.pop('nq_0_rag')
        with self.assertRaises(ValueError):
            build_rows(sample, raw)


if __name__ == '__main__':
    unittest.main()
