import logging

from celery_once import AlreadyQueued
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import status
from rest_framework.generics import (
    RetrieveAPIView, DestroyAPIView, UpdateAPIView, ListAPIView,
    CreateAPIView)
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from core.client_configs.views import ResourceClientConfigsView
from core.collections.constants import (
    INCLUDE_REFERENCES_PARAM, HEAD_OF_CONCEPT_ADDED_TO_COLLECTION,
    HEAD_OF_MAPPING_ADDED_TO_COLLECTION, CONCEPT_ADDED_TO_COLLECTION_FMT, MAPPING_ADDED_TO_COLLECTION_FMT,
    DELETE_FAILURE, DELETE_SUCCESS, NO_MATCH, VERSION_ALREADY_EXISTS,
    SOURCE_MAPPINGS,
    UNKNOWN_REFERENCE_ADDED_TO_COLLECTION_FMT, SOURCE_TO_CONCEPTS)
from core.collections.documents import CollectionDocument
from core.collections.models import Collection, CollectionReference
from core.collections.search import CollectionSearch
from core.collections.serializers import (
    CollectionDetailSerializer, CollectionListSerializer,
    CollectionCreateSerializer, CollectionReferenceSerializer, CollectionVersionDetailSerializer,
    CollectionVersionListSerializer, CollectionVersionExportSerializer, CollectionSummaryDetailSerializer,
    CollectionVersionSummaryDetailSerializer, CollectionReferenceDetailSerializer, ExpansionSerializer,
    ExpansionDetailSerializer)
from core.collections.utils import is_version_specified
from core.common.constants import (
    HEAD, RELEASED_PARAM, PROCESSING_PARAM, OK_MESSAGE,
    ACCESS_TYPE_NONE)
from core.common.exceptions import Http409
from core.common.mixins import (
    ConceptDictionaryCreateMixin, ListWithHeadersMixin, ConceptDictionaryUpdateMixin,
    ConceptContainerExportMixin,
    ConceptContainerProcessingMixin, ConceptContainerExtraRetrieveUpdateDestroyView)
from core.common.permissions import (
    CanViewConceptDictionary, CanEditConceptDictionary, HasAccessToVersionedObject,
    CanViewConceptDictionaryVersion
)
from core.common.swagger_parameters import q_param, compress_header, page_param, verbose_param, exact_match_param, \
    include_facets_header, sort_asc_param, sort_desc_param, updated_since_param, include_retired_param, limit_param
from core.common.tasks import add_references, export_collection, delete_collection
from core.common.utils import compact_dict_by_values, parse_boolean_query_param
from core.common.views import BaseAPIView, BaseLogoView
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping

logger = logging.getLogger('oclapi')


class CollectionBaseView(BaseAPIView):
    lookup_field = 'collection'
    pk_field = 'mnemonic'
    model = Collection
    permission_classes = (CanViewConceptDictionary,)
    queryset = Collection.objects.filter(is_active=True)

    def verify_scope(self):
        has_owner_scope = self.has_owner_scope()
        has_no_kwargs = self.has_no_kwargs()

        if not self.user_is_self:
            if has_no_kwargs:
                if self.request.method not in ['GET', 'HEAD']:
                    raise Http404()
            elif not has_owner_scope:
                raise Http404()

    def set_parent_resource(self):
        from core.orgs.models import Organization
        from core.users.models import UserProfile
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)
        if not user and self.user_is_self:
            user = self.request.user.username
        parent_resource = None
        if org:
            parent_resource = Organization.objects.filter(mnemonic=org).first()
        if user:
            parent_resource = UserProfile.objects.filter(username=user).first()

        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({'request': self.request, INCLUDE_REFERENCES_PARAM: self.should_include_references()})
        return context

    @staticmethod
    def get_detail_serializer(obj):
        return CollectionDetailSerializer(obj)

    def get_filter_params(self, default_version_to_head=True):
        query_params = self.request.query_params.dict()

        version = query_params.get('version', None) or self.kwargs.get('version', None)
        if not version and default_version_to_head:
            version = HEAD

        kwargs = self.kwargs.copy()
        if self.user_is_self and self.request.user.is_authenticated:
            kwargs['user'] = self.request.user.username

        return {
            **query_params.copy(), **kwargs,
            'version': version, 'include_references': self.should_include_references()
        }

    def get_queryset(self):
        return self.get_base_queryset()

    def get_base_queryset(self):
        queryset = Collection.get_base_queryset(
            compact_dict_by_values(self.get_filter_params())
        ).select_related(
            'user', 'organization'
        )

        if self.is_verbose():
            queryset = queryset.select_related('created_by', 'updated_by')

        return queryset

    def should_include_references(self):
        return self.request.query_params.get(INCLUDE_REFERENCES_PARAM, 'false').lower() == 'true'


