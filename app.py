import json
import re
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

try:
    import yaml
except Exception:
    yaml = None

import runner
import hermes_client

BASE = Path("/opt/oracle-forge")
STATIC = BASE / "static"
CONFIG = BASE / "config.yaml"
UPLOAD_TMP = BASE / "workspace" / "downloads" / "uploads"
CONV_DIR = BASE / "workspace" / "conversations"
HANDOFF_DIR = CONV_DIR / "handoffs"

app = FastAPI(title="Oracle Forge 神谕台")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

RUN_LOCK = threading.Lock()

PROVIDERS = {
    "DeepSeek": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
    "OpenAI": ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.1"],
    "OpenRouter": ["openai/gpt-5.5", "deepseek/deepseek-v4-pro", "google/gemini-3-pro", "anthropic/claude-sonnet-4.5"],
    "自定义 OpenAI 兼容接口": ["custom"],
}

DEFAULT_BASE = {
    "DeepSeek": "https://api.deepseek.com/v1",
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "自定义 OpenAI 兼容接口": "",
}

MODE_OPTIONS = [
    {"value": "stable", "label": "稳控模式", "desc": "失败即停止，只显示错误和日志。"},
    {"value": "cooperative", "label": "协同模式", "desc": "失败后自动发给 AI 分析，生成修复建议，继续执行前需要确认。"},
    {"value": "autonomous", "label": "自治模式", "desc": "低风险错误自动修复并继续；中高风险暂停确认。"},
]

def load_config():
    if not CONFIG.exists():
        return {}
    try:
        if yaml:
            return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if yaml:
        CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def current_model():
    return hermes_client.model_config()

def extract_actions(text):
    if not text:
        return []
    blocks = re.findall(r"```json\s*(.*?)```", text, flags=re.S | re.I)
    candidates = blocks + [text]
    for raw in candidates:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("actions"), list):
                return data["actions"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
        m = re.search(r'\{\s*"actions"\s*:\s*\[.*?\]\s*\}', raw, flags=re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                return data.get("actions") or []
            except Exception:
                pass
    return []


def readable_plan_from_actions(actions, preflight=None):
    preflight = preflight or {}
    labels = {
        "init_source": "拉取 / 更新 AzerothCore 源码",
        "init_database": "初始化 MySQL 数据库",
        "configure_core": "生成 CMake 构建配置",
        "build_core": "编译并安装服务端",
        "prepare_configs": "修正服务端配置文件",
        "download_official_data": "导入 / 下载官方地图数据",
        "install_module": "安装 C++ 模块",
        "run_sql": "执行 SQL",
        "write_lua": "写入 Lua 脚本",
        "upload_lua_script": "上传 Lua 脚本",
        "install_lua_from_url": "从 URL 下载并安装 Lua 脚本",
        "start_all": "启动 Auth + World",
        "stop_all": "停止 Auth + World",
        "restart_all": "重启 Auth + World",
        "restart_world": "仅重启 worldserver",
    }

    risks = {}
    for r in preflight.get("risks") or []:
        risks[r.get("action")] = r.get("risk")

    lines = []
    lines.append("【结论】")
    lines.append("已生成可执行方案。模型本次只返回了动作 JSON，系统已自动转换为可读方案。")
    lines.append("")
    lines.append("【动作计划】")
    for i, item in enumerate(actions or [], start=1):
        action = item.get("action")
        args = item.get("args") or {}
        name = labels.get(action, action or "未知动作")
        risk = risks.get(action) or "待评估"
        extra = ""
        if action == "install_module":
            extra = "：" + (args.get("name") or args.get("module_name") or "") + " " + (args.get("module_url") or args.get("url") or args.get("source") or "")
        if action in {"restart_all", "restart_world", "stop_all"} and args.get("countdown"):
            extra += f"；提前通知 {args.get('countdown')} 秒"
        lines.append(f"{i}. {name}{extra}。风险等级：{risk}。")

    lines.append("")
    lines.append("【风险提示】")
    risk_text = "；".join([f"{r.get('index')}.{r.get('action')}={r.get('risk')}" for r in preflight.get("risks") or []]) or "暂无风险信息"
    lines.append(risk_text)
    if preflight.get("fixes"):
        lines.append("")
        lines.append("【自动参数修正】")
        lines.append("；".join(preflight.get("fixes") or []))
    if preflight.get("issues"):
        lines.append("")
        lines.append("【预检问题】")
        lines.append("；".join(preflight.get("issues") or []))

    lines.append("")
    lines.append("【下一步】")
    lines.append("请先点击“方案预检”，确认风险和参数无误后，再点击“确认执行”。执行后请查看下方任务中心。")
    return "\\n".join(lines)

def hide_json_blocks(text):
    return re.sub(r"```json\s*.*?```", "", text or "", flags=re.S | re.I).strip()

def run_background(func, *args):
    if RUN_LOCK.locked():
        return {"ok": False, "message": "当前已有任务执行中，请先查看任务中心或强行中断。"}
    def target():
        with RUN_LOCK:
            try:
                func(*args)
            except Exception as e:
                runner.TASK.finish(ok=False, error=str(e))
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return {"ok": True, "message": "任务已开始，请查看任务中心。"}

def save_conversation(history, title=None, handoff=None):
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "title": title or "Oracle Forge 会话",
        "history": history or [],
        "handoff": handoff,
        "current_model": current_model(),
    }
    (CONV_DIR / "current.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")

@app.get("/api/config")
def api_config():
    cfg = load_config()
    mc = current_model()
    return {"ok": True, "config": cfg, "providers": PROVIDERS, "default_base": DEFAULT_BASE, "current_model": mc, "modes": MODE_OPTIONS, "execution_mode": cfg.get("execution_mode", "cooperative")}

@app.post("/api/config/save")
async def api_config_save(payload: dict):
    cfg = load_config()
    provider = payload.get("provider") or "DeepSeek"
    model = payload.get("model") or "deepseek-v4-pro"
    base_url = payload.get("base_url") or DEFAULT_BASE.get(provider, "")
    api_key = payload.get("api_key")
    context_limit = int(payload.get("context_limit") or 64000)

    old_model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    cfg["model"] = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key if api_key is not None else old_model.get("api_key", ""),
        "context_limit": context_limit,
    }
    cfg["provider"] = provider
    cfg["model_name"] = model
    cfg["base_url"] = base_url
    cfg["context_limit"] = context_limit
    if api_key is not None:
        cfg["api_key"] = api_key
    save_config(cfg)
    return {"ok": True, "message": "模型配置已保存", "current_model": current_model()}

