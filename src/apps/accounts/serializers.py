from rest_framework import serializers

from .models import Role, User


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "code", "name", "description"]


class UserSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    role_code = serializers.CharField(source="role.code", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "role",
            "role_code",
            "role_name",
            "department",
            "department_name",
            "vendor",
            "is_active",
            "date_joined",
        ]
        read_only_fields = ["id", "date_joined"]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=10)
    role = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(),
        required=True,
        allow_null=False,
        error_messages={"required": "Role is required. Every user must hold an assigned RBAC role."},
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "password",
            "first_name",
            "last_name",
            "phone_number",
            "role",
            "department",
            "vendor",
        ]

    def validate_email(self, value):
        val = value.strip().lower()
        if User.objects.filter(email__iexact=val).exists():
            raise serializers.ValidationError("A user with this email address already exists (case-insensitive).")
        return val

    def validate_phone_number(self, value):
        if not value:
            return ""
        val = value.strip()
        import re
        if not re.match(r"^\+?[0-9\s\-()]{7,20}$", val) or re.search(r"[a-zA-Z]", val):
            raise serializers.ValidationError("Phone number must contain only valid digits and standard phone symbols.")
        return val

    def validate(self, attrs):
        if not attrs.get("role"):
            raise serializers.ValidationError({"role": ["Role is required. Every user must hold an assigned RBAC role."]})
        email = attrs.get("email", "")
        password = attrs.get("password", "")
        if password:
            import django.core.exceptions
            from django.contrib.auth.password_validation import validate_password
            temp_user = User(email=email) if email else None
            try:
                validate_password(password, user=temp_user)
            except django.core.exceptions.ValidationError as e:
                raise serializers.ValidationError({"password": list(e.messages)})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=True,
        help_text="Registered user email address (e.g. admin@example.com)",
    )
    password = serializers.CharField(
        required=True,
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
        help_text="User account password",
    )


class LoginResponseSerializer(serializers.Serializer):
    message = serializers.CharField(default="Login successful.")
    user = UserSerializer()
    csrf_token = serializers.CharField(help_text="CSRF token for subsequent state-changing requests")

