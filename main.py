# -*- coding: utf-8 -*-
"""
UE5 -> GODOT CONSTRUCTOR
PySide6 orchestration UI

IMPORTANT
---------
This application is intentionally an ORCHESTRATOR / UI shell.

It does NOT contain the UE5 conversion algorithms and it does NOT pretend
to execute the current project-specific scripts.

The future integration point is the OrchestratorAdapter / PipelineAdapter.
The UI builds a conversion plan, validates the source at a structural level,
shows the pipeline, and prepares the configuration that future generalized
scripts will consume.

Main UX:
    01 Projet
    02 Sources
    03 Options
    04 Vérification
    05 Reconstruction
    06 Résultat

Design principles:
    - one source of truth: the finalized Unreal map
    - progressive disclosure
    - simple by default
    - technical details on demand
    - contextual inspector
    - no unnecessary nested navigation
    - resumable pipeline state
    - no hard-coded project-specific execution logic
"""

from __future__ import annotations

import json
import os
import sys
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import (
    QObject,
    QPoint,
    QSize,
    Qt,
    QTimer,
    Signal,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QFont,
    QIcon,
    QKeySequence,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import ue2godot
from ue2godot.core.config import ResolvedConfig, deep_merge, compute_config_hash
from ue2godot.orchestrator.step5_copy import copy_step5


APP_NAME = "UE5 → GODOT CONSTRUCTOR"
APP_VERSION = "0.2.0"
CONFIG_EXTENSION = ".gconstructor.json"


# ============================================================================
# PERSISTED CONFIGURATION
# ============================================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "project": {
        "name": "",
        "godot_project": "",
        "config_path": "",
    },
    "source": {
        "unreal_map": "",
        "export_output": "",
        # Étape 0 (ue5_preprocess_detach_all.py + break manuel des LevelInstances)
        # ne laisse aucune trace vérifiable dans le manifest (voir source de vérité,
        # §11). C'est une attestation humaine explicite, pas une case cochée par défaut.
        "preprocessing_confirmed": False,
    },
    "construction": {
        "objective": "maximum",
        "fidelity": "maximum",
        "fidelity_percent": 100,
        "modules": {
            "landscape": True,
            "materials": True,
            "decals": True,
            "lighting": True,
            "vfx": True,
            # Audio n'a jamais reçu de traitement dédié à aucun stade du pipeline
            # (ni export, ni marqueurs de type VFX_MARKERS_NOT_CONVERTED) — ce n'est
            # pas un choix assumé comme Niagara, c'est un point mort. On ne le
            # présente donc pas comme un module fonctionnel activé par défaut.
            "audio": False,
            "level_instances": True,
        },
    },
    "advanced": {
        "transform_policy": "preserve",
        "hierarchy_policy": "preserve",
        "asset_resolution": "strict",
        "validation_mode": "strict",
        "fallback_policy": "report",
        "overwrite_policy": "safe",
        "resume_enabled": True,
        "keep_intermediate": True,
        "verbose_logs": False,
        "godot_executable": "",
        "constructor_script": "",
        # Racine res:// où les assets exportés (Meshes/Decals + les 3 JSON) sont
        # copiés côté projet Godot. Correspond à `godot_asset_root` documenté
        # dans ue5_godot_asset_map.json.
        "godot_asset_root": "UEAssets",
        # Convention d'axe attendue (voir source de vérité §6.4/§12.4) : stockée
        # ici pour être comparée à AXIS_MAP_LABEL trouvé dans l'asset map, au lieu
        # de rester une trace de documentation jamais vérifiée.
        "expected_axis_convention": "(ue.x, ue.z, ue.y) * 0.01",
    },
    "ui": {
        "expert_mode": False,
        "theme": "dark",
    },
}


# ============================================================================
# GENERIC HELPERS
# ============================================================================

def deep_copy_config() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def merge_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """
    Recursive merge.

    Keeps the current schema usable if an older .gconstructor.json is opened.
    """
    result = json.loads(json.dumps(base))

    def merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                merge(dst[key], value)
            else:
                dst[key] = value

    merge(result, incoming)
    return result


def format_number(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,}".replace(",", " ")


def now_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def find_first_existing(root: Path, candidates: list[str]) -> Optional[Path]:
    for relative in candidates:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def recursive_find_json(root: Path, names: set[str]) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []

    result: list[Path] = []
    try:
        for path in root.rglob("*.json"):
            if path.name in names:
                result.append(path)
    except (OSError, PermissionError):
        pass
    return result


