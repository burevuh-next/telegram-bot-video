import asyncio
import json
import logging
from pathlib import Path

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
        self.client = client
        self.state_file = state_file
        self.poll_interval = poll_interval
        self.event_limit = event_limit
        self.include_labels = include_labels or ["person"]
        self.exclude_labels = exclude_labels or []
        self.include_cameras = include_cameras or ["all"]
        self.exclude_cameras = exclude_cameras or []
        self._seen_ids: set[str] = set()
        self._on_event: list[callable] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._load_state()

    def on_event(self, callback: callable):
        self._on_event.append(callback)

    def _load_state(self):
        path = Path(self.state_file)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._seen_ids = set(data.get("seen_ids", []))
                logger.info("Loaded %d seen event IDs", len(self._seen_ids))
            except Exception as exc:
                logger.warning("Failed to load state: %s", exc)

    async def _save_state(self):
        try:
            path = Path(self.state_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"seen_ids": list(self._seen_ids)[-5000:]}))
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

    def set_include_labels(self, labels: list[str]):
        self.include_labels = labels

    def set_include_cameras(self, cameras: list[str]):
        self.include_cameras = cameras

    def get_filters(self) -> dict:
        return {
            "include_labels": list(self.include_labels),
            "exclude_labels": list(self.exclude_labels),
            "include_cameras": list(self.include_cameras),
            "exclude_cameras": list(self.exclude_cameras),
        }

    def add_include_label(self, label: str) -> str:
        if label == "all":
            self.include_labels = []
            return "все"
        if label in self.include_labels:
            return "уже есть"
        self.include_labels.append(label)
        return "добавлена"

    def remove_include_label(self, label: str) -> str:
        if label == "all":
            self.include_labels = []
            return "теперь всё"
        if label not in self.include_labels:
            return "не найдена"
        self.include_labels.remove(label)
        return "удалена"

    def add_include_camera(self, camera: str) -> str:
        if camera == "all":
            self.include_cameras = []
            return "все"
        if camera in self.include_cameras:
            return "уже есть"
        self.include_cameras.append(camera)
        return "добавлена"

    def remove_include_camera(self, camera: str) -> str:
        if camera == "all":
            self.include_cameras = []
            return "теперь всё"
        if camera not in self.include_cameras:
            return "не найдена"
        self.include_cameras.remove(camera)
        return "удалена"

    async def _poll(self):
        while self._running:
            try:
                events = await self.client.get_events(limit=self.event_limit)
                for event in events:
                    if event.id not in self._seen_ids:
                        self._seen_ids.add(event.id)
                        if self._should_process(event):
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
                    self._seen_ids = set(list(self._seen_ids)[-5000:])
                await self._save_state()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Poll error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll())
        logger.info("Monitor started (poll every %ds)", self.poll_interval)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._save_state()
        logger.info("Monitor stopped")
