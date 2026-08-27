"""Dry preview: show what the engine WOULD assign for given content_ids, using
current code — no DB writes, no rekordbox-closed requirement. For before/after
tuning checks."""
import json, os, random, re, sqlite3, sys
import numpy as np
sys.path.insert(0, os.path.expanduser("~/rekordbox-lighting"))
from phrase_energy_test import decode, phrase_features
from assign_lighting import (ensemble_labels, pick_scenes, drop_profile, AMAP, CACHE,
                             VIBE_W, KEY_PRIORS)

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
from pyrekordbox import Rekordbox6Database
from pyrekordbox.anlz import AnlzFile

mc = sqlite3.connect(LDB + "macro.db3")
name2id = {}
for mid, nm in mc.execute("SELECT id, name FROM macro"):
    name2id.setdefault(nm, mid)
uc = sqlite3.connect(LDB + "user.db3")
db = Rekordbox6Database()
meta = {}
for c in db.get_content():
    try:
        meta[int(c.ID)] = (c.Title or "?", c.FolderPath or "", c.AnalysisDataPath or "",
                           (c.Key.ScaleName if c.Key else "") or "")
    except Exception:
        pass
root = os.path.expanduser("~/Library/Pioneer/rekordbox/share")

for cid in [int(a) for a in sys.argv[1:]]:
    sid, = uc.execute("SELECT song_id FROM content WHERE id=?", (cid,)).fetchone()
    title, path, anlz, key = meta[sid]
    segf = os.path.join(CACHE, f"{sid}.json")
    if not (os.path.exists(path) and os.path.exists(segf)):
        print(f"SKIP {title}: not ready"); continue
    segs = json.load(open(segf))
    base = os.path.dirname(root + anlz)
    pssi = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT")).get_tag("PSSI").content
    beats = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT")).get_tag("PQTZ").content.entries
    times = {i + 1: b.time / 1000.0 for i, b in enumerate(beats)}
    entries = list(pssi.entries); end = beats[-1].time / 1000.0
    win = []; ents = []
    for j, e in enumerate(entries):
        t0 = times.get(e.beat); t1 = times.get(entries[j + 1].beat) if j + 1 < len(entries) else end
        if t0 and t1 and t1 > t0: win.append((t0, t1)); ents.append(e)
    d_ai = []
    for t0, t1 in win:
        ov = {}
        for s in segs:
            o = max(0.0, min(t1, s["end"]) - max(t0, s["start"]))
            lab = AMAP.get(s["label"])
            if o > 0 and lab: ov[lab] = ov.get(lab, 0) + o
        d_ai.append(max(ov, key=ov.get) if ov else None)
    feats = phrase_features(decode(path), win)
    plens = [(ents[m + 1].beat - ents[m].beat) if m + 1 < len(ents)
             else (pssi.end_beat - ents[m].beat) for m in range(len(ents))]
    labels = ensemble_labels(d_ai, feats, plens)
    from assign_lighting import VIBE_PATTERN
    dp = drop_profile(labels, feats)
    club = bool(dp and (dp["abs"] >= 0.9 or (dp["abs"] + dp["wall"]) / 2 >= 0.85))
    weights = {v: 1.0 for v in VIBE_PATTERN if not v.startswith("CLUB")}
    forced = random.Random(sid).choice(sorted(weights))   # even random (batch balances in the real run)
    base_v, scenes = pick_scenes(labels, weights, random.Random(sid), name2id, forced)
    nch = labels.count("CHORUS")
    print(f"\n{title[:52]}  [{key}, base {base_v}, {nch} chorus{' , CLUB-candidate' if club else ''}]")
    for (t0, _), lab, (mid, nm) in zip(win, labels, scenes):
        print(f"  {int(t0//60)}:{int(t0%60):02d}  {lab:6}  {nm}")
