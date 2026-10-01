from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import UserSerializer

User = get_user_model()


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        attrs[self.username_field] = attrs[self.username_field].strip().lower()
        data = super().validate(attrs)
        now = timezone.now()
        User.objects.filter(pk=self.user.pk).update(last_login=now)
        self.user.last_login = now
        data["user"] = UserSerializer(self.user, context=self.context).data
        return data


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)

    def validate_refresh(self, value):
        try:
            return RefreshToken(value)
        except TokenError as exc:
            raise serializers.ValidationError("Refresh token invalide ou expiré.") from exc

    def save(self, **kwargs):
        try:
            self.validated_data["refresh"].blacklist()
        except TokenError as exc:
            raise AuthenticationFailed("Refresh token invalide ou déjà révoqué.") from exc
