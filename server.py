import os
from datetime import datetime, timezone
import ccxt
import cot_reports as cot
import pandas as pd
import requests
import yfinance as yf
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

PORT = int(os.environ.get("PORT", 8000))
mcp = FastMCP("QuantMarketMCP", host="0.0.0.0", port=PORT)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "QuantMarketMCP"})


@mcp.tool()
def cek_crypto_ccxt(symbol: str = "BTC/USDT") -> str:
    """Ambil harga live, perubahan 24 jam, volume, dan funding rate kripto via CCXT."""
    exchanges = ["okx", "kraken", "kucoin", "bybit"]
    last_err = ""
    for ex_id in exchanges:
        try:
            ex_class = getattr(ccxt, ex_id)
            exchange = ex_class({"enableRateLimit": True, "timeout": 10000})
            ticker = exchange.fetch_ticker(symbol)

            funding_info = "N/A"
            try:
                swap_sym = symbol if ":" in symbol else f"{symbol}:USDT"
                fr = exchange.fetch_funding_rate(swap_sym)
                if fr and fr.get("fundingRate") is not None:
                    funding_info = f"{float(fr['fundingRate']) * 100:.4f}%"
            except Exception:
                pass

            return (
                f"[{ex_id.upper()} | {symbol}]\n"
                f"Harga Terakhir: {ticker.get('last')}\n"
                f"Perubahan 24J: {ticker.get('percentage')}%\n"
                f"High / Low 24J: {ticker.get('high')} / {ticker.get('low')}\n"
                f"Volume 24J: {ticker.get('quoteVolume')}\n"
                f"Funding Rate: {funding_info}"
            )
        except Exception as e:
            last_err = f"{ex_id}: {e}"
            continue
    return f"Gagal ambil data CCXT untuk {symbol}. Error terakhir: {last_err}"


@mcp.tool()
def cek_market_yfinance(ticker: str = "GC=F", period: str = "1mo") -> str:
    """Ambil harga historis, perubahan harian/mingguan, ATR(14), dan tren EMA dari Yahoo Finance (contoh: GC=F, ^IXIC, DX-Y.NYB, ^TNX, BTC-USD)."""
    try:
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df.empty or len(df) < 5:
            return f"Data yfinance kosong atau terlalu sedikit untuk {ticker}."

        close = df["Close"]
        last_price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])
        chg_1d = ((last_price - prev_price) / prev_price) * 100

        idx_7d = -6 if len(df) >= 6 else 0
        price_7d = float(close.iloc[idx_7d])
        chg_5d = ((last_price - price_7d) / price_7d) * 100

        high_low = df["High"] - df["Low"]
        atr_14 = float(high_low.tail(14).mean())

        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        trend = "BULLISH" if last_price > ema20 > ema50 else ("BEARISH" if last_price < ema20 < ema50 else "SIDEWAYS / TRANSISI")

        return (
            f"[Yahoo Finance | {ticker}]\n"
            f"Close Terakhir: {last_price:.4f}\n"
            f"Perubahan 1 Hari: {chg_1d:+.2f}%\n"
            f"Perubahan 5 Hari: {chg_5d:+.2f}%\n"
            f"ATR(14) Harian: {atr_14:.4f}\n"
            f"EMA20 / EMA50: {ema20:.4f} / {ema50:.4f}\n"
            f"Status Tren: {trend}"
        )
    except Exception as e:
        return f"Error yfinance ({ticker}): {e}"


@mcp.tool()
def cek_cot_cftc(keyword: str = "GOLD") -> str:
    """Ambil laporan mingguan CFTC COT posisi Non-Commercial / Hedge Fund (contoh keyword: GOLD, EURO FX, NASDAQ, BITCOIN, CRUDE OIL)."""
    try:
        tahun = datetime.now(timezone.utc).year
        df = cot.cot_year(year=tahun, cot_report_type="legacy_fut")
        col_name = [c for c in df.columns if "Market and Exchange Names" in c][0]
        filtered = df[df[col_name].str.contains(keyword.upper(), na=False)].copy()

        if filtered.empty:
            return f"Kontrak dengan kata kunci '{keyword}' tidak ditemukan di laporan COT {tahun}."

        date_col = [c for c in filtered.columns if "As of Date" in c or "Report_Date" in c][0]
        filtered = filtered.sort_values(by=date_col)
        latest = filtered.iloc[-1]

        oi = int(latest.get("Open Interest (All)", 0))
        nc_long = int(latest.get("Noncommercial Positions-Long (All)", 0))
        nc_short = int(latest.get("Noncommercial Positions-Short (All)", 0))
        net_pos = nc_long - nc_short
        bias = "NET LONG (Bullish)" if net_pos > 0 else "NET SHORT (Bearish)"

        return (
            f"[CFTC COT Report | {latest[col_name]}]\n"
            f"Tanggal Laporan: {latest[date_col]}\n"
            f"Open Interest: {oi:,}\n"
            f"Non-Commercial Long: {nc_long:,}\n"
            f"Non-Commercial Short: {nc_short:,}\n"
            f"Net Position: {net_pos:+,} ({bias})"
        )
    except Exception as e:
        return f"Gagal ambil laporan CFTC COT ({keyword}): {e}"


@mcp.tool()
def cek_defillama_liquidity() -> str:
    """Ambil pertumbuhan suplai Stablecoin global 7 hari dan TVL 5 blockchain teratas dari DeFiLlama."""
    try:
        sc_resp = requests.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=12)
        sc_data = sc_resp.json()
        latest_mcap = float(sc_data[-1]["totalCirculatingUSD"]["peggedUSD"])
        prev_7d_mcap = float(sc_data[-8]["totalCirculatingUSD"]["peggedUSD"])
        diff_7d = latest_mcap - prev_7d_mcap
        pct_7d = (diff_7d / prev_7d_mcap) * 100

        chains_resp = requests.get("https://api.llama.fi/v2/chains", timeout=12)
        chains = sorted(chains_resp.json(), key=lambda x: float(x.get("tvl", 0)), reverse=True)[:5]
        top_chains = ", ".join([f"{c['name']} (${float(c['tvl'])/1e9:.2f}B)" for c in chains])

        return (
            f"[DeFiLlama Macro Liquidity]\n"
            f"Total Market Cap Stablecoin: ${latest_mcap/1e9:.2f} B\n"
            f"Arus Bersih Stablecoin 7 Hari: ${diff_7d/1e9:+.2f} B ({pct_7d:+.2f}%)\n"
            f"Top 5 Chain TVL: {top_chains}"
        )
    except Exception as e:
        return f"Gagal ambil data DeFiLlama: {e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
