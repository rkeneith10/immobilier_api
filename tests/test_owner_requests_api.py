import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.notifications.models import Notification
from apps.users.models import OwnerProfile, OwnerRequest

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def test_users(db):
    return {
        "user": User.objects.create_user(
            email="regular-user@example.com",
            password=PASSWORD,
            role=User.Role.USER,
            first_name="Jean",
            last_name="Dupont",
            phone="+33612345678",
        ),
        "user2": User.objects.create_user(
            email="regular-user2@example.com",
            password=PASSWORD,
            role=User.Role.USER,
            first_name="Marie",
            last_name="Curie",
            phone="+33687654321",
        ),
        "owner": User.objects.create_user(
            email="existing-owner@example.com",
            password=PASSWORD,
            role=User.Role.OWNER,
        ),
        "admin": User.objects.create_user(
            email="admin-user@example.com",
            password=PASSWORD,
            role=User.Role.ADMIN,
        ),
    }


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


@pytest.mark.django_db
def test_user_creates_owner_request_and_admin_is_notified(test_users):
    """1. USER crée une demande & admin est notifié."""
    client = APIClient()
    user = test_users["user"]
    admin = test_users["admin"]
    auth(client, user)

    payload = {
        "full_name": "Jean Dupont",
        "phone": "+33612345678",
        "address": "123 Rue de la République, Paris",
        "message": "Je souhaite mettre en location mes appartements.",
    }
    response = client.post(reverse("users:owner-request-create"), payload, format="json")
    assert response.status_code == 201
    assert response.data["status"] == OwnerRequest.Status.PENDING
    assert response.data["request_type"] == OwnerRequest.RequestType.OWNER
    assert response.data["full_name"] == "Jean Dupont"
    assert response.data["phone"] == "+33612345678"
    assert response.data["address"] == "123 Rue de la République, Paris"
    assert response.data["message"] == "Je souhaite mettre en location mes appartements."
    assert response.data["rejection_reason"] == ""
    assert response.data["reviewed_at"] is None

    # Notification created for ADMIN
    admin_notif = Notification.objects.filter(
        user=admin,
        type=Notification.Type.NEW_OWNER_REQUEST,
    ).first()
    assert admin_notif is not None
    assert "Jean Dupont" in admin_notif.message


@pytest.mark.django_db
def test_user_consults_owner_request_status(test_users):
    """2. USER consulte sa demande et son statut."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)

    # Before creating: 400 with detail "Aucune demande trouvée."
    res_before = client.get(reverse("users:owner-request-me"))
    assert res_before.status_code == 400

    # Create request
    client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678"},
        format="json",
    )

    # Consult via /me/
    response = client.get(reverse("users:owner-request-me"))
    assert response.status_code == 200
    assert response.data["status"] == OwnerRequest.Status.PENDING
    assert response.data["full_name"] == "Jean Dupont"

    # Consult via list /owner-requests/
    list_response = client.get(reverse("users:owner-request-create"))
    assert list_response.status_code == 200
    assert len(list_response.data) == 1
    assert list_response.data[0]["status"] == OwnerRequest.Status.PENDING


@pytest.mark.django_db
def test_user_cannot_create_two_pending_requests(test_users):
    """3. USER ne peut pas créer deux demandes PENDING."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)

    # First request
    r1 = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678"},
        format="json",
    )
    assert r1.status_code == 201

    # Second request while first is still pending
    r2 = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678"},
        format="json",
    )
    assert r2.status_code == 400
    assert "déjà en cours" in str(r2.data)
    assert OwnerRequest.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_owner_and_admin_cannot_create_owner_request(test_users):
    """4. OWNER (et ADMIN) ne peut pas créer une demande OWNER."""
    client = APIClient()

    # Unauthenticated -> 401
    assert client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Anonyme", "phone": "+3300000000"},
        format="json",
    ).status_code == 401

    # OWNER -> 403
    auth(client, test_users["owner"])
    r_owner = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Owner Existing", "phone": "+3300000000"},
        format="json",
    )
    assert r_owner.status_code == 403

    # ADMIN -> 403
    auth(client, test_users["admin"])
    r_admin = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Admin User", "phone": "+3300000000"},
        format="json",
    )
    assert r_admin.status_code == 403


