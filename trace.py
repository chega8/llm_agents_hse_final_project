#!/usr/bin/env python3
"""Сбор ПОЛНЫХ трейсов работы агента для слайдов: вопрос → рассуждение → вызовы
инструментов (tool-calls + результаты, включая RAG) → рефлексия → финальный ответ.

Сохраняет два артефакта:
  * <prefix>.json — структурированный трейс (для дальнейшей обработки);
  * <prefix>.md   — человекочитаемый трейс (готов к вставке в презентацию).

Примеры:
    python trace.py --ids Q-001,Q-050,Q-120 --prefix traces_demo
    python trace.py --sample-per-category 1 --prefix traces_sample
    python trace.py --categories transactional,escalation_sales --sample-per-category 1
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from credit_agent.agent import CreditAgent
from credit_agent.config import QA_FILE
from credit_agent.rag import make_search_tool


def _clip(text, n=1200):
    text = str(text)
    return text if len(text) <= n else text[:n] + f"… [+{len(text) - n} симв.]"


def serialize_messages(messages) -> list:
    """LangChain-сообщения → список простых dict'ов с типом, текстом, tool-calls."""
    steps = []
    for m in messages:
        cls = m.__class__.__name__  # HumanMessage / AIMessage / ToolMessage / SystemMessage
        entry = {"type": cls}
        content = getattr(m, "content", "")
        if isinstance(content, list):  # иногда content — список блоков
            content = " ".join(str(b) for b in content)
        entry["content"] = content or ""
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {"name": tc.get("name"), "args": tc.get("args", {})} for tc in tool_calls
            ]
        if cls == "ToolMessage":
            entry["tool_name"] = getattr(m, "name", None)
        steps.append(entry)
    return steps


def load_cases(ids=None, categories=None, sample_per_category=None):
    rows = [json.loads(l) for l in QA_FILE.open(encoding="utf-8")]
    if ids:
        idset = set(ids)
        return [r for r in rows if r["id"] in idset]
    if categories:
        rows = [r for r in rows if r["category"] in categories]
    if sample_per_category:
        seen = defaultdict(int)
        out = []
        for r in rows:
            if seen[r["category"]] < sample_per_category:
                out.append(r)
                seen[r["category"]] += 1
        return out
    return rows


def trace_case(case, search_tool) -> dict:
    agent = CreditAgent(
        client_id=case.get("client_id"),
        channel=case.get("channel"),
        search_tool=search_tool,
    )
    res = agent.run(case["question"], history=case.get("history"))
    return {
        "id": case["id"],
        "category": case["category"],
        "subcategory": case.get("subcategory"),
        "channel": case.get("channel"),
        "client_id": case.get("client_id"),
        "question": case["question"],
        "history": case.get("history", []),
        "expected_behavior": case.get("expected_behavior"),
        "expected_outcome_type": case.get("expected_outcome_type"),
        "final_answer": res["answer"],
        "escalations": res["escalations"],
        "reflection": res.get("reflection"),
        "trace": serialize_messages(res["messages"]),
    }


def to_markdown(traces: list) -> str:
    out = ["# Полные трейсы работы агента\n"]
    for t in traces:
        out.append(f"## {t['id']} · {t['category']}"
                   + (f" / {t['subcategory']}" if t.get("subcategory") else ""))
        meta = f"**Канал:** {t['channel']} · **client_id:** {t['client_id'] or '—'}"
        out.append(meta + "\n")
        for h in t.get("history") or []:
            role = h.get("role", "user")
            out.append(f"> _(история, {role})_ {h.get('content') or h.get('text','')}")
        out.append(f"**Вопрос клиента:** {t['question']}\n")
        out.append("**Трейс:**\n")
        step_n = 0
        for s in t["trace"]:
            typ = s["type"]
            if typ == "HumanMessage":
                continue  # уже показан как вопрос/история
            if typ == "AIMessage":
                if s.get("tool_calls"):
                    for tc in s["tool_calls"]:
                        step_n += 1
                        args = json.dumps(tc["args"], ensure_ascii=False)
                        out.append(f"{step_n}. 🔧 **вызов** `{tc['name']}({_clip(args, 300)})`")
                if s.get("content", "").strip():
                    out.append(f"   💬 _ассистент рассуждает/отвечает:_ {_clip(s['content'], 600)}")
            elif typ == "ToolMessage":
                out.append(f"   ↳ ⚙️ **результат** `{s.get('tool_name','tool')}` → {_clip(s['content'], 700)}")
        if t.get("reflection"):
            r = t["reflection"]
            verdict = "OK" if r.get("ok") else f"НЕДОЧЁТЫ: {r.get('issues')}"
            out.append(f"\n🪞 **Самопроверка (рефлексия):** {verdict}")
        if t.get("escalations"):
            out.append(f"\n**Эскалации:** {json.dumps(t['escalations'], ensure_ascii=False)}")
        out.append(f"\n✅ **Финальный ответ:**\n\n{t['final_answer']}\n")
        out.append("\n---\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=None, help="через запятую, напр. Q-001,Q-050")
    ap.add_argument("--categories", default=None, help="через запятую")
    ap.add_argument("--sample-per-category", type=int, default=None)
    ap.add_argument("--prefix", default="traces")
    args = ap.parse_args()

    ids = args.ids.split(",") if args.ids else None
    cats = args.categories.split(",") if args.categories else None
    cases = load_cases(ids, cats, args.sample_per_category)
    if not cases:
        print("Нет кейсов под заданный фильтр.")
        return
    print(f"Трейсов к сбору: {len(cases)}")

    search_tool = make_search_tool()
    traces = []
    for i, c in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {c['id']} {c['category']} …", flush=True)
        try:
            traces.append(trace_case(c, search_tool))
        except Exception as e:
            print(f"   ОШИБКА: {e}")
            traces.append({"id": c["id"], "category": c["category"],
                           "question": c["question"], "error": str(e), "trace": []})
        # инкрементальное сохранение — устойчиво к протуханию токена на длинном прогоне
        Path(f"{args.prefix}.json").write_text(
            json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8")

    Path(f"{args.prefix}.md").write_text(to_markdown(traces), encoding="utf-8")
    print(f"\nГотово: {args.prefix}.json (структура), {args.prefix}.md (для слайдов)")


if __name__ == "__main__":
    main()
