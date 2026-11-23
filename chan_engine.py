#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chan.py（MIT 许可）引擎适配层。

把 Hyperliquid 格式的 CSV 喂给 chan.py，输出与推送层兼容的 JSON。
只输出**已确认**（is_sure）的买卖点——未确认的结构会随行情变化而消失，不推。

用法：
    python chan_engine.py data.csv --symbol BTC
"""

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

BSP_CN = {
    "1": "第一类{}点",
    "1p": "第一类{}点(盘整背驰)",
    "2": "第二类{}点",
    "2s": "第二类{}点(类二)",
    "3a": "第三类{}点",
    "3b": "第三类{}点",
}

CONF = {
    "bi_strict": True,
    "trigger_step": False,
    "divergence_rate": float("inf"),
    "min_zs_cnt": 0,
    "bs_type": "1,2,3a,1p,2s,3b",
    "macd_algo": "peak",
    "zs_algo": "normal",
    "print_warning": False,
}


def locate_chanpy():
    cands = []
    env = (os.environ.get("CHANPY_DIR") or "").strip()
    if env:
        cands.append(env)
    cands += [
        os.path.join(HERE, "vendor", "chan.py"),
        os.path.join(HERE, "chanpy"),
        os.path.join(os.path.dirname(HERE), "vendor", "chan.py"),
    ]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "Chan.py")):
            return c
    raise RuntimeError("找不到 chan.py 目录，试过：%s" % " | ".join(cands))


def load_klus(path, CKLine_Unit, CTime, DATA_FIELD):
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ts = (r.get("date") or "").strip()
            if len(ts) < 16:
                continue
            out.append(CKLine_Unit({
                DATA_FIELD.FIELD_TIME: CTime(
                    int(ts[0:4]), int(ts[5:7]), int(ts[8:10]),
                    int(ts[11:13]), int(ts[14:16]), auto=False),
                DATA_FIELD.FIELD_OPEN: float(r["open"]),
                DATA_FIELD.FIELD_HIGH: float(r["high"]),
                DATA_FIELD.FIELD_LOW: float(r["low"]),
                DATA_FIELD.FIELD_CLOSE: float(r["close"]),
            }))
    return out


def fmt_t(t):
    return "%04d-%02d-%02d %02d:%02d" % (t.year, t.month, t.day, t.hour, t.minute)


def fmt_px(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return ("%.1f" % v) if abs(v) >= 1000 else ("%.2f" % v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--symbol", default="BTC")
    args = ap.parse_args()

    chanpy = locate_chanpy()
    sys.path.insert(0, chanpy)

    from Chan import CChan
    from ChanConfig import CChanConfig
    from Common.CEnum import DATA_FIELD, DATA_SRC, KL_TYPE
    from Common.CTime import CTime
    from KLine.KLine_Unit import CKLine_Unit

    class CChanFeed(CChan):
        def load(self, step=False):
            return
            yield

    klus = load_klus(args.csv, CKLine_Unit, CTime, DATA_FIELD)
    if len(klus) < 100:
        raise RuntimeError("K线仅 %d 根，不足以形成笔与中枢" % len(klus))

    chan = CChanFeed(code=args.symbol, data_src=DATA_SRC.CSV,
                     lv_list=[KL_TYPE.K_15M], config=CChanConfig(CONF))
    chan.trigger_load({KL_TYPE.K_15M: klus})
    kl = chan[0]

    confirmed, pending = [], []
    for b in kl.bs_point_lst.bsp_iter():
        sure = bool(getattr(b.bi, "is_sure", False))
        side = "买" if b.is_buy else "卖"
        kinds = []
        for t in b.type:
            tpl = BSP_CN.get(t.value)
            if tpl:
                k = tpl.format(side)
                if k not in kinds:
                    kinds.append(k)
        if not kinds:
            kinds = ["%s点(%s)" % (side, b.type2str())]
        t = b.klu.time
        rec = {
            "kind": " / ".join(kinds),
            "type": "bottom" if b.is_buy else "top",
            "price": float(b.klu.close),
            "date": fmt_t(t),
            "sure": sure,
        }
        (confirmed if sure else pending).append(rec)

    confirmed.sort(key=lambda x: x["date"])
    pending.sort(key=lambda x: x["date"])

    zs_txt = ""
    if len(kl.zs_list):
        z = kl.zs_list[-1]
        zs_txt = "区间 [%.4f, %.4f] 中轴 %.4f" % (
            z.low, z.high, (z.low + z.high) / 2.0)
        if not getattr(z, "is_sure", True):
            zs_txt += "（未确认）"

    bi_txt = ""
    if len(kl.bi_list):
        bi = kl.bi_list[-1]
        up = bi.is_up()
        b_t, e_t = bi.get_begin_klu().time, bi.get_end_klu().time
        b_px = bi._low() if up else bi._high()
        e_px = bi._high() if up else bi._low()
        bi_txt = "%s笔,由 %s(%.4f) 到 %s(%.4f),长度 %.4f" % (
            "向上" if up else "向下", fmt_t(b_t), b_px, fmt_t(e_t), e_px, abs(e_px - b_px))

    result = {
        "engine": "chan.py (MIT)",
        "summary": {
            "标的": args.symbol,
            "周期": "15m",
            "K线数": len(klus),
            "包含处理后K线": len(kl.lst),
            "有效笔数": len(kl.bi_list),
            "中枢数": len(kl.zs_list),
            "最新收盘": float(klus[-1].close),
            "最近中枢": zs_txt,
            "当前笔": bi_txt,
            "已确认买卖点": len(confirmed),
            "未确认买卖点": len(pending),
        },
        "buy_sell": confirmed,
        "pending": pending,
        "signals": [],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("[chan_engine] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        sys.exit(1)
