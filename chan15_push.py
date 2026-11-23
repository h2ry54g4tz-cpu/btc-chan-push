#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 15m 缠论买卖点监控 → Bark 手机推送。

流程：拉 Hyperliquid 15m K线 → 缠论结构分析 → 与新信号比对 → 有新的才推 → 记录状态
用法：
    python chan15_push.py            # 正常跑，有信号则推送
    python chan15_push.py --dry-run  # 只看会推什么，不真发
    python chan15_push.py --test     # 发一条测试通知，验证 Bark 通不通
    python chan15_push.py --reset    # 清空推送记录（重跑历史信号）
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")
STATE_PATH = os.path.join(HERE, "state.json")
LOG_PATH = os.path.join(HERE, "push.log")
DATA_DIR = os.path.join(HERE, "data")

API = "https://api.hyperliquid.xyz/info"
BJ = timezone(timedelta(hours=8))

DEFAULTS = {
    "bark_key": "",
    "bark_server": "https://api.day.app",
    "bark_group": "BTC缠论15m",
    "symbol": "BTC",
    "hyperliquid_coin": "BTC",
    "days": 12,
    "lookback_hours": 8,
    "chanlun_dir": os.path.expanduser("~/.workbuddy/skills/chanlun-obays"),
    "engine": "chanpy",
    "push_kinds": ["bottom", "top", "ratio"],
}


