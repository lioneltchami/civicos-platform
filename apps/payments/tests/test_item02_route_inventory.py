from django.test import SimpleTestCase

from apps.payments.resolver_inventory import build_payment_route_inventory, mutation_routes


class PaymentRouteInventoryTests(SimpleTestCase):
    def test_inventory_includes_govstack_and_internal_payments_surfaces(self):
        routes = build_payment_route_inventory()
        paths = {route.full_route for route in routes}
        self.assertTrue(any(path.startswith("govstack/payments/") for path in paths))
        self.assertTrue(any(path.startswith("payments/") for path in paths))

    def test_all_mutations_are_explicitly_discoverable(self):
        routes = mutation_routes()
        self.assertTrue(routes)
        self.assertTrue(all(route.methods & {"POST", "PUT", "PATCH", "DELETE"} for route in routes))
        names = {route.name for route in routes}
        self.assertIn("bulk_payment", names)