def count_glb(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0

    try:
        return sum(1 for _ in root.rglob("*.glb"))
    except (OSError, PermissionError):
        return 0


def count_png(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0

    try:
        return sum(1 for _ in root.rglob("*.png"))
    except (OSError, PermissionError):
        return 0


# ============================================================================
# SOURCE ANALYSIS
# ============================================================================

@dataclass
class SourceInventory:
    source_root: str = ""
    manifest: str = ""
    asset_map: str = ""
    decal_map: str = ""
    mesh_directory: str = ""
    decal_directory: str = ""

    manifest_found: bool = False
    asset_map_found: bool = False
    decal_map_found: bool = False
    mesh_directory_found: bool = False

    mesh_files: int = 0
    decal_files: int = 0

    # geometry / structure (level_manifest_v10.json — voir source de vérité §4)
    placements: Optional[int] = None
    unique_meshes: Optional[int] = None
    unique_skeletal_meshes: Optional[int] = None
    skeletal_mesh_placements: Optional[int] = None
    level_instances: Optional[int] = None
    landscapes: Optional[int] = None
    lights: Optional[int] = None
    vfx: Optional[int] = None
    decals: Optional[int] = None
    audio: Optional[int] = None
    materials: Optional[int] = None

    # Contrat "reconstruction" du manifest — jamais consommé par le
    # reconstructeur .gd lui-même, mais l'orchestrateur, lui, doit le lire.
    ready_for_godot_geometry: Optional[bool] = None
    ready_for_godot_fx: Optional[bool] = None
    reconstruction_blockers: list[str] = field(default_factory=list)

    # asset map (ue5_godot_asset_map.json)
    asset_map_entries: Optional[int] = None
    asset_map_failures: Optional[int] = None
    landscape_entry_present: Optional[bool] = None
    axis_map_label: str = ""

    # decal map (ue5_godot_decal_map.json)
    decal_material_entries: Optional[int] = None
    decal_placement_total: Optional[int] = None

    manifest_version: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_detected(self) -> bool:
        return bool(self.source_root)

    @property
    def core_valid(self) -> bool:
        return (
            self.manifest_found
            and self.asset_map_found
            and self.mesh_directory_found
            and self.mesh_files > 0
        )


class SourceAnalyzer:
    """
    Structural analyzer only.

    It does not convert anything.
    It does not modify Unreal.
    It does not execute project scripts.

    Its purpose is to discover the conversion package / map source and
    summarize what is currently available, using the *real* schema of
    level_manifest_v10.json / ue5_godot_asset_map.json / ue5_godot_decal_map.json
    (see "Source de vérité" §4-5). This replaces an earlier version that
    guessed at generic-sounding field names that never actually existed in
    the manifest — it silently reported "non détecté" on real, valid data.
    """

    MANIFEST_NAMES = {
        "level_manifest_v10.json",
        "level_manifest_v9.json",
        "level_manifest_v8.json",
    }

    ASSET_MAP_NAMES = {
        "ue5_godot_asset_map.json",
    }

    DECAL_MAP_NAMES = {
        "ue5_godot_decal_map.json",
    }

    # Clé synthétique documentée pour l'entrée Landscape ajoutée par
    # unreal_export_landscape.py dans l'asset map (§5).
    LANDSCAPE_SYNTHETIC_KEY = "/AutoTerrain/Landscape.BakedLandscape"

    def analyze(self, source: str) -> SourceInventory:
        root = Path(source).expanduser()

        inventory = SourceInventory(source_root=str(root))

        if not root.exists():
            inventory.errors.append("Le dossier source n'existe pas.")
            return inventory

        if not root.is_dir():
            inventory.errors.append("La source sélectionnée n'est pas un dossier.")
            return inventory

        manifest = find_first_existing(
            root,
            [
                "level_manifest_v10.json",
                "manifest/level_manifest_v10.json",
                "level_manifest.json",
            ],
        )

        if manifest is None:
            matches = recursive_find_json(root, self.MANIFEST_NAMES)
            manifest = matches[0] if matches else None

        if manifest:
            inventory.manifest = str(manifest)
            inventory.manifest_found = True
            self._read_manifest(manifest, inventory)
        else:
            inventory.errors.append(
                "Manifest introuvable. Le constructeur a besoin de la description "
                "complète de la map (level_manifest_v10.json)."
            )

        asset_map = find_first_existing(
            root,
            [
                "ue5_godot_asset_map.json",
                "GodotAssets/ue5_godot_asset_map.json",
                "assets/ue5_godot_asset_map.json",
            ],
        )

        if asset_map is None:
            matches = recursive_find_json(root, self.ASSET_MAP_NAMES)
            asset_map = matches[0] if matches else None

        if asset_map:
            inventory.asset_map = str(asset_map)
            inventory.asset_map_found = True
            self._read_asset_map(asset_map, inventory)
        else:
            inventory.errors.append(
                "Asset map introuvable (ue5_godot_asset_map.json). La résolution "
                "UE5 → GLB ne peut pas être garantie."
            )

        decal_map = find_first_existing(
            root,
            [
                "ue5_godot_decal_map.json",
                "GodotAssets/ue5_godot_decal_map.json",
                "assets/ue5_godot_decal_map.json",
            ],
        )

        if decal_map is None:
            matches = recursive_find_json(root, self.DECAL_MAP_NAMES)
            decal_map = matches[0] if matches else None

        if decal_map:
            inventory.decal_map = str(decal_map)
            inventory.decal_map_found = True
            self._read_decal_map(decal_map, inventory)
        # Absence de decal_map n'est pas systématiquement une erreur : toutes
        # les maps n'ont pas de decals. Le vrai contrôle (cohérence avec le
        # nombre de decals du manifest) se fait dans _cross_validate().

        mesh_dir = self._find_mesh_directory(root)
        if mesh_dir:
            inventory.mesh_directory = str(mesh_dir)
            inventory.mesh_directory_found = True
            inventory.mesh_files = count_glb(mesh_dir)
        else:
            inventory.errors.append(
                "Dossier de meshes introuvable (GodotAssets/Meshes)."
            )

        decal_dir = self._find_decal_directory(root)
        if decal_dir:
            inventory.decal_directory = str(decal_dir)
            inventory.decal_files = count_png(decal_dir)

        self._cross_validate(inventory)

        return inventory

    def _find_mesh_directory(self, root: Path) -> Optional[Path]:
        candidates = [
            root / "GodotAssets" / "Meshes",
            root / "assets" / "Meshes",
            root / "Meshes",
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate

        try:
            for candidate in root.rglob("Meshes"):
                if candidate.is_dir() and any(candidate.glob("*.glb")):
                    return candidate
        except (OSError, PermissionError):
            pass

        return None

    def _find_decal_directory(self, root: Path) -> Optional[Path]:
        candidates = [
            root / "GodotAssets" / "Decals",
            root / "assets" / "Decals",
            root / "Decals",
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate

        try:
            for candidate in root.rglob("Decals"):
                if candidate.is_dir() and any(candidate.glob("*.png")):
                    return candidate
        except (OSError, PermissionError):
            pass

        return None

    # ------------------------------------------------------------------
    # level_manifest_v10.json — voir Source de vérité §4
    # ------------------------------------------------------------------

    def _read_manifest(self, path: Path, inventory: SourceInventory) -> None:
        data = read_json(path)
        if data is None:
            inventory.errors.append("Le manifest existe mais n'est pas lisible (JSON invalide).")
            return

        # manifest_version est resté figé à "10.0" à travers 8 sous-versions
        # de correctifs de comportement (V10.1 → V10.8, voir §9/§12) : ce champ
        # ne doit pas être présenté comme un numéro de version fiable.
        inventory.manifest_version = str(data.get("manifest_version") or "inconnue")

        geometry = data.get("geometry") if isinstance(data.get("geometry"), dict) else {}
        level_instances = data.get("level_instances") if isinstance(data.get("level_instances"), dict) else {}
        materials = data.get("materials") if isinstance(data.get("materials"), dict) else {}
        effects = data.get("effects") if isinstance(data.get("effects"), dict) else {}
        world_features = data.get("world_features") if isinstance(data.get("world_features"), dict) else {}
        reconstruction = data.get("reconstruction") if isinstance(data.get("reconstruction"), dict) else {}

        inventory.unique_meshes = self._len_of(geometry.get("unique_meshes"))
        inventory.placements = self._len_of(geometry.get("placements"))
        inventory.unique_skeletal_meshes = self._len_of(geometry.get("unique_skeletal_meshes"))
        inventory.skeletal_mesh_placements = self._len_of(geometry.get("skeletal_mesh_placements"))

        inventory.level_instances = self._len_of(level_instances.get("placements"))

        inventory.materials = self._len_of(materials.get("unique_materials"))

        inventory.vfx = self._len_of(effects.get("niagara"))
        inventory.decals = self._len_of(effects.get("decals"))

        inventory.landscapes = self._len_of(world_features.get("landscape"))
        inventory.lights = self._len_of(world_features.get("lights"))
        inventory.audio = self._len_of(world_features.get("audio"))

        # Contrat formel "prêt pour Godot" (§4/§11) : le reconstructeur .gd
        # actuel ne le lit jamais, mais l'orchestrateur, lui, le doit.
        if isinstance(reconstruction.get("ready_for_godot_geometry"), bool):
            inventory.ready_for_godot_geometry = reconstruction["ready_for_godot_geometry"]
        if isinstance(reconstruction.get("ready_for_godot_fx"), bool):
            inventory.ready_for_godot_fx = reconstruction["ready_for_godot_fx"]

        for key, value in reconstruction.items():
            if key in ("ready_for_godot_geometry", "ready_for_godot_fx"):
                continue
            if isinstance(value, str) and value:
                inventory.reconstruction_blockers.append(f"{key}: {value}")
            elif isinstance(value, list) and value:
                inventory.reconstruction_blockers.append(
                    f"{key}: {len(value)} élément(s)"
                )

    # ------------------------------------------------------------------
    # ue5_godot_asset_map.json — voir Source de vérité §5
    # ------------------------------------------------------------------

    def _read_asset_map(self, path: Path, inventory: SourceInventory) -> None:
        data = read_json(path)
        if data is None:
            inventory.errors.append("L'asset map existe mais n'est pas lisible (JSON invalide).")
            return

        assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
        inventory.asset_map_entries = len(assets)

        failures = data.get("failures")
        if isinstance(failures, list):
            inventory.asset_map_failures = len(failures)

        inventory.landscape_entry_present = self.LANDSCAPE_SYNTHETIC_KEY in assets

        landscape_entry = assets.get(self.LANDSCAPE_SYNTHETIC_KEY)
        if isinstance(landscape_entry, dict):
            axis_label = landscape_entry.get("axis_map") or landscape_entry.get("axis_map_label")
            if isinstance(axis_label, str):
                inventory.axis_map_label = axis_label

    # ------------------------------------------------------------------
    # ue5_godot_decal_map.json — voir Source de vérité §5
    # ------------------------------------------------------------------

    def _read_decal_map(self, path: Path, inventory: SourceInventory) -> None:
        data = read_json(path)
        if data is None:
            inventory.errors.append("La decal map existe mais n'est pas lisible (JSON invalide).")
            return

        decal_materials = data.get("decal_materials") if isinstance(data.get("decal_materials"), dict) else {}
        inventory.decal_material_entries = len(decal_materials)

        total = data.get("decal_placement_total")
        if isinstance(total, int) and not isinstance(total, bool):
            inventory.decal_placement_total = total

    # ------------------------------------------------------------------
    # Vérifications croisées — automatisent ce qui était documenté comme
    # "contrôle manuel" dans la source de vérité (§1, §9, §10, §12, §13.C.25)
    # ------------------------------------------------------------------

    def _cross_validate(self, inventory: SourceInventory) -> None:
        # 1. Comptage GLB physiques vs. registre déclaré. Le Landscape ajoute
        #    1 entrée synthétique à l'asset map mais son GLB est écrit à la
        #    main (pas forcément dans le même dossier), donc on tolère +1.
        if inventory.asset_map_entries is not None and inventory.mesh_files:
            expected_min = inventory.asset_map_entries - (1 if inventory.landscape_entry_present else 0)
            if inventory.mesh_files < expected_min:
                inventory.errors.append(
                    f"L'asset map référence {inventory.asset_map_entries} assets, mais seulement "
                    f"{inventory.mesh_files} fichiers GLB sont présents sur disque : au moins "
                    f"{expected_min - inventory.mesh_files} export(s) manquant(s) ou déplacé(s)."
                )
            elif inventory.mesh_files > inventory.asset_map_entries:
                inventory.warnings.append(
                    f"{inventory.mesh_files} fichiers GLB présents pour {inventory.asset_map_entries} "
                    "entrées déclarées dans l'asset map — des GLB orphelins (anciens exports) "
                    "traînent peut-être dans le dossier Meshes."
                )

        # 2. Cohérence manifest.geometry.unique_meshes <-> asset_map.assets
        #    (documentée comme contrôle manuel au §1 : "L'orchestrateur devrait
        #    automatiser cette vérification.")
        if inventory.unique_meshes is not None and inventory.asset_map_entries is not None:
            expected = inventory.unique_meshes + (1 if inventory.landscape_entry_present else 0)
            if inventory.asset_map_entries != expected:
                inventory.errors.append(
                    f"Incohérence manifest ↔ asset map : {inventory.unique_meshes} unique_meshes "
                    f"dans le manifest, {inventory.asset_map_entries} entrées dans l'asset map "
                    f"(attendu {expected}). Ordre d'exécution probablement rompu : le manifest ou "
                    "l'export des meshes a peut-être été relancé après une autre étape, écrasant "
                    "un travail précédent (voir §1 de la source de vérité — étapes 1/2 réécrivent "
                    "leur JSON from scratch)."
                )

        if inventory.asset_map_failures:
            inventory.warnings.append(
                f"{inventory.asset_map_failures} échec(s) d'export signalés dans l'asset map."
            )

        # 3. Landscape déclaré dans le manifest mais jamais bake côté asset map.
        if (inventory.landscapes or 0) > 0 and inventory.asset_map_found and not inventory.landscape_entry_present:
            inventory.warnings.append(
                "Le manifest décrit un Landscape mais aucune entrée Landscape "
                f"({self.LANDSCAPE_SYNTHETIC_KEY}) n'est présente dans l'asset map — "
                "unreal_export_landscape.py n'a probablement pas été exécuté (ou a été "
                "exécuté avant une relance du manifest/asset map, qui l'a écrasé)."
            )

        # 4. Decals déclarés dans le manifest mais decal map absente/vide.
        if (inventory.decals or 0) > 0:
            if not inventory.decal_map_found:
                inventory.warnings.append(
                    f"{inventory.decals} decal(s) déclaré(s) dans le manifest mais "
                    "ue5_godot_decal_map.json est introuvable — unreal_export_decals_vfx.py "
                    "n'a probablement pas été exécuté."
                )
            elif not inventory.decal_files:
                inventory.warnings.append(
                    "La decal map est présente mais aucun PNG n'a été trouvé dans le dossier Decals."
                )

        # 5. SkeletalMesh : capturés dans le manifest mais jamais exportés en
        #    GLB par le pipeline actuel (trou connu, §9 point 4).
        if (inventory.unique_skeletal_meshes or 0) > 0:
            inventory.warnings.append(
                f"Le manifest référence {inventory.unique_skeletal_meshes} SkeletalMesh unique(s) "
                f"({inventory.skeletal_mesh_placements or 0} placement(s)), mais l'exporteur actuel "
                "n'exporte que les StaticMesh en GLB : ces éléments seront absents de la scène "
                "reconstruite tant que ce trou n'est pas comblé côté export."
            )

        # 6. Audio : point mort non tranché du pipeline (§13.C.25). On ne le
        #    passe jamais sous silence dans l'interface.
        if (inventory.audio or 0) > 0:
            inventory.notes.append(
                f"{inventory.audio} élément(s) audio détecté(s) dans le manifest. Le pipeline actuel "
                "ne les exporte ni ne les marque comme non convertis : ils seront simplement absents "
                "de la scène reconstruite (choix non tranché, pas une conversion silencieuse)."
            )

        # 7. Contrat "prêt pour Godot" jamais consulté par le reconstructeur
        #    lui-même — l'orchestrateur doit être le premier à s'y fier.
        if inventory.ready_for_godot_geometry is False:
            inventory.errors.append(
                "Le manifest indique lui-même reconstruction.ready_for_godot_geometry = false : "
                "au moins un échec de transform, de mesh manquant, de Level Instance ou d'instance "
                "ISM/HISM a été détecté côté scan Unreal."
            )
        if inventory.ready_for_godot_fx is False:
            inventory.warnings.append(
                "Le manifest indique reconstruction.ready_for_godot_fx = false : les décals/VFX "
                "risquent d'être incomplets ou absents."
            )

        # 8. Convention d'axe : stockée dans l'asset map mais historiquement
        #    jamais comparée à quoi que ce soit (§10, §12.4). On la remonte au
        #    moins comme information affichée plutôt qu'enfouie dans le JSON.
        if inventory.axis_map_label:
            inventory.notes.append(
                f"Convention d'axe déclarée par le Landscape bake : {inventory.axis_map_label}"
            )

        if inventory.manifest_found and inventory.asset_map_found:
            inventory.notes.append(
                f"Manifest détecté : manifest_version={inventory.manifest_version or 'inconnue'} "
                "(ce champ n'a historiquement jamais changé entre correctifs — ne pas s'y fier "
                "seul pour dater le fichier)."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _len_of(value: Any) -> Optional[int]:
        """Longueur d'un dict/list JSON, sans jamais inventer de chiffre."""
        if isinstance(value, (dict, list)):
            return len(value)
        return None


# ============================================================================
# PIPELINE MODEL
# ============================================================================

@dataclass
class PipelineStep:
    key: str
    label: str
    description: str
    state: str = "pending"
    detail: str = ""


PIPELINE_STEPS = [
    PipelineStep(
        "preprocess",
        "Prétraitement UE5",
        "Préparer les éléments nécessaires à la conversion.",
    ),
    PipelineStep(
        "landscape",
        "Landscape",
        "Génération/préparation du terrain et de ses données.",
    ),
    PipelineStep(
        "manifest",
        "Manifest",
        "Produire ou exploiter la description exhaustive de la map.",
    ),
    PipelineStep(
        "meshes",
        "Meshes",
        "Exporter/résoudre les meshes uniques et leur mapping.",
    ),
    PipelineStep(
        "landscape_bake",
        "Landscape Bake",
        "Produire les données de landscape exploitables côté Godot.",
    ),
    PipelineStep(
        "decals",
        "Decals",
        "Exporter les decals et leurs représentations.",
    ),
    PipelineStep(
        "output",
        "Output",
        "Valider le package complet de conversion.",
    ),
    PipelineStep(
        "godot",
        "Godot Constructor",
        "Reconstruire la scène à partir des outputs.",
    ),
]


# ============================================================================
# FUTURE ORCHESTRATOR CONTRACT
# ============================================================================

class OrchestratorAdapter(QObject):
    """
    Integration boundary between the PySide6 UI and the real Godot side.

    The UE5 -> Godot reconstruction ALGORITHM is NOT duplicated here — it
    remains entirely in the existing Godot EditorScript. What this adapter
    DOES really execute (real file I/O, not a simulation) is the part the
    source of truth documents as "étape 5 : copie manuelle" (§1, §11) :
    copying the exported Meshes/Decals + the 3 JSON files into the target
    Godot project, then opening that project. Everything after that point
    (the actual scene construction) is a manual step inside Godot, and is
    presented as such rather than faked.
    """

    log = Signal(str)
    progress = Signal(int)
    step_changed = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.process: Optional[subprocess.Popen] = None
        # Compteurs de la dernière copie réelle — capturés pour que l'UI
        # puisse afficher autre chose qu'un texte générique une fois
        # l'étape terminée (voir MainWindow._on_orchestrator_finished).
        self.last_copy_stats: dict[str, int] = {}

    @staticmethod
    def find_godot(executable: str = "") -> Optional[str]:
        if executable:
            path = Path(executable).expanduser()
            if path.is_file():
                return str(path)
        for candidate in ("godot", "godot4", "Godot", "Godot_v4.5.exe", "Godot_v4.6.exe"):
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def prepare(self, config: dict[str, Any], inventory: SourceInventory) -> dict[str, Any]:
        selected_modules = [
            key for key, enabled in config["construction"]["modules"].items()
            if enabled
        ]
        return {
            "created_at": now_string(),
            "source": inventory.source_root,
            "manifest": inventory.manifest,
            "asset_map": inventory.asset_map,
            "decal_map": inventory.decal_map,
            "mesh_directory": inventory.mesh_directory,
            "decal_directory": inventory.decal_directory,
            "godot_project": config["project"]["godot_project"],
            "godot_asset_root": config["advanced"].get("godot_asset_root", "UEAssets"),
            "constructor_script": config["advanced"].get("constructor_script", ""),
            "godot_executable": config["advanced"].get("godot_executable", ""),
            "objective": config["construction"]["objective"],
            "fidelity": config["construction"]["fidelity"],
            "modules": selected_modules,
            "overwrite_policy": config["advanced"].get("overwrite_policy", "safe"),
            "fallback_policy": config["advanced"].get("fallback_policy", "report"),
            "resume_enabled": bool(config["advanced"].get("resume_enabled", True)),
            "keep_intermediate": bool(config["advanced"].get("keep_intermediate", True)),
            "verbose_logs": bool(config["advanced"].get("verbose_logs", False)),
            "decals_selected": bool(config["construction"]["modules"].get("decals", False)),
        }

    # ------------------------------------------------------------------
    # ÉTAPE 5 — copie des assets exportés vers le projet Godot cible.
    #
    # Documentée comme "entièrement manuelle aujourd'hui" dans la source de
    # vérité (§1, §11). C'est la seule partie du pipeline UE5 -> Godot que
    # cet orchestrateur automatise réellement lui-même (le reste reste dans
    # le .gd, un EditorScript qui ne peut pas être piloté sans intervention
    # manuelle dans l'éditeur — voir §2, §11).
    # ------------------------------------------------------------------

    def copy_assets(self, plan: dict[str, Any]) -> bool:
        project = Path(plan.get("godot_project", "")).expanduser()
        if not project.is_dir() or not (project / "project.godot").is_file():
            self.failed.emit("Projet Godot invalide ou project.godot introuvable.")
            return False

        source_root = plan.get("source", "")
        cfg_dict = {
            "paths": {
                "ue_export_root": source_root,
                "godot_project_root": str(project),
                "godot_asset_root": plan.get("godot_asset_root", "res://UEAssets")
            }
        }
        res_cfg = ResolvedConfig.resolve(cfg_dict, {}, {})

        report = copy_step5(res_cfg)

        copied = report.counters.get("files_copied", 0)
        errors = report.errors

        self.last_copy_stats = {"copied": copied, "skipped": 0, "errors": len(errors)}

        if report.status == "FAILED":
            self.failed.emit(f"Échec de la copie step5: {', '.join(errors)}")
            return False

        self.log.emit(f"Step 5 Copy (ue2godot) terminée : {copied} fichier(s) copié(s) et vérifié(s).")
        return True

    def open_godot(self, plan: dict[str, Any]) -> bool:
        project = Path(plan.get("godot_project", "")).expanduser()
        if not project.is_dir() or not (project / "project.godot").is_file():
            self.failed.emit("Projet Godot invalide ou project.godot introuvable.")
            return False

        godot = self.find_godot(plan.get("godot_executable", ""))
        if not godot:
            self.failed.emit(
                "Godot n'a pas été trouvé. Configure l'exécutable dans les options avancées."
            )
            return False

        self.step_changed.emit("godot_open")

        try:
            self.process = subprocess.Popen(
                [godot, "--editor", "--path", str(project)],
                cwd=str(project),
            )
        except OSError as exc:
            self.failed.emit(f"Impossible de lancer Godot : {exc}")
            return False

        self.log.emit(f"Godot ouvert : {project}")
        script = plan.get("constructor_script", "")
        if script:
            self.log.emit(f"Constructeur sélectionné : {script}")
            self.log.emit(
                "Le constructeur est un EditorScript : son exécution reste effectuée depuis Godot "
                "(FileSystem → clic droit sur le script → Run), comme prévu par le script existant. "
                "Cet orchestrateur ne le lance pas à ta place."
            )
        else:
            self.log.emit(
                "Aucun EditorScript explicite configuré. Le projet Godot a été ouvert sans lancer de logique supplémentaire."
            )
        self.step_changed.emit("done")
        self.finished.emit({"opened": True, "project": str(project)})
        return True

    def execute(self, plan: dict[str, Any]) -> None:
        # Réellement exécuté : la copie des assets (étape 5) puis l'ouverture
        # de Godot. Rien au-delà n'est simulé — la reconstruction de scène
        # elle-même reste celle du constructeur Godot existant.
        if not self.copy_assets(plan):
            return
        self.open_godot(plan)


# ============================================================================
# CUSTOM WIDGETS
# ============================================================================

class PageContainer(QScrollArea):
    """
    Scrollable page that safely exposes a QVBoxLayout-like API.

    This replaces the previous broken page-container implementation.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.content = QWidget()
        self.content.setObjectName("PageContent")

        self._layout = QVBoxLayout(self.content)
        self._layout.setContentsMargins(38, 30, 38, 38)
        self._layout.setSpacing(18)

        self.setWidget(self.content)

    def addWidget(self, widget: QWidget, stretch: int = 0) -> None:
        self._layout.addWidget(widget, stretch)

    def addLayout(self, layout: QVBoxLayout | QHBoxLayout | QGridLayout, stretch: int = 0) -> None:
        self._layout.addLayout(layout, stretch)

    def addStretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)

    def insertWidget(self, index: int, widget: QWidget, stretch: int = 0) -> None:
        self._layout.insertWidget(index, widget, stretch)

    def insertLayout(self, index: int, layout, stretch: int = 0) -> None:
        self._layout.insertLayout(index, layout, stretch)

    def insertStretch(self, index: int, stretch: int = 1) -> None:
        self._layout.insertStretch(index, stretch)

    def addSpacing(self, size: int) -> None:
        self._layout.addSpacing(size)

    def insertSpacing(self, index: int, size: int) -> None:
        self._layout.insertSpacing(index, size)


class Card(QFrame):
    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        if title:
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("CardSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        layout.addLayout(self.body)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self.body.addLayout(layout, stretch)


class StatusPill(QLabel):
    def __init__(self, text: str = "—", state: str = "neutral"):
        super().__init__(text)
        self.setObjectName("StatusPill")
        self.set_state(state)

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class BrowseRow(QWidget):
    path_changed = Signal(str)

    def __init__(
        self,
        label: str,
        placeholder: str = "",
        directory: bool = True,
        allow_files: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)

        self.directory = directory
        self.allow_files = allow_files

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.textChanged.connect(self.path_changed)

        self.button = QPushButton("Parcourir")
        self.button.setObjectName("SecondaryButton")
        self.button.clicked.connect(self.browse)

        layout.addWidget(self.input, 1)
        layout.addWidget(self.button)

        self.label = label

    def browse(self) -> None:
        if self.directory and not self.allow_files:
            path = QFileDialog.getExistingDirectory(
                self,
                f"Sélectionner le dossier — {self.label}",
            )
            if path:
                self.input.setText(path)
        elif self.allow_files:
            path, _ = QFileDialog.getOpenFileName(
                self,
                f"Sélectionner un fichier (.umap, .uproject, manifest) — {self.label}",
                "",
                "Fichiers Unreal / Manifest (*.umap *.uproject *.json);;Dossier / Tous les fichiers (*)"
            )
            if not path:
                path = QFileDialog.getExistingDirectory(
                    self,
                    f"Sélectionner le dossier source — {self.label}",
                )
            if path:
                self.input.setText(path)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                f"Sélectionner — {self.label}",
            )
            if path:
                self.input.setText(path)

    def text(self) -> str:
        return self.input.text().strip()

    def setText(self, value: str) -> None:
        self.input.setText(value)


class StatCard(QFrame):
    def __init__(
        self,
        label: str,
        value: str = "—",
        state: str = "neutral",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("StatCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")

        self.label = QLabel(label)
        self.label.setObjectName("StatLabel")

        layout.addWidget(self.value_label)
        layout.addWidget(self.label)

        self.set_state(state)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class StepIndicator(QFrame):
    clicked = Signal(int)

    def __init__(
        self,
        index: int,
        title: str,
        description: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)

        self.index = index
        self.setObjectName("StepIndicator")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(11)

        self.number = QLabel(str(index + 1))
        self.number.setObjectName("StepNumber")
        self.number.setAlignment(Qt.AlignCenter)
        self.number.setFixedSize(30, 30)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("StepTitle")

        self.description_label = QLabel(description)
        self.description_label.setObjectName("StepDescription")
        self.description_label.setWordWrap(True)

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.description_label)

        layout.addWidget(self.number)
        layout.addLayout(text_layout, 1)

        self.set_state("pending")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class SectionHeader(QWidget):
    def __init__(
        self,
        eyebrow: str,
        title: str,
        description: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        eyebrow_label = QLabel(eyebrow.upper())
        eyebrow_label.setObjectName("Eyebrow")

        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")

        description_label = QLabel(description)
        description_label.setObjectName("PageDescription")
        description_label.setWordWrap(True)

        layout.addWidget(eyebrow_label)
        layout.addWidget(title_label)
        layout.addWidget(description_label)


# ============================================================================
# ADVANCED DIALOG
# ============================================================================

class AdvancedDialog(QDialog):
    def __init__(self, config: dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.setWindowTitle("Options techniques avancées")
        self.resize(720, 620)

        self.config = config

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        intro = QLabel(
            "Ces paramètres sont destinés au diagnostic et au contrôle "
            "fin de la future pipeline. Ils ne sont normalement pas "
            "nécessaires pour une reconstruction standard."
        )
        intro.setObjectName("DialogIntro")
        intro.setWordWrap(True)
        root.addWidget(intro)

        transform_card = Card(
            "Transforms et hiérarchie",
            "Contrôle de la manière dont les données structurelles UE5 seront interprétées.",
        )

        transform_row = QHBoxLayout()
        transform_row.addWidget(QLabel("Transforms"))
        self.transform_combo = QComboBox()
        self.transform_combo.addItem("Conserver les transforms UE5", "preserve")
        self.transform_combo.addItem("Composition contrôlée", "compose")
        self.transform_combo.addItem("Diagnostic uniquement", "diagnostic")
        self.transform_combo.setCurrentIndex(
            max(0, self.transform_combo.findData(
                config["advanced"]["transform_policy"]
            ))
        )
        transform_row.addWidget(self.transform_combo, 1)

        hierarchy_row = QHBoxLayout()
        hierarchy_row.addWidget(QLabel("Hiérarchie"))
        self.hierarchy_combo = QComboBox()
        self.hierarchy_combo.addItem("Conserver", "preserve")
        self.hierarchy_combo.addItem("Aplatir", "flatten")
        self.hierarchy_combo.addItem("Diagnostic uniquement", "diagnostic")
        self.hierarchy_combo.setCurrentIndex(
            max(0, self.hierarchy_combo.findData(
                config["advanced"]["hierarchy_policy"]
            ))
        )
        hierarchy_row.addWidget(self.hierarchy_combo, 1)

        transform_card.add_layout(transform_row)
        transform_card.add_layout(hierarchy_row)
        root.addWidget(transform_card)

        assets_card = Card(
            "Résolution des assets",
            "Détermine le comportement lorsqu'un asset référencé par le manifest "
            "ne peut pas être résolu.",
        )

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("Résolution"))
        self.asset_combo = QComboBox()
        self.asset_combo.addItem("Stricte", "strict")
        self.asset_combo.addItem("Tolérante + rapport", "report")
        self.asset_combo.addItem("Best effort", "best_effort")
        self.asset_combo.setCurrentIndex(
            max(0, self.asset_combo.findData(
                config["advanced"]["asset_resolution"]
            ))
        )
        asset_row.addWidget(self.asset_combo, 1)
        assets_card.add_layout(asset_row)

        fallback_row = QHBoxLayout()
        fallback_row.addWidget(QLabel("Fallback"))
        self.fallback_combo = QComboBox()
        self.fallback_combo.addItem("Signaler sans masquer", "report")
        self.fallback_combo.addItem("Autoriser fallback contrôlé", "controlled")
        self.fallback_combo.addItem("Interdire", "none")
        self.fallback_combo.setCurrentIndex(
            max(0, self.fallback_combo.findData(
                config["advanced"]["fallback_policy"]
            ))
        )
        fallback_row.addWidget(self.fallback_combo, 1)
        assets_card.add_layout(fallback_row)

        root.addWidget(assets_card)

        validation_card = Card(
            "Validation et sécurité",
            "Les options ci-dessous influencent le niveau de contrôle avant/après exécution.",
        )

        validation_row = QHBoxLayout()
        validation_row.addWidget(QLabel("Validation"))
        self.validation_combo = QComboBox()
        self.validation_combo.addItem("Stricte", "strict")
        self.validation_combo.addItem("Standard", "standard")
        self.validation_combo.addItem("Diagnostic", "diagnostic")
        self.validation_combo.setCurrentIndex(
            max(0, self.validation_combo.findData(
                config["advanced"]["validation_mode"]
            ))
        )
        validation_row.addWidget(self.validation_combo, 1)
        validation_card.add_layout(validation_row)

        overwrite_row = QHBoxLayout()
        overwrite_row.addWidget(QLabel("Écriture"))
        self.overwrite_combo = QComboBox()
        self.overwrite_combo.addItem("Mode sûr", "safe")
        self.overwrite_combo.addItem("Remplacement contrôlé", "replace")
        self.overwrite_combo.addItem("Forcer", "force")
        self.overwrite_combo.setCurrentIndex(
            max(0, self.overwrite_combo.findData(
                config["advanced"]["overwrite_policy"]
            ))
        )
        overwrite_row.addWidget(self.overwrite_combo, 1)
        validation_card.add_layout(overwrite_row)

        self.resume_check = QCheckBox(
            "Autoriser la reprise d'une pipeline interrompue"
        )
        self.resume_check.setChecked(
            bool(config["advanced"]["resume_enabled"])
        )

        self.intermediate_check = QCheckBox(
            "Conserver les outputs intermédiaires pour diagnostic"
        )
        self.intermediate_check.setChecked(
            bool(config["advanced"]["keep_intermediate"])
        )

        self.verbose_check = QCheckBox(
            "Activer les logs techniques détaillés"
        )
        self.verbose_check.setChecked(
            bool(config["advanced"]["verbose_logs"])
        )

        validation_card.add(self.resume_check)
        validation_card.add(self.intermediate_check)
        validation_card.add(self.verbose_check)

        root.addWidget(validation_card)

        integration_card = Card(
            "Intégration Godot",
            "Ces chemins servent uniquement à ouvrir le projet et à conserver le lien avec le constructeur Godot existant.",
        )

        godot_row = QHBoxLayout()
        godot_row.addWidget(QLabel("Godot"))
        self.godot_executable_input = QLineEdit(
            config["advanced"].get("godot_executable", "")
        )
        self.godot_executable_input.setPlaceholderText(
            "Laisser vide pour utiliser Godot trouvé dans le PATH"
        )
        godot_browse = QPushButton("Parcourir")
        godot_browse.setObjectName("SecondaryButton")
        godot_browse.clicked.connect(self.browse_godot_executable)
        godot_row.addWidget(self.godot_executable_input, 1)
        godot_row.addWidget(godot_browse)
        integration_card.add_layout(godot_row)

        script_row = QHBoxLayout()
        script_row.addWidget(QLabel("Constructeur"))
        self.constructor_script_input = QLineEdit(
            config["advanced"].get("constructor_script", "")
        )
        self.constructor_script_input.setPlaceholderText(
            "Optionnel : chemin du .gd constructeur existant"
        )
        script_browse = QPushButton("Parcourir")
        script_browse.setObjectName("SecondaryButton")
        script_browse.clicked.connect(self.browse_constructor_script)
        script_row.addWidget(self.constructor_script_input, 1)
        script_row.addWidget(script_browse)
        integration_card.add_layout(script_row)
        root.addWidget(integration_card)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def browse_godot_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner l'exécutable Godot", "", "Executable (*.exe);;Tous les fichiers (*)"
        )
        if path:
            self.godot_executable_input.setText(path)

    def browse_constructor_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sélectionner le constructeur Godot", "", "Godot script (*.gd);;Tous les fichiers (*)"
        )
        if path:
            self.constructor_script_input.setText(path)

    def apply(self) -> None:
        self.config["advanced"]["transform_policy"] = (
            self.transform_combo.currentData()
        )
        self.config["advanced"]["hierarchy_policy"] = (
            self.hierarchy_combo.currentData()
        )
        self.config["advanced"]["asset_resolution"] = (
            self.asset_combo.currentData()
        )
        self.config["advanced"]["fallback_policy"] = (
            self.fallback_combo.currentData()
        )
        self.config["advanced"]["validation_mode"] = (
            self.validation_combo.currentData()
        )
        self.config["advanced"]["overwrite_policy"] = (
            self.overwrite_combo.currentData()
        )
        self.config["advanced"]["resume_enabled"] = self.resume_check.isChecked()
        self.config["advanced"]["keep_intermediate"] = (
            self.intermediate_check.isChecked()
        )
        self.config["advanced"]["verbose_logs"] = (
            self.verbose_check.isChecked()
        )
        self.config["advanced"]["godot_executable"] = self.godot_executable_input.text().strip()
        self.config["advanced"]["constructor_script"] = self.constructor_script_input.text().strip()


# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow(QMainWindow):
    PAGE_PROJECT = 0
    PAGE_SOURCES = 1
    PAGE_OPTIONS = 2
    PAGE_VALIDATION = 3
    PAGE_RECONSTRUCTION = 4
    PAGE_RESULTS = 5

    def __init__(self):
        super().__init__()

        self.config = deep_copy_config()
        self.inventory = SourceInventory()
        self.plan: dict[str, Any] = {}

        # Set by run_validation(); consumed by prepare_reconstruction() to
        # decide whether it's safe to proceed. None means "never run".
        self._last_validation_ok: Optional[bool] = None
        self._last_validation_real_errors: bool = False
        self._last_validation_warnings: bool = False
        self._last_validation_mode: str = "strict"

        self.source_analyzer = SourceAnalyzer()
        self.orchestrator = OrchestratorAdapter(self)

        self.page_titles = [
            "Projet",
            "Sources",
            "Options",
            "Vérification",
            "Reconstruction",
            "Résultat",
        ]

        self.step_indicators: list[StepIndicator] = []

        self.build_ui()
        self.apply_theme()
        self.refresh_all()

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(1420, 900)
        self.setMinimumSize(1120, 720)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self.build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = self.build_sidebar()
        body_layout.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")

        self.project_page = self.build_project_page()
        self.sources_page = self.build_sources_page()
        self.options_page = self.build_options_page()
        self.validation_page = self.build_validation_page()
        self.reconstruction_page = self.build_reconstruction_page()
        self.results_page = self.build_results_page()

        for page in [
            self.project_page,
            self.sources_page,
            self.options_page,
            self.validation_page,
            self.reconstruction_page,
            self.results_page,
        ]:
            self.pages.addWidget(page)

        body_layout.addWidget(self.pages, 1)
        root_layout.addWidget(body, 1)

        self.statusBar().showMessage("Prêt")

        self.pages.currentChanged.connect(self.on_page_changed)

    def build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(72)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(14)

        logo = QLabel("UE5 → GODOT")
        logo.setObjectName("HeaderLogo")

        title = QLabel("CONSTRUCTOR")
        title.setObjectName("HeaderTitle")

        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addStretch()

        self.header_state = StatusPill("Aucune source", "neutral")
        layout.addWidget(self.header_state)

        self.expert_button = QPushButton("Mode expert")
        self.expert_button.setCheckable(True)
        self.expert_button.clicked.connect(self.toggle_expert_mode)
        layout.addWidget(self.expert_button)

        self.theme_button = QPushButton("☀")
        self.theme_button.setObjectName("IconButton")
        self.theme_button.setFixedSize(38, 38)
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)

        return header

    def build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(290)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 18)
        layout.setSpacing(6)

        for index, title in enumerate(self.page_titles):
            descriptions = [
                "Projet cible",
                "Map Unreal",
                "Objectif et fidélité",
                "État de la conversion",
                "Exécution",
                "Rapport final",
            ]

            indicator = StepIndicator(
                index,
                title,
                descriptions[index],
            )
            indicator.clicked.connect(self.go_to_page)
            self.step_indicators.append(indicator)
            layout.addWidget(indicator)

        layout.addStretch()

        footer = QLabel(
            f"{APP_NAME}\n"
            f"UI / Orchestrateur {APP_VERSION}\n"
            "Les scripts de conversion seront branchés séparément."
        )
        footer.setObjectName("SidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)

        return sidebar

    # ------------------------------------------------------------------
    # PAGE 1 — PROJECT
    # ------------------------------------------------------------------

    def build_project_page(self) -> QWidget:
        page = PageContainer()

        page.addWidget(
            SectionHeader(
                "01 / Projet",
                "Préparer le projet Godot",
                "Définis uniquement la destination. La logique de conversion "
                "reste séparée de cette interface.",
            )
        )

        card = Card(
            "Projet Godot",
            "Le projet cible sera détecté et vérifié avant toute reconstruction.",
        )

        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("Nom du projet")
        self.project_name.textChanged.connect(self.on_project_changed)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nom"))
        name_row.addWidget(self.project_name, 1)
        card.add_layout(name_row)

        self.godot_project_row = BrowseRow(
            "Projet Godot",
            "Sélectionner le dossier contenant project.godot",
            directory=True,
        )
        self.godot_project_row.input.textChanged.connect(
            self.on_project_changed
        )
        card.add(self.godot_project_row)

        self.project_status = StatusPill(
            "Projet non détecté",
            "neutral",
        )
        card.add(self.project_status)

        page.addWidget(card)

        overview = Card(
            "État du projet",
            "Résumé intelligent de ce que l'application connaît déjà.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        self.project_summary = QLabel(
            "Aucun projet cible sélectionné."
        )
        self.project_summary.setObjectName("LargeSummary")
        self.project_summary.setWordWrap(True)

        grid.addWidget(self.project_summary, 0, 0, 1, 2)
        overview.add_layout(grid)
        page.addWidget(overview)

        actions = QHBoxLayout()

        self.open_config_button = QPushButton("Ouvrir une configuration")
        self.open_config_button.setObjectName("SecondaryButton")
        self.open_config_button.clicked.connect(self.load_config)

        self.save_config_button = QPushButton("Enregistrer la configuration")
        self.save_config_button.clicked.connect(self.save_config)

        actions.addWidget(self.open_config_button)
        actions.addStretch()
        actions.addWidget(self.save_config_button)

        page.addLayout(actions)
        page.addStretch()

        return page

    # ------------------------------------------------------------------
    # PAGE 2 — SOURCES
    # ------------------------------------------------------------------

    def build_sources_page(self) -> QWidget:
        page = PageContainer()

        page.addWidget(
            SectionHeader(
                "02 / Sources",
                "Une seule source de vérité",
                "Sélectionne la map Unreal finale prête à être convertie. "
                "L'application cherchera ensuite automatiquement les données "
                "générées autour d'elle.",
            )
        )

        source_card = Card(
            "Map Unreal finale",
            "Tu n'as pas à sélectionner séparément le manifest, les meshes, "
            "l'asset map ou les autres outputs.",
        )

        self.source_row = BrowseRow(
            "Map Unreal",
            "Dossier de la map ou fichier (.umap, .uproject, manifest)",
            directory=True,
            allow_files=True,
        )
        self.source_row.input.textChanged.connect(
            self.on_source_changed
        )
        source_card.add(self.source_row)

        source_hint = QLabel(
            "La détection recherche notamment le manifest, l'asset map, "
            "GodotAssets/Meshes et les autres outputs présents dans la source."
        )
        source_hint.setObjectName("Hint")
        source_hint.setWordWrap(True)
        source_card.add(source_hint)

        self.source_status = StatusPill(
            "Aucune source analysée",
            "neutral",
        )
        source_card.add(self.source_status)

        page.addWidget(source_card)

        self.preprocessing_card = Card(
            "Prétraitement UE5 (étape 0)",
            "Aucune donnée du manifest ne permet de prouver que cette étape a été "
            "faite — c'est une confirmation humaine, pas un contrôle automatique.",
        )

        preprocessing_text = QLabel(
            "Avant l'export, <b>ue5_preprocess_detach_all.py</b> doit avoir tourné "
            "(détachement de tous les acteurs enfants, KEEP_WORLD) et toutes les "
            "LevelInstances doivent avoir été cassées manuellement dans le World "
            "Outliner (répéter jusqu'à ce qu'il n'en reste plus). Sans ça, la chaîne "
            "d'attachement recomposée par le manifest est plus fragile."
        )
        preprocessing_text.setObjectName("Hint")
        preprocessing_text.setWordWrap(True)
        self.preprocessing_card.add(preprocessing_text)

        self.preprocessing_check = QCheckBox(
            "Je confirme que le prétraitement (détachement + LevelInstances cassées) "
            "a bien été effectué sur cette map avant l'export."
        )
        self.preprocessing_check.stateChanged.connect(self.on_preprocessing_confirmed_changed)
        self.preprocessing_card.add(self.preprocessing_check)

        page.addWidget(self.preprocessing_card)

        self.inventory_card = Card(
            "Contenu détecté",
            "Les valeurs affichées proviennent de l'analyse réelle de la source. "
            "Aucune valeur inconnue n'est inventée.",
        )

        grid = QGridLayout()
        grid.setSpacing(12)

        self.stat_placements = StatCard("Placements")
        self.stat_meshes = StatCard("Meshes uniques")
        self.stat_levels = StatCard("Level Instances")
        self.stat_landscape = StatCard("Landscapes")
        self.stat_lights = StatCard("Lights")
        self.stat_vfx = StatCard("VFX / Niagara")
        self.stat_decals = StatCard("Decals")
        self.stat_audio = StatCard("Audio")
        self.stat_audio.setToolTip(
            "Le pipeline actuel n'exporte ni ne convertit l'audio : ces éléments "
            "seront absents de la scène reconstruite (point non traité, voir Options)."
        )
        self.stat_skeletal = StatCard("SkeletalMesh")
        self.stat_skeletal.setToolTip(
            "Capturés dans le manifest mais non exportés en GLB par l'exporteur "
            "actuel (limitation connue) : absents de la scène reconstruite."
        )

        stats = [
            self.stat_placements,
            self.stat_meshes,
            self.stat_levels,
            self.stat_landscape,
            self.stat_lights,
            self.stat_vfx,
            self.stat_decals,
            self.stat_audio,
            self.stat_skeletal,
        ]

        for i, stat in enumerate(stats):
            grid.addWidget(stat, i // 4, i % 4)

        self.inventory_card.add_layout(grid)

        self.source_files_summary = QLabel()
        self.source_files_summary.setObjectName("LargeSummary")
        self.source_files_summary.setWordWrap(True)
        self.inventory_card.add(self.source_files_summary)

        page.addWidget(self.inventory_card)

        self.context_source_card = Card(
            "Détails de la source",
            "Informations techniques disponibles sans polluer le flux principal.",
        )

        self.context_source_text = QPlainTextEdit()
        self.context_source_text.setReadOnly(True)
        self.context_source_text.setFixedHeight(190)
        self.context_source_card.add(self.context_source_text)

        page.addWidget(self.context_source_card)

        page.addStretch()
        return page

    # ------------------------------------------------------------------
    # PAGE 3 — OPTIONS
    # ------------------------------------------------------------------

    def build_options_page(self) -> QWidget:
        page = PageContainer()

        page.addWidget(
            SectionHeader(
                "03 / Options",
                "Que veux-tu obtenir ?",
                "Choisis simplement le résultat recherché. Les réglages techniques restent cachés tant qu'ils ne sont pas nécessaires.",
            )
        )

        objective_card = Card(
            "Que veux-tu obtenir ?",
            "L'application adapte ensuite automatiquement la reconstruction à ton choix.",
        )

        self.objective_combo = QComboBox()
        objective_items = [
            (
                "Reconstruction maximale",
                "maximum",
                "Reproduire la scène le plus fidèlement possible.",
            ),
            (
                "Géométrie",
                "geometry",
                "Priorité aux meshes, placements et hiérarchie.",
            ),
            (
                "Environnement",
                "environment",
                "Géométrie + lumières + environnement.",
            ),
            (
                "Visuel",
                "visual",
                "Géométrie + matériaux + éclairage + VFX.",
            ),
            (
                "Personnalisé",
                "custom",
                "Choisir précisément les éléments à reconstruire.",
            ),
        ]

        for label, value, description in objective_items:
            self.objective_combo.addItem(label, value)

        self.objective_combo.currentIndexChanged.connect(
            self.on_objective_changed
        )
        objective_card.add(self.objective_combo)

        self.objective_description = QLabel()
        self.objective_description.setObjectName("LargeSummary")
        self.objective_description.setWordWrap(True)
        objective_card.add(self.objective_description)

        page.addWidget(objective_card)

        fidelity_card = Card(
            "Niveau de fidélité",
            "Choisis le compromis entre temps d'exécution et fidélité.",
        )

        fidelity_row = QHBoxLayout()
        fidelity_row.setSpacing(14)

        self.fidelity_combo = QComboBox()
        self.fidelity_combo.addItem("Rapide", "fast")
        self.fidelity_combo.addItem("Équilibré", "balanced")
        self.fidelity_combo.addItem("Maximum", "maximum")
        self.fidelity_combo.addItem("Personnalisé", "custom")
        self.fidelity_combo.currentIndexChanged.connect(
            self.apply_fidelity_preset
        )
        fidelity_row.addWidget(self.fidelity_combo)

        self.fidelity_slider = QSlider(Qt.Horizontal)
        self.fidelity_slider.setRange(1, 100)
        self.fidelity_slider.setValue(100)
        self.fidelity_slider.setMinimumWidth(240)
        self.fidelity_slider.valueChanged.connect(self.on_fidelity_slider_changed)
        fidelity_row.addWidget(self.fidelity_slider, 1)

        self.fidelity_value = QSpinBox()
        self.fidelity_value.setRange(1, 100)
        self.fidelity_value.setSuffix(" %")
        self.fidelity_value.setValue(100)
        self.fidelity_value.setFixedWidth(86)
        self.fidelity_value.valueChanged.connect(self.on_fidelity_spinbox_changed)
        fidelity_row.addWidget(self.fidelity_value)

        fidelity_card.add_layout(fidelity_row)

        self.fidelity_mode_label = QLabel()
        self.fidelity_mode_label.setObjectName("LargeSummary")
        fidelity_card.add(self.fidelity_mode_label)

        self.fidelity_summary = QLabel()
        self.fidelity_summary.setObjectName("Hint")
        self.fidelity_summary.setWordWrap(True)
        fidelity_card.add(self.fidelity_summary)

        page.addWidget(fidelity_card)

        # The technical module matrix is intentionally hidden for normal users.
        # It becomes visible only for a custom objective or explicit expert mode.
        self.modules_card = Card(
            "Éléments à reconstruire",
            "Sélection technique disponible pour les configurations personnalisées.",
        )
        self.modules_card.setObjectName("AdvancedCard")
        self.module_checks: dict[str, QCheckBox] = {}

        module_definitions = [
            ("landscape", "Landscape", "Terrain et données associées.", True),
            ("materials", "Materials", "Matériaux et apparence.", True),
            ("decals", "Decals", "Textures et placements de decals.", True),
            ("lighting", "Lighting", "Lumières et données d'éclairage.", True),
            ("vfx", "VFX / Niagara", "Effets visuels présents dans les données.", True),
            (
                "audio",
                "Audio",
                "Non implémenté : le pipeline actuel n'exporte ni ne convertit "
                "l'audio (point non tranché, pas un choix assumé). Le sélectionner "
                "ici n'aurait aucun effet.",
                False,
            ),
            ("level_instances", "Level Instances", "Hiérarchie et instances imbriquées.", True),
        ]

        for key, label, description, available in module_definitions:
            row = QHBoxLayout()
            check = QCheckBox(label if available else f"{label} (non disponible)")
            check.setObjectName("ModuleCheck")
            if available:
                check.stateChanged.connect(self.on_module_changed)
            else:
                # Cosmétiquement cochable ne servirait à rien de réel : ce module
                # n'a aucune implémentation dans le pipeline actuel (voir §13.C.25
                # de la source de vérité). Le désactiver évite de faire croire à
                # une conversion audio qui n'existera jamais.
                check.setEnabled(False)
            check.setToolTip(description)
            description_label = QLabel(description)
            description_label.setObjectName("Hint")
            row.addWidget(check)
            row.addWidget(description_label, 1)
            self.module_checks[key] = check
            self.modules_card.add_layout(row)

        page.addWidget(self.modules_card)

        self.advanced_card = Card(
            "Options techniques avancées",
            "Les paramètres internes du constructeur restent séparés de l'expérience normale.",
        )

        advanced_row = QHBoxLayout()
        self.advanced_button = QPushButton("Ouvrir les options avancées")
        self.advanced_button.setObjectName("SecondaryButton")
        self.advanced_button.clicked.connect(self.open_advanced_dialog)

        self.options_summary = QLabel()
        self.options_summary.setObjectName("Hint")
        self.options_summary.setWordWrap(True)

        advanced_row.addWidget(self.advanced_button)
        advanced_row.addWidget(self.options_summary, 1)
        self.advanced_card.add_layout(advanced_row)
        page.addWidget(self.advanced_card)

        page.addStretch()
        return page

    # ------------------------------------------------------------------
    # PAGE 4 — VALIDATION
    # ------------------------------------------------------------------

    def build_validation_page(self) -> QWidget:
        page = PageContainer()

        page.addWidget(
            SectionHeader(
                "04 / Vérification",
                "Est-ce que la conversion peut être préparée correctement ?",
                "Cette étape détecte les problèmes avant de brancher l'exécution réelle.",
            )
        )

        self.validation_header_card = Card(
            "État de la conversion",
            "Les erreurs bloquantes empêchent le lancement futur de la pipeline.",
        )

        self.validation_main_status = StatusPill(
            "Analyse non effectuée",
            "neutral",
        )
        self.validation_header_card.add(
            self.validation_main_status
        )

        self.validation_summary = QLabel()
        self.validation_summary.setObjectName("LargeSummary")
        self.validation_summary.setWordWrap(True)
        self.validation_header_card.add(self.validation_summary)

        page.addWidget(self.validation_header_card)

        self.validation_card = Card(
            "Contrôles",
            "Chaque contrôle indique ce qui est réellement connu.",
        )

        self.validation_list = QVBoxLayout()
        self.validation_list.setSpacing(8)
        self.validation_card.add_layout(self.validation_list)

        page.addWidget(self.validation_card)

        self.validation_impact_card = Card(
            "Impact",
            "Les avertissements expliquent leur conséquence au lieu de simplement afficher un nom technique.",
        )

        self.validation_impact = QLabel()
        self.validation_impact.setObjectName("Hint")
        self.validation_impact.setWordWrap(True)
        self.validation_impact_card.add(self.validation_impact)

        page.addWidget(self.validation_impact_card)

        actions = QHBoxLayout()

        self.recheck_button = QPushButton("Relancer la vérification")
        self.recheck_button.setObjectName("SecondaryButton")
        self.recheck_button.clicked.connect(
            self.run_validation
        )

        self.continue_button = QPushButton(
            "Préparer la reconstruction"
        )
        self.continue_button.clicked.connect(
            self.prepare_reconstruction
        )

        actions.addWidget(self.recheck_button)
        actions.addStretch()
        actions.addWidget(self.continue_button)

        page.addLayout(actions)
        page.addStretch()

        return page

    # ------------------------------------------------------------------
    # PAGE 5 — RECONSTRUCTION
    # ------------------------------------------------------------------

    def build_reconstruction_page(self) -> QWidget:
        page = PageContainer()

        page.addWidget(
            SectionHeader(
                "05 / Reconstruction",
                "Reconstruction",
                "Chaque étape ci-dessous reflète un traitement réellement exécuté par "
                "l'orchestrateur. La reconstruction de scène elle-même reste effectuée "
                "par le constructeur Godot existant (EditorScript), hors de portée de "
                "cette fenêtre — voir l'étape « Constructeur Godot ».",
            )
        )

        self.execution_state_card = Card(
            "Progression",
            "Copie des assets et ouverture du projet Godot : les seules étapes que "
            "cet orchestrateur exécute réellement lui-même.",
        )

        phase_row = QHBoxLayout()
        phase_text = QVBoxLayout()
        self.execution_phase = QLabel("Aucune exécution en cours")
        self.execution_phase.setObjectName("ExecutionPhase")
        self.execution_stage = QLabel("En attente du lancement")
        self.execution_stage.setObjectName("ExecutionStage")
        phase_text.addWidget(self.execution_phase)
        phase_text.addWidget(self.execution_stage)
        phase_row.addLayout(phase_text, 1)

        self.execution_status = StatusPill("En attente", "neutral")
        phase_row.addWidget(self.execution_status, 0, Qt.AlignTop)
        self.execution_state_card.add_layout(phase_row)

        self.execution_progress = QProgressBar()
        self.execution_progress.setRange(0, 100)
        self.execution_progress.setValue(0)
        self.execution_progress.setTextVisible(True)
        self.execution_state_card.add(self.execution_progress)

        counters = QHBoxLayout()
        self.execution_placements = QLabel("— fichiers copiés")
        self.execution_elapsed = QLabel("Temps écoulé    —")
        self.execution_eta = QLabel("")
        for label in (self.execution_placements, self.execution_elapsed, self.execution_eta):
            label.setObjectName("ExecutionMetric")
            counters.addWidget(label)
        counters.addStretch()
        self.execution_state_card.add_layout(counters)

        self.execution_summary = QLabel(
            "Rien n'a encore été exécuté. Prépare le plan depuis la page Vérification."
        )
        self.execution_summary.setObjectName("LargeSummary")
        self.execution_summary.setWordWrap(True)
        self.execution_state_card.add(self.execution_summary)

        page.addWidget(self.execution_state_card)

        pipeline_card = Card(
            "État des étapes",
            "Les 3 premières lignes sont exécutées par cet orchestrateur. La "
            "dernière reste un geste manuel dans l'éditeur Godot (EditorScript).",
        )

        self.pipeline_list = QVBoxLayout()
        self.pipeline_list.setSpacing(6)
        self.pipeline_rows: dict[str, QLabel] = {}

        visual_steps = [
            ("validation", "Vérification exécutée", "○", "neutral"),
            ("plan", "Plan de conversion préparé", "○", "neutral"),
            ("copy_assets", "Copie des assets vers le projet Godot", "○", "neutral"),
            ("godot_open", "Projet Godot ouvert", "○", "neutral"),
            (
                "manual_run",
                "Exécution du constructeur .gd (manuelle, dans Godot)",
                "—",
                "neutral",
            ),
        ]

        for key, label_text, marker, state in visual_steps:
            row = QFrame()
            row.setObjectName("PipelineRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)

            pill = StatusPill(marker, state)
            pill.setFixedWidth(34)
            label = QLabel(label_text)
            label.setObjectName("PipelineLabel")
            row_layout.addWidget(pill)
            row_layout.addWidget(label, 1)
            self.pipeline_rows[key] = pill
            self.pipeline_list.addWidget(row)

        pipeline_card.add_layout(self.pipeline_list)
        page.addWidget(pipeline_card)

        details_card = Card(
            "Détails techniques",
            "Masqués par défaut pour garder la reconstruction lisible.",
        )

        self.execution_detail = QLabel(
            "Aucun détail technique affiché."
        )
        self.execution_detail.setObjectName("Hint")
        self.execution_detail.setWordWrap(True)
        details_card.add(self.execution_detail)

        self.execution_logs = QPlainTextEdit()
        self.execution_logs.setReadOnly(True)
        self.execution_logs.setFixedHeight(190)
        self.execution_logs.setPlainText(
            "[placeholder] Les logs détaillés du constructeur apparaîtront ici lorsqu'ils seront réellement connectés."
        )
        details_card.add(self.execution_logs)
        details_card.setVisible(False)
        self.execution_details_card = details_card
        page.addWidget(details_card)

        actions = QHBoxLayout()
        self.stop_execution_button = QPushButton("Arrêter")
        self.stop_execution_button.setObjectName("DangerButton")
        self.stop_execution_button.setEnabled(False)

        self.execution_details_button = QPushButton("Afficher les détails")
        self.execution_details_button.setObjectName("SecondaryButton")
        self.execution_details_button.setCheckable(True)
        self.execution_details_button.toggled.connect(
            self.execution_details_card.setVisible
        )

        self.execution_placeholder_button = QPushButton("Copier les assets et ouvrir Godot")
        self.execution_placeholder_button.clicked.connect(self.launch_reconstruction)

        actions.addWidget(self.stop_execution_button)
        actions.addWidget(self.execution_details_button)
        actions.addStretch()
        actions.addWidget(self.execution_placeholder_button)

        page.addLayout(actions)
        page.addStretch()
        return page

    # ------------------------------------------------------------------
    # PAGE 6 — RESULTS
    # ------------------------------------------------------------------

    def build_results_page(self) -> QWidget:
        page = PageContainer()

        page.addWidget(
            SectionHeader(
                "06 / Résultat",
                "Rapport de reconstruction",
                "Le rapport final sera alimenté par le moteur connecté et permettra "
                "de comprendre précisément ce qui a été produit.",
            )
        )

        result_card = Card(
            "Résumé",
            "Aucun résultat de reconstruction disponible pour le moment.",
        )

        self.result_status = StatusPill(
            "Pas encore exécuté",
            "neutral",
        )
        result_card.add(self.result_status)

        self.result_summary = QLabel(
            "Les résultats sont remplis uniquement lorsqu'une reconstruction réelle a produit une scène ou un rapport."
        )
        self.result_summary.setObjectName("LargeSummary")
        self.result_summary.setWordWrap(True)
        result_card.add(self.result_summary)

        page.addWidget(result_card)

        stats_card = Card(
            "Comptage",
            "Ces compteurs seront alimentés par le rapport réel.",
        )

        grid = QGridLayout()
        grid.setSpacing(12)

        self.result_created = StatCard("Réussis")
        self.result_warnings = StatCard("Avertissements")
        self.result_errors = StatCard("Erreurs")
        self.result_skipped = StatCard("Ignorés")

        grid.addWidget(self.result_created, 0, 0)
        grid.addWidget(self.result_warnings, 0, 1)
        grid.addWidget(self.result_errors, 0, 2)
        grid.addWidget(self.result_skipped, 0, 3)

        stats_card.add_layout(grid)
        page.addWidget(stats_card)

        fidelity_card = Card(
            "Fidélité",
            "Vue synthétique de ce qui a été conservé ou reconstruit.",
        )

        self.fidelity_report = QLabel(
            "Transforms        —\n"
            "Hierarchy         —\n"
            "Meshes            —\n"
            "Level Instances   —\n"
            "Landscape         —\n"
            "Materials         —\n"
            "Decals            —\n"
            "Lighting          —\n"
            "VFX               —\n"
            "Audio             —"
        )
        self.fidelity_report.setObjectName("ReportText")
        fidelity_card.add(self.fidelity_report)

        page.addWidget(fidelity_card)

        problem_card = Card(
            "Problèmes détectés",
            "Les problèmes devront rester traçables jusqu'à l'élément UE5 concerné.",
        )

        self.result_problems = QPlainTextEdit()
        self.result_problems.setReadOnly(True)
        self.result_problems.setFixedHeight(190)
        self.result_problems.setPlainText(
            "Aucun rapport disponible."
        )
        problem_card.add(self.result_problems)

        page.addWidget(problem_card)

        actions = QHBoxLayout()

        self.open_scene_button = QPushButton("Ouvrir la scène")
        self.open_scene_button.setEnabled(False)
        self.open_scene_button.clicked.connect(self.open_result_scene)

        self.report_button = QPushButton("Exporter le rapport")
        self.report_button.setObjectName("SecondaryButton")
        self.report_button.clicked.connect(self.export_report)

        self.reconstruct_again_button = QPushButton(
            "Préparer une nouvelle reconstruction"
        )
        self.reconstruct_again_button.clicked.connect(
            lambda: self.go_to_page(self.PAGE_OPTIONS)
        )

        actions.addWidget(self.open_scene_button)
        actions.addWidget(self.report_button)
        actions.addStretch()
        actions.addWidget(self.reconstruct_again_button)

        page.addLayout(actions)
        page.addStretch()

        return page

    # ------------------------------------------------------------------
    # PAGE / NAVIGATION
    # ------------------------------------------------------------------

    def go_to_page(self, index: int) -> None:
        """
        Navigate freely through the six UI stages.

        NAVIGATION POLICY
        -----------------
        Navigation is deliberately independent from source availability.
        The user must be able to open and inspect every tab/page even when
        no Unreal map has been selected yet. This keeps the complete UI
        architecture visible and prevents navigation from being confused
        with business validation.

        Therefore this method MUST NOT:
            - require a source before displaying a page;
            - run source validation;
            - start UE5 -> Godot reconstruction;
            - invent or simulate pipeline results.

        Real source detection remains handled by SourceAnalyzer and the
        SourceInventory state. Operations that actually require a valid
        source must perform their own checks (for example validation or
        reconstruction preparation).
        """
        if index < 0 or index >= self.pages.count():
            return

        self.pages.setCurrentIndex(index)

    def on_page_changed(self, index: int) -> None:
        # Navigation is intentionally visual only. Opening a page must never
        # trigger validation, reconstruction preparation, or execution.
        self.update_step_states(index)
        self.statusBar().showMessage(
            f"{index + 1}/6 — {self.page_titles[index]}"
        )

    def update_step_states(self, active_index: int) -> None:
        for i, indicator in enumerate(self.step_indicators):
            if i < active_index:
                indicator.set_state("complete")
            elif i == active_index:
                indicator.set_state("active")
            else:
                indicator.set_state("pending")

        if self.inventory.core_valid:
            self.step_indicators[self.PAGE_SOURCES].set_state("complete")

        if self.config["project"]["godot_project"]:
            self.step_indicators[self.PAGE_PROJECT].set_state("complete")

    # ------------------------------------------------------------------
    # PROJECT
    # ------------------------------------------------------------------

    def on_project_changed(self) -> None:
        self.config["project"]["name"] = self.project_name.text().strip()
        self.config["project"]["godot_project"] = (
            self.godot_project_row.text()
        )
        self.refresh_project_state()

    def refresh_project_state(self) -> None:
        path_text = self.config["project"]["godot_project"]

        if not path_text:
            self.project_status.setText("Projet non sélectionné")
            self.project_status.set_state("neutral")
            self.project_summary.setText(
                "Aucun projet cible sélectionné."
            )
            return

        path = Path(path_text)

        project_file = path / "project.godot"

        if project_file.exists():
            self.project_status.setText("Projet Godot détecté")
            self.project_status.set_state("success")

            project_name = self.config["project"]["name"] or path.name

            self.project_summary.setText(
                f"<b>{project_name}</b><br>"
                f"Projet Godot : <b>{path}</b><br>"
                f"project.godot : ✓ détecté<br>"
                "Destination prête pour la future reconstruction."
            )
        else:
            self.project_status.setText(
                "Dossier sélectionné — project.godot introuvable"
            )
            self.project_status.set_state("warning")

            self.project_summary.setText(
                f"Dossier : <b>{path}</b><br>"
                "Le fichier project.godot n'a pas encore été détecté."
            )

        self.update_step_states(self.pages.currentIndex())

    # ------------------------------------------------------------------
    # SOURCE
    # ------------------------------------------------------------------

    def on_source_changed(self) -> None:
        path = self.source_row.text()

        self.config["source"]["unreal_map"] = path

        if not path:
            self.inventory = SourceInventory()
            self.refresh_source_ui()
            return

        self.inventory = self.source_analyzer.analyze(path)
        self.refresh_source_ui()

    def on_preprocessing_confirmed_changed(self) -> None:
        self.config["source"]["preprocessing_confirmed"] = (
            self.preprocessing_check.isChecked()
        )

    def refresh_source_ui(self) -> None:
        inv = self.inventory

        if not inv.source_root:
            self.source_status.setText("Aucune source analysée")
            self.source_status.set_state("neutral")
            self.header_state.setText("Aucune source")
            self.header_state.set_state("neutral")
        elif inv.core_valid:
            self.source_status.setText("Source détectée et exploitable")
            self.source_status.set_state("success")
            self.header_state.setText("Source valide")
            self.header_state.set_state("success")
        elif inv.errors:
            self.source_status.setText("Source incomplète")
            self.source_status.set_state("danger")
            self.header_state.setText("Source incomplète")
            self.header_state.set_state("danger")
        else:
            self.source_status.setText("Source détectée")
            self.source_status.set_state("warning")
            self.header_state.setText("Source détectée")
            self.header_state.set_state("warning")

        self.set_stat(
            self.stat_placements,
            inv.placements,
        )
        self.set_stat(
            self.stat_meshes,
            inv.unique_meshes,
        )
        self.set_stat(
            self.stat_levels,
            inv.level_instances,
        )
        self.set_stat(
            self.stat_landscape,
            inv.landscapes,
        )
        self.set_stat(
            self.stat_lights,
            inv.lights,
        )
        self.set_stat(
            self.stat_vfx,
            inv.vfx,
        )
        self.set_stat(
            self.stat_decals,
            inv.decals,
        )
        self.set_stat(
            self.stat_audio,
            inv.audio,
        )
        self.set_stat(
            self.stat_skeletal,
            inv.unique_skeletal_meshes,
        )

        if inv.materials is not None:
            material_text = f"{format_number(inv.materials)} materials"
        else:
            material_text = "materials : non détectés"

        decal_map_text = (
            "✓ " + Path(inv.decal_map).name if inv.decal_map
            else ("— absente (aucun decal ?)" if not (inv.decals or 0) else "✕ introuvable")
        )

        ready_bits = []
        if inv.ready_for_godot_geometry is not None:
            ready_bits.append("géométrie " + ("✓" if inv.ready_for_godot_geometry else "✕"))
        if inv.ready_for_godot_fx is not None:
            ready_bits.append("FX " + ("✓" if inv.ready_for_godot_fx else "✕"))
        ready_text = " · ".join(ready_bits) if ready_bits else "non renseigné par le manifest"

        self.source_files_summary.setText(
            f"<b>Manifest :</b> "
            f"{'✓ ' + Path(inv.manifest).name if inv.manifest else '✕ introuvable'}"
            f"<br><b>Asset map :</b> "
            f"{'✓ ' + Path(inv.asset_map).name if inv.asset_map else '✕ introuvable'}"
            f"<br><b>Decal map :</b> {decal_map_text}"
            f"<br><b>Meshes :</b> "
            f"{format_number(inv.mesh_files)} GLB détectés"
            f"<br><b>Prêt pour Godot (déclaré par le manifest) :</b> {ready_text}"
            f"<br><b>Autres :</b> {material_text}"
        )

        details = [
            f"Source : {inv.source_root or '—'}",
            f"Manifest : {inv.manifest or '—'}",
            f"Asset map : {inv.asset_map or '—'}",
            f"Decal map : {inv.decal_map or '—'}",
            f"Meshes : {inv.mesh_directory or '—'}",
            f"Decals (fichiers) : {inv.decal_directory or '—'}",
            "",
            "ERREURS",
        ]

        if inv.errors:
            details.extend(f"✕ {item}" for item in inv.errors)
        else:
            details.append("Aucune erreur structurelle détectée.")

        details.append("")
        details.append("AVERTISSEMENTS")

        if inv.warnings:
            details.extend(f"⚠ {item}" for item in inv.warnings)
        else:
            details.append("Aucun avertissement.")

        details.append("")
        details.append("NOTES")

        if inv.notes:
            details.extend(f"ℹ {item}" for item in inv.notes)
        else:
            details.append("Aucune note.")

        self.context_source_text.setPlainText(
            "\n".join(details)
        )

        self.refresh_project_state()
        self.update_step_states(self.pages.currentIndex())

    @staticmethod
    def set_stat(
        card: StatCard,
        value: Optional[int],
    ) -> None:
        if value is None:
            card.set_value("—")
            card.set_state("neutral")
        else:
            card.set_value(format_number(value))
            card.set_state("success")

    # ------------------------------------------------------------------
    # OPTIONS
    # ------------------------------------------------------------------

    def on_objective_changed(self) -> None:
        objective = self.objective_combo.currentData()

        self.config["construction"]["objective"] = objective

        descriptions = {
            "maximum": (
                "Priorité absolue à la fidélité : structure, géométrie, "
                "landscape, materials, decals, lighting, VFX, audio et "
                "Level Instances lorsque les données sont disponibles."
            ),
            "geometry": (
                "Priorité aux meshes, transforms, placements, hiérarchie "
                "et Level Instances."
            ),
            "environment": (
                "Géométrie + terrain + éclairage + éléments d'environnement."
            ),
            "visual": (
                "Priorité au rendu visuel : géométrie, materials, landscape, "
                "decals, lighting et VFX."
            ),
            "custom": (
                "Les modules sont contrôlés manuellement."
            ),
        }

        self.objective_description.setText(
            descriptions.get(objective, "")
        )

        if objective != "custom":
            self.apply_objective_modules(objective)

        self.refresh_options_summary()
        self.update_advanced_visibility()

    def apply_objective_modules(self, objective: str) -> None:
        presets = {
            "maximum": {
                "landscape": True,
                "materials": True,
                "decals": True,
                "lighting": True,
                "vfx": True,
                # Audio reste False même en "maximum" : ce n'est pas un module
                # désactivable/activable, il n'existe simplement pas encore
                # dans le pipeline (voir le module correspondant, non disponible).
                "audio": False,
                "level_instances": True,
            },
            "geometry": {
                "landscape": True,
                "materials": False,
                "decals": False,
                "lighting": False,
                "vfx": False,
                "audio": False,
                "level_instances": True,
            },
            "environment": {
                "landscape": True,
                "materials": True,
                "decals": False,
                "lighting": True,
                "vfx": False,
                "audio": False,
                "level_instances": True,
            },
            "visual": {
                "landscape": True,
                "materials": True,
                "decals": True,
                "lighting": True,
                "vfx": True,
                "audio": False,
                "level_instances": True,
            },
        }

        values = presets.get(objective)
        if values is None:
            return

        for key, value in values.items():
            checkbox = self.module_checks[key]
            checkbox.blockSignals(True)
            checkbox.setChecked(value)
            checkbox.blockSignals(False)
            self.config["construction"]["modules"][key] = value

    def on_module_changed(self) -> None:
        for key, checkbox in self.module_checks.items():
            self.config["construction"]["modules"][key] = (
                checkbox.isChecked()
            )

        self.config["construction"]["objective"] = "custom"
        index = self.objective_combo.findData("custom")

        if index >= 0:
            self.objective_combo.blockSignals(True)
            self.objective_combo.setCurrentIndex(index)
            self.objective_combo.blockSignals(False)

        self.refresh_options_summary()

    FIDELITY_PRESETS = {
        "fast": 35,
        "balanced": 50,
        "maximum": 100,
    }

    def apply_fidelity_preset(self) -> None:
        """Apply a named fidelity preset without disabling manual control."""
        fidelity = self.fidelity_combo.currentData()

        if fidelity in self.FIDELITY_PRESETS:
            value = self.FIDELITY_PRESETS[fidelity]
            self._set_fidelity_value(value, update_combo=False)
        else:
            # Personnalisé: keep the current slider value.
            self._set_fidelity_value(self.fidelity_slider.value(), update_combo=False)

        self.refresh_options_summary()

    def _fidelity_label_for_value(self, value: int) -> str:
        if value == 35:
            return "Rapide"
        if value == 50:
            return "Équilibré"
        if value == 100:
            return "Maximum"
        return "Personnalisé"

    def _fidelity_description(self, value: int) -> str:
        if value == 35:
            return "Rapide : niveau de traitement réduit, avec une priorité donnée au temps d'exécution."
        if value == 50:
            return "Équilibré : compromis entre fidélité et temps d'exécution."
        if value == 100:
            return "Maximum : priorité maximale à la fidélité et à la conservation des informations disponibles."
        return f"Valeur personnalisée : {value} %. Le niveau de fidélité est réglable librement entre 1 et 100 %."

    def _set_fidelity_value(self, value: int, update_combo: bool = True) -> None:
        value = max(1, min(100, int(value)))

        self.fidelity_slider.blockSignals(True)
        self.fidelity_slider.setValue(value)
        self.fidelity_slider.blockSignals(False)

        self.fidelity_value.blockSignals(True)
        self.fidelity_value.setValue(value)
        self.fidelity_value.blockSignals(False)

        label = self._fidelity_label_for_value(value)
        self.fidelity_mode_label.setText(f"{label} · {value} %")
        self.fidelity_summary.setText(self._fidelity_description(value))

        if update_combo:
            data = {35: "fast", 50: "balanced", 100: "maximum"}.get(value, "custom")
            index = self.fidelity_combo.findData(data)
            if index >= 0:
                self.fidelity_combo.blockSignals(True)
                self.fidelity_combo.setCurrentIndex(index)
                self.fidelity_combo.blockSignals(False)

        fidelity_key = {35: "fast", 50: "balanced", 100: "maximum"}.get(value, "custom")
        self.config["construction"]["fidelity"] = fidelity_key
        self.config["construction"]["fidelity_percent"] = value

        # Keep the existing technical defaults coherent with the visible level.
        if value >= 90:
            self.config["advanced"]["validation_mode"] = "strict"
            self.config["advanced"]["asset_resolution"] = "strict"
            self.config["advanced"]["fallback_policy"] = "report"
        elif value >= 50:
            self.config["advanced"]["validation_mode"] = "standard"
            self.config["advanced"]["asset_resolution"] = "report"
            self.config["advanced"]["fallback_policy"] = "controlled"
        else:
            self.config["advanced"]["validation_mode"] = "standard"
            self.config["advanced"]["asset_resolution"] = "report"
            self.config["advanced"]["fallback_policy"] = "controlled"

    def on_fidelity_slider_changed(self, value: int) -> None:
        self._set_fidelity_value(value, update_combo=True)
        self.refresh_options_summary()

    def on_fidelity_spinbox_changed(self, value: int) -> None:
        self._set_fidelity_value(value, update_combo=True)
        self.refresh_options_summary()

    def refresh_options_summary(self) -> None:
        enabled = [
            checkbox.text()
            for checkbox in self.module_checks.values()
            if checkbox.isChecked()
        ]

        objective = self.objective_combo.currentText()
        fidelity = self.fidelity_combo.currentText()

        if self.config["ui"].get("expert_mode", False) or objective == "Personnalisé":
            self.options_summary.setText(
                f"Objectif : {objective} · Fidélité : {fidelity} · "
                f"Éléments actifs : {', '.join(enabled) if enabled else 'aucun'}"
            )
        else:
            self.options_summary.setText(
                f"Objectif : {objective} · Fidélité : {fidelity} · "
                "Les réglages techniques restent masqués."
            )

        self.update_advanced_visibility()

    def open_advanced_dialog(self) -> None:
        dialog = AdvancedDialog(self.config, self)

        if dialog.exec() == QDialog.Accepted:
            dialog.apply()
            self.refresh_options_summary()

    # ------------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------------

    def clear_validation_rows(self) -> None:
        while self.validation_list.count():
            item = self.validation_list.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def add_validation_row(
        self,
        label: str,
        description: str,
        state: str,
    ) -> None:
        row = QFrame()
        row.setObjectName("ValidationRow")

        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        pill_text = {
            "success": "✓",
            "warning": "⚠",
            "danger": "✕",
            "neutral": "—",
        }.get(state, "—")

        pill = StatusPill(pill_text, state)

        title = QLabel(label)
        title.setObjectName("ValidationTitle")

        detail = QLabel(description)
        detail.setObjectName("Hint")
        detail.setWordWrap(True)

        layout.addWidget(pill)
        layout.addWidget(title)
        layout.addWidget(detail, 1)

        self.validation_list.addWidget(row)

    def run_validation(self) -> bool:
        self.clear_validation_rows()

        errors = list(self.inventory.errors)
        warnings = list(self.inventory.warnings)
        notes = list(self.inventory.notes)

        validation_mode = self.config["advanced"].get("validation_mode", "strict")
        asset_resolution = self.config["advanced"].get("asset_resolution", "strict")

        # asset_resolution="strict" : un échec d'export d'asset n'est plus un
        # simple avertissement, c'est bloquant — un mesh manquant produirait un
        # trou silencieux dans la scène reconstruite (§13.C.24 : ne jamais
        # produire un résultat qui a l'air converti quand il ne l'est pas).
        if asset_resolution == "strict" and (self.inventory.asset_map_failures or 0) > 0:
            failure_text = (
                f"{self.inventory.asset_map_failures} échec(s) d'export signalés dans "
                "l'asset map."
            )
            if failure_text in warnings:
                warnings.remove(failure_text)
            errors.append(
                failure_text + " Résolution stricte : bloquant tant que non résolu "
                "(passer en résolution « Tolérante » ou « Best effort » dans les "
                "options avancées pour continuer malgré ça)."
            )

        # Attestation manuelle de l'étape 0 (voir source de vérité §11 : rien
        # dans le manifest ne peut prouver que ce prétraitement a eu lieu).
        if not self.config["source"].get("preprocessing_confirmed", False):
            errors.append(
                "Le prétraitement UE5 (détachement + LevelInstances cassées) n'a pas "
                "été confirmé sur la page Sources. Rien dans le manifest ne peut le "
                "prouver automatiquement — la case doit être cochée explicitement."
            )

        # Source
        if self.inventory.core_valid:
            self.add_validation_row(
                "Source",
                "Map Unreal détectée et structure de conversion principale disponible.",
                "success",
            )
        else:
            self.add_validation_row(
                "Source",
                "La source principale est incomplète ou inaccessible.",
                "danger",
            )

        # Manifest
        if self.inventory.manifest_found:
            self.add_validation_row(
                "Manifest",
                "Manifest de la map détecté.",
                "success",
            )
        else:
            self.add_validation_row(
                "Manifest",
                "La description exhaustive de la map manque.",
                "danger",
            )

        # Asset map
        if self.inventory.asset_map_found:
            self.add_validation_row(
                "Asset map",
                "Correspondance UE5 → asset exporté détectée.",
                "success",
            )
        else:
            self.add_validation_row(
                "Asset map",
                "Impossible de garantir la résolution des meshes.",
                "danger",
            )

        # GLB
        if self.inventory.mesh_files > 0:
            self.add_validation_row(
                "Meshes",
                f"{format_number(self.inventory.mesh_files)} fichiers GLB détectés.",
                "success",
            )
        else:
            self.add_validation_row(
                "Meshes",
                "Aucun GLB détecté.",
                "danger",
            )

        # Project
        godot_path = self.config["project"]["godot_project"]
        if godot_path and (Path(godot_path) / "project.godot").exists():
            self.add_validation_row(
                "Projet Godot",
                "project.godot détecté dans la destination.",
                "success",
            )
        else:
            self.add_validation_row(
                "Projet Godot",
                "Le projet Godot cible n'est pas encore validé.",
                "warning",
            )
            warnings.append("Projet Godot non validé.")

        # Prétraitement (étape 0) — attestation manuelle
        if self.config["source"].get("preprocessing_confirmed", False):
            self.add_validation_row(
                "Prétraitement",
                "Détachement + LevelInstances cassées confirmés manuellement.",
                "success",
            )
        else:
            self.add_validation_row(
                "Prétraitement",
                "Non confirmé — voir la page Sources.",
                "danger",
            )

        # Contrat "prêt pour Godot" déclaré par le manifest lui-même
        if self.inventory.ready_for_godot_geometry is not None:
            self.add_validation_row(
                "Prêt (géométrie)",
                "reconstruction.ready_for_godot_geometry = "
                f"{self.inventory.ready_for_godot_geometry}",
                "success" if self.inventory.ready_for_godot_geometry else "danger",
            )
        if self.inventory.ready_for_godot_fx is not None:
            self.add_validation_row(
                "Prêt (FX)",
                "reconstruction.ready_for_godot_fx = "
                f"{self.inventory.ready_for_godot_fx}",
                "success" if self.inventory.ready_for_godot_fx else "warning",
            )

        # Selected modules
        for key, checkbox in self.module_checks.items():
            if not checkbox.isChecked():
                continue

            labels = {
                "landscape": "Landscape",
                "materials": "Materials",
                "decals": "Decals",
                "lighting": "Lighting",
                "vfx": "VFX",
                "audio": "Audio",
                "level_instances": "Level Instances",
            }

            count_map = {
                "landscape": self.inventory.landscapes,
                "materials": self.inventory.materials,
                "decals": self.inventory.decals,
                "lighting": self.inventory.lights,
                "vfx": self.inventory.vfx,
                "audio": self.inventory.audio,
                "level_instances": self.inventory.level_instances,
            }

            count = count_map[key]

            if count is None:
                self.add_validation_row(
                    labels[key],
                    "Module sélectionné mais quantité non détectée dans le manifest.",
                    "warning",
                )
            else:
                self.add_validation_row(
                    labels[key],
                    f"{format_number(count)} éléments détectés.",
                    "success",
                )

        real_errors = bool(errors) or not self.inventory.core_valid

        # validation_mode="strict" traite aussi les avertissements comme
        # bloquants (rien ne passe sans être résolu ou explicitement rétrogradé).
        # "standard" ne bloque que sur les vraies erreurs mais exige une
        # confirmation explicite s'il reste des avertissements (voir
        # prepare_reconstruction). "diagnostic" ne bloque jamais : il ne fait
        # que produire le rapport, pour du diagnostic pur.
        if validation_mode == "strict":
            has_blocking_errors = real_errors or bool(warnings)
        elif validation_mode == "diagnostic":
            has_blocking_errors = False
        else:  # "standard"
            has_blocking_errors = real_errors

        # Exposé pour prepare_reconstruction() : distinguer "bloqué par une
        # vraie erreur" de "bloqué seulement parce que le mode est strict".
        self._last_validation_real_errors = real_errors
        self._last_validation_warnings = bool(warnings)
        self._last_validation_mode = validation_mode
        self._last_validation_ok = not has_blocking_errors

        if real_errors:
            self.validation_main_status.setText(
                "Conversion non prête"
            )
            self.validation_main_status.set_state("danger")

            self.validation_summary.setText(
                "Des erreurs bloquantes doivent être corrigées avant de "
                "pouvoir lancer la pipeline réelle."
            )
        elif warnings:
            self.validation_main_status.setText(
                "Bloqué par le mode strict (avertissements)"
                if validation_mode == "strict"
                else "Prêt avec avertissements"
            )
            self.validation_main_status.set_state(
                "danger" if validation_mode == "strict" else "warning"
            )

            self.validation_summary.setText(
                "La structure principale est disponible. Certains éléments "
                "restent à vérifier ou seront potentiellement partiels."
                + (
                    " La validation stricte empêche de continuer tant qu'ils "
                    "ne sont pas résolus ou que le mode n'est pas assoupli."
                    if validation_mode == "strict" else ""
                )
            )
        else:
            self.validation_main_status.setText(
                "Prêt à reconstruire"
            )
            self.validation_main_status.set_state("success")

            self.validation_summary.setText(
                "Les données nécessaires à la reconstruction sélectionnée "
                "sont présentes et cohérentes au niveau de cette interface."
            )

        impact_lines = []

        for error in errors:
            impact_lines.append(
                f"✕ Bloquant : {error}"
            )

        for warning in warnings:
            impact_lines.append(
                f"⚠ Impact à vérifier"
                f"{' (bloquant en mode strict)' if validation_mode == 'strict' else ''}"
                f" : {warning}"
            )

        for note in notes:
            impact_lines.append(
                f"ℹ Information : {note}"
            )

        if not impact_lines:
            impact_lines.append(
                "Aucun problème structurel détecté."
            )

        self.validation_impact.setText(
            "\n".join(impact_lines)
        )

        return not has_blocking_errors

    # ------------------------------------------------------------------
    # RECONSTRUCTION PREPARATION
    # ------------------------------------------------------------------

    def _set_pipeline_row(self, key: str, marker: str, state: str) -> None:
        pill = self.pipeline_rows.get(key)
        if pill is not None:
            pill.setText(marker)
            pill.set_state(state)

    def prepare_reconstruction(self) -> None:
        ok = self.run_validation()
        self._set_pipeline_row(
            "validation", "✓" if ok else "✕", "success" if ok else "danger"
        )

        if self._last_validation_real_errors:
            QMessageBox.critical(
                self,
                "Vérification bloquante",
                "Des erreurs bloquantes empêchent de préparer la reconstruction.\n\n"
                "Consulte la page Vérification pour le détail, corrige la source ou "
                "ajuste les options avancées (résolution des assets, etc.), puis "
                "relance la vérification.",
            )
            self.pages.setCurrentIndex(self.PAGE_VALIDATION)
            return

        if not ok and self._last_validation_mode == "strict" and self._last_validation_warnings:
            answer = QMessageBox.warning(
                self,
                "Mode de validation strict",
                "La validation « stricte » bloque tant qu'il reste des avertissements.\n\n"
                "Tu peux corriger la source, ou passer la validation en mode « Standard » "
                "ou « Diagnostic » dans les options avancées si tu veux avancer malgré ça.",
                QMessageBox.Ok,
            )
            self.pages.setCurrentIndex(self.PAGE_VALIDATION)
            return

        if self._last_validation_warnings and self._last_validation_mode != "diagnostic":
            answer = QMessageBox.question(
                self,
                "Avertissements non bloquants",
                "La vérification a relevé des avertissements (voir la page "
                "Vérification pour le détail). Continuer quand même ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.pages.setCurrentIndex(self.PAGE_VALIDATION)
                return

        self.plan = self.orchestrator.prepare(
            self.config,
            self.inventory,
        )
        self._set_pipeline_row("plan", "✓", "success")

        self.execution_logs.appendPlainText(
            f"[{now_string()}] Plan préparé."
        )
        self.execution_logs.appendPlainText(
            f"[{now_string()}] Source : "
            f"{self.inventory.source_root or '—'}"
        )
        self.execution_logs.appendPlainText(
            f"[{now_string()}] Objectif : "
            f"{self.config['construction']['objective']}"
        )
        self.execution_logs.appendPlainText(
            f"[{now_string()}] Fidélité : "
            f"{self.config['construction']['fidelity']}"
        )

        self.execution_status.setText(
            "Plan prêt — assets à copier"
        )
        self.execution_status.set_state("success")
        self.execution_phase.setText("Plan préparé")
        self.execution_stage.setText("En attente de la copie des assets")

        self.execution_summary.setText(
            "Le plan est préparé à partir des données réellement détectées. Le bouton "
            "ci-dessous copie réellement les assets vers le projet Godot puis l'ouvre — "
            "aucune reconstruction de scène n'est simulée ici."
        )

        self.execution_progress.setValue(0)
        self.execution_placements.setText("— fichiers copiés")
        self.execution_detail.setText(
            "Prêt à copier les assets puis ouvrir le projet Godot cible."
        )

        self.execution_logs.appendPlainText(
            f"[{now_string()}] Aucun faux traitement n'est simulé. Le moteur UE5 → Godot reste celui du constructeur Godot existant."
        )
        self.execution_placeholder_button.setEnabled(bool(self.config["project"].get("godot_project")))

        self.result_status.setText(
            "Plan préparé"
        )
        self.result_status.set_state("success")

    def launch_reconstruction(self) -> None:
        if not self.plan:
            self.prepare_reconstruction()
            if not self.plan:
                return

        self.execution_status.setText("Copie des assets…")
        self.execution_status.set_state("warning")
        self.execution_phase.setText("Copie des assets")
        self.execution_stage.setText("Copie des fichiers vers le projet Godot cible…")
        self.execution_detail.setText(
            "Copie des Meshes/Decals + des 3 JSON vers le projet Godot cible, "
            "puis ouverture de Godot."
        )
        self.execution_progress.setValue(0)
        self.execution_logs.appendPlainText(
            f"[{now_string()}] Copie des assets puis ouverture de Godot pour la reconstruction UE5 → Godot."
        )
        self.execution_placeholder_button.setEnabled(False)

        # (Re)connecte proprement : évite l'accumulation de connexions si
        # l'utilisateur relance après un échec (Godot introuvable, etc.).
        for signal, slot in (
            (self.orchestrator.log, self._on_orchestrator_log),
            (self.orchestrator.progress, self.execution_progress.setValue),
            (self.orchestrator.step_changed, self._on_orchestrator_step_changed),
            (self.orchestrator.finished, self._on_orchestrator_finished),
            (self.orchestrator.failed, self._on_orchestrator_failed),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
            signal.connect(slot)

        self.orchestrator.execute(self.plan)

    def _on_orchestrator_log(self, message: str) -> None:
        self.execution_logs.appendPlainText(f"[{now_string()}] {message}")

    def _on_orchestrator_step_changed(self, step: str) -> None:
        """
        Reflète les étapes RÉELLEMENT exécutées par l'orchestrateur
        (copy_assets -> godot_open -> done) sur les lignes de pipeline déjà
        prévues à cet effet dans la page Reconstruction (self.pipeline_rows).
        """
        labels = {
            "copy_assets": ("Copie des assets", "Copie des fichiers vers le projet Godot cible…"),
            "godot_open": ("Ouverture de Godot", "Lancement de l'éditeur Godot…"),
            "done": ("Godot ouvert", "En attente de l'exécution manuelle du constructeur .gd."),
        }
        phase, stage = labels.get(step, (step, ""))
        self.execution_phase.setText(phase)
        self.execution_stage.setText(stage)

        ordered_steps = ["copy_assets", "godot_open"]
        if step in ordered_steps:
            index = ordered_steps.index(step)
            for previous in ordered_steps[:index]:
                self._set_pipeline_row(previous, "✓", "success")
            self._set_pipeline_row(step, "•", "warning")
        elif step == "done":
            for key in ordered_steps:
                self._set_pipeline_row(key, "✓", "success")

    def _on_orchestrator_finished(self, result: dict[str, Any]) -> None:
        self._set_pipeline_row("copy_assets", "✓", "success")
        self._set_pipeline_row("godot_open", "✓", "success")

        stats = getattr(self.orchestrator, "last_copy_stats", {}) or {}
        copied = stats.get("copied", 0)
        skipped = stats.get("skipped", 0)
        errors = stats.get("errors", 0)
        placements_text = f"{copied} copié(s), {skipped} ignoré(s)"
        if errors:
            placements_text += f", {errors} erreur(s)"
        self.execution_placements.setText(placements_text)

        self.execution_phase.setText("Godot ouvert")
        self.execution_stage.setText("En attente de l'exécution manuelle du constructeur .gd.")
        self.execution_status.setText("Godot ouvert")
        self.execution_status.set_state("success")
        self.execution_progress.setValue(100)
        self.execution_summary.setText(
            "Les assets ont été copiés vers le projet Godot cible, qui est maintenant "
            "ouvert. La logique de reconstruction reste dans le constructeur Godot "
            "existant (EditorScript) — voir l'étape « Constructeur Godot »."
        )
        self.execution_detail.setText(
            "Godot est prêt ; exécute l'EditorScript constructeur depuis Godot "
            "(FileSystem → clic droit sur le script → Run)."
        )
        self.execution_placeholder_button.setEnabled(True)
        self.result_status.setText("Projet Godot ouvert")
        self.result_status.set_state("success")
        self.result_summary.setText(
            "Les assets exportés ont été copiés dans le projet Godot cible et "
            "l'éditeur a été ouvert. Aucun faux résultat de reconstruction n'est "
            "généré : la scène finale dépend de l'exécution du constructeur .gd."
        )
        self.open_scene_button.setEnabled(False)

    def _on_orchestrator_failed(self, message: str) -> None:
        self.execution_status.setText("Échec")
        self.execution_status.set_state("danger")
        self.execution_phase.setText("Interrompu")
        self.execution_stage.setText("Voir le détail ci-dessous.")
        self.execution_detail.setText(message)
        self.execution_logs.appendPlainText(f"[{now_string()}] ERREUR : {message}")
        self.execution_placeholder_button.setEnabled(True)

    def step_is_available(self, key: str) -> bool:
        """
        UI-level availability only.

        This does NOT mean the script exists or has executed.
        """
        modules = self.config["construction"]["modules"]

        if key == "landscape":
            return modules["landscape"]

        if key == "landscape_bake":
            return modules["landscape"]

        if key == "decals":
            return modules["decals"]

        return True

    # ------------------------------------------------------------------
    # RESULTS
    # ------------------------------------------------------------------

    def open_result_scene(self) -> None:
        project = Path(self.config["project"].get("godot_project", "")).expanduser()
        if not project.is_dir():
            QMessageBox.warning(self, "Projet Godot", "Le projet Godot cible n'est pas disponible.")
            return
        scene = project / "Map--_REBUILT.tscn"
        if not scene.is_file():
            QMessageBox.information(
                self,
                "Scène",
                "La scène reconstruite n'existe pas encore. Le constructeur Godot doit d'abord être exécuté.",
            )
            return
        godot = self.orchestrator.find_godot(self.config["advanced"].get("godot_executable", ""))
        if not godot:
            QMessageBox.warning(self, "Godot", "Godot n'a pas été trouvé.")
            return
        try:
            subprocess.Popen([godot, "--editor", "--path", str(project), str(scene)], cwd=str(project))
        except OSError as exc:
            QMessageBox.critical(self, "Godot", f"Impossible d'ouvrir la scène.\n\n{exc}")

    def export_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le rapport", "reconstruction_report.txt", "Texte (*.txt)"
        )
        if not path:
            return
        lines = [
            "UE5 → GODOT CONSTRUCTOR",
            f"Généré : {now_string()}",
            "",
            self.result_summary.text(),
            "",
            self.fidelity_report.text(),
            "",
            "Problèmes :",
            self.result_problems.toPlainText(),
        ]
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            QMessageBox.information(self, "Rapport", f"Rapport exporté :\n{path}")
        except OSError as exc:
            QMessageBox.critical(self, "Rapport", f"Impossible d'exporter le rapport.\n\n{exc}")

    # ------------------------------------------------------------------
    # CONFIG SAVE / LOAD
    # ------------------------------------------------------------------

    def collect_config_from_ui(self) -> None:
        self.config["project"]["name"] = self.project_name.text().strip()
        self.config["project"]["godot_project"] = (
            self.godot_project_row.text()
        )
        self.config["source"]["unreal_map"] = (
            self.source_row.text()
        )

        self.config["construction"]["objective"] = (
            self.objective_combo.currentData()
        )

        self.config["construction"]["fidelity"] = (
            self.fidelity_combo.currentData()
        )

        for key, checkbox in self.module_checks.items():
            self.config["construction"]["modules"][key] = (
                checkbox.isChecked()
            )

    def save_config(self) -> None:
        self.collect_config_from_ui()

        default_name = (
            self.config["project"]["name"].strip()
            or "godot_constructor"
        )

        default_path = (
            Path(self.config["source"]["unreal_map"])
            / f"{default_name}{CONFIG_EXTENSION}"
            if self.config["source"]["unreal_map"]
            else Path.cwd() / f"{default_name}{CONFIG_EXTENSION}"
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la configuration",
            str(default_path),
            "Godot Constructor (*.gconstructor.json)",
        )

        if not path:
            return

        if not path.endswith(CONFIG_EXTENSION):
            path += CONFIG_EXTENSION

        self.config["project"]["config_path"] = path

        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    self.config,
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )

            self.statusBar().showMessage(
                f"Configuration enregistrée : {path}"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Erreur",
                f"Impossible d'enregistrer la configuration.\n\n{exc}",
            )

    def load_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir une configuration",
            "",
            "Godot Constructor (*.gconstructor.json *.json)",
        )

        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as handle:
                incoming = json.load(handle)

            if not isinstance(incoming, dict):
                raise ValueError(
                    "Le fichier ne contient pas une configuration valide."
                )

            self.config = merge_dicts(
                DEFAULT_CONFIG,
                incoming,
            )

            self.config["project"]["config_path"] = path

            self.apply_config_to_ui()

            self.statusBar().showMessage(
                f"Configuration chargée : {path}"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Erreur",
                f"Impossible de charger la configuration.\n\n{exc}",
            )

    def apply_config_to_ui(self) -> None:
        self.project_name.blockSignals(True)
        self.godot_project_row.input.blockSignals(True)
        self.source_row.input.blockSignals(True)
        self.objective_combo.blockSignals(True)
        self.fidelity_combo.blockSignals(True)

        self.project_name.setText(
            self.config["project"]["name"]
        )

        self.godot_project_row.setText(
            self.config["project"]["godot_project"]
        )

        self.source_row.setText(
            self.config["source"]["unreal_map"]
        )

        objective_index = self.objective_combo.findData(
            self.config["construction"]["objective"]
        )

        if objective_index >= 0:
            self.objective_combo.setCurrentIndex(
                objective_index
            )

        fidelity_index = self.fidelity_combo.findData(
            self.config["construction"]["fidelity"]
        )

        if fidelity_index >= 0:
            self.fidelity_combo.setCurrentIndex(
                fidelity_index
            )

        self.project_name.blockSignals(False)
        self.godot_project_row.input.blockSignals(False)
        self.source_row.input.blockSignals(False)
        self.objective_combo.blockSignals(False)
        self.fidelity_combo.blockSignals(False)

        fidelity_percent = int(self.config["construction"].get("fidelity_percent", {
            "fast": 35, "balanced": 50, "maximum": 100
        }.get(self.config["construction"].get("fidelity", "maximum"), 100)))
        self._set_fidelity_value(fidelity_percent, update_combo=True)

        for key, checkbox in self.module_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(
                bool(
                    self.config["construction"]["modules"].get(
                        key,
                        False,
                    )
                )
            )
            checkbox.blockSignals(False)

        self.refresh_project_state()
        self.on_source_changed()
        self.objective_description.setText(
            self.objective_combo.currentText()
        )
        self.refresh_options_summary()
        self.update_advanced_visibility()

    # ------------------------------------------------------------------
    # THEMING
    # ------------------------------------------------------------------

    def toggle_theme(self) -> None:
        current = self.config["ui"].get("theme", "dark")
        self.config["ui"]["theme"] = (
            "light" if current == "dark" else "dark"
        )
        self.apply_theme()

    def toggle_expert_mode(self) -> None:
        enabled = self.expert_button.isChecked()
        self.config["ui"]["expert_mode"] = enabled

        if enabled:
            self.expert_button.setText("Mode expert : ON")
        else:
            self.expert_button.setText("Mode expert")

        self.update_advanced_visibility()
        self.refresh_options_summary()

    def update_advanced_visibility(self) -> None:
        """Keep the normal surface simple while exposing technical controls on demand."""
        expert = bool(self.config["ui"].get("expert_mode", False))
        custom = self.objective_combo.currentData() == "custom"

        # Expert mode is functional: it exposes the technical surface.
        # Normal users see only the objective/fidelity controls.
        self.modules_card.setVisible(expert or custom)
        self.advanced_card.setVisible(expert or custom)
        self.expert_button.setToolTip(
            "Affiche les réglages techniques et les options de contrôle avancées."
            if expert else
            "Activer pour afficher les réglages techniques avancés."
        )

    def apply_theme(self) -> None:
        theme = self.config["ui"].get("theme", "dark")

        if theme == "light":
            self.theme_button.setText("🌙")
            self.setStyleSheet(LIGHT_STYLESHEET)
        else:
            self.theme_button.setText("☀")
            self.setStyleSheet(DARK_STYLESHEET)

    # ------------------------------------------------------------------
    # REFRESH
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        self.refresh_project_state()

        self.preprocessing_check.setChecked(
            bool(self.config["source"].get("preprocessing_confirmed", False))
        )

        if self.config["source"]["unreal_map"]:
            self.source_row.setText(
                self.config["source"]["unreal_map"]
            )
            self.inventory = self.source_analyzer.analyze(
                self.config["source"]["unreal_map"]
            )

        self.refresh_source_ui()

        objective_index = self.objective_combo.findData(
            self.config["construction"]["objective"]
        )

        if objective_index >= 0:
            self.objective_combo.setCurrentIndex(
                objective_index
            )

        fidelity_index = self.fidelity_combo.findData(
            self.config["construction"]["fidelity"]
        )

        if fidelity_index >= 0:
            self.fidelity_combo.setCurrentIndex(
                fidelity_index
            )

        fidelity_percent = int(self.config["construction"].get("fidelity_percent", 100))
        self._set_fidelity_value(fidelity_percent, update_combo=True)

        for key, checkbox in self.module_checks.items():
            checkbox.setChecked(
                bool(
                    self.config["construction"]["modules"].get(
                        key,
                        False,
                    )
                )
            )

        self.refresh_options_summary()
        self.update_step_states(self.PAGE_PROJECT)

    # ------------------------------------------------------------------
    # CLOSE
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.config["ui"].get("expert_mode"):
            answer = QMessageBox.question(
                self,
                "Quitter",
                "Quitter l'application ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                event.ignore()
                return

        event.accept()


# ============================================================================
# STYLES
# ============================================================================

DARK_STYLESHEET = r"""
QWidget {
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
    color: #e7eaf0;
}

QMainWindow,
#Root,
#Pages,
#PageContent {
    background: #111318;
}

#Header {
    background: #191c23;
    border-bottom: 1px solid #2a2e38;
}

#HeaderLogo {
    color: #8fa7ff;
    font-size: 14px;
    font-weight: 700;
}

#HeaderTitle {
    color: #f2f4f8;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 1px;
}

#Sidebar {
    background: #15181e;
    border-right: 1px solid #2a2e38;
}

