# Contributing

This is a pre-alpha payment integration, not yet usable for checkout. Please
discuss changes before implementing payment semantics and add tests for every
change. Do not use real payer information, bank details or credentials in tests,
issues or pull requests. Mock data and GoCardless sandbox only.

Run `python -m unittest discover -s tests -v` after installing Django and
requests. Integration tests and a supported pretix version matrix remain to be
added. Changes involving payment or refund state require particular attention to
concurrency, retries, out-of-order events and accounting effects.
