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

logger = logging.getLogger("StockAnalyzer")
logger.setLevel(logging.INFO)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_report(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- [데이터 분석 엔진] ---

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return (100.0 - (100.0 / (1.0 + (roll_up / roll_down)))).iloc[-1]

def get_market_sentiment():
    """글로벌 및 국내 지수 변화율 및 국면 판단"""
    indices = {
        '나스닥': 'IXIC', 'S&P500': 'US500', 
        '코스피': 'KS11', '코스닥': 'KQ11'
    }
    results = {}
    mode = 'SIDEWAYS'
    
    try:
        for name, code in indices.items():
            df = fdr.DataReader(code, (get_now_kst() - timedelta(days=10)).strftime('%Y-%m-%d'))
            curr = df['Close'].iloc[-1]
            prev = df['Close'].iloc[-2]
            chg = ((curr / prev) - 1) * 100
            results[name] = f"{chg:+.2f}%"
            
            # 코스피 기준으로 BULL/SIDEWAYS 판단
            if name == '코스피':
                df_long = fdr.DataReader(code, (get_now_kst() - timedelta(days=60)).strftime('%Y-%m-%d'))
                ma20 = df_long['Close'].rolling(20).mean().iloc[-1]
                if curr > ma20: mode = 'BULL'
                
        return results, mode
    except: return results, 'SIDEWAYS'

def get_indicators(df):
    if len(df) < 60: return None
    last = df.iloc[-1]
    curr_p = last['Close']
    peak_p = df['Close'].iloc[-30:].max()
    rsi = calculate_rsi_wilder(df['Close'])
    amt_억 = (curr_p * last['Volume']) / 100_000_000
    ma10 = df['Close'].rolling(10).mean().iloc[-1]
    ma20 = df['Close'].rolling(20).mean().iloc[-1]
    ma60 = df['Close'].rolling(60).mean().iloc[-1]
    vol_ratio = last['Volume'] / df['Volume'].iloc[:-1].rolling(5).mean().iloc[-1]
    
    return {'price': curr_p, 'peak': peak_p, 'rsi': rsi, 'amount': amt_억, 
            'ma10': ma10, 'ma20': ma20, 'ma60': ma60, 'vol_ratio': vol_ratio}

def check_grade(ind, mode):
    if not ind or ind['amount'] < 300: return None # 300억 필터
    
    # 강세장: 돌파형 정배열 / 횡보장: 눌림목형 장기이평 지지
    if mode == 'BULL':
        if (ind['price'] > ind['ma10'] > ind['ma20'] > ind['ma60']) and (45 <= ind['rsi'] <= 75):
            return 'S'
    else:
        if (ind['price'] > ind['ma60']) and (35 <= ind['rsi'] <= 50):
            return 'S'
            
    if (ind['price'] > ind['ma20']) and (50 <= ind['rsi'] <= 70):
        return 'A'
    return None

def analyze_stock(symbol, name, mode, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (get_now_kst() - timedelta(days=120)).strftime('%Y-%m-%d'))
        ind = get_indicators(df)
        if not ind: return None
        
        grade = check_grade(ind, mode)
        res = {'name': name, 'symbol': symbol, 'rsi': ind['rsi'], 'grade': grade, 
               'vol_ratio': ind['vol_ratio'], 'is_portfolio': is_portfolio, 'action': "💎 보유 유지"}
        
        if is_portfolio:
            stop_line = ind['ma20'] if mode == 'BULL' else ind['ma10']
            if ind['price'] < stop_line: res['action'] = "🚨 추세 이탈 매도"
            elif ind['price'] < (ind['peak'] * 0.88): res['action'] = "🛑 익절(고점-12%)"
            elif ind['rsi'] > 85: res['action'] = "🔥 초과열 경고"
            
        if not is_portfolio:
            if not grade: return None
            consistency = 1
            for i in range(2, 5):
                if check_grade(get_indicators(df.iloc[:-i+1]), mode): consistency += 1
                else: break
            res['consistency'] = consistency
            
        return res
    except: return None

# --- [메인 실행부] ---

def main():
    idx_stats, mode = get_market_sentiment()
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
        tasks = []
        for c in my_codes:
            m = krx[krx['Code'] == c]
            if not m.empty: tasks.append(executor.submit(analyze_stock, c, m.iloc[0]['Name'], mode, True))
        for _, r in robust.iterrows():
            if r['Code'] not in my_codes:
                tasks.append(executor.submit(analyze_stock, r['Code'], r['Name'], mode, False))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: port_res.append(res)
            elif res['grade'] == 'S': s_cands.append(res)
            elif res['grade'] == 'A': a_cands.append(res)

    # 리포트 조립
    msg = f"🌿 **rootee님, 스마트 피킹 v5.9 (Dual-Mode)**\n"
    msg += f"📊 기준: KST {get_now_kst().strftime('%H:%M')}\n\n"
    
    msg += f"🌡️ **1) 시장 국면 평가**\n"
    msg += f"- 국면: **{'🚀 강세(BULL)' if mode == 'BULL' else '⚖️ 횡보(SIDE)'}**\n"
    msg += f"- 국외: 나스닥({idx_stats.get('나스닥','')}), S&P500({idx_stats.get('S&P500','')})\n"
    msg += f"- 국내: 코스피({idx_stats.get('코스피','')}), 코스닥({idx_stats.get('코스닥','')})\n\n"

    msg += "📁 **2) 내 보유 종목 대응**\n"
    msg += "\n".join([f"- {r['name']}: {r['action']} (RSI:{r['rsi']:.1f})" for r in port_res]) if port_res else "- 없음"
    msg += "\n\n"

    badge = lambda c: "🔥 **[3일 우수]**" if c >= 3 else "✅ **[2일 우수]**" if c == 2 else "🆕 **[신규 진입]**"
    
    msg += "💎 **3) S급: 주도주 & 눌림목 (300억+)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {badge(r.get('consistency',1))}" for r in sorted(s_cands, key=lambda x: x['vol_ratio'], reverse=True)[:5]]) if s_cands else "- 없음"
    msg += "\n\n"

    msg += "✨ **4) A급: 안정적 추세 안착**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {badge(r.get('consistency',1))}" for r in sorted(a_cands, key=lambda x: x['vol_ratio'], reverse=True)[:10]]) if a_cands else "- 없음"

    send_telegram_report(msg)

if __name__ == "__main__":
    main()