#SidebarFooter {
    color: #707887;
    font-size: 11px;
    line-height: 1.4;
    padding: 8px;
}

#StepIndicator {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
}

#StepIndicator[state="active"] {
    background: #202633;
    border: 1px solid #34405a;
}

#StepIndicator[state="complete"] {
    background: #1a2420;
}

#StepNumber {
    background: #272c36;
    border-radius: 15px;
    color: #aeb6c5;
    font-weight: 700;
}

#StepIndicator[state="active"] #StepNumber {
    background: #7187ff;
    color: #ffffff;
}

#StepIndicator[state="complete"] #StepNumber {
    background: #3f8f68;
    color: #ffffff;
}

#StepTitle {
    color: #e9ecf2;
    font-weight: 650;
}

#StepDescription {
    color: #747d8d;
    font-size: 11px;
}

#StepIndicator[state="active"] #StepDescription {
    color: #9da8bb;
}

#Eyebrow {
    color: #788cff;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.4px;
}

#PageTitle {
    color: #f3f5f8;
    font-size: 28px;
    font-weight: 700;
}

#PageDescription {
    color: #8c95a5;
    font-size: 13px;
}

#Card {
    background: #191c23;
    border: 1px solid #2a2e38;
    border-radius: 12px;
}

#CardTitle {
    color: #f0f2f6;
    font-size: 16px;
    font-weight: 700;
}

