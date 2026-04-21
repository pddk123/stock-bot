import FinanceDataReader as fdr
import pandas as pd
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_market_sentiment():
    """1단계: 글로벌 및 국내 시장 분위기 파악 (BULL/BEAR 판별)"""
    print("🌍 글로벌 시장 분석 중...")
    
    # 미국 증시 데이터 (나스닥, S&P 500)
    us_indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC'}
    us_score = 0
    # 최근 10일치 데이터를 가져와 마지막 2거래일 비교
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
    
    for name, ticker in us_indices.items():
        try:
            df_us = fdr.DataReader(ticker, start_date)
            if len(df_us) < 2: continue
            change = (df_us['Close'].iloc[-1] - df_us['Close'].iloc[-2]) / df_us['Close'].iloc[-2] * 100
            # -0.5% 이상이면 시장이 견조하다고 판단
            if change > -0.5: us_score += 1
            print(f"  - 미 증시({name}): {change:+.2f}%")
        except: continue

    # 국내 증시 (KOSPI 5일 이평선 위에 있는지 확인)
    df_ko = fdr.DataReader('KS11', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    df_ko['MA5'] = df_ko['Close'].rolling(window=5).mean()
    is_ko_up = df_ko['Close'].iloc[-1] > df_ko['MA5'].iloc[-1]
    print(f"  - 국내 증시(KOSPI): {'상승추세' if is_ko_up else '하향추세'}")

    # 미국 지수 1개 이상 안정 + 코스피 상승추세일 때만 공격적 매수(BULL)
    return "BULL" if (us_score >= 1 and is_ko_up) else "BEAR"

def calculate_rsi(series, period=14):
    """최적화된 RSI 계산 (Pandas 벡터 연산)"""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def analyze_single_stock(symbol, name, sentiment, start_date, end_date):
    """개별 종목 분석 로직 (Thread용)"""
    try:
        df = fdr.DataReader(symbol, start_date, end_date)
        if len(df) < 30: return None
        
        # --- 핵심 전략 지표: 10일선(MA10) 적용 ---
        df['MA10'] = df['Close'].rolling(window=10).mean()
        df['Vol_MA5'] = df['Volume'].rolling(window=5).mean()
        df['RSI'] = calculate_rsi(df['Close'])

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        result = {'name': name, 'symbol': symbol, 'type': None, 'rsi': curr['RSI']}

        if sentiment == "BULL":
            # 1. 완화된 RSI 기준 (40 이하)
            if curr['RSI'] <= 40:
                result['type'] = 'BUY_OVERSOLD'
            
            # 2. 10일선 상향 돌파 + 거래량 동반
            elif (prev['Close'] < prev['MA10']) and (curr['Close'] > curr['MA10']) and (curr['Volume'] > curr['Vol_MA5']):
                result['type'] = 'BUY_BREAKOUT'
        
        # 매도 조건: RSI 70 이상 과열 시
        if curr['RSI'] >= 70:
            result['type'] = 'SELL_OVERHEAT'
            
        return result if result['type'] else None
    except:
        return None

def main():
    sentiment = get_market_sentiment()
    print(f"\n📢 현재 시장 판단: [{'안정(매수 가능)' if sentiment == 'BULL' else '위험(관망 권장)'}]")

    # 1. 시가총액 기준 정렬하여 우량주 위주로 스캔 (KOSPI 200, KOSDAQ 150)
    print("🔍 우량주 리스트 필터링 중...")
    k200 = fdr.StockListing('KOSPI').sort_values(by='MarCap', ascending=False).head(200)
    kd150 = fdr.StockListing('KOSDAQ').sort_values(by='MarCap', ascending=False).head(150)
    target_stocks = pd.concat([k200, kd150])

    buy_candidates = []
    sell_candidates = []
    
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')

    # 2. 멀티스레딩 분석 (속도 극대화)
    print(f"🚀 {len(target_stocks)}개 종목 40-10 전략 분석 시작...")
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [
            executor.submit(analyze_single_stock, row['Code'], row['Name'], sentiment, start_date, end_date) 
            for _, row in target_stocks.iterrows()
        ]
        
        for future in as_completed(futures):
            res = future.result()
            if res:
                if res['type'] == 'BUY_OVERSOLD':
                    buy_candidates.append(f"{res['name']}({res['symbol']}) [과매도-RSI40]")
                elif res['type'] == 'BUY_BREAKOUT':
                    buy_candidates.append(f"{res['name']}({res['symbol']}) [10일선돌파]")
                elif res['type'] == 'SELL_OVERHEAT':
                    sell_candidates.append(f"{res['name']}({res['symbol']}) - RSI:{res['rsi']:.1f}")

    # 3. 최종 결과 출력
    print("\n" + "="*70)
    if sentiment == "BEAR":
        print("⚠️ [시장 경보] 하락 추세입니다. 신규 매수보다는 보유 종목 관리에 집중하세요.")
    else:
        print(f"✅ 매수 후보 (총 {len(buy_candidates)}건):")
        if buy_candidates:
            print(", ".join(buy_candidates[:20]) + (" 등" if len(buy_candidates) > 20 else ""))
        else:
            print("조건에 맞는 종목이 없습니다.")

    print(f"\n⚠️ 매도(과열) 주의 (총 {len(sell_candidates)}건):")
    if sell_candidates:
        print(", ".join(sell_candidates[:15]) + (" 등" if len(sell_candidates) > 15 else ""))
    else:
        print("과열 종목이 없습니다.")
    print("="*70)

if __name__ == "__main__":
    main()
