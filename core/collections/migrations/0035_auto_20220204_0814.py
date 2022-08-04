# Generated by Django 3.2.8 on 2022-02-04 08:14

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('concepts', '0028_auto_20220203_0729'),
        ('collections', '0034_merge_0028_auto_20220106_0919_0033_auto_20211208_0322'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReferencedConcept',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('concept', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='concepts.concept')),
                ('reference', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='collections.collectionreference')),
            ],
        ),
        migrations.AddField(
            model_name='collectionreference',
            name='_concepts',
            field=models.ManyToManyField(related_name='references', through='collections.ReferencedConcept', to='concepts.Concept'),
        ),
    ]
