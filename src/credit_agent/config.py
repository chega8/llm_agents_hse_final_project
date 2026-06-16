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
MEMORY_DIR = PROJECT_ROOT / ".memory"
ENV_FILE = PROJECT_ROOT / "env"

# --- Модель -------------------------------------------------------------
GIGACHAT_MODEL = "GigaChat-2"
LLM_TEMPERATURE = 0.1

# "Сегодня" в учебном кейсе фиксировано для воспроизводимости расчётов.
TODAY = "2026-06-15"


def _read_env_var(name: str) -> str | None:
    """Значение переменной из окружения или из файла ./env."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return None


def get_credentials() -> str | None:
    """Авторизационный ключ GIGACHAT_CREDENTIALS (auth key) — даёт авто-обновление токена."""
    return _read_env_var("GIGACHAT_CREDENTIALS")


def get_access_token() -> str:
    """Берёт GIGACHAT_ACCESS_TOKEN из окружения или из файла ./env."""
    token = _read_env_var("GIGACHAT_ACCESS_TOKEN")
    if token:
        return token
    raise RuntimeError(
        "GIGACHAT_ACCESS_TOKEN не найден ни в окружении, ни в файле ./env"
    )


def _auth_kwargs() -> dict:
    """Аргументы авторизации GigaChat.

    Предпочитаем GIGACHAT_CREDENTIALS (auth key) — клиент сам обновляет короткоживущий
    access-токен, поэтому длинные прогоны (eval/traces) не падают по «Token has expired».
    Если ключа нет — откатываемся на статический GIGACHAT_ACCESS_TOKEN.
    """
    creds = get_credentials()
    if creds:
        scope = _read_env_var("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS"
        return {"credentials": creds, "scope": scope}
    return {"access_token": get_access_token()}


def make_llm(temperature: float = LLM_TEMPERATURE):
    """Создаёт GigaChat LLM (как в примере задания)."""
    from langchain_gigachat import GigaChat

    return GigaChat(
        verify_ssl_certs=False,
        model=GIGACHAT_MODEL,
        temperature=temperature,
        **_auth_kwargs(),
    )


def make_embeddings():
    """Создаёт GigaChatEmbeddings для RAG."""
    from langchain_gigachat import GigaChatEmbeddings

    return GigaChatEmbeddings(
        verify_ssl_certs=False,
        **_auth_kwargs(),
    )
