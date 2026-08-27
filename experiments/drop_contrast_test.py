"""Test the hypothesis: vibe energy-tier follows DROP CONTRAST — how much
louder the chorus/drop phrases are than the rest of the track.
Big contrast -> {COOL, HOT, CLUB1, CLUB2}; small -> {NATURAL, VIVID, WARM, SUBTLE}.

Ground truth: curated "Here we go" tracks (deliberate vibes) + club rows 19/20.
Caches per-track contrast in drop_contrast_cache.json.
"""
import json, math, os, sqlite3, sys
import numpy as np

sys.path.insert(0, os.path.expanduser("~/rekordbox-lighting"))
from phrase_energy_test import decode, phrase_features

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
CACHE = os.path.expanduser("~/rekordbox-lighting/drop_contrast_cache.json")
PAT = {1: "COOL", 2: "NATURAL", 3: "HOT", 4: "SUBTLE", 5: "WARM", 6: "VIVID",
       19: "CLUB1", 20: "CLUB2"}
BIG = {"COOL", "HOT", "CLUB1", "CLUB2"}


def main():
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.anlz import AnlzFile

    uc = sqlite3.connect(LDB + "user.db3")
    mc = sqlite3.connect(LDB + "macro.db3")
    names = dict(mc.execute("SELECT id, name FROM macro"))
    db = Rekordbox6Database()
    meta = {}
    for c in db.get_content():
        try:
            meta[int(c.ID)] = (c.Title or "?", c.FolderPath or "", c.AnalysisDataPath or "",
                               (c.Length or 0))
        except Exception:
            pass
    pl = [p for p in db.get_playlist() if p.Name == "Here we go"][0]
    curated = {int(s.ContentID) for s in pl.Songs}

    pop = {}   # sid -> vibe
    for cid, sid, pid in uc.execute("SELECT id, song_id, macro_pattern_id FROM content"):
        v = PAT.get(pid)
        if not v:
            continue
        if pid in (19, 20) or sid in curated:
            pop[(sid, cid)] = v

    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    root = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
    done = fail = 0
    for (sid, cid), vibe in pop.items():
        key = str(sid)
        if key in cache:
            continue
        title, path, anlz, length = meta.get(sid, ("?", "", "", 0))
        if not (path and os.path.exists(path)) or length > 600 or title.startswith("REC"):
            continue
        try:
            base = os.path.dirname(root + anlz)
            pssi = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT")).get_tag("PSSI").content
            beats = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT")).get_tag("PQTZ").content.entries
            times = {i + 1: b.time / 1000.0 for i, b in enumerate(beats)}
            end = beats[-1].time / 1000.0
            rows = dict(uc.execute("SELECT phrase_num, macro_id FROM phrase_data "
                                   "WHERE content_id=?", (cid,)))
            entries = list(pssi.entries)
            win, roles = [], []
            for j, e in enumerate(entries):
                t0 = times.get(e.beat)
                t1 = times.get(entries[j + 1].beat) if j + 1 < len(entries) else end
                if t0 is None or t1 is None or t1 <= t0:
                    continue
                nm = names.get(rows.get(e.index), "")
                win.append((t0, t1))
                roles.append("CH" if "CHORUS" in nm else "other")
            if roles.count("CH") < 1 or roles.count("other") < 2:
                continue
            feats = phrase_features(decode(path), win)
            ch = [f[0] for f, r in zip(feats, roles) if r == "CH" and f[0] > 0]
            ot = [f[0] for f, r in zip(feats, roles) if r == "other" and f[0] > 0]
            if not ch or not ot:
                continue
            contrast = 20 * math.log10(float(np.median(ch)) / (float(np.median(ot)) + 1e-9))
            cache[key] = {"vibe": vibe, "contrast_db": round(contrast, 2),
                          "title": title[:50]}
            done += 1
        except Exception:
            fail += 1
        if done % 15 == 0:
            json.dump(cache, open(CACHE, "w"))
            print(f"progress: {len(cache)} cached ({fail} failed)", flush=True)
    json.dump(cache, open(CACHE, "w"))
    print(f"DONE: {len(cache)} tracks cached ({fail} failed)", flush=True)

    import statistics as st
    byv = {}
    for v in cache.values():
        byv.setdefault(v["vibe"], []).append(v["contrast_db"])
    print(f"\n{'vibe':>8} {'n':>4} {'median dB':>10} {'mean':>6}")
    order = ["CLUB2", "CLUB1", "HOT", "COOL", "NATURAL", "VIVID", "WARM", "SUBTLE"]
    for v in order:
        if v in byv:
            a = byv[v]
            print(f"{v:>8} {len(a):>4} {st.median(a):>10.2f} {st.mean(a):>6.2f}")
    big = [v["contrast_db"] for v in cache.values() if v["vibe"] in BIG]
    small = [v["contrast_db"] for v in cache.values() if v["vibe"] not in BIG]
    wins = sum((x > y) + 0.5 * (x == y) for x in big for y in small)
    print(f"\nbig-tier n={len(big)} median {st.median(big):.2f}dB | "
          f"small-tier n={len(small)} median {st.median(small):.2f}dB | "
          f"AUC {wins / max(1, len(big) * len(small)):.2f}")
    clubc = [v["contrast_db"] for v in cache.values() if v["vibe"].startswith("CLUB")]
    nonclub = [v["contrast_db"] for v in cache.values() if not v["vibe"].startswith("CLUB")]
    wins = sum((x > y) + 0.5 * (x == y) for x in clubc for y in nonclub)
    print(f"club vs rest: club median {st.median(clubc):.2f}dB, rest "
          f"{st.median(nonclub):.2f}dB, AUC {wins / max(1, len(clubc) * len(nonclub)):.2f}")


if __name__ == "__main__":
    main()
