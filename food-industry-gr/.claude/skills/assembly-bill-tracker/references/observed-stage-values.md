# Observed stage values (raw, from assembly_bill_detail, observed 2026-10-29)

Raw strings from the National Assembly tool, shown as-is. There is no official order table yet;
this list was promoted to `shared/assembly_stages.py` (the skill carries a copy at `scripts/lib/assembly_stages.py`).
Unknown values go to `warnings[]`.

| PASSGUBN | PROC_STAGE_CD | Bill numbers looked up | Notes |
|---|---|---|---|
| 계류의안 | 접수 | 2222216 | no committee yet |
| 계류의안 | 소관위접수 | 2222098, 2221991 (government), 2221536 | 2222098: JRCMIT_NM 보건복지위원회, JRCMIT_CMMT_DT 2026-10-27 |
| 계류의안 | 소관위심사 | 2219198, 2215376 | committee_reviews: 상정, 제안설명, 검토보고, 대체토론, 소위회부 |
| 처리의안 | 본회의불부의 | 2216569 | result 대안반영폐기; alternative bill 2221665 |
| 처리의안 | 철회 | 2215735 | RGS_CONF_RSLT 철회 |
| 처리의안 | 공포 | 2221665, 2221667 (committee-chair alternatives) | chain: 대안가결 -> 수정가결 -> 원안가결 -> GVRN_TRSF_DT -> PROM_DT |

## Where each field lives

- List rows (`assembly_bills`): BILL_ID, BILL_NO, BILL_NAME, PROPOSER, RST_PROPOSER (absent for government),
  COMMITTEE (empty before a committee is assigned), PROPOSE_DT, DETAIL_LINK. Committee-chair rows also carry
  CMT_PROC_RESULT_CD, LAW_PROC_RESULT_CD, PROC_RESULT, PROC_DT.
- Detail (`assembly_bill_detail`): PASSGUBN, PROC_STAGE_CD, JRCMIT_NM, PPSR_KND (의원 / 위원장 / 정부),
  LINK_URL, committee_reviews[], alternative / original_bills (merged bills).

## Limits seen

- Default list returns member bills only; query proposer_kind separately and de-duplicate by BILL_NO.
- `total` ignores the period; `returned` can be below `limit` without the list being complete.
- Results above ~96k characters are saved by the tool to a file under `tool-results/`; small ones arrive inline.
