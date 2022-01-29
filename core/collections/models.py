from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import UniqueConstraint
from django.utils import timezone
from pydash import get

from core.collections.constants import (
    COLLECTION_TYPE, CONCEPTS_EXPRESSIONS,
    MAPPINGS_EXPRESSIONS,
    REFERENCE_ALREADY_EXISTS, CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE,
    CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE, ALL_SYMBOL, COLLECTION_VERSION_TYPE)
from core.collections.utils import is_concept, is_mapping
from core.common.constants import (
    DEFAULT_REPOSITORY_TYPE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT,
    ACCESS_TYPE_NONE, SEARCH_PARAM)
from core.common.models import ConceptContainerModel, BaseResourceModel
from core.common.tasks import seed_children_to_expansion, batch_index_resources, index_expansion_concepts, \
    index_expansion_mappings
from core.common.utils import drop_version, to_owner_uri, generate_temp_version, api_get
from core.concepts.constants import LOCALES_FULLY_SPECIFIED
from core.concepts.models import Concept
from core.mappings.models import Mapping


class Collection(ConceptContainerModel):
    OBJECT_TYPE = COLLECTION_TYPE
    OBJECT_VERSION_TYPE = COLLECTION_VERSION_TYPE
    es_fields = {
        'collection_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': True, 'filterable': True},
        'experimental': {'sortable': False, 'filterable': False, 'facet': False},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': False},
    }

    class Meta:
        db_table = 'collections'
        constraints = [
            UniqueConstraint(
                fields=['mnemonic', 'version', 'organization'],
                name="org_collection_unique",
                condition=models.Q(user=None),
            ),
            UniqueConstraint(
                fields=['mnemonic', 'version', 'user'],
                name="user_collection_unique",
                condition=models.Q(organization=None),
            )
        ]
        indexes = [] + ConceptContainerModel.Meta.indexes

    collection_type = models.TextField(blank=True)
    preferred_source = models.TextField(blank=True)
    repository_type = models.TextField(default=DEFAULT_REPOSITORY_TYPE, blank=True)
    custom_resources_linked_source = models.TextField(blank=True)
    concepts = models.ManyToManyField('concepts.Concept', blank=True, related_name='collection_set')
    mappings = models.ManyToManyField('mappings.Mapping', blank=True, related_name='collection_set')
    references = models.ManyToManyField('collections.CollectionReference', blank=True, related_name='collections')
    immutable = models.BooleanField(null=True, blank=True, default=None)
    locked_date = models.DateTimeField(null=True, blank=True)
    autoexpand_head = models.BooleanField(default=True, null=True)
    autoexpand = models.BooleanField(default=True, null=True)
    expansion_uri = models.TextField(null=True, blank=True)

    def set_active_concepts(self):
        if self.expansion_uri:
            self.active_concepts = self.expansion.concepts.filter(retired=False, is_active=True).count()

    def set_active_mappings(self):
        if self.expansion_uri:
            self.active_mappings = self.expansion.mappings.filter(retired=False, is_active=True).count()

    @staticmethod
    def get_search_document():
        from core.collections.documents import CollectionDocument
        return CollectionDocument

    @classmethod
    def get_base_queryset(cls, params):
        collection = params.pop('collection', None)
        contains_uri = params.pop('contains', None)
        include_references = params.pop('include_references', None) in [True, 'true']
        queryset = super().get_base_queryset(params)
        if collection:
            queryset = queryset.filter(cls.get_exact_or_criteria('mnemonic', collection))
        if contains_uri:
            queryset = queryset.filter(
                references__expression=contains_uri, public_access__in=[ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]
            )
        if include_references:
            queryset = queryset.prefetch_related('references')

        return queryset

    @property
    def collection(self):
        return self.mnemonic

    @staticmethod
    def get_resource_url_kwarg():
        return 'collection'

    def update_version_data(self, head):
        super().update_version_data(head)
        self.collection_type = head.collection_type
        self.preferred_source = head.preferred_source
        self.repository_type = head.repository_type
        self.custom_resources_linked_source = head.custom_resources_linked_source
        self.immutable = head.immutable
        self.locked_date = head.locked_date

    @property
    def should_auto_expand(self):
        if self.is_head:
            return self.autoexpand_head

        return self.autoexpand

    def validate(self, reference):
        if self.should_auto_expand:
            reference.full_clean()
        else:
            reference.last_resolved_at = None
        if reference.without_version in [reference.without_version for reference in self.references.all()]:
            raise ValidationError({reference.expression: [REFERENCE_ALREADY_EXISTS]})

        if self.is_openmrs_schema and self.expansion_uri:
            if reference.concepts is None or reference.concepts.count() == 0:
                return

            concept = reference.concepts[0]
            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                concept, attribute='type__in', value=LOCALES_FULLY_SPECIFIED,
                error_message=CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
            )
            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                concept, attribute='locale_preferred', value=True,
                error_message=CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
            )

    def check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
            self, concept, attribute, value, error_message
    ):
        other_concepts_in_collection = self.expansion.concepts
        if not other_concepts_in_collection.exists():
            return

        matching_names_in_concept = {}
        names = concept.names.filter(**{attribute: value})

        for name in names:
            validation_error = dict(names=[error_message])
            # making sure names in the submitted concept meet the same rule
            name_key = name.locale + name.name
            if name_key in matching_names_in_concept:
                raise ValidationError(validation_error)

            matching_names_in_concept[name_key] = True
            if other_concepts_in_collection.filter(
                    names__name=name.name, names__locale=name.locale, **{f"names__{attribute}": value}
            ).exists():
                raise ValidationError(validation_error)

    @transaction.atomic
    def add_expressions(self, data, user, cascade_mappings=False, cascade_to_concepts=False):
        expressions = data.get('expressions', [])
        concept_expressions = data.get('concepts', [])
        mapping_expressions = data.get('mappings', [])
        source = None
        source_uri = data.get('uri')
        if source_uri:
            from core.sources.models import Source
            source = Source.objects.filter(uri=source_uri).first()

        if source:
            can_view_all_content = source.can_view_all_content(user)

            def get_child_expressions(queryset):
                if source.is_head:
                    queryset = queryset.filter(is_latest_version=True)
                if not can_view_all_content:
                    queryset = queryset.filter(public_access=ACCESS_TYPE_NONE)
                return list(queryset.values_list('uri', flat=True))

            if concept_expressions == ALL_SYMBOL:
                expressions.extend(get_child_expressions(source.concepts))
            if mapping_expressions == ALL_SYMBOL:
                expressions.extend(get_child_expressions(source.mappings))
        else:
            expressions.extend(concept_expressions)
            expressions.extend(mapping_expressions)

        if cascade_mappings or cascade_to_concepts:
            expressions += self.get_all_related_uris(expressions, cascade_to_concepts)

        return self.add_references(expressions, user)

    def add_references(self, expressions, user=None):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements  # Fixme: Sny
        errors = {}
        collection_version = self.head

        new_expressions = set(expressions)
        new_versionless_expressions = {drop_version(expression): expression for expression in new_expressions}
        for reference in collection_version.references.all():
            existing_versionless_expression = reference.without_version
            if existing_versionless_expression in new_versionless_expressions:
                existing_expression = new_versionless_expressions[existing_versionless_expression]
                new_expressions.discard(existing_expression)
                errors[existing_expression] = [REFERENCE_ALREADY_EXISTS]

        added_references = []
        for expression in new_expressions:
            ref = CollectionReference(expression=expression)
            try:
                if self.autoexpand_head:
                    ref.clean()
                else:
                    ref.last_resolved_at = None
                ref.save()
            except Exception as ex:
                errors[expression] = ex.messages if hasattr(ex, 'messages') else ex
                continue

            added = False
            if ref.concepts:
                if self.is_openmrs_schema:
                    for concept in ref.concepts:
                        try:
                            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                                concept, attribute='type__in', value=LOCALES_FULLY_SPECIFIED,
                                error_message=CONCEPT_FULLY_SPECIFIED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
                            )
                            self.check_concept_uniqueness_in_collection_and_locale_by_name_attribute(
                                concept, attribute='locale_preferred', value=True,
                                error_message=CONCEPT_PREFERRED_NAME_UNIQUE_PER_COLLECTION_AND_LOCALE
                            )
                        except Exception as ex:
                            errors[expression] = ex.messages if hasattr(ex, 'messages') else ex
                            continue
                        collection_version.expansion.concepts.add(concept)
                        concept.index()
                        added = True
                else:
                    collection_version.expansion.add_references(ref)
                    added = True
            if ref.mappings:
                collection_version.expansion.add_references(ref)
                added = True
            if not added and ref.id:
                added = True

            if added:
                collection_version.references.add(ref)
                self.references.add(ref)
                added_references.append(ref)

        if user and user.is_authenticated:
            collection_version.updated_by = user
            self.updated_by = user
        collection_version.save()
        collection_version.update_children_counts()
        if collection_version.id != self.id:
            self.save()
        return added_references, errors

    def seed_references(self):
        head = self.head
        if head:
            references = CollectionReference.objects.bulk_create(
                [CollectionReference(expression=ref.expression) for ref in head.references.all()]
            )
            self.references.set(references)

    @staticmethod
    def is_validation_necessary():
        return False

    @property
    def expansions_count(self):
        return self.expansions.count()

    def delete_references(self, expressions):
        if self.expansion_uri:
            self.expansion.delete_expressions(expressions)

        self.references.set(self.references.exclude(expression__in=expressions))

    def get_all_related_uris(self, expressions, cascade_to_concepts=False):
        all_related_mappings = []
        unversioned_mappings = []
        concept_expressions = []

        for expression in expressions:
            if is_mapping(expression):
                unversioned_mappings.append(drop_version(expression))
            elif is_concept(expression):
                concept_expressions.append(expression)

        for concept_expression in concept_expressions:
            ref = CollectionReference(expression=concept_expression)
            try:
                self.validate(ref)
                all_related_mappings += ref.get_related_uris(unversioned_mappings, cascade_to_concepts)
            except:  # pylint: disable=bare-except
                continue

        return all_related_mappings

    def get_cascaded_mapping_uris_from_concept_expressions(self, expressions):
        mapping_uris = []

        if not self.expansion_uri:
            return mapping_uris

        for expression in expressions:
            if is_concept(expression):
                mapping_uris += list(
                    self.expansion.mappings.filter(
                        from_concept__uri__icontains=drop_version(expression)).values_list('uri', flat=True)
                )

        return mapping_uris

    # Fixes auto expansions by upserting
    # This should be deleted when all the old style collections are migrated to new style
    def fix_auto_expansion(self):
        if self.should_auto_expand:
            expansion = self.expansion
            if not expansion:
                expansion = Expansion(mnemonic=f'autoexpand-{self.version}', collection_version=self)
                expansion.save()
                self.expansion_uri = expansion.uri
                self.save()
            expansion.concepts.set(self.concepts.all())
            expansion.mappings.set(self.mappings.all())
            expansion.index_concepts()
            expansion.index_mappings()
            return expansion

        return None

    def cascade_children_to_expansion(self, expansion_data=None, index=True):  # pylint: disable=arguments-differ
        if not expansion_data:
            expansion_data = {}
        should_auto_expand = self.should_auto_expand

        if should_auto_expand and not self.expansions.exists():
            expansion_data['mnemonic'] = f'autoexpand-{self.version}'
        expansion = Expansion.persist(index=index, **expansion_data, collection_version=self)

        if should_auto_expand and not self.expansion_uri:
            self.expansion_uri = expansion.uri
            self.save()

        return expansion

    def index_children(self):
        if self.expansion_uri:
            from core.concepts.documents import ConceptDocument
            from core.mappings.documents import MappingDocument

            self.batch_index(self.expansion.concepts, ConceptDocument)
            self.batch_index(self.expansion.mappings, MappingDocument)

    @property
    def expansion(self):
        if self.expansion_uri:
            return self.expansions.filter(uri=self.expansion_uri).first()

        return None

    @property
    def active_references(self):
        return self.references.count()

    @property
    def last_concept_update(self):
        updated_at = None
        if self.expansion_uri and self.expansion.concepts.exists():
            updated_at = self.expansion.concepts.latest('updated_at').updated_at
        return updated_at

    @property
    def last_mapping_update(self):
        updated_at = None
        if self.expansion_uri and self.expansion.mappings.exists():
            updated_at = self.expansion.mappings.latest('updated_at').updated_at
        return updated_at

    @property
    def expansions_url(self):
        return self.uri + 'expansions/'


