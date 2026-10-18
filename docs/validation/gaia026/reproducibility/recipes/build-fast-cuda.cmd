@echo off
setlocal
set "RESEARCH=C:\Users\timch\Documents\GitHub\Cloth_neXT\.hydro-test-tmp\reproducibility-20261017"
set "SRC_DIR=%RESEARCH%\fast-source"
set "DEPS=C:\Users\timch\Documents\GitHub\ppf-contact-solver-hydrodynamics\.tools"
set "BUILD_WIN=%SRC_DIR%\build-win-native"
set "CPP_DIR=%SRC_DIR%\crates\ppf-cts-compute\cuda"
set "KERNEL_DIR=%SRC_DIR%\crates\ppf-cts-solver\src\kernels"
set "KERNELGEN=%SRC_DIR%\crates\ppf-cts-compute\seam\kernelgen.py"
set "OUT_DIR=%RESEARCH%\fast-cuda-build"
set "KERNELGEN_DIR=%OUT_DIR%\kernelgen"
set "LIB_DIR=%OUT_DIR%\lib"
set "EIGEN_STEM=eigen-3.4.0"
set "CUDA_PATH=%DEPS%\cuda-12.8"
set "PYTHON_EXE=C:\Users\timch\AppData\Local\Programs\Python\Python311\python.exe"
set "PPF_WIN_BUILD_JOBS=3"
set "PATH=%CUDA_PATH%\bin;C:\Users\timch\AppData\Local\Programs\Python\Python311;%PATH%"
call C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat >nul
call "%BUILD_WIN%\scripts\build-cuda.bat"
exit /b %ERRORLEVEL%

