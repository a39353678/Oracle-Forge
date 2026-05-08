import os
import re
import json
import time
import shutil
import zipfile
import signal
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import yaml
except Exception:
    yaml = None

BASE = Path("/opt/oracle-forge")
WORKSPACE = BASE / "workspace"
SRC = WORKSPACE / "src" / "azerothcore-wotlk"
BUILD = WORKSPACE / "build" / "azerothcore"
INSTALL = WORKSPACE / "install" / "azerothcore"
BIN = INSTALL / "bin"
ETC = INSTALL / "etc"
DATA = WORKSPACE / "data"
DOWNLOADS = WORKSPACE / "downloads"
LUA_DIR = WORKSPACE / "lua"
MODULES_DIR = SRC / "modules"
LOGS = WORKSPACE / "logs"
ACORE_LOGS = LOGS / "acore"
TASKS = WORKSPACE / "tasks"
HISTORY = TASKS / "history"
CURRENT_TASK = TASKS / "current_task.json"
TASK_LOG = TASKS / "current_task.log"
STOP_FILE = TASKS / "STOP_REQUESTED"
CONFIG = BASE / "config.yaml"

SAFE_ACTIONS = {
    "init_source", "init_database", "configure_core", "build_core",
    "prepare_configs", "download_official_data", "install_module",
    "run_sql", "write_lua", "upload_lua_script", "install_lua_from_url",
    "start_all", "stop_all", "restart_all", "restart_world",
    "generate_launcher",
}

LOG_LABELS = {
    "source.log": "源码拉取日志",
    "database.log": "数据库初始化日志",
    "cmake.log": "CMake 构建配置日志",
    "build.log": "编译安装日志",
    "data_download.log": "官方地图数据下载日志",
    "data_upload.log": "上传数据 / Lua 日志",
    "server.log": "服务动作日志",
    "Auth.log": "Auth 内部日志",
    "Auth.screen.log": "Auth 启动输出 / 秒退日志",
    "Server.log": "World 内部日志",
    "World.screen.log": "World 启动输出 / 秒退日志",
    "command.log": "控制台命令发送日志",
    "sql.log": "SQL 执行日志",
    "launcher.log": "登录器生成 / realmlist 修正日志",
}

MODE_LABELS = {
    "stable": "稳控模式",
    "cooperative": "协同模式",
    "autonomous": "自治模式",
}

def ensure_dirs():
    for p in [WORKSPACE, SRC.parent, BUILD, INSTALL, DATA, DOWNLOADS, LUA_DIR, LOGS, ACORE_LOGS, TASKS, HISTORY, WORKSPACE / "conversations" / "handoffs"]:
        p.mkdir(parents=True, exist_ok=True)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def load_config():
    if not CONFIG.exists():
        return {}
    try:
        if yaml:
            data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        else:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def current_mode():
    cfg = load_config()
    mode = cfg.get("execution_mode") or cfg.get("mode") or "cooperative"
    if mode not in MODE_LABELS:
        mode = "cooperative"
    return mode

def mode_label():
    return MODE_LABELS.get(current_mode(), "协同模式")

def mysql_root_password():
    cfg = load_config()
    return cfg.get("mysql_root_password") or cfg.get("mysql", {}).get("root_password") or "gswxy.com"

def acore_password():
    cfg = load_config()
    return cfg.get("acore_password") or cfg.get("mysql", {}).get("acore_password") or "gswxy.com"