#CardSubtitle {
    color: #7f8898;
    font-size: 12px;
}

QLineEdit,
QComboBox,
QSpinBox,
QPlainTextEdit {
    background: #101218;
    border: 1px solid #303541;
    border-radius: 8px;
    padding: 9px 11px;
    color: #e8ebf0;
    selection-background-color: #586ed6;
}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QPlainTextEdit:focus {
    border: 1px solid #7187ff;
}

QComboBox::drop-down {
    border: none;
    width: 26px;
}

QComboBox QAbstractItemView {
    background: #191c23;
    border: 1px solid #303541;
    selection-background-color: #303a5d;
    color: #e8ebf0;
}

QPushButton {
    background: #6f83f4;
    border: 1px solid #7f91ff;
    border-radius: 8px;
    color: white;
    font-weight: 650;
    padding: 9px 15px;
}

QPushButton:hover {
    background: #7c8fff;
}

QPushButton:pressed {
    background: #5e71d6;
}

QPushButton:disabled {
    background: #292d35;
    border-color: #30343d;
    color: #686f7d;
}

#SecondaryButton {
    background: #20242c;
    border-color: #353b47;
    color: #d7dbe4;
}

#SecondaryButton:hover {
    background: #282d37;
}

#DangerButton {
    background: #47272c;
    border-color: #69383f;
}

