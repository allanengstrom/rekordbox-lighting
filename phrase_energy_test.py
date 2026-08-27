"""Hypothesis: phrase roles are readable from the waveform alone.

Per rekordbox phrase: loudness z-score (within track), kick-band share, and
end-of-phrase ramp. Grammar: intro/outro caps, chorus = loud + kick,
pre-drop = UP if ramping else DOWN, rest by kick/loudness. Scored vs the hand-labeled reference.
"""
import json, os, sqlite3, subprocess, sys
import numpy as np

SR = 22050
LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")


def decode(path):
    import imageio_ffmpeg
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path,
           "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, timeout=300).stdout,
                         dtype=np.float32)


def phrase_features(y, windows):
    import librosa
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    kick = S[(freqs >= 20) & (freqs <= 120)].sum(axis=0)
    mid = S[(freqs >= 400) & (freqs <= 4000)].sum(axis=0)   # lead/vocal body
    total = S.sum(axis=0) + 1e-9
    rms = librosa.feature.rms(S=S)[0]
    hop_t = 512 / SR
    out = []
    for t0, t1 in windows:
        i0, i1 = int(t0 / hop_t), max(int(t0 / hop_t) + 4, int(t1 / hop_t))
        r = rms[i0:i1]; k = kick[i0:i1] / total[i0:i1]; md = mid[i0:i1] / total[i0:i1]
        if not len(r): out.append((0, 0, 0, 0)); continue
        tail = r[int(len(r) * 0.6):]
        ramp = np.polyfit(np.arange(len(tail)), tail, 1)[0] if len(tail) > 4 else 0
        out.append((float(np.median(r)), float(np.median(k)), float(ramp),
                    float(np.median(md))))
    return out


def classify(feats):
    n = len(feats)
    loud = np.array([f[0] for f in feats]); kickf = np.array([f[1] for f in feats])
    lz = (loud - loud.mean()) / (loud.std() + 1e-9)
    km = np.median(kickf)
    labels = [None] * n
    chorus = (lz >= 0.35) & (kickf >= km)
    for i in range(n):
        if chorus[i]: labels[i] = "CHORUS"
    # caps
    if labels[0] != "CHORUS" or lz[0] < 0.6: labels[0] = "INTRO"
    if labels[-1] != "CHORUS": labels[-1] = "OUTRO"
    # run-ups: within 2 phrases before a chorus run
    for i in range(n):
        if labels[i] is not None: continue
        nxt = next((j for j in range(i + 1, min(i + 3, n)) if labels[j] == "CHORUS"), None)
        if nxt is not None:
            labels[i] = "UP" if feats[i][2] > 0 or lz[i] > -0.25 else "DOWN"
    # everything else: kick off or quiet -> DOWN, else UP
    for i in range(n):
        if labels[i] is None:
            labels[i] = "DOWN" if (kickf[i] < km or lz[i] < 0) else "UP"
    return labels


def main():
    from pyrekordbox import Rekordbox6Database
    uc = sqlite3.connect(LDB + "user.db3")
    sid_of = dict(uc.execute("SELECT id, song_id FROM content"))
    db = Rekordbox6Database()
    paths = {}
    for c in db.get_content():
        try: paths[int(c.ID)] = c.FolderPath or ""
        except Exception: pass

    rep = json.load(open(os.path.expanduser("~/rekordbox-lighting/validation_report.json")))
    tot = {"n": 0, "hit": 0, "ch_hit": 0, "ch_n": 0, "ch_fp": 0, "up_hit": 0, "up_n": 0}
    for r in rep:
        if "detail" not in r: continue
        path = paths.get(sid_of[r["content_id"]], "")
        if not os.path.exists(path): continue
        d = sorted(r["detail"], key=lambda p: p["t0"])
        t0s = [p["t0"] for p in d]
        med = np.median(np.diff(t0s)) if len(t0s) > 1 else 15
        windows = [(t0s[i], t0s[i + 1] if i + 1 < len(t0s) else t0s[i] + med)
                   for i in range(len(t0s))]
        y = decode(path)
        pred = classify(phrase_features(y, windows))
        hits = 0
        for p, lab in zip(d, pred):
            a = p["allan"]
            ok = lab == a or {lab, a} == {"INTRO", "OUTRO"}
            hits += ok; tot["hit"] += ok; tot["n"] += 1
            if a == "CHORUS": tot["ch_n"] += 1; tot["ch_hit"] += lab == "CHORUS"
            elif lab == "CHORUS": tot["ch_fp"] += 1
            if a == "UP": tot["up_n"] += 1; tot["up_hit"] += lab == "UP"
        print(f"{r['title'][:44]:46} {hits}/{len(d)}", flush=True)
    print(f"\nWAVEFORM GRAMMAR overall (lenient): {tot['hit']}/{tot['n']} "
          f"({100 * tot['hit'] // max(1, tot['n'])}%)")
    print(f"  chorus recall {tot['ch_hit']}/{tot['ch_n']}  false chorus {tot['ch_fp']}")
    print(f"  UP accuracy   {tot['up_hit']}/{tot['up_n']}")
    print("  (analyzer was: 56% lenient overall, chorus 34/35, UP 13/29)")


if __name__ == "__main__":
    sys.exit(main())
