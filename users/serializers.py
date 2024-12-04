import re

from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, RegexValidator
from django.utils import timezone
from django_rest_passwordreset.serializers import PasswordTokenSerializer
from rest_framework import serializers

from users.constants import min_birth_age, REGEX
from users.models import User


def validate_password(password):
    if re.search("[!@#$%^&*(),.?\":{}|<>]", password) is None:
        raise ValidationError(
            'The password must contain at least one special character.',
            code='password_missing_special_character',
        )


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(max_length=30, min_length=8, write_only=True, validators=[validate_password])
    phone_number = serializers.CharField(max_length=20, min_length=6,
                                         validators=[RegexValidator(REGEX.PHONE_NUMBER_RULE,
                                                                    'Only numbers and an optional, + sign in the '
                                                                    'first character is allowed.')])
    name = serializers.CharField(min_length=2, max_length=50,
                                 validators=[
                                     RegexValidator(REGEX.NAME_RULE, 'Only alphabetic characters are allowed.')])

    address = serializers.CharField(min_length=2, max_length=50)
    date_of_birth = serializers.DateField()

    class Meta:
        model = User
        fields = ('id', 'email', 'name', 'address', 'password', 'date_of_birth', 'phone_number')

        read_only_fields = ('otp_base32', 'otp_auth_url',)

        extra_kwargs = {
            'date_of_birth': {'required': True},
            'phone_number': {'required': True},
            'address': {'required': True},
        }

    def create(self, validated_data):
        email = validated_data['email']
        name = validated_data['name']
        address = validated_data.get('address', None)
        date_of_birth = validated_data['date_of_birth']
        phone_number = validated_data['phone_number']

        user = User.objects.create(
            email=email,
            name=name,
            address=address,
            date_of_birth=date_of_birth,
            phone_number=phone_number
        )
        user.set_password(validated_data['password'])
        user.save()
        return user


class ConfirmPasswordSerializer(PasswordTokenSerializer):
    password = serializers.CharField(max_length=30, min_length=8, validators=[validate_password, MinLengthValidator(8)])
    confirm_password = serializers.CharField(label="Confirm Password", style={'input_type': 'password'})

    def validate(self, attrs):
        if attrs.get('password') != attrs.get('confirm_password'):
            raise serializers.ValidationError("Passwords do not match.")
        return super().validate(attrs)



class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(max_length=30, min_length=8, write_only=True,
                                         validators=[validate_password, MinLengthValidator(8)])
    confirm_new_password = serializers.CharField(required=True)

    def validate(self, data):
        user = self.context['user']
        if not user.check_password(data['old_password']):
            raise serializers.ValidationError("Incorrect old password")
        if data['new_password'] != data['confirm_new_password']:
            raise serializers.ValidationError("Passwords don't match")
        if data['old_password'] == data['new_password']:
            raise serializers.ValidationError("Old password cannot be the same as new password.")
        return data

    def save(self):
        user = self.context['user']
        user.set_password(self.validated_data['new_password'])
        user.save()


class ValidateEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=50)
