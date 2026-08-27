"""Final laser de-blend pass — run AFTER the batch re-run and after the manual
edits. On the Boston-30 profile the laser's channels are lookup tables, but rekordbox
smooths them; any sloped brightness / swept movement makes the pattern flicker.
This hard-steps the laser in every baked track row (and the shared scenes) so values
HOLD and hard-jump. Idempotent, backs up user.db3 + macro.db3, refuses to run with
rekordbox open.

Usage: fix_lasers.py
"""
import os, re, shutil, sqlite3, subprocess, sys, time
import xml.etree.ElementTree as ET

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
BK = os.path.expanduser("~/rekordbox-lighting/backups")


def hardstep_and_freeze(data):
    """Freeze movement sweep (w/h=0) and hard-step the brightness curve."""
    changed = False
    # freeze any laser MovementBlock sweep
    def zero(m):
        nonlocal changed
        changed = True
        return m.group(1) + "0" + m.group(2)
    data = re.sub(r'(<MovementBlock\b[^>]*?\bwidth=")[^"]*(")', zero, data)
    data = re.sub(r'(<MovementBlock\b[^>]*?\bheight=")[^"]*(")', zero, data)
    # hard-step brightness: insert a hold point just before every value change
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data, changed
    b = root.find("Brightness")
    pb = b.find("PointBlock") if b is not None else None
    if pb is not None:
        pts = [(float(p.get("x")), float(p.get("y")), p.get("type")) for p in pb.findall("Point")]
        if len(pts) >= 2:
            new = []
            for i, (x, y, t) in enumerate(pts):
                if i > 0:
                    px, py, _ = pts[i - 1]
                    if abs(y - py) > 0.005 and (x - px) > 0.05:   # sloped -> hold then jump
                        new.append((x - 0.02, py)); changed = True
                new.append((x, y))
            if changed:
                for p in list(pb.findall("Point")):
                    pb.remove(p)
                for i, (x, yy) in enumerate(new):
                    tt = "1" if i == 0 else ("3" if i == len(new) - 1 else "2")
                    ET.SubElement(pb, "Point", {"x": repr(x), "y": repr(yy), "type": tt})
                data = ET.tostring(root, encoding="unicode")
    return data, changed


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it first.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(LDB + "user.db3", os.path.join(BK, f"user-fixlaser-{stamp}.db3"))
    shutil.copy2(LDB + "macro.db3", os.path.join(BK, f"macro-fixlaser-{stamp}.db3"))
    uc = sqlite3.connect(LDB + "user.db3"); mc = sqlite3.connect(LDB + "macro.db3")
    lfid = uc.execute("SELECT id FROM fixture WHERE macro_fixture_id=19").fetchone()[0]
    n = 0
    for rid, data in uc.execute("SELECT rowid, data FROM lighting_data WHERE fixture_id=?",
                                (lfid,)).fetchall():
        nd, ch = hardstep_and_freeze(data)
        if ch:
            uc.execute("UPDATE lighting_data SET data=? WHERE rowid=?", (nd, rid)); n += 1
    uc.commit()
    m = 0
    for rid, data in mc.execute("SELECT rowid, data FROM macro_data WHERE macro_fixture_id=19").fetchall():
        nd, ch = hardstep_and_freeze(data)
        if ch:
            mc.execute("UPDATE macro_data SET data=? WHERE rowid=?", (nd, rid)); m += 1
    mc.commit()
    print(f"de-blended laser: {n} baked track rows + {m} scene rows. backups @ {stamp}")


if __name__ == "__main__":
    main()
