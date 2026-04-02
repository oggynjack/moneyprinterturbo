@echo off
set CURRENT_DIR=%CD%
echo ***** Current directory: %CURRENT_DIR% *****
set PYTHONPATH=%CURRENT_DIR%
set PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.0\bin;%PATH%

rem set HF_ENDPOINT=https://hf-mirror.com
streamlit run .\webui\Main.py --browser.gatherUsageStats=False --server.enableCORS=True