def append_file(path: Path, line: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as f:
        f.write(line)
        if not line.endswith("\n"):
            f.write("\n")

class TaskManager:
    def start(self, action, title=None, total=1, payload=None):
        ensure_dirs()
        if STOP_FILE.exists():
            STOP_FILE.unlink()
        TASK_LOG.write_text("", encoding="utf-8")
        data = {
            "task_id": f"{stamp()}_{action}",
            "action": action,
            "title": title or action,
            "status": "running",
            "step": "已开始",
            "progress": {"current": 0, "total": total},
            "started_at": now(),
            "finished_at": None,
            "result": None,
            "error": None,
            "blocked": None,
            "ai_analysis": None,
            "execution_mode": current_mode(),
            "execution_mode_label": mode_label(),
            "payload": payload or {},
            "log_file": str(TASK_LOG),
            "completed_actions": [],
            "remaining_actions": [],
            "failed_action": None,
        }
        atomic_write(CURRENT_TASK, json.dumps(data, ensure_ascii=False, indent=2))
        self.log(f"[{now()}] 任务开始：{title or action}")
        self.log(f"[{now()}] 执行模式：{mode_label()}")

    def get(self):
        if not CURRENT_TASK.exists():
            return {
                "status": "idle",
                "title": "空闲",
                "step": "暂无任务",
                "progress": {"current": 0, "total": 0},
                "execution_mode": current_mode(),
                "execution_mode_label": mode_label(),
                "result": None,
                "error": None,
            }
        try:
            return json.loads(CURRENT_TASK.read_text(encoding="utf-8"))
        except Exception as e:
            return {"status": "unknown", "error": str(e)}

    def update(self, **kwargs):
        data = self.get()
        if not isinstance(data, dict):
            data = {}
        data.update(kwargs)
        atomic_write(CURRENT_TASK, json.dumps(data, ensure_ascii=False, indent=2))

    def progress(self, current=None, total=None, step=None):
        data = self.get()
        prog = data.get("progress") or {}
        if current is not None:
            prog["current"] = current
        if total is not None:
            prog["total"] = total
        update = {"progress": prog}
        if step:
            update["step"] = step
        self.update(**update)

    def log(self, text):
        append_file(TASK_LOG, text)

    def block(self, reason, failed_action=None, ai_analysis=None, completed=None, remaining=None):
        data = self.get()
        data["status"] = "blocked"
        data["step"] = "执行受阻"
        data["blocked"] = {"reason": reason, "time": now()}
        data["error"] = reason
        if failed_action is not None:
            data["failed_action"] = failed_action
        if ai_analysis is not None:
            data["ai_analysis"] = ai_analysis
        if completed is not None:
            data["completed_actions"] = completed
        if remaining is not None:
            data["remaining_actions"] = remaining
        atomic_write(CURRENT_TASK, json.dumps(data, ensure_ascii=False, indent=2))
        self.log(f"[{now()}] 执行受阻：{reason}")
        if ai_analysis:
            self.log("[AI分析]\n" + str(ai_analysis))

    def finish(self, ok=True, result=None, error=None):
        data = self.get()
        data["status"] = "success" if ok else "failed"
        data["finished_at"] = now()
        data["result"] = result
        data["error"] = error
        atomic_write(CURRENT_TASK, json.dumps(data, ensure_ascii=False, indent=2))
        final_path = HISTORY / f"{data.get('task_id', stamp())}.json"
        atomic_write(final_path, json.dumps(data, ensure_ascii=False, indent=2))
        self.log(f"[{now()}] 任务结束：{'成功' if ok else '失败'}")
        if error:
            self.log(f"[错误] {error}")

TASK = TaskManager()

def request_stop():
    ensure_dirs()
    STOP_FILE.write_text(now(), encoding="utf-8")
    TASK.update(status="stopping", step="已请求中断")
    TASK.log(f"[{now()}] 收到强行中断请求")
    return {"ok": True, "message": "已请求中断。正在运行的受控命令会被终止。"}

def stop_requested():
    return STOP_FILE.exists()

def log_path(name: str):
    safe = Path(name).name
    if safe in {"Auth.log", "Auth.screen.log", "Server.log", "World.screen.log"}:
        return ACORE_LOGS / safe
    return LOGS / safe

def tail_text(path: Path, max_bytes=260000):
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        return f"读取日志失败：{e}"

def read_log(name: str):
    path = log_path(name)
    if path.exists():
        return tail_text(path)

    if name == "Auth.log" and (ACORE_LOGS / "Auth.screen.log").exists():
        return (
            f"日志文件不存在：{path}\n"
            "已自动回退显示 Auth.screen.log。\n"
            "说明：Auth 已运行但内部 Auth.log 不存在时，通常是 LogsDir/Appender/日志级别未对上；"
            "启动输出和秒退错误优先看 Auth.screen.log。\n\n"
            + tail_text(ACORE_LOGS / "Auth.screen.log")
        )
    if name == "Server.log" and (ACORE_LOGS / "World.screen.log").exists():
        return (
            f"日志文件不存在：{path}\n"
            "已自动回退显示 World.screen.log。\n\n"
            + tail_text(ACORE_LOGS / "World.screen.log")
        )

    return (
        f"日志文件不存在：{path}\n"
        f"说明：如果是 Auth.log / Server.log 不存在，优先查看 *.screen.log；"
        f"如果 screen 日志也不存在，说明启动命令没有执行或日志目录没有创建。"
    )

def run_shell(cmd, log_name="server.log", step=None, cwd=None, env=None, timeout=None):
    ensure_dirs()
    log_file = log_path(log_name)
    if step:
        TASK.update(step=step)
        TASK.log(f"[{now()}] {step}")
    append_file(log_file, f"\n===== {now()} | {cmd} =====")
    TASK.log(f"$ {cmd}")

    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        preexec_fn=os.setsid,
    )

    start = time.time()
    output_tail = []
    try:
        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                append_file(log_file, line.rstrip("\n"))
                TASK.log(line.rstrip("\n"))
                output_tail.append(line.rstrip("\n"))
                output_tail = output_tail[-100:]

            if stop_requested():
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    pass
                TASK.log(f"[{now()}] 命令已被中断")
                append_file(log_file, "命令已被中断")
                return {"ok": False, "code": -15, "error": "命令已被用户中断", "tail": output_tail}

            if timeout and time.time() - start > timeout:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    pass
                return {"ok": False, "code": -9, "error": "命令超时", "tail": output_tail}

            if proc.poll() is not None:
                rest = proc.stdout.read() if proc.stdout else ""
                if rest:
                    for rline in rest.splitlines():
                        append_file(log_file, rline)
                        TASK.log(rline)
                        output_tail.append(rline)
                        output_tail = output_tail[-100:]
                break

            time.sleep(0.05)
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

    code = proc.returncode
    ok = code == 0
    if not ok:
        TASK.log(f"[{now()}] 命令失败，退出码：{code}")
    return {"ok": ok, "code": code, "tail": output_tail, "log": str(log_file)}

