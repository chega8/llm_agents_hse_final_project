"""Локальная долговременная память агента по сессии/клиенту.

Агент САМ решает, что сохранить (озвученное предпочтение, цель, заявленный факт,
договорённость), чтобы вернуться к этому на следующих ходах или в новой сессии.
Память локальная (JSON-файлы в .memory/), ключ — client_id (или 'anon' для анонима).
Соответствует уровню Memory=1 рубрики зрелости агента: агент по собственному
решению пишет/читает данные в свою долговременную память.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from langchain_core.tools import tool

from .config import MEMORY_DIR


def _path(key: str):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return MEMORY_DIR / f"{safe}.json"


def _load(key: str) -> list:
    p = _path(key)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(key: str, notes: list) -> None:
    _path(key).write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def make_memory_tools(session_client_id: Optional[str]) -> List:
    """Инструменты долговременной памяти, замкнутые на сессию (client_id или 'anon')."""
    key = session_client_id or "anon"

    @tool
    def remember(note: str) -> str:
        """Сохранить в долговременную память важный факт о клиенте/сессии, к которому
        стоит вернуться позже: озвученное предпочтение, цель обращения, заявленный факт
        (отрасль, желаемая сумма/срок), достигнутую договорённость. Вызывай САМ, как
        только замечаешь такую информацию, без запроса подтверждения у клиента. НЕ
        сохраняй пароли, коды и платёжные реквизиты."""
        notes = _load(key)
        notes.append(
            {"ts": datetime.now().isoformat(timespec="seconds"), "note": note.strip()}
        )
        _save(key, notes)
        return f"Запомнил (в памяти сессии сейчас {len(notes)} заметок)."

    @tool
    def recall(query: str = "") -> str:
        """Вернуть ранее сохранённые в долговременной памяти заметки о клиенте/сессии.
        query — необязательная подстрока для фильтра. Используй в начале диалога и когда
        нужен контекст из прошлых ходов или прошлых сессий этого клиента."""
        notes = _load(key)
        if query:
            ql = query.lower()
            notes = [n for n in notes if ql in n.get("note", "").lower()]
        if not notes:
            return "В памяти сессии пока нет заметок."
        return "\n".join(
            f"- ({n.get('ts', '')}) {n.get('note', '')}" for n in notes
        )

    return [remember, recall]