@app.post("/api/config/test")
def api_config_test():
    return hermes_client.test_connection()

@app.post("/api/mode/save")
async def api_mode_save(payload: dict):
    mode = payload.get("mode") or "cooperative"
    if mode not in {"stable", "cooperative", "autonomous"}:
        return {"ok": False, "message": "未知执行模式"}
    cfg = load_config()
    cfg["execution_mode"] = mode
    save_config(cfg)
    return {"ok": True, "mode": mode, "message": "执行模式已保存"}

@app.post("/api/ai/chat")
async def api_ai_chat(payload: dict):
    message = payload.get("message") or ""
    history = payload.get("history") or []
    if not message.strip():
        return {"ok": False, "reply": "消息不能为空。", "current_model": current_model()}
    res = hermes_client.chat(message, history)
    save_conversation(history + [{"role": "user", "content": message}, {"role": "assistant", "content": res.get("reply", "")}])
    return {"ok": res.get("ok", True), "reply": res.get("reply", ""), "usage": res.get("usage"), "current_model": current_model()}

@app.post("/api/hermes/plan")
async def api_plan(payload: dict):
    message = payload.get("message") or ""
    history = payload.get("history") or []
    if not message.strip():
        return {"ok": False, "reply": "生成方案前需要先输入需求。", "display_reply": "生成方案前需要先输入需求。", "actions": [], "current_model": current_model()}
    res = hermes_client.generate_plan(message, history)
    raw = res.get("reply", "")
    actions = extract_actions(raw)
    normalized = runner.preflight_actions(actions) if actions else {"actions": [], "risks": [], "fixes": [], "issues": []}
    summary = "已识别可执行动作：" + (" → ".join([a.get("action", "?") for a in normalized.get("actions", [])]) if normalized.get("actions") else "无")
    display = hide_json_blocks(raw)
    if not display.strip() and normalized.get("actions"):
        display = readable_plan_from_actions(normalized.get("actions"), normalized)
    if normalized.get("fixes"):
        summary += "\n自动参数修正：" + "；".join(normalized["fixes"])
    if normalized.get("issues"):
        summary += "\n预检问题：" + "；".join(normalized["issues"])
    return {"ok": res.get("ok", True), "reply": raw, "display_reply": display, "actions": normalized.get("actions", actions), "preflight": normalized, "action_summary": summary, "usage": res.get("usage"), "current_model": current_model()}

