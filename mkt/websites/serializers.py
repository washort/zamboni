from drf_compound_fields.fields import ListField
from rest_framework import serializers


class WebsiteSerializer(serializers.Serializer):
    url = serializers.CharField()
    title = serializers.CharField()
    short_title = serializers.CharField()
    description = serializers.CharField()
    keywords = serializers.CharField()
    devices = ListField(serializers.CharField())
    categories = ListField(serializers.CharField())
    icon_url = serializers.URLField()
