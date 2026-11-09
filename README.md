# 🀄 Mortal 麻雀AI トレーニングシミュレーター (MahjongTrainer)

麻雀AI「Mortal」および独自のカスタム教師AI（7大メタ戦略探索型・スピード特化型・ロマン特化型）を搭載した、四人麻雀（「一般的なネット麻雀の競技ルール（赤3枚・クイタンあり・東南戦））の実戦形式トレーニング＆リアルタイムコーチングシミュレーターです。

1打ごとのAI評価採点・5軸の日本語比較解説・悪手ストップ（再選択）機能・危険牌レーダー・リアルタイム符計算・7種類のテーマ別配牌特訓機能を備え、「なぜその牌を切るのか」を対局しながら直感的に学ぶことができます。

---

## ✨ 主な機能・特徴

### 1. 4種類の「教師AIコーチ」リアルタイム切り替え
プレイスタイルや学びたい課題に合わせて、指導してくれる教師AIをいつでも切り替えられます。
* **🎓 標準型 (`mortal`)**: Mortalのニューラルネットワーク（ResNet + DQN）が出力する局収支期待値（Q値）をそのまま用いる標準バランス型コーチ。
* **🔍 タイプA：探索型 (`search`)**: GPU並列山読みロールアウトに加え、**「7大メタ戦略（①対AIトラップ待ち、②中盤の先切りスリム化、③親番連荘＆親流し、④カベ読み、⑤他家染め手絞り、⑥終盤ケイテン奪取、⑦南場・着順条件逆算）」** を統合した実戦戦術コーチ（高速／標準プリセット対応）。
* **🌟 タイプB：ロマン型 (`roman`)**: 役満・清一色・混一色・対子手・三色・ドラ死守など、高打点・手役ルートを最優先で指導する高打点特化コーチ（実戦／夢追い／狂気プリセット対応）。
* **⚡ タイプC：スピード型 (`speed`)**: 受け入れ枚数の最大化・シャンテン戻し完全禁止・喰いタン／役牌の積極仕掛けで最速アガリを指導するスピード特化コーチ（純粋牌効率／高速仕掛け／超音速プリセット対応）。

### 2. 7つの「テーマ別配牌特訓モード」
実戦では数十局に1回しか来ない特定の状況や手役を、ワンクリックで何度でも引き直して反復練習できます。
* **通常対局**（ランダム配牌）
* **混一色・清一色**（染め手特訓）
* **七対子・対々和**（対子手・鳴き分岐特訓）
* **断幺九・平和**（好形・複合ターツ捌き特訓）
* **役牌・速度仕掛け**（鳴き判断特訓）
* **ドラ3以上**（高打点・押し引き特訓）
* **国士・チャンタ・混老頭**（端牌・特殊手特訓）

### 3. 視覚的アシスト ＆ 悪手ストップ（再選択）機能
* **🔄 再選択（待った）モード**: 基準外の悪手を選んだ瞬間に進行をストップし、金色に光る合格候補から選び直せる機能（採点基準は「標準／厳格／1位のみ許容」から選択可能）。
* **📐 シャンテン ＆ 🀄 有効牌ナビ**: 手牌の各牌の下に「切った後の有効牌枚数・聴牌・フリテン・シャンテン戻し」を表示し、マウスホバーで具体的な待ち牌と山残り枚数を即座にプレビュー。
* **🧮 リアルタイム点数・符・飜計算**: 聴牌時は待ち牌ごとのロン／ツモ打点と符の内訳を、1シャンテン以上でも本線ルートとリーチルートの想定打点を常時計算。
* **🛡️ 危険牌レーダー**: 他家リーチや副露に対して、現物・完全安牌・字牌・中筋／壁（ノーチャンス）・ワンチャンス・片筋・無筋高危険をバッジと理由テキストで可視化。
* **💬 生成AI（ChatGPT / Gemini等）質問用プロンプト自動生成**: 現在の局面や、1局の中でAI第1位以外を選んだ全場面を振り返りリスト化し、ワンクリックで質問用プロンプトをコピー可能。

---

<!-- ... existing code ... -->
## 💻 動作環境

* **OS**: Windows 10 / 11 (64bit)
* **Python**: Python 3.10 〜 3.13 (64bit)
* **GPU (推奨)**: NVIDIA GeForce RTX / GTX シリーズ（CUDA 12.4 対応環境）
  * ※GPU非搭載のPCでも自動的にCPUモードへフォールバックして動作します。
