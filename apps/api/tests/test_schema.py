from django.test import SimpleTestCase, TestCase
from drf_spectacular.generators import SchemaGenerator

from apps.api.schema import GovStackCitizenAuthScheme, GovStackSchedulerAuthScheme


class GovStackSchemaExtensionTests(SimpleTestCase):
    def test_scheduler_auth_documents_required_query_credential(self):
        definition = GovStackSchedulerAuthScheme.__new__(
            GovStackSchedulerAuthScheme
        ).get_security_definition(None)

        self.assertEqual(definition["type"], "apiKey")
        self.assertEqual(definition["in"], "query")
        self.assertEqual(definition["name"], "request_token")
        self.assertIn("requestor_id", definition["description"])

    def test_citizen_auth_documents_scheduler_credential_and_optional_jwt(self):
        definition = GovStackCitizenAuthScheme.__new__(
            GovStackCitizenAuthScheme
        ).get_security_definition(None)

        self.assertEqual(definition["type"], "apiKey")
        self.assertEqual(definition["in"], "query")
        self.assertEqual(definition["name"], "request_token")
        self.assertIn("requestor_id", definition["description"])
        self.assertIn("Authorization Bearer JWT", definition["description"])

    def test_extensions_target_runtime_authenticator_classes(self):
        self.assertEqual(
            self._target_path(GovStackSchedulerAuthScheme.target_class),
            "apps.appointments.govstack_auth.GovStackSchedulerAuth",
        )
        self.assertEqual(
            self._target_path(GovStackCitizenAuthScheme.target_class),
            "apps.appointments.govstack_auth.GovStackCitizenAuth",
        )

    @staticmethod
    def _target_path(target):
        if isinstance(target, str):
            return target
        return f"{target.__module__}.{target.__name__}"


class GovStackGeneratedSchemaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def _protected_operation(self, marker, scheme):
        for path, path_item in self.schema["paths"].items():
            if marker not in path:
                continue
            for method, operation in path_item.items():
                if method in {"get", "post", "put", "patch", "delete"} and any(
                    scheme in requirement for requirement in operation.get("security", [])
                ):
                    return operation
        self.fail(f"No {scheme} operation found for {marker}")

    def test_scheduler_operation_exposes_required_bb_pair(self):
        operation = self._protected_operation("govstack/scheduler", "GovStackSchedulerAuth")
        parameters = {item["name"]: item for item in operation["parameters"]}
        self.assertTrue(parameters["requestor_id"]["required"])
        self.assertTrue(parameters["request_token"]["required"])

    def test_citizen_operation_exposes_pair_and_jwt_behavior(self):
        operation = self._protected_operation("govstack/scheduler", "GovStackCitizenAuth")
        parameters = {item["name"]: item for item in operation["parameters"]}
        self.assertTrue(parameters["requestor_id"]["required"])
        self.assertTrue(parameters["request_token"]["required"])
        self.assertIn(
            "Bearer JWT",
            self.schema["components"]["securitySchemes"]["GovStackCitizenAuth"]["description"],
        )
