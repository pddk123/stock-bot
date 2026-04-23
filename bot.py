import FinanceDataReader as fdr
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

def load_portfolio():
    """portfolio.txt에서 종목 코드를 읽어옴"""
    codes = []
    try:
        if os.path.exists('portfolio.txt'):
            with open('portfolio.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    clean_line = line.split('#')[0].strip()
                    if not clean_line: continue
                    parts = clean_line.replace(',', ' ').split()
                    for code in parts:
                        if code.strip(): codes.append(code.strip())
            return list(set(codes))
    except: pass
    return []

def get_market_sentiment():
    """시장 동향 분석"""
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    report = "📊 시장 동향 브리핑\n"
    scores, total_chg = 0, 0
    
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            curr, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
            chg = (curr - prev) / prev * 100
            total_chg += chg
            report += f"- {name}: {chg:+.2f}%\n"
            if chg > -0.5: scores += 1
        except: continue
    
    sentiment = "BULL" if scores >= 2 else "BEAR"
    return sentiment, report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sentiment, is_portfolio=False):
    """추세 대폭발 초입 종목을 찾아내는 로직"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        # 지표 계산
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        
        curr, prev = df.iloc[-1], df.iloc[-2]
        vol_ratio = curr['Volume'] / curr['Vol_MA5']
        rsi_val = df['RSI'].iloc[-1]
        
        res = {'name': name, 'symbol': symbol, 'is_portfolio': is_portfolio, 
               'grade': None, 'desc': "", 'sell_desc': "", 'rsi': rsi_val}
        
        metrics = f"(RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"

        # --- 핵심 로직: 추세 추종 매매 (Trend Following) ---
        
        # 1. [S급: 추세 대폭발 초입] 
        # 에너지가 응축(RSI 45~60)되었다가 강력한 거래량과 함께 정배열로 진입하는 순간
        is_s_class = (45 <= rsi_val <= 62) and \
                     (curr['Close'] > curr['MA10'] > curr['MA20']) and \
                     (vol_ratio >= 1.5) and \
                     (curr['Close'] > prev['Close'])

        if is_s_class:
            res.update({'grade': 'S', 'desc': f"🚀 추세 폭발 초입 {metrics}"})
        
        # 2. [A급: 안정적 추세 지속/눌림목]
        # 이미 상승 추세이며, 10일선 지지를 받으며 재차 머리를 드는 종목
        elif (rsi_val > 50) and \
             (curr['Close'] > curr['MA10']) and \
             (prev['Close'] <= prev['MA10'] or (0.98 <= curr['Close']/curr['MA10'] <= 1.02)) and \
             (vol_ratio >= 1.0):
            res.update({'grade': 'A', 'desc': f"✨ 안정적 추세안착 {metrics}"})

        # [매도 로직: 과열권 진입]
        if rsi_val >= 80:
            res['sell_desc'] = f"🔥 과열 매도검토 {metrics}"
            
        return res if (res['grade'] or res['sell_desc'] or is_portfolio) else None
    except: return None

def main():
    sentiment, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    # KRX 리스팅 및 시총 필터링 (1,500억 이상 건실 기업)
    krx_listing = fdr.StockListing('KRX')
    robust_stocks = krx_listing[krx_listing['Marcap'] >= 150_000_000_000]
    name_map = dict(zip(robust_stocks['Code'], robust_stocks['Name']))
    
    # 시총 상위 400개 종목 위주 분석
    target_market = robust_stocks.head(400)
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        p_futures = [executor.submit(analyze_stock, code, name_map.get(code, code), sentiment, True) for code in my_stock_codes]
        m_futures = [executor.submit(analyze_stock, row['Code'], row['Name'], sentiment, False) for _, row in target_market.iterrows()]
        
        for future in as_completed(p_futures + m_futures):
            r = future.result()
            if not r: continue
            
            if r['is_portfolio']:
                status = "보유유지"
                if r['sell_desc']: status = f"매도검토({r['sell_desc']})"
                elif r['grade']: status = f"추가매수권장({r['grade']}급)"
                portfolio_res.append(f"- {r['symbol']}({r['name']}): {status}")
            else:
                if r['grade'] == 'S': s_list.append(f"- *{r['name']}*: {r['desc']}")
                elif r['grade'] == 'A': a_list.append(f"- {r['name']}: {r['desc']}")
                if r['sell_desc']: sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 결과 전송
    report = f"🌿 rootee님, 오늘의 'Smart Picking' 결과입니다.\n\n{mkt_report}\n"
    report += "💎 S급: 추세 폭발 초입 (2~4주 스윙 최적)\n" + ("\n".join(s_list[:10]) if s_list else "- 없음") + "\n\n"
    report += "✨ A급: 안정적 우상향 종목\n" + ("\n".join(a_list[:10]) if a_list else "- 없음") + "\n\n"
    report += "📁 내 포트폴리오 상태\n" + ("\n".join(portfolio_res) if portfolio_res else "- 데이터 없음") + "\n\n"
    report += "🔔 과열 주의보 (분할매도 권장)\n" + ("\n".join(sell_list[:10]) if sell_list else "- 없음")
    
    send_telegram_message(report)

if __name__ == "__main__":
    main()