class CollectionVersionBaseView(CollectionBaseView):
    def get_filter_params(self, default_version_to_head=False):
        return super().get_filter_params(default_version_to_head)


class CollectionListView(CollectionBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    serializer_class = CollectionListSerializer
    is_searchable = True
    es_fields = Collection.es_fields
    document_model = CollectionDocument
    facet_class = CollectionSearch
    default_filters = dict(is_active=True, version=HEAD)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if get(user, 'is_staff'):
            return queryset
        if get(user, 'is_anonymous'):
            return queryset.exclude(public_access=ACCESS_TYPE_NONE)

        public_queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
        private_queryset = queryset.filter(public_access=ACCESS_TYPE_NONE)
        private_queryset = private_queryset.filter(Q(user_id=user.id) | Q(organization__members__id=user.id))
        return public_queryset.union(private_queryset)

    def get_serializer_class(self):
        if self.request.method == 'GET' and self.is_verbose():
            return CollectionDetailSerializer
        if self.request.method == 'POST':
            return CollectionCreateSerializer

        return CollectionListSerializer

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, page_param, verbose_param,
            include_retired_param, updated_since_param, include_facets_header, compress_header
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_csv_rows(self, queryset=None):  # pragma: no cover
        if not queryset:
            queryset = self.get_queryset()

        values = queryset.values('mnemonic', 'name', 'full_name', 'collection_type', 'description', 'default_locale',
                                 'supported_locales', 'website', 'external_id', 'updated_at', 'updated_by', 'uri')

        for value in values:
            value['Owner'] = Collection.objects.get(uri=value['uri']).parent.mnemonic
            value['Collection ID'] = value.pop('mnemonic')
            value['Collection Name'] = value.pop('name')
            value['Collection Full Name'] = value.pop('full_name')
            value['Collection Type'] = value.pop('collection_type')
            value['Description'] = value.pop('description')
            value['Default Locale'] = value.pop('default_locale')
            value['Supported Locales'] = ",".join(value.pop('supported_locales'))
            value['Website'] = value.pop('website')
            value['External ID'] = value.pop('external_id')
            value['Last Updated'] = value.pop('updated_at')
            value['Updated By'] = value.pop('updated_by')
            value['URI'] = value.pop('uri')

        values.field_names.extend([
            'Owner', 'Collection ID', 'Collection Name', 'Collection Full Name', 'Collection Type', 'Description',
            'Default Locale', 'Supported Locales', 'Website', 'External ID', 'Last Updated', 'Updated By', 'URI'
        ])
        del values.field_names[0:12]
        return values


class CollectionLogoView(CollectionBaseView, BaseLogoView):
    serializer_class = CollectionDetailSerializer

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [IsAuthenticated(), CanEditConceptDictionary()]


class CollectionRetrieveUpdateDestroyView(CollectionBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView):
    serializer_class = CollectionDetailSerializer

    def get_object(self, queryset=None):
        instance = self.get_queryset().filter(is_active=True).order_by('-created_at').first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        if not get(settings, 'TEST_MODE', False):
            if instance.should_set_active_concepts:
                instance.update_concepts_count()
            if instance.should_set_active_mappings:
                instance.update_mappings_count()
            for version in instance.versions.exclude(id=instance.id):
                if version.should_set_active_concepts:
                    version.update_concepts_count()
                if version.should_set_active_mappings:
                    version.update_mappings_count()
        return instance

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [IsAuthenticated(), CanEditConceptDictionary()]

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        collection = self.get_object()

        if not self.is_inline_requested():
            task = delete_collection.delay(collection.id)
            return Response(dict(task=task.id), status=status.HTTP_202_ACCEPTED)

        result = delete_collection(collection.id)

        if result is True:
            return Response({'detail': DELETE_SUCCESS}, status=status.HTTP_204_NO_CONTENT)

        return Response({'detail': get(result, 'messages', [DELETE_FAILURE])}, status=status.HTTP_400_BAD_REQUEST)


class CollectionReferenceView(CollectionBaseView, RetrieveAPIView):
    serializer_class = CollectionReferenceDetailSerializer
    permission_classes = (CanViewConceptDictionary, )

    def get_object(self, queryset=None):
        collection = super().get_queryset().filter(is_active=True).order_by('-created_at').first()

        if not collection:
            raise Http404()

        self.check_object_permissions(self.request, collection)

        reference = CollectionReference.objects.filter(id=self.kwargs.get('reference')).first()
        if not reference:
            raise Http404()

        return reference


class CollectionReferencesView(
        CollectionBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, DestroyAPIView, ListWithHeadersMixin
):
    serializer_class = CollectionReferenceSerializer

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def get_object(self, queryset=None):
        instance = super().get_queryset().filter(is_active=True).order_by('-created_at').first()

        if not instance:
            raise Http404(NO_MATCH)

        self.check_object_permissions(self.request, instance)

        return instance

    def get_queryset(self):
        search_query = self.request.query_params.get('q', '')
        sort = self.request.query_params.get('search_sort', 'ASC')
        if sort == 'ASC':
            sort = ''
        else:
            sort = '-'

        instance = self.get_object()
        queryset = instance.references

        if search_query:
            queryset = queryset.filter(expression__icontains=search_query).order_by(sort + 'expression')

        return queryset.all()

    def retrieve(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        expressions = request.data.get("references") or request.data.get("expressions")
        if not expressions:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if expressions == '*':
            expressions = list(instance.references.values_list('expression', flat=True))
        if self.should_cascade_mappings():
            expressions += instance.get_cascaded_mapping_uris_from_concept_expressions(expressions)

        instance.delete_references(expressions)
        return Response({'message': OK_MESSAGE}, status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):  # pylint: disable=too-many-locals,unused-argument # Fixme: Sny
        collection = self.get_object()
        cascade_to_concepts = self.should_cascade_to_concepts()
        cascade_mappings = cascade_to_concepts or self.should_cascade_mappings()
        data = request.data.get('data')
        concept_expressions = data.get('concepts', [])
        mapping_expressions = data.get('mappings', [])
        expressions = data.get('expressions', [])

        adding_all = mapping_expressions == '*' or concept_expressions == '*'

        if adding_all:
            result = add_references.delay(
                self.request.user.id, data, collection.id, cascade_mappings, cascade_to_concepts)
            return Response(
                dict(
                    state=result.state, username=request.user.username, task=result.task_id, queue='default'
                ) if result else [],
                status=status.HTTP_202_ACCEPTED
            )

        (added_references, errors) = collection.add_expressions(
            data, request.user, cascade_mappings, cascade_to_concepts
        )

        all_expressions = expressions + concept_expressions + mapping_expressions

        added_expressions = [reference.expression for reference in added_references]
        added_original_expressions = set(
            [reference.original_expression or reference.expression for reference in added_references] + all_expressions
        )

        response = []

        for expression in added_original_expressions:
            response_item = self.create_response_item(added_expressions, errors, expression)
            if response_item:
                response.append(response_item)

        for ref in added_references:
            if ref.concepts:
                collection.batch_index(ref.concepts, ConceptDocument)
            if ref.mappings:
                collection.batch_index(ref.mappings, MappingDocument)

        return Response(response, status=status.HTTP_200_OK)

    def should_cascade_mappings(self):
        return self.request.query_params.get('cascade', '').lower() == SOURCE_MAPPINGS

    def should_cascade_to_concepts(self):
        return self.request.query_params.get('cascade', '').lower() == SOURCE_TO_CONCEPTS

    def create_response_item(self, added_expressions, errors, expression):
        adding_expression_failed = len(errors) > 0 and expression in errors
        if adding_expression_failed:
            return self.create_error_message(errors, expression)
        return self.create_success_message(added_expressions, expression)

    def create_success_message(self, added_expressions, expression):
        message = self.select_update_message(expression)

        references = list(filter(lambda expr: expr.startswith(expression), added_expressions))
        if len(references) < 1:
            return None

        return {
            'added': True,
            'expression': references[0],
            'message': message
        }

    @staticmethod
    def create_error_message(errors, expression):
        error_message = errors.get(expression, {})
        return {
            'added': False,
            'expression': expression,
            'message': error_message
        }

    def select_update_message(self, expression):
        adding_head_version = not is_version_specified(expression)

        expression_parts = expression.split('/')
        resource_type = get(expression_parts, '5')

        if adding_head_version:
            return self.adding_to_head_message_by_type(resource_type)

        resource_name = get(expression_parts, '6')
        return self.version_added_message_by_type(resource_name, self.parent_resource.name, resource_type)

    @staticmethod
    def adding_to_head_message_by_type(resource_type):
        if resource_type == 'concepts':
            return HEAD_OF_CONCEPT_ADDED_TO_COLLECTION
        if resource_type == 'mappings':
            return HEAD_OF_MAPPING_ADDED_TO_COLLECTION
        return UNKNOWN_REFERENCE_ADDED_TO_COLLECTION_FMT.format('')

    @staticmethod
    def version_added_message_by_type(resource_name, collection_name, resource_type):
        if resource_type == 'concepts':
            return CONCEPT_ADDED_TO_COLLECTION_FMT.format(resource_name, collection_name)
        if resource_type == 'mappings':
            return MAPPING_ADDED_TO_COLLECTION_FMT.format(resource_name, collection_name)
        return UNKNOWN_REFERENCE_ADDED_TO_COLLECTION_FMT.format(collection_name)


class CollectionVersionReferencesView(CollectionVersionBaseView, ListWithHeadersMixin):
    serializer_class = CollectionReferenceSerializer

    def get(self, request, *args, **kwargs):
        query_params = self.request.query_params
        search_query = query_params.get('q', '')
        sort = query_params.get('search_sort', 'ASC')
        object_version = self.get_queryset().first()
        if not object_version:
            raise Http404()
        references = object_version.references.filter(expression__icontains=search_query)
        self.object_list = references if sort == 'ASC' else list(reversed(references))
        return self.list(request, *args, **kwargs)


class CollectionVersionListView(CollectionVersionBaseView, CreateAPIView, ListWithHeadersMixin):
    released_filter = None
    processing_filter = None
    default_qs_sort_attr = '-created_at'

    def get_serializer_class(self):
        if self.request.method in ['GET', 'HEAD'] and self.is_verbose():
            return CollectionVersionDetailSerializer
        if self.request.method == 'POST':
            return CollectionCreateSerializer

        return CollectionVersionListSerializer

    def get(self, request, *args, **kwargs):
        self.released_filter = parse_boolean_query_param(request, RELEASED_PARAM, self.released_filter)
        self.processing_filter = parse_boolean_query_param(request, PROCESSING_PARAM, self.processing_filter)
        return self.list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        head_object = self.get_queryset().first()
        version = request.data.pop('id', None)
        payload = {
            "mnemonic": head_object.mnemonic, "id": head_object.mnemonic, "name": head_object.name, **request.data,
            "organization_id": head_object.organization_id, "user_id": head_object.user_id,
            'version': version
        }
        serializer = self.get_serializer(data=payload)
        if serializer.is_valid():
            try:
                instance = serializer.create_version(payload)
                if serializer.is_valid():
                    serializer = CollectionDetailSerializer(instance, context={'request': request})
                    return Response(serializer.data, status=status.HTTP_201_CREATED)
            except IntegrityError as ex:
                return Response(
                    dict(
                        error=str(ex), detail=VERSION_ALREADY_EXISTS.format(version)
                    ),
                    status=status.HTTP_409_CONFLICT
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.released_filter is not None:
            queryset = queryset.filter(released=self.released_filter)
        return queryset.order_by('-created_at')


class CollectionLatestVersionRetrieveUpdateView(CollectionVersionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = CollectionVersionDetailSerializer
    permission_classes = (CanViewConceptDictionaryVersion,)

    def get_object(self, queryset=None):
        obj = self.get_queryset().first()
        if not obj:
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset().filter(released=True)
        return queryset.order_by('-created_at')

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        head = self.object.head
        if not head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        serializer = self.get_serializer(self.object, data=request.data, partial=True)

        if serializer.is_valid():
            self.object = serializer.save(force_update=True)
            if serializer.is_valid():
                serializer = CollectionVersionDetailSerializer(self.object, context={'request': request})
                return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CollectionVersionRetrieveUpdateDestroyView(CollectionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = CollectionVersionDetailSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [CanViewConceptDictionaryVersion()]
        return [HasAccessToVersionedObject()]

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        if not get(settings, 'TEST_MODE', False):
            if instance.should_set_active_concepts:
                instance.update_concepts_count()
            if instance.should_set_active_mappings:
                instance.update_mappings_count()
        return instance

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        head = self.object.head
        if not head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        serializer = self.get_serializer(self.object, data=request.data, partial=True)

        if serializer.is_valid():
            self.object = serializer.save(force_update=True)
            if serializer.is_valid():
                return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, *args, **kwargs):  # to fix/create default expansion for the version
        instance = self.get_object()
        expansion = instance.fix_auto_expansion()
        if expansion and expansion.id:
            return Response(ExpansionDetailSerializer(expansion).data, status=status.HTTP_200_OK)
        return Response(status=status.HTTP_400_BAD_REQUEST)

    def delete(self, _, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()

        try:
            instance.delete()
        except ValidationError as ex:
            return Response(ex.message_dict, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class CollectionVersionExpansionsView(CollectionBaseView, ListWithHeadersMixin, CreateAPIView):
    def get_serializer_class(self):
        if self.is_verbose():
            return ExpansionDetailSerializer
        return ExpansionSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [CanViewConceptDictionaryVersion()]
        return [HasAccessToVersionedObject()]

    def get_queryset(self):
        instance = get_object_or_404(super().get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance.expansions.all()

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_base_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = self.get_object()
        expansion = version.cascade_children_to_expansion(expansion_data=serializer.data)
        headers = self.get_success_headers(serializer.data)
        return Response(ExpansionSerializer(expansion).data, status=status.HTTP_201_CREATED, headers=headers)


class CollectionVersionExpansionBaseView(CollectionBaseView):
    serializer_class = ExpansionSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [CanViewConceptDictionaryVersion()]
        return [HasAccessToVersionedObject()]

    def get_object(self, queryset=None):
        expansion = self.get_queryset().first()
        if not expansion:
            raise Http404()

        return expansion

    def get_queryset(self):
        version = get_object_or_404(super().get_queryset())
        self.check_object_permissions(self.request, version)
        return version.expansions.filter(mnemonic=self.kwargs.get('expansion'))


class CollectionVersionExpansionView(CollectionVersionExpansionBaseView, RetrieveAPIView, DestroyAPIView):
    serializer_class = ExpansionSerializer
    permission_classes = (CanViewConceptDictionary, )

    def destroy(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        obj = self.get_object()
        if obj.is_default:
            return Response(dict(erors=['Cannot delete default expansion']), status=status.HTTP_400_BAD_REQUEST)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CollectionVersionExpansionChildrenView(CollectionVersionExpansionBaseView, ListWithHeadersMixin):
    def get_queryset(self):
        expansion = super().get_queryset().first()

        if not expansion:
            raise Http404()

        return expansion

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class CollectionVersionExpansionConceptsView(CollectionVersionExpansionChildrenView):
    is_searchable = True
    document_model = ConceptDocument
    es_fields = Concept.es_fields

    def get_serializer_class(self):
        from core.concepts.serializers import ConceptDetailSerializer, ConceptListSerializer
        return ConceptDetailSerializer if self.is_verbose() else ConceptListSerializer

    def get_queryset(self):
        return super().get_queryset().concepts


class CollectionVersionExpansionMappingsView(CollectionVersionExpansionChildrenView):
    is_searchable = True
    document_model = MappingDocument
    es_fields = Mapping.es_fields

    def get_serializer_class(self):
        from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer
        return MappingDetailSerializer if self.is_verbose() else MappingListSerializer

    def get_queryset(self):
        return super().get_queryset().mappings


class CollectionVersionConceptsView(CollectionBaseView, ListWithHeadersMixin):
    is_searchable = True
    document_model = ConceptDocument
    es_fields = Concept.es_fields

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_base_queryset())
        self.check_object_permissions(self.request, instance)
        return instance.expansion

    def get_serializer_class(self):
        return Concept.get_serializer_class(verbose=self.is_verbose(), version=True, brief=self.is_brief())

    def get_queryset(self):
        expansion = self.get_object()
        if expansion:
            return expansion.concepts

        return Concept.objects.none()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class CollectionVersionConceptRetrieveView(CollectionBaseView, RetrieveAPIView):
    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_base_queryset())
        self.check_object_permissions(self.request, instance)
        expansion = instance.expansion
        if not expansion:
            raise Http404()
        concepts = expansion.concepts.filter(mnemonic=self.kwargs['concept'])
        if 'concept_version' in self.kwargs:
            concepts = concepts.filter(version=self.kwargs['concept_version'])

        uri_param = self.request.query_params.dict().get('uri')
        if uri_param:
            concepts = concepts.filter(**Concept.get_parent_and_owner_filters_from_uri(uri_param))
        count = concepts.count()
        if count == 0:
            raise Http404()
        if count > 1 and not uri_param:
            raise Http409()

        return concepts.first()

    def get_serializer_class(self):
        return Concept.get_serializer_class(verbose=self.is_verbose(), version=True, brief=self.is_brief())


class CollectionVersionMappingsView(CollectionBaseView, ListWithHeadersMixin):
    is_searchable = True
    document_model = MappingDocument
    es_fields = Mapping.es_fields

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_base_queryset())
        self.check_object_permissions(self.request, instance)
        return instance.expansion

    def get_serializer_class(self):
        return Mapping.get_serializer_class(verbose=self.is_verbose(), version=True, brief=self.is_brief())

    def get_queryset(self):
        expansion = self.get_object()
        if expansion:
            return expansion.mappings

        return Mapping.objects.none()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class CollectionVersionMappingRetrieveView(CollectionBaseView, RetrieveAPIView):
    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_base_queryset())
        self.check_object_permissions(self.request, instance)
        expansion = instance.expansion
        if not expansion:
            raise Http404()
        mappings = expansion.mappings.filter(mnemonic=self.kwargs['mapping'])
        if 'mapping_version' in self.kwargs:
            mappings = mappings.filter(version=self.kwargs['mapping_version'])

        uri_param = self.request.query_params.dict().get('uri')
        if uri_param:
            mappings = mappings.filter(**Mapping.get_parent_and_owner_filters_from_uri(uri_param))
        count = mappings.count()
        if count == 0:
            raise Http404()
        if count > 1 and not uri_param:
            raise Http409()

        return mappings.first()

    def get_serializer_class(self):
        return Mapping.get_serializer_class(verbose=self.is_verbose(), version=True, brief=self.is_brief())


class CollectionExtrasBaseView(CollectionBaseView):
    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset(), version=HEAD)


class CollectionExtrasView(CollectionExtrasBaseView, ListAPIView):
    serializer_class = CollectionDetailSerializer

    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class CollectionExtraRetrieveUpdateDestroyView(CollectionExtrasBaseView,
                                               ConceptContainerExtraRetrieveUpdateDestroyView):
    serializer_class = CollectionDetailSerializer


class CollectionVersionProcessingView(CollectionBaseView, ConceptContainerProcessingMixin):
    serializer_class = CollectionVersionDetailSerializer
    resource = 'collection'


class CollectionVersionExportView(ConceptContainerExportMixin, CollectionVersionBaseView):
    entity = 'Collection'
    permission_classes = (CanViewConceptDictionary,)
    serializer_class = CollectionVersionExportSerializer

    def handle_export_version(self):
        version = self.get_object()
        try:
            export_collection.delay(version.id)
            return status.HTTP_202_ACCEPTED
        except AlreadyQueued:
            return status.HTTP_409_CONFLICT


class CollectionSummaryView(CollectionBaseView, RetrieveAPIView, CreateAPIView):
    serializer_class = CollectionSummaryDetailSerializer

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAdminUser()]
        return [CanViewConceptDictionary()]

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        self.perform_update()
        return Response(status=status.HTTP_202_ACCEPTED)

    def perform_update(self):
        instance = self.get_object()
        instance.update_children_counts()


class CollectionVersionSummaryView(CollectionBaseView, RetrieveAPIView):
    serializer_class = CollectionVersionSummaryDetailSerializer

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAdminUser()]
        return [CanViewConceptDictionary()]

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        self.perform_update()
        return Response(status=status.HTTP_202_ACCEPTED)

    def perform_update(self):
        instance = self.get_object()
        instance.update_children_counts()


class CollectionLatestVersionSummaryView(CollectionVersionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = CollectionVersionSummaryDetailSerializer
    permission_classes = (CanViewConceptDictionary,)

    def get_object(self, queryset=None):
        obj = self.get_queryset().first()
        if not obj:
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset().filter(released=True)
        return queryset.order_by('-created_at')


class CollectionClientConfigsView(CollectionBaseView, ResourceClientConfigsView):
    lookup_field = 'collection'
    model = Collection
    queryset = Collection.objects.filter(is_active=True, version='HEAD')
    permission_classes = (CanViewConceptDictionary, )
