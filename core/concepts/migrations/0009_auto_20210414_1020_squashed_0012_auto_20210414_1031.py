# Generated by Django 3.1.8 on 2021-04-14 10:36

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    replaces = [('concepts', '0009_auto_20210414_1020'), ('concepts', '0010_auto_20210414_1024'), ('concepts', '0011_auto_20210414_1028'), ('concepts', '0012_auto_20210414_1031')]

    dependencies = [
        ('concepts', '0008_auto_20210326_1029'),
    ]

    operations = [
        migrations.CreateModel(
            name='HierarchicalConcepts',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('child', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='child_concept', to='concepts.concept')),
                ('parent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parent_concept', to='concepts.concept')),
            ],
        ),
        migrations.AddField(
            model_name='concept',
            name='parent_concepts',
            field=models.ManyToManyField(through='concepts.HierarchicalConcepts', to='concepts.Concept'),
        ),
        migrations.AlterField(
            model_name='hierarchicalconcepts',
            name='child',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parents', to='concepts.concept'),
        ),
        migrations.AlterField(
            model_name='hierarchicalconcepts',
            name='parent',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='children', to='concepts.concept'),
        ),
        migrations.AlterField(
            model_name='concept',
            name='parent_concepts',
            field=models.ManyToManyField(related_name='child_concepts', through='concepts.HierarchicalConcepts', to='concepts.Concept'),
        ),
        migrations.AlterField(
            model_name='hierarchicalconcepts',
            name='child',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='child_parent', to='concepts.concept'),
        ),
        migrations.AlterField(
            model_name='hierarchicalconcepts',
            name='parent',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parent_child', to='concepts.concept'),
        ),
    ]
