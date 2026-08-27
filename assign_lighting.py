"""The assignment engine: label a track's phrases (ensemble) and write scene
assignments into rekordbox's lighting DB, with a curated vibe preference model + spice dial.

Usage: assign_lighting.py <content_id> [...]
Refuses to write while rekordbox is running. Appends undo SQL to rollback_engine.sql.
"""
import json, os, random, re, sqlite3, subprocess, sys
from collections import Counter
import numpy as np

from phrase_energy_test import decode, phrase_features
from edit_phrases import apply_pssi, RoundTripError

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
KIND_OF = {"INTRO": 1, "UP": 2, "DOWN": 3, "CHORUS": 5, "OUTRO": 6}
K1_OF = {1: 1, 2: 0, 3: 0, 5: 1, 6: 1}   # rekordbox's style bit per kind (mood=high)
CACHE = os.path.expanduser("~/rekordbox-lighting/allin1_cache")
ROLLBACK = os.path.expanduser("~/rekordbox-lighting/rollback_engine.sql")
AMAP = {"intro": "INTRO", "verse": "UP", "chorus": "CHORUS", "inst": "CHORUS",
        "solo": "CHORUS", "break": "DOWN", "bridge": "DOWN", "outro": "OUTRO",
        "start": "INTRO", "end": "OUTRO"}
# Curated vibe distribution, conditioned on key mode (fit from a reference playlist)
VIBE_W = {"minor": {"COOL": 32, "HOT": 25, "NATURAL": 18, "WARM": 11, "VIVID": 10, "SUBTLE": 10},
          "major": {"WARM": 15, "VIVID": 15, "SUBTLE": 12, "COOL": 9, "NATURAL": 8, "HOT": 3}}
VIBE_PATTERN = {"COOL": 1, "NATURAL": 2, "HOT": 3, "SUBTLE": 4, "WARM": 5, "VIVID": 6,
                "CLUB1": 19, "CLUB2": 20}   # 19/20 = the HIGH-mood club rows
SPICE = 0.15
# base-vibe draw shaping: 1.0 = faithful to the curated per-key history (COOL-heavy on
# minor keys, as in the reference library); <1 flattens toward more color variety per batch.
VIBE_TEMP = 1.0
_KP = os.path.expanduser("~/rekordbox-lighting/key_vibe_priors.json")
KEY_PRIORS = json.load(open(_KP)) if os.path.exists(_KP) else {}
_DS = os.path.expanduser("~/rekordbox-lighting/drop_stats.json")
DROP_STATS = json.load(open(_DS)) if os.path.exists(_DS) else None


def drop_profile(labels, feats):
    """Percentiles (vs the hand-annotated reference library) of: absolute drop slam,
    impact jump at chorus onset, and wall-to-wall loudness fraction."""
    if DROP_STATS is None:
        return None
    import bisect, math, statistics as stt
    pct = lambda k, x: bisect.bisect_left(DROP_STATS[k], x) / max(1, len(DROP_STATS[k]))
    rms = [f[0] for f in feats]
    ch = [1 if l == "CHORUS" else 0 for l in labels]
    c = [r for r, k in zip(rms, ch) if k and r > 0]
    o = [r for r, k in zip(rms, ch) if not k and r > 0]
    if not c or not o:
        return None
    jumps = [20 * math.log10(rms[i] / (rms[i - 1] + 1e-9)) for i in range(1, len(ch))
             if ch[i] and not ch[i - 1] and rms[i] > 0 and rms[i - 1] > 0]
    absd = stt.median(c)
    wall = sum(r >= absd * 10 ** (-2 / 20) for r in o) / len(o)
    return {"abs": pct("abs", absd), "jump": pct("jump", max(jumps) if jumps else 0),
            "wall": pct("wall", wall)}


