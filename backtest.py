import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [설정값] ---
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
FEE = 0.002
# 날짜를 직접 수정해서 테스트해보세요 (예: '2024-01-01' ~ '2025-01-01')
START_DATE = (datetime.now() - timedelta(days=365)).strftime('2024-01-01')
END_DATE = datetime.now().strftime('2025-01-01')

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = roll_up / roll_down
    return 100.0 - (100.0 / (1.0 + rs))

def get_stock_data(symbol):
    """백테스트용 데이터 로드 및 v5.8 지표 계산"""
    df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=100)).strftime('%Y-%m-%d'), END_DATE)
    if len(df) < 60: return None
    
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['RSI'] = calculate_rsi_wilder(df['Close'])
    # 트레일링 스탑을 위한 직전 20일 최고가 (당일 고가 포함)
    df['Peak'] = df['Close'].rolling(window=20, min_periods=1).max()
    
    df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
    df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
    
    # S급 신호 (거래대금 300억 필터 적용)
    df['Signal'] = ((df['Close'] > df['MA10']) & (df['MA10'] > df['MA20']) & (df['MA20'] > df['MA60']) & 
                    (df['Amount'] >= 300) & (df['RSI'].between(45, 65)) & (df['Vol_Ratio'] >= 1.5))
    return df.loc[START_DATE:]

def run_backtest():
    print(f"🚀 v5.8 (Bull & Bear Adaptive) 백테스트 시작: {START_DATE} ~ {END_DATE}")
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(200, 'Marcap')['Code'].tolist()
    
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    # 시장 안정성 체크 (지수 > MA20)
    market_stability = kospi['Close'] > kospi['MA20']

    all_data = {}
    for code in top_stocks:
        data = get_stock_data(code)
        if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'entry_price': p}}
    trade_log = []
    history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        market_stable = market_stability.loc[today] if today in market_stability.index else False
        
        # [1] 매도 로직 (v5.8 가변 손절 & 트레일링 스탑 적용)
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_row = df.loc[today]
            
            # A. 가변 손절선 설정
            stop_line = curr_row['MA20'] if market_stable else curr_row['MA10']
            # B. 트레일링 스탑 (고점 대비 -5%)
            is_trailing_hit = curr_row['Close'] < (curr_row['Peak'] * 0.95)
            
            if curr_row['Close'] < stop_line or is_trailing_hit:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_row['Close']
                pnl_pct = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl_pct)
                cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # [2] 매수 로직 (S급 주도주 선착순)
        if len(positions) < MAX_POSITIONS:
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

        # [3] 일일 자산 기록
        total_val = cash
        for symbol, pos in positions.items():
            df = all_data[symbol]
            price = df.loc[today]['Close'] if today in df.index else pos['entry_price']
            total_val += price * pos['qty']
        history.append(total_val)

    # 결과 분석
    history_ser = pd.Series(history)
    final_val = history[-1]
    total_return = (final_val - INITIAL_CASH) / INITIAL_CASH * 100
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    avg_return = np.mean(trade_log) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100

    print(f"\n🏆 rootee님, v5.8(Adaptive) 백테스트 성적표")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 최종 자산: {final_val:,.0f}원")
    print(f"📈 누적 수익률: {total_return:.2f}%")
    print(f"🎯 종목별 승률: {win_rate:.2f}% (총 {len(trade_log)}회 매매)")
    print(f"💸 매도당 평균 수익률: {avg_return:.2f}%")
    print(f"📉 최대 낙폭 (MDD): {mdd:.2f}%")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    run_backtest()
