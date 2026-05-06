import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v6.3 설정값: 스나이퍼 모드] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
FEE = 0.002
TARGET_PROFIT = 0.07 # 7% 목표 수익
TIME_CUT_DAYS = 30   # 30일 타임컷

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))

def get_stock_data(symbol):
    try:
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 80: return None
        df['MA10'] = df['Close'].rolling(10).mean(); df['MA20'] = df['Close'].rolling(20).mean(); df['MA60'] = df['Close'].rolling(60).mean()
        df['RSI'] = calculate_rsi_wilder(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v6.3 (7% Target & Time-cut) 백테스트: {START_DATE} ~ {END_DATE}")
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    top_stocks = fdr.StockListing('KRX').nlargest(200, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH; positions = {}; trade_log = []; history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]; tomorrow = trading_days[i+1]
        market_mode = 'BULL' if kospi.loc[today]['Close'] > kospi.loc[today]['MA20'] else 'SIDEWAYS'
        
        # [1] 매도 로직 (7% 익절 OR 30일 타임컷)
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_price = df.loc[today]['Close']
            profit_rate = (curr_price / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            
            # 조건 1: 7% 수익 달성
            # 조건 2: 30일 경과 (타임컷)
            if profit_rate >= TARGET_PROFIT or hold_days >= TIME_CUT_DAYS:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_price
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl); cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # [2] 매수 로직 (스마트 피킹 S급 진입)
        if len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] < 300: continue
                    # S급 진입 조건 유지
                    grade = 'S' if market_mode == 'BULL' and (row['Close'] > row['MA10'] > row['MA20']) and (45 <= row['RSI'] <= 72) else None
                    if not grade and market_mode == 'SIDEWAYS' and (row['Close'] > row['MA60']) and (35 <= row['RSI'] <= 50): grade = 'S'
                    if grade: candidates.append((symbol, row['Vol_Ratio']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates[:2]:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                buy_price = df.loc[tomorrow]['Open']
                buy_unit = cash / (MAX_POSITIONS - len(positions))
                qty = (buy_unit * (1 - FEE)) // buy_price
                if qty > 0:
                    positions[symbol] = {
                        'qty': qty, 
                        'entry_price': buy_price, 
                        'entry_date': today # 진입일 기록
                    }
                    cash -= (qty * buy_price) * (1 + FEE)

        # [3] 자산 기록
        total_val = cash
        for symbol, pos in positions.items():
            price = all_data[symbol].loc[today]['Close'] if today in all_data[symbol].index else pos['entry_price']
            total_val += price * pos['qty']
        history.append(total_val)

    # 결과 리포트
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((pd.Series(history) - pd.Series(history).cummax()) / pd.Series(history).cummax()).min() * 100
    print(f"\n🎯 v6.3 스나이퍼 모드 성적표")
    print(f"💰 수익률: {((history[-1]/INITIAL_CASH)-1)*100:.2f}% | 승률: {win_rate:.2f}% | MDD: {mdd:.2f}% | 매매: {len(trade_log)}회")

if __name__ == "__main__":
    run_backtest()
