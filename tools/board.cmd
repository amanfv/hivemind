@echo off
rem Windows launcher: runs tools/board (a bash script) with Git Bash, so it works from PowerShell and cmd.
setlocal
for %%G in (git.exe) do set "GITDIR=%%~dp$PATH:G"
set "GITBASH=%GITDIR%..\bin\bash.exe"
if not exist "%GITBASH%" (
  echo board: Git Bash not found. Install Git for Windows: https://git-scm.com/download/win 1>&2
  exit /b 1
)
"%GITBASH%" "%~dp0board" %*
