import pandas as pd
import FinanceDataReader as fdr
import requests
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 로깅 설정: 개선 4 (세분화된 예외 처리 및 로깅)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('stock_analyzer.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 환경 변수 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Telegram 전송 실패: {e}")

def get_weighted_volume_multiplier():
    """KOSPI/KOSDAQ U자형 거래량 곡선을 반영한 보정 계수"""
    now = datetime.now() + timedelta(hours=9) # KST 기준
    if now.weekday() >= 5: return 1.0 # 주말
    
    current_time = now.hour * 60 + now.minute
    start_market = 9 * 60 # 09:00
    elapsed = current_time - start_market
    
    if elapsed <= 0 or elapsed >= 390: return 1.0

    # 시간대별 누적 거래량 비중 기반 보정
    if elapsed <= 60: # 09:00 ~ 10:00
        weight = (elapsed / 60) * 0.35
    elif elapsed <= 360: # 10:00 ~ 15:00
        weight = 0.35 + ((elapsed - 60) / 300) * 0.40
    else: # 15:00 ~ 15:30
        weight = 0.75 + ((elapsed - 360) / 30) * 0.25
        
    return 1.0 / weight

def calculate_rsi_wilder(series, period=14):
    """개선 2: Wilder's Smoothing (RMA) 방식의 RSI 계산"""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    
    # RMA 방식: ewm의 alpha를 1/period로 설정
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = roll_up / roll_down
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.iloc[-1]

def get_kind_managed_stocks():
    """관리종목 리스트 확보"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = 'https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=05'
        res = requests.get(url, headers=headers)
        df = pd.read_html(res.text, header=0)[0]
        return df['종목코드'].apply(lambda x: f"{x:06d}").tolist()
    except Exception as e:
        logger.error(f"관리종목 로드 실패: {e}")
        return []

def load_portfolio():
    """portfolio.txt 로드"""
    codes = []
    if os.path.exists('portfolio.txt'):
        try:
            with open('portfolio.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    clean = line.split('#')[0].strip().replace(',', ' ')
                    for c in clean.split(): codes.append(c.strip())
        except Exception as e:
            logger.error(f"포트폴리오 파일 읽기 실패: {e}")
    return list(set(codes))

def analyze_stock(symbol, name, sector, multiplier, is_portfolio=False):
    try:
        today = datetime.now() + timedelta(hours=9)
        df = fdr.DataReader(symbol, (today - timedelta(days=120)).strftime('%Y-%m-%d'))
        if df is None or len(df) < 30: return None
        
        # 개선 1: 당일 데이터 여부 검증
        last_date = df.index[-1]
        effective_multiplier = multiplier if last_date.date() == today.date() else 1.0
        
        # 지표 계산
        curr_price = df['Close'].iloc[-1]
        ma10 = df['Close'].rolling(10).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
        rsi_val = calculate_rsi_wilder(df['Close']) # Wilder 방식
        
        # 거래량 보정
        estimated_vol = df['Volume'].iloc[-1] * effective_multiplier
        vol_ratio = estimated_vol / vol_ma5 if vol_ma5 > 0 else 0
        amount_억 = (curr_price * estimated_vol) / 100_000_000

        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'amount': amount_억, 'action': ""}
        
        if is_portfolio:
            if rsi_val >= 70: res['action'] = "🚨 **매도 추천 (과열)**"
            elif rsi_val <= 45 and curr_price > ma60: res['action'] = "✅ **추가 매수 추천**"
            else: res['action'] = "💎 **보유 유지**"
        
        if not is_portfolio:
            # S급: 정배열 + 거래대금 50억 + RSI 적정 + 거래량 폭발
            is_dependable = (curr_price > ma10 > ma20 > ma60) and (amount_억 >= 50) and (rsi_val < 70)
            if is_dependable and (45 <= rsi_val <= 62) and (vol_ratio >= 1.5):
                res['grade'] = 'S'
            elif (50 <= rsi_val < 70) and (curr_price > ma10) and (vol_ratio >= 1.0):
                res['grade'] = 'A'
        
        return res
    except (KeyError, ValueError) as e:
        logger.error(f"데이터 처리 오류 ({name}/{symbol}): {e}")
    except Exception as e:
        logger.error(f"분석 중 알 수 없는 오류 ({name}/{symbol}): {e}")
    return None

def main():
    logger.info("Smart Picking v5.2 분석 시작")
    
    # 1. 시장 지수 확인
    mkt_report, up_count = [], 0
    for name, ticker in {'Nasdaq': '^IXIC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}.items():
        try:
            idx_df = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
            chg = (idx_df['Close'].iloc[-1] - idx_df['Close'].iloc[-2]) / idx_df['Close'].iloc[-2] * 100
            mkt_report.append(f"- {name}: {chg:+.2f}%")
            if chg > 0: up_count += 1
        except: pass
    
    m_status = "🚀 **상승장**" if up_count >= 2 else "📉 **하락/조정장**"
    m_desc = "글로벌 동조화 속 매수 심리가 살아나고 있습니다." if up_count >= 2 else "지수 하방 압력이 강합니다. 방어적인 관점이 필요합니다."
    
    # 2. 데이터 준비
    krx = fdr.StockListing('KRX')
    managed = get_kind_managed_stocks()
    robust = krx[(krx['Marcap'] >= 500_000_000_000) & (~krx['Code'].isin(managed))]
    my_codes = load_portfolio()
    vol_multiplier = get_weighted_volume_multiplier()

    all_s_candidates, all_a_candidates, portfolio_res = [], [], []

    # 3. 병렬 분석 실행
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = [executor.submit(analyze_stock, r['Code'], r['Name'], r.get('Sector', '기타'), vol_multiplier, False) for _, r in robust.iterrows()]
        for c in my_codes:
            match = krx[krx['Code'] == c]
            if not match.empty:
                tasks.append(executor.submit(analyze_stock, c, match.iloc[0]['Name'], match.iloc[0].get('Sector', '기타'), vol_multiplier, True))
        
        for f in as_completed(tasks):
            res = f.result()
            if not res: continue
            if res['is_portfolio']: portfolio_res.append(res)
            elif res['grade'] == 'S': all_s_candidates.append(res)
            elif res['grade'] == 'A': all_a_candidates.append(res)

    # 개선 3: 리스트 분리 및 합산 로직
    s_list_sorted = sorted(all_s_candidates, key=lambda x: x['vol_ratio'], reverse=True)
    final_s = s_list_sorted[:5] # S급 상위 5개
    
    # S급 5위 밖 종목들을 A급 풀과 합쳐서 정렬
    remaining_s = s_list_sorted[5:]
    final_a = sorted(remaining_s + all_a_candidates, key=lambda x: x['vol_ratio'], reverse=True)[:10]

    # 4. 메시지 조립 (5.1 출력 방식 유지)
    msg = f"🌿 **rootee님, 듬직한 우량주 리포트 (v5.2)**\n\n📊 **시장 상황: {m_status}**\n{m_desc}\n" + "\n".join(mkt_report) + "\n\n"
    
    p_lines = [f"- {r['name']}: {r['action']} (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in portfolio_res]
    msg += "📁 **내 보유 종목 대응**\n" + ("\n".join(p_lines) if p_lines else "- 없음") + "\n\n"
    
    msg += "💎 **S급: 추세 폭발 우량주 (Max 5)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in final_s]) if final_s else "- 조건 충족 없음"
    
    msg += "\n\n✨ **A급: 안정적 추세 안착 (Max 10)**\n"
    msg += "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in final_a]) if final_a else "- 없음"
    
    send_telegram_message(msg)
    logger.info("리포트 전송 완료")

if __name__ == "__main__":
    main()
