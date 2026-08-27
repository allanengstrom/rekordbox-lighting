"""Build virtual_rig.html (artifact, silent) and virtual_rig_local.html
(file:// audio) from the template + virtual_rig_data.json, then syntax-check."""
import json, os, re, subprocess, sys, urllib.parse

root = os.path.expanduser("~/rekordbox-lighting/")
tpl = open(root + "virtual_rig_template.html").read()
data = json.load(open(root + "virtual_rig_data.json"))


def build(local):
    dd = json.loads(json.dumps(data))
    for tr in dd["tracks"]:
        p = tr.pop("audio_path", "")
        if local and p and os.path.exists(p):
            tr["audio"] = "file://" + urllib.parse.quote(p)
    return tpl.replace("__RIG_DATA__", json.dumps(dd).replace("</", "<\\/"))


fail = False
for name, local in (("virtual_rig.html", False), ("virtual_rig_local.html", True)):
    html = build(local)
    open(root + name, "w").write(html)
    for i, s in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        p = f"/tmp/rigcheck{i}.js"
        open(p, "w").write(s)
        r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        if r.returncode:
            print(name, "FAIL:", r.stderr[:300]); fail = True; break
    else:
        print(name, f"OK ({len(html)//1024} KB)")
sys.exit(1 if fail else 0)
