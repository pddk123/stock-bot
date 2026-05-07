import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.3 Optimized 설정값] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000 
MAX_POSITIONS = 5
FEE = 0.002               
TARGET_PROFIT = 0.10      
ATR_MULTIPLIER = 1.5      # [수정] 2.0 -> 1.5 (손절을 더 민감하게)
TIME_CUT_DAYS = 20        # [수정] 15일 -> 20일 (매매 횟수 감소 및 수수료 절감)
MOMENTUM_THRESHOLD = 0.03 
MOMENTUM_CEILING = 0.70   # [수정] 0.50 -> 0.70 (수익의 천장을 높여 대장주 추종)
RSI_OVERHEAT = 75
PERMANENT_BLACKLIST = ['440110'] # 파두 등 제외

def calculate_atr(df, n=14):
    high = df['High']; low = df['Low']; close = df['Close']
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_stock_data(symbol):
    try:
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 100: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = calculate_atr(df)
        
        delta = df['Close'].diff()
        up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
        roll_up = up.ewm(alpha=1/14, adjust=False).mean()
        roll_down = down.ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))
        
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        return df.loc[START_DATE:]
    except:
        return None

def run_backtest():
    print(f"## 🚀 v8.3 Optimized Backtest Report")
    print(f"**Analysis Period:** {START_DATE} ~ {END_DATE}")
    print(f"**Key Tweak:** Ceiling 70% / ATR 1.5x / Time-cut 20d\n")
    
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    kospi['Momentum'] = kospi['Close'].pct_change(20)
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=15) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {}
    trade_log = []
    history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        market_alive = kospi.loc[today]['Close'] > kospi.loc[today]['MA20']
        idx_ret = kospi.loc[today]['Momentum']
        
        # --- Sell Logic ---
        to_sell = []
        for s, pos in positions.items():
            df = all_data[s]
            if today not in df.index: continue
            curr = df.loc[today]
            profit = (curr['Close'] / pos['price']) - 1
            days = (today - pos['date']).days
            pos['max'] = max(pos.get('max', 0), profit)
            
            stop = pos['price'] - (ATR_MULTIPLIER * pos['atr'])
            if pos['max'] >= 0.03: stop = max(stop, pos['price'])

            trigger = False
            if profit >= TARGET_PROFIT: trigger = True
            elif curr['Close'] < stop: trigger = True
            elif days >= TIME_CUT_DAYS and profit < 0.03: trigger = True
            
            if trigger:
                s_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                if s_price > 0:
                    pnl = (s_price / pos['price'] - 1) * 100 - (FEE * 2 * 100)
                    trade_log.append(pnl)
                    cash += (s_price * pos['qty']) * (1 - FEE)
                to_sell.append(s)
        for s in to_sell: del positions[s]

        # --- Buy Logic ---
        if market_alive and len(positions) < MAX_POSITIONS:
            cands = []
            for s, df in all_data.items():
                if s not in positions and today in df.index and s not in PERMANENT_BLACKLIST:
                    row = df.loc[today]
                    # 최적화 필터 적용
                    if row['Amount'] >= 300 and (idx_ret + MOMENTUM_THRESHOLD) < row['Momentum'] < MOMENTUM_CEILING:
                        if row['Close'] > row['MA20'] and row['RSI'] < RSI_OVERHEAT:
                            cands.append((s, row['Momentum']))
            
            cands.sort(key=lambda x: x[1], reverse=True)
            for s, _ in cands:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[s]
                if tomorrow in df.index:
                    b_price = df.loc[tomorrow]['Open']
                    unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (unit * (1 - FEE)) // b_price
                    if qty > 0:
                        positions[s] = {'qty': qty, 'price': b_price, 'date': today, 'atr': df.loc[today]['ATR'], 'max': 0}
                        cash -= (qty * b_price) * (1 + FEE)

        val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(val)

    # --- Result Table ---
    h_ser = pd.Series(history)
    final_ret = ((history[-1]/INITIAL_CASH)-1)*100
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((h_ser - h_ser.cummax()) / h_ser.cummax()).min() * 100
    
    print(f"### 📊 Optimized Performance")
    print(f"| Metric | Result |")
    print(f"| :--- | :--- |")
    print(f"| **Final Asset** | {history[-1]:,.0f} KRW |")
    print(f"| **Total Return** | **{final_ret:.2f}%** |")
    print(f"| **Win Rate** | {win_rate:.2f}% |")
    print(f"| **Max Drawdown (MDD)** | **{mdd:.2f}%** |")
    print(f"| **Trade Count** | {len(trade_log)} |")

if __name__ == "__main__":
    run_backtest()
