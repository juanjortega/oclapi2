from django.db.models import Q
from pydash import compact, get

from core.bundles.constants import BUNDLE_TYPE_SEARCHSET, RESOURCE_TYPE
from core.collections.constants import SOURCE_MAPPINGS, SOURCE_TO_CONCEPTS
from core.common.constants import INCLUDE_MAPPINGS_PARAM, CASCADE_LEVELS_PARAM, CASCADE_MAPPINGS_PARAM, \
    CASCADE_HIERARCHY_PARAM, CASCADE_METHOD_PARAM, MAP_TYPES_PARAM, EXCLUDE_MAP_TYPES_PARAM


class BundleMeta:
    def __init__(self, root):
        self.root = root

    @property
    def last_updated(self):
        return self.root.updated_at

    def to_dict(self):
        return dict(lastUpdated=self.last_updated)


class Bundle:
    def __init__(self, root, params=None, verbose=False):
        self.verbose = verbose
        self.brief = not verbose
        self.root = root
        self.params = params
        self.cascade_hierarchy = True
        self.cascade_mappings = True
        self.cascade_levels = '*'
        self.include_mappings = True
        self._meta = BundleMeta(self.root)
        self.concepts = None
        self.mappings = None
        self._total = None
        self.cascade_method = SOURCE_TO_CONCEPTS
        self.mappings_criteria = Q()
        self.entries = []

    def set_cascade_parameters(self):
        self.set_cascade_method()
        self.set_cascade_hierarchy()
        self.set_cascade_mappings()
        self.set_cascade_mappings_criteria()
        self.set_cascade_levels()
        self.set_include_mappings()

    def set_include_mappings(self):
        if INCLUDE_MAPPINGS_PARAM in self.params:
            self.include_mappings = self.params[INCLUDE_MAPPINGS_PARAM] in ['true', True]

    def set_cascade_levels(self):
        if CASCADE_LEVELS_PARAM in self.params:
            level = self.params.get(CASCADE_LEVELS_PARAM, '*')
            if level != '*' and level:
                self.cascade_levels = int(level)

    def set_cascade_mappings(self):
        if CASCADE_MAPPINGS_PARAM in self.params:
            self.cascade_mappings = self.params[CASCADE_MAPPINGS_PARAM] in ['true', True]

    def set_cascade_hierarchy(self):
        if CASCADE_HIERARCHY_PARAM in self.params:
            self.cascade_hierarchy = self.params[CASCADE_HIERARCHY_PARAM] in ['true', True]

    def set_cascade_method(self):
        if CASCADE_METHOD_PARAM in self.params:
            self.cascade_method = self.params.get(CASCADE_METHOD_PARAM, '').lower()

    def set_cascade_mappings_criteria(self):
        map_types = self.params.dict().get(MAP_TYPES_PARAM, None)
        exclude_map_types = self.params.dict().get(EXCLUDE_MAP_TYPES_PARAM, None)
        if map_types:
            self.mappings_criteria &= Q(map_type__in=compact(map_types.split(',')))
        if exclude_map_types:
            self.mappings_criteria &= ~Q(map_type__in=compact(exclude_map_types.split(',')))

    @property
    def resource_type(self):
        return RESOURCE_TYPE

    @property
    def id(self):  # pylint: disable=invalid-name
        return self.root.mnemonic

    @property
    def timestamp(self):  # pylint: disable=invalid-name
        return self.root.updated_at

    @property
    def meta(self):
        return self._meta.to_dict()

    @property
    def bundle_type(self):
        return BUNDLE_TYPE_SEARCHSET

    @property
    def total(self):
        if self._total:
            return self._total
        self.set_total()
        return self._total

    def set_total(self):
        total = self.concepts.count()
        total += self.mappings.count()

        self._total = total

    def cascade(self):
        self.set_cascade_parameters()
        result = self.root.cascade(
            source_mappings=self.cascade_method == SOURCE_MAPPINGS,
            source_to_concepts=self.cascade_method == SOURCE_TO_CONCEPTS,
            mappings_criteria=self.mappings_criteria,
            cascade_mappings=self.cascade_mappings,
            cascade_hierarchy=self.cascade_hierarchy,
            cascade_levels=self.cascade_levels,
            include_mappings=self.include_mappings,
        )
        self.concepts = get(result, 'concepts')
        self.mappings = get(result, 'mappings')
        self.set_total()
        self.set_entries()

    @property
    def entry(self):
        if self.entries:
            return self.entries
        self.set_entries()
        return self.entries

    def set_entries(self):
        from core.concepts.models import Concept
        serializer = Concept.get_serializer_class(verbose=self.verbose, version=True, brief=self.brief)
        self.entries += serializer(self.concepts, many=True).data
        from core.mappings.models import Mapping
        serializer = Mapping.get_serializer_class(verbose=self.verbose, version=True, brief=self.brief)
        self.entries += serializer(self.mappings, many=True).data
