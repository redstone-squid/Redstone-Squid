from typing import final, Any


@final
class MISSING: pass
Missing = MISSING()
def drop_missing(x: Any):
    return None if x is Missing else x
