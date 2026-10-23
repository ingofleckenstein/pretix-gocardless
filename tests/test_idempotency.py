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

    def test_hosted_flow_generation_preserves_old_key_and_separates_renewal(self):
        seed = uuid4()
        self.assertEqual(operation_key(seed, "hosted-flow"), operation_key(seed, "hosted-flow", 0))
        self.assertNotEqual(operation_key(seed, "hosted-flow", 0), operation_key(seed, "hosted-flow", 1))
        self.assertEqual(operation_key(seed, "hosted-flow", 1), operation_key(seed, "hosted-flow", 1))

    def test_invalid_generation_is_rejected(self):
        for purpose, generation in (("hosted-flow", -1), ("hosted-flow", True),
                                    ("debit-payment", 1), ("hosted-flow", "1")):
            with self.subTest(purpose=purpose, generation=generation), self.assertRaises(ValueError):
                operation_key(uuid4(), purpose, generation)