#IconButton {
    background: #20242c;
    border-color: #343945;
    padding: 0;
}

#Hint {
    color: #7e8796;
    font-size: 12px;
}

#LargeSummary {
    color: #b9c0cc;
    font-size: 13px;
    line-height: 1.5;
}

#StatusPill {
    border-radius: 7px;
    padding: 6px 10px;
    background: #262a32;
    color: #aab1bd;
    font-weight: 650;
}

#StatusPill[state="success"] {
    background: #183126;
    color: #79d6a7;
}

#StatusPill[state="warning"] {
    background: #3b321c;
    color: #e8c66d;
}

#StatusPill[state="danger"] {
    background: #3f252a;
    color: #ef929d;
}

#StatusPill[state="neutral"] {
    background: #252930;
    color: #a2a9b5;
}

#StatCard {
    background: #15181e;
    border: 1px solid #2b303a;
    border-radius: 10px;
}

#StatValue {
    color: #f1f3f7;
    font-size: 22px;
    font-weight: 750;
}

#StatLabel {
    color: #7f8795;
    font-size: 11px;
}

#StatCard[state="success"] {
    border-color: #2d493d;
}

#ModuleCheck {
    font-weight: 650;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
}

#ValidationRow,
#PipelineRow {
    background: #15181e;
    border: 1px solid #292e37;
    border-radius: 8px;
}

