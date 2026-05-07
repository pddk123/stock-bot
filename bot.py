import pandas as pd
import FinanceDataReader as fdr
import os
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [GitHub Secrets 환경변수 로드] ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- [v8.3 실전 엔진 설정값] ---
PORTFOLIO_FILE = 'my_portfolio.csv'
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10
ATR_MULTIPLIER = 2.0
TIME_CUT_DAYS = 15
MOMENTUM_THRESHOLD = 0.03 
RSI_OVERHEAT = 75

# [매뉴얼 필터링] 숫자가 좋아도 사기 싫은 종목 코드를 여기에 추가하세요.
BLACKLIST = ['440110'] # 파두(440110)

def send_telegram_report(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=15)
    except: print("❌ 텔레그램 전송 실패")

def get_live_data(symbol):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d'))
        if len(df) < 50: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1).rolling(14).mean()
        df['RSI'] = (lambda s: 100 - (100 / (1 + (s.diff().clip(lower=0).ewm(alpha=1/14, adjust=False).mean() / -s.diff().clip(upper=0).ewm(alpha=1/14, adjust=False).mean()))))(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        return df.iloc[-1]
    except: return None

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE): return
    
    krx = fdr.StockListing('KRX')
    name_map = dict(zip(krx['Code'], krx['Name']))
    
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    current_cash = all_data[cash_mask]['qty'].iloc[0] if any(cash_mask) else 0
    portfolio = all_data[~cash_mask].copy()

    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    # --- [리포트 디자인 시작] ---
    report = f"📊 *Smart Picking v8.3 Status*\n"
    report += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    report += f"━━━━━━━━━━━━━━━━━━\n\n"

    # 1) 시장 상태 지표
    market_icon = "🔵 ACTIVE" if market_alive else "🔴 CAUTION"
    report += f"*1. MARKET MONITOR*\n"
    report += f"● Status: {market_icon}\n"
    report += f"● KOSPI Mom: {idx_ret:+.2%}\n"
    report += f"● Reserves: {current_cash:,.0f}원\n\n"

    # 2) 보유 종목 현황 (섹션 구분 강화)
    report += f"*2. CURRENT POSITIONS*\n"
    if portfolio.empty:
        report += "└ [ - ] No Active Positions\n"
    else:
        for idx, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            p_name = name_map.get(row['code'], row['code'])
            profit = (curr['Close'] / row['entry_price']) - 1
            
            # 본전 사수 로직 반영된 손절가
            stop = max(row['entry_price'] - (ATR_MULTIPLIER * row['entry_atr']), 
                       row['entry_price'] if row['max_profit'] >= 0.03 else 0)
            
            p_icon = "📈" if profit > 0 else "📉"
            signal = "HOLD"
            if profit >= TARGET_PROFIT: signal = "🎯 TARGET(SELL)"
            elif curr['Close'] < stop: signal = "🚨 STOP-LOSS(SELL)"
            
            report += f"{p_icon} *{p_name}* ({row['code']})\n"
            report += f"   └ Profit: *{profit:+.2%}* | Mom: {curr['Momentum']:.1%}\n"
            report += f"   └ Action: `{signal}` (SL: {stop:,.0f})\n"
            report += f"   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"

    # 3) 추천 종목 (파두 필터링 적용)
    report += f"\n*3. NEW OPPORTUNITIES*\n"
    empty_slots = MAX_POSITIONS - len(portfolio)
    if not market_alive or empty_slots <= 0:
        report += "└ [ - ] Conditions Not Met\n"
    else:
        # 블랙리스트 제외 처리
        top_codes = [c for c in krx.nlargest(250, 'Marcap')['Code'].tolist() if c not in BLACKLIST]
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            for i, res in enumerate(results):
                if res is not None and top_codes[i] not in portfolio['code'].values:
                    if res['Amount'] >= 300 and res['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD) and res['RSI'] < RSI_OVERHEAT:
                        candidates.append({'code': top_codes[i], 'mom': res['Momentum']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        buy_unit = current_cash / empty_slots
        
        for i, cand in enumerate(candidates[:5], 1):
            c_name = name_map.get(cand['code'], cand['code'])
            report += f"{i}️⃣ *{c_name}* ({cand['code']})\n"
            report += f"   └ Mom: {cand['mom']:.1%} | Buy: {buy_unit:,.0f}원\n"

    report += f"\n━━━━━━━━━━━━━━━━━━"
    
    print(report)
    send_telegram_report(report)

if __name__ == "__main__":
    run_bot()
