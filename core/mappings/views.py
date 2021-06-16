from django.db.models import F
from django.http import QueryDict, Http404
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.generics import DestroyAPIView, UpdateAPIView, RetrieveAPIView, ListAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.common.constants import HEAD
from core.common.exceptions import Http409
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.swagger_parameters import (
    q_param, limit_param, sort_desc_param, page_param, exact_match_param, sort_asc_param, verbose_param,
    include_facets_header, updated_since_param, include_retired_param,
    compress_header, include_source_versions_param, include_collection_versions_param)
from core.common.views import SourceChildCommonBaseView, SourceChildExtrasView, \
    SourceChildExtraRetrieveUpdateDestroyView
from core.concepts.permissions import CanEditParentDictionary, CanViewParentDictionary
from core.mappings.constants import PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_MAPPING
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.search import MappingSearch
from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer, MappingVersionListSerializer, \
    MappingVersionDetailSerializer


class MappingBaseView(SourceChildCommonBaseView):
    lookup_field = 'mapping'
    model = Mapping
    queryset = Mapping.objects.filter(is_active=True)
    document_model = MappingDocument
    facet_class = MappingSearch
    es_fields = Mapping.es_fields

    @staticmethod
    def get_detail_serializer(obj, data=None, files=None, partial=False):
        return MappingDetailSerializer(obj, data, files, partial)

    def get_queryset(self):
        return Mapping.get_base_queryset(self.params)


