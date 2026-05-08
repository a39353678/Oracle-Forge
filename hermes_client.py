import json
import re
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None

try:
    import requests
except Exception:
    requests = None

BASE = Path("/opt/oracle-forge")
CONFIG = BASE / "config.yaml"

PROVIDER_DEFAULT_BASE = {
    "DeepSeek": "https://api.deepseek.com/v1",
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "自定义 OpenAI 兼容接口": "",
}

DEFAULT_CONTEXT_LIMIT = 64000

def load_config():
    if not CONFIG.exists():
        return {}
    try:
        if yaml:
            return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}

def model_config():
    cfg = load_config()
    m = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    provider = m.get("provider") or cfg.get("provider") or "DeepSeek"
    model = m.get("model") or cfg.get("model_name") or cfg.get("model") or "deepseek-v4-pro"
    if isinstance(model, dict):
        model = model.get("model") or "deepseek-v4-pro"
    base_url = m.get("base_url") or cfg.get("base_url") or PROVIDER_DEFAULT_BASE.get(provider, "")
    api_key = m.get("api_key") or cfg.get("api_key") or ""
    context_limit = int(m.get("context_limit") or cfg.get("context_limit") or DEFAULT_CONTEXT_LIMIT)
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "context_limit": context_limit,
        "display": f"{provider} / {model}",
        "configured": bool(api_key and model and provider),
    }

SYSTEM_PROMPT = """你是 Oracle Forge 神谕台的 AI 助手，服务对象是 AzerothCore 单机服玩家和服主。

原则：
1. 平时自由回答，不要每次强制输出执行方案。
2. 只有用户要求生成方案，或前端调用生成方案接口时，才输出正式执行方案。
3. 不暴露隐藏 chain-of-thought。需要展示思考时，只输出可公开、可审计的【结论】【分析过程】【判断依据】【下一步建议】【动作计划】。
4. 不允许建议任意 shell 执行。只能使用白名单动作：
init_source, init_database, configure_core, build_core, prepare_configs, download_official_data, install_module, run_sql, write_lua, upload_lua_script, install_lua_from_url, start_all, stop_all, restart_all, restart_world。
5. Lua 相关必须纠错：
- 不要默认说 -DELUNA=1。
- 不要默认安装 mod-eluna。
- AzerothCore Lua 方案要区分：A. 官方 ALE，B. mod-eluna，C. 只是上传 Lua 脚本。
- 默认推荐官方 ALE：git clone https://github.com/azerothcore/mod-ale.git mod-ale。
6. Auth 日志诊断必须区分：
- Auth 未运行；
- Auth 启动后秒退；
- Auth 运行但内部 Auth.log 未生成；
- 优先查看 Auth.screen.log，再看 authserver.conf 的 LogsDir。
7. 输出给普通用户看的内容要清楚、短句、避免堆砌代码。
"""

PLAN_PROMPT = """请根据用户需求生成 Oracle Forge 可执行方案。

输出格式必须包含：
【结论】
【分析过程】
【判断依据】
【风险提示】
【动作计划】

严禁只输出 JSON。必须先输出给用户看的中文方案正文，再在最后追加一个 JSON 代码块。

最后必须追加一个 JSON 代码块，格式如下：
```json
{
  "actions": [
    {
      "action": "prepare_configs",
      "args": {}
    }
  ]
}
```

动作只能来自白名单：
init_source, init_database, configure_core, build_core, prepare_configs, download_official_data, install_module, run_sql, write_lua, upload_lua_script, install_lua_from_url, start_all, stop_all, restart_all, restart_world。

注意：
- 安装 C++ 模块必须用 install_module，并在 args 中提供 module_url。
- 可以兼容 name/module_name，但标准字段应为 name。
- Lua 脚本 URL 安装用 install_lua_from_url。
- 涉及停服/重启/SQL/编译必须明确风险。
"""

def estimate_tokens_text(text):
    if not text:
        return 0
    chinese = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    other = max(0, len(text) - chinese)
    return int(chinese * 1.05 + other / 3.6) + 1

def estimate_messages_tokens(messages):
    total = 0
    for m in messages or []:
        total += 6 + estimate_tokens_text(m.get("content", ""))
    return total

