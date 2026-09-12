"""
app.py — VoteStrategy TradingView-style 客户端
- Flask web 服务
- SSE 推送实时信号
- REST 接口: K线/指标/信号
"""
import os
import sys
import json
import time
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
import pandas as pd
import numpy as np
from flask import Flask, Response, render_template, jsonify, request, stream_with_context

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from vote import VoteSignal, vote
from fetcher import fetch_indicators, fetch_indicators_incremental

STRATEGY_DIR = Path(__file__).parent
CACHE_DIR = STRATEGY_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

SYMBOL = "BTCUSDT"
START_DATE = "2024-01-01"
FETCH_INTERVAL_SEC = 3600
LAST_N_BARS_DISPLAY = 200
FETCH_LOOKBACK_DAYS = 7

# ── 状态 ──
STATE = {
    "kline": None,
    "indicators": {},
    "signals": None,
    "last_update": None,
    "last_signal_value": "flat",
}

# ── SSE 客户端队列 ──
SSE_CLIENTS: list = []
SSE_LOCK = threading.Lock()


def _broadcast_sse(event: str, data: dict):
    """广播到所有 SSE 客户端"""
    msg = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
    with SSE_LOCK:
        dead = []
        for q in SSE_CLIENTS:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            SSE_CLIENTS.remove(q)


