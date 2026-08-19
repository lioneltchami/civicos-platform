# File Management operation coverage matrix

This is a source-level mapping for the scoped local runner. Module presence is not a pass claim; current pass evidence is produced only by executing the runner.

| Operation | Happy-path node(s) | Failure/rejection node(s) | Layer |
|---|---|---|---|
| Upload/create | `test_services_upload.py`, `test_upload_presign_template.py` | `test_upload_content_gating.py`, `test_wave3_clamav.py` | unit/integration |
| Read/metadata | `test_models.py`, `test_api.py` | `test_api.py`, `test_views_http_contract.py` | unit/integration |
| List/search | `test_api.py`, `test_views_citizen.py`, `test_views_staff.py` | `test_views_citizen.py`, `test_views_staff.py` | integration |
| Download/presign | `test_wave3_download.py`, `test_access_tokens.py` | `test_access_tokens.py`, `test_wave3_download.py` | integration |
| Attach/reference | `test_signals.py`, `test_services_versioning.py` | `test_signals.py` | unit/integration |
| Version | `test_services_versioning.py` | `test_services_versioning.py` | unit |
| Delete/soft-delete | `test_models.py`, `test_api.py` | `test_models.py`, `test_api.py` | unit/integration |
| Scan/promotion | `test_scan_audit_and_promotion.py`, `test_tasks.py` | `test_upload_content_gating.py`, `test_wave3_clamav.py`, `test_tasks.py` | integration |
| Quarantine/unavailable | `test_scan_audit_and_promotion.py`, `test_api.py` | `test_scan_audit_and_promotion.py`, `test_views_http_contract.py` | integration |
| Retention/expiry | `test_services_retention.py`, `test_wave4_retention.py` | `test_services_retention.py`, `test_wave4_retention.py` | unit/integration |
| Audit | `test_audit.py`, `test_scan_audit_and_promotion.py` | `test_audit.py` | integration |
| Accessibility/view contract | `test_wcag.py`, `test_wave5_views.py` | `test_views_http_contract.py` | e2e/integration |

The matrix deliberately points to existing project tests rather than inventing duplicate generic test behavior. Reviewers should update both the matrix and the guide when adding a new operation or layer.