* **ブラウザ**: Microsoft Edge または Google Chrome（起動時に専用アプリウィンドウモードで自動起動します）

---

## 🛠️ 環境構築手順 (Windows / PowerShell)

ソースコードから環境を構築して実行する場合は、以下の手順に従ってください。

### 1. 事前準備（必須ツールのインストール）
以下の3つのツールがインストールされていることを確認してください。
1. **Python (64bit)**: インストール時に必ず **「Add Python to PATH」** にチェックを入れてください。
2. **[Git for Windows](https://git-scm.com/download/win)**: リポジトリおよびMortalの取得に使用します。
3. **[Rust ツールチェーン (rustup)](https://rustup.rs/) ＆ Visual Studio C++ Build Tools**: `Mortal/libriichi`（Rust製麻雀ライブラリ）をビルドする際に必要です。

### 2. リポジトリのクローンと作業ディレクトリへの移動
PowerShellを開き、任意の作業フォルダへ移動して本リポジトリをクローンします。

```powershell
git clone https://github.com/demianq-cyber/mahjong-trainer.git
cd mahjong-trainer
```

### 3. 仮想環境 (venv) の作成
プロジェクト専用のPython仮想環境を作成します。

```powershell
python -m venv venv
```

### 4. 仮想環境の有効化
作成した仮想環境をアクティベートします。

```powershell
.\venv\Scripts\Activate.ps1
```
> **💡 エラーが出る場合（スクリプト実行ポリシーの制限）**:
> 初回のみ以下のコマンドを実行して許可してから、再度 `.\venv\Scripts\Activate.ps1` を実行してください。
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### 5. 依存パッケージのインストール
PyTorchの公式ホイールリポジトリ（CUDA 12.4）を指定して、依存パッケージをインストールします。

```powershell
# pipを最新化
python -m pip install --upgrade pip

# 方法A：アプリ実行に必要な最小パッケージのみを素早くインストールする場合（推奨）
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install Flask numpy riichienv maturin

# 方法B：開発・チューニング用も含め requirements.txt 全パッケージをインストールする場合
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
```
*(※GPU非搭載のPCでCPUのみで動かす場合は、`--index-url https://download.pytorch.org/whl/cu124` を省略して `pip install torch` を実行してください)*

---

### 6. Mortal（AIエンジン・libriichi・学習済みモデル）のダウンロードとビルド
本アプリは麻雀AI「Mortal」の推論スクリプト（`model.py` / `engine.py`）、Rust製ライブラリ（`libriichi`）、および学習済みモデル重みファイル（`.pth`）を使用します。以下の手順で配置・ビルドしてください。

#### 6-1. Mortal ソースコードの取得
プロジェクトのルート直下（`web_app.py` と同じ階層）に [Equim-chan/Mortal](https://github.com/Equim-chan/Mortal) をクローンします。

```powershell
git clone https://github.com/Equim-chan/Mortal.git
```

#### 6-2. `libriichi` のビルドとインストール
クローンした `Mortal/libriichi` ディレクトリへ移動し、`Cargo.toml` のライブラリ名を `libriichi` に合わせた上で、`maturin` でビルド＆インストールします。

```powershell
# Python 3.13 等の最新環境で PyO3 ビルドを通すための環境変数を設定
$env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY = "1"

# libriichi ディレクトリへ移動
cd Mortal\libriichi

# Cargo.toml の [lib] name = "riichi" を "libriichi" に修正
(Get-Content Cargo.toml) -replace 'name = "riichi"', 'name = "libriichi"' | Set-Content Cargo.toml

# Rust から Python ホイール (.whl) をビルドしてインストール
maturin build --release
pip install --force-reinstall (Get-ChildItem -Recurse -Filter "libriichi-*.whl" .. | Select-Object -Last 1).FullName

# プロジェクトのルートディレクトリに戻る
cd ..\..
```
> **💡 確認コマンド**:
> `python -c "import libriichi; print('libriichi OK!')"` を実行して `libriichi OK!` と表示されれば成功です。

#### 6-3. 学習済みモデル重みファイル（`.pth`）の配置
1. プロジェクトルートに `models` フォルダが存在しない場合は作成します。
   ```powershell
   mkdir -Force models
   ```
2. Mortal用の学習済みモデルファイル（4人麻雀用 `.pth` ファイル）を用意し、ファイル名を **`mortal_298k.pth`** として `models/` フォルダ内に配置してください。
   * 配置パス: `models/mortal_298k.pth`
   * *(※別ファイル名の `.pth` モデルを使用する場合は、`mortal_298k.pth` にリネームするか、`ai_evaluator.py` 内の `weight_path` のデフォルト値を変更してください)*
   * ※Download: [Hugging Face（mortal-298k）](https://huggingface.co/VoidShine/mortal-298k)
   * ※ご利用の際は、mortalの規約をご確認の上、自己責任でお願いいたします。

---

## 📁 ディレクトリ構成
<!-- ... existing code ... -->


## 📁 ディレクトリ構成

環境構築完了後、以下のようなフォルダ構成になっていれば準備完了です。

```text
mahjong-trainer/
├── venv/                      # Python仮想環境
├── Mortal/                    # Step 6-1 でクローンした Mortal リポジトリ
│   └── mortal/
│       ├── engine.py
│       └── model.py
├── models/
│   └── mortal_298k.pth        # Step 6-2 で配置した学習済み重みファイル
├── teachers/                  # 教師AIモジュール群
│   ├── __init__.py
│   ├── base_teacher.py
│   ├── mortal_teacher.py      # 標準型 (Mortal純粋評価)
│   ├── search_teacher.py      # タイプA：探索型 (7大メタ戦略＋GPU山読み)
│   ├── search_params.json     # 探索型AI用チューニング済みパラメータ
│   ├── roman_teacher.py       # タイプB：ロマン型 (高打点・手役特化)
│   └── speed_teacher.py       # タイプC：スピード型 (即効・牌効率特化)
├── ai_evaluator.py            # Mortal推論・デバイス管理モジュール
├── web_app.py                 # メインアプリケーション (Flaskサーバー)
├── index.html                 # フロントエンドUI
├── requirements.txt           # Python依存パッケージ一覧
├── COPYING.txt                # GNU AGPL v3.0 ライセンス条文全文
├── LICENSE_NOTICE.txt         # 本アプリの著作権・ライセンス案内
└── THIRD_PARTY_LICENSES.txt   # サードパーティ製ライブラリのライセンス一覧
```

---

## 🚀 アプリの起動方法

仮想環境（`venv`）が有効な状態で以下のコマンドを実行すると、ローカルサーバーが起動し、自動的に専用ウィンドウ（アプリモード）が立ち上がります。

```powershell
python web_app.py
```

* 画面右上の「✖」ボタンでウィンドウを閉じると、バックグラウンドのPythonサーバーも自動的に終了します。
* もしウィンドウが自動で開かない場合は、ブラウザで `http://127.0.0.1:5000` にアクセスしてください。

---

## 📦 スタンドアロン実行ファイル（.exe）のビルド方法（任意）

PyInstallerを使用して、Python環境のないPCでも起動できる配布用実行ファイル（`dist/MahjongTrainer/MahjongTrainer.exe`）をビルドする場合は、以下を実行してください。

```powershell
pyinstaller --clean -y MahjongTrainer_Release.spec
```

---

## 📜 ライセンスおよび著作権について

本リポジトリには、ライセンスおよび権利関係に関する以下のファイルを同梱しています。詳細は各ファイルをご確認ください。

1. **`LICENSE_NOTICE.txt`**: 本アプリ全体のライセンス案内および利用オープンソースソフトウェアの表記
2. **`COPYING.txt`**: GNU Affero General Public License Version 3 (AGPL-3.0) 条文全文
3. **`THIRD_PARTY_LICENSES.txt`**: 同梱・利用しているサードパーティ製Pythonライブラリのライセンス全文一覧

### ライセンス概要
* 本ソフトウェアのプログラムコードは、[Equim-chan/Mortal](https://github.com/Equim-chan/Mortal)（`libriichi`）を利用しているため、**GNU Affero General Public License v3.0 (AGPL-3.0-or-later)** の下で公開・頒布されています。
* ※なお、**学習済みAIモデルデータは本リポジトリには同梱されていません。** 利用に必要なモデルの入手方法および権利関係については、[環境構築手順 / LICENSE_NOTICE.txt] をご確認ください。
