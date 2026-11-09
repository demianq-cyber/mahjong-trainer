# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# riichienv と libriichi のバイナリ・内部データを収集
riichienv_datas, riichienv_binaries, riichienv_hidden = collect_all("riichienv")
libriichi_datas, libriichi_binaries, libriichi_hidden = collect_all("libriichi")

# アプリ実行に必要な最小限のデータのみ同梱（外部にソースを出さないため内部に格納）
added_datas = [
    ("index.html", "."),
    ("models/mortal_298k.pth", "models"),
    ("teachers/search_params.json", "teachers"),
    ("Mortal/mortal/model.py", "Mortal/mortal"),
    ("Mortal/mortal/engine.py", "Mortal/mortal"),
] + riichienv_datas + libriichi_datas

added_binaries = riichienv_binaries + libriichi_binaries

hidden_imports = [
    "teachers",
    "teachers.base_teacher",
    "teachers.mortal_teacher",
    "teachers.search_teacher",
    "teachers.roman_teacher",
    "teachers.speed_teacher",
    "model",
    "engine",
] + riichienv_hidden + libriichi_hidden

# パラメータ探索・ベンチマーク・デバッグ関連のコードとライブラリを完全除外
excluded_modules = [
    "optuna",
    "optuna_tuner",
    "tune_search_params",
    "arena_benchmark",
    "debug_ui_tester",
    "sqlite3",
    "matplotlib",
    "pandas",
    "scipy",
    "tkinter",
    "pytest",
]

a = Analysis(
    ["web_app.py"],
    pathex=[".", "./Mortal/mortal"],
    binaries=added_binaries,
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# ▼ 追加：Mortal推論＆GPU探索では一切使わない巨大CUDA DLL・開発用.libをビルドから除外
UNUSED_BIN_PREFIXES = (
    "cufft",        # フーリエ変換 (約250MB・不要)
    "cusparse",     # 疎行列計算 (約250MB・不要)
    "cusolver",     # 行列ソルバー (約200MB・不要)
    "curand",       # CUDA乱数ライブラリ (PyTorch独自のATen乱数を使うため不要)
    "cudnn_adv",    # cuDNN RNN/高度学習用エンジン (推論のみのため不要)
    "cudnn_ops_train",
    "cudnn_cnn_train",
)

# Windowsの起動チェック(WinError 126)に引っかからない巨大ファイルのみを安全に除外
SAFE_EXCLUDE_DLLS = (
    "cudnn_engines_precompiled",  # 約520MB (cuDNN無効化により不要)
    "cudnn_engines_runtime",      # 約50MB
    "cudnn_adv",                  # 約160MB (RNN/LSTM用・不要)
    "cudnn_heuristic",            # 約50MB
)

a.binaries = [
    (name, path, typecode)
    for (name, path, typecode) in a.binaries
    if not any(os.path.basename(name).lower().startswith(p) for p in SAFE_EXCLUDE_DLLS)
    and not name.lower().endswith((".lib", ".pdb", ".a"))
]

a.datas = [
    (name, path, typecode)
    for (name, path, typecode) in a.datas
    if "torch\\include\\" not in path.lower()
    and "torch/include/" not in path.lower()
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MahjongTrainer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # ★Falseにすることで起動時の黒いプロンプト画面を完全非表示化
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MahjongTrainer",
)