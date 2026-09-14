# -*- coding: utf-8 -*-
"""Configuration handling, merging, hashing, and freezing."""

import json
import hashlib
import uuid
from dataclasses import dataclass
from typing import Dict, Any, Optional


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override dictionary into base dictionary."""
    result = json.loads(json.dumps(base))

    def merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = v

    merge(result, override)
    return result


def compute_config_hash(config_data: Dict[str, Any]) -> str:
    """Compute canonical SHA256 hash of configuration data."""
    canonical_json = json.dumps(config_data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResolvedConfig:
    data: Dict[str, Any]
    run_id: str
    config_hash: str

    @classmethod
    def resolve(cls, defaults: Dict[str, Any], profile: Dict[str, Any], run_config: Dict[str, Any], run_id: Optional[str] = None) -> "ResolvedConfig":
        merged = deep_merge(defaults, profile)
        merged = deep_merge(merged, run_config)
        cfg_hash = compute_config_hash(merged)
        r_id = run_id or str(uuid.uuid4())
        return cls(data=merged, run_id=r_id, config_hash=cfg_hash)

    def get(self, path: str, default: Any = None) -> Any:
        keys = path.split(".")
        curr = self.data
        for k in keys:
            if isinstance(curr, dict) and k in curr:
                curr = curr[k]
            else:
                return default
        return curr

    def to_dict(self) -> Dict[str, Any]:
        res = dict(self.data)
        res["_resolved"] = {
            "run_id": self.run_id,
            "config_hash": self.config_hash
        }
        return res
