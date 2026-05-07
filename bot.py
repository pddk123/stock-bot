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

def sync_portfolio(df):
    """CSV 파일에 비어있는 ATR과 max_profit을 실시간으로 동기화합니다."""
    for index, row in df.iterrows():
        code = row['code']
        # 1. entry_atr이 없으면(nan) 과거 데이터를 가져와서 자동 계산
        if pd.isna(row.get('entry_atr')) or row.get('entry_atr') == 0:
            try:
                hist = fdr.DataReader(code, (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
                tr = pd.concat([hist['High']-hist['Low'], abs(hist['High']-hist['Close'].shift()), abs(hist['Low']-hist['Close'].shift())], axis=1).max(axis=1)
                df.at[index, 'entry_atr'] = tr.rolling(14).mean().iloc[-1]
            except: pass
        
        # 2. max_profit 업데이트 (본전 사수 로직용)
        try:
            curr_price = fdr.DataReader(code).iloc[-1]['Close']
            curr_profit = (curr_price / row['entry_price']) - 1
            df.at[index, 'max_profit'] = max(row.get('max_profit', 0) if not pd.isna(row.get('max_profit')) else 0, curr_profit)
        except: pass
    return df

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE): return
    
    krx = fdr.StockListing('KRX')
    name_map = dict(zip(krx['Code'], krx['Name']))
    
    # 데이터 로드 및 자동 동기화
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    cash_row = all_data[cash_mask]
    portfolio = all_data[~cash_mask].copy()

    # [핵심] 여기서 비어있는 정보들을 채우고 저장합니다.
    portfolio = sync_portfolio(portfolio)
    
    # 수정한 포트폴리오 정보를 다시 합쳐서 CSV에 저장 (자동 업데이트)
    updated_all_data = pd.concat([portfolio, cash_row])
    updated_all_data.to_csv(PORTFOLIO_FILE, index=False)

    current_cash = cash_row['qty'].iloc[0] if not cash_row.empty else 0
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    # --- 리포트 생성 로직 ---
    report = f"📊 *Smart Picking v8.3 Dashboard*\n"
    report += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    report += f"━━━━━━━━━━━━━━━━━━\n\n"

    report += f"*1. MARKET MONITOR*\n"
    report += f"● Status: {'🔵 ACTIVE' if market_alive else '🔴 CAUTION'}\n"
    report += f"● KOSPI Mom: {idx_ret:+.2%}\n"
    report += f"● Reserves: {current_cash:,.0f}원\n\n"

    report += f"*2. CURRENT POSITIONS*\n"
    if portfolio.empty:
        report += "└ [ - ] No Active Positions\n"
    else:
        for idx, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            p_name = name_map.get(row['code'], row['code'])
            profit = (curr['Close'] / row['entry_price']) - 1
            
            # ATR이 계산되었으므로 이제 정상적으로 손절가가 출력됩니다.
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

    # 3. 신규 추천 (파두 제외)
    report += f"\n*3. NEW OPPORTUNITIES*\n"
    empty_slots = MAX_POSITIONS - len(portfolio)
    if not market_alive or empty_slots <= 0:
        report += "└ [ - ] Conditions Not Met\n"
    else:
        top_codes = [c for c in krx.nlargest(250, 'Marcap')['Code'].tolist() if c not in BLACKLIST]
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            for i, res in enumerate(results):
                if res is not None and top_codes[i] not in portfolio['code'].values:
                    if res['Amount'] >= 300 and res['Momentum'] > (idx_ret + 0.03) and res['RSI'] < 75:
                        candidates.append({'code': top_codes[i], 'mom': res['Momentum']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        buy_unit = current_cash / empty_slots
        for i, cand in enumerate(candidates[:5], 1):
            report += f"{i}️⃣ *{name_map.get(cand['code'])}* ({cand['code']})\n"
            report += f"   └ Mom: {cand['mom']:.1%} | Buy: {buy_unit:,.0f}원\n"

    report += f"\n━━━━━━━━━━━━━━━━━━"
    print(report)
    send_telegram_report(report)

if __name__ == "__main__":
    run_bot()