def run_capture(cmd):
    try:
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=15)
        return p.stdout.strip()
    except Exception as e:
        return str(e)

def screen_ls():
    return run_capture("screen -ls || true")

def pgrep(pattern):
    out = run_capture(f"pgrep -af {json.dumps(pattern)} || true")
    return [line for line in out.splitlines() if line.strip()]

def is_auth_running():
    s = screen_ls()
    return ("oracle-auth" in s) or bool(pgrep("authserver"))

def is_world_running():
    s = screen_ls()
    return ("oracle-world" in s) or bool(pgrep("worldserver"))

def count_files():
    result = {"dbc": 0, "maps": 0, "vmaps": 0, "mmaps": 0}
    dirs = {"dbc": DATA / "dbc", "maps": DATA / "maps", "vmaps": DATA / "vmaps", "mmaps": DATA / "mmaps"}
    for key, d in dirs.items():
        if d.exists():
            result[key] = sum(1 for p in d.rglob("*") if p.is_file())
    return result

def data_ready_score():
    c = count_files()
    return sum(1 for k in ["dbc", "maps", "vmaps", "mmaps"] if c.get(k, 0) > 0)

def online_players():
    pw = mysql_root_password().replace("'", "'\\''")
    cmd = f"mysql -N -B -uroot -p'{pw}' acore_characters -e \"SELECT COUNT(*) FROM characters WHERE online=1;\" 2>/dev/null || echo 0"
    out = run_capture(cmd).splitlines()
    try:
        return int(out[-1].strip())
    except Exception:
        return 0

def runtime_status():
    ensure_dirs()
    task = TASK.get()
    return {
        "auth_running": is_auth_running(),
        "world_running": is_world_running(),
        "source_exists": SRC.exists(),
        "build_exists": BUILD.exists(),
        "install_exists": BIN.exists(),
        "data_counts": count_files(),
        "data_ready": data_ready_score(),
        "online_players": online_players(),
        "screen": screen_ls(),
        "task": task,
        "execution_mode": current_mode(),
        "execution_mode_label": mode_label(),
        "logs": {name: str(log_path(name)) for name in LOG_LABELS},
    }

def backup_existing(dest: Path, category="data"):
    if dest.exists():
        backup_dir = WORKSPACE / "backups" / category / stamp()
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / dest.name
        if dest.is_dir():
            shutil.move(str(dest), str(backup))
        else:
            shutil.move(str(dest), str(backup))
        return backup
    return None

def backup_data():
    ensure_dirs()
    target = WORKSPACE / "backups" / "data" / stamp()
    if DATA.exists() and any(DATA.iterdir()):
        target.mkdir(parents=True, exist_ok=True)
        for item in DATA.iterdir():
            shutil.move(str(item), str(target / item.name))
        return str(target)
    return None

def unpack_data_zip(zip_file):
    ensure_dirs()
    z = Path(zip_file)
    if not z.exists():
        raise FileNotFoundError(str(z))
    backup = backup_data()
    DATA.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z, "r") as zp:
        zp.extractall(DATA)
    append_file(log_path("data_upload.log"), f"{now()} 解压 data.zip 到 {DATA}，旧数据备份：{backup}")
    return {"ok": True, "backup": backup, "data_dir": str(DATA)}

def target_for_data_file(filename, target=None):
    ext = Path(filename).suffix.lower()
    if target and target != "auto":
        return DATA / target.strip("/")
    if ext == ".dbc":
        return DATA / "dbc"
    if ext == ".map":
        return DATA / "maps"
    if ext in {".vmtree", ".vmtile"}:
        return DATA / "vmaps"
    if ext in {".mmap", ".mmtile"}:
        return DATA / "mmaps"
    return DATA / "misc"

def upload_single_file(src_file, target=None):
    ensure_dirs()
    srcp = Path(src_file)
    dest_dir = target_for_data_file(srcp.name, target)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / srcp.name
    backup = backup_existing(dest, "data")
    shutil.move(str(srcp), str(dest))
    append_file(log_path("data_upload.log"), f"{now()} 上传文件 {srcp.name} -> {dest}，旧文件备份：{backup}")
    return {"ok": True, "dest": str(dest), "backup": str(backup) if backup else None}

def upload_lua_script(src_file):
    ensure_dirs()
    srcp = Path(src_file)
    if srcp.suffix.lower() != ".lua":
        raise ValueError("只允许上传 .lua 文件")
    dest = LUA_DIR / srcp.name
    backup = backup_existing(dest, "lua")
    shutil.move(str(srcp), str(dest))
    append_file(log_path("data_upload.log"), f"{now()} 上传 Lua {srcp.name} -> {dest}，旧文件备份：{backup}")
    return {"ok": True, "dest": str(dest), "backup": str(backup) if backup else None}

