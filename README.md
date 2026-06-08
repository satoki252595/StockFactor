# StockFactor — 日本株「半年で2倍」要素抽出・日次スクリーニング

全上場銘柄・約6年の履歴で **半年（≒126営業日）以内に株価が2倍以上** になった日本株を抽出し、その
「本質的要素」を **テクニカル / ファンダメンタルズ×ミクロ / マクロ** の観点で定量化。2倍株は
**モメンタム型（順張り）／反転型（逆張り）** の2モードに分かれるため2トラックで機械採点し、
**Claude スキル**が型を踏まえ定性統合して候補ウォッチリストを出す。

> **ランニングコスト 0 円** が絶対制約。データは yfinance（Yahoo Finance, 無料）と JPX 公開リスト、
> 計算は決定論 Python、LLM 評価は Claude スキル（既存サブスク内）、検証は GitHub Actions 無料枠。

## アーキテクチャ

```
JPX 上場一覧(無料) ──▶ data/universe.csv（コミット, ネット不要で再利用）
                                  │
yfinance(無料) OHLCV ──▶ 流動性フィルタ ──▶ 要素抽出(テクニカル/マクロ/ファンダ)
                                  │                    │
                                  ▼                    ▼
                          2倍株スクリーナ        スコアリング(充足要素数)
                          (実験・要素検証)              │
                                                       ▼
                                        output/daily_scores.csv + daily_report.md
                                                       │
                                                       ▼
                                    Claude スキル (.claude/skills/jp-doubling-screener)
                                    = 要素定義に沿って定性統合 → watchlist
```

## ディレクトリ

| パス | 役割 |
| --- | --- |
| `requirements.md` | 要件一覧（R1–R13, 優先度順）と仮説（H1–H4） |
| `src/stockfactor/` | コアライブラリ（データ取得・スクリーナ・要素・スコア・ユニバース） |
| `experiments/` | 仮説検証（2倍株抽出・要素の識別力 backtest）。Actions で実行 |
| `experiments/results/` | 実験の出力（doublers, factor_stats, report.md など） |
| `tests/test_screen.py` | 合成データ単体テスト（ネット不要） |
| `run_daily.py` | 日次バッチ本体（決定論パート） |
| `.claude/skills/` | Claude スキル（LLM 定性評価） |
| `.github/workflows/experiment.yml` | 無料の検証ハーネス |
| `docs/operations.md` | スケジュール運用・ネットワーク設定手順 |

## クイックスタート

```bash
pip install -r requirements.txt
python tests/test_screen.py                 # ロジック検証（ネット不要）

# 実データ（要ネットワーク: Full もしくは Custom で *.finance.yahoo.com 許可）
python -c "from src.stockfactor import universe as u; u.refresh()"   # ユニバース取得
python run_daily.py --limit 300             # 動作確認（300銘柄）
python run_daily.py                          # 全銘柄
```

## 仮説検証の流れ（重要）

本リポジトリは「2倍株には共通要素がある」という仮説をデータで検証してから採用する方針。
`experiments/run_experiment.py` が doubler 群 vs 対照群で各要素の識別力（AUC / 命中率 lift）を測り、
`experiments/results/report.md` に結果を出力する。**識別力が確認された要素だけ**を
`src/stockfactor/score.py` の採点ルールとして採用し、Claude スキルに反映する。

### 📌 検証で分かったこと（結論は [FINDINGS.md](FINDINGS.md)）
全銘柄（3,734）・正例1,233件の検証で、日本の半年2倍株は **2つのモードが拮抗** して存在すると判明:
- **モメンタム型（43%）**: 高値圏 × 上昇トレンド × パーフェクトオーダー × 市場アウトパフォーム（キオクシア型）。
- **反転型（57%）**: 高値から下落 × 押し目 × 出来高急増 × 直近は投げ（出遅れ・逆張り）。
- 両型の唯一の共通必須は **高ボラ（atr_pct≥0.04, AUC0.78）**。`score.py` は2トラックで採点し型を判定。

> 旧 v1（500サンプル）は「反転型のみ・モメンタム型は棄却」としていたが、これは標本バイアスだった。
> 2トラック設計の primary score は AUC=0.717、充足要素数は正例 median 6 vs 負例 4 と明確に分離する。

## 重要な制約・限界

- **過去の点別ファンダ（決算発表時点データ）は無料では検証不可**（実証済み）: yfinance の
  `quarterly_income_stmt` は直近5四半期しか返さず、過去6年に分布する doubler 点火点をカバーできない。
  ファンダは日次採点には `.info` で反映するが、過去の識別力は未測定（validated=False）。
  真の PIT 検証には **J-Quants API**（要認証）が必要。テクニカル・マクロは価格履歴のみで検証可能。
- 本システムは投資助言ではなく、要素該当度のスクリーニング支援。
