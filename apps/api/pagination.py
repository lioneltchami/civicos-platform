"""
Standard pagination for the Govstack API.

All list endpoints use StandardPagination by default (configured in
DEFAULT_PAGINATION_CLASS in settings). Consumers can control page size
via the ``page_size`` query parameter up to ``max_page_size``.
"""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """
    Page-number–based pagination with a sensible default and a hard cap.

    Response envelope:
        {
            "count": <total items>,
            "next": "<url or null>",
            "previous": "<url or null>",
            "results": [...]
        }
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        """OpenAPI schema for the paginated envelope (used by drf-spectacular)."""
        return {
            "type": "object",
            "required": ["count", "results"],
            "properties": {
                "count": {"type": "integer"},
                "next": {"type": "string", "nullable": True},
                "previous": {"type": "string", "nullable": True},
                "results": schema,
            },
        }
