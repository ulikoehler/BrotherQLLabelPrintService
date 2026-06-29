import os
import yaml
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = {
    "printer": {
        "model": "QL-560",
        "backend": "pyusb",
        "identifier": "usb://04f9:2027",
        "label": "62",
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "workers": 2,
    },
    "printing": {
        "tape_width_mm": 62,
        "rotate": "auto",
        "threshold": 70,
        "dither": False,
        "compress": False,
        "cut": True,
        "hq": True,
        "dpi_600": False,
        "copies": 1,
        "copy_order": "sequential",
        "on_print_error": "stop",
    },
    "ui": {
        "show_preview": True,
        "max_history": 100,
    },
    "storage": {
        "data_dir": "/data",
        "queue_file": "/data/queue.yaml",
    },
}

VALID_TAPE_WIDTHS = [12, 29, 38, 50, 54, 62, 102, 103]
VALID_ROTATIONS = ["auto", "0", "90", "180", "270"]
VALID_BACKENDS = ["pyusb", "network", "linux_kernel"]
VALID_COPY_ORDERS = ["sequential", "grouped"]
VALID_ON_PRINT_ERROR = ["stop", "continue"]


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        path = Path(self.config_path)
        if path.exists():
            with open(path, "r") as f:
                user_config = yaml.safe_load(f) or {}
            self._data = _deep_merge(DEFAULT_CONFIG, user_config)
        self._validate()

    def reload(self) -> None:
        self.load()

    def save(self) -> None:
        with open(self.config_path, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False, sort_keys=False)

    def _validate(self) -> None:
        p = self._data["printing"]
        if p["tape_width_mm"] not in VALID_TAPE_WIDTHS:
            raise ValueError(
                f"Invalid tape_width_mm: {p['tape_width_mm']}. "
                f"Must be one of {VALID_TAPE_WIDTHS}"
            )
        if str(p["rotate"]) not in VALID_ROTATIONS:
            raise ValueError(
                f"Invalid rotate: {p['rotate']}. Must be one of {VALID_ROTATIONS}"
            )
        if p.get("copy_order", "sequential") not in VALID_COPY_ORDERS:
            raise ValueError(
                f"Invalid copy_order: {p.get('copy_order')}. Must be one of {VALID_COPY_ORDERS}"
            )
        if p.get("on_print_error", "stop") not in VALID_ON_PRINT_ERROR:
            raise ValueError(
                f"Invalid on_print_error: {p.get('on_print_error')}. Must be one of {VALID_ON_PRINT_ERROR}"
            )
        b = self._data["printer"]["backend"]
        if b not in VALID_BACKENDS:
            raise ValueError(
                f"Invalid backend: {b}. Must be one of {VALID_BACKENDS}"
            )

    @property
    def printer(self) -> dict:
        return self._data["printer"]

    @property
    def server(self) -> dict:
        return self._data["server"]

    @property
    def printing(self) -> dict:
        return self._data["printing"]

    @property
    def ui(self) -> dict:
        return self._data["ui"]

    @property
    def storage(self) -> dict:
        return self._data["storage"]

    def to_dict(self) -> dict:
        return dict(self._data)

    def update(self, partial: dict) -> None:
        self._data = _deep_merge(self._data, partial)
        self._validate()
        self.save()
