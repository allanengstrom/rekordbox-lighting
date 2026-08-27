"""Store per-phrase (rms, kick-fraction, is-chorus) profiles for every
hand-vibed track, so contrast-metric variants can be tested without re-decoding.
Output: drop_profiles.json
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.expanduser("~/rekordbox-lighting"))
from phrase_energy_test import decode, phrase_features

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
OUT = os.path.expanduser("~/rekordbox-lighting/drop_profiles.json")
PAT = {1: "COOL", 2: "NATURAL", 3: "HOT", 4: "SUBTLE", 5: "WARM", 6: "VIVID",
       19: "CLUB1", 20: "CLUB2"}


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

    pop = {}
    for cid, sid, pid in uc.execute("SELECT id, song_id, macro_pattern_id FROM content"):
        v = PAT.get(pid)
        if v and (pid in (19, 20) or sid in curated):
            pop[(sid, cid)] = v

    cache = json.load(open(OUT)) if os.path.exists(OUT) else {}
    root = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
    n = 0
    for (sid, cid), vibe in pop.items():
        if str(sid) in cache:
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
            win, ch = [], []
            for j, e in enumerate(entries):
                t0 = times.get(e.beat)
                t1 = times.get(entries[j + 1].beat) if j + 1 < len(entries) else end
                if t0 is None or t1 is None or t1 <= t0:
                    continue
                win.append((t0, t1))
                ch.append(1 if "CHORUS" in names.get(rows.get(e.index), "") else 0)
            if sum(ch) < 1 or len(ch) - sum(ch) < 2:
                continue
            feats = phrase_features(decode(path), win)
            cache[str(sid)] = {"vibe": vibe, "title": title[:50],
                               "ph": [[round(f[0], 5), round(f[1], 4), c]
                                      for f, c in zip(feats, ch)]}
            n += 1
            if n % 20 == 0:
                json.dump(cache, open(OUT, "w"))
                print(f"progress: {len(cache)}", flush=True)
        except Exception:
            pass
    json.dump(cache, open(OUT, "w"))
    print(f"DONE: {len(cache)} profiles", flush=True)


if __name__ == "__main__":
    main()
