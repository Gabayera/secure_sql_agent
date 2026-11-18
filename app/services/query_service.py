from __future__ import annotations

from app.agent import SQLAgent
from app.models import AgentResponse


class QueryService:
    def __init__(self, agent: SQLAgent) -> None:
        self.agent = agent

    def execute(self, request: str, user: str | None = None) -> AgentResponse:
        return self.agent.process_request(request, user=user)
