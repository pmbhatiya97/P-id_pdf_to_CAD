"""Bounded, isolated batch execution for CLI and desktop clients."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from time import monotonic
from .core import convert_file, ConversionCancelled


@dataclass
class BatchItem:
    source: Path
    destination: Path
    status: str = "Queued"
    error: str = ""
    result: dict = field(default_factory=dict)


def run_batch(items: list[BatchItem], *, workers=2, cancel=None, callback=None, config=None, **options):
    cancel = cancel or Event()
    if not 1 <= workers <= 4:
        raise ValueError("Batch workers must be between 1 and 4")
    destinations = [str(i.destination.resolve()).casefold() for i in items]
    if len(set(destinations)) != len(destinations):
        raise ValueError("Batch destinations must be unique to prevent overwrites")
    sources = {str(i.source.resolve()).casefold() for i in items}
    if sources.intersection(destinations):
        raise ValueError("Batch destinations must not overwrite any input file")
    before = monotonic()
    def notify(item):
        if callback:
            callback(item)
    def work(item):
        if cancel.is_set():
            item.status = "Cancelled"
            notify(item)
            return
        item.status = "Converting"
        notify(item)
        try:
            def progress(percent, message):
                if callback:
                    callback(item, percent, message)
            item.result = convert_file(item.source, item.destination, config=config, cancel=cancel,
                                       progress=progress, **options)
            item.status = "Done"
        except ConversionCancelled as exc:
            item.status, item.error = "Cancelled", str(exc)
        except Exception as exc:
            item.status, item.error = "Failed", str(exc)
        notify(item)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lineaforge") as pool:
        for future in as_completed([pool.submit(work, item) for item in items]):
            future.result()
    return {"done": sum(i.status == "Done" for i in items),
            "failed": sum(i.status == "Failed" for i in items),
            "cancelled": sum(i.status == "Cancelled" for i in items),
            "seconds": monotonic()-before}


def retry_failed(items, **kwargs):
    retry = [i for i in items if i.status == "Failed"]
    for item in retry:
        item.status, item.error = "Queued", ""
    return run_batch(retry, **kwargs)
