# care_portal/ui/utils.py
from __future__ import annotations

import threading
import traceback
from typing import Callable, Any, Optional

def run_in_thread(
    *,
    work: Callable[[], Any],
    on_done: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[BaseException], None]] = None,
    tk_after: Callable[[int, Callable[[], None]], str] | None = None,
) -> None:
    """
    Run 'work' in a background thread. If 'on_done' is provided, it will be called
    on the Tk thread (via tk_after) with the result. Same for 'on_error'.
    """
    def _runner():
        try:
            result = work()
        except BaseException as e:  # noqa: BLE001
            if on_error:
                if tk_after:
                    tk_after(0, lambda: on_error(e))
                else:
                    on_error(e)
            else:
                traceback.print_exc()
            return
        if on_done:
            if tk_after:
                tk_after(0, lambda: on_done(result))
            else:
                on_done(result)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
