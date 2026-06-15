"""
SET Data Fetcher v3 — Batch Download + Flask-ready
ดึงหุ้น SET ทั้งหมดด้วย yf.download() batch (~7 นาที แทน 25 นาที)

ใช้เป็น library:
    from set_data_fetcher import run_with_progress
    run_with_progress(callback, base_dir)

รันตรง:
    python set_data_fetcher.py
"""

import os
import json
import time
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
    from tqdm import tqdm
except ImportError as e:
    print(f"ติดตั้ง library ก่อน: pip install yfinance pandas openpyxl xlrd tqdm flask")
    print(f"Error: {e}")
    raise

XLS_FILE = "listedCompanies_en_US.xlsx"
OUT_FILE  = "set_data.json"


# ============================================================
# 1. อ่านรายชื่อหุ้นจากไฟล์ SET
# ============================================================

def load_set_symbols(base_dir=None):
    path = os.path.join(base_dir, XLS_FILE) if base_dir else XLS_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"ไม่พบไฟล์ {path}\n"
            "โหลดจาก: https://www.set.or.th/dat/eod/listedcompany/static/listedCompanies_en_US.xls"
        )

    df = pd.read_excel(path, header=None, engine="openpyxl")

    # หา header row
    header_row = None
    for i, row in df.iterrows():
        row_str = " ".join(str(v).lower() for v in row.values)
        if "symbol" in row_str or "market" in row_str:
            header_row = i
            break
    if header_row is None:
        raise ValueError("หา header row ไม่เจอในไฟล์ Excel")

    df.columns = df.iloc[header_row]
    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df.columns = [str(c).strip() for c in df.columns]

    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if "symbol"   in cl: col_map["symbol"]   = col
        elif "company" in cl or "name" in cl: col_map["name"] = col
        elif "market"  in cl: col_map["market"]   = col
        elif "industry" in cl: col_map["industry"] = col
        elif "sector"  in cl: col_map["sector"]   = col

    symbols = []
    for _, row in df.iterrows():
        sym = str(row.get(col_map.get("symbol", ""), "")).strip()
        if not sym or sym in ("nan", "Symbol"):
            continue
        market = str(row.get(col_map.get("market", ""), "")).strip()
        if market not in ("SET", "mai", ""):
            continue
        name     = str(row.get(col_map.get("name",     ""), "")).strip()
        industry = str(row.get(col_map.get("industry", ""), "")).strip()
        sector   = str(row.get(col_map.get("sector",   ""), "")).strip()
        _blank   = {"nan", "-", "", "N/A"}
        clean_industry = industry if industry not in _blank else "Unknown"
        clean_sector   = sector   if sector   not in _blank else None
        if clean_sector is None:
            clean_sector = (clean_industry + " -mai") if market == "mai" else "Unknown"
        symbols.append({
            "symbol":   sym,
            "ticker":   f"{sym}.BK",
            "name":     name     if name     not in _blank else sym,
            "market":   market,
            "industry": clean_industry,
            "sector":   clean_sector,
        })

    return symbols


# pattern สำหรับ กองทุนรวม / REIT / Infra Fund ใน SET
import re as _re
_FUND_PAT = _re.compile(
    r'(GIF|IF|REIT|PF|ARAF|BT|RT|MNIT\d*)$'   # ลงท้ายด้วย suffix กองทุน
    r'|^(CG)$'                                   # exact match เท่านั้น (ป้องกัน BCG ผิดพลาด)
    r'|^M-'
    r'|(LUXF|MNRF|WHAIR)$',
    _re.IGNORECASE
)

def _is_reit(symbol: str) -> bool:
    return bool(_FUND_PAT.search(symbol))


# ============================================================
# 2. คำนวณ metrics จาก Series ที่ดาวน์โหลดมาแล้ว
# ============================================================

def _calc_return(series, days):
    if len(series) < days + 1:
        return None
    try:
        past = float(series.iloc[-(days + 1)])
        now  = float(series.iloc[-1])
        if past == 0:
            return None
        return round((now - past) / past * 100, 2)
    except Exception:
        return None