def log(msg):
    line = "[%s] %s" % (datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    # config.json 含密钥、不进仓库；缺失时退回 config.example.json
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cfg.update(json.load(f))
            break
    # 云端跑时 key 走环境变量/Secret，不落盘
    env_key = (os.environ.get("BARK_KEY") or "").strip()
    if env_key:
        cfg["bark_key"] = env_key
    return cfg


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {"pushed": [], "bootstrapped": False}


def save_state(st):
    st["pushed"] = st.get("pushed", [])[-500:]
    st["updated_at"] = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


# ---------------- 数据 ----------------

def post(payload, timeout=20):
    req = urllib.request.Request(
        API,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_15m(coin, days):
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 86400 * 1000
    raw = post({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": "15m", "startTime": start, "endTime": now_ms},
    })
    rows = []
    for c in sorted(raw, key=lambda x: int(x["t"])):
        ts = int(c["t"])
        if ts + 15 * 60 * 1000 > now_ms:      # 丢掉仍在走的中途K线
            continue
        rows.append([
            datetime.fromtimestamp(ts / 1000, BJ).strftime("%Y-%m-%d %H:%M"),
            c["o"], c["h"], c["l"], c["c"], c["v"],
        ])
    if len(rows) < 200:
        raise RuntimeError("K线不足 %d 根，无法做结构分析" % len(rows))
    return rows


def write_csv(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "btc_15m.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    return path


# ---------------- 结构分析 ----------------

def resolve_analyzer(cfg):
    """按优先级找分析器：环境变量 → 本机技能目录 → 脚本同目录自带副本。"""
    cands = []
    env = (os.environ.get("CHANLUN_ANALYZE") or "").strip()
    if env:
        cands.append(env)
    d = (cfg.get("chanlun_dir") or "").strip()
    if d:
        cands.append(os.path.join(d, "scripts", "chanlun_analyze.py"))
        cands.append(os.path.join(d, "chanlun_analyze.py"))
    cands.append(os.path.join(HERE, "chanlun_analyze.py"))
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("找不到 chanlun_analyze.py，试过：%s" % " | ".join(cands))


def run_chanlun(csv_path, cfg):
    """按配置选择分析引擎，两者都输出同一种 JSON 契约。"""
    engine = (cfg.get("engine") or "chanpy").strip()
    if engine == "chanpy":
        entry = os.path.join(HERE, "chan_engine.py")
        argv = [sys.executable, entry, csv_path, "--symbol", cfg["symbol"]]
    else:
        entry = resolve_analyzer(cfg)
        argv = [sys.executable, entry, csv_path, "--symbol", cfg["symbol"], "--period", "15m"]
    if not os.path.exists(entry):
        raise RuntimeError("找不到分析引擎入口：%s" % entry)

    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("分析失败(%s)：%s" % (engine, (proc.stderr or proc.stdout)[:300]))
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError("分析输出不是合法 JSON：%s" % proc.stdout[:300])


def kind_bucket(kind):
    """把各式 kind 归成三类，兼容两个引擎的命名。"""
    k = kind or ""
    if "买点" in k or "底背" in k:
        return "bottom"
    if "卖点" in k or "顶背" in k:
        return "top"
    if "结构信号" in k or "幅度比" in k:
        return "ratio"
    return "other"


def collect_signals(result):
    """合并 buy_sell 与 signals，按时间点归属。"""
    seen = {}
    for src in ("buy_sell", "signals"):
        for s in result.get(src) or []:
            date = (s.get("date") or "").strip()
            kind = (s.get("kind") or "").strip()
            price = s.get("price")
            if not date or price is None:
                continue
            bucket = kind_bucket(kind)
            key = date
            item = seen.setdefault(key, {"date": date, "price": float(price), "kinds": [], "buckets": []})
            if kind not in item["kinds"]:
                item["kinds"].append(kind)
            if bucket not in item["buckets"]:
                item["buckets"].append(bucket)
    return [seen[k] for k in sorted(seen)]


def parse_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=BJ)


# ---------------- 推送 ----------------

def bark_send(cfg, title, body):
    key = (cfg.get("bark_key") or "").strip()
    if not key:
        raise RuntimeError("config.json 里 bark_key 还没填")

    # 常见误填：把 Bark 设置页的 Device Token（64位十六进制）当成 key
    head = key.rstrip("/").split("/")[-1]
    if len(head) >= 48 and all(c in "0123456789abcdefABCDEF" for c in head):
        raise RuntimeError(
            "这一串是 Apple 的 Device Token，不是 Bark 的 key。\n"
            "正确的 key 在 Bark App【首页】的示例 URL 里：https://api.day.app/AbCdEfGh/... \n"
            "域名后面那一小段（二十来个字符）才是 key。"
        )

    base = (cfg.get("bark_server") or "https://api.day.app").rstrip("/")
    # 兼容两种填法：只填 key，或直接粘完整地址 https://api.day.app/xxxxx/
    target = key.rstrip("/") if key.startswith("http") else "%s/%s" % (base, key)

    q = urllib.parse.urlencode({
        "group": cfg.get("bark_group", "BTC缠论"),
        "sound": "alarm",
        "level": "timeSensitive",
    })
    full = "%s/%s/%s?%s" % (
        target,
        urllib.parse.quote(title, safe=""),
        urllib.parse.quote(body, safe=""),
        q,
    )
    req = urllib.request.Request(full, headers={"User-Agent": "chan15-push/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if data.get("code") not in (200, None):
        raise RuntimeError("Bark 返回异常：%s" % text[:200])
    return data


def verdict(item):
    """区分「已确认结构」和「候选/未确认」，避免把弱证据当成正式买卖点。"""
    kinds = item.get("kinds") or []
    buckets = item.get("buckets") or []
    side = "买点" if "bottom" in buckets else "卖点" if "top" in buckets else "结构信号"

    if any(("候选" in k) or ("未确认" in k) for k in kinds):
        return "疑似" + side
    if any(("买点" in k) or ("卖点" in k) or ("力度衰减" in k) for k in kinds):
        return side
    return "结构信号"


def build_body(item, result, cfg):
    summary = result.get("summary") or {}
    close = summary.get("最新收盘")

    lines = [item["kinds"][0]]
    for extra in item["kinds"][1:]:
        lines.append("另：" + extra)
    lines.append("信号点位 %s" % format_price(item["price"]))
    if close:
        lines.append("现价 %s" % format_price(close))

    hub = summary.get("最近中枢") or ""
    m = re.search(r"区间\s*\[([\d.]+),\s*([\d.]+)\]", hub)
    if m:
        lines.append("中枢 %s – %s" % (format_price(m.group(1)), format_price(m.group(2))))

    bi = summary.get("当前笔") or ""
    mb = re.search(r"(向上笔|向下笔).*?\(([\d.]+)\)\s*到\s*[\d\-: ]*\(([\d.]+)\)", bi)
    if mb:
        lines.append("%s  %s→%s" % (mb.group(1), format_price(mb.group(2)), format_price(mb.group(3))))
    elif bi:
        lines.append(bi.split(",")[0])

    return "\n".join(lines)


def format_price(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if abs(v) >= 1000:
        return "{:,.0f}".format(v) if v == int(v) else "{:,.1f}".format(v)
    return "{:,.2f}".format(v)


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="BTC 15m 缠论买卖点推送")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    ap.add_argument("--test", action="store_true", help="发一条测试通知")
    ap.add_argument("--reset", action="store_true", help="清空推送记录")
    ap.add_argument("--force-all", action="store_true", help="忽略时间窗，所有新信号都推")
    args = ap.parse_args()

    cfg = load_config()

    if args.reset:
        save_state({"pushed": [], "bootstrapped": False})
        log("已清空推送记录")
        return 0

    if args.test:
        try:
            bark_send(cfg, "BTC 缠论监控 · 测试", "通道正常，这是测试消息。\n收到说明配置成功。")
            log("测试通知已发送")
            return 0
        except Exception as exc:
            log("测试失败：%s" % exc)
            return 1

    try:
        rows = fetch_15m(cfg["hyperliquid_coin"], cfg["days"])
        csv_path = write_csv(rows)
        log("K线 %d 根，末根 %s" % (len(rows), rows[-1][0]))
        result = run_chanlun(csv_path, cfg)
    except Exception as exc:
        log("数据/分析失败：%s" % exc)
        return 1

    items = collect_signals(result)
    if not items:
        log("未识别到任何信号")
        return 0

    state = load_state()
    pushed = set(state.get("pushed", []))
    cutoff = datetime.now(BJ) - timedelta(hours=cfg["lookback_hours"])

    fresh = []
    for it in items:
        # 用「时间+价格」做指纹：不受 kind 文案变化影响，也不会漏去重
        sig = "%s|%.2f" % (it["date"], it["price"])
        if sig in pushed:
            continue
        if not args.force_all and parse_dt(it["date"]) < cutoff:
            pushed.add(sig)          # 过期信号直接标记，避免以后反复判断
            continue
        if not any(b in cfg["push_kinds"] for b in it["buckets"]):
            pushed.add(sig)
            continue
        fresh.append((sig, it))

    if not state.get("bootstrapped"):
        state["bootstrapped"] = True
        state["pushed"] = sorted(pushed | {s for s, _ in fresh})
        save_state(state)
        log("首次运行：已建立基线（%d 个历史信号不推送）" % len(items))
        if not args.dry_run and (cfg.get("bark_key") or "").strip():
            try:
                bark_send(cfg, "BTC 缠论监控已启动",
                          "15m 买卖点监控开始运行。\n当前无新信号，有信号会立刻通知你。")
            except Exception as exc:
                log("启动通知失败：%s" % exc)
        return 0

    if not fresh:
        log("无新信号（已记录 %d 条）" % len(pushed))
        save_state(state)
        return 0

    ok = 0
    for sig, it in fresh:
        body = build_body(it, result, cfg)
        title = "BTC 15m %s · %s" % (verdict(it), it["date"][5:])
        if args.dry_run:
            log("[DRY] %s\n%s\n---" % (title, body))
            ok += 1
            continue
        try:
            bark_send(cfg, title, body)
            pushed.add(sig)
            ok += 1
            log("已推送 %s @ %s" % (it["kinds"][0], it["date"]))
        except Exception as exc:
            log("推送失败 %s：%s" % (it["date"], exc))

    state["pushed"] = sorted(pushed)
    save_state(state)
    log("本轮推送 %d 条" % ok)
    return 0 if ok or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
