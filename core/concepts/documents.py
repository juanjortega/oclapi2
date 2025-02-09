from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from elasticsearch_dsl import analyzer

from core.common.utils import jsonify_safe, flatten_dict
from core.concepts.models import Concept, LocalizedText
from core.sources.models import Source

folding_analyzer = analyzer('folding_analyzer',
                            tokenizer="standard",
                            filter=["lowercase", "asciifolding"]
   )

@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    id = fields.KeywordField(attr='mnemonic', normalizer="lowercase")
    numeric_id = fields.LongField()
    name = fields.TextField()
    _name = fields.KeywordField(attr='display_name', normalizer='lowercase')
    last_update = fields.DateField(attr='updated_at')
    locale = fields.ListField(fields.KeywordField())
    synonyms = fields.ListField(fields.KeywordField(normalizer="lowercase"))
    source = fields.KeywordField(attr='parent_resource', normalizer="lowercase")
    owner = fields.KeywordField(attr='owner_name', normalizer="lowercase")
    owner_type = fields.KeywordField(attr='owner_type')
    source_version = fields.ListField(fields.KeywordField())
    collection_version = fields.ListField(fields.KeywordField())
    expansion = fields.ListField(fields.KeywordField())
    collection = fields.ListField(fields.KeywordField())
    collection_url = fields.ListField(fields.KeywordField())
    collection_owner_url = fields.ListField(fields.KeywordField())
    public_can_view = fields.BooleanField(attr='public_can_view')
    datatype = fields.KeywordField(attr='datatype', normalizer="lowercase")
    concept_class = fields.KeywordField(attr='concept_class', normalizer="lowercase")
    retired = fields.KeywordField(attr='retired')
    is_active = fields.KeywordField(attr='is_active')
    is_latest_version = fields.KeywordField(attr='is_latest_version')
    extras = fields.ObjectField(dynamic=True)
    created_by = fields.KeywordField(attr='created_by.username')
    source_canonical_url = fields.KeywordField(attr='parent.canonical_url')
    name_types = fields.ListField(fields.KeywordField())
    description_types = fields.ListField(fields.KeywordField())
    name_locales = fields.NestedField(attr='names', properties={
        'name': fields.TextField(analyzer=folding_analyzer),
        'type': fields.TextField(),
        'locale': fields.TextField(),
        'locale_preferred': fields.BooleanField(),
        'external_id': fields.TextField(),
        'pk': fields.IntegerField()
    })
    class Django:
        model = Concept
        fields = [
            'version',
            'external_id',
        ]
        related_models = [LocalizedText]

    @staticmethod
    def get_boostable_search_attrs():
        return dict(id=dict(boost=3), _name=dict(boost=5), synonyms=dict(boost=2, wildcard=True, lower=True))

    @staticmethod
    def prepare_numeric_id(instance):
        try:
            return int(instance.mnemonic)
        except:  # pylint: disable=bare-except
            return 0

    @staticmethod
    def prepare_name(instance):
        try:
            name = instance.display_name
            if name:
                name = name.replace('-', '_')
            return name
        except:  # pylint: disable=bare-except
            return ''

    @staticmethod
    def prepare_locale(instance):
        return list(
            instance.names.filter(locale__isnull=False).distinct('locale').values_list('locale', flat=True)
        )

    @staticmethod
    def prepare_synonyms(instance):
        return list(map(lambda x: x.lower(), instance.names.filter(name__isnull=False).values_list('name', flat=True)))

    @staticmethod
    def prepare_source_version(instance):
        return list(instance.sources.values_list('version', flat=True))

    @staticmethod
    def prepare_collection_version(instance):
        return list(set(instance.expansion_set.values_list('collection_version__version', flat=True)))

    @staticmethod
    def prepare_expansion(instance):
        return list(instance.expansion_set.values_list('mnemonic', flat=True))

    @staticmethod
    def prepare_collection(instance):
        return list(set(instance.expansion_set.values_list('collection_version__mnemonic', flat=True)))

    @staticmethod
    def prepare_collection_url(instance):
        return list(set(list(instance.expansion_set.values_list('collection_version__uri', flat=True))))

    @staticmethod
    def prepare_collection_owner_url(instance):
        return list(set(expansion.owner_url for expansion in instance.expansion_set.all()))

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_dict(value)

        return value or {}

    @staticmethod
    def prepare_name_types(instance):
        return list(
            instance.names.filter(type__isnull=False).distinct('type').values_list('type', flat=True)
        )

    @staticmethod
    def prepare_description_types(instance):
        return list(
            instance.descriptions.filter(type__isnull=False).distinct('type').values_list('type', flat=True)
        )
