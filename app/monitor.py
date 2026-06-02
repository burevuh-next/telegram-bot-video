import asyncio
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from app.frigate import FrigateClient, FrigateEvent

logger = logging.getLogger(__name__)


class EventMonitor:
    def __init__(
        self,
        client: FrigateClient,
        state_file: str,
        poll_interval: int = 5,
        event_limit: int = 50,
        include_labels: list[str] | None = None,
        exclude_labels: list[str] | None = None,
        include_cameras: list[str] | None = None,
        exclude_cameras: list[str] | None = None,
    ):
        self.client: FrigateClient = client
        self.state_file: str = state_file
        self.poll_interval: int = poll_interval
        self.event_limit: int = event_limit
        self.include_labels: list[str] = include_labels or ["person"]
        self.exclude_labels: list[str] = exclude_labels or []
        self.include_cameras: list[str] = include_cameras or ["all"]
        self.exclude_cameras: list[str] = exclude_cameras or []
        self._seen_ids: OrderedDict[str, bool] = OrderedDict()
        self._on_event: list[Callable[[FrigateEvent], Any]] = []
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._last_save: float = 0
        self._load_state()

    def on_event(self, callback: Callable[[FrigateEvent], Any]) -> None:
        self._on_event.append(callback)

    def _load_state(self) -> None:
        path = Path(self.state_file)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                ids = data.get("seen_ids", [])
                self._seen_ids = OrderedDict.fromkeys(ids)
                logger.info("Loaded %d seen event IDs", len(self._seen_ids))
            except Exception as exc:
                logger.warning("Failed to load state: %s", exc)

    async def _save_state(self) -> None:
        try:
            path = Path(self.state_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            limit = 10000
            ids = list(self._seen_ids.keys())[-limit:]
            path.write_text(json.dumps({"seen_ids": ids}))
        except Exception as exc:
            logger.warning("Failed to save state: %s", exc)

    def _should_process(self, event: FrigateEvent) -> bool:
        if "all" not in self.include_cameras and event.camera not in self.include_cameras:
            return False
        if event.camera in self.exclude_cameras:
            return False
        if event.label in self.exclude_labels:
            return False
        if self.include_labels and event.label not in self.include_labels:
            return False
        return True

    async def set_include_labels(self, labels: list[str]) -> None:
        async with self._lock:
            self.include_labels = labels

    async def set_include_cameras(self, cameras: list[str]) -> None:
        async with self._lock:
            self.include_cameras = cameras

    def get_filters(self) -> dict[str, list[str]]:
        return {
            "include_labels": list(self.include_labels),
            "exclude_labels": list(self.exclude_labels),
            "include_cameras": list(self.include_cameras),
            "exclude_cameras": list(self.exclude_cameras),
        }

    async def add_include_label(self, label: str) -> str:
        if label == "all":
            await self.set_include_labels([])
            return "все"
        async with self._lock:
            if label in self.include_labels:
                return "уже есть"
            self.include_labels.append(label)
            return "добавлена"

    async def remove_include_label(self, label: str) -> str:
        if label == "all":
            await self.set_include_labels([])
            return "теперь всё"
        async with self._lock:
            if label not in self.include_labels:
                return "не найдена"
            self.include_labels.remove(label)
            return "удалена"

    async def add_include_camera(self, camera: str) -> str:
        if camera == "all":
            await self.set_include_cameras([])
            return "все"
        async with self._lock:
            if camera in self.include_cameras:
                return "уже есть"
            self.include_cameras.append(camera)
            return "добавлена"

    async def remove_include_camera(self, camera: str) -> str:
        if camera == "all":
            await self.set_include_cameras([])
            return "теперь всё"
        async with self._lock:
            if camera not in self.include_cameras:
                return "не найдена"
            self.include_cameras.remove(camera)
            return "удалена"

    async def _poll(self):
        consecutive_errors = 0
        while self._running:
            try:
                events = await self.client.get_events(limit=self.event_limit)
                for event in events:
                    if event.id not in self._seen_ids:
                        self._seen_ids[event.id] = True
                        async with self._lock:
                            should_process = self._should_process(event)
                        if should_process:
                            logger.info(
                                "New event: %s on %s at %s",
                                event.label,
                                event.camera,
                                event.start_time_dt.isoformat(),
                            )
                            for cb in self._on_event:
                                try:
                                    await cb(event)
                                except Exception as exc:
                                    logger.error("Callback error: %s", exc)
                if len(self._seen_ids) > 10000:
                    keys = list(self._seen_ids.keys())[-5000:]
                    self._seen_ids = OrderedDict.fromkeys(keys)

                now = time.time()
                if now - self._last_save > 30:
                    await self._save_state()
                    self._last_save = now

                consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_errors += 1
                logger.error("Poll error (%d): %s", consecutive_errors, exc)
                delay = min(self.poll_interval * (2 ** (consecutive_errors - 1)), 300)
                await asyncio.sleep(delay)
                continue
            await asyncio.sleep(self.poll_interval)

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll())
        logger.info("Monitor started (poll every %ds)", self.poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._save_state()
        logger.info("Monitor stopped")