def install_lua_from_url(url, filename=None):
    ensure_dirs()
    if not url:
        raise ValueError("缺少 Lua URL")
    name = filename or Path(url.split("?")[0]).name or f"script_{stamp()}.lua"
    if not name.endswith(".lua"):
        name += ".lua"
    tmp = DOWNLOADS / "uploads" / name
    cmd = f"curl -L --fail --max-time 90 -o {json.dumps(str(tmp))} {json.dumps(url)}"
    r = run_shell(cmd, "data_upload.log", "下载 Lua 脚本")
    if not r.get("ok"):
        return r
    return upload_lua_script(tmp)

def normalize_module_payload(payload):
    payload = payload or {}
    url = (
        payload.get("module_url") or payload.get("url") or payload.get("source")
        or payload.get("repo") or payload.get("git_url") or payload.get("repository")
    )
    name = payload.get("name") or payload.get("module_name") or payload.get("module")
    rebuild = bool(payload.get("rebuild") or payload.get("recompile") or payload.get("need_build"))
    if url and not name:
        name = Path(str(url).rstrip("/").replace(".git", "")).name
    new = dict(payload)
    if url:
        new["module_url"] = url
    if name:
        new["name"] = name
    new["rebuild"] = rebuild
    return new

def normalize_action_item(item):
    if isinstance(item, str):
        action, payload = item, {}
    else:
        action = item.get("action") or item.get("name") or item.get("tool")
        payload = item.get("args") or item.get("payload") or item.get("params") or {}
    if action == "install_module":
        payload = normalize_module_payload(payload)
    if action == "install_lua_from_url":
        payload = {"url": payload.get("url") or payload.get("source") or payload.get("lua_url"), "filename": payload.get("filename")}
    return {"action": action, "args": payload}

def normalize_actions(actions):
    return [normalize_action_item(x) for x in actions]

def action_risk(action, args=None):
    args = args or {}
    if action in {"init_source", "prepare_configs", "upload_lua_script", "write_lua", "install_lua_from_url"}:
        return "低"
    if action in {"install_module", "configure_core", "build_core", "download_official_data"}:
        return "中"
    if action in {"generate_launcher"}:
        return "中"
    if action in {"start_all", "stop_all", "restart_all", "restart_world", "run_sql", "init_database"}:
        return "高"
    return "未知"

def preflight_actions(actions):
    normalized = normalize_actions(actions)
    issues = []
    fixes = []
    risks = []
    for i, item in enumerate(normalized, start=1):
        action = item.get("action")
        args = item.get("args") or {}
        if action not in SAFE_ACTIONS:
            issues.append(f"第 {i} 个动作不在白名单：{action}")
        if action == "install_module" and not args.get("module_url"):
            issues.append(f"第 {i} 个动作 install_module 缺少 module_url")
        risk = action_risk(action, args)
        risks.append({"index": i, "action": action, "risk": risk})
        orig = actions[i-1] if i-1 < len(actions) else {}
        if isinstance(orig, dict):
            orig_args = orig.get("args") or orig.get("payload") or {}
            if action == "install_module" and orig_args.get("source") and args.get("module_url"):
                fixes.append("install_module: source → module_url")
            if action == "install_module" and orig_args.get("module_name") and args.get("name"):
                fixes.append("install_module: module_name → name")
    high = [r for r in risks if r["risk"] in {"高", "极高"}]
    return {"ok": not issues, "actions": normalized, "issues": issues, "fixes": fixes, "risks": risks, "high_risk": high}

def ensure_config_file(name):
    conf = ETC / name
    dist = ETC / f"{name}.dist"
    if not conf.exists() and dist.exists():
        shutil.copy2(dist, conf)
    return conf

def patch_kv(text, key, value):
    line = f'{key} = "{value}"'
    pattern = re.compile(rf'(?m)^\s*#?\s*{re.escape(key)}\s*=.*$')
    if pattern.search(text):
        return pattern.sub(line, text)
    return text.rstrip() + "\n" + line + "\n"

def prepare_configs_impl():
    ensure_dirs()
    ETC.mkdir(parents=True, exist_ok=True)
    auth_conf = ensure_config_file("authserver.conf")
    world_conf = ensure_config_file("worldserver.conf")
    changed = []
    for conf in [auth_conf, world_conf]:
        if conf.exists():
            text = conf.read_text(encoding="utf-8", errors="replace")
            text = patch_kv(text, "LogsDir", str(ACORE_LOGS))
            if conf.name == "worldserver.conf":
                text = patch_kv(text, "DataDir", str(DATA))
            conf.write_text(text, encoding="utf-8")
            changed.append(str(conf))
    ACORE_LOGS.mkdir(parents=True, exist_ok=True)
    append_file(log_path("server.log"), f"{now()} 已修正配置：{changed}")
    return {"ok": True, "changed": changed, "logs_dir": str(ACORE_LOGS)}

def send_world_command(command):
    if not command.strip():
        raise ValueError("命令不能为空")
    escaped = command.replace('"', '\\"')
    run_shell(f'screen -S oracle-world -p 0 -X stuff "{escaped}\\r"', "command.log", f"发送控制台命令：{command}")
    return {"ok": True, "command": command}