class CollectionReference(models.Model):
    class Meta:
        db_table = 'collection_references'

    concepts = None
    mappings = None
    original_expression = None

    id = models.BigAutoField(primary_key=True)
    expression = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_resolved_at = models.DateTimeField(default=timezone.now, null=True)

    @property
    def without_version(self):
        return drop_version(self.expression)

    @property
    def reference_type(self):
        reference = None
        if is_concept(self.expression):
            reference = CONCEPTS_EXPRESSIONS
        if is_mapping(self.expression):
            reference = MAPPINGS_EXPRESSIONS

        return reference

    def fetch_concepts(self, user):
        if self.should_fetch_from_api:
            return Concept.objects.filter(uri__in=self.fetch_uris(user))
        return self.get_concepts()

    def fetch_mappings(self, user):
        if self.should_fetch_from_api:
            return Mapping.objects.filter(uri__in=self.fetch_uris(user))
        return self.get_mappings()

    def fetch_uris(self, user):
        data = api_get(self.expression, user)
        if not isinstance(data, list):
            data = [data]
        return [obj['version_url'] for obj in data]

    def get_concepts(self):
        return Concept.from_uri_queryset(self.expression)

    def get_mappings(self):
        return Mapping.from_uri_queryset(self.expression)

    def clean(self):
        self.original_expression = str(self.expression)

        self.create_entities_from_expressions()
        is_resolved = bool((self.mappings and self.mappings.count()) or (self.concepts and self.concepts.count()))
        if not is_resolved:
            self.last_resolved_at = None

    @property
    def should_fetch_from_api(self):
        return SEARCH_PARAM in get(self.expression.split('?'), '1', '')

    @property
    def is_concept(self):
        return is_concept(self.expression)

    @property
    def is_mapping(self):
        return is_mapping(self.expression)

    def create_entities_from_expressions(self):
        if self.is_concept:
            self.concepts = self.get_concepts()
        elif self.is_mapping:
            self.mappings = self.get_mappings()

        if self.concepts and self.concepts.exists():
            self.expression = self.concepts.first().uri
        elif self.mappings and self.mappings.exists():
            self.expression = self.mappings.first().uri

    def get_related_uris(self, exclude_mapping_uris, cascade_to_concepts=False):
        uris = []
        concepts = self.get_concepts()
        if concepts.exists():
            for concept in concepts:
                mapping_queryset = concept.get_unidirectional_mappings().exclude(uri__in=exclude_mapping_uris)
                uris = list(mapping_queryset.values_list('uri', flat=True))
                if cascade_to_concepts:
                    to_concepts_queryset = mapping_queryset.filter(to_concept__parent_id=concept.parent_id)
                    uris += list(to_concepts_queryset.values_list('to_concept__uri', flat=True))

        return uris


