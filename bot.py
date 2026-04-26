import pandas as pd
import FinanceDataReader as fdr
from pykrx import stock
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

def get_market_fundamental():
    """재무 데이터를 가져오되, 실패해도 빈 표를 반환해 에러를 방지함"""
    target_date = datetime.now().strftime("%Y%m%d")
    try:
        df = stock.get_market_fundamental_by_date(target_date, target_date, "ALL")
        if df is None or df.empty:
            target_date = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
            df = stock.get_market_fundamental_by_date(target_date, target_date, "ALL")
        
        if df is not None and not df.empty:
            df = df.replace('-', '0').reset_index().rename(columns={'티커': 'Code'})
            return df
    except: pass
    return pd.DataFrame(columns=['Code', 'PBR', 'PER'])

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

def analyze_stock(symbol, name, sector, pbr, per, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 20: return None
        
        # 지표 계산
        ma10, ma20 = df['Close'].rolling(10).mean().iloc[-1], df['Close'].rolling(20).mean().iloc[-1]
        vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
        rsi_val = calculate_rsi(df['Close'])
        curr_price = df['Close'].iloc[-1]
        vol_ratio = df['Volume'].iloc[-1] / vol_ma5 if vol_ma5 > 0 else 0
        
        # PBR/PER 안전 변환
        try: pbr_f = float(pbr)
        except: pbr_f = 99.0
        try: per_f = float(per)
        except: per_f = 99.0
        
        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'pbr': pbr_f, 'per': per_f, 'sell_desc': ""}
        
        # 듬직한 우량주 조건
        is_dependable = (0.5 <= pbr_f <= 4.0) and (0 < per_f <= 35)
        if is_dependable and (45 <= rsi_val <= 62) and (curr_price > ma10 > ma20) and (vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        elif (rsi_val > 50) and (curr_price > ma10) and (vol_ratio >= 1.0):
            res.update({'grade': 'A'})
        
        if rsi_val >= 70: res['sell_desc'] = f"🔔 목표가 도달(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    mkt_report = []
    try:
        for name, ticker in {'Nasdaq': '^IXIC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}.items():
            idx_df = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
            chg = (idx_df['Close'].iloc[-1] - idx_df['Close'].iloc[-2]) / idx_df['Close'].iloc[-2] * 100
            mkt_report.append(f"- {name}: {chg:+.2f}%")
    except: pass

    # 데이터 수집 및 병합
    try:
        krx = fdr.StockListing('KRX')
        fund = get_market_fundamental()
        all_stocks = pd.merge(krx, fund, on='Code', how='left').fillna(0)
    except:
        send_telegram_message("⚠️ 데이터 서버 연결 실패")
        return

    # 컬럼 존재 확인 (Sector가 없을 경우 대비)
    if 'Sector' not in all_stocks.columns: all_stocks['Sector'] = '기타'
    
    robust = all_stocks[all_stocks['Marcap'] >= 500_000_000_000]
    my_codes = load_portfolio()
    
    portfolio_res, s_list, a_list = [], [], []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        tasks = []
        # 우량주 스캔
        for _, r in robust.iterrows():
            tasks.append(executor.submit(analyze_stock, r['Code'], r['Name'], r['Sector'], r.get('PBR', 99), r.get('PER', 99), False))
        # 보유 종목 스캔
        for c in my_codes:
            match = all_stocks[all_stocks['Code'] == c]
            if not match.empty:
                r = match.iloc[0]
                tasks.append(executor.submit(analyze_stock, c, r['Name'], r['Sector'], r.get('PBR', 99), r.get('PER', 99), True))
        
        for f in as_completed(tasks):
            try:
                res = f.result()
                if not res: continue
                if res['is_portfolio']: portfolio_res.append(f"- {res['name']}: {res['sell_desc'] or '보유'} (RSI:{res['rsi']:.1f}, PBR:{res['pbr']:.1f})")
                elif res['grade'] == 'S': s_list.append(res)
                elif res['grade'] == 'A': a_list.append(res)
            except: pass

    # 최종 조립
    s_list = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)[:5]
    a_list = sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True)[:10]

    final_msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v4.7)**\n\n📊 **시장 상황**\n" + "\n".join(mkt_report) + "\n\n"
    final_msg += "📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    final_msg += "💎 **S급: 저평가 우량주**\n"
    for r in s_list: final_msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배, PBR:{r['pbr']:.1f})\n"
    final_msg += "\n✨ **A급: 안정적 추세**\n"
    for r in a_list: final_msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, PBR:{r['pbr']:.1f})\n"
    
    send_telegram_message(final_msg)

if __name__ == "__main__":
    main()
