# §14 — Décisions de conception du framework (session 1 : architecture)

> À annexer à `UE5_TO_GODOT_PIPELINE_SOURCE_OF_TRUTH.md`.
> Chaque décision référence la section du document source qui la motive.
> **Statut d'honnêteté (§13.A.6)** : ce document est une conception *structurelle*.
> Je n'exécute ni Unreal ni Godot ici. Deux hypothèses de comportement runtime sont
> marquées `[À VÉRIFIER EMPIRIQUEMENT]` et ne doivent pas être traitées comme acquises.

---

## 14.0 Décision n°0 — sortir le reconstructeur du mode `EditorScript`

### Problème nu

`ue5_godot_map_constructor_v10_PATCHED.gd` est un `@tool extends EditorScript` lancé
par clic droit → *Run* dans le panneau FileSystem (§3.9.1). Conséquences directes,
toutes listées en §10 :

- aucun orchestrateur externe ne peut le déclencher sans main humaine ;
- il ne rend **aucun code de retour** — « il n'y en a pas, puisque l'exécution se fait
  dans l'éditeur » (§10) ;
- il ne reçoit **aucun argument** : ses ~15 presets VFX, ses 5 `FAIL_ON_*`, ses chemins
  et son nom de scène de sortie sont des constantes de fichier (§3.9.5, §3.9.8, §6.8).

Tant que ce point n'est pas tranché, les §14.1 à §14.3 n'ont pas d'objet : un pipeline
dont la dernière étape exige un clic n'est pas orchestrable.

### Ce qui rend le choix possible

§3.9.7 est le fait décisif : **ce fichier n'utilise aucune API réservée à l'éditeur.**
Il repose entièrement sur `ParticleProcessMaterial`, `GradientTexture2D`, `Decal`,
`GPUParticles3D`, `PackedScene`, `ResourceSaver`, `load()` — toutes disponibles hors
éditeur. La seule chose qui l'attache à l'éditeur, c'est `extends EditorScript` et
son point d'entrée `_run()`. Ce n'est donc **pas** une réécriture fonctionnelle, c'est
un changement de point d'entrée.

### Tranche retenue : script headless `SceneTree`, obtenu par extraction, pas par réécriture

```
addons/ue2godot/
  core/
    map_builder.gd        # class_name MapBuilder extends RefCounted
                          #   func build(cfg: Dictionary) -> Dictionary   (le rapport)
    transform.gd          # _transform_from_v10 / _unreal_rotator_to_basis /
                          #   _convert_basis_ue_to_godot  — LE CŒUR INTOUCHABLE
    geometry_builder.gd   # _build_geometry_placement + ISM/HISM
    decal_builder.gd      # _build_decals  (déjà fusionné, §3.8/§3.9.5)
    vfx_builder.gd        # presets, lois, budget de lumières, embers
    manifest.gd           # chargement + validation des 3 JSON
  entry_headless.gd       # extends SceneTree   <- l'orchestrateur appelle CELUI-CI
  entry_editor.gd         # extends EditorScript <- conservé, appelle MapBuilder
```

**Pourquoi cette forme et pas une simple conversion du fichier en `SceneTree`** :
§13.C.23 est une règle de collaboration explicite — « toute extension du
reconstructeur doit être additive et isolée, jamais une réécriture qui repasse sur du
code déjà validé empiriquement ». Le cœur mathématique (`_unreal_rotator_to_basis`
avec le signe `qy` négatif de V10.7, `_convert_basis_ue_to_godot` à déterminant -1 de
V10.8) a coûté deux conventions fausses avant d'être juste (§7.9, §7.10, §13.A.5).
On le **déplace de fichier sans le retoucher** et on garde l'ancien chemin d'exécution
en parallèle. `entry_editor.gd` reste le filet : si le headless se comporte
différemment, le workflow manuel d'aujourd'hui fonctionne toujours.

**Ce que l'orchestrateur gagne concrètement** :

```bash
# passe 1 — import des assets (obligatoire, voir plus bas)
godot --headless --path <projet_godot> --import

# passe 2 — construction
godot --headless --path <projet_godot> \
      --script res://addons/ue2godot/entry_headless.gd -- \
      --manifest res://ue2godot_in/level_manifest.json \
      --report   <chemin_hôte>/reports/step6_build.json
```

`entry_headless.gd` lit ses arguments via `OS.get_cmdline_user_args()` (ce qui suit
`--`), appelle `MapBuilder.build()`, écrit le rapport JSON, puis `quit(0)` ou
`quit(1)`. C'est exactement le manque pointé en §10 : un code de retour réel **plus**
un fichier de rapport.

### Pourquoi pas les deux autres options

- **Plugin / `EditorPlugin` avec entrée de menu** : améliore l'ergonomie du lancement
  manuel, mais ne supprime pas l'éditeur de la boucle. §11 demande un orchestrateur
  qui « gère tout à partir de là » — un bouton de menu ne répond pas à ça. À garder
  comme confort ultérieur, pas comme solution.
