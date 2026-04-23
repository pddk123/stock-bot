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
    
    avg_chg = total_chg / len(indices)
    sentiment = "BULL" if scores >= 2 else "BEAR"
    return sentiment, report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sentiment, is_portfolio=False):
    """건실 기업 로직이 강화된 개별 종목 분석"""
    try:
        # 데이터 로드 (최근 120일)
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        # 보조지표 계산
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        
        curr, prev = df.iloc[-1], df.iloc[-2]
        vol_ratio, rsi_val = curr['Volume'] / curr['Vol_MA5'], df['RSI'].iloc[-1]
        
        res = {'name': name, 'symbol': symbol, 'is_portfolio': is_portfolio, 
               'grade': None, 'desc': "", 'sell_desc': "", 'rsi': rsi_val}
        
        metrics = f"(RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"

        # [매수 로직 - BULL 마켓에서만 공격적 분석]
        if sentiment == "BULL":
            # S급: RSI 과매도 구간이면서 거래량이 터지며 20일선 돌파 (강한 반등 시그널)
            if (rsi_val <= 40) and (curr['Close'] > curr['MA20']) and (vol_ratio > 1.2):
                res.update({'grade': 'S', 'desc': f"건실주 추세전환 {metrics}"})
            # A급: 단기 눌림목 후 10일선 지지 반등
            elif (prev['Close'] < prev['MA10']) and (curr['Close'] > prev['MA10']) and (vol_ratio > 0.8):
                res.update({'grade': 'A', 'desc': f"10일선 반등 {metrics}"})

        # [매도 로직]
        if rsi_val >= 80:
            res['sell_desc'] = f"🔥 즉시매도(극심과열) {metrics}"
        elif rsi_val >= 70 and curr['Close'] < curr['MA10']:
            res['sell_desc'] = f"📢 매도결행(추세이탈) {metrics}"
            
        return res if (res['grade'] or res['sell_desc'] or is_portfolio) else None
    except: return None

def main():
    sentiment, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    # KRX 전체 상장사 정보 가져오기 (시가총액 필터링용)
    # 컬럼명이 버전별로 다를 수 있어 'Marcap'을 우선 찾음
    krx_listing = fdr.StockListing('KRX')
    
    # 1. 건실 기업 필터링 (시가총액 1,500억 이상만 분석 대상으로 선정)
    # 피코그램 등 시총이 너무 작은 종목을 여기서 1차로 걸러냅니다.
    min_cap = 150_000_000_000 # 1,500억 기준
    robust_stocks = krx_listing[krx_listing['Marcap'] >= min_cap]
    
    # 이름 매핑 및 분석 대상 선정
    name_map = dict(zip(robust_stocks['Code'], robust_stocks['Name']))
    
    # 분석 대상: 내 포트폴리오 + 시총 필터링된 상위 종목들
    target_market = robust_stocks.head(400) # 시총 상위 400개 기업 집중 분석
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        # 1. 보유 종목 분석
        p_futures = [executor.submit(analyze_stock, code, name_map.get(code, code), sentiment, True) for code in my_stock_codes]
        # 2. 필터링된 시장 종목 분석
        m_futures = [executor.submit(analyze_stock, row['Code'], row['Name'], sentiment, False) for _, row in target_market.iterrows()]
        
        for future in as_completed(p_futures + m_futures):
            r = future.result()
            if not r: continue
            
            if r['is_portfolio']:
                status = "보유유지"
                if r['sell_desc']: status = f"매도검토({r['sell_desc']})"
                elif r['grade']: status = f"추가매수권장({r['grade']}급: {r['desc']})"
                portfolio_res.append(f"- {r['symbol']}({r['name']}): {status}")
            else:
                if r['grade'] == 'S': s_list.append(f"- *{r['name']}*: {r['desc']}")
                elif r['grade'] == 'A': a_list.append(f"- {r['name']}: {r['desc']}")
                if r['sell_desc']: sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 메시지 조립
    report = f"{mkt_report}\n"
    report += "📁 내 보유 종목 현황\n" + ("\n".join(portfolio_res) if portfolio_res else "- 등록된 종목 없음") + "\n\n"
    report += "💎 건실 S급 후보 (시총 1,500억↑)\n" + ("\n".join(s_list[:10]) if s_list else "- 조건 충족 종목 없음") + "\n\n"
    report += "✨ 우량 A급 후보\n" + ("\n".join(a_list[:10]) if a_list else "- 없음") + "\n\n"
    report += "🔔 매도 추천 리스트\n" + ("\n".join(sell_list[:10]) if sell_list else "- 없음")
    
    send_telegram_message(report)

if __name__ == "__main__":
    main()
