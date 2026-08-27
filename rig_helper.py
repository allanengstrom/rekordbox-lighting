"""Localhost bridge between the virtual rig's color picker and the engine.

Run: ./venv/bin/python rig_helper.py   (listens on 127.0.0.1:8765)

POST /choose {"content_id": N, "vibe": "HOT"|"CLUB1"|...} queues the choice;
a worker applies it with assign_lighting --vibe (which needs rekordbox closed),
then re-exports and rebuilds both rig pages. GET /status reports state.
"""
import json, os, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.expanduser("~/rekordbox-lighting")
PY = os.path.join(ROOT, "venv", "bin", "python")
PENDING = os.path.join(ROOT, "pending_choices.json")
ROSTER = [1145, 1162, 1164, 1165, 976]
VIBES = {"COOL", "NATURAL", "HOT", "SUBTLE", "WARM", "VIVID", "CLUB1", "CLUB2"}
lock = threading.Lock()
log = []


def rb_running():
    return bool(subprocess.run(["pgrep", "-x", "rekordbox"],
                               capture_output=True).stdout)


def load_pending():
    return json.load(open(PENDING)) if os.path.exists(PENDING) else {}


def save_pending(p):
    json.dump(p, open(PENDING, "w"))


def note(msg):
    log.append(f"{time.strftime('%H:%M:%S')} {msg}")
    del log[:-20]
    print(log[-1], flush=True)


def worker():
    while True:
        with lock:
            pending = load_pending()
        if pending and not rb_running():
            with lock:
                pending = load_pending()
                save_pending({})
            for cid, vibe in pending.items():
                note(f"applying {vibe} to content {cid}")
                r = subprocess.run([PY, os.path.join(ROOT, "assign_lighting.py"),
                                    f"--vibe={vibe}", str(cid)],
                                   capture_output=True, text=True, cwd=ROOT)
                note(f"content {cid}: " +
                     ("done" if r.returncode == 0 else "FAILED " + (r.stdout + r.stderr)[-150:]))
            subprocess.run([PY, os.path.join(ROOT, "export_virtual_rig.py")]
                           + [str(c) for c in ROSTER if c not in (978, 2)],
                           capture_output=True, cwd=ROOT)
            subprocess.run(["python3", os.path.join(ROOT, "build_rig.py")],
                           capture_output=True, cwd=ROOT)
            note("rig pages rebuilt — reload the browser tab")
        time.sleep(3)


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {})

    def do_GET(self):
        if self.path == "/status":
            self._send(200, {"rekordbox_open": rb_running(),
                             "pending": load_pending(), "log": log[-6:]})
        else:
            self._send(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path != "/choose":
            return self._send(404, {"error": "unknown path"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n))
            cid, vibe = int(req["content_id"]), str(req["vibe"]).upper()
            assert vibe in VIBES
        except Exception:
            return self._send(400, {"error": "bad request"})
        with lock:
            p = load_pending()
            p[str(cid)] = vibe
            save_pending(p)
        note(f"queued {vibe} for content {cid}")
        self._send(200, {"status": "queued", "rekordbox_open": rb_running()})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    note("rig helper listening on 127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
