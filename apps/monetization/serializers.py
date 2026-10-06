from rest_framework import serializers


class PublicationEligibilitySerializer(serializers.Serializer):
    property_id = serializers.UUIDField(help_text="Identifiant de la propriété")
    is_free = serializers.BooleanField(help_text="La publication est-elle gratuite pour cette annonce ?")
    requires_payment = serializers.BooleanField(help_text="Un paiement est-il requis avant soumission ?")
    already_paid = serializers.BooleanField(help_text="Cette annonce a-t-elle déjà un paiement confirmé ?")
    free_publications_limit = serializers.IntegerField(help_text="Nombre total de publications gratuites allouées")
    free_publications_consumed = serializers.IntegerField(help_text="Nombre de publications gratuites déjà utilisées")
    free_remaining = serializers.IntegerField(help_text="Nombre de publications gratuites restantes")
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        allow_null=True,
        help_text="Montant requis (null si gratuit ou prix non configuré)",
    )
    currency = serializers.CharField(max_length=3, help_text="Devise du paiement (ex: HTG)")


class PublicationPaymentInitiateResponseSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField(help_text="Identifiant unique du paiement interne")
    status = serializers.CharField(help_text="Statut actuel du paiement (ex: PENDING)")
    order_id = serializers.CharField(help_text="Référence unique de commande transmise à la passerelle")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="Montant en gourdes (HTG)")
    currency = serializers.CharField(max_length=3, help_text="Devise du paiement (HTG)")
    provider = serializers.CharField(help_text="Fournisseur de paiement (KOBARA ou MONCASH)")
    redirect_url = serializers.URLField(help_text="URL de paiement sécurisée pour redirection de l’utilisateur")


class PublicationPaymentVerifyResponseSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField(help_text="Identifiant unique du paiement interne")
    property_id = serializers.UUIDField(help_text="Identifiant unique de la propriété")
    status = serializers.CharField(help_text="Statut actuel du paiement (ex: PAID, PENDING, FAILED)")
    order_id = serializers.CharField(help_text="Référence unique de commande")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="Montant en gourdes (HTG)")
    currency = serializers.CharField(max_length=3, help_text="Devise du paiement (HTG)")
    provider = serializers.CharField(help_text="Fournisseur de paiement (KOBARA ou MONCASH)")
    paid_at = serializers.DateTimeField(allow_null=True, help_text="Date et heure de confirmation du paiement")
    provider_transaction_id = serializers.CharField(
        allow_null=True, required=False, default=None, help_text="Numéro de transaction officiel de la passerelle"
    )


class MonCashWebhookPayloadSerializer(serializers.Serializer):
    orderId = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Identifiant de commande transmis à MonCash (camelCase)",
    )
    order_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Identifiant de commande transmis à MonCash (snake_case)",
    )
    transactionId = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Identifiant de transaction MonCash (camelCase)",
    )
    transaction_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Identifiant de transaction MonCash (snake_case)",
    )


class MonCashWebhookResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="Message descriptif du résultat")
    status = serializers.CharField(help_text="Statut actuel du paiement (PAID, PENDING, FAILED)")
    order_id = serializers.CharField(help_text="Référence de commande MonCash")
    payment_id = serializers.UUIDField(help_text="Identifiant UUID interne du paiement")
    property_id = serializers.UUIDField(
        required=False, allow_null=True, help_text="Identifiant de la propriété associée"
    )


class KobaraWebhookPayloadSerializer(serializers.Serializer):
    event_type = serializers.CharField(help_text="Type d'événement Kobara (ex: payment.succeeded)")
    data = serializers.DictField(help_text="Données de l'événement Kobara")


class KobaraWebhookResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="Message descriptif du résultat")
    status = serializers.CharField(required=False, help_text="Statut actuel du paiement (PAID, PENDING, FAILED)")
    order_id = serializers.CharField(required=False, help_text="Référence de commande")
    payment_id = serializers.UUIDField(required=False, help_text="Identifiant UUID interne du paiement")
    property_id = serializers.UUIDField(
        required=False, allow_null=True, help_text="Identifiant de la propriété associée"
    )