#ValidationTitle,
#PipelineLabel {
    color: #e2e6ed;
    font-weight: 650;
}

QProgressBar {
    background: #101218;
    border: 1px solid #303541;
    border-radius: 7px;
    height: 18px;
    text-align: center;
    color: #e9edf4;
}

QProgressBar::chunk {
    background: #6f83f4;
    border-radius: 6px;
}

#ReportText {
    color: #b9c0cc;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 12px;
}

QScrollBar:vertical {
    background: #111318;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #343a46;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}
"""


LIGHT_STYLESHEET = r"""
QWidget {
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
    color: #20242b;
}

QMainWindow,
#Root,
#Pages,
#PageContent {
    background: #eef0f4;
}

#Header {
    background: #e4e7ec;
    border-bottom: 1px solid #d1d5dc;
}

#HeaderLogo {
    color: #4c61d9;
    font-size: 14px;
    font-weight: 700;
}

#HeaderTitle {
    color: #20242b;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 1px;
}

#Sidebar {
    background: #e8ebef;
    border-right: 1px solid #d1d5dc;
}

#SidebarFooter {
    color: #727985;
    font-size: 11px;
    line-height: 1.4;
    padding: 8px;
}

#StepIndicator {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
}

#StepIndicator[state="active"] {
    background: #f6f7f9;
    border: 1px solid #c9cfda;
}

