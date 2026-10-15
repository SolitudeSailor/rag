"""补充评价的误判边界、对称性及复核导入保护。"""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from supplementary_evaluation import assess, evaluate, load_aliases, load_reviews

ROOT = Path(__file__).resolve().parents[1]
ALIASES = ROOT / 'data' / 'evaluation_aliases.json'
QUESTION = "who's the model on let's make a deal"


class SupplementaryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.aliases = load_aliases(ALIASES)

    def result(self, response, gold=None, question=QUESTION, status='ok'):
        return assess(question, gold or ['Tiffany Adams Coyne'],
                      dict(answer=response, status=status), self.aliases)

    def test_verified_alias_and_question_scope(self):
        self.assertEqual(self.result('The model is **Tiffany Coyne**.')['decision'], 'matched')
        self.assertEqual(self.result('Tiffany Coyne', question='who won the prize')['decision'], 'needs_review')
        self.assertEqual(self.result('Tiffany Coyne', gold=['Tiffany Jones Coyne'])['decision'], 'needs_review')

    def test_boundaries_prevent_embedded_matches(self):
        for response, gold in [('1979', ['7']), ('price', ['Rice']), ('Tiffany Coynes', ['Tiffany Adams Coyne'])]:
            with self.subTest(response=response):
                self.assertEqual(self.result(response, gold)['decision'], 'needs_review')

    def test_uncertainty_negation_and_multiple_candidates_require_review(self):
        for response in ['It is not Tiffany Coyne.', 'Tiffany Coyne or Carol Merrill.',
                         'Maybe Tiffany Coyne.', '不是 Tiffany Coyne。',
                         "Tiffany Coyne. I can't confirm it."]:
            with self.subTest(response=response):
                self.assertEqual(self.result(response)['decision'], 'needs_review')

    def test_late_mentions_and_numeric_context_require_review(self):
        self.assertEqual(self.result('I choose someone else. Tiffany Coyne is mentioned elsewhere.')['decision'], 'needs_review')
        self.assertEqual(self.result('The answer is 7 seasons.', ['7'])['decision'], 'needs_review')
        self.assertEqual(self.result('The answer is 3.5.', ['3'])['decision'], 'needs_review')

    def test_normalization_and_unrecognized_synonym(self):
        self.assertEqual(self.result('ＴＩＦＦＡＮＹ  ＡＤＡＭＳ ＣＯＹＮＥ')['decision'], 'matched')
        self.assertEqual(self.result('白色', ['white'])['decision'], 'needs_review')
        self.assertEqual(self.result('Tiffany Coyne', status='ConnectionError')['decision'], 'incorrect')

    def test_review_import_validates_identity_and_evidence(self):
        rows = [dict(task='nq_0_rag', record_sha256='abc', execution_status='ok')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.csv'
            def write(**changes):
                row = dict(task='nq_0_rag', record_sha256='abc', decision='correct',
                           reviewer='核验人', reason='已核对人物', evidence='官方人物页')
                row.update(changes)
                with path.open('w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerow(row)
            write()
            self.assertEqual(load_reviews(path, rows)['nq_0_rag']['decision'], 'correct')
            for changes in [dict(record_sha256='changed'), dict(evidence=''), dict(decision='yes')]:
                write(**changes)
                with self.assertRaises(ValueError):
                    load_reviews(path, rows)

    def test_fixed_sample_both_modes_and_originals_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sample = [dict(question=QUESTION, answer=['Tiffany Adams Coyne']) for _ in range(30)]
            raw = {f'nq_{i}_{mode}': dict(question=QUESTION, mode=mode, answer='Tiffany Coyne', status='ok')
                   for i in range(30) for mode in ('blind', 'rag')}
            for name, content in [('sample30.json', sample), ('raw_results.json', raw)]:
                (directory / name).write_text(json.dumps(content), encoding='utf-8')
            sentinel = b'original six-column results'
            (directory / 'results.csv').write_bytes(sentinel)
            out, summary = evaluate(directory, ALIASES)
            for mode in ('blind', 'rag'):
                stats = summary['modes'][mode]
                self.assertEqual(stats['course_correct'], 0)
                self.assertEqual(stats['matched'], 30)
                self.assertIsNone(stats['semantic_accuracy_percent'])
            self.assertEqual((directory / 'results.csv').read_bytes(), sentinel)
            second, _ = evaluate(directory, ALIASES)
            self.assertNotEqual(out, second)
            with (out / 'manual_review.csv').open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                fields = reader.fieldnames
                reviews = list(reader)
                self.assertEqual(len(reviews), 60)
            for row in reviews:
                row.update(decision='correct', reviewer='测试核验人', reason='测试结论', evidence='测试来源')
            review_path = directory / 'filled_review.csv'
            with review_path.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(reviews)
            reviewed_out, reviewed = evaluate(directory, ALIASES, review_path)
            self.assertEqual(reviewed['modes']['rag']['semantic_accuracy_percent'], 100.0)
            with (reviewed_out / 'manual_review.csv').open(encoding='utf-8-sig', newline='') as f:
                self.assertTrue(all(row['decision'] == 'correct' for row in csv.DictReader(f)))
            self.assertEqual((directory / 'results.csv').read_bytes(), sentinel)


if __name__ == '__main__':
    unittest.main()
