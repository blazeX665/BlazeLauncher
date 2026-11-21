# -*- coding: utf-8 -*-
"""BlazeLauncher - a small Minecraft launcher. Pure Python stdlib, works on Windows 7-11 (Python 3.8+)."""
import os, sys, re, json, time, uuid, shutil, hashlib, zipfile, queue, glob, platform, threading
import base64, subprocess, traceback, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

APP, VER = "BlazeLauncher", "1.3.0"
BASE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), ".blazelauncher")
MC = os.path.join(BASE, "minecraft")
GAME = os.path.join(BASE, "game")
RT = os.path.join(BASE, "runtime")
CFG = os.path.join(BASE, "config.json")
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
NOWIN = 0x08000000 if os.name == "nt" else 0
IS64 = platform.machine().endswith("64") or "PROCESSOR_ARCHITEW6432" in os.environ
ARCH = "x64" if IS64 else "x86"
BG, PANEL, CARD, EDGE = "#0f0c0a", "#17120f", "#201a15", "#2e261f"
ORANGE, ORANGE2, TEXT, MUTED = "#ff7a1a", "#ff9a4a", "#f4ede5", "#9b9086"

DEFAULT = {"mode": "offline", "name": "Player", "ram": 2048, "java": "", "snapshots": False,
           "fabric": False, "version": "", "client_id": "", "ms": {}, "friends": [], "shortcut_done": False}


# ---------------------------------------------------------------- config / http
def load_cfg():
    c = dict(DEFAULT)
    try:
        with open(CFG, encoding="utf-8") as f:
            c.update(json.load(f))
    except Exception:
        pass
    return c


def save_cfg(c):
    os.makedirs(BASE, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)


def req(url, data=None, headers=None, timeout=30):
    h = {"User-Agent": "BlazeLauncher/" + VER}
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout)


def get_json(url, headers=None):
    with req(url, headers=headers) as r:
        return json.loads(r.read().decode("utf-8"))


def json_post(url, obj):
    with req(url, json.dumps(obj).encode(), {"Content-Type": "application/json", "Accept": "application/json"}) as r:
        return json.loads(r.read().decode("utf-8"))


def form_post(url, obj):
    with req(url, urllib.parse.urlencode(obj).encode(), {"Content-Type": "application/x-www-form-urlencoded"}) as r:
        return json.loads(r.read().decode("utf-8"))


