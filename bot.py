import FinanceDataReader as fdr
from pykrx import stock
import pandas as pd
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
    """pykrx 데이터 정제 (하이픈 제거 로직 추가)"""
    target_date = datetime.now().strftime("%Y%m%d")
    try:
        df = stock.get_market_fundamental_by_date(target_date, target_date, "ALL")
        if df.empty:
            target_date = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
            df = stock.get_market_fundamental_by_date(target_date, target_date, "ALL")
        
        # 🌟 [에러 해결 포인트] 하이픈(-)을 0으로 바꾸고 숫자로 강제 변환
        df = df.replace('-', '0')
        df = df.apply(pd.to_numeric, errors='coerce').fillna(0)
        
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

def get_market_sentiment():
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    scores, total_chg, details = 0, 0, []
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            if df.empty: continue
            curr, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
            chg = (curr - prev) / prev * 100
            total_chg += chg
            details.append(f"- {name}: {chg:+.2f}%")
            if chg > -0.1: scores += 1
        except: continue
    if not details: return "📊 **진단 불가**", "서버 지연", ""
    avg_chg = total_chg / len(details)
    status = "🚀 **강력 상승장**" if scores >= 3 and avg_chg > 0.4 else ("📈 **완만한 상승장**" if scores >= 2 else "📉 **하락 구간**")
    return status, f"글로벌 평균 {avg_chg:+.2f}% 기반", "\n".join(details)

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
        curr = df.iloc[-1]
        vol_ratio, rsi_val = curr['Volume'] / curr['Vol_MA5'], df['RSI'].iloc[-1]
        
        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'pbr': pbr, 'per': per, 'sell_desc': ""}
        
        # 듬직한 우량주 조건
        is_dependable = (0.5 <= pbr <= 4.0) and (0 < per <= 35) # PER이 0인(적자) 기업은 제외
        
        if is_dependable and (45 <= rsi_val <= 62) and (curr['Close'] > curr['MA10'] > curr['MA20']) and (vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        elif (rsi_val > 50) and (curr['Close'] > curr['MA10']) and (vol_ratio >= 1.0):
            res.update({'grade': 'A'})
            
        if rsi_val >= 70: res['sell_desc'] = f"🔔 목표가 도달(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    mkt_status, mkt_reason, mkt_report = get_market_sentiment()
    my_codes = load_portfolio()
    
    krx_listing = fdr.StockListing('KRX')
    fund_df = get_market_fundamental()
    
    # 데이터 병합 및 결측치 처리
    all_stocks = pd.merge(krx_listing, fund_df, on='Code', how='left')
    all_stocks[['PBR', 'PER']] = all_stocks[['PBR', 'PER']].fillna(99)
    all_stocks['Sector'] = all_stocks['Sector'].fillna('기타')
    
    robust_market = all_stocks[all_stocks['Marcap'] >= 500_000_000_000]
    
    portfolio_res, s_list, a_list = [], [], []
    
    with ThreadPoolExecutor(max_workers=12) as executor:
        p_tasks = [executor.submit(analyze_stock, code, row['Name'], row['Sector'], row['PBR'], row['PER'], True) 
                   for code in my_codes for _, row in all_stocks[all_stocks['Code']==code].iterrows()]
        m_tasks = [executor.submit(analyze_stock, row['Code'], row['Name'], row['Sector'], row['PBR'], row['PER'], False) 
                   for _, row in robust_market.iterrows()]
        
        for future in as_completed(p_tasks + m_tasks):
            r = future.result()
            if not r: continue
            if r['is_portfolio']:
                portfolio_res.append(f"- {r['name']}: {r['sell_desc'] or '보유유지'} (RSI:{r['rsi']:.1f}, PBR:{r['pbr']:.1f})")
            else:
                if r['grade'] == 'S': s_list.append(r)
                elif r['grade'] == 'A': a_list.append(r)

    s_list = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)
    final_s = s_list[:5]
    final_a = (s_list[5:] + sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True))[:10]

    msg = f"🌿 **rootee님, 듬직한 우량주 리포트**\n\n📊 **시장**: {mkt_status}\n🧐 **근거**: {mkt_reason}\n{mkt_report}\n\n"
    msg += "📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    msg += "💎 **S급: 저평가 우량주 (PBR 0.5~4.0)**\n"
    if final_s:
        for r in final_s: msg += f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배, PBR:{r['pbr']:.1f})\n"
    else: msg += "- 조건 충족 없음\n"
    msg += "\n✨ **A급: 안정적 추세안착**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, PBR:{r['pbr']:.1f})" for r in final_a]) if final_a else "- 없음"
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
