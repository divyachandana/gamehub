---
name: setup
description: Set up the ExecuTorch + QNN backend build environment natively on Windows-on-Snapdragon ARM64 (no cross-compile, no adb — this device is both host and target). Use before building or running anything QNN-related here.
---

# Setup (Windows-on-Snapdragon, native ARM64)

Full narrative and every error hit along the way: `dog_fooding_writeup/qnn-backend-windows-arm64.md` (§1-9).

## 1. Toolchain

```powershell
winget install Python.Python.3.12   # must resolve to the ARM64 build, not the x64 emulated one
winget install Kitware.CMake
winget install Microsoft.VisualStudio.2022.BuildTools --force --override "--add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Llvm.Clang --add Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset --add Microsoft.VisualStudio.Component.VC.Tools.ARM64"
```
`--force` matters — a plain `winget install` on an already-installed package no-ops instead of adding components.

Verify Python is genuinely ARM64-native (the Microsoft Store alias runs under x64 emulation and lies about `platform.machine()`):
```powershell
python -c "import sysconfig; print(sysconfig.get_platform())"   # must print win-arm64
```

## 2. Git symlinks

```powershell
git config --global core.symlinks true
```
Also enable Windows Developer Mode — on some builds the relevant registry key is `AllowDevelopmentWithoutDevLicense` (not `...DevMode`) under `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock`. If tracked symlinks became plain-text placeholder files before this was set, re-checkout them: `git checkout HEAD -- <paths>`.

## 3. One source patch (check if already applied: `git diff backends/qualcomm/CMakeLists.txt`)

`backends/qualcomm/CMakeLists.txt` gates the AOT pybind export module to x86_64 only:
```diff
- if(${CMAKE_SYSTEM_PROCESSOR} MATCHES "x86_64|AMD64")
+ if(${CMAKE_SYSTEM_PROCESSOR} MATCHES "x86_64|AMD64|ARM64|aarch64")
```
Skip this if you only need to *run* models exported elsewhere — it's only needed to export/compile `.pte` files on this device.

## 4. Build

```powershell
backends\qualcomm\scripts\build.ps1 -SkipX86Windows
```
Output: `build-arm64-windows\` — runtime, QNN backend DLL, `qnn_executor_runner.exe`, `qnn_llama_runner.exe`, `qnn_multimodal_runner.exe`, and (with the §3 patch) the AOT pybind module.

## Known gaps — don't try to fix, route around

| Blocks | Workaround |
|---|---|
| Full `pip install .` wheel (no ARM64 SIMD path in `extension/llm/custom_ops`; some deps hardcode `-A x64`) | Skip it. Run against the source tree: `$env:PYTHONPATH = "<repo's parent>;<repo>"`, plus manually `pip install py-cpuinfo ruamel.yaml flatbuffers tabulate pandas` |
| Local LLM export/compile (`oss_scripts/llama/__init__.py` eagerly imports every model converter → `pyarrow`, which has never shipped a `win_arm64` wheel) | Export `.pte` files on an x86_64 host; only run them here |
| `flatc` not found after skipping the wheel build | `$env:FLATC_EXECUTABLE = "<repo>\build-arm64-windows\third-party\flatc_ep\bin\flatc.exe"` |

Once built, see the `run-model` skill to actually execute a `.pte`.
