"""Сборка агента на LangGraph (create_react_agent): GigaChat + RAG-тул + клиентские тулы."""
from __future__ import annotations

from typing import List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .config import make_llm
from .prompts import SYSTEM_PROMPT, build_session_context
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
    ):
        from langgraph.prebuilt import create_react_agent

        self.client_id = client_id
        self.channel = channel
        self.escalations: list = []

        llm = llm or make_llm()
        search_tool = search_tool or make_search_tool()
        tools = [search_tool] + make_tools(client_id, escalation_sink=self.escalations)

        system = SYSTEM_PROMPT + "\n\n" + build_session_context(client_id, channel)
        self._agent = create_react_agent(llm, tools, prompt=system)

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

    def run(self, question: str, history: Optional[List[dict]] = None) -> dict:
        """Прогоняет один ход. Возвращает {'answer', 'escalations', 'messages'}."""
        self.escalations.clear()
        messages = self._history_to_messages(history) + [HumanMessage(content=question)]
        result = self._agent.invoke(
            {"messages": messages}, config={"recursion_limit": 16}
        )
        out = result["messages"]
        answer = ""
        for m in reversed(out):
            if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                answer = m.content
                break
        return {
            "answer": answer,
            "escalations": list(self.escalations),
            "messages": out,
        }
