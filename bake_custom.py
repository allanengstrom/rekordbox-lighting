"""Bake a track's phrase-scene assignments into full-track custom lighting_data
rows (one per venue fixture) — rekordbox's own mechanism for hand-built shows.
This is what lets lighting change mid-phrase: split points re-tile a second
scene from an arbitrary beat inside a phrase.

Usage: bake_custom.py <content_id> [t_sec:offset_beats:macro_id ...]
  each split arg: the phrase containing t_sec gets scene <macro_id> starting
  offset_beats after the phrase start.

Refuses to run while rekordbox is open. Undo appended to rollback_engine.sql.
"""
import json, os, sqlite3, subprocess, sys
import xml.etree.ElementTree as ET

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
ROLLBACK = os.path.expanduser("~/rekordbox-lighting/rollback_engine.sql")
SECTIONS = ["Brightness", "Colour", "Strobe", "Position", "Rotate", "Gobo"]


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def bake_fixture(segments, slot_xml, scene_beats, track_end, step_only=False):
    """segments: [(beat0, beat1, macro_id)]; slot_xml: macro_id -> xml or None."""
    bright = []                       # (x, y)
    blocks = {s: [] for s in SECTIONS if s != "Brightness"}
    present = {"Brightness", "Colour", "Strobe"}
    for b0, b1, mid in segments:
        xml = slot_xml.get(mid)
        if xml is None:               # scene has nothing for this slot -> dark span
            bright += [(b0, 0.0), (b1, 0.0)]
            continue
        root = ET.fromstring(xml)
        for sec in SECTIONS:
            if root.find(sec) is not None:
                present.add(sec)
        sb = scene_beats[mid]
        off = b0
        while off < b1 - 1e-6:
            clip = min(off + sb, b1)
            pts = sorted((float(p.get("x")), float(p.get("y")))
                         for p in root.iter("Point"))
            prev = None
            for x, y in pts:
                gx = off + x
                if gx <= clip + 1e-9:
                    bright.append((gx, y))
                    prev = (gx, y)
                else:                 # clipped mid-curve: close at the boundary
                    if prev and gx > prev[0]:
                        # lasers must HOLD (stepped lookup channel); interpolating
                        # the value creates phantom in-between levels -> funky sweep
                        val = prev[1] if step_only else prev[1] + \
                            (clip - prev[0]) / (gx - prev[0]) * (y - prev[1])
                        bright.append((clip, val))
                    break
            for sec in blocks:
                el = root.find(sec)
                if el is None:
                    continue
                for blk in el:
                    xl = float(blk.get("xleft", 0)) + off
                    xr = float(blk.get("xright", 0)) + off
                    if xl >= clip - 1e-9:
                        continue
                    a = dict(blk.attrib)
                    a["xleft"], a["xright"] = repr(xl), repr(min(xr, clip))
                    blocks[sec].append((blk.tag, a))
            off += sb
        # hard cut at the segment boundary: without a closing point, rekordbox
        # draws a straight fade from the last point to the next scene's first
        if bright and bright[-1][0] < b1 - 0.05:
            bright.append((b1 - 0.02, bright[-1][1]))

    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<LightingEditModel ver="1.0">']
    out.append(" <Brightness>")
    out.append(f'  <PointBlock xleft="0.0" xright="{track_end!r}">')
    for i, (x, y) in enumerate(bright):
        t = 1 if i == 0 else (3 if i == len(bright) - 1 else 2)
        out.append(f'   <Point x="{x!r}" y="{y!r}" type="{t}"/>')
    out.append("  </PointBlock>")
    out.append(" </Brightness>")
    for sec in SECTIONS[1:]:
        if sec not in present:
            continue
        if not blocks[sec]:
            out.append(f" <{sec}/>")
            continue
        out.append(f" <{sec}>")
        for tag, a in blocks[sec]:
            attrs = " ".join(f'{k}="{esc(v)}"' for k, v in a.items())
            out.append(f"  <{tag} {attrs}/>")
        out.append(f" </{sec}>")
    out.append("</LightingEditModel>")
    return "\n".join(out)


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it before writing lighting data.")
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.anlz import AnlzFile

    cid = int(sys.argv[1])
    splits = []
    for a in sys.argv[2:]:
        t, offb, mid = a.split(":")
        splits.append((float(t), int(offb), int(mid)))

    uc = sqlite3.connect(LDB + "user.db3")
    mc = sqlite3.connect(LDB + "macro.db3")
    scene_beats = dict(mc.execute("SELECT id, beats FROM macro"))
    macro_names = dict(mc.execute("SELECT id, name FROM macro"))

    sid, = uc.execute("SELECT song_id FROM content WHERE id=?", (cid,)).fetchone()
    db = Rekordbox6Database()
    meta = None
    for c in db.get_content():
        try:
            if int(c.ID) == sid:
                meta = (c.Title or "?", (c.BPM or 0) / 100.0, c.AnalysisDataPath or "")
        except Exception:
            pass
    title, bpm, anlz = meta
    root_share = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
    base = os.path.dirname(root_share + anlz)
    pssi = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT")).get_tag("PSSI").content
    beats = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT")).get_tag("PQTZ").content.entries
    t0 = beats[0].time / 1000.0
    spb = 60.0 / bpm
    track_end = round((beats[-1].time / 1000.0 - t0) / spb, 4)

    rows = dict(uc.execute(
        "SELECT phrase_num, macro_id FROM phrase_data WHERE content_id=?", (cid,)))
    entries = list(pssi.entries)
    segments = []                     # (beat0, beat1, macro_id)
    for j, e in enumerate(entries):
        b0 = e.beat - 1
        b1 = entries[j + 1].beat - 1 if j + 1 < len(entries) else track_end
        mid = rows.get(e.index)
        if mid is None or b1 <= b0:
            continue
        t_start, t_stop = t0 + b0 * spb, t0 + b1 * spb
        segs = [(b0, b1, mid)]
        for ts, offb, smid in splits:
            if t_start <= ts < t_stop and b0 + offb < b1:
                segs = [(b0, b0 + offb, mid), (b0 + offb, b1, smid)]
                print(f"split: phrase at {int(t_start//60)}:{int(t_start%60):02d} -> "
                      f"{macro_names[mid]} for {offb} beats, then {macro_names[smid]}")
        segments += segs

    venue = list(uc.execute("SELECT id, name, macro_fixture_id FROM fixture ORDER BY id"))
    used_macros = sorted({m for _, _, m in segments})
    slot_xml_all = {}                 # (macro, slot) -> xml
    for mid in used_macros:
        for slot, data in mc.execute(
                "SELECT macro_fixture_id, data FROM macro_data WHERE macro_id=?", (mid,)):
            slot_xml_all[(mid, slot)] = data

    prior = uc.execute("SELECT COUNT(*) FROM lighting_data WHERE content_id=?",
                       (cid,)).fetchone()[0]
    if prior:
        sys.exit(f"content {cid} already has {prior} custom rows — refusing to stack; "
                 "delete them first if a rebake is intended.")

    print(f"\n{title}: {len(segments)} segments, track_end {track_end} beats")
    new_ids = []
    for fid, name, slot in venue:
        slot_xml = {mid: slot_xml_all.get((mid, slot)) for mid in used_macros}
        missing = [macro_names[m] for m, x in slot_xml.items() if x is None]
        xml = bake_fixture(segments, slot_xml, scene_beats, track_end,
                           step_only=(slot == 19))   # slot 19 = laser (stepped lookups)
        ET.fromstring(xml)            # must parse back cleanly before it touches the DB
        cur = uc.execute("INSERT INTO lighting_data (content_id, fixture_id, data) "
                         "VALUES (?,?,?)", (cid, fid, xml))
        new_ids.append(cur.lastrowid)
        note = f"  (dark during: {', '.join(missing)})" if missing else ""
        print(f"  fixture {fid:3} {name[:40]:42} {len(xml)//1024:3}KB{note}")
    uc.commit()
    with open(ROLLBACK, "a") as f:
        f.write(f"-- bake_custom {cid}\nDELETE FROM lighting_data WHERE id IN "
                f"({','.join(map(str, new_ids))});\n")
    mf_path = os.path.expanduser("~/rekordbox-lighting/engine_bakes.json")
    mf = json.load(open(mf_path)) if os.path.exists(mf_path) else {}
    mf[str(cid)] = new_ids
    json.dump(mf, open(mf_path, "w"))
    print(f"\nwritten {len(new_ids)} custom rows. undo appended to {ROLLBACK}")


if __name__ == "__main__":
    main()
