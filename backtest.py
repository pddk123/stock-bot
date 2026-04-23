import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def run_backtest():
    initial_balance = 10_000_000
    balance = initial_balance
    
    print("🚀 시뮬레이션 데이터를 가져오는 중입니다...")
    try:
        df_krx = fdr.StockListing('KRX')
    except Exception as e:
        print(f"데이터 로드 실패: {e}")
        return

    # [수정 포인트] 시가총액 컬럼 자동 감지
    cols = df_krx.columns
    marcap_col = 'MarCap' if 'MarCap' in cols else 'MarketCap' if 'MarketCap' in cols else None
    
    if not marcap_col:
        print("❌ 시가총액 정보를 찾을 수 없어 시뮬레이션을 중단합니다.")
        return

    # 1. 건실한 종목 필터링 (로티님 전용 로직)
    mask = (df_krx[marcap_col] >= 200_000_000_000)
    if 'PBR' in cols:
        mask &= (df_krx['PBR'] >= 0.3)
    
    is_red_bio = pd.Series(False, index=df_krx.index)
    if 'PER' in cols and 'Sector' in cols:
        is_red_bio = (df_krx['PER'] <= 0) & (df_krx['Sector'].str.contains('의약|제약|바이오|생물|헬스케어', na=False))
    
    targets = df_krx[mask & ~is_red_bio].sort_values(by=marcap_col, ascending=False).head(50)
    
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    print(f"📊 총 {len(targets)}개 우량 종목을 대상으로 1년 백테스트를 시작합니다.")

    # 2. 가상 매매 시뮬레이션
    total_profit_factor = 1.0
    for _, row in targets.iterrows():
        symbol, name = row['Code'], row['Name']
        try:
            df = fdr.DataReader(symbol, start_date, end_date)
            if len(df) < 50: continue
            
            df['RSI'] = calculate_rsi(df['Close'])
            df['MA10'] = df['Close'].rolling(10).mean()
            
            # 간단한 스윙 전략 시뮬레이션
            buy_price = 0
            for i in range(20, len(df)):
                curr = df.iloc[i]
                
                # 매수 시점: RSI 30 이하 (과매도)
                if buy_price == 0 and curr['RSI'] <= 30:
                    buy_price = curr['Close']
                
                # 매도 시점: RSI 72 이상 & 10일선 이탈 (매도 알림 로직)
                elif buy_price > 0:
                    if curr['RSI'] >= 80 or (curr['RSI'] >= 72 and curr['Close'] < curr['MA10']):
                        profit = (curr['Close'] / buy_price) - 1
                        total_profit_factor *= (1 + profit)
                        buy_price = 0
        except:
            continue

    final_balance = initial_balance * total_profit_factor
    
    print("\n" + "="*30)
    print(f"🏆 [백테스트 결과 보고서]")
    print(f"초기 투자금: {initial_balance:,}원")
    print(f"1년 뒤 예상 자산: {int(final_balance)::,}원")
    print(f"누적 수익률: {((final_balance/initial_balance)-1)*100:.2f}%")
    print("="*30)

if __name__ == "__main__":
    run_backtest()
