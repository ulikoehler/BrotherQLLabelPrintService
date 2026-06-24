import uuid
import yaml
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import threading


class QueueItem:
    def __init__(
        self,
        item_id: str,
        original_filename: str,
        stored_filename: str,
        timestamp: str,
        status: str,
        label: str = "",
        rotation: int = 0,
        copies: int = 1,
        orientation: str = "",
        width_mm: float = 0,
        height_mm: float = 0,
        error_message: str = "",
        preview_filename: str = "",
    ):
        self.id = item_id
        self.original_filename = original_filename
        self.stored_filename = stored_filename
        self.timestamp = timestamp
        self.status = status
        self.label = label
        self.rotation = rotation
        self.copies = copies
        self.orientation = orientation
        self.width_mm = width_mm
        self.height_mm = height_mm
        self.error_message = error_message
        self.preview_filename = preview_filename

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_filename": self.original_filename,
            "stored_filename": self.stored_filename,
            "timestamp": self.timestamp,
            "status": self.status,
            "label": self.label,
            "rotation": self.rotation,
            "copies": self.copies,
            "orientation": self.orientation,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "error_message": self.error_message,
            "preview_filename": self.preview_filename,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QueueItem":
        return cls(
            item_id=d["id"],
            original_filename=d.get("original_filename", ""),
            stored_filename=d.get("stored_filename", ""),
            timestamp=d.get("timestamp", ""),
            status=d.get("status", "unknown"),
            label=d.get("label", ""),
            rotation=d.get("rotation", 0),
            copies=d.get("copies", 1),
            orientation=d.get("orientation", ""),
            width_mm=d.get("width_mm", 0),
            height_mm=d.get("height_mm", 0),
            error_message=d.get("error_message", ""),
            preview_filename=d.get("preview_filename", ""),
        )


class PrintQueue:
    def __init__(self, queue_file: str, max_history: int = 100):
        self.queue_file = queue_file
        self.max_history = max_history
        self._lock = threading.Lock()
        self._items: list[QueueItem] = []
        self._load()

    def _load(self) -> None:
        path = Path(self.queue_file)
        if path.exists():
            with open(path, "r") as f:
                data = yaml.safe_load(f) or []
            self._items = [QueueItem.from_dict(d) for d in data]

    def _save(self) -> None:
        path = Path(self.queue_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(
                [item.to_dict() for item in self._items],
                f,
                default_flow_style=False,
                sort_keys=False,
            )

    def add(self, item: QueueItem) -> QueueItem:
        with self._lock:
            self._items.append(item)
            self._trim()
            self._save()
        return item

    def get(self, item_id: str) -> Optional[QueueItem]:
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    return item
        return None

    def update(self, item_id: str, **kwargs) -> Optional[QueueItem]:
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    for key, val in kwargs.items():
                        if hasattr(item, key):
                            setattr(item, key, val)
                    self._save()
                    return item
        return None

    def remove(self, item_id: str) -> bool:
        with self._lock:
            for i, item in enumerate(self._items):
                if item.id == item_id:
                    self._items.pop(i)
                    self._save()
                    return True
        return False

    def list_all(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    def list_pending(self) -> list[QueueItem]:
        with self._lock:
            return [item for item in self._items if item.status in ("queued", "printing")]

    def _trim(self) -> None:
        if len(self._items) > self.max_history:
            self._items = self._items[-self.max_history:]

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()


def create_queue_item(
    original_filename: str,
    stored_filename: str,
    label: str = "",
    rotation: int = 0,
    copies: int = 1,
    orientation: str = "",
    width_mm: float = 0,
    height_mm: float = 0,
    preview_filename: str = "",
) -> QueueItem:
    return QueueItem(
        item_id=str(uuid.uuid4()),
        original_filename=original_filename,
        stored_filename=stored_filename,
        timestamp=datetime.now(timezone.utc).isoformat(),
        status="queued",
        label=label,
        rotation=rotation,
        copies=copies,
        orientation=orientation,
        width_mm=width_mm,
        height_mm=height_mm,
        preview_filename=preview_filename,
    )
