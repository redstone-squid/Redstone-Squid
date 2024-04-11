from typing import final, Any


@final
class MISSING:
    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __str__(self):
        return 'MISSING'

Missing = MISSING()
def drop_missing(x: Any):
    return None if x is Missing else x
