# rekordbox-lighting

Listens to DJ tracks and designs their light shows automatically: it labels each track's structure, decides how hard each drop should hit, picks a color mood to match the song, and writes the finished scene assignments into Pioneer's Rekordbox lighting database.

## Approach

**Ensemble structure labeling.** Phrase labels (intro / build / drop / break / outro) come from an ensemble of an ML music-structure model ([allin1](https://github.com/mir-aidj/all-in-one)) and hand-built DSP features extracted with `librosa` (loudness, kick presence, mid-band energy). The ML model's labels are never trusted alone: waveform rules split analyzer-lumped chorus runs at their quiet phrases, demote kick-less risers to builds, and apply post-drop grammar learned from a hand-labeled library.

**Percentile calibration.** A track's "drop profile" — absolute slam, impact jump at chorus onset, wall-to-wall loudness — is scored as percentiles against a hand-annotated reference library (`drop_stats.json`), not against absolute thresholds. A drop is "big" relative to real curated examples, so the system degrades gracefully across genres and mastering styles.

**Key-conditioned aesthetic model.** Color vibe is drawn from a distribution conditioned on the track's key mode (minor keys lean cool, major keys lean warm), fit from a curated reference library. A temperature parameter (`VIBE_TEMP`) flattens the draw toward variety, and a `SPICE` dial injects controlled randomness.

**Reverse-engineered write path.** Rekordbox's lighting database (`LightingDB`, SQLite) is undocumented. This project writes scene assignments into it directly via `pyrekordbox` — and defensively: every write appends compensating undo SQL to `rollback_engine.sql`, and the tool refuses to run while Rekordbox is open.

## Running it

macOS only — the write target is `~/Library/Application Support/Pioneer/rekordbox6/LightingDB/`. Requires Python 3.9+, `allin1`, `librosa`, `numpy`, and `pyrekordbox`.

Typical flow: analyze tracks (`analyze_batch.py`), then assign lighting for one or more tracks by Rekordbox content id (`assign_lighting.py <content_id> ...`), or re-run the whole library with `reassign_scenes.py`. `preview_assign.py` shows what would be written without writing it. `fix_derby.py` is a post-pass for a hardware quirk: a derby unit whose LEDs ignore the master dimmer, so dark scenes still lit it until their color blocks are clipped to the brightness envelope.

## Safety

This tool writes into Rekordbox's own database. It generates rollback SQL for every write and refuses to run while Rekordbox is open — but back up your Rekordbox library before using it.

## Notes

Built with substantial AI assistance (Claude). I designed the approach — the ensemble labeling, the percentile calibration, and the write-safety model — and directed the implementation.