@app.post("/api/plan/preflight")
async def api_plan_preflight(payload: dict):
    actions = payload.get("actions") or []
    return {"ok": True, "preflight": runner.preflight_actions(actions)}

@app.post("/api/plan/execute")
async def api_execute_plan(payload: dict):
    actions = payload.get("actions") or []
    if not isinstance(actions, list) or not actions:
        return {"ok": False, "message": "当前方案没有可执行动作。请先生成带 JSON actions 的执行方案。"}
    return run_background(runner.execute_plan, actions)

@app.post("/api/plan/continue")
async def api_continue_plan():
    return run_background(runner.continue_remaining)

@app.post("/api/action/run")
async def api_action_run(payload: dict):
    action = payload.get("action")
    args = payload.get("args") or {}
    if not action:
        return {"ok": False, "message": "缺少 action"}
    return run_background(runner.execute_action, action, args)

@app.get("/api/task/current")
def api_task_current():
    return {"ok": True, "task": runner.TASK.get()}

@app.get("/api/task/log")
def api_task_log():
    return {"ok": True, "log": runner.tail_text(runner.TASK_LOG)}

@app.post("/api/task/stop")
def api_task_stop():
    return runner.request_stop()

@app.post("/api/task/analyze")
def api_task_analyze(payload: dict = None):
    task = runner.TASK.get()
    context = {"task": task, "task_log": runner.tail_text(runner.TASK_LOG), "runtime": runner.runtime_status()}
    res = hermes_client.analyze_failure(context)
    task["ai_analysis"] = res.get("reply", "")
    runner.TASK.update(ai_analysis=task["ai_analysis"])
    return {"ok": res.get("ok", True), "reply": res.get("reply", ""), "usage": res.get("usage")}

@app.get("/api/server/runtime")
def api_runtime():
    return {"ok": True, "runtime": runner.runtime_status()}

@app.get("/api/log/{name}")
def api_log(name: str):
    return {"ok": True, "name": name, "label": runner.LOG_LABELS.get(name, name), "content": runner.read_log(name)}

@app.post("/api/log/analyze")
async def api_log_analyze(payload: dict):
    name = payload.get("name") or "server.log"
    content = runner.read_log(name)
    prompt = f"请分析以下 Oracle Forge / AzerothCore 日志，按【结论】【分析过程】【判断依据】【下一步建议】输出：\n\n日志名：{name}\n\n{content[-16000:]}"
    res = hermes_client.chat(prompt, payload.get("history") or [])
    return {"ok": res.get("ok", True), "reply": res.get("reply", ""), "usage": res.get("usage"), "current_model": current_model()}

@app.post("/api/server/control")
async def api_server_control(payload: dict):
    action = payload.get("action")
    countdown = int(payload.get("countdown") or 0)
    mapping = {"start_all": "start_all", "stop_all": "stop_all", "restart_all": "restart_all", "restart_world": "restart_world"}
    if action not in mapping:
        return {"ok": False, "message": "未知服务动作"}
    return run_background(runner.execute_action, mapping[action], {"countdown": countdown})

@app.post("/api/command")
async def api_command(payload: dict):
    command = payload.get("command") or ""
    try:
        return runner.send_world_command(command)
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/upload/data-zip")
async def api_upload_data_zip(file: UploadFile = File(...)):
    UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_TMP / file.filename
    with dest.open("wb") as f:
        f.write(await file.read())
    def task():
        runner.TASK.start("upload_data_zip", "上传并解压 data.zip", total=1, payload={"file": str(dest)})
        try:
            result = runner.unpack_data_zip(dest)
            runner.TASK.progress(1, 1, "data.zip 已解压")
            runner.TASK.finish(ok=True, result=result)
        except Exception as e:
            runner.TASK.finish(ok=False, error=str(e))
    return run_background(task)

