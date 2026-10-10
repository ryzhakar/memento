# Change Log

## 2026-04-09 — The base model changes
KIND:          computed
FROM → TO:     a small English-only encoder → a larger multilingual encoder
WHY:           the small encoder lacked the languages production serves
INVALIDATES:   every number the small encoder produced

## 2026-05-07 → 2026-05-09 — Splits stop materializing
KIND:          runnable
FROM → TO:     a semi-join that ran out of memory at 370M rows → a membership filter that streams
WHY:           the join was one of six crashes; the filter holds only the positive ids
INVALIDATES:   nothing
EVIDENCE:      merge abc1234
SUPERSEDED-BY: 2026-06-26 — offer-level routing replaced the filter

## 2026-06-26 — The split unit changes from the pair to the offer
KIND:          computed
FROM → TO:     a pair-level split → an offer-level holdout drawn per seed
WHY:           one offer sat in both train and eval through different pairs
               and inflated the measured separation
INVALIDATES:   every result produced before the fix,
               by explicit verdict
EVIDENCE:      merge def5678
