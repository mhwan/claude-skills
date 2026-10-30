# Assembly tools: what they return and where they fall short

Observed 2026-10-29 with the 22nd Assembly. Re-check a point before relying on it for something new.

## assembly_bills (the list)
- Arguments used: `name` (bill name keyword), `from`, `to` (propose date, YYYY-MM-DD), `committee`, `proposer` (member name), `proposer_kind` (의원 / 위원장 / 정부), `limit` (at most 300).
- With no `proposer_kind` it returns member bills only. Query 의원, 위원장 and 정부 separately; the script removes duplicates by `BILL_NO`.
- Result: `{"total", "returned", "bills": [...]}`. `total` ignores the period, so it says nothing about completeness. `returned` below `limit` does not prove the list is complete either; only `returned == limit` is a sure sign of a cut.
- Row fields used: `BILL_ID`, `BILL_NO`, `BILL_NAME`, `PROPOSER`, `COMMITTEE` (empty before a committee is assigned), `PROPOSE_DT`, `DETAIL_LINK`.
- The list has no stage. Stage comes only from `assembly_bill_detail`.

## assembly_bill_detail (one bill)
- Argument: `bill_no` (or `bill_id`).
- Result: `{"bill": {...}, "committee_reviews": [...], "alternative": ..., "original_bills": ...}`.
- Fields used: `bill.PASSGUBN`, `bill.PROC_STAGE_CD`, `bill.JRCMIT_NM`, `bill.PPSR_KND`, `bill.LINK_URL`, and `alternative` (merged bills; shown in the report's stage cell).
- A call is one bill. Thirty per run is the default cap to keep a run short.

## Big results
Results above about 96,000 characters are saved by the tool into a file under `tool-results/` and the reply names that path. Put that path (absolute) in the query's `file`. Smaller results arrive inline: write them into `run_dir/raw/` unchanged and run `build` with `--transcribed`.
