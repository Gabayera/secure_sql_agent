#!/usr/bin/env bash
# Fix 1: stop the model from substituting a different meaning (prompt rule 9).
# Fix 2: SQL display no longer breaks lines inside brackets, e.g. EXTRACT(YEAR FROM ...).
# Run from the project root.
set -euo pipefail
[ -f app/llm/prompts.py ] && [ -f app/web/static/index.html ] || {
  echo "Run this from the secure_sql_agent project root (v0.3 must be applied)"; exit 1; }

python3 - <<'PY'
from pathlib import Path

# ---------------------------------------------------------- prompts.py
p = Path("app/llm/prompts.py")
s = p.read_text(encoding="utf-8")
old = '''9. If the request cannot be answered with this schema, or is not a database
   request, return "query": "" and explain why in "explanation".'''
new = '''9. If the request asks for information the schema does not store, do NOT
   substitute another column or invent a calculation. Example: there is no
   birth date, so "customer age" cannot be answered; account age from
   created_at is NOT the same thing. Return "query": "" and say in
   "explanation" which information is missing. Do the same if the request
   is not a database request.'''
if new in s:
    print("prompts.py: already updated")
elif old in s:
    p.write_text(s.replace(old, new), encoding="utf-8")
    print("prompts.py: rule 9 updated")
else:
    raise SystemExit("prompts.py: rule 9 not found - file differs from v0.3, not changed")

# ---------------------------------------------------------- index.html
p = Path("app/web/static/index.html")
s = p.read_text(encoding="utf-8")
start = s.find("function prettySql(sql) {")
end = s.find("\n/* ---------------------------------------------------------- data table */")
if start == -1 or end == -1 or end < start:
    raise SystemExit("index.html: prettySql not found - file differs from v0.3, not changed")
new_fn = r'''function prettySql(sql) {
  // New line before main clauses, but only outside brackets and strings,
  // so EXTRACT(YEAR FROM ...) and sub-queries stay intact.
  sql = sql.trim();
  if (sql.includes("\n")) return sql;
  const kw = /\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|LEFT JOIN|RIGHT JOIN|INNER JOIN|JOIN|UNION ALL|UNION|SET|VALUES|RETURNING|ON CONFLICT)\b/iy;
  let out = "", depth = 0, quote = false;
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i];
    if (quote) { out += ch; if (ch === "'") quote = false; continue; }
    if (ch === "'") { quote = true; out += ch; continue; }
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (depth === 0 && /\s/.test(ch)) {
      kw.lastIndex = i;
      const m = kw.exec(sql);
      if (m) { out += "\n" + m[1]; i += m[0].length - 1; continue; }
    }
    out += ch;
  }
  return out;
}
'''
if "only outside brackets and strings" in s[start:end]:
    print("index.html: already updated")
else:
    p.write_text(s[:start] + new_fn + s[end:], encoding="utf-8")
    print("index.html: prettySql updated")
PY

echo
echo "Done. Restart the web server (Ctrl+C, then: uv run python -m app.web) and reload the page."
