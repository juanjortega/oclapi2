import uuid

from django.contrib.auth.models import update_last_login
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import mixins, status
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.constants import NOT_FOUND, MUST_SPECIFY_EXTRA_PARAM_IN_BODY, LAST_LOGIN_SINCE_PARAM, \
    LAST_LOGIN_BEFORE_PARAM, DATE_JOINED_SINCE_PARAM, DATE_JOINED_BEFORE_PARAM
from core.common.exceptions import Http400
from core.common.mixins import ListWithHeadersMixin
from core.common.swagger_parameters import last_login_before_param, last_login_since_param, updated_since_param, \
    date_joined_since_param, date_joined_before_param
from core.common.utils import parse_updated_since_param, parse_updated_since
from core.common.views import BaseAPIView, BaseLogoView
from core.orgs.models import Organization
from core.users.constants import VERIFICATION_TOKEN_MISMATCH, VERIFY_EMAIL_MESSAGE, REACTIVATE_USER_MESSAGE
from core.users.documents import UserProfileDocument
from core.users.search import UserProfileSearch
from core.users.serializers import UserDetailSerializer, UserCreateSerializer, UserListSerializer, UserSummarySerializer
from .models import UserProfile


class TokenAuthenticationView(ObtainAuthToken):
    """Implementation of ObtainAuthToken with last_login update"""

    @swagger_auto_schema(request_body=AuthTokenSerializer)
    def post(self, request, *args, **kwargs):
        user = UserProfile.objects.filter(username=request.data.get('username')).first()
        if not user or not user.check_password(request.data.get('password')):
            raise Http400(dict(non_field_errors=["Unable to log in with provided credentials."]))

        if not user.is_active:
            user.verify()
            return Response(
                {'detail': REACTIVATE_USER_MESSAGE, 'email': user.email}, status=status.HTTP_401_UNAUTHORIZED
            )
        if not user.verified:
            user.send_verification_email()
            return Response(
                {'detail': VERIFY_EMAIL_MESSAGE, 'email': user.email}, status=status.HTTP_401_UNAUTHORIZED
            )

        result = super().post(request, *args, **kwargs)

        try:
            update_last_login(None, user)
        except:  # pylint: disable=bare-except
            pass

        return result


class UserBaseView(BaseAPIView):
    lookup_field = 'user'
    pk_field = 'username'
    model = UserProfile
    queryset = UserProfile.objects
    es_fields = UserProfile.es_fields
    document_model = UserProfileDocument
    facet_class = UserProfileSearch
    is_searchable = True
    default_qs_sort_attr = '-date_joined'
    serializer_class = UserDetailSerializer

    def get_queryset(self):
        updated_since = parse_updated_since_param(self.request.query_params)
        last_login_since = self.request.query_params.get(LAST_LOGIN_SINCE_PARAM, None)
        last_login_before = self.request.query_params.get(LAST_LOGIN_BEFORE_PARAM, None)
        date_joined_since = self.request.query_params.get(DATE_JOINED_SINCE_PARAM, None)
        date_joined_before = self.request.query_params.get(DATE_JOINED_BEFORE_PARAM, None)
        if updated_since:
            self.queryset = self.queryset.filter(updated_at__gte=updated_since)
        if last_login_since:
            self.queryset = self.queryset.filter(last_login__gte=parse_updated_since(last_login_since))
        if last_login_before:
            self.queryset = self.queryset.filter(last_login__lt=parse_updated_since(last_login_before))
        if date_joined_since:
            self.queryset = self.queryset.filter(created_at__gte=parse_updated_since(date_joined_since))
        if date_joined_before:
            self.queryset = self.queryset.filter(created_at__lt=parse_updated_since(date_joined_before))
        if not self.should_include_inactive():
            self.queryset = self.queryset.filter(is_active=True)
        return self.queryset


class UserLogoView(UserBaseView, BaseLogoView):
    permission_classes = (IsAuthenticated, )