def ensemble_labels(d_ai, feats, plens=None):
    n = len(d_ai)
    if plens is None:
        plens = [1.0] * n
    loud = np.array([f[0] for f in feats]); kickf = np.array([f[1] for f in feats])
    lz = (loud - loud.mean()) / (loud.std() + 1e-9)
    km = np.median(kickf)
    lab = list(d_ai)
    for i in range(n):                                 # analyzer silent -> waveform rule
        if lab[i] is None:
            lab[i] = "CHORUS" if (lz[i] >= 0.35 and kickf[i] >= km) else \
                     ("DOWN" if (kickf[i] < km or lz[i] < 0) else "UP")
    i = 0                                              # trim non-drop phrases from chorus runs
    while i < n:
        if lab[i] == "CHORUS":
            j = i
            while j + 1 < n and lab[j + 1] == "CHORUS": j += 1
            peak = max(lz[i:j + 1])
            # a phrase is the DROP only if it's near the run's peak loudness, or
            # clearly kicking-and-loud. Melodic builds/risers inside a long run
            # (kick off, or well below peak) become UP — so dubstep drops carry
            # real builds instead of wall-to-wall chorus, and analyzer-lumped
            # drop+verse+drop runs split at the quiet parts.
            for m in range(i, j + 1):
                # a real drop needs the KICK and near-peak loudness. Loud risers with
                # no kick (synth builds right before the drop) are UP, not chorus.
                is_drop = kickf[m] >= km and peak - lz[m] <= 0.6
                if not is_drop:
                    lab[m] = "DOWN" if lz[m] < -0.4 else "UP"
            # keep a sustained drop whole: a lone non-drop phrase between two drop
            # phrases is a momentary dip, not a real break -> stays CHORUS (operator feedback:
            # "it chopped up the 2nd chorus when it should have just stayed")
            for m in range(i + 1, j):
                if lab[m] != "CHORUS" and lab[m - 1] == "CHORUS" and lab[m + 1] == "CHORUS":
                    lab[m] = "CHORUS"
            i = j + 1
        else: i += 1
    mids = np.array([f[3] if len(f) > 3 else 0.0 for f in feats])
    i = 0                                  # drop tail: sustained step DOWN inside a chorus
    while i < n:                           # run + lead thinning -> rolling groove (INTRO2,
        if lab[i] == "CHORUS":             # the reference post-drop move, 61x in the hand-edits)
            j = i
            while j + 1 < n and lab[j + 1] == "CHORUS": j += 1
            if j > i:
                dz, k = max((lz[k - 1] - lz[k], k) for k in range(i + 1, j + 1))
                if dz >= 0.15 and mids[k] <= 0.9 * max(mids[i:k]) \
                        and max(lz[k:j + 1]) < max(lz[i:k]) - 0.15:
                    for m in range(k, j + 1): lab[m] = "INTRO"
            i = j + 1
        else: i += 1
    # a mid-track drop's LAST chunk is its wind-down into the breakdown, not the
    # drop itself (per the reference library): if a chorus run isn't the track's last chorus and ends
    # on a SHORTER phrase than its main (longest) drop phrase, that tail -> INTRO
    i = 0
    while i < n:
        if lab[i] == "CHORUS":
            j = i
            while j + 1 < n and lab[j + 1] == "CHORUS": j += 1
            if j > i and any(lab[m] == "CHORUS" for m in range(j + 1, n)):
                main = max(range(i, j + 1), key=lambda m: plens[m])
                # wind-down only if the tail is BOTH shorter AND quieter than the main
                # drop — never demote a peak-loud phrase (chorus belongs at the peaks)
                if plens[j] < plens[main] and lz[j] < max(lz[i:j]) - 0.3:
                    lab[j] = "INTRO"
            i = j + 1
        else: i += 1
    # post-drop grammar, verified on the 27 post-drop phrases in the hand-labeled
    # tracks: collapse -> DOWN (13/13); after the final drop -> DOWN wind-down; beats
    # still rolling mid-track -> INTRO groove (6/8); lone gap -> DOWN unless building
    last_ch = max((k for k in range(n) if lab[k] == "CHORUS"), default=-1)
    for i in range(1, n):
        if lab[i - 1] != "CHORUS" or lab[i] != "UP":
            continue
        if kickf[i] < km or lz[i] < -0.5 or i > last_ch:
            lab[i] = "DOWN"
        elif i + 1 < n and lab[i + 1] == "CHORUS":
            if feats[i][2] <= 0:
                lab[i] = "DOWN"
        else:
            lab[i] = "INTRO"
    # short-down / big-up-build (the preferred reading): a breakdown that rebuilds into a drop
    # keeps only its deepest phrases DOWN; once energy lifts off the floor the rest
    # becomes UP building into the drop (down shouldn't run long)
    i = 0
    while i < n:
        if lab[i] == "DOWN":
            j = i
            while j + 1 < n and lab[j + 1] == "DOWN": j += 1
            k = j + 1
            while k < n and lab[k] == "UP": k += 1
            if k < n and lab[k] == "CHORUS":           # this run builds into a drop
                for m in range(i + 1, j + 1):          # only the first phrase stays DOWN;
                    lab[m] = "UP"                      # the rest is the build (reference note: less
                                                       # DOWN in the buildup to a drop)
            i = j + 1
        else: i += 1
    # CHORUS lives at the highest waveform points (reference rule): any near-peak kicking
    # phrase IS the drop — force it, overriding any wind-down/groove demotion
    peak = max(lz)
    for i in range(1, n - 1):        # ends keep their intro/outro caps
        if lz[i] >= peak - 0.3 and kickf[i] >= km:
            lab[i] = "CHORUS"                       # near-peak kicking = the drop
        elif lab[i] == "CHORUS" and lz[i] < peak - 0.5:
            lab[i] = "UP" if lz[i] >= -0.9 else "DOWN"   # not near-peak -> not the drop
    # caps — cold-open stays CHORUS only on overwhelming evidence (validation showed
    # the loose version re-created rekordbox's intro-as-chorus habit 4x)
    if not (lab[0] == "CHORUS" and lz[0] >= 0.9 and kickf[0] >= km): lab[0] = "INTRO"
    # the "second part of the intro" (loud intro groove before the first real drop)
    # often reads as chorus. If the first chorus is early, sits below the track's
    # peak, and a clearly louder drop comes later, it's still intro — not the drop.
    first_ch = next((k for k in range(n) if lab[k] == "CHORUS"), None)
    if first_ch is not None and first_ch <= 2:
        j = first_ch
        while j + 1 < n and lab[j + 1] == "CHORUS": j += 1
        run_peak = max(lz[first_ch:j + 1])
        if run_peak < max(lz) - 0.4 and any(lab[k] == "CHORUS" and lz[k] >= run_peak + 0.3
                                            for k in range(j + 1, n)):
            for m in range(first_ch, j + 1): lab[m] = "INTRO"
        # isolated loud INTRO HIT: a single early chorus flanked by much-quieter
        # phrases is an intro stab, not the drop (Ophelia 0:14: +1.0 between -1.3/-1.1)
        elif j == first_ch:
            pq = lz[first_ch - 1] if first_ch > 0 else -9
            nq = lz[first_ch + 1] if first_ch + 1 < n else -9
            if lz[first_ch] - pq > 1.2 and lz[first_ch] - nq > 1.2:
                lab[first_ch] = "INTRO"
    # Reference tracks open with two INTROs (57/109 hand-edited tracks) unless the drop is imminent
    if n > 2 and lab[0] == "INTRO" and lab[1] == "UP" and lab[2] != "CHORUS":
        lab[1] = "INTRO"
    # ...and once the track has opened up, a collapse phrase in the opening run is
    # the moody DOWN pocket (INTRO1 -> INTRO2 -> DOWN, 55/109), not a third INTRO
    i = 2
    while i < n and lab[i] == "INTRO":
        if kickf[i] < km and lz[i] < -0.5 and max(lz[:i]) - lz[i] >= 0.5:
            lab[i] = "DOWN"
        i += 1
    if lab[-1] != "CHORUS": lab[-1] = "OUTRO"
    for i in range(n - 1):        # OUTROs never stack: before the final phrase,
        if lab[i] == "OUTRO":     # the tail rolls as INTRO if beats continue, else DOWN
            lab[i] = "INTRO" if (kickf[i] >= km and lz[i] >= -0.5) else "DOWN"
    # DOWN is a slow scene — reserve it for genuinely low volume (reference note: it's overused).
    # A only-moderately-quiet phrase is really a build/groove, not a DOWN.
    for i in range(n):
        if lab[i] == "DOWN" and lz[i] >= -0.9:
            lab[i] = "UP"
    for i in range(1, n):                              # guarantee a build before each drop —
        if lab[i] == "CHORUS" and lab[i - 1] != "CHORUS":   # but a lone post-drop gap stays DOWN
            w = [k for k in range(max(0, i - 2), i) if lab[k] not in ("CHORUS", "INTRO")
                 and not (k > 0 and lab[k - 1] == "CHORUS")]
            if w and not any(lab[k] == "UP" for k in w):
                lab[max(w, key=lambda k: feats[k][2])] = "UP"
    # safety net: every track needs at least one drop. If the rules left none,
    # make the loudest non-intro/outro phrase the chorus.
    if "CHORUS" not in lab:
        cand = [k for k in range(n) if lab[k] not in ("INTRO", "OUTRO")] or list(range(n))
        lab[max(cand, key=lambda k: lz[k])] = "CHORUS"
    return lab


