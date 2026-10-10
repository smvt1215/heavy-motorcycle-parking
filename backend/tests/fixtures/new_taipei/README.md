# New Taipei source samples

`static_sample.json` and `realtime_sample.json` are reduced official offstreet feed records captured for M7 (2026-10-06); the source fields and ambiguous original tariffs are retained.

`roadside_sample.json` is the first three actual records from page 0 of official roadside dataset `54a507c4-c038-41b5-bf60-bbecb9d052c6`, checked on 2026-10-10. These are ordinary **car** cells. The captured first page had 1000 records; this fixture is not a complete snapshot and does not establish live motorcycle-policy coverage. Codes `parkingstatus` and `cellstatus` are retained without invented definitions.

`roadside_motorcycle_real.json` holds five actual `機車停車位` records from the same dataset, captured on 2026-10-10 from the complete 31-page JSON snapshot (30,941 rows; 48 motorcycle cells, all `paycash` = `每次四小時30元/次`). They cover the weekday `限時計次收` mode, a holiday-only mode, a combined weekday/holiday value whose `day` says holidays only, and the undefined `車彎` memo. Records are unmodified; status codes keep no invented meaning.

`offstreet_roster_real.json` holds nine unmodified offstreet records captured on 2026-10-10 (1,384-row snapshot) for roster reconciliation: two captured managed facilities and the live near-name leads for unmatched roster rows. Near names remain leads; they are not evidence that a lot is transport-managed.

`roadside_real.csv` holds ten rows from the official complete CSV file of the same dataset (5,134,622 bytes, 31,560 rows, downloaded 2026-10-10): four motorcycle cells including `159680`, which the paged JSON API omitted, two car cells and all four rows that share id `968` with different roads. Values are unmodified; rows were re-serialized with a UTF-8 BOM and CRLF like the source.

Policy tests which replace a roadside sample with `機車停車位` are explicitly synthetic. They test the scope contract, not factual identification of a real paid motorcycle cell.

Source ownership mapping and limitations: [雙北政策與資料](../../../../docs/twin-city-policy.md).
