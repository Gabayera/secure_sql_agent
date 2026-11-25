from __future__ import annotations
 
import re
from typing import Any
 
from sqlglot import exp
 
from app.models import ValidationResult
 
# SQLAlchemy's text() reads ":name::int" as the bind ":nam" + "e::int" -> broken SQL.
_BIND_WITH_PG_CAST = re.compile(r"(?<![:\w]):[A-Za-z_]\w*::")
 
_SCALAR_TYPES = (str, int, float, bool, type(None))
 
 
def placeholder_names(tree: exp.Expression) -> set[str]:
    return {node.name for node in tree.find_all(exp.Placeholder) if node.name}
 
 
def check_parameters(
    tree: exp.Expression, sql: str, parameters: dict[str, Any]
) -> tuple[ValidationResult, dict[str, Any]]:
    """Check that bind placeholders and supplied parameters line up.
 
    Returns the validation result and the parameters with unused keys removed.
    """
    result = ValidationResult(stage="parameters")
 
    names: set[str] = set()
    for node in tree.find_all(exp.Placeholder):
        if node.name:
            names.add(node.name)
        else:
            result.add(
                "positional_placeholder",
                "Use named placeholders like :p_name, not '?'",
            )
    if any(True for _ in tree.find_all(exp.Parameter)):
        result.add(
            "positional_placeholder",
            "Use named placeholders like :p_name, not $1 / @var",
        )
 
    if _BIND_WITH_PG_CAST.search(sql):
        result.add(
            "bind_cast",
            "Do not write ':name::type'; use CAST(:name AS type) instead",
        )
 
    missing = sorted(names - set(parameters))
    if missing:
        result.add(
            "missing_parameter",
            f"No value supplied for placeholder(s): {', '.join(missing)}",
        )
 
    for key, value in parameters.items():
        if key not in names:
            continue
        if isinstance(value, list):
            if not all(isinstance(v, _SCALAR_TYPES) for v in value):
                result.add("bad_parameter", f"Parameter '{key}' must contain scalar values")
        elif not isinstance(value, _SCALAR_TYPES):
            result.add("bad_parameter", f"Parameter '{key}' must be a scalar value")
 
    cleaned = {k: v for k, v in parameters.items() if k in names}
    return result, cleaned
