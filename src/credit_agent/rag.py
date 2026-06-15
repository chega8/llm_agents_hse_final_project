"""RAG по нормативным документам: чанкинг по заголовкам, GigaChat embeddings, FAISS."""
from __future__ import annotations

import re
from typing import List

from langchain_core.documents import Document
from langchain_core.tools import tool

from .config import DOCUMENTS_DIR, INDEX_DIR, make_embeddings

# Заголовки markdown с номером пункта: "### 2.1.2. Условия" -> номер "2.1.2"
_HEADER_RE = re.compile(r"^(#{1,4})\s+(\d+(?:\.\d+)*)\.?\s+(.*)$")
_PLAIN_HEADER_RE = re.compile(r"^(#{1,4})\s+(.*)$")


def _load_chunks() -> List[Document]:
    """Режет каждый документ на чанки по заголовкам уровней #/##/###/####.

    В каждый чанк включается ПУТЬ родительских заголовков (напр.
    «Линейка продуктов > Кредит "Бизнес-Оборот" > Условия»), чтобы дочерние
    секции вроде «Условия» несли контекст продукта — это критично для качества
    семантического поиска. Метаданные: source (файл), section (номер пункта).
    """
    chunks: List[Document] = []
    for path in sorted(DOCUMENTS_DIR.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        # Стек заголовков: (уровень, номер, заголовок) от верхнего к текущему.
        stack: List[tuple] = []
        cur_section = ""
        buf: List[str] = []

        def flush():
            text = "\n".join(buf).strip()
            if not text or not stack:
                return
            path_titles = " > ".join(h[2] for h in stack)
            anchor = cur_section or path.stem
            header = f"[{path.name}#{anchor}] {path_titles}"
            chunks.append(
                Document(
                    page_content=f"{header}\n{text}",
                    metadata={
                        "source": path.name,
                        "section": cur_section,
                        "title": path_titles,
                    },
                )
            )

        for line in lines:
            num_match = _HEADER_RE.match(line)
            plain_match = _PLAIN_HEADER_RE.match(line)
            if num_match or plain_match:
                flush()
                buf = []
                m = num_match or plain_match
                level = len(m.group(1))
                if num_match:
                    cur_section = num_match.group(2)
                    title = num_match.group(3).strip()
                else:
                    cur_section = ""
                    title = plain_match.group(2).strip()
                # Срезаем из стека всё на этом уровне и глубже, затем добавляем текущий.
                stack = [h for h in stack if h[0] < level]
                stack.append((level, cur_section, title))
            else:
                buf.append(line)
        flush()

    chunks.extend(_product_overview_chunk())
    return chunks


# Заголовки продуктов: "### 2.1. Кредит «Бизнес-Оборот» (оборотный кредит)"
_PRODUCT_RE = re.compile(r"^###\s+(\d+\.\d+)\.\s+(.*«[^»]+».*)$")


def _product_overview_chunk() -> List[Document]:
    """Синтетический обзорный чанк со списком всех продуктов линейки — чтобы на запрос
    «какие кредиты предлагаете» одним фрагментом возвращался полный перечень."""
    path = DOCUMENTS_DIR / "01_credit_products.md"
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PRODUCT_RE.match(line)
        if m:
            items.append(f"- (п. {m.group(1)}) {m.group(2).strip()}")
    if not items:
        return []
    body = "Полная линейка кредитных продуктов для малого и микробизнеса (МСБ):\n" + "\n".join(items)
    header = "[01_credit_products.md#2] Линейка кредитных продуктов — перечень всех продуктов"
    return [
        Document(
            page_content=f"{header}\n{body}",
            metadata={"source": "01_credit_products.md", "section": "2", "title": "Линейка продуктов"},
        )
    ]


def build_index() -> None:
    """Строит FAISS-индекс из документов и сохраняет в .index/."""
    from langchain_community.vectorstores import FAISS

    docs = _load_chunks()
    if not docs:
        raise RuntimeError(f"Не найдено документов в {DOCUMENTS_DIR}")
    store = FAISS.from_documents(docs, make_embeddings())
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    store.save_local(str(INDEX_DIR))
    print(f"Индекс построен: {len(docs)} чанков -> {INDEX_DIR}")


def load_retriever(k: int = 6):
    """Загружает сохранённый FAISS-ретривер."""
    from langchain_community.vectorstores import FAISS

    if not (INDEX_DIR / "index.faiss").exists():
        raise RuntimeError(
            f"Индекс не найден в {INDEX_DIR}. Сначала запусти: python build_index.py"
        )
    store = FAISS.load_local(
        str(INDEX_DIR), make_embeddings(), allow_dangerous_deserialization=True
    )
    return store.as_retriever(search_kwargs={"k": k})


def make_search_tool(k: int = 6):
    """Создаёт инструмент search_regulations поверх ретривера."""
    retriever = load_retriever(k=k)

    @tool
    def search_regulations(query: str) -> str:
        """Поиск по нормативным документам Банка о кредитовании МСБ: кредитные продукты и их
        условия, процесс подачи заявки и статусы, досрочное погашение, реструктуризация,
        регламент обращений. Возвращает релевантные фрагменты с указанием документа и пункта.
        Используй для любых фактов об условиях, ставках, требованиях и процедурах."""
        # GigaChat embeddings требуют event loop; в треде агента его может не быть.
        import asyncio

        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        docs = retriever.invoke(query)
        if not docs:
            return "По запросу ничего не найдено в нормативных документах."
        parts = []
        for d in docs:
            src = d.metadata.get("source", "?")
            sec = d.metadata.get("section", "")
            ref = f"{src}#{sec}" if sec else src
            parts.append(f"--- [{ref}] ---\n{d.page_content}")
        return "\n\n".join(parts)

    return search_regulations
