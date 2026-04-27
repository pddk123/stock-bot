import pandas as pd
import FinanceDataReader as fdr
import requests
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────

# 로깅 설정 (개선 4: 광범위한 except 제거 → 로깅으로 대체)

# ─────────────────────────────────────────────

logging.basicConfig(
level=logging.INFO,
format=’%(asctime)s [%(levelname)s] %(message)s’,
handlers=[
logging.FileHandler(‘stock_analyzer.log’, encoding=‘utf-8’),
logging.StreamHandler()
]
)
logger = logging.getLogger(**name**)

# ─────────────────────────────────────────────

# 1. 환경 변수 설정

# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get(‘TELEGRAM_TOKEN’)
TELEGRAM_CHAT_ID = os.environ.get(‘TELEGRAM_CHAT_ID’)

def send_telegram_message(message):
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
logger.warning(“Telegram 환경 변수가 설정되지 않았습니다.”)
return
url = f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”
payload = {“chat_id”: TELEGRAM_CHAT_ID, “text”: message, “parse_mode”: “Markdown”}
try:
requests.post(url, json=payload, timeout=10)
except Exception as e:
logger.error(f”Telegram 메시지 전송 실패: {e}”)

# ─────────────────────────────────────────────

# 2. 거래량 보정 계수 (개선 1: 장중 실행 여부 검증 추가)

# ─────────────────────────────────────────────

def get_weighted_volume_multiplier():
“””
U자형 거래량 곡선을 반영한 시간 가중치 보정 계수 (KST 기준).
장 마감 후 또는 장 시작 전이면 1.0을 반환하고 경고 로그를 남긴다.
“””
now_kst = datetime.utcnow() + timedelta(hours=9)
start_market = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
end_market   = now_kst.replace(hour=15, minute=30, second=0, microsecond=0)
elapsed = (now_kst - start_market).total_seconds() / 60  # 분

```
if now_kst < start_market or now_kst > end_market:
    logger.warning(
        f"현재 시각 {now_kst.strftime('%H:%M')} KST — 장외 시간입니다. "
        "거래량 보정 계수를 1.0으로 설정합니다. "
        "당일 거래량 추정이 의미 없을 수 있습니다."
    )
    return 1.0

if elapsed <= 60:       # 09:00 ~ 10:00 (초반 폭발기)
    weight = (elapsed / 60) * 0.35
elif elapsed <= 360:    # 10:00 ~ 15:00 (정체기)
    weight = 0.35 + ((elapsed - 60) / 300) * 0.40
else:                   # 15:00 ~ 15:30 (마감 집중기)
    weight = 0.75 + ((elapsed - 360) / 30) * 0.25

weight = max(weight, 1e-6)  # 0 나누기 방지
return 1.0 / weight
```

# ─────────────────────────────────────────────

# 3. KIND 관리종목 조회

# ─────────────────────────────────────────────

def get_kind_managed_stocks():
try:
headers = {‘User-Agent’: ‘Mozilla/5.0’}
url = ‘https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=05’
res = requests.get(url, headers=headers, timeout=15)
df = pd.read_html(res.text, header=0)[0]
return df[‘종목코드’].apply(lambda x: f”{x:06d}”).tolist()
except Exception as e:
logger.error(f”KIND 관리종목 조회 실패: {e}”)
return []

# ─────────────────────────────────────────────

# 4. 포트폴리오 로드

# ─────────────────────────────────────────────

