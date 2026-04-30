import pandas as pd
import FinanceDataReader as fdr
import requests
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [전역 설정] ---
def get_now_kst():
    return datetime.now() + timedelta(hours=9)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_report(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- [데이터 분석 엔진 v5.9] ---

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return (100.0 - (100.0 / (1.0 + (roll_up / roll_down)))).iloc[-1]

def get_market_mode():
    """시장 국면 판단: 강세(Trend) vs 횡보(Range)"""
    try:
        df = fdr.DataReader('KS11', (get_now_kst() - timedelta(days=60)).strftime('%Y-%m-%d'))
        curr_p = df['Close'].iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        # 지수가 20일선 위에 있으면 강세 모드, 아래면 횡보/눌림 모드
        return 'BULL' if curr_p > ma20 else 'SIDEWAYS'
    except: return 'SIDEWAYS'

def analyze_stock(symbol, name, market_mode):
    try:
        df = fdr.DataReader(symbol, (get_now_kst() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 60: return None
        
        curr_p = df['Close'].iloc[-1]
        ma10 = df['Close'].rolling(10).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = calculate_rsi_wilder(df['Close'])
        amt_억 = (curr_p * df['Volume'].iloc[-1]) / 100_000_000
        peak_p = df['Close'].iloc[-30:].max() # 최근 30일 고점

        # 1. 매도 로직 (v5.9 트레일링 스탑 12% 적용)
        action = "💎 보유 유지"
        is_trailing_hit = curr_p < (peak_p * 0.88) # 고점 대비 -12%
        stop_line = ma20 if market_mode == 'BULL' else ma10
        
        if curr_p < stop_line: action = "🚨 추세 이탈 매도"
        elif is_trailing_hit: action = "🛑 익절/보호 (고점-12%)"
        elif rsi > 85: action = "🔥 초과열 분할익절"

        # 2. 매수 로직 (Dual-Mode Grade)
        grade = None
        if amt_억 >= 300: # 체급 필터
            if market_mode == 'BULL':
                # 강세장: 돌파형 S급 (RSI 45~75)
                if (curr_p > ma10 > ma20 > ma60) and (45 <= rsi <= 75):
                    grade = 'S'
            else:
                # 횡보장: 눌림목형 S급 (RSI 35~50, 장기이평선 지지)
                if (curr_p > ma60) and (35 <= rsi <= 50):
                    grade = 'S'

            # A급: 안정적 추세
            if not grade and (curr_p > ma20) and (50 <= rsi <= 70):
                grade = 'A'

        return {'name': name, 'symbol': symbol, 'rsi': rsi, 'grade': grade, 'action': action}
    except: return None

# --- [메인 실행부] ---

def main():
    mode = get_market_mode()
    krx = fdr.StockListing('KRX')
    robust = krx[krx['Marcap'] >= 500_000_000_000]
    
    # 포트폴리오 로드
    my_codes = []
    portfolio_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'portfolio.txt')
    if os.path.exists(portfolio_file):
        with open(portfolio_file, 'r', encoding='utf-8-sig') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    my_codes.append(line.split('#')[0].strip().zfill(6))

    port_res, s_cands, a_cands = [], [], []
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = {executor.submit(analyze_stock, r['Code'], r['Name'], mode): r for _, r in robust.iterrows()}
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['symbol'] in my_codes: port_res.append(res)
            elif res['grade'] == 'S': s_cands.append(res)
            elif res['grade'] == 'A': a_cands.append(res)

    # 리포트 출력
    msg = f"🌿 **스마트 피킹 리포트 v5.9 (Dual-Mode)**\n"
    msg += f"🌡️ 시장 국면: **{'🚀 강세(Trend)' if mode == 'BULL' else '⚖️ 횡보/눌림(Range)'}**\n\n"
    
    msg += "📁 **내 보유 종목 대응**\n"
    msg += "\n".join([f"- {r['name']}: {r['action']}" for r in port_res]) if port_res else "- 없음"
    msg += "\n\n"

    msg += "💎 **S급: 주도주 & 눌림목 포착**\n"
    msg += "\n".join([f"- {r['name']} (RSI:{r['rsi']:.1f})" for r in sorted(s_cands, key=lambda x: x['rsi'])[:5]]) if s_cands else "- 없음"
    msg += "\n\n"

    msg += "✨ **A급: 안정적 추세 안착**\n"
    msg += "\n".join([f"- {r['name']} (RSI:{r['rsi']:.1f})" for r in sorted(a_cands, key=lambda x: x['rsi'])[:10]]) if a_cands else "- 없음"

    send_telegram_report(msg)

if __name__ == "__main__":
    main()
