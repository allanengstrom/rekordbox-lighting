"""Export lighting timelines for the virtual rig artifact.

Bundles per-track: bpm/beat offset, phrase windows with assigned scenes,
scene curves per slot (parsed from LightingEditModel XML), custom per-fixture
overrides, and the venue fixture list. Output: virtual_rig_data.json
"""
import json, os, sqlite3, sys
import xml.etree.ElementTree as ET

LDB = os.path.expanduser(
    "~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
OUT = os.path.expanduser("~/rekordbox-lighting/virtual_rig_data.json")
TRACKS = {978: "custom", 2: "auto"}  # content_id -> flavor
for _a in sys.argv[1:]:
    TRACKS[int(_a)] = "auto"


def argb(v):
    u = int(v) & 0xFFFFFFFF
    return "#{:02x}{:02x}{:02x}".format((u >> 16) & 255, (u >> 8) & 255, u & 255)


def parse_model(xml_text):
    root = ET.fromstring(xml_text)
    out = {"bright": [], "colour": [], "strobe": []}
    for p in root.iter("Point"):
        out["bright"].append([round(float(p.get("x")), 3),
                              round(float(p.get("y")), 3)])
    for c in root.iter("ColourBlock"):
        out["colour"].append([round(float(c.get("xleft")), 3), argb(c.get("colourleft")),
                              round(float(c.get("xright")), 3), argb(c.get("colourright"))])
    for s in root.iter("StrobeBlock"):
        out["strobe"].append([round(float(s.get("xleft")), 3), float(s.get("strobeleft")),
                              round(float(s.get("xright")), 3), float(s.get("stroberight"))])
    for mb in root.iter("MovementBlock"):
        try:
            out.setdefault("move", []).append({
                "x0": float(mb.get("xleft", 0)), "x1": float(mb.get("xright", 0)),
                "pat": mb.get("pattern", ""), "w": float(mb.get("width", 0)),
                "h": float(mb.get("height", 0)), "ox": float(mb.get("offset_x", 127)),
                "oy": float(mb.get("offset_y", 127)),
                "per": max(200.0, float(mb.get("period_time", 4000))),
                "fx": float(mb.get("frequency_x", 1)), "fy": float(mb.get("frequency_y", 1)),
                "px": float(mb.get("phase_x", 0)), "py": float(mb.get("phase_y", 0)),
                "dir": -1.0 if mb.get("direction") == "Backward" else 1.0})
        except (TypeError, ValueError):
            pass
    return out


def main():
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.anlz import AnlzFile

    mc = sqlite3.connect(LDB + "macro.db3")
    uc = sqlite3.connect(LDB + "user.db3")
    macro_meta = {mid: (nm, beats) for mid, nm, beats in
                  mc.execute("SELECT id, name, beats FROM macro")}

    venue = [dict(zip(("id", "name", "slot"), r)) for r in uc.execute(
        "SELECT id, name, macro_fixture_id FROM fixture ORDER BY id")]
    slots_used = sorted({f["slot"] for f in venue})

    db = Rekordbox6Database()
    meta = {}
    for c in db.get_content():
        try:
            meta[int(c.ID)] = (c.Title or "?", (c.BPM or 0) / 100.0,
                               c.AnalysisDataPath or "", c.FolderPath or "")
        except Exception:
            pass

    root_share = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
    KINDS = {1: "INTRO", 2: "UP", 3: "DOWN", 5: "CHORUS", 6: "OUTRO"}
    bundle = {"venue": venue, "tracks": [], "scenes": {}}
    need_scenes = set()

    PATNAME = {1: "COOL", 2: "NATURAL", 3: "HOT", 4: "SUBTLE", 5: "WARM", 6: "VIVID",
               19: "CLUB1", 20: "CLUB2"}
    for cid, flavor in TRACKS.items():
        sid, pat = uc.execute("SELECT song_id, macro_pattern_id FROM content WHERE id=?",
                              (cid,)).fetchone()
        title, bpm, anlz, audio_path = meta[sid]
        base = os.path.dirname(root_share + anlz)
        ext = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT"))
        dat = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT"))
        pssi = ext.get_tag("PSSI").content
        beats = dat.get_tag("PQTZ").content.entries
        t0 = beats[0].time / 1000.0
        end = beats[-1].time / 1000.0
        spb = 60.0 / bpm
        rows = dict(uc.execute(
            "SELECT phrase_num, macro_id FROM phrase_data WHERE content_id=?",
            (cid,)).fetchall())
        entries = list(pssi.entries)
        phrases = []
        for j, e in enumerate(entries):
            start = t0 + (e.beat - 1) * spb
            stop = t0 + (entries[j + 1].beat - 1) * spb if j + 1 < len(entries) else end
            mid = rows.get(e.index)
            nm, blen = macro_meta.get(mid, ("?", 32))
            kind_num = int(str(e.kind).split()[0]) if str(e.kind).split()[0].isdigit() else 0
            phrases.append({"start": round(start, 2), "end": round(stop, 2),
                            "kind": KINDS.get(kind_num, "?"), "scene": mid,
                            "scene_name": nm, "scene_beats": blen,
                            "start_beat": e.beat - 1})
            if mid:
                need_scenes.add(mid)

        custom = {}
        for fid, data in uc.execute(
                "SELECT fixture_id, data FROM lighting_data WHERE content_id=?", (cid,)):
            try:
                custom[fid] = parse_model(data)
            except Exception:
                pass
        bundle["tracks"].append({"content_id": cid, "flavor": flavor, "title": title,
                                 "bpm": bpm, "first_beat": round(t0, 3),
                                 "duration": round(end, 2), "phrases": phrases,
                                 "custom": custom, "audio_path": audio_path,
                                 "vibe": PATNAME.get(pat, "?")})

    for mid in need_scenes:
        slots = {}
        for slot, data in mc.execute(
                "SELECT macro_fixture_id, data FROM macro_data WHERE macro_id=?", (mid,)):
            if slot in slots_used:
                try:
                    slots[slot] = parse_model(data)
                except Exception:
                    pass
        nm, blen = macro_meta[mid]
        bundle["scenes"][mid] = {"name": nm, "beats": blen, "slots": slots}

    json.dump(bundle, open(OUT, "w"))
    print(f"exported {len(bundle['tracks'])} tracks, {len(bundle['scenes'])} scenes, "
          f"{os.path.getsize(OUT)//1024} KB")


if __name__ == "__main__":
    main()
