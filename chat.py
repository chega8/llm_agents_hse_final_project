#!/usr/bin/env python3
"""Интерактивный CLI-чат с агентом поддержки кредитования МСБ.

Примеры:
    python chat.py                              # анонимная сессия (chat_site)
    python chat.py --client C-000001 --channel chat_intern   # авторизованная
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from credit_agent.agent import CreditAgent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=None, help="client_id (напр. C-000001)")
    ap.add_argument("--channel", default="chat_site",
                    help="chat_site|chat_intern|mobile|contact_center")
    args = ap.parse_args()

    print("Инициализация агента...")
    agent = CreditAgent(client_id=args.client, channel=args.channel)
    history = []
    print(f"Сессия: канал={args.channel}, клиент={args.client or 'аноним'}")
    print("Введите сообщение (пустая строка или 'exit' — выход).\n")

    while True:
        try:
            q = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        res = agent.run(q, history=history)
        print(f"\nПомощник: {res['answer']}")
        if res["escalations"]:
            print(f"  [эскалация: {[e['trigger'] for e in res['escalations']]}]")
        print()
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": res["answer"]})


if __name__ == "__main__":
    main()
