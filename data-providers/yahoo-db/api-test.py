"""
api-test.py - Simple test for the YahooDB API client.

Reads TSLA ticker info from the running Yahoo-DB Flask application.
Make sure the server is running before executing this script:
    run.cmd   (Windows)
    ./run.sh  (Linux / macOS)
"""

import importlib.util
import pathlib
import json

# ---- Load the YahooDB class from yahoodb-api.py (hyphen prevents normal import) ----
_here = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("yahoodb_api", _here / "yahoodb-api.py")
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
YahooDB = _mod.YahooDB
YahooDBError = _mod.YahooDBError


def main():
    # Connect to the Yahoo-DB server
    db = YahooDB(host="localhost", port=5000)

    print("=" * 60)
    print("  Yahoo-DB API Test")
    print("=" * 60)

    # 1. Service status
    print("\n[1] Service status")
    try:
        status = db.get_status()
        print(f"    Sync running : {status.get('sync_running')}")
        print(f"    Sync status  : {status.get('sync_status')}")
        print(f"    Ticker count : {status.get('ticker_count')}")
        print(f"    Last sync    : {status.get('last_sync_time')}")
    except YahooDBError as e:
        print(f"    ERROR: {e}")

    # 2. TSLA ticker info
    symbol = "TSLA"
    print(f"\n[2] Ticker info for {symbol}")
    try:
        info = db.get_ticker_info(symbol)
        print(json.dumps(info, indent=4))
    except YahooDBError as e:
        print(f"    ERROR ({e.status_code}): {e}")

    # 3. Last 7 days of TSLA history
    print(f"\n[3] Last 7 days of {symbol} price history")
    try:
        history = db.get_ticker_history(symbol, days=7)
        if history:
            print(f"    {'Date':<12}  {'Open':>9}  {'Close':>9}  {'High':>9}  {'Low':>9}  {'Volume':>12}")
            print(f"    {'-'*12}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*12}")
            for row in history:
                print(
                    f"    {row['date']:<12}  "
                    f"{row['open']:>9.2f}  "
                    f"{row['close']:>9.2f}  "
                    f"{row['high']:>9.2f}  "
                    f"{row['low']:>9.2f}  "
                    f"{row['volume']:>12,}"
                )
        else:
            print("    No history records returned.")
    except YahooDBError as e:
        print(f"    ERROR ({e.status_code}): {e}")

    print("\n" + "=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
