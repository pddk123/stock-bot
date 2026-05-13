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

# 매매 기준 (백테스트 승리 공식: ATR 손절 & 40일 타임컷)
MAX_POSITIONS = 5
TARGET_PROFIT = 0.05
ATR_MULTIPLIER = 2.0
TIME_CUT_DAYS = 40         # 약 2달 (영업일 40일)
RSI_OVERHEAT = 75
MOMENTUM_GAP = 0.03
MIN_DAILY_AMOUNT = 300     
SCAN_UNIVERSE = 250
BLACKLIST = ['440110']     # 파두(Fadu) 등 영구 제명 리스트

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- [Helper Functions] ---

def send_telegram_report(message):
    """텔레그램 리포트 전송 및 에러 처리"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("텔레그램 설정이 누락되어 메시지를 보낼 수 없습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        logging.error(f"텔레그램 전송 중 오류 발생: {e}")

def calculate_atr(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def get_live_data(symbol):
    """지표 계산 및 에너지 상태 진단 (에러 로깅 포함)"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d'))
        if len(df) < 60: return None
        
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        
        delta = df['Close'].diff()
        up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        down = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + up/down))
        
        curr = df.iloc[-1]
        prev_10d = df.iloc[-11:-1]
        
        # 4단계 에너지 센서
        p_high_10d = prev_10d['Close'].max()
        r_high_10d = prev_10d['RSI'].max()
        
        if curr['RSI'] > RSI_OVERHEAT: energy = "🔥 OVERHEATED"
        elif curr['Close'] > p_high_10d and curr['RSI'] > r_high_10d: energy = "🚀 ACCELERATING"
        elif curr['Close'] > p_high_10d and curr['RSI'] < r_high_10d: energy = "⚠️ EXHAUSTED"
        else: energy = "✅ STABLE"
            
        return {
            'code': symbol, 'Close': curr['Close'], 'Momentum': curr['Momentum'], 
            'RSI': curr['RSI'], 'Amount': (curr['Close'] * curr['Volume']) / 100_000_000,
            'ATR': calculate_atr(df), 'Energy': energy, 'is_above_ma20': curr['Close'] > curr['MA20']
        }
    except Exception as e:
        logging.warning(f"[{symbol}] 데이터 로드 실패: {e}")
        return None

def is_valid_candidate(res, portfolio_codes, idx_ret):
    """신규 종목 필터링 (에너지 상태 및 블랙리스트 체크)"""
    if not res or res['code'] in portfolio_codes: return False
    if res['Energy'] not in ["🚀 ACCELERATING", "✅ STABLE"]: return False
    
    return (res['Amount'] >= MIN_DAILY_AMOUNT and 
            res['Momentum'] > (idx_ret + MOMENTUM_GAP) and 
            res['RSI'] < RSI_OVERHEAT and 
            res['is_above_ma20'])

def sync_stock_with_data(row):
    """포트폴리오 동기화 (max_profit 제거로 로직 간소화)"""
    code = str(row['code'])
    live_data = get_live_data(code)
    if not live_data: return {**row.to_dict(), 'live': None}
    
    updated_row = row.to_dict()
    if pd.isna(row.get('entry_atr')) or row.get('entry_atr') == 0:
        updated_row['entry_atr'] = live_data['ATR']
    if pd.isna(row.get('entry_date')):
        updated_row['entry_date'] = datetime.now().strftime('%Y-%m-%d')
        
    return {**updated_row, 'live': live_data}

# --- [Reporting Module] ---

