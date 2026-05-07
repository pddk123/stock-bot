import pandas as pd
import FinanceDataReader as fdr
import os
import requests
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [Settings] ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
PORTFOLIO_FILE = 'my_portfolio.csv'

MAX_POSITIONS = 5
TARGET_PROFIT = 0.10
ATR_MULTIPLIER = 2.0
TRAILING_THRESHOLD = 0.03
RSI_OVERHEAT = 75
MOMENTUM_GAP = 0.03

SCAN_UNIVERSE = 250
MIN_DAILY_AMOUNT = 300
LOOKBACK_LONG = 150
LOOKBACK_SHORT = 60
BLACKLIST = ['440110'] # 파두

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- [Helper Functions] ---

def send_telegram_report(message):
    """텔레그램 리포트 전송 및 에러 로깅"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("텔레그램 설정이 누락되었습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        logging.error(f"텔레그램 전송 실패: {e}")

def calculate_atr(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def get_live_data(symbol):
    """지표 계산 및 에너지 센서 진단"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=LOOKBACK_LONG)).strftime('%Y-%m-%d'))
        if len(df) < 60: return None
        
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        
        delta = df['Close'].diff()
        up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        down = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + up/down))
        
        curr = df.iloc[-1]
        
        prev_10d = df.iloc[-11:-1]
        price_high_10d = prev_10d['Close'].max()
        rsi_at_high = prev_10d.loc[prev_10d['Close'].idxmax(), 'RSI']
        energy = "⚠️ EXHAUSTED" if (curr['Close'] > price_high_10d and curr['RSI'] < rsi_at_high) else "✅ STABLE"
            
        return {
            'code': symbol, 'Close': curr['Close'], 'Momentum': curr['Momentum'], 
            'RSI': curr['RSI'], 'Amount': (curr['Close'] * curr['Volume']) / 100_000_000,
            'ATR': calculate_atr(df), 'Energy': energy, 'is_above_ma20': curr['Close'] > curr['MA20']
        }
    except Exception as e:
        logging.warning(f"[{symbol}] 데이터 로드 오류: {e}")
        return None

def is_valid_candidate(res, portfolio_codes, idx_ret):
    """[New] 신규 매수 후보 필터링 조건 함수화"""
    if not res: return False
    return (
        res['code'] not in portfolio_codes and
        res['Amount'] >= MIN_DAILY_AMOUNT and
        res['Momentum'] > (idx_ret + MOMENTUM_GAP) and
        res['RSI'] < RSI_OVERHEAT and
        res['is_above_ma20']
    )

def sync_stock_with_data(row):
    code = str(row['code'])
    live_data = get_live_data(code)
    if not live_data: return {**row.to_dict(), 'live': None}
    
    updated_row = row.to_dict()
    if pd.isna(row.get('entry_atr')) or row.get('entry_atr') == 0:
        updated_row['entry_atr'] = live_data['ATR']
        
    curr_profit = (live_data['Close'] / row['entry_price']) - 1
    updated_row['max_profit'] = max(row.get('max_profit', 0) if not pd.isna(row.get('max_profit')) else 0, curr_profit)
    return {**updated_row, 'live': live_data}

# --- [Core Functions] ---

def generate_report(portfolio_with_data, candidates, market_status, cash, idx_ret, name_map):
    report = f"📊 *Smart Picking v8.7 Final*\n📅 {datetime.now().strftime('%m-%d %H:%M')}\n━━━━━━━━━━━━━━━━━━\n\n"
    report += f"*1. MARKET MONITOR*\n● Status: {market_status}\n● KOSPI Mom: {idx_ret:+.2%}\n● Reserves: {cash:,.0f}원\n\n"

    report += f"*2. CURRENT POSITIONS*\n"
    if not portfolio_with_data:
        report += "└ [ - ] No Active Positions\n"
    else:
        for item in portfolio_with_data:
            live = item['live']
            if not live: continue
            profit = (live['Close'] / item['entry_price']) - 1
            atr_stop = item['entry_price'] - (ATR_MULTIPLIER * item['entry_atr'])
            trailing_stop = item['entry_price'] if item['max_profit'] >= TRAILING_THRESHOLD else -float('inf')
            stop_price = max(atr_stop, trailing_stop)
            
            signal = "SELL" if (profit >= TARGET_PROFIT or live['Close'] < stop_price) else "HOLD"
            report += f"{'📈' if profit > 0 else '📉'} *{name_map.get(item['code'], item['code'])}*\n"
            report += f"   └ Profit: *{profit:+.2%}* | Energy: `{live['Energy']}`\n"
            report += f"   └ Action: `{signal}` (SL: {stop_price:,.0f})\n   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"

    report += f"\n*3. NEW OPPORTUNITIES*\n"
    empty_slots = MAX_POSITIONS - len(portfolio_with_data)
    if not candidates:
        report += "└ [ - ] Conditions Not Met\n"
    else:
        for i, cand in enumerate(candidates, 1):
            report += f"{i}️⃣ *{name_map.get(cand['code'], cand['code'])}* | `{cand['Energy']}`\n"
            report += f"   └ Mom: {cand['Momentum']:.1%} | Buy: {cash/max(1, empty_slots):,.0f}원\n"

    report += f"\n━━━━━━━━━━━━━━━━━━"
    return report

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE): return
    krx = fdr.StockListing('KRX')
    name_map = dict(zip(krx['Code'], krx['Name']))
    
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    portfolio_rows = all_data[~cash_mask]
    
    with ThreadPoolExecutor(max_workers=10) as ex:
        portfolio_with_data = list(ex.map(sync_stock_with_data, [row for _, row in portfolio_rows.iterrows()]))
    
    portfolio_df = pd.DataFrame([{k: v for k, v in item.items() if k != 'live'} for item in portfolio_with_data])
    pd.concat([portfolio_df, all_data[cash_mask]]).to_csv(PORTFOLIO_FILE, index=False)

    cash = all_data[cash_mask]['qty'].iloc[0] if any(cash_mask) else 0
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    market_status = '🔵 ACTIVE' if market_alive else '🔴 CAUTION'

    candidates = []
    if market_alive and (MAX_POSITIONS - len(portfolio_with_data)) > 0:
        top_codes = [c for c in krx.nlargest(SCAN_UNIVERSE, 'Marcap')['Code'].tolist() if c not in BLACKLIST]
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            candidates = sorted([r for r in results if is_valid_candidate(r, all_data['code'].values, idx_ret)], 
                                key=lambda x: x['Momentum'], reverse=True)[:5]

    final_report = generate_report(portfolio_with_data, candidates, market_status, cash, idx_ret, name_map)
    print(final_report)
    send_telegram_report(final_report)

if __name__ == "__main__":
    run_bot()
