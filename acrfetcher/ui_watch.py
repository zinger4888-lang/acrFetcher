from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .models import RowState, UiEvent


@dataclass(slots=True)
class UiStateReducer:
    state: dict[str, RowState] = field(default_factory=dict)
    events: asyncio.Queue[UiEvent] = field(default_factory=asyncio.Queue)

    def init_row(self, label: str, row: RowState) -> None:
        self.state[label] = row

    async def emit(self, event: UiEvent) -> None:
        await self.events.put(event)

    def apply(self, event: UiEvent) -> None:
        row = self.state.get(event.label)
        if row is None:
            return
        if event.status:
            row.status = event.status
        row.detail = event.detail or ""
        if event.ticket:
            row.ticket = event.ticket

    async def reduce_once(self) -> None:
        event = await self.events.get()
        try:
            self.apply(event)
        finally:
            try:
                self.events.task_done()
            except Exception:
                pass

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(self.reduce_once(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