#StepIndicator[state="complete"] {
    background: #e0ece6;
}

#StepNumber {
    background: #d9dde4;
    border-radius: 15px;
    color: #59616d;
    font-weight: 700;
}

#StepIndicator[state="active"] #StepNumber {
    background: #667be7;
    color: white;
}

#StepIndicator[state="complete"] #StepNumber {
    background: #4e966f;
    color: white;
}

#StepTitle {
    color: #252a31;
    font-weight: 650;
}

#StepDescription {
    color: #737b87;
    font-size: 11px;
}

#Eyebrow {
    color: #5369df;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.4px;
}

#PageTitle {
    color: #20242b;
    font-size: 28px;
    font-weight: 700;
}

#PageDescription {
    color: #68717e;
    font-size: 13px;
}

#Card {
    background: #f7f8fa;
    border: 1px solid #d5d9e0;
    border-radius: 12px;
}

#CardTitle {
    color: #252a31;
    font-size: 16px;
    font-weight: 700;
}

#CardSubtitle {
    color: #747c88;
    font-size: 12px;
}

QLineEdit,
QComboBox,
QSpinBox,
QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #cbd0d8;
    border-radius: 8px;
    padding: 9px 11px;
    color: #252a31;
    selection-background-color: #7184e6;
}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QPlainTextEdit:focus {
    border: 1px solid #667be7;
}

