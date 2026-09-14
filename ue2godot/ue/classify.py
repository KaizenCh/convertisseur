# -*- coding: utf-8 -*-
"""Actor and component classification with external rule support."""

from typing import Any, Dict, List, Optional
from ue2godot.core.ids import class_name


def classify_actor(actor: Any, rules: Optional[List[Dict[str, str]]] = None) -> str:
    cname = class_name(actor)
    if rules:
        for rule in rules:
            match = rule.get("match", "")
            cat = rule.get("category", "")
            if match and match in cname:
                return cat

    if "LevelInstance" in cname:
        return "level_instance"
    if "StaticMeshActor" in cname:
        return "static_mesh"
    if "Landscape" in cname:
        return "landscape"
    if "Light" in cname:
        return "light"
    if "Niagara" in cname or "Particle" in cname:
        return "vfx"
    if "Decal" in cname:
        return "decal"
    if "Audio" in cname or "Sound" in cname:
        return "audio"

    return "generic"


def classify_component(component: Any, rules: Optional[List[Dict[str, str]]] = None) -> str:
    cname = class_name(component)
    if rules:
        for rule in rules:
            match = rule.get("match", "")
            kind = rule.get("kind", "")
            if match and match in cname:
                return kind

    if "StaticMeshComponent" in cname:
        return "static_mesh"
    if "DecalComponent" in cname:
        return "decal"
    if "NiagaraComponent" in cname or "ParticleSystemComponent" in cname:
        return "vfx"
    if "LightComponent" in cname:
        return "light"
    if "AudioComponent" in cname:
        return "audio"

    return "generic"
