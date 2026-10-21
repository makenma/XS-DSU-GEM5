class ReconciliationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReconciliationError(message)