class MappingListView(MappingBaseView, ListWithHeadersMixin, CreateModelMixin):
    serializer_class = MappingListSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [CanEditParentDictionary(), ]

        return [CanViewParentDictionary(), ]

    def get_serializer_class(self):
        if (self.request.method == 'GET' and self.is_verbose()) or self.request.method == 'POST':
            return MappingDetailSerializer

        return MappingListSerializer

    def get_queryset(self):
        is_latest_version = 'collection' not in self.kwargs and 'version' not in self.kwargs
        queryset = super().get_queryset()
        if is_latest_version:
            queryset = queryset.filter(is_latest_version=True)

        return queryset.select_related(
            'parent__organization', 'parent__user', 'from_concept__parent', 'to_concept__parent', 'to_source',
            'versioned_object', 'from_source',
        )

    def get(self, request, *args, **kwargs):
        self.set_parent_resource(False)
        if self.parent_resource:
            self.check_object_permissions(request, self.parent_resource)
        return self.list(request, *args, **kwargs)

    def set_parent_resource(self, __pop=True):
        from core.sources.models import Source
        source = self.kwargs.pop('source', None) if __pop else self.kwargs.get('source', None)
        collection = self.kwargs.pop('collection', None) if __pop else self.kwargs.get('collection', None)
        container_version = self.kwargs.pop('version', HEAD) if __pop else self.kwargs.get('version', HEAD)
        parent_resource = None
        if 'org' in self.kwargs:
            filters = dict(organization__mnemonic=self.kwargs['org'])
        else:
            username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
            filters = dict(user__username=username)
        if source:
            parent_resource = Source.get_version(source, container_version or HEAD, filters)
        if collection:
            from core.collections.models import Collection
            parent_resource = Collection.get_version(source, container_version or HEAD, filters)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        self.set_parent_resource()
        data = request.data.dict() if isinstance(request.data, QueryDict) else request.data
        serializer = self.get_serializer(data={
            **data, 'parent_id': self.parent_resource.id
        })
        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MappingRetrieveUpdateDestroyView(MappingBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    serializer_class = MappingDetailSerializer

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        filters = dict(id=F('versioned_object_id'))
        if 'collection' in self.kwargs:
            filters = dict()
            queryset = queryset.order_by('id').distinct('id')
            uri_param = self.request.query_params.dict().get('uri')
            if uri_param:
                filters.update(Mapping.get_parent_and_owner_filters_from_uri(uri_param))
            if queryset.count() > 1 and not uri_param:
                raise Http409()

        instance = queryset.filter(**filters).first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance

    def get_permissions(self):
        if self.request.method in ['GET']:
            return [CanViewParentDictionary(), ]

        if self.request.method == 'DELETE' and self.is_hard_delete_requested():
            return [IsAdminUser(), ]

        return [CanEditParentDictionary(), ]

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        self.object = self.get_object()
        self.parent_resource = self.object.parent

        if not self.parent_resource.is_head:
            return Response(
                {'non_field_errors': PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_MAPPING},
                status=status.HTTP_400_BAD_REQUEST
            )
        self.object = self.object.clone()
        serializer = self.get_serializer(self.object, data=request.data, partial=partial)
        success_status_code = status.HTTP_200_OK

        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                return Response(serializer.data, status=success_status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def is_hard_delete_requested(self):
        return self.request.query_params.get('hardDelete', None) in ['true', True, 'True']

    def destroy(self, request, *args, **kwargs):
        mapping = self.get_object()
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        if self.is_hard_delete_requested():
            mapping.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        errors = mapping.retire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class MappingReactivateView(MappingBaseView, UpdateAPIView):
    serializer_class = MappingDetailSerializer

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset(), id=F('versioned_object_id'))
        self.check_object_permissions(self.request, instance)
        return instance

    def get_permissions(self):
        if self.request.method in ['GET']:
            return [CanViewParentDictionary(), ]

        return [CanEditParentDictionary(), ]

    def update(self, request, *args, **kwargs):
        mapping = self.get_object()
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        errors = mapping.unretire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class MappingVersionsView(MappingBaseView, ConceptDictionaryMixin, ListWithHeadersMixin):
    permission_classes = (CanViewParentDictionary,)

    def get_queryset(self):
        queryset = super().get_queryset()
        instance = queryset.first()

        self.check_object_permissions(self.request, instance)

        return queryset.exclude(id=F('versioned_object_id'))

    def get_serializer_class(self):
        return MappingVersionDetailSerializer if self.is_verbose() else MappingVersionListSerializer

    @swagger_auto_schema(
        manual_parameters=[
            include_source_versions_param, include_collection_versions_param
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class MappingVersionRetrieveView(MappingBaseView, RetrieveAPIView):
    serializer_class = MappingVersionDetailSerializer
    permission_classes = (CanViewParentDictionary,)

    def get_object(self, queryset=None):
        instance = self.get_queryset().first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance


class MappingVersionListAllView(MappingBaseView, ListWithHeadersMixin):
    permission_classes = (CanViewParentDictionary,)

    def get_serializer_class(self):
        return MappingDetailSerializer if self.is_verbose() else MappingListSerializer

    def get_queryset(self):
        return Mapping.global_listing_queryset(
            self.get_filter_params(), self.request.user
        ).select_related(
            'parent__organization', 'parent__user',
        )

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, page_param, verbose_param,
            include_retired_param, updated_since_param,
            include_facets_header, compress_header
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class MappingExtrasView(SourceChildExtrasView, MappingBaseView):
    serializer_class = MappingVersionDetailSerializer


class MappingExtraRetrieveUpdateDestroyView(SourceChildExtraRetrieveUpdateDestroyView, MappingBaseView):
    serializer_class = MappingVersionDetailSerializer
    model = Mapping


class MappingDebugRetrieveDestroyView(ListAPIView):
    permission_classes = (IsAdminUser, )
    serializer_class = MappingVersionDetailSerializer

    def get_queryset(self):
        params = self.request.query_params.dict()
        if not params:
            Mapping.objects.none()
        to_concept_code = params.pop('to_concept_code', None)
        from_concept_code = params.pop('from_concept_code', None)
        filters = params
        if to_concept_code:
            filters['to_concept_code__icontains'] = to_concept_code
        if from_concept_code:
            filters['from_concept_code__icontains'] = from_concept_code

        return Mapping.objects.filter(**filters)
