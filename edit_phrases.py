"""Rewrite a track's phrase structure (PSSI tag in ANLZ0000.EXT) — kinds and
boundaries — so rekordbox displays the corrected grid instead of its own guess.

Usage: edit_phrases.py <content_id> <spec.json>
  spec.json: {"set":   [{"beat": 33, "kind": 2, ...fields...}, ...],
              "insert":[{"beat": 273, "kind": 2, ...fields...}, ...]}
  "set" matches an existing entry by beat and overwrites the given fields;
  "insert" adds a new entry at beat (other fields zero unless given).
  kinds (mood=high): 1 intro, 2 up, 3 down, 5 chorus, 6 outro.

Hard gates: rekordbox must be closed; the EXT file must round-trip byte-exact
BEFORE editing; the whole ANLZ dir is backed up first; the result is re-parsed
and verified after writing. Restore = copy the backup dir back.
"""
import json, os, shutil, sqlite3, subprocess, sys, time

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
BACKUPS = os.path.expanduser("~/rekordbox-lighting/backups")
ENTRY_FIELDS = ["u1", "k1", "u2", "k2", "u3", "b", "beat_2", "beat_3", "beat_4",
                "u4", "k3", "u5", "fill", "beat_fill"]
KIND_NAMES = {1: "INTRO", 2: "UP", 3: "DOWN", 5: "CHORUS", 6: "OUTRO"}


class RoundTripError(Exception):
    pass


def apply_pssi(ext_path, set_ops, insert_ops, label="", delete_beats=(), mood=None):
    """Apply phrase edits to an EXT file. Backs up the containing dir first;
    raises RoundTripError if the file doesn't rebuild byte-exact beforehand.
    Returns (backup_dir, verified_entries)."""
    from pyrekordbox.anlz import AnlzFile

    raw = open(ext_path, "rb").read()
    f = AnlzFile.parse(raw)
    if f.build() != raw:
        raise RoundTripError(ext_path)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUPS, f"anlz-{label or 'edit'}-{stamp}")
    shutil.copytree(os.path.dirname(ext_path), bdir)

    tag = f.get_tag("PSSI")
    p = tag.content
    if mood is not None:
        p.mood = mood                      # the scene bank only has HIGH scenes -> force high
    entries = list(p.entries)

    for b in delete_beats:
        e = next((e for e in entries if e.beat == b), None)
        if e is None:
            raise ValueError(f"no phrase starts at beat {b} to delete")
        entries.remove(e)
    for s in set_ops:
        e = next((e for e in entries if e.beat == s["beat"]), None)
        if e is None:
            raise ValueError(f"no phrase starts at beat {s['beat']}")
        for k, v in s.items():
            if k != "beat":
                setattr(e, k, v)
    for s in insert_ops:
        if any(e.beat == s["beat"] for e in entries):
            raise ValueError(f"phrase already starts at beat {s['beat']}")
        new = entries[0].copy()
        new.beat = s["beat"]
        for k in ENTRY_FIELDS + ["kind"]:
            setattr(new, k, s.get(k, 0) if k != "kind" else s["kind"])
        entries.append(new)

    entries.sort(key=lambda e: e.beat)
    for i, e in enumerate(entries):
        e.index = i + 1
    p.entries = entries
    p.len_entries = len(entries)
    # PSSI has no update_len in pyrekordbox: fixed 32B header + 24B per entry
    tag.struct.len_tag = tag.struct.len_header + 24 * len(entries)
    f.update_len()

    out = f.build()
    check = AnlzFile.parse(out)
    q = check.get_tag("PSSI").content
    assert q.len_entries == len(entries), "rebuilt entry count mismatch"

    with open(ext_path, "wb") as fh:
        fh.write(out)
    return bdir, list(q.entries)


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it before editing analysis files.")
    from pyrekordbox import Rekordbox6Database

    cid = int(sys.argv[1])
    spec = json.load(open(sys.argv[2]))

    uc = sqlite3.connect(LDB + "user.db3")
    sid, = uc.execute("SELECT song_id FROM content WHERE id=?", (cid,)).fetchone()
    db = Rekordbox6Database()
    title = anlz = None
    for c in db.get_content():
        try:
            if int(c.ID) == sid:
                title, anlz = c.Title, c.AnalysisDataPath
        except Exception:
            pass
    base = os.path.dirname(os.path.expanduser("~/Library/Pioneer/rekordbox/share") + anlz)
    try:
        bdir, entries = apply_pssi(os.path.join(base, "ANLZ0000.EXT"),
                                   spec.get("set", []), spec.get("insert", []), str(cid),
                                   spec.get("delete", []))
    except RoundTripError:
        sys.exit("REFUSING: EXT file does not round-trip byte-exact; editing would corrupt.")
    print(f"backup: {bdir}")
    print(f"\n{title}: PSSI now {len(entries)} phrases")
    for e in entries:
        extra = f"  fill@{e.beat_fill}" if e.fill else ""
        print(f"  {e.index:>3}  beat {e.beat:>4}  {KIND_NAMES.get(e.kind, e.kind)}{extra}")
    print(f"\nrestore: cp {bdir}/* '{base}/'")


if __name__ == "__main__":
    main()
