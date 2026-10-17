@echo off
setlocal
set "SRC_DIR=C:\Users\timch\Documents\GitHub\Cloth_neXT\.hydro-test-tmp\reproducibility-20261017\native-source"
set "NATIVE_DEPS=C:\Users\timch\Documents\GitHub\ppf-contact-solver-hydrodynamics\.tools"
set "CARGO_HOME=%NATIVE_DEPS%\cargo"
set "RUSTUP_HOME=%NATIVE_DEPS%\rustup"
set "CARGO_TARGET_DIR=%SRC_DIR%\target\research-cuda"
set "CUDA_PATH=%NATIVE_DEPS%\cuda-12.8"
set "PYTHONHOME=C:\Users\timch\AppData\Local\Programs\Python\Python311"
set "PATH=C:\Users\timch\Documents\GitHub\Cloth_neXT\.hydro-test-tmp\reproducibility-20261017\python-shim;%CARGO_HOME%\bin;%CUDA_PATH%\bin;%SRC_DIR%\crates\ppf-cts-compute\cuda\build\lib;%PATH%"
call C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat >nul
cd /d "%SRC_DIR%"
cargo build --offline --release -p ppf-cts-solver --features cuda --no-default-features
exit /b %ERRORLEVEL%
