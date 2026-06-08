# 運用ガイド（0円・日次バッチ）

本システムを **Claude Code Web のスケジュール（routine）** で日次運用するための手順。
すべて無料の範囲で完結する。

## 全体の流れ（日次）

1. Claude Code Web の routine が毎営業日（例: 平日 18:00 JST）に起動。
2. スキル `jp-doubling-screener` が発火 → `python run_daily.py` を実行（決定論パート）。
   - JPX ユニバース（`data/universe.csv`）→ yfinance で OHLCV 取得 → 流動性フィルタ
     → テクニカル/マクロ/ファンダ要素を採点 → `output/daily_scores.csv` と
     `output/daily_report.md` を生成。
3. Claude（スキルの指示）が採点結果を読み、要素定義に沿って定性統合（＝「LLM 評価」）。
   最終ウォッチリストと根拠を `output/watchlist_<date>.md` に出力。
4. 結果をブランチにコミット（任意）。

## 必須設定: ネットワークアクセス

yfinance / JPX を使うため、routine が使う環境のネットワークを既定の **Trusted** から
変更する必要がある（Trusted では金融ホストが遮断される）。

- 推奨: **Custom** を選び、**Allowed domains** に以下を追加。
  「Also include default list of common package managers」も**チェック**（pip を使うため）。

  ```text
  query1.finance.yahoo.com
  query2.finance.yahoo.com
  *.finance.yahoo.com
  fc.yahoo.com
  www.jpx.co.jp
  ```

- 簡便にするなら **Full**（全ドメイン許可）でも可。

設定場所: クラウド環境の編集ダイアログ → **Network access** セレクタ。
（参考: https://code.claude.com/docs/en/claude-code-on-the-web#network-access ）

### 具体的なクリック手順（ブラウザが確実。デスクトップアプリ単体の画面には出ない）
1. ブラウザで **https://claude.ai/code/routines** を開く
2. ルーティンを作成（**New routine**）または既存を開いて **鉛筆アイコン（Edit routine）**
3. **Instructions 入力欄の下**にある、環境名が表示された**クラウドアイコン**（例: **Default**）をクリック
4. 一覧で対象環境に**マウスを乗せると右側に出る歯車（設定）アイコン**をクリック
5. **「Update cloud environment」**ダイアログで **Network access** を設定:
   - 手軽に動かすなら **Full**（全許可、これ1つでOK）
   - 絞るなら **Custom** → **Allowed domains** に上記ドメインを貼り付け、
     **「Also include default list of common package managers」にチェック**（pip用）
6. **Save changes**（次回実行から有効）

> 補足: このクラウドアイコン→歯車は「ルーティン編集時」または「クラウドセッション開始時」にのみ表示される。
> デスクトップでもよいが **Routines → New routine → 「Remote」** を選ぶと同じ環境アイコンが現れる（Localは不可）。
> ブロック時はログに `403 host_not_allowed` が出る → ドメイン追加か `Full` に変更。

## スケジュール（routine）の設定

1. Claude Code Web で本リポジトリ・ブランチを対象に routine を作成。
2. 上記ネットワーク設定の環境を割り当てる。
3. 実行間隔を「平日日次（市場引け後）」に設定。
4. 起動プロンプト例:

   ```
   /jp-doubling-screener を実行して、本日のウォッチリストを作成しコミットして。
   ```

   （スキルが `run_daily.py` 実行→定性評価→`output/watchlist_<date>.md` 出力まで行う）

## セットアップ（初回のみ）

```bash
pip install -r requirements.txt
# ユニバースを最新化（月1回程度でよい。要ネットワーク）
python -c "from src.stockfactor import universe as u; print(len(u.refresh()))"
git add data/universe.csv && git commit -m "chore: refresh universe"
```

## コスト

| 項目 | コスト |
| --- | --- |
| 株価データ（yfinance / Yahoo Finance） | 無料 |
| ユニバース（JPX 公開 xls） | 無料 |
| 決定論計算（Python） | 無料（routine 環境内） |
| LLM 定性評価（Claude スキル） | 既存サブスク内（追加課金なし） |
| 検証ハーネス（GitHub Actions） | 無料枠／パブリックリポは無制限 |

合計ランニングコスト: **0 円**。

## 代替: GitHub Actions で日次実行（スケジュールを Claude に依存したくない場合）

`.github/workflows/experiment.yml` と同様に `schedule:` トリガーの daily ワークフローを作れば、
データ取得＋決定論採点を Actions（フルネット・無料）で回し、結果をリポジトリにコミットできる。
その場合、定性評価のみ Claude スキルで後追い実行する構成も可能。
