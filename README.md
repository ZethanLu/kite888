# STOCK WEB

GitHub Actions 每個交易日台北時間 15:10 抓資料存成 `data/data.json`,網頁(`index.html`)只讀這個 JSON。

## 設定步驟
1. 把整個資料夾(含隱藏的 `.github`)上傳到新的 GitHub repo。
2. Settings → Pages:Source 選 Deploy from a branch,Branch 選 `main` / `(root)`。
3. Settings → Actions → General → Workflow permissions 選 Read and write permissions。
4. Actions → update-data → Run workflow,手動跑第一次(repo 內附的 `data/data.json` 是範例資料,跑完會被取代)。
5. 之後只要編輯 `holdings.json`:`holdings` 是持股(成本、股數、買進原因、停損停利),`watchlist` 是只想搜尋或觀察的個股。

## 注意
- 搜尋範圍是 `holdings` 與 `watchlist`;想搜尋新個股,把代號加進 `watchlist`(台股上市 `.TW`、上櫃 `.TWO`)。
- 排程沒觸發時,到 Actions 手動 Run workflow;排程只會在預設分支執行,repo 長期沒動作時 GitHub 可能停用排程。
- 本機預覽:在資料夾執行 `python -m http.server`,再開 http://localhost:8000(直接雙擊 index.html 讀不到 JSON)。
- 資料來源為 Yahoo Finance 免費資料,可能延遲或偶爾失敗,失敗的個股會保留上一次的資料。

## 在網頁上編輯持股(寫回 GitHub)
1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token。
2. Repository access 選 Only select repositories,只勾這個 repo;Permissions 把 Contents 設為 Read and write;Actions 也設為 Read and write(新增個股後才會自動觸發資料更新,不給也能用)。到期日自己決定。
3. 打開網頁,總覽頁最下方「GitHub 連線」填帳號、repo、分支與 token,按「儲存設定」再按「測試連線」。
4. 之後在個股頁可新增、修改、移除持股,在持股頁可新增觀察個股;每次儲存都會在 repo 留下一筆 commit,改錯可以到 GitHub 還原。

token 只存在該瀏覽器的 localStorage,請只在自己的裝置上使用,不要在公用電腦設定。

## 策略模組(依你提供的規格書)
每日排程除了抓行情,也會用 5 年日 K 計算:
- **大盤風度**:加權、櫃買(及 S&P 500、那斯達克)判定為強風・易漲、無風・易跌、亂流・震盪,並顯示當下可用的策略。
- **進場掃描**:對 holdings 與 watchlist 逐檔檢查 A 週拉回風箏、B 廉價收購、C 週趨勢風箏、D 強勢週風箏的每個條件,個股頁可展開查看哪些條件符合。
- **出場評估**:holdings 的每一檔可填 `strategy`(A/B/C/D)與 `entry_date`(YYYY-MM-DD)。成本取 `cost`;最高損益從進場日起算;損益達 +10% 進入放風箏階段,否則為防守階段,並依策略列出觸發的出場條件。
- **營收盾牌**:每月營收年增率來自證交所/櫃買公開資料(盡力抓取)。「創歷史新高」(LEVEL_1)沒有免費歷史資料,請在該檔加上 `"rev_high": true`;也可用 `"rev_yoy": 數字` 手動覆蓋。

holdings.json 範例:
`{"sym":"3443.TW","name":"創意","shares":1000,"cost":7050,"stop":6739,"tp":8200,"strategy":"A","entry_date":"2026-10-27","why":"...","rule":"..."}`

### 目前沒做的部分
- 盤中規則:盤中急跌 5%、12:00 / 13:00 後分批、13:25 撮合、尾盤貼近 5 日線且收黑。排程只用盤後日 K,網頁只會顯示「收盤貼近 5 日線」作為參考。
- 全市場掃描與 TOP_10 / TOP_30 成交值排行:目前只掃 holdings 與 watchlist。
- 營收 80% / 金流 20%(老闆型)、營收 60% / 金流 40%(上班族型)的加權評分。
- 週五判斷以資料最後一根為週五為準,週五休市的那一週不會觸發週五規則。週/月 OSC 使用進行中的週、月 K,與規格書偽碼的算法一致。
- 美股略過「成值 ≥ 1 億」門檻(原規格以新台幣為單位)。