def usage_stats(messages, response_text="", real_usage=None):
    mc = model_config()
    prompt_est = estimate_messages_tokens(messages)
    completion_est = estimate_tokens_text(response_text)
    if real_usage:
        prompt_est = real_usage.get("prompt_tokens", prompt_est)
        completion_est = real_usage.get("completion_tokens", completion_est)
    total = prompt_est + completion_est
    limit = mc["context_limit"] or DEFAULT_CONTEXT_LIMIT
    return {
        "prompt_tokens": prompt_est,
        "completion_tokens": completion_est,
        "total_tokens": total,
        "context_limit": limit,
        "context_percent": min(100, round(prompt_est / limit * 100, 1)) if limit else 0,
        "is_estimated": not bool(real_usage),
    }

def call_openai_compatible(messages, temperature=0.3):
    mc = model_config()
    if not requests:
        return {"ok": False, "reply": "当前 Python 环境缺少 requests，无法调用模型。", "usage": usage_stats(messages)}
    if not mc["api_key"]:
        return {"ok": False, "reply": f"当前未配置 API Key。当前模型显示为：{mc['display']}。请先在左侧模型配置保存 API Key。", "usage": usage_stats(messages)}
    if not mc["base_url"]:
        return {"ok": False, "reply": "当前服务商缺少 Base URL。自定义接口必须填写 Base URL。", "usage": usage_stats(messages)}

    url = mc["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {mc['api_key']}", "Content-Type": "application/json"}
    if mc["provider"] == "OpenRouter":
        headers["HTTP-Referer"] = "http://localhost:7860"
        headers["X-Title"] = "Oracle Forge"

    payload = {"model": mc["model"], "messages": messages, "temperature": temperature}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=160)
        if r.status_code >= 400:
            reply = f"模型接口返回错误：HTTP {r.status_code}\n{r.text[:2000]}"
            return {"ok": False, "reply": reply, "usage": usage_stats(messages, reply)}
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        usage = usage_stats(messages, reply, data.get("usage"))
        return {"ok": True, "reply": reply, "usage": usage}
    except Exception as e:
        reply = f"模型调用失败：{e}"
        return {"ok": False, "reply": reply, "usage": usage_stats(messages, reply)}

def chat(user_message, history=None):
    history = history or []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-24:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return call_openai_compatible(messages)

def generate_plan(user_message, history=None):
    history = history or []
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + PLAN_PROMPT}]
    for item in history[-24:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return call_openai_compatible(messages, temperature=0.2)

def analyze_failure(context):
    prompt = """请分析 Oracle Forge 执行受阻问题，并输出：
【结论】
【失败步骤】
【根因判断】
【可自动修复性】
【修复建议】
【是否需要用户确认】

上下文如下：
""" + json.dumps(context, ensure_ascii=False, indent=2)[-24000:]
    return chat(prompt, [])

def generate_handoff(history, runtime=None, task=None, length="short"):
    target = "800-1500字" if length == "short" else "3000-6000字"
    prompt = f"""请为 Oracle Forge 神谕台生成新会话交接词，长度约 {target}。

必须包含：
【项目名称】
【当前目标】
【当前环境】
【已经完成】
【用户明确偏好】
【当前问题】
【当前执行模式】
【当前任务状态】
【最近失败/受阻】
【未完成事项】
【下一步建议】
【重要安全边界】

会话历史：
{json.dumps(history[-30:], ensure_ascii=False, indent=2)}

运行状态：
{json.dumps(runtime or {}, ensure_ascii=False, indent=2)[:12000]}

任务状态：
{json.dumps(task or {}, ensure_ascii=False, indent=2)[:12000]}
"""
    return chat(prompt, [])

def test_connection():
    mc = model_config()
    if not mc["api_key"]:
        return {"ok": False, "message": "未填写 API Key", "current_model": mc["display"]}
    res = call_openai_compatible([
        {"role": "system", "content": "只回复 OK。"},
        {"role": "user", "content": "ping"}
    ], temperature=0)
    reply = res.get("reply", "")
    fail_words = ["HTTP ", "失败", "错误", "未配置", "缺少", "无法调用", "API Key", "Base URL"]
    ok = bool(res.get("ok")) and not any(w in reply for w in fail_words)
    return {"ok": ok, "message": reply[:500], "current_model": mc["display"], "usage": res.get("usage")}
