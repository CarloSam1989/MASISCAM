from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0007_registro_tipos")]
    operations = [migrations.AddField(model_name="documento", name="drive_upload_id", field=models.CharField(blank=True, editable=False, max_length=255))]
