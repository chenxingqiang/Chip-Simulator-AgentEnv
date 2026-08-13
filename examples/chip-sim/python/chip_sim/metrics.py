from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Metrics:
    snapshot_aliases_used: set[str] = field(default_factory=set)
    start_latency_samples: list[float] = field(default_factory=list)

    def record_start(self, alias: str, latency_secs: float) -> None:
        self.snapshot_aliases_used.add(alias)
        self.start_latency_samples.append(latency_secs)
