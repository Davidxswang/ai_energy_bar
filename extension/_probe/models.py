from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Final

JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

REPO_ROOT_HINT_FILE: Final[str] = ".repo-root"


def resolve_project_root() -> Path:
    probe_path = Path(__file__).resolve()
    hint_path = probe_path.parent.parent / REPO_ROOT_HINT_FILE
    if hint_path.exists():
        try:
            hinted_root = Path(hint_path.read_text().strip()).expanduser().resolve()
        except OSError:
            hinted_root = None
        if hinted_root is not None and hinted_root.exists():
            return hinted_root
    return probe_path.parent.parent.parent


PROJECT_ROOT = resolve_project_root()


@dataclasses.dataclass(slots=True)
class LimitMetric:
    label: str
    percent_remaining: float | None = None
    text: str | None = None

    def to_json(self) -> dict[str, JSONValue]:
        return {
            "label": self.label,
            "percent_remaining": self.percent_remaining,
            "text": self.text,
        }


@dataclasses.dataclass(slots=True)
class ProviderStatus:
    key: str
    display_name: str
    available: bool
    summary: str
    detail: str
    source: str
    compact: str | None = None
    warning: str | None = None
    version: str | None = None
    metrics: dict[str, LimitMetric] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict[str, JSONValue]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "available": self.available,
            "summary": self.summary,
            "detail": self.detail,
            "source": self.source,
            "compact": self.compact,
            "warning": self.warning,
            "version": self.version,
            "metrics": {
                name: metric.to_json() for name, metric in self.metrics.items()
            },
        }
