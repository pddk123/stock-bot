import pandas as pd
import FinanceDataReader as fdr
import requests
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [전역 설정 및 유틸리티] ---

# 1. KST 시간 헬퍼
def get_now_kst():
    return datetime.now() + timedelta(hours=9)

# 2. 로깅 설정
logger = logging.getLogger("StockAnalyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    file_handler = logging.FileHandler('stock_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

# 3. 텔레그램 전송
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_report(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정이 없어 메시지를 전송하지 않습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
        logger.info("텔레그램 리포트 전송 성공")
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")

# --- [데이터 분석 엔진] ---

def get_weighted_volume_multiplier():
    """U자형 거래량 곡선 반영 보정 계수 (실시간 반영)"""
    now = get_now_kst()
    if now.weekday() >= 5: return 1.0
    start_market = 9 * 60
    current_time = now.hour * 60 + now.minute
    elapsed = current_time - start_market
    if elapsed <= 0 or elapsed >= 390: return 1.0
    
    if elapsed <= 60: weight = (elapsed / 60) * 0.35
    elif elapsed <= 360: weight = 0.35 + ((elapsed - 60) / 300) * 0.40
    else: weight = 0.75 + ((elapsed - 360) / 30) * 0.25
    return 1.0 / weight

def calculate_rsi_wilder(series, period=14):
    """Wilder's Smoothing 방식 RSI"""
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = roll_up / roll_down
    return (100.0 - (100.0 / (1.0 + rs))).iloc[-1]

def get_indicators(df, multiplier=1.0):
    """지표 계산 (RSI, MA, 거래량비, 거래대금)"""
    if len(df) < 20: return None
    last_row = df.iloc[-1]
    curr_p = last_row['Close']
    ma10 = df['Close'].rolling(10).mean().iloc[-1]
    ma20 = df['Close'].rolling(20).mean().iloc[-1]
    ma60 = df['Close'].rolling(60).mean().iloc[-1]
    vol_ma5 = df['Volume'].iloc[:-1].rolling(5).mean().iloc[-1]
    est_vol = last_row['Volume'] * multiplier
    v_ratio = est_vol / vol_ma5 if vol_ma5 > 0 else 0
    amt_억 = (curr_p * est_vol) / 100_000_000
    rsi_v = calculate_rsi_wilder(df['Close'])
    
    return {'price': curr_p, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60, 'rsi': rsi_v, 'vol_ratio': v_ratio, 'amount': amt_억}

def check_grade(ind):
    """등급 판정 로직 (S/A급)"""
    if not ind: return None
    is_s = (ind['price'] > ind['ma10'] > ind['ma20'] > ind['ma60']) and \
           (ind['amount'] >= 50) and (45 <= ind['rsi'] <= 62) and (ind['vol_ratio'] >= 1.5)
    if is_s: return 'S'
    is_a = (ind['price'] > ind['ma10']) and (50 <= ind['rsi'] < 70) and (ind['vol_ratio'] >= 1.0)
    if is_a: return 'A'
    return None

def analyze_stock(symbol, name, sector, multiplier, is_portfolio=False):
    """v5.6: 5단계 상태 메시지 로직 통합"""
    try:
        today_date = get_now_kst().date()
        df = fdr.DataReader(symbol, (get_now_kst() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 35: return None
        
        consistency_count, today_res = 0, None

        for i in range(3):
            target_df = df.iloc[:len(df)-i]
            is_today = (target_df.index[-1].date() == today_date)
            current_mult = multiplier if (is_today and i == 0) else 1.0
            
            ind = get_indicators(target_df, current_mult)
            grade = check_grade(ind)
            
            if i == 0:
                today_res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio,
                              'rsi': ind['rsi'], 'vol_ratio': ind['vol_ratio'], 'amount': ind['amount'],
                              'grade': grade, 'action': ""}
                
                if is_portfolio:
                    # --- [v5.6: 동근 님이 설계한 5단계 상태 메시지] ---
                    if ind['price'] < ind['ma10'] and grade is None:
                        today_res['action'] = "🚨 **탈출 고려 (추세 이탈)**"
                    elif ind['rsi'] >= 70:
                        today_res['action'] = "🛑 **탈출 권고 (과열 구간)**"
                    elif ind['rsi'] < 50 and grade is not None:
                        today_res['action'] = "✅ **추매 고려 (에너지 응축)**"
                    elif grade is not None and ind['price'] >= ind['ma10']:
                        today_res['action'] = "💎 **보유 유지 (추세 있음)**"
                    else:
                        today_res['action'] = "🧐 **관찰 필요 (기세 약화)**"
                
                if not is_portfolio and grade is None: return None
                if grade: consistency_count = 1
                else: break
            else:
                if grade: consistency_count += 1
                else: break
        
        if today_res: today_res['consistency'] = consistency_count
        return today_res
    except Exception as e:
        logger.error(f"오류 ({name}): {e}")
        return None

# --- [메인 실행부] ---

def main():
    logger.info("Smart Picking v5.6 분석 시작")
    
    krx = fdr.StockListing('KRX')
    robust = krx[krx['Marcap'] >= 500_000_000_000]
    
    my_codes = []
    if os.path.exists('portfolio.txt'):
        with open('portfolio.txt', 'r', encoding='utf-8') as f:
            my_codes = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    vol_multiplier = get_weighted_volume_multiplier()
    s_cands, a_cands, port_res = [], [], []

    # v5.5의 이중 루프 방식을 그대로 유지 (확실한 분석 보장)
    with ThreadPoolExecutor(max_workers=10) as executor:
        # 1. 전체 시장 후보 분석
        tasks = [executor.submit(analyze_stock, r['Code'], r['Name'], r.get('Sector', '기타'), vol_multiplier, False) for _, r in robust.iterrows()]
        
        # 2. 내 보유 종목 개별 분석 (portfolio.txt 기반)
        for c in my_codes:
            m = krx[krx['Code'] == c]
            if not m.empty: 
                tasks.append(executor.submit(analyze_stock, c, m.iloc[0]['Name'], m.iloc[0].get('Sector', '기타'), vol_multiplier, True))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: port_res.append(res)
            elif res['grade'] == 'S': s_cands.append(res)
            elif res['grade'] == 'A': a_cands.append(res)

    # 리포트 데이터 정렬
    s_sorted = sorted(s_cands, key=lambda x: x['vol_ratio'], reverse=True)
    final_s = s_sorted[:5]
    final_a = sorted(s_sorted[5:] + a_cands, key=lambda x: x['vol_ratio'], reverse=True)[:10]

    def get_badge(c):
        if c >= 3: return "🔥 **[3일 연속 우수]**"
        if c == 2: return "✅ **[2일 연속 우수]**"
        return "🆕 **[신규 진입]**"

    msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v5.6)**\n"
    msg += f"📊 분석 기준: KST {get_now_kst().strftime('%H:%M')}\n\n"
    
    msg += "📁 **내 보유 종목 대응**\n"
    msg += "\n".join([f"- {r['name']}: {r['action']} (RSI:{r['rsi']:.1f})" for r in port_res]) if port_res else "- 없음"
    msg += "\n\n"

    msg += "💎 **S급: 추세 폭발 우량주 (Max 5)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {get_badge(r['consistency'])}" for r in final_s]) if final_s else "- 없음"
    msg += "\n\n"

    msg += "✨ **A급: 안정적 추세 안착 (Max 10)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, {r['vol_ratio']:.1f}배) {get_badge(r['consistency'])}" for r in final_a]) if final_a else "- 없음"

    send_telegram_report(msg)

if __name__ == "__main__":
    main()
