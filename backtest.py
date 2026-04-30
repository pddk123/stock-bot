import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [설정값: 여기서 날짜를 수정하세요] ---
START_DATE = '2024-01-01'  # 테스트 시작일
END_DATE = '2025-01-01'    # 테스트 종료일
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
FEE = 0.002

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))

def get_stock_data(symbol):
    """백테스트용 데이터 로드 및 v5.9 지표 계산"""
    try:
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 70: return None
        
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['RSI'] = calculate_rsi_wilder(df['Close'])
        # v5.9: 최근 30일 고점 추적 (트레일링 스탑용)
        df['Peak'] = df['Close'].rolling(window=30, min_periods=1).max()
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v5.9 (Dual-Mode) 백테스트 시작: {START_DATE} ~ {END_DATE}")
    
    # 1. 지수 데이터 로드 (국면 판단용)
    kospi = fdr.DataReader('KS11', (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=60)).strftime('%Y-%m-%d'), END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    
    # 2. 대상 종목 (시총 상위 200종목)
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(200, 'Marcap')['Code'].tolist()

    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    # 3. 시뮬레이션 변수
    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'entry_price': p}}
    trade_log = []
    history = []
    trading_days = kospi.loc[START_DATE:].index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # [A] 시장 국면 판단 (v5.9 핵심)
        k_row = kospi.loc[today]
        mode = 'BULL' if k_row['Close'] > k_row['MA20'] else 'SIDEWAYS'
        
        # [B] 매도 로직
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            curr = df.loc[today]
            
            # 1. 가변 손절 (BULL: MA20 / SIDE: MA10)
            stop_line = curr['MA20'] if mode == 'BULL' else curr['MA10']
            # 2. 트레일링 스탑 (고점 대비 -12%)
            is_trailing_hit = curr['Close'] < (curr['Peak'] * 0.88)
            
            if curr['Close'] < stop_line or is_trailing_hit:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl)
                cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # [C] 매수 로직 (Dual-Mode Grade)
        if len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] < 300: continue # 300억 필터
                    
                    grade = None
                    if mode == 'BULL':
                        # 강세장: 돌파형 S급 (RSI 45~75)
                        if (row['Close'] > row['MA10'] > row['MA20'] > row['MA60']) and (45 <= row['RSI'] <= 75):
                            grade = 'S'
                    else:
                        # 횡보장: 눌림목형 S급 (RSI 35~50)
                        if (row['Close'] > row['MA60']) and (35 <= row['RSI'] <= 50):
                            grade = 'S'
                    
                    if grade == 'S':
                        candidates.append((symbol, row['Vol_Ratio']))
            
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

        # 자산 기록
        total_val = cash
        for symbol, pos in positions.items():
            df = all_data[symbol]
            price = df.loc[today]['Close'] if today in df.index else pos['entry_price']
            total_val += price * pos['qty']
        history.append(total_val)

    # 최종 리포트
    history_ser = pd.Series(history)
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    
    print(f"\n✨ v5.9(Dual-Mode) 최종 성적표")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 최종 자산: {history[-1]:,.0f}원")
    print(f"📈 누적 수익률: {((history[-1]/INITIAL_CASH)-1)*100:.2f}%")
    print(f"🎯 종목별 승률: {win_rate:.2f}% (총 {len(trade_log)}회 매매)")
    print(f"💸 매도당 평균 수익률: {np.mean(trade_log):.2f}%")
    print(f"📉 최대 낙폭 (MDD): {mdd:.2f}%")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    run_backtest()
