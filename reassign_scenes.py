"""Grid-locked scene re-assignment. Run AFTER phrase grids are hand-edited in
rekordbox. It reads HIS phrase structure (breaks + labels) as-is, assigns scenes
to match, and re-bakes so playback follows his grid — WITHOUT changing the grid,
and KEEPING each track's existing color (he likes the colors, just not the grid).

Usage: reassign_scenes.py --folder Downloads    (all analyzed Downloads tracks)
       reassign_scenes.py <content_id> [...]

Does NOT: relabel phrases, move boundaries, snap drops, or change mood/color.
Refuses to run while rekordbox is open. Undo appended to rollback_engine.sql.
"""
import json, os, random, subprocess, sys
from pyrekordbox import Rekordbox6Database
from pyrekordbox.anlz import AnlzFile
import sqlite3

sys.path.insert(0, os.path.expanduser("~/rekordbox-lighting"))
from assign_lighting import pick_scenes, VIBE_PATTERN

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
ROLLBACK = os.path.expanduser("~/rekordbox-lighting/rollback_engine.sql")
KIND_ROLE = {1: "INTRO", 2: "UP", 3: "DOWN", 5: "CHORUS", 6: "OUTRO"}
PAT_VIBE = {v: k for k, v in VIBE_PATTERN.items()}   # pattern id -> color name


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it first (it would overwrite these writes).")
    mc = sqlite3.connect(LDB + "macro.db3")
    name2id = {}
    for mid, nm in mc.execute("SELECT id, name FROM macro"):
        name2id.setdefault(nm, mid)
    uc = sqlite3.connect(LDB + "user.db3")
    db = Rekordbox6Database()
    meta = {}
    for c in db.get_content():
        try:
            meta[int(c.ID)] = (c.Title or "?", c.FolderPath or "", c.AnalysisDataPath or "")
        except Exception:
            pass
    root = os.path.expanduser("~/Library/Pioneer/rekordbox/share")

    argv = sys.argv[1:]
    if argv and argv[0] == "--folder":
        needle = f"/{argv[1]}/"
        sid_by = {sid: cid for cid, sid in uc.execute("SELECT id, song_id FROM content")}
        cids = []
        for c in db.get_content():
            try:
                if needle in (c.FolderPath or "") and c.AnalysisDataPath and int(c.ID) in sid_by:
                    cids.append(sid_by[int(c.ID)])
            except Exception:
                pass
    else:
        cids = [int(a) for a in argv if not a.startswith("--")]

    mf_path = os.path.expanduser("~/rekordbox-lighting/engine_bakes.json")
    mf = json.load(open(mf_path)) if os.path.exists(mf_path) else {}
    undo = ["-- reassign_scenes (grid-locked)\n"]
    done = 0
    for cid in cids:
        row = uc.execute("SELECT song_id, macro_pattern_id FROM content WHERE id=?",
                         (cid,)).fetchone()
        if not row:
            continue
        sid, pat = row
        title, path, anlz = meta.get(sid, ("?", "", ""))
        base_color = PAT_VIBE.get(pat, "COOL")       # keep the track's existing color
        base = os.path.dirname(root + anlz)
        try:
            pssi = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT")).get_tag("PSSI").content
        except Exception:
            print(f"SKIP {title}: no analysis"); continue
        ents = list(pssi.entries)
        # roles straight from HIS edited grid — no re-derivation
        labels, pnums = [], []
        for e in ents:
            role = KIND_ROLE.get(int(e.kind) if str(e.kind).isdigit()
                                 else int(str(e.kind).split()[0]) if str(e.kind).split()[0].isdigit() else 0)
            if role:
                labels.append(role); pnums.append(e.index)
        if not labels:
            print(f"SKIP {title}: no mappable phrases"); continue
        weights = {v: 1.0 for v in VIBE_PATTERN if not v.startswith("CLUB")}
        rng = random.Random(sid)
        _, scenes = pick_scenes(labels, weights, rng, name2id, pin=base_color)

        old_rows = dict(uc.execute("SELECT phrase_num, macro_id FROM phrase_data "
                                   "WHERE content_id=?", (cid,)).fetchall())
        for pn, om in old_rows.items():
            undo.append(f"UPDATE phrase_data SET macro_id={om} WHERE content_id={cid} AND phrase_num={pn};")
        for pn, (mid, nm) in zip(pnums, scenes):
            if mid is None:
                continue
            if pn in old_rows:
                uc.execute("UPDATE phrase_data SET macro_id=? WHERE content_id=? AND phrase_num=?",
                           (mid, cid, pn))
            else:
                uc.execute("INSERT INTO phrase_data (content_id, phrase_num, macro_id, initial_macro_id) "
                           "VALUES (?,?,?,?)", (cid, pn, mid, mid))
        uc.commit()

        # re-bake to HIS grid (only if the existing rows are engine-owned, never a
        # hand-built custom show)
        owned = set(mf.get(str(cid), []))
        existing = [r[0] for r in uc.execute(
            "SELECT id FROM lighting_data WHERE content_id=?", (cid,))]
        if existing and set(existing) - owned:
            print(f"  {title[:40]}: scenes reassigned (hand-built bake left intact)")
        else:
            if existing:
                uc.execute("DELETE FROM lighting_data WHERE content_id=?", (cid,)); uc.commit()
            r = subprocess.run([sys.executable, os.path.expanduser("~/rekordbox-lighting/bake_custom.py"),
                                str(cid)], capture_output=True, text=True)
            tag = "re-baked to your grid" if r.returncode == 0 else f"bake FAILED {(r.stdout+r.stderr)[-120:]}"
            print(f"  {title[:40]} [{base_color}]: {len(labels)} phrases, {tag}")
        done += 1
    with open(ROLLBACK, "a") as f:
        f.write("\n".join(undo) + "\n")
    print(f"\nreassigned {done} tracks to their edited grids. undo -> {ROLLBACK}")


if __name__ == "__main__":
    main()
