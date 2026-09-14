# -*- coding: utf-8 -*-
"""StepReport execution output container."""

import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class StepReport:
    step: str
    run_id: str
    config_hash: str
    status: str  # "OK" | "OK_WITH_WARNINGS" | "FAILED"
    started_at: str
    duration_s: float
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    counters: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, filepath: str) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