@app.post("/api/upload/file")
async def api_upload_file(file: UploadFile = File(...), target: Optional[str] = Form(None)):
    UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_TMP / file.filename
    with dest.open("wb") as f:
        f.write(await file.read())
    def task():
        runner.TASK.start("upload_data_file", "上传单个地图 / DBC 文件", total=1, payload={"file": str(dest), "target": target})
        try:
            result = runner.upload_single_file(dest, target)
            runner.TASK.progress(1, 1, "文件已上传")
            runner.TASK.finish(ok=True, result=result)
        except Exception as e:
            runner.TASK.finish(ok=False, error=str(e))
    return run_background(task)

@app.post("/api/upload/lua")
async def api_upload_lua(file: UploadFile = File(...)):
    UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_TMP / file.filename
    with dest.open("wb") as f:
        f.write(await file.read())
    def task():
        runner.TASK.start("upload_lua_script", "上传 Lua 脚本", total=1, payload={"file": str(dest)})
        try:
            result = runner.upload_lua_script(dest)
            runner.TASK.progress(1, 1, "Lua 已上传")
            runner.TASK.finish(ok=True, result=result)
        except Exception as e:
            runner.TASK.finish(ok=False, error=str(e))
    return run_background(task)

@app.post("/api/lua/install-url")
async def api_lua_install_url(payload: dict):
    url = payload.get("url")
    filename = payload.get("filename")
    if not url:
        return {"ok": False, "message": "缺少 Lua URL"}
    return run_background(runner.execute_action, "install_lua_from_url", {"url": url, "filename": filename})

@app.post("/api/module/install")
async def api_module_install(payload: dict):
    return run_background(runner.execute_action, "install_module", payload)


@app.get("/api/launcher/info")
def api_launcher_info():
    return {"ok": True, "info": runner.detect_server_ips()}

@app.post("/api/launcher/generate")
async def api_launcher_generate(payload: dict):
    if RUN_LOCK.locked():
        return {"ok": False, "message": "当前已有任务执行中，请等待完成后再生成登录器。"}
    with RUN_LOCK:
        return runner.execute_action("generate_launcher", payload)

@app.get("/api/download/launcher/{filename}")
def api_download_launcher(filename: str):
    safe = Path(filename).name
    path = runner.DOWNLOADS / "launcher" / safe
    if not path.exists():
        return {"ok": False, "message": "文件不存在"}
    return FileResponse(str(path), filename=safe, media_type="application/octet-stream")

@app.post("/api/conversation/handoff")
async def api_handoff(payload: dict):
    history = payload.get("history") or []
    length = payload.get("length") or "short"
    runtime = runner.runtime_status()
    task = runner.TASK.get()
    res = hermes_client.generate_handoff(history, runtime, task, length)
    handoff = res.get("reply", "")
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    hp = HANDOFF_DIR / f"{runner.stamp()}_handoff.md"
    hp.write_text(handoff, encoding="utf-8")
    save_conversation(history, handoff=handoff)
    return {"ok": res.get("ok", True), "handoff": handoff, "path": str(hp), "usage": res.get("usage")}

@app.post("/api/conversation/save")
async def api_conversation_save(payload: dict):
    return {"ok": True, "conversation": save_conversation(payload.get("history") or [], payload.get("title"), payload.get("handoff"))}


# ---- Oracle Forge v7.5.3 launcher routes ----
@app.get("/api/launcher/info")
def oracle_forge_launcher_info_v753():
    return {"ok": True, "info": runner.detect_server_ips()}

@app.post("/api/launcher/generate")
async def oracle_forge_launcher_generate_v753(payload: dict):
    if RUN_LOCK.locked():
        return {"ok": False, "message": "当前已有任务执行中，请等待完成后再生成登录器。"}
    with RUN_LOCK:
        return runner.execute_action("generate_launcher", payload)

@app.get("/api/download/launcher/{filename}")
def oracle_forge_download_launcher_v753(filename: str):
    safe = Path(filename).name
    path = runner.DOWNLOADS / "launcher" / safe
    if not path.exists():
        return {"ok": False, "message": "文件不存在"}
    return FileResponse(str(path), filename=safe, media_type="application/octet-stream")
# ---- /Oracle Forge v7.5.3 launcher routes ----

