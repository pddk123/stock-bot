import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [설정값] ---
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
FEE = 0.002
# --- [설정값 수정] ---
START_DATE = '2024-01-01'  # 테스트 시작일
END_DATE = '2025-01-01'    # 테스트 종료일 (25년 초)

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = roll_up / roll_down
    return 100.0 - (100.0 / (1.0 + rs))

def get_stock_data(symbol):
    df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=100)).strftime('%Y-%m-%d'), END_DATE)
    if len(df) < 60: return None
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['RSI'] = calculate_rsi_wilder(df['Close'])
    df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
    df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
    df['Signal'] = ((df['Close'] > df['MA10']) & (df['MA10'] > df['MA20']) & (df['MA20'] > df['MA60']) & 
                    (df['Amount'] >= 50) & (df['RSI'].between(45, 63)) & (df['Vol_Ratio'] >= 1.5))
    return df.loc[START_DATE:]

def run_backtest():
    print(f"🚀 {START_DATE} ~ {END_DATE} 현실 밀착형 백테스트 시작...")
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(150, 'Marcap')['Code'].tolist() # 시총 상위 150종목 대상
    
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    market_filter = kospi['Close'] > kospi['MA20']

    all_data = {}
    for code in top_stocks:
        data = get_stock_data(code)
        if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} 
    trade_log = [] # 각 매매의 수익률을 기록할 리스트
    history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # [1] 매도 로직
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_row = df.loc[today]
            if curr_row['Close'] < curr_row['MA10'] or curr_row['RSI'] >= 70:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_row['Close']
                
                # 수익률 계산 및 기록
                revenue = (sell_price * pos['qty']) * (1 - FEE)
                pnl_pct = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl_pct)
                
                cash += revenue
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # [2] 매수 로직
        if market_filter.loc[today] and len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index and df.loc[today]['Signal']:
                    candidates.append((symbol, df.loc[today]['Vol_Ratio']))
            
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

        # [3] 총자산 기록
        total_val = cash
        for symbol, pos in positions.items():
            df = all_data[symbol]
            price = df.loc[today]['Close'] if today in df.index else pos['entry_price']
            total_val += price * pos['qty']
        history.append(total_val)

    # --- [결과 분석] ---
    history_ser = pd.Series(history)
    final_val = history[-1]
    total_return = (final_val - INITIAL_CASH) / INITIAL_CASH * 100
    
    # 1) 종목별 승률
    win_trades = [r for r in trade_log if r > 0]
    win_rate = (len(win_trades) / len(trade_log) * 100) if trade_log else 0
    
    # 2) 매도당 평균 수익률
    avg_return = np.mean(trade_log) if trade_log else 0
    
    # 3) 최대 낙폭 (MDD)
    roll_max = history_ser.cummax()
    drawdown = (history_ser - roll_max) / roll_max
    mdd = drawdown.min() * 100

    print(f"\n✨ rootee님을 위한 v5.7 백테스트 성적표")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 최종 자산: {final_val:,.0f}원")
    print(f"📈 누적 수익률: {total_return:.2f}%")
    print(f"🎯 종목별 승률: {win_rate:.2f}% (총 {len(trade_log)}회 매매)")
    print(f"💸 매도당 평균 수익률: {avg_return:.2f}%")
    print(f"📉 최대 낙폭 (MDD): {mdd:.2f}%")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    run_backtest()
