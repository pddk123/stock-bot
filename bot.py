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
    try: requests.post(url, json=payload)
    except: pass

def get_market_fundamental():
    target_date = datetime.now().strftime("%Y%m%d")
    try:
        df = stock.get_market_fundamental_by_date(target_date, target_date, "ALL")
        if df.empty:
            target_date = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
            df = stock.get_market_fundamental_by_date(target_date, target_date, "ALL")
        df = df.replace('-', '0')
        return df.reset_index().rename(columns={'티커': 'Code'})
    except:
        return pd.DataFrame()

def load_portfolio():
    codes = []
    if os.path.exists('portfolio.txt'):
        with open('portfolio.txt', 'r', encoding='utf-8') as f:
            for line in f:
                clean_line = line.split('#')[0].strip()
                if not clean_line: continue
                parts = clean_line.replace(',', ' ').split()
                for code in parts:
                    if code.strip(): codes.append(code.strip())
    return list(set(codes))

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sector, pbr, per, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr, rsi_val = df.iloc[-1], df['RSI'].iloc[-1]
        vol_ratio = curr['Volume'] / curr['Vol_MA5']
        
        # PBR/PER 숫자 변환 안전장치
        try: pbr_val = float(pbr)
        except: pbr_val = 99.0
        try: per_val = float(per)
        except: per_val = 99.0
        
        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'pbr': pbr_val, 'per': per_val, 'sell_desc': ""}
        
        is_dependable = (0.5 <= pbr_val <= 4.0) and (0 < per_val <= 35)
        if is_dependable and (45 <= rsi_val <= 62) and (curr['Close'] > curr['MA10'] > curr['MA20']) and (vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        elif (rsi_val > 50) and (curr['Close'] > curr['MA10']) and (vol_ratio >= 1.0):
            res.update({'grade': 'A'})
        
        if rsi_val >= 70: res['sell_desc'] = f"🔔 목표가 도달(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    mkt_report = []
    scores = 0
    total_chg = 0
    for name, ticker in indices.items():
        try:
            df_idx = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
            c, p = df_idx['Close'].iloc[-1], df_idx['Close'].iloc[-2]
            chg = (c - p) / p * 100
            mkt_report.append(f"- {name}: {chg:+.2f}%")
            total_chg += chg
            if chg > -0.1: scores += 1
        except: pass

    avg_chg = total_chg / len(mkt_report) if mkt_report else 0
    mkt_status = "📈 상승세" if scores >= 2 else "📉 주의"
    
    my_codes = load_portfolio()
    krx_listing = fdr.StockListing('KRX')
    fund_df = get_market_fundamental()
    all_stocks = pd.merge(krx_listing, fund_df, on='Code', how='left').fillna(0)
    
    robust_market = all_stocks[all_stocks['Marcap'] >= 500_000_000_000]
    
    portfolio_res, s_list, a_list = [], [], []
    with ThreadPoolExecutor(max_workers=8) as executor:
        m_tasks = []
        for _, row in robust_market.iterrows():
            sec = row['Sector'] if 'Sector' in row else '기타'
            m_tasks.append(executor.submit(analyze_stock, row['Code'], row['Name'], sec, row['PBR'], row['PER'], False))
        
        p_tasks = []
        for code in my_codes:
            stock_info = all_stocks[all_stocks['Code'] == code]
            if not stock_info.empty:
                r = stock_info.iloc[0]
                sec = r['Sector'] if 'Sector' in r else '기타'
                p_tasks.append(executor.submit(analyze_stock, code, r['Name'], sec, r['PBR'], r['PER'], True))
        
        for future in as_completed(m_tasks + p_tasks):
            r = future.result()
            if not r: continue
            if r['is_portfolio']: portfolio_res.append(f"- {r['name']}: {r['sell_desc'] or '보유'} (RSI:{r['rsi']:.1f}, PBR:{r['pbr']:.1f})")
            elif r['grade'] == 'S': s_list.append(r)
            elif r['grade'] == 'A': a_list.append(r)

    s_list = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)[:5]
    a_list = sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True)[:10]

    msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v4.6)**\n\n📊 **시장**: {mkt_status} ({avg_chg:+.2f}%)\n" + "\n".join(mkt_report) + "\n\n"
    msg += "📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    msg += "💎 **S급: 저평가 우량주**\n"
    for r in s_list: msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배, PBR:{r['pbr']:.1f})\n"
    msg += "\n✨ **A급: 안정적 추세**\n"
    for r in a_list: msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, PBR:{r['pbr']:.1f})\n"
    
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
