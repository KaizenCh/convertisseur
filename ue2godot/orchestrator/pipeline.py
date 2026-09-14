# -*- coding: utf-8 -*-
"""Main pipeline state machine and gates G0-G6."""

from typing import Dict, Any, List
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.orchestrator.step5_copy import copy_step5


class PipelineOrchestrator:
    def __init__(self, cfg: ResolvedConfig):
        self.cfg = cfg

    def run_pipeline(self) -> List[StepReport]:
        reports = []

        # Gate G5 / Step 5 Copy
        step5_rep = copy_step5(self.cfg)
        reports.append(step5_rep)

        return reports
