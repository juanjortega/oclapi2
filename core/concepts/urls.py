from django.urls import path
from django.views.decorators.cache import cache_page

from core.concepts.feeds import ConceptFeed
from . import views

urlpatterns = [
    path('', views.ConceptListView.as_view(), name='concept-list'),
    path(
        'lookup/',
        cache_page(
            timeout=60 * 60 * 24, key_prefix='cache_lookup'
        )(views.ConceptLookupValuesView.as_view()),
        name='concept-lookup-list'
    ),
    path(
        "<str:concept>/",
        views.ConceptRetrieveUpdateDestroyView.as_view(),
        name='concept-detail'
    ),
    path(
        "<str:concept>/$cascade/",
        views.ConceptCascadeView.as_view(),
        name='concept-$cascade'
    ),
    # duplicate due to swagger not accepting $cascade
    path(
        "<str:concept>/cascade/",
        views.ConceptCascadeView.as_view(),
        name='concept-cascade'
    ),
    path(
        "<str:concept>/collection-versions/",
        views.ConceptCollectionMembershipView.as_view(),
        name='concept-collection-versions'
    ),
    path(
        "<str:concept>/summary/",
        views.ConceptSummaryView.as_view(),
        name='concept-summary'
    ),
    path(
        "<str:concept>/reactivate/",
        views.ConceptReactivateView.as_view(),
        name='concept-reactivate'
    ),
    path(
        "<str:concept>/children/",
        views.ConceptChildrenView.as_view(),
        name='concept-children'
    ),
    path(
        "<str:concept>/parents/",
        views.ConceptParentsView.as_view(),
        name='concept-parents'
    ),
    path('<str:concept>/atom/', ConceptFeed()),
    path(
        "<str:concept>/descriptions/",
        views.ConceptDescriptionListCreateView.as_view(),
        name='concept-descriptions'
    ),
    path(
        '<str:concept>/descriptions/<str:uuid>/',
        views.ConceptDescriptionRetrieveUpdateDestroyView.as_view(),
        name='concept-description'
    ),
    path(
        "<str:concept>/names/",
        views.ConceptNameListCreateView.as_view(),
        name='concept-names'
    ),
    path(
        '<str:concept>/names/<str:uuid>/',
        views.ConceptNameRetrieveUpdateDestroyView.as_view(),
        name='concept-name'
    ),
    path(
        '<str:concept>/extras/',
        views.ConceptExtrasView.as_view(),
        name='concept-extras'
    ),
    path(
        '<str:concept>/extras/<str:extra>/',
        views.ConceptExtraRetrieveUpdateDestroyView.as_view(),
        name='concept-extra'
    ),
    path(
        "<str:concept>/versions/",
        views.ConceptVersionsView.as_view(),
        name='concept-version-list'
    ),
    path(
        "<str:concept>/mappings/",
        views.ConceptMappingsView.as_view(),
        name='concept-mapping-list'
    ),
    path(
        '<str:concept>/<str:concept_version>/',
        views.ConceptVersionRetrieveView.as_view(),
        name='concept-version-detail'
    ),
    path(
        "<str:concept>/<str:concept_version>/$cascade/",
        views.ConceptCascadeView.as_view(),
        name='concept-version-$cascade'
    ),
    # duplicate due to swagger not accepting $cascade
    path(
        "<str:concept>/<str:concept_version>/cascade/",
        views.ConceptCascadeView.as_view(),
        name='concept-version-cascade'
    ),
    path(
        '<str:concept>/<str:concept_version>/collection-versions/',
        views.ConceptCollectionMembershipView.as_view(),
        name='concept-version-collection-versions'
    ),
    path(
        '<str:concept>/<str:concept_version>/summary/',
        views.ConceptSummaryView.as_view(),
        name='concept-version-summary'
    ),
    path(
        '<str:concept>/<str:concept_version>/mappings/',
        views.ConceptMappingsView.as_view(),
        name='concept-version-mapping-list'
    ),
    path(
        '<str:concept>/<str:concept_version>/descriptions/',
        views.ConceptDescriptionListCreateView.as_view(),
        name='concept-descriptions'
    ),
    path(
        '<str:concept>/<str:concept_version>/descriptions/<str:uuid>/',
        views.ConceptDescriptionRetrieveUpdateDestroyView.as_view(),
        name='concept-name'
    ),
    path(
        '<str:concept>/<str:concept_version>/extras/',
        views.ConceptExtrasView.as_view(),
        name='concept-extras'
    ),
    path(
        '<str:concept>/<str:concept_version>/extras/<str:extra>/',
        views.ConceptExtraRetrieveUpdateDestroyView.as_view(),
        name='concept-extra'
    ),
    path(
        '<str:concept>/<str:concept_version>/names/',
        views.ConceptNameListCreateView.as_view(),
        name='concept-names'
    ),
    path(
        '<str:concept>/<str:concept_version>/names/<str:uuid>/',
        views.ConceptNameRetrieveUpdateDestroyView.as_view(),
        name='concept-name'
    ),
]
