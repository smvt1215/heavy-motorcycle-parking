# 雙北車種政策與路邊資料

車種契約為 `NORMAL_HEAVY`（普重）及 `LARGE_HEAVY`（大重，包含黃牌／紅牌）。政策只提供分類級規則，不用格數、價格、場站名稱或目前是否收費推導許可。

## 官方政策

| 政策來源 | 公告日期 | 本系統生效時間（Asia/Taipei） | 適用範圍 |
| --- | --- | --- | --- |
| [臺北停管處公告](https://pma.gov.taipei/News_Content.aspx?n=E43F2E5AE1223B3D&s=1190C312983454BA&sms=78D644F2755ACCAA) | 2026-09-30 | 2026-10-06 00:00 | 公有路邊已公告收費一般機車格，限一般機車可停時段；未收費格、特殊禁停及路外不自動套用 |
| [新北路邊公告](https://www.traffic.ntpc.gov.tw/home.jsp?act=be4f48068b2b0031&dataserno=99345bcf365b8f6ed7a6315705335638&id=54fa46e9e522dde4&mserno=39e5192ff77897e0ae099c1886ca9b09) | 2026-06-29 | 2026-07-01 00:00 | 公有路邊收費一般機車格；大重每4小時計次30元；特殊限制不自動套用 |
| [新北公有路外汽車格公告](https://www.traffic.ntpc.gov.tw/home.jsp?act=be4f48068b2b0031&dataserno=dac9467f3186024b02ee3214e1ab7dcd&id=148) | 2025-03-21 | 2025-03-21 00:00（最早已確認公告時間） | 交通局轄管公有路外場站汽車區；不擴張至私營或路外一般機車區 |

`app/ingestion/policies.py` 保存政策代碼、來源 URL、發布日期、生效時間、範圍和明確配置的 authority priority 200。規則沿用既有優先序：區域 > 全場、EXCEPTION > BASELINE、authority priority，最高同層的衝突／未知仍為 UNKNOWN。原始資料 baseline priority 100 不會取代政策，區域特殊禁停 EXCEPTION 仍可覆蓋政策。

政策許可、費率、即時及入口各用自己的 source_id。政策 scope evidence 保存在 parking_rules.notes JSON；費率原文及 scope evidence 保存在 parking_rate_sources.raw_payload。選車種 API 的各自 provenance 提供來源名稱及 URL；手機可開啟來源原文。公告僅有發布日期時不猜精確更新時間，未記錄公告接收時間時其 source_updated_at／fetched_at 保持 NULL；發布日期與場站範圍的獨立擷取時間分別保存在證據，不把 feed 的時間代入公告來源。

個別區域的許可與政策費率，起日取政策生效、已核對範圍與首次車格快照擷取時間的最晚者。政策公告日不能證明某個車格在過去已有相同分類、限制或時段。首次範圍時間保存為 scope_verified_from；相同範圍後續匯入不重設起日。

每次匯入取得來源鎖後，檢查該來源所有政策歷史版本；即使場站已消失或對應失敗，也會收緊過早的窗口。完全早於範圍證據的許可停用、費率保持不可確認；原 ID、區間、數值及來源證據保留。其他來源不受影響。

## 新北公有路外場站核對

[官方名冊（114/05/12）](https://www.traffic.ntpc.gov.tw/websitedowndoc?file=traffic%2F202505141350170.pdf&filedisplay=%E6%96%B0%E5%8C%97%E5%B8%82%E5%81%9C%E8%BB%8A%E5%A0%B4%E5%A4%A7%E5%9E%8B%E9%87%8D%E5%9E%8B%E6%A9%9F%E8%BB%8A%E6%A0%BC%E4%BD%8D%E5%8F%8A%E9%81%A9%E7%95%B6%E7%A9%BA%E9%96%93%E7%B5%B1%E8%A8%88%E8%A1%A8%281140512%29.pdf) 有71處。以行政區及唯一完整名稱核對路外來源，僅容許相同行政區名稱前綴差異；55處取得來源 ID，16處未匹配。結果及 PDF SHA-256 保存於 `backend/app/ingestion/evidence/new_taipei_managed_facilities.json`，隨 backend wheel 打包。

個別場站的政策規則不早於名冊確認範圍的2025-05-12；公告日期仍獨立保存為2025-03-21，不據此推測更早的轄管身份。執行時必須同時精確符合來源 ID、名稱、行政區、地址。地址或名稱變更、ID 未核對、沒有明確汽車區時不掛政策。名冊不能證明全部政府資料中的場站都是轄管公有場站；`TYPE` 表示即時資料分類，也不能當成所有權。

例如 `010152` 四維公園地下停車場只在既有 `CAR_SHARED` 區新增大重政策。機車區及由重機價格建立的容量未知區仍維持 UNKNOWN。不增加大重專用區，不複製 capacity／AVAILABLECAR，也不把小型車費率搬到大重。

來源不再匹配、區域消失或完整快照中場站消失時，政策有效區間結束。重新出現建立新有效區間，撤回期間保持 UNKNOWN。其他來源的規則／費率不受影響。政策時段變更也建立新版本。無效的費率撤回時間會明確拒絕該筆資料。

## 新北路邊介接

[新北市路邊停車位資訊](https://data.gov.tw/dataset/122901) 的 JSON 端點：

`https://data.ntpc.gov.tw/api/datasets/54a507c4-c038-41b5-bf60-bbecb9d052c6/json`

```bash
python -m app.ingestion.cli --city new_taipei_roadside --feed all
python -m app.ingestion.cli --city new_taipei_roadside --feed static \
  --static-file tests/fixtures/new_taipei/roadside_sample.json \
  --fetched-at 2026-10-10T02:00:00Z --no-cache
```

static 與 realtime 使用不同來源代碼，保留相同來源的原始 payload。每頁1000筆、最多100頁；失敗頁不當成完整快照，資料驟減低於80%時不撤回缺漏車格。使用獨立 namespace、鎖及快取鍵，避免與路外 ID 撞號。

| 欄位 | 使用方式 |
| --- | --- |
| `id`／`cellid`／`roadname` | 車格來源 ID、展示名稱；每個明確車格一區，不以費率或格數判斷合法性 |
| `latitude`／`longitude` | 明確車格座標；不是已確認可進入的入口 |
| `name` | 僅精確一般機車／汽車標籤提供該類 baseline；特殊或未定義類別 UNKNOWN |
| `countycode`、`pay`、`memo` | 政策需新北、公有來源、明確已收費的一般機車格及確認無特殊限制；未定義 memo 不授權 |
| `day`／`hour` | 只接受明確週一至週五、週一至週六、每天及 HH:MM 時段；不明時段維持 UNKNOWN |
| `paycash` | 獨立保留原文並核對 `pay`；免費／計次／計時與原文衝突或模式未知時不產生確認價格；不證明合法性 |
| `isnowcash` | 目前收費旗標，不能證明政策範圍 |
| `parkingstatus`／`cellstatus` | 未取得明確官方代碼定義，僅留原始證據；即時空位 UNKNOWN／NULL |

新北政策的30元／4小時以 PER_ENTRY（unit_minutes=240）呈現，沒有7.5元／小時比較值。未確認時段不能產生確定價格比較值。首次匯入早於政策生效時，費率待生效後再次匯入啟用；個別許可與費率仍須受各自範圍證據的時間限制。入口不足維持空集合／UNKNOWN。展示名稱最多200字，過長路名以省略號縮短；完整地址、車格分類與來源 ID 保留。

## 已確認資料與限制

2026-10-10 抽查新北路外兩頁（1384筆）及路邊首頁（1000筆）；路邊樣本前三筆為真實汽車格。測試中的「機車停車位」政策邊界案例明確標為合成資料，不冒充已找到的真實付費機車格。

臺北既有 V2 來源為路外場站。路邊 XML 的 `roadSegCarType`、費率單位與車格狀態缺少已確認的分類字典，不能證明收費一般機車格。因此臺北路邊政策已納入可套用的規則及生效／範圍測試，但尚未把未核對 XML 車格授權。`apply_roadside_policy` 僅接受已確認公有、收費、一般機車、特殊限制與停放時段的 scope evidence；新增實際 scope mapping 前需取得可靠來源定義。

共享空位只留在汽車區。來源無同步的總格數時間時，total=NULL；不完整的 ALLOWED 區覆蓋不能產生數字總計。UNKNOWN 車格須主動開啟；已知不可停仍排除。附近排序公式與 sort_version=1 不變。

後續須補核對16個未匹配公有場站、官方路邊狀態字典、真實一般機車付費格及臺北收費機車格 scope；補資料前保持 UNKNOWN。更新排程與正式部署另行安排。
