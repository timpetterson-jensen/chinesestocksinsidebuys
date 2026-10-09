import os
import sys
import html
import requests
import datetime
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_lookback_window(days=90):
    """Returns today's date and the start date for insider trade lookback."""
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=days)
    return start_date, today

def check_china_insider_trades(sym):
    """Worker function to parse insider transactions for a Chinese ticker using yfinance."""
    try:
        tk = yf.Ticker(sym)
        insider_df = tk.insider_transactions
        
        if insider_df is None or insider_df.empty:
            return None

        # Clean column names
        insider_df.columns = [str(c).lower().strip() for c in insider_df.columns]
        
        buy_shares = 0
        total_value = 0.0
        transaction_count = 0

        # Filter for Purchases / Buy transactions
        for _, row in insider_df.iterrows():
            text_str = str(row.to_dict()).lower()
            if 'buy' in text_str or 'purchase' in text_str:
                shares = row.get('shares', 0) or 0
                val = row.get('value', 0) or 0
                try:
                    shares = float(shares)
                    val = float(val)
                except (ValueError, TypeError):
                    continue

                if shares > 0:
                    buy_shares += shares
                    total_value += val
                    transaction_count += 1

        info = {}
        try:
            info = tk.info or {}
        except Exception:
            pass

        price = info.get('regularMarketPrice') or info.get('currentPrice') or info.get('previousClose') or 0.0
        currency = str(info.get('currency', 'USD')).upper()
        company_name = info.get('shortName') or info.get('longName') or sym

        if buy_shares > 0 or transaction_count > 0:
            return {
                'symbol': sym,
                'name': company_name,
                'buy_shares': buy_shares,
                'est_value': total_value,
                'tx_count': transaction_count,
                'price': price,
                'currency': currency
            }
    except Exception as e:
        print("Error checking ticker:", sym, e)
    return None

def fetch_china_insider_buying(fmp_key):
    """Fetches and aggregates Chinese stocks with insider purchases."""
    start_date, end_date = get_lookback_window(days=90)
    print("--> Screening China Stock Insider Buying from", start_date, "to", end_date)

    candidate_symbols = set()

    # Strategy 1: FMP Insider Trading API
    if fmp_key:
        print("--> Querying FMP Insider Trading Feed...")
        fmp_url = "https://financialmodelingprep.com/api/v4/insider-trading?page=0&apikey=" + fmp_key
        try:
            res = requests.get(fmp_url, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    for item in data:
                        acq = str(item.get('acquisitonOrDisposition', '')).upper()
                        sym = str(item.get('symbol', '')).strip().upper()
                        if acq == 'A' and sym:  # 'A' = Acquisition / Purchase
                            candidate_symbols.add(sym)
        except Exception as e:
            print("FMP Request Exception:", e)

    # Strategy 2: Curated Watchlist of Top Chinese ADRs and HK dual-listed equities
    china_pool = [
        "BABA", "PDD", "JD", "BIDU", "NIO", "XPEV", "LI", "TME", "BILI", "NTES",
        "EDU", "TAL", "YUMC", "TCHEY", "PROSY", "BEKE", "KWEB", "FXI", "ZTO", "HTHT",
        "FUTU", "TIGR", "IQ", "HUYA", "GDS", "KC", "VNET", "DQ", "JKS", "CSIQ",
        "RLX", "BZ", "LKNCY", "DIDIY", "GOTU", "BEST", "FINV", "QFIN", "LU", "LX",
        "ATHM", "WB", "SOHU", "MOMO", "YY", "TUYA", "CD", "XNET", "YMM", "KANZHUN"
    ]

    for sym in china_pool:
        candidate_symbols.add(sym)

    print("--> Multi-threading insider checks for candidates:", len(candidate_symbols))
    qualifying_stocks = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(check_china_insider_trades, sym)
            for sym in candidate_symbols
        ]
        for future in as_completed(futures):
            res = future.result()
            if res:
                qualifying_stocks.append(res)

    return qualifying_stocks

def send_telegram_msg(bot_token, chat_id, msg):
    """Sends plain text message to Telegram with automatic payload chunking."""
    tg_url = "https://api.telegram.org/bot" + bot_token + "/sendMessage"

    lines = msg.split('\n')
    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > 3500:
            chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk:
        chunks.append(current_chunk)

    for idx, chunk in enumerate(chunks, 1):
        payload = {'chat_id': chat_id, 'text': chunk}
        try:
            res = requests.post(tg_url, json=payload, timeout=10)
            print("Chunk Delivery Code:", res.status_code)
            if res.status_code != 200:
                print("Telegram API Error Payload:", res.text)
        except Exception as e:
            print("Telegram Post Exception:", e)

def main():
    fmp_key = os.environ.get('FMP_API_KEY', '').strip()
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

    print("=== TOP 50 CHINA STOCKS INSIDER BUYING SCREENER ===")

    if not bot_token or not chat_id:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing.")
        sys.exit(1)

    raw_stocks = fetch_china_insider_buying(fmp_key)
    start_date, end_date = get_lookback_window(days=90)

    if not raw_stocks:
        no_stocks_msg = "🇨🇳 China Stocks Insider Buying Report:\nNo insider purchases detected in the last 90 days across scanned equities."
        send_telegram_msg(bot_token, chat_id, no_stocks_msg)
        sys.exit(0)

    df = pd.DataFrame(raw_stocks)
    df = df.drop_duplicates(subset=['symbol'])
    
    # Sort by total transaction count / insider buy activity and take top 50
    df_sorted = df.sort_values(by=['tx_count', 'buy_shares'], ascending=[False, False]).head(50)

    formatted_rows = []
    for idx, row in enumerate(df_sorted.to_dict('records'), 1):
        sym_clean = html.escape(str(row['symbol']))
        name_clean = html.escape(str(row['name'])[:20])
        tx_cnt = str(row['tx_count'])
        shares_bought = f"{float(row['buy_shares']):,.0f}" if row['buy_shares'] > 0 else "Active"
        prc = f"{float(row['price']):.2f}"
        curr = str(row['currency'])
        
        row_str = str(idx) + ". " + sym_clean + " (" + name_clean + ") | Buys: " + tx_cnt + " | Shares: " + shares_bought + " | Price: $" + prc + " " + curr
        formatted_rows.append(row_str)

    header = "🇨🇳 Top 50 Chinese Stocks - Insider Buying Tracker\nLookback: " + str(start_date) + " to " + str(end_date) + "\n\n"
    msg_body = header + "\n".join(formatted_rows)

    send_telegram_msg(bot_token, chat_id, msg_body)

if __name__ == '__main__':
    try:
        main()
    except Exception as fatal_err:
        print("Caught Script Exception:", fatal_err)
        sys.exit(1)
