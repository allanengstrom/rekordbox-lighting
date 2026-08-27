"""Validate allin1 against hand-corrected lighting phrase assignments.

Picks the most-edited tracks, runs allin1 per track (byproducts cleaned each time),
maps labels into a shared role vocabulary, and scores rekordbox-vs-human and
allin1-vs-human agreement per phrase window. Resume-safe via allin1_cache/.
"""
import json, os, re, shutil, sqlite3

N_TRACKS = 12
CACHE = os.path.expanduser("~/rekordbox-lighting/allin1_cache")
OUT = os.path.expanduser("~/rekordbox-lighting/validation_report.json")
LDB = os.path.expanduser(
    "~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")

# shared vocabulary: INTRO / UP / DOWN / CHORUS / OUTRO
ALLIN1_MAP = {"intro": "INTRO", "verse": "UP", "chorus": "CHORUS", "inst": "CHORUS",
              "solo": "CHORUS", "break": "DOWN", "bridge": "DOWN",
              "outro": "OUTRO", "start": "INTRO", "end": "OUTRO"}
RB_KINDS = {1: "INTRO", 2: "UP", 3: "DOWN", 5: "CHORUS", 6: "OUTRO"}


def scene_role(name):
    m = re.match(r"(?:HIGH|MID|LOW)\s+([A-Z]+)\d*\s+", name or "")
    if m:
        r = m.group(1)
        return {"INTRO": "INTRO", "UP": "UP", "DOWN": "DOWN", "CHORUS": "CHORUS",
                "OUTRO": "OUTRO"}.get(r)
    if name and name.startswith("CHORUS CLUB"):
        return "CHORUS"
    if name and name.startswith("DOWN CLUB"):
        return "DOWN"
    if name and name.startswith("INTERLUDE"):
        return "DOWN"
    return None


def main():
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.anlz import AnlzFile
    import allin1

    os.makedirs(CACHE, exist_ok=True)
    mc = sqlite3.connect(LDB + "macro.db3")
    names = dict(mc.execute("SELECT id, name FROM macro").fetchall())
    uc = sqlite3.connect(LDB + "user.db3")

    edits = uc.execute(
        "SELECT content_id, COUNT(*) FROM phrase_data "
        "WHERE macro_id != initial_macro_id AND content_id != 2 "
        "GROUP BY content_id ORDER BY COUNT(*) DESC").fetchall()
    content = dict(uc.execute("SELECT id, song_id FROM content").fetchall())

    db = Rekordbox6Database()
    meta = {}
    for c in db.get_content():
        try:
            meta[int(c.ID)] = (c.Title or "?", c.FolderPath or "",
                               c.AnalysisDataPath or "")
        except Exception:
            pass

    picked = []
    for cid, n in edits:
        sid = content.get(cid)
        if sid in meta and os.path.exists(meta[sid][1]):
            picked.append((cid, sid, n))
        if len(picked) >= N_TRACKS:
            break
    print(f"validating on {len(picked)} tracks", flush=True)

    root = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
    report = []
    for cid, sid, n_edits in picked:
        title, path, anlz = meta[sid]
        print(f"--- {title[:60]} ({n_edits} edits)", flush=True)
        cache_f = os.path.join(CACHE, f"{sid}.json")
        try:
            if os.path.exists(cache_f):
                segs = json.load(open(cache_f))
            else:
                r = allin1.analyze(path, device="cpu")
                segs = [{"start": s.start, "end": s.end, "label": s.label}
                        for s in r.segments]
                json.dump(segs, open(cache_f, "w"))
                for d in ("demix", "spec"):
                    shutil.rmtree(d, ignore_errors=True)

            base = os.path.dirname(root + anlz)
            ext = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT"))
            dat = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT"))
            pssi = ext.get_tag("PSSI").content
            beats = dat.get_tag("PQTZ").content.entries
            times = {i + 1: b.time / 1000.0 for i, b in enumerate(beats)}
            entries = list(pssi.entries)
            track_end = beats[-1].time / 1000.0

            rows = dict(uc.execute(
                "SELECT phrase_num, macro_id FROM phrase_data WHERE content_id=?",
                (cid,)).fetchall())

            per_phrase = []
            for j, e in enumerate(entries):
                t0 = times.get(e.beat)
                t1 = times.get(entries[j + 1].beat) if j + 1 < len(entries) else track_end
                if t0 is None or t1 is None or t1 <= t0:
                    continue
                kind_num = int(str(e.kind).split()[0]) if str(e.kind).split()[0].isdigit() else 0
                rb = RB_KINDS.get(kind_num)
                allan = scene_role(names.get(rows.get(e.index, -1)))
                overlap = {}
                for s in segs:
                    o = max(0.0, min(t1, s["end"]) - max(t0, s["start"]))
                    if o > 0:
                        lab = ALLIN1_MAP.get(s["label"])
                        if lab:
                            overlap[lab] = overlap.get(lab, 0) + o
                ai = max(overlap, key=overlap.get) if overlap else None
                if allan:
                    per_phrase.append({"phrase": e.index, "t0": round(t0, 1),
                                       "rb": rb, "allan": allan, "ai": ai})
            n = len(per_phrase)
            rb_ok = sum(1 for p in per_phrase if p["rb"] == p["allan"])
            ai_ok = sum(1 for p in per_phrase if p["ai"] == p["allan"])
            report.append({"title": title, "content_id": cid, "n_phrases": n,
                           "rb_agree": rb_ok, "ai_agree": ai_ok,
                           "detail": per_phrase})
            print(f"    phrases={n} rekordbox agrees {rb_ok}, allin1 agrees {ai_ok}",
                  flush=True)
        except Exception as ex:
            print(f"    ERROR {type(ex).__name__}: {ex}", flush=True)
            report.append({"title": title, "content_id": cid,
                           "error": f"{type(ex).__name__}: {ex}"})
        json.dump(report, open(OUT, "w"), indent=1)

    ok = [r for r in report if "n_phrases" in r and r["n_phrases"]]
    tot = sum(r["n_phrases"] for r in ok)
    print(f"\nTOTAL over {len(ok)} tracks, {tot} phrases: "
          f"rekordbox {sum(r['rb_agree'] for r in ok)}/{tot}, "
          f"allin1 {sum(r['ai_agree'] for r in ok)}/{tot}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
