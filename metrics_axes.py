#!/usr/bin/env python3
"""Метрики агента в разрезе ДОПОЛНИТЕЛЬНЫХ осей датасета — для слайда с метриками.

Джойнит eval_report.json с data/qa/qa.jsonl по id и считает judge/escalation pass-rate
по осям, которых нет в самом отчёте:
  * difficulty (easy/medium/hard);
  * is_multiturn (одно- vs многоходовые);
  * referenced_documents → нормативный ДОКУМЕНТ (часть до '#'); кейс учитывается во всех
    документах, на которые он ссылается (мультичленство).
Плюс базовая ось category — для единой картинки.

Артефакты:
  * metrics_by_axis.md   — таблицы для слайда;
  * metrics_axes.json    — структура;
  * charts/*.png         — столбчатые диаграммы (matplotlib).
Если задан --before, добавляет колонку «было» (до фикса) для сравнения.

    python metrics_axes.py --report eval_report.json --before eval_report_before.json
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

QA_FILE = Path(__file__).resolve().parent / "data" / "qa" / "qa.jsonl"

DOC_SHORT = {
    "01_credit_products.md": "Продукты",
    "02_application_process.md": "Заявка",
    "03_early_repayment.md": "Досроч.погаш.",
    "04_restructuring.md": "Реструктур.",
    "05_customer_communication.md": "Коммуникация",
}


def load_qa():
    meta = {}
    for line in QA_FILE.open(encoding="utf-8"):
        r = json.loads(line)
        docs = set()
        for ref in r.get("referenced_documents") or []:
            doc = ref.split("#", 1)[0]
            docs.add(DOC_SHORT.get(doc, doc))
        meta[r["id"]] = {
            "difficulty": r.get("difficulty") or "—",
            "is_multiturn": bool(r.get("is_multiturn")),
            "docs": sorted(docs),
        }
    return meta


def rate(rows, key):
    """judge pass-rate по ключу группировки key(row)->список значений (для мультичленства)."""
    agg = defaultdict(lambda: [0, 0])  # bucket -> [pass, total]
    for r in rows:
        buckets = key(r)
        for b in buckets:
            agg[b][0] += int(bool(r.get("judge_pass")))
            agg[b][1] += 1
    return {b: {"pass": p, "n": n, "rate": p / n if n else 0} for b, (p, n) in agg.items()}


def esc_rate(rows, key):
    """escalation pass-rate (только по кейсам, где escalation_ok не None)."""
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        if r.get("escalation_ok") is None:
            continue
        for b in key(r):
            agg[b][0] += int(bool(r.get("escalation_ok")))
            agg[b][1] += 1
    return {b: {"ok": o, "n": n, "rate": o / n if n else 0} for b, (o, n) in agg.items()}


def build(report_path, meta, only_ids=None):
    """Считает оси по отчёту. Кейсы с [ОШИБКА ПРОГОНА] (протухший токен — инфра, не агент)
    исключаются. only_ids ограничивает выборку общим чистым набором id (apples-to-apples)."""
    rows = json.loads(Path(report_path).read_text(encoding="utf-8"))
    rows = [r for r in rows if not (r.get("answer") or "").startswith("[ОШИБКА")]
    if only_ids is not None:
        rows = [r for r in rows if r["id"] in only_ids]
    for r in rows:
        m = meta.get(r["id"], {})
        r["_difficulty"] = m.get("difficulty", "—")
        r["_multiturn"] = "многоходовые" if m.get("is_multiturn") else "одноходовые"
        r["_docs"] = m.get("docs", [])
    axes = {
        "category": rate(rows, lambda r: [r["category"]]),
        "difficulty": rate(rows, lambda r: [r["_difficulty"]]),
        "multiturn": rate(rows, lambda r: [r["_multiturn"]]),
        "document": rate(rows, lambda r: r["_docs"] or ["(без ссылки)"]),
    }
    axes_esc = {
        "category": esc_rate(rows, lambda r: [r["category"]]),
        "difficulty": esc_rate(rows, lambda r: [r["_difficulty"]]),
        "multiturn": esc_rate(rows, lambda r: [r["_multiturn"]]),
    }
    return rows, axes, axes_esc


DIFF_ORDER = ["easy", "medium", "hard"]


def _order(axis_name, keys):
    if axis_name == "difficulty":
        return [k for k in DIFF_ORDER if k in keys] + [k for k in keys if k not in DIFF_ORDER]
    if axis_name == "multiturn":
        return [k for k in ["одноходовые", "многоходовые"] if k in keys]
    return sorted(keys)


def chart(axis_name, data, title, out_png, before=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    keys = _order(axis_name, list(data.keys()))
    vals = [data[k]["rate"] * 100 for k in keys]
    labels = [f"{k}\n(n={data[k]['n']})" for k in keys]

    fig, ax = plt.subplots(figsize=(max(4, len(keys) * 1.5), 3.6))
    x = range(len(keys))
    if before:
        bvals = [before.get(k, {}).get("rate", 0) * 100 for k in keys]
        ax.bar([i - 0.2 for i in x], bvals, width=0.4, label="до фикса", color="#bbbbbb")
        ax.bar([i + 0.2 for i in x], vals, width=0.4, label="после фикса", color="#2a7ade")
        ax.legend(fontsize=8)
    else:
        bars = ax.bar(x, vals, width=0.6, color="#2a7ade")
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.0f}%",
                    ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylim(0, 105)
    ax.set_ylabel("judge pass-rate, %")
    ax.set_title(title, fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def chart_escalation_3pt(cats, before, mid, after, out_png):
    """Сгруппированная диаграмма escalation-сигнала: baseline / +промпт / +guard."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [f"{c}\n(n={after[c]['n']})" for c in cats]
    x = range(len(cats))
    series = [
        ("baseline", [before[c]["rate"] * 100 for c in cats], "#bbbbbb"),
        ("+промпт", [mid[c]["rate"] * 100 for c in cats], "#f0a93b"),
        ("+guard", [after[c]["rate"] * 100 for c in cats], "#2a7ade"),
    ]
    fig, ax = plt.subplots(figsize=(6, 4))
    w = 0.26
    for i, (name, vals, color) in enumerate(series):
        bars = ax.bar([j + (i - 1) * w for j in x], vals, width=w, label=name, color=color)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.0f}", ha="center",
                    va="bottom", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 105)
    ax.set_ylabel("escalation pass-rate, %")
    ax.set_title("Эскалация (детерминир. сигнал): baseline → +промпт → +guard", fontsize=11)
    ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def md_table(title, data, axis_name, before=None, esc=None):
    keys = _order(axis_name, list(data.keys()))
    lines = [f"\n### {title}\n"]
    if before is not None:
        lines.append("| Значение | N | judge (до) | judge (после) |")
        lines.append("|---|---|---|---|")
        for k in keys:
            b = before.get(k, {}).get("rate")
            bs = f"{b:.0%}" if b is not None else "—"
            lines.append(f"| {k} | {data[k]['n']} | {bs} | {data[k]['rate']:.0%} |")
    elif esc is not None:
        lines.append("| Значение | N | judge | escalation |")
        lines.append("|---|---|---|---|")
        for k in keys:
            e = esc.get(k)
            es = f"{e['rate']:.0%} (n={e['n']})" if e else "—"
            lines.append(f"| {k} | {data[k]['n']} | {data[k]['rate']:.0%} | {es} |")
    else:
        lines.append("| Значение | N | judge pass |")
        lines.append("|---|---|---|")
        for k in keys:
            lines.append(f"| {k} | {data[k]['n']} | {data[k]['rate']:.0%} |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="eval_report.json")
    ap.add_argument("--before", default=None, help="отчёт до фикса для колонки сравнения")
    ap.add_argument("--mid", default=None, help="промежуточный отчёт (только промпт-фикс)")
    ap.add_argument("--charts-dir", default="charts")
    args = ap.parse_args()

    meta = load_qa()
    rows, axes, axes_esc = build(args.report, meta)
    clean_ids = {r["id"] for r in rows}
    total_in_report = len(json.loads(Path(args.report).read_text(encoding="utf-8")))
    excluded = total_in_report - len(rows)
    before_axes = before_esc = None
    if args.before and Path(args.before).exists():
        # сравнение на ТОМ ЖЕ чистом наборе id, что и «после» — честный apples-to-apples
        _, before_axes, before_esc = build(args.before, meta, only_ids=clean_ids)

    Path(args.charts_dir).mkdir(exist_ok=True)
    n = len(rows)
    overall = sum(1 for r in rows if r.get("judge_pass")) / n if n else 0

    note = ""
    if excluded:
        note = (f" · исключено {excluded} кейсов с ошибкой прогона (протухший токен, инфра — "
                f"не агент); сравнение до/после построено на общих {n} чистых кейсах")
    md = ["# Метрики агента по осям датасета\n",
          f"Чистых кейсов: **{n}** из {total_in_report}{note}.\n",
          f"Общий judge pass-rate (на чистых): **{overall:.0%}**\n"]
    md += md_table("По категориям (× escalation)", axes["category"], "category",
                   esc=axes_esc["category"])
    md += md_table("По сложности (difficulty)", axes["difficulty"], "difficulty",
                   esc=axes_esc["difficulty"])
    md += md_table("Одно- vs многоходовые (is_multiturn)", axes["multiturn"], "multiturn",
                   esc=axes_esc["multiturn"])
    md += md_table("По нормативному документу (referenced_documents)", axes["document"], "document")

    # графики
    chart("category", axes["category"], "judge pass-rate по категориям",
          f"{args.charts_dir}/by_category.png")
    chart("difficulty", axes["difficulty"], "judge pass-rate по сложности",
          f"{args.charts_dir}/by_difficulty.png")
    chart("multiturn", axes["multiturn"], "judge pass-rate: одно- vs многоходовые",
          f"{args.charts_dir}/by_multiturn.png")
    chart("document", axes["document"], "judge pass-rate по документу",
          f"{args.charts_dir}/by_document.png")
    if before_axes:
        chart("category", axes["category"], "judge pass-rate по категориям: до/после фикса",
              f"{args.charts_dir}/by_category_beforeafter.png", before=before_axes["category"])
        md += md_table("Сравнение до/после фикса — judge (по категориям)", axes["category"],
                       "category", before=before_axes["category"])

        # ГЛАВНОЕ для истории фикса: ДЕТЕРМИНИРОВАННЫЙ escalation-сигнал до/после.
        esc_cats = ["escalation_sales", "escalation_negative"]
        esc_after = {c: axes_esc["category"].get(c, {"rate": 0, "n": 0}) for c in esc_cats}
        esc_before = {c: before_esc["category"].get(c, {"rate": 0, "n": 0}) for c in esc_cats}
        chart("category", esc_after, "escalation (детерминир.) до/после фикса",
              f"{args.charts_dir}/escalation_beforeafter.png", before=esc_before)
        esc_mid = None
        if args.mid and Path(args.mid).exists():
            _, _, mid_esc = build(args.mid, meta, only_ids=clean_ids)
            esc_mid = {c: mid_esc["category"].get(c, {"rate": 0, "n": 0}) for c in esc_cats}
            chart_escalation_3pt(esc_cats, esc_before, esc_mid, esc_after,
                                 f"{args.charts_dir}/escalation_beforeafter.png")

        md.append("\n### Сравнение по фиксам — ESCALATION (детерминированный сигнал)\n")
        md.append("Главный эффект: эскалация = реальный вызов инструмента, а не печать текстом. "
                  "Guard **только добавляет** восстановленные эскалации (никогда не убирает), "
                  "поэтому его вклад (mid→guard) монотонно неотрицателен; колебания baseline→mid — "
                  "это стохастичность tool-calling GigaChat между прогонами.\n")
        if esc_mid:
            md.append("| Категория | N | baseline | +промпт | +guard |")
            md.append("|---|---|---|---|---|")
            for c in esc_cats:
                md.append(f"| {c} | {esc_after[c]['n']} | {esc_before[c]['rate']:.0%} | "
                          f"{esc_mid[c]['rate']:.0%} | {esc_after[c]['rate']:.0%} |")
        else:
            md.append("| Категория | N | escalation (до) | escalation (после) |")
            md.append("|---|---|---|---|")
            for c in esc_cats:
                md.append(f"| {c} | {esc_after[c]['n']} | {esc_before[c]['rate']:.0%} | "
                          f"{esc_after[c]['rate']:.0%} |")

    Path("metrics_by_axis.md").write_text("\n".join(md), encoding="utf-8")
    Path("metrics_axes.json").write_text(
        json.dumps({"overall": overall, "n": n, "axes": axes, "escalation": axes_esc},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Готово: metrics_by_axis.md, metrics_axes.json, {args.charts_dir}/*.png")


if __name__ == "__main__":
    main()
