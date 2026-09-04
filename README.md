# rekordbox-lighting

Listens to DJ tracks and designs their light shows automatically: it labels
each track's structure, decides how hard each drop should hit, picks a color
mood to match the song, and writes the finished scene assignments into
Pioneer's Rekordbox lighting database.

## Approach

Phrase labels (intro, build, drop, break, outro) come from an ensemble: an ML
music-structure model ([allin1](https://github.com/mir-aidj/all-in-one)) plus
hand-built DSP features from `librosa` (loudness, kick presence, mid-band
energy). The ML model's labels are never trusted alone. Waveform rules split
analyzer-lumped chorus runs at their quiet phrases, demote kick-less risers to
builds, and apply post-drop grammar learned from a hand-labeled library.

How hard a drop hits is scored as percentiles against a hand-annotated
reference library (`drop_stats.json`) rather than absolute thresholds: a
track's slam, its impact jump at chorus onset, and its wall-to-wall loudness
are all judged relative to real curated examples. That keeps the scoring sane
across genres and mastering styles.

Color is drawn from a distribution conditioned on the track's key mode. Minor
keys lean cool, major keys lean warm, and both were fit from the same curated
library. A temperature parameter (`VIBE_TEMP`) flattens the draw toward
variety, and a `SPICE` dial injects controlled randomness.

The write path is the reverse-engineered part. Rekordbox's lighting database
(`LightingDB`, SQLite) is undocumented, so this project writes scene
assignments into it directly via `pyrekordbox`, and defensively: every write
appends compensating undo SQL to `rollback_engine.sql`, and the tool refuses
to run while Rekordbox is open.

## Running it

macOS only, since the write target is
`~/Library/Application Support/Pioneer/rekordbox6/LightingDB/`.

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

Note that `allin1` pulls in PyTorch, so the install is large.

Typical flow: analyze tracks (`analyze_batch.py`), then assign lighting for
one or more tracks by Rekordbox content id (`assign_lighting.py <content_id>
...`), or re-run the whole library with `reassign_scenes.py`.
`preview_assign.py` shows what would be written without writing it.
`fix_derby.py` is a post-pass for a hardware quirk: a derby unit whose LEDs
ignore the master dimmer, so dark scenes still lit it until their color blocks
are clipped to the brightness envelope.

## Safety

This tool writes into Rekordbox's own database. It generates rollback SQL for
every write and refuses to run while Rekordbox is open. Back up your Rekordbox
library before using it anyway.

## Notes

Built with substantial AI assistance (Claude). I designed the approach — the
ensemble labeling, the percentile calibration, and the write-safety model —
and directed the implementation.
