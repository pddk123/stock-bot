import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / (ema_down.replace(0, 1e-9))
    return 100 - (100 / (1 + rs))

def run_backtest():
    initial_balance = 10_000_000
    total_profit_factor = 1.0
    
    # [수정] 10년치 데이터 설정 (3650일)
    start_date = (datetime.now() - timedelta(days=3650)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    print(f"🚀 {start_date}부터 10년치 대장정 시뮬레이션 시작...")
    
    try:
        df_krx = fdr.StockListing('KRX')
    except: return

    cols = df_krx.columns
    marcap_col = next((c for c in cols if c.lower() in ['marcap', 'marketcap', '시가총액']), None)
    
    # 10년 테스트이므로 확실한 우량주 30개만 먼저 봅니다 (속도와 안정성)
    targets = df_krx.sort_values(by=marcap_col, ascending=False).head(30) if marcap_col else df_krx.head(30)

    for _, row in targets.iterrows():
        symbol, name = row['Code'], row['Name']
        try:
            df = fdr.DataReader(symbol, start_date, end_date)
            if len(df) < 200: continue # 데이터가 너무 적으면 패스
            
            df['RSI'] = calculate_rsi(df['Close'])
            df['MA10'] = df['Close'].rolling(10).mean()
            
            buy_price = 0
            for i in range(20, len(df)):
                curr = df.iloc[i]
                # 매수/매도 로직 동일
                if buy_price == 0 and curr['RSI'] <= 30:
                    buy_price = curr['Close']
                elif buy_price > 0:
                    if curr['RSI'] >= 80 or (curr['RSI'] >= 72 and curr['Close'] < curr['MA10']):
                        profit = (curr['Close'] / buy_price) - 1
                        total_profit_factor *= (1 + profit)
                        buy_price = 0
            print(f"✅ {name} 분석 완료")
        except: continue

    final_balance = initial_balance * total_profit_factor
    print("\n" + "="*40)
    print(f"🏆 [로티's 10년 장기 집권 성적표]")
    print(f"💰 초기 자금: {initial_balance:,}원")
    print(f"📈 10년 뒤 자산: {int(final_balance):,}원")
    print(f"🔥 누적 수익률: {((total_profit_factor)-1)*100:.2f}%")
    print("="*40)

if __name__ == "__main__":
    run_backtest()