class UserListView(UserBaseView,
                   ListWithHeadersMixin,
                   mixins.CreateModelMixin):

    def get_serializer_class(self):
        if self.request.query_params.get('summary') in ['true', True] and self.request.method == 'GET':
            return UserSummarySerializer
        if self.request.method == 'GET' and self.is_verbose():
            return UserDetailSerializer
        if self.request.method == 'POST':
            return UserCreateSerializer

        return UserListSerializer

    def get_permissions(self):
        if self.request.method in ['POST', 'DELETE']:
            return [IsAdminUser()]
        return []

    def can_view(self, organization):
        user = self.request.user
        return organization.public_can_view or user.is_staff or organization.is_member(user)

    @swagger_auto_schema(
        manual_parameters=[
            last_login_before_param, last_login_since_param, date_joined_before_param, date_joined_since_param,
            updated_since_param
        ]
    )
    def get(self, request, *args, **kwargs):
        org = kwargs.pop('org', None)
        if org:
            organization = Organization.objects.filter(mnemonic=org).first()
            if not organization:
                return Response(status=status.HTTP_404_NOT_FOUND)

            if not self.can_view(organization):
                return Response(status=status.HTTP_403_FORBIDDEN)
            queryset = organization.members
            if not self.should_include_inactive():
                queryset = queryset.filter(is_active=True)
            self.queryset = queryset.all()
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        if serializer.errors:
            return Response(serializer.data, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.data.copy()
        if 'username' in serializer.errors and 'token' not in data and get(serializer, 'instance.token'):
            data['token'] = serializer.instance.token  # for ocl_web

        return Response(data, status=status.HTTP_201_CREATED, headers=headers)


class UserSignup(UserBaseView, mixins.CreateModelMixin):
    serializer_class = UserCreateSerializer
    permission_classes = (AllowAny, )

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data={**request.data, 'verified': False})
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        if serializer.errors:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        headers = self.get_success_headers(serializer.data)
        return Response({'detail': VERIFY_EMAIL_MESSAGE}, status=status.HTTP_201_CREATED, headers=headers)


class UserEmailVerificationView(UserBaseView):
    permission_classes = (AllowAny, )

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        user = self.get_object()
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = user.mark_verified(kwargs.get('verification_token'))
        if result:
            update_last_login(None, user)
            return Response({'token': user.get_token()}, status=status.HTTP_200_OK)

        return Response(dict(detail=VERIFICATION_TOKEN_MISMATCH), status=status.HTTP_401_UNAUTHORIZED)


class UserPasswordResetView(UserBaseView):
    permission_classes = (AllowAny, )

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument,no-self-use
        """Sends reset password mail"""

        email = request.data.get('email')
        if not email:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        user = UserProfile.objects.filter(email=email).first()
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        user.verification_token = uuid.uuid4()
        user.save()
        user.send_reset_password_email()
        return Response(status=status.HTTP_200_OK)

    def put(self, request, *args, **kwargs):  # pylint: disable=unused-argument,no-self-use
        """Resets password"""

        token = request.data.get('token', None)
        password = request.data.get('new_password', None)
        if not token or not password:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        user = UserProfile.objects.filter(verification_token=token).first()

        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = user.update_password(password=password)
        if get(result, 'errors'):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_200_OK)


class UserDetailView(UserBaseView, RetrieveAPIView, DestroyAPIView, mixins.UpdateModelMixin):
    def get_serializer_class(self):
        if self.request.query_params.get('summary') in ['true', True] and self.request.method == 'GET':
            return UserSummarySerializer

        return UserDetailSerializer

    def get_queryset(self):
        if self.kwargs.get('user_is_self'):
            return self.queryset.filter(username=self.request.user.username)
        return self.queryset.filter(username=self.kwargs['user'])

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser()]

        if self.request.query_params.get('includeVerificationToken') and self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_object(self, queryset=None):
        instance = self.request.user if self.kwargs.get('user_is_self') else super().get_object(queryset)
        self.user_is_self = self.request.user.username == instance.username

        if not instance or instance.is_anonymous:
            raise Http404()

        is_self = self.kwargs.get('user_is_self') or self.user_is_self
        is_admin = self.request.user.is_staff

        if self.request.query_params.get('includeVerificationToken') and self.request.method == 'GET':
            return instance

        if not is_self and not is_admin:
            raise PermissionDenied()

        return instance

    def put(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.update_password(request.data.get('password'), request.data.get('hashed_password'))

        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        if self.user_is_self:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        if self.is_hard_delete_requested():
            obj.delete()
        else:
            obj.deactivate()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserReactivateView(UserBaseView, UpdateAPIView):
    permission_classes = (IsAdminUser, )
    queryset = UserProfile.objects.filter(is_active=False)

    def get_queryset(self):
        return self.queryset

    def update(self, request, *args, **kwargs):
        profile = self.get_object()
        profile.undelete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserStaffToggleView(UserBaseView, UpdateAPIView):
    permission_classes = (IsAdminUser, )
    swagger_schema = None

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        if user.username == self.request.user.username:
            raise Http400()
        user.is_staff = not user.is_staff
        user.is_superuser = not user.is_superuser
        user.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserExtrasBaseView(APIView):
    serializer_class = UserDetailSerializer

    def get_object(self):
        instance = self.request.user if self.kwargs.get('user_is_self') else UserProfile.objects.filter(
            username=self.kwargs['user']).first()

        if not instance:
            raise Http404()
        return instance


class UserExtrasView(UserExtrasBaseView):
    def get(self, request, **kwargs):  # pylint: disable=unused-argument
        return Response(get(self.get_object(), 'extras', {}))


class UserExtraRetrieveUpdateDestroyView(UserExtrasBaseView, RetrieveUpdateDestroyAPIView):
    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})

        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):  # pylint: disable=arguments-differ
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        instance.extras[key] = value
        instance.save()
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        if key in instance.extras:
            del instance.extras[key]
            instance.save()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)
