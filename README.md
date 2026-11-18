# Secure SQL AI Agent

A production-oriented Python application that lets users interact with a
PostgreSQL database using natural language while enforcing multiple
independent security layers.

## Architecture

    User
      -> Natural language
      -> LLM SQL generation (parameterized)
      -> SQLGlot AST parsing
      -> Statement classification (read / write / DDL / unsupported)
      -> Static schema validation (tables, columns, aliases, joins, CTEs)
      -> Live EXPLAIN planning validation (read queries only)
      -> Read path: automatic execution on read-only connection
      -> Write path: mandatory human approval, then writer connection
      -> Audit logging

## Installation

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env

## Running

    python -m app.main

## Tests

    pytest

All unit tests use mocks and do not require a live database or LLM API key.

## Security notes

- Read queries execute automatically through the read-only role.
- Writes never execute without explicit human approval.
- Approval tokens are bound to the exact SQL and parameter set and are one-time.
- Edited SQL is re-parsed, reclassified, revalidated and re-approved.
- Multi-statement requests are always rejected.
- SELECT INTO, FOR UPDATE, data-modifying CTEs, EXPLAIN ANALYZE and
  dangerous PostgreSQL functions are rejected.
- UPDATE/DELETE without WHERE are rejected.
- EXPLAIN (without ANALYZE) is used only as a best-effort planning probe.
- Audit logs redact sensitive parameter keys.
- DDL is disabled by default.
