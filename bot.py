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
    """KIND에서 관리종목/투자주의 종목 리스트 확보 (부실주 필터)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # 관리종목 리스트
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
        ma10 = df['Close'].rolling(10).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
        rsi_val = calculate_rsi(df['Close'])
        curr_price = df['Close'].iloc[-1]
        vol_ratio = df['Volume'].iloc[-1] / vol_ma5 if vol_ma5 > 0 else 0
        
        # [신규 지표] 거래대금 (단위: 억)
        avg_amount = (df['Close'] * df['Volume']).rolling(5).mean().iloc[-1] / 100_000_000

        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'amount': avg_amount, 'sell_desc': ""}
        
        # 🌟 듬직한 우량주 필터 (정배열 + 거래대금)
        # 1. 정배열 (10일 > 20일 > 60일): 추세가 확실히 살아있는 듬직한 놈
        # 2. 거래대금 상위: 하루 평균 50억 이상 거래되는 활발한 놈
        is_dependable = (curr_price > ma10 > ma20 > ma60) and (avg_amount >= 50)
        
        if is_dependable and (45 <= rsi_val <= 62) and (vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        elif (rsi_val > 50) and (curr_price > ma10) and (vol_ratio >= 1.0):
            res.update({'grade': 'A'})
        
        if rsi_val >= 70: res['sell_desc'] = f"🔔 매도검토(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    # 1. 시장 상황 파악
    mkt_report = []
    try:
        for name, ticker in {'Nasdaq': '^IXIC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}.items():
            idx_df = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
            chg = (idx_df['Close'].iloc[-1] - idx_df['Close'].iloc[-2]) / idx_df['Close'].iloc[-2] * 100
            mkt_report.append(f"- {name}: {chg:+.2f}%")
    except: pass

    # 2. 종목 리스트 및 부실주 필터
    krx = fdr.StockListing('KRX')
    managed_codes = get_kind_managed_stocks()
    
    # [필터] 시총 5,000억 이상 + 관리종목 제외
    robust = krx[(krx['Marcap'] >= 500_000_000_000) & (~krx['Code'].isin(managed_codes))]
    my_codes = load_portfolio()
    
    portfolio_res, s_list, a_list = [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        for _, r in robust.iterrows():
            tasks.append(executor.submit(analyze_stock, r['Code'], r['Name'], r.get('Sector', '기타'), False))
        for c in my_codes:
            match = krx[krx['Code'] == c]
            if not match.empty:
                r = match.iloc[0]
                tasks.append(executor.submit(analyze_stock, c, r['Name'], r.get('Sector', '기타'), True))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: 
                portfolio_res.append(f"- {res['name']}: {res['sell_desc'] or '보유'} (RSI:{res['rsi']:.1f}, 거래량:{res['vol_ratio']:.1f}배)")
            elif res['grade'] == 'S': s_list.append(res)
            elif res['grade'] == 'A': a_list.append(res)

    # 3. 결과 정렬 (거래량 폭발력 순)
    s_list = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)
    final_s = s_list[:5]
    final_a = (s_list[5:] + sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True))[:10]

    # 4. 주도 섹터 분석
    combined = pd.DataFrame(final_s + final_a)
    top_sector_msg = ""
    if not combined.empty:
        top_s = combined[combined['sector'] != '기타']['sector'].value_counts().head(2).index.tolist()
        if top_s: top_sector_msg = f"🔥 **현재 주도 섹터**: {', '.join(top_s)}\n\n"

    # 5. 최종 메시지 조립 (v3.8 스타일)
    msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v4.8)**\n\n📊 **시장 상황**\n" + "\n".join(mkt_report) + "\n\n"
    msg += top_sector_msg
    msg += "📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    
    msg += "💎 **S급: 추세 폭발 우량주 (Max 5)**\n"
    if final_s:
        df_s = pd.DataFrame(final_s)
        # 섹터별 거래량 대장주 마킹
        leaders = df_s.groupby('sector')['vol_ratio'].idxmax()
        for i, r in df_s.iterrows():
            tag = " 🏆 **대장주**" if i in leaders.values else ""
            msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배{tag})\n"
    else: msg += "- 조건 충족 없음\n"
    
    msg += "\n✨ **A급: 안정적 추세 안착 (Max 10)**\n"
    for r in final_a:
        msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)\n"
    
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
