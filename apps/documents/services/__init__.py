"""
Document Management Building Block — Services package.

Public service API for the documents BB. All document operations (upload,
download, versioning, retention) must go through this layer — never directly
manipulate Document model fields from views or signal handlers.

Available service modules:
  upload     — presigned URL generation, upload confirmation, validation
  download   — access token issuance, presigned download URL generation
  versioning — new version creation, version chain queries
  retention  — expiry scheduling, soft-delete, hard-delete, legal hold
"""
