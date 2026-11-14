# smarthr-ui に寄せたスタイル

モックとプロトタイプは、実際のプロダクト画面に近く見えることが大事。見た目が実物と違うと、読み手は案の中身ではなく見た目に引っ張られる。

## 手順

### 1. リポジトリの既存 UI を探す

作業ディレクトリのリポジトリで、次を探す。

- `package.json` に `smarthr-ui` があるか、バージョンはいくつか
- 検討対象と似た画面 (一覧、詳細、フォーム、ダイアログ) のコンポーネント
- smarthr-ui をラップした独自コンポーネント (例: `components/` 配下の `XxxTable`、`XxxDialog`)
- テーマのカスタマイズ (`createTheme` の引数など。MAIN 色や余白の基準を変えていないか)

### 2. 見つかったらまねる

まねるのは次の4点。

- **レイアウト**: ページの骨格 (ヘッダー、見出し、操作ボタンの位置、余白)
- **コンポーネントの組み合わせ**: どの場面でどのコンポーネントを使っているか (例: 一覧の上に検索と絞り込み、行末に操作ボタン)
- **文言のトーン**: ボタンラベル、見出し、空状態のメッセージ。i18n ファイルから実際の文言を借りる
- **テーマの差分**: カスタマイズがあれば `assets/smarthr-ui-like.css` の `:root` のトークンを上書きする

実際の React コンポーネントをそのまま動かす必要はない。HTML/CSS で見た目と振る舞いを再現する。

### 3. なければ `assets/smarthr-ui-like.css` で組む

このファイルは smarthr-ui v103 のデザイントークンとコンポーネントのスタイルを、素の CSS に移したもの。クラスは `.sui-` で始まり、`.sui-frame` (モックの枠) の内側で使う。

## デザイントークン (smarthr-ui v103 の既定値)

### 色

| トークン | 値 | 用途 |
|---|---|---|
| TEXT_BLACK | `#23221e` | 本文 |
| TEXT_GREY | `#706d65` | 補足テキスト、プレースホルダー |
| TEXT_DISABLED | `#c1bdb7` | 無効状態 |
| TEXT_LINK | `#0071c1` | リンク |
| MAIN | `#0077c7` | プライマリボタン、選択状態、フォーカス |
| DANGER | `#e01e5a` | 破壊的操作、エラー |
| WARNING_YELLOW | `#ffcc17` | 警告 |
| green | `#0f7f85` | 完了などの肯定的ステータス |
| BRAND | `#00c4cc` | ブランドカラー (AppHeader など。UI 要素には使わない) |
| WHITE | `#fff` | 面 |
| BACKGROUND | `#f8f7f6` | ページ背景 |
| COLUMN | `#f8f7f6` | 区切られた領域の背景 |
| BASE_GREY | `#f5f4f3` | ホバー背景など |
| OVER_BACKGROUND | `#f2f1f0` | 背景の上に重ねる領域 |
| HEAD | `#edebe8` | テーブルヘッダー |
| BORDER | `#d6d3d0` | 罫線 |
| ACTION_BACKGROUND | `#d6d3d0` | 一括操作行の背景 |
| OVERLAY | `rgba(3,3,2,.15)` | 半透明の重ね |
| SCRIM | `rgba(3,3,2,.5)` | ダイアログの背景 |

ホバー色は元の色を 5% 暗くしたもの (`darken(0.05)`)。

### 文字サイズ (html 16px 基準)

| トークン | 値 | 用途 |
|---|---|---|
| XXS | 0.667rem (約11px) | ごく小さな注記 |
| XS | 0.75rem (12px) | 注記 |
| S | 0.857rem (約14px) | テーブルヘッダー、ステータスラベル、小ボタン |
| M | 1rem (16px) | 本文、ボタン、入力 |
| L | 1.2rem (約19px) | セクション見出し |
| XL | 1.5rem (24px) | 画面タイトル |
| XXL | 2rem (32px) | 大見出し |