def default_expansion_parameters():
    return {
        "filter": "",
        "date": "",
        "count": 0,
        "offset": 0,
        "includeDesignations": True,
        "activeOnly": False,
        "includeDefinition": False,
        "excludeNested": True,
        "excludeNotForUI": True,
        "excludePostCoordinated": True,
        "exclude - system": "",
        "system - version": "",
        "check - system - version": "",
        "force - system - version": ""
    }


class Expansion(BaseResourceModel):
    class Meta:
        db_table = 'collection_expansions'
        indexes = [] + BaseResourceModel.Meta.indexes

    parameters = models.JSONField(default=default_expansion_parameters)
    canonical_url = models.URLField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    concepts = models.ManyToManyField('concepts.Concept', blank=True, related_name='expansion_set')
    mappings = models.ManyToManyField('mappings.Mapping', blank=True, related_name='expansion_set')
    collection_version = models.ForeignKey(
        'collections.Collection', related_name='expansions', on_delete=models.CASCADE)

    @staticmethod
    def get_resource_url_kwarg():
        return 'expansion'

    @staticmethod
    def get_url_kwarg():
        return 'expansion'

    @property
    def is_default(self):
        return self.uri == self.collection_version.expansion_uri

    @property
    def expansion(self):
        return self.mnemonic

    @property
    def active_concepts(self):
        return self.concepts.count()

    @property
    def active_mappings(self):
        return self.mappings.count()

    @property
    def owner_url(self):
        return to_owner_uri(self.uri)

    def apply_parameters(self, queryset):
        parameters = ExpansionParameters(self.parameters)
        return parameters.apply(queryset)

    def index_concepts(self):
        if self.concepts.exists():
            if get(settings, 'TEST_MODE', False):
                index_expansion_concepts(self.id)
            else:
                index_expansion_concepts.apply_async((self.id, ), queue='indexing')

    def index_mappings(self):
        if self.mappings.exists():
            if get(settings, 'TEST_MODE', False):
                index_expansion_mappings(self.id)
            else:
                index_expansion_mappings.apply_async((self.id, ), queue='indexing')

    def delete_expressions(self, expressions):
        self.concepts.set(self.concepts.exclude(uri__in=expressions))
        self.mappings.set(self.mappings.exclude(uri__in=expressions))

        if not get(settings, 'TEST_MODE', False):
            batch_index_resources.apply_async(('concept', dict(uri__in=expressions)), queue='indexing')
            batch_index_resources.apply_async(('mapping', dict(uri__in=expressions)), queue='indexing')

    def add_references(self, references, index=True):
        if isinstance(references, CollectionReference):
            refs = [references]
        elif isinstance(references, list):
            refs = references
        else:
            refs = references.all()

        index_concepts = False
        index_mappings = False
        for reference in refs:
            if reference.is_concept:
                concepts = reference.fetch_concepts(self.created_by)
                if concepts.exists():
                    index_concepts = True
                    self.concepts.add(*self.apply_parameters(concepts))
            elif reference.is_mapping:
                mappings = reference.fetch_mappings(self.created_by)
                if mappings.exists():
                    index_mappings = True
                    self.mappings.add(*self.apply_parameters(mappings))

        if index:
            if index_concepts:
                self.index_concepts()
            if index_mappings:
                self.index_mappings()

    def seed_children(self, index=True):
        return self.add_references(self.collection_version.references, index)

    def calculate_uri(self):
        version = self.collection_version
        if version.is_head:
            return self.collection_version.uri + f'HEAD/expansions/{self.mnemonic}/'
        return self.collection_version.uri + f'expansions/{self.mnemonic}/'

    def clean(self):
        if not self.parameters:
            self.parameters = default_expansion_parameters()

        super().clean()

    @classmethod
    def persist(cls, index, **kwargs):
        expansion = cls(**kwargs)
        temp_version = not bool(expansion.mnemonic)
        if temp_version:
            expansion.mnemonic = generate_temp_version()
        expansion.clean()
        expansion.full_clean()
        expansion.save()
        if temp_version and expansion.id:
            expansion.mnemonic = expansion.id
            expansion.save()

        if get(settings, 'TEST_MODE', False):
            seed_children_to_expansion(expansion.id, index)
        else:
            seed_children_to_expansion.delay(expansion.id, index)

        return expansion


class ExpansionParameters:
    ACTIVE = 'activeOnly'

    def __init__(self, parameters):
        self.parameters = parameters
        self.parameter_classes = {}
        self.filters = {}
        self.to_parameter_classes()
        self.get_filters()

    def to_parameter_classes(self):
        for parameter, value in self.parameters.items():
            if parameter == self.ACTIVE:
                self.parameter_classes[parameter] = ExpansionActiveParameter(value=value)

    def apply(self, queryset):
        queryset = queryset.filter(**self.filters)
        return queryset

    def get_filters(self):
        for _, klass in self.parameter_classes.items():
            if klass.can_apply_filters:
                self.filters = {**self.filters, **klass.filters}


class ExpansionParameter:
    can_apply_filters = True

    def __init__(self, value):
        self.value = value


class ExpansionActiveParameter(ExpansionParameter):
    default_filters = dict(is_active=True, retired=False)

    @property
    def filters(self):
        if self.value is True:
            return self.default_filters

        return {}
