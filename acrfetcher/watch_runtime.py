from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Optional


TaskFactory = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class RunController:
    """Lifecycle controller for run/pause/quit semantics using TaskGroup."""

    factories: list[TaskFactory] = field(default_factory=list)
    state: str = "stopped"  # running | paused | stopped | quit
    _stop_evt: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _tg_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)

    async def _runner(self) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                for factory in self.factories:
                    tg.create_task(factory())
                await self._stop_evt.wait()
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            return

    async def start(self) -> None:
        if self.state == "running":
            return
        self._stop_evt = asyncio.Event()
        self._tg_task = asyncio.create_task(self._runner())
        self.state = "running"

    async def pause(self) -> None:
        if self.state != "running":
            self.state = "paused"
            return
        self._stop_evt.set()
        if self._tg_task is not None:
            try:
                await self._tg_task
            except Exception:
                pass
        self.state = "paused"

    async def run(self) -> None:
        if self.state == "running":
            return
        await self.start()

    async def quit(self) -> None:
        self._stop_evt.set()
        if self._tg_task is not None:
            try:
                await self._tg_task
            except Exception:
                pass
        self.state = "quit"
