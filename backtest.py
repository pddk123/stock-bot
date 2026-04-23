import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / (ema_down.replace(0, 1e-9)) # 0으로 나누기 방지
    return 100 - (100 / (1 + rs))

def run_backtest():
    initial_balance = 10_000_000
    total_profit_factor = 1.0
    
    print("🚀 시뮬레이션 시작...")
    try:
        df_krx = fdr.StockListing('KRX')
    except Exception as e:
        print(f"❌ 데이터 로드 실패: {e}")
        return

    cols = df_krx.columns.tolist()
    print(f"📌 현재 사용 가능한 데이터 항목: {cols}")

    # 1. 시가총액 컬럼 찾기 (더 유연한 검색)
    marcap_col = next((c for c in cols if c.lower() in ['marcap', 'marketcap', '시가총액', 'amount']), None)
    
    # 2. 필터링 로직 (컬럼이 있을 때만 적용)
    if marcap_col:
        print(f"✅ '{marcap_col}' 컬럼으로 우량주를 선별합니다.")
        mask = (df_krx[marcap_col] >= 200_000_000_000)
    else:
        print("⚠️ 시가총액 정보가 없어 모든 종목을 대상으로 검토합니다.")
        mask = pd.Series(True, index=df_krx.index)

    # 추가 재무 필터 (PBR, PER 등)
    if 'PBR' in cols:
        mask &= (df_krx['PBR'] >= 0.3)
    
    is_red_bio = pd.Series(False, index=df_krx.index)
    if 'PER' in cols and 'Sector' in cols:
        is_red_bio = (df_krx['PER'] <= 0) & (df_krx['Sector'].str.contains('의약|제약|바이오|생물|헬스케어', na=False))
    
    targets = df_krx[mask & ~is_red_bio]
    
    # 정렬 및 개수 제한 (시총 컬럼 없으면 코드순)
    if marcap_col:
        targets = targets.sort_values(by=marcap_col, ascending=False).head(50)
    else:
        targets = targets.head(50)

    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    print(f"📊 {len(targets)}개 종목 분석 중... (잠시만 기다려주세요)")

    for _, row in targets.iterrows():
        symbol, name = row['Code'], row['Name']
        try:
            df = fdr.DataReader(symbol, start_date, end_date)
            if len(df) < 50: continue
            
            df['RSI'] = calculate_rsi(df['Close'])
            df['MA10'] = df['Close'].rolling(10).mean()
            
            buy_price = 0
            for i in range(20, len(df)):
                curr = df.iloc[i]
                if buy_price == 0 and curr['RSI'] <= 30:
                    buy_price = curr['Close']
                elif buy_price > 0:
                    if curr['RSI'] >= 80 or (curr['RSI'] >= 72 and curr['Close'] < curr['MA10']):
                        profit = (curr['Close'] / buy_price) - 1
                        total_profit_factor *= (1 + profit)
                        buy_price = 0
        except:
            continue

    final_balance = initial_balance * total_profit_factor
    print("\n" + "="*40)
    print(f"🏆 [로티's 스마트 피킹 1년 성적표]")
    print(f"💰 초기 자금: {initial_balance:,}원")
    print(f"📈 최종 자산: {int(final_balance):,}원")
    print(f"🔥 수익률: {((total_profit_factor)-1)*100:.2f}%")
    print("="*40)

if __name__ == "__main__":
    run_backtest()
