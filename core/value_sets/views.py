import logging

from django.db.models import F

from core.bundles.serializers import FhirBundleSerializer
from core.collections.views import CollectionListView, CollectionRetrieveUpdateDestroyView, \
    CollectionVersionExpansionsView
from core.concepts.views import ConceptRetrieveUpdateDestroyView
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.value_sets.serializers import ValueSetDetailSerializer, \
    ValueSetExpansionParametersSerializer, ValueSetExpansionSerializer

logger = logging.getLogger('oclapi')


class ValueSetListView(CollectionListView):
    serializer_class = ValueSetDetailSerializer

    @staticmethod
    def bundle_response(data):
        bundle = FhirBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FhirBundleSerializer.convert_to_entry(data)})
        return bundle.data

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def apply_query_filters(self, queryset):
        query_fields = list(self.serializer_class.Meta.fields)

        url = self.request.query_params.get('url')
        if url:
            queryset = queryset.filter(canonical_url=url)
            query_fields.remove('url')

        language = self.request.query_params.get('language')
        if language:
            queryset = queryset.filter(locale=language)
            query_fields.remove('language')
        status = self.request.query_params.get('status')
        if status:
            query_fields.remove('status')
            if status == 'retired':
                queryset = queryset.filter(retired=True)
            elif status == 'active':
                queryset = queryset.filter(released=True)
            elif status == 'draft':
                queryset = queryset.filter(released=False)

        for query_field in query_fields:
            query_value = self.request.query_params.get(query_field)
            if query_value:
                kwargs = {query_field: query_value}
                queryset = queryset.filter(**kwargs)

        return queryset

    def apply_filters(self, queryset):
        queryset = queryset.exclude(version='HEAD').filter(is_latest_version=True)
        return self.apply_query_filters(queryset)

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class ValueSetValidateCodeView(ConceptRetrieveUpdateDestroyView):
    serializer_class = ParametersSerializer
    parameters = {}

    def process_parameters(self):
        self.parameters = {}
        for key, value in self.request.query_params.items():
            self.parameters[key] = value

        if self.request.data:
            serializer = ParametersSerializer(data=self.request.data)
            serializer.is_valid(raise_exception=True)
            body_parameters = serializer.validated_data
            for parameter in body_parameters.get('parameter', []):
                name = parameter.get('name')
                value = None
                match name:
                    case 'url' | 'system':
                        value = parameter.get('valueUrl') | parameter.get('valueUri')
                    case 'code' | 'displayLanguage':
                        value = parameter.get('valueCode')
                    case 'display' | 'systemVersion':
                        value = parameter.get('valueString')

                if value:
                    self.parameters[name] = value

    def is_container_version_specified(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        self.process_parameters()
        code = self.parameters.get('code')
        system = self.parameters.get('system')
        display = self.parameters.get('display')
        system_version = self.parameters.get('systemVersion')
        if code and system:
            concept_source = Source.objects.filter(canonical_url=system)
            if system_version:
                concept_source = concept_source.filter(version=system_version)
            else:
                concept_source = concept_source.filter(is_latest_version=True).exclude(version='HEAD')
            if concept_source:
                queryset = queryset.filter(sources=concept_source.first(), mnemonic=code)

        if display:
            instance = queryset.first()
            if display not in (instance.name, instance.display_name):
                return queryset.none()

        return queryset

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        if not self.is_container_version_specified():
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()
        if instance:
            self.check_object_permissions(self.request, instance)

        return instance

    def get_serializer(self, instance=None):  # pylint: disable=arguments-differ
        if instance:
            return ParametersSerializer({'parameter': [
                {
                    'name': 'result',
                    'valueBoolean': True
                }
            ]})
        return ParametersSerializer({'parameter': [
            {
                'name': 'result',
                'valueBoolean': False
            },
            {
                'name': 'message',
                'valueString': 'The code is incorrect.'
            }
        ]})


class ValueSetRetrieveUpdateView(CollectionRetrieveUpdateDestroyView):
    serializer_class = ValueSetDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.exclude(version='HEAD')

    def get_detail_serializer(self, obj):
        return ValueSetDetailSerializer(obj)


class ValueSetExpandView(CollectionVersionExpansionsView):
    sync = True
    def get_serializer_class(self):
        return ValueSetExpansionParametersSerializer

    def get_response_serializer_class(self):
        return ValueSetExpansionSerializer
