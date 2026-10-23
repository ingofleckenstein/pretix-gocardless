# Contributing

This is a pre-alpha payment integration with an experimental sandbox checkout. Please
discuss changes before implementing payment semantics and add tests for every
change. Do not use real payer information, bank details or credentials in tests,
issues or pull requests. Mock data and GoCardless sandbox only.

Run `python -m unittest discover -s tests -v` after installing Django and
requests. For disposable real-pretix/PostgreSQL tests, install
`pretix==2026.8.0`, `Django==5.2.17`, `psycopg[binary]`, `pgserver` and this
plugin, then run `python integration/run.py` on Linux/Python 3.12. It creates its own
temporary cluster. On Python 3.13, omit `pgserver` and explicitly supply a
disposable PostgreSQL server through `GC_TEST_POSTGRES_DSN`; the runner creates
and later drops a fresh random database on that server. It never migrates the
database named in the DSN. Neither mode reads operational pretix configuration
or calls an external payment API. CI uses a PostgreSQL 16 service on Python
3.12 and 3.13; configured CI jobs
are not evidence that those jobs have passed.

Changes involving payment or refund state require particular attention to
concurrency, retries, out-of-order events and accounting effects.
