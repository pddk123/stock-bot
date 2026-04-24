import FinanceDataReader as fdr
import pandas as pd
import requests
import os
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# [환경 변수 및 기본 함수는 기존과 동일]
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def get_krx_sectors():
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        url = 'https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        response = requests.get(url, headers=headers)
        df = pd.read_html(response.text, header=0)[0]
        df['종목코드'] = df['종목코드'].apply(lambda x: f"{x:06d}")
        return df[['종목코드', '업종']].rename(columns={'종목코드':'Code', '업종':'Sector'})
    except: return pd.DataFrame(columns=['Code', 'Sector'])

def load_portfolio():
    codes = []
    if os.path.exists('portfolio.txt'):
        with open('portfolio.txt', 'r', encoding='utf-8') as f:
            for line in f:
                clean_line = line.split('#')[0].strip()
                if not clean_line: continue
                parts = clean_line.replace(',', ' ').split()
                for code in parts: codes.append(code.strip())
    return list(set(codes))

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sector, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr = df.iloc[-1]
        rsi_val = df['RSI'].iloc[-1]

        # 시간 가중치 계산 (10시 전략)
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        m_start = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed = max(0.1, min((now_kst - m_start).total_seconds() / 3600, 6.5))
        time_weight = 6.5 / elapsed
        expected_vol_ratio = (curr['Volume'] / curr['Vol_MA5']) * time_weight

        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': expected_vol_ratio, 'sell_desc': ""}

        if (45 <= rsi_val <= 65) and (curr['Close'] > curr['MA10'] > curr['MA20']) and (expected_vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        elif (rsi_val > 50) and (curr['Close'] > curr['MA10']) and (expected_vol_ratio >= 1.0):
            res.update({'grade': 'A'})

        if rsi_val >= 80: res['sell_desc'] = f"🔥 과열(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    # 시장 리스팅 및 섹터 통합
    krx_price = fdr.StockListing('KRX')
    krx_sector = get_krx_sectors()
    all_stocks = pd.merge(krx_price, krx_sector, on='Code', how='left').fillna({'Sector': '기타'})
    
    # 1. 시총 1500억 이상 상위 400개 스캔 (여유있게 스캔)
    robust_market = all_stocks[all_stocks['Marcap'] >= 150_000_000_000].sort_values(by='Marcap', ascending=False).head(400)
    my_codes = load_portfolio()
    
    s_list, a_list, portfolio_res = [], [], []

    with ThreadPoolExecutor(max_workers=10) as executor:
        p_tasks = [executor.submit(analyze_stock, code, row['Name'], row['Sector'], True) 
                   for code in my_codes for _, row in all_stocks[all_stocks['Code']==code].iterrows()]
        m_tasks = [executor.submit(analyze_stock, row['Code'], row['Name'], row['Sector'], False) 
                   for _, row in robust_market.iterrows()]
        
        for future in as_completed(p_tasks + m_tasks):
            r = future.result()
            if not r: continue
            if r['is_portfolio']:
                status = f"✅ 추가매수({r['grade']}급)" if r['grade'] else "보유유지"
                if r['sell_desc']: status = f"🚨 매도검토({r['sell_desc']})"
                portfolio_res.append(f"- {r['name']}: {status}")
            else:
                if r['grade'] == 'S': s_list.append(r)
                elif r['grade'] == 'A': a_list.append(r)

    # --- [리포트 생성: S급 시총 상위 5선 필터] ---
    final_msg = f"🌿 **rootee님, 10시 전략 (시총 상위 5선) 리포트**\n\n"
    
    final_msg += "💎 **S급: 정예 대장주 (시총 상위 5선)**\n"
    if s_list:
        df_s = pd.DataFrame(s_list)
        # 시총 데이터 결합 및 정렬
        df_s = pd.merge(df_s, all_stocks[['Code', 'Marcap']], left_on='symbol', right_on='Code', how='left')
        df_s = df_s.sort_values(by='Marcap', ascending=False).head(5)
        
        # 5개 중 거래량 폭발력이 가장 좋은 종목 찾기
        tech_leader_idx = df_s['vol_ratio'].idxmax()
        
        for i, row in df_s.iterrows():
            stability = "🏢" if row['Marcap'] >= 1_000_000_000_000 else "🔹"
            leader_tag = " 🏆 **거래량 대장**" if i == tech_leader_idx else ""
            final_msg += f"{stability} {row['name']}: (RSI:{row['rsi']:.1f}, 예상:{row['vol_ratio']:.1f}배{leader_tag})\n"
    else: final_msg += "- 조건 충족 없음\n"

    final_msg += "\n✨ **A급: 추세 안착 (상위 7선)**\n"
    if a_list:
        # A급도 시총 순으로 상위 7개만
        df_a = pd.merge(pd.DataFrame(a_list), all_stocks[['Code', 'Marcap']], left_on='symbol', right_on='Code', how='left')
        for _, row in df_a.sort_values(by='Marcap', ascending=False).head(7).iterrows():
            final_msg += f"- {row['name']}: (RSI:{row['rsi']:.1f}, 예상:{row['vol_ratio']:.1f}배)\n"

    final_msg += f"\n📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음")
    send_telegram_message(final_msg)

if __name__ == "__main__":
    main()
