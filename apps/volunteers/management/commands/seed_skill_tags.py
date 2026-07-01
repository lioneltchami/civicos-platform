"""
Management command: seed_skill_tags
====================================
Populates the SkillTag table with a canonical set of bilingual (EN/FR) skill
tags grouped by category.  Safe to run multiple times (idempotent via
get_or_create on slug).

Usage::

    python manage.py seed_skill_tags
    python manage.py seed_skill_tags --clear   # drop existing first

Categories mirror Volunteer Canada CCVI competency domains:
  - interpersonal
  - communications
  - technical
  - health_safety
  - digital
  - administrative
  - leadership
  - languages
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.volunteers.models import SkillTag

# ---------------------------------------------------------------------------
# Canonical skill tag fixtures
# Format: (name_en, name_fr, category)
# ---------------------------------------------------------------------------
SKILL_TAGS: list[tuple[str, str, str]] = [
    # --- Interpersonal -------------------------------------------------------
    ("Active Listening", "Écoute active", "interpersonal"),
    ("Empathy", "Empathie", "interpersonal"),
    ("Conflict Resolution", "Résolution de conflits", "interpersonal"),
    ("Cultural Competency", "Compétence culturelle", "interpersonal"),
    ("Team Collaboration", "Collaboration en équipe", "interpersonal"),
    ("Mentoring", "Mentorat", "interpersonal"),
    ("Patience", "Patience", "interpersonal"),
    # --- Communications ------------------------------------------------------
    ("Public Speaking", "Prise de parole en public", "communications"),
    ("Copywriting", "Rédaction", "communications"),
    ("Social Media", "Médias sociaux", "communications"),
    ("Translation / Interpretation", "Traduction / Interprétation", "communications"),
    ("Newsletter Writing", "Rédaction de bulletins", "communications"),
    ("Media Relations", "Relations avec les médias", "communications"),
    # --- Technical -----------------------------------------------------------
    ("Carpentry", "Charpenterie", "technical"),
    ("Plumbing", "Plomberie", "technical"),
    ("Electrical (basic)", "Électricité (de base)", "technical"),
    ("Painting / Finishing", "Peinture / Finition", "technical"),
    ("Moving & Heavy Lifting", "Déménagement et charges lourdes", "technical"),
    ("Vehicle Operation", "Conduite de véhicule", "technical"),
    ("Forklift Operation", "Conduite de chariot élévateur", "technical"),
    ("Audio / Visual Setup", "Installation audiovisuelle", "technical"),
    # --- Health & Safety -----------------------------------------------------
    ("First Aid (Standard)", "Premiers secours (standard)", "health_safety"),
    ("First Aid (Advanced)", "Premiers secours (avancé)", "health_safety"),
    ("CPR / AED", "RCR / DEA", "health_safety"),
    ("WHMIS", "SIMDUT", "health_safety"),
    ("Food Handler Certified", "Certificat manipulation des aliments", "health_safety"),
    ("Mental Health First Aid", "Premiers secours en santé mentale", "health_safety"),
    ("Safe Talk (Suicide Prevention)", "Safe Talk (prévention du suicide)", "health_safety"),
    ("NVCI / Non-Violent Crisis Intervention", "NVCI / Intervention en crise non violente", "health_safety"),
    # --- Digital -------------------------------------------------------------
    ("Microsoft Office", "Microsoft Office", "digital"),
    ("Google Workspace", "Google Workspace", "digital"),
    ("Data Entry", "Saisie de données", "digital"),
    ("Database Management", "Gestion de bases de données", "digital"),
    ("Web Development", "Développement web", "digital"),
    ("Graphic Design", "Design graphique", "digital"),
    ("Photography", "Photographie", "digital"),
    ("Videography / Video Editing", "Vidéographie / Montage vidéo", "digital"),
    ("Cybersecurity Awareness", "Sensibilisation à la cybersécurité", "digital"),
    # --- Administrative / Organizational ------------------------------------
    ("Event Planning", "Organisation d'événements", "administrative"),
    ("Bookkeeping / Accounting", "Comptabilité / Tenue de livres", "administrative"),
    ("Grant Writing", "Rédaction de demandes de subvention", "administrative"),
    ("Project Management", "Gestion de projet", "administrative"),
    ("Fundraising", "Collecte de fonds", "administrative"),
    ("Legal / Paralegal", "Juridique / Parajuridique", "administrative"),
    ("Human Resources", "Ressources humaines", "administrative"),
    ("Research / Analysis", "Recherche / Analyse", "administrative"),
    # --- Leadership ----------------------------------------------------------
    ("Group Facilitation", "Animation de groupes", "leadership"),
    ("Volunteer Coordination", "Coordination de bénévoles", "leadership"),
    ("Training / Coaching", "Formation / Coaching", "leadership"),
    ("Community Organizing", "Organisation communautaire", "leadership"),
    # --- Languages -----------------------------------------------------------
    ("English", "Anglais", "languages"),
    ("French / Français", "Français / French", "languages"),
    ("Spanish / Español", "Espagnol / Español", "languages"),
    ("Arabic / العربية", "Arabe / العربية", "languages"),
    ("Mandarin / 普通话", "Mandarin / 普通话", "languages"),
    ("Cantonese / 廣東話", "Cantonais / 廣東話", "languages"),
    ("Punjabi / ਪੰਜਾਬੀ", "Pendjabi / ਪੰਜਾਬੀ", "languages"),
    ("Tagalog", "Tagalog", "languages"),
    ("Hindi / हिंदी", "Hindi / हिंदी", "languages"),
    ("Portuguese / Português", "Portugais / Português", "languages"),
    ("Italian / Italiano", "Italien / Italiano", "languages"),
    ("Ukrainian / Українська", "Ukrainien / Українська", "languages"),
    ("ASL / American Sign Language", "ASL / Langue des signes américaine", "languages"),
    ("LSQ / Langue des signes québécoise", "LSQ / Langue des signes québécoise", "languages"),
    ("Indigenous Language", "Langue autochtone", "languages"),
]


class Command(BaseCommand):
    help = "Seed the SkillTag table with canonical bilingual skill tags (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing SkillTag records before seeding.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            count, _ = SkillTag.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {count} existing SkillTag records."))

        created_count = 0
        updated_count = 0

        for name_en, name_fr, category in SKILL_TAGS:
            slug = slugify(name_en)
            obj, created = SkillTag.objects.get_or_create(
                slug=slug,
                defaults={
                    "name_en": name_en,
                    "name_fr": name_fr,
                    "category": category,
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
            else:
                # Sync bilingual names and category in case fixture changed.
                changed = False
                if obj.name_en != name_en:
                    obj.name_en = name_en
                    changed = True
                if obj.name_fr != name_fr:
                    obj.name_fr = name_fr
                    changed = True
                if obj.category != category:
                    obj.category = category
                    changed = True
                if changed:
                    obj.save(update_fields=["name_en", "name_fr", "category"])
                    updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"SkillTag seed complete: {created_count} created, "
                f"{updated_count} updated, "
                f"{len(SKILL_TAGS) - created_count - updated_count} unchanged."
            )
        )
