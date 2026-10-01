from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import OwnerProfile, OwnerVerification
from .owner_permissions import IsAdminUserRole, IsOwnerOrAgent
from .owner_serializers import (
    OwnerProfileSerializer, OwnerVerificationReviewSerializer, OwnerVerificationSerializer,
)
from .services import review_owner_verification, submit_owner_verification


@extend_schema_view(
    get=extend_schema(tags=["Owner profiles"], summary="Consulter son profil propriétaire"),
    post=extend_schema(tags=["Owner profiles"], summary="Créer son profil propriétaire"),
    patch=extend_schema(tags=["Owner profiles"], summary="Modifier son profil propriétaire"),
)
class OwnerProfileView(generics.GenericAPIView):
    serializer_class = OwnerProfileSerializer
    permission_classes = (IsOwnerOrAgent,)

    def get(self, request):
        profile = get_object_or_404(OwnerProfile, user=request.user)
        return Response(self.get_serializer(profile).data)

    def post(self, request):
        if OwnerProfile.objects.filter(user=request.user).exists():
            raise ValidationError({"detail": "Un profil propriétaire existe déjà."})
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save(user=request.user)
        return Response(self.get_serializer(profile).data, status=status.HTTP_201_CREATED)

    def patch(self, request):
        profile = get_object_or_404(OwnerProfile, user=request.user)
        serializer = self.get_serializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return Response(self.get_serializer(profile).data)


@extend_schema(tags=["Owner verification"], summary="Soumettre une demande de vérification", request=None, responses={201: OwnerVerificationSerializer})
class OwnerVerificationSubmitView(generics.GenericAPIView):
    serializer_class = OwnerVerificationSerializer
    permission_classes = (IsOwnerOrAgent,)

    def post(self, request):
        verification = submit_owner_verification(user=request.user)
        return Response(self.get_serializer(verification).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Owner verification"], summary="Lister les demandes de vérification (ADMIN)")
class OwnerVerificationListView(generics.ListAPIView):
    serializer_class = OwnerVerificationSerializer
    permission_classes = (IsAdminUserRole,)
    queryset = OwnerVerification.objects.select_related("owner_profile", "owner_profile__user", "reviewed_by")
    filterset_fields = ("status",)
    ordering_fields = ("created_at", "reviewed_at")
    ordering = ("-created_at",)


@extend_schema_view(
    get=extend_schema(tags=["Owner verification"], summary="Consulter une demande de vérification (ADMIN)", responses=OwnerVerificationSerializer),
    patch=extend_schema(tags=["Owner verification"], summary="Approuver ou rejeter une demande (ADMIN)", request=OwnerVerificationReviewSerializer, responses={200: OwnerVerificationSerializer}),
)
class OwnerVerificationReviewView(generics.GenericAPIView):
    serializer_class = OwnerVerificationReviewSerializer
    permission_classes = (IsAdminUserRole,)

    def get(self, request, pk):
        verification = get_object_or_404(
            OwnerVerification.objects.select_related("owner_profile", "owner_profile__user", "reviewed_by"),
            pk=pk,
        )
        return Response(OwnerVerificationSerializer(verification, context=self.get_serializer_context()).data)

    def patch(self, request, pk):
        verification = get_object_or_404(OwnerVerification.objects.select_related("owner_profile"), pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verification = review_owner_verification(
            verification_id=verification.pk,
            reviewer=request.user,
            status=serializer.validated_data["status"],
            rejection_reason=serializer.validated_data.get("rejection_reason", ""),
        )
        return Response(OwnerVerificationSerializer(verification, context=self.get_serializer_context()).data)
