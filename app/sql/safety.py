from __future__ import annotations

from sqlglot import exp


def has_where_clause(tree: exp.Expression) -> bool:
    return tree.find(exp.Where) is not None


def is_potentially_unbounded_write(tree: exp.Expression) -> bool:
    if isinstance(tree, (exp.Update, exp.Delete)):
        return not has_where_clause(tree)
    return False
