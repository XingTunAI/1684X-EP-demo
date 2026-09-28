# VPP attribution v2 — plan before measurement

Question: do the long vpp_basic/convert_to API calls actually spend their time in the VPP driver, and which driver substage differs between the two cards?

Keep the deployed binary, two cards ×32 streams, sync preview cap10, input/model/score gate/reuse unchanged. No driver or production modification.

Add a temporary observer of the two outer BMCV APIs and bm_trigger_vpp. Associate nested submissions with outer API by thread-local parent IDs; retain every raw begin/end, batch descriptor count, return code and lost count. This first checks whether a BMCV operation maps to one or more driver submissions and prevents confusing decoder VPP calls with inference preprocessing.

Use function_graph only for the VPP root and its admission/upload/CDMA/mutex/completion children. Enable named returns, monotonic timestamps and sleep-time; record original configuration and restore it in finally. No BPF/kprobes are available on this kernel. Two bounded captures during a120s formal window; concurrent status sampling continues independently during capture. Preserve trace setup/enable/disable/export clocks and CPU buffer loss statistics. Analyze only complete, internally consistent roots uniquely enclosed by a same-TID observed bm_trigger_vpp call.

Validation: smoke must preserve output and show driver submission counts; every API/driver return successful; no observer loss; raw graph and wrapper durations agree for paired calls; no negative stage remainders; report complete/matched fraction and discarded reasons. Compare throughput and API distributions during captures to neighboring untraced windows and prior same-configuration no-kernel-trace controls. Do not interpret invalid/perturbed samples as the original baseline or quietly discard failed runs.

Hypotheses to distinguish: H1 waiting before VPP admission; H2 CDMA wait while holding VPP slot; H3 after-start completion or driver work; H4 work/wait outside driver; H5 observer/trace artifacts. Different hardware/slots remain confounders until any dominant effect is checked on the same card at both rates.
