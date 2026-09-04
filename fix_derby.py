"""Derby de-ghost pass — the derby (slot 17) is patched as "LED Derby Light
Effect (Standard)", whose profile has a dedicated Dimmer channel, but the
physical unit's LEDs are driven directly by the colour channels (no working
master dimmer). rekordbox emits Colour/Rotate data raw and trusts the dimmer
to gate it, so any scene that paints the derby a colour lights the real unit
for the whole loop — even scenes whose Brightness curve is fully dark
(CHORUS CLUB1: white; HIGH CHORUS1 WARM: red; both brightness-empty).

Fix: in every PRESET derby scene, clip the Colour/Strobe/Rotate/Position
blocks to the time spans where the Brightness envelope is actually lit, and
strip them entirely where the scene is dark. The unit then follows the
scene's real intent. Custom (preset=0) scenes are left untouched — they were
authored watching the real fixture. Idempotent. Backs up macro.db3.
Run BEFORE reassign_scenes so re-bakes inherit the clean scenes.
"""
import os, shutil, sqlite3, subprocess, sys, time
import xml.etree.ElementTree as ET

LDB = os.path.expanduser("~/Library/Application Support/Pioneer/rekordbox6/LightingDB/")
BACKUPS = os.path.expanduser("~/rekordbox-lighting/backups")
DERBY_SLOT = 17
THRESH = 0.05
EPS = 1e-6
GATED = ("Colour", "Strobe", "Position", "Rotate")
# per block tag: (left, right) numeric attrs to interpolate at clipped edges
LERP_ATTRS = {"StrobeBlock": ("strobeleft", "stroberight"),
              "RotateBlock": ("rotateleft", "rotateright")}


def lit_intervals(root):
    """Spans (merged, sorted) where the Brightness envelope exceeds THRESH."""
    spans = []
    for pb in root.iter("PointBlock"):
        pts = sorted((float(p.get("x")), float(p.get("y"))) for p in pb.iter("Point")
                     if p.get("x") and p.get("y"))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x1 - x0 <= 0:
                continue
            lo, hi = (y0, y1) if y0 <= y1 else (y1, y0)
            if hi <= THRESH:
                continue
            if lo > THRESH:                       # fully lit segment
                spans.append((x0, x1))
                continue
            xc = x0 + (x1 - x0) * (THRESH - y0) / (y1 - y0)   # crossing point
            spans.append((x0, xc) if y0 > THRESH else (xc, x1))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def clip_element(elem, spans):
    """Clip every child block of elem to the lit spans. Returns True if changed."""
    changed = False
    kept = []
    for blk in list(elem):
        xl, xr = blk.get("xleft"), blk.get("xright")
        if xl is None or xr is None:               # spanless block (e.g. MovementBlock)
            if spans:
                kept.append(blk)                   # scene has lit time — keep as-is
            else:
                changed = True                     # fully dark scene — drop
            continue
        xl, xr = float(xl), float(xr)
        pieces = [(max(xl, a), min(xr, b)) for a, b in spans if min(xr, b) - max(xl, a) > EPS]
        if len(pieces) == 1 and abs(pieces[0][0] - xl) < EPS and abs(pieces[0][1] - xr) < EPS:
            kept.append(blk)                       # already inside lit time
            continue
        changed = True
        la, ra = LERP_ATTRS.get(blk.tag, (None, None))
        for a, b in pieces:
            c = ET.Element(blk.tag, dict(blk.attrib))
            c.set("xleft", repr(a)); c.set("xright", repr(b))
            if la and blk.get(la) and blk.get(ra) and xr > xl:
                v0, v1 = float(blk.get(la)), float(blk.get(ra))
                c.set(la, repr(v0 + (a - xl) / (xr - xl) * (v1 - v0)))
                c.set(ra, repr(v0 + (b - xl) / (xr - xl) * (v1 - v0)))
            c.extend(list(blk))
            kept.append(c)
    if changed:
        for blk in list(elem):
            elem.remove(blk)
        elem.extend(kept)
    return changed


def main():
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout:
        sys.exit("rekordbox is running — close it before writing lighting data.")
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(LDB + "macro.db3", f"{BACKUPS}/macro-derby-{stamp}.db3")
    print(f"[backup] backups/macro-derby-{stamp}.db3")

    mc = sqlite3.connect(LDB + "macro.db3")
    rows = mc.execute(
        "SELECT md.id, md.data, m.name, m.preset FROM macro_data md "
        "JOIN macro m ON m.id = md.macro_id WHERE md.macro_fixture_id = ?",
        (DERBY_SLOT,)).fetchall()
    edited = skipped_custom = dark = 0
    for rid, data, name, preset in rows:
        if preset == 0:
            skipped_custom += 1
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            print(f"[skip] {name}: unparseable XML")
            continue
        spans = lit_intervals(root)
        if not spans:
            dark += 1
        changed = False
        for tag in GATED:
            el = root.find(tag)
            if el is not None:
                changed |= clip_element(el, spans)
        if not changed:
            continue
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n\n' + ET.tostring(root, encoding="unicode")
        mc.execute("UPDATE macro_data SET data=? WHERE id=?", (xml, rid))
        edited += 1
        print(f"[gate] {name}" + ("  (fully dark -> stripped)" if not spans else ""))
    mc.commit()
    mc.close()
    print(f"[done] {edited} scene rows gated ({dark} fully dark), "
          f"{skipped_custom} custom rows untouched, {len(rows)} total derby rows")


if __name__ == "__main__":
    main()
