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

echo Committing model weights...
git add vocab_model.json frames_model.json

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
