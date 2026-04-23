import FinanceDataReader as fdr
import pandas as pd
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 환경 변수 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def load_portfolio():
    """portfolio.txt에서 종목 코드를 읽어옴"""
    codes = []
    try:
        if os.path.exists('portfolio.txt'):
            with open('portfolio.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    clean_line = line.split('#')[0].strip()
                    if not clean_line: continue
                    parts = clean_line.replace(',', ' ').split()
                    for code in parts:
                        if code.strip(): codes.append(code.strip())
            return list(set(codes))
    except: pass
    return []

def get_market_sentiment():
    """시장 동향 및 상태 분석"""
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    scores, total_chg = 0, 0
    report = ""
    
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            curr, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
            chg = (curr - prev) / prev * 100
            total_chg += chg
            report += f"- {name}: {chg:+.2f}%\n"
            if chg > -0.2: scores += 1
        except: continue
    
    avg_chg = total_chg / len(indices)
    if scores >= 3 and avg_chg > 0.5: status = "🚀 **강력 상승장** (공격적 투자 가능)"
    elif scores >= 2: status = "📈 **완만한 상승장** (추세 추종 유리)"
    elif scores == 1: status = "📉 **주의 구간** (보수적 접근 권장)"
    else: status = "🆘 **하락장** (현금 비중 확대 필요)"
    
    return status, report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sector, sentiment, is_portfolio=False):
    """추세 대폭발 및 섹터 분석 로직"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        
        curr, prev = df.iloc[-1], df.iloc[-2]
        vol_ratio = curr['Volume'] / curr['Vol_MA5']
        rsi_val = df['RSI'].iloc[-1]
        
        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'desc': "", 'sell_desc': "", 'rsi': rsi_val, 'vol_ratio': vol_ratio}

        # 1. [S급: 추세 폭발 초입] 
        is_s_class = (45 <= rsi_val <= 62) and (curr['Close'] > curr['MA10'] > curr['MA20']) and (vol_ratio >= 1.5)
        if is_s_class:
            res.update({'grade': 'S', 'desc': f"추세폭발 (RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"})
        
        # 2. [A급: 안정적 추세안착]
        elif (rsi_val > 50) and (curr['Close'] > curr['MA10']) and (vol_ratio >= 1.0):
            res.update({'grade': 'A', 'desc': f"추세안착 (RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"})

        # [매도 로직: 과열권]
        if rsi_val >= 80:
            res['sell_desc'] = f"🔥 과열매도 (RSI:{rsi_val:.1f})"
            
        return res if (res['grade'] or res['sell_desc'] or is_portfolio) else None
    except: return None

def main():
    mkt_status, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    krx_listing = fdr.StockListing('KRX')
    # 시총 1,500억 이상 & 섹터 정보 있는 종목만 필터링
    robust_stocks = krx_listing[(krx_listing['Marcap'] >= 150_000_000_000) & (krx_listing['Sector'].notnull())]
    
    name_map = dict(zip(robust_stocks['Code'], robust_stocks['Name']))
    sector_map = dict(zip(robust_stocks['Code'], robust_stocks['Sector']))
    
    portfolio_res, s_grade_list, a_grade_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        # 1. 보유 종목 분석
        p_futures = [executor.submit(analyze_stock, code, name_map.get(code, code), sector_map.get(code, "기타"), mkt_status, True) for code in my_stock_codes]
        # 2. 시장 종목 분석 (상위 400개)
        m_futures = [executor.submit(analyze_stock, row['Code'], row['Name'], row['Sector'], mkt_status, False) for _, row in robust_stocks.head(400).iterrows()]
        
        for future in as_completed(p_futures + m_futures):
            r = future.result()
            if not r: continue
            if r['is_portfolio']:
                status = "보유유지"
                if r['sell_desc']: status = f"🚨 매도검토 ({r['sell_desc']})"
                elif r['grade']: status = f"✅ 추가매수권장 ({r['grade']}급)"
                portfolio_res.append(f"- {r['symbol']}({r['name']}): {status}")
            
            if r['grade'] == 'S': s_grade_list.append(r)
            elif r['grade'] == 'A': a_grade_list.append(r)
            if r['sell_desc'] and not r['is_portfolio']: sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 메시지 조립
    final_msg = f"🌿 **rootee님, 오늘의 투자 리포트**\n\n"
    final_msg += f"📊 **시장 진단**: {mkt_status}\n{mkt_report}\n"
    
    # 1. 매도 추천 (최상단)
    final_msg += "🔔 **[긴급] 매도 및 익절 검토**\n"
    final_msg += ("\n".join(sell_list[:7]) if sell_list else "- 현재 과열 종목 없음") + "\n\n"
    
    # 2. 내 포트폴리오
    final_msg += "📁 **내 보유 종목 현황**\n"
    final_msg += ("\n".join(portfolio_res) if portfolio_res else "- 등록된 종목 없음") + "\n\n"
    
    # 3. 섹터별 분류 및 대장주 (S급 & A급 합산 분석)
    combined_targets = s_grade_list + a_grade_list
    if combined_targets:
        df_res = pd.DataFrame(combined_targets)
        top_sectors = df_res['sector'].value_counts().head(3).index.tolist()
        final_msg += f"🔥 **현재 주도 섹터**: {', '.join(top_sectors)}\n\n"
        
        final_msg += "💎 **S급 섹터별 대장주 스캐닝**\n"
        df_s = pd.DataFrame(s_grade_list)
        if not df_s.empty:
            for sector, group in df_s.groupby('sector'):
                leader = group.loc[group['vol_ratio'].idxmax()]
                final_msg += f"📂 {sector}\n - 🏆 대장: *{leader['name']}* ({leader['vol_ratio']:.1f}배)\n"
                others = group[group['symbol'] != leader['symbol']]['name'].tolist()
                if others: final_msg += f" - 부대장: {', '.join(others[:2])}\n"
        else: final_msg += "- 조건 충족 종목 없음\n"
        
        final_msg += "\n✨ **A급 안정적 추세 섹터**\n"
        df_a = pd.DataFrame(a_grade_list)
        if not df_a.empty:
            for sector, group in df_a.groupby('sector').head(3).groupby('sector'): # 섹터당 3개씩만
                final_msg += f"- {sector}: {', '.join(group['name'].tolist())}\n"
    
    send_telegram_message(final_msg)

if __name__ == "__main__":
    main()
