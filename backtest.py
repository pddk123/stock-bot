import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.3 Ultimate Auto 설정값] ---
START_DATE = '2023-01-01' 
END_DATE = '2024-01-01'
INITIAL_CASH = 10_000_000 
MAX_POSITIONS = 5
FEE = 0.002               
TARGET_PROFIT = 0.10      
ATR_MULTIPLIER = 2.0      
TIME_CUT_DAYS = 15        
MOMENTUM_THRESHOLD = 0.03 
MOMENTUM_CEILING = 0.50   # 20일 수익률 50% 초과 종목 자동 차단
RSI_OVERHEAT = 75
PERMANENT_BLACKLIST = ['440110'] # 파두 등 제외 종목

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
    print(f"## 🚀 v8.3 Ultimate Auto Backtest Report")
    print(f"**Period:** {START_DATE} ~ {END_DATE}\n")
    
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    kospi['Momentum'] = kospi['Close'].pct_change(20)
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=15) as executor: # GitHub Runner 사양에 맞춰 조절
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
        
        market_is_alive = kospi.loc[today]['Close'] > kospi.loc[today]['MA20']
        idx_ret = kospi.loc[today]['Momentum']
        
        # --- Sell Logic ---
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr = df.loc[today]
            profit_rate = (curr['Close'] / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            pos['max_profit'] = max(pos.get('max_profit', 0), profit_rate)
            
            stop_price = pos['entry_price'] - (ATR_MULTIPLIER * pos['entry_atr'])
            if pos['max_profit'] >= 0.03: stop_price = max(stop_price, pos['entry_price'])

            sell_trigger = False
            if profit_rate >= TARGET_PROFIT: sell_trigger = True
            elif curr['Close'] < stop_price: sell_trigger = True
            elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: sell_trigger = True
            
            if sell_trigger:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                if sell_price > 0:
                    pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                    trade_log.append(pnl)
                    cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # --- Buy Logic ---
        if market_is_alive and len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index and symbol not in PERMANENT_BLACKLIST:
                    row = df.loc[today]
                    # Ultimate Auto 필터: 모멘텀 천장 적용
                    if row['Amount'] >= 300 and (idx_ret + MOMENTUM_THRESHOLD) < row['Momentum'] < MOMENTUM_CEILING:
                        if row['Close'] > row['MA20'] and row['RSI'] < RSI_OVERHEAT:
                            candidates.append((symbol, row['Momentum']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price, 'entry_date': today, 'entry_atr': df.loc[today]['ATR'], 'max_profit': 0}
                        cash -= (qty * buy_price) * (1 + FEE)

        curr_val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_val)

    # --- Summary ---
    history_ser = pd.Series(history)
    final_return = ((history[-1]/INITIAL_CASH)-1)*100
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    
    print(f"### 📊 Performance Summary")
    print(f"| Metric | Value |")
    print(f"| :--- | :--- |")
    print(f"| **Final Balance** | {history[-1]:,.0f} KRW |")
    print(f"| **Total Return** | **{final_return:.2f}%** |")
    print(f"| **Win Rate** | {win_rate:.2f}% |")
    print(f"| **Max Drawdown (MDD)** | **{mdd:.2f}%** |")
    print(f"| **Total Trades** | {len(trade_log)} |")

if __name__ == "__main__":
    run_backtest()
