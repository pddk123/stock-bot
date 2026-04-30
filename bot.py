import pandas as pd
import FinanceDataReader as fdr
import requests
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [전역 설정 및 유틸리티] ---

def get_now_kst():
    return datetime.now() + timedelta(hours=9)

logger = logging.getLogger("StockAnalyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_report(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except Exception as e: logger.error(f"전송 실패: {e}")

# --- [데이터 분석 엔진] ---

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = roll_up / roll_down
    return (100.0 - (100.0 / (1.0 + rs))).iloc[-1]

def get_market_status():
    """지수 상황 판단: 안정(MA20 위) vs 주의(MA20 아래)"""
    try:
        # 코스피 기준으로 대표 판단
        df = fdr.DataReader('KS11', (get_now_kst() - timedelta(days=60)).strftime('%Y-%m-%d'))
        curr_p = df['Close'].iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        rsi = calculate_rsi_wilder(df['Close'])
        return {'is_stable': curr_p > ma20, 'rsi': rsi, 'price': curr_p, 'ma20': ma20}
    except: return {'is_stable': False, 'rsi': 50}

def get_indicators(df, multiplier=1.0):
    if len(df) < 60: return None
    last_row = df.iloc[-1]
    curr_p = last_row['Close']
    ma10 = df['Close'].rolling(10).mean().iloc[-1]
    ma20 = df['Close'].rolling(20).mean().iloc[-1]
    ma60 = df['Close'].rolling(60).mean().iloc[-1]
    
    # 트레일링 스탑을 위한 최근 20일 최고가 계산
    peak_p = df['Close'].iloc[-20:].max()
    
    vol_ma5 = df['Volume'].iloc[:-1].rolling(5).mean().iloc[-1] 
    est_vol = last_row['Volume'] * multiplier
    v_ratio = est_vol / vol_ma5 if vol_ma5 > 0 else 0
    amt_억 = (curr_p * est_vol) / 100_000_000
    rsi_v = calculate_rsi_wilder(df['Close'])
    
    return {'price': curr_p, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60, 
            'peak': peak_p, 'rsi': rsi_v, 'vol_ratio': v_ratio, 'amount': amt_억}

def check_grade(ind):
    """S/A급 판정 (거래대금 300억 필터 적용)"""
    if not ind or ind['amount'] < 300: return None # 체급 필터 상향
    
    is_s = (ind['price'] > ind['ma10'] > ind['ma20'] > ind['ma60']) and \
           (45 <= ind['rsi'] <= 65) and (ind['vol_ratio'] >= 1.5)
    if is_s: return 'S'
    
    is_a = (ind['price'] > ind['ma10']) and (50 <= ind['rsi'] < 72) and (ind['vol_ratio'] >= 1.0)
    if is_a: return 'A'
    return None

def analyze_stock(symbol, name, sector, multiplier, market_stable, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (get_now_kst() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 35: return None
        
        # 오늘자 지표
        ind = get_indicators(df, multiplier)
        if not ind: return None
        
        grade = check_grade(ind)
        res = {'name': name, 'symbol': symbol, 'rsi': ind['rsi'], 'vol_ratio': ind['vol_ratio'], 
               'grade': grade, 'is_portfolio': is_portfolio, 'action': ""}

        if is_portfolio:
            # 1. 가변적 손절 로직 (안정장 MA20 / 하락장 MA10)
            stop_line = ind['ma20'] if market_stable else ind['ma10']
            
            # 2. 트레일링 스탑 로직 (고점 대비 -5%)
            is_trailing_hit = ind['price'] < (ind['peak'] * 0.95)
            
            if ind['price'] < stop_line:
                res['action'] = f"🚨 **탈출 고려 ({'20일선' if market_stable else '10일선'} 이탈)**"
            elif is_trailing_hit:
                res['action'] = "🛑 **익절/보호 (고점 -5% 돌파)**"
            elif ind['rsi'] >= 80: # 극심한 과열권 추가 경고
                res['action'] = "🔥 **과열 경고 (분할익절)**"
            else:
                res['action'] = "💎 **보유 유지**"

        # 신규 진입을 위한 연속성 체크 (S/A급 후보만)
        consistency = 0
        if not is_portfolio:
            if not grade: return None
            for i in range(1, 4):
                prev_df = df.iloc[:-i]
                prev_ind = get_indicators(prev_df, 1.0)
                if check_grade(prev_ind): consistency += 1
                else: break
            res['consistency'] = consistency + 1

        return res
    except: return None

# --- [메인 실행부] ---

def main():
    market = get_market_status()
    market_text = f"KOSPI: {'☀ 안정' if market['is_stable'] else '☁ 주의'} [RSI:{market['rsi']:.1f}, 이격:{market['price']/market['ma20']*100:.1f}%]"
    
    krx = fdr.StockListing('KRX')
    robust = krx[krx['Marcap'] >= 500_000_000_000] # 5천억 이상 우량주
    
    # 포트폴리오 로드
    my_codes = []
    portfolio_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'portfolio.txt')
    if os.path.exists(portfolio_file):
        with open(portfolio_file, 'r', encoding='utf-8-sig') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    my_codes.append(line.split('#')[0].strip().zfill(6))

    vol_mult = 1.0 # 장중이라면 보정 계수 함수 호출 가능
    s_cands, a_cands, port_res = [], [], []

    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        for c in my_codes:
            m = krx[krx['Code'] == c]
            if not m.empty:
                tasks.append(executor.submit(analyze_stock, c, m.iloc[0]['Name'], "", vol_mult, market['is_stable'], True))
        for _, r in robust.iterrows():
            if r['Code'] not in my_codes:
                tasks.append(executor.submit(analyze_stock, r['Code'], r['Name'], "", vol_mult, market['is_stable'], False))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: port_res.append(res)
            elif res['grade'] == 'S': s_cands.append(res)
            elif res['grade'] == 'A': a_cands.append(res)

    # 리포트 구성
    msg = f"🌿 **rootee님, 스마트 피킹 리포트 (v5.8)**\n"
    msg += f"📊 기준: KST {get_now_kst().strftime('%H:%M')}\n\n"
    
    msg += f"🌡️ **현 시장 상황 평가**\n{market_text}\n\n"

    msg += "📁 **내 보유 종목 대응**\n"
    msg += "\n".join([f"- {r['name']}: {r['action']}" for r in port_res]) if port_res else "- 없음"
    msg += "\n\n"

    badge = lambda c: "🔥 **[3일 우수]**" if c >= 3 else "✅ **[2일 우수]**" if c == 2 else "🆕 **[신규]**"
    
    msg += "💎 **S급: 추세 폭발 주도주 (300억+)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {badge(r['consistency'])}" for r in sorted(s_cands, key=lambda x: x['vol_ratio'], reverse=True)[:5]]) if s_cands else "- 없음"
    msg += "\n\n"

    msg += "✨ **A급: 안정적 추세 안착**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {badge(r['consistency'])}" for r in sorted(a_cands, key=lambda x: x['vol_ratio'], reverse=True)[:10]]) if a_cands else "- 없음"

    send_telegram_report(msg)

if __name__ == "__main__":
    main()
