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

# [필터링 설정] 
# 1. 수동 블랙리스트: 숫자가 좋아도 사기 싫은 종목 코드를 넣으세요 (예: 파두 '440110')
MANUAL_BLACKLIST = ['440110'] 
# 2. 자동 차단 키워드: 아래 단어가 상태창에 하나라도 있으면 즉시 제외합니다.
DANGER_PATTERN = '경고|위험|관리|정지|과열|주의'

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
    for index, row in df.iterrows():
        if pd.isna(row.get('entry_atr')) or row.get('entry_atr') == 0:
            try:
                start_search = (datetime.strptime(row['entry_date'], '%Y-%m-%d') - timedelta(days=40)).strftime('%Y-%m-%d')
                hist = fdr.DataReader(row['code'], start_search, row['entry_date'])
                tr = pd.concat([hist['High']-hist['Low'], abs(hist['High']-hist['Close'].shift()), abs(hist['Low']-hist['Close'].shift())], axis=1).max(axis=1)
                df.at[index, 'entry_atr'] = tr.rolling(14).mean().iloc[-1]
            except: pass
        try:
            curr_price = fdr.DataReader(row['code']).iloc[-1]['Close']
            curr_profit = (curr_price / row['entry_price']) - 1
            df.at[index, 'max_profit'] = max(row['max_profit'] if not pd.isna(row.get('max_profit')) else 0, curr_profit)
        except: pass
    return df

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE): return
    
    # 1. 실시간 KRX 데이터 및 안전 필터링
    krx = fdr.StockListing('KRX')
    
    # [자동 필터] 상태 컬럼(State)에서 위험 키워드 제거 + 수동 블랙리스트 제거
    state_col = next((c for c in krx.columns if c in ['State', '상태']), None)
    if state_col:
        safe_krx = krx[~krx[state_col].fillna('').str.contains(DANGER_PATTERN)].copy()
    else:
        safe_krx = krx.copy()
        
    safe_krx = safe_krx[~safe_krx['Code'].isin(MANUAL_BLACKLIST)]
    name_map = dict(zip(safe_krx['Code'], safe_krx['Name']))
    
    # 2. 포트폴리오 로드
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    cash_row = all_data[cash_mask]
    portfolio = all_data[~cash_mask].copy()

    portfolio = sync_portfolio(portfolio)
    current_cash = cash_row['qty'].iloc[0] if not cash_row.empty else 0

    # 3. 시장 상황 판단
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    report = f"🛡️ *Smart Picking v8.3 (Full-Auto Safe Mode)*\n\n"
    report += f"1) 시장 지수 : {'✅ 매수 가능' if market_alive else '⚠️ 관망'}\n"
    report += f"- 지수 모멘텀: {idx_ret:.2%} | 현금: {current_cash:,.0f}원\n\n"

    # 4. 보유 종목 리포트
    report += f"2) 보유 종목 리포트\n━━━━━━━━━━━━\n"
    if portfolio.empty: report += "- 보유 종목 없음\n"
    else:
        for idx, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            p_name = name_map.get(row['code'], row['code']) or row['code']
            profit = (curr['Close'] / row['entry_price']) - 1
            
            atr_val = row['entry_atr'] if not pd.isna(row['entry_atr']) else 0
            stop = row['entry_price'] - (ATR_MULTIPLIER * atr_val)
            if row['max_profit'] >= 0.03: stop = max(stop, row['entry_price'])
            
            signal = "KEEP"
            if profit >= TARGET_PROFIT: signal = "🎯 익절(SELL)"
            elif curr['Close'] < stop: signal = "🚨 손절(SELL)"
            
            report += f"*{p_name}* ({row['code']})\n"
            report += f"- 수익: {profit:.2%} | *모멘텀: {curr['Momentum']:.2%}*\n"
            report += f"- 제안: *{signal}* (손절가: {stop:,.0f})\n"
            report += f"----------------------------\n"

    # 5. 신규 안전 종목 추천
    report += f"\n3) 안전 종목 추천 (경고/블랙리스트 제외)\n━━━━━━━━━━━━\n"
    empty_slots = MAX_POSITIONS - len(portfolio)
    if not market_alive or empty_slots <= 0:
        report += "- 신규 매수 조건 미충족\n"
    else:
        top_codes = safe_krx.nlargest(250, 'Marcap')['Code'].tolist()
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            for i, res in enumerate(results):
                if res is not None and top_codes[i] not in portfolio['code'].values:
                    if res['Amount'] >= 300 and res['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD) and res['RSI'] < RSI_OVERHEAT:
                        candidates.append({'code': top_codes[i], 'mom': res['Momentum'], 'price': res['Close']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        buy_unit = current_cash / empty_slots
        for cand in candidates[:5]:
            report += f"✅ *{name_map.get(cand['code'])}*\n- 모멘텀: {cand['mom']:.2%} | *매수금: {buy_unit:,.0f}원*\n"

    # 6. 저장 및 보고
    final_df = pd.concat([portfolio, cash_row])
    final_df.to_csv(PORTFOLIO_FILE, index=False)
    
    print(report)
    send_telegram_report(report)

if __name__ == "__main__":
    run_bot()