def generate_report(portfolio_with_data, candidates, market_status, cash, idx_ret, name_map):
    report = f"📊 *Smart Picking v9.1 Clean Master*\n📅 {datetime.now().strftime('%m-%d %H:%M')}\n━━━━━━━━━━━━━━━━━━\n\n"
    report += f"*1. MARKET MONITOR*\n● 상태: {market_status}\n● 시장 모멘텀: {idx_ret:+.2%}\n● 예수금: {cash:,.0f}원\n\n"

    report += f"*2. CURRENT POSITIONS*\n"
    if not portfolio_with_data:
        report += "└ [ - ] 보유 종목 없음\n"
    else:
        for item in portfolio_with_data:
            live = item['live']
            if not live: continue
            
            profit = (live['Close'] / item['entry_price']) - 1
            entry_dt = datetime.strptime(item['entry_date'], '%Y-%m-%d')
            hold_days = (datetime.now() - entry_dt).days
            
            target_price = item['entry_price'] * (1 + TARGET_PROFIT)
            stop_price = item['entry_price'] - (ATR_MULTIPLIER * item['entry_atr'])
            
            report += f"{'📈' if profit > 0 else '📉'} *{name_map.get(item['code'], item['code'])}* ({item['code']})\n"
            report += f"   └ 상태: `{live['Energy']}`\n"
            report += f"   └ 목표: *{target_price:,.0f}원* (🎯 10%)\n"
            report += f"   └ 손절: *{stop_price:,.0f}원* (🚨 ATR 2배)\n"
            report += f"   └ 수익: *{profit:+.2%}* | 보유: *{hold_days}일 / {TIME_CUT_DAYS}일*\n"
            
            sell_reason = ""
            if profit >= TARGET_PROFIT: sell_reason = "TARGET"
            elif live['Close'] < stop_price: sell_reason = "STOP"
            elif hold_days >= (TIME_CUT_DAYS * 1.4): sell_reason = "TIME-CUT"
            
            signal = f"SELL ({sell_reason})" if sell_reason else "HOLD"
            report += f"   └ 대응: `{signal}`\n   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"

    report += f"\n*3. NEW OPPORTUNITIES*\n"
    empty_slots = MAX_POSITIONS - len(portfolio_with_data)
    if not candidates:
        report += "└ [ - ] 매수 적격 종목 없음\n"
    else:
        buy_unit = cash / max(1, empty_slots)
        for i, cand in enumerate(candidates, 1):
            report += f"{i}️⃣ *{name_map.get(cand['code'], cand['code'])}* ({cand['code']})\n"
            report += f"   └ 상태: `{cand['Energy']}`\n"
            report += f"   └ 모멘텀: *{cand['Momentum']:.1%}*\n"
            report += f"   └ 분할매수: *{buy_unit:,.0f}원*\n"

    return report

# --- [Execution Module] ---

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE):
        logging.error(f"포트폴리오 파일을 찾을 수 없습니다: {PORTFOLIO_FILE}")
        return
        
    krx = fdr.StockListing('KRX')
    name_map = dict(zip(krx['Code'], krx['Name']))
    
    all_data_df = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data_df['code'].str.upper() == 'CASH'
    portfolio_rows = all_data_df[~cash_mask]
    
    # 1. 포트폴리오 데이터 동기화
    with ThreadPoolExecutor(max_workers=10) as ex:
        portfolio_with_data = list(ex.map(sync_stock_with_data, [row for _, row in portfolio_rows.iterrows()]))
    
    # live 데이터를 제외한 순수 정보만 CSV에 저장
    portfolio_df = pd.DataFrame([{k: v for k, v in item.items() if k != 'live'} for item in portfolio_with_data])
    pd.concat([portfolio_df, all_data_df[cash_mask]]).to_csv(PORTFOLIO_FILE, index=False)

    # 2. 시장 판단 및 예수금 확인
    cash = all_data_df[cash_mask]['qty'].iloc[0] if any(cash_mask) else 0
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1

    # 3. 블랙리스트를 제외한 신규 종목 스캔
    candidates = []
    if market_alive and (MAX_POSITIONS - len(portfolio_with_data)) > 0:
        top_codes = [c for c in krx.nlargest(SCAN_UNIVERSE, 'Marcap')['Code'].tolist() if c not in BLACKLIST]
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            candidates = sorted([r for r in results if is_valid_candidate(r, all_data_df['code'].values, idx_ret)], 
                                key=lambda x: x['Momentum'], reverse=True)[:5]

    # 4. 최종 리포트 생성 및 전송
    final_report = generate_report(portfolio_with_data, candidates, '🔵 ACTIVE' if market_alive else '🔴 CAUTION', cash, idx_ret, name_map)
    print(final_report)
    send_telegram_report(final_report)

if __name__ == "__main__":
    run_bot()