- **Automatisation de l'éditeur Godot (piloter l'UI)** : §10 la mentionne comme
  possibilité (« si elle est disponible et fiable pour ce cas d'usage »). C'est la
  seule option qui ajoute une dépendance fragile là où on vient de démontrer qu'aucune
  API d'éditeur n'est nécessaire (§3.9.7). Écartée.

### Deux prérequis runtime `[À VÉRIFIER EMPIRIQUEMENT]`

1. **L'import des `.glb` doit avoir eu lieu.** En dehors de l'éditeur, `load()` résout
   via les métadonnées d'import (`.godot/imported/`). D'où la passe `--import`
   séparée. Bonne nouvelle sur le mode d'échec : si l'import n'a pas eu lieu, `load()`
   renvoie `null` → le chemin `FAIL_ON_MISSING_GLB` (systémique → abort, §3.9.3) se
   déclenche bruyamment. C'est un échec visible, pas une scène silencieusement vide.
2. **`ResourceSaver.save()` vers `res://` depuis un `SceneTree` headless.** Attendu
   comme fonctionnel quand le binaire tourne sur un répertoire de projet (pas sur un
   `.pck` packagé), à confirmer.

### Protocole d'équivalence à exécuter avant de basculer

§13.A.1 (« mesurer, pas diagnostiquer à l'œil ») et §13.A.5 (« aucun recoupement
théorique ne remplace un test empirique ») s'appliquent directement ici. Avant de
déclarer le headless bon :

1. construire une fois avec `entry_editor.gd` → `ref.tscn` (référence) ;
2. construire avec `entry_headless.gd` sur exactement les mêmes 3 JSON → `head.tscn` ;
3. comparer **mécaniquement** : nombre de nœuds, et pour chaque nœud
   `(nom, classe, Transform3D quantifié à 1e-4, chemin de ressource mesh/matériau)` ;
4. vérifier à l'œil **un objet asymétrique** (escalier), jamais un pilier — §13.A.3 ;
5. vérifier que le compte de nœuds sauvegardés est égal au compte de nœuds construits :
   c'est le test qui attrape un `.owner` mal assigné (§13.B.10), qui ne produit ni
   erreur ni warning, seulement des nœuds absents du `.tscn`.

Tant que (3) n'est pas à zéro écart, on ne supprime rien de l'ancien chemin.

---

## 14.1 Architecture du framework

### La frontière core / profil / run

Une règle unique, testable :

> **Le core connaît des *formats*. Le profil connaît des *packs*. Le run connaît
> *cette map-ci*.**
>
> Test : si la réponse change quand on change de pack d'assets ou de direction
> artistique → **profil**. Si elle change quand on change de version de moteur ou de
> format de fichier → **core**. Si elle change quand on change de map ou de machine →
> **run**.

C'est la formalisation directe du partage fait en §8. Deux cas limites que cette règle
tranche et qui étaient ambigus dans le document source :

- le réarrangement des axes de la boîte de décal (Unreal projette sur +X avec
  `[épaisseur, largeur, hauteur]`, Godot sur -Y avec `size=(l, h, p)`, §3.8) est une
  conversion **de format**, donc **core** — le document le dit déjà explicitement ;
- les `MASK_PARAMETER_NAMES` / `TINT_PARAMETER_NAMES` (§3.6) sont une convention de
  nommage **de pack**, donc **profil**, même si le mécanisme de résolution en cascade
  qui les consomme est core.

### Arborescence

```
ue2godot/
├── core/                         # Python pur — AUCUN `import unreal`, testable hors éditeur
│   ├── result.py                 # Result(value, error) — §6.3, convention imposée partout
│   ├── safe.py                   # safe_call / safe_property / safe_int|float|bool — §6.1
│   ├── ids.py                    # object_path(), stable_id() SHA1 — §6.2
│   ├── registry.py               # AssetRegistry générique (info + usage_count + back-refs) — §6.7
│   ├── axis.py                   # LA source de vérité de la convention d'axe — §6.4, §9.12
│   ├── glb.py                    # writer glTF binaire maison — §6.6
│   ├── png_codec.py              # tel quel, zéro modification — §3.7
│   ├── schema/                   # dataclasses + validateurs des 3 JSON — §4, §5
│   ├── config.py                 # chargement, fusion en couches, hash, gel
│   ├── crosscheck.py             # vérification croisée des 3 JSON — §9.8
│   └── report.py                 # StepReport (contrat commun à toutes les étapes)
│
├── ue/                           # tout ce qui fait `import unreal`
│   ├── session.py                # découverte d'acteurs "V4/V5/V6 éprouvée" — §3.2.2 NE PAS REMPLACER
│   ├── classify.py               # actor_category / component_kind + table externe — §3.2.4, §9.5
│   ├── transforms.py             # actor_transform, component_transform_diagnostic,
│   │                             #   *_via_properties, compose_chain_transform — §3.2.5, §3.2.6
│   ├── raycast.py                # raycast_grid_heights() unifié — §6.5 (voir §14.4, décision H3)
│   ├── rendertarget.py           # bake par matériau temporaire + RT — commun Landscape/décals
│   │                             #   (§3.4 bake_base_color ≡ §3.6 extraction de texture)
│   ├── steps/
│   │   ├── step0_preprocess.py   # ex-ue5_preprocess_detach_all.py — §3.1
│   │   ├── step1_manifest.py     # ex-unreal_export_manifest_v10-8.py — §3.2
│   │   ├── step2_meshes.py       # ex-unreal_export_godot_assets_PATCHED.py — §3.3
│   │   ├── step3_landscape.py    # ex-unreal_export_landscape.py — §3.4
│   │   └── step4_decals_vfx.py   # ex-unreal_export_decals_vfx.py — §3.6
│   └── entry.py                  # SEUL point d'entrée in-editor : lit un run_config.json, dispatch
│
├── godot/addons/ue2godot/        # cf. §14.0
│
├── profiles/
│   ├── _default.json             # cascade générique seule, aucun nom de paramètre deviné
│   └── necropolis.json           # noms de params décals, 12 presets VFX, calibrations torch/candle
│
└── orchestrator/
    ├── pipeline.py               # machine d'états + garde-fous entre étapes
    ├── adapters/ue_remote.py     # transport vers l'éditeur Unreal
    └── adapters/godot_cli.py     # sous-processus Godot headless
```

### Ce qui n'entre pas dans le framework

`auto_terrain_generator_ue55.py` reste dehors, intégralement (§3.5, §8). Le framework
n'en retient que **le contrat**, formulé en §3.5 : *« avant d'exporter le Landscape, le
Landscape doit avoir un matériau qui produit le rendu voulu ; la génération de ce
matériau est hors scope. »* Traduction opérationnelle : `step3_landscape` vérifie
qu'un matériau non-défaut est assigné au Landscape et **avertit** sinon, plutôt que de
baker en silence un damier par défaut.

### Empaquetage — ce qui débloque l'importabilité

Les 9 fichiers actuels sont des scripts autonomes à constantes de module, sans API
(§6.8). Le framework s'installe comme un **vrai package Python sous
`<ProjetUE>/Content/Python/ue2godot/`**, répertoire déjà ajouté au `sys.path` de la
session Python embarquée d'Unreal. `import ue2godot` fonctionne alors sans bricolage.

Effet de bord utile : cela supprime la contrainte §10 « `png_codec.py` doit être
physiquement à côté de `unreal_export_decals_vfx.py` » (chargement par
`exec(compile(...))` sur chemin relatif, §3.6). Le codec devient
`from ue2godot.core import png_codec`, un import normal.

### Signature imposée à chaque étape

```python
def run(cfg: ResolvedConfig) -> StepReport: ...
```

Aucune constante de module, aucun effet de bord à l'import, aucun `main()` implicite.
C'est le « changement structurel n°1 » nommé en §6.8 et §9.1.

---

## 14.2 Schéma de configuration externe

### Problème nu

Les valeurs qui varient d'une map à l'autre sont aujourd'hui dispersées en constantes
dans 6 fichiers, des deux côtés du pipeline (§6.8, §11). Pire, deux d'entre elles
doivent être **identiques** des deux côtés sans qu'aucun code ne le vérifie : la
convention d'axe (§10, §12.4).

### Format et couches

**JSON**, pas YAML ni TOML : GDScript parse nativement le JSON (`JSON.parse_string`) et
n'a pas de parseur YAML en stdlib ; tout le pipeline pivote déjà sur du JSON (§4, §5).
Une dépendance externe côté GDScript serait un point de fragilité gratuit.

Trois couches, fusionnées par *deep merge*, priorité croissante :

```
core/defaults.json      livré avec le framework, jamais édité par l'utilisateur
profiles/<pack>.json    par pack d'assets / direction artistique
runs/<map>.json         par map : chemins, activation d'étapes, surcharges ponctuelles
```

### Le mécanisme qui ferme la classe de bugs §12.4

1. L'orchestrateur résout les trois couches en une **config gelée** (`ResolvedConfig`)
   et calcule `config_hash` (sha256 du JSON canonique).
2. Il génère un `run_id` (uuid4) par exécution du pipeline.
3. `step1_manifest` **écrit la config résolue entière dans le manifeste**
   (`manifest.pipeline.resolved_config`) avec `run_id` et `config_hash`.
4. `step2/3/4` estampillent le même `run_id` + `config_hash` dans `asset_map.json` et
   `decal_map.json`.
5. Côté Godot, `MapBuilder` **ne lit pas un fichier de config séparé** : il lit la
   config embarquée dans le manifeste qu'il est en train de reconstruire.

Deux gains mécaniques, pas documentaires :

- **La convention d'axe ne peut plus diverger** entre les deux moitiés du pipeline
  (§10, §12.4) : il n'y a plus deux valeurs à synchroniser à la main, il y en a une,
  transportée par la donnée elle-même. `MapBuilder` garde une table
  `KNOWN_AXIS_PRESETS` des conventions qu'il *implémente réellement* et **abandonne
  bruyamment** si le manifeste en déclare une autre. C'est la différence entre
  `AXIS_MAP_LABEL` (une trace texte jamais vérifiée, §12.4) et un contrôle actif.
- **L'ordre d'exécution strict devient vérifiable** (§1, §10) : si on relance
  `step1` après `step3`, le manifeste porte un `run_id` neuf tandis que `asset_map`
  garde l'ancien. La divergence est détectée avant la reconstruction, au lieu d'être
  découverte par un terrain manquant dans la scène finale.

### Le schéma

```jsonc
{
  "config_version": "1.0",
  "profile": "necropolis",

  // ── chemins — remplace OUTPUT_PATH, TXT_OUTPUT_PATH, MANIFEST_PATH,
  //    OUTPUT_ROOT, GODOT_ROOT, DECAL_MAP_PATH, "Map--_REBUILT.tscn"
  //    (§3.2, §3.3, §3.4, §3.9.8, §8, §11)
  "paths": {
    "ue_export_root":    "C:/Export",
    "godot_project_root":"D:/Jeux/HorrorCoop",      // §11 : jamais géré aujourd'hui
    "godot_asset_root":  "res://UEAssets",
    "mesh_subdir":       "Meshes",
    "decal_subdir":      "Decals",
    "output_scene":      "res://Maps/Map_REBUILT.tscn",
    "report_dir":        "C:/Export/reports"
  },

  // ── convention d'axe — §6.4, §9.12, §10, §12.4
  //    NON surchargeable côté Godot. Voir décision humaine H4.
  "axis": {
    "preset": "unreal_gltf_exporter",   // seule valeur bénie aujourd'hui
    "map": ["x", "z", "y"],             // godot = (ue.x, ue.z, ue.y)
    "determinant": -1,
    "unit_scale": 0.01,
    "invert_winding": true              // dérivé du déterminant, écrit explicitement
                                        // et ASSERTÉ (§6.4, §10)
  },

  // ── étape 0 — §3.1, §11
  "preprocess": {
    "dry_run": true,
    "break_level_instances": "manual",  // "manual" | "auto_if_available"  (§9.7)
    "write_stamp": true                 // §11 : rien ne prouve aujourd'hui que l'étape 0 a tourné
  },

  // ── classification — §3.2.4, §9.5
  //    LISTE ORDONNÉE, jamais un dict : le plus spécifique d'abord (§13.B.16)
  "classification": {
    "actor_rules":     [ {"match": "LevelInstance", "category": "level_instance"} ],
    "component_rules": [ {"match": "Decal", "kind": "decal"} ],
    "blueprint_detection": { "class_suffix": "_C", "path_prefixes": ["/Game/", "/Plugin"] }
  },

  // ── export de meshes — §3.3, §9.4, §12.6
  "meshes": {
    "export_static": true,
    "export_skeletal": false,           // trou connu, voir décision humaine H5
    "reexport_policy": "skip_existing", // "skip_existing" | "force" | "hash_check"
    "validate_glb_header": true         // §3.3 : taille >= 20 o ET magie b"glTF"
  },

  // ── Landscape — §3.4, §9.6
  "landscape": {
    "enabled": true,
    "grid_resolution": 256,
    "texture_resolution": 2048,
    "max_trace_retries": 12,
    "capture_source": "SCS_BASE_COLOR", // §3.4 : albédo non éclairé, choix délibéré
    "rt_format": "RTF_RGBA8_SRGB",
    "synthetic_ue_path": "/AutoTerrain/Landscape.BakedLandscape",  // §8
    "multi_policy": "primary_only",     // "primary_only" | "all" | "fail"  (voir H6)
    "require_assigned_material": true   // contrat §3.5
  },

  // ── décals — §3.6, §3.9.5, §9.3, §12.8
  //    Ce bloc vit dans le PROFIL, pas dans le run.
  "decals": {
    "enabled": true,
    "material_parameters": {
      "mask":   ["Mask", "Opacity Mask", "Alpha"],
      "tint":   ["Tint 02", "Tint 01", "Color"],
      "normal": ["Normal", "NormalMap"]
    },
    "tint_fallback": [1.0, 1.0, 1.0],   // §3.6 : blanc neutre, visible plutôt que sombre
    "flat_mask_policy": "warn",         // "warn" | "fail"  (§3.6 détection de masque plat)
    "base_size_uu": 256.0,
    "size_scale": 1.0                   // §12.8 : constante inerte aujourd'hui — À BRANCHER
  },

  // ── VFX — §3.9.6, §3.9.8, §9.10, §9.10b, §13.B.15/16
  //    Presets et mots-clés = PROFIL. Budgets et lois = core.
  "vfx": {
    "mode": "substitutes",              // "markers_only" | "substitutes"  (voir H7)
    "marker_root_name": "VFX_MARKERS_NOT_CONVERTED",   // §13.C.24 : le nom porte l'intention
    "category_rules": [                 // ORDONNÉE : "candle" avant "flame" (§13.B.16)
      {"match": "candle", "category": "candle"},
      {"match": "torch",  "category": "torch"},
      {"match": "fire",   "category": "fire"}
    ],
    "presets": { "candle": { /* couleurs, durées de vie, vitesses */ } },
    "max_lights": 24,                   // §13.B.15 : 341 bougies ≠ 341 OmniLight3D
    "light_min_spacing_m": 6.0,
    "use_parcel_shader": false,         // §13.B.12 + §7.12 : défaut sans shader, toujours
    "calibration": {
      "source": "constants",            // "constants" | "mesh_bounds"  (§9.10b)
      "torch_assumed_height_m": 1.40,
      "candle_assumed_height_m": 0.05
    }
  },

  // ── audio — §13.C.25 : champ OBLIGATOIRE, sans valeur par défaut implicite
  "audio": { "policy": null },          // "ignore" | "markers" | "export" — voir H8

  // ── tolérance — les 5 FAIL_ON_* du reconstructeur, §3.9.3
  //    5 catégories distinctes, JAMAIS fusionnées en un flag unique (leçon V10.4)
  "tolerance": {
    "missing_asset_mapping": "abort",   // systémique
    "missing_glb":           "abort",   // systémique
    "transform_failure":     "skip",    // isolé
    "instantiate_failure":   "skip",    // isolé
    "duplicate_placement_id":"skip",    // isolé
    "readiness_policy":      "warn"     // "ignore" | "warn" | "abort"  — voir H2
  },

  "metadata": { "keep_placement_metadata": true }   // §3.8 : traçabilité Godot → Unreal
}
```

### Deux règles de rédaction du schéma

- **Aucune valeur par défaut implicite pour une question jamais tranchée.**
  `audio.policy` vaut `null` et fait échouer la validation de config tant qu'un humain
  ne l'a pas renseignée. C'est la traduction mécanique de §13.C.25 : l'audio n'est pas
  un choix assumé comme Niagara, c'est un oubli reconduit — un défaut silencieux le
  reconduirait une fois de plus.
- **Toute correspondance par sous-chaîne est une liste ordonnée**, jamais un
  dictionnaire. §13.B.16 formule la leçon sur les mots-clés VFX (`NS_candle_flame`
  contient `candle` *et* `flame`), mais c'est exactement le même mode de défaillance
  que la classification par `in` sur les noms de classe pointée comme « le point le
  plus fragile du système » en §3.2.4. Un dict Python conserve l'ordre d'insertion,
  mais ne le *documente* pas comme sémantique ; une liste, si.

---

## 14.3 Le contrat d'invocation de l'orchestrateur

### Problème nu

L'orchestrateur vit hors d'Unreal et hors de Godot. Les étapes 0-4 tournent dans la
session Python embarquée de l'éditeur Unreal (`import unreal`), l'étape 6 dans Godot,
et aucun des scripts actuels n'est ni paramétrable ni importable (§6.8, §9.2, §11).
§9.2 laisse trois transports ouverts côté Unreal : (a) exécution distante, (b) script
de config temporaire, (c) plugin/commandlet invocable en ligne de commande.

### Tranche retenue côté Unreal : (a) exécution distante contre un éditeur ouvert, avec (b) en repli

**Pourquoi pas (c), le commandlet headless** — c'est le choix qui paraît le plus propre
et c'est celui qui casserait le plus discrètement. Deux raisons, toutes deux dans le
document :

- §3.2.2 : la découverte d'acteurs passe par `ObjectIterator` filtré sur
  `actor.get_level() == level`, avec un commentaire bloc **NE PAS REMPLACER** qui a
  survécu à 10 versions majeures. Ce que cette méthode voit dépend de ce qui est
  chargé dans la session.
- §10 : « World Partition limite la garantie du scan au contenu chargé/accessible ».

Un éditeur lancé en commandlet ne charge pas forcément les mêmes cellules qu'un
éditeur où un humain a ouvert la map et navigué. On changerait donc l'environnement
d'exécution de la partie explicitement marquée « à ne jamais reconsidérer sans preuve
nouvelle », pour gagner en automatisation. Mauvais échange à ce stade.

**Transport (a)** : l'exécution Python distante d'Unreal (activée dans
`Project Settings → Python → Enable Remote Execution`) permet à un processus externe
d'envoyer une commande à l'éditeur en cours et d'en récupérer stdout/stderr. La
commande envoyée est minimale et toujours la même forme :

```python
import ue2godot.entry as e; e.run_step("manifest", r"C:/Export/runs/necropolis_01.json")
```

**Transport (b), repli permanent** : si l'exécution distante est indisponible,
l'orchestrateur écrit le même `run_config.json` et **imprime la ligne à coller** dans
la console Python d'Unreal. Le pipeline reste utilisable à 100 %, avec un humain dans
la boucle sur une seule ligne au lieu de six scripts lancés à la main. Ce mode doit
exister dès le premier jour : il rend le framework indépendant de la fiabilité du
transport.

### Tranche retenue côté Godot : sous-processus CLI, deux passes

Voir §14.0 pour les deux commandes. L'orchestrateur récupère un vrai code de retour,
ce qui n'existe pas aujourd'hui (§10).

### La règle qui rend les deux transports équivalents : le rapport d'étape

Le canal de retour d'une exécution distante est du texte sur stdout — peu fiable pour
du structuré, et §10 rappelle qu'on ne peut pas s'en remettre à un code de retour côté
Unreal. Donc : **la source de vérité de l'orchestrateur n'est jamais le canal de
transport, c'est un fichier de rapport sur disque**, plus la présence réelle des
sorties déclarées.

```jsonc
// C:/Export/reports/step1_manifest.json
{
  "step": "manifest",
  "run_id": "…", "config_hash": "…",
  "status": "OK",                    // OK | OK_WITH_WARNINGS | FAILED
  "started_at": "…", "duration_s": 412.7,
  "outputs": [ {"path": "C:/Export/level_manifest.json", "bytes": 88431203, "sha256": "…"} ],
  "counters": { "actors": 5929, "placements": 7876, "unique_meshes": 166,
                "transform_failures": 0 },
  "errors": [], "warnings": []
}
```

C'est la réponse directe à §10 : *« un orchestrateur qui attend un fichier de sortie
doit vérifier sa présence réelle après l'exécution, pas seulement l'absence de code de
retour d'erreur »*.

### La machine d'états et ses garde-fous

```
  ┌─ G0 ─┐                   G0 : tampon de prétraitement présent et postérieur
  │      ▼                         à la dernière modification du niveau — §11
  │   step0 preprocess             (sinon : --allow-unpreprocessed explicite)
  │      ▼
  ├─ G1 ─┤                   G1 : niveau chargé ; World Partition — cellules
  │      ▼                         pertinentes chargées — §10
  │   step1 manifest  ────────► écrit run_id + config_hash + config résolue
  │      ▼
  ├─ G2 ─┤                   G2 : GLTFExporter disponible (`unreal.GLTFExporter`
  │      ▼                         non None) — §10, §11
  │   step2 meshes
  │      ▼
  ├─ G3 ─┤                   G3 : len(asset_map["assets"]) == len(unique_meshes)
  │      ▼                         — automatisation de la vérif MANUELLE de §1/§9.8
  │   step3 landscape  (si landscape.enabled)
  │      ▼
  │   step4 decals_vfx (si decals.enabled)
  │      ▼
  ├─ G4 ─┤                   G4 : run_id identique dans les 3 JSON — détecte
  │      ▼                         mécaniquement une relance de step1/2 après
  │   step5 copy                   step3/4, qui efface leur travail — §1, §10
  │      ▼
  ├─ G5 ─┤                   G5 : readiness — selon tolerance.readiness_policy
  │      ▼                         (décision humaine H2)
  │   step6 build (godot --import puis --script)
  │      ▼
  └─ G6 ─┘                   G6 : le .tscn existe réellement sur disque ET son
                                   compte de nœuds est cohérent avec le rapport —
                                   §10 : pack/save échoue → root.free(), rien écrit
```

**step5 (copie) devient une vraie étape du framework.** §11 constate qu'elle est
« entièrement manuelle aujourd'hui ». L'orchestrateur connaît `paths.ue_export_root`
et `paths.godot_project_root` : il copie les 2 dossiers et les 3 JSON, et vérifie les
sha256 déclarés dans les rapports après copie.

**G3 et G4 sont les deux garde-fous les plus rentables** du lot : ils remplacent les
deux contrôles aujourd'hui purement documentaires (le comptage croisé noté en §1, et
l'avertissement « Landscape EN DERNIER » écrit dans les en-têtes de fichiers).

---

## 14.4 Décisions qui demandent un retour humain

Ces points ne sont pas tranchés ici volontairement. Chacun a des conséquences que la
lecture du code ne suffit pas à arbitrer.

### H1 — Stratégie Blueprint (§9.11, §12.2)

Le manifeste capture la *structure* des composants d'un Blueprint, jamais son
comportement (`blueprint_logic_included: false`). Trois postures possibles :

- **(a) limite assumée** : on documente, on n'ajoute rien. Coût nul.
- **(b) point d'extension** : le profil déclare un mapping `BP_door_C →
  res://scenes/Door.tscn`, et le reconstructeur instancie la scène Godot fournie à la
  main au lieu du mesh nu. Coût moyen, valeur élevée pour les portes/leviers d'un jeu.
- **(c) tentative de conversion de logique** — hors de question, mais à écarter
  explicitement une fois pour que la question ne revienne pas.

**Ce qui doit être mesuré avant de décider** (§13.A.1) : combien de Blueprints
distincts portent réellement une logique qui compte pour le jeu, dans le manifeste
actuel ? `blueprints.actors[]` donne la réponse par comptage, pas par intuition.

*Note séparée* : §12.2 signale que `blueprint_component_detail()` utilise
`component_transform()` **brut**, sans le diagnostic ni le repli propriétés — un Decal
ou un Niagara *à l'intérieur* d'un Blueprint peut donc récupérer `transform: null` sans
jamais passer par les correctifs V10.3/V10.8. Ce point-là n'est pas une décision, c'est
un bug à mesurer puis corriger (§14.5).

### H2 — Fusion du contrat de « readiness » (§9.10c, §12.7, §3.9.2)

Le manifeste calcule `ready_for_godot_geometry` / `ready_for_godot_fx` ; le
reconstructeur ne les lit **jamais** et refait sa propre validation complète.

**Piège à ne pas franchir en tranchant** : ces deux mécanismes ne font pas la même
chose, et les confondre coûterait cher.

| | Portée | Rôle | Origine |
|---|---|---|---|
| `reconstruction.ready_*` | globale | **verdict d'entrée** : « faut-il lancer la reconstruction ? » | manifeste, §3.2.8 |
| `_build_geometry_placement` | par placement | **résilience** : un cas limite connu ne doit pas jeter 99,8 % de bonnes données | `.gd`, §3.9.3 |

Faire du `.gd` un simple consommateur du verdict global (option (a) de §9.10c) est
défendable ; **supprimer sa tolérance par placement ne l'est pas** — c'est exactement
le bug V10.4 (§3.9.3, §7.4) où un flag catch-all avortait toute la reconstruction pour
un seul composant périmé déjà documenté.

Options à arbitrer :
- **(a)** le manifeste porte la vérité, le `.gd` lit le verdict et refuse de démarrer
  si `false` ; il garde ses 5 tolérances par placement telles quelles ;
- **(b)** le `.gd` reste indépendant, et c'est **l'orchestrateur** (gate G5) qui
  compare les deux verdicts et échoue sur divergence ;
- **(c)** `readiness_policy: "warn"` par défaut — on journalise la divergence pendant
  quelques maps avant de décider, sur données réelles.

(c) est le choix conservateur et il est déjà câblé dans le schéma §14.2. À confirmer.

### H3 — Unification du raycast en grille (§6.5)

§6.5 appelle la factorisation « candidat évident ». Deux frictions qui demandent un
arbitrage :

1. **Le sens de la dépendance.** Les deux implémentations sont dans
   `unreal_export_landscape.py` (dans le scope du framework) et
   `auto_terrain_generator_ue55.py` (**explicitement hors scope**, §3.5, §8).
   Factoriser signifie soit faire de l'outil d'authoring un consommateur du framework
   — ce qui l'y rattache alors qu'on vient de l'en exclure — soit assumer la
   duplication. À trancher.
2. **Les deux usages n'ont pas la même sémantique des trous.** L'export Landscape
   traite un « non touché » comme un trou qu'il faut préserver (`build_grid_mesh`
   n'émet un quad que si les 4 coins sont touchés, §3.4) ; le générateur de matériau
   fait une analyse statistique de distribution d'altitudes où un trou n'a pas de
   sens. Une fonction commune doit donc renvoyer **la grille brute avec ses `None`
   préservés**, et laisser chaque appelant décider — ne pas boucher, ne pas
   interpoler. Si la factorisation devait aboutir à une signature qui masque les
   trous, mieux vaut garder deux implémentations.

Forme minimale proposée si l'unification est retenue :
`raycast_grid_heights(bounds, resolution, channel, ignore_seed, max_retries) -> list[Optional[float]]`.

### H4 — La convention d'axe doit-elle rester configurable ? (§6.4, §9.12, §13.A.5)

Tension réelle entre deux exigences du document. §9.12 demande une source de vérité
unique partagée par les deux côtés — d'où le bloc `axis` en config. Mais §13.A.5 est
la « leçon la plus chère de tout ce projet » : la bonne convention ne se déduit pas,
elle s'observe sur la sortie réelle de l'exporteur glTF d'Unreal. Un champ librement
éditable invite précisément au raisonnement qui a produit deux conventions fausses.

Proposition à valider : **configurable, mais avec un seul preset béni**
(`unreal_gltf_exporter`), et une valeur `custom` qui exige de déclarer explicitement
`determinant` et `invert_winding`, et qui **refuse de tourner** sans un test de
validation passé sur un objet asymétrique (§13.A.3). Alternative : figer en dur dans
le core et n'externaliser que le *label* vérifié. À trancher.

### H5 — SkeletalMesh : dans le scope v1 ou non ? (§9.4, §12.6)

Capturés dans le manifeste depuis V10.1, **aucun chemin d'export GLB n'existe nulle
part** dans le pipeline. Sans animation ni retargeting côté Godot, un skeletal mesh
exporté est un mesh statique coûteux. Question de périmètre, pas de technique.

### H6 — Multi-Landscape (§9.6)

Aujourd'hui `landscapes[0]` avec un avertissement. Généraliser implique : une passe de
raycast et un bake par Landscape, la gestion des coutures entre dalles, et un choix
entre une texture par terrain ou un atlas. Coût non trivial. Nécessaire maintenant ou
repoussé ?

### H7 — Le système de substitution VFX est-il du core ou un profil ? (§3.9.6, §3.9.8)

~1000 lignes de substitution visuelle (12 catégories, 8 lois de panache, embers,
budget de lumières) dont §3.9.8 dit que les *valeurs* sont réglées pour ce cimetière,
mais dont les *mécanismes* sont génériques. Deux lectures possibles :

- **core avec presets en profil** : une nouvelle map a des flammes par défaut, réglées
  sur des valeurs génériques possiblement fausses pour elle ;
- **plugin Necropolis** : une nouvelle map obtient `markers_only` (§13.C.24, honnête)
  et n'a des VFX que si quelqu'un écrit son profil.

La deuxième est plus conforme à §13.C.24 (« ne jamais produire un résultat qui a l'air
converti quand il ne l'est pas »), la première est plus utile immédiatement.

### H8 — Audio (§13.C.25)

Le seul point mort jamais tranché du pipeline : ni export, ni même une passe de
marqueurs à la `VFX_MARKERS_NOT_CONVERTED`. Trois valeurs possibles pour
`audio.policy` : `ignore` (assumé et documenté), `markers` (placement seul, cohérent
avec le traitement Niagara), `export` (sortir les fichiers son — coût réel, valeur à
estimer). Le schéma §14.2 refuse de démarrer tant que ce champ est `null` : c'est
délibéré.

### H9 — Version de schéma (§9.9, §12.3)

`manifest_version` est figé à `"10.0"` à travers 8 sous-versions de correctifs de
comportement. Passer à `"11.0"` casse deux contrôles existants : le
`begins_with("10")` du reconstructeur (§3.9.2) et la vérification de
l'exporteur d'assets (§3.3). Décision couplée : soit bump + fenêtre de compatibilité
acceptant `10.*` et `11.*`, soit conserver `"10.0"` et ajouter un champ
`pipeline_version` distinct qui, lui, bouge à chaque correctif de comportement.

---

## 14.5 Correctifs mécaniques à embarquer (ce ne sont pas des décisions)

Repérés en §12, à traiter pendant la généralisation — chacun précédé d'une **mesure**,
jamais d'une correction à l'aveugle (§13.A.1) :

| # | Point | Mesure préalable | §
|---|---|---|---|
| 1 | `asset_inventory.skeletal_mesh_assets` toujours à `0` | comparer au registre réel | §12.1 |
| 2 | `component_transform()` brut sur `particle` et sur tous les composants de Blueprint | compter les Decal/Niagara vivant sous un Blueprint dans le manifeste actuel — si le compte est non nul, c'est un vrai trou de couverture des correctifs V10.3/V10.8 | §12.2 |
| 3 | `DECAL_SIZE_SCALE := 0.9` déclarée, documentée, jamais référencée | vérifier l'empreinte réelle des décals avant/après branchement | §12.8 |
| 4 | `stats["recomputed_li_transforms"]` imprimé, jamais incrémenté | supprimer le compteur ou l'implémenter — pas laisser un 0 trompeur | §12.9 |
| 5 | numérotation d'étapes flottante entre en-têtes (« script 4 » vs « script 5 sur 6 ») | — | §12.5 |

---

## 14.6 Ordre de travail proposé

| Jalon | Contenu | Débloque |
|---|---|---|
| **M0** | Extraction `MapBuilder` + `entry_headless.gd` + **test d'équivalence §14.0** | tout le reste ; sans ça l'orchestrateur n'existe pas |
| **M1** | `core/config.py`, `run_id`, `config_hash`, config résolue embarquée dans le manifeste | gates G3/G4, fin de la classe de bugs §12.4 |
| **M2** | Empaquetage `Content/Python/ue2godot/` + `run(cfg)` sur step1 et step2 | importabilité (§6.8, §9.1), fin du chargement relatif de `png_codec` |
| **M3** | `crosscheck.py`, `StepReport`, `orchestrator/pipeline.py` avec G0→G6, step5 automatisée | pipeline pilotable de bout en bout |
| **M4** | Profils : décals (§9.3) puis VFX (H7) | réutilisation sur une 2ᵉ map |
| **M5** | Trous : skeletal (H5), multi-landscape (H6), audio (H8) | couverture |

**Axes de test obligatoires à chaque jalon** — la leçon transversale de §7 : presque
tous les bugs de ce projet ont la même signature, « ça marche pour le cas simple et
casse silencieusement dès qu'un cas complexe apparaît ». Toute validation doit couvrir
les 4 axes, jamais le cas trivial seul :

1. rotation non-identité (pas seulement de la translation — §7.5) ;
2. acteur d'origine Blueprint (pas seulement `StaticMeshActor` — §7.8) ;
3. LevelInstances imbriqués à profondeur ≥ 2 (§7.7) ;
4. ISM/HISM multi-instance (§7.4) ;
5. et, pour tout ce qui touche aux axes : **un objet asymétrique**, jamais un pilier
   (§13.A.3).
