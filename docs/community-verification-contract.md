# 社群核實契約

更新日期：2026-10-10。Issue：[#32](https://github.com/smvt1215/heavy-motorcycle-parking/issues/32)。來源計畫：[社群核實計畫](community-verification-plan.md)。

本文件是 #33–#37 實作要遵守的規格。#32 交付的範圍是：資料庫遷移 `006_community_verification`、ORM 模型、`app/domain/community.py` 的純函式，以及照片上限的預設值。**API 端點、背景處理與手機／Web 畫面尚未實作**；第 9 節列出的端點是 #34 要實作的規格，目前 OpenAPI 裡還沒有這些端點。

不變的部分：`/api/v1` 既有端點、共通 zone wire schema、規則優先序與 `sort_version=1` 都不變。社群資料不寫入 `parking_rules`、`parking_rates`、`parking_realtime`、`parking_entrances` 或 `parking_facilities`。

## 1. 事實類型與範圍

| `fact_type` | 類別 | `vehicle` | `zone_id` | 可發布方式 |
| --- | --- | --- | --- | --- |
| `LIGHTING` | 低風險觀察 | 必須為 NULL | 可選 | 社群補證、人工採納 |
| `RAIN_COVER` | 低風險觀察 | 必須為 NULL | 可選 | 同上 |
| `CHARGING` | 低風險觀察 | 必須為 NULL | 可選 | 同上 |
| `ENTRANCE_LOCATION` | 低風險觀察 | 必須為 NULL | 可選 | 同上 |
| `PARKING_PERMISSION` | 需來源解析 | `NORMAL_HEAVY`／`LARGE_HEAVY` | 必填 | 已核實來源、人工採納 |
| `RATE` | 需來源解析 | 同上 | 必填 | 同上 |
| `ENTRANCE_ACCESS` | 需來源解析 | 同上 | 必填 | 同上 |

資料庫約束 `ck_community_cases_scope_by_fact_type` 與 `ck_community_cases_vehicle_rider` 強制以上規則；`CAR` 不是騎士車種，不能用在案件上。`zone_id` 須屬於同一場站（複合外鍵）。

`proposed_value` 與 `observed_value` 都是 JSON object（DB 檢查 `jsonb_typeof = 'object'`），各類型的欄位如下。#34 用 Pydantic 嚴格驗證，不接受未知欄位：

| `fact_type` | 欄位 |
| --- | --- |
| `LIGHTING`／`RAIN_COVER`／`CHARGING` | `{"present": true \| false}` |
| `ENTRANCE_LOCATION` | `{"latitude": number, "longitude": number, "entrance_type": "VEHICLE" \| "PEDESTRIAN" \| "MIXED" \| null}` |
| `PARKING_PERMISSION` | `{"allowed": true \| false}`，只描述 `vehicle` 欄位指定的車種 |
| `RATE` | `{"raw_text": string}`，由來源解析器正規化；不接受使用者自填的比較價 |
| `ENTRANCE_ACCESS` | `{"entrance_id": integer \| null, "heavy_motorcycle_access": true \| false}` |

提議值不能是「未知」。觀察者無法確認時用 `CANNOT_CONFIRM` 立場，而不是送出 null。

## 2. 狀態

### 2.1 審查狀態 `case_review_status`

`PRECHECK_PENDING` → 初審後進入 `AWAITING_CORROBORATION`、`MANUAL_REVIEW` 或 `ACCEPTED`。人工可改為 `ACCEPTED`、`REJECTED`、`NEEDS_EVIDENCE` 或 `SUPERSEDED`。`NEEDS_EVIDENCE` 的案件由作者補件，建立新的 revision 後回到 `PRECHECK_PENDING`。`SUPERSEDED` 必須記錄取代它的案件（`superseded_by_case_id`，不可指向自己）。

### 2.2 發布生命週期

儲存值 `case_publication_state`：`UNPUBLISHED`、`PUBLISHED`、`SUSPENDED`、`WITHDRAWN`。API 的 `publication_status` 另外多一個 `EXPIRED`，**只在讀取時推導，不儲存**（`derive_publication_status`）：

- `UNPUBLISHED`、`WITHDRAWN` 原樣回傳。
- `PUBLISHED`、`SUSPENDED` 在 `now >= published_until` 時回傳 `EXPIRED`。有效期間是 `[first_published_at, published_until)`。
- `published_until` 為 NULL（`VERIFIED_SOURCE`）時沒有觀察期限。

DB 約束：

- `UNPUBLISHED` 時 `publication_basis`、`first_published_at`、`published_until` 都必須是 NULL；其他狀態必須有 basis 與首次發布時間。
- `COMMUNITY_CORROBORATED` 與 `MANUAL_REVIEW` 的期限固定為首次發布起 90 天；`VERIFIED_SOURCE` 的 `published_until` 必須是 NULL，不會被推導成 `EXPIRED`（`ck_community_cases_observation_term_90_days`）。`first_published_at` 只寫一次，暫停後恢復不重設期限。
- `PUBLISHED` 必須是 `ACCEPTED`。被異議暫停時，審查狀態可回到 `MANUAL_REVIEW`，發布狀態為 `SUSPENDED`。
- `WITHDRAWN` 必須有 `withdrawn_at`，反之亦然。

| `publication_basis` | 意義 | 公開標示 |
| --- | --- | --- |
| `COMMUNITY_CORROBORATED` | 低風險觀察，達自動補證門檻 | 「社群核實」，附核實人數 |
| `MANUAL_REVIEW` | 人工採納，未達或不適用自動門檻 | 「人工複審採納」，不宣稱 3 人核實 |
| `VERIFIED_SOURCE` | 已核實來源經解析器確認 | 顯示來源 provenance |

`COMMUNITY_CORROBORATED` 只允許低風險類型（`ck_community_cases_corroboration_low_risk_only`），票數不能代替許可、費率或通行性的證據。

### 2.3 舊回報對照

`user_reports` 保持原狀，**不轉成案件、不發布、不給分、不推測拍攝時間**。舊的 `PATCH /reports/{id}/status` 只處理舊回報，不能發布新事實。

| 舊 `user_report_status` | 意義 | 與新流程的關係 |
| --- | --- | --- |
| `PENDING` | 待審 | 無對應案件 |
| `VERIFIED` | 管理員確認回報內容屬實，公開時才顯示文字 | **不等於** `ACCEPTED`／`PUBLISHED`；不產生停車事實 |
| `REJECTED` | 不採納 | 無 |
| `SUPERSEDED` | 已被新資訊取代 | 無 |

管理員可以依舊回報手動開新案件，並把 `origin_report_id` 指向原回報（原回報被刪除時會設為 NULL）。新案件一律從 `PRECHECK_PENDING` 開始，照片要重新上傳並重新判定時間。

## 3. 參與者身分

`community_participants` 是內部用的參與者識別，一個使用者最多一筆。刪除使用者時 `user_id` 會變成 NULL，但這筆資料保留，案件、立場、照片、事件與積分仍指向同一位參與者。服務層**不得**把參與者改指向其他使用者。管理員第一次操作時也建立參與者，利益迴避用參與者 ID 比對：作者、提出支持或異議的人、上傳證據的人，都不能審核該案件。

公開回應不暴露 participant ID 或 user ID。

## 4. 來源核實與正規化

`source_verifications` 記錄管理員核實過的來源：

- **基本欄位**：`data_sources` 的來源、可確認的 `fact_kind`（`PERMISSION`／`RATE`／`REALTIME`／`ENTRANCE`／`FACILITY`），以及範圍（`parking_id` 為 NULL 代表該來源自己發布的所有場站；`zone_id` 為 NULL 代表全場）。
- **證據 URL**：必須是 HTTPS。
- **核實資訊**：核實者、核實時間、撤銷時間。

規則：

- 只有 `PERMISSION` 帶 `rule_kind` 與 `authority_priority`，其他類型這兩欄必須是 NULL。審查人在案件決定中**不能**提高層級或權威，只能使用這份設定。
- `parser_code` 與 `parser_config_version` 要同時存在或同時為 NULL。NULL 代表沒有實作解析器，只能走人工。人工採納不會把格式加入解析器。
- `REALTIME` 只用於官方或營運商的即時來源；公告與照片不能確認即時空位。
- 撤銷時設定 `revoked_at`（不早於 `verified_at`）。撤銷後，依既有版本化流程結束受影響事實的有效區間。
- 寫入規則前，先解析並預覽兩車種的結果。不得複製另一車種的值來填 NULL；更新同來源既有規則時，證據沒提到的車種保留原值。會產生同層 NULL 衝突時停止自動發布，轉人工。

## 5. 照片拍攝時間

### 5.1 擷取

後端在轉正、縮放、重新編碼與移除 metadata **之前**讀取 EXIF `DateTimeOriginal`、`OffsetTimeOriginal`，有的話也讀 `SubSecTimeOriginal`，交給 `classify_photo_time` 判定。資料庫只保存：

- 這三個欄位的原始字串；
- 解析出的 UTC 時間 `captured_at`；
- `time_status`、`time_parser_version`（目前為 `exif-time-1`）；
- 伺服器接收時間 `received_at`。

不保存 GPS 或整份 EXIF。儲存的照片仍重新編碼成無 metadata 的 JPEG。客戶端不能送出拍攝時間；上傳時間、檔案修改時間、EXIF 修改時間與裝置時區都不能拿來代替拍攝時間。畫面與 API 文件稱它為「照片中繼資料時間」，不宣稱是已驗證的真實拍攝時間。

### 5.2 判定（伺服器接收時，之後不改寫）

| `time_status` | 條件 | `captured_at` |
| --- | --- | --- |
| `VALID` | 時間與偏移都合法，且 `received_at − 30 天 ≤ captured_at ≤ received_at`，兩端都包含 | 有值 |
| `MISSING` | 沒有 `DateTimeOriginal`，或只有空白／NUL | NULL |
| `TIMEZONE_UNKNOWN` | 時間合法但沒有偏移；**不假定 Asia/Taipei** | NULL |
| `INVALID` | 格式不符 `YYYY:MM:DD HH:MM:SS`、日期不存在（含 `0000:00:00`）、偏移格式錯誤或超出 −12:00～+14:00、小數秒不是數字 | NULL |
| `FUTURE` | 晚於 `received_at`，不容許誤差 | 有值 |
| `OUTSIDE_WINDOW` | 早於 `received_at − 30 天` | 有值 |

小數秒存在時保留到微秒；沒有小數秒不影響是否有效。原始字串用 `exif_raw_for_storage` 正規化後才存入：只有空白或 NUL 的值存成 NULL，前後的空白與 NUL 會去掉，超過欄位長度的值不存（這種值一定判定為 `INVALID`）。DB 約束 `ck_community_evidence_photos_time_status_consistent` 確保時間狀態與儲存欄位一致。

自動發布當下另外用 `counts_at_publication` 檢查：原回報與每張支持照片都必須是 `VALID`，而且在發布時刻往前 30 天內。這個檢查不改寫接收時的判定。發布後因撤票或異議重新計票時，照片自然變舊不會取消資格。

### 5.3 上傳與刪除

- 原檔上限的預設值從 5 MB 提高到 **15 MB**（`REPORT_PHOTO_MAX_BYTES`，設定最大 20 MB）。像素上限維持 4,000 萬。這也影響現有的回報照片端點。
- 解碼失敗、超過大小或像素上限仍會被拒絕；缺 metadata 的照片仍可提交。
- `normalized_sha256` 是儲存後 JPEG 的 SHA-256，只用來在同一案件內做確定性去重：重複的照片仍會保存，但不算獨立證據。它不宣稱能識別所有改圖或灌票。
- 每張照片只屬於一個來源：原回報的 revision，或某個立場（`ck_community_evidence_photos_one_owner`），而且必須是同一案件（複合外鍵）。
- 照片在接收時就固定：所屬來源、雜湊、接收時間、EXIF 值、解析版本與時間狀態都不能修改。唯一允許的 UPDATE 是一次性的刪除轉換（trigger `community_guard_photo_update`）。
- 刪除照片時清除儲存物件 key 與私人原始時間欄位，設定 `deleted_at`，保留這筆紀錄供稽核與去重。若因此證據不足，暫停受影響的發布並轉複審。

## 6. 補證、異議與初審

- 每位參與者在每個案件同時只能有一個有效立場（部分唯一索引 `uq_community_case_stances_active`）。改變立場時，先撤回舊立場（`withdrawn_at`），再新增一筆，歷史全部保留。被認定無效時記錄 `invalidated_at` 與 `invalidated_reason`。立場的內容不能修改，這三個欄位只能從 NULL 設定一次（trigger `community_guard_set_once`）。revision 同樣不能修改，只有 `description_approved_at` 可以設定一次。
- `SUPPORT`、`OPPOSE` 必須附觀察值；`CANNOT_CONFIRM` 不需要。
- **自動補證門檻**（`CORROBORATION_THRESHOLD = 3`）：低風險案件除了原作者之外，還要有 3 位參與者各自提出 `SUPPORT`、附自己的觀察值，以及至少一張發布時仍符合條件的照片。原回報本身也要有符合條件的照片。重複的 evidence ID 或 SHA-256 不算獨立證據。作者的支持不計入。
- **有效異議**：提出者是未停權的登入者，不是重複支持的證據，指向同一事實與區域，附符合條件的照片和不同的觀察值。有效異議會暫停已發布的觀察並轉人工；未達條件的異議可以送人工，但不能自動暫停。
- **初審**：每次執行寫入 `community_prechecks`（規則版本、整體結果、時間；`(case_id, revision)` 必須指向既有的 revision），每項檢查寫入 `community_precheck_results`：

| `check_code` | 內容 |
| --- | --- |
| `IDENTITY` | 登入、停權、利益迴避 |
| `REQUIRED_FIELDS` | 必填欄位與值的格式 |
| `SCOPE` | 場站、區域與車種範圍 |
| `PHOTO_TIME` | 照片時間狀態 |
| `SOURCE_APPLICABILITY` | 已核實來源與解析器是否涵蓋 |
| `EVIDENCE_DUPLICATE` | evidence ID／SHA-256 去重 |
| `CORROBORATION` | 有效支持數 |
| `SOURCE_CONFLICT` | 與現有來源事實或同層規則衝突 |
| `PUBLICATION_ELIGIBILITY` | 最終是否可自動發布 |

結果為 `PASS`、`FAIL` 或 `NEEDS_MANUAL`；不是 `PASS` 的項目必須附 `reason_code`。不使用 AI 信心分數。

- **時間軸**：`community_case_events` 不可修改。`MANUAL_ACCEPTED`、`MANUAL_REJECTED`、`EVIDENCE_REQUESTED`、`SUPERSEDED` 必須記錄操作的管理員、原因代碼和原因文字。`actor_participant_id` 為 NULL 代表系統操作。
- **人工採納**：缺 metadata 的照片被人工採納後，`time_status` 仍然不是 `VALID`，也不回填成自動補證的票數。

社群自動發布有功能開關 `COMMUNITY_AUTO_PUBLISH_ENABLED`，由 #34 實作，**預設關閉**。開關關閉時，原本可以自動發布的案件改轉人工。對外公開啟用前，必須先有正式的身分驗證。

## 7. 貢獻積分

`contribution_ledger` 只能新增，不能修改（DB trigger 會拒絕 UPDATE）：

| `entry_type` | `points` | 說明 |
| --- | --- | --- |
| `AWARD` | +1 | 每案每位參與者最多 1 筆（`uq_contribution_ledger_one_award`） |
| `FREEZE` | 0 | 爭議或刪照造成證據不足時凍結，指向對應的 AWARD |
| `UNFREEZE` | 0 | 恢復 |
| `REVOKE` | −1 | 判定無效，指向對應的 AWARD；每案每人最多 1 筆 |

`FREEZE`、`UNFREEZE`、`REVOKE` 必須指向同一案件、同一參與者的 `AWARD`（複合外鍵加上 INSERT trigger 檢查）。

`reason` 有三種：

- `ORIGINAL_REPORT`：原回報被採納。
- `CORROBORATION`：補證被採納，必須是在採納前、或在有效發布期間提交並經確認有效。
- `UPHELD_OBJECTION`：異議經人工認定成立，於 #32 決定給分。

單純投票、待審、不採納、重複證據、自動暫停階段的異議，以及到期後才追加的補證，都不給分。

有效積分由 `effective_points` 計算：同一案件最近一次標記是 `FREEZE` 時，該案件不計分。等級（`contribution_level`）：0 分是 `NEWCOMER`，1 分 `L1`，5 分 `L2`，20 分 `L3`，50 分 `L4`。等級只是認可，不改變票權、門檻或來源權威。自然到期不收回分數。不設公開排行榜，積分與等級只給本人和授權的管理員看。

## 8. 隱私、媒體與冪等

- 照片永遠不公開。提交者只能透過媒體代理端點看自己上傳的照片和原始時間欄位；別人的私人證據只有 `MODERATOR` 可以看。回應不暴露儲存 key，並帶 `Cache-Control: no-store`。
- 公開資料只有結構化摘要：事實類型、範圍、值、`publication_basis`、發布狀態、首次發布時間、期限，以及（社群核實時）核實人數。不顯示作者或補證者，也不顯示未經審核的文字。自由文字要經管理員核准（`description_approved_at`）才能顯示。
- 建立案件、補件、立場、異議、管理員決定都要帶 `Idempotency-Key` header（1–128 字元）。後端保存 24 小時（`IDEMPOTENCY_TTL`），以 `(user_id, operation, key)` 為唯一值：同一個 key 但請求內容不同，回 `422 IDEMPOTENCY_KEY_REUSED`；內容相同的重試，回傳當初的狀態碼與內容。過期紀錄由讀取時判斷，不依賴背景排程。
- 管理員寫入時帶 `expected_version`；與案件目前的 `version` 不符時回 `409 VERSION_CONFLICT`。計票、發布、版本與積分在同一個交易內更新。

## 9. #34 要實作的 API

所有寫入端點都用 Bearer token 驗證身分，request body 不接受 `user_id` 等未知欄位。管理員端點要求 `MODERATOR`。

| 方法與路徑 | 權限 | 說明 |
| --- | --- | --- |
| `POST /community/cases` | 登入 | 建立案件與第 1 版 revision；201 回傳案件 |
| `POST /community/cases/{id}/revisions` | 作者，限 `NEEDS_EVIDENCE` | 補件，建立新 revision 並重新初審 |
| `POST /community/cases/{id}/photos` | 作者或已提出立場者 | multipart `file` 加 `owner=revision\|stance`；回傳時間判定 |
| `PUT /community/cases/{id}/stance` | 登入，非作者 | `{"stance", "observed_value"}`，取代自己目前的立場 |
| `DELETE /community/cases/{id}/stance` | 本人 | 撤回立場；204 |
| `GET /me/community/cases` | 本人 | 自己的案件（keyset 分頁） |
| `GET /me/community/cases/{id}` | 作者或參與者 | 案件、revision、自己的證據與時間軸 |
| `GET /me/contributions` | 本人 | `{"points", "level", "entries"}` |
| `GET /community/photos/{photo_id}` | 上傳者本人或 `MODERATOR` | 媒體代理，`no-store` |
| `GET /parking/{id}/community-observations` | 公開 | 結構化摘要，預設只列 `PUBLISHED` |
| `GET /moderation/cases` | `MODERATOR` | 疑義佇列與篩選 |
| `GET /moderation/cases/{id}` | `MODERATOR`，非參與者 | 完整案件、私人證據、初審與預覽 |
| `POST /moderation/cases/{id}/decisions` | `MODERATOR`，非參與者 | `{"decision": "ACCEPT"\|"REJECT"\|"REQUEST_EVIDENCE"\|"SUPERSEDE", "reason_code", "reason_text", "expected_version", "superseded_by_case_id"?}` |
| `DELETE /moderation/photos/{photo_id}` | `MODERATOR` | 處理照片刪除請求 |
| `POST /moderation/source-verifications` | `MODERATOR` | 建立來源核實 |
| `POST /moderation/source-verifications/{id}/revoke` | `MODERATOR` | 撤銷來源核實 |

上傳照片的回應：

```json
{
  "photo_id": 41,
  "metadata_time": {
    "status": "TIMEZONE_UNKNOWN",
    "captured_at": null,
    "datetime_original": "2026:10:10 11:30:00",
    "offset_time_original": null
  },
  "counts_toward_corroboration": false,
  "message_code": "MANUAL_REVIEW_REQUIRED"

}
```

公開觀察：

```json
{
  "observation_id": 7,
  "parking_id": 12,
  "zone_id": null,
  "fact_type": "LIGHTING",
  "value": {"present": true},
  "publication_basis": "COMMUNITY_CORROBORATED",
  "publication_status": "PUBLISHED",
  "corroborator_count": 3,
  "first_published_at": "2026-10-10T04:00:00Z",
  "published_until": "2027-01-08T04:00:00Z",
  "label": "社群核實位置・通行性未確認"

}
```

`label` 只在 `ENTRANCE_LOCATION` 出現：社群核實時為「社群核實位置・通行性未確認」，人工採納時為「人工複審採納位置・通行性未確認」，其他類型為 null。入口觀察不影響預設導航，導航仍優先已確認 ALLOWED 的入口。`corroborator_count` 只在 `COMMUNITY_CORROBORATED` 時有值，其他情況為 null。

新增錯誤代碼：

- `409 VERSION_CONFLICT`
- `422 IDEMPOTENCY_KEY_REUSED`
- `428 IDEMPOTENCY_KEY_REQUIRED`
- `403 CONFLICT_OF_INTEREST`
- `403 PARTICIPANT_SUSPENDED`
- `409 CASE_NOT_AWAITING_EVIDENCE`
- `422 SCOPE_INVALID`（車種或區域不符合第 1 節）

既有的 `401 UNAUTHENTICATED`／`403 FORBIDDEN` 語義不變。

## 10. 程式對應

| 契約 | 位置 |
| --- | --- |
| 列舉值 | `backend/app/models/enums.py` |
| 資料表與約束 | `backend/app/models/community.py`、`backend/migrations/versions/006_community_verification.py` |
| 拍攝時間、發布時檢查、EXPIRED、積分、等級 | `backend/app/domain/community.py` |
| 測試 | `backend/tests/test_community_contract.py`、`backend/tests/test_community_schema.py` |
