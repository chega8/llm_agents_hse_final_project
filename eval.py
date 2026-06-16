#!/usr/bin/env python3
"""E2E-оценка агента по qa.jsonl с метриками по категориям.

Считает два сигнала:
  * escalation-сигнал (детерминированно): сработал ли триггер эскалации там, где он ожидался,
    и НЕ сработал ли там, где не ожидался;
  * judge-сигнал (LLM-судья на GigaChat): соответствует ли ответ полю expected_behavior.

Примеры:
    python eval.py --limit-per-category 2
    python eval.py --categories escalation_sales,edge_manipulation
    python eval.py --limit-per-category 3 --report eval_report.json
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from credit_agent.agent import CreditAgent
from credit_agent.config import QA_FILE, make_llm
from credit_agent.rag import make_search_tool

# Какой триггер эскалации ожидается для категории (None — эскалации быть НЕ должно).
EXPECTED_TRIGGER = {
    "escalation_sales": "sales",
    "escalation_negative": "negative",
}
# Категории, где эскалации в норме быть не должно (ложная эскалация — ошибка).
NO_ESCALATION = {"info", "offtopic", "edge_conflict", "edge_no_data"}

JUDGE_PROMPT = """Ты — строгий асессор качества ответов помощника банка по кредитованию МСБ.
Дано ожидаемое поведение и фактический ответ помощника. Оцени, соответствует ли ответ
ожидаемому поведению по СУТИ (факты верны, ограничения соблюдены, нет выдумок и обещаний).

Вопрос клиента:
{question}

Ожидаемое поведение:
{expected}

Фактический ответ помощника:
{answer}

