from __future__ import annotations
 
 
def db_error_message(exc: BaseException) -> str:
    """Return the first line of the underlying DB driver error.
 
    SQLAlchemy's StatementError has an ``orig`` attribute that may be ``None``
    (e.g. a missing bind parameter), so ``getattr(exc, "orig", exc)`` would
    produce the useless message "None".
    """
    orig = getattr(exc, "orig", None)
    source = orig if orig is not None else exc
    message = str(source).strip()
    if not message:
        return type(source).__name__
    return message.splitlines()[0]
