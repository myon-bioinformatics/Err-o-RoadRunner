try:
    1 / 0
except ZeroDivisionError as exc:
    raise ValueError('while handling') from exc
