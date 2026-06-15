"""Конфигурация: пути, загрузка токена GigaChat, фабрики LLM и эмбеддингов."""
from __future__ import annotations

import os
from pathlib import Path

# --- Пути ---------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
CLIENTS_DB = DATA_DIR / "clients" / "clients.sqlite"
QA_FILE = DATA_DIR / "qa" / "qa.jsonl"
INDEX_DIR = PROJECT_ROOT / ".index"
ENV_FILE = PROJECT_ROOT / "env"

# --- Модель -------------------------------------------------------------
GIGACHAT_MODEL = "GigaChat-2"
LLM_TEMPERATURE = 0.1

# "Сегодня" в учебном кейсе фиксировано для воспроизводимости расчётов.
TODAY = "2026-06-15"


def get_access_token() -> str:
    """Берёт GIGACHAT_ACCESS_TOKEN из окружения или из файла ./env."""
    token = os.environ.get("GIGACHAT_ACCESS_TOKEN")
    if token:
        return token.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GIGACHAT_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(
        "GIGACHAT_ACCESS_TOKEN не найден ни в окружении, ни в файле ./env"
    )


def make_llm(temperature: float = LLM_TEMPERATURE):
    """Создаёт GigaChat LLM (как в примере задания)."""
    from langchain_gigachat import GigaChat

    return GigaChat(
        access_token=get_access_token(),
        verify_ssl_certs=False,
        model=GIGACHAT_MODEL,
        temperature=temperature,
    )


def make_embeddings():
    """Создаёт GigaChatEmbeddings для RAG."""
    from langchain_gigachat import GigaChatEmbeddings

    return GigaChatEmbeddings(
        access_token=get_access_token(),
        verify_ssl_certs=False,
    )
