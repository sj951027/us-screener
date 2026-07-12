@echo off
title US Screener - seed collector
cd /d "%~dp0"

echo ========================================================================
echo   US seed collector  (listing + index membership, backfill-impossible)
echo   Start: %DATE% %TIME%
echo ========================================================================

python us_seed_collector.py

echo.
echo ========================================================================
echo   US ohlcv incremental (daily prices, ~10-20 min for full universe)
echo ========================================================================
python us_ohlcv_collector.py

echo.
echo   Done: %DATE% %TIME%   (data: ..\us-screener-data\)
echo ========================================================================