def _merge_indicators_to_df(kline: pd.DataFrame,
                            indicators: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    df = kline.copy()
    for name, ind in indicators.items():
        if ind is None or len(ind) == 0:
            df[name] = np.nan
            continue
        s = ind.iloc[:, 0]
        s = s.reindex(df.index, method="ffill")
        df[name] = s
    return df


def _update_signals(df: pd.DataFrame) -> pd.DataFrame:
    """对 df 每一根 K 线跑 vote, 加状态机"""
    signals = []
    n = len(df)
    streak = 0
    streak_dir = 0
    last_signal = "flat"
    for i in range(n):
        if i < 5:
            signals.append({"signal": "flat", "conf": 0.0, "raw": 0})
            continue
        sub = df.iloc[: i + 1]
        inds = {col: list(sub[col].dropna().values[-10:])
                for col in ["HC", "MS", "WH", "TI", "LM"]}
        for k in inds:
            inds[k] = [v if pd.notna(v) else 0.0 for v in inds[k]]
        raw, conf, _ = vote(inds)
        if raw == streak_dir and raw != 0:
            streak += 1
        else:
            streak_dir = raw
            streak = 1 if raw != 0 else 0
        if streak >= 3 and conf >= 0.3:
            sig = "long" if raw > 0 else "short"
        elif conf < 0.3:
            sig = "uncertain"
        else:
            sig = "flat"
        if sig != last_signal and sig in ("long", "short"):
            # 新信号出现 — 推送
            _broadcast_sse("signal", {
                "time": str(df.index[i]),
                "signal": sig,
                "conf": float(conf),
                "raw": int(raw),
                "close": float(df["Close"].iloc[i]),
            })
            last_signal = sig
        elif sig == "flat" and last_signal in ("long", "short"):
            last_signal = "flat"

        signals.append({"signal": sig, "conf": conf, "raw": raw})

    STATE["last_signal_value"] = last_signal
    return pd.DataFrame(signals, index=df.index)


def _do_full_update():
    print(f"[{datetime.now()}] [FULL] 拉历史指标 ({START_DATE} ~)")
    indicators = fetch_indicators(SYMBOL, START_DATE)
    STATE["kline"] = indicators.pop("kline")
    STATE["indicators"] = indicators
    STATE["last_update"] = datetime.now()

    df = _merge_indicators_to_df(STATE["kline"], STATE["indicators"])
    STATE["signals"] = _update_signals(df)

    _broadcast_sse("update", {
        "type": "full",
        "time": str(STATE["last_update"]),
        "n_bars": len(STATE["kline"]),
        "indicators": {k: len(v) if v is not None else 0
                       for k, v in STATE["indicators"].items()},
    })
    print(f"[{datetime.now()}] [FULL OK] {len(STATE['kline'])} 根 K 线")


def _do_incremental_update():
    print(f"[{datetime.now()}] [INCR] 拉最近 {FETCH_LOOKBACK_DAYS} 天")
    try:
        new_inds = fetch_indicators_incremental(SYMBOL, lookback_days=FETCH_LOOKBACK_DAYS)
        new_kline = new_inds.pop("kline")
        old_k = STATE["kline"]
        if old_k is not None:
            combined = pd.concat([old_k, new_kline])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            STATE["kline"] = combined
        else:
            STATE["kline"] = new_kline
        for k, v in new_inds.items():
            if v is not None and len(v) > 0:
                if k in STATE["indicators"] and STATE["indicators"][k] is not None:
                    old_v = STATE["indicators"][k]
                    merged = pd.concat([old_v, v])
                    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    STATE["indicators"][k] = merged
                else:
                    STATE["indicators"][k] = v
        STATE["last_update"] = datetime.now()

        df = _merge_indicators_to_df(STATE["kline"], STATE["indicators"])
        STATE["signals"] = _update_signals(df)

        _broadcast_sse("update", {
            "type": "incremental",
            "time": str(STATE["last_update"]),
            "n_bars": len(STATE["kline"]),
        })
        print(f"[{datetime.now()}] [INCR OK] {len(STATE['kline'])} 根 K 线, 最新: {STATE['kline'].index[-1]}")
    except Exception as e:
        print(f"[{datetime.now()}] [INCR FAIL] {e}")
        _broadcast_sse("error", {"msg": str(e)})


def _scheduler():
    """后台线程: 每 1h 增量拉取"""
    # 启动时先 sleep 30s 让 web 先起来
    time.sleep(30)
    while True:
        _do_incremental_update()
        time.sleep(FETCH_INTERVAL_SEC)


# ── Flask ──
app = Flask(__name__,
            template_folder=str(STRATEGY_DIR / "templates"),
            static_folder=str(STRATEGY_DIR / "static"))


@app.route("/")
def index():
    n = len(STATE["kline"]) if STATE["kline"] is not None else 0
    n_long = int((STATE["signals"]["signal"] == "long").sum()) if STATE["signals"] is not None else 0
    n_short = int((STATE["signals"]["signal"] == "short").sum()) if STATE["signals"] is not None else 0
    return render_template("index.html",
                           symbol=SYMBOL,
                           last_update=str(STATE["last_update"]) if STATE["last_update"] else "--",
                           n_bars=n,
                           n_long=n_long, n_short=n_short)


@app.route("/api/klines")
def api_klines():
    """K线 + 5 指标 + 信号, 最近 N 根"""
    n = int(request.args.get("n", LAST_N_BARS_DISPLAY))
    if STATE["kline"] is None:
        return jsonify({"error": "not ready"}), 503
    k = STATE["kline"].iloc[-n:]
    sig = STATE["signals"].reindex(k.index, method="ffill").tail(n) if STATE["signals"] is not None else None

    out = {
        "time": [int(t.timestamp()) for t in k.index],
        "open": k["Open"].tolist(),
        "high": k["High"].tolist(),
        "low": k["Low"].tolist(),
        "close": k["Close"].tolist(),
        "volume": k["Volume"].tolist() if "Volume" in k.columns else [],
        "indicators": {},
        "signals": [],
    }
    for name in ["HC", "MS", "WH", "TI", "LM"]:
        if name in STATE["indicators"] and STATE["indicators"][name] is not None and len(STATE["indicators"][name]) > 0:
            ind = STATE["indicators"][name].reindex(k.index, method="ffill").iloc[:, 0]
            out["indicators"][name] = [None if pd.isna(v) else float(v) for v in ind.values]
        else:
            out["indicators"][name] = [None] * len(k)

    if sig is not None:
        out["signals"] = [
            {"time": int(t.timestamp()), "signal": row["signal"],
             "conf": float(row["conf"]), "raw": int(row["raw"])}
            for t, row in sig.iterrows() if row["signal"] in ("long", "short")
        ]
    return jsonify(out)


@app.route("/api/state")
def api_state():
    return jsonify({
        "symbol": SYMBOL,
        "last_update": str(STATE["last_update"]),
        "n_bars": len(STATE["kline"]) if STATE["kline"] is not None else 0,
        "indicator_status": {k: len(v) if v is not None else 0
                             for k, v in STATE["indicators"].items()},
        "last_signal": STATE["last_signal_value"],
    })


@app.route("/sse")
def sse():
    """SSE 推送: 信号 + 状态更新"""
    def gen():
        q = queue.Queue(maxsize=100)
        with SSE_LOCK:
            SSE_CLIENTS.append(q)
        # 先发一个 hello
        yield f"event: hello\ndata: {json.dumps({'time': str(datetime.now())})}\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield msg
                except queue.Empty:
                    # 心跳
                    yield f": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with SSE_LOCK:
                if q in SSE_CLIENTS:
                    SSE_CLIENTS.remove(q)

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """手动触发刷新 (调试用)"""
    threading.Thread(target=_do_incremental_update, daemon=True).start()
    return jsonify({"status": "triggered"})


if __name__ == "__main__":
    _do_full_update()
    t = threading.Thread(target=_scheduler, daemon=True)
    t.start()
    print("=" * 60)
    print(f"VoteStrategy Client  →  http://127.0.0.1:5050")
    print(f"Symbol: {SYMBOL}  |  K线: {START_DATE} ~ now  |  Refresh: {FETCH_INTERVAL_SEC}s")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False, threaded=True)
