# Interview Story Bank

## 1. Exchange Timestamp vs Observation Timestamp

**Situation:** The project used historical incremental L2 and trades for
BTC-USDT. Exchange timestamps were available, but they were not enough to define
what a live process could have observed.

**Technical issue:** Exchange event time can arrive out of order relative to the
vendor receive/local timestamp. If replay sorts only by exchange time, it can
create a cleaner book than a real observer would have had.

**Decision:** Replay was governed by local observation time plus preserved
source row order. Exchange event time was retained as metadata, not used as the
sole causal ordering key.

**Result:** Features used only book and trade events observable by T, labels
were generated after T, and the replay supported same-local-timestamp
source-order tie-breaking.

**What it demonstrates:** I treated causality as a data-engineering property,
not as an assumption added during modeling.

## 2. Event-Level OFI vs Sampled-BBO Shortcut

**Situation:** Queue imbalance already produced strong short-horizon predictive
IC, so the next question was whether OFI added independent information.

**Technical issue:** A sampled-BBO shortcut can miss the event-level pressure
between fixed-clock samples and accidentally blur cause and effect.

**Decision:** OFI was computed from causally eligible event-level book updates
inside backward-looking windows, while downstream models kept QI as the simple
baseline.

**Result:** OFI added the clearest incremental information beyond QI. Phase 9
showed +0.0067 QI+OFI delta and +0.0107 Extended delta under expanding
walk-forward validation.

**What it demonstrates:** I prefer a simple interpretable baseline first, then
test incremental information with controls rather than replacing the baseline
with a complex model by default.

## 3. HEAD/GET Source Availability Bug

**Situation:** Source verification was added before expanding multi-day
research, including checks for known-good Tardis historical files.

**Technical issue:** A HEAD request could fail or be blocked even when a GET
request to the same object worked. Treating HEAD failure as source
unavailability would incorrectly block valid data.

**Decision:** The availability check was changed to match the actual access
path. The verification used browser-compatible GET semantics and retained exact
source identity checks.

**Result:** The project avoided a false source-failure conclusion while keeping
source verification strict.

**What it demonstrates:** I do not weaken controls when they fail; I make the
control match the real system boundary.

## 4. YAML `null` Parser / CI Bug

**Situation:** A GitHub Actions research-smoke workflow failed in config
hashing even though local fallback parsing had passed.

**Technical issue:** PyYAML parsed the unquoted mapping key `null` as Python
`None`. Canonical JSON with `sort_keys=True` then failed when comparing `None`
and string keys.

**Decision:** The model config key was renamed to `null_baseline`, and recursive
validation was added to reject any non-string YAML mapping keys after loading.

**Result:** The CI portability issue was fixed without silently coercing keys
or weakening deterministic hashing.

**What it demonstrates:** I treat config serialization as part of the research
surface. Reproducibility failures deserve tests, not local exceptions.

## 5. Pre-Registering Dates Before Alpha Analysis

**Situation:** The initial statistical and execution results were strong enough
that date selection could easily become biased.

**Technical issue:** Choosing dates after seeing outcomes can make robustness
look better than it is.

**Decision:** Development dates, execution robustness dates, passive dates,
order-size dates, queue fractions, TTLs, and fee overlays were frozen in
manifests before the relevant outcome analysis.

**Result:** Negative days, model underperformance cases, low passive fill
rates, and residual inventory remained in the reports.

**What it demonstrates:** I designed the workflow to make weak results visible
instead of tuning them away.

## 6. Suspicious-Result Audit After 0.43 IC

**Situation:** QI showed a roughly 0.43 mean daily IC at 1s, which is unusually
strong for noisy market data.

**Technical issue:** A result that strong could indicate leakage, repeated
states, overlapping labels, or a timestamp bug.

**Decision:** I performed a targeted robustness audit: changed-state analysis,
unique-state checks, non-overlap sampling, manual lineage checks, independent
label recomputation, and a temporal mismatch control.

**Result:** The primary QI result remained stable. The audit found 0.447
changed-state IC, 0.425 non-overlap IC, and only 0.004 temporal mismatch IC.

**What it demonstrates:** I treat surprising results as something to attack
before using them.

## 7. Why LightGBM Added Only Modest Value

**Situation:** LightGBM had access to a broader feature set than QI alone.

**Technical issue:** Many book-state features were redundant with QI or
microprice, so a nonlinear model could easily add complexity without much
independent signal.

**Decision:** The modeling comparison kept QI as the baseline and measured
incremental IC by date and fold.

**Result:** LightGBM improved predictive IC consistently but modestly. Phase 8
found +0.008 Extended LightGBM lift, and Phase 9 found +0.0107 Extended delta
under expanding folds.

**What it demonstrates:** I can explain when model complexity is useful at the
margin but not transformational.

## 8. Why QI Beat Extended Economically Despite Lower Predictive IC

**Situation:** Extended LightGBM produced better predictive metrics than QI.

**Technical issue:** Better forecasts can create more turnover or different
trade timing. After spread crossing and fees, higher gross dollar PnL can be
less efficient per unit turnover.

**Decision:** Execution and accounting reports emphasized PnL per turnover,
breakeven fees, fee overlays, and cross-date ranking rather than only gross
dollars.

**Result:** QI remained the stronger economic-efficiency baseline. It had 8
first-place efficiency contexts in Phase 14, while Extended-minus-QI averaged
-2443.68 mean Extended-minus-QI net delta in the 0.25 bps, 0ms market scenario.

**What it demonstrates:** I separate predictive lift from executable economics.

## 9. Why Passive Limit Orders Did Not Automatically Improve Results

**Situation:** Passive execution looked like a natural way to avoid paying
spread.

**Technical issue:** Passive orders introduce queue uncertainty, partial fills,
expired orders, adverse selection, and terminal inventory.

**Decision:** The simulator tracked full fills, partial fills, no-fill/expired
orders, maker/taker roles, post-fill markouts, terminal position, and inventory
stress.

**Result:** Passive execution remained fill-constrained. Phase 14 showed 1.56%
QI passive mean fill rate and 4.70% Extended passive mean fill rate at 0ms,
while residual inventory remained visible.

**What it demonstrates:** I do not assume a cheaper order type improves
economics without fill-quality and inventory evidence.

## 10. Why 2026 Holdout Remains Sealed

**Situation:** The project had many development results, including strong
predictive IC and mixed execution robustness.

**Technical issue:** Opening the final temporal holdout before all rules are
fully frozen would turn confirmatory evaluation into another research step.

**Decision:** 2026 was never accessed during Phases 1-15. The final report
states that it remains reserved for a future confirmatory evaluation.

**Result:** The project can still perform a clean final test after the research,
execution, and reporting decisions are frozen.

**What it demonstrates:** I understand the difference between development
evidence and confirmatory validation.
