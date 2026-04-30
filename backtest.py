import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v6.0 설정값: 안정성 중심] ---
START_DATE = '2024-01-01' 
END_DATE = '2026-04-30'
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
FEE = 0.002 # 수수료 0.2%

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
        df['Peak'] = df['Close'].rolling(window=40, min_periods=1).max()
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v6.0 안정성 강화 백테스트: {START_DATE} ~ {END_DATE}")
    
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist() # 유니버스 확대

    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'entry_price': p, 'max_p': p}}
    trade_log, history = [], []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # [매도 로직]
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            curr = df.loc[today]
            
            # 1. 가변 손절 (여유 버퍼 3% 추가)
            market_stable = kospi.loc[today]['Close'] > kospi.loc[today]['MA20']
            stop_line = curr['MA20'] * 0.97 if market_stable else curr['MA10'] * 0.98
            
            # 2. 트레일링 스탑 (수익이 10% 이상 났을 때만 15% 적용)
            profit_pct = (curr['Close'] / pos['entry_price'] - 1) * 100
            is_trailing_hit = False
            if profit_pct > 10:
                if curr['Close'] < (curr['Peak'] * 0.85): # 여유 있게 15%
                    is_trailing_hit = True
            
            if curr['Close'] < stop_line or is_trailing_hit:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl); cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # [매수 로직]
        if len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] < 150: continue # 필터 현실화
                    
                    # S급 조건 (강세장 돌파 / 횡보장 눌림목)
                    if market_stable:
                        if (row['Close'] > row['MA10'] > row['MA20']) and (45 <= row['RSI'] <= 70):
                            candidates.append((symbol, row['Vol_Ratio']))
                    else:
                        if (row['Close'] > row['MA60']) and (35 <= row['RSI'] <= 48):
                            candidates.append((symbol, row['Vol_Ratio']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates[:MAX_POSITIONS]:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price}
                        cash -= (qty * buy_price) * (1 + FEE)

        total_val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(total_val)

    # 결과 리포트
    final_return = ((history[-1]/INITIAL_CASH)-1)*100
    mdd = ((pd.Series(history) - pd.Series(history).cummax()) / pd.Series(history).cummax()).min() * 100
    print(f"💰 최종 수익률: {final_return:.2f}% | 승률: {(len([r for r in trade_log if r > 0])/len(trade_log)*100):.2f}% | MDD: {mdd:.2f}%")

if __name__ == "__main__":
    run_backtest()