@pytest.mark.django_db
def test_user_cannot_tamper_with_server_controlled_fields(test_users):
    """Vérifier qu'un utilisateur ne peut pas s'auto-promouvoir ou forcer le statut APPROVED."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)

    # Attempt to send status="APPROVED" and reviewed_by
    response = client.post(
        reverse("users:owner-request-create"),
        {
            "full_name": "Attacker",
            "phone": "+33612345678",
            "status": "APPROVED",
            "reviewed_at": "2026-01-01T00:00:00Z",
        },
        format="json",
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.role == User.Role.USER


@pytest.mark.django_db
def test_admin_views_requests_list_and_detail(test_users):
    """5. ADMIN voit les demandes."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)
    client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678", "message": "Location"},
        format="json",
    )

    admin = test_users["admin"]
    auth(client, admin)

    response = client.get(reverse("admin_api:owner-request-list"))
    assert response.status_code == 200
    # Pagination results check
    results = response.data.get("results", response.data)
    assert len(results) == 1
    req_data = results[0]
    assert req_data["full_name"] == "Jean Dupont"
    assert req_data["user_email"] == user.email
    assert req_data["user_phone"] == user.phone
    assert req_data["status"] == OwnerRequest.Status.PENDING

    # Detail
    detail_res = client.get(reverse("admin_api:owner-request-detail", kwargs={"pk": req_data["id"]}))
    assert detail_res.status_code == 200
    assert detail_res.data["id"] == req_data["id"]
    assert detail_res.data["message"] == "Location"


@pytest.mark.django_db
def test_user_cannot_access_admin_endpoints(test_users):
    """6. USER ne peut pas accéder aux endpoints ADMIN."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)
    req = OwnerRequest.objects.create(
        user=user,
        full_name="Jean Dupont",
        phone="+33612345678",
        status=OwnerRequest.Status.PENDING,
    )

    # List
    assert client.get(reverse("admin_api:owner-request-list")).status_code == 403
    # Detail
    assert client.get(reverse("admin_api:owner-request-detail", kwargs={"pk": req.pk})).status_code == 403
    # Approve
    assert client.post(reverse("admin_api:owner-request-approve", kwargs={"pk": req.pk})).status_code == 403
    # Reject
    assert client.post(
        reverse("admin_api:owner-request-reject", kwargs={"pk": req.pk}),
        {"rejection_reason": "No"},
        format="json",
    ).status_code == 403


@pytest.mark.django_db
def test_admin_approves_request_flow(test_users):
    """7. ADMIN approuve une demande ;
       8. le rôle devient OWNER après approbation ;
       9. la demande devient APPROVED ;
       10. une notification est créée."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)
    create_res = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678"},
        format="json",
    )
    req_id = create_res.data["id"]

    admin = test_users["admin"]
    auth(client, admin)

    approve_res = client.post(reverse("admin_api:owner-request-approve", kwargs={"pk": req_id}))
    assert approve_res.status_code == 200
    assert approve_res.data["status"] == OwnerRequest.Status.APPROVED
    assert approve_res.data["reviewed_by_id"] == str(admin.pk)
    assert approve_res.data["reviewer_email"] == admin.email
    assert approve_res.data["reviewed_at"] is not None

    # Check user role promoted to OWNER
    user.refresh_from_db()
    assert user.role == User.Role.OWNER

    # Check request status APPROVED in db
    req = OwnerRequest.objects.get(pk=req_id)
    assert req.status == OwnerRequest.Status.APPROVED
    assert req.reviewed_by == admin
    assert req.reviewed_at is not None

    # Check OwnerProfile was auto-initialized
    assert OwnerProfile.objects.filter(user=user).exists()
    assert user.owner_profile.display_name == "Jean Dupont"

    # Check user received notification
    notif = Notification.objects.filter(
        user=user,
        type=Notification.Type.OWNER_REQUEST_APPROVED,
    ).first()
    assert notif is not None
    assert "Votre demande pour devenir propriétaire a été acceptée." in notif.message


