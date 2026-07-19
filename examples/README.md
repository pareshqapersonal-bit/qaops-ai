# Golden example requirements

Permanent regression fixtures for the Test Design pipeline. Each document is a
realistic requirement spec that **deliberately contains gaps** (unspecified
limits, undefined failure behavior, missing permissions) so it exercises the
GapAnalyzer as well as extraction. Do not "fix" the gaps - they are the point.
Future phases reuse these files for scenario/test-case generation and for
live-eval quality review (`pytest -m llm`).

| File | Domain | Notable deliberate gaps |
|---|---|---|
| `login.md` | Authentication | password rules, lockout duration, session timeout |
| `checkout.md` | E-commerce checkout | payment failure behavior, stock race, address validation |
| `video_playback.md` | OTT streaming | buffering thresholds, concurrent-stream conflict, offline behavior |
| `fund_transfer.md` | Banking | daily limit currency handling, timeout mid-transfer, beneficiary validation |
