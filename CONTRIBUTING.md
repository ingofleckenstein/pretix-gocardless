# Contributing

This is a pre-alpha payment integration with an experimental sandbox checkout. Please
discuss changes before implementing payment semantics and add tests for every
change. Do not use real payer information, bank details or credentials in tests,
issues or pull requests. Mock data and GoCardless sandbox only.

Run `python -m unittest discover -s tests -v` after installing Django and
requests. For disposable real-pretix/PostgreSQL tests, install
`pretix==2026.8.0`, `Django==5.2.17`, `psycopg[binary]`, `pgserver` and this
plugin, then run `python integration/run.py` on Linux. It creates its own
temporary cluster, never reads operational pretix configuration, and uses no
external payment API. CI runs this on Python 3.12 and 3.13; configured CI jobs
are not evidence that those jobs have passed.

Changes involving payment or refund state require particular attention to
concurrency, retries, out-of-order events and accounting effects.
