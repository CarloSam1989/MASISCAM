"""Flujo operativo con datos, políticas y protocolo exclusivamente ficticios QA."""
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_taller, test_tecnica as qa, test_calibracion as cq
from . import tecnica_services as t, operacion_services as o, calibracion_services as c
from . import plano_services as p, cierre_services as cierre
from .test_operacion import QA_POLICY, presupuesto_qa, verificacion_qa
from .models import ExpedienteMantenimiento, PiezaRequerida, Auditoria
from .test_plano import foto_pieza
from .recepcion_confirmacion import confirmar_recepcion
from .recepcion_services import abrir_recepcion
from .test_utils import consumir_respuesta


FLAGS = dict(cq.FLAGS, MASISCAM_CIERRE_LOCAL=True)


@override_settings(**FLAGS)
class TallerOperativoTests(TestCase):
    setUp = test_taller.TallerTests.setUp
    datos = test_taller.TallerTests.datos
    crear = test_taller.TallerTests.crear
    recepcion = test_taller.TallerTests.recepcion

    def ingreso(self):
        equipo = self.crear('OPERATIVO')
        self.assertFalse(ExpedienteMantenimiento.objects.filter(registro__equipo=equipo).exists())
        self.assertContains(self.client.get(reverse('masiscam:taller_equipo', args=[equipo.pk])), 'Registrar recepción')
        exp = self.recepcion(equipo)
        self.assertEqual(exp.asignaciones_tecnicas.get(fin__isnull=True).tecnico, self.taller)
        self.assertTrue(Auditoria.objects.filter(expediente=exp, accion='TALLER_ASIGNADO_INGRESO').exists())
        confirmar_recepcion(usuario=self.taller, empresa=self.empresa, expediente_id=exp.pk,
            clave=uuid.uuid4(), placa='SIN_PLACA', motivo_sin_placa='QA', motivo_sin_guia='QA',
            general_id=exp.recepcion.evidencias.get().pk)
        return equipo, exp

    def test_flujo_completo_sin_cambiar_usuario_y_aprobaciones_independientes(self):
        equipo, exp = self.ingreso()
        empresa, taller, planta = self.empresa, self.taller, self.usuarios['PLANTA']
        common = dict(usuario=taller, empresa=empresa)
        guia = exp.guias.get(documento__isnull=False)
        response = self.client.get(reverse('masiscam:recepcion_documento', args=[exp.pk, guia.documento_id]))
        self.assertTrue(consumir_respuesta(response).startswith(b'%PDF-'))
        qa.inspeccion_enviada(empresa, taller, exp)
        self.exp = exp  # Reuse the published-reference fixture, with explicit applicability.
        self.planta = planta
        ref, parte = qa.TecnicaTests.referencia(self)
        mapa = p.guardar_mapa(usuario=planta, empresa=empresa, referencia_id=ref.pk,
            clave=uuid.uuid4(), base_version=0, posiciones=[dict(parte=parte.pk, pagina=1, x='0.5', y='0.4')])
        diag = t.crear_revision(**common, expediente_id=exp.pk, tipo='DIAGNOSTICO', clave=uuid.uuid4())
        url = reverse('masiscam:plano_datos', args=[ref.pk])
        response = self.client.get(url, {'diagnostico': diag.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['editable'])
        seleccion = dict(origen='BIBLIOTECA', parte_id=parte.pk, posicion_plano_id=mapa.posiciones.get().pk,
            cantidad='2', unidad='unidad', condicion='Desgaste QA', accion='REEMPLAZAR', observaciones='Solo QA')
        p.guardar_seleccion(**common, diagnostico_id=diag.pk, version=diag.version_edicion,
            clave=uuid.uuid4(), datos=seleccion, fotografias=[foto_pieza()])
        pieza = PiezaRequerida.objects.get(diagnostico=diag)
        self.assertEqual(pieza.fotografias.count(), 1)
        diag.refresh_from_db()
        diag = t.guardar_revision(**common, tipo='DIAGNOSTICO', revision_id=diag.pk, version=diag.version_edicion,
            clave=uuid.uuid4(), datos=dict(fecha=timezone.localdate(), fallas='Falla QA', causas='Causa QA',
            componentes_afectados='Componente QA', recomendaciones='Evaluación QA', conclusiones='Conclusión QA', repuestos='REQUERIDOS'))
        diag = t.enviar_revision(**common, tipo='DIAGNOSTICO', revision_id=diag.pk, version=diag.version_edicion, clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            t.decidir_diagnostico(**common, diagnostico_id=diag.pk, decision='APROBADO', motivo='Intento QA', clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            p.guardar_seleccion(**common, diagnostico_id=diag.pk, version=diag.version_edicion, clave=uuid.uuid4(), datos=seleccion)
        t.decidir_diagnostico(usuario=planta, empresa=empresa, diagnostico_id=diag.pk, decision='APROBADO', motivo='', clave=uuid.uuid4())
        informe = t.reservar_informe(**common, diagnostico_id=diag.pk, clave=uuid.uuid4())
        t.generar_informe(informe.pk)
        with self.assertRaises(ValidationError):
            o.iniciar_actividad(**common, expediente_id=exp.pk, clave=uuid.uuid4(), datos=dict(trabajo='QA', componentes='QA', inicio=timezone.now()))
        gerente = self.usuarios['GERENCIA']
        prop = o.proponer(usuario=gerente, empresa=empresa, expediente_id=exp.pk, diagnostico_id=diag.pk,
            clave=uuid.uuid4(), datos=dict(alcance='Alcance ficticio QA', presupuesto_referencia='QA v1'), archivo=presupuesto_qa())
        with self.assertRaises(PermissionDenied):
            o.decidir_comercial(**common, propuesta_id=prop.pk, clave=uuid.uuid4(), archivo=qa.pdf_plano(), datos={})
        o.decidir_comercial(usuario=gerente, empresa=empresa, propuesta_id=prop.pk, clave=uuid.uuid4(), archivo=qa.pdf_plano(),
            datos=dict(decision='APROBADO', fecha_cliente=timezone.now(), representante_cliente='FICTICIO QA', motivo='Solo QA', **verificacion_qa()))
        recibido = o.recibir_repuesto(**common, expediente_id=exp.pk, clave=uuid.uuid4(), archivo=None, fotografia=None,
            datos=dict(pieza_id=pieza.pk, numero_parte=pieza.numero_parte, descripcion=pieza.descripcion,
                cantidad='2', unidad=pieza.unidad, fecha=timezone.now(), procedencia='Entrega QA', entregado_por='Proveedor QA', guia='', discrepancia=''))
        act = o.iniciar_actividad(**common, expediente_id=exp.pk, clave=uuid.uuid4(),
            datos=dict(trabajo='Montaje ficticio QA', componentes='Componente QA', inicio=timezone.now()))
        o.usar_repuesto(**common, actividad_id=act.pk, recepcion_id=recibido.pk, cantidad='2', observaciones='QA', clave=uuid.uuid4())
        resultado = o.terminar_actividad(**common, actividad_id=act.pk, clave=uuid.uuid4(),
            datos=dict(fin=timezone.now(), trabajo_realizado='Trabajo QA', resultado='Resultado QA'))
        with self.assertRaises(PermissionDenied):
            o.revisar_actividad(**common, resultado_id=resultado.pk, clave=uuid.uuid4(), decision='APROBADO', motivo='Intento QA')
        o.revisar_actividad(usuario=planta, empresa=empresa, resultado_id=resultado.pk, clave=uuid.uuid4(), decision='APROBADO', motivo='Revisión QA')
        definicion = cq.definicion_qa()
        definicion['aplicacion'].update(marca=equipo.marca, modelo=equipo.modelo)
        protocolo = c.crear_plantilla(usuario=planta, empresa=empresa, clave=uuid.uuid4(), archivo=cq.protocolo_pdf(),
            datos=dict(codigo='TALLER-QA', titulo='FICTICIO QA', tipo_equipo=equipo.tipo_producto,
                definicion=definicion, proyecto_id=equipo.proyecto_id))
        c.aplicar_plantilla(usuario=planta, empresa=empresa, plantilla_id=protocolo.pk, equipo_id=equipo.pk,
            clave=uuid.uuid4(), fundamento='Solo QA', version_equipo=protocolo.definicion['aplicacion']['version_equipo'])
        with self.assertRaises(ValidationError):
            c.iniciar(**common, expediente_id=exp.pk, plantilla_id=protocolo.pk, clave=uuid.uuid4())
        revisor = qa.usuario(empresa, 'revisor-protocolo-taller', 'PLANTA')
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            cq.validar_qa(empresa, revisor, protocolo)
            cal = c.iniciar(**common, expediente_id=exp.pk, plantilla_id=protocolo.pk, clave=uuid.uuid4())
            cal = cq.completar(empresa, taller, cal)
            self.assertEqual(cal.mediciones.count(), 16)
            cal = c.enviar(**common, calibracion_id=cal.pk, version=cal.version_edicion, clave=uuid.uuid4())
            with self.assertRaises(PermissionDenied):
                c.medir(**common, calibracion_id=cal.pk, version=cal.version_edicion, clave=uuid.uuid4(),
                    punto='QA-01', valor='1', observaciones='Intento posterior al envío QA')
            decision = dict(calibracion_id=cal.pk, clave=uuid.uuid4(), decision='APROBADA', motivo='Revisión QA',
                evaluaciones={row['codigo']:'CONFORME' for row in protocolo.definicion['mediciones']})
            with self.assertRaises(PermissionDenied): c.decidir(**common, **decision)
            c.decidir(usuario=planta, empresa=empresa, **decision)
            self.assertContains(self.client.get(reverse('masiscam:taller_equipo', args=[equipo.pk])), 'Aprobada')
            with self.assertRaises(PermissionDenied): cierre.reservar(**common, expediente_id=exp.pk, clave=uuid.uuid4())
        self.assertEqual(taller.perfiles.get().rol_masiscam.rol, 'TALLER')

    def test_limites_urls_ajax_documentos_y_revocacion_asignacion(self):
        equipo, exp = self.ingreso()
        urls = [('tecnica_expediente',[exp.pk]), ('operacion_detalle',[exp.pk]),
                ('calibracion_expediente',[exp.pk]), ('cierre_detalle',[exp.pk]), ('cierre_historial',[equipo.pk])]
        for nombre, args in urls:
            self.assertEqual(self.client.get(reverse('masiscam:'+nombre,args=args)).status_code, 200)
        self.client.force_login(self.otro)
        with self.assertRaises(PermissionDenied):
            t.guardar_archivo(usuario=self.otro, empresa=self.empresa, proyecto=equipo.proyecto,
                equipo=equipo, archivo=foto_pieza(), categoria='FOTOGRAFIAS')
        for nombre,args in urls:
            self.assertIn(self.client.get(reverse('masiscam:'+nombre,args=args)).status_code, (403,404))
        self.assertEqual(self.client.get(reverse('masiscam:taller_equipo',args=[equipo.pk])).status_code,404)
        self.client.force_login(self.taller)
        for operacion in ('decision','propuesta','revision','respaldo','documento'):
            self.assertEqual(self.client.get(reverse('masiscam:operacion_operar',args=[exp.pk,operacion])).status_code,403)
        self.assertEqual(self.client.post(reverse('masiscam:biblioteca_referencia_crear'),{}).status_code,403)
        t.asignar_tecnico(usuario=self.usuarios['PLANTA'], empresa=self.empresa, expediente_id=exp.pk,
            tecnico_id=self.usuarios['TECNICO'].pk, motivo='Reasignación QA', clave=uuid.uuid4())
        for nombre,args in urls:
            self.assertIn(self.client.get(reverse('masiscam:'+nombre,args=args)).status_code,(403,404))
        with self.assertRaises(PermissionDenied):
            t.guardar_archivo(usuario=self.taller, empresa=self.empresa, proyecto=equipo.proyecto,
                equipo=equipo, archivo=foto_pieza(), categoria='FOTOGRAFIAS')

    def test_borrador_y_envio_desde_la_interfaz_continuan_en_el_mismo_flujo(self):
        equipo, exp = self.ingreso()
        response = self.client.post(reverse('masiscam:tecnica_nueva_revision', args=[exp.pk,'INSPECCION']),
            {'clave':str(uuid.uuid4())})
        self.assertEqual(response.status_code,302)
        inspeccion = exp.inspeccion_set.get()
        url = reverse('masiscam:tecnica_revision',args=['INSPECCION',inspeccion.pk])
        self.assertRedirects(response,url)
        self.assertTrue(self.client.get(url).context['editable'])
        response = self.client.post(url, dict(op='guardar', clave=str(uuid.uuid4()), version=inspeccion.version_edicion,
            fecha=timezone.localdate().isoformat(), estado_fisico='QA', condiciones_generales='QA', hallazgos='QA',
            observaciones='', motivo_sin_evidencia='Sin evidencia adicional QA'))
        self.assertRedirects(response,url)
        inspeccion.refresh_from_db()
        self.assertIsNone(inspeccion.enviado_en)
        response = self.client.post(url, dict(op='enviar',clave=str(uuid.uuid4()), version=inspeccion.version_edicion))
        self.assertRedirects(response,reverse('masiscam:taller_equipo',args=[equipo.pk])+f'?expediente={exp.pk}')
        response = self.client.post(reverse('masiscam:tecnica_nueva_revision',args=[exp.pk,'DIAGNOSTICO']),
            {'clave':str(uuid.uuid4())})
        self.assertEqual(response.status_code,302)
        self.assertEqual(exp.diagnostico_set.get().tecnico_id,self.taller.pk)

    def test_equipo_separado_de_multiples_mantenimientos_y_selector_acotado(self):
        equipo, anterior = self.ingreso()
        snapshot = dict(anterior.recepcion.snapshot)
        nuevo = abrir_recepcion(usuario=self.taller, empresa=self.empresa, equipo_id=equipo.pk,
            cliente_id=self.cliente.pk, clave=uuid.uuid4(), tipo='MANTENIMIENTO', fecha=timezone.now(),
            condicion_ingreso='Segundo ingreso ficticio QA', fotografias=[foto_pieza()])
        url = reverse('masiscam:taller_equipo',args=[equipo.pk])
        self.assertEqual(self.client.get(url).context['expediente'].pk,nuevo.pk)
        self.assertEqual(self.client.get(url,{'expediente':anterior.pk}).context['expediente'].pk,anterior.pk)
        self.assertEqual(self.client.get(url,{'expediente':'invalido'}).status_code,404)
        otro_equipo = self.crear('OTRO-MANTENIMIENTO')
        self.assertEqual(self.client.get(reverse('masiscam:taller_equipo',args=[otro_equipo.pk]),
            {'expediente':anterior.pk}).status_code,404)
        anterior.recepcion.refresh_from_db()
        self.assertEqual(anterior.recepcion.snapshot,snapshot)
        self.assertEqual(equipo.registros.count(),2)
