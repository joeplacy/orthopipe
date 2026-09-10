"""Deterministic bilateral print preparation derived from the fabricator workflow.

Replaces the repeatable Meshmixer layout actions: stand each finished orthosis with
the forefoot upward, place the medial faces toward the center with a controlled gap,
bring the pair to the bed (including the documented Simplify3D z embed), and export
one multi-body STL. Slicing and visual G-code approval remain explicit downstream
human gates until the real Simplify3D factory file, printer, and material are known.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh

from workflow import WorkflowState, next_state


ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@dataclass(frozen=True)
class PrintPrepProfile:
    """Confirmed defaults plus unknown bed constraints kept explicitly optional."""

    pair_gap_mm: float = 5.0
    bed_embed_mm: float = 0.3
    support_angle_threshold_deg: float = 23.0
    bed_width_mm: float | None = None
    bed_depth_mm: float | None = None
    bed_height_mm: float | None = None

    def __post_init__(self):
        if self.pair_gap_mm <= 0:
            raise ValueError("pair_gap_mm must be > 0 so the orthoses never touch")
        if self.bed_embed_mm < 0:
            raise ValueError("bed_embed_mm must be >= 0")
        if not 0 < self.support_angle_threshold_deg < 90:
            raise ValueError("support_angle_threshold_deg must be between 0 and 90")
        for name in ("bed_width_mm", "bed_depth_mm", "bed_height_mm"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_mesh(path: str | Path) -> trimesh.Trimesh:
    mesh = trimesh.load(str(path), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"{path}: no triangle mesh found")
    mesh = trimesh.Trimesh(vertices=np.asarray(mesh.vertices),
                           faces=np.asarray(mesh.faces), process=True)
    mesh.metadata.clear()
    return mesh


def _stand_forefoot_up(mesh: trimesh.Trimesh, embed_mm: float) -> tuple[trimesh.Trimesh, np.ndarray]:
    """Map canonical +Y heel-to-toe onto +Z, then place the lowest point at -embed."""
    transform = trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0])
    out = mesh.copy()
    out.apply_transform(transform)
    translation = np.array([0.0, -float(out.centroid[1]), -embed_mm - float(out.bounds[0, 2])])
    out.apply_translation(translation)
    final = trimesh.transformations.translation_matrix(translation) @ transform
    return out, final


def _translate(mesh: trimesh.Trimesh, delta: np.ndarray, transform: np.ndarray) -> np.ndarray:
    mesh.apply_translation(delta)
    return trimesh.transformations.translation_matrix(delta) @ transform


def _render_layout_preview(left: trimesh.Trimesh, right: trimesh.Trimesh, path: Path) -> None:
    """Write a headless front/top QA view without requiring an OpenGL mesh renderer."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    for mesh, color, label in ((left, "#2563eb", "left"),
                               (right, "#dc2626", "right")):
        vertices = np.asarray(mesh.vertices)
        stride = max(1, len(vertices) // 5000)
        sample = vertices[::stride]
        axes[0].scatter(sample[:, 0], sample[:, 2], s=1, alpha=0.35,
                        color=color, label=label)
        axes[1].scatter(sample[:, 0], sample[:, 1], s=1, alpha=0.35,
                        color=color, label=label)
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set(title="Front view", xlabel="printer X mm", ylabel="printer Z mm")
    axes[1].set(title="Top view", xlabel="printer X mm", ylabel="printer Y mm")
    for axis in axes:
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.2)
        axis.legend()
    fig.suptitle("Bilateral print layout preview")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def prepare_pair(left_path: str | Path, right_path: str | Path, output_stl: str | Path,
                 order_id: str, profile: PrintPrepProfile | None = None,
                 manifest_path: str | Path | None = None) -> dict:
    """Prepare and export a non-touching left/right multi-body STL plus JSON manifest."""
    if not ORDER_ID_RE.fullmatch(order_id):
        raise ValueError("order_id must be 1-80 letters, numbers, hyphens, or underscores")
    profile = profile or PrintPrepProfile()
    left_source, right_source = Path(left_path), Path(right_path)
    left, left_t = _stand_forefoot_up(_load_mesh(left_source), profile.bed_embed_mm)
    right, right_t = _stand_forefoot_up(_load_mesh(right_source), profile.bed_embed_mm)

    # Generated left outputs have medial toward -X; right outputs have medial toward
    # +X. Place left on +X and right on -X so both medial faces point toward center.
    half_gap = profile.pair_gap_mm / 2.0
    left_delta = np.array([half_gap - float(left.bounds[0, 0]), 0.0, 0.0])
    right_delta = np.array([-half_gap - float(right.bounds[1, 0]), 0.0, 0.0])
    left_t = _translate(left, left_delta, left_t)
    right_t = _translate(right, right_delta, right_t)

    actual_gap = float(left.bounds[0, 0] - right.bounds[1, 0])
    pair = trimesh.util.concatenate([left, right])
    output = Path(output_stl)
    output.parent.mkdir(parents=True, exist_ok=True)
    pair.export(str(output))
    preview = output.with_suffix(".preview.png")
    _render_layout_preview(left, right, preview)

    extents = [round(float(value), 3) for value in pair.extents]
    bed_fit = {
        "width": None if profile.bed_width_mm is None else extents[0] <= profile.bed_width_mm,
        "depth": None if profile.bed_depth_mm is None else extents[1] <= profile.bed_depth_mm,
        "height": None if profile.bed_height_mm is None else extents[2] <= profile.bed_height_mm,
    }
    bed_fit_verified = all(value is True for value in bed_fit.values())
    components = len(pair.split(only_watertight=False))
    checks = {
        "left_watertight": bool(left.is_watertight),
        "right_watertight": bool(right.is_watertight),
        "two_disconnected_bodies": components == 2,
        "pair_not_touching": actual_gap > 0,
        "requested_gap_met": actual_gap + 1e-6 >= profile.pair_gap_mm,
        "bed_fit": True if bed_fit_verified else None,
    }
    state = WorkflowState.PRINT_LAYOUT_READY
    manifest = {
        "schema_version": 1,
        "order_id": order_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_checkpoint": {
            "current": state.value,
            "next_required": next_state(state).value,
            "gcode_release_blocked": True,
            "reason": "supports, slicing, and visual toolpath QC are not performed by print_prep",
        },
        "profile": asdict(profile),
        "workflow_provenance": {
            "orientation": "forefoot upward; left/right medial faces toward pair center",
            "combine_semantics": "single STL containing two disconnected mesh bodies",
            "simplify3d_bed_embed_mm": profile.bed_embed_mm,
            "simplify3d_support_angle_threshold_deg": profile.support_angle_threshold_deg,
            "support_generation_performed": False,
            "toolpath_qc_performed": False,
        },
        "inputs": {
            "left": {"path": str(left_source), "sha256": _sha256(left_source)},
            "right": {"path": str(right_source), "sha256": _sha256(right_source)},
        },
        "output": {"path": str(output), "sha256": _sha256(output),
                   "preview_path": str(preview), "preview_sha256": _sha256(preview),
                   "extents_mm": extents, "component_count": components},
        "placement": {
            "actual_pair_gap_mm": round(actual_gap, 3),
            "minimum_z_mm": round(float(pair.bounds[0, 2]), 3),
            "left_transform": np.round(left_t, 9).tolist(),
            "right_transform": np.round(right_t, 9).tolist(),
        },
        "bed_fit_axes": bed_fit,
        "checks": checks,
        "pass": all(value for value in checks.values() if value is not None),
        "bed_fit_verified": bed_fit_verified,
        "unresolved_inputs": [
            "Simplify3D factory file and process profile",
            "printer build volume and coordinate convention",
            "printer, nozzle, material, and layer settings",
            "fabricator visual G-code acceptance criteria",
        ],
    }
    manifest_file = Path(manifest_path) if manifest_path else output.with_suffix(".manifest.json")
    manifest["manifest_path"] = str(manifest_file)
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a bilateral orthotic pair for slicing")
    parser.add_argument("--left", required=True, help="completed left orthotic STL")
    parser.add_argument("--right", required=True, help="completed right orthotic STL")
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--out", required=True, help="combined multi-body STL output")
    parser.add_argument("--profile", help="optional JSON overrides for PrintPrepProfile")
    args = parser.parse_args(argv)
    profile_data = json.loads(Path(args.profile).read_text()) if args.profile else {}
    manifest = prepare_pair(args.left, args.right, args.out, args.order_id,
                            PrintPrepProfile(**profile_data))
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
