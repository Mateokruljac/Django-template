import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.forms import _unicode_ci_compare
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password, get_password_validators
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, EmailMessage, send_mail
from django.db import IntegrityError
from django.dispatch import receiver
from django.http.request import QueryDict
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _
from django_rest_passwordreset.models import ResetPasswordToken, clear_expired, get_password_reset_token_expiry_time, \
    get_password_reset_lookup_field
from django_rest_passwordreset.signals import reset_password_token_created, pre_password_reset, post_password_reset
from django_rest_passwordreset.views import HTTP_USER_AGENT_HEADER, HTTP_IP_ADDRESS_HEADER
from rest_framework import exceptions
from rest_framework import generics
from rest_framework import parsers, renderers
from rest_framework import status
from rest_framework import views, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.compat import coreapi, coreschema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.schemas import ManualSchema
from rest_framework.schemas import coreapi as coreapi_schema
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet


from .models import User
from .permissions import HasEmailConfirmed
from .serializers import ValidateEmailSerializer
from .serializers import UserSerializer, ConfirmPasswordSerializer, ChangePasswordSerializer


class RegisterView(APIView):

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            if User.objects.filter(email=email).exists():
                return Response({"message": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

            user = serializer.save()
            user.confirmation_token = uuid.uuid4().hex
            user.provider = "password"
            user.save()
            confirmation_url = settings.FRONTEND_URL + '/login-account?token=' + user.confirmation_token
            send_mail(
                subject='Confirm Your Email',
                message=f'Please confirm your email by clicking this link: {confirmation_url}',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            return Response({"message": "Registration successfull, please confirm your email."},
                            status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ConfirmEmailView(views.APIView):
    def post(self, request):
        email = request.data.get('email')
        user = User.objects.filter(email=email).first()
        if user.email_confirmed:
            return Response({"message": "Email has already been confirmed."}, status=status.HTTP_400_BAD_REQUEST)
        user.email_confirmed = True
        user.save()
        # Send email to user
        subject = 'Email Confirmation'
        message = 'Your email has been confirmed successfully.'
        email_from = settings.EMAIL_HOST_USER
        recipient_list = [user.email]
        email = EmailMessage(subject, message, email_from, recipient_list)
        email.send()
        return Response({"message": "Email confirmed successfully."}, status=status.HTTP_200_OK)


class LogoutView(views.APIView):
    """ Logout current user """

    def post(self, request):
        if request.user is not None:
            logout(request)
            return Response({'message': 'User is successfully logged out.'}, status=status.HTTP_200_OK)
        else:
            return Response({'message': 'User is not logged in.'}, status=status.HTTP_400_BAD_REQUEST)


@receiver(reset_password_token_created)
def password_reset_token_created(sender, instance, reset_password_token, *args, **kwargs):
    """
    Handles password reset tokens
    When a token is created, an e-mail needs to be sent to the user
    :param sender: View Class that sent the signal
    :param instance: View Instance that sent the signal
    :param reset_password_token: Token Model Object
    :param args:
    :param kwargs:
    :return:
    """
    # send an e-mail to the user
    context = {
        'current_user': reset_password_token.user,
        'username': reset_password_token.user.name,
        'email': reset_password_token.user.email,
        'reset_password_url': "{}?token={}".format(
            settings.FRONTEND_URL + '/reset-password',
            reset_password_token.key)
    }

    # render email text
    email_html_message = render_to_string('email/user_reset_password.html', context)
    email_plaintext_message = render_to_string('email/user_reset_password.txt', context)

    msg = EmailMultiAlternatives(
        # title:
        "Password Reset for {title}".format(title="django-template"),
        # message:
        email_plaintext_message,
        # from:
        settings.DEFAULT_FROM_EMAIL,
        # to:
        [reset_password_token.user.email]
    )
    msg.attach_alternative(email_html_message, "text/html")
    msg.send()


class ResetPasswordConfirm(GenericAPIView):
    """
    An Api View which provides a method to reset a password based on a unique token
    """

    permission_classes = ()
    serializer_class = ConfirmPasswordSerializer
    authentication_classes = ()

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = serializer.validated_data['password']
        token = serializer.validated_data['token']

        # find token
        reset_password_token = ResetPasswordToken.objects.filter(key=token).first()

        # change users password (if we got to this code it means that the user is_active)
        if reset_password_token.user.eligible_for_reset():
            pre_password_reset.send(sender=self.__class__, user=reset_password_token.user)
            try:
                # validate the password against existing validators
                validate_password(
                    password,
                    user=reset_password_token.user,
                    password_validators=get_password_validators(settings.AUTH_PASSWORD_VALIDATORS)
                )
            except ValidationError as e:
                # raise a validation error for the serializer
                raise exceptions.ValidationError({
                    'password': e.messages
                })

            reset_password_token.user.set_password(password)
            reset_password_token.user.save()
            post_password_reset.send(sender=self.__class__, user=reset_password_token.user)

        # Delete all password reset tokens for this user
        ResetPasswordToken.objects.filter(user=reset_password_token.user).delete()

        return Response({'status': 'OK'})


class ResetPasswordThrottle(SimpleRateThrottle):
    scope = 'custom'

    def get_cache_key(self, request, view):
        return self.get_ident(request)


class ResetPasswordRequestToken(GenericAPIView):
    """
    An Api View which provides a method to request a password reset token based on an e-mail address

    Sends a signal reset_password_token_created when a reset token was created
    """
    throttle_classes = (ResetPasswordThrottle,)
    permission_classes = ()
    serializer_class = ValidateEmailSerializer
    authentication_classes = ()

    def post(self, request, *args, **kwargs):
        global token
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        # before we continue, delete all existing expired tokens
        password_reset_token_validation_time = get_password_reset_token_expiry_time()

        # datetime.now minus expiry hours
        now_minus_expiry_time = timezone.now() - timedelta(hours=password_reset_token_validation_time)

        # delete all tokens where created_at < now - 24 hours
        clear_expired(now_minus_expiry_time)

        # find a user by email address (case insensitive search)
        users = User.objects.filter(**{'{}__iexact'.format(get_password_reset_lookup_field()): email})

        active_user_found = False

        # iterate over all users and check if there is any user that is active
        # also check whether the password can be changed (is useable), as there could be users that are not allowed
        # to change their password (e.g., LDAP user)
        for user in users:
            if user.eligible_for_reset():
                active_user_found = True
                break

        # No active user found, raise a validation error
        # but not if DJANGO_REST_PASSWORDRESET_NO_INFORMATION_LEAKAGE == True
        if not active_user_found and not getattr(settings, 'DJANGO_REST_PASSWORDRESET_NO_INFORMATION_LEAKAGE', False):
            raise exceptions.ValidationError({
                'email': [_(
                    "We couldn't find an account associated with that email. Please try a different e-mail address.")],
            })

        # last but not least: iterate over all users that are active and can change their password
        # and create a Reset Password Token and send a signal with the created token
        for user in users:
            if user.eligible_for_reset() and \
                    _unicode_ci_compare(email, getattr(user, get_password_reset_lookup_field())):
                # define the token as none for now
                token = None
                # check if the user already has a token
                if user.password_reset_tokens.all().count() > 0:
                    # yes, already has a token, re-use this token
                    token = user.password_reset_tokens.all()[0]
                else:
                    # no token exists, generate a new token
                    token = ResetPasswordToken.objects.create(
                        user=user,
                        user_agent=request.META.get(HTTP_USER_AGENT_HEADER, ''),
                        ip_address=request.META.get(HTTP_IP_ADDRESS_HEADER, ''),
                    )
                # send a signal that the password token was created
                # let whoever receives this signal handle sending the email for the password reset
                reset_password_token_created.send(sender=self.__class__, instance=self, reset_password_token=token)
        # done
        return Response({'status': 'OK'})


class UserProfileViewSet(viewsets.ModelViewSet):
    """ User profile views returns current user in queryset """
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, HasEmailConfirmed]
    http_method_names = ["get"]

    def get_object(self):
        """ Get current user """
        return User.objects.get(id=self.request.user.id)


class UpdateUserProfileViewSet(viewsets.ModelViewSet):
    """ Update current user API view """
    permission_classes = [IsAuthenticated, HasEmailConfirmed]
    serializer_class = UserSerializer

    def get_queryset(self):
        return User.objects.filter(id=self.request.user.id)

    def update(self, request, *args, **kwargs):
        instance = User.objects.get(pk=self.request.user.id)
        old_email = instance.email
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        new_email = serializer.validated_data.get('email')
        if new_email and new_email != old_email:
            # send email
            try:
                user = serializer.save()
                # Generate the confirmation token
                user.confirmation_token = uuid.uuid4().hex
                # Set email_confirmed to False
                user.email_confirmed = False
                user.save()
                confirmation_url = settings.FRONTEND_URL + '/login-account?token=' + user.confirmation_token
                send_mail(
                    subject='Confirm Your New Email Address',
                    message=f'Please confirm your new email address by clicking this link: {confirmation_url}',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[new_email],
                    fail_silently=False,
                )
            except IntegrityError as e:
                if 'users_user_iban_key' in str(e):
                    return Response({'iban': 'Invalid IBAN.'}, status=status.HTTP_400_BAD_REQUEST)
                else:
                    raise e
            # return response message
            return Response({"message": "Please confirm your new email address."}, status=status.HTTP_200_OK)
        self.perform_update(serializer)
        return Response(serializer.data)


class UserViewSet(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (IsAuthenticated, HasEmailConfirmed)

    def get_object(self):
        return self.request.user

    def perform_destroy(self, instance):
        Token.objects.filter(user=instance).delete()
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response({"error": "You must be logged in to delete your account"},
                            status=status.HTTP_401_UNAUTHORIZED)
        instance = self.get_object()
        if request.user.id != instance.id:
            return Response({"error": "You are not authorized to delete this account"},
                            status=status.HTTP_403_FORBIDDEN)
        self.perform_destroy(instance)
        return Response({"message": "Account deleted successfully"}, status=status.HTTP_200_OK)





class ChangePasswordView(APIView):
    permission_classes = (IsAuthenticated, HasEmailConfirmed)

    def post(self, request):
        user = request.user
        if user.provider != 'password':
            return Response(
                {"message": "Because you used Google/Facebook login you can not change password."},
                status=status.HTTP_400_BAD_REQUEST)
        serializer = ChangePasswordSerializer(data=request.data, context={'user': user})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Password changed successfully"}, status=status.HTTP_204_NO_CONTENT)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ObtainAuthToken(APIView):
    throttle_classes = ()
    permission_classes = ()
    parser_classes = (parsers.FormParser, parsers.MultiPartParser, parsers.JSONParser,)
    renderer_classes = (renderers.JSONRenderer,)
    serializer_class = AuthTokenSerializer

    if coreapi_schema.is_enabled():
        schema = ManualSchema(
            fields=[
                coreapi.Field(
                    name="username",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="Username",
                        description="Valid username for authentication",
                    ),
                ),
                coreapi.Field(
                    name="password",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="Password",
                        description="Valid password for authentication",
                    ),
                ),
            ],
            encoding="application/json",
        )

    def get_serializer_context(self):
        return {
            'request': self.request,
            'format': self.format_kwarg,
            'view': self
        }

    def get_serializer(self, *args, **kwargs):
        kwargs['context'] = self.get_serializer_context()
        return self.serializer_class(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        if not user.email_confirmed:
            return Response({"code": "email_not_confirmed",
                             "message": "Email not confirmed. Please confirm your email before logging in."},
                            status=status.HTTP_400_BAD_REQUEST)
        token, created = Token.objects.get_or_create(user=user)
        return Response({'token': token.key})


class ResendConfirmEmailView(views.APIView):
    throttle_classes = (ResetPasswordThrottle,)
    serializer_class = ValidateEmailSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data.get('email')
        user = User.objects.filter(email=email).first()
        if not user:
            return Response({"message": "User with this email does not exist."}, status=status.HTTP_400_BAD_REQUEST)
        if user.email_confirmed:
            return Response({"message": "Email has already been confirmed."}, status=status.HTTP_400_BAD_REQUEST)
        # Generate new confirmation token
        user.confirmation_token = uuid.uuid4().hex
        user.save()
        # Send new confirmation email
        confirmation_url = settings.FRONTEND_URL + '/login-account?token=' + user.confirmation_token
        send_mail(
            subject='Confirm Your Email',
            message=f'Please confirm your email by clicking this link: {confirmation_url}',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return Response({"message": "New confirmation email sent successfully."}, status=status.HTTP_200_OK)
