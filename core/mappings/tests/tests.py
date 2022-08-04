from uuid import UUID

import factory
from django.core.exceptions import ValidationError

from core.common.constants import HEAD, CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.serializers import MappingMinimalSerializer, MappingVersionDetailSerializer, \
    MappingDetailSerializer, \
    MappingVersionListSerializer, MappingListSerializer, MappingReverseMinimalSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile


class MappingTest(OCLTestCase):
    def test_mapping(self):
        self.assertEqual(Mapping(mnemonic='foobar').mapping, 'foobar')

    def test_get_search_document(self):
        self.assertEqual(Mapping.get_search_document(), MappingDocument)

    def test_source(self):
        self.assertIsNone(Mapping().source)
        self.assertEqual(Mapping(parent=Source(mnemonic='source')).source, 'source')

    def test_parent_source(self):
        source = Source(mnemonic='source')
        self.assertEqual(Mapping(parent=source).parent_source, source)

    def test_from_source_owner_mnemonic(self):
        from_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(from_concept=from_concept, from_source=from_concept.parent)

        self.assertEqual(mapping.from_source_owner_mnemonic, 'org-foo')

    def test_to_source_owner_mnemonic(self):
        to_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(to_concept=to_concept)

        self.assertEqual(mapping.to_source_owner_mnemonic, 'org-foo')

    def test_from_source_shorthand(self):
        from_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(from_concept=from_concept)

        self.assertEqual(mapping.from_source_shorthand, 'org-foo:foobar')

    def test_to_source_shorthand(self):
        to_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(to_concept=to_concept)

        self.assertEqual(mapping.to_source_shorthand, 'org-foo:foobar')

    def test_from_concept_shorthand(self):
        from_concept = ConceptFactory(
            mnemonic='concept-foo',
            parent=OrganizationSourceFactory(
                mnemonic='source-foo', organization=OrganizationFactory(mnemonic='org-foo')
            )
        )
        mapping = Mapping(from_concept=from_concept, from_concept_code='concept-foo', from_source=from_concept.parent)

        self.assertEqual(mapping.from_concept_shorthand, 'org-foo:source-foo:concept-foo')

    def test_to_concept_shorthand(self):
        to_concept = ConceptFactory(
            mnemonic='concept-foo',
            parent=OrganizationSourceFactory(
                mnemonic='source-foo', organization=OrganizationFactory(mnemonic='org-foo')
            )
        )
        mapping = Mapping(to_concept=to_concept)

        self.assertEqual(mapping.to_concept_shorthand, 'org-foo:source-foo:concept-foo')

    def test_get_to_source(self):
        mapping = Mapping()

        self.assertIsNone(mapping.get_to_source())

        source = Source(id=123)
        mapping = Mapping(to_source=source)

        self.assertEqual(mapping.get_to_source(), source)

        concept = ConceptFactory()
        mapping = Mapping(to_concept=concept)

        self.assertEqual(mapping.get_to_source(), concept.parent)

    def test_get_to_concept_name(self):
        mapping = Mapping()

        self.assertIsNone(mapping.get_to_concept_name())

        mapping = Mapping(to_concept_name='to-concept-name')

        self.assertEqual(mapping.get_to_concept_name(), 'to-concept-name')

        concept = ConceptFactory(names=[LocalizedTextFactory()])
        self.assertIsNotNone(concept.display_name)

        mapping = Mapping(to_concept=concept)

        self.assertEqual(mapping.get_to_concept_name(), concept.display_name)

    def test_owner(self):
        org = Organization(id=123)
        user = UserProfile(id=123)

        self.assertIsNone(Mapping().owner)
        self.assertEqual(Mapping(parent=Source(organization=org)).owner, org)
        self.assertEqual(Mapping(parent=Source(user=user)).owner, user)

    def test_owner_name(self):
        org = Organization(id=123, mnemonic='org')
        user = UserProfile(id=123, username='user')

        self.assertEqual(Mapping().owner_name, '')
        self.assertEqual(Mapping(parent=Source(organization=org)).owner_name, 'org')
        self.assertEqual(Mapping(parent=Source(user=user)).owner_name, 'user')

    def test_owner_type(self):
        org = Organization(id=123, mnemonic='org')
        user = UserProfile(id=123, username='user')

        self.assertIsNone(Mapping().owner_type)
        self.assertEqual(Mapping(parent=Source(organization=org)).owner_type, 'Organization')
        self.assertEqual(Mapping(parent=Source(user=user)).owner_type, 'User')

    def test_persist_new(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.version, str(mapping.id))
        self.assertEqual(source.mappings_set.count(), 2)
        self.assertEqual(source.mappings.count(), 2)
        self.assertEqual(
            mapping.uri,
            f'/orgs/{source.organization.mnemonic}/sources/{source.mnemonic}/mappings/{mapping.mnemonic}/'
        )

    def test_persist_new_autoid_sequential(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_mapping_mnemonic='sequential', autoid_mapping_external_id='sequential')
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'map_type': 'same-as', 'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '1')
        self.assertEqual(mapping.external_id, '1')

        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'map_type': 'close-to', 'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '2')
        self.assertEqual(mapping.external_id, '2')

        for mapping in Mapping.objects.filter(mnemonic='1'):
            mapping.delete()

        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'map_type': 'same-as', 'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '3')
        self.assertEqual(mapping.external_id, '3')

        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'map_type': 'foobar', 'parent_id': source.id, 'mnemonic': '1', 'external_id': '1'
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '1')
        self.assertEqual(mapping.external_id, '1')

        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory),
            'from_concept': concept1,
            'to_concept': concept2,
            'map_type': 'same-as2',
            'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '4')
        self.assertEqual(mapping.external_id, '4')

        source.autoid_mapping_mnemonic_start_from = 100
        source.save()

        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory),
            'from_concept': concept1,
            'to_concept': concept2,
            'map_type': 'same-as3',
            'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '100')
        self.assertEqual(mapping.external_id, '5')

        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory),
            'from_concept': concept1,
            'to_concept': concept2,
            'map_type': 'same-as4',
            'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.mnemonic, '101')
        self.assertEqual(mapping.external_id, '6')

    def test_persist_new_autoid_uuid(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_mapping_mnemonic='uuid', autoid_mapping_external_id='uuid')
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping1 = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'map_type': 'same-as', 'parent_id': source.id
        }, source.created_by)

        self.assertIsNotNone(mapping1.id)
        self.assertIsInstance(mapping1.mnemonic, UUID)
        self.assertIsInstance(mapping1.external_id, UUID)

        mapping2 = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'map_type': 'close-to', 'parent_id': source.id
        }, source.created_by)

        self.assertIsNotNone(mapping2.id)
        self.assertIsInstance(mapping2.mnemonic, UUID)
        self.assertIsInstance(mapping2.external_id, UUID)

        self.assertNotEqual(mapping2.mnemonic, mapping1.mnemonic)
        self.assertNotEqual(mapping2.external_id, mapping1.external_id)

    def test_persist_clone(self):
        source_head = OrganizationSourceFactory(version=HEAD)
        OrganizationSourceFactory(
            version='v0', mnemonic=source_head.mnemonic, organization=source_head.organization
        )

        self.assertEqual(source_head.versions.count(), 2)

        mapping = MappingFactory(parent=source_head)
        cloned_mapping = mapping.clone(mapping.created_by)

        self.assertEqual(
            Mapping.persist_clone(cloned_mapping),
            dict(version_created_by='Must specify which user is attempting to create a new mapping version.')
        )

        self.assertEqual(Mapping.persist_clone(cloned_mapping, mapping.created_by), {})

        persisted_mapping = Mapping.objects.filter(
            id=cloned_mapping.id, version=cloned_mapping.version
        ).first()
        self.assertEqual(mapping.versions.count(), 2)
        self.assertNotEqual(mapping.id, persisted_mapping.id)
        self.assertEqual(persisted_mapping.from_concept_id, mapping.from_concept_id)
        self.assertEqual(persisted_mapping.to_concept_id, mapping.to_concept_id)
        self.assertEqual(persisted_mapping.parent, source_head)
        self.assertEqual(persisted_mapping.sources.count(), 1)
        self.assertEqual(
            persisted_mapping.uri,
            f'/orgs/{source_head.organization.mnemonic}/sources/{source_head.mnemonic}/'
            f'mappings/{persisted_mapping.mnemonic}/{persisted_mapping.version}/'
        )
        self.assertEqual(
            persisted_mapping.version_url, persisted_mapping.uri
        )

    def test_get_serializer_class(self):
        self.assertEqual(Mapping.get_serializer_class(), MappingListSerializer)
        self.assertEqual(Mapping.get_serializer_class(version=True), MappingVersionListSerializer)
        self.assertEqual(Mapping.get_serializer_class(verbose=True), MappingDetailSerializer)
        self.assertEqual(Mapping.get_serializer_class(verbose=True, version=True), MappingVersionDetailSerializer)
        self.assertEqual(Mapping.get_serializer_class(brief=True), MappingMinimalSerializer)
        self.assertEqual(Mapping.get_serializer_class(brief=True, reverse=True), MappingReverseMinimalSerializer)

    def test_clean(self):
        mapping = Mapping()
        with self.assertRaises(ValidationError) as ex:
            mapping.clean()
        self.assertEqual(
            ex.exception.messages,
            ["Must specify a 'from_concept'. Must specify either 'to_concept_url' or 'to_source_url' & 'to_concept_code'."]   # pylint: disable=line-too-long
        )


