import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v6.7 설정값: 수익 로직 복구 버전] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 30_000_000 
FEE = 0.002
TARGET_PROFIT = 0.07 
TIME_CUT_DAYS = 30   
MAX_PER_STOCK = 10_000_000 # 종목당 한도 유지

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
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['RSI'] = calculate_rsi_wilder(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v6.7 (48% 로직 복구형) 백테스트: {START_DATE} ~ {END_DATE}")
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
        
        # 시장 국면 판단 (v6.3 핵심 로직)
        market_mode = 'BULL' if kospi.loc[today]['Close'] > kospi.loc[today]['MA20'] else 'SIDEWAYS'
        
        # [1] 매도 로직
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            curr_price = df.loc[today]['Close']
            profit_rate = (curr_price / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            
            if profit_rate >= TARGET_PROFIT or hold_days >= TIME_CUT_DAYS:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_price
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl); cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # [2] 동적 자산 배분
        current_total_val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        num_splits = max(3, math.ceil(current_total_val / 10_000_000))
        buy_unit = min(MAX_PER_STOCK, current_total_val / num_splits)

        # [3] 매수 로직 (v6.3 복구: BULL/SIDEWAYS 듀얼 매수)
        if len(positions) < num_splits:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] < 300: continue
                    
                    grade = None
                    if market_mode == 'BULL':
                        # 강세장: 돌파형 (RSI 45~72)
                        if (row['Close'] > row['MA10'] > row['MA20']) and (45 <= row['RSI'] <= 72): grade = 'S'
                    else:
                        # 횡보장: 눌림목형 (RSI 35~50) - 이게 있어야 48%가 나옴!
                        if (row['Close'] > row['MA60']) and (35 <= row['RSI'] <= 50): grade = 'S'
                    
                    if grade: candidates.append((symbol, row['Vol_Ratio']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates[:2]:
                if len(positions) >= num_splits or cash < buy_unit: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price, 'entry_date': today}
                        cash -= (qty * buy_price) * (1 + FEE)

        history.append(current_total_val)

    # 결과 리포트
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((pd.Series(history) - pd.Series(history).cummax()) / pd.Series(history).cummax()).min() * 100
    print(f"\n📊 v6.7 완성형 스나이퍼 결과")
    print(f"💰 시작 금액: {INITIAL_CASH:,.0f}원 | 테스트 후: {history[-1]:,.0f}원")
    print(f"📈 수익률: {((history[-1]/INITIAL_CASH)-1)*100:.2f}% | 승률: {win_rate:.2f}% | MDD: {mdd:.2f}% | 매매: {len(trade_log)}회")

if __name__ == "__main__":
    run_backtest()
