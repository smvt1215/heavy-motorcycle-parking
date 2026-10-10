# 普重／大重與雙北整合驗收

驗收日期：2026-10-10。現有里程碑 Issues #1–#10 已 CLOSED；本次使用三個獨立分支與 PR，沒有自行合併。

| Issue | 分支 | PR／審查順序 |
| --- | --- | --- |
| [#23 車種契約](https://github.com/smvt1215/heavy-motorcycle-parking/issues/23) | issue-23-vehicle-classes | [#24](https://github.com/smvt1215/heavy-motorcycle-parking/pull/24) → main |
| [#25 政策與資料](https://github.com/smvt1215/heavy-motorcycle-parking/issues/25) | issue-25-twin-city-policy | [#26](https://github.com/smvt1215/heavy-motorcycle-parking/pull/26) → #24 分支 |
| [#27 整合驗收](https://github.com/smvt1215/heavy-motorcycle-parking/issues/27) | issue-27-integration-acceptance | [#28](https://github.com/smvt1215/heavy-motorcycle-parking/pull/28) → #26 分支 |

這些 PR 採堆疊方式，審查可比對各自基底。合併前須重新指定後續 PR 的基底並確認 CI；本次不執行合併、部署或正式登入設定。

## 自動驗收結果

| 檢查 | 結果與證據範圍 |
| --- | --- |
| Backend ruff check／format | 通過 |
| Backend pytest | 1267 passed；含 PostGIS／Redis、migration upgrade/downgrade、舊資料保留、API及匯入測試 |
| Flutter analyze | No issues found |
| Flutter test | 225 passed；涵蓋 iOS／Android widget variant、深淺色、2倍文字、語意、登入／收藏／回報及導航 URI |
| Docker build | heavy-parking-api:issue27 成功；在 /tmp 匯入 API、CLI、政策 JSON 與來源 metadata 成功 |
| Compose config | 使用 .env.example 驗證通過 |
| Android／iOS 建置 CI | #24、#26 已通過；整合驗收 PR 以其最新 CI 結果為準 |

既有 SQLAlchemy DISTINCT ON 呼叫產生94次 deprecation warning；測試通過，未改動查詢語義或排名公式。裝置偵測僅找到 Linux desktop，沒有可用 iOS／Android 實機。

## 整合行為

- `/api/v1` 的附近、詳情、費率、即時、偏好及手機只用 NORMAL_HEAVY／LARGE_HEAVY。大重包含黃牌／紅牌；無偏好預設大重；車種每次明確傳入。舊公開車種值422，舊／錯誤／車種不符游標明確拒絕。
- 雙北樣本從 adapter → writer → PostGIS → HTTP API 重播。詳情、費率、即時的共用區域欄位及 evaluation_at 相同；游標延續固定時間，sort_version=1。
- 新北四維公園汽車區取得政策許可，普通機車區大重仍 UNKNOWN；遠東百貨等未核對私營場站不取得公有政策。地址／分類／場站消失會撤回政策，重新出現不補回撤回期間；其他來源不受影響。
- AVAILABLECAR 樣本44只在原汽車共享區，沒有大重專用複製數字。機車區不能借用汽車空位。未知 total 及不完整涵蓋使整體數字為 NULL，available_only 仍按有效 ALLOWED 區的已確認新鮮正數空位判斷。
- 小型車費率不搬到大重；新北路邊30元／4小時計次不產生7.5元／小時比較值。不確定費率不通過價格篩選。未知路邊狀態代碼保持 UNKNOWN／NULL。
- Bearer 開發登入的偏好不取代明確查詢車種；收藏／回報使用 token 身分。PENDING 回報不覆蓋官方許可。

API驗收的 clock／fetched_at 固定為2026-10-10T02:00:00Z，以驗證時間與車種契約；它是合成測試時間，不是這些舊樣本的真實即時狀態。正式 CLI 重播需提供檔案實際接收時間，不能用現在時間讓舊資料變新鮮。臺北 fixtures 故意保留3筆無效 static 與3筆無效 realtime 案例，驗收確認它們保留原始資料並逐筆拒絕。

## 本次整合修正

1. 政策發布日期與範圍快照擷取時間分開保留；不把範圍 feed 時間當作公告更新／接收時間。沒有確切公告時間時為 NULL。每一個 provenance 加入可為 NULL 的 source_name／source_url；規則、費率、即時、入口及完整總計各自保留來源。手機以可讀名稱呈現並開啟有效 HTTPS 原文；連結失敗保留詳情與提示，不推導來源 URL。
2. 詳情停車區採用詳情回應並遵循查詢車格分類，避免繼續顯示附近查詢的舊區域事實。標頭保留查詢區域的相容性，不採用範圍外的 ALLOWED 區；普通機車區篩選隨所選車種。切換車種保留相容車格篩選，只有普重的 LIGHT_MOTO_ONLY 切到大重時清除。整體空位標明為查詢時結果，不自行重算區域數字。
3. 匯入批次時鐘倒退不再違反 finished_at >= started_at。保留觀測到的倒退時間及 BATCH_CLOCK_ROLLBACK，批次完成時間保守限制到起始時間；來源時間、政策評估及空位 freshness 不改寫。
4. 個別政策許可與費率不早於首次範圍快照；新北公有場站另受2025-05-12名冊日期限制。2025-03-21公告時間獨立保存，不能據此猜更早的場站分類、轄管身份及時段；重播會收緊所有歷史版本的窗口，包括已撤回或對應失敗場站，保留原規則 ID、區間與證據。
5. 路邊 pay／paycash 衝突時，原文保留且價格不可比較；過長展示名稱縮短路名，保留完整地址、分類與格號。三個 PR 的七項審查建議均有程式／文件修正及相應回歸驗證。

## PR 審查修正

| PR | 建議 | 修正與驗證 |
| --- | --- | --- |
| #24 | 新北 UNKNOWN 描述混淆普重／大重 | 文件明列已確認一般機車區可供普重，UNKNOWN 限未確認大重區 |
| #24 | 切換車種清除所有車格篩選 | 三種共用分類雙向保留；僅 LIGHT_MOTO_ONLY 切到大重清除，四個 controller 回歸案例 |
| #26 | 首次規則／費率回推至政策日 | 起日受首次 scope 快照限制；後續相同匯入不重設，API 檢查起日前後與再次匯入 |
| #26 | 收費方式與金額原文衝突 | 五種衝突／未知組合保持 PARTIALLY_PARSED、比較金額 NULL，原文及模式保留 |
| #26 | 展示名稱可能超過200字 | 省略過長路名，完整地址、分類、格號與來源 ID 保留，最大長度案例通過 |
| #28 | 詳情標頭借用範圍外可停區 | 標頭保留查詢 rollup；詳情遵循車格分類，UNKNOWN 搜尋區＋範圍外 ALLOWED 區 widget 回歸通過 |
| #28 | 已結束版本仍保留過早許可 | 正常、對應失敗、汽車區消失、場站消失四種重播都修正所有歷史窗口；不支持的窗口保留證據且停用／不可確認 |

#24 手機209項、#26後端1259項、#28完整後端1267項與手機225項在各自分支通過。三個 PR 的最新提交仍須依 GitHub checks 確認雙平台建置；不自行合併。

## 真實來源抽查與資料待核對

已檢查官方公告及71處轄管名冊，並重新比對新北路外兩頁1384筆、1374個唯一 ID。55個場站的 ID／名稱／行政區／地址精確符合 manifest，16處未匹配。抽查市民廣場010013、四維公園010152、永和仁愛公園040001。

路邊首頁1000筆類型：899汽車、36汽車身障專用、30家長接送、12時段禁停、21裝卸貨、2大型車。這次抽查沒有證明一般收費機車格的實際涵蓋；前三筆汽車格保留為真實 fixture，機車政策案例明確標記為合成。

以下資料項目仍待核對；完成前保持 UNKNOWN／NULL：

- 臺北路邊 XML 車種、收費一般機車格範圍、費率單位及狀態字典。政策規則和日期已實作，但不能對未核對車格套用。
- 新北路邊一般收費機車格的實際來源涵蓋，以及 parkingstatus／cellstatus 官方字典。
- 名冊16個未匹配場站，以及場站資料變更後重新核對的更新流程。
- 上游沒有發布入口時，不把車格／場站中心冒充已確認可通行入口。

完整來源與政策範圍：[雙北政策與資料](twin-city-policy.md)。

## 實機與金鑰待驗收

下列項目尚未實機執行。需各一台 iPhone／Android、可連線測試 backend，以及正確限制的 Google Maps／Places 金鑰；每一項完成後記錄日期、機型、OS、app commit、測試帳號及結果。

| 項目 | iOS／Android操作與通過條件 | 狀態 |
| --- | --- | --- |
| 車種 | 未設定偏好開啟為大重；切普重／大重，正式名稱可讀，請求 vehicle 與畫面一致；不另選牌色 | 待實機 |
| 目的地 | 搜尋台北101與新北目的地，選取結果、重新搜尋此區，拒絕定位時仍可操作；測試 Google quota／網路錯誤 | 待設備／金鑰 |
| 詳情與來源 | 核對車種、許可、費率、空位、時間及個別來源連結；未收費／特殊／私營／未核對項目不得被誤授權 | 待實機及範圍資料 |
| 入口與導航 | 確認 ALLOWED 入口才優先導航；UNKNOWN 清楚提示；無入口用明確場址 fallback；Apple／Google Maps 實際可開啟 | 待實機 |
| 外觀與字體 | 深淺色、系統色、最大字體／顯示大小，資訊不截斷、控制可操作 | 待實機 |
| VoiceOver／TalkBack | 車種正式名稱、選中狀態、搜尋、結果、來源、入口、導航、收藏／回報順序可理解 | 待實機 |
| 開發登入 | 正確 token 的收藏、取消、重登入保留；回報及照片成功／失敗提示；訪客不能寫入 | 待實機連線 |

正式登入供應商、正式部署與更新排程為後續工作，沒有在這次驗收中啟用。

## 重跑

依專案 .env.example 建立 PostGIS／Redis，再執行：

```bash
cd backend
ruff check .
ruff format --check .
pytest tests/ -q
```

另在 mobile 目錄執行 flutter pub get、flutter analyze、flutter test；Docker／Compose與雙平台建置使用 `.github/workflows/ci.yml` 的相同檢查。資料庫 migration 測試會切換 schema，必須使用隔離測試資料庫並按順序執行，不能和另一個匯入／測試程序共用同一 DB。