def pick_scenes(labels, weights, rng, name2id, pin=None):
    vibes, wts = list(weights), list(weights.values())
    club = pin if pin in ("CLUB1", "CLUB2") else None
    base = pin if pin else rng.choices(vibes, [w ** VIBE_TEMP for w in wts])[0]
    # UP variants are intensity levels in the scene bank: first build phrase = UP1,
    # the climax phrase right before a drop = UP3, anything between = UP2
    upvar, i = {}, 0
    while i < len(labels):
        if labels[i] == "UP":
            j = i
            while j + 1 < len(labels) and labels[j + 1] == "UP": j += 1
            nxt = labels[j + 1] if j + 1 < len(labels) else None
            for k in range(i, j + 1):
                upvar[k] = 3 if (k == j and nxt == "CHORUS") else (1 if k == i else 2)
            i = j + 1
        else: i += 1
    cyc = {"INTRO": [1, 2], "CHORUS": [1, 2]}
    ci = {k: 0 for k in cyc}
    out, prev, prevnm, used, club_ch = [], None, None, Counter(), 0
    other_club = ("CLUB2" if club == "CLUB1" else "CLUB1") if club else None
    for pi, lab in enumerate(labels):
        if club:
            if lab == "UP":
                stem, banks = f"UP{upvar[pi]}", [club, other_club]
            elif lab == "CHORUS":
                # each CLUB bank has ONE chorus — alternate CLUB1/CLUB2 for variety
                first = club if club_ch % 2 == 0 else other_club
                stem, banks, club_ch = "CHORUS", [first, other_club if first == club else club], club_ch + 1
            else:
                role = {"DOWN": "DOWN", "INTERLUDE": "DOWN"}.get(lab, lab)
                stem, banks = role, [club, other_club]
            nm = f"{stem} {banks[0]}"
            if nm == prevnm and len(banks) > 1:      # never the same scene twice in a row
                nm = f"{stem} {banks[1]}"
            mid = name2id.get(nm) or name2id.get(f"{stem} {club}") or name2id.get(f"DOWN {club}")
            out.append((mid, nm if mid else nm + " (MISSING)"))
            prev, prevnm = lab, nm
            continue
        vibe = base
        if rng.random() < SPICE:
            alt = rng.choices(vibes, wts)[0]
            vibe = alt if alt != base else vibe
        if lab == "DOWN":
            stem = "HIGH DOWN"                       # the scene bank has no INTERLUDE
        elif lab == "UP":
            stem = f"HIGH UP{upvar[pi]}"
        elif lab == "OUTRO":
            # Reference endings: OUTRO2 straight off the final chorus, OUTRO1 after a wind-down
            stem = f"HIGH OUTRO{2 if prev == 'CHORUS' else 1}"
        else:
            idx = ci[lab] % len(cyc[lab]) if lab == "CHORUS" else min(ci[lab], len(cyc[lab]) - 1)
            v = cyc[lab][idx]; ci[lab] += 1         # intros clamp at INTRO2, choruses alternate
            stem = f"HIGH {lab}{v}"
        nm = f"{stem} {vibe}"
        # never the same scene twice in a row, and no exact scene >2x per song —
        # vary the COLOR (base preferred) to satisfy both
        if nm == prevnm or used[nm] >= 2:
            order = [vibe, base] + [x for x in vibes if x not in (vibe, base)]
            pick = next((v for v in order if f"{stem} {v}" != prevnm and used[f"{stem} {v}"] < 2),
                        None) or next((v for v in order if f"{stem} {v}" != prevnm), vibe)
            vibe, nm = pick, f"{stem} {pick}"
        used[nm] += 1
        mid = name2id.get(nm) or name2id.get(f"{stem} {base}") \
              or name2id.get(f"HIGH DOWN {vibe}")
        out.append((mid, nm if mid else nm + " (MISSING)"))
        prev, prevnm = lab, nm
    return base, out


