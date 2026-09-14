# -*- coding: utf-8 -*-
"""Godot CLI adapter."""

import subprocess
import os
from typing import Tuple


class GodotCLIAdapter:
    def __init__(self, godot_executable: str = "godot"):
        self.godot_executable = godot_executable

    def import_assets(self, godot_project_root: str) -> bool:
        cmd = [self.godot_executable, "--headless", "--path", godot_project_root, "--import"]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return res.returncode == 0
        except Exception:
            return False

    def build_scene(self, godot_project_root: str, manifest_path: str, report_path: str) -> Tuple[bool, str]:
        script_path = "res://addons/ue2godot/entry_headless.gd"
        cmd = [
            self.godot_executable, "--headless", "--path", godot_project_root,
            "--script", script_path, "--",
            "--manifest", manifest_path,
            "--report", report_path
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return res.returncode == 0, res.stdout
        except Exception as exc:
            return False, str(exc)
