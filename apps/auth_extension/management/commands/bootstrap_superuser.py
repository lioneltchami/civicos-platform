"""Create or update a development superuser account."""

from __future__ import annotations

import os
import secrets
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Create or update a superuser in a non-interactive way. "
        "Uses DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD when set."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--email",
            default=os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@govstack.local"),
            help="Email address for the superuser.",
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("DJANGO_SUPERUSER_PASSWORD"),
            help="Password for the superuser. If omitted, a temporary password is generated.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        email = options["email"].strip()
        if not email:
            raise CommandError("A superuser email address is required.")

        password = options["password"]
        user_model = get_user_model()

        with transaction.atomic():
            user, created = user_model.objects.get_or_create(
                email=email,
                defaults={"is_staff": True, "is_superuser": True, "is_active": True},
            )

            user.is_staff = True
            user.is_superuser = True
            user.is_active = True

            generated_password = None
            if created:
                generated_password = password or secrets.token_urlsafe(18)
                user.set_password(generated_password)
            elif password:
                user.set_password(password)
            elif not user.has_usable_password():
                generated_password = secrets.token_urlsafe(18)
                user.set_password(generated_password)

            update_fields = ["is_staff", "is_superuser", "is_active"]
            if generated_password is not None or password:
                update_fields.append("password")
            user.save(update_fields=update_fields)

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} superuser: {email}"))

        if generated_password is not None:
            self.stdout.write(
                self.style.WARNING(
                    "No DJANGO_SUPERUSER_PASSWORD was set, so a temporary password was generated."
                )
            )
            self.stdout.write(self.style.WARNING(f"Temporary password: {generated_password}"))
