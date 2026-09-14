# -*- coding: utf-8 -*-
"""
event-radar 每日快照脚本
由 GitHub Actions 在交易日 9:15 / 15:40（北京时间）运行。
抓取：指数（腾讯）、板块资金流（东财 delay 节点）、涨跌停（东财）→ snapshot.json
全部失败容忍：单项失败跳过，不阻塞整体。
"""
import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)
BASE = Path(__file__).resolve().parent.parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def fetch(url: str, retries: int = 2, timeout: int = 15):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if i == retries - 1:
                print(f"  [WARN] {url[:70]}... {e}")
                return None
            time.sleep(3)
    return None


def fetch_gbk(url: str, retries: int = 2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout_sec()) as r:
                return r.read().decode("gbk", errors="ignore")
        except Exception as e:
            if i == retries - 1:
                print(f"  [WARN] {url[:70]}... {e}")
                return None
            time.sleep(3)
    return None


def timeout_sec():
    return 15


def fetch_indices() -> dict:
    """腾讯：三大指数"""
    raw = fetch_gbk("https://qt.gtimg.cn/q=sh000001,sz399001,sh000300")
    if not raw:
        return {}
    result = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', raw):
        f = m.group(2).split("~")
        if len(f) < 40:
            continue
        try:
            price, prev = float(f[3]), float(f[4])
            result[f[2]] = {"name": f[1], "price": price, "pct": (price - prev) / prev * 100 if prev else 0}
        except (ValueError, ZeroDivisionError):
            continue
    return result


def fetch_sector(top_n: int = 10) -> list:
    """东财 delay 节点：行业主力净流入 TOP"""
    for host in ("push2delay.eastmoney.com", "90.push2.eastmoney.com", "push2.eastmoney.com"):
        raw = fetch(
            f"https://{host}/api/qt/clist/get?pn=1&pz={top_n}&po=1&np=1&fltt=2&invt=2"
            f"&fid=f62&fs=m:90+t:2&fields=f14,f3,f62"
        )
        if not raw:
            continue
        try:
            data = json.loads(raw)
            rows = [
                {"name": it["f14"], "net": round(it["f62"] / 1e8, 2), "pct": it.get("f3", 0)}
                for it in (data.get("data") or {}).get("diff", [])
                if isinstance(it.get("f62"), (int, float)) and it["f62"] > 0
            ]
            if rows:
                return rows
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return []


def fetch_limit() -> dict:
    """东财：涨跌停家数"""
    ds = NOW.strftime("%Y%m%d")

    def count(kind):
        raw = fetch(
            f"https://push2ex.eastmoney.com/getTopic{kind}Pool?ut=7eea3edcaed734bea9cbfc24409ed989"
            f"&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort=fbt%3Aasc&date={ds}"
        )
        if not raw:
            return 0
        try:
            return (json.loads(raw).get("data") or {}).get("tc", 0)
        except json.JSONDecodeError:
            return 0

    return {"up": count("ZT"), "down": count("DT"), "date": NOW.strftime("%m%d %H:%M")}


def fetch_event_quotes() -> list:
    """腾讯：事件股票池行情（埋伏池快照）"""
    data_file = BASE / "data.json"
    if not data_file.exists():
        return []
    try:
        events = json.loads(data_file.read_text(encoding="utf-8")).get("events", [])
    except json.JSONDecodeError:
        return []
    codes, name_ev = [], {}
    for ev in events:
        for s in ev.get("stocks", []):
            code = s.get("code") if isinstance(s, dict) else None
            if code:
                codes.append(code)
                name_ev[code] = {"name": s.get("name", ""), "event": ev.get("name", "")}
    if not codes:
        return []
    raw = fetch_gbk("https://qt.gtimg.cn/q=" + ",".join(codes))
    if not raw:
        return []
    rows = []
    for m in re.finditer(r'v_(\w+)="([^"]*)"', raw):
        code, f = m.group(1), m.group(2).split("~")
        if len(f) < 40:
            continue
        try:
            price, prev = float(f[3]), float(f[4])
            pct = (price - prev) / prev * 100 if prev else 0
            info = name_ev.get(code, {})
            rows.append({
                "name": info.get("name") or f[1],
                "event": info.get("event", ""),
                "price": price,
                "pct": round(pct, 2),
            })
        except (ValueError, ZeroDivisionError):
            continue
    return rows


def main():
    print(f"[{NOW:%Y-%m-%d %H:%M:%S}] 快照生成开始")
    snap = {
        "updated": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "type": "pre-open" if NOW.hour < 10 else "close",
        "idx": fetch_indices(),
        "sector": fetch_sector(),
        "limit": fetch_limit(),
        "ambush": fetch_event_quotes(),
    }
    print(f"  指数 {len(snap['idx'])} | 板块 {len(snap['sector'])} | "
          f"涨停 {snap['limit'].get('up')} | 埋伏池 {len(snap['ambush'])}")

    out = BASE / "snapshot.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  写入 {out}")


if __name__ == "__main__":
    main()
