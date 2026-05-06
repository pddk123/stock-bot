import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v6.3.2 가변 손절 모드] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000 
MAX_POSITIONS = 5
FEE = 0.002
TARGET_PROFIT = 0.07 
TIME_CUT_DAYS = 30   
DEEP_STOP_LOSS = -0.15 # 하락장에서만 작동하는 최후의 보루

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
        df['MA10'] = df['Close'].rolling(10).mean(); df['MA20'] = df['Close'].rolling(20).mean()
        df['RSI'] = calculate_rsi_wilder(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v6.3.2 가변 손절 백테스트: {START_DATE} ~ {END_DATE}")
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    top_stocks = fdr.StockListing('KRX').nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH; positions = {}; trade_log = []; history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]; tomorrow = trading_days[i+1]
        market_stable = kospi.loc[today]['Close'] > kospi.loc[today]['MA20']
        
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_price = df.loc[today]['Close']
            profit_rate = (curr_price / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            
            sell_trigger = False
            # 1. 목표 수익 달성
            if profit_rate >= TARGET_PROFIT: sell_trigger = True
            # 2. 타임컷
            elif hold_days >= TIME_CUT_DAYS: sell_trigger = True
            # 3. 가변 손절 (시장이 위험하고 수익률이 -15% 이하일 때만)
            elif not market_stable and profit_rate <= DEEP_STOP_LOSS: sell_trigger = True
            
            if sell_trigger:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_price
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl); cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # 매수 로직 (v6.3.1 주도주 집중 유지)
        if len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] < 300: continue
                    if (row['Close'] > row['MA10'] > row['MA20']) and (45 <= row['RSI'] <= 72):
                        candidates.append((symbol, row['Vol_Ratio']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates[:2]:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price, 'entry_date': today}
                        cash -= (qty * buy_price) * (1 + FEE)

        history.append(cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index]))

    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((pd.Series(history) - pd.Series(history).cummax()) / pd.Series(history).cummax()).min() * 100
    print(f"\n🛡️ v6.3.2 성적표 (가변 손절)")
    print(f"💰 수익률: {((history[-1]/INITIAL_CASH)-1)*100:.2f}% | 승률: {win_rate:.2f}% | MDD: {mdd:.2f}% | 매매: {len(trade_log)}회")

if __name__ == "__main__":
    run_backtest()
