match: \bcontain|containment|remediat|respond to the incident
Go through the policy rule by rule. For each rule make ONE tool call that ends in a list result (a line "-- r1 = these N values"):
- who is affected: {"action":"table","arguments":{"document":"<log>","operation":"distinct","column":"<actor column>","where":{"<action column>":"<trigger action>"}}}
- what they did afterwards: {"action":"after","arguments":{"document":"<log>","key":"<actor column>","first":{"<action column>":"<trigger action>"},"where":{"<action column>":"<action the rule names>"},"operation":"distinct","column":"<column that holds the thing to act on>"}}
The example row under a result shows which column holds the thing to act on; it is often "target", not the column named like the thing.
Finish with one action per rule and never type targets by hand:
{"action":"final","result":{"status":"ok","answer":"contained","evidence":["r1","r2"],"actions":[{"type":"<action type 1>","targets_from":"r1"},{"type":"<action type 2>","targets_from":"r2, r3"}]}}
