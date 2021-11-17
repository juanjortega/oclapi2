from rest_framework.fields import CharField, JSONField
from rest_framework.serializers import Serializer


class RootSerializer(Serializer):  # pylint: disable=abstract-method
    version = CharField()
    routes = JSONField()


class TaskSerializer(Serializer):  # pylint: disable=abstract-method
    pass
