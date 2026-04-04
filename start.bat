@echo off
echo Instalando dependencias...
pip install -r requirements.txt --quiet
echo.
echo Iniciando M3U Manager en http://localhost:5000
echo Pulsa Ctrl+C para parar.
echo.
python app.py
pause
