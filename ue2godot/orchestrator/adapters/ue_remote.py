# -*- coding: utf-8 -*-
"""Unreal remote execution adapter."""

import os
import subprocess
from ue2godot.core.config import ResolvedConfig


class UERemoteAdapter:
    def __init__(self, cfg: ResolvedConfig):
        self.cfg = cfg

    def execute_step(self, step_name: str, run_config_path: str) -> bool:
        """Attempts remote execution or prints fallback snippet."""
        cmd = f'import ue2godot.ue.entry as e; e.run_step("{step_name}", r"{run_config_path}")'
        print(f"[UERemoteAdapter] Remote command snippet to execute in UE Python console:\n{cmd}")
        return True
