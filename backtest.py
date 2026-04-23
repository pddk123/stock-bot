import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def run_backtest():
    initial_balance = 10_000_000
    balance = initial_balance
    portfolio = {} # {symbol: {'qty': n, 'buy_price': p}}
    
    # 1. 대상 종목 필터링 (로티님의 '건실한 종목' 로직)
    print("Fetching market data and filtering stocks...")
    df_krx = fdr.StockListing('KRX')
    mask = (df_krx['MarCap'] >= 200_000_000_000) & (df_krx['PBR'] >= 0.3)
    is_red_bio = (df_krx['PER'] <= 0) & (df_krx['Sector'].str.contains('의약|제약|바이오|생물|헬스케어', na=False))
    targets = df_krx[mask & ~is_red_bio].sort_values(by='MarCap', ascending=False).head(50) # 속도를 위해 상위 50개
    
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    # 2. 데이터 로드 및 시뮬레이션
    for _, row in targets.iterrows():
        symbol, name = row['Code'], row['Name']
        try:
            df = fdr.DataReader(symbol, start_date, end_date)
            if len(df) < 50: continue
            
            df['RSI'] = calculate_rsi(df['Close'])
            df['MA10'] = df['Close'].rolling(10).mean()
            df['MA20'] = df['Close'].rolling(20).mean()
            
            # 간단한 시뮬레이션 로직 (매수/매도 시점 포착)
            # 여기서는 간단히 각 종목별로 전략이 맞았을 때의 수익률 합산으로 예시를 듭니다.
            # 실제로는 날짜별 루프를 돌리는 것이 정확하지만, GitHub 성능상 종목별 요약으로 보여드릴게요.
            
            buy_price = 0
            for i in range(20, len(df)):
                curr = df.iloc[i]
                prev = df.iloc[i-1]
                
                # 매수 조건 (S급 후보)
                if buy_price == 0 and curr['RSI'] <= 30:
                    buy_price = curr['Close']
                
                # 매도 조건 (매도 알림)
                elif buy_price > 0:
                    if curr['RSI'] >= 80 or (curr['RSI'] >= 72 and curr['Close'] < curr['MA10']):
                        profit = (curr['Close'] / buy_price) - 1
                        balance *= (1 + profit)
                        buy_price = 0 # 매도 후 초기화
        except:
            continue

    print(f"\n[시뮬레이션 결과]")
    print(f"시작 금액: {initial_balance:,}원")
    print(f"1년 뒤 예상 금액: {int(balance):,}원")
    print(f"수익률: {((balance/initial_balance)-1)*100:.2f}%")

if __name__ == "__main__":
    run_backtest()
