import pandas as pd
import FinanceDataReader as fdr
import requests
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [유틸리티 및 로깅] ---
def get_now_kst():
    return datetime.now() + timedelta(hours=9)

logger = logging.getLogger("StockAnalyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

def send_telegram_report(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)

# --- [분석 엔진] ---
def get_weighted_volume_multiplier():
    now = get_now_kst()
    if now.weekday() >= 5: return 1.0
    start_market = 9 * 60
    current_time = now.hour * 60 + now.minute
    elapsed = current_time - start_market
    if elapsed <= 0 or elapsed >= 390: return 1.0
    if elapsed <= 60: weight = (elapsed / 60) * 0.35
    elif elapsed <= 360: weight = 0.35 + ((elapsed - 60) / 300) * 0.40
    else: weight = 0.75 + ((elapsed - 360) / 30) * 0.25
    return 1.0 / weight

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return (100.0 - (100.0 / (1.0 + roll_up / roll_down))).iloc[-1]

def get_indicators(df, multiplier=1.0):
    if len(df) < 20: return None
    last = df.iloc[-1]; curr_p = last['Close']
    ma10 = df['Close'].rolling(10).mean().iloc[-1]
    ma20 = df['Close'].rolling(20).mean().iloc[-1]
    ma60 = df['Close'].rolling(60).mean().iloc[-1]
    vol_ma5 = df['Volume'].iloc[:-1].rolling(5).mean().iloc[-1]
    est_vol = last['Volume'] * multiplier
    return {'price': curr_p, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60, 
            'rsi': calculate_rsi_wilder(df['Close']), 
            'vol_ratio': est_vol / vol_ma5 if vol_ma5 > 0 else 0, 
            'amount': (curr_p * est_vol) / 100_000_000}

def check_grade(ind):
    if not ind: return None
    if (ind['price'] > ind['ma10'] > ind['ma20'] > ind['ma60']) and (ind['amount'] >= 50) and (45 <= ind['rsi'] <= 62) and (ind['vol_ratio'] >= 1.5): return 'S'
    if (ind['price'] > ind['ma10']) and (50 <= ind['rsi'] < 70) and (ind['vol_ratio'] >= 1.0): return 'A'
    return None

def analyze_stock(symbol, name, sector, multiplier, is_portfolio):
    try:
        today_date = get_now_kst().date()
        df = fdr.DataReader(symbol, (get_now_kst() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 35: return None
        
        consistency_count, today_res = 0, None
        for i in range(3):
            target_df = df.iloc[:len(df)-i]
            ind = get_indicators(target_df, multiplier if (i==0 and target_df.index[-1].date()==today_date) else 1.0)
            grade = check_grade(ind)
            if i == 0:
                today_res = {'name': name, 'symbol': symbol, 'is_portfolio': is_portfolio, 'rsi': ind['rsi'], 'vol_ratio': ind['vol_ratio'], 'grade': grade, 'action': ""}
                if is_portfolio: # 동근 님의 v5.6 5단계 로직
                    if ind['price'] < ind['ma10'] and grade is None: today_res['action'] = "🚨 **탈출 고려 (추세 이탈)**"
                    elif ind['rsi'] >= 70: today_res['action'] = "🛑 **탈출 권고 (과열 구간)**"
                    elif ind['rsi'] < 50 and grade: today_res['action'] = "✅ **추매 고려 (에너지 응축)**"
                    elif grade and ind['price'] >= ind['ma10']: today_res['action'] = "💎 **보유 유지 (추세 있음)**"
                    else: today_res['action'] = "🧐 **관찰 필요 (기세 약화)**"
                if not is_portfolio and grade is None: return None
                if grade: consistency_count = 1
                else: break
            elif grade: consistency_count += 1
            else: break
        if today_res: today_res['consistency'] = consistency_count
        return today_res
    except: return None

# --- [메인] ---
def main():
    krx = fdr.StockListing('KRX')
    my_codes = []
    if os.path.exists('portfolio.txt'):
        with open('portfolio.txt', 'r', encoding='utf-8') as f:
            my_codes = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    # 1. 분석 대상 선정 (중복 제거)
    target_dict = {c: True for c in my_codes} # 포트폴리오 우선
    robust = krx[krx['Marcap'] >= 500_000_000_000]
    for _, r in robust.iterrows():
        if r['Code'] not in target_dict: target_dict[r['Code']] = False
    
    vol_multiplier = get_weighted_volume_multiplier()
    port_res, s_cands, a_cands = [], [], []

    # 2. 병렬 분석
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        for code, is_port in target_dict.items():
            name = krx[krx['Code'] == code]['Name'].iloc[0]
            tasks.append(executor.submit(analyze_stock, code, name, "", vol_multiplier, is_port))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: port_res.append(res)
            if res['grade'] == 'S': s_cands.append(res)
            elif res['grade'] == 'A': a_cands.append(res)

    # 3. 리포트 조립
    s_sorted = sorted(s_cands, key=lambda x: x['vol_ratio'], reverse=True)
    final_s = s_sorted[:5]
    final_a = sorted([x for x in s_sorted[5:] + a_cands], key=lambda x: x['vol_ratio'], reverse=True)[:10]

    def get_badge(c):
        return "🔥 **[3일 우수]**" if c >= 3 else "✅ **[2일 우수]**" if c == 2 else "🆕 **[신규]**"

    msg = f"🌿 **rootee님, 우량주 리포트 (v5.6)**\n📊 기준: KST {get_now_kst().strftime('%H:%M')}\n\n"
    msg += "📁 **내 보유 종목 대응**\n" + ("\n".join([f"- {r['name']}: {r['action']} (RSI:{r['rsi']:.1f})" for r in port_res]) if port_res else "- 없음")
    msg += "\n\n💎 **S급 (Max 5)**\n" + ("\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {get_badge(r['consistency'])}" for r in final_s]) if final_s else "- 없음")
    msg += "\n\n✨ **A급 (Max 10)**\n" + ("\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {get_badge(r['consistency'])}" for r in final_a]) if final_a else "- 없음")
    
    send_telegram_report(msg)

if __name__ == "__main__":
    main()
