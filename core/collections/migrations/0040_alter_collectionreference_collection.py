# Generated by Django 3.2.8 on 2022-02-04 12:30

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('collections', '0039_auto_20220204_1229'),
    ]

    operations = [
        migrations.AlterField(
            model_name='collectionreference',
            name='collection',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reference_set', to='collections.collection'),
        ),
    ]
