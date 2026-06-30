# Deployment Notes

Pre-deploy actions that must be completed manually before or after specific migrations run.
Check each item off as you go. Remove entries once confirmed done in production.

---

## Wave 9 — `0004_wave9_fixes` migration

### ⚠️ Re-enter webhook secrets after migrating (required)

**Why**: `TenantPaymentConfig.webhook_endpoint_secret` was changed from a plaintext
`CharField` to a Fernet-encrypted `EncryptedCharField` (AES-128-CBC). Existing rows
stored as plaintext cannot be decrypted by the new field — they will silently return
an empty string until re-entered.

**Steps (do this during the deploy window):**

1. **Before `migrate`** — retrieve all current webhook secrets. Either:
   - Django admin → TenantPaymentConfig → note the `webhook_endpoint_secret` for each row, or
   - Stripe Dashboard → Developers → Webhooks → [your endpoint] → "Signing secret" (click Reveal)

2. **Set `FERNET_KEYS`** in your production environment (`.env` / secrets manager):
   ```bash
   # Generate a proper Fernet key (one-time, store securely):
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   # Add to environment:
   FERNET_KEYS=<the-generated-key>
   ```
   > If `FERNET_KEYS` is not set, the field falls back to `SECRET_KEY`. That works but
   > is not recommended for production — use a dedicated key.

3. **Run `python manage.py migrate`** — the column changes from `VARCHAR` to `BYTEA`.

4. **Re-enter each webhook secret** in Django admin → TenantPaymentConfig → save each row.
   The field encrypts transparently on save.

5. **Verify** the Stripe webhook endpoint receives and processes a test event successfully.

**Key rotation (future):** Prepend the new key to `FERNET_KEYS`:
```
FERNET_KEYS=new_key,old_key
```
`MultiFernet` tries keys in order — old ciphertexts still decrypt during the transition.
Once all rows have been re-saved under the new key, remove the old key from the list.

---