def find_kick_return(y, times, b0, b1):
    """Inside a long DOWN phrase (1-based beats b0..b1), find the 8-beat boundary
    where sustained kick returns after a breakdown. Returns 1-based beat or None."""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=22050, n_fft=2048)
    band = S[(freqs >= 20) & (freqs <= 120)].sum(axis=0)
    hop_t = 512 / 22050
    blocks = []
    b = b0
    while b + 8 <= b1:
        t0, t1 = times.get(b), times.get(min(b + 8, b1))
        if t0 is None or t1 is None: return None
        blocks.append(float(band[int(t0 / hop_t):int(t1 / hop_t)].mean()))
        b += 8
    if len(blocks) < 4:
        return None
    loud_ref = float(np.percentile(band, 95))      # ~drop-level energy for this track
    for k in range(2, len(blocks) - 1):
        base = min(blocks[:k]) + 1e-9
        if blocks[k] >= 2.5 * base and blocks[k + 1] >= 2.0 * base \
                and blocks[k] >= 0.25 * loud_ref:
            if (b1 - (b0 + 8 * k)) >= 16:
                return b0 + 8 * k
    return None


def find_drop_onset(y, times, boundary, end_beat):
    """A drop's true onset is where sub-bass SLAMS back after a pre-drop cutout.
    rekordbox's phrase boundary can sit several bars off. Scan +/- ~4 bars around
    `boundary` (1-based beat) for the bar-aligned beat where bass goes near-silent
    then jumps to drop level. Return that beat if it's >=1 bar from the boundary
    and clearly a cutout->slam, else None (boundary already fine)."""
    import librosa
    lo = max(1, boundary - 16); hi = min(end_beat - 1, boundary + 12)
    if hi - lo < 8:
        return None
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=22050, n_fft=2048)
    band = S[(freqs >= 20) & (freqs <= 120)].sum(axis=0)
    hop_t = 512 / 22050
    def bval(b):
        t0, t1 = times.get(b), times.get(b + 1)
        if t0 is None or t1 is None: return None
        return float(band[int(t0 / hop_t):max(int(t1 / hop_t), int(t0 / hop_t) + 1)].mean())
    ref = float(np.percentile(band, 95)) + 1e-9
    best = None
    for b in range(lo, hi + 1):
        if (b - 1) % 4 != 0:                       # bar-aligned onsets only
            continue
        prev = [bval(x) for x in range(max(lo, b - 3), b)]
        nxt = [bval(x) for x in range(b, min(hi, b + 3) + 1)]
        prev = [p for p in prev if p is not None]; nxt = [p for p in nxt if p is not None]
        if not prev or not nxt: continue
        pre, post = np.mean(prev), np.mean(nxt)
        if pre <= 0.10 * ref and post >= 0.45 * ref:   # cutout -> slam
            if best is None or post - pre > best[1]:
                best = (b, post - pre)
    if best and abs(best[0] - boundary) >= 4:
        return best[0]
    return None


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it before writing lighting data.")
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
    undo = [f"-- engine run\n"]

    sync = "--no-sync" not in sys.argv
    vibe_pin = next((a.split("=", 1)[1].upper() for a in sys.argv
                     if a.startswith("--vibe=")), None)
    if vibe_pin and vibe_pin not in VIBE_PATTERN:
        sys.exit(f"unknown vibe {vibe_pin}; options: {', '.join(VIBE_PATTERN)}")
    vibe_pool = Counter()          # batch-balanced color spread across this run
    for cid in [int(a) for a in sys.argv[1:] if not a.startswith("--")]:
        sid, = uc.execute("SELECT song_id FROM content WHERE id=?", (cid,)).fetchone()
        title, path, anlz, key = meta[sid]
        seg_f = os.path.join(CACHE, f"{sid}.json")
        if not (os.path.exists(path) and os.path.exists(seg_f)):
            print(f"SKIP {title}: missing audio or analysis"); continue
        segs = json.load(open(seg_f))
        base = os.path.dirname(root + anlz)
        ext_path = os.path.join(base, "ANLZ0000.EXT")
        beats = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT")).get_tag("PQTZ").content.entries
        times = {i + 1: b.time / 1000.0 for i, b in enumerate(beats)}
        end = beats[-1].time / 1000.0
        y = decode(path)

        def grid_pipeline():
            pssi = AnlzFile.parse_file(ext_path).get_tag("PSSI").content
            entries = list(pssi.entries)
            win, pnums, ents = [], [], []
            for j, e in enumerate(entries):
                t0 = times.get(e.beat)
                t1 = times.get(entries[j + 1].beat) if j + 1 < len(entries) else end
                if t0 is None or t1 is None or t1 <= t0: continue
                win.append((t0, t1)); pnums.append(e.index); ents.append(e)
            d_ai = []
            for t0, t1 in win:
                ov = {}
                for s in segs:
                    o = max(0.0, min(t1, s["end"]) - max(t0, s["start"]))
                    lab = AMAP.get(s["label"])
                    if o > 0 and lab: ov[lab] = ov.get(lab, 0) + o
                d_ai.append(max(ov, key=ov.get) if ov else None)
            feats = phrase_features(y, win)
            plens = [(ents[m + 1].beat - ents[m].beat) if m + 1 < len(ents)
                     else (pssi.end_beat - ents[m].beat) for m in range(len(ents))]
            return pssi, ents, win, pnums, feats, ensemble_labels(d_ai, feats, plens)

        pssi, ents, win, pnums, feats, labels = grid_pipeline()

        # default: write the engine's structure back into the phrase grid itself, and
        # force HIGH mood — the scene bank only has HIGH scenes, so "medium"/"low" promote up
        if sync:
            set_ops, ins_ops = [], []
            for e, lab in zip(ents, labels):
                want = KIND_OF[lab]
                if e.kind != want:
                    set_ops.append({"beat": e.beat, "kind": want, "k1": K1_OF[want],
                                    "k2": 0, "b": 0, "beat_2": 0, "beat_3": 0,
                                    "beat_4": 0, "k3": 0})
            for i, (e, lab) in enumerate(zip(ents, labels)):
                nb = ents[i + 1].beat if i + 1 < len(ents) else pssi.end_beat
                if lab != "DOWN" or nb - e.beat < 32:
                    continue
                sp = find_kick_return(y, times, e.beat, nb)
                if sp:
                    ins = {"beat": sp, "kind": 2, "k1": 0}
                    if e.fill and e.beat_fill >= sp:      # drum fill belongs to the new phrase
                        ins["fill"], ins["beat_fill"] = 1, e.beat_fill
                        op = next((o for o in set_ops if o["beat"] == e.beat), None)
                        if op is None:
                            op = {"beat": e.beat}; set_ops.append(op)
                        op["fill"], op["beat_fill"] = 0, 0
                    ins_ops.append(ins)
            # snap each drop's start to where the bass actually slams in — rekordbox's
            # phrase boundary can be bars late, firing the chorus after the drop hits
            existing = {e.beat for e in ents}
            for i, (e, lab) in enumerate(zip(ents, labels)):
                if lab != "CHORUS" or (i > 0 and labels[i - 1] == "CHORUS"):
                    continue                              # only the phrase that starts a drop
                onset = find_drop_onset(y, times, e.beat, pssi.end_beat)
                if onset and onset < e.beat and onset not in existing \
                        and not any(o["beat"] == onset for o in ins_ops):
                    ins_ops.append({"beat": onset, "kind": 5, "k1": 1})
                    existing.add(onset)
                    print(f"  [grid] drop snapped earlier to beat {onset} "
                          f"({int(times[onset]//60)}:{times[onset]%60:04.1f})")
            promoted = pssi.mood != 1
            if set_ops or ins_ops or promoted:
                try:
                    bdir, _ = apply_pssi(ext_path, set_ops, ins_ops, str(cid), mood=1)
                    print(f"  [grid] {len(set_ops)} relabeled, {len(ins_ops)} split"
                          + (", promoted to HIGH" if promoted else "")
                          + f" — backup {os.path.basename(bdir)}")
                    pssi, ents, win, pnums, feats, labels = grid_pipeline()
                except RoundTripError:
                    print("  [grid] SKIPPED: file fails byte-exact round-trip; scenes only")
        # Color model (design intent): show every scene ~evenly, random per track
        # and varied WITHIN a key (sets are mixed in key, so key must NOT steer color) —
        # EXCEPT the biggest-drop songs, which are reserved for CLUB1/CLUB2.
        dp = drop_profile(labels, feats)
        weights = {v: 1.0 for v in VIBE_PATTERN if not v.startswith("CLUB")}  # uniform (for spice)
        big_drop = bool(dp and (dp["abs"] >= 0.9 or (dp["abs"] + dp["wall"]) / 2 >= 0.85))
        if vibe_pin:
            forced = vibe_pin
        elif big_drop and random.Random(sid ^ 0xC1).random() < 0.5:
            # CLUB lands on some (not all) of the biggest drops, regardless
            # of color (verified: reference CLUB tracks skew high drop-slam, ~half of big
            # drops). CLUB2 for wall-to-wall sustained drops, else CLUB1.
            forced = "CLUB2" if dp["wall"] >= 0.7 else "CLUB1"
            print(f"  [color] big drop -> {forced} sprinkled (slam p{int(dp['abs']*100)})")
        else:
            # batch-balanced: hand out the least-used color so far this run, so the
            # whole set is ~even; seeded tiebreak keeps it varied (incl. within a key)
            lo = min(vibe_pool[v] for v in weights)
            cands = sorted(v for v in weights if vibe_pool[v] == lo)
            forced = random.Random(sid).choice(cands)
            vibe_pool[forced] += 1
        rng = random.Random(sid)
        basevibe, scenes = pick_scenes(labels, weights, rng, name2id, forced)

        # rollback + write
        old_pat, = uc.execute("SELECT macro_pattern_id FROM content WHERE id=?", (cid,)).fetchone()
        undo.append(f"UPDATE content SET macro_pattern_id={old_pat} WHERE id={cid};")
        old_rows = dict(uc.execute(
            "SELECT phrase_num, macro_id FROM phrase_data WHERE content_id=?", (cid,)).fetchall())
        for pn, om in old_rows.items():
            undo.append(f"UPDATE phrase_data SET macro_id={om} WHERE content_id={cid} AND phrase_num={pn};")
        uc.execute("UPDATE content SET macro_pattern_id=? WHERE id=?", (VIBE_PATTERN[basevibe], cid))
        print(f"\n{title}  [{key}, base vibe {basevibe}]")
        for pn, (t0, t1), lab, (mid, nm) in zip(pnums, win, labels, scenes):
            if mid is None: continue
            if pn in old_rows:
                uc.execute("UPDATE phrase_data SET macro_id=? WHERE content_id=? AND phrase_num=?",
                           (mid, cid, pn))
            else:
                uc.execute("INSERT INTO phrase_data (content_id, phrase_num, macro_id, initial_macro_id) "
                           "VALUES (?,?,?,?)", (cid, pn, mid, mid))
            print(f"  {int(t0//60)}:{int(t0%60):02d}  {lab:6}  {nm}")
        uc.commit()
        # phrase display variant (Intro 1 vs Intro 2 etc.) follows the assigned scene
        if sync:
            vops = []
            for e, (mid, nm) in zip(ents, scenes):
                m = re.search(r"(INTRO|CHORUS|OUTRO)(\d)", nm)
                if not m or e.kind not in (1, 5, 6):
                    continue
                k1 = 1 if m.group(2) == "1" else 0
                if e.k1 != k1:
                    vops.append({"beat": e.beat, "k1": k1})
            if vops:
                try:
                    apply_pssi(ext_path, vops, [], f"{cid}-var")
                    print(f"  [grid] {len(vops)} phrase display variants synced to scenes")
                except RoundTripError:
                    pass
        # playback layer: bake to custom rows — scene mode fades across phrase
        # boundaries whenever a phrase isn't a whole number of scene loops
        mf_path = os.path.expanduser("~/rekordbox-lighting/engine_bakes.json")
        mf = json.load(open(mf_path)) if os.path.exists(mf_path) else {}
        owned = set(mf.get(str(cid), []))
        existing = [r[0] for r in uc.execute(
            "SELECT id FROM lighting_data WHERE content_id=?", (cid,))]
        if set(existing) - owned:
            print(f"  [bake] SKIPPED: hand-built custom rows present — not touching them")
        else:
            if existing:
                uc.execute("DELETE FROM lighting_data WHERE content_id=?", (cid,))
                uc.commit()
            r = subprocess.run([sys.executable,
                                os.path.expanduser("~/rekordbox-lighting/bake_custom.py"),
                                str(cid)], capture_output=True, text=True)
            if r.returncode == 0:
                print("  [bake] playback baked — hard cuts at every scene change")
            else:
                print(f"  [bake] FAILED: {(r.stdout + r.stderr)[-300:]}")
    with open(ROLLBACK, "a") as f:
        f.write("\n".join(undo) + "\n")
    print(f"\nwritten. undo appended to {ROLLBACK}")


if __name__ == "__main__":
    main()