行間: TIGHT 1.25 / NORMAL 1.5 / RELAXED 1.75。フォントは `system-ui, sans-serif`。

### 余白 (1 = 16px)

| トークン | 値 |
|---|---|
| X3S | 4px (0.25) |
| XXS | 8px (0.5) |
| XS | 16px (1) |
| S | 24px (1.5) |
| M | 32px (2) |
| L | 40px (2.5) |
| XL | 48px (3) |

### 角丸・影・罫線

- 角丸: s 4px / m 6px / l 8px / full
- 罫線: `1px solid #d6d3d0`
- 影: LAYER1 `0 1px 2px 0 rgba(3,3,2,.3)` / LAYER2 `0 2px 4px 1px rgba(3,3,2,.3)` / LAYER3 `0 4px 8px 2px rgba(3,3,2,.3)` / LAYER4 `0 8px 16px 4px rgba(3,3,2,.3)`
- フォーカス: `outline: 2px solid #0077c7; outline-offset: 2px; box-shadow: 0 0 0 2px #fff`
- 入力ホバー: `box-shadow: 0 0 0 2px rgba(0,119,199,.22)`

## コンポーネントの見た目

`assets/smarthr-ui-like.css` に実装済み。対応するクラスは次の通り。

| smarthr-ui | クラス | 要点 |
|---|---|---|
| Button | `.sui-btn` + `--primary` / `--danger` / `--text` / `--s` | 角丸 m、太字、M は上下12px 左右16px。secondary が既定 |
| Input | `.sui-input` | 角丸 m、上下12px 左右8px、エラーは枠が DANGER |
| Select | `.sui-select` | Input と同じ高さ、右に▼ |
| Textarea | `.sui-textarea` | |
| Checkbox / RadioButton | `.sui-check` / `.sui-radio` | 選択時 MAIN |
| FormControl | `.sui-field` > `.sui-field__label` / `.sui-field__help` / `.sui-field__error` | ラベル太字、必須は `.sui-required` |
| Base | `.sui-base` | 白、角丸 m、LAYER1 |
| Heading | `.sui-h-screen` / `.sui-h-section` / `.sui-h-block` / `.sui-h-sub` | XL / L / M太字 / S太字グレー |
| Table | `.sui-table` | ヘッダー HEAD 背景 S 太字、セル上下8px 左右16px |
| StatusLabel | `.sui-status` + `--blue` / `--green` / `--red` / `--warning` / `--grey` | 白地に色付きの枠と文字、S 太字 |
| Badge | `.sui-badge` | 丸、MAIN |
| TabBar | `.sui-tabs` > `.sui-tab[aria-selected=true]` | 選択タブの下に 4px の MAIN 線 |
| Dialog | `.sui-dialog-scrim` > `.sui-dialog` | 角丸 m、LAYER4、フッターに操作ボタン右寄せ |
| InformationPanel | `.sui-info` | LAYER3、角丸 l |
| NotificationBar | `.sui-notice` + `--info` / `--success` / `--warning` / `--error` | |
| ResponseMessage | `.sui-message--error` など | アイコン + 文言 |
| TextLink | `.sui-link` | TEXT_LINK |
| AppHeader | `.sui-appheader` | 白、下罫線。ロゴの代わりにプロダクト名テキストを置く |
| Stack / Cluster | `.sui-stack` / `.sui-cluster` | 縦積み / 横並び (gap 16px) |

## 注意

- 実在のプロダクト画面と見分けがつかないものを作るのが目的ではない。社内の検討用であることがわかるように、アーティファクトの各モックに「モック」と表示する (テンプレートの `.mock-label`)
- SmartHR のロゴや商標画像は埋め込まない
- smarthr-ui にないコンポーネントが必要になったら、既存コンポーネントの組み合わせで表現できないかを先に考える。どうしても必要なら、その旨を案の「未解決の論点」に書く
