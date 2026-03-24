@echo off
REM Nightly SRS model retraining
REM Schedule with Windows Task Scheduler (separate from vocab sync job)
REM Suggested time: 30 minutes after the vocab sync job

cd /d "%~dp0"

REM Activate virtual environment if you have one (uncomment and adjust if needed)
REM call venv\Scripts\activate

echo Retraining SRS models...
python retrain.py

if errorlevel 1 (
    echo Retrain failed, skipping git push
    echo Retrain FAILED at %date% %time% >> retrain_log.txt
    exit /b 1
)

echo Updating version timestamp...
python -c "import json; from datetime import datetime; d=datetime.now(); open('version.json','w').write(json.dumps({'updated': d.strftime('%%b') + ' ' + str(d.day) + ', ' + d.strftime('%%Y') + ' ' + str(d.hour %% 12 or 12) + ':' + d.strftime('%%M') + ' ' + ('AM' if d.hour < 12 else 'PM')}))"

echo Committing model weights...
git add vocab_model.json frames_model.json version.json

REM Only commit if there are actual changes
git diff --staged --quiet
if errorlevel 1 (
    git commit -m "Update SRS model weights"
    git push
    echo Pushed model updates
) else (
    echo No model changes since last run
)

echo Retrain completed at %date% %time% >> retrain_log.txt