def _calc_ema(series, period):
    if len(series) < period:
        return None
    try:
        return round(float(series.ewm(span=period, adjust=False).mean().iloc[-1]), 4)
    except Exception:
        return None


def process_stock(info_dict, close, volume):
    """คำนวณ metrics จาก close/volume Series — ไม่ดึงข้อมูลเพิ่ม"""
    try:
        if close is None or len(close) < 5:
            return None

        dates = close.index
        price = round(float(close.iloc[-1]), 4)

        ema20  = _calc_ema(close, 20)
        ema50  = _calc_ema(close, 50)
        ema200 = _calc_ema(close, 200)

        ret_1d = _calc_return(close, 1)
        ret_1w = _calc_return(close, 5)
        ret_1m = _calc_return(close, 21)
        ret_3m = _calc_return(close, 63)
        ret_6m = _calc_return(close, 126)
        ret_1y = _calc_return(close, 250)

        current_year = datetime.now().year
        ytd_pairs = [(d, p) for d, p in zip(dates, close) if d.year == current_year]
        ret_ytd = None
        if ytd_pairs:
            first_price = float(ytd_pairs[0][1])
            if first_price > 0:
                ret_ytd = round((price - first_price) / first_price * 100, 2)

        above_ema20  = bool(price > ema20)  if ema20  is not None else None
        above_ema50  = bool(price > ema50)  if ema50  is not None else None
        above_ema200 = bool(price > ema200) if ema200 is not None else None

        parts = [(ret_1m, 2), (ret_3m, 1), (ret_6m, 1), (ret_1y, 1)]
        valid = [(v, w) for v, w in parts if v is not None]
        rs_raw = round(sum(v * w for v, w in valid) / sum(w for _, w in valid), 4) if valid else None

        vol_20 = int(volume.tail(20).mean()) if len(volume) >= 20 else None

        price_history = [
            [d.strftime("%Y-%m-%d"), round(float(p), 2)]
            for d, p in zip(dates, close)
        ]

        return {
            "symbol":       info_dict["symbol"],
            "ticker":       info_dict["ticker"],
            "name":         info_dict["name"],
            "market":       info_dict["market"],
            "industry":     info_dict["industry"],
            "sector":       info_dict["sector"],
            "price":        price,
            "mkt_cap":      None,
            "is_reit":      _is_reit(info_dict["symbol"]),
            "ret_1d":       ret_1d,
            "ret_1w":       ret_1w,
            "ret_1m":       ret_1m,
            "ret_3m":       ret_3m,
            "ret_6m":       ret_6m,
            "ret_1y":       ret_1y,
            "ret_ytd":      ret_ytd,
            "ema20":        ema20,
            "ema50":        ema50,
            "ema200":       ema200,
            "above_ema20":  above_ema20,
            "above_ema50":  above_ema50,
            "above_ema200": above_ema200,
            "rs_raw":       rs_raw,
            "rs_score":     None,
            "vol_avg20":    vol_20,
            "high_52w":     round(float(close.tail(260).max()), 2),
            "low_52w":      round(float(close.tail(260).min()), 2),
            "pe":           None,
            "pbv":          None,
            "div_yield":    None,
            "price_history": price_history,
        }
    except Exception:
        return None


# ============================================================
# 3. Batch downloader — ดึง 100 ตัวต่อครั้ง
# ============================================================

BATCH_SIZE = 100


