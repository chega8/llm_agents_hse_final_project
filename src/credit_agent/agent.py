"""Сборка агента на LangGraph (create_react_agent): GigaChat + RAG-тул + клиентские тулы."""
from __future__ import annotations

import re
from typing import List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .config import make_llm
from .memory import make_memory_tools
from .prompts import REFLECTION_PROMPT, SYSTEM_PROMPT, build_session_context
from .rag import make_search_tool
from .tools import make_tools


class CreditAgent:
    """Обёртка над LangGraph-агентом для одной сессии (один клиент/канал).

    Создаёт ReAct-агента с системным промптом, RAG-инструментом и клиентскими
    инструментами, замкнутыми на client_id сессии.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        channel: Optional[str] = None,
        search_tool=None,
        llm=None,
        reflect: bool = True,
    ):
        from langgraph.prebuilt import create_react_agent

        self.client_id = client_id
        self.channel = channel
        self.reflect = reflect
        self.escalations: list = []

        self._llm = llm or make_llm()
        search_tool = search_tool or make_search_tool()
        tools = (
            [search_tool]
            + make_tools(client_id, escalation_sink=self.escalations)
            + make_memory_tools(client_id)
        )

        system = SYSTEM_PROMPT + "\n\n" + build_session_context(client_id, channel)
        self._agent = create_react_agent(self._llm, tools, prompt=system)

    @staticmethod
    def _history_to_messages(history: List[dict]) -> list:
        """qa.history -> сообщения LangChain. Ожидается список {role, content/text}."""
        msgs = []
        for turn in history or []:
            role = turn.get("role") or turn.get("speaker") or "user"
            content = turn.get("content") or turn.get("text") or turn.get("message") or ""
            if role in ("user", "client", "human"):
                msgs.append(HumanMessage(content=content))
            else:
                msgs.append(AIMessage(content=content))
        return msgs

    # Граф упирается в этот лимит на длинных цепочках tool-call'ов; обрезанный прогон
    # возвращает ответ-заглушку (см. _STUB_MARKERS).
    RECURSION_LIMIT = 24
    _STUB_MARKERS = ("need more steps", "need more step")

    # GigaChat-2 иногда печатает вызов инструмента ТЕКСТОМ в ответе вместо структурного
    # tool-call — тогда реального действия (эскалации/записи в память) не происходит. Ловим это.
    _TEXT_ESC_RE = re.compile(
        r"(?:escalate|эскал\w*)\s*\([^)]*trigger\s*=\s*['\"]?(sales|negative|security)"
        r"|trigger\s*=\s*['\"](sales|negative|security)['\"]",
        re.I | re.S,
    )
    _TEXT_TOOLCALL_RE = re.compile(r"^\s*(?:escalate|эскал\w*|remember|recall)\s*\(", re.I)

    @classmethod
    def _is_stub(cls, answer: str) -> bool:
        """True, если ход фактически не завершился: пустой ответ, обрезка по лимиту или
        «сырой» текстовый вызов инструмента вместо реального ответа клиенту."""
        a = (answer or "").strip()
        if not a:
            return True
        if any(m in a.lower() for m in cls._STUB_MARKERS):
            return True
        return bool(cls._TEXT_TOOLCALL_RE.match(a))

    def _recover_text_toolcall(self, answer: str) -> tuple:
        """Чинит самый вредный случай текстового вызова — пропущенную эскалацию.

        Если модель напечатала escalate(...)/trigger="..." текстом, детерминированно
        фиксируем эскалацию (если её ещё нет) и заменяем ответ на корректный hand-off,
        чтобы клиент не видел «сырой» вызов инструмента. Возвращает (answer, recovered?).
        """
        m = self._TEXT_ESC_RE.search(answer or "")
        if not m:
            return answer, False
        trigger = (m.group(1) or m.group(2) or "").lower()
        if trigger and not any(e.get("trigger") == trigger for e in self.escalations):
            self.escalations.append(
                {"trigger": trigger, "summary": "восстановлено из текстового вызова инструмента"}
            )
        return (
            "Понимаю вашу ситуацию. Передаю обращение специалисту — он свяжется с вами "
            "в ближайшее время.",
            True,
        )

    def _invoke_once(self, messages: list) -> tuple:
        """Один прогон ReAct-графа. Возвращает (answer, messages)."""
        result = self._agent.invoke(
            {"messages": messages}, config={"recursion_limit": self.RECURSION_LIMIT}
        )
        out = result["messages"]
        answer = ""
        for m in reversed(out):
            if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                answer = m.content
                break
        return answer, out

    def _reflect(self, question: str, answer: str) -> dict:
        """Самооценка финального ответа всей цепочки относительно цели и ограничений.

        Соответствует уровню Reflection=2 рубрики. Fail-open: любая ошибка самопроверки
        не должна ломать базовый ответ, поэтому при сбое считаем ответ корректным.
        """
        import json
        import re

        try:
            resp = self._llm.invoke(
                REFLECTION_PROMPT.format(question=question, answer=answer)
            ).content
            m = re.search(r"\{.*\}", resp, re.DOTALL)
            if not m:
                return {"ok": True, "issues": ""}
            data = json.loads(m.group(0))
            return {"ok": bool(data.get("ok", True)), "issues": str(data.get("issues", ""))[:300]}
        except Exception:
            return {"ok": True, "issues": ""}

    def run(self, question: str, history: Optional[List[dict]] = None) -> dict:
        """Прогоняет один ход. Возвращает {'answer', 'escalations', 'messages', 'reflection'}.

        При reflect=True агент выполняет самооценку результата всей цепочки (Reflection=2).
        Самокоррекция БЕЗОПАСНА: переигровка делается только при ЯВНОМ провале хода
        (пустой/обрезанный по лимиту ответ) и из ЧИСТОГО контекста (не поверх накопленной
        цепочки — иначе раздувается длина и снова упирается в recursion_limit). Результат
        переигровки принимается, лишь если он не заглушка, — поэтому рефлексия гарантированно
        не ухудшает ответ относительно прогона без неё.
        """
        self.escalations.clear()
        base = self._history_to_messages(history) + [HumanMessage(content=question)]
        answer, out = self._invoke_once(base)
        answer, _ = self._recover_text_toolcall(answer)  # эскалация, напечатанная текстом

        reflection = None
        if self.reflect:
            reflection = self._reflect(question, answer)
            if self._is_stub(answer):
                saved_esc = list(self.escalations)
                self.escalations.clear()
                retry_answer, retry_out = self._invoke_once(base)
                retry_answer, _ = self._recover_text_toolcall(retry_answer)
                if not self._is_stub(retry_answer):
                    answer, out = retry_answer, retry_out
                    reflection = self._reflect(question, answer)
                else:  # переигровка не помогла — откатываемся к исходному прогону
                    self.escalations.clear()
                    self.escalations.extend(saved_esc)

        return {
            "answer": answer,
            "escalations": list(self.escalations),
            "messages": out,
            "reflection": reflection,
        }