def world_shutdown_notice(seconds, reason):
    try:
        seconds = int(seconds or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return
    if is_world_running():
        TASK.update(step=f"{reason}：已向 worldserver 发送 server shutdown {seconds}")
        TASK.log(f"[{now()}] {reason}：server shutdown {seconds}")
        try:
            send_world_command(f"server shutdown {seconds}")
        except Exception as e:
            TASK.log(f"[{now()}] 发送 server shutdown 失败，将改用本地等待：{e}")
    else:
        TASK.log(f"[{now()}] World 未运行，跳过 server shutdown 提醒")
    for remain in range(seconds, 0, -1):
        if stop_requested():
            raise RuntimeError("倒计时已被中断")
        if remain % 10 == 0 or remain <= 5:
            TASK.update(step=f"{reason}等待关服：{remain} 秒")
            TASK.log(f"[{now()}] {reason}等待关服：{remain} 秒")
        time.sleep(1)

def stop_screen(name):
    run_shell(f"screen -S {name} -X quit || true", "server.log", f"停止 screen：{name}")
    time.sleep(1)

def safe_pkill(process_name):
    # 不使用 pkill -f，避免匹配自己的 shell 命令导致 -15。
    run_shell(f"pkill -x {process_name} || true", "server.log", f"清理残留进程：{process_name}")

def start_all_impl():
    ensure_dirs()
    prepare_configs_impl()
    if not BIN.exists():
        raise RuntimeError(f"bin 目录不存在：{BIN}")
    auth_log = ACORE_LOGS / "Auth.screen.log"
    world_log = ACORE_LOGS / "World.screen.log"
    auth_cmd = f'cd {BIN} && ./authserver -c ../etc/authserver.conf >> {auth_log} 2>&1'
    world_cmd = f'cd {BIN} && ./worldserver -c ../etc/worldserver.conf >> {world_log} 2>&1'
    run_shell('screen -S oracle-auth -X quit || true', "server.log", "清理旧 Auth 会话")
    run_shell('screen -S oracle-world -X quit || true', "server.log", "清理旧 World 会话")
    run_shell(f"screen -dmS oracle-auth bash -lc {json.dumps(auth_cmd)}", "server.log", "启动 Auth")
    time.sleep(2)
    run_shell(f"screen -dmS oracle-world bash -lc {json.dumps(world_cmd)}", "server.log", "启动 World")
    time.sleep(2)
    return {"ok": True, "auth_running": is_auth_running(), "world_running": is_world_running()}

def stop_all_impl(seconds=0):
    if seconds:
        world_shutdown_notice(seconds, "停止服务端")
    stop_screen("oracle-world")
    stop_screen("oracle-auth")
    safe_pkill("worldserver")
    safe_pkill("authserver")
    return {"ok": True, "auth_running": is_auth_running(), "world_running": is_world_running()}

def restart_all_impl(seconds=0):
    if seconds:
        world_shutdown_notice(seconds, "重启服务端")
        stop_all_impl(0)
    else:
        stop_all_impl(0)
    return start_all_impl()

def restart_world_impl(seconds=0):
    if seconds:
        world_shutdown_notice(seconds, "重启 worldserver")
    stop_screen("oracle-world")
    safe_pkill("worldserver")
    world_log = ACORE_LOGS / "World.screen.log"
    world_cmd = f'cd {BIN} && ./worldserver -c ../etc/worldserver.conf >> {world_log} 2>&1'
    run_shell(f"screen -dmS oracle-world bash -lc {json.dumps(world_cmd)}", "server.log", "启动 World")
    time.sleep(2)
    return {"ok": True, "world_running": is_world_running()}



def launcher_safe_name(name):
    name = (name or "OracleForge").strip()
    name = re.sub(r'[^0-9A-Za-z._\-\u4e00-\u9fff]+', '_', name)
    return name[:60] or "OracleForge"

def detect_server_ips():
    ensure_dirs()
    raw = run_capture("hostname -I 2>/dev/null || true")
    ips = []
    for x in raw.split():
        if re.match(r'^\d+\.\d+\.\d+\.\d+$', x):
            ips.append(x)

    preferred = [
        x for x in ips
        if not (
            x.startswith("127.")
            or x.startswith("169.254.")
            or x.startswith("172.")
        )
    ]
    guessed = preferred[0] if preferred else (ips[0] if ips else "")
    return {
        "ips": ips,
        "guessed_ip": guessed,
        "docker_like": [x for x in ips if x.startswith("172.")]
    }

def mysql_escape(value):
    return str(value or "").replace("\\", "\\\\").replace("'", "''")

def generate_launcher_bat(server_ip, realm_name):
    title = launcher_safe_name(realm_name)
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f"title {title} Login",
        "",
        f'set "REALMLIST={server_ip}"',
        "echo Oracle Forge 神谕台登录器",
        "echo 服务器地址: %REALMLIST%",
        "echo.",
        "",
        'set "FOUND="',
        "for %%D in (zhCN zhTW enUS enGB koKR frFR deDE esES ruRU) do (",
        '  if exist "Data\\%%D\\realmlist.wtf" (',
        '    echo set realmlist %REALMLIST%> "Data\\%%D\\realmlist.wtf"',
        '    echo 已写入 Data\\%%D\\realmlist.wtf',
        '    set "FOUND=1"',
        "  )",
        ")",
        "",
        "if not defined FOUND (",
        '  if exist "realmlist.wtf" (',
        '    echo set realmlist %REALMLIST%> "realmlist.wtf"',
        "    echo 已写入 realmlist.wtf",
        '    set "FOUND=1"',
        "  )",
        ")",
        "",
        "if not defined FOUND (",
        "  echo 未找到 realmlist.wtf。",
        "  echo 请把本文件放到 Wow.exe 同目录后再运行。",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        'if exist "Wow.exe" (',
        '  start "" "Wow.exe"',
        ") else (",
        "  echo 未找到 Wow.exe，请把本文件放到魔兽客户端根目录。",
        "  pause",
        ")",
        "",
    ]
    return "\r\n".join(lines)

