"""Development supervisor for the API, bot, and database worker processes."""

import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from types import FrameType

PROCESS_MODULES = ("squid.api.app", "squid.bot.app", "squid.worker.app")


def _interrupt(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def supervise(commands: Sequence[Sequence[str]]) -> int:
    """Run child commands together and stop every sibling when one exits."""
    processes = [subprocess.Popen(command) for command in commands]
    try:
        while True:
            for process in processes:
                exit_code = process.poll()
                if exit_code is not None:
                    return exit_code
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> int:
    """Start each production entry point as a supervised development child."""
    for process_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(process_signal, _interrupt)
    return supervise(tuple((sys.executable, "-m", module) for module in PROCESS_MODULES))


if __name__ == "__main__":
    raise SystemExit(main())
