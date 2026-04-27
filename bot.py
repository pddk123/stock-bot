import pandas as pd
import FinanceDataReader as fdr
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 환경 변수 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

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

def analyze_stock(symbol, name, sector, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 30: return None
        
        # 지표 계산
        ma10, ma20, ma60 = df['Close'].rolling(10).mean().iloc[-1], df['Close'].rolling(20).mean().iloc[-1], df['Close'].rolling(60).mean().iloc[-1]
        vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
        rsi_val = calculate_rsi(df['Close'])
        curr_price = df['Close'].iloc[-1]
        vol_ratio = df['Volume'].iloc[-1] / vol_ma5 if vol_ma5 > 0 else 0
        avg_amount = (df['Close'] * df['Volume']).rolling(5).mean().iloc[-1] / 100_000_000

        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'amount': avg_amount, 'action': "보유 유지"}
        
        # 1. 포트폴리오 액션 로직
        if is_portfolio:
            if rsi_val >= 70: res['action'] = "🚨 **매도 추천 (과열)**"
            elif rsi_val <= 45 and curr_price > ma60: res['action'] = "✅ **추가 매수 추천**"
            else: res['action'] = "💎 **보유 유지**"
        
        # 2. 신규 추천 로직 (RSI 70 이상은 아예 추천에서 제외)
        if not is_portfolio:
            is_dependable = (curr_price > ma10 > ma20 > ma60) and (avg_amount >= 50) and (rsi_val < 70)
            if is_dependable and (45 <= rsi_val <= 62) and (vol_ratio >= 1.5):
                res.update({'grade': 'S'})
            elif (rsi_val > 50 and rsi_val < 70) and (curr_price > ma10) and (vol_ratio >= 1.0):
                res.update({'grade': 'A'})
        
        return res
    except: return None

def main():
    # 시장 상황 파악
    mkt_report, up_count = [], 0
    indices = {'Nasdaq': '^IXIC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    for name, ticker in indices.items():
        try:
            idx_df = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
            chg = (idx_df['Close'].iloc[-1] - idx_df['Close'].iloc[-2]) / idx_df['Close'].iloc[-2] * 100
            mkt_report.append(f"- {name}: {chg:+.2f}%")
            if chg > 0: up_count += 1
        except: pass
    
    # 시장 진단 설명
    if up_count >= 2: 
        mkt_status = "🚀 **상승장 (Bull Market)**"
        mkt_desc = "글로벌 기술주와 국내 지수가 동반 상승하며 매수 심리가 우세합니다. 추세 추종 전략이 유효합니다."
    else:
        mkt_status = "📉 **하락/조정장 (Bear Market)**"
        mkt_desc = "주요 지수가 힘을 못 쓰고 있습니다. 무리한 진입보다는 현금 비중을 유지하며 S급 신호를 기다릴 때입니다."

    krx = fdr.StockListing('KRX')
    managed_codes = get_kind_managed_stocks()
    robust = krx[(krx['Marcap'] >= 500_000_000_000) & (~krx['Code'].isin(managed_codes))]
    my_codes = load_portfolio()
    
    portfolio_res, s_list, a_list = [], [], []
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        for _, r in robust.iterrows(): tasks.append(executor.submit(analyze_stock, r['Code'], r['Name'], r.get('Sector', '기타'), False))
        for c in my_codes:
            match = krx[krx['Code'] == c]
            if not match.empty: tasks.append(executor.submit(analyze_stock, c, match.iloc[0]['Name'], match.iloc[0].get('Sector', '기타'), True))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: portfolio_res.append(f"- {res['name']}: {res['action']} (RSI:{res['rsi']:.1f}, 거래량:{res['vol_ratio']:.1f}배)")
            elif res['grade'] == 'S': s_list.append(res)
            elif res['grade'] == 'A': a_list.append(res)

    s_list = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)[:5]
    final_a = (s_list[5:] + sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True))[:10]

    # 메시지 조립
    msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v4.9)**\n\n"
    msg += f"📊 **시장 상황: {mkt_status}**\n{mkt_desc}\n" + "\n".join(mkt_report) + "\n\n"
    msg += "📁 **내 보유 종목 대응**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    msg += "💎 **S급: 추세 폭발 우량주 (Max 5)**\n"
    if s_list:
        for r in s_list: msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)\n"
    else: msg += "- 조건 충족 없음\n"
    msg += "\n✨ **A급: 안정적 추세 안착 (Max 10)**\n"
    for r in final_a: msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)\n"
    
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
