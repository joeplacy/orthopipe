"""Fabrication workflow states confirmed by the September 2026 written SOP.

This module names the handoffs without claiming that the currently in-memory web
app is a durable workflow engine. It gives future persistence and audit work one
shared transition contract instead of scattering status strings through the UI.
"""
from __future__ import annotations

from enum import Enum


class WorkflowState(str, Enum):
    RECEIVED = "received"
    FILES_STAGED = "files_staged"
    SCANS_ORIENTED = "scans_oriented"
    DESIGN_COMPLETE = "design_complete"
    PRINT_LAYOUT_READY = "print_layout_ready"
    SUPPORTS_GENERATED = "supports_generated"
    TOOLPATH_QC_APPROVED = "toolpath_qc_approved"
    GCODE_RELEASED = "gcode_released"


WORKFLOW_SEQUENCE = tuple(WorkflowState)
ALLOWED_TRANSITIONS = {
    state: ({WORKFLOW_SEQUENCE[i + 1]} if i + 1 < len(WORKFLOW_SEQUENCE) else set())
    for i, state in enumerate(WORKFLOW_SEQUENCE)
}


def require_transition(current: WorkflowState, target: WorkflowState) -> None:
    """Reject skipped or backward production states."""
    if target not in ALLOWED_TRANSITIONS[current]:
        allowed = sorted(state.value for state in ALLOWED_TRANSITIONS[current])
        raise ValueError(f"invalid workflow transition {current.value!r} -> {target.value!r}; "
                         f"allowed: {allowed}")


def next_state(current: WorkflowState) -> WorkflowState | None:
    allowed = ALLOWED_TRANSITIONS[current]
    return next(iter(allowed)) if allowed else None
