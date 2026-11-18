from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Protocol

from app.models import ApprovalDecision, ApprovalRequest


class ApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalToken:
    token: str
    request_id: str
    sql_hash: str
    params_hash: str


class ApprovalProvider(Protocol):
    def request(self, request: ApprovalRequest) -> ApprovalDecision: ...


class CLIApprovalProvider:
    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        print("\n" + "=" * 42)
        print("          WRITE APPROVAL REQUIRED")
        print("=" * 42)
        print(f"\nOperation: {request.operation}\n")
        print("SQL:\n")
        print(request.sql.rstrip(";") + ";\n")
        print("Parameters:\n")
        for key, value in request.parameters.items():
            print(f"  {key} = {value!r}")
        print(f"\nTables: {', '.join(request.tables)}")
        print(f"\nDescription:\n{request.explanation}\n")
        print("1. Approve")
        print("2. Reject")
        print("3. Edit")
        print("=" * 42)

        choice = input("Approve? (yes/no/edit): ").strip().lower()
        if choice in ("yes", "y", "1", "approve"):
            return ApprovalDecision(approved=True)
        if choice in ("edit", "e", "3"):
            edited_sql = input("Edited SQL: ").strip()
            raw_params = input(
                "Edited parameters as JSON (leave blank to keep existing): "
            ).strip()
            params = request.parameters
            if raw_params:
                try:
                    params = json.loads(raw_params)
                except json.JSONDecodeError as exc:
                    print(f"Invalid JSON: {exc}. Rejecting.")
                    return ApprovalDecision(approved=False)
            return ApprovalDecision(
                approved=False,
                edited_sql=edited_sql or None,
                edited_parameters=params,
            )
        return ApprovalDecision(approved=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _params_hash(params: dict) -> str:
    return _hash(json.dumps(params, sort_keys=True, default=str))


class ApprovalManager:
    def __init__(self, provider: ApprovalProvider | None) -> None:
        self.provider = provider
        self._pending: dict[str, ApprovalToken] = {}

    def create_token(self, request: ApprovalRequest) -> ApprovalToken:
        token = secrets.token_urlsafe(32)
        approval_token = ApprovalToken(
            token=token,
            request_id=request.request_id,
            sql_hash=_hash(request.sql),
            params_hash=_params_hash(request.parameters),
        )
        self._pending[token] = approval_token
        return approval_token

    def verify_and_consume(self, token: str, sql: str, parameters: dict) -> None:
        stored = self._pending.pop(token, None)
        if stored is None:
            raise ApprovalError("Approval token is invalid or has already been used")
        if stored.sql_hash != _hash(sql):
            raise ApprovalError("Approval token is not bound to this SQL statement")
        if stored.params_hash != _params_hash(parameters):
            raise ApprovalError("Approval token is not bound to these parameters")
