from dataclasses import dataclass


@dataclass
class StorageStatus:
    message: str


_events: list[StorageStatus] = []


def record_storage_event(message: str) -> None:
    _events.append(StorageStatus(message=message))


def pop_storage_events() -> list[StorageStatus]:
    events = list(_events)
    _events.clear()
    return events
