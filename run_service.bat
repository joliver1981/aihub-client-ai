@echo off
:: Set the path to the Anaconda installation
SET "CONDA_PATH=C:\Users\james\miniconda3"

:: Initialize Conda for the command prompt
CALL "%CONDA_PATH%\Scripts\activate.bat"

:: Activate the conda environment
CALL conda activate aihub2.1

:: Navigate to the project folder
cd /d C:\src\aihub-client

:: Run Python app
python run_app.py

