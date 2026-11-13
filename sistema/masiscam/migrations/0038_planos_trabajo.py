from django.db import migrations, models
import django.db.models.deletion
import importlib


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        raise RuntimeError('Los planos de trabajo requieren PostgreSQL.')
    schema_editor.execute("""
        CREATE FUNCTION masiscam_plano_trabajo_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' AND (NEW.expediente_trabajo_id IS DISTINCT FROM OLD.expediente_trabajo_id OR
            (OLD.expediente_trabajo_id IS NOT NULL AND NEW.documento_id<>OLD.documento_id)) THEN
            RAISE EXCEPTION 'Origen del plano de trabajo inmutable' USING ERRCODE='23514';
          END IF;
          IF NEW.expediente_trabajo_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM masiscam_expedientemantenimiento x JOIN masiscam_registroequipo r ON r.id=x.registro_id
              JOIN masiscam_equipo e ON e.id=r.equipo_id JOIN masiscam_documento d ON d.id=NEW.documento_id
            WHERE x.id=NEW.expediente_trabajo_id AND x.empresa_id=NEW.empresa_id AND d.equipo_id=e.id
              AND d.proyecto_id=e.proyecto_id AND d.tipo_mime='application/pdf' AND NEW.tipo='PLANO'
          ) THEN RAISE EXCEPTION 'Plano fuera del expediente' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER masiscam_plano_trabajo_scope BEFORE INSERT OR UPDATE ON masiscam_referenciatecnica
          FOR EACH ROW EXECUTE FUNCTION masiscam_plano_trabajo_scope();
        CREATE FUNCTION masiscam_seleccion_congelada() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE diag bigint; congelar boolean;
        BEGIN
          IF TG_OP='DELETE' THEN diag=OLD.diagnostico_id; ELSE diag=NEW.diagnostico_id; END IF;
          PERFORM 1 FROM masiscam_expedientemantenimiento x JOIN masiscam_diagnostico d ON d.expediente_id=x.id
            WHERE d.id=diag FOR UPDATE OF x;
          IF TG_TABLE_NAME='masiscam_piezarequerida' THEN congelar=true;
          ELSE congelar=(TG_OP<>'DELETE' AND NEW.pieza_id IS NOT NULL) OR (TG_OP<>'INSERT' AND OLD.pieza_id IS NOT NULL);
          END IF;
          IF congelar THEN
            IF EXISTS (SELECT 1 FROM masiscam_solicitudrepuestos WHERE diagnostico_id=diag) OR
              (TG_OP='UPDATE' AND EXISTS (SELECT 1 FROM masiscam_solicitudrepuestos WHERE diagnostico_id=OLD.diagnostico_id)) THEN
              RAISE EXCEPTION 'Seleccion enviada a OFICINA inmutable' USING ERRCODE='23514';
            END IF;
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER masiscam_seleccion_congelada BEFORE INSERT OR UPDATE OR DELETE ON masiscam_piezarequerida
          FOR EACH ROW EXECUTE FUNCTION masiscam_seleccion_congelada();
        CREATE TRIGGER masiscam_seleccion_congelada BEFORE INSERT OR UPDATE OR DELETE ON masiscam_evidenciatecnica
          FOR EACH ROW EXECUTE FUNCTION masiscam_seleccion_congelada();
        CREATE FUNCTION masiscam_componente_trabajo_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM masiscam_referenciatecnica r JOIN masiscam_piezarequerida p ON p.parte_id=OLD.id
            WHERE r.id=OLD.referencia_id AND r.expediente_trabajo_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Componente citado en trabajo inmutable' USING ERRCODE='23514';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER masiscam_componente_trabajo_guard BEFORE UPDATE OR DELETE ON masiscam_partereferenciatecnica
          FOR EACH ROW EXECUTE FUNCTION masiscam_componente_trabajo_guard();
        CREATE OR REPLACE FUNCTION flujo_solicitudrepuestos_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Registro operativo inmutable' USING ERRCODE='23514'; END IF;
          PERFORM 1 FROM masiscam_expedientemantenimiento WHERE id=NEW.expediente_id FOR UPDATE;
          IF NOT EXISTS (SELECT 1 FROM masiscam_diagnostico d JOIN masiscam_etapaexpediente e
              ON e.diagnostico_id=d.id AND e.codigo='DIAGNOSTICO'
            WHERE d.id=NEW.diagnostico_id AND d.expediente_id=NEW.expediente_id AND d.tecnico_id=NEW.autor_id
              AND (d.enviado_en IS NOT NULL OR (d.estado='EN_PROCESO' AND NEW.snapshot->>'preliminar'='true')))
          THEN RAISE EXCEPTION 'Origen operativo ajeno o incoherente' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END $$;
    """)


def uninstall(apps, schema_editor):
    schema_editor.execute('DROP TRIGGER masiscam_plano_trabajo_scope ON masiscam_referenciatecnica')
    schema_editor.execute('DROP FUNCTION masiscam_plano_trabajo_scope()')
    for table in ('piezarequerida', 'evidenciatecnica'):
        schema_editor.execute(f'DROP TRIGGER masiscam_seleccion_congelada ON masiscam_{table}')
    schema_editor.execute('DROP FUNCTION masiscam_seleccion_congelada()')
    schema_editor.execute('DROP TRIGGER masiscam_componente_trabajo_guard ON masiscam_partereferenciatecnica')
    schema_editor.execute('DROP FUNCTION masiscam_componente_trabajo_guard()')
    checks = importlib.import_module('masiscam.migrations.0034_guardas_relevo_operativo').CHECKS
    schema_editor.execute(f"""CREATE OR REPLACE FUNCTION flujo_solicitudrepuestos_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Registro operativo inmutable' USING ERRCODE='23514'; END IF;
        IF NOT ({checks['solicitudrepuestos']}) THEN RAISE EXCEPTION 'Origen operativo ajeno o incoherente' USING ERRCODE='23514'; END IF;
        RETURN NEW; END $$""")


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0037_diagnostico_borrador_inspeccion')]
    operations = [
        migrations.AddField(model_name='referenciatecnica', name='expediente_trabajo',
            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='planos_trabajo', to='masiscam.expedientemantenimiento')),
        migrations.RunPython(install, uninstall),
    ]