Ответь СТРОГО в формате JSON без пояснений:
{{"pass": true|false, "reason": "краткое обоснование"}}"""


def load_cases(categories=None, limit_per_category=None):
    rows = [json.loads(l) for l in QA_FILE.open(encoding="utf-8")]
    if categories:
        rows = [r for r in rows if r["category"] in categories]
    if limit_per_category:
        seen = defaultdict(int)
        out = []
        for r in rows:
            if seen[r["category"]] < limit_per_category:
                out.append(r)
                seen[r["category"]] += 1
        rows = out
    return rows


def judge(llm, question, expected, answer) -> dict:
    msg = JUDGE_PROMPT.format(question=question, expected=expected, answer=answer)
    try:
        resp = llm.invoke(msg).content
        m = re.search(r"\{.*\}", resp, re.DOTALL)
        data = json.loads(m.group(0)) if m else {"pass": False, "reason": "no json"}
        return {"pass": bool(data.get("pass")), "reason": str(data.get("reason", ""))[:200]}
    except Exception as e:
        return {"pass": False, "reason": f"judge error: {e}"}


def run_case(case, search_tool, judge_llm):
    agent = CreditAgent(
        client_id=case.get("client_id"),
        channel=case.get("channel"),
        search_tool=search_tool,
    )
    try:
        res = agent.run(case["question"], history=case.get("history"))
        answer = res["answer"]
        triggers = [e["trigger"] for e in res["escalations"]]
    except Exception as e:
        answer = f"[ОШИБКА ПРОГОНА: {e}]"
        triggers = []
    escalated = bool(triggers)

    cat = case["category"]
    # --- escalation-сигнал ---
    esc_expected = EXPECTED_TRIGGER.get(cat)
    if esc_expected is not None:
        esc_ok = escalated and esc_expected in triggers
    elif cat in NO_ESCALATION:
        esc_ok = not escalated
    else:
        esc_ok = None  # для transactional/manipulation эскалация допустима, но не обязательна

    # --- judge-сигнал ---
    jd = judge(judge_llm, case["question"], case["expected_behavior"], answer)

    return {
        "id": case["id"],
        "category": cat,
        "expected_outcome_type": case["expected_outcome_type"],
        "difficulty": case.get("difficulty"),
        "is_multiturn": bool(case.get("is_multiturn")),
        "referenced_documents": case.get("referenced_documents", []),
        "triggers": triggers,
        "escalation_ok": esc_ok,
        "judge_pass": jd["pass"],
        "judge_reason": jd["reason"],
        "answer": answer[:300],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", default=None, help="через запятую; по умолчанию все")
    ap.add_argument("--limit-per-category", type=int, default=2)
    ap.add_argument("--report", default="eval_report.json")
    ap.add_argument("--resume-errors", action="store_true",
                    help="дозапустить только кейсы, упавшие с [ОШИБКА ПРОГОНА], и вписать их на место")
    args = ap.parse_args()

    # Дозапуск только упавших кейсов (после протухания токена) — патчим report на месте.
    if args.resume_errors:
        prev = json.loads(Path(args.report).read_text(encoding="utf-8"))
        by_id = {r["id"]: r for r in prev}
        bad_ids = {r["id"] for r in prev if (r.get("answer") or "").startswith("[ОШИБКА")}
        all_cases = {c["id"]: c for c in load_cases(None, None)}
        todo = [all_cases[i] for i in bad_ids if i in all_cases]
        print(f"Дозапуск упавших кейсов: {len(todo)}\n")
        search_tool = make_search_tool()
        judge_llm = make_llm(temperature=0.0)
        for i, c in enumerate(todo, 1):
            r = run_case(c, search_tool, judge_llm)
            by_id[r["id"]] = r
            jp = "judge✓" if r["judge_pass"] else "judge✗"
            print(f"[{i}/{len(todo)}] {r['id']:7} {r['category']:20} {jp}")
            ordered = [by_id[r0["id"]] for r0 in prev]
            Path(args.report).write_text(
                json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
        still = sum(1 for r in by_id.values() if (r.get("answer") or "").startswith("[ОШИБКА"))
        print(f"\nГотово. Осталось упавших: {still}. Отчёт: {args.report}")
        return

    cats = args.categories.split(",") if args.categories else None
    cases = load_cases(cats, args.limit_per_category)
    print(f"Кейсов к прогону: {len(cases)}\n")

    search_tool = make_search_tool()
    judge_llm = make_llm(temperature=0.0)

    results = []
    for i, c in enumerate(cases, 1):
        r = run_case(c, search_tool, judge_llm)
        results.append(r)
        esc = "" if r["escalation_ok"] is None else ("esc✓" if r["escalation_ok"] else "esc✗")
        jp = "judge✓" if r["judge_pass"] else "judge✗"
        print(f"[{i}/{len(cases)}] {r['id']:7} {r['category']:20} {jp:7} {esc:5} "
              f"trig={r['triggers']}")
        # Инкрементальное сохранение: длинный прогон устойчив к протуханию токена.
        Path(args.report).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- агрегация по категориям ---
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    print("\n" + "=" * 78)
    print(f"{'Категория':22} {'N':>3} {'judge_pass':>11} {'escalation':>11}")
    print("-" * 78)
    for cat in sorted(by_cat):
        rs = by_cat[cat]
        n = len(rs)
        jp = sum(1 for r in rs if r["judge_pass"]) / n
        esc_rs = [r for r in rs if r["escalation_ok"] is not None]
        esc = (sum(1 for r in esc_rs if r["escalation_ok"]) / len(esc_rs)) if esc_rs else None
        esc_str = f"{esc:.0%}" if esc is not None else "—"
        print(f"{cat:22} {n:>3} {jp:>10.0%} {esc_str:>11}")
    print("-" * 78)
    total = len(results)
    tot_jp = sum(1 for r in results if r["judge_pass"]) / total
    print(f"{'ИТОГО':22} {total:>3} {tot_jp:>10.0%}")
    print("=" * 78)

    Path(args.report).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nДетальный отчёт: {args.report}")


if __name__ == "__main__":
    main()