def sha1_of(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def download(url, path, sha1=None):
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    for i in range(3):
        try:
            with req(url, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            if sha1 and sha1_of(tmp) != sha1:
                raise IOError("SHA1 mismatch: " + url)
            os.replace(tmp, path)
            return
        except Exception:
            if i == 2:
                raise
            time.sleep(1)


def fetch_all(tasks, status, label):
    todo = [t for t in tasks if not (os.path.isfile(t[1]) and os.path.getsize(t[1]) > 0)]
    n, done = len(todo), 0
    if not n:
        return
    with ThreadPoolExecutor(12) as ex:
        for f in as_completed([ex.submit(download, *t) for t in todo]):
            f.result()
            done += 1
            if done % 10 == 0 or done == n:
                status("%s %d/%d" % (label, done, n), done / n)


# ---------------------------------------------------------------- minecraft metadata
def vpath(vid):
    return os.path.join(MC, "versions", vid, vid + ".json")


def rule_match(r):
    if "features" in r:
        return False
    o = r.get("os")
    if o:
        if "name" in o and o["name"] != "windows":
            return False
        if "arch" in o and o["arch"] != ARCH:
            return False
        if "version" in o and not re.match(o["version"], platform.version()):
            return False
    return True


def rules_ok(rules):
    if not rules:
        return True
    res = False
    for r in rules:
        if rule_match(r):
            res = r.get("action") == "allow"
    return res


def maven_path(name):
    p = name.split("@")[0].split(":")
    cl = ("-" + p[3]) if len(p) > 3 else ""
    return "%s/%s/%s/%s-%s%s.jar" % (p[0].replace(".", "/"), p[1], p[2], p[1], p[2], cl)


def load_version(vid):
    with open(vpath(vid), encoding="utf-8") as f:
        v = json.load(f)
    parent = v.get("inheritsFrom")
    if not parent:
        v.setdefault("jar", vid)
        return v
    base = load_version(parent)
    m = dict(base)
    for k, val in v.items():
        if k not in ("libraries", "arguments", "inheritsFrom"):
            m[k] = val
    m["libraries"] = v.get("libraries", []) + base.get("libraries", [])
    a = {"jvm": [], "game": []}
    for src in (base.get("arguments", {}), v.get("arguments", {})):
        for k in a:
            a[k] += src.get(k, [])
    if a["jvm"] or a["game"]:
        m["arguments"] = a
    m["jar"] = base.get("jar", parent)
    return m


def install_fabric(mc, status):
    status("Fabric...", None)
    lst = get_json("https://meta.fabricmc.net/v2/versions/loader/" + mc)
    if not lst:
        raise RuntimeError("Fabric does not support Minecraft " + mc)
    loader = next((x["loader"]["version"] for x in lst if x["loader"].get("stable")), lst[0]["loader"]["version"])
    vid = "fabric-loader-%s-%s" % (loader, mc)
    p = vpath(vid)
    if not os.path.isfile(p):
        j = get_json("https://meta.fabricmc.net/v2/versions/loader/%s/%s/profile/json" % (mc, loader))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(j, f)
    return vid


def prepare(mcver, fabric, manifest, status):
    """Download everything needed. Returns (version_dict, version_id, classpath_list, natives_dir, assets_root)."""
    p = vpath(mcver)
    if manifest:
        e = next((x for x in manifest["versions"] if x["id"] == mcver), None)
        if e:
            download(e["url"], p, e.get("sha1"))
    if not os.path.isfile(p):
        raise RuntimeError("Version %s is not available (no internet?)" % mcver)
    v = load_version(mcver)
    cl = v["downloads"]["client"]
    jar = os.path.join(MC, "versions", mcver, mcver + ".jar")
    status("Minecraft " + mcver, None)
    download(cl["url"], jar, cl.get("sha1"))
    vid = install_fabric(mcver, status) if fabric else mcver
    v = load_version(vid)

    libs_dir = os.path.join(MC, "libraries")
    tasks, cp, seen, natives = [], [], set(), []
    for lib in v["libraries"]:
        if not rules_ok(lib.get("rules")):
            continue
        name, dl = lib["name"], lib.get("downloads", {})
        nat = lib.get("natives", {}).get("windows")
        if nat:
            nat = nat.replace("${arch}", "64" if IS64 else "32")
            a = dl.get("classifiers", {}).get(nat)
            if a:
                tp = os.path.join(libs_dir, *a["path"].split("/"))
                tasks.append((a["url"], tp, a.get("sha1")))
                natives.append((tp, lib.get("extract", {}).get("exclude", [])))
        art = dl.get("artifact")
        if art:
            path, url, sha = art["path"], art["url"], art.get("sha1")
        elif not dl and not nat:
            path = maven_path(name)
            url = lib.get("url", "https://libraries.minecraft.net/")
            url += "" if url.endswith("/") else "/"
            url, sha = url + path, None
        else:
            continue
        parts = name.split(":")
        key = ":".join(parts[:2] + parts[3:4])
        if key in seen:
            continue
        seen.add(key)
        tp = os.path.join(libs_dir, *path.split("/"))
        tasks.append((url, tp, sha))
        cp.append(tp)
    fetch_all(tasks, status, "Libraries")
    jarid = v.get("jar", vid)
    cp.append(os.path.join(MC, "versions", jarid, jarid + ".jar"))

    natdir = os.path.join(MC, "versions", vid, "natives")
    shutil.rmtree(natdir, ignore_errors=True)
    os.makedirs(natdir, exist_ok=True)
    for tp, excl in natives:
        with zipfile.ZipFile(tp) as z:
            for n in z.namelist():
                if n.endswith("/") or n.startswith("META-INF") or any(n.startswith(x) for x in excl):
                    continue
                z.extract(n, natdir)

    ai = v["assetIndex"]
    ip = os.path.join(MC, "assets", "indexes", ai["id"] + ".json")
    download(ai["url"], ip, ai.get("sha1"))
    with open(ip, encoding="utf-8") as f:
        idx = json.load(f)
    atasks = []
    for name, o in idx["objects"].items():
        h = o["hash"]
        atasks.append(("https://resources.download.minecraft.net/%s/%s" % (h[:2], h),
                       os.path.join(MC, "assets", "objects", h[:2], h), h))
    fetch_all(atasks, status, "Assets")
    assets_root = os.path.join(MC, "assets")
    if idx.get("virtual") or idx.get("map_to_resources"):
        assets_root = os.path.join(MC, "assets", "virtual", "legacy")
        for name, o in idx["objects"].items():
            dst = os.path.join(assets_root, *name.split("/"))
            if not os.path.isfile(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(MC, "assets", "objects", o["hash"][:2], o["hash"]), dst)
    return v, vid, cp, natdir, assets_root


def build_args(v, vars_):
    def expand(items):
        res = []
        for it in items:
            if isinstance(it, str):
                res.append(it)
            elif rules_ok(it.get("rules")):
                val = it["value"]
                res += [val] if isinstance(val, str) else val
        return res

    if "arguments" in v:
        jvm, game = expand(v["arguments"].get("jvm", [])), expand(v["arguments"].get("game", []))
    else:
        jvm = ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"]
        game = v.get("minecraftArguments", "").split()
    sub = lambda s: re.sub(r"\$\{(\w+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), s)
    return [sub(a) for a in jvm], [sub(a) for a in game]


# ---------------------------------------------------------------- java
def java_major(exe):
    try:
        p = subprocess.run([exe, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=NOWIN, timeout=20)
        m = re.search(r'version "(\d+)(?:\.(\d+))?', p.stdout.decode("utf-8", "ignore"))
        if not m:
            return 0
        a = int(m.group(1))
        return int(m.group(2) or 0) if a == 1 else a
    except Exception:
        return 0


def java_ok(found, need):
    return found == need or (need >= 17 and found >= need)


def find_exe(folder):
    for root, _, files in os.walk(folder):
        if "java.exe" in files:
            return os.path.join(root, "java.exe")
    return None


def find_java(need, cfg, status):
    if cfg.get("java") and os.path.isfile(cfg["java"]):
        return cfg["java"]
    own = find_exe(os.path.join(RT, "jre%d" % need)) if os.path.isdir(os.path.join(RT, "jre%d" % need)) else None
    if own:
        return own
    cands = []
    if os.environ.get("JAVA_HOME"):
        cands.append(os.path.join(os.environ["JAVA_HOME"], "bin", "java.exe"))
    cands.append(shutil.which("java") or "")
    mcrt = os.path.join(os.environ.get("APPDATA", ""), ".minecraft", "runtime")
    for root, _, files in os.walk(mcrt):
        if "java.exe" in files:
            cands.append(os.path.join(root, "java.exe"))
    for pf in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        for sub in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "BellSoft"):
            cands += glob.glob(os.path.join(pf, sub, "*", "bin", "java.exe"))
    for c in cands:
        if c and os.path.isfile(c) and java_ok(java_major(c), need):
            return c
    status("Downloading Java %d..." % need, None)
    url = "https://api.adoptium.net/v3/binary/latest/%d/ga/windows/%s/jre/hotspot/normal/eclipse" % (
        need, "x64" if IS64 else "x32")
    dest = os.path.join(RT, "jre%d" % need)
    zp = dest + ".zip"
    download(url, zp)
    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(zp) as z:
        z.extractall(dest)
    os.remove(zp)
    exe = find_exe(dest)
    if not exe:
        raise RuntimeError("Could not install Java %d. Install it manually and set the path in Settings." % need)
    return exe


# ---------------------------------------------------------------- microsoft login (device code flow)
def ms_login(cid, on_code, refresh=None):
    T = "https://login.microsoftonline.com/consumers/oauth2/v2.0/"
    scope = "XboxLive.signin offline_access"
    tok = None
    if refresh:
        try:
            tok = form_post(T + "token", {"client_id": cid, "grant_type": "refresh_token",
                                          "refresh_token": refresh, "scope": scope})
        except Exception:
            tok = None
    if not tok:
        d = form_post(T + "devicecode", {"client_id": cid, "scope": scope})
        on_code(d["user_code"], d["verification_uri"])
        end = time.time() + d.get("expires_in", 900)
        while time.time() < end and not tok:
            time.sleep(d.get("interval", 5))
            try:
                tok = form_post(T + "token", {"client_id": cid,
                                              "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                              "device_code": d["device_code"]})
            except urllib.error.HTTPError as e:
                err = json.loads(e.read().decode("utf-8", "ignore") or "{}").get("error")
                if err not in ("authorization_pending", "slow_down"):
                    raise RuntimeError(err or "login failed")
        if not tok:
            raise RuntimeError("Login timed out")
    xbl = json_post("https://user.auth.xboxlive.com/user/authenticate", {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": "d=" + tok["access_token"]},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
    xsts = json_post("https://xsts.auth.xboxlive.com/xsts/authorize", {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
        "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
    uhs = xsts["DisplayClaims"]["xui"][0]["uhs"]
    mc = json_post("https://api.minecraftservices.com/authentication/login_with_xbox",
                   {"identityToken": "XBL3.0 x=%s;%s" % (uhs, xsts["Token"])})
    prof = get_json("https://api.minecraftservices.com/minecraft/profile",
                    {"Authorization": "Bearer " + mc["access_token"]})
    return {"name": prof["name"], "uuid": prof["id"], "token": mc["access_token"],
            "refresh": tok.get("refresh_token", refresh), "exp": time.time() + mc.get("expires_in", 86000) - 300}


def offline_uuid(name):
    return str(uuid.UUID(bytes=hashlib.md5(("OfflinePlayer:" + name).encode()).digest(), version=3)).replace("-", "")


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def install_shortcuts():
    """When running as the .exe: copy it to %APPDATA%\\.blazelauncher\\app and create Desktop + Start menu shortcuts."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False, "Shortcuts can only be created from BlazeLauncher.exe"
    dest_dir = os.path.join(BASE, "app")
    dest = os.path.join(dest_dir, "BlazeLauncher.exe")
    cur = os.path.abspath(sys.executable)
    if os.path.normcase(cur) != os.path.normcase(dest):
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copyfile(cur, dest)
        except Exception:
            dest, dest_dir = cur, os.path.dirname(cur)
    q = lambda t: t.replace("'", "''")
    ps = ("$w = New-Object -ComObject WScript.Shell;"
          "foreach ($f in 'Desktop','Programs') {"
          "$d = [Environment]::GetFolderPath($f);"
          "$s = $w.CreateShortcut((Join-Path $d 'BlazeLauncher.lnk'));"
          "$s.TargetPath = '%s'; $s.WorkingDirectory = '%s'; $s.IconLocation = '%s,0';"
          "$s.Description = 'BlazeLauncher'; $s.Save() }") % (q(dest), q(dest_dir), q(dest))
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NOWIN, timeout=40)
        if p.returncode != 0:
            return False, p.stdout.decode("utf-8", "replace").strip() or "PowerShell failed"
        return True, "Shortcuts created on the Desktop and in the Start menu"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- mods / news helpers
MODS = os.path.join(GAME, "mods")


def list_mods():
    os.makedirs(MODS, exist_ok=True)
    out = []
    for n in sorted(os.listdir(MODS), key=str.lower):
        low = n.lower()
        if low.endswith(".jar") or low.endswith(".jar.disabled"):
            out.append((n, low.endswith(".disabled"), os.path.getsize(os.path.join(MODS, n))))
    return out


def toggle_mod(name):
    src = os.path.join(MODS, name)
    dst = src[:-9] if name.lower().endswith(".disabled") else src + ".disabled"
    os.replace(src, dst)


def fetch_news():
    j = get_json("https://launchercontent.mojang.com/v2/javaPatchNotes.json")
    es = sorted(j.get("entries", []), key=lambda e: str(e.get("date", "")), reverse=True)[:4]
    return [(e.get("title") or ("Minecraft " + str(e.get("version", ""))), str(e.get("date", ""))[:10],
             str(e.get("type", ""))) for e in es]


# ---------------------------------------------------------------- modrinth
MR = "https://api.modrinth.com/v2"


def mr_search(query, mcver):
    facets = [["project_type:mod"], ["categories:fabric"]]
    if mcver:
        facets.append(["versions:" + mcver])
    url = "%s/search?query=%s&limit=20&facets=%s" % (MR, urllib.parse.quote(query),
                                                       urllib.parse.quote(json.dumps(facets)))
    return get_json(url).get("hits", [])


def mr_version(project, mcver):
    url = "%s/project/%s/version?loaders=%s&game_versions=%s" % (
        MR, urllib.parse.quote(project), urllib.parse.quote(json.dumps(["fabric"])),
        urllib.parse.quote(json.dumps([mcver])))
    vs = get_json(url)
    for v in vs:
        if v.get("version_type") == "release":
            return v
    return vs[0] if vs else None


def mr_install(project, mcver, done, got, top):
    """Install a Modrinth project (Fabric build for mcver) plus its required dependencies."""
    if project in done:
        return
    done.add(project)
    v = mr_version(project, mcver)
    if not v or not v.get("files"):
        if top:
            raise RuntimeError("No Fabric build for Minecraft %s" % mcver)
        return
    f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
    path = os.path.join(MODS, f["filename"])
    existed = os.path.isfile(path)
    os.makedirs(MODS, exist_ok=True)
    download(f["url"], path, f.get("hashes", {}).get("sha1"))
    if not existed:
        got.append(f["filename"])
    for d in v.get("dependencies", []):
        if d.get("dependency_type") == "required" and d.get("project_id"):
            mr_install(d["project_id"], mcver, done, got, False)


def fmt_num(n):
    n = int(n or 0)
    return "%.1fM" % (n / 1e6) if n >= 1000000 else ("%.1fK" % (n / 1e3) if n >= 1000 else str(n))


# ---------------------------------------------------------------- custom widgets
class RBtn(tk.Canvas):
    """Rounded button drawn on a canvas."""

    def __init__(self, parent, text, command, w=120, h=36, bg=ORANGE, hover=ORANGE2, fg="#1a0f05",
                 font=("Segoe UI", 11, "bold"), r=9, outer=BG):
        tk.Canvas.__init__(self, parent, width=w, height=h, bg=outer, highlightthickness=0, bd=0, cursor="hand2")
        self.cmd, self.c0, self.c1, self.on = command, bg, hover, True
        pts = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h, w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
        self.shape = self.create_polygon(pts, smooth=True, fill=bg, outline="")
        self.label = self.create_text(w // 2, h // 2, text=text, fill=fg, font=font)
        self.bind("<Enter>", lambda e: self.paint(self.c1))
        self.bind("<Leave>", lambda e: self.paint(self.c0))
        self.bind("<Button-1>", self.click)

    def paint(self, c):
        if self.on:
            self.itemconfig(self.shape, fill=c)

    def click(self, e):
        if self.on and self.cmd:
            self.cmd()

    def enable(self, on):
        self.on = on
        self.itemconfig(self.shape, fill=self.c0 if on else "#3a322b")
        self.config(cursor="hand2" if on else "arrow")

    def set_text(self, t):
        self.itemconfig(self.label, text=t)


class Switch(tk.Canvas):
    def __init__(self, parent, on, cmd, outer=CARD):
        tk.Canvas.__init__(self, parent, width=44, height=22, bg=outer, highlightthickness=0, bd=0, cursor="hand2")
        self.on, self.cmd = on, cmd
        self.draw()
        self.bind("<Button-1>", self.click)

    def draw(self):
        self.delete("all")
        c = ORANGE if self.on else "#4a4038"
        self.create_oval(0, 0, 22, 22, fill=c, outline="")
        self.create_oval(22, 0, 44, 22, fill=c, outline="")
        self.create_rectangle(11, 0, 33, 22, fill=c, outline="")
        x = 24 if self.on else 2
        self.create_oval(x, 2, x + 18, 20, fill="#ffffff", outline="")

    def click(self, e):
        self.on = not self.on
        self.draw()
        self.cmd(self.on)


class Nav(tk.Frame):
    def __init__(self, parent, text, cmd):
        tk.Frame.__init__(self, parent, bg=PANEL, cursor="hand2")
        self.active = False
        self.bar = tk.Frame(self, width=4, bg=PANEL)
        self.bar.pack(side="left", fill="y")
        self.lbl = tk.Label(self, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 12), anchor="w", padx=18, pady=11)
        self.lbl.pack(side="left", fill="x", expand=True)
        for w in (self, self.lbl, self.bar):
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e: self.tint(True))
            w.bind("<Leave>", lambda e: self.tint(False))

    def tint(self, hover):
        if not self.active:
            c = CARD if hover else PANEL
            self.config(bg=c)
            self.lbl.config(bg=c)

    def set(self, active):
        self.active = active
        c = CARD if active else PANEL
        self.config(bg=c)
        self.lbl.config(bg=c, fg=ORANGE if active else MUTED)
        self.bar.config(bg=ORANGE if active else PANEL)


class Scroll(tk.Frame):
    def __init__(self, parent, bg=CARD):
        tk.Frame.__init__(self, parent, bg=bg)
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(self, command=self.cv.yview)
        self.inner = tk.Frame(self.cv, bg=bg)
        self.win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfig(self.win, width=e.width))

    def clear(self):
        for c in self.inner.winfo_children():
            c.destroy()


# ---------------------------------------------------------------- UI
class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.cfg.setdefault("friends", [])
        self.q, self.busy, self.manifest = queue.Queue(), False, None
        self.heads, self.cur, self.scrolls = {}, "home", {}
        self.browse_loaded, self.installing = False, False
        r = self.root = tk.Tk()
        r.title("%s %s" % (APP, VER))
        r.geometry("1020x640")
        r.minsize(900, 560)
        r.configure(bg=BG)
        try:
            r.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        self.setup_style()
        side = tk.Frame(r, bg=PANEL, width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.build_side(side)
        self.body = tk.Frame(r, bg=BG)
        self.body.pack(side="left", fill="both", expand=True)
        self.pages = {}
        for key, builder in (("home", self.page_home), ("mods", self.page_mods), ("browse", self.page_browse),
                             ("friends", self.page_friends),
                             ("acc", self.page_acc), ("set", self.page_set), ("log", self.page_log)):
            f = tk.Frame(self.body, bg=BG)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[key] = f
            builder(f)
        r.bind_all("<MouseWheel>", self.wheel)
        self.refresh_friends()
        self.refresh_mods()
        self.update_account_card()
        self.show("home")
        r.after(50, self.pump)
        r.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.load_versions, daemon=True).start()
        threading.Thread(target=self.load_news, daemon=True).start()
        if not self.cfg.get("shortcut_done"):
            threading.Thread(target=self.first_run, daemon=True).start()

    def first_run(self):
        ok, msg = install_shortcuts()
        self.log(msg)
        if ok:
            self.cfg["shortcut_done"] = True
            save_cfg(self.cfg)

    def make_shortcut(self):
        def work():
            ok, msg = install_shortcuts()
            self.log(msg)
            if ok:
                self.cfg["shortcut_done"] = True
                save_cfg(self.cfg)
            self.ui(messagebox.showinfo if ok else messagebox.showerror, APP, msg)
        threading.Thread(target=work, daemon=True).start()

    # ---- styling helpers
    def setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=BG, background=CARD, foreground=TEXT, arrowcolor=ORANGE,
                     bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, padding=6)
        st.map("TCombobox", fieldbackground=[("readonly", BG)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", BG)], selectforeground=[("readonly", TEXT)])
        st.configure("Horizontal.TProgressbar", troughcolor=BG, background=ORANGE, bordercolor=EDGE,
                     lightcolor=ORANGE, darkcolor=ORANGE, thickness=10)
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ORANGE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#1a0f05")

    def lbl(self, p, text, size=11, color=TEXT, bold=False, bg=BG, **kw):
        return tk.Label(p, text=text, bg=bg, fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                        anchor="w", justify="left", **kw)

    def card(self, p, **kw):
        return tk.Frame(p, bg=CARD, highlightbackground=EDGE, highlightthickness=1, **kw)

    def field(self, p, var, width=30, bg=BG):
        return tk.Entry(p, textvariable=var, width=width, bg=bg, fg=TEXT, insertbackground=ORANGE, relief="flat",
                        highlightbackground=EDGE, highlightcolor=ORANGE, highlightthickness=1,
                        font=("Segoe UI", 11))

    def check(self, p, text, var, cmd=None, bg=CARD):
        return tk.Checkbutton(p, text=text, variable=var, command=cmd, bg=bg, fg=TEXT, selectcolor=BG,
                              activebackground=bg, activeforeground=ORANGE, font=("Segoe UI", 10), bd=0,
                              highlightthickness=0)

    def title(self, p, text, sub):
        self.lbl(p, text, 24, TEXT, True).pack(anchor="w")
        self.lbl(p, sub, 11, MUTED).pack(anchor="w", pady=(2, 18))

    # ---- plumbing
    def show(self, k):
        self.cur = k
        self.pages[k].tkraise()
        for key, n in self.nav.items():
            n.set(key == k)
        if k == "mods":
            self.refresh_mods()
        if k == "browse" and not self.browse_loaded:
            self.search_mods()

    def wheel(self, e):
        s = self.scrolls.get(self.cur)
        if s:
            s.cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def ui(self, f, *a):
        self.q.put((f, a))

    def pump(self):
        try:
            while True:
                f, a = self.q.get_nowait()
                try:
                    f(*a)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.root.after(50, self.pump)

    def log(self, s):
        def w():
            self.logbox.config(state="normal")
            self.logbox.insert("end", s if s.endswith("\n") else s + "\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        self.ui(w)

    def status(self, text, frac=None):
        def w():
            self.stat.config(text=text)
            if frac is None:
                self.bar.config(mode="indeterminate")
                self.bar.start(15)
            else:
                self.bar.stop()
                self.bar.config(mode="determinate", value=frac * 100)
        self.ui(w)
        self.log(text)

    def head(self, name, label):
        key = name.lower()
        if key in self.heads:
            label.config(image=self.heads[key])
            label.image = self.heads[key]
            return
        if not re.match(r"^\w{3,16}$", name):
            return

        def work():
            try:
                with req("https://minotar.net/avatar/%s/32.png" % urllib.parse.quote(name), timeout=10) as r:
                    raw = r.read()

                def done():
                    im = self.heads.get(key) or tk.PhotoImage(data=base64.b64encode(raw).decode())
                    self.heads[key] = im
                    try:
                        label.config(image=im)
                        label.image = im
                    except tk.TclError:
                        pass
                self.ui(done)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    # ---- sidebar
    def build_side(self, side):
        cv = tk.Canvas(side, width=210, height=100, bg=PANEL, highlightthickness=0)
        cv.pack(pady=(14, 6))
        cv.create_polygon(40, 18, 56, 40, 64, 58, 56, 80, 40, 90, 24, 80, 16, 58, 28, 44, 32, 54, 36, 36,
                          smooth=True, fill=ORANGE, outline="")
        cv.create_polygon(40, 52, 50, 68, 45, 82, 40, 86, 34, 82, 31, 68, smooth=True, fill="#ffc15a", outline="")
        cv.create_text(76, 44, text="BLAZE", anchor="w", fill=TEXT, font=("Segoe UI", 20, "bold"))
        cv.create_text(78, 68, text="LAUNCHER", anchor="w", fill=ORANGE, font=("Segoe UI", 10, "bold"))
        self.nav = {}
        for key, text in (("home", "Home"), ("mods", "Mods"), ("browse", "Modrinth"), ("friends", "Friends"),
                          ("acc", "Account"),
                          ("set", "Settings"), ("log", "Log")):
            n = Nav(side, text, lambda k=key: self.show(k))
            n.pack(fill="x")
            self.nav[key] = n
        bottom = tk.Frame(side, bg=CARD, highlightbackground=EDGE, highlightthickness=1)
        bottom.pack(side="bottom", fill="x", padx=14, pady=16)
        self.acc_head = tk.Label(bottom, bg=EDGE, width=4, height=2)
        self.acc_head.pack(side="left", padx=10, pady=10)
        col = tk.Frame(bottom, bg=CARD)
        col.pack(side="left")
        self.acc_name = self.lbl(col, "Player", 11, TEXT, True, CARD)
        self.acc_name.pack(anchor="w")
        self.acc_mode = self.lbl(col, "Offline", 9, MUTED, False, CARD)
        self.acc_mode.pack(anchor="w")

    def update_account_card(self):
        c = self.cfg
        ms = c.get("ms") or {}
        name = ms.get("name") if (c["mode"] == "ms" and ms.get("name")) else c["name"]
        self.acc_name.config(text=name or "Player")
        self.acc_mode.config(text="Microsoft" if c["mode"] == "ms" else "Offline")
        self.head(name or "", self.acc_head)

    # ---- home
    def panel(self, p, title):
        c = self.card(p)
        c.pack(fill="x", pady=(0, 14))
        self.lbl(c, title, 12, TEXT, True, CARD).pack(anchor="w", padx=14, pady=(12, 6))
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(0, 12))
        return inner

    def page_home(self, f):
        f.grid_columnconfigure(0, weight=1)
        f.grid_columnconfigure(1, minsize=300)
        f.grid_rowconfigure(0, weight=1)
        left = tk.Frame(f, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(30, 14), pady=26)
        right = tk.Frame(f, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 30), pady=26)
        self.title(left, "Ready to play?", "Pick a version and hit PLAY")
        card = self.card(left)
        card.pack(fill="x")
        self.lbl(card, "VERSION", 9, MUTED, True, CARD).pack(anchor="w", padx=18, pady=(16, 4))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=18)
        self.ver = tk.StringVar(value=self.cfg["version"])
        self.combo = ttk.Combobox(row, textvariable=self.ver, state="readonly", width=16, font=("Segoe UI", 13))
        self.combo.pack(side="left")
        self.fab = tk.BooleanVar(value=self.cfg["fabric"])
        self.snap = tk.BooleanVar(value=self.cfg["snapshots"])
        self.check(row, "Fabric", self.fab).pack(side="left", padx=(18, 4))
        self.check(row, "Snapshots", self.snap, self.fill_versions).pack(side="left", padx=4)
        self.play_btn = RBtn(card, "PLAY", self.play, w=320, h=64, font=("Segoe UI", 24, "bold"), r=14, outer=CARD)
        self.play_btn.pack(anchor="w", padx=18, pady=20)
        self.stat = self.lbl(card, "Idle", 10, MUTED, False, CARD)
        self.stat.pack(anchor="w", padx=18)
        self.bar = ttk.Progressbar(card, mode="determinate")
        self.bar.pack(fill="x", padx=18, pady=(6, 18))
        row = tk.Frame(left, bg=BG)
        row.pack(anchor="w", pady=16)
        RBtn(row, "Game folder", lambda: self.open_dir(GAME), 130, 36, CARD, "#2c241d", TEXT).pack(side="left", padx=(0, 10))
        RBtn(row, "Mods folder", lambda: self.open_dir(MODS), 130, 36, CARD, "#2c241d", TEXT).pack(side="left")
        self.home_friends = self.panel(right, "Friends")
        self.news_box = self.panel(right, "Recent news")
        self.lbl(self.news_box, "Loading...", 10, MUTED, False, CARD).pack(anchor="w")

    # ---- mods
    def page_mods(self, f):
        f.configure(padx=30, pady=26)
        self.title(f, "Mods", "Switch mods on and off. Works with Fabric and a matching Minecraft version.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(0, 12))
        RBtn(row, "Add mod", self.add_mod, 110, 36).pack(side="left", padx=(0, 10))
        RBtn(row, "Refresh", self.refresh_mods, 100, 36, CARD, "#2c241d", TEXT).pack(side="left", padx=(0, 10))
        RBtn(row, "Open folder", lambda: self.open_dir(MODS), 120, 36, CARD, "#2c241d", TEXT).pack(side="left")
        self.mods_count = self.lbl(row, "", 10, MUTED)
        self.mods_count.pack(side="right")
        sc = Scroll(f)
        sc.pack(fill="both", expand=True)
        self.scrolls["mods"] = sc

    def refresh_mods(self):
        sc = self.scrolls["mods"]
        sc.clear()
        try:
            mods = list_mods()
        except Exception:
            mods = []
        on = sum(1 for m in mods if not m[1])
        self.mods_count.config(text="%d mods, %d enabled" % (len(mods), on) if mods else "")
        if not mods:
            self.lbl(sc.inner, "No mods yet. Press Add mod and pick .jar files.", 11, MUTED, False, CARD).pack(
                anchor="w", padx=16, pady=16)
            return
        for name, disabled, size in mods:
            r = tk.Frame(sc.inner, bg=CARD)
            r.pack(fill="x", padx=12, pady=5)
            Switch(r, not disabled, lambda v, n=name: self.flip_mod(n)).pack(side="left")
            shown = name[:-9] if disabled else name
            self.lbl(r, shown, 11, MUTED if disabled else TEXT, False, CARD).pack(side="left", padx=12)
            self.lbl(r, "%.1f MB" % (size / 1048576.0), 10, MUTED, False, CARD).pack(side="right")
            tk.Frame(sc.inner, bg=EDGE, height=1).pack(fill="x", padx=12)

    def flip_mod(self, name):
        try:
            toggle_mod(name)
        except Exception as e:
            messagebox.showerror(APP, "Could not change the mod (is the game running?):\n%s" % e)
        self.refresh_mods()

    def add_mod(self):
        files = filedialog.askopenfilenames(title="Pick mods", filetypes=[("Mod files", "*.jar")])
        os.makedirs(MODS, exist_ok=True)
        for p in files:
            try:
                shutil.copy(p, MODS)
            except Exception as e:
                self.log("Could not copy %s: %s" % (p, e))
        self.refresh_mods()

    # ---- modrinth
    def page_browse(self, f):
        f.configure(padx=30, pady=26)
        self.title(f, "Modrinth", "Search Fabric mods and install them in one click")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        self.mq = tk.StringVar()
        e = self.field(row, self.mq, 30)
        e.pack(side="left", ipady=5, padx=(0, 10))
        e.bind("<Return>", lambda ev: self.search_mods())
        RBtn(row, "Search", self.search_mods, 100, 36).pack(side="left")
        self.browse_stat = self.lbl(f, "", 10, MUTED)
        self.browse_stat.pack(anchor="w", pady=(0, 8))
        sc = Scroll(f)
        sc.pack(fill="both", expand=True)
        self.scrolls["browse"] = sc

    def set_browse(self, text):
        self.browse_stat.config(text=text)

    def search_mods(self):
        self.browse_loaded = True
        mc, q = self.ver.get(), self.mq.get().strip()
        self.set_browse("Searching Modrinth (Fabric %s)..." % (mc or "any version"))

        def work():
            try:
                hits, err = mr_search(q, mc), None
            except Exception as e:
                hits, err = [], str(e)
            self.ui(self.fill_results, hits, err, mc)
        threading.Thread(target=work, daemon=True).start()

    def fill_results(self, hits, err, mc):
        sc = self.scrolls["browse"]
        sc.clear()
        if err:
            self.set_browse("Could not reach Modrinth: " + err)
            return
        self.set_browse("%d mods for Fabric %s. Turn on Fabric on the Home tab to load them." % (len(hits), mc or "(any)"))
        if not hits:
            self.lbl(sc.inner, "Nothing found.", 11, MUTED, False, CARD).pack(anchor="w", padx=16, pady=16)
        for h in hits:
            r = tk.Frame(sc.inner, bg=CARD)
            r.pack(fill="x", padx=12, pady=8)
            left = tk.Frame(r, bg=CARD)
            left.pack(side="left", fill="x", expand=True)
            self.lbl(left, h.get("title", "?"), 12, TEXT, True, CARD).pack(anchor="w")
            self.lbl(left, "by %s, %s downloads" % (h.get("author", "?"), fmt_num(h.get("downloads"))), 9, MUTED,
                     False, CARD).pack(anchor="w")
            self.lbl(left, (h.get("description") or "")[:140], 10, TEXT, False, CARD, wraplength=430).pack(
                anchor="w", pady=(2, 0))
            RBtn(r, "Install", lambda p=h["project_id"], t=h.get("title", "mod"): self.install_mod(p, t),
                 90, 32, outer=CARD).pack(side="right", padx=(10, 0))
            tk.Frame(sc.inner, bg=EDGE, height=1).pack(fill="x", padx=12)

    def install_mod(self, pid, title):
        mc = self.ver.get()
        if not mc:
            messagebox.showinfo(APP, "Pick a Minecraft version on the Home tab first.")
            return
        if self.installing:
            return
        self.installing = True
        self.set_browse("Installing %s..." % title)

        def work():
            try:
                got = []
                mr_install(pid, mc, set(), got, True)
                msg = ("Installed: " + ", ".join(got)) if got else ("Already installed: " + title)
            except Exception as e:
                msg = "Install failed (%s): %s" % (title, e)
            self.log(msg)
            self.ui(self.set_browse, msg)
            self.installing = False
            self.ui(self.refresh_mods)
        threading.Thread(target=work, daemon=True).start()

    # ---- friends
    def page_friends(self, f):
        f.configure(padx=30, pady=26)
        self.title(f, "Friends", "Your list is stored on this computer. Minecraft has no public online status.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(0, 12))
        self.fname = tk.StringVar()
        e = self.field(row, self.fname, 24)
        e.pack(side="left", ipady=5, padx=(0, 10))
        e.bind("<Return>", lambda ev: self.add_friend())
        RBtn(row, "Add friend", self.add_friend, 110, 36).pack(side="left")
        sc = Scroll(f)
        sc.pack(fill="both", expand=True)
        self.scrolls["friends"] = sc

    def add_friend(self):
        n = self.fname.get().strip()
        if not re.match(r"^\w{3,16}$", n):
            messagebox.showinfo(APP, "Nickname: 3-16 characters (letters, digits, _).")
            return
        if n.lower() in [x.lower() for x in self.cfg["friends"]]:
            messagebox.showinfo(APP, "%s is already in your list." % n)
            return
        self.cfg["friends"].append(n)
        save_cfg(self.cfg)
        self.fname.set("")
        self.refresh_friends()

    def remove_friend(self, n):
        self.cfg["friends"] = [x for x in self.cfg["friends"] if x != n]
        save_cfg(self.cfg)
        self.refresh_friends()

    def refresh_friends(self):
        sc = self.scrolls["friends"]
        sc.clear()
        for w in self.home_friends.winfo_children():
            w.destroy()
        fr = self.cfg["friends"]
        if not fr:
            self.lbl(sc.inner, "No friends yet. Add a nickname above.", 11, MUTED, False, CARD).pack(
                anchor="w", padx=16, pady=16)
            self.lbl(self.home_friends, "No friends yet. Add some in the Friends tab.", 10, MUTED, False, CARD,
                     wraplength=250).pack(anchor="w")
        for n in fr:
            r = tk.Frame(sc.inner, bg=CARD)
            r.pack(fill="x", padx=12, pady=6)
            h = tk.Label(r, bg=EDGE, width=4, height=2)
            h.pack(side="left")
            self.head(n, h)
            self.lbl(r, n, 12, TEXT, False, CARD).pack(side="left", padx=12)
            RBtn(r, "Remove", lambda x=n: self.remove_friend(x), 84, 28, EDGE, "#3a3028", TEXT,
                 ("Segoe UI", 9, "bold"), 7, CARD).pack(side="right")
        for n in fr[:5]:
            r = tk.Frame(self.home_friends, bg=CARD)
            r.pack(fill="x", pady=3)
            h = tk.Label(r, bg=EDGE, width=4, height=2)
            h.pack(side="left")
            self.head(n, h)
            self.lbl(r, n, 11, TEXT, False, CARD).pack(side="left", padx=10)

    # ---- news
    def load_news(self):
        try:
            items = fetch_news()
        except Exception:
            items = None
        self.ui(self.fill_news, items)

    def fill_news(self, items):
        for w in self.news_box.winfo_children():
            w.destroy()
        if not items:
            self.lbl(self.news_box, "News unavailable.", 10, MUTED, False, CARD).pack(anchor="w")
            return
        for title, date, kind in items:
            self.lbl(self.news_box, title, 10, TEXT, True, CARD, wraplength=250).pack(anchor="w", pady=(4, 0))
            self.lbl(self.news_box, (date + "  " + kind).strip(), 9, MUTED, False, CARD).pack(anchor="w")

    # ---- account / settings / log
    def page_acc(self, f):
        f.configure(padx=30, pady=26)
        self.title(f, "Account", "Play offline with a nickname or sign in with Microsoft")
        c = self.card(f)
        c.pack(fill="x")
        self.mode = tk.StringVar(value=self.cfg["mode"])
        for text, val in (("Offline (nickname only)", "offline"), ("Microsoft account", "ms")):
            tk.Radiobutton(c, text=text, variable=self.mode, value=val, bg=CARD, fg=TEXT, selectcolor=BG,
                           activebackground=CARD, activeforeground=ORANGE, font=("Segoe UI", 11), bd=0,
                           highlightthickness=0).pack(anchor="w", padx=16, pady=(12 if val == "offline" else 0, 0))
        self.lbl(c, "Nickname", 10, MUTED, False, CARD).pack(anchor="w", padx=18, pady=(14, 3))
        self.name = tk.StringVar(value=self.cfg["name"])
        self.field(c, self.name, 28).pack(anchor="w", padx=18, ipady=4)
        self.lbl(c, "Azure Client ID (Microsoft login only, see README)", 10, MUTED, False, CARD).pack(
            anchor="w", padx=18, pady=(14, 3))
        self.cid = tk.StringVar(value=self.cfg["client_id"])
        self.field(c, self.cid, 48).pack(anchor="w", padx=18, ipady=4)
        RBtn(c, "Sign in with Microsoft", self.ms_signin, 210, 38, outer=CARD).pack(anchor="w", padx=18, pady=16)
        ms = self.cfg.get("ms") or {}
        self.msinfo = self.lbl(c, ("Signed in as " + ms["name"]) if ms.get("name") else "Not signed in", 10, MUTED,
                               False, CARD)
        self.msinfo.pack(anchor="w", padx=18, pady=(0, 16))

    def page_set(self, f):
        f.configure(padx=30, pady=26)
        self.title(f, "Settings", "Memory and Java")
        c = self.card(f)
        c.pack(fill="x")
        self.lbl(c, "Memory (MB)", 10, MUTED, False, CARD).pack(anchor="w", padx=18, pady=(16, 3))
        self.ram = tk.IntVar(value=self.cfg["ram"])
        tk.Spinbox(c, from_=1024, to=16384, increment=512, textvariable=self.ram, width=10, bg=BG, fg=TEXT,
                   buttonbackground=CARD, relief="flat", font=("Segoe UI", 11), highlightbackground=EDGE,
                   highlightthickness=1).pack(anchor="w", padx=18)
        self.lbl(c, "Java path (empty = automatic)", 10, MUTED, False, CARD).pack(anchor="w", padx=18, pady=(16, 3))
        row = tk.Frame(c, bg=CARD)
        row.pack(anchor="w", padx=18)
        self.java = tk.StringVar(value=self.cfg["java"])
        self.field(row, self.java, 44).pack(side="left", ipady=4)
        RBtn(row, "Browse", lambda: self.java.set(filedialog.askopenfilename(
            filetypes=[("java.exe", "java.exe")]) or self.java.get()), 90, 32, EDGE, "#3a3028", TEXT,
            outer=CARD).pack(side="left", padx=10)
        RBtn(c, "Create desktop shortcut", self.make_shortcut, 200, 34, EDGE, "#3a3028", TEXT,
             outer=CARD).pack(anchor="w", padx=18, pady=(18, 0))
        self.lbl(c, "Data folder: " + BASE, 9, MUTED, False, CARD).pack(anchor="w", padx=18, pady=16)

    def page_log(self, f):
        f.configure(padx=30, pady=26)
        self.title(f, "Log", "Downloads and game output")
        self.logbox = tk.Text(f, bg=PANEL, fg=TEXT, relief="flat", font=("Consolas", 9), state="disabled",
                              wrap="none", highlightbackground=EDGE, highlightthickness=1)
        self.logbox.pack(fill="both", expand=True)

    def open_dir(self, p):
        os.makedirs(p, exist_ok=True)
        try:
            os.startfile(p)
        except Exception:
            self.log("Folder: " + p)

    # ---- versions
    def load_versions(self):
        p = os.path.join(MC, "version_manifest.json")
        try:
            m = get_json(MANIFEST)
            os.makedirs(MC, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(m, f)
        except Exception:
            try:
                with open(p, encoding="utf-8") as f:
                    m = json.load(f)
            except Exception:
                m = None
        self.manifest = m
        self.ui(self.fill_versions)

    def fill_versions(self):
        if not self.manifest:
            self.status("Could not load the version list (no internet?)", 0)
            return
        vs = [v["id"] for v in self.manifest["versions"] if self.snap.get() or v["type"] == "release"]
        self.combo["values"] = vs
        if self.ver.get() not in vs:
            self.ver.set(self.manifest["latest"]["release"])

    # ---- actions
    def collect(self):
        c = self.cfg
        c.update(mode=self.mode.get(), name=self.name.get().strip(), client_id=self.cid.get().strip(),
                 ram=int(self.ram.get() or 2048), java=self.java.get().strip(), fabric=self.fab.get(),
                 snapshots=self.snap.get(), version=self.ver.get())
        save_cfg(c)
        self.update_account_card()

    def ms_signin(self):
        self.collect()
        if not self.cfg["client_id"]:
            messagebox.showinfo(APP, "Enter an Azure Client ID first (see README).")
            return

        def work():
            try:
                def code(c, url):
                    self.ui(messagebox.showinfo, APP, "Open %s\nand enter code: %s" % (url, c))
                    self.ui(lambda: __import__("webbrowser").open(url))
                ms = ms_login(self.cfg["client_id"], code, (self.cfg.get("ms") or {}).get("refresh"))
                self.cfg["ms"] = ms
                save_cfg(self.cfg)
                self.ui(lambda: self.msinfo.config(text="Signed in as " + ms["name"]))
                self.ui(self.update_account_card)
            except Exception as e:
                self.log(traceback.format_exc())
                self.ui(messagebox.showerror, APP, "Microsoft login failed:\n%s" % e)
        threading.Thread(target=work, daemon=True).start()

    def play(self):
        if self.busy:
            return
        if not self.ver.get():
            messagebox.showinfo(APP, "Pick a version first.")
            return
        self.collect()
        if self.cfg["mode"] == "offline" and not re.match(r"^\w{3,16}$", self.cfg["name"]):
            messagebox.showinfo(APP, "Nickname: 3-16 characters (letters, digits, _).")
            return
        self.busy = True
        self.play_btn.enable(False)
        threading.Thread(target=self.run_game, daemon=True).start()

    def run_game(self):
        c = self.cfg
        try:
            if c["mode"] == "ms":
                ms = c.get("ms") or {}
                if not ms.get("token"):
                    raise RuntimeError("Sign in with Microsoft on the Account page first.")
                if time.time() > ms.get("exp", 0):
                    self.status("Refreshing Microsoft login...", None)
                    ms = ms_login(c["client_id"], lambda a, b: None, ms.get("refresh"))
                    c["ms"] = ms
                    save_cfg(c)
                name, uid, token, utype = ms["name"], ms["uuid"], ms["token"], "msa"
            else:
                name, uid, token, utype = c["name"], offline_uuid(c["name"]), "0", "legacy"
            v, vid, cp, natdir, assets_root = prepare(c["version"], c["fabric"], self.manifest, self.status)
            need = v.get("javaVersion", {}).get("majorVersion", 8)
            java = find_java(need, c, self.status)
            os.makedirs(GAME, exist_ok=True)
            vars_ = {"auth_player_name": name, "version_name": vid, "game_directory": GAME,
                     "assets_root": assets_root, "game_assets": assets_root, "assets_index_name": v["assetIndex"]["id"],
                     "auth_uuid": uid, "auth_access_token": token, "auth_session": "token:%s:%s" % (token, uid),
                     "clientid": "", "auth_xuid": "", "user_type": utype, "version_type": v.get("type", "release"),
                     "user_properties": "{}", "natives_directory": natdir, "launcher_name": APP,
                     "launcher_version": VER, "classpath": ";".join(cp),
                     "library_directory": os.path.join(MC, "libraries"), "classpath_separator": ";"}
            jvm, game = build_args(v, vars_)
            cmd = [java, "-Xmx%dM" % c["ram"], "-Xms512M"] + jvm + [v["mainClass"]] + game
            self.status("Starting Minecraft...", 1)
            shown = " ".join(cmd)
            self.log("CMD: " + (shown.replace(token, "***") if token != "0" else shown))
            p = subprocess.Popen(cmd, cwd=GAME, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NOWIN)
            for line in p.stdout:
                self.log(line.decode("utf-8", "replace").rstrip())
            self.status("Game closed (exit code %s)" % p.wait(), 0)
        except Exception as e:
            self.log(traceback.format_exc())
            self.status("Error", 0)
            self.ui(messagebox.showerror, APP, str(e))
        finally:
            self.busy = False
            self.ui(self.play_btn.enable, True)

    def close(self):
        try:
            self.collect()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    os.makedirs(BASE, exist_ok=True)
    App().root.mainloop()
