import FinanceDataReader as fdr
import pandas as pd
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_market_sentiment():
    """1단계: 글로벌 및 국내 시장 흐름 파악"""
    print("🌍 글로벌 시장 분석 중...")
    us_indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC'}
    us_score = 0
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
    
    for name, ticker in us_indices.items():
        try:
            df_us = fdr.DataReader(ticker, start_date)
            if len(df_us) < 2: continue
            change = (df_us['Close'].iloc[-1] - df_us['Close'].iloc[-2]) / df_us['Close'].iloc[-2] * 100
            if change > -0.5: us_score += 1
            print(f"  - 미 증시({name}): {change:+.2f}%")
        except: continue

    try:
        df_ko = fdr.DataReader('KS11', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
        df_ko['MA5'] = df_ko['Close'].rolling(window=5).mean()
        is_ko_up = df_ko['Close'].iloc[-1] > df_ko['MA5'].iloc[-1]
        print(f"  - 국내 증시(KOSPI): {'상승추세' if is_ko_up else '하향추세'}")
    except:
        is_ko_up = False

    return "BULL" if (us_score >= 1 and is_ko_up) else "BEAR"

def calculate_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def analyze_single_stock(symbol, name, sentiment, start_date, end_date):
    try:
        df = fdr.DataReader(symbol, start_date, end_date)
        if len(df) < 30: return None
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(window=5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        result = {'name': name, 'symbol': symbol, 'type': None, 'rsi': curr['RSI']}
        if sentiment == "BULL":
            if curr['RSI'] <= 30: result['type'] = 'BUY_OVERSOLD'
            elif (prev['Close'] < prev['MA20']) and (curr['Close'] > curr['MA20']) and (curr['Volume'] > curr['Vol_MA5']):
                result['type'] = 'BUY_BREAKOUT'
        if curr['RSI'] >= 70: result['type'] = 'SELL_OVERHEAT'
        return result if result['type'] else None
    except: return None

def main():
    sentiment = get_market_sentiment()
    print(f"\n📢 현재 시장 판단: [{'안정(매수 가능)' if sentiment == 'BULL' else '위험(관망 권장)'}]")

    print("🔍 종목 리스트 업데이트 중...")
    # 시가총액 컬럼명 이슈를 피하기 위해 상위 200/150개를 가져오는 방식 수정
    k200 = fdr.StockListing('KOSPI').head(200)
    kd150 = fdr.StockListing('KOSDAQ').head(150)
    target_stocks = pd.concat([k200, kd150])

    buy_candidates, sell_candidates = [], []
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')

    print(f"🚀 {len(target_stocks)}개 종목 병렬 분석 시작...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(analyze_single_stock, row['Code'], row['Name'], sentiment, start_date, end_date) for _, row in target_stocks.iterrows()]
        for future in as_completed(futures):
            res = future.result()
            if res:
                if res['type'] and res['type'].startswith('BUY'):
                    label = "[과매도]" if res['type'] == 'BUY_OVERSOLD' else "[이평선돌파]"
                    buy_candidates.append(f"{res['name']}({res['symbol']}) {label}")
                elif res['type'] == 'SELL_OVERHEAT':
                    sell_candidates.append(f"{res['name']}({res['symbol']}) - RSI:{res['rsi']:.1f}")

    print("\n" + "="*60)
    if sentiment == "BEAR":
        print("⚠️ [시장 경보] 하락장입니다. 관망을 추천합니다.")
    else:
        print(f"✅ 매수 후보 (총 {len(buy_candidates)}건): " + (", ".join(buy_candidates[:15]) if buy_candidates else "없음"))
    print(f"\n⚠️ 매도 주의 (총 {len(sell_candidates)}건): " + (", ".join(sell_candidates[:15]) if sell_candidates else "없음"))
    print("="*60)

if __name__ == "__main__":
    main()
