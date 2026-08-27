#!/bin/bash
cd ~/rekordbox-lighting
SID=$(./venv/bin/python -c "import sqlite3,os;print(sqlite3.connect(os.path.expanduser('~/Library/Application Support/Pioneer/rekordbox6/LightingDB/user.db3')).execute('SELECT song_id FROM content WHERE id=1260').fetchone()[0])" 2>/dev/null)
echo "PASS: analyzing HAVEN sid $SID"
HF_HUB_OFFLINE=1 ./venv/bin/python analyze_batch.py $SID >> pass.log 2>&1
echo "PASS: promoting HAVEN"
./venv/bin/python assign_lighting.py 1260 >> pass.log 2>&1
echo "PASS: reassigning all with new rules"
./venv/bin/python reassign_scenes.py --folder Downloads >> pass.log 2>&1
open -a rekordbox
echo "PASS: DONE"
