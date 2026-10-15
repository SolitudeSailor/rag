"""真实模型 + 固定模拟工具观察的提示词诊断；不是网页检索或课程成绩。"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 均为虚构问题、虚构观察；不使用课程样本或评价别名。
CASES = [
    dict(id='missing_property', question='Who provided the English voice of Miko in Lantern Bay (2018)?',
         entity='Lantern Bay', property=r'english.*(?:voice|dub)|(?:voice|dub).*english',
         observations=[
             ['【模拟电影页面】Lantern Bay (2018). Japanese voice of Miko: Ryota Nami. This excerpt does not identify the English voice cast.'],
             ['【模拟英文配音表】Lantern Bay (2018), English-language dub: Miko — Avery Quinn. Original Japanese cast: Ryota Nami.']],
         searches=2, answer='Avery Quinn', avoid='Ryota Nami'),
    dict(id='wrong_year_category', question='Who won the Sable Prize for Chemistry in 2022?',
         entity='Sable Prize', property=r'chemistry', required_year='2022',
         observations=[
             ['【模拟无关页面】Sable Prize for Physics, 2023 winner: Nadia Frost. No chemistry result is provided.'],
             ['【模拟获奖公告】Sable Prize for Chemistry, 2022 winner: Eli Moss.']],
         searches=2, answer='Eli Moss', avoid='Nadia Frost'),
    dict(id='no_evidence', question='Who was the lead engineer of the Ardent Harbor tidal pilot in 2016?',
         entity='Ardent Harbor', property=r'engineer|project|tidal|pilot',
         observations=[[], []], searches=2, abstain=True),
    dict(id='language_and_stop', question='Which city hosted the Veridian Health Expo in 2019?',
         entity='Veridian Health Expo', property=r'city|host|location|venue|2019',
         observations=[['【模拟展会议程】Veridian Health Expo：2019 年举办城市为 Reykjavik；2020 年举办城市为 Paris。']],
         searches=1, answer='Reykjavik', avoid='Paris'),
]


def checks(case, answer, queries):
    results = dict(search_count=len(queries) == case['searches'],
                   query_entity=bool(queries) and all(case['entity'].casefold() in q.casefold() for q in queries),
                   query_property=bool(queries) and bool(re.search(case['property'], queries[-1], re.I)),
                   no_third_search=len(queries) <= 2 and not re.match(r'^\s*搜索[:：]', answer),
                   original_language=not bool(re.search(r'[\u4e00-\u9fff]', answer)))
    if case.get('required_year'):
        results['query_year'] = bool(queries) and case['required_year'] in queries[-1]
    if case.get('answer'):
        results['answer_supported'] = case['answer'].casefold() in answer.casefold()
        results['no_wrong_candidate'] = case['avoid'].casefold() not in answer.casefold()
    if case.get('abstain'):
        results['abstains'] = bool(re.search(
            r"cannot|can't|could not|couldn't|unable|not found|no reliable|no relevant|don't know|not determine|no information", answer, re.I))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    baseline = runpy.run_path(str(args.baseline_dir / 'prompt_versions.py'))['V3']
    from scripts.prompt_versions import V3
    import search_rag_example as rag
    if rag.SYSTEM_PROMPT != V3:
        raise SystemExit('提示词副本不同步')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    original_search, original_create = rag.bing_search, rag.client.chat.completions.create
    bounded_client = rag.client.with_options(timeout=45.0, max_retries=0)
    results = dict(kind='synthetic_tool_observations_real_model', model=rag.MODEL,
                   temperature=0.3, request_timeout_seconds=45, max_retries=0,
                   started_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
                   note='模拟工具材料，不联网检索、不计课程成绩；启发式检查不能代替人工核验。',
                   cases=CASES, prompt_sha256={k: hashlib.sha256(v.encode()).hexdigest()
                                               for k, v in [('before', baseline), ('after', V3)]}, results=[])
    destination = args.output_dir / 'results.json'
    blocked = False
    for version, prompt in [('before', baseline), ('after', V3)]:
        for case in CASES:
            queries, usage = [], []

            def simulated_search(query, top_k=3):
                index = len(queries)
                queries.append(query)
                return case['observations'][min(index, len(case['observations']) - 1)]

            def tracked_create(*a, **kw):
                response = bounded_client.chat.completions.create(*a, **kw)
                info = getattr(response, 'usage', None)
                usage.append({name: getattr(info, name, None)
                              for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')})
                return response

            rag.bing_search, rag.client.chat.completions.create = simulated_search, tracked_create
            result = dict(version=version, case=case['id'], queries=queries, usage=usage)
            if blocked:
                result.update(status='not_run_after_error', passes_all=False)
                results['results'].append(result)
                destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
                continue
            result['status'] = 'running'
            results['results'].append(result)
            destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            try:
                with redirect_stdout(io.StringIO()):
                    answer, _ = rag.rag_answer(case['question'], system_prompt=prompt)
                result.update(status='ok', answer=answer, checks=checks(case, answer, queries))
                result['passes_all'] = all(result['checks'].values())
            except Exception as exc:
                result.update(status=type(exc).__name__, passes_all=False)
                blocked = True
            finally:
                rag.bing_search, rag.client.chat.completions.create = original_search, original_create
            destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{version} {case["id"]}: {result["status"]}, pass={result["passes_all"]}, searches={len(queries)}', flush=True)
    print(destination, flush=True)
    if blocked:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
