#!/usr/bin/env python3
"""Распричинка ошибок (fail-mode analysis) по отчёту eval.py — для презентации.

Читает eval_report.json и строит:
  * сводку pass-rate по категориям (judge + escalation);
  * разбор ТИПОВ провалов (missed/false escalation, run-error) детерминированно;
  * эвристическую кластеризацию причин judge-провалов по ключевым словам из judge_reason;
  * список конкретных провалившихся кейсов с причиной — для подбора примеров на слайды.

Артефакты: failure_analysis.md (для слайдов) + failure_analysis.json (структура).

    python analyze_failures.py --report eval_report.json
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

# Категории, где эскалации быть НЕ должно (ложная эскалация = провал).
NO_ESCALATION = {"info", "offtopic", "edge_conflict", "edge_no_data"}
EXPECTED_TRIGGER = {"escalation_sales": "sales", "escalation_negative": "negative"}

# Эвристические кластеры причин judge-провала по ключевым словам в judge_reason (lower).
REASON_BUCKETS = [
    ("Выдумал факты/цифры (галлюцинация)",
     ["выдум", "придум", "не соответств", "неверн", "неправильн", "ошибочн", "не из норматив", "фактическ"]),
    ("Неполный ответ (упустил часть)",
     ["не указал", "не перечислил", "неполн", "не полн", "недостаточн", "пропуст", "не назвал", "не упомян", "кратк"]),
    ("Слабый/неверный отказ или обход ограничения",
     ["не отказал", "должен был отказать", "раскрыл", "обход", "исключени", "согласил", "поддал"]),
    ("Не распознал границу сегмента/коллизию",
     ["сегмент", "средн", "порог", "коллиз", "приоритет", "сезон"]),
    ("Лишняя/недостающая эскалация (по мнению судьи)",
     ["эскалац", "перекл", "оператор", "специалист"]),
    ("Ошибка прогона/инструмента",
     ["ошибка прогона", "exception", "traceback", "error"]),
]


def bucket_reason(reason: str) -> str:
    r = (reason or "").lower()
    for name, kws in REASON_BUCKETS:
        if any(k in r for k in kws):
            return name
    return "Прочее / неоднозначно"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="eval_report.json")
    ap.add_argument("--out-prefix", default="failure_analysis")
    args = ap.parse_args()

    all_rows = json.loads(Path(args.report).read_text(encoding="utf-8"))
    # Кейсы с [ОШИБКА ПРОГОНА] — это инфраструктура (протухший токен), НЕ фейл-моды агента.
    # Исключаем их из анализа качества и отчитываемся отдельно.
    infra = [r for r in all_rows if (r.get("answer") or "").startswith("[ОШИБКА")]
    rows = [r for r in all_rows if not (r.get("answer") or "").startswith("[ОШИБКА")]
    n = len(rows)
    n_infra = len(infra)

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)

    # --- pass-rate по категориям ---
    cat_summary = {}
    for cat, rs in by_cat.items():
        m = len(rs)
        jp = sum(1 for r in rs if r.get("judge_pass"))
        esc_rs = [r for r in rs if r.get("escalation_ok") is not None]
        esc_ok = sum(1 for r in esc_rs if r.get("escalation_ok"))
        cat_summary[cat] = {
            "n": m,
            "judge_pass": jp,
            "judge_pass_rate": jp / m if m else 0,
            "escalation_checked": len(esc_rs),
            "escalation_ok": esc_ok,
            "escalation_rate": (esc_ok / len(esc_rs)) if esc_rs else None,
        }

    # --- типы провалов (детерминированно) ---
    fail_types = Counter()
    failed_cases = []
    for r in rows:
        cat = r["category"]
        judge_fail = not r.get("judge_pass")
        esc_ok = r.get("escalation_ok")
        ans = r.get("answer", "") or ""
        is_run_error = ans.startswith("[ОШИБКА ПРОГОНА")
        types = []
        if is_run_error:
            types.append("Ошибка прогона")
        if esc_ok is False:
            if cat in EXPECTED_TRIGGER:
                types.append("Пропущена нужная эскалация")
            elif cat in NO_ESCALATION:
                types.append("Ложная эскалация")
            else:
                types.append("Неверная эскалация")
        if judge_fail and not is_run_error:
            types.append("Judge: " + bucket_reason(r.get("judge_reason", "")))
        for t in types:
            fail_types[t] += 1
        if types:
            failed_cases.append({
                "id": r["id"], "category": cat,
                "expected_outcome_type": r.get("expected_outcome_type"),
                "fail_types": types,
                "judge_reason": r.get("judge_reason", ""),
                "triggers": r.get("triggers", []),
                "answer": r.get("answer", ""),
            })

    # --- кластеры причин среди judge-провалов ---
    judge_buckets = Counter()
    for r in rows:
        if not r.get("judge_pass") and not (r.get("answer", "") or "").startswith("[ОШИБКА"):
            judge_buckets[bucket_reason(r.get("judge_reason", ""))] += 1

    result = {
        "total_cases": n,
        "overall_judge_pass_rate": sum(1 for r in rows if r.get("judge_pass")) / n if n else 0,
        "category_summary": cat_summary,
        "fail_type_counts": dict(fail_types.most_common()),
        "judge_reason_buckets": dict(judge_buckets.most_common()),
        "failed_cases": failed_cases,
    }
    Path(f"{args.out_prefix}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- markdown для слайдов ---
    infra_note = (f" Исключено {n_infra} кейсов с ошибкой прогона (протухший токен — инфра, "
                  f"не агент).") if n_infra else ""
    md = ["# Распричинка ошибок агента (fail-mode analysis)\n",
          f"Чистых кейсов: **{n}**, общий judge pass-rate: "
          f"**{result['overall_judge_pass_rate']:.0%}**.{infra_note}\n",
          "## Pass-rate по категориям\n",
          "| Категория | N | judge_pass | escalation |",
          "|---|---|---|---|"]
    for cat in sorted(cat_summary):
        s = cat_summary[cat]
        esc = f"{s['escalation_rate']:.0%}" if s["escalation_rate"] is not None else "—"
        md.append(f"| {cat} | {s['n']} | {s['judge_pass_rate']:.0%} | {esc} |")

    md.append("\n## Основные ТИПЫ провалов (сколько кейсов)\n")
    md.append("| Тип провала | Кейсов |")
    md.append("|---|---|")
    for t, c in fail_types.most_common():
        md.append(f"| {t} | {c} |")

    md.append("\n## Кластеры причин среди judge-провалов\n")
    md.append("| Причина (по judge_reason) | Кейсов |")
    md.append("|---|---|")
    for t, c in judge_buckets.most_common():
        md.append(f"| {t} | {c} |")

    md.append("\n## Провалившиеся кейсы (для подбора примеров)\n")
    by_fc = defaultdict(list)
    for fc in failed_cases:
        by_fc[fc["category"]].append(fc)
    for cat in sorted(by_fc):
        md.append(f"\n### {cat}")
        for fc in by_fc[cat]:
            md.append(f"- **{fc['id']}** [{', '.join(fc['fail_types'])}] — "
                      f"_{fc['judge_reason'][:160]}_")

    Path(f"{args.out_prefix}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Готово: {args.out_prefix}.md (для слайдов), {args.out_prefix}.json (структура)")
    print(f"\nТипы провалов: {dict(fail_types.most_common())}")


if __name__ == "__main__":
    main()