def load_portfolio():
codes = []
if os.path.exists(‘portfolio.txt’):
with open(‘portfolio.txt’, ‘r’, encoding=‘utf-8’) as f:
for line in f:
clean = line.split(’#’)[0].strip().replace(’,’, ’ ’)
for c in clean.split():
codes.append(c.strip())
else:
logger.warning(“portfolio.txt 파일이 없습니다. 보유 종목 분석을 건너뜁니다.”)
return list(set(codes))

# ─────────────────────────────────────────────

# 5. RSI 계산 (개선 2: Wilder RSI로 교체 + 임계값 주석 추가)

# ─────────────────────────────────────────────

def calculate_rsi(series, period=14):
“””
Wilder의 표준 RSI (RMA/Smoothed MA 방식).
기존 EWM 방식과 수치 차이가 있으므로 S/A 등급 임계값을 함께 재검토할 것.
현재 임계값: S급 45~62, A급 50~70 → Wilder RSI 기준으로 설정됨.
“””
if len(series) < period + 1:
return 50.0

```
delta = series.diff().dropna()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

# 첫 번째 평균: 단순 평균 (Wilder 초기값)
avg_gain = gain.iloc[:period].mean()
avg_loss = loss.iloc[:period].mean()

# 이후: Wilder Smoothing (RMA)
for i in range(period, len(gain)):
    avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
    avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period

if avg_loss == 0:
    return 100.0

rs = avg_gain / avg_loss
return 100 - (100 / (1 + rs))
```

# ─────────────────────────────────────────────

# 6. 개별 종목 분석

# ─────────────────────────────────────────────

def analyze_stock(symbol, name, sector, multiplier, market_is_up, is_portfolio=False):
“””
개선 1: DataReader 마지막 행이 당일 데이터인지 확인
개선 3: s_list[5:] 버그 제거 — 등급 분류만 담당, 슬라이싱은 main()에서 처리
개선 4: 예외를 구체적으로 잡고 로깅
“””
try:
end_date = datetime.now()
start_date = (end_date - timedelta(days=120)).strftime(’%Y-%m-%d’)
df = fdr.DataReader(symbol, start_date)

```
    if df is None or len(df) < 30:
        logger.debug(f"[{symbol}] 데이터 부족 (행 수: {len(df) if df is not None else 0})")
        return None

    # ── 개선 1: 당일 데이터 여부 확인 ──────────────────────────
    last_date = df.index[-1]
    today = pd.Timestamp(datetime.now().date())
    is_today_data = (last_date.date() == today.date())

    if not is_today_data:
        logger.debug(
            f"[{symbol}] 마지막 데이터가 당일이 아님 ({last_date.date()}). "
            "거래량 보정 계수가 적용되지 않습니다."
        )
        effective_multiplier = 1.0  # 전일 종가 → 보정 무의미
    else:
        effective_multiplier = multiplier
    # ────────────────────────────────────────────────────────────

    ma10  = df['Close'].rolling(10).mean().iloc[-1]
    ma20  = df['Close'].rolling(20).mean().iloc[-1]
    ma60  = df['Close'].rolling(60).mean().iloc[-1]
    vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
    rsi_val = calculate_rsi(df['Close'])
    curr_price = df['Close'].iloc[-1]

    estimated_vol = df['Volume'].iloc[-1] * effective_multiplier
    vol_ratio = estimated_vol / vol_ma5 if vol_ma5 > 0 else 0
    avg_amount = (curr_price * estimated_vol) / 100_000_000  # 예측 거래대금(억)

    res = {
        'name': name, 'symbol': symbol, 'sector': sector,
        'is_portfolio': is_portfolio,
        'grade': None, 'rsi': rsi_val,
        'vol_ratio': vol_ratio, 'amount': avg_amount, 'action': ""
    }

    # 보유 종목 대응 전략
    if is_portfolio:
        if rsi_val >= 70:
            res['action'] = "🚨 **매도 추천 (과열)**"
        elif rsi_val <= 45 and curr_price > ma60:
            res['action'] = "✅ **추가 매수 추천**"
        else:
            res['action'] = "💎 **보유 유지**"

    # ── 개선 5 (시장 상황 연동): 하락장이면 S급 기준 강화 ────────
    if not is_portfolio:
        s_vol_threshold = 1.5 if market_is_up else 2.0   # 하락장엔 거래량 기준 상향
        s_rsi_max       = 62  if market_is_up else 55    # 하락장엔 RSI 상한 하향
        a_vol_threshold = 1.0 if market_is_up else 1.3

        is_dependable = (
            curr_price > ma10 > ma20 > ma60
            and avg_amount >= 50
            and rsi_val < 70
        )
        if is_dependable and (45 <= rsi_val <= s_rsi_max) and (vol_ratio >= s_vol_threshold):
            res['grade'] = 'S'
        elif (50 <= rsi_val < 70) and (curr_price > ma10) and (vol_ratio >= a_vol_threshold):
            res['grade'] = 'A'
    # ────────────────────────────────────────────────────────────

    return res

except KeyError as e:
    logger.error(f"[{symbol}] 컬럼 누락 오류: {e}")
except ValueError as e:
    logger.error(f"[{symbol}] 값 처리 오류: {e}")
except Exception as e:
    logger.error(f"[{symbol}] 예상치 못한 오류: {e}")
return None
```

# ─────────────────────────────────────────────

# 7. 메인

# ─────────────────────────────────────────────

def main():
logger.info(”===== 주식 분석 시작 =====”)

```
# 시장 상황 분석
mkt_report, up_count = [], 0
for name, ticker in {'Nasdaq': '^IXIC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}.items():
    try:
        idx_df = fdr.DataReader(ticker, (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d'))
        chg = (idx_df['Close'].iloc[-1] - idx_df['Close'].iloc[-2]) / idx_df['Close'].iloc[-2] * 100
        mkt_report.append(f"- {name}: {chg:+.2f}%")
        if chg > 0:
            up_count += 1
    except Exception as e:
        logger.error(f"시장 지수 조회 실패 ({name}): {e}")

market_is_up = up_count >= 2
m_status = "🚀 **상승장**" if market_is_up else "📉 **하락/조정장**"
m_desc = (
    "글로벌 동조화 속 매수 심리가 살아나고 있습니다." if market_is_up
    else "지수 하방 압력이 강합니다. 방어적인 관점이 필요합니다."
)

# 데이터 준비
krx     = fdr.StockListing('KRX')
managed = get_kind_managed_stocks()
robust  = krx[(krx['Marcap'] >= 500_000_000_000) & (~krx['Code'].isin(managed))]
my_codes = load_portfolio()
vol_multiplier = get_weighted_volume_multiplier()

logger.info(f"분석 대상 우량주: {len(robust)}개 | 보유 종목: {len(my_codes)}개 | 거래량 보정 계수: {vol_multiplier:.2f}")

portfolio_res, s_list, a_list = [], [], []

with ThreadPoolExecutor(max_workers=10) as executor:
    tasks = [
        executor.submit(
            analyze_stock,
            r['Code'], r['Name'], r.get('Sector', '기타'),
            vol_multiplier, market_is_up, False
        )
        for _, r in robust.iterrows()
    ]
    for c in my_codes:
        match = krx[krx['Code'] == c]
        if not match.empty:
            tasks.append(executor.submit(
                analyze_stock,
                c, match.iloc[0]['Name'], match.iloc[0].get('Sector', '기타'),
                vol_multiplier, market_is_up, True
            ))
        else:
            logger.warning(f"보유 종목 코드 [{c}]를 KRX 목록에서 찾을 수 없습니다.")

    for f in as_completed(tasks):
        try:
            res = f.result()
        except Exception as e:
            logger.error(f"스레드 결과 수집 오류: {e}")
            continue

        if not res:
            continue
        if res['is_portfolio']:
            portfolio_res.append(
                f"- {res['name']}: {res['action']} (RSI:{res['rsi']:.1f}, 거래량:{res['vol_ratio']:.1f}배)"
            )
        elif res['grade'] == 'S':
            s_list.append(res)
        elif res['grade'] == 'A':
            a_list.append(res)

# ── 개선 3: s_list[5:] 버그 수정 ────────────────────────────────
# 기존: s_list = sorted(...)[:5] 이후 s_list[5:] → 항상 빈 리스트
# 수정: 정렬 후 상위 5개(S급)와 나머지를 분리한 뒤 A급과 합산
s_list_sorted   = sorted(s_list, key=lambda x: x['vol_ratio'], reverse=True)
s_top5          = s_list_sorted[:5]
s_overflow      = s_list_sorted[5:]   # S급 탈락분 → A급 풀에 편입

a_combined = sorted(
    s_overflow + a_list,
    key=lambda x: x['vol_ratio'], reverse=True
)[:10]
# ────────────────────────────────────────────────────────────────

# 메시지 조립
msg  = f"🌿 **rootee님, 듬직한 우량주 리포트 (v5.2)**\n\n"
msg += f"📊 **시장 상황: {m_status}**\n{m_desc}\n"
msg += "\n".join(mkt_report) + "\n\n"

msg += "📁 **내 보유 종목 대응**\n"
msg += ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"

msg += "💎 **S급: 추세 폭발 우량주 (Max 5)**\n"
msg += (
    "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in s_top5])
    if s_top5 else "- 조건 충족 없음"
)

msg += "\n\n✨ **A급: 안정적 추세 안착 (Max 10)**\n"
msg += (
    "\n".join([f"- {r['name']}: (RSI:{r['rsi']:.1f}, 거래량:{r['vol_ratio']:.1f}배)" for r in a_combined])
    if a_combined else "- 없음"
)

logger.info("메시지 전송 중...")
send_telegram_message(msg)
logger.info("===== 주식 분석 완료 =====")
```

if **name** == “**main**”:
main()
