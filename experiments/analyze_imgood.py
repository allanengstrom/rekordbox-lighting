"""Run allin1 on I'm Good (Blue) and print segments next to rekordbox's phrases."""
import json, os

PATH = ("/Users/allanengstrom/Desktop/Music/Tracks/"
        "David Guetta & Bebe Rexha - I'm Good (Blue) (Dirty).mp3")
OUT = os.path.expanduser("~/rekordbox-lighting/allin1_imgood.json")

def main():
    import allin1

    result = allin1.analyze(PATH, device="cpu")
    segs = [{"start": round(s.start, 1), "end": round(s.end, 1), "label": s.label}
            for s in result.segments]
    with open(OUT, "w") as f:
        json.dump({"bpm": result.bpm, "segments": segs,
                   "n_beats": len(result.beats),
                   "n_downbeats": len(result.downbeats)}, f, indent=1)

    print(f"allin1: bpm={result.bpm}, {len(segs)} segments")
    for s in segs:
        print(f"  {s['start']:6.1f} - {s['end']:6.1f}  {s['label']}")

    # rekordbox's reading, for the side-by-side
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.anlz import AnlzFile

    db = Rekordbox6Database()
    c = [x for x in db.get_content() if x.Title == "I'm Good (Blue) (Dirty)"][0]
    root = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
    base = os.path.dirname(root + c.AnalysisDataPath)
    ext = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.EXT"))
    dat = AnlzFile.parse_file(os.path.join(base, "ANLZ0000.DAT"))
    pssi = ext.get_tag("PSSI").content
    beats = dat.get_tag("PQTZ").content.entries
    times = {i + 1: b.time / 1000.0 for i, b in enumerate(beats)}
    KINDS = {1: "intro", 2: "up", 3: "down", 5: "chorus", 6: "outro"}
    print("\nrekordbox phrases:")
    for e in pssi.entries:
        print(f"  {times.get(e.beat, -1):6.1f}  {KINDS.get(int(str(e.kind).split()[0]) if str(e.kind).split() else 0, str(e.kind))}")
    print("\nDONE")


if __name__ == "__main__":
    main()
