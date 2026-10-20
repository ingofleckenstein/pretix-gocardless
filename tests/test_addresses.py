from types import SimpleNamespace
from unittest import TestCase
from pretix_gocardless.addresses import prefilled_address


class AddressTests(TestCase):
    def test_order_address_and_all_street_lines(self):
        address = SimpleNamespace(street="First 1\nSecond\nThird\nFourth", zipcode="12345", city="Berlin", state="BE", country="DE")
        order = SimpleNamespace(invoice_address=address, customer_id=None)
        self.assertEqual(prefilled_address(order), {"address_line1": "First 1", "address_line2": "Second",
            "address_line3": "Third\nFourth", "postal_code": "12345", "city": "Berlin", "region": "BE", "country_code": "DE"})

    def test_missing_fields_and_no_address_are_omitted(self):
        address = SimpleNamespace(street="\nFirst\n", zipcode="", city=None, state="", country="")
        self.assertEqual(prefilled_address(SimpleNamespace(invoice_address=address, customer_id=None)), {"address_line1": "First"})
        self.assertEqual(prefilled_address(SimpleNamespace(customer_id=None)), {})

    def test_one_profile_address_is_used_but_multiple_are_not_guessed(self):
        address = SimpleNamespace(street="Saved 1", country="DE")
        class Stored:
            def __init__(self, addresses): self.addresses = addresses
            def all(self): return self.addresses
        customer = SimpleNamespace(stored_addresses=Stored([address]))
        order = SimpleNamespace(customer_id=1, customer=customer)
        self.assertEqual(prefilled_address(order), {"address_line1": "Saved 1", "country_code": "DE"})
        customer.stored_addresses = Stored([address, address])
        self.assertEqual(prefilled_address(order), {})
        order.invoice_address = SimpleNamespace(street="Chosen 1")
        self.assertEqual(prefilled_address(order), {"address_line1": "Chosen 1"})

