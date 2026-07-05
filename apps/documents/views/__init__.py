"""
apps/documents/views/__init__.py
================================
View package for the Document Management Building Block.

Sub-modules
-----------
citizen
    Views available to authenticated citizens (their own documents only).
staff
    Views restricted to staff with explicit model-level permissions.

Security invariants (apply to ALL views in this package):
---------------------------------------------------------
1.  ``storage_key`` is NEVER placed in template context, JSON responses,
    HTTP headers, or log messages.
2.  ``original_filename`` is NEVER written to audit ``event_detail``
    (potential PII — filenames can contain personal names).
3.  Citizens receive HTTP 404 (not 403) for document PKs they do not own,
    preventing IDOR enumeration.
4.  Only ``scan_status == ACTIVE`` documents are downloadable by citizens.
5.  ``LoginRequiredMixin`` always precedes ``PermissionRequiredMixin`` in the
    class MRO so unauthenticated requests are redirected to login rather than
    served a 403.
6.  All staff views set ``raise_exception = True`` on
    ``PermissionRequiredMixin`` to return 403 (not redirect) for authenticated
    users who lack the required permission.
7.  PII (names, email addresses) is NEVER written to log records — only
    ``pk`` values.
"""
