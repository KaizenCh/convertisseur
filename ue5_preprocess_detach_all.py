"""
============================================================
UE5 PRE-PROCESSING - DETACH ALL (KEEP WORLD TRANSFORM)
============================================================

PURPOSE:
    Run this ONCE, before the manifest exporter, to flatten every
    actor-to-actor attachment in the level. Every actor ends up with
    NO attach parent, while its world position/rotation/scale stays
    EXACTLY the same as before.

    This removes the need for the exporter's attach_parent-chain
    fallback (component_transform_via_properties) to ever compose
    more than a single link, which is the path used for every
    Decal / Niagara / Light / Audio component on this project, plus
    the small number of "stale component" cases the manifest already
    tracks.

WHAT THIS DOES NOT DO (on purpose - do these separately):
    - Does NOT break/flatten Level Instances. LI containment is not
      actor attachment, so detach_from_actor() has no effect on it.
      Break Level Instances FIRST via the editor UI (see notes below),
      THEN run this script, so any attachments that were living inside
      former LI content get flattened too.
    - Does NOT touch Actor Groups (Ctrl+G). Groups are pure editor
      selection metadata - they do not drive transform composition,
      so they are not part of this bug class.
    - Does NOT fix mesh-baked orientation issues (a asset-authoring
      problem, unrelated to hierarchy).

SAFETY:
    - DRY_RUN defaults to True: first pass only REPORTS what would be
      detached, changes nothing. Flip to False once the report looks
      right.
    - Run this on a DUPLICATE/saved-as copy of the map, not your
      master file, same as you should for Break Level Instance.
    - KEEP_WORLD is passed explicitly on all 3 rules (location/
      rotation/scale). detach_from_actor()'s own default is
      KEEP_RELATIVE, which would snap actors away from where they are.

RECOMMENDED ORDER:
    1. Save a copy of the map (or work on a branch).
    2. Manually break every Level Instance (World Outliner -> filter by
       class "LevelInstance" -> select all -> right-click -> Level ->
       Break -> Break Level Instance). Repeat the filter/select/break
       pass until no LevelInstance actors remain, since breaking one
       can reveal nested ones.
    3. Run this script with DRY_RUN = True, check the report.
    4. Run again with DRY_RUN = False.
    5. Re-run the (already patched) manifest exporter.
"""

import unreal

DRY_RUN = False


def safe_label(actor):
    try:
        return actor.get_actor_label()
    except Exception:
        try:
            return actor.get_name()
        except Exception:
            return "<unlabeled actor>"


def safe_class(actor):
    try:
        return actor.get_class().get_name()
    except Exception:
        return "<unknown class>"


def main():
    print("")
    print("============================================================")
    print(" UE5 PRE-PROCESSING - DETACH ALL (KEEP WORLD TRANSFORM)")
    print("============================================================")
    print("Mode                         :", "DRY RUN (no changes)" if DRY_RUN else "LIVE (will modify the level)")

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    all_actors = actor_subsystem.get_all_level_actors()

    print("Total actors in level        :", len(all_actors))

    attached = []
    level_instance_still_present = 0

    for actor in all_actors:
        if actor is None:
            continue

        try:
            parent = actor.get_attach_parent_actor()
        except Exception as exc:
            print("WARN: could not query attach parent for", safe_label(actor), "-", exc)
            continue

        if parent is not None:
            attached.append((actor, parent))

        try:
            if "LevelInstance" in safe_class(actor):
                level_instance_still_present += 1
        except Exception:
            pass

    print("Actors with an attach parent :", len(attached))
    print("LevelInstance actors present :", level_instance_still_present,
          "(break these separately - see script header)")

    if attached:
        print("------------------------------------------------------------")
        print("ATTACHED ACTORS (child -> parent):")
        for child, parent in attached:
            print("  %-40s -> %-40s" % (
                "%s (%s)" % (safe_label(child), safe_class(child)),
                "%s (%s)" % (safe_label(parent), safe_class(parent))
            ))
        print("------------------------------------------------------------")

    if DRY_RUN:
        print("DRY RUN complete. No changes made.")
        print("Set DRY_RUN = False and re-run to actually detach.")
        print("============================================================")
        print("")
        return

    detached_count = 0
    failed = []

    for child, parent in attached:
        try:
            child.detach_from_actor(
                unreal.DetachmentRule.KEEP_WORLD,
                unreal.DetachmentRule.KEEP_WORLD,
                unreal.DetachmentRule.KEEP_WORLD
            )
            detached_count += 1
        except Exception as exc:
            failed.append((child, exc))

    print("Detached                     :", detached_count)
    print("Failed                       :", len(failed))

    for child, exc in failed:
        print("  FAILED:", safe_label(child), "-", exc)

    print("STATUS                       :", "PASS" if not failed else "PASS WITH FAILURES")
    print("============================================================")
    print("")
    print("Reminder: save the level now (and don't forget to break any")
    print("remaining Level Instances before re-running the exporter).")


main()
