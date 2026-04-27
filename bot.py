import pandas as pd
import FinanceDataReader as fdr
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 환경 변수 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def get_weighted_volume_multiplier():
    """U자형 거래량 곡선을 반영한 시간 가중치 보정 계수 (KST 기준)"""
    now = datetime.utcnow() + timedelta(hours=9) # KST 변환
    start_market = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed = (now - start_market).total_seconds() / 60
    
    if elapsed <= 0: return 1.0
    if elapsed >= 390: return 1.0 # 장 마감 후

    # 시간대별 누적 거래량 비중(통계치) 기반
    if elapsed <= 60: # 09:00 ~ 10:00 (초반 폭발기)
        weight = (elapsed / 60) * 0.35 
    elif elapsed <= 360: # 10:00 ~ 15:00 (정체기)
        weight = 0.35 + ((elapsed - 60) / 300) * 0.40
    else: # 15:00 ~ 15:30 (마감 집중기)
        weight = 0.75 + ((elapsed - 360) / 30) * 0.25
        
    return 1.0 / weight

def get_kind_managed_stocks():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = 'https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=05'
        res = requests.get(url, headers=headers)
        df = pd.read_html(res.text, header=0)[0]
        return df['종목코드'].apply(lambda x: f"{x:06d}").tolist()
    except: return []

def load_portfolio():
    codes = []
    if os.path.exists('portfolio.txt'):
        with open('portfolio.txt', 'r', encoding='utf-8') as f:
            for line in f:
                clean = line.split('#')[0].strip().replace(',', ' ')
                for c in clean.split(): codes.append(c.strip())
    return list(set(codes))

def calculate_rsi(series, period=14):
    if len(series) < period: return 50
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down))).iloc[-1]

def analyze_stock(symbol, name, sector, multiplier, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 30: return None
        
        # 지표 계산
        ma10, ma20, ma60 = df['Close'].rolling(10).mean().iloc[-1], df['Close'].rolling(20).mean().iloc[-1], df['Close'].rolling(60).mean().iloc[-1]
        vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
        rsi_val = calculate_rsi(df['Close'])
        curr_price = df['Close'].iloc[-1]
        
        # 🌟 가중치 보정 거래량 적용
        estimated_vol = df['Volume'].iloc[-1] * multiplier
        vol_ratio = estimated_vol / vol_ma5 if vol_ma5 > 0 else 0
        avg_amount = (curr_price * estimated_vol) / 100_000_000 # 예측 거래대금(억)

        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'amount': avg_amount, 'action': ""}
        
        # 1. 내 종목 대응 전략
        if is_portfolio:
            if rsi_val >= 70: res['action'] = "🚨 **매도 추천 (과열)**"
            elif rsi_val <= 45 and curr_price > ma60: res['action'] = "✅ **추가 매수 추천**"
            else: res['action'] = "💎 **보유 유지**"
        
        # 2. 신규 추천 필터 (RSI 70 미만 컷오프 포함)
        if not is_portfolio:
            is_dependable = (curr_price > ma10 > ma20 > ma60) and (avg_amount >= 50) and (rsi_val < 70)
            if is_dependable and (45 <= rsi_val <= 62) and (vol_ratio >= 1.5):
                res.update({'grade': 'S'})
            elif (50 <= rsi_val < 70) and (curr_price > ma10) and (vol_ratio >= 1.0):
                res.update({'grade': 'A'})
        
        return res
    except: return None

def main():
    # 시장 상황 분석
    mkt_report, up_count = [], 0
    for name, ticker in {'Nasdaq': '^IXIC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}.items():
        try:
            idx_df = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
            chg = (idx_df['Close'].iloc[-1] - idx_df['Close'].iloc[-2]) / idx_df['Close'].iloc[-2] * 100
            mkt_report.append(f"- {name}: {chg:+.2f}%")
            if chg > 0: up_count += 1
        except: pass
    
    m_status = "🚀 **상승장**" if up_count >= 2 else "📉 **하락/조정장**"
    m_desc = "글로벌 동조화 속 매수 심리가 살아나고 있습니다." if up_count >= 2 else "지수 하방 압력이 강합니다. 방어적인 관점이 필요합니다."
    
    # 데이터 준비
    krx = fdr.StockListing('KRX')
    managed = get_kind_managed_stocks()
    robust = krx[(krx['Marcap'] >= 500_000_000_000) & (~krx['Code'].isin(managed))]
    my_codes = load_portfolio()
    vol_multiplier = get_weighted_volume_multiplier() # 🌟 보정 계수

    portfolio_res, s_list, a_list = [], [], []
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = [executor.submit(analyze_stock, r['Code'], r['Name'], r.get('Sector', '기타'), vol_multiplier, False) for _, r in robust.iterrows()]
        for c in my_codes:
            match = krx[krx['Code'] == c]
            if not match.empty: tasks.append(executor.submit(analyze_stock, c, match.iloc[0]['Name'], match.iloc[0].get('Sector', '기타'), vol_multiplier, True))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: portfolio_res.append(f"- {res['name']}: {res['action']} (RSI:{res['rsi']:.1f}, 거래량:{res['vol_ratio']:.1f}배)")
            elif res['grade'] == 'S': s_list.append(res)
            elif res['grade'] == 'A': a_list.append(res)

    s_list = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)[:5]
    final_a = (s_list[5:] + sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True))[:10]

    # 메시지 조립
    msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v5.1)**\n\n📊 **시장 상황: {m_status}**\n{m_desc}\n" + "\n".join(mkt_report) + "\n\n"
    msg += "📁 **내 보유 종목 대응**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    msg += "💎 **S급: 추세 폭발 우량주 (Max 5)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in s_list]) if s_list else "- 조건 충족 없음"
    msg += "\n\n✨ **A급: 안정적 추세 안착 (Max 10)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in final_a]) if final_a else "- 없음"
    
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
