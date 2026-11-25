from __future__ import annotations
 
from sqlglot import exp
 
 
def has_where_clause(tree: exp.Expression) -> bool:
    """True only if the *statement itself* has a WHERE clause.
 
    The old ``tree.find(exp.Where)`` also matched a WHERE inside a sub-query,
    so ``UPDATE t SET x = (SELECT ... WHERE ...)`` was wrongly seen as bounded.
    """
    return tree.args.get("where") is not None
 
 
def is_potentially_unbounded_write(tree: exp.Expression) -> bool:
    """UPDATE/DELETE with no WHERE, or a WHERE that references no column
    (e.g. ``WHERE 1 = 1`` / ``WHERE TRUE``)."""
    if not isinstance(tree, (exp.Update, exp.Delete)):
        return False
    where = tree.args.get("where")
    if where is None:
        return True
    return where.find(exp.Column) is None
