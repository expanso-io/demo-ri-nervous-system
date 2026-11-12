"""Run blocking SDK calls off the decision path.

Real SDK calls block for seconds (a take-off waits for the climb). The
decide node must answer in milliseconds, so commands go on a queue drained by
one worker thread. A land clears the queue first: the safety reflex never
waits behind a climb.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

Command = tuple[str, Callable[[], None]]


class CommandWorker:
    def __init__(self, name: str) -> None:
        self._queue: queue.Queue[Command | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=f"{name}-sdk", daemon=True)
        self._thread.start()

    def submit(self, label: str, call: Callable[[], None], *, preempt: bool = False) -> None:
        if preempt:
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        self._queue.put((label, call))

    def close(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            label, call = item
            try:
                call()
            except Exception:  # an SDK failure must not kill the worker
                log.exception("drone command failed: %s", label)