def fetch_all_batch(tickers, callback=None):
    """
    ดาวน์โหลดราคาทุกตัวด้วย yf.download() แบบ batch
    คืนค่า dict: ticker -> {'close': pd.Series, 'volume': pd.Series}
    """
    chunks = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    n_chunks = len(chunks)
    all_data = {}

    for ci, chunk in enumerate(chunks):
        done_so_far = ci * BATCH_SIZE
        if callback:
            callback(done_so_far, len(tickers),
                     f"ดาวน์โหลด batch {ci + 1}/{n_chunks} ({len(chunk)} หุ้น)...")

        try:
            if len(chunk) == 1:
                raw = yf.download(
                    chunk[0], period="1y", auto_adjust=True,
                    progress=False, threads=False,
                )
                if not raw.empty and len(raw) >= 5:
                    close  = raw["Close"].dropna()
                    volume = raw["Volume"].dropna()
                    if len(close) >= 5:
                        all_data[chunk[0]] = {"close": close, "volume": volume}
            else:
                raw = yf.download(
                    chunk, period="1y", auto_adjust=True,
                    progress=False, group_by="ticker", threads=True,
                )
                for tick in chunk:
                    try:
                        close  = raw[tick]["Close"].dropna()
                        volume = raw[tick]["Volume"].dropna()
                        if len(close) >= 5:
                            all_data[tick] = {"close": close, "volume": volume}
                    except Exception:
                        pass
        except Exception as e:
            print(f"  [batch {ci + 1}] error: {e}")

        time.sleep(0.3)

    return all_data


# ============================================================
# 4. Parallel fundamentals fetcher (market cap + P/E + P/BV + Div Yield)
# ============================================================

def fetch_market_caps_parallel(tickers, callback=None, workers=3):
    """ดึง market_cap + P/E + P/BV + Div Yield — sequential per-ticker เพื่อใช้ crumb เดียวกัน"""
    import random
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # สร้าง session เดียวร่วมกัน เพื่อให้ crumb ไม่หมดอายุระหว่างการดึง
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    results = {}

    def _get_fund(tick):
        time.sleep(random.uniform(0.3, 1.0))
        for attempt in range(4):
            try:
                t    = yf.Ticker(tick, session=session)
                info = t.info
                mc   = info.get("marketCap")
                pe   = info.get("trailingPE")
                pbv  = info.get("priceToBook")
                dy   = info.get("dividendYield")
                # yfinance .BK คืน % (5.83) แต่ถ้าเปลี่ยนเป็น decimal (0.0583) ให้ normalize
                if dy is not None and float(dy) < 1.0:
                    dy = float(dy) * 100
                return tick, {
                    "mkt_cap":   int(mc)          if mc  is not None else None,
                    "pe":        round(float(pe),  2) if pe  is not None else None,
                    "pbv":       round(float(pbv), 2) if pbv is not None else None,
                    "div_yield": round(float(dy),  2) if dy  is not None else None,
                }
            except Exception as e:
                err = str(e).lower()
                if "rate" in err or "too many" in err or "429" in err or "401" in err or "crumb" in err:
                    wait = (2 ** attempt) + random.uniform(1, 3)
                    time.sleep(wait)
                else:
                    return tick, {}
        return tick, {}

    total = len(tickers)
    done  = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_get_fund, t): t for t in tickers}
        for f in as_completed(futures):
            tick, data = f.result()
            results[tick] = data
            done += 1
            if callback and done % 50 == 0:
                callback(done, total, f"Fundamentals {done}/{total}...")
    return results


# ============================================================
# 5. RS Rank / Group summaries / Sanitize
# ============================================================

def rank_rs(stocks):
    valid = [s for s in stocks if s.get("rs_raw") is not None]
    valid.sort(key=lambda x: x["rs_raw"])
    n = len(valid)
    for i, s in enumerate(valid):
        s["rs_score"] = int(round(i / n * 99))
    return stocks