def generate_launcher_impl(payload=None):
    ensure_dirs()
    payload = payload or {}

    server_ip = (payload.get("server_ip") or payload.get("address") or "").strip()
    if not server_ip:
        raise ValueError("缺少 server_ip。请填写玩家实际连接的服务器 IP 或域名。")

    realm_name = (payload.get("realm_name") or "Oracle Forge").strip()
    local_ip = (payload.get("local_ip") or server_ip).strip()
    try:
        world_port = int(payload.get("world_port") or 8085)
    except Exception:
        world_port = 8085

    update_database = bool(payload.get("update_database", True))
    generate_bat = bool(payload.get("generate_bat", True))

    result = {
        "ok": True,
        "server_ip": server_ip,
        "local_ip": local_ip,
        "realm_name": realm_name,
        "world_port": world_port,
        "updated_database": False,
        "bat_file": None,
        "download_url": None,
    }

    append_file(log_path("launcher.log"), f"===== {now()} | 登录器生成 / realmlist 修正 =====")
    append_file(log_path("launcher.log"), f"server_ip={server_ip}, local_ip={local_ip}, realm_name={realm_name}, world_port={world_port}")

    if update_database:
        pw = mysql_root_password().replace("'", "'\\''")
        backup_dir = WORKSPACE / "backups" / "realmlist" / stamp()
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "realmlist_before.tsv"

        run_shell(
            f"mysql -N -B -uroot -p'{pw}' acore_auth -e \"SELECT id,name,address,localAddress,port FROM realmlist ORDER BY id;\" > {backup_file} 2>> {log_path('launcher.log')}",
            "launcher.log",
            "备份 acore_auth.realmlist"
        )

        rn = mysql_escape(realm_name)
        si = mysql_escape(server_ip)
        li = mysql_escape(local_ip)
        sql = (
            "UPDATE realmlist "
            f"SET name='{rn}', address='{si}', localAddress='{li}', port={world_port} "
            "WHERE id=1;"
        )
        sql_file = TASKS / f"realmlist_update_{stamp()}.sql"
        sql_file.write_text(sql, encoding="utf-8")
        r = run_shell(f"mysql -uroot -p'{pw}' acore_auth < {sql_file}", "launcher.log", "修正 acore_auth.realmlist")
        if not r.get("ok"):
            result["ok"] = False
            result["error"] = "realmlist 数据库更新失败"
            return result
        result["updated_database"] = True
        result["backup_file"] = str(backup_file)

    if generate_bat:
        launcher_dir = DOWNLOADS / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        safe = launcher_safe_name(realm_name)
        bat = launcher_dir / f"{safe}_Login.bat"
        bat.write_text(generate_launcher_bat(server_ip, realm_name), encoding="utf-8-sig")
        result["bat_file"] = str(bat)
        result["download_url"] = f"/api/download/launcher/{bat.name}"
        append_file(log_path("launcher.log"), f"已生成登录器：{bat}")

    TASK.log("登录器生成完成：" + json.dumps(result, ensure_ascii=False))
    return result