@pytest.mark.django_db
def test_admin_rejects_request_flow(test_users):
    """11. ADMIN refuse une demande ;
       12. rejection_reason obligatoire ;
       13. le rôle reste USER après refus ;
       14. la demande devient REJECTED ;
       15. une notification de refus est créée."""
    client = APIClient()
    user = test_users["user"]
    auth(client, user)
    create_res = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678"},
        format="json",
    )
    req_id = create_res.data["id"]

    admin = test_users["admin"]
    auth(client, admin)

    # 12. rejection_reason obligatoire
    no_reason = client.post(
        reverse("admin_api:owner-request-reject", kwargs={"pk": req_id}),
        {"rejection_reason": "   "},
        format="json",
    )
    assert no_reason.status_code == 400
    assert "rejection_reason" in no_reason.data

    # Valid rejection
    reject_res = client.post(
        reverse("admin_api:owner-request-reject", kwargs={"pk": req_id}),
        {"rejection_reason": "Pièces justificatives incomplètes."},
        format="json",
    )
    assert reject_res.status_code == 200
    assert reject_res.data["status"] == OwnerRequest.Status.REJECTED
    assert reject_res.data["rejection_reason"] == "Pièces justificatives incomplètes."
    assert reject_res.data["reviewed_by_id"] == str(admin.pk)

    # 13. le rôle reste USER
    user.refresh_from_db()
    assert user.role == User.Role.USER

    # 14. la demande devient REJECTED
    req = OwnerRequest.objects.get(pk=req_id)
    assert req.status == OwnerRequest.Status.REJECTED
    assert req.rejection_reason == "Pièces justificatives incomplètes."

    # 15. notification de refus créée
    notif = Notification.objects.filter(
        user=user,
        type=Notification.Type.OWNER_REQUEST_REJECTED,
    ).first()
    assert notif is not None
    assert "Votre demande pour devenir propriétaire a été refusée" in notif.message
    assert "Pièces justificatives incomplètes." in notif.message


@pytest.mark.django_db
def test_cannot_approve_already_approved_request(test_users):
    """16. Une demande déjà APPROVED ne peut pas être approuvée une deuxième fois."""
    client = APIClient()
    user = test_users["user"]
    admin = test_users["admin"]

    req = OwnerRequest.objects.create(
        user=user,
        full_name="Jean Dupont",
        phone="+33612345678",
        status=OwnerRequest.Status.APPROVED,
        reviewed_by=admin,
    )

    auth(client, admin)
    response = client.post(reverse("admin_api:owner-request-approve", kwargs={"pk": req.pk}))
    assert response.status_code == 400
    assert "Seules les demandes en attente" in str(response.data)


@pytest.mark.django_db
def test_cannot_reject_already_rejected_request(test_users):
    """17. Une demande déjà REJECTED ne peut pas être refusée une deuxième fois."""
    client = APIClient()
    user = test_users["user"]
    admin = test_users["admin"]

    req = OwnerRequest.objects.create(
        user=user,
        full_name="Jean Dupont",
        phone="+33612345678",
        status=OwnerRequest.Status.REJECTED,
        rejection_reason="First rejection",
        reviewed_by=admin,
    )

    auth(client, admin)
    response = client.post(
        reverse("admin_api:owner-request-reject", kwargs={"pk": req.pk}),
        {"rejection_reason": "Second rejection attempt"},
        format="json",
    )
    assert response.status_code == 400
    assert "Seules les demandes en attente" in str(response.data)


@pytest.mark.django_db
def test_user_can_resubmit_after_rejection(test_users):
    """Vérifier qu'après une demande REJECTED, l'utilisateur peut soumettre une nouvelle demande."""
    client = APIClient()
    user = test_users["user"]
    admin = test_users["admin"]
    auth(client, user)

    # 1. First request
    r1 = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678"},
        format="json",
    )
    req1_id = r1.data["id"]

    # 2. Admin rejects it
    auth(client, admin)
    client.post(
        reverse("admin_api:owner-request-reject", kwargs={"pk": req1_id}),
        {"rejection_reason": "Manque de précisions"},
        format="json",
    )

    # 3. User checks their status: sees REJECTED with reason
    auth(client, user)
    status_res = client.get(reverse("users:owner-request-me"))
    assert status_res.status_code == 200
    assert status_res.data["status"] == OwnerRequest.Status.REJECTED
    assert status_res.data["rejection_reason"] == "Manque de précisions"

    # 4. User submits a new request with updated message
    r2 = client.post(
        reverse("users:owner-request-create"),
        {"full_name": "Jean Dupont", "phone": "+33612345678", "message": "Voici les précisions demandées."},
        format="json",
    )
    assert r2.status_code == 201
    assert r2.data["status"] == OwnerRequest.Status.PENDING
    assert r2.data["id"] != req1_id

    # 5. Database now contains 2 requests: 1 REJECTED, 1 PENDING
    assert OwnerRequest.objects.filter(user=user).count() == 2
    assert OwnerRequest.objects.filter(user=user, status=OwnerRequest.Status.PENDING).count() == 1