QComboBox::drop-down {
    border: none;
    width: 26px;
}

QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #cbd0d8;
    selection-background-color: #e1e5f7;
    color: #252a31;
}

QPushButton {
    background: #667be7;
    border: 1px solid #7386eb;
    border-radius: 8px;
    color: white;
    font-weight: 650;
    padding: 9px 15px;
}

QPushButton:hover {
    background: #7386eb;
}

QPushButton:pressed {
    background: #586cd0;
}

QPushButton:disabled {
    background: #d9dce2;
    border-color: #d0d4db;
    color: #8b919b;
}

#SecondaryButton {
    background: #edf0f4;
    border-color: #cbd0d8;
    color: #363c45;
}

#SecondaryButton:hover {
    background: #e4e7ec;
}

#DangerButton {
    background: #f2dfe1;
    border-color: #dfb9be;
    color: #8c3c46;
}

#IconButton {
    background: #edf0f4;
    border-color: #cbd0d8;
    color: #343a43;
    padding: 0;
}

#Hint {
    color: #747c88;
    font-size: 12px;
}

#LargeSummary {
    color: #505864;
    font-size: 13px;
    line-height: 1.5;
}

#StatusPill {
    border-radius: 7px;
    padding: 6px 10px;
    background: #e5e8ed;
    color: #616975;
    font-weight: 650;
}

#StatusPill[state="success"] {
    background: #dcece3;
    color: #27704d;
}

#StatusPill[state="warning"] {
    background: #f0e7c9;
    color: #7b641f;
}

#StatusPill[state="danger"] {
    background: #f2dfe1;
    color: #9a414b;
}

#StatusPill[state="neutral"] {
    background: #e4e7ec;
    color: #626a76;
}

#StatCard {
    background: #f0f2f5;
    border: 1px solid #d2d6dd;
    border-radius: 10px;
}

#StatValue {
    color: #242930;
    font-size: 22px;
    font-weight: 750;
}

#StatLabel {
    color: #747c88;
    font-size: 11px;
}

#StatCard[state="success"] {
    border-color: #b9d3c4;
}

QCheckBox {
    spacing: 8px;
}

#ValidationRow,
#PipelineRow {
    background: #f0f2f5;
    border: 1px solid #d5d9e0;
    border-radius: 8px;
}

#ValidationTitle,
#PipelineLabel {
    color: #30353d;
    font-weight: 650;
}

QProgressBar {
    background: #ffffff;
    border: 1px solid #cbd0d8;
    border-radius: 7px;
    height: 18px;
    text-align: center;
    color: #343942;
}

QProgressBar::chunk {
    background: #667be7;
    border-radius: 6px;
}

#ReportText {
    color: #505864;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 12px;
}

QScrollBar:vertical {
    background: #eef0f4;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #c4c9d2;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}
"""


# ============================================================================
# ENTRY POINT
# ============================================================================

def main() -> None:
    app = QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()