def do_action(action, payload=None):
    payload = payload or {}
    if action not in SAFE_ACTIONS:
        raise ValueError(f"动作不在白名单：{action}")

    if action == "init_source":
        if SRC.exists():
            return run_shell(f"cd {SRC} && git pull --ff-only && git submodule update --init --recursive", "source.log", "更新 AzerothCore 源码")
        return run_shell(f"git clone --depth 1 --branch master https://github.com/azerothcore/azerothcore-wotlk.git {SRC} && cd {SRC} && git submodule update --init --recursive", "source.log", "拉取 AzerothCore 源码")

    if action == "init_database":
        pw = mysql_root_password().replace("'", "'\\''")
        apw = acore_password().replace("'", "'\\''")
        sql = (
            "CREATE DATABASE IF NOT EXISTS acore_auth DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            "CREATE DATABASE IF NOT EXISTS acore_characters DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            "CREATE DATABASE IF NOT EXISTS acore_world DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            f"CREATE USER IF NOT EXISTS 'acore'@'localhost' IDENTIFIED BY '{apw}';"
            f"CREATE USER IF NOT EXISTS 'acore'@'127.0.0.1' IDENTIFIED BY '{apw}';"
            "GRANT ALL PRIVILEGES ON acore_auth.* TO 'acore'@'localhost';"
            "GRANT ALL PRIVILEGES ON acore_characters.* TO 'acore'@'localhost';"
            "GRANT ALL PRIVILEGES ON acore_world.* TO 'acore'@'localhost';"
            "GRANT ALL PRIVILEGES ON acore_auth.* TO 'acore'@'127.0.0.1';"
            "GRANT ALL PRIVILEGES ON acore_characters.* TO 'acore'@'127.0.0.1';"
            "GRANT ALL PRIVILEGES ON acore_world.* TO 'acore'@'127.0.0.1';"
            "FLUSH PRIVILEGES;"
        )
        return run_shell(f"mysql -uroot -p'{pw}' -e {json.dumps(sql)}", "database.log", "初始化 MySQL 数据库与 acore 用户")

    if action == "configure_core":
        BUILD.mkdir(parents=True, exist_ok=True)
        cmd = f"cd {BUILD} && cmake {SRC} -DCMAKE_INSTALL_PREFIX={INSTALL} -DCMAKE_BUILD_TYPE=RelWithDebInfo -DTOOLS=1 -DSCRIPTS=static"
        return run_shell(cmd, "cmake.log", "生成 CMake 构建配置")

    if action == "build_core":
        jobs = int(payload.get("jobs") or 4)
        if payload.get("cmake_first"):
            run_shell(f"cd {BUILD} && cmake {SRC} -DCMAKE_INSTALL_PREFIX={INSTALL} -DCMAKE_BUILD_TYPE=RelWithDebInfo -DTOOLS=1 -DSCRIPTS=static", "cmake.log", "编译前重新 CMake")
        cmd = f"cd {BUILD} && make -j{jobs} && make install"
        return run_shell(cmd, "build.log", f"编译并安装服务端，线程：{jobs}")

    if action == "prepare_configs":
        return prepare_configs_impl()

    if action == "download_official_data":
        candidate = Path(payload.get("uploaded_zip") or DOWNLOADS / "data.zip")
        if candidate.exists():
            return unpack_data_zip(candidate)
        append_file(log_path("data_download.log"), f"{now()} 未发现 {candidate}。请上传 data.zip 或放入 downloads/data.zip。")
        return {"ok": False, "message": "未配置官方地图数据下载源。请上传 data.zip。"}

    if action == "install_module":
        payload = normalize_module_payload(payload)
        url = payload.get("module_url")
        rebuild = bool(payload.get("rebuild", False))
        if not url:
            raise ValueError("缺少 module_url")
        MODULES_DIR.mkdir(parents=True, exist_ok=True)
        name = payload.get("name") or Path(url.rstrip("/").replace(".git", "")).name
        dest = MODULES_DIR / name
        if dest.exists() and (dest / ".git").exists():
            r = run_shell(f"cd {dest} && git pull --ff-only", f"module_{name}.log", f"更新 C++ 模块：{name}")
        elif dest.exists():
            backup = backup_existing(dest, "modules")
            r = run_shell(f"git clone {json.dumps(url)} {dest}", f"module_{name}.log", f"备份旧目录并重新安装 C++ 模块：{name}，备份：{backup}")
        else:
            r = run_shell(f"git clone {json.dumps(url)} {dest}", f"module_{name}.log", f"安装 C++ 模块：{name}")
        if rebuild:
            cr = do_action("configure_core", {})
            if not cr.get("ok"):
                return cr
            br = do_action("build_core", {})
            if not br.get("ok"):
                return br
        return r

    if action == "run_sql":
        db = payload.get("database") or "acore_world"
        sql = payload.get("sql") or ""
        if not sql.strip():
            raise ValueError("SQL 为空")
        dangerous = re.search(r'\b(DROP|TRUNCATE)\b', sql, flags=re.I)
        if dangerous:
            raise ValueError("检测到 DROP/TRUNCATE，高风险 SQL 默认拒绝执行")
        pw = mysql_root_password().replace("'", "'\\''")
        sql_file = TASKS / f"sql_{stamp()}.sql"
        sql_file.write_text(sql, encoding="utf-8")
        return run_shell(f"mysql -uroot -p'{pw}' {db} < {sql_file}", "sql.log", f"执行 SQL：{db}")

    if action == "write_lua":
        rel = payload.get("path") or "script.lua"
        content = payload.get("content") or ""
        if not rel.endswith(".lua"):
            raise ValueError("Lua 文件必须以 .lua 结尾")
        dest = LUA_DIR / Path(rel).name
        backup = backup_existing(dest, "lua")
        dest.write_text(content, encoding="utf-8")
        append_file(log_path("data_upload.log"), f"{now()} 写入 Lua：{dest}，旧文件备份：{backup}")
        return {"ok": True, "dest": str(dest), "backup": str(backup) if backup else None}

    if action == "upload_lua_script":
        src_file = payload.get("src_file")
        if not src_file:
            raise ValueError("缺少 src_file")
        return upload_lua_script(src_file)

    if action == "install_lua_from_url":
        return install_lua_from_url(payload.get("url"), payload.get("filename"))


    if action == "generate_launcher":
        return generate_launcher_impl(payload)

    if action == "start_all":
        return start_all_impl()

    if action == "stop_all":
        return stop_all_impl(payload.get("countdown") or 0)

    if action == "restart_all":
        return restart_all_impl(payload.get("countdown") or 0)

    if action == "restart_world":
        return restart_world_impl(payload.get("countdown") or 0)

    raise ValueError(f"未实现动作：{action}")

