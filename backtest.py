import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [설정값] ---
INITIAL_CASH = 10_000_000  # 초기 자본금 1,000만원
MAX_POSITIONS = 5          # 최대 보유 종목 수
FEE = 0.002                # 거래 비용 (0.2%)
START_DATE = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
END_DATE = datetime.now().strftime('%Y-%m-%d')

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = roll_up / roll_down
    return 100.0 - (100.0 / (1.0 + rs))

def get_stock_data(symbol):
    """백테스트용 데이터 로드 및 지표 계산"""
    df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=100)).strftime('%Y-%m-%d'), END_DATE)
    if len(df) < 60: return None
    
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['RSI'] = calculate_rsi_wilder(df['Close'])
    df['Vol_MA5'] = df['Volume'].shift(1).rolling(5).mean()
    df['Vol_Ratio'] = df['Volume'] / df['Vol_MA5']
    df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
    
    # 등급 판정 (S급 우선)
    df['Signal'] = ((df['Close'] > df['MA10']) & (df['MA10'] > df['MA20']) & (df['MA20'] > df['MA60']) & 
                    (df['Amount'] >= 50) & (df['RSI'].between(45, 63)) & (df['Vol_Ratio'] >= 1.5))
    return df.loc[START_DATE:]

def run_backtest():
    print(f"🚀 {START_DATE} ~ {END_DATE} 백테스트 시작...")
    
    # 1. 대상 종목 선정 (시총 상위 우량주 100종목 예시)
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(100, 'Marcap')['Code'].tolist()
    
    # 2. 지수 데이터 (필터링용)
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    market_filter = kospi['Close'] > kospi['MA20']

    # 3. 데이터 사전 로드
    all_data = {}
    for code in top_stocks:
        data = get_stock_data(code)
        if data is not None: all_data[code] = data

    # 4. 시뮬레이션 변수
    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'entry_price': p}}
    history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # [A] 매도 로직 (보유 종목 체크)
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_row = df.loc[today]
            # 매도 조건: MA10 이탈 또는 RSI 과열(70)
            if curr_row['Close'] < curr_row['MA10'] or curr_row['RSI'] >= 70:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_row['Close']
                cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # [B] 매수 로직 (시장 상황이 좋을 때만)
        if market_filter.loc[today] and len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index and df.loc[today]['Signal']:
                    candidates.append((symbol, df.loc[today]['Vol_Ratio']))
            
            # 거래량 비율이 높은 순으로 매수
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price}
                        cash -= (qty * buy_price) * (1 + FEE)

        # [C] 일일 가치 기록
        total_val = cash
        for symbol, pos in positions.items():
            df = all_data[symbol]
            price = df.loc[today]['Close'] if today in df.index else pos['entry_price']
            total_val += price * pos['qty']
        history.append(total_val)

    # 5. 결과 리포트
    final_val = history[-1]
    return_pct = (final_val - INITIAL_CASH) / INITIAL_CASH * 100
    print(f"\n--- [백테스트 결과] ---")
    print(f"최종 자산: {final_val:,.0f}원")
    print(f"누적 수익률: {return_pct:.2f}%")
    print(f"최대 낙폭(MDD): {((min(history) - max(history)) / max(history) * 100):.2f}%")

if __name__ == "__main__":
    run_backtest()
