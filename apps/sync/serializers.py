from rest_framework import serializers


class PushItemSerializer(serializers.Serializer):
    entity_type = serializers.CharField(max_length=50)
    client_id = serializers.CharField(max_length=64)
    data = serializers.DictField(required=False, default=dict)


class PushRequestSerializer(serializers.Serializer):
    items = PushItemSerializer(many=True)
