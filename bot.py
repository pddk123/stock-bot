import FinanceDataReader as fdr
import pandas as pd
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 환경 변수 설정 (GitHub Secrets)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    """텔레그램 메시지 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def load_portfolio():
    """portfolio.txt에서 종목 코드를 읽어옴 (# 뒤의 주석 무시)"""
    codes = []
    try:
        if os.path.exists('portfolio.txt'):
            with open('portfolio.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    # '#' 앞부분만 취하고 공백 제거
                    clean_line = line.split('#')[0].strip()
                    if not clean_line: continue
                    
                    # 쉼표나 공백으로 구분된 코드 추출
                    parts = clean_line.replace(',', ' ').split()
                    for code in parts:
                        if code.strip(): codes.append(code.strip())
            return list(set(codes)) # 중복 제거
    except Exception as e:
        print(f"❌ 포트폴리오 로드 오류: {e}")
    return []

def get_market_sentiment():
    """시장 동향 상세 분석 (5일선 이격도 포함)"""
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11'}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    report = "📊 시장 동향 브리핑\n"
    scores = 0
    
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            curr = df['Close'].iloc[-1]
            prev = df['Close'].iloc[-2]
            ma5 = df['Close'].rolling(5).mean().iloc[-1]
            chg = (curr - prev) / prev * 100
            gap_ma5 = (curr - ma5) / ma5 * 100 # 5일선 대비 이격도
            
            trend = "과열" if gap_ma5 > 3 else "안정" if gap_ma5 > -2 else "침체"
            report += f"- {name}: {chg:+.2f}% ({trend} / 5일선이격 {gap_ma5:+.1f}%)\n"
            if chg > -0.5: scores += 1
        except: continue
    
    sentiment = "BULL" if scores >= 2 else "BEAR"
    return sentiment, report

def calculate_rsi(series, period=14):
    """RSI 지표 계산"""
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sentiment, is_portfolio=False):
    """개별 종목 상세 분석"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        # 주요 지표 계산
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        
        curr, prev = df.iloc[-1], df.iloc[-2]
        vol_ratio = curr['Volume'] / curr['Vol_MA5']
        rsi_val = curr['RSI']
        
        res = {'name': name, 'symbol': symbol, 'is_portfolio': is_portfolio, 
               'grade': None, 'desc': "", 'sell_desc': "", 'rsi': rsi_val}
        
        # 상세 수치 정보
        metrics = f"(RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"

        # 1. 매수 로직 (BULL 시장일 때만 추천)
        if sentiment == "BULL":
            if rsi_val <= 30:
                res.update({'grade': 'S', 'desc': f"강한 과매도 {metrics}"})
            elif (prev['Close'] < prev['MA20']) and (curr['Close'] > curr['MA20']) and (vol_ratio > 1.0):
                res.update({'grade': 'S', 'desc': f"20일선 돌파 {metrics}"})
            elif rsi_val <= 45:
                res.update({'grade': 'A', 'desc': f"단기 과매도 {metrics}"})
            elif (prev['Close'] < prev['MA10']) and (curr['Close'] > prev['MA10']) and (vol_ratio > 0.8):
                res.update({'grade': 'A', 'desc': f"10일선 반등 {metrics}"})

        # 2. 매도 로직 (보유 여부와 상관없이 감시)
        if rsi_val >= 80:
            res['sell_desc'] = f"극심 과열 {metrics}"
        elif rsi_val >= 70 and curr['Close'] < curr['MA10']:
            res['sell_desc'] = f"추세 이탈 {metrics}"
            
        return res if (res['grade'] or res['sell_desc'] or is_portfolio) else None
    except: return None

def main():
    sentiment, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    # 분석 대상: 코스피 200 + 코스닥 150
    k200 = fdr.StockListing('KOSPI').head(200)
    kd150 = fdr.StockListing('KOSDAQ').head(150)
    total_market = pd.concat([k200, kd150])
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        # 1. 보유 종목 분석용 태스크
        p_futures = [executor.submit(analyze_stock, code, code, sentiment, True) for code in my_stock_codes]
        # 2. 시장 종목 분석용 태스크
        m_futures = [executor.submit(analyze_stock, row['Code'], row['Name'], sentiment, False) for _, row in total_market.iterrows()]
        
        for future in as_completed(p_futures + m_futures):
            r = future.result()
            if not r: continue
            
            # 내 보유 종목 리포트 (중복 방지 및 우선 배치)
            if r['is_portfolio']:
                status = "보유유지" if not r['sell_desc'] else f"매도검토({r['sell_desc']})"
                portfolio_res.append(f"- {r['name']}({r['symbol']}): {status} / RSI:{r['rsi']:.1f}")
            
            # 신규 후보 리포트
            if r['grade'] == 'S': 
                s_list.append(f"- *{r['name']}*: {r['desc']}")
            elif r['grade'] == 'A': 
                a_list.append(f"- {r['name']}: {r['desc']}")
            
            # 매도 추천 리스트 (내 종목이 아닌 경우만 표시)
            if r['sell_desc'] and not r['is_portfolio']:
                sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 최종 보고서 조립
    report = f"{mkt_report}\n"
    report += "📁 내 보유 종목 현황\n" + ("\n".join(portfolio_res) if portfolio_res else "- 등록된 종목 없음") + "\n\n"
    report += "💎 S급 필승 후보\n" + ("\n".join(s_list[:10]) if s_list else "- 없음") + "\n\n"
    report += "✨ A급 관심 후보\n" + ("\n".join(a_list[:10]) if a_list else "- 없음") + "\n\n"
    report += "🔔 매도 추천 리스트\n" + ("\n".join(sell_list[:10]) if sell_list else "- 없음")
    
    send_telegram_message(report)

if __name__ == "__main__":
    main()
