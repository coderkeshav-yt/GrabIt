"""A download or local conversion tracked by the desktop queue."""

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event


@dataclass(eq=False)
class Job:
    source: str
    title: str
    folder: Path
    quality: str
    format_id: str | None = None
    index: int | None = None
    overwrite: bool = False
    convert: bool = False
    local: bool = False
    state: str = "Waiting"
    message: str = ""
    progress: float = 0.0
    pause_requested: bool = False
    cancelled: Event = field(default_factory=Event, repr=False)
