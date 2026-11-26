from __future__ import annotations
 
from dataclasses import dataclass
from typing import Any
 
_DIALECT_NAMES = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
}
 
 
@dataclass(frozen=True)
class PromptOptions:
    dialect: str = "postgres"
    default_schema: str = "public"
    max_result_rows: int = 1000
    enable_ddl: bool = False
 
 
SYSTEM_TEMPLATE = """You are an expert {dialect_name} SQL writer inside a secured application.
Translate the user's request into exactly ONE {dialect_name} SQL statement.
 
Rules:
1. Exactly one statement. No comments, no markdown, no text outside the JSON.
2. Use ONLY tables and columns listed in the schema. Never invent names.
   Use the foreign keys shown to build JOINs.
3. When more than one table is used, give every table a short alias and
   prefix every column with it (c.full_name, o.created_at).
4. Every literal value coming from the user (text, numbers, dates, ids) must be
   a named bind parameter like :p_city and its value goes into "parameters".
   - Parameter names: lowercase, start with "p_" (e.g. :p_id, :p_min_total).
   - Never use ?, $1 or %s. Never write :p_x::type, write CAST(:p_x AS type).
   - For LIKE, put the wildcards in the value: {{"p_name": "%smith%"}}.
5. Reads: list explicit columns (no SELECT *) and add LIMIT {limit}
   unless the user asked for an aggregate, a single row, or a smaller number.
   Use NOW() / CURRENT_DATE and INTERVAL for relative dates.
6. Writes (INSERT / UPDATE / DELETE) are allowed but a human must approve them.
   UPDATE and DELETE must have a WHERE clause on real columns that targets
   only the intended rows. Never write to more rows than the user asked for.
7. {ddl_rule}
8. Never touch system catalogs (pg_catalog, information_schema) and never
   call administrative or file functions (pg_sleep, pg_read_file, set_config, dblink ...).
9. If the request asks for information the schema does not store, do NOT
   substitute another column or invent a calculation. Example: there is no
   birth date, so "customer age" cannot be answered; account age from
   created_at is NOT the same thing. Return "query": "" and say in
   "explanation" which information is missing. Do the same if the request
   is not a database request.
 
Respond with ONLY this JSON object:
{{"query": "<one SQL statement>", "parameters": {{"p_name": value}}, "explanation": "<one short sentence>"}}
 
Example (illustrative schema, NOT the user's):
request: "customers from Berlin who joined this year"
{{"query": "SELECT c.id, c.full_name, c.email FROM customers c JOIN addresses a ON a.customer_id = c.id WHERE a.city = :p_city AND c.created_at >= date_trunc('year', CURRENT_DATE) LIMIT {limit}", "parameters": {{"p_city": "Berlin"}}, "explanation": "Customers with a Berlin address created this year."}}
"""
 
 
def build_system_prompt(options: PromptOptions) -> str:
    dialect_name = _DIALECT_NAMES.get(options.dialect.lower(), options.dialect)
    ddl_rule = (
        "DDL (CREATE / ALTER / DROP / TRUNCATE) is allowed only when explicitly requested."
        if options.enable_ddl
        else "DDL (CREATE / ALTER / DROP / TRUNCATE) is DISABLED: never generate it; "
        'return "query": "" instead.'
    )
    return SYSTEM_TEMPLATE.format(
        dialect_name=dialect_name,
        limit=min(options.max_result_rows, 100),
        ddl_rule=ddl_rule,
    )
 
 
def _is_metadata(schema: dict) -> bool:
    for tables in schema.values():
        if not isinstance(tables, dict):
            return False
        for info in tables.values():
            return isinstance(info, dict) and isinstance(info.get("columns"), dict)
    return False
 
 
def render_schema(schema: dict[str, Any]) -> str:
    """Compact, LLM-friendly schema text. Accepts either the simple
    {schema: {table: {col: type}}} mapping or the richer metadata mapping
    (with primary/foreign keys) produced by SchemaIntrospector."""
    lines: list[str] = []
    rich = _is_metadata(schema)
    for db in sorted(schema):
        for table in sorted(schema[db]):
            info = schema[db][table]
            if rich:
                columns = info.get("columns", {})
                pk = set(info.get("primary_key") or [])
                kind = info.get("kind", "table")
            else:
                columns, pk, kind = info, set(), "table"
            cols = ", ".join(
                f"{name} {ctype}{' PK' if name in pk else ''}".strip()
                for name, ctype in columns.items()
            )
            label = "VIEW" if kind == "view" else "TABLE"
            lines.append(f"{label} {db}.{table} ({cols})")
            if rich:
                for fk in info.get("foreign_keys") or []:
                    src = ", ".join(fk.get("columns") or [])
                    dst = ", ".join(fk.get("referred_columns") or [])
                    ref = f"{fk.get('referred_schema') or db}.{fk.get('referred_table')}"
                    lines.append(f"  FK ({src}) -> {ref}({dst})")
    return "\n".join(lines) if lines else "(no tables visible)"
 
 
def build_user_prompt(
    request: str,
    schema: dict[str, Any],
    options: PromptOptions,
    error_context: str | None = None,
) -> str:
    parts = [
        f"Default schema: {options.default_schema}",
        "Schema:",
        render_schema(schema),
        "",
        f"User request: {request.strip()}",
    ]
    if error_context:
        parts += [
            "",
            "Your previous attempt was rejected by the validator:",
            error_context.strip(),
            "Return a corrected statement that fixes these problems.",
        ]
    return "\n".join(parts)
