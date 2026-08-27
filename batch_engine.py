"""Batch driver: write lighting for every rekordbox track under a folder (or all
recently-added), end to end.

  1. resolve target tracks -> (song_id, lighting content_id)
  2. allin1 structure analysis for any missing from allin1_cache  (the slow part)
  3. assign_lighting on all resolved content_ids (grid edit + scenes + bake)

Usage:
  batch_engine.py --folder Downloads        # every rb track whose file is in ~/Downloads
  batch_engine.py --since 5                  # every rb track added in the last 5 days
  batch_engine.py <content_id> [...]         # explicit list

Runs analysis under caffeinate. The assign phase needs rekordbox CLOSED; this
script quits it gracefully for that phase and relaunches after (the operator's standing
OK). Resumable: allin1_cache + engine's own skips mean re-runs continue.
"""
import os, subprocess, sys, time

ROOT = os.path.expanduser("~/rekordbox-lighting")
PY = os.path.join(ROOT, "venv", "bin", "python")
CACHE = os.path.join(ROOT, "allin1_cache")
LOG = os.path.join(ROOT, "batch_engine.log")


def log(m):
    line = f"{time.strftime('%H:%M:%S')} {m}"
    open(LOG, "a").write(line + "\n")
    print(line, flush=True)


def resolve_targets(argv):
    import sqlite3
    from pyrekordbox import Rekordbox6Database
    uc = sqlite3.connect(os.path.expanduser(
        "~/Library/Application Support/Pioneer/rekordbox6/LightingDB/user.db3"))
    sid_to_cid = {}
    for cid, sid in uc.execute("SELECT id, song_id FROM content"):
        sid_to_cid.setdefault(sid, cid)
    db = Rekordbox6Database()

    want_cids = []
    if argv and argv[0] == "--folder":
        needle = f"/{argv[1]}/"
        for c in db.get_content():
            try:
                if needle in (c.FolderPath or "") and c.AnalysisDataPath:
                    cid = sid_to_cid.get(int(c.ID))
                    if cid:
                        want_cids.append((cid, int(c.ID), c.Title or "?"))
            except Exception:
                pass
    elif argv and argv[0] == "--since":
        cutoff = time.time() - float(argv[1]) * 86400
        for c in db.get_content():
            try:
                da = c.DateCreated
                ts = da.timestamp() if hasattr(da, "timestamp") else 0
                if ts >= cutoff and c.AnalysisDataPath:
                    cid = sid_to_cid.get(int(c.ID))
                    if cid:
                        want_cids.append((cid, int(c.ID), c.Title or "?"))
            except Exception:
                pass
    else:
        titles = {int(c.ID): (c.Title or "?") for c in db.get_content()
                  if str(c.ID).isdigit()}
        for a in argv:
            cid = int(a)
            sid, = uc.execute("SELECT song_id FROM content WHERE id=?", (cid,)).fetchone()
            want_cids.append((cid, sid, titles.get(sid, "?")))
    return want_cids


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    targets = resolve_targets(argv)
    if not targets:
        sys.exit("no analyzed rekordbox tracks matched — import + analyze them first.")
    log(f"=== batch: {len(targets)} tracks ===")
    sids = [str(s) for _, s, _ in targets]
    cids = [str(c) for c, _, _ in targets]
    need = [s for s in sids if not os.path.exists(os.path.join(CACHE, f"{s}.json"))]

    # phase 1: allin1 (slow), under caffeinate, resumable
    if need:
        log(f"allin1 structure analysis: {len(need)} tracks (this is the long part)")
        env = dict(os.environ, HF_HUB_OFFLINE="1")
        r = subprocess.run(["caffeinate", "-i", PY,
                            os.path.join(ROOT, "analyze_batch.py")] + need,
                           env=env, cwd=ROOT)
        log(f"allin1 phase exit {r.returncode}")
    else:
        log("allin1 cache already complete for all targets")

    # phase 2: assign lighting — needs rekordbox closed
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        log("quitting rekordbox for the write phase")
        subprocess.run(["osascript", "-e", 'tell application "rekordbox" to quit'])
        for _ in range(30):
            if not subprocess.run(["pgrep", "-x", "rekordbox"],
                                  capture_output=True).stdout:
                break
            time.sleep(1)
    log(f"assign_lighting on {len(cids)} tracks")
    r = subprocess.run(["caffeinate", "-i", PY,
                        os.path.join(ROOT, "assign_lighting.py")] + cids, cwd=ROOT)
    log(f"assign phase exit {r.returncode}")
    subprocess.run(["open", "-a", "rekordbox"])
    log("relaunched rekordbox — done")


if __name__ == "__main__":
    main()