def analyze_failure_with_ai(context):
    try:
        import hermes_client
        return hermes_client.analyze_failure(context).get("reply", "")
    except Exception as e:
        return f"AI 分析失败：{e}"

def execute_action(action, payload=None):
    ensure_dirs()
    payload = payload or {}
    if action == "install_module":
        payload = normalize_module_payload(payload)
    TASK.start(action, f"执行动作：{action}", total=1, payload=payload)
    try:
        result = do_action(action, payload)
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        TASK.progress(1, 1, "已完成" if ok else "执行失败")
        if ok:
            TASK.finish(ok=True, result=result)
        else:
            mode = current_mode()
            analysis = None
            if mode in {"cooperative", "autonomous"}:
                analysis = analyze_failure_with_ai({"action": action, "payload": payload, "result": result, "task_log": tail_text(TASK_LOG)})
                TASK.block(str(result), failed_action={"action": action, "args": payload}, ai_analysis=analysis)
            else:
                TASK.finish(ok=False, result=result, error=str(result))
        return result
    except Exception as e:
        mode = current_mode()
        if mode in {"cooperative", "autonomous"}:
            analysis = analyze_failure_with_ai({"action": action, "payload": payload, "error": str(e), "task_log": tail_text(TASK_LOG)})
            TASK.block(str(e), failed_action={"action": action, "args": payload}, ai_analysis=analysis)
        else:
            TASK.finish(ok=False, error=str(e))
        return {"ok": False, "error": str(e)}

def execute_plan(actions):
    ensure_dirs()
    pf = preflight_actions(actions)
    normalized = pf["actions"]
    if not pf["ok"]:
        TASK.start("execute_plan", "方案预检失败", total=len(normalized), payload={"actions": normalized, "preflight": pf})
        TASK.block("方案预检未通过：" + "; ".join(pf["issues"]), remaining=normalized)
        return {"ok": False, "preflight": pf}

    TASK.start("execute_plan", "执行方案", total=len(normalized), payload={"actions": normalized, "preflight": pf})
    if pf.get("fixes"):
        TASK.log("[方案预检] 自动修正：" + "；".join(pf["fixes"]))
    if pf.get("high_risk"):
        TASK.log("[方案预检] 存在高风险动作：" + json.dumps(pf["high_risk"], ensure_ascii=False))

    results = []
    completed = []
    try:
        for idx, item in enumerate(normalized, start=1):
            if stop_requested():
                raise RuntimeError("执行方案已被中断")
            action = item.get("action")
            payload = item.get("args") or {}
            TASK.progress(idx - 1, len(normalized), f"准备执行：{action}")
            TASK.log(f"[{now()}] === 执行 {idx}/{len(normalized)}：{action}，风险：{action_risk(action, payload)} ===")
            result = do_action(action, payload)
            results.append({"action": action, "args": payload, "result": result})
            ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
            if not ok:
                failed = {"action": action, "args": payload, "result": result, "index": idx}
                remaining = normalized[idx-1:]
                mode = current_mode()
                analysis = None
                if mode in {"cooperative", "autonomous"}:
                    analysis = analyze_failure_with_ai({
                        "failed": failed,
                        "completed": completed,
                        "remaining": remaining,
                        "task_log": tail_text(TASK_LOG),
                        "runtime": runtime_status(),
                    })
                TASK.block(f"动作失败：{action}", failed_action=failed, ai_analysis=analysis, completed=completed, remaining=remaining)
                return {"ok": False, "results": results, "blocked": True, "failed": failed, "remaining": remaining, "ai_analysis": analysis}
            completed.append({"action": action, "args": payload, "result": result})
            TASK.progress(idx, len(normalized), f"完成：{action}")
        TASK.finish(ok=True, result=results)
        return {"ok": True, "results": results}
    except Exception as e:
        remaining = normalized[len(completed):]
        mode = current_mode()
        analysis = None
        if mode in {"cooperative", "autonomous"}:
            analysis = analyze_failure_with_ai({
                "error": str(e),
                "completed": completed,
                "remaining": remaining,
                "task_log": tail_text(TASK_LOG),
                "runtime": runtime_status(),
            })
            TASK.block(str(e), ai_analysis=analysis, completed=completed, remaining=remaining)
            return {"ok": False, "results": results, "blocked": True, "error": str(e), "remaining": remaining, "ai_analysis": analysis}
        TASK.finish(ok=False, result=results, error=str(e))
        return {"ok": False, "results": results, "error": str(e)}

def continue_remaining():
    data = TASK.get()
    remaining = data.get("remaining_actions") or []
    if not remaining:
        return {"ok": False, "message": "没有可续跑的剩余动作。"}
    return execute_plan(remaining)