def summarize_groups(stocks, key):
    from collections import defaultdict
    groups = defaultdict(list)
    for s in stocks:
        groups[s.get(key, "Unknown")].append(s)

    result = []
    for name, members in groups.items():
        def avg(f):
            vals = [m[f] for m in members if m.get(f) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        result.append({
            "name":             name,
            "count":            len(members),
            "ret_1d":           avg("ret_1d"),
            "ret_1w":           avg("ret_1w"),
            "ret_1m":           avg("ret_1m"),
            "ret_3m":           avg("ret_3m"),
            "ret_6m":           avg("ret_6m"),
            "ret_1y":           avg("ret_1y"),
            "avg_rs":           avg("rs_score"),
            "pct_above_ema50":  round(
                sum(1 for m in members if m.get("above_ema50")) / len(members) * 100
            ),
            "avg_pe":           avg("pe"),
            "avg_pbv":          avg("pbv"),
            "avg_div_yield":    avg("div_yield"),
        })
    return sorted(result, key=lambda x: x.get("ret_1m") or -999, reverse=True)


def sanitize(obj):
    import math
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize(i) for i in obj]
    elif isinstance(obj, bool):
        return bool(obj)
    elif hasattr(obj, "item"):
        return obj.item()
    elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None  # Infinity/NaN ไม่ใช่ valid JSON
    elif obj is None or isinstance(obj, (int, float, str)):
        return obj
    else:
        return str(obj)


# ============================================================
# 5. run_with_progress — API สำหรับ Flask
# ============================================================

def run_with_progress(callback, base_dir=None):
    """
    ดึงข้อมูลทั้งหมดและบันทึก set_data.json
    callback(current: int, total: int, message: str)
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    callback(0, 100, "กำลังอ่านรายชื่อหุ้น...")
    symbols = load_set_symbols(base_dir)
    total   = len(symbols)

    callback(0, total, f"พบ {total} หุ้น — เริ่ม batch download...")

    tickers  = [s["ticker"] for s in symbols]
    sym_map  = {s["ticker"]: s for s in symbols}
    all_data = fetch_all_batch(tickers, callback=callback)

    callback(total, total, f"ดาวน์โหลดเสร็จ — คำนวณ metrics ({len(all_data)}/{total} หุ้น)...")

    stocks = []
    for i, info_dict in enumerate(symbols):
        tick = info_dict["ticker"]
        d    = all_data.get(tick)
        if d is None:
            continue
        result = process_stock(info_dict, d["close"], d["volume"])
        if result:
            stocks.append(result)
        if i % 100 == 0:
            callback(i, total, f"คำนวณ {i}/{total}...")

    callback(0, total, f"ดึง Fundamentals ({len(stocks)} หุ้น) แบบ parallel...")
    cap_tickers = [s["ticker"] for s in stocks]
    try:
        fundamentals = fetch_market_caps_parallel(cap_tickers, callback=callback)
    except Exception as e:
        print(f"[Fundamentals] ดึงไม่สำเร็จ ({e}) — ข้ามไป ใช้ค่า None แทน")
        fundamentals = {}
    for s in stocks:
        fund = fundamentals.get(s["ticker"]) or {}
        s["mkt_cap"]   = fund.get("mkt_cap")
        s["pe"]        = fund.get("pe")
        s["pbv"]       = fund.get("pbv")
        s["div_yield"] = fund.get("div_yield")

    callback(total, total, f"คำนวณ RS Rank ({len(stocks)} หุ้น)...")
    stocks = rank_rs(stocks)

    industries = summarize_groups(stocks, "industry")
    sectors    = summarize_groups(stocks, "sector")

    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total":      len(stocks),
        "stocks":     stocks,
        "industries": industries,
        "sectors":    sectors,
    }

    out_path = os.path.join(base_dir, OUT_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sanitize(output), f, ensure_ascii=False, indent=2)

    callback(total, total, f"บันทึกเสร็จ! {len(stocks)} หุ้น")


# ============================================================
# 6. Standalone (python set_data_fetcher.py)
# ============================================================

def main():
    print("=" * 55)
    print("  SET Data Fetcher v3  (Batch Download)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55 + "\n")

    base_dir = os.path.dirname(os.path.abspath(__file__))

    def cb(current, total, msg):
        if total > 0:
            pct = int(current / total * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct:3d}%  {msg}          ", end="", flush=True)
        else:
            print(f"  {msg}")

    print()
    run_with_progress(cb, base_dir)
    print("\n\n✅ เสร็จแล้ว! ดู set_data.json")


if __name__ == "__main__":
    main()
