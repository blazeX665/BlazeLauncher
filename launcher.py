# -*- coding: utf-8 -*-
"""BlazeLauncher - a small Minecraft launcher. Pure Python stdlib, works on Windows 7-11 (Python 3.8+)."""
import os, sys, re, json, time, uuid, shutil, hashlib, zipfile, queue, glob, platform, threading
import subprocess, traceback, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

APP, VER = "BlazeLauncher", "1.0.0"
BASE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), ".blazelauncher")
MC = os.path.join(BASE, "minecraft")
GAME = os.path.join(BASE, "game")
RT = os.path.join(BASE, "runtime")
CFG = os.path.join(BASE, "config.json")
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
NOWIN = 0x08000000 if os.name == "nt" else 0
IS64 = platform.machine().endswith("64") or "PROCESSOR_ARCHITEW6432" in os.environ
ARCH = "x64" if IS64 else "x86"
BG, PANEL, ORANGE, TEXT, MUTED = "#15110e", "#201b16", "#ff7a1a", "#f4ede5", "#9b9086"

DEFAULT = {"mode": "offline", "name": "Player", "ram": 2048, "java": "", "snapshots": False,
           "fabric": False, "version": "", "client_id": "", "ms": {}}


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


# ---------------------------------------------------------------- UI
class App:
    def __init__(self):
        self.cfg, self.q, self.busy, self.manifest = load_cfg(), queue.Queue(), False, None
        r = self.root = tk.Tk()
        r.title("%s %s" % (APP, VER))
        r.geometry("920x580")
        r.minsize(780, 500)
        r.configure(bg=BG)
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=ORANGE)
        st.configure("Horizontal.TProgressbar", troughcolor=PANEL, background=ORANGE, bordercolor=BG,
                     lightcolor=ORANGE, darkcolor=ORANGE)
        r.option_add("*TCombobox*Listbox.background", PANEL)
        r.option_add("*TCombobox*Listbox.foreground", TEXT)

        side = tk.Frame(r, bg=PANEL, width=180)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="BLAZE", bg=PANEL, fg=ORANGE, font=("Segoe UI", 22, "bold")).pack(pady=(24, 0))
        tk.Label(side, text="LAUNCHER", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(pady=(0, 24))
        self.body = tk.Frame(r, bg=BG)
        self.body.pack(side="left", fill="both", expand=True)
        self.pages = {}
        for key, title, builder in (("home", "Home", self.page_home), ("acc", "Account", self.page_acc),
                                    ("set", "Settings", self.page_set), ("log", "Log", self.page_log)):
            f = tk.Frame(self.body, bg=BG)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[key] = f
            builder(f)
            b = tk.Button(side, text=title, bg=PANEL, fg=TEXT, activebackground=BG, activeforeground=ORANGE,
                          relief="flat", font=("Segoe UI", 11), anchor="w", padx=22, pady=8, bd=0,
                          command=lambda k=key: self.show(k))
            b.pack(fill="x")
        self.show("home")
        r.after(50, self.pump)
        r.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.load_versions, daemon=True).start()

    # helpers
    def lbl(self, p, text, size=11, color=TEXT, bold=False):
        return tk.Label(p, text=text, bg=BG, fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                        anchor="w", justify="left")

    def btn(self, p, text, cmd, accent=False):
        return tk.Button(p, text=text, command=cmd, relief="flat", bd=0, padx=14, pady=6,
                         bg=ORANGE if accent else PANEL, fg="#1a0f05" if accent else TEXT,
                         activebackground="#ff9a4a" if accent else "#2c251e", activeforeground=TEXT,
                         font=("Segoe UI", 10, "bold"))

    def entry(self, p, var, width=30, show=None):
        return tk.Entry(p, textvariable=var, width=width, bg=PANEL, fg=TEXT, insertbackground=ORANGE,
                        relief="flat", font=("Segoe UI", 11), show=show)

    def show(self, k):
        self.pages[k].tkraise()

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

    # pages
    def page_home(self, f):
        f.configure(padx=40, pady=30)
        self.lbl(f, "Ready to play?", 22, TEXT, True).pack(anchor="w")
        self.lbl(f, "Pick a version and press PLAY", 11, MUTED).pack(anchor="w", pady=(0, 24))
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w")
        self.ver = tk.StringVar(value=self.cfg["version"])
        self.combo = ttk.Combobox(row, textvariable=self.ver, state="readonly", width=18, font=("Segoe UI", 12))
        self.combo.pack(side="left")
        self.fab = tk.BooleanVar(value=self.cfg["fabric"])
        self.snap = tk.BooleanVar(value=self.cfg["snapshots"])
        for text, var, cmd in (("Fabric", self.fab, None), ("Snapshots", self.snap, self.fill_versions)):
            tk.Checkbutton(row, text=text, variable=var, command=cmd, bg=BG, fg=TEXT, selectcolor=PANEL,
                           activebackground=BG, activeforeground=ORANGE, font=("Segoe UI", 10)).pack(side="left", padx=12)
        self.play_btn = tk.Button(f, text="PLAY", command=self.play, bg=ORANGE, fg="#1a0f05", relief="flat", bd=0,
                                  activebackground="#ff9a4a", font=("Segoe UI", 20, "bold"), padx=60, pady=12)
        self.play_btn.pack(anchor="w", pady=30)
        self.stat = self.lbl(f, "Idle", 10, MUTED)
        self.stat.pack(anchor="w")
        self.bar = ttk.Progressbar(f, length=420, mode="determinate")
        self.bar.pack(anchor="w", pady=6)
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=20)
        self.btn(row, "Game folder", lambda: self.open_dir(GAME)).pack(side="left", padx=(0, 8))
        self.btn(row, "Mods folder", lambda: self.open_dir(os.path.join(GAME, "mods"))).pack(side="left")

    def page_acc(self, f):
        f.configure(padx=40, pady=30)
        self.lbl(f, "Account", 22, TEXT, True).pack(anchor="w", pady=(0, 16))
        self.mode = tk.StringVar(value=self.cfg["mode"])
        for text, val in (("Offline (nickname only)", "offline"), ("Microsoft account", "ms")):
            tk.Radiobutton(f, text=text, variable=self.mode, value=val, bg=BG, fg=TEXT, selectcolor=PANEL,
                           activebackground=BG, activeforeground=ORANGE, font=("Segoe UI", 11)).pack(anchor="w")
        self.lbl(f, "Nickname", 10, MUTED).pack(anchor="w", pady=(16, 2))
        self.name = tk.StringVar(value=self.cfg["name"])
        self.entry(f, self.name).pack(anchor="w")
        self.lbl(f, "Azure Client ID (for Microsoft login, see README)", 10, MUTED).pack(anchor="w", pady=(16, 2))
        self.cid = tk.StringVar(value=self.cfg["client_id"])
        self.entry(f, self.cid, 44).pack(anchor="w")
        self.btn(f, "Sign in with Microsoft", self.ms_signin, True).pack(anchor="w", pady=16)
        ms = self.cfg.get("ms") or {}
        self.msinfo = self.lbl(f, ("Signed in as " + ms["name"]) if ms.get("name") else "Not signed in", 10, MUTED)
        self.msinfo.pack(anchor="w")

    def page_set(self, f):
        f.configure(padx=40, pady=30)
        self.lbl(f, "Settings", 22, TEXT, True).pack(anchor="w", pady=(0, 16))
        self.lbl(f, "Memory (MB)", 10, MUTED).pack(anchor="w")
        self.ram = tk.IntVar(value=self.cfg["ram"])
        tk.Spinbox(f, from_=1024, to=16384, increment=512, textvariable=self.ram, width=10, bg=PANEL, fg=TEXT,
                   buttonbackground=PANEL, relief="flat", font=("Segoe UI", 11)).pack(anchor="w", pady=(2, 16))
        self.lbl(f, "Java path (empty = automatic)", 10, MUTED).pack(anchor="w")
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w")
        self.java = tk.StringVar(value=self.cfg["java"])
        self.entry(row, self.java, 44).pack(side="left")
        self.btn(row, "Browse", lambda: self.java.set(filedialog.askopenfilename(
            filetypes=[("java.exe", "java.exe")]) or self.java.get())).pack(side="left", padx=8)
        self.lbl(f, "Data folder: " + BASE, 9, MUTED).pack(anchor="w", pady=24)

    def page_log(self, f):
        f.configure(padx=20, pady=20)
        self.logbox = tk.Text(f, bg=PANEL, fg=TEXT, relief="flat", font=("Consolas", 9), state="disabled", wrap="none")
        self.logbox.pack(fill="both", expand=True)

    def open_dir(self, p):
        os.makedirs(p, exist_ok=True)
        try:
            os.startfile(p)
        except Exception:
            self.log("Folder: " + p)

    # versions
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

    # actions
    def collect(self):
        c = self.cfg
        c.update(mode=self.mode.get(), name=self.name.get().strip(), client_id=self.cid.get().strip(),
                 ram=int(self.ram.get() or 2048), java=self.java.get().strip(), fabric=self.fab.get(),
                 snapshots=self.snap.get(), version=self.ver.get())
        save_cfg(c)

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
        self.play_btn.config(state="disabled")
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
            self.log("CMD: " + " ".join(cmd).replace(token, "***") if token != "0" else " ".join(cmd))
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
            self.ui(lambda: self.play_btn.config(state="normal"))

    def close(self):
        try:
            self.collect()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    os.makedirs(BASE, exist_ok=True)
    App().root.mainloop()
