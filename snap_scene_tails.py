"""Kill the fade-out tails baked into scene brightness curves.

Pioneer's preset scenes end many brightness curves with a ramp to zero over the
final beat or so of the 32-beat loop — that's the unwanted soft on->off fade
at scene changes. This compresses any final descending ramp that ENDS at the
scene boundary and STARTS within the last 2 beats into a hard step (0.02 beats).
Longer fades are treated as intentional design and left alone.

Backs up macro.db3 first. Refuses to run while rekordbox is open.
"""
import os, re, shutil, sqlite3, subprocess, sys, time

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
BACKUPS = os.path.expanduser("~/rekordbox-lighting/backups")
PT = re.compile(r'<Point x="([0-9.eE+-]+)" y="([0-9.eE+-]+)" type="(\d)"/>')


def snap(data):
    """Return (new_data, snapped) — compress the final boundary fade if present."""
    pts = list(PT.finditer(data))
    if len(pts) < 3:
        return data, False
    xs = [float(m.group(1)) for m in pts]
    ys = [float(m.group(2)) for m in pts]
    x_end, y_end = xs[-1], ys[-1]
    # s = start of the maximal non-increasing suffix (the fade ramp)
    s = len(pts) - 1
    while s > 0 and ys[s - 1] >= ys[s] - 1e-9:
        s -= 1
    if not (xs[s] >= x_end - 2.0 and xs[s] < x_end - 0.1 and ys[s] > y_end + 0.1):
        return data, False
    # move every descent point short of the boundary to just before it
    targets = [i for i in range(s, len(pts) - 1) if xs[i] < x_end - 0.05]
    if not targets:
        return data, False
    out = data
    for i in reversed(targets):
        m = pts[i]
        new_pt = f'<Point x="{x_end - 0.02:.6f}" y="{m.group(2)}" type="{m.group(3)}"/>'
        out = out[:m.start()] + new_pt + out[m.end():]
    return out, True


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it first.")
    src = LDB + "macro.db3"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bk = os.path.join(BACKUPS, f"macro-{stamp}.db3")
    shutil.copy2(src, bk)
    print(f"backup: {bk}")

    mc = sqlite3.connect(src)
    names = dict(mc.execute("SELECT id, name FROM macro"))
    changed, per_scene = 0, {}
    for rid, mid, slot, data in mc.execute(
            "SELECT rowid, macro_id, macro_fixture_id, data FROM macro_data"):
        new, hit = snap(data)
        if hit:
            mc.execute("UPDATE macro_data SET data=? WHERE rowid=?", (new, rid))
            changed += 1
            per_scene.setdefault(names.get(mid, mid), []).append(slot)
    mc.commit()
    print(f"\nsnapped {changed} fade tails across {len(per_scene)} scenes:")
    for nm in sorted(per_scene):
        print(f"  {nm:26} slots {sorted(per_scene[nm])}")


if __name__ == "__main__":
    main()
