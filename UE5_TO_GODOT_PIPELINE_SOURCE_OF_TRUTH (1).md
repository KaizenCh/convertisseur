# Pipeline UE5 → Godot : Source de vérité pour la généralisation en framework

> Document produit à partir d'une lecture exhaustive, ligne par ligne, des 8 fichiers
> actuellement dans la zone `project` (aucune troncature, aucun saut).
> Objectif : servir de base à la conception du **framework général** et du
> **fichier orchestrateur**, sans avoir à relire les scripts sources.

---

## Sommaire

1. [Vue d'ensemble et ordre d'exécution réel](#1-vue-densemble-et-ordre-dexécution-réel)
2. [Le reconstructeur `.gd` — désormais lu intégralement](#2-le-reconstructeur-gd--désormais-lu-intégralement)
3. [Fichier par fichier](#3-fichier-par-fichier)
4. [Le contrat de données central : `level_manifest_v10.json`](#4-le-contrat-de-données-central--level_manifest_v10json)
5. [Les deux autres fichiers d'échange](#5-les-deux-autres-fichiers-déchange)
6. [Patterns et algorithmes transversaux](#6-patterns-et-algorithmes-transversaux)
7. [Chronologie des bugs — pourquoi chaque règle existe](#7-chronologie-des-bugs--pourquoi-chaque-règle-existe)
8. [Déjà généralisable vs encore lié au projet X (Necropolis)](#8-déjà-généralisable-vs-encore-lié-au-projet-x-necropolis)
9. [Ce qu'il faudra généraliser, point par point](#9-ce-quil-faudra-généraliser-point-par-point)
10. [Contraintes et pièges qui peuvent tout casser](#10-contraintes-et-pièges-qui-peuvent-tout-casser)
11. [Ce dont l'orchestrateur a besoin](#11-ce-dont-lorchestrateur-a-besoin)
12. [Incohérences et bugs latents repérés pendant cette lecture](#12-incohérences-et-bugs-latents-repérés-pendant-cette-lecture)
13. [Leçons tirées de l'historique des conversations — ce qu'il ne faut plus refaire](#13-leçons-tirées-de-lhistorique-des-conversations--ce-quil-ne-faut-plus-refaire)

---

## 1. Vue d'ensemble et ordre d'exécution réel

Le pipeline convertit une map Unreal Engine 5.5 en scène Godot 4 en 6 étapes,
réparties entre deux moteurs. Unreal ne fait qu'**écrire des fichiers**
(JSON + GLB + PNG) ; Godot **lit ces fichiers** et reconstruit la scène. Il n'y a
aucune communication directe entre les deux moteurs — le manifeste JSON est
l'unique pivot.

```
CÔTÉ UNREAL (Python, dans l'éditeur)                    CÔTÉ GODOT (GDScript)
──────────────────────────────────────────────────────  ──────────────────────
0. ue5_preprocess_detach_all.py        (manuel, 1x)
   └─ casse les LevelInstances (UI) + détache tous
      les acteurs enfants en gardant leur transform
      monde (KEEP_WORLD)

1. unreal_export_manifest_v10-8.py
   └─ scanne TOUT le niveau (acteurs, composants,
      matériaux, textures, LevelInstances, FX...)
   └─ ÉCRIT (from scratch) :
        C:/Export/level_manifest_v10.json
        C:/Export/level_manifest_v10.txt

2. unreal_export_godot_assets_PATCHED.py
   └─ LIT  level_manifest_v10.json (geometry.unique_meshes)
   └─ exporte chaque StaticMesh en GLB (GLTFExporter UE natif)
   └─ ÉCRIT (from scratch) :
        C:/Export/GodotAssets/Meshes/*.glb
        C:/Export/GodotAssets/ue5_godot_asset_map.json

3. unreal_export_landscape.py
   └─ LIT  level_manifest_v10.json (pour le patcher)
   └─ LIT  ue5_godot_asset_map.json (pour l'enrichir)
   └─ reconstruit la géométrie du Landscape par raycasts
      + bake la texture BaseColor via SceneCapture2D
   └─ ÉCRIT un GLB "à la main" (writer glTF binaire maison)
   └─ PATCHE (append, ne réécrit pas from scratch) :
        level_manifest_v10.json      (ajoute 1 placement identité)
        ue5_godot_asset_map.json     (ajoute 1 entrée asset)

4. unreal_export_decals_vfx.py  (utilise png_codec.py)
   └─ LIT  level_manifest_v10.json (effects.decals, effects.niagara)
   └─ résout textures/tint depuis les matériaux de décal
   └─ compose les PNG RGBA (algo pur Python, sans PIL)
   └─ ÉCRIT :
        C:/Export/GodotAssets/Decals/*.png
        C:/Export/GodotAssets/ue5_godot_decal_map.json
                                                          5. [COPIE MANUELLE]
                                                             les 2 dossiers +
                                                             3 JSON dans le
                                                             projet Godot
                                                          ─────────────────────
                                                          6. ue5_godot_map_constructor_v10_PATCHED.gd
                                                             (EditorScript @tool,
                                                             lancé depuis
                                                             FileSystem → clic
                                                             droit → Run)
                                                             LIT les 3 JSON
                                                             CONSTRUIT le .tscn
                                                             et l'ÉCRIT via
                                                             ResourceSaver
```

**Statut mis à jour** : ce fichier a été fourni et lu intégralement (2283
lignes, version interne V10.12). Il n'est plus manquant — voir §2 et §3.9
pour l'analyse complète. Le patch `godot_decals_vfx_addition.gd` documenté
au §3.8 est **déjà fusionné** dedans (`_build_decals()` y est identique à
l'addition documentée) : à lire désormais comme une archive historique de
la façon dont le patch a été appliqué, pas comme un fichier encore à coller.

**Ordre strict et sa raison** : le manifeste (étape 1) et l'export d'assets
(étape 2) réécrivent leur JSON **intégralement à chaque exécution**
(`json.dump` sur un dict reconstruit de zéro). Le Landscape (étape 3) et les
décals (étape 4) ne font que **lire-modifier-réécrire en append** ces mêmes
JSON. Si on relance l'étape 1 ou 2 après l'étape 3/4, tout le travail du
Landscape et des décals est perdu silencieusement — c'est écrit noir sur blanc
dans les headers de fichiers ("Landscape EN DERNIER").

Contrôle de cohérence documenté (mais **manuel**, jamais vérifié par du code) :
le nombre d'entrées de `ue5_godot_asset_map.json["assets"]` doit égaler le
nombre de `unique_meshes` du manifeste. **L'orchestrateur devrait automatiser
cette vérification.**

---

## 2. Le reconstructeur `.gd` — désormais lu intégralement

**Le fichier `ue5_godot_map_constructor_v10_PATCHED.gd` a été fourni et lu
en entier (2283 lignes, version interne V10.12).** C'était la seule pièce
manquante de tout le pipeline lors de la première lecture ; l'analyse
complète — rôle, fonctions, connexions, ce qui est déjà générique vs encore
lié au projet X, ce qui doit être généralisé — est maintenant au §3.9, au
même niveau de détail que les 8 autres fichiers. Ce fichier est de très loin
le plus important côté reconstruction : c'est un **`@tool extends
EditorScript`** de Godot 4, lancé manuellement depuis le panneau
FileSystem de l'éditeur (clic droit → *Run*) — pas un script exécuté au
runtime du jeu, et pas trivialement pilotable depuis l'extérieur de
l'éditeur (implication directe pour l'orchestrateur, voir §10 et §11).

Il charge les 3 JSON produits côté Unreal, construit la scène entière en
mémoire (géométrie, décals, marqueurs VFX, métadonnées de LevelInstance),
puis l'empaquette en `PackedScene` et l'écrit sur disque via
`ResourceSaver.save()` — `res://Map--_REBUILT.tscn`. Aucune scène n'existe
tant que ce script n'a pas tourné jusqu'au bout ; en cas d'échec systémique
(asset map ou GLB réellement absent), il libère la racine (`root.free()`)
et n'écrit rien, plutôt que de sauvegarder une scène partielle.

Le module décals/VFX documenté au §3.8 (`godot_decals_vfx_addition.gd`)
est **déjà fusionné mot pour mot** dans ce fichier — `_build_decals()` y
est identique à l'addition. Le patch a bien été appliqué à un moment de
l'historique ; il ne reste qu'une trace archivée de la méthode utilisée.

---

## 3. Fichier par fichier

### 3.1 `ue5_preprocess_detach_all.py` — Prétraitement (étape 0, manuel)

**Pourquoi il existe.** Le manifeste (étape 1) doit remonter, pour chaque
composant, la chaîne `attach_parent` afin de reconstruire un transform monde
quand `get_component_transform()` échoue (voir V10.3 en §7). Plus cette
chaîne est longue, plus il y a de points de défaillance. Ce script **supprime
le besoin de composer plus d'un maillon** en aplatissant tous les
attachements acteur→acteur *avant* le scan.

**Ce qu'il fait.** Pour chaque acteur du niveau : lit `get_attach_parent_actor()` ;
si un parent existe, appelle
`detach_from_actor(KEEP_WORLD, KEEP_WORLD, KEEP_WORLD)` explicitement sur les
3 règles (location/rotation/scale) — le défaut de l'API
(`KEEP_RELATIVE`) ferait sauter les acteurs à un autre endroit, donc **ne
jamais laisser les valeurs par défaut**.

**Ce qu'il NE fait PAS (volontairement)** :
- ne casse pas les LevelInstances (le containment LI n'est pas un attachement
  acteur — `detach_from_actor()` n'a aucun effet dessus). Cela doit être fait
  **manuellement via l'UI** (World Outliner → filtrer classe "LevelInstance" →
  sélectionner tout → clic droit → Level → Break → *répéter* car casser un LI
  peut en révéler d'autres imbriqués) ;
- ne touche pas aux Groupes d'acteurs (Ctrl+G, métadonnée d'éditeur pure,
  aucun effet sur la composition de transform) ;
- ne corrige pas les meshes mal orientés à l'origine (problème d'asset,
  pas de hiérarchie).

**Sécurité intégrée** : `DRY_RUN` (bool en tête de fichier) — première passe
= rapport seul, aucune modification. Recommandation explicite de travailler
sur une copie du niveau.

**Entrées/Sorties** : aucune donnée fichier — modifie l'état live de l'éditeur
Unreal. **Ce que ce script utilise** : uniquement l'API `unreal` native
(`EditorActorSubsystem`, `DetachmentRule`). **Ce qui l'utilise** : personne en
aval directement — c'est un prérequis manuel qui change l'état de la scène
que le manifeste scannera ensuite.

**Généralisable tel quel.** Complètement neutre vis-à-vis du contenu — aucune
référence au projet Necropolis. Candidat direct pour le framework, sans
modification. Seul point à généraliser : automatiser le "casser tous les LI"
qui reste actuellement 100% manuel (voir §9).

---

### 3.2 `unreal_export_manifest_v10-8.py` — Le scanner (étape 1, le cœur du système)

7609 lignes, aucun historique commenté (contrairement à une version antérieure
à 30318 lignes citée en mémoire — celle-ci est déjà "propre"). C'est de très
loin le fichier le plus important : toutes les autres étapes ne font que
*lire* ou *patcher* ce qu'il produit.

#### 3.2.1 Rôle et sortie

Scanne l'intégralité du niveau UE actuellement chargé/accessible et produit
`level_manifest_v10.json` — une **spécification complète et déclarative** de
la scène (transforms, hiérarchie, matériaux, textures, composants) mais
**sans aucun payload d'asset** (`asset_payload_included: false` dans le champ
`reconstruction` du manifeste — documenté explicitement). Aucune opération
destructive n'est effectuée sur la scène.

#### 3.2.2 Découverte des acteurs — la méthode "V4/V5/V6 éprouvée"

Un commentaire bloc marque explicitement **NE PAS REMPLACER** cette méthode :

```python
for actor in unreal.ObjectIterator(unreal.Actor):
    if actor.get_level() == level:
        result.append(actor)
```

au lieu de `world.get_current_level()`. Cette contrainte a survécu à 10
versions majeures — un signal fort qu'une approche plus "propre" en apparence
a été essayée et a échoué. **À ne jamais reconsidérer sans preuve nouvelle.**

Deux sources d'acteurs sont fusionnées et dédupliquées par `object_path()` :
- **acteurs directs** : `EditorActorSubsystem.get_all_level_actors()` (avec
  repli sur `EditorLevelLibrary` si le subsystem échoue) ;
- **contenu récursif des LevelInstances** : `collect_level_contents()`
  descend récursivement dans chaque `LevelInstance.get_loaded_level()`,
  jusqu'à `MAX_LEVEL_INSTANCE_DEPTH = 64`.

#### 3.2.3 Enregistrement canonique des LevelInstances

`register_level_instance()` maintient un registre unique **par actor_path**
(`level_instance_registry`), même si un LI est rencontré plusieurs fois
(occurrences vs. `unique_actor_paths` — les deux comptes sont exposés
séparément dans le diagnostic). Chaque entrée porte :
- `instance_chain` : liste des actor_paths depuis la racine jusqu'à ce LI
  inclus — **c'est cette chaîne, portée par CHAQUE placement/acteur/composant
  descendant, qui permettra plus tard de recomposer le transform monde final** ;
- `children_actor_paths` : liens parent→enfants pour reconstruire l'arbre ;
- `world_asset` : la sous-map source (permet de dédupliquer les LI qui
  pointent vers la même sous-scène) ;
- `encounter_count` / `encounter_sources` : traçabilité des doublons.

Le résultat alimente 3 structures dérivées mais synchronisées :
`level_instances.placements[]` (liste plate), `level_instances.hierarchy[]`
(arbre récursif via `build_hierarchy_node`), `level_instances.registry{}`
(dict canonique indexé par actor_path — **la source de vérité**, les deux
autres n'en sont que des projections).

#### 3.2.4 Classification (le point le plus fragile du système)

Deux fonctions classifient tout par **sous-chaîne de nom de classe** :

```python
def actor_category(actor):      # "level_instance" si "LevelInstance" in class_name
def component_kind(component):  # "decal" si "Decal" in class_name, etc.
```

C'est une heuristique par `in` sur des chaînes — fonctionne bien pour les
classes natives d'Unreal (`StaticMeshActor`, `PointLightComponent`,
`NiagaraComponent`, `DecalComponent`, `AudioComponent`...) mais **c'est le
point le plus fragile en cas de renommage de classe custom ou de nouvelle
version d'UE**. `is_blueprint_generated_actor()` détecte les Blueprints via
le suffixe `_C` du class_path + préfixe `/Game/` ou `/Plugin(s)/`.

#### 3.2.5 Récupération de transform — la partie la plus retravaillée

C'est le sous-système qui a subi le plus de correctifs (V10.1 → V10.8, voir
§7 pour la chronologie complète). Fonctions clés, dans l'ordre d'appel :

1. **`actor_transform(actor)`** — `get_actor_transform()`, **taux d'échec
   mesuré : 0 sur toute la scène**. C'est la fondation la plus fiable.
2. **`component_transform(component)`** — `get_component_transform()` brut,
   utilisé tel quel pour les composants Blueprint (`blueprint_component_detail`)
   et pour Audio/Particle (jamais passé au diagnostic — incohérence, voir §12).
3. **`component_transform_diagnostic(component, actor)`** — version "qui ne
   ment jamais" : retourne toujours `(valeur, erreur)` au lieu d'avaler
   l'exception. Utilisée pour StaticMesh/Decal/Niagara/Light/Audio. Logique :
   - si `get_component_transform()` réussit → utilisé directement ;
   - si `AttributeError` → tente un re-fetch de l'objet (staleness
     ObjectIterator, cas Blueprint dont le Construction Script régénère ses
     composants pendant le scan) via `_try_refetch_stale_component()` (essaie
     `unreal.find_object` puis `unreal.load_object` sur le path stable) ;
   - si le re-fetch échoue aussi → **repli sur les propriétés**
     (`component_transform_via_properties`), jamais sur la méthode.
4. **`component_transform_via_properties(component, actor, max_depth=32)`**
   — le repli final (V10.8, le plus récent). Remonte la chaîne
   `attach_parent` en lisant `relative_location/relative_rotation/
   relative_scale3d` via `get_editor_property()` (jamais via une méthode —
   ces classes n'exposent pas la méthode transform en Python, mais les
   UPROPERTY passent). **Point clé de la version finale** : la base n'est
   *jamais* le composant racine lui-même (son relative est l'identité sur un
   Blueprint — la position vit sur l'ACTEUR), mais toujours
   `actor_transform(actor)`, sur laquelle on compose *seulement* les
   relatives des composants non-racines rencontrés en remontant.

Chaque type de composant a sa propre fonction d'extraction
(`static_mesh_component_info`, `decal_component_info`,
`niagara_component_info`, `light_component_info`, `audio_component_info`),
mais toutes appellent `component_transform_diagnostic()` en interne et
stockent systématiquement `transform` + `transform_extraction_error` +
`actor_transform` (le dernier sert de filet de sécurité côté Godot).

**Cas des ISM/HISM** (Instanced/Hierarchical Instanced Static Mesh) :
`static_mesh_component_info` lit **chaque instance individuellement** via
`get_instance_transform(i, world_space=True)` (avec 3 signatures d'appel
essayées pour compatibilité inter-versions d'API), pas seulement le transform
du composant — sinon un ISM de 165 occurrences ne produirait qu'1 placement
Godot (bug V10.4 corrigé, voir §7).

#### 3.2.6 Composition des transforms monde — `finalize_v10_transforms()`

La fonction la plus critique du fichier. Composition Unreal **child \* parent**
(jamais l'inverse — voir V10.5 en §7) via l'API native `unreal.Transform` /
`unreal.KismetMathLibrary.compose_transforms` (repli sur l'opérateur `*`) —
**jamais de dérivation manuelle des signes de rotation**. Étapes, dans l'ordre :

1. Transforms finaux de chaque LevelInstance (`final_li_transforms`), en
   composant récursivement le long de `parent_level_instance` — **seule la
   LI la plus interne compte pour la composition de la chaîne** (voir V10.7,
   §7 — piège du double comptage) ;
2. Report de ces transforms sur `level_instances.placements[]` ;
3. Report récursif sur `level_instances.hierarchy[]` ;
4. Transform de reconstruction pour chaque acteur (`actors.records[]`) ;
5. **Le plus important** : `geometry.placements[]` — pour chaque placement,
   compose `level_instance_chain` + `transform` (composant) **et**
   séparément + `actor_transform` (`final_actor_world_transform`, filet de
   sécurité), puis **compose aussi individuellement chaque
   `instance_transforms[i]`** en `instance_final_world_transforms[]` (V10.4) ;
6. Systèmes spatiaux secondaires (lights/audio/niagara/decals) — même
   traitement générique via `compose_chain_transform()` ;
7. Composants de Blueprints (`blueprints.actors[].component_details[]`) ;
8. Vérification des références parent cassées ;
9. Diagnostics FX séparés (`ready_for_godot_fx`, par catégorie
   decals/niagara/lights/audio) — **volontairement distinct** de
   `ready_for_godot_geometry`, pour qu'un échec silencieux sur les décals ne
   soit jamais masqué par un statut global "ready" (voir V10.2, §7).

`compose_chain_transform(chain, source_transform, li_registry, diagnostics, context)`
est la fonction generic réutilisée partout : compose la chaîne de LI (du plus
externe au plus interne, via `LI_root * ... * LI_leaf`) puis compose
`source_transform` par-dessus (`source * chaîne_LI`). Utilise le
**transform local (`source_transform`) de chaque LI dans la chaîne, jamais
son `final_world_transform` déjà composé** — sinon double comptage garanti.

#### 3.2.7 Registres dédupliqués (mesh / matériau / texture)

Trois dicts globaux, clés par `object_path()` UE (identifiant stable) :
- `mesh_registry` : `mesh_info()` + `usage_count` + `instance_total`
  (somme des instances ISM/HISM, pas juste le nb de placements) +
  `materials[]` (paths référencés) ;
- `material_registry` : `material_full_info()` (inclut
  `material_instance_parameters()` — scalar/vector/texture params, parent
  chain) + `usage_count` + `mesh_references[]` ;
- `texture_registry` : `texture_info()` (dimensions, srgb, format,
  compression) + `material_references[]` + `parameter_references[]`
  (`{material, parameter}`).

`register_material_textures()` résout automatiquement les textures
référencées par un matériau (via `material_instance_parameters()`), y
compris quand la texture n'est pas chargeable (`unreal.load_object` échoue) —
dans ce cas, l'entrée de registre est construite depuis le dict déjà extrait
plutôt que d'être perdue.

#### 3.2.8 Diagnostics et contrat de "readiness"

Le fichier maintient une **doctrine de diagnostic** très déliberée : chaque
échec est catégorisé (échec réel vs. cas légitime). Exemple emblématique :
`empty_mesh_slot_count` (un StaticMeshComponent ancre/socket sans mesh
assigné — normal) est **explicitement séparé** de
`missing_mesh_references` (un mesh assigné mais introuvable dans le
registre — un vrai bug). Cette distinction évite les faux positifs qui
avaient pollué les comptes de bugs avant V10.1.

Le bloc `reconstruction` en fin de manifeste est le **contrat formel** que
consomme (ou devrait consommer) le reconstructeur : `ready_for_godot_geometry`
n'est vrai que si zéro échec de transform géométrie + zéro mesh manquant +
zéro échec LI + **zéro échec d'instance ISM/HISM** (V10.4 — avant cela, un
ISM à 0/458 instances placées pouvait quand même passer "ready").

**Constat après lecture du reconstructeur (§3.9)** : le `.gd` ne lit en
réalité **jamais** ce bloc `reconstruction` — ni `ready_for_godot_geometry`
ni `ready_for_godot_fx` n'apparaissent dans son code. Il refait sa propre
validation intégrale, indépendante, placement par placement
(`_build_geometry_placement`). Les deux côtés du pipeline calculent donc
chacun leur propre verdict de "prêt", sans jamais se référencer l'un
l'autre — une duplication qui pourrait diverger silencieusement si l'un des
deux évolue sans l'autre (voir §12).

**Ce que le fichier utilise** : uniquement l'API `unreal` (aucune dépendance
aux autres scripts du pipeline). **Ce qui l'utilise** : les 3 scripts
suivants (asset exporter lit `geometry.unique_meshes`, landscape et décals
lisent/patchent le manifeste entier), et en bout de chaîne, le
reconstructeur `.gd` (§3.9).

**Spécifique au projet vs généralisable** : la logique de scan, de
classification, de composition de transform et de diagnostic est **100%
générique** — aucune référence à Necropolis, aucun chemin d'asset en dur
dans ce fichier précis (contrairement aux 2 suivants). Seuls
`OUTPUT_PATH`/`TXT_OUTPUT_PATH` (`C:/Export/...`) sont en dur et à
externaliser en configuration.

---

### 3.3 `unreal_export_godot_assets_PATCHED.py` — Export des meshes (étape 2)

**Rôle.** Lit `geometry.unique_meshes` du manifeste et exporte chaque
StaticMesh unique en GLB via l'API native `unreal.GLTFExporter`
(`export_to_gltf`) — nécessite le plugin GLTFExporter activé côté UE. Produit
`ue5_godot_asset_map.json`, la **table de correspondance UE-path → fichier
Godot** que le reconstructeur résout pour charger chaque mesh.

**Pourquoi il ne réutilise pas simplement le nom de l'asset comme nom de
fichier** : deux assets de même nom court dans des dossiers différents
(`/Game/Env/Rocks/SM_Rock01` vs `/Game/Props/Misc/SM_Rock01`) collisionnaient
sur le même `.glb`, silencieusement marqués `EXISTING` alors que c'était le
mauvais mesh (bug corrigé : `unique_filename_for_path()` ajoute un hash SHA1
de 8 caractères du path UE complet au nom de fichier).

**Validation stricte du GLB produit** (pas seulement "le fichier existe") :
taille minimale (20 octets) **et** vérification du header magique `b"glTF"` —
un export tronqué à 0 octet était auparavant compté comme un succès. Les
messages d'avertissement de l'exporteur natif (`get_error_messages`, etc.,
API variable selon version) sont récupérés et propagés dans le JSON plutôt
que perdus.

**Idempotence** : si le fichier GLB existe déjà avec une taille non nulle, le
statut est `EXISTING` (pas de ré-export) — utile pour relancer le pipeline
sans tout regénérer, mais **attention** : cela ne détecte pas un mesh source
modifié depuis (pas de hash de contenu, seulement présence/taille).

**Ce qu'il utilise** : `level_manifest_v10.json` (lecture seule, vérifie
`manifest_version == "10.0"` et avertit sinon — mais continue quand même).
**Ce qui l'utilise** : le reconstructeur `.gd` (résout chaque mesh par
`ue_path`). **Portée volontairement limitée** : uniquement les StaticMesh —
**les SkeletalMesh (capturés dans le manifeste depuis V10.1,
`geometry.unique_skeletal_meshes`) ne sont PAS exportés par ce script**, gap
à combler pour la généralisation (§9).

**Généralisable tel quel**, à l'exception des chemins en dur
(`MANIFEST_PATH`, `OUTPUT_ROOT`, `GODOT_ROOT`) — aucune référence au projet X.

---

### 3.4 `unreal_export_landscape.py` — Le Landscape (étape 3, "script 4" selon son propre header)

**Pourquoi il existe** (le "problème nu", cité explicitement dans le
fichier) : le manifeste décrit le Landscape avec transform + bounds mais
**aucune géométrie ni texture** — c'est marqué HIGH risk. Or
`ALandscapeProxy::ExportToRawMesh()` existe en C++ mais **n'est pas exposé à
l'API Python**, et l'export GLB natif via le menu UE sur un Landscape est
connu pour produire un résultat cassé. La géométrie est donc reconstruite
**de l'extérieur**, par une méthode indépendante de toute API non exposée.

**Méthode géométrie — raycasting sur grille** :
`sample_heightfield()` trace verticalement `(GRID_RESOLUTION+1)²` rayons
(256×256 par défaut → 66049 tirs, ~20-60s) à travers toute la bounding box du
Landscape (+ ses `LandscapeStreamingProxy`). **Réutilise la même technique
que `auto_terrain_generator_ue55.py`** (`sample_landscape_heights`) —
**y compris sa liste d'ignorés** : un impact sur un prop posé sur le terrain
ajoute cet acteur à une ignore-list et retire le rayon, jusqu'à
`MAX_TRACE_RETRIES` (12). Une case non touchée par aucun rayon reste un
"trou" — le maillage n'émet un quad que si ses 4 coins ont été touchés
(`build_grid_mesh`), donc **les trous restent des trous plutôt que d'être
comblés par une hauteur inventée**.

**Méthode texture — bake orthographique** : un `SceneCapture2D` (spawné
temporairement, toujours détruit dans un bloc `finally` même en cas
d'échec) capture le Landscape **seul** (`PRM_USE_SHOW_ONLY_LIST`, avec 2
replis en cascade si cette propriété refuse l'écriture — jusqu'à cacher
manuellement tous les autres acteurs) en `SCS_BASE_COLOR` (albédo **non
éclairé**, choix délibéré : les props exportés ailleurs sont rééclairés par
Godot, baker l'éclairage UE ferait diverger les deux rendus). Format de
render target `RTF_RGBA8_SRGB` (avec repli sur linéaire + avertissement si
absent — sinon le PNG ressortirait délavé dans Godot qui l'interprète en
sRGB).

**Packaging — writer glTF binaire écrit à la main** (`write_glb()`) : buffer
unique, 5 bufferViews (positions/normales/UV/indices/PNG embarqué), 1
matériau PBR. **Aucune dépendance à une bibliothèque glTF externe** — juste
`struct` + `json`. Le PNG est **embarqué dans le binaire**, pas référencé en
fichier séparé.

**Convention d'axe — le point le plus sensible du fichier** :

```python
def AXIS_MAP(x, y, z):
    return (x * UE_CM_TO_GODOT_M, z * UE_CM_TO_GODOT_M, y * UE_CM_TO_GODOT_M)
# godot = (ue.x, ue.z, ue.y) * 0.01 — déterminant -1
```

Ce mapping **doit être bit-à-bit identique** à celui utilisé par
`_transform_from_v10()` dans le `.gd` (absent, voir §2). Le fichier documente
sa propre histoire : une V1 avec `(x, z, -y)` (déterminant +1) donnait un
rendu correct pour les objets symétriques (piliers, murs, tombes) mais
**faux pour les escaliers** — parce que les GLB produits par l'exporteur
glTF natif d'Unreal ont **déjà** la handedness inversée par cet exporteur.
Déterminant -1 → **le winding des triangles est inversé en conséquence**
dans `build_grid_mesh()` (`(a,b,c)` puis `(b,d,c)` au lieu de l'ordre
naturel) pour que les faces restent visibles depuis le dessus.
`AXIS_MAP_LABEL` (`"(ue.x, ue.z, ue.y) * 0.01"`) est **stocké dans le JSON**
mais **jamais vérifié automatiquement** contre la convention réellement
utilisée côté `.gd` — un futur désaccord serait à nouveau silencieux (voir
§10).

**Registration — patch, pas réécriture from scratch** : `register_in_asset_map()`
et `register_in_manifest()` lisent le JSON existant, retirent toute entrée
préexistante pour le path synthétique `/AutoTerrain/Landscape.BakedLandscape`
(idempotence en cas de relance), puis ajoutent la nouvelle. Le placement
manifeste injecté a un **transform identité** — cohérent puisque les sommets
du GLB sont **déjà** pré-convertis en espace Godot (contrairement à tous les
autres placements, dont la conversion se fait au niveau du `Transform3D` du
nœud, pas des sommets). Le script met aussi à jour le risque `LANDSCAPE`
existant dans `conversion_risks[]` (passe de `HIGH` à `INFO`).

**Ce qu'il utilise** : `level_manifest_v10.json` (lecture + réécriture),
`ue5_godot_asset_map.json` (lecture + réécriture), et implicitement le
matériau construit par `auto_terrain_generator_ue55.py` (c'est ce matériau
que la caméra de capture rend). **Ce qui l'utilise** : le reconstructeur
`.gd`, exactement comme n'importe quel autre mesh de l'asset map (aucune
branche spéciale requise côté `.gd` — conçu explicitement pour ça).

**Généralisable avec adaptation** : l'algorithme (raycast + bake + writer
GLB maison) est **totalement indépendant du projet X**. Seuls
`GRID_RESOLUTION`, `TEXTURE_RESOLUTION`, les chemins, et la convention d'axe
sont à externaliser. Limite structurelle actuelle : **un seul Landscape
"primaire" traité** (`find_landscape_actors()` prend `landscapes[0]` avec un
avertissement si plusieurs Landscape existent — les proxies de streaming
sont bien tous inclus, mais pas des Landscapes multiples indépendants).

---

### 3.5 `auto_terrain_generator_ue55.py` — Générateur de matériau de terrain (HORS pipeline d'export)

**Ce fichier n'est PAS une étape de conversion.** C'est un outil
d'**authoring côté Unreal** : il construit procéduralement, nœud par nœud
via `MaterialEditingLibrary`, le graphe de matériau du Landscape
(`M_AutoTerrain` / instance `MI_AutoTerrain`) — mélange de textures Necropolis
et Quixel selon l'altitude, la pente, du bruit, des couches de peinture
manuelle (`PAINT_LAYERS`), un look "rocher" biplanaire, un remplissage de
boue, etc. v9 au moment de la lecture (historique v4→v9 documenté en
commentaire de tête, contrairement au manifeste qui a purgé son historique).

**Son seul point de contact avec le pipeline de conversion** : c'est *ce*
matériau (assigné au Landscape via `assign_material()`) que
`unreal_export_landscape.py` **photographie** avec son SceneCapture2D pour
produire `BakedLandscape_BaseColor.png`. Sans ce script (ou un équivalent),
le Landscape UE afficherait son matériau par défaut et le bake serait vide
de sens visuellement — mais le script d'export fonctionnerait quand même
techniquement avec n'importe quel matériau assigné.

**Pattern partagé notable** : `sample_landscape_heights()` /
`levels_from_heights()` utilise **exactement** la même technique de
raycasting en grille avec ignore-list que `unreal_export_landscape.py`
(implémentée deux fois indépendamment — candidat de factorisation, §6.5),
pour **mesurer** les niveaux d'altitude (plateau bas / piedmont / bande
rocheuse) par analyse statistique de la distribution des hauteurs
(histogramme lissé, recherche du mode dans la fraction basse — "surface la
plus peuplée", pas juste le point le plus bas).

**Entièrement spécifique au projet X** : références en dur au pack
Necropolis (`SETS`, chemins `/Game/Necropolis/...`), aux assets Quixel
(`/Game/AutoTerrain/...`), aux noms de layers de peinture
(`Paint_RockySand`, etc.), aux réglages esthétiques (teintes, seuils).
**Aucune part de ce fichier ne devrait entrer dans le framework général** —
sinon, au mieux, comme *exemple* de plugin de génération de matériau que
l'utilisateur pourrait fournir/remplacer pour sa propre map. Le point à
retenir pour le framework n'est pas le contenu, mais le **contrat** : "avant
d'exporter le Landscape, le Landscape doit avoir un matériau qui produit le
rendu voulu — la génération de ce matériau est hors du scope de l'export."

---

### 3.6 `unreal_export_decals_vfx.py` — Décals et VFX (étape 4)

**Pourquoi il existe** (problème nu) : le manifeste décrit déjà 1423 décals
et 422 composants Niagara avec transform/taille/matériau/système corrects,
mais **rien ne matérialisait cette description** — aucune texture écrite,
aucun nœud créé côté Godot. Ce script ferme la moitié "décal" de ce trou et
donne aux VFX un atterrissage honnête (placement seul, jamais converti).

**Décals — résolution de texture** : sur ce projet, seulement 5 matériaux de
décal distincts couvrent les 1423 placements, et les instances **n'overrident
aucune texture** — tout vient des valeurs par défaut du matériau parent
partagé (`M_Decals_01`). `resolve_texture_parameter()` cherche dans l'ordre :
(1) override explicite sur l'instance, (2)
`get_material_default_texture_parameter_value()` sur le parent pour une
liste de noms candidats (`MASK_PARAMETER_NAMES`, `NORMAL_PARAMETER_NAMES` —
**noms de paramètres en dur, dépendants de la convention de nommage du
pack**), (3) scan de tous les noms de paramètres texture du parent en
cherchant une sous-chaîne correspondante. Le tint vient de
`TINT_PARAMETER_NAMES` (`"Tint 02"`, `"Tint 01"`, etc.) avec repli sur blanc
neutre si rien n'est trouvé (delibérément visible plutôt que silencieusement
sombre).

**Extraction des pixels — même contrainte que le bake du Landscape** :
aucune API Python UE ne donne accès aux pixels bruts d'une texture. Solution
identique en substance à `bake_base_color()` : matériau temporaire "unlit"
(`MSM_UNLIT`) échantillonnant la texture cible, branché en Emissive,
`draw_material_to_render_target()`, puis `export_render_target()` en PNG.
Le matériau temporaire est **toujours supprimé** après usage
(`unreal.EditorAssetLibrary.delete_asset`), y compris en cas d'échec.

**Composition RGBA — le rôle de `png_codec.py`** : le masque exporté est un
PNG en niveaux de gris (couverture). Un Decal Godot veut une texture RGBA où
la **couleur** vient du Tint du matériau et l'**alpha** de la couverture du
masque — ni l'API UE ni PIL (indisponible dans l'éditeur) ne composent deux
images, d'où le module `png_codec.py` chargé dynamiquement
(`exec(compile(...))`, **doit être physiquement dans le même dossier** que ce
script — chargement par chemin relatif, pas par import de package).

**Détection de masque plat** : `compose_tinted_rgba()` retourne
`(largeur, hauteur, alpha_moyen, alpha_min, alpha_max)`. Si `min == max`, le
décal serait un **rectangle plein** plutôt qu'une tache — signalé bruyamment
(`entry["warning"]`) plutôt que silencieusement livré comme si c'était
normal.

**VFX (Niagara) — position claire et documentée** : *"un système Niagara
n'est pas convertible"* — comportement dans des modules propriétaires sans
équivalent Godot. Seul le **placement** est exporté (`effects.niagara[]` du
manifeste, déjà présent), groupé par nom de système dans le JSON de sortie
(`vfx.systems{name: count}`) pour que les 341 `NS_candle_flame` (sur 422
systèmes) puissent être reconstruits en masse plus tard côté Godot.

**Ce qu'il utilise** : `level_manifest_v10.json` (lecture seule —
`effects.decals`, `effects.niagara`), `png_codec.py` (chargement dynamique).
**Ce qui l'utilise** : le reconstructeur `.gd`, via `godot_decals_vfx_addition.gd`.

**Spécifique au projet X** : les listes de noms de paramètres
(`MASK_PARAMETER_NAMES`, `TINT_PARAMETER_NAMES`, `NORMAL_PARAMETER_NAMES`)
sont des heuristiques de convention de nommage propres au pack utilisé —
**premier vrai point de configuration par pack/projet** à exposer dans le
framework (une autre map avec un autre pack de décals aura d'autres noms de
paramètres). Le reste (résolution en cascade, bake par render target,
composition RGBA, détection de masque plat) est générique.

---

### 3.7 `png_codec.py` — Codec PNG minimal pur Python

**Pourquoi il existe** : ni l'API UE ni PIL ne permettent de composer deux
images ensemble depuis l'éditeur. Ce module lit/écrit du PNG 8-bit non
entrelacé, types de couleur 0/2/4/6 (gris / RGB / gris+alpha / RGBA) — **tout
ce que produit `export_render_target()`**. Toute autre variante lève une
exception explicite plutôt que de retourner une image fausse silencieusement.

**Contenu** : lecteur PNG complet avec dé-filtrage des 5 types de filtre PNG
(None/Sub/Up/Average/Paeth, algorithme `_paeth()` standard), writer RGBA
simple (filtre None uniquement — image petites, déjà bien compressées par
zlib), `luminance_at()` (formule de luminance perceptuelle standard
0.299/0.587/0.114), et la fonction métier `compose_tinted_rgba()` qui teinte
un masque de couverture avec une couleur RGB.

**Ce qu'il utilise** : seulement `struct` + `zlib` (stdlib pure, zéro
dépendance externe). **Ce qui l'utilise** : `unreal_export_decals_vfx.py`
exclusivement, actuellement — mais **c'est le module le plus indépendant et
le plus directement réutilisable de tout le pipeline**, sans aucune
modification, pour n'importe quel besoin futur de composition d'image côté
Unreal (aucune référence à Unreal elle-même à l'intérieur : pas d'`import
unreal`, testable en dehors de l'éditeur).

**100% générique, aucune trace de projet X.** Premier candidat pour devenir
un utilitaire du framework, tel quel.

---

### 3.8 `godot_decals_vfx_addition.gd` — Patch d'addition côté Godot (archive historique)

**Statut** : ce patch a depuis été **appliqué et fusionné** dans
`ue5_godot_map_constructor_v10_PATCHED.gd` (§3.9) — `_build_decals()` y est
identique, au caractère près, à ce qui est décrit ici. Cette section reste
utile pour comprendre **la méthode** employée, pas pour retrouver du code
qui resterait à intégrer.

**Ce n'est pas un script autonome** — c'était un bloc de code **à coller
manuellement** dans le fichier principal, avec des instructions étape par
étape en commentaire (const à ajouter, compteurs `stats` à ajouter, appels à
insérer dans `_run()` et `_print_report()`, fonctions à coller en fin de
fichier). Choix délibéré documenté : *"rien à supprimer : ton correctif de
quaternion et ta conversion d'axes restent intacts, ce bloc réutilise
`_transform_from_v10()` telle quelle"* — écrit pour **ne jamais toucher** à
la logique de conversion déjà validée et fragile du fichier principal.

**`_build_decals()`** : charge `ue5_godot_decal_map.json`, précharge **une
seule fois par matériau** chaque texture (partagée par les 781 placements
`MI_decal_leak_01`, par exemple, au lieu de 781 chargements identiques),
crée un nœud `Decal` par placement avec `decal.transform` recalculé via
`_transform_from_v10()` (orthonormalisé — `transform.basis.orthonormalized()`
— pour ne jamais accumuler de scale involontaire dans la base).

**Point le plus subtil du fichier — la boîte de décal** : Unreal projette
un décal le long de son axe X propre, avec une échelle `[épaisseur, largeur,
hauteur]` sur un cube de base 256 unités ; Godot projette le long de `-Y`
avec `size = (largeur, hauteur, profondeur)`. Comme `_transform_from_v10()`
a déjà tourné le nœud selon la convention d'axe globale, **seul l'ordre des
composantes de taille doit être réarrangé ici** — pas une deuxième rotation :

```gdscript
decal.size = Vector3(width, thickness, height)  # noter l'ordre : (w, thickness, h)
```

**`_build_vfx_markers()`** : crée un `Marker3D` par composant Niagara,
groupé par nom de système sous un nœud racine nommé de façon très explicite
`VFX_MARKERS_NOT_CONVERTED` — l'intention (ne jamais faire croire qu'un VFX
a été "converti") est portée jusque dans le nommage du nœud runtime, pas
seulement dans la documentation.

**Métadonnées** (`set_meta`) systématiquement attachées si
`KEEP_PLACEMENT_METADATA` : `ue_actor_path`, `ue_component_path`,
`ue_material_path` / `ue_niagara_system`, permettant une traçabilité
complète Godot → Unreal après reconstruction (utile pour du debug ou une
édition manuelle post-import).

**Ce qu'il utilise (dépendances vers le fichier principal)** :
`_transform_from_v10()`, `UE_CM_TO_GODOT_M`, `_safe_node_name()`,
`KEEP_PLACEMENT_METADATA`, `manifest` (variable), `stats` (dict) — toutes
confirmées présentes exactement sous ces noms dans `ue5_godot_map_constructor_
v10_PATCHED.gd` (§3.9). **Ce qui l'utilise** : rien d'autre — c'est une
feuille du graphe de dépendances.

**Généralisable** : le pattern (précharger les textures uniques, un nœud
par placement, group-by pour les FX non convertis, métadonnées de
traçabilité) est générique. Le détail de réarrangement des axes de la boîte
de décal est spécifique à la sémantique "Decal" d'Unreal vs. Godot — mais
c'est une conversion **de format**, pas de projet, donc généralisable une
fois isolée dans une fonction dédiée.

---

### 3.9 `ue5_godot_map_constructor_v10_PATCHED.gd` — Le reconstructeur Godot (le cœur de la moitié "destination")

2283 lignes, `@tool extends EditorScript`, version interne **V10.12**. C'est
la pièce qui manquait à la première lecture — désormais lue intégralement.
C'est le seul point du pipeline qui **écrit réellement une scène Godot** ;
tout ce qui précède (les 8 fichiers UE) ne produit que des données et des
assets en attente d'être consommés.

#### 3.9.1 Mode d'exécution — une contrainte structurante

`extends EditorScript` signifie que ce fichier ne s'exécute **que dans le
contexte de l'éditeur Godot**, lancé manuellement (panneau FileSystem, clic
droit sur le script → *Run*). Ce n'est ni un autoload, ni un script de jeu,
ni — par défaut — quelque chose qu'un orchestrateur externe peut déclencher
par une simple invocation en ligne de commande sans passer par l'éditeur
(voir §10 et §11 pour les implications concrètes sur la conception de
l'orchestrateur).

`_run()` orchestre tout le fichier dans l'ordre suivant : `_load_inputs()`
→ `_validate_manifest_and_asset_map()` → boucle sur
`geometry.placements[]` (`_build_geometry_placement()`) → `_build_decals()`
→ `_build_vfx_markers()` → `_build_li_metadata_nodes()` → `PackedScene.pack()`
→ `ResourceSaver.save()`. Si le pack ou la sauvegarde échoue, la racine est
libérée (`root.free()`) et rien n'est écrit — pas de scène partielle
silencieuse.

#### 3.9.2 Validation d'entrée — indépendante de celle du manifeste

`_validate_manifest_and_asset_map()` vérifie que `manifest_version` et
`asset_map["manifest_version"]` commencent tous les deux par `"10"`, puis
que **chaque** `ue_path` de `geometry.unique_meshes` a une entrée dans
`asset_map["assets"]` — jamais de repli par nom de fichier ou de devinette,
uniquement une résolution exacte. Un `exported_count` déclaré mais
incohérent avec la taille réelle de `assets` déclenche un `push_warning`,
pas un blocage.

**Constat important** : ce script **ne lit jamais**
`manifest["reconstruction"]["ready_for_godot_geometry"]` ni
`ready_for_godot_fx` — le contrat formel calculé côté Python n'est jamais
consulté côté Godot. Le reconstructeur refait sa propre validation
complète, indépendamment, placement par placement. Les deux moitiés du
pipeline ont chacune leur propre notion de "prêt", qui pourraient diverger
sans qu'aucun code ne le détecte (voir §12).

#### 3.9.3 `_build_geometry_placement()` — la boucle centrale, avec échec catégorisé

Reprend exactement la philosophie de diagnostic du manifeste (§3.2.8,
§6.3) côté Godot : un `enum Outcome { OK, SKIPPED, FATAL }` et **5 flags de
tolérance par catégorie d'échec**, pas un seul flag générique :

```gdscript
const FAIL_ON_MISSING_ASSET_MAPPING := true   # problème SYSTÉMIQUE -> abort
const FAIL_ON_MISSING_GLB := true             # problème SYSTÉMIQUE -> abort
const FAIL_ON_TRANSFORM_FAILURE := false      # problème ISOLÉ -> skip + compte
const FAIL_ON_INSTANTIATE_FAILURE := false    # problème ISOLÉ -> skip + compte
const FAIL_ON_DUPLICATE_PLACEMENT_ID := false # problème ISOLÉ -> skip + compte
```

Commentaire de tête explicite sur le bug que ce découpage corrige (V10.4) :
un seul échec connu et déjà documenté par le manifeste (un des 14 cas de
composant périmé, par exemple) **avortait la reconstruction entière avec
zéro `.tscn` produit**, parce qu'un unique couple de flags catch-all était
vérifié pour *toute* raison d'échec, pas seulement les deux vraiment
systémiques. Depuis, seuls asset-map/GLB réellement absents abortent par
défaut ; le reste est ignoré avec un avertissement compté — **99,8 % de
bonnes données ne sont plus jetées à cause d'une poignée de cas limites
connus**.

Pour chaque placement, la fonction résout d'abord `mesh.path` (slot vide →
skip comptabilisé, cohérent avec `empty_mesh_slot_count` du manifeste),
puis distingue **statique vs instancié** :
- `static_mesh` → un seul transform (`reconstruction_transform`, repli sur
  `final_world_transform`) ;
- `instanced_mesh` / `hierarchical_instanced_mesh` → lit
  `instance_final_world_transforms[]` (le tableau par-instance ajouté par
  le manifeste en V10.4) et **spawne un nœud par instance réelle**, avec
  repli gracieux sur le transform unique du composant si ce tableau est
  absent (manifeste pré-V10.4) — dégradation, pas échec.
- Un ISM/HISM à `instance_count <= 0` est **skippé explicitement**
  (`zero_instance_ism_skipped`), pour ne jamais spawner un objet fantôme à
  la transform du composant sur une liste d'instances réellement vide.

Chaque instance spawnée reçoit une clé de placement unique
(`base_placement_id` ou `..._INST_<i>` si multi-instance), vérifiée contre
`seen_placement_keys` pour détecter les doublons.

#### 3.9.4 La fonction pivot : `_transform_from_v10()` + conversion de repère

Code exact (le cœur de tout le pipeline de reconstruction) :

```gdscript
func _transform_from_v10(data: Dictionary):
    # ... location/rotation/scale extraits du dict UE ...
    var ue_basis := _unreal_rotator_to_basis(pitch, yaw, roll)
    var converted_basis := _convert_basis_ue_to_godot(ue_basis)
    converted_basis = converted_basis.scaled(ue_scale)
    var godot_pos := Vector3(ue_pos.x, ue_pos.z, ue_pos.y) * UE_CM_TO_GODOT_M
    return Transform3D(converted_basis, godot_pos)
```

Trois fonctions s'y articulent :

- **`_unreal_rotator_to_basis(pitch, yaw, roll) -> Basis`** — reconstruit le
  quaternion d'Unreal à partir de pitch/yaw/roll **avec les signes exacts de
  `FRotator::Quaternion()`** :
  ```gdscript
  var qx := cr * sp * sy - sr * cp * cy
  var qy := -cr * sp * cy - sr * cp * sy   # signe négatif : le correctif V10.7
  var qz := cr * cp * sy - sr * sp * cy
  var qw := cr * cp * cy + sr * sp * sy
  ```
  Commentaire de tête : *"pour une rotation yaw-seul, l'inversion s'annule —
  c'est pourquoi les bâtiments avaient l'air corrects ; tout ce qui avait du
  pitch ou du roll ressortait en miroir."* Exactement la signature du bug
  décrit en §7 (V10.7) — confirmée ici dans le code final.
- **`_convert_basis_ue_to_godot(ue_basis) -> Basis`** — construit la base de
  conversion `C` explicitement (`Vector3(1,0,0), Vector3(0,0,1),
  Vector3(0,1,0)` — Gx=Ux, Gy=Uz, Gz=Uy, déterminant -1) et calcule
  `C * ue_basis * C.inverse()`, **jamais** une permutation d'angles d'Euler —
  exactement la méthode que la personne avait exigée dès le départ
  (§13.A.4) : *"pas en dérivant les signes à la main"*. Commentaire de tête :
  *"matching Unreal's own glTF exporter convention — verified against the
  actual mesh exports, not just reasoned about in the abstract"* — la trace
  écrite, dans le code final, de la leçon la plus chère du projet (§13.A.5).
- **`godot_pos = (ue.x, ue.z, ue.y) * 0.01`** — identique bit à bit à
  `AXIS_MAP` dans `unreal_export_landscape.py` (§3.4, §6.4). Les deux côtés
  du pipeline sont bien synchronisés à l'heure de cette lecture — mais
  toujours sans vérification croisée automatique (le risque documenté au
  §10 reste valable pour toute évolution future).

Un commentaire de code confirme littéralement le piège d'inférence de type
documenté en §13.B.8 : *"`_transform_from_v10()` returns Transform3D OR
null, so it has no single declared return type - `:=` cannot infer one.
Plain `var` keeps it a Variant"* — la fonction est délibérément déclarée
sans type de retour et appelée avec `var` (jamais `:=`) à chaque site
d'appel, en connaissance de cause.

#### 3.9.5 Décals — voir §3.8 (fusionné mot pour mot)

`_build_decals()`, `UE_DECAL_BASE_SIZE`, `DECAL_MAP_PATH`, `BUILD_DECALS`
sont présents ici exactement comme documenté au §3.8. Une constante
supplémentaire existe, **`DECAL_SIZE_SCALE := 0.9`**, documentée comme
*"multiplicateur global sur l'empreinte du décal... 1.0 = taille UE
exacte"* — mais **jamais référencée nulle part ailleurs dans le fichier**
(voir §12, incohérence latente).

#### 3.9.6 VFX — la partie la plus retravaillée du fichier (V10.9 → V10.12)

De très loin la section la plus longue (environ 1000 lignes). Contrairement
aux décals (conversion fidèle d'une donnée qui existe), les VFX Niagara
**n'ont pas d'équivalent Godot direct** — cette section construit un
**système de substitution visuelle**, explicitement assumé comme tel, pas
comme une conversion.

**Catégorisation par mot-clé** (`_vfx_category_for_name`) : 12 catégories
(candle, torch, fire, smoke, steam, spark, swarm, blood, magic, dust,
water, leaves, snow) + un repli `generic` délibérément terne. Le code
porte lui-même le commentaire de la leçon documentée en §13.B.16 : *"Order
matters: 'candle' is tested before 'flame' so NS_candle_flame does not get
the bonfire preset."*

**Physique réduite pour les catégories qui en ont besoin** (fire/smoke/
steam/candle/torch) : 8 fonctions de loi (`_plume_height_law` en `t^1.5`,
`_plume_width_law`, `_plume_density_law` en dilution `(z-z0)^(-5/3)`,
`_smoke_scale_law`, `_fire_scale_law`, `_candle_scale_law`,
`_fire_density_law`, `_turbulence_influence_law`) — un modèle de panache
flottant à ordre réduit, pas un solveur CFD, référencé en commentaire à un
document externe (`VFX_Physics_Reference_Godot_V10_8.md`, **non fourni
parmi les fichiers lus**). `_curve_from_law()` échantillonne chaque loi en
`CurveTexture` (12 points par défaut) — **et corrige explicitement le
piège documenté en §13.B.9** : `curve.max_value` est recalculé depuis
l'amplitude réelle de la loi (`maxf(1.0, highest)`) au lieu de rester au
défaut 1.0, avec le commentaire *"a law reaching 2.75 (the smoke scale)
would be silently flattened at the default 1.0"* — preuve que la leçon a
bien été encodée en garde-fou, pas seulement racontée.

**Ressources partagées par catégorie** (`_vfx_resources()`, mise en cache
dans `_vfx_cache`) : `ParticleProcessMaterial`, `QuadMesh`, matériau de
dessin — construits **une fois par catégorie**, jamais par instance. C'est
la correction directe du problème des ~2500 ressources/shaders documenté
en §13.B.11.

**Chemin de rendu par défaut sans shader** : `_falloff_texture()` génère un
disque radial via `GradientTexture2D` (opaque au centre, transparent au
bord), combiné à `StandardMaterial3D` avec `vertex_color_use_as_albedo` et
`billboard_mode = BILLBOARD_PARTICLES`. **Rien ici ne peut échouer à
compiler** — la correction directe du bug "carrés blancs" de §13.B.12. Un
shader de déformation plus riche (`_parcel_shader()`, ~400 lignes de GLSL
construisant une flamme à partir de 5 "lobes" elliptiques qui convergent
vers une pointe, avec turbulence FBM) existe et reste disponible, mais est
**explicitement désactivé par défaut** (`VFX_USE_PARCEL_SHADER := false`) —
l'historique de conversation (§13.B.12) documentait déjà cette constante
comme "censée être à false" ; à cette version, **elle l'est effectivement**
(un désaccord entre intention documentée et valeur réelle avait existé
temporairement — voir l'entrée "V10.11" dans l'en-tête du fichier lui-même,
qui décrit et corrige exactement ce décalage).

**Budget de lumières temps réel** (`_vfx_may_add_light` / `VFX_MAX_LIGHTS
:= 24` / `VFX_LIGHT_MIN_SPACING := 6.0`) : empêche les 341 flammes de
bougies de produire 341 `OmniLight3D` — correction directe de §13.B.15.

**Nouveauté non documentée dans les conversations lues (V10.12)** : une
**surcouche d'"embers" (braises)**. Les catégories `fire`/`torch` reçoivent
un **second émetteur séparé**, réutilisant la physique déjà correcte de la
catégorie `spark`, posé par-dessus le corps de flamme désormais
délibérément quasi-statique à la source. Raison documentée en tête de
fichier : à l'ancien réglage (vitesse jusqu'à 2,25 m/s, flottabilité 0,70,
durée de vie 1,15 s) une flamme unique parcourait ~3 m avant de s'éteindre —
un problème visuel concret ("une colonne de feu qui atteint la canopée
au-dessus d'un brasier"). La solution retenue n'est pas de réduire
uniformément le mouvement (ce qui tuerait la sensation de chaleur montante)
mais de **séparer les responsabilités entre deux émetteurs** : le corps de
flamme reste ancré, les braises/cendres portent seule la sensation de
montée. `candle` est explicitement exclu de cette surcouche (une flamme à
l'échelle d'une bougie ne projette pas de braises visibles).

**Calibration de hauteur de flamme** (`_vfx_flame_height_offset`) : le
transform d'un marqueur Niagara est l'origine acteur/composant exportée
depuis Unreal — pour un torch/candle, presque toujours la **base** du mesh
(le pivot est au sol/au socket, pas à la flamme). Sans correction, chaque
flamme apparaîtrait au pied du prop. Un décalage le long de l'axe "haut"
local du marqueur (`transform.basis.y`, **non normalisé** exprès, pour
porter l'échelle propre de l'instance) est appliqué avant toute création de
particule/lumière. Valeurs de référence (`VFX_TORCH_ASSUMED_HEIGHT_M
:= 1.40`, ratio 0.78 ; `VFX_CANDLE_ASSUMED_HEIGHT_M := 0.05`, ratio 1.0) sont
explicitement documentées comme des **estimations, pas des mesures** —
faute d'accès aux bounds réels du GLB par instance depuis cette passe VFX :
*"if the flame still isn't at the right height after a rebuild, that's a
calibration problem, not a direction problem — measure the real torch/
candle mesh height in Unreal (in cm) and set ASSUMED_HEIGHT_M to that
number / 100."*

**`_try_set()`** : utilisé systématiquement pour toute propriété de
particule dont le nom a varié entre versions mineures de Godot 4
(`velocity_pivot`, `turbulence_*`, `use_scale_3d`, `rotation_3d_*`,
`transform_align`, `radial_velocity_*`) — une écriture directe non protégée
casserait tout le constructeur sur un éditeur plus ancien qui n'expose pas
encore la propriété.

#### 3.9.7 Ce que ce fichier utilise / ce qui l'utilise

**Utilise** : les 3 JSON produits côté Unreal (`level_manifest_v10.json`,
`ue5_godot_asset_map.json`, `ue5_godot_decal_map.json`), les GLB sous
`res://UEAssets/Meshes/`. Aucune dépendance à un addon Godot tiers — tout
repose sur l'API native (`ParticleProcessMaterial`, `GradientTexture2D`,
`Decal`, `GPUParticles3D`, `PackedScene`, `ResourceSaver`).
**Ce qui l'utilise** : personne — c'est la feuille terminale de tout le
pipeline, le point où les données deviennent une scène jouable.

#### 3.9.8 Généralisable vs spécifique au projet X

**Générique et réutilisable tel quel** : toute l'architecture de
`_build_geometry_placement` (gestion ISM/HISM, flags de tolérance par
catégorie, dédoublonnage par placement_id), `_transform_from_v10` +
les 3 fonctions de conversion d'axe/quaternion (le cœur mathématique du
fichier), le mécanisme de ressources VFX partagées par catégorie, le
pattern `_try_set`, le budget de lumières, la séparation "corps de flamme
statique + surcouche embers".

**Spécifique au projet X (Necropolis)** : les valeurs numériques précises
des 12 presets VFX (couleurs, durées de vie, vitesses — réglées pour ce
cimetière), les mots-clés de catégorisation eux-mêmes (adaptés au
vocabulaire de nommage `NS_*` de ce projet), les constantes de calibration
`VFX_TORCH_ASSUMED_HEIGHT_M` / `VFX_CANDLE_ASSUMED_HEIGHT_M` (mesurées sur
les meshes de ce pack), le nom de scène de sortie `Map--_REBUILT.tscn`.

---

## 4. Le contrat de données central : `level_manifest_v10.json`

C'est LE schéma à figer/versionner explicitement pour le framework — tout
le reste du pipeline (et le futur reconstructeur généralisé) en dépend.
Clés de premier niveau, avec ce qu'elles contiennent :

| Clé | Contenu | Écrit par | Patché par |
|---|---|---|---|
| `manifest_version` | `"10.0"` (jamais incrémenté malgré V10.1→V10.8, voir §12) | manifest | — |
| `exporter` | name/version/engine/world_resolution | manifest | — |
| `world` | path/name/class du niveau UE | manifest | — |
| `actors` | `records[]` (tous les acteurs, `actor_basic_info`), compteurs par catégorie/origine | manifest | — |
| `level_instances` | `placements[]`, `hierarchy[]` (arbre), `registry{}` (canonique, clé=actor_path), `unique_world_assets{}` | manifest | — |
| `geometry` | `unique_meshes{}`, `placements[]` (LE plus gros volume), `unique_skeletal_meshes{}`, `skeletal_mesh_placements[]` | manifest | landscape (append 1 placement + 1 mesh) |
| `materials` | `unique_materials{}` (params scalar/vector/texture, parent chain) | manifest | — |
| `textures` | `unique_textures{}` (dimensions, format, refs) | manifest | — |
| `blueprints` | `actors[]` (détail composant par composant), compteurs | manifest | — |
| `effects` | `niagara[]`, `decals[]`, `particles[]` | manifest | — |
| `world_features` | `landscape[]`, `lights[]`, `foliage[]`, `audio[]`, `environment[]` | manifest | — |
| `world_partition` | detected/system_actor_count | manifest | — |
| `asset_inventory` | comptes uniques par type d'asset | manifest | — |
| `conversion_diagnostic` | ~30 compteurs + `v10_transform_diagnostic` (détails d'échec) | manifest | — |
| `conversion_risks[]` | par catégorie (LEVEL_INSTANCES/LANDSCAPE/NIAGARA/BLUEPRINTS/STATIC_MESH/DECALS/LIGHTING/TEXTURE_EXTRACTION), severity HIGH/MEDIUM/INFO | manifest | landscape (repasse LANDSCAPE en INFO) |
| `warnings[]` | WORLD_PARTITION/LEVEL_INSTANCE_DUPLICATES/TRANSFORM_CONTEXT/ASSET_DEDUPLICATION | manifest | — |
| `reconstruction` | **le contrat formel de "prêt pour Godot"**, géométrie vs FX séparés | manifest | — |

**Chaque placement de géométrie** (`geometry.placements[]`) porte, après
finalisation : `kind`, `actor{}`, `component{}`, `source_level`,
`level_instance_chain[]`, `mesh` (bounds/lod/collision/nanite/material_slots),
`transform` (source), `transform_extraction_error`, `actor_transform`,
`materials[]`, `mobility`, `collision{}`, `instance_count`,
`instance_transforms[]`, `instance_transform_errors[]`, puis après
`finalize_v10_transforms()` : `placement_id` (hash stable), `source_transform`,
`final_world_transform`, `reconstruction_transform` (= final_world_transform,
redondant intentionnellement — nom "métier" vs nom "technique"),
`transform_space` (`"world"` ou `"composed_world"`), `final_actor_world_transform`,
`instance_final_world_transforms[]`.

**Chaque entrée du registre LevelInstance** porte : identité complète +
`world_asset`, `loaded`/`loaded_level`, `transform`, `parent_level_instance`,
`parent_level_instance_chain[]`, `instance_chain[]`, `depth`,
`encounter_count`/`encounter_sources[]`, `children_actor_paths[]`, `status`
(`LOADED`/`NOT_LOADED`), puis après finalisation : `placement_id`,
`parent_placement_id`, `source_transform`, `final_world_transform`,
`reconstruction_transform`, `transform_space`.

**`transform` (le dict de base, partout dans le fichier)** a toujours la
forme :
```json
{"location": [x, y, z], "rotation": {"pitch": p, "yaw": y, "roll": r}, "scale": [sx, sy, sz]}
```
en **unités et convention Unreal natives** (centimètres, FRotator
pitch/yaw/roll). La conversion vers Godot (cm→m, axes, quaternion) n'a
**jamais lieu côté Unreal** — elle est **entièrement déléguée au
reconstructeur `.gd`**, via `_transform_from_v10()`. C'est un choix
d'architecture explicite : le manifeste reste une trace fidèle de la scène
source, indépendante du moteur cible.

---

## 5. Les deux autres fichiers d'échange

### `ue5_godot_asset_map.json`
```json
{
  "exporter_version": "1.0", "manifest_version": "10.0", "format": "glb",
  "godot_asset_root": "res://UEAssets", "godot_mesh_root": "res://UEAssets/Meshes",
  "source_manifest": "...", "unique_mesh_count": N, "exported_count": N,
  "existing_count": N, "failure_count": N,
  "assets": {
    "<ue_path>": {
      "ue_path": "...", "ue_name": "...", "godot_path": "res://UEAssets/Meshes/Name_HASH.glb",
      "disk_path": "...", "format": "glb", "status": "EXPORTED|EXISTING",
      "export_warnings": "..."  // optionnel
    }
  },
  "failures": [{"ue_path": "...", "reason": "..."}],
  "notes": ["..."]
}
```
Le Landscape y ajoute une entrée synthétique sous la clé
`/AutoTerrain/Landscape.BakedLandscape`, avec des champs additionnels
(`source`, `axis_map`, `vertex_count`, `triangle_count`, `note`) absents des
entrées normales — **le reconstructeur doit tolérer des champs optionnels
par asset**, pas assumer un schéma rigide identique pour toutes les entrées.

### `ue5_godot_decal_map.json`
```json
{
  "exporter_version": "1.0", "manifest_version": "10.0",
  "godot_decal_root": "res://UEAssets/Decals",
  "decal_materials": {
    "<ue_material_path>": {
      "ue_material_path": "...", "name": "...", "godot_path": "...", "disk_path": "...",
      "placement_count": N, "tint": [r,g,b], "tint_parameter": "...",
      "mask_texture": "...", "mask_parameter": "...", "resolution": [w,h],
      "alpha_mean": f, "alpha_min": i, "alpha_max": i,
      "warning": "...",              // optionnel : masque plat détecté
      "normal_godot_path": "..."     // optionnel
    }
  },
  "decal_placement_total": N,
  "vfx": {"converted": false, "reason": "...", "systems": {"<name>": count}, "placement_total": N},
  "notes": ["..."]
}
```
**Clé de conception à retenir** : le mapping est **par matériau de décal**,
pas par placement — un placement individuel (dans `effects.decals[]` du
manifeste) référence son matériau par path, et le reconstructeur doit
**joindre les deux fichiers** pour obtenir la texture réelle. Le nombre de
textures physiques (5 sur ce projet) est donc bien inférieur au nombre de
placements (1423) — pattern de déduplication identique à celui des meshes.

---

## 6. Patterns et algorithmes transversaux

### 6.1 Le triptyque "safe_*" — ne jamais laisser une exception casser le scan
`safe_call(function, default)`, `safe_property(obj, name, default)`,
`safe_int/float/bool(value, default)`. Omniprésent dans le manifeste : une
scène de 5929 acteurs avec des Blueprints custom et des versions d'API
variables **va** avoir des accès qui échouent ponctuellement ; le principe
est de **continuer le scan et signaler**, jamais de planter. C'est ce qui
permet au manifeste de rester exploitable même à 100% d'échec sur une
catégorie entière (cas réel : Decal/Niagara/Light/Audio avant V10.3).

### 6.2 Identité stable des objets UE
`object_path()` (préféré, fallback sur `get_name()`), `object_name()`,
`class_name()`, `class_path()` — utilisés systématiquement comme **clé de
dédoublonnage** dans tous les registres (mesh/matériau/texture/LI) et comme
**identifiant traçable** dans toutes les métadonnées Godot. `stable_id(prefix,
path)` (SHA1 tronqué à 16 caractères) génère les `placement_id` — **ce sont
ces IDs, pas les chemins UE bruts, qui devraient servir de clé primaire dans
un futur schéma de framework**, car ils sont compacts et stables.

### 6.3 Le pattern "retourne (valeur, erreur), n'avale rien"
Introduit en V10.1 (`component_transform_diagnostic`), généralisé à travers
tout le fichier de composition de transform. **C'est le pattern qui a permis
de découvrir et corriger 8 versions de bugs successifs** (§7) — sans lui,
chaque bug se serait manifesté comme un simple "ça ne marche pas" sans piste.
**À imposer comme convention dans tout code du futur framework qui touche à
l'extraction de données UE.**

### 6.4 Conversion d'axe UE → Godot (la convention finale, validée)
```
godot.x =  ue.x
godot.y =  ue.z
godot.z =  ue.y
godot   *= 0.01   (cm → m)
```
Déterminant **-1** (inversion de handedness), **choisi pour matcher la
convention de l'exporteur glTF natif d'Unreal**, pas une convention
"sémantique" dérivée à la main (avant/droite/haut) — piège vécu deux fois
(le `.gd` d'Oumi et une tentative indépendante de correctif ont chacun essayé
une variante différente avant de converger sur celle-ci, voir §7). Deux
conséquences mécaniques à ne jamais oublier lors d'une réimplémentation :
- **le winding des triangles doit être inversé** partout où une géométrie
  est écrite à la main (voir `build_grid_mesh()`) ;
- **la conversion de rotation ne peut pas être une simple permutation de
  composantes d'angle** — elle doit passer par un quaternion/une matrice de
  base, avec un ou deux signes inversés déterminés empiriquement (le fichier
  `.gd`, absent, contient le correctif exact : `qx`/`qy` inversés par
  rapport à `FRotator::Quaternion()`).

### 6.5 Raycasting en grille contre le terrain (implémenté 2 fois indépendamment)
`sample_landscape_heights()` (dans le générateur de matériau) et
`sample_heightfield()` (dans l'export Landscape) partagent le même
algorithme : grille régulière, tir vertical `line_trace_single`, ignore-list
cumulative pour transpercer les props. **Candidat évident de factorisation**
dans le framework — une seule fonction utilitaire `raycast_grid_heights()`
paramétrée par (bounds, résolution, canal de trace, prédicat "est-ce le
terrain").

### 6.6 Écriture de GLB "à la main" (sans bibliothèque)
`write_glb()` (Landscape) construit un glTF binaire complet en pur Python :
un buffer, des bufferViews avec offsets calculés/paddés manuellement à 4
octets, des accessors avec `min`/`max` requis par la spec pour `POSITION`,
JSON paddé à l'espace. **Un pattern réutilisable** pour tout export de
géométrie procédurale future (ex. : un autre système que le Landscape qui
n'aurait pas d'équivalent exportable nativement).

### 6.7 Registres dédupliqués avec back-references
Le pattern `{path: {...info..., usage_count, xxx_references[]}}` revient
identiquement pour meshes, matériaux, textures, et décals-par-matériau. À
formaliser comme une structure générique (`AssetRegistry<T>`) dans le
framework plutôt que 4 implémentations parallèles quasi-identiques.

### 6.8 Configuration en constantes de module, jamais en paramètres
**Chaque script est un `main()` autonome** lisant des constantes définies en
haut de fichier (`OUTPUT_PATH`, `GRID_RESOLUTION`, `MASK_PARAMETER_NAMES`,
etc.) — **aucun des 8 fichiers n'expose une fonction paramétrable ou une
interface CLI/API**. C'est le changement structurel n°1 requis pour qu'un
orchestrateur puisse piloter ces étapes plutôt que les invoquer une par une,
à la main, dans l'éditeur (§9, §11).

---

## 7. Chronologie des bugs — pourquoi chaque règle existe

Cette chronologie **doit être conservée** dans la documentation du futur
framework : chaque règle ci-dessous encode un bug réel, mesuré, sur une
scène de production (5929 acteurs). Les reperdre pendant la généralisation
reproduirait les mêmes symptômes.

1. **V10.1** — Introduction du pattern `(valeur, erreur)` partout au lieu
   d'avaler les exceptions ; séparation `empty_mesh_slot_count` vs
   `missing_mesh_references` (faux positifs) ; capture des SkeletalMesh
   (comptés avant, jamais enregistrés) ; diagnostic FX séparé de la
   géométrie ; correction d'un bug où `conversion_diagnostic` entier
   écrasait `v10_transform_diagnostic` déjà calculé (un dict litéral
   remplaçait tout au lieu de fusionner) ; collision de noms de fichiers
   GLB entre assets homonymes de dossiers différents.
2. **V10.2** — Le même diagnostic staleness/re-fetch appliqué aux
   composants Decal (pas seulement StaticMesh).
3. **V10.3 — Cause racine, le bug le plus important du fichier** : sur
   Decal/Niagara/Light/Audio, `get_component_transform()` lève
   `AttributeError` à 100% alors que `get_editor_property()` sur les mêmes
   objets fonctionne parfaitement (matériau, taille, intensité, couleur,
   volume tous lisibles). Ce n'est **pas** un objet mort — seule la méthode
   Python n'est pas bindée pour ces classes. Correctif : reconstruire le
   transform depuis `relative_location/relative_rotation/relative_scale3d`
   en remontant `attach_parent`.
4. **V10.4** — Les ISM/HISM ne stockaient que le transform du composant
   (donc 1 instance rendue au lieu de N, jusqu'à 165 perdues pour un seul
   foliage) : lecture de `get_instance_transform(i, world_space=True)` pour
   *chaque* instance, avec 3 signatures d'appel essayées, et composition
   individuelle de chaque instance à travers la chaîne LI.
5. **V10.5 — Ordre de composition des transforms** : Unreal compose
   `child * parent` (`NewTransform = RelativeTransform * ParentToWorld`),
   le code composait `parent * child` (inversé). Invisible tant qu'aucun LI
   de la chaîne n'avait de rotation non-identité (translation pure =
   commutative) ; dès qu'une rotation intervenait, tout ce qui en dépendait
   se retrouvait dispersé de façon apparemment aléatoire dans la scène.
6. **V10.6** — `unreal.Rotator` a la signature Python `(roll, pitch, yaw)`,
   **pas** `(pitch, yaw, roll)` comme le constructeur C++ `FRotator`. Passer
   les arguments positionnellement dans l'ordre C++ permutait silencieusement
   chaque rotation. Correctif : toujours des arguments nommés.
7. **V10.7 — Double comptage sur les chaînes profondes** : le transform
   d'une LevelInstance stocké par `level_instance_info()` vient de
   `get_actor_transform()`, donc **déjà en espace monde**, pas local à son
   parent. Composer toute la chaîne appliquait chaque ancêtre deux fois.
   Seuls les objets à profondeur ≥2 étaient touchés (580 sur 7872, jusqu'à
   ~230m d'écart). Correctif : ne composer que le maillon le plus interne.
   Mesuré : écart médian composant↔acteur 48m → 3m.
8. **V10.8 (dernière version présente)** — Repli V10.3 remontait jusqu'au
   composant racine et utilisait SA relative comme transform monde — valide
   pour un `StaticMeshActor` classique, faux pour un Blueprint (la relative
   du root component y vaut l'identité ; la position monde vit sur
   l'ACTEUR). 327 placements (94 petits piliers, 50 grands piliers, 39
   torches, plus grilles/statues/pots) atterrissaient tous à l'origine.
   Correctif définitif : partir de `actor_transform()` (fiabilité mesurée :
   0 échec sur toute la scène) et composer **seulement** les relatives des
   composants **non-racines**.

**Côté `.gd` (fichier maintenant lu intégralement, §3.9 — correctifs
confirmés dans le code final)** :
9. **V10.7** — signes de `qx`/`qy` inversés dans `_unreal_rotator_to_basis()`
   par rapport à `FRotator::Quaternion()`. Une rotation yaw-seul annulait
   l'erreur (d'où des bâtiments qui semblaient corrects) ; tout ce qui avait
   du pitch ou du roll ressortait en miroir. Correctif visible dans le code
   final : `qy := -cr * sp * cy - sr * cp * sy` (signe négatif explicite).
10. **V10.8** — le déterminant de la conversion d'axe passe de +1 à -1
    (`_convert_basis_ue_to_godot`, base `Gx=Ux, Gy=Uz, Gz=Uy`) pour matcher
    la handedness déjà inversée par l'exporteur glTF natif d'Unreal. Une
    tentative antérieure de changer le mapping vers un preset "sémantique"
    (y,z,-x) avait **empiré le rendu** et été annulée avant d'arriver à
    cette version — la bonne convention ne se déduit jamais par
    raisonnement géométrique seul (voir §13.A.5).
11. **V10.9 → V10.10** — première passe décals + VFX, puis réécriture
    complète du module VFX après qu'un shader personnalisé (`parcel
    shader`) ait produit des carrés blancs opaques sur les rendus de test
    (Godot retombe sur le matériau par défaut d'un `QuadMesh` quand un
    shader échoue à recevoir `COLOR`/`INSTANCE_CUSTOM` correctement — voir
    §13.B.12). Base par défaut reconstruite sans aucun shader
    (`GradientTexture2D` + `vertex_color_use_as_albedo` + `BILLBOARD_
    PARTICLES`).
12. **V10.11** — `VFX_USE_PARCEL_SHADER` était **documentée** comme
    "désactivée par défaut" mais la constante elle-même était restée à
    `true` : chaque catégorie marquée `"parcel": true` (candle, fire,
    smoke, steam) heurtait donc encore le chemin shader non vérifié. La
    constante correspond maintenant réellement à son intention documentée
    (`false`). `torch` a aussi cessé de partager le preset "bonfire" de
    `fire` (trop grand pour une torche murale/tenue en main) : preset
    dédié, dimensionné entre `candle` et `fire`.
13. **V10.12** — `fire` parcourait encore ~3 m avant de s'éteindre (un
    brasier dont la flamme atteignait la canopée d'un arbre au-dessus).
    Portée ramenée à ~0,8 m, et le corps de flamme rendu délibérément
    quasi-statique à la source ; un **second émetteur séparé**, réutilisant
    la physique déjà correcte de la catégorie `spark`, est superposé pour
    `fire`/`torch` afin que la sensation de montée vienne des braises, pas
    de la flamme elle-même qui s'étire.

**Leçon transversale** : presque tous ces bugs partagent la même signature —
*"ça marche pour le cas simple (translation pure / StaticMeshActor / racine
peu profonde) et casse silencieusement dès qu'un cas plus complexe apparaît
(rotation / Blueprint / profondeur ≥2)"*. Le framework généralisé doit être
**testé systématiquement contre ces 4 axes de complexité** (rotation
non-identité, origine Blueprint, LevelInstances imbriqués, ISM multi-instance)
et pas seulement contre le cas trivial.

---

## 8. Déjà généralisable vs encore lié au projet X (Necropolis)

### Généralisable tel quel (aucune référence au projet)
- `ue5_preprocess_detach_all.py` — entièrement neutre.
- `png_codec.py` — entièrement neutre, zéro dépendance UE.
- `unreal_export_manifest_v10-8.py` — logique de scan/classification/
  composition/diagnostic 100% générique ; seuls les chemins de sortie sont
  en dur.
- `unreal_export_godot_assets_PATCHED.py` — générique, chemins en dur
  seulement.
- L'algorithme de `unreal_export_landscape.py` (raycast + bake + writer GLB
  maison) — générique ; chemins, résolutions et convention d'axe à
  paramétrer.
- La logique de `unreal_export_decals_vfx.py` (cascade de résolution de
  texture, bake par render target, composition RGBA, détection de masque
  plat) — générique ; les *listes de noms de paramètres* ne le sont pas.
- Le pattern de `godot_decals_vfx_addition.gd` (préchargement dédupliqué,
  group-by pour marqueurs non convertis, métadonnées de traçabilité).

### Encore lié au projet X
- **`auto_terrain_generator_ue55.py` en entier** — pack Necropolis, assets
  Quixel spécifiques, réglages esthétiques. Hors scope de la conversion.
- `MASK_PARAMETER_NAMES` / `TINT_PARAMETER_NAMES` / `NORMAL_PARAMETER_NAMES`
  dans `unreal_export_decals_vfx.py` — conventions de nommage du pack
  Necropolis (`M_Decals_01`, `Tint 01/02`).
- Tous les chemins `C:/Export/...` et `res://UEAssets/...` codés en dur dans
  les 4 scripts d'export.
- `LANDSCAPE_UE_PATH = "/AutoTerrain/Landscape.BakedLandscape"` — nom
  synthétique arbitraire, sans conséquence fonctionnelle mais à rendre
  configurable.

---

## 9. Ce qu'il faudra généraliser, point par point

1. **Paramétrer chaque script** : remplacer les constantes de module par une
   fonction `run(config: dict)` (ou dataclass), pour que l'orchestrateur
   puisse les invoquer avec des chemins/résolutions différents sans éditer
   le code source à chaque map.
2. **Recevoir la config depuis l'orchestrateur** — puisque ces scripts
   s'exécutent **dans le contexte Python embarqué de l'éditeur Unreal**
   (`import unreal`), l'orchestrateur devra soit (a) les piloter via
   l'API distante d'Unreal (remote execution / Python bridge), soit
   (b) générer un script de config temporaire lu au démarrage, soit
   (c) les exposer comme un plugin/commandlet UE invocable en ligne de
   commande. À trancher explicitement dans la conception de l'orchestrateur.
3. **Externaliser les conventions de nommage de matériau** (décals) dans un
   fichier de config **par pack d'assets**, pas en dur dans le code —
   potentiellement un système de "profils" sélectionnables (Necropolis,
   Quixel générique, etc.), avec une résolution en cascade générique déjà
   présente comme fallback ultime.
4. **Exporter les SkeletalMesh** — capturés dans le manifeste
   (`geometry.unique_skeletal_meshes`) depuis V10.1 mais jamais exportés en
   GLB par `unreal_export_godot_assets_PATCHED.py`. Trou à combler.
5. **Généraliser la classification par substring** (`actor_category`,
   `component_kind`) vers un système extensible (table de correspondance
   externe classe→catégorie, avec règles par défaut + surcharge par
   projet), pour supporter des classes d'acteur custom sans toucher au code
   du scanner.
6. **Multi-Landscape** — le script actuel ne traite qu'un seul Landscape
   "primaire" avec avertissement s'il y en a plusieurs. À généraliser si le
   framework doit supporter des maps multi-terrain.
7. **Automatiser le pré-traitement manuel** — casser les LevelInstances est
   actuellement 100% manuel via l'UI. Si l'API `unreal.LevelInstanceEditor
   Subsystem` (ou équivalent) expose une opération de "break" scriptable,
   l'intégrer à `ue5_preprocess_detach_all.py` supprimerait une étape
   humaine source d'erreur.
8. **Vérification croisée automatique** entre les 3 JSON (au lieu de la
   note manuelle "Asset-map entries doit égaler Manifest unique meshes") —
   un contrôle de cohérence formel, exécutable, avant de lancer le
   reconstructeur.
9. **Versionner réellement le schéma** — `manifest_version` est resté figé à
   `"10.0"` à travers 8 sous-versions de bugfix (V10.1→V10.8). Le framework
   devrait soit versionner à chaque évolution de schéma réelle, soit
   documenter explicitement que le numéro de version ne reflète que des
   changements de *forme* du JSON, pas de comportement interne.
10. **Le reconstructeur `.gd` (§3.9)** — maintenant lu intégralement ; ce
    qui reste à faire n'est plus "l'obtenir" mais **l'extraire de son mode
    d'exécution `EditorScript`**. Pour un framework pilotable, il devra
    devenir soit un script Godot headless (`--headless --script ...`, via
    `SceneTree` plutôt que `EditorScript`, si l'API utilisée le permet en
    dehors de l'éditeur), soit un plugin/commande exposée que
    l'orchestrateur peut déclencher sans intervention humaine dans
    l'éditeur. Externaliser aussi les ~15 presets/constantes VFX
    (catégories, mots-clés, couleurs, budget de lumières, hauteurs de
    calibration torch/candle) et les 5 `FAIL_ON_*` en configuration,
    exactement comme pour les scripts côté Unreal (§9.1).
10b. **Donner au reconstructeur accès aux bounds réels du GLB par
    instance** — `_vfx_flame_height_offset` calibre la hauteur de flamme
    sur des constantes estimées (`VFX_TORCH_ASSUMED_HEIGHT_M`, etc.) faute
    d'accès aux dimensions réelles du mesh depuis la passe VFX. Si le
    framework expose les bounds du GLB (déjà capturés côté manifeste,
    `mesh.bounds`) à cette étape, la calibration peut devenir automatique
    plutôt qu'estimée à la main par projet.
10c. **Faire consommer au reconstructeur le contrat `reconstruction` du
    manifeste** — actuellement, `ready_for_godot_geometry` /
    `ready_for_godot_fx` sont calculés côté Python mais jamais lus côté
    `.gd`, qui refait sa propre validation complète indépendamment. Un
    framework devrait faire porter la vérité par un seul côté (probablement
    le manifeste, puisqu'il a la vue la plus complète) et faire du
    reconstructeur un simple consommateur de ce verdict, pour éliminer le
    risque de divergence entre deux validations qui ne se parlent pas.
11. **Stratégie Blueprint** — explicitement hors scope
    (`blueprint_logic_included: false`) : le manifeste capture la
    *structure* des composants d'un Blueprint (transform, mesh, matériaux)
    mais jamais son comportement scripté. Le framework doit documenter cette
    limite clairement comme un choix assumé, pas un oubli — et éventuellement
    exposer un point d'extension pour qu'un utilisateur mappe certains
    Blueprints connus (ex. portes, leviers) vers des scènes Godot
    équivalentes fournies à la main.
12. **Table de correspondance axe UE→Godot vérifiable automatiquement** —
    actuellement stockée comme simple *label* texte (`AXIS_MAP_LABEL`,
    `axis_map` dans le JSON) sans aucune vérification croisée avec ce que le
    `.gd` utilise réellement. Un framework robuste devrait faire porter
    cette convention par **une seule source de vérité partagée** (un fichier
    de config lu par les deux côtés, Python et GDScript) plutôt que deux
    implémentations synchronisées à la main.

---

## 10. Contraintes et pièges qui peuvent tout casser

- **Ordre d'exécution strict** : manifeste → assets → **Landscape en
  dernier** parmi les scripts qui réécrivent from-scratch (les scripts 3 et
  4 ne font qu'ajouter, mais scripts 1 et 2 réécrivent tout — les relancer
  après 3/4 efface leur travail).
- **`png_codec.py` doit être physiquement à côté** de
  `unreal_export_decals_vfx.py` (chargement par chemin relatif au fichier,
  pas par import de package Python standard).
- **KEEP_WORLD explicite obligatoire** sur les 3 règles de
  `detach_from_actor()` — le défaut de l'API (`KEEP_RELATIVE`) déplacerait
  silencieusement les acteurs.
- **`unreal.Rotator` en Python attend `(roll, pitch, yaw)`**, jamais l'ordre
  C++ `(pitch, yaw, roll)` — toujours utiliser des kwargs, jamais des
  positionnels, pour tout code qui construit un `Rotator`.
- **La composition de transform Unreal est `child * parent`**, jamais
  l'inverse — vrai pour `compose_chain_transform`, pour les LI imbriquées,
  et implicitement pour toute logique de composition future.
- **Le transform stocké pour une LevelInstance est déjà en espace monde**
  (vient de `get_actor_transform()`) — ne jamais le recomposer une deuxième
  fois avec ses propres ancêtres déjà inclus.
- **`get_component_transform()` n'est PAS fiable sur
  Decal/Niagara/Light/Audio** (et sur certains composants de Blueprint) —
  toujours passer par le diagnostic + repli sur les propriétés, jamais
  utiliser cette méthode nue pour ces classes.
- **La base du repli par propriétés doit être l'acteur, jamais le composant
  racine** — sinon tout Blueprint dont le root a une relative identité
  atterrit à l'origine.
- **La convention d'axe (`ue.x, ue.z, ue.y`, déterminant -1) doit être
  identique des deux côtés du pipeline** (script d'export landscape ET
  reconstructeur `.gd`) — aucune vérification automatique n'existe
  actuellement ; un futur refactor de l'un sans l'autre romprait tout
  silencieusement (déjà arrivé une fois, corrigé, annulé, re-corrigé).
- **Le winding des triangles dépend du déterminant de la conversion d'axe**
  — un déterminant -1 (handedness inversée) exige d'inverser l'ordre des
  indices, sous peine de géométrie invisible (faces retournées).
- **World Partition limite la garantie du scan au contenu chargé/accessible**
  — le manifeste le documente explicitement comme un warning permanent, pas
  une erreur ; un futur orchestrateur devrait s'assurer que toutes les
  cellules pertinentes sont chargées avant de lancer le scan.
- **`GLTFExporter` doit être activé comme plugin UE** pour que l'export de
  StaticMesh fonctionne — sinon `unreal.GLTFExporter` est `None` et l'export
  échoue proprement avec un message explicite, mais c'est un prérequis
  d'environnement à documenter/vérifier dans l'orchestrateur.
- **Le SceneCapture2D doit toujours être détruit**, y compris en cas
  d'échec (pattern `finally` déjà en place dans le code existant) — sinon
  des relances répétées polluent le niveau d'acteurs temporaires.
- **Le reconstructeur est un `EditorScript` : il ne s'exécute que dans le
  contexte de l'éditeur Godot, déclenché manuellement.** Ce n'est pas un
  détail cosmétique — tant qu'il reste sous cette forme, aucun
  orchestrateur externe ne peut l'invoquer sans qu'un humain clique sur
  *Run* dans le panneau FileSystem (ou sans passer par l'automatisation
  d'éditeur de Godot, si elle est disponible et fiable pour ce cas d'usage).
  C'est la contrainte la plus structurante pour la conception de
  l'orchestrateur (voir §9 point 10 et §11).
- **Un échec de `PackedScene.pack()` ou de `ResourceSaver.save()` libère
  toute la racine (`root.free()`) sans écrire de fichier** — bonne
  propriété (jamais de `.tscn` à moitié construit), mais signifie qu'un
  orchestrateur qui attend un fichier de sortie doit vérifier sa présence
  réelle après l'exécution, pas seulement l'absence de code de retour
  d'erreur (il n'y en a pas, puisque l'exécution se fait dans l'éditeur).
- **Les deux côtés du pipeline valident chacun indépendamment** — le
  reconstructeur ne lit jamais le bloc `reconstruction` du manifeste et
  refait sa propre passe de validation. Un futur consommateur ne doit pas
  supposer que "le manifeste dit prêt" garantit que "le `.gd` accepte tout"
  ou inversement.

---

## 11. Ce dont l'orchestrateur a besoin

D'après le contexte donné en plus par Oumi (l'orchestrateur prend en entrée
**la map Unreal complète comme source de vérité** + **le projet Godot de
destination** + **une configuration avancée**, et gère tout à partir de là),
voici ce que cette lecture exhaustive permet de préciser :

**Étapes à orchestrer, dans l'ordre, avec leurs dépendances de données**
(reprend exactement §1, condensé pour référence rapide) :
```
0. detach_all         (état éditeur uniquement, aucune I/O fichier)
1. manifest           (écrit : manifest.json)                    [from scratch]
2. assets             (lit : manifest.json  | écrit : asset_map.json + *.glb)  [from scratch]
3. landscape          (lit+écrit : manifest.json, asset_map.json | écrit : landscape.glb) [patch]
4. decals_vfx         (lit : manifest.json | écrit : decal_map.json + *.png)   [patch sur rien d'autre]
5. copie              (déplace les 2 dossiers + 3 JSON vers res:// du projet Godot cible)
6. reconstruction .gd (lit les 3 JSON | écrit : la scène .tscn)
```

**Points de configuration à exposer** (actuellement tous en dur, dispersés
dans les 6 fichiers) :
- chemins d'export UE (`C:/Export/...`) et racine Godot (`res://UEAssets`) ;
- chemin du projet Godot de destination (jamais géré par les scripts actuels
  — la "copie" à l'étape 5 est **entièrement manuelle** aujourd'hui) ;
- `GRID_RESOLUTION` / `TEXTURE_RESOLUTION` du Landscape ;
- listes de noms de paramètres matériau pour les décals (par pack d'assets) ;
- convention d'axe (actuellement une seule constante `AXIS_MAP`, mais à
  synchroniser avec le `.gd`) ;
- activer/désactiver chaque étape optionnelle (Landscape, décals/VFX) —
  toutes les maps n'ont pas forcément un Landscape ou des décals.

**Ce que l'orchestrateur doit vérifier/garantir (actuellement manuel)** :
- que le pré-traitement (étape 0 + break LI manuel) a bien été fait avant le
  scan — aucun signal actuel dans le manifeste ne prouve que
  `ue5_preprocess_detach_all.py` a tourné ;
- cohérence `asset_map.json["assets"]` ↔ `manifest.json["geometry"]
  ["unique_meshes"]` (compte égal) ;
- `reconstruction.ready_for_godot_geometry` et `ready_for_godot_fx` à `true`
  avant de lancer la reconstruction Godot — actuellement seulement visible
  dans les logs console, jamais vérifié programmatiquement en aval ;
- que `GLTFExporter` est bien disponible avant de lancer l'étape 2 ;
- ordre d'exécution correct (ne jamais relancer manifest/assets après
  landscape/decals sans tout refaire depuis le début).

**Interface d'invocation à concevoir** : puisque les scripts actuels
tournent **dans** l'éditeur Unreal (session Python embarquée, `import
unreal`), l'orchestrateur — s'il vit en dehors d'Unreal — doit choisir entre
piloter Unreal à distance (remote Python execution, souvent via un socket
ou un plugin d'exécution de commande), ou être lui-même un outil qui
s'exécute *dans* Unreal (menu éditeur / commandlet) et appelle
optionnellement des utilitaires externes pour la partie Godot. C'est une
décision d'architecture à prendre tôt, car elle détermine si les 6 scripts
deviennent des modules important par un orchestrateur unique (nécessite de
les rendre importables/paramétrables, §9.1) ou restent des scripts déclenchés
séquentiellement par un mécanisme externe.

---

## 12. Incohérences et bugs latents repérés pendant cette lecture

Ces points n'étaient pas nécessairement connus d'Oumi — repérés par lecture
attentive et croisée du code, à vérifier/corriger pendant la généralisation :

1. **`asset_inventory.skeletal_mesh_assets` reste toujours codé à `0`** dans
   `unreal_export_manifest_v10-8.py`, alors que
   `geometry.unique_skeletal_meshes{}` est bien peuplé depuis V10.1. Le
   compteur récapitulatif n'a jamais été branché sur le registre réel — un
   consommateur du manifeste qui ne lirait que `asset_inventory` croirait
   qu'il n'y a aucun SkeletalMesh dans la scène.
2. **`component_transform()` brut (sans diagnostic) reste utilisé** pour les
   composants de type `particle` (bloc `elif kind == "particle"` dans la
   boucle principale) et pour tous les `blueprint_component_detail()` (tous
   types confondus, y compris Decal/Niagara/Light/Audio à l'intérieur d'un
   Blueprint) — alors que le diagnostic robuste
   (`component_transform_diagnostic` + repli propriétés) n'est appliqué
   qu'aux composants **directement attachés à un acteur non-Blueprint**. Un
   Blueprint contenant un Decal ou un Niagara pourrait donc silencieusement
   récupérer `transform: null` sans jamais passer par le correctif V10.3/V10.8.
3. **Le numéro de version du schéma (`manifest_version: "10.0"`) n'a jamais
   bougé** malgré 8 sous-versions de correctifs de comportement (V10.1 à
   V10.8) - seul du texte en commentaire de fichier documente la version
   réelle. Un consommateur externe versionnant strictement sur ce champ ne
   verrait aucune différence entre un manifeste V10.1 (bugué) et V10.8 (corrigé).
4. **`AXIS_MAP_LABEL` est stocké mais jamais vérifié** — aucun code ne
   compare la valeur déclarée dans le JSON à la convention réellement
   implémentée côté `.gd`. C'est une trace de documentation, pas un
   contrôle de cohérence actif.
5. **Léger flottement de numérotation des étapes entre les headers de
   fichiers** : `unreal_export_landscape.py` se déclare "script 4" et liste
   4 étapes totales (sans les décals) ; `unreal_export_decals_vfx.py` se
   déclare "script 5 de 6" et inclut le Landscape comme étape 4 — cohérent
   entre eux, mais le premier a manifestement été écrit avant que
   l'étape décals n'existe et n'a pas été mis à jour. Sans conséquence
   fonctionnelle, mais à corriger dans la documentation du framework pour
   éviter toute confusion future.
6. **`unreal_export_godot_assets_PATCHED.py` n'exporte que les
   StaticMesh** (`geometry.unique_meshes`) — les SkeletalMesh n'ont donc
   aujourd'hui **aucun chemin d'export GLB implémenté nulle part** dans le
   pipeline, malgré leur capture au niveau du manifeste.
7. **Le reconstructeur ne lit jamais le contrat `reconstruction` du
   manifeste** (`ready_for_godot_geometry`, `ready_for_godot_fx`) — les deux
   moitiés du pipeline calculent chacune leur propre verdict de "prêt",
   indépendamment, sans jamais se référencer. Repéré en lisant
   `ue5_godot_map_constructor_v10_PATCHED.gd` en entier (§3.9.2) : aucune
   occurrence de `ready_for_godot` dans tout le fichier.
8. **`DECAL_SIZE_SCALE := 0.9`** est déclarée dans le reconstructeur,
   documentée comme *"multiplicateur global sur l'empreinte du décal"*,
   mais **n'est référencée nulle part ailleurs dans le fichier** — le calcul
   de `decal.size` (`_build_decals()`) n'y fait jamais appel. Un réglage
   qui a l'air actif dans le code (et dans tout audit rapide du fichier) est
   en réalité inerte.
9. **`stats["recomputed_li_transforms"]`** est déclaré, imprimé en fin de
   rapport (`_print_report()`), mais **jamais incrémenté** nulle part dans
   le fichier — un compteur systématiquement affiché à 0, qui laisse croire
   à un mécanisme de recalcul de transform LI côté Godot qui n'existe pas
   (cohérent avec l'architecture documentée : les LI ne portent
   délibérément aucun transform appliqué côté `.gd`, la géométrie porte
   déjà son transform monde final — voir §3.9.1/§3.9.7 — mais le compteur
   mort suggère malgré tout une intention non finalisée).

---

## 13. Leçons tirées de l'historique des conversations — ce qu'il ne faut plus refaire

Cette section vient d'une fouille des conversations passées (recherche par mot-clé,
plusieurs dizaines de sessions couvrant le développement du pipeline depuis sa V1
jusqu'à la V10.10 côté `.gd`, plus le générateur de matériau de terrain depuis sa v1
jusqu'à sa v9), **volontairement indépendante du code actuellement présent** dans les
fichiers. Le code montre l'état final validé ; les conversations montrent **le chemin
pour y arriver**, y compris toutes les versions intermédiaires fausses, les
hypothèses rejetées, et les erreurs de Claude lui-même. C'est cette partie-là qui
constitue la vraie liste de "ce qu'il ne faut pas refaire" — le code seul ne la
révèle jamais, puisqu'un fichier final ne montre que ce qui a fini par marcher.

Deux fils de discussion étroitement liés au projet ont fourni l'essentiel de cette
matière : *"Appliquer des textures au landscape"* (le fil le plus long, qui couvre
tout le pipeline d'export V10→V10.10 ainsi que le générateur de matériau v1→v7.1) et
*"Couches peignables sur matériau procédural Landscape"* (l'épisode des couches de
peinture manuelle et du terrain qui rendait noir).

### A. Épistémologie du debug — comment ne plus se tromper de piste

1. **Ne jamais diagnostiquer un symptôme visuel seul.** Face à une capture d'écran
   ("un tas d'objets à l'origine", "des carrés blancs", "un damier gris"), la
   première question n'est jamais "qu'est-ce que ça pourrait être ?" mais "qu'est-ce
   que je peux **mesurer** dans les données réelles (le manifeste JSON) pour le
   confirmer ?". Principe explicitement formulé et systématiquement appliqué dans
   tout le fil : *"la méthode qui a marché à chaque fois reste la mesure."* Chaque
   bug corrigé (327 placements à l'origine, écart médian composant↔acteur, etc.) a
   été localisé par comptage/mesure sur le JSON, jamais par inspection visuelle seule
   de la scène rendue.
2. **Une hypothèse "objet mort / référence périmée" doit être testée en vérifiant si
   D'AUTRES propriétés du même objet sont lisibles.** Si le matériau, la taille,
   l'intensité, la couleur, le volume se lisent tous parfaitement via
   `get_editor_property()` mais qu'une seule méthode précise
   (`get_component_transform()`) échoue à 100%, ce n'est **pas** un objet invalide —
   c'est un binding Python manquant pour cette méthode sur cette classe. La piste
   "référence périmée + re-fetch par chemin" a été explicitement formulée, testée, et
   **rejetée** ("j'avais tort sur mes deux hypothèses, et tes données le prouvaient
   déjà") avant de trouver la vraie cause. Le test décisif : comparer le taux
   d'échec d'une méthode d'ACTEUR (0 sur 5864) à celui de la méthode de COMPOSANT sur
   les mêmes classes d'objets (100% sur 2106) — une asymétrie aussi nette entre deux
   API sur le même objet pointe vers un problème de binding, pas vers un objet mort.
3. **Un désaccord de chiralité (handedness) sur une conversion d'axe est invisible
   sur les objets symétriques.** Piliers, murs, tombes ne révèlent rien ; seuls les
   objets directionnels/asymétriques (escaliers) trahissent le problème. **Toute
   validation future d'une convention d'axe ou de rotation doit se faire sur un objet
   explicitement asymétrique**, jamais sur les premiers objets qui sautent aux yeux
   dans le rendu.
4. **Ne jamais dériver une formule de rotation/quaternion "à la main" par
   raisonnement sur les signes.** Systématiquement source d'erreur silencieuse,
   difficile à repérer à l'œil. Toujours passer soit par l'API native du moteur
   source (`unreal.Quat`, `FRotator::Quaternion()`), soit par un **test empirique
   concret et défini à l'avance** : placer un prop directionnel, le faire tourner de
   90° sur chaque axe séparément dans le moteur source, vérifier qu'il atterrit dans
   la bonne orientation côté destination — jamais une déduction "sémantique"
   (avant/droite/haut) qui a produit **deux fois** une convention finalement fausse
   dans ce projet.
5. **Une conviction "recoupée par deux méthodes indépendantes" n'est toujours qu'une
   hypothèse tant qu'elle n'a pas été testée sur la vraie scène.** La formule d'axe
   `godot.x=ue.y, godot.y=ue.z, godot.z=-ue.x` (déterminant +1) a été présentée à un
   moment comme *"validée par deux méthodes indépendantes (raisonnement sur les axes
   + confirmation croisée par l'outil externe trouvé)"* — et s'est pourtant révélée
   fausse à l'usage : la bonne convention a déterminant **-1**, parce que les GLB
   produits par l'exporteur glTF natif d'Unreal ont déjà leur main inversée par cet
   exporteur, ce qu'aucun raisonnement géométrique abstrait ne pouvait deviner sans
   regarder ce que l'exporteur fait réellement. **La leçon la plus chère de tout ce
   projet : aucun degré de recoupement théorique ne remplace un test empirique sur la
   sortie réelle du pipeline.**
6. **Toujours séparer "structure vérifiée" de "comportement runtime vérifié".**
   Claude ne peut exécuter ni Unreal ni Godot. Chaque livraison de script GDScript a
   été explicitement qualifiée : contrôles passés = absence de séquences `\t`/`\n`
   littérales, tous les appels résolus, toutes les fonctions avec type de retour
   déclaré, aucune duplication — mais jamais "testé et ça marche". Formule à
   retenir : *"je n'exécute pas GDScript ici, donc la structure est vérifiée mais pas
   le comportement au runtime."* Ne jamais laisser un livrable futur sous-entendre
   plus de certitude que ce qui a réellement été contrôlable depuis le sandbox.

### B. Pièges techniques précis, génériques (à encoder en garde-fous du framework)

7. **Jamais de `\t`/`\n` littéraux dans un fichier de code assemblé par
   concaténation de chaînes Python.** Un patch GDScript entier est devenu
   imparsable parce que sa section VFX contenait des séquences d'échappement
   textuelles au lieu de vrais caractères de tabulation/saut de ligne — invisible à
   la relecture, fatal à l'exécution (GDScript est sensible à l'indentation). Tout
   générateur de code du framework doit écrire de vrais caractères de contrôle, et
   un contrôle d'intégrité automatique doit vérifier leur absence après génération.
8. **`:=` en GDScript exige que la fonction appelée ait un type de retour
   déclaré.** Ce bug de type d'inférence est réapparu une deuxième fois après avoir
   déjà été corrigé une première fois, simplement parce qu'il était présent dans le
   fichier de base utilisé comme point de départ d'un rebuild ultérieur. **Leçon
   générale** : un patch chirurgical qui ne touche qu'une section doit quand même
   auditer l'ensemble des fonctions custom utilisées avec `:=` dans tout le fichier,
   pas seulement celles qu'on vient d'écrire — un vieux bug non corrigé dans une
   section "stable" peut réapparaître silencieusement dès qu'on repart de cette base.
9. **La ressource `Curve` de Godot clippe silencieusement à `max_value` (1.0 par
   défaut).** Une courbe censée monter à 2.75 aurait été écrasée sans erreur. Toujours
   calculer/fixer les bornes de `Curve` depuis l'amplitude réelle des données
   utilisées, ne jamais laisser la valeur par défaut si le domaine dépasse [0,1].
10. **`.owner` doit être assigné APRÈS `add_child()`, jamais avant.** Un nœud dont
    `.owner` est fixé avant d'être ajouté à l'arbre n'est pas sauvegardé dans la
    `.tscn` — perte silencieuse, aucune erreur, aucun warning.
11. **Ne jamais générer une ressource par instance quand un pattern par catégorie
    suffit.** Sur 422 systèmes VFX, générer une courbe/un gradient/un matériau/un
    shader par marqueur aurait produit environ 2500 ressources et autant de
    compilations de shader. Construire une fois par catégorie (candle, fire, smoke,
    swarm...) et partager systématiquement entre toutes les instances de cette
    catégorie.
12. **Ne pas faire dépendre le rendu par défaut d'un shader personnalisé qui doit
    compiler ET recevoir exactement les bons varyings.** Godot ne dégrade pas
    gracieusement un shader cassé — il retombe sur le matériau blanc opaque par
    défaut du mesh. Un chemin par défaut doit reposer sur des mécanismes qui ne
    peuvent pas échouer à compiler (texture procédurale type `GradientTexture2D`,
    `vertex_color_use_as_albedo`, `BILLBOARD_PARTICLES`) ; un shader plus riche reste
    possible mais **désactivé par défaut** (flag explicite), à activer et valider un
    seul émetteur à la fois.
13. **Deux mécanismes qui contrôlent la même chose entrent en conflit silencieux.**
    `transform_align` du nœud ET le mode billboard du matériau contrôlaient tous
    deux l'orientation en même temps → quads vus par la tranche. Un seul propriétaire
    par axe de contrôle, jamais deux.
14. **Désactiver l'écriture de profondeur pour tout élément censé s'accumuler**
    visuellement (panaches de particules superposés) — sinon les éléments se
    découpent mutuellement au lieu de s'additionner.
15. **Toujours plafonner les ressources runtime dérivées d'un comptage de scène
    source.** 341 flammes de bougies ne doivent jamais produire 341 lumières
    temps réel dans la scène finale — imposer un budget explicite (ex. `VFX_MAX_LIGHTS`)
    avec un espacement minimal entre lumières retenues.
16. **Ordonner les tests de sous-chaîne du plus spécifique au plus général quand
    les noms peuvent se chevaucher.** `NS_candle_flame` contient à la fois `"candle"`
    et `"flame"` — tester `"candle"` avant `"flame"`, sinon la catégorie générale
    absorbe silencieusement les cas spécifiques prévus pour une catégorie dédiée.
17. **Calibrer tout seuil de diagnostic sur la sémantique exacte du compteur
    sous-jacent, jamais sur une intuition.** Un seuil "nombre de samplers > 13"
    s'est révélé faux parce que le mode d'échantillonnage partagé
    (`SSM_WRAP_WORLD_GROUP_SHARED`) change ce que le compteur mesure réellement
    (des opérations d'échantillonnage, pas des objets sampler uniques) — vérifier
    la sémantique précise d'un compteur d'API avant de fixer un seuil d'alerte dessus.
18. **`recompile_material()` d'Unreal ne renvoie AUCUNE erreur en cas d'échec de
    compilation shader** — un matériau qui échoue à compiler produit un rendu noir
    silencieux côté éditeur, jamais une exception Python côté script. Tout code qui
    manipule un graphe de matériau doit prévoir son propre diagnostic de repli (ex. un
    flag `debug_flat_normal` pour isoler la chaîne fautive par élimination), puisque
    l'API ne signalera jamais l'échec elle-même.
19. **Vérifier l'existence d'une propriété avant de l'écrire, dès que son nom peut
    varier entre versions de moteur.** Le pattern `_try_set()` (déjà appliqué aux
    propriétés de particules Godot comme `velocity_pivot`/`turbulence_*`) doit être
    la norme pour toute écriture de propriété nommée en dur, côté Unreal comme côté
    Godot — jamais une écriture directe non protégée sur un nom d'API susceptible
    d'avoir changé.
20. **Un import de texture externe (glTF/Quixel) peut hasher les noms de fichiers**,
    rendant tout matching par mot-clé sur ces noms illusoire — le vécu réel :
    `'rocky_sand' (gravel) not found` alors que la texture existait bel et bien,
    juste sous un nom haché dans un dossier différent. Toujours préférer la
    résolution par référence d'asset **exacte** (chemin complet stocké en config) à
    une heuristique de nom, et ne garder le scan par mot-clé qu'en dernier recours
    explicitement documenté comme tel.
21. **Ne jamais dimensionner un tableau/pipeline sur un nombre de slots supposé
    fixe sans vérifier le nombre réel d'éléments disponibles.** Un blend écrit pour
    4 slots a crashé en `IndexError: list index out of range` dès qu'un projet n'en
    fournissait qu'un seul — dimensionner dynamiquement sur `len(donnée_réelle)`,
    jamais sur une constante supposée.

### C. Décisions de collaboration/produit à respecter dans le futur

22. **Un outil externe complet a été activement recherché puis explicitement
    écarté.** UnrealToGodot (vortechU) a été identifié, son code lu, ses solutions
    aux deux points bloquants (décals, axes) comparées en détail — puis la personne a
    choisi de garder son pipeline maison plutôt que d'y basculer. **Ne pas
    re-proposer un remplacement complet par un outil tiers** sans qu'il soit
    redemandé ; il reste légitime d'aller y chercher une référence ponctuelle (une
    formule, une idée d'implémentation), jamais comme automatisme "remplaçons tout
    par cet outil".
23. **Toute extension du reconstructeur `.gd` doit être additive et isolée, jamais
    une réécriture qui repasse sur du code déjà validé empiriquement.** Le module
    décals/VFX a été livré comme un bloc séparé à coller (`godot_decals_vfx_addition.gd`)
    précisément pour ne jamais risquer d'écraser le correctif de quaternion et la
    conversion d'axes déjà validés à ce moment-là — un principe de conception à
    maintenir dans le framework : les nouvelles capacités s'ajoutent à côté du cœur
    de conversion déjà éprouvé, elles ne le retouchent pas en passant.
24. **Ne jamais produire un résultat qui a l'air converti quand il ne l'est pas.**
    Le choix "Niagara non convertible, placement seul" a été assumé et communiqué
    explicitement (jusque dans le nom du nœud runtime,
    `VFX_MARKERS_NOT_CONVERTED`) plutôt que de simuler une fausse conversion qui
    aurait l'air correcte de loin. Ce principe de transparence doit s'appliquer à
    toute future catégorie non convertible que le framework rencontrera.
25. **L'audio reste un point mort non tranché, à ne pas laisser filer par défaut
    dans le framework.** Contrairement à Niagara (choix "non convertible" assumé et
    documenté) et aux décals (finalement traités), l'`effects`/`world_features.audio`
    du manifeste n'a **jamais reçu de traitement dédié à aucun stade** de
    l'historique du projet — ni export de fichiers son, ni même une passe de
    marqueurs à la `VFX_MARKERS_NOT_CONVERTED`. Ce n'est pas un choix assumé comme
    Niagara, c'est un oubli qui a simplement toujours été repoussé. Le framework
    doit trancher explicitement ce cas plutôt que de reproduire le même silence.

---

## Résumé exécutif (une page)

Le pipeline convertit fidèlement une map UE5.5 vers Godot 4 en 6 étapes
strictement ordonnées, pivotant intégralement autour d'un manifeste JSON
déclaratif qui sépare **description de la scène** (4 scripts Python UE) de
**reconstruction visuelle** (1 `EditorScript` GDScript de 2283 lignes,
maintenant lu en entier — §3.9). La quasi-totalité de la logique de scan,
classification, composition de transform, export de mesh, bake de Landscape,
export de décal/VFX **et** reconstruction Godot (géométrie, décals, marqueurs
VFX) est **déjà générique** — elle ne contient aucune dépendance forte au
projet Necropolis, à l'exception (a) des chemins codés en dur, (b) des
listes de noms de paramètres de matériau de décal, (c) des ~15
presets/constantes VFX et des mots-clés de catégorisation Niagara côté
reconstructeur, et (d) du générateur de matériau de terrain
(`auto_terrain_generator_ue55.py`), qui est un outil d'authoring totalement
hors du scope de conversion. La généralisation en framework consiste donc
principalement à **paramétrer** ce qui existe déjà des deux côtés
(remplacer les constantes de module par de la configuration, côté Python
comme côté GDScript), **combler 3 trous connus** (export SkeletalMesh,
multi-Landscape, contrat de "readiness" jamais consommé par le
reconstructeur alors qu'il existe côté manifeste), **factoriser 1
duplication** (raycast en grille), et **extraire le reconstructeur de son
mode d'exécution `EditorScript`** pour le rendre pilotable par un
orchestrateur sans clic manuel dans l'éditeur Godot — la contrainte
structurante la plus importante découverte à cette lecture.

Au-delà du code, l'historique des conversations (§13) apporte une deuxième
couche de contraintes tout aussi importante : des règles de méthode
(mesurer plutôt que deviner, ne jamais valider une convention d'axe sur un
objet symétrique, séparer structure vérifiée et comportement runtime
vérifié) et des pièges techniques précis (échappement littéral cassant le
parsing GDScript, clipping silencieux de `Curve`, `.owner` assigné trop
tôt, dépendance à un shader qui ne dégrade pas gracieusement, tableaux
dimensionnés sur un nombre de slots supposé fixe) qui ne sont visibles nulle
part dans le code final, puisque celui-ci ne montre que ce qui a fini par
marcher.

---

## Annexe — Comment utiliser ce document

Ce fichier est conçu pour être **collé intégralement en contexte** (upload
projet, ou début de conversation) d'une future session consacrée à la
généralisation. Il n'y a rien à relire en amont : tout ce qui a été lu (9
fichiers + l'historique de conversation) est déjà digéré dedans.

Ordre de lecture recommandé pour qui reprend le travail : §4-5 (les 3
schémas JSON, le contrat de données réel) → §6 (patterns transversaux) →
§8-9 (ce qui est déjà générique vs à généraliser) → §10-12 (pièges,
besoins de l'orchestrateur, incohérences) → §13 (ce qu'il ne faut plus
refaire). Les §2-3 (fichier par fichier) servent de référence à consulter
au besoin, pas à lire linéairement.

Prochaine étape logique une fois la généralisation entamée : tenir ce même
document à jour au fil des décisions de conception du framework (nouvelle
section "§14 Décisions de conception du framework", par exemple), plutôt
que de repartir d'une lecture exhaustive à chaque session future.
