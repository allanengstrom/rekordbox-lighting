"""Compute 1-10 energy scores for the 'Here we go' playlist (+ MIK anchor tracks).

Decodes audio via ffmpeg pipe (fast), computes loudness/onset/brightness features,
calibrates the combined score against Mixed In Key 'Energy N' comment anchors,
and writes energy_scores.json incrementally (resume-safe).
"""
import json, os, re, subprocess, sys, time

import numpy as np
import librosa
import imageio_ffmpeg
from pyrekordbox import Rekordbox6Database

SR = 22050
OUT = os.path.expanduser("~/rekordbox-lighting/energy_scores.json")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def decode(path):
    cmd = [FFMPEG, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
           "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.float32)


def features(y):
    if len(y) < SR * 30:
        return None
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    rms = librosa.feature.rms(S=S)[0]
    top_rms = float(np.mean(np.sort(rms)[-max(1, len(rms) // 5):]))  # sustained loudness
    onset = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S, ref=np.max), sr=SR)
    onset_mean = float(np.mean(onset))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    low = S[(freqs >= 20) & (freqs <= 150)].sum()
    low_ratio = float(low / max(S.sum(), 1e-9))  # kick/bass share
    centroid = float(np.mean(librosa.feature.spectral_centroid(S=S, sr=SR)))
    return {"top_rms": top_rms, "onset": onset_mean, "low_ratio": low_ratio,
            "centroid": centroid}


def main():
    db = Rekordbox6Database()
    tracks, anchors = {}, {}
    pl = [p for p in db.get_playlist() if p.Name == "Here we go"][0]
    wanted = {int(s.ContentID) for s in pl.Songs}
    for c in db.get_content():
        try:
            sid = int(c.ID)
        except Exception:
            continue
        m = re.search(r"[Ee]nergy\s*(\d{1,2})", str(c.Commnt or ""))
        if m:
            anchors[sid] = int(m.group(1))
        if sid in wanted or m:
            tracks[sid] = {"title": c.Title or "?", "artist": c.ArtistName or "?",
                           "path": c.FolderPath or "", "mik": anchors.get(sid)}

    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            done = {int(k): v for k, v in json.load(f).get("tracks", {}).items()}

    todo = [sid for sid in tracks if sid not in done]
    print(f"{len(tracks)} tracks total, {len(done)} already done, {len(todo)} to scan",
          flush=True)

    t0 = time.time()
    for i, sid in enumerate(todo):
        t = tracks[sid]
        try:
            if not os.path.exists(t["path"]):
                raise FileNotFoundError(t["path"])
            y = decode(t["path"])
            f = features(y)
            if f is None:
                raise ValueError("track too short / decode failed")
            done[sid] = {**t, **f}
        except Exception as e:
            done[sid] = {**t, "error": f"{type(e).__name__}: {e}"}
            print(f"  SKIP {t['title'][:50]}: {e}", flush=True)
        if (i + 1) % 10 == 0 or i == len(todo) - 1:
            _save(done)
            el = time.time() - t0
            print(f"  {i+1}/{len(todo)} scanned ({el/ (i+1):.1f}s/track)", flush=True)

    # calibrate combined score to 1-10 using MIK anchors where possible
    ok = {sid: v for sid, v in done.items() if "top_rms" in v}
    if not ok:
        print("no successful scans; aborting calibration", flush=True)
        return
    arr = {k: np.array([v[k] for v in ok.values()])
           for k in ("top_rms", "onset", "low_ratio", "centroid")}
    z = {k: (a - a.mean()) / max(a.std(), 1e-9) for k, a in arr.items()}
    raw = 0.45 * z["top_rms"] + 0.30 * z["onset"] + 0.15 * z["low_ratio"] \
        + 0.10 * z["centroid"]
    sids = list(ok.keys())
    anchor_idx = [(i, ok[sid]["mik"]) for i, sid in enumerate(sids) if ok[sid].get("mik")]
    if len(anchor_idx) >= 3:
        xs = np.array([raw[i] for i, _ in anchor_idx])
        ys = np.array([m for _, m in anchor_idx], dtype=float)
        slope, icept = np.polyfit(xs, ys, 1) if np.std(xs) > 1e-6 else (1.5, 6.0)
        scaled = raw * slope + icept
        print(f"calibrated on {len(anchor_idx)} MIK anchors (slope={slope:.2f})", flush=True)
    else:
        # map percentiles onto 2..10 so the playlist spans the familiar range
        order = raw.argsort().argsort() / max(len(raw) - 1, 1)
        scaled = 2 + order * 8
        print("too few MIK anchors; using percentile scaling", flush=True)
    for i, sid in enumerate(sids):
        done[sid]["energy10"] = round(float(np.clip(scaled[i], 1, 10)), 1)
    _save(done)
    print("DONE", flush=True)


def _save(done):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"tracks": {str(k): v for k, v in done.items()}}, f)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    sys.exit(main())
