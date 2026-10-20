from unittest import TestCase
from uuid import uuid4

from pretix_gocardless.idempotency import operation_key


class IdempotencyKeyTests(TestCase):
    def test_stable_and_separate_for_each_operation(self):
        seed = uuid4()
        purposes = ("mandate-request", "hosted-flow", "debit-payment")
        keys = [operation_key(seed, purpose) for purpose in purposes]
        self.assertEqual(len(set(keys)), len(purposes))
        self.assertEqual(keys, [operation_key(seed, purpose) for purpose in purposes])
        self.assertNotEqual(keys[0], operation_key(uuid4(), purposes[0]))

    def test_invalid_purpose_or_seed_is_rejected(self):
        for seed, purpose in ((uuid4(), "other"), ("not-a-uuid", "debit-payment")):
            with self.subTest(seed=seed, purpose=purpose), self.assertRaises(ValueError):
                operation_key(seed, purpose)
