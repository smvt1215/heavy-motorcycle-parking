# New Taipei source samples

`static_sample.json` and `realtime_sample.json` are reduced official offstreet feed records captured for M7 (2026-10-06); the source fields and ambiguous original tariffs are retained.

`roadside_sample.json` is the first three actual records from page 0 of official roadside dataset `54a507c4-c038-41b5-bf60-bbecb9d052c6`, checked on 2026-10-10. These are ordinary **car** cells. The captured first page had 1000 records; this fixture is not a complete snapshot and does not establish live motorcycle-policy coverage. Codes `parkingstatus` and `cellstatus` are retained without invented definitions.

Policy tests which replace a roadside sample with `機車停車位` are explicitly synthetic. They test the scope contract, not factual identification of a real paid motorcycle cell.

Source ownership mapping and limitations: [雙北政策與資料](../../../../docs/twin-city-policy.md).
