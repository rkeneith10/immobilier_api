from django.core.management.base import BaseCommand
from apps.monetization.services import sync_all_pending_moncash_payments


class Command(BaseCommand):
    help = (
        "Interroge l'API MonCash (RetrieveOrderPayment) pour tous les paiements PENDING "
        "et met à jour leur statut (PAID/FAILED) de manière atomique et idempotente."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age-hours",
            type=int,
            default=48,
            help="Âge maximal des paiements PENDING à vérifier (défaut: 48h)",
        )
        parser.add_argument(
            "--min-age-seconds",
            type=int,
            default=30,
            help="Délai minimal avant vérification (défaut: 30s pour laisser le client payer)",
        )

    def handle(self, *args, **options):
        max_age_hours = options["max_age_hours"]
        min_age_seconds = options["min_age_seconds"]

        self.stdout.write(
            self.style.NOTICE(
                f"Synchronisation des paiements MonCash PENDING (âge: {min_age_seconds}s à {max_age_hours}h)..."
            )
        )

        stats = sync_all_pending_moncash_payments(
            max_age_hours=max_age_hours,
            min_age_seconds=min_age_seconds,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Synchronisation terminée :\n"
                f" - Total vérifiés : {stats['total_checked']}\n"
                f" - Confirmés (PAID) : {stats['paid']}\n"
                f" - Rejetés (FAILED) : {stats['failed']}\n"
                f" - En attente : {stats['still_pending']}\n"
                f" - Erreurs réseau : {stats['errors']}"
            )
        )