class OpenMRSMappingValidatorTest(OCLTestCase):
    def setUp(self):
        self.create_lookup_concept_classes()

    def test_single_mapping_between_concepts(self):
        source = OrganizationSourceFactory(version=HEAD, custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept1 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        concept2 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        mapping1 = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2)
        mapping1.populate_fields_from_relations({})
        mapping1.save()

        self.assertIsNotNone(mapping1.id)

        mapping2 = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2, mnemonic='m2')
        mapping2.populate_fields_from_relations({})

        with self.assertRaises(ValidationError) as ex:
            mapping2.clean()

        self.assertEqual(ex.exception.messages, ['There can be only one mapping between two concepts'])

        mapping3 = MappingFactory.build(parent=source, to_concept=concept2, from_concept=concept1)
        mapping3.populate_fields_from_relations({})
        mapping3.clean()

    def test_invalid_map_type(self):
        source = OrganizationSourceFactory(version=HEAD, custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept1 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        concept2 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])

        mapping = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2, map_type='Foo bar')
        mapping.populate_fields_from_relations({})

        with self.assertRaises(ValidationError) as ex:
            mapping.clean()
        self.assertEqual(ex.exception.messages, ['Invalid mapping type'])

        # 'Q-AND-A' is present in OpenMRS lookup values
        mapping = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2, map_type='Q-AND-A')
        mapping.populate_fields_from_relations({})
        mapping.clean()

    def test_external_id(self):
        source = OrganizationSourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept1 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        concept2 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])

        mapping = MappingFactory.build(
            parent=source, to_concept=concept1, from_concept=concept2, map_type='Q-AND-A', external_id='1'*37)
        mapping.populate_fields_from_relations({})

        with self.assertRaises(ValidationError) as ex:
            mapping.clean()
        self.assertEqual(ex.exception.messages, ['Mapping External ID cannot be more than 36 characters.'])

        mapping = MappingFactory.build(
            parent=source, to_concept=concept1, from_concept=concept2, map_type='Q-AND-A', external_id='1'*36)
        mapping.populate_fields_from_relations({})
        mapping.clean()
