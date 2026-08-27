"""Run allin1 on given song_ids, caching segments to allin1_cache/{sid}.json."""
import json, os, shutil, sys


def main():
    import allin1
    from pyrekordbox import Rekordbox6Database

    sids = [int(a) for a in sys.argv[1:]]
    db = Rekordbox6Database()
    paths = {}
    for c in db.get_content():
        try: paths[int(c.ID)] = c.FolderPath or ""
        except Exception: pass
    cache = os.path.expanduser("~/rekordbox-lighting/allin1_cache")
    os.makedirs(cache, exist_ok=True)
    for sid in sids:
        out = os.path.join(cache, f"{sid}.json")
        if os.path.exists(out):
            print(f"{sid}: cached", flush=True); continue
        p = paths.get(sid)
        if not p or not os.path.exists(p):
            print(f"{sid}: FILE MISSING", flush=True); continue
        print(f"{sid}: analyzing {os.path.basename(p)[:60]}", flush=True)
        r = allin1.analyze(p, device="cpu")
        json.dump([{"start": s.start, "end": s.end, "label": s.label}
                   for s in r.segments], open(out, "w"))
        for d in ("demix", "spec"):
            shutil.rmtree(d, ignore_errors=True)
    print("BATCH DONE", flush=True)


if __name__ == "__main__":
    main